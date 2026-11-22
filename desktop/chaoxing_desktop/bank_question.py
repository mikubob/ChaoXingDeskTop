"""Build the text and image environment used by OCS-compatible v2 banks."""
import base64
from collections import OrderedDict
import hashlib
from html import unescape
from html.parser import HTMLParser
import re
import threading
import unicodedata
from urllib.parse import urljoin, urlparse


def option_lines(options):
    if isinstance(options, list):
        return [str(value) for value in options if value is not None]
    text = str(options or '')
    # Accept an explicitly labelled, single-line list such as a.1 b.2 c.3.
    if '\n' not in text and re.match(r'^\s*[Aa][.、．:：]', text):
        parts = re.split(r'\s+(?=[A-Za-z][.、．:：])', text.strip())
        labels = [re.match(r'^([A-Za-z])[.、．:：]', part) for part in parts]
        if len(parts) > 1 and all(labels) and [m[1].upper() for m in labels] == list('ABCDEFGHIJKLMNOPQRSTUVWXYZ'[:len(parts)]):
            return parts
    return text.splitlines()


class _TextWithImages(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.images = []

    def handle_starttag(self, tag, attrs):
        if tag == 'img':
            source = unescape(dict(attrs).get('src', ''))
            if source.startswith('//'):
                source = 'https:' + source
            if source.startswith(('https://', 'http://', 'data:image/')):
                self.images.append(source)
                self.parts.append(' ' + source + ' ')
        elif tag in ('br', 'p', 'div'):
            self.parts.append(' ')

    def handle_data(self, data):
        self.parts.append(data)


def text_with_images(value):
    parser = _TextWithImages()
    parser.feed(str(value or ''))
    text = re.sub(r'[\s\u200b\ufeff]+', ' ', ''.join(parser.parts)).strip()
    images = parser.images + re.findall(r'https?://[^\s<>"\']+\.(?:png|jpe?g|webp|gif)(?:\?[^\s<>"\']*)?', text, re.I)
    return text, list(dict.fromkeys(images))


QUESTION_TYPE = r'(?:单选题|多选题|判断题|填空题|简答题|名词解释|完形填空|阅读理解)'


def clean_title(title):
    # A number directly followed by an explicit type badge is a question index,
    # even without punctuation. Remove it before removing the badge, otherwise
    # "5【单选题】2025年" would become the ambiguous "52025年".
    title = re.sub(r'^\s*\d+\s*(?=[\[(【（]\s*' + QUESTION_TYPE + r'(?:\s*[,，]|\s*[\])】）]))', '', title)
    title = re.sub(r'^\s*\d+[.、。](?!\d)\s*', '', title)
    title = re.sub(r'[\[(【（]\s*' + QUESTION_TYPE + r'(?:\s*[,，]\s*\d+(?:\.\d+)?\s*分)?\s*[\])】）]', '', title)
    title = re.sub(r'[（(]\s*\d+(?:\.\d+)?\s*分\s*[)）]', '', title)
    return title.strip()


def query_title_variants(title, limit=2):
    """Typographical equivalents only: keep numbers, words and logical symbols."""
    if text_with_images(title)[1] or len(title) > 4096:
        return []
    normalized = unicodedata.normalize('NFKC', title)
    normalized = re.sub(r'\(\s*\)', '()', normalized)
    normalized = normalized.translate(str.maketrans({'“': '"', '”': '"', '‘': "'", '’': "'"}))
    # A '?' after '=' is an answer placeholder, not sentence punctuation.
    if not re.search(r'=\s*[?？]$', normalized):
        normalized = re.sub(r'[。?？]+$', '', normalized).rstrip()
    compact = re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])', '', normalized)
    if not re.search(r'字符串|字符|\bprint\b|\blen\b|[=+*/]', normalized):
        compact = compact.replace('"', '').replace("'", '')
    variants = []
    for item in (normalized, compact):
        if item and item != title and item not in variants:
            variants.append(item)
    return variants[:limit]


class ImageTooLarge(ValueError):
    pass


_CHROME_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
_MIME_EXT = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/gif': '.gif', 'image/webp': '.webp', 'image/bmp': '.bmp'}


def _detect_image_mime(raw):
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if raw.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if raw.startswith((b'GIF87a', b'GIF89a')):
        return 'image/gif'
    if len(raw) >= 12 and raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
        return 'image/webp'
    if raw.startswith(b'BM') and len(raw) >= 14:
        return 'image/bmp'
    return ''


