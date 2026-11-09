# -*- coding: utf-8 -*-
import functools
import random
import secrets
import re
import threading
import time
from enum import Enum
from hashlib import md5
from typing import Optional, Literal
from typing_extensions import Self

import requests
from loguru import logger
from requests import RequestException
from requests.adapters import HTTPAdapter
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception

from chaoxing_desktop.control import checkpoint, wait as controlled_wait
from api.responses import checked_page, PlatformResponseError
from api.answer import Tiku
from api.cipher import AESCipher
from api.config import GlobalConst as gc
from api.cookies import save_cookies, use_cookies
from api.decode import (
    decode_course_list,
    decode_course_point,
    decode_course_card,
    decode_course_folder,
    decode_questions_info,
)


def get_timestamp():
    return str(int(time.time() * 1000))


def resource_json(response):
    """Resource endpoints must return JSON, never a login/verification page."""
    try:
        from api.captcha import is_captcha_response
        if is_captcha_response(response):
            raise PlatformResponseError('平台要求人工验证。', 'verification_required')
        checked_page(response, 'cards')
        try:
            data = response.json()
        except ValueError:
            raise PlatformResponseError('资源接口未返回有效 JSON。') from None
        if not isinstance(data, dict):
            raise PlatformResponseError('资源接口返回的数据结构异常。')
        return data
    finally:
        response.close()


def resource_failure(error):
    logger.warning('资源任务处理失败：{}', error)
    if isinstance(error, PlatformResponseError):
        if error.kind == 'verification_required':
            return StudyResult.VERIFICATION_REQUIRED
        if error.kind == 'auth_error':
            return StudyResult.FORBIDDEN
    return StudyResult.ERROR


class SessionManager:
    _instance = None
    _instance_lock = threading.Lock()
    _login_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._instance_lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._initialize_session()
                cls._instance = instance
        return cls._instance

    def _initialize_session(self):
        self._session = requests.Session()
        # Chapter retries already own recovery; avoid multiplying them by ten.
        self._session.mount("https://", HTTPAdapter(max_retries=1))
        self._session.mount("http://", HTTPAdapter(max_retries=1))
        from chaoxing_desktop.http import platform_request
        self._session.request = functools.partial(platform_request, self._session, self._session.request)
        self._session.max_redirects = 5
        self._session.headers.clear()
        self._session.headers.update(gc.HEADERS)
        self._session.cookies.update(use_cookies())

    @classmethod
    def get_instance(cls) -> Self:
        return cls()

    @classmethod
    def get_session(cls) -> requests.Session:
        instance = cls.get_instance()
        return instance._session

    @classmethod
    def update_cookies(cls):
        cls.get_instance()._session.cookies.update(use_cookies())

    @classmethod
    def relogin_if_needed(cls, chaoxing_instance) -> bool:
        with cls._login_lock:
            # 检查 cookie 会话是否仍然无效
            if chaoxing_instance._validate_cookie_session():
                return True

            logger.info("Cookie session invalid, attempting thread-safe relogin...")
            if chaoxing_instance.account and chaoxing_instance.account.username and chaoxing_instance.account.password:
                login_result = chaoxing_instance.login(login_with_cookies=False)
                if login_result.get("status"):
                    cls.update_cookies()
                    logger.info("Thread-safe relogin succeeded")
                    return True
                else:
                    logger.warning(f"Thread-safe relogin failed: {login_result.get('msg')}")
            return False


class Account:
    def __init__(self, _username, _password):
        self.username = _username
        self.password = _password


class RateLimiter:
    def __init__(self, call_interval):
        self.last_call = time.time()
        self.lock = threading.Lock()
        self.call_interval = call_interval

    def limit_rate(self, random_time=False, random_min=0.0, random_max=1.0):
        with self.lock:
            now = time.time()
            base_wait = max(self.last_call + self.call_interval - now, 0)
            extra_wait = random.uniform(random_min, random_max) if random_time else 0
            call_wait = base_wait + extra_wait
            self.last_call = now + call_wait

        time.sleep(call_wait)


class StudyResult(Enum):
    SUCCESS = 0
    FORBIDDEN = 1  # 403
    ERROR = 2
    TIMEOUT = 3
    VERIFICATION_REQUIRED = 4

    def is_failure(self):
        return self != StudyResult.SUCCESS


def _parse_work_record_list(html_text: str) -> list[tuple[int, float]]:
    """
    解析章节检测作答记录列表页面（/work/record-list）。

    Args:
        html_text: record-list 页面 HTML

    Returns:
        作答记录列表，元素为 (作答序号times, 成绩score)，例如 [(0, 80.0), (1, 100.0)]
    """
    records = []
    times_list = re.findall(r'viewNum">第(\d+)次', html_text)
    scores = re.findall(r'viewScore">([\d.]+)分', html_text)
    if len(times_list) != len(scores):
        # 次数/成绩数量不齐时 zip 会错位, 成绩按 0 处理(放弃分数捷径, 仅做逐题比对)
        logger.warning(f"作答记录次数/成绩数量不匹配({len(times_list)}/{len(scores)}), 成绩按0处理")
        scores = ['0'] * len(times_list)
    for t, s in zip(times_list, scores):
        try:
            records.append((int(t), float(s)))
        except ValueError:
            continue
    return records


def _parse_work_record_detail(html_text: str) -> list[dict]:
    """
    解析章节检测单次作答详情页面（/work/record-detail）。

    Args:
        html_text: record-detail 页面 HTML

    Returns:
        每题信息列表：{id, title, type_label, my_answer, correct_answer}
    """
    questions = []
    for qm in re.finditer(r'<div class="TiMu[^"]*singleQuesId" data="(\d+)"[^>]*>(.*?)(?=<div class="TiMu|$)', html_text, re.S):
        qid = qm.group(1)
        qb = qm.group(2)

        # 题型 + 题目
        tm = re.search(r'newZy_TItle">(.*?)</span>(.*?)</div>', qb, re.S)
        if tm:
            type_label = re.sub(r'<[^>]+>', '', tm.group(1)).strip()
            title = re.sub(r'<[^>]+>', '', tm.group(2))
        else:
            type_label = ""
            title = ""
        title = re.sub(r'\s+', ' ', title).strip()

        # 我的答案/正确答案: 多模式匹配, 兼容属性顺序变化与额外 class 的页面变体
        ans_patterns = [
            r'{label}[:：]</span>\s*<div class="fl answerCon">\s*(.*?)\s*</div>',
            r'{label}[:：]\s*</span>\s*<div[^>]*class="[^"]*answerCon[^"]*"[^>]*>\s*(.*?)\s*</div>',
            r'{label}[:：]\s*</span>\s*<span[^>]*>\s*(.*?)\s*</span>',
        ]

        def _extract_answer(label):
            for pat in ans_patterns:
                m = re.search(pat.format(label=label), qb, re.S)
                if m:
                    return re.sub(r'<[^>]+>', '', m.group(1)).strip()
            return None

        my_raw = _extract_answer('我的答案')
        correct_raw = _extract_answer('正确答案')

        questions.append({
            "id": qid,
            "title": title,
            "type_label": type_label,
            "my_answer": my_raw or '',
            "correct_answer": correct_raw or '',
            "parse_ok": my_raw is not None and correct_raw is not None,
        })
    return questions


