"""Chaoxing image verification; derived from skreon's upstream captcha adapter."""
__author__ = 'skreon 1340554713@qq.com'

from http.cookies import SimpleCookie
from random import randint
import threading
import time
from urllib.parse import urljoin, urlparse

from loguru import logger
import requests


def ocr_init():
    """Initialize once per recovery manager, using the bundled CPU model."""
    try:
        from api.ocr import CaptchaOCR
        return CaptchaOCR()
    except Exception as exc:
        logger.warning('验证码识别组件不可用（{}），请检查完整发行目录或手动验证。', type(exc).__name__)
        return None


def is_captcha_response(response):
    text = response.text.lstrip().lower()
    # A normal JSON field named "validate" is not a captcha interception.
    return text.startswith('<') and any(word in text for word in ('验证码', 'processverify', 'captcha'))


_MISSING = object()


class CxCaptcha:
    host = 'https://mooc1.chaoxing.com'
    api = {'get': '/processVerifyPng.ac', 'submit': '/html/processVerify.ac'}

    def __init__(self, user_agent, cookies='', ocr=_MISSING, source_session=None, challenge_response=None):
        self.user_agent = user_agent
        # Sharing the session preserves cookie domain/path, proxies and TLS.
        self.s = source_session if source_session is not None else requests.Session()
        if source_session is None and cookies:
            parsed = SimpleCookie()
            parsed.load(cookies)
            for name, value in parsed.items():
                self.s.cookies.set(name, value.value, domain='mooc1.chaoxing.com', path='/')
        self.ocr = ocr_init() if ocr is _MISSING else ocr
        self.last_failure = ''
        self.image_url = self.host + self.api['get']
        self.submit_url = self.host + self.api['submit']
        self.code_field = 'ucode'
        self.form_fields = {'app': 0}
        self.image_challenge = challenge_response is None
        if challenge_response is not None:
            self._read_challenge(challenge_response)

    def _read_challenge(self, response):
        """Use the current challenge's same-origin image/form, not guessed APIs."""
        from bs4 import BeautifulSoup
        base = response.url if isinstance(getattr(response, 'url', None), str) else self.host + '/'
        origin = urlparse(base)
        if origin.hostname != urlparse(self.host).hostname or origin.scheme != 'https':
            return
        text = response.text
        if 'processverify' in text.lower():
            self.image_challenge = True  # Explicit legacy protocol indication.
        document = BeautifulSoup(text, 'html.parser')
        fields = ('ucode', 'verifycode', 'captcha', 'vcode', 'code', 'validatecode')
        for form in document.find_all('form', action=True):
            code = form.find('input', attrs={'name': lambda value: value and value.lower() in fields})
            image = form.find('img', src=True)
            if code is None or image is None:
                continue
            image_url, submit_url = urljoin(base, image['src']), urljoin(base, form['action'])
            if not all(urlparse(url).scheme == 'https' and urlparse(url).hostname == origin.hostname for url in (image_url, submit_url)):
                continue
            if form.get('method', 'get').lower() != 'get':
                continue  # Do not invent a different submission protocol.
            self.image_url, self.submit_url = image_url, submit_url
            self.code_field = code['name']
            self.form_fields = {field['name']: field.get('value', '') for field in form.find_all('input', type='hidden', attrs={'name': True})}
            self.image_challenge = True
            return

    def getCaptcha(self):
        res = self.s.get(self.image_url, params={'t': randint(0, 2147483647)},
                         headers={'User-Agent': self.user_agent, 'Referer': self.host + '/', 'Accept': 'image/*'},
                         timeout=(5, 15), allow_redirects=False)
        try:
            mime = res.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
            if res.status_code == 200 and mime in ('image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/bmp') and res.content and len(res.content) <= 2 * 1024 * 1024:
                return res.content
            self.last_failure = f'验证码图片不可用（HTTP {res.status_code}）'
            return None
        finally:
            res.close()

    def submitCaptcha(self, cap_token):
        res = self.s.get(self.submit_url, params=dict(self.form_fields, **{self.code_field: cap_token}),
                         headers={'User-Agent': self.user_agent, 'Referer': self.host + '/'},
                         timeout=(5, 15), allow_redirects=False)
        try:
            destination = urlparse(urljoin(self.host, res.headers.get('Location', '')))
            # Observe the redirect itself. Following it hides a successful 302
            # behind a 200; a redirect to login is not successful verification.
            accepted = (res.status_code in (302, 303) and bool(res.headers.get('Location'))
                        and destination.scheme in ('http', 'https')
                        and destination.hostname == urlparse(self.host).hostname
                        and not any(word in destination.path.lower() for word in ('login', 'processverify')))
            if not accepted:
                self.last_failure = '验证码提交未被平台接受'
            return accepted
        finally:
            res.close()

    def recognition(self, img):
        if self.ocr is None:
            raise RuntimeError('验证码识别组件不可用')
        return self.ocr.classification(img).strip()

    def try_pass(self):
        self.last_failure = ''
        if not self.image_challenge:
            self.last_failure = '当前验证不是可识别的图片验证码，请在平台页面手动验证'
            return False
        if self.ocr is None:
            self.last_failure = '验证码识别组件不可用'
            return False
        try:
            picture = self.getCaptcha()
            if not picture:
                return False
            token = self.recognition(picture)
            if not token or len(token) > 32 or not token.isalnum():
                self.last_failure = '验证码识别结果无效'
                return False
            return self.submitCaptcha(token)
        except requests.RequestException as exc:
            self.last_failure = f'验证码请求失败（{type(exc).__name__}）'
        except Exception as exc:
            self.last_failure = f'验证码识别失败（{type(exc).__name__}）'
        return False


class CaptchaRecovery:
    """Serialize challenges and cap attempts for the shared login session."""
    def __init__(self):
        self.lock = threading.Lock()
        self.ocr = _MISSING
        self.generation = 0
        self.blocked = False
        self.reason = ''

    def recover(self, source_session, user_agent, response, retry_request, observed_generation=None):
        generation = self.generation if observed_generation is None else observed_generation
        with self.lock:
            if self.blocked:
                return response, True
            if generation != self.generation:
                response.close()
                response = retry_request()
                if not is_captcha_response(response):
                    return response, False
            if self.ocr is _MISSING:
                self.ocr = ocr_init()
            if self.ocr is None:
                self.reason = '验证码识别组件不可用'
            else:
                solver = CxCaptcha(user_agent, ocr=self.ocr, source_session=source_session, challenge_response=response)
                for attempt in range(3):
                    logger.info('正在处理超星验证码（第 {}/3 次）', attempt + 1)
                    if solver.try_pass():
                        self.generation += 1
                        response.close()
                        response = retry_request()
                        if not is_captcha_response(response) and response.status_code != 403:
                            logger.info('验证码提交完成，媒体请求返回 HTTP {}，继续核验进度。', response.status_code)
                            return response, False
                        self.reason = '验证码提交后媒体上报仍被拦截'
                        break
                    self.reason = solver.last_failure
                    if self.reason not in ('验证码提交未被平台接受', '验证码识别结果无效'):
                        break
                    if attempt < 2:
                        time.sleep(2)
            self.blocked = True
            logger.warning('超星验证仍未通过：{}。相关章节保留待处理；请在浏览器手动验证后重新登录或导入 Cookie，再启动任务。', self.reason)
            return response, True