class ImageEncoder:
    MAX_BYTES = 8 * 1024 * 1024
    MAX_CACHE_BYTES = 16 * 1024 * 1024

    def __init__(self, timeout=30):
        self.timeout = timeout
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self._lock = threading.Lock()

    def _fetch_following_redirects(self, url, headers):
        from .http import service_request
        import requests
        current = url
        for _ in range(4):
            try:
                response = service_request(self, 'get', current, max_bytes=self.MAX_BYTES, headers=headers,
                                           timeout=(10, self.timeout), stream=True)
            except requests.RequestException:
                raise
            except ValueError:
                raise ImageTooLarge('图片文件过大') from None
            if 300 <= response.status_code < 400:
                location = response.headers.get('Location', '')
                response.close()
                if not location:
                    raise requests.HTTPError('图源发生重定向', response=response)
                next_url = urljoin(current, location)
                if urlparse(next_url).scheme not in ('http', 'https'):
                    raise requests.HTTPError('图源重定向协议无效', response=response)
                current = next_url
                continue
            return response
        raise requests.HTTPError('图源重定向次数过多')

    def download(self, url):
        origin = urlparse(url)
        candidates = [
            {'Referer': f'{origin.scheme}://{origin.netloc}/', 'User-Agent': _CHROME_UA},
            {'Referer': 'https://mooc1.chaoxing.com/', 'User-Agent': _CHROME_UA},
            {'User-Agent': _CHROME_UA},
        ]
        response = None
        for index, headers in enumerate(candidates):
            response = self._fetch_following_redirects(url, headers)
            if response.status_code == 403 and index + 1 < len(candidates):
                response.close()
                continue
            break
        try:
            response.raise_for_status()
            parts, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > self.MAX_BYTES:
                    raise ImageTooLarge('图片文件过大')
                parts.append(chunk)
            return b''.join(parts)
        finally:
            response.close()

    def encode(self, url):
        with self._lock:
            if url in self.cache:
                self.cache.move_to_end(url)
                return self.cache[url]
        if url.startswith('data:image/'):
            raw = base64.b64decode(url.partition(',')[2], validate=True)
        else:
            raw = self.download(url)
        if len(raw) > self.MAX_BYTES:
            raise ImageTooLarge('图片文件过大')
        mime = _detect_image_mime(raw)
        if mime:
            result = f'data:{mime};base64,' + base64.b64encode(raw).decode('ascii')
        else:
            # 非常见位图格式时按需尝试 Qt 解码，无 PySide6 环境直接按格式无效处理。
            try:
                from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
                from PySide6.QtGui import QImageReader
            except ImportError:
                raise ValueError('图片格式无效') from None
            buffer = QBuffer()
            buffer.setData(QByteArray(raw))
            buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            reader = QImageReader(buffer)
            size = reader.size()
            if not size.isValid():
                raise ValueError('图片格式无效')
            if size.width() * size.height() > 16_000_000:
                raise ImageTooLarge('图片尺寸过大')
            picture = reader.read()
            if picture.isNull():
                raise ValueError('图片无法解码')
            if max(picture.width(), picture.height()) > 2000:
                picture = picture.scaled(2000, 2000, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            output = QBuffer()
            output.open(QIODevice.OpenModeFlag.WriteOnly)
            if not picture.save(output, 'PNG'):
                raise ValueError('图片无法转换为 PNG')
            result = 'data:image/png;base64,' + base64.b64encode(bytes(output.data())).decode('ascii')
        if len(result) > self.MAX_CACHE_BYTES:
            raise ImageTooLarge('转换后的图片过大')
        # Inline image sources can themselves be several MiB; caching them as
        # keys would bypass the cache's retained-data limit.
        if not url.startswith('data:'):
            with self._lock:
                while self.cache and (len(self.cache) >= 64 or self.cache_bytes + len(result) > self.MAX_CACHE_BYTES):
                    self.cache_bytes -= len(self.cache.popitem(last=False)[1])
                self.cache[url] = result
                self.cache_bytes += len(result)
        return result

    def cached_file_uri(self, url, directory):
        """将已缓存（或可下载）的图片落盘到临时目录供原生界面显示，失败返回空串。"""
        try:
            data_url = self.encode(url)
        except Exception:
            return ''
        header, _, payload = data_url.partition(',')
        mime = header.removeprefix('data:').partition(';')[0].lower()
        ext = _MIME_EXT.get(mime, '.png')
        digest = hashlib.sha256(url.encode('utf-8')).hexdigest()[:24]
        target = directory / f'{digest}{ext}'
        try:
            # 缓存目录由工作台创建，退出后运行中的旧后台不能重新创建它。
            if not target.exists():
                target.write_bytes(base64.b64decode(payload))
        except OSError:
            return ''
        return target.as_uri()


def build_yanxi_environment(question, encoder, warning=lambda message: None):
    """Return wire fields and the original URLs in uploaded image order."""
    title, title_images = text_with_images(question.get('title'))
    title = clean_title(title)
    options, option_images = [], []
    for line in option_lines(question.get('options')):
        text, images = text_with_images(line)
        text = re.sub(r'^\s*[A-Za-z][.、．:：\s]+', '', text).strip()
        if text:
            options.append(text)
        option_images.extend(images)
    options = '\n'.join(options)
    encoded, image_urls, replacements, total_bytes = [], [], {}, 0
    for url in list(dict.fromkeys(title_images + option_images))[:32]:
        try:
            image = encoder.encode(url)
        except Exception as exc:
            warning(f'图片转换失败（{type(exc).__name__}），保留原图片地址供题库检索')
            continue
        total_bytes += len(image)
        if total_bytes > ImageEncoder.MAX_CACHE_BYTES:
            warning('题目图片总量过大，剩余图片保留原地址供题库检索')
            break
        encoded.append(image)
        image_urls.append(url)
        replacements[url] = f' [图片{len(encoded)}] '
    def suggestion(text):
        for url, placeholder in replacements.items():
            text = text.replace(url, placeholder)
        return text
    env = dict(title=title, options=options, type=question.get('type') or 'unknown', images=encoded,
               suggestion_title=suggestion(title) if encoded else '',
               suggestion_options=suggestion(options) if any(url in replacements for url in option_images) else '')
    return env, image_urls


def yanxi_environment(question, encoder, warning=lambda message: None):
    return build_yanxi_environment(question, encoder, warning)[0]


def restore_image_placeholders(value, image_urls):
    """Resolve only known uploaded images; retain unknown diagnostic tokens."""
    if isinstance(value, str):
        def replace(match):
            index = int(match[1]) - 1
            return image_urls[index] if 0 <= index < len(image_urls) else match[0]
        return re.sub(r'\[图片(\d{1,6})\]', replace, value)
    if isinstance(value, list):
        return [restore_image_placeholders(item, image_urls) for item in value]
    if isinstance(value, dict):
        return {key: restore_image_placeholders(item, image_urls) for key, item in value.items()}
    return value