_JUDGE_TRUE_SET = {'TRUE', 'T', '1', '对', '正确', '√', '是', 'YES', 'Y'}
_JUDGE_FALSE_SET = {'FALSE', 'F', '0', '错', '错误', '×', 'X', '否', 'NO', 'N', '不对', '不正确'}


def normalize_answer_text(ans) -> str:
    """作答详情答案归一化: 消除 对/错与true/false、全半角标点等格式差异.

    归一化是确定性的, 两侧同格式必然相等; 跨格式差异(如字母 vs 选项全文)
    交由调用方的满分分数兜底处理.
    """
    s = re.sub(r'\s+', '', str(ans or '')).upper()
    if s in _JUDGE_TRUE_SET:
        return 'TRUE'
    if s in _JUDGE_FALSE_SET:
        return 'FALSE'
    if re.fullmatch(r'[A-Z0-9]+', s):
        return s
    s = re.sub(r'^[A-Z][.、:：)?）]', '', s)
    s = re.sub(r'[，。、！？；：,.!?;:()（）\[\]【】"“”‘’\-_/\\|]', '', s)
    return s


class Chaoxing:
    def __init__(self, account: Account = None, tiku: Tiku = None, **kwargs):
        self.account = account
        self.cipher = AESCipher()
        self.tiku = tiku
        self.kwargs = kwargs
        self.rate_limiter = RateLimiter(0.5)  # 其他接口速率限制比较松
        self.video_log_limiter = RateLimiter(2)  # 上报进度极其容易卡验证码，限制2s一次
        from api.captcha import CaptchaRecovery
        self._captcha_recovery = CaptchaRecovery()

    def login(self, login_with_cookies=False):
        self._course_snapshot = None
        if login_with_cookies:
            logger.info("Logging in with cookies")
            SessionManager.update_cookies()
            try:
                valid = self._validate_cookie_session()
            except PlatformResponseError as exc:
                # Only explicit authentication failure permits password fallback.
                if exc.kind != 'auth_error':
                    return {"status": False, "msg": str(exc), "kind": exc.kind}
                valid = False
            if not valid:
                logger.warning("Cookie 登录校验失败，尝试使用账号密码重新登录")
                if self.account and self.account.username and self.account.password:
                    return self.login(login_with_cookies=False)
                return {"status": False, "msg": "cookies 已失效，请更新 cookies 或提供账号密码"}
            logger.info("登录成功...")
            return {"status": True, "msg": "登录成功"}

        _session = requests.Session()
        _url = "https://passport2.chaoxing.com/fanyalogin"
        _data = {
            "fid": "-1",
            "uname": self.cipher.encrypt(self.account.username),
            "password": self.cipher.encrypt(self.account.password),
            "refer": "https%3A%2F%2Fi.chaoxing.com",
            "t": True,
            "forbidotherlogin": 0,
            "validate": "",
            "doubleFactorLogin": 0,
            "independentId": 0,
        }
        logger.trace("正在尝试登录...")
        try:
            resp = _session.post(_url, headers=gc.HEADERS, data=_data, timeout=(10, 30))
        except RequestException:
            _session.close()
            return {"status": False, "msg": "登录服务连接失败或超时，请检查网络后重试。", "kind": "network_error"}
        if resp.status_code != 200:
            status = resp.status_code
            resp.close()
            _session.close()
            return {"status": False, "msg": f"登录服务暂时不可用（HTTP {status}），请稍后重试。", "kind": "service_error"}
        try:
            body = resp.json()
        except ValueError:
            resp.close()
            _session.close()
            return {"status": False, "msg": "登录响应不是有效 JSON，可能需要验证或平台暂时异常，请稍后重试。", "kind": "protocol_error"}
        if not isinstance(body, dict) or type(body.get('status')) is not bool:
            resp.close()
            _session.close()
            return {"status": False, "msg": "登录响应格式发生变化，请稍后重试或检查更新。", "kind": "protocol_error"}
        if body['status']:
            save_cookies(_session)
            # Password authentication replaces the old identity and stale cookies.
            SessionManager.get_session().cookies.clear()
            SessionManager.update_cookies()
            logger.info("登录成功...")
            resp.close()
            _session.close()
            return {"status": True, "msg": "登录成功"}
        else:
            message = body.get('msg2') or body.get('msg') or '登录未通过，请检查账密或完成平台验证。'
            resp.close()
            _session.close()
            return {"status": False, "msg": str(message)[:300], "kind": "auth_error"}

    def _validate_cookie_session(self) -> bool:
        session = SessionManager.get_instance()._session
        if not session.cookies.get("_uid"):
            return False

        try:
            resp = session.post(
                "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata",
                data={"courseType": 1, "courseFolderId": 0, "query": "", "superstarClass": 0},
                timeout=8,
            )
        except RequestException:
            raise PlatformResponseError('会话校验暂时连接失败或超时，请重试；尚未确认 Cookie 失效。', 'network_error') from None
        try:
            text = checked_page(resp, 'courses')
            courses = decode_course_list(text)
            self._course_snapshot = (self.get_uid(), time.monotonic(), courses)
        except (KeyError, IndexError, AttributeError, TypeError):
            raise PlatformResponseError('课程页面结构变化，无法核验会话，请稍后重试。') from None
        finally:
            resp.close()
        return True

    def get_fid(self):
        _session = SessionManager.get_session()
        return _session.cookies.get("fid", 1024)

    def get_uid(self):
        s = SessionManager.get_session()
        if "_uid" in s.cookies:
            return s.cookies["_uid"]
        if "UID" in s.cookies:
            return s.cookies["UID"]
        raise ValueError("Cannot get uid !")

    def get_course_list(self):
        checkpoint(self)
        _session = SessionManager.get_session()
        _url = "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata"
        _data = {"courseType": 1, "courseFolderId": 0, "query": "", "superstarClass": 0}
        logger.trace("正在读取所有的课程列表...")

        # 接口突然抽风, 增加headers
        # 有可能只是referer的问题
        _headers = {
            "Referer": "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/interaction?moocDomain=https://mooc1-1.chaoxing.com/mooc-ans",
        }
        snapshot = getattr(self, '_course_snapshot', None)
        self._course_snapshot = None  # A one-use snapshot, never shared across clients/accounts.
        if snapshot and snapshot[0] == self.get_uid() and time.monotonic() - snapshot[1] < 15:
            course_list = list(snapshot[2])
        else:
            _resp = _session.post(_url, headers=_headers, data=_data)
            try:
                course_list = decode_course_list(checked_page(_resp, 'courses'))
            finally:
                _resp.close()
        logger.info("课程列表读取完毕...")

        _interaction_url = "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/interaction"
        _interaction_resp = _session.get(_interaction_url)
        try:
            course_folder = decode_course_folder(checked_page(_interaction_resp, 'folders'))
        finally:
            _interaction_resp.close()
        for folder in course_folder:
            checkpoint(self)
            _data = {
                "courseType": 1,
                "courseFolderId": folder["id"],
                "query": "",
                "superstarClass": 0,
            }
            _resp = _session.post(_url, data=_data)
            try:
                course_list += decode_course_list(checked_page(_resp, 'courses'))
            finally:
                _resp.close()
        return course_list

    def get_course_point(self, _courseid, _clazzid, _cpi):
        checkpoint(self)
        _session = SessionManager.get_session()
        _url = f"https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/studentcourse?courseid={_courseid}&clazzid={_clazzid}&cpi={_cpi}&ut=s"
        logger.trace("URL: " + _url)
        logger.trace("开始读取课程所有章节...")
        _resp = _session.get(_url)

        logger.trace(f"原始章节列表内容:\n{_resp.text}")
        logger.info("课程章节读取成功...")
        try:
            return decode_course_point(checked_page(_resp, 'chapters', _courseid, _clazzid))
        except (KeyError, IndexError, AttributeError, TypeError):
            raise PlatformResponseError('章节页面结构发生变化，未将其当作空章节。') from None
        finally:
            _resp.close()

    def get_job_list(self, course: dict, point: dict) -> tuple[list[dict], dict]:
        _session = SessionManager.get_session()
        self.rate_limiter.limit_rate()
        job_list = []
        job_info = {}
        recognized = False
        cards_params = {
            "clazzid": course["clazzId"],
            "courseid": course["courseId"],
            "knowledgeid": point["id"],
            "ut": "s",
            "cpi": course["cpi"],
            "v": "2025-0424-1038-3",
            "mooc2": 1
        }

        # 学习界面任务卡片数, 很少有3个的, 但是对于章节解锁任务点少一个都不行, 可以从API /mooc-ans/mycourse/studentstudyAjax获取值, 或者干脆直接加, 但二者都会造成额外的请求
        for _possible_num in "0123456":
            checkpoint(self)

            logger.trace("开始读取章节所有任务点...")

            cards_params.update({"num": _possible_num})
            _resp = _session.get("https://mooc1.chaoxing.com/mooc-ans/knowledge/cards", params=cards_params)
            try:
                text = checked_page(_resp, 'cards')
                recognized = recognized or bool(re.search(r'\bmArg\s*=', text))
                _job_list, _job_info = decode_course_card(text)
            finally:
                _resp.close()
            if _job_info.get("notOpen", False):
                # 直接返回, 节省一次请求
                logger.info("该章节未开放")
                return [], _job_info

            for job in _job_list:
                job['_job_info'] = dict(_job_info)
                job['_job_info']['card_index'] = _possible_num
            job_list += _job_list
            job_info.update(_job_info)

        if not job_list:
            if not recognized:
                raise PlatformResponseError('无法确认任务卡是否为空，请重试；未跳过该章节。')
            checkpoint(self)
            if self.study_emptypage(course, point).is_failure():
                raise PlatformResponseError('空章节访问失败，请重试。', 'service_error')

        logger.trace(f"原始任务点列表内容:\n{_resp.text}")
        logger.info("章节任务点读取成功...")

        return job_list, job_info

    def get_enc(self, clazzId, jobid, objectId, playingTime, duration, userid):
        return md5(
            f"[{clazzId}][{userid}][{jobid}][{objectId}][{playingTime * 1000}][d_yHJ!$pdA~5][{duration * 1000}][0_{duration}]"
            .encode()).hexdigest()

    def video_progress_log(
            self,
            _session,
            _course,
            _job,
            _job_info,
            _dtoken,
            _duration,
            _playingTime,
            _type: str = "Video",
            _isdrag: int = 3,
            headers: Optional[dict] = None,
    ) -> tuple[bool, int]:

        if headers is None:
            logger.warning("null headers")
            headers = gc.VIDEO_HEADERS

        if self._captcha_recovery.blocked:
            return False, 428  # Local state: stop retries until manual verification.
        self.video_log_limiter.limit_rate(random_time=True, random_max=2)

        if "courseId" in _job["otherinfo"]:
            logger.error(_job["otherinfo"])
            raise RuntimeError("this is not possible")

        enc = self.get_enc(_course["clazzId"], _job["jobid"], _job["objectid"], _playingTime, _duration, self.get_uid())
        params = {
            "clazzId": _course["clazzId"],
            "playingTime": _playingTime,
            "duration": _duration,
            "clipTime": f"0_{_duration}",
            "objectId": _job["objectid"],
            "otherInfo": _job["otherinfo"],
            "courseId": _course["courseId"],
            "jobid": _job["jobid"],
            "userid": self.get_uid(),
            "isdrag": _isdrag,
            "view": "pc",
            "enc": enc,
            "dtype": _type
        }

        _url = (
            f"https://mooc1.chaoxing.com/mooc-ans/multimedia/log/a/"
            f"{_course['cpi']}/"
            f"{_dtoken}"
        )

        face_capture_enc = _job.get("videoFaceCaptureEnc")
        att_duration = _job.get("attDuration")
        att_duration_enc = _job.get("attDurationEnc")

        if face_capture_enc:
            params["videoFaceCaptureEnc"] = face_capture_enc
        if att_duration:
            params["attDuration"] = att_duration
        if att_duration_enc:
            params["attDurationEnc"] = att_duration_enc

        verification_required = False

        def perform_request(rt_val):
            nonlocal verification_required
            params.update({"rt": rt_val, "_t": get_timestamp()})
            generation = self._captcha_recovery.generation
            res = _session.get(_url, params=params, headers=headers)
            from api.captcha import is_captcha_response
            if is_captcha_response(res):
                ua = headers.get('User-Agent', gc.HEADERS.get('User-Agent'))
                res, verification_required = self._captcha_recovery.recover(
                    _session, ua, res, lambda: _session.get(_url, params=params, headers=headers), generation)
            return res

        rt = _job.get('rt')
        if not rt:
            rt_search = re.search(r"-rt_([1d])", _job['otherinfo'])
            if rt_search:
                rt_char = rt_search.group(1)
                rt = "0.9" if rt_char == "d" else "1"
                logger.trace(f"Got rt from otherinfo: {rt}")

        if rt:
            logger.trace(f"Got rt: {rt}")
            _job['rt'] = rt
            resp = perform_request(rt)
        else:
            logger.warning("Failed to get rt")
            for rt in [0.9, 1]:
                resp = perform_request(rt)
                if verification_required:
                    resp.close()
                    return False, 428
                if resp.status_code == 200:
                    _job['rt'] = rt
                    break
                elif resp.status_code == 403:
                    logger.warning("出现403报错, 正常尝试切换rt")
                    if rt != 1:
                        resp.close()
                else:
                    logger.warning("未知错误 jobid={}, status_code={}, 摘要:\n{}",
                                   _job.get("jobid"),
                                   resp.status_code,
                                   resp.text[:200])
                    break

        if verification_required:
            resp.close()
            return False, 428

        if resp.status_code == 200:
            try:
                data = resource_json(resp)
                if not isinstance(data.get('isPassed'), bool):
                    raise ValueError
                return data['isPassed'], 200
            except PlatformResponseError as exc:
                return False, {'auth_error': 403, 'verification_required': 428}.get(exc.kind, 502)
            except ValueError:
                logger.warning('媒体上报响应不是有效的进度 JSON，未确认任务完成。')
                return False, 502

        elif resp.status_code == 403:
            logger.debug(
                "视频进度上报返回403, jobid={}, 摘要={}",
                _job.get("jobid"),
                resp.text[:200],
            )

            # 若出现两个rt参数都返回403的情况, 则跳过当前任务
            logger.warning('任务「{}」媒体上报 HTTP 403，类型={}，rt={}；会话或权限仍未恢复。',
                           _job.get('name', ''), _type, _job.get('rt', rt))
            resp.close()
            return False, 403

        logger.error(f"未知错误: {resp.status_code}")
        status = resp.status_code
        resp.close()
        return False, status

    def _refresh_video_status(self, session: requests.Session, job: dict, _type: Literal["Video", "Audio"]) \
            -> Optional[dict]:
        self.rate_limiter.limit_rate(random_time=True, random_max=0.2)
        try:
            return self._load_media_status(session, job, _type)
        except (RequestException, PlatformResponseError) as exc:
            if isinstance(exc, PlatformResponseError) and exc.kind == 'verification_required':
                raise
            logger.warning('刷新媒体状态失败：{}', exc)
            return None

    def _load_media_status(self, session, job, _type):
        headers = gc.VIDEO_HEADERS if _type == "Video" else gc.AUDIO_HEADERS
        info_url = (
            f"https://mooc1.chaoxing.com/ananas/status/{job['objectid']}?"
            f"k={self.get_fid()}&flag=normal"
        )
        data = resource_json(session.get(info_url, timeout=8, headers=headers))
        if data.get('status') != 'success':
            raise PlatformResponseError('媒体尚未就绪，可能转码未完成、格式不支持或资源不可用。')
        try:
            if isinstance(data.get('duration'), bool) or int(data['duration']) <= 0:
                raise ValueError
            if not isinstance(data.get('dtoken'), str) or not data['dtoken'].strip():
                raise ValueError
        except (KeyError, TypeError, ValueError, OverflowError):
            raise PlatformResponseError('媒体缺少有效时长或上报令牌，保留待处理。') from None
        return data

    def _recover_after_forbidden(self, session: requests.Session, job: dict, _type: Literal["Video", "Audio"]):
        SessionManager.update_cookies()
        refreshed = self._refresh_video_status(session, job, _type)
        if refreshed:
            return refreshed

        if SessionManager.relogin_if_needed(self):
            return self._refresh_video_status(session, job, _type)

        return None

    def study_video(self, _course, _job, _job_info, _speed: float = 1.0,
                    _type: Literal["Video", "Audio"] = "Video") -> StudyResult:
        _session = SessionManager.get_session()

        headers = gc.VIDEO_HEADERS if _type == "Video" else gc.AUDIO_HEADERS
        if self._captcha_recovery.blocked:
            return StudyResult.VERIFICATION_REQUIRED
        try:
            _video_info = self._load_media_status(_session, _job, _type)
        except (RequestException, PlatformResponseError) as exc:
            return resource_failure(exc)

        _dtoken = _video_info["dtoken"]

        duration = int(_video_info["duration"])
        play_time = min(duration, max(0, int(_job["playTime"]) // 1000))
        # The card interval is in wall-clock seconds, independent of playback
        # speed. Invalid values must not cause a tight retry loop or endless wait.
        try:
            report_interval = float(_job_info.get("reportTimeInterval", 60))
            if not 1 <= report_interval <= 300:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            report_interval = 60.0
        last_iter = time.monotonic()
        next_report_at = last_iter

        logger.info("开始任务: {}, 类型: {}, 总时长: {}s, 已进行: {}s, 倍速: {}, 上报间隔: {}s",
                    _job['name'], _type, duration, play_time, _speed, report_interval)
        def report_media(verified=False):
            callback = getattr(self, 'desktop_media_progress', None)
            if callback:
                callback(name=_job['name'], kind=_type, duration=duration,
                         position=duration if verified else play_time, verified=verified)
        report_media()

        forbidden_retry = 0
        max_forbidden_retry = 2
        completion_attempts = 0
        first_report = True
        end_reported = False

        while True:
            checkpoint(self, cancel_only=True)
            now = time.monotonic()
            # Preserve the recorded position for the first report. Subsequent
            # updates include request time, just as playback continues during IO.
            if not first_report:
                play_time = min(duration, play_time + (now - last_iter) * _speed)
            last_iter = now
            at_end = play_time >= duration
            if now >= next_report_at or (at_end and not end_reported):
                initial = first_report
                first_report = False

                # Chaoxing's player maps the ended event to isdrag=4. A full
                # playingTime with the ordinary heartbeat type (3) is not an
                # ended event. Only send it once the recorded/local time is full.
                passed, state = self.video_progress_log(_session, _course, _job, _job_info, _dtoken, duration,
                                                        int(play_time), _type, headers=headers,
                                                        _isdrag=4 if at_end else 3)

                if state == 428:
                    return StudyResult.VERIFICATION_REQUIRED
                if state == 403:
                    if forbidden_retry >= max_forbidden_retry:
                        logger.warning('媒体上报持续 HTTP 403，未发现可自动处理的验证码；请检查登录会话、访问权限与网络，章节保留待处理。')
                        return StudyResult.FORBIDDEN
                    forbidden_retry += 1
                    logger.warning(
                        "出现403报错, 正在尝试刷新会话状态 (第{}次)",
                        forbidden_retry,
                    )
                    controlled_wait(self, random.uniform(2, 4), cancel_only=True)
                    try:
                        refreshed_meta = self._recover_after_forbidden(_session, _job, _type)
                    except (RequestException, PlatformResponseError) as exc:
                        return resource_failure(exc)
                    if refreshed_meta and refreshed_meta.get("dtoken") and refreshed_meta.get(
                            "duration") is not None:
                        _dtoken = refreshed_meta["dtoken"]
                        duration = int(refreshed_meta["duration"])
                        refreshed_play_time = refreshed_meta.get("playTime")
                        if refreshed_play_time is not None:
                            play_time = min(duration, max(0, int(refreshed_play_time)))

                        logger.debug("刷新后的持续时间: {}, 播放时间: {}", duration, play_time)
                        next_report_at = time.monotonic()
                        end_reported = False
                        continue
                    else:
                        logger.warning('403 会话恢复失败，请重新登录并确认课程访问权限；章节保留待处理。')
                        return StudyResult.FORBIDDEN

                elif not passed and state != 200:
                    return StudyResult.ERROR

                if passed:
                    report_media(True)
                    logger.info("任务完成: {}", _job['name'])
                    return StudyResult.SUCCESS

                # Space completion confirmations too: five requests a second
                # apart give the platform only a few seconds to settle. Keep a
                # finite retry count, with one full card interval between them.
                next_report_at = time.monotonic() + report_interval
                if at_end:
                    end_reported = True
                    if not initial:
                        completion_attempts += 1
                    logger.info("任务「{}」时长已到达，平台尚未确认；结束确认 {}/5，上报类型=4，间隔={}s",
                                _job['name'], completion_attempts, report_interval)
                    if completion_attempts >= 5:
                        logger.warning("任务「{}」结束上报后平台仍未确认完成，已按 {}s 间隔核验，任务点保留待核验",
                                       _job['name'], report_interval)
                        return StudyResult.TIMEOUT

                logger.trace("Progress logged")

            report_media()
            controlled_wait(self, gc.THRESHOLD, cancel_only=True)

    def study_document(self, _course, _job, _job_info=None) -> StudyResult:
        """
        Study a document in Chaoxing platform.

        This method makes a GET request to fetch document information for a given course and job.

        Args:
            _course (dict): Dictionary containing course information with keys:
                - courseId: ID of the course
                - clazzId: ID of the class
            _job (dict): Dictionary containing job information with keys:
                - jobid: ID of the job
                - otherinfo: String containing node information
                - jtoken: Authentication token for the job

        Returns:
            requests.Response: Response object from the GET request

        Note:
            This method requires the following helper functions:
            - init_session(): To initialize a new session
            - get_timestamp(): To get current timestamp
            - re module for regular expression matching
        """
        node = re.search(r'nodeId_([^-&]+)', _job.get('otherinfo', ''))
        knowledgeid = (_job_info or {}).get('knowledgeid') or _job.get('knowledgeid') or (node.group(1) if node else '')
        if not knowledgeid:
            logger.warning('文档任务缺少章节 ID，保留待处理。')
            return StudyResult.ERROR
        return self._study_resource('document', _course, _job, knowledgeid)

    def _study_resource(self, endpoint, course, job, knowledgeid):
        checkpoint(self)
        if self._captcha_recovery.blocked:
            return StudyResult.VERIFICATION_REQUIRED
        if not job.get('jobid') or not job.get('jtoken') or not knowledgeid:
            logger.warning('资源任务缺少任务 ID、章节 ID 或访问令牌，保留待处理。')
            return StudyResult.ERROR
        try:
            data = resource_json(SessionManager.get_session().get(
                f'https://mooc1.chaoxing.com/ananas/job/{endpoint}',
                params={'jobid': job['jobid'], 'knowledgeid': knowledgeid,
                        'courseid': course['courseId'], 'clazzid': course['clazzId'],
                        'jtoken': job['jtoken'], '_dc': get_timestamp()}))
        except (RequestException, PlatformResponseError) as exc:
            return resource_failure(exc)
        if data.get('status') is True:
            return StudyResult.SUCCESS
        logger.warning('资源任务接口未确认成功，保留待核验。')
        return StudyResult.ERROR

    def study_work(self, _course, _job, _job_info) -> StudyResult:
        checkpoint(self)
        if self.tiku.DISABLE or not self.tiku:
            return StudyResult.SUCCESS

        _session = SessionManager.get_session()
        _url = "https://mooc1.chaoxing.com/mooc-ans/api/work"

        def is_not_permission_error(exception):
            return not isinstance(exception, PermissionError)

        @retry(
            stop=stop_after_attempt(3),
            wait=wait_fixed(1),
            retry=retry_if_exception(is_not_permission_error),
            reraise=True
        )
        def fetch_response_with_retry():
            _resp = _session.get(
                _url,
                params={
                    "api": "1",
                    "workId": _job["jobid"].replace("work-", ""),
                    "jobid": _job["jobid"],
                    "originJobId": _job["jobid"],
                    "needRedirect": "true",
                    "skipHeader": "true",
                    "knowledgeid": str(_job_info["knowledgeid"]),
                    "ktoken": _job_info["ktoken"],
                    "cpi": _job_info["cpi"],
                    "ut": "s",
                    "clazzId": _course["clazzId"],
                    "type": "",
                    "enc": _job["enc"],
                    "mooc2": "1",
                    "courseid": _course["courseId"],
                }
            )

            # 未创建完成该测验则不进行答题，目前遇到的情况是未创建完成等同于没题目
            if '教师未创建完成该测验' in _resp.text:
                raise PermissionError("教师未创建完成该测验")

            questions = decode_questions_info(_resp.text)

            if _resp.status_code == 200 and questions.get("questions"):
                return questions

            logger.warning(
                f"无效响应 (Code: {getattr(_resp, 'status_code', 'Unknown')}), 重试中...")
            raise RuntimeError(f"请求返回无效数据 (Code: {_resp.status_code})")

        # 章节检测最大重做次数（答错后收集错误反馈并重新提交，直到全对）
        # work_redo_enabled=false 时为单轮模式: 提交+检查+报告成绩, 不自动重做(适用于只允许作答一次的课程)
        if bool(self.kwargs.get("work_redo_enabled", False)):
            try:
                max_retries = max(1, int(self.kwargs.get("work_max_retries", 3)))
            except (TypeError, ValueError):
                max_retries = 3
        else:
            max_retries = 0
        query_delay = self.kwargs.get("query_delay", 0)
        feedback_history = None
        last_submitted_score = None

        for attempt in range(max_retries + 1):
            checkpoint(self)
            if attempt > 0:
                logger.warning(
                    f"章节检测重做第 {attempt}/{max_retries} 轮，携带上一轮错误反馈重新作答")
                time.sleep(2)

            # 1. 获取题目
            questions = {}
            try:
                questions = fetch_response_with_retry()
            except PermissionError as e:
                logger.warning(f"跳过章节检测: {e}")
                return StudyResult.SUCCESS
            except Exception as e:
                if attempt > 0:
                    logger.error(f"重做轮无法获取题目(上一轮提交成绩 {last_submitted_score} 分), "
                                 f"该课程可能仅允许作答一次: {e}")
                else:
                    logger.error(f"获取章节检测题目失败, 达到最大重试次数: {e}")
                return StudyResult.ERROR

            # 2. 设置上一轮错误反馈（供AI重新作答时参考）
            if feedback_history and hasattr(self.tiku, 'set_work_feedback'):
                try:
                    self.tiku.set_work_feedback(feedback_history)
                    logger.debug("已将上一轮错误反馈设置到题库")
                except Exception as e:
                    logger.warning(f"设置题库错误反馈失败: {e}")

            # 3. 搜题
            total_questions = len(questions["questions"])
            found_answers = 0
            filled_answers = 0
            random_answers = 0
            if hasattr(self.tiku, 'set_context'):
                self.tiku.set_context(attempt=attempt)
            # A dict form cannot represent colliding keys from different
            # questions. Reject the complete affected layouts before adoption.
            owners = {}
            for q in questions['questions']:
                if q['type'] == 'completion':
                    q.pop('desktop_completion_parts', None)
                    for field in q.get('completion_fields', []):
                        owners.setdefault(field, []).append(q)
            for group in owners.values():
                if len(group) > 1:
                    for q in group:
                        q['completion_layout'] = dict(supported=False, reason='多题填空字段重名，无法安全序列化')
            answers = self.tiku.query_all(questions["questions"], query_delay=query_delay)

            if not isinstance(answers, list):
                logger.error("题库 query_all 返回的数据格式异常，期望列表；本轮答案保留为空。")
                answers = [None] * total_questions
            elif len(answers) != total_questions:
                logger.error(
                    f"题库返回的答案数量（{len(answers)}）与题目数量（{total_questions}）不匹配，正在补齐或截断以防错位！")
                answers = list(answers) + [None] * (total_questions - len(answers))
                answers = answers[:total_questions]

            for q, res in zip(questions["questions"], answers):
                logger.debug(f"当前题目信息 -> {q}")
                answer = ""
                # Redo/reused page objects must never retain stale blank values.
                if q['type'] == 'completion':
                    for field in q.get('completion_fields', []):
                        q['answerField'][field] = ''
                if not res:
                    # Desktop keeps unresolved questions empty for review.
                    answer = ""
                    q[f'answerSource{q["id"]}'] = "missing"
                else:
                    # 根据响应结果选择答案
                    if q["type"] in ("single", "multiple"):
                        # The desktop router already validated the exact options.
                        # Fuzzy rematching can change B into A for overlapping text.
                        answer = self.tiku.choice_letters(res, q)
                    elif q["type"] == "judgement":
                        answer = "true" if self.tiku.judgement_select(res) else "false"
                    elif q["type"] == "completion":
                        from chaoxing_desktop.completion import completion_parts, layout_error
                        fields = q.get('completion_fields', [])
                        parts = completion_parts(q.get('desktop_completion_parts', res), q.get('blank_count'))
                        if parts and len(fields) == len(parts) and len(set(fields)) == len(fields) and not layout_error(q):
                            q['answerField'].update(zip(fields, parts))
                            answer = parts[0] if len(parts) == 1 and fields[0] == f'answer{q["id"]}' else '已按空位填写'
                        else:
                            answer = ''
                            logger.warning('填空字段或数量无法核验，保留待处理')
                    else:
                        # 其他类型直接使用答案 （目前仅知有简答题，待补充处理）
                        answer = res

                    if not answer:  # 检查 answer 是否为空
                        logger.warning("找到答案但答案未能匹配，此题保留待处理")
                        answer = ""
                        q[f'answerSource{q["id"]}'] = "missing"
                    else:
                        logger.info(f"成功获取到答案：{answer}")
                        filled_answers += 1
                        if q.get("desktop_answer_source") == "random":
                            q[f'answerSource{q["id"]}'] = "random"
                            random_answers += 1
                        else:
                            q[f'answerSource{q["id"]}'] = "cover"
                            found_answers += 1
                # 填充答案
                q["answerField"][f'answer{q["id"]}'] = answer if q['type'] != 'completion' or q.get('completion_fields') == [f'answer{q["id"]}'] else ''
                logger.info(f'{q["title"]} 填写答案为 {answer}')
            cover_rate = (found_answers / total_questions) * 100
            logger.info(f"章节检测题库覆盖率： {cover_rate:.0f}%")
            if random_answers:
                logger.info(f"本章节随机填入 {random_answers} 道答案（不计入题库覆盖率）")
            # 提交模式  现在与题库绑定,留空直接提交, 1保存但不提交
            if self.tiku.get_submit_params() == "1":
                questions["pyFlag"] = "1"
            elif filled_answers == total_questions and (cover_rate >= self.tiku.COVER_RATE * 100 or getattr(self.tiku, "RANDOM_FALLBACK", False)):
                questions["pyFlag"] = ""
            else:
                questions["pyFlag"] = "1"
                logger.info("仍有未解决题目，已保存可用答案，等待人工处理")
            checkpoint(self)
            # Copy only actual named completion fields; never invent an empty
            # aggregate answer{id} alongside a different observed protocol.
            for q in questions['questions']:
                questions[f'answertype{q["id"]}'] = q['answerField'][f'answertype{q["id"]}']
                if q['type'] == 'completion':
                    from chaoxing_desktop.completion import layout_error
                    if not layout_error(q):
                        for field in q.get('completion_fields', []):
                            questions[field] = q['answerField'].get(field, '')
                else:
                    questions[f'answer{q["id"]}'] = q['answerField'][f'answer{q["id"]}']

            del questions["questions"]

            # 4. 提交
            checkpoint(self)
            res = _session.post(
                "https://mooc1.chaoxing.com/mooc-ans/work/addStudentWorkNew",
                data=questions,
                headers={
                    "Host": "mooc1.chaoxing.com",
                    "sec-ch-ua-platform": '"Windows"',
                    "X-Requested-With": "XMLHttpRequest",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "sec-ch-ua": '"Microsoft Edge";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "sec-ch-ua-mobile": "?0",
                    "Origin": "https://mooc1.chaoxing.com",
                    "Sec-Fetch-Site": "same-origin",
                    "Sec-Fetch-Mode": "cors",
                    "Sec-Fetch-Dest": "empty",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,ja;q=0.5",
                },
            )
            if res.status_code == 200:
                try:
                    res_json = res.json()
                    if not isinstance(res_json, dict) or type(res_json.get('status')) is not bool:
                        raise ValueError
                except (ValueError, TypeError):
                    logger.error('答题保存／提交响应不可读，平台结果未确认；答案采用不代表平台保存成功')
                    return StudyResult.ERROR
                if res_json['status']:
                    logger.info(f'{"提交" if questions["pyFlag"] == "" else "保存"}答题成功 -> {res_json.get("msg", "平台已确认")}')
                else:
                    logger.error(f'{"提交" if questions["pyFlag"] == "" else "保存"}答题失败 -> {res_json.get("msg", "平台拒绝")}')
                    return StudyResult.ERROR
            else:
                logger.error(f'{"提交" if questions["pyFlag"] == "" else "保存"}答题失败 -> {res.text}')
                return StudyResult.ERROR

            # 5. 若只是保存未提交，无法判断成绩，保持原有行为
            if questions["pyFlag"] == "1":
                return StudyResult.SUCCESS

            # 6. 提交后检查成绩：若未全部正确，则收集错误反馈并重新作答提交
            result_info = self._check_work_result(_session, _course, _job, _job_info, questions)
            if result_info is None:
                logger.warning('平台已接受提交，但成绩读取未确认；请在平台核对成绩与章节状态。')
                # 无法获取成绩详情（如接口异常），按原行为返回成功，避免误判失败
                return StudyResult.SUCCESS

            score = float(result_info.get("score", 0.0) or 0.0)
            unknown = int(result_info.get("unknown", 0) or 0)
            if result_info.get("all_correct", False):
                logger.info(f"章节检测全部正确（成绩 {score} 分），通过！")
                return StudyResult.SUCCESS
            if score >= 99.95:
                # 逐题比对存在格式差异或解析失配, 但满分成绩说明实际全对, 以分数为准
                if unknown > 0:
                    logger.warning(f"成绩 {score} 分达到满分, 以分数为准通过"
                                   f"（{unknown} 题答案字段未能解析, 建议人工复核）")
                else:
                    logger.info(f"逐题比对存在格式差异, 但成绩 {score} 分达到满分, 以分数为准, 通过！")
                return StudyResult.SUCCESS
            if unknown > 0:
                # 解析失配且未达满分: 判定不可信, 不触发重做, 保留已提交成绩
                logger.warning(f"{unknown} 题答案字段解析失败且成绩 {score} 分未达满分, 跳过重做判定")
                return StudyResult.SUCCESS

            # 7. 未全对：收集错误反馈，进入下一轮重做
            feedback_history = result_info.get("feedback", [])
            wrong_count = len(feedback_history)
            if attempt >= max_retries:
                # 已是最后一轮(含单轮模式), 保留已提交成绩, 不再触发无意义的重做
                logger.warning(f"章节检测有 {wrong_count}/{total_questions} 题回答错误（成绩 {score} 分），"
                               f"已保留本次成绩, 请人工检查处理")
                return StudyResult.SUCCESS
            logger.warning(
                f"章节检测有 {wrong_count}/{total_questions} 题回答错误（成绩 {score} 分），"
                f"已将错误反馈给AI，准备重新作答提交 (第 {attempt + 1}/{max_retries + 1} 轮)"
            )
            last_submitted_score = score

        # 达到最大重试次数仍未全对
        logger.error(f"章节检测重试 {max_retries + 1} 次仍未全部正确，请人工检查处理")
        return StudyResult.ERROR

    def _check_work_result(self, _session, _course, _job, _job_info, questions) -> Optional[dict]:
        """
        章节检测提交后，查询最新一次作答的成绩与对错详情，供判断是否需要重做。

        Args:
            _session: 当前会话
            _course: 课程信息
            _job: 任务点信息
            questions: 提交时使用的表单数据（含 workId / workAnswerId 等）

        Returns:
            {"all_correct": bool, "feedback": list[str], "score": float, "times": int}
            或 None（无法获取成绩详情时返回 None）
        """
        work_id = str(
            questions.get("workId", "")
            or questions.get("workRelationId", "")
            or _job["jobid"].replace("work-", "")
        )
        work_answer_id = str(questions.get("workAnswerId", "") or "")
        course_id = str(_course.get("courseId", ""))
        class_id = str(_course.get("clazzId", ""))
        cpi = str(_course.get("cpi", "") or questions.get("cpi", ""))

        # 1. 获取作答记录列表（提交后服务端异步生成记录，需稍作等待并多次重试）
        records = None
        for attempt in range(5):
            try:
                resp = _session.get(
                    "https://mooc1.chaoxing.com/mooc-ans/work/record-list",
                    params={
                        "courseId": course_id,
                        "classId": class_id,
                        "workId": work_id,
                        "workAnswerId": work_answer_id,
                        "cpi": cpi,
                        "api": "1",
                        "mooc2": "1",
                        "ut": "s",
                    },
                    timeout=20,
                )
                records = _parse_work_record_list(resp.text)
                if records:
                    break
            except Exception as e:
                logger.warning(f"获取章节检测作答记录失败 (第{attempt + 1}次): {e}")
            if attempt < 4:
                time.sleep(1.5)

        if not records:
            # 兜底：重新访问题目页判断是否已通过（详情页=已提交有成绩；可编辑页=未通过可重做）
            logger.warning("无法获取章节检测作答记录，尝试通过题目页状态判断")
            try:
                resp = _session.get(
                    "https://mooc1.chaoxing.com/mooc-ans/api/work",
                    params={
                        "api": "1",
                        "workId": _job["jobid"].replace("work-", ""),
                        "jobid": _job["jobid"],
                        "originJobId": _job["jobid"],
                        "needRedirect": "true",
                        "skipHeader": "true",
                        "knowledgeid": str(_job_info.get("knowledgeid", "") or _job.get("knowledgeid", "")),
                        "ktoken": str(_job_info.get("ktoken", "") or _job.get("ktoken", "")),
                        "cpi": str(_job_info.get("cpi", "") or _job.get("cpi", "") or cpi),
                        "ut": "s",
                        "clazzId": class_id,
                        "type": "",
                        "enc": str(_job.get("enc", "")),
                        "mooc2": "1",
                        "courseid": course_id,
                    },
                    timeout=20,
                )
                html = resp.text
                if 'answerwqbid' in html:
                    # 可编辑页面：说明未全部正确，可重新作答（该判定为页面状态推断, 未核对实际成绩）
                    logger.warning("题目页仍可编辑, 推断章节检测未全部正确(未核对成绩)")
                    return {
                        "all_correct": False,
                        "feedback": [],
                        "score": 0.0,
                        "times": 0,
                    }
                elif '正确答案' in html and '我的答案' in html:
                    # 已提交详情页：解析成绩与对错
                    detail = _parse_work_record_detail(html)
                    if detail:
                        feedback = []
                        all_correct = True
                        unknown = 0
                        for q in detail:
                            if not q.get("parse_ok", True):
                                unknown += 1
                                all_correct = False
                                continue
                            my_ans = (q.get("my_answer") or "").strip()
                            correct_ans = (q.get("correct_answer") or "").strip()
                            if normalize_answer_text(my_ans) != normalize_answer_text(correct_ans):
                                all_correct = False
                                feedback.append(
                                    f"- 题目：{q.get('title', '')}\n"
                                    f"  题型：{q.get('type_label', '')}\n"
                                    f"  你的上次答案：{my_ans or '(空)'}\n"
                                    f"  正确答案：{correct_ans or '(空)'}"
                                )
                        m = re.search(r'本次成绩<i>([\d.]+)</i>分', html)
                        score = float(m.group(1)) if m else 0.0
                        return {
                            "all_correct": all_correct,
                            "feedback": feedback,
                            "score": score,
                            "times": 0,
                            "unknown": unknown,
                        }
                return None
            except Exception as e:
                logger.warning(f"兜底判断章节检测状态失败: {e}")
                return None

        latest_times = max(r[0] for r in records)
        latest_score = dict(records).get(latest_times, 0.0)

        # 2. 获取最新一次作答详情（含每道题对错与正确答案）
        try:
            resp = _session.get(
                "https://mooc1.chaoxing.com/mooc-ans/work/record-detail",
                params={
                    "courseId": course_id,
                    "classId": class_id,
                    "workId": work_id,
                    "workAnswerId": work_answer_id,
                    "times": str(latest_times),
                    "cpi": cpi,
                    "ut": "s",
                    "isdisplaytable": "0",
                    "firstHeader": "2",
                    "isWork": "false",
                    "workSystem": "0",
                    "api": "1",
                    "archive": "false",
                    "mooc2": "1",
                },
                timeout=20,
            )
            detail = _parse_work_record_detail(resp.text)
        except Exception as e:
            logger.warning(f"获取章节检测作答详情失败: {e}")
            return None

        if not detail:
            logger.warning("章节检测作答详情解析为空，跳过成绩检查")
            return None

        # 3. 逐题判断对错，收集错误反馈（解析失配的题跳过并计数, 避免误判全错）
        feedback = []
        all_correct = True
        unknown = 0
        for q in detail:
            if not q.get("parse_ok", True):
                unknown += 1
                all_correct = False
                continue
            my_ans = (q.get("my_answer") or "").strip()
            correct_ans = (q.get("correct_answer") or "").strip()
            if normalize_answer_text(my_ans) != normalize_answer_text(correct_ans):
                all_correct = False
                feedback.append(
                    f"- 题目：{q.get('title', '')}\n"
                    f"  题型：{q.get('type_label', '')}\n"
                    f"  你的上次答案：{my_ans or '(空)'}\n"
                    f"  正确答案：{correct_ans or '(空)'}"
                )

        logger.debug(f"章节检测成绩: {latest_score} 分, 全部正确: {all_correct}, 错题数: {len(feedback)}, 未解析题数: {unknown}")
        return {
            "all_correct": all_correct,
            "feedback": feedback,
            "score": latest_score,
            "times": latest_times,
            "unknown": unknown,
        }

    def study_read(self, _course, _job, _job_info) -> StudyResult:
        """
        阅读任务学习, 仅完成任务点, 并不增长时长
        """
        return self._study_resource('readv2', _course, _job, _job_info.get('knowledgeid'))

    def _send_monitor_heartbeat(self, course, point):
        """
        发送章节监控心跳包到 detect.chaoxing.com。

        模拟真实浏览器的 JSONP 打点请求，佐证访问行为的真人属性。

        Args:
            course: 课程信息字典
            point: 当前章节信息字典
        """
        version = get_timestamp()
        callback = f"jsonp{secrets.randbelow(10**21 - 10**20) + 10**20}"
        params = {
            "version": version,
            "refer": "http://i.mooc.chaoxing.com",
            "from": "",
            "fid": self.get_fid(),
            "jsoncallback": callback,
            "t": get_timestamp(),
        }
        referer_url = (
            f"https://mooc1.chaoxing.com/mycourse/studentstudy?"
            f"chapterId={point['id']}&courseId={course['courseId']}"
            f"&clazzid={course['clazzId']}&cpi={course['cpi']}&mooc2=1"
        )
        try:
            session = SessionManager.get_session()
            resp = session.get(
                "https://detect.chaoxing.com/api/monitor",
                params=params,
                headers={"Referer": referer_url},
                timeout=5,
            )
            logger.trace(f"Monitor heartbeat sent -> {resp.status_code}")
        except Exception as e:
            logger.trace(f"Monitor heartbeat failed (non-critical): {e}")

    def study_emptypage(self, _course, point):
        _session = SessionManager.get_session()
        # &cpi=0&verificationcode=&mooc2=1&microTopicId=0&editorPreview=0
        _resp = _session.get(
            url="https://mooc1.chaoxing.com/mooc-ans/mycourse/studentstudyAjax",
            params={
                "courseId": _course["courseId"],
                "clazzid": _course["clazzId"],
                "chapterId": point["id"],
                "cpi": _course["cpi"],
                "verificationcode": "",
                "mooc2": 1,
                "microTopicId": 0,
                "editorPreview": 0,
            },
            timeout=8,
        )
        if _resp.status_code != 200:
            logger.error(f"空页面任务失败 -> [{_resp.status_code}]{point['title']}")
            return StudyResult.ERROR
        else:
            logger.info(f"空页面任务完成 -> {point['title']}")
            return StudyResult.SUCCESS

    def _access_chapter_for_count(self, _course, point):
        _session = SessionManager.get_session()
        # &cpi=0&verificationcode=&mooc2=1&microTopicId=0&editorPreview=0
        _resp = _session.get(
            url="https://mooc1.chaoxing.com/mooc-ans/mycourse/studentstudyAjax",
            params={
                "courseId": _course["courseId"],
                "clazzid": _course["clazzId"],
                "chapterId": point["id"],
                "cpi": _course["cpi"],
                "verificationcode": "",
                "mooc2": 1,
                "microTopicId": 0,
                "editorPreview": 0,
            },
            timeout=8,
        )
        if _resp.status_code != 200:
            logger.error(f"章节访问失败 -> [{_resp.status_code}]{point['title']}")
            return None
        else:
            logger.info(f"章节访问成功 -> {point['title']}")
            return _resp.text

    def _extract_and_send_setlog(self, html_text):
        """
        从 studentstudyAjax 返回的 HTML 中提取 setlog URL 并执行。

        该 URL 包含服务端生成的 encode 参数，是记录章节学习次数的关键 API。

        Args:
            html_text: studentstudyAjax 返回的 HTML 内容
        """
        match = re.search(
            r'<script[^>]+src="(https://fystat-ans\.chaoxing\.com/log/setlog[^"]+)"',
            html_text
        )
        if not match:
            logger.trace("未在响应中找到 setlog URL")
            return

        setlog_url = match.group(1)
        try:
            session = SessionManager.get_session()
            resp = session.get(setlog_url, timeout=5)
            logger.trace(f"Setlog sent -> {resp.status_code}")
        except Exception as e:
            logger.trace(f"Setlog failed (non-critical): {e}")

    def increase_chapter_learning_count(self, course, points, target_count):
        """
        增加课程章节学习次数。

        循环遍历课程的所有章节，每访问一个章节页面：
        1. 调 studentstudyAjax 获取页面 HTML（含服务端生成的 setlog URL）
        2. 提取并执行 setlog URL（记录学习次数）
        3. 立即发送 monitor 心跳包（模拟 fn() 首次心跳）
        4. 停留 30 秒（模拟前端 setInterval(fn, 30000) 的间隔）
        5. 再次发送 monitor 心跳包（模拟 30s 后的第二次心跳）
        6. 计数器 +1，继续下一个章节

        Args:
            course: 课程信息字典
            points: 课程所有章节列表
            target_count: 目标总次数

        Returns:
            StudyResult: 操作结果
        """
        total = 0
        consecutive_failures = 0
        max_consecutive_failures = 10
        logger.info(f"开始增加章节学习次数, 目标总次数: {target_count}, 章节数: {len(points)}")
        if not points:
            logger.warning("章节列表为空, 跳过章节学习次数增加")
            return StudyResult.SUCCESS
        while total < target_count:
            checkpoint(self)
            for point in points:
                checkpoint(self)
                if total >= target_count:
                    break
                self.rate_limiter.limit_rate(random_time=True, random_min=0, random_max=0.2)
                html_text = self._access_chapter_for_count(course, point)
                if not html_text:
                    logger.error(f"章节学习次数增加失败, 当前章节: {point['title']}")
                    consecutive_failures += 1
                    if consecutive_failures >= max_consecutive_failures:
                        logger.error(
                            f"章节学习次数增加连续失败 {consecutive_failures} 次, 终止任务"
                        )
                        return StudyResult.ERROR
                    continue

                consecutive_failures = 0

                # 第 1 步：从 HTML 中提取 setlog URL 并执行（真正的计次 API）
                checkpoint(self)
                self._extract_and_send_setlog(html_text)

                # 第 2 步：立即发送 monitor 心跳包（模拟 fn()）
                checkpoint(self)
                self._send_monitor_heartbeat(course, point)

                # 第 3 步：停留 30 秒（模拟前端 setInterval 间隔）
                controlled_wait(self, 30)

                # 第 4 步：再次发送 monitor 心跳包（模拟 setInterval 触发的第二次心跳）
                checkpoint(self)
                self._send_monitor_heartbeat(course, point)

                total += 1
                logger.info(f"章节学习次数进度: {total}/{target_count}")
        logger.info(f"章节学习次数增加完成, 共完成: {total} 次")
        return StudyResult.SUCCESS
