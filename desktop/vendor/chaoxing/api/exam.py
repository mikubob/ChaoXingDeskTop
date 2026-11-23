"""超星独立考试（exam-ans / mooc2）HTTP 协议适配层。

只负责受信主机校验、只读读取、题目解析、0 重试保存／交卷与只读核验；
不写本地草稿，不发 UI 事件，不绕过验证码或监考限制。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from requests.exceptions import RequestException

from api.font_decoder import FontDecoder
from api.responses import PlatformResponseError, checked_page
from chaoxing_desktop.text import plain_text

HOSTS = {"mooc1.chaoxing.com", "mooc1-api.chaoxing.com", "mooc2-ans.chaoxing.com"}
MAX_EXAM_QUESTIONS = 200
SUBMIT_PATH = "/exam-ans/exam/test/reVersionSubmitTestNew"
EXAM_LIST_URL = "https://mooc1.chaoxing.com/exam-ans/mooc2/exam/exam-list"
EXAM_NOTES_URL = "https://mooc1.chaoxing.com/exam-ans/exam/test/examcode/examnotes"
EXAM_LOOK_URL = "https://mooc1-api.chaoxing.com/exam-ans/exam/test/look"
COURSE_MIDDLE_URL = "https://mooc1.chaoxing.com/visit/stucoursemiddle"

_GO_TEST_RE = re.compile(
    r"goTest\(\s*'?([^'\s),]*)'?\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*'([^']*)'\s*,\s*(\d+)\s*,\s*(true|false)\s*,\s*'([^']*)'\s*,\s*'?([^'\s),]*)'?\s*\)"
)
_TEST_RELATION_INFO_RE = re.compile(r"TestRelationInfo\s*=\s*(\{.*?\})\s*;", re.S)


def trusted_url(url: str) -> str:
    """校验 URL 仅使用 HTTPS 且位于已核实的超星考试主机白名单内。"""
    p = urlsplit(str(url or "").strip())
    if (
        p.scheme != "https"
        or p.hostname not in HOSTS
        or p.username
        or p.password
        or p.port not in (None, 443)
    ):
        raise PlatformResponseError("考试跳转地址不在已核实的超星主机范围。")
    return url


def _hidden_value(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        node = soup.find("input", attrs={"id": name}) or soup.find("input", attrs={"name": name})
        if node is not None:
            return str(node.get("value") or "").strip()
    return ""


def _truthy_flag(value: str) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes")


def _any_flag(soup: BeautifulSoup, *names: str) -> bool:
    return any(_truthy_flag(_hidden_value(soup, name)) for name in names)


def _extract_server_times(soup: BeautifulSoup) -> tuple[str, str]:
    """从页面隐藏域提取服务端当前时间与截止时间（UTC ISO-8601）。"""
    enter_ms_raw = _hidden_value(soup, "enterPageTime", "receiveTime")
    remain_raw = _hidden_value(soup, "remainTime")
    if not enter_ms_raw.isdigit():
        return "", ""
    try:
        server_now = datetime.fromtimestamp(int(enter_ms_raw) / 1000.0, tz=timezone.utc)
        remain_sec = int(remain_raw) if remain_raw.lstrip("-").isdigit() else 0
        deadline = server_now + timedelta(seconds=max(0, remain_sec))
        return server_now.isoformat(timespec="seconds"), deadline.isoformat(timespec="seconds")
    except (ValueError, OverflowError, OSError):
        return "", ""


def _decode_cxsecret(html_text: str, soup: BeautifulSoup) -> None:
    """若页面包含加密字体，原地替换 .font-cxsecret 节点文本。"""
    nodes = soup.select(".font-cxsecret")
    if not nodes:
        return
    try:
        decoder = FontDecoder(html_text)
    except Exception:
        return
    for node in nodes:
        raw = node.get_text()
        if not raw:
            continue
        try:
            decoded = decoder.decode(raw)
        except Exception:
            continue
        node.string = decoded


def _normalize_judgement_value(raw_val: str, text: str) -> str:
    val = str(raw_val or "").strip().lower()
    txt = plain_text(text)
    if val in ("true", "1", "ri", "right", "正确", "对", "√") or txt in ("对", "正确", "√", "T", "True"):
        return "正确"
    if val in ("false", "0", "wr", "wrong", "错误", "错", "×") or txt in ("错", "错误", "×", "F", "False"):
        return "错误"
    return ""


def _parse_single_question(q_node, index: int, base_url: str) -> tuple[dict, dict | None, dict, list[str], str]:
    """解析单道题目节点，返回 (question_dict, answer_dict, form_info, image_urls, unsupported_reason)。"""
    qid = str(q_node.get("data") or "").strip()
    if not qid:
        qid_inp = q_node.find("input", attrs={"name": "questionId"})
        if qid_inp:
            qid = str(qid_inp.get("value") or "").strip()
    if not qid:
        return {}, None, {}, [], "题目缺少原生标识，页面结构未适配。"

    title_node = q_node.select_one("h3.mark_name") or q_node.select_one(".mark_name")
    if not title_node:
        return {}, None, {}, [], "题目缺少题干结构，页面结构未适配。"

    # 提取分值与题型提示
    shallow = title_node.select_one("span.colorShallow")
    shallow_text = plain_text(shallow.get_text()) if shallow else ""
    score = ""
    score_m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*分", shallow_text)
    if score_m:
        score = score_m.group(1)

    # 题型编号（type{qid} 隐藏域）
    type_inp = q_node.find("input", attrs={"name": f"type{qid}"})
    raw_type = str(type_inp.get("value") or "").strip() if type_inp else ""

    # 收集题干图片
    image_urls = []
    for img in title_node.select("img"):
        src = str(img.get("src") or "").strip()
        if src:
            abs_src = urljoin(base_url, src)
            if abs_src.startswith("https://"):
                image_urls.append(abs_src)

    # 剥离题号前缀与 (题型, 分数) 标签以获得干净题干
    title_clone = BeautifulSoup(str(title_node), "lxml")
    for span in title_clone.select("span.colorShallow"):
        span.decompose()
    raw_title = plain_text(title_clone.get_text(" ", strip=True))
    raw_title = re.sub(r"^\d+\s*[.、．]\s*", "", raw_title).strip()
    if not raw_title:
        raw_title = f"第 {index} 题"

    stem = q_node.select_one(".stem_answer")
    if stem is None:
        return {}, None, {}, [], f"第 {index} 题缺少作答区域，页面结构未适配。"

    # 判定具体题型
    single_spans = stem.select("span.addChoice")
    multi_spans = stem.select("span.addMultipleChoice")
    blank_editors = stem.select(f"textarea[name^='answerEditor{qid}']")
    blank_inputs = [
        inp for inp in stem.select(f"input[name^='answer{qid}']")
        if inp.get("type", "text") in ("text", "")
    ]
    short_textarea = stem.select_one(f"textarea[name='answer{qid}']")

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    answer_obj = None
    form_info = {"type": raw_type, "answer_name": f"answer{qid}", "option_map": {}, "raw_option_values": []}

    if raw_type == "11" or stem.select(".line_answer"):
        return {}, None, {}, [], f"第 {index} 题为连线题，暂不支持原生作答。"
    if stem.select("input[type='file']"):
        return {}, None, {}, [], f"第 {index} 题包含文件上传控件，暂不支持原生作答。"

    is_judgement = ("判断" in shallow_text) or raw_type == "3"
    if multi_spans or raw_type == "1":
        qtype = "multiple"
        options = []
        for sp in multi_spans:
            oid = str(sp.get("data") or "").strip()
            p_node = sp.find_next_sibling(class_="answer_p") or sp.parent.select_one(".answer_p")
            otext = plain_text(p_node.get_text(" ", strip=True)) if p_node else oid
            if not oid:
                return {}, None, {}, [], f"第 {index} 题多选选项缺少标识。"
            options.append({"id": oid, "text": otext})
        if not options:
            return {}, None, {}, [], f"第 {index} 题多选选项为空，页面结构未适配。"
        ans_inp = q_node.find("input", attrs={"name": f"answers{qid}"}) or q_node.find("input", attrs={"name": f"answer{qid}"})
        form_info["answer_name"] = str(ans_inp.get("name")) if ans_inp else f"answers{qid}"
        saved_raw = str(ans_inp.get("value") or "").strip() if ans_inp else ""
        selected_ids = [opt["id"] for opt in options if opt["id"] in saved_raw] if saved_raw else []
        if selected_ids:
            answer_obj = {"option_ids": selected_ids, "text": "", "blanks": [], "source": "platform", "updated_at": now_iso}
        q_dict = {"id": qid, "no": index, "type": qtype, "title": raw_title, "options": options, "blank_count": 0}

    elif is_judgement and single_spans:
        qtype = "judgement"
        options = []
        option_map = {}
        reverse_map = {}
        for sp in single_spans:
            raw_oid = str(sp.get("data") or "").strip()
            p_node = sp.find_next_sibling(class_="answer_p") or sp.parent.select_one(".answer_p")
            otext = plain_text(p_node.get_text(" ", strip=True)) if p_node else raw_oid
            norm = _normalize_judgement_value(raw_oid, otext)
            if not norm:
                return {}, None, {}, [], f"第 {index} 题判断题选项无法映射：{otext}"
            options.append({"id": norm, "text": norm})
            option_map[norm] = raw_oid
            reverse_map[raw_oid] = norm
        form_info["option_map"] = option_map
        ans_inp = q_node.find("input", attrs={"name": f"answer{qid}"})
        saved_raw = str(ans_inp.get("value") or "").strip() if ans_inp else ""
        if saved_raw:
            mapped = reverse_map.get(saved_raw) or _normalize_judgement_value(saved_raw, "")
            if mapped:
                answer_obj = {"option_ids": [mapped], "text": "", "blanks": [], "source": "platform", "updated_at": now_iso}
        q_dict = {"id": qid, "no": index, "type": qtype, "title": raw_title, "options": options, "blank_count": 0}

    elif single_spans or raw_type == "0":
        qtype = "single"
        options = []
        for sp in single_spans:
            oid = str(sp.get("data") or "").strip()
            p_node = sp.find_next_sibling(class_="answer_p") or sp.parent.select_one(".answer_p")
            otext = plain_text(p_node.get_text(" ", strip=True)) if p_node else oid
            if not oid:
                return {}, None, {}, [], f"第 {index} 题单选选项缺少标识。"
            options.append({"id": oid, "text": otext})
        if not options:
            return {}, None, {}, [], f"第 {index} 题单选选项为空，页面结构未适配。"
        ans_inp = q_node.find("input", attrs={"name": f"answer{qid}"})
        saved_raw = str(ans_inp.get("value") or "").strip() if ans_inp else ""
        if saved_raw and any(o["id"] == saved_raw for o in options):
            answer_obj = {"option_ids": [saved_raw], "text": "", "blanks": [], "source": "platform", "updated_at": now_iso}
        q_dict = {"id": qid, "no": index, "type": qtype, "title": raw_title, "options": options, "blank_count": 0}

    elif blank_editors or blank_inputs or raw_type in ("2", "9", "10"):
        qtype = "completion"
        fields = blank_editors or blank_inputs
        if not fields:
            return {}, None, {}, [], f"第 {index} 题填空题缺少输入框，页面结构未适配。"
        field_names = [str(f.get("name") or "") for f in fields]
        if any(not n for n in field_names):
            return {}, None, {}, [], f"第 {index} 题填空输入框缺少字段名。"
        form_info["blank_names"] = field_names
        blanks = []
        for f in fields:
            val = plain_text(f.get_text()) if f.name == "textarea" else str(f.get("value") or "")
            blanks.append(val)
        if any(b.strip() for b in blanks):
            answer_obj = {"option_ids": [], "text": "", "blanks": blanks, "source": "platform", "updated_at": now_iso}
        q_dict = {"id": qid, "no": index, "type": qtype, "title": raw_title, "options": [], "blank_count": len(fields)}

    elif short_textarea is not None or raw_type in ("4", "5", "6", "7", "8"):
        if short_textarea is None:
            return {}, None, {}, [], f"第 {index} 题简答题缺少文本域，页面结构未适配。"
        qtype = "shortanswer"
        val = short_textarea.get_text() or ""
        if val.strip():
            answer_obj = {"option_ids": [], "text": val.strip(), "blanks": [], "source": "platform", "updated_at": now_iso}
        q_dict = {"id": qid, "no": index, "type": qtype, "title": raw_title, "options": [], "blank_count": 0}

    else:
        return {}, None, {}, [], f"第 {index} 题为未适配题型（type={raw_type or 'unknown'}）。"

    if score:
        q_dict["score"] = score
    return q_dict, answer_obj, form_info, image_urls, ""


class ExamAdapter:
    """超星独立考试协议适配器。"""

    def read(self, session: requests.Session, url: str, params: dict | None = None, *,
             retry_throttled: bool = False) -> tuple[str, str]:
        """沿受信重定向链执行只读 GET，拒绝非白名单跳转并识别会话失效。"""
        current = trusted_url(url)
        referer = "https://mooc1.chaoxing.com/"
        throttled = 0
        redirects = 0
        while redirects < 6:
            response = session.get(
                current,
                params=params,
                allow_redirects=False,
                timeout=20,
                headers={
                    "Referer": referer,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
            )
            if retry_throttled and response.status_code in (202, 429):
                response.close()
                if throttled < 3:
                    throttled += 1
                    time.sleep(2.0 * (2 ** (throttled - 1)))
                    continue
                raise PlatformResponseError(
                    f"平台请求过于频繁（HTTP {response.status_code}），请等待约 30 秒后再重试。",
                    "rate_limited",
                )
            params = None
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location", "")
                response.close()
                destination = urljoin(current, location)
                if urlsplit(destination).hostname == "passport2.chaoxing.com":
                    raise PlatformResponseError("考试会话已失效，请重新登录。", "auth_error")
                current = trusted_url(destination)
                referer = current
                redirects += 1
                continue
            try:
                return checked_page(response, "cards"), trusted_url(str(response.url or current))
            finally:
                response.close()
        raise PlatformResponseError("考试页面重定向次数超过上限。")

    def list_exams(self, session: requests.Session, courses: list[dict]) -> list[dict]:
        """按课程读取考试安排列表，返回标准化考试条目。"""
        exams: list[dict] = []
        seen_keys: set[tuple[str, str, str]] = set()
        for idx, course in enumerate(courses):
            if idx > 0:
                time.sleep(0.3)
            cid = str(course.get("courseId") or "").strip()
            clazz_id = str(course.get("clazzId") or "").strip()
            cpi = str(course.get("cpi") or "").strip()
            course_title = str(course.get("title") or "").strip()
            if not (cid and clazz_id and cpi):
                continue
            mid_text, _ = self.read(
                session,
                COURSE_MIDDLE_URL,
                params={"courseid": cid, "clazzid": clazz_id, "cpi": cpi, "ismooc2": "1", "v": "2"},
                retry_throttled=True,
            )
            mid_soup = BeautifulSoup(mid_text, "lxml")
            exam_enc = _hidden_value(mid_soup, "examEnc")
            openc = _hidden_value(mid_soup, "openc")
            t_val = _hidden_value(mid_soup, "t")
            if not (exam_enc and openc):
                continue
            list_text, _ = self.read(
                session,
                EXAM_LIST_URL,
                params={
                    "courseid": cid,
                    "clazzid": clazz_id,
                    "cpi": cpi,
                    "ut": "s",
                    "t": t_val,
                    "enc": exam_enc,
                    "openc": openc,
                },
                retry_throttled=True,
            )
            if "参数传递不正确" in list_text or "没有此页面访问权限" in list_text:
                continue
            exams.extend(
                self.parse_exam_list_html(
                    list_text,
                    course_id=cid,
                    clazz_id=clazz_id,
                    cpi=cpi,
                    course_title=course_title,
                    seen_keys=seen_keys,
                )
            )
        return exams

    @staticmethod
    def parse_exam_list_html(
        html_text: str,
        *,
        course_id: str,
        clazz_id: str,
        cpi: str,
        course_title: str = "",
        seen_keys: set[tuple[str, str, str]] | None = None,
    ) -> list[dict]:
        """解析单个课程的考试列表 HTML。"""
        soup = BeautifulSoup(html_text, "lxml")
        rel_info: dict = {}
        rel_match = _TEST_RELATION_INFO_RE.search(html_text)
        if rel_match:
            try:
                parsed = json.loads(rel_match.group(1))
                if isinstance(parsed, dict):
                    rel_info = parsed
            except ValueError:
                rel_info = {}

        results: list[dict] = []
        for li in soup.select("div.bottomList ul li, ul li"):
            trigger = li.select_one("[onclick*='goTest(']")
            if trigger is None:
                continue
            match = _GO_TEST_RE.search(str(trigger.get("onclick") or ""))
            if not match:
                continue
            g_cid, exam_id, attempt_id, end_time, paper_id, is_retest_raw, _enc, stop_exam = match.groups()
            cid = g_cid or course_id
            key = (cid, clazz_id, exam_id)
            if seen_keys is not None:
                if key in seen_keys:
                    continue
                seen_keys.add(key)

            title_el = li.select_one("p.overHidden2")
            status_el = li.select_one("p.status")
            tip_el = li.select_one(".span_tip span")
            time_el = li.select_one(".time")

            title = plain_text(title_el.get_text(" ", strip=True)) if title_el else f"考试 {exam_id}"
            raw_status = plain_text(status_el.get_text(" ", strip=True)) if status_el else ""
            tip_text = plain_text(tip_el.get_text(" ", strip=True)) if tip_el else ""
            time_text = plain_text(time_el.get_text(" ", strip=True)) if time_el else end_time

            official_url = (
                f"{EXAM_NOTES_URL}?courseId={cid}&classId={clazz_id}&examId={exam_id}&cpi={cpi}"
            )
            limit_min = 0
            info_entry = rel_info.get(str(exam_id))
            if isinstance(info_entry, dict) and isinstance(info_entry.get("limitTime"), int):
                limit_min = info_entry["limitTime"]

            native_supported = True
            reason = ""
            status = "ready"
            status_text = raw_status or "可进入"

            if stop_exam == "1":
                native_supported = False
                status = "locked"
                status_text = "已暂停进入"
                reason = "当前考试已被暂停进入，请在官方入口查看。"
            elif "未开始" in raw_status or "未开放" in raw_status:
                native_supported = False
                status = "not_started"
                status_text = "未开始"
                reason = "尚未到开考时间。"
            elif "已过期" in raw_status or "已结束" in raw_status or "已截止" in time_text:
                native_supported = False
                status = "ended"
                status_text = "已结束"
                reason = "已过截止时间。"
            elif "已完成" in raw_status or "已交卷" in raw_status or "已交卷" in time_text:
                native_supported = False
                status = "submitted"
                status_text = "已交卷"
            elif tip_text:
                native_supported = False
                status = "blocked"
                reason = tip_text

            results.append(
                {
                    "examId": str(exam_id),
                    "courseId": str(cid),
                    "clazzId": str(clazz_id),
                    "cpi": str(cpi),
                    "courseTitle": course_title,
                    "title": title,
                    "timeText": time_text,
                    "modeText": f"限时 {limit_min} 分钟" if limit_min > 0 else "在线考试",
                    "status": status,
                    "statusText": status_text,
                    "nativeSupported": native_supported,
                    "reason": reason,
                    "officialUrl": official_url,
                    "attemptId": str(attempt_id),
                    "paperId": str(paper_id),
                    "isRetest": is_retest_raw == "true",
                    "limitMinutes": limit_min,
                    "hasLocalDraft": False,
                }
            )
        return results

    def inspect_exam(self, session: requests.Session, exam_entry: dict) -> dict:
        """只读读取 examnotes 进入须知与监考／客户端标志。"""
        cid = str(exam_entry.get("courseId") or "").strip()
        clazz_id = str(exam_entry.get("clazzId") or "").strip()
        exam_id = str(exam_entry.get("examId") or "").strip()
        cpi = str(exam_entry.get("cpi") or "").strip()
        official_url = str(
            exam_entry.get("officialUrl")
            or f"{EXAM_NOTES_URL}?courseId={cid}&classId={clazz_id}&examId={exam_id}&cpi={cpi}"
        )
        html_text, _ = self.read(
            session,
            EXAM_NOTES_URL,
            params={"courseId": cid, "classId": clazz_id, "examId": exam_id, "cpi": cpi},
        )
        return self.parse_examnotes_html(
            html_text,
            official_url=official_url,
            preset_start_url=str(exam_entry.get("startUrl") or "").strip(),
        )

    @staticmethod
    def parse_examnotes_html(html_text: str, *, official_url: str = "", preset_start_url: str = "") -> dict:
        """解析 examnotes 页面隐藏域与开卷条件。"""
        soup = BeautifulSoup(html_text, "lxml")
        if "无权限访问" in html_text or "参数传递不正确" in html_text:
            return {
                "nativeSupported": False,
                "reason": "当前无权限读取考试详情，请在官方入口确认。",
                "limitMinutes": 0,
                "isRetest": False,
                "expiredAutoSubmit": False,
                "attemptId": "0",
                "startUrl": "",
                "officialUrl": official_url,
            }

        limit_raw = _hidden_value(soup, "limitTime")
        limit_minutes = int(limit_raw) if limit_raw.isdigit() else 0
        attempt_id = _hidden_value(soup, "testUserRelationId", "answerId") or "0"
        is_retest = _hidden_value(soup, "reset").lower() == "true"
        expired_auto = _hidden_value(soup, "expiredAutoSubmit") == "1"

        start_btn = soup.select_one("#startBtn")
        btn_data = str(start_btn.get("data") or "").strip() if start_btn else ""
        start_url = preset_start_url or (urljoin("https://mooc1.chaoxing.com", btn_data) if btn_data else "")

        reason = ""
        if _truthy_flag(_hidden_value(soup, "isChaoxingExamPc")):
            reason = "该场考试要求使用专用考试客户端，请从官方入口进入。"
        elif _any_flag(soup, "needFaceRecognition", "webNeedFaceRecognition"):
            reason = "该场考试要求人脸识别，请在官方入口参加。"
        elif _any_flag(soup, "snapshotMonitor", "webSnapshotMonitor", "electronSnapshotMonitor"):
            reason = "该场考试启用了抓拍监考，请在官方入口参加。"
        elif _any_flag(soup, "checkSecondDeviceLive", "liveMonitor"):
            reason = "该场考试启用了实时监考，请在官方入口参加。"
        elif _truthy_flag(_hidden_value(soup, "needCode")):
            reason = "该场考试需要输入考试码，请在官方入口进入。"
        elif not start_url:
            reason = "未识别到开卷入口，请在官方入口确认。"
        elif not preset_start_url:
            query = parse_qs(urlsplit(start_url).query)
            enc_vals = query.get("enc") or []
            if _truthy_flag(_hidden_value(soup, "captchaCheck")) and (not enc_vals or enc_vals[0] == "?"):
                reason = "进入该考试前需要完成人机验证码，请先在官方入口进入考试。"

        return {
            "nativeSupported": not bool(reason),
            "reason": reason,
            "limitMinutes": limit_minutes,
            "isRetest": is_retest,
            "expiredAutoSubmit": expired_auto,
            "attemptId": attempt_id,
            "startUrl": start_url,
            "officialUrl": official_url,
        }

    def open_exam(self, session: requests.Session, exam_entry: dict) -> dict:
        """打开试卷并解析全部题目、服务端时间与平台已保存答案。"""
        start_url = str(exam_entry.get("startUrl") or "").strip()
        if not start_url:
            notes = self.inspect_exam(session, exam_entry)
            if not notes["nativeSupported"]:
                return {"native_supported": False, "reason": notes["reason"]}
            start_url = notes["startUrl"]

        first_html, final_url = self.read(session, start_url)
        parsed = self.parse_exam_page_html(
            first_html,
            page_url=final_url,
            course_title=str(exam_entry.get("courseTitle") or ""),
            title_fallback=str(exam_entry.get("title") or ""),
        )
        if not parsed["native_supported"]:
            return parsed

        # 若为单题分页模式（左侧题号导航数量大于当前页渲染的题数），逐题读取 start=0..N-1
        total_nav = parsed.pop("nav_count", 0)
        questions = list(parsed["paper"]["questions"])
        platform_answers = dict(parsed["platform_answers"])
        form_questions = dict(parsed["form_meta"]["questions"])
        image_urls = dict(parsed["image_urls"])

        if total_nav > 1 and len(questions) == 1:
            if total_nav > MAX_EXAM_QUESTIONS:
                return {"native_supported": False, "reason": f"试卷题目数（{total_nav}）超过 {MAX_EXAM_QUESTIONS} 题上限。"}
            parsed["form_meta"]["paginated"] = True
            qid0 = questions[0]["id"]
            form_questions[qid0]["hidden_fields"] = dict(parsed["form_meta"]["hidden_fields"])
            parsed_url = urlsplit(start_url)
            base_qs = parse_qs(parsed_url.query, keep_blank_values=True)
            for idx in range(1, total_nav):
                page_qs = {k: v[0] if len(v) == 1 else v for k, v in base_qs.items()}
                page_qs["start"] = str(idx)
                next_url = urlunsplit(
                    (parsed_url.scheme, parsed_url.netloc, parsed_url.path, urlencode(page_qs, doseq=True), "")
                )
                page_html, page_final = self.read(session, next_url)
                sub = self.parse_exam_page_html(page_html, page_url=page_final, expected_no=idx + 1)
                if not sub["native_supported"]:
                    return sub
                if sub["paper"]["questions"]:
                    q_item = sub["paper"]["questions"][0]
                    q_item["no"] = idx + 1
                    questions.append(q_item)
                    sub_qid = q_item["id"]
                    form_questions.update(sub["form_meta"]["questions"])
                    form_questions[sub_qid]["hidden_fields"] = dict(sub["form_meta"]["hidden_fields"])
                else:
                    form_questions.update(sub["form_meta"]["questions"])
                platform_answers.update(sub["platform_answers"])
                image_urls.update(sub["image_urls"])

        parsed["paper"]["questions"] = questions
        parsed["platform_answers"] = platform_answers
        parsed["form_meta"]["questions"] = form_questions
        parsed["image_urls"] = image_urls
        return parsed

    @staticmethod
    def parse_exam_page_html(
        html_text: str,
        *,
        page_url: str = "https://mooc1-api.chaoxing.com/exam-ans/exam/test/reVersionTestStartNew",
        course_title: str = "",
        title_fallback: str = "",
        expected_no: int | None = None,
    ) -> dict:
        """解析 reVersionTestStartNew 试卷页面。"""
        if "无权限访问" in html_text or "参数传递不正确" in html_text or "您长时间没有操作" in html_text:
            return {"native_supported": False, "reason": "无权限访问试卷页面或会话已过期，请从官方入口确认。"}

        soup = BeautifulSoup(html_text, "lxml")
        _decode_cxsecret(html_text, soup)

        form = soup.select_one("form#submitTest")
        if form is None:
            return {"native_supported": False, "reason": "页面结构未适配（缺少试卷表单）。"}

        # 监考／客户端／分段限时检查（fail-closed）
        if _truthy_flag(_hidden_value(soup, "isChaoxingExamPc")):
            return {"native_supported": False, "reason": "该场考试要求使用专用考试客户端，请在官方入口参加。"}
        if _truthy_flag(_hidden_value(soup, "switchScreenControl")):
            return {"native_supported": False, "reason": "该场考试启用了切屏限制，请在官方入口参加。"}
        if _any_flag(soup, "snapshotMonitor", "webSnapshotMonitor", "electronSnapshotMonitor"):
            return {"native_supported": False, "reason": "该场考试启用了抓拍监考，请在官方入口参加。"}
        if _truthy_flag(_hidden_value(soup, "liveMonitor")):
            return {"native_supported": False, "reason": "该场考试启用了实时监考，请在官方入口参加。"}
        if _hidden_value(soup, "limitTimeType") not in ("", "0") or _hidden_value(soup, "groupLimitTimeType") not in ("", "0"):
            return {"native_supported": False, "reason": "该场考试启用了单题或分卷限时，暂不支持原生作答。"}

        server_now_utc, deadline_utc = _extract_server_times(soup)
        if not server_now_utc or not deadline_utc:
            return {"native_supported": False, "reason": "无法取得服务端考试截止时间，已停止开卷。"}

        course_id = _hidden_value(soup, "courseId")
        clazz_id = _hidden_value(soup, "classId")
        exam_id = _hidden_value(soup, "testPaperId", "tId")
        attempt_id = _hidden_value(soup, "testUserRelationId")
        paper_id = _hidden_value(soup, "paperId")
        cpi = _hidden_value(soup, "cpi")
        if not (course_id and clazz_id and exam_id and attempt_id):
            return {"native_supported": False, "reason": "试卷缺少课程或尝试标识，页面结构未适配。"}

        q_nodes = soup.select(".questionLi")
        if not q_nodes:
            return {"native_supported": False, "reason": "未识别到试卷题目结构，页面结构未适配。"}
        if len(q_nodes) > MAX_EXAM_QUESTIONS:
            return {"native_supported": False, "reason": f"试卷题目数超过 {MAX_EXAM_QUESTIONS} 题上限。"}

        nav_items = soup.select("#topicNumberScroll .topicNumber_list li")
        questions: list[dict] = []
        platform_answers: dict[str, dict] = {}
        form_questions: dict[str, dict] = {}
        image_urls: dict[str, list[str]] = {}

        for idx, q_node in enumerate(q_nodes, start=1):
            no = expected_no if expected_no is not None else idx
            q_dict, ans_dict, q_form, imgs, reason = _parse_single_question(q_node, no, page_url)
            if reason:
                return {"native_supported": False, "reason": reason}
            qid = q_dict["id"]
            questions.append(q_dict)
            form_questions[qid] = q_form
            if ans_dict is not None:
                platform_answers[qid] = ans_dict
            if imgs:
                image_urls[qid] = imgs

        # 收集表单隐藏域用于后续保存／交卷（保留 type/score/answeredView 等官方字段，仅排除答案输入）
        hidden_fields: dict[str, str] = {}
        for inp in form.select("input[type='hidden'][name]"):
            name = str(inp.get("name") or "").strip()
            if not name or re.fullmatch(r"answers?\d+", name):
                continue
            hidden_fields[name] = str(inp.get("value") or "")

        title_el = soup.select_one(".mark_title") or soup.select_one("h2")
        title = title_fallback or (plain_text(title_el.get_text(" ", strip=True)) if title_el else f"考试 {exam_id}")
        submit_action = _hidden_value(soup, "saveUrl") or SUBMIT_PATH
        submit_url = trusted_url(urljoin("https://mooc1-api.chaoxing.com", submit_action))

        return {
            "native_supported": True,
            "reason": "",
            "nav_count": len(nav_items),
            "server_now_utc": server_now_utc,
            "platform_answers": platform_answers,
            "image_urls": image_urls,
            "paper": {
                "course_id": course_id,
                "clazz_id": clazz_id,
                "course_title": course_title,
                "exam_id": exam_id,
                "attempt_id": attempt_id,
                "paper_id": paper_id,
                "cpi": cpi,
                "title": title,
                "deadline_utc": deadline_utc,
                "demo": False,
                "questions": questions,
            },
            "form_meta": {
                "submit_url": submit_url,
                "start_url": page_url,
                "hidden_fields": hidden_fields,
                "questions": form_questions,
            },
        }

    def _build_submit_request(self, paper: dict, form_meta: dict, answers: dict, *, temp_save: bool) -> tuple[str, dict, dict]:
        hidden = dict(form_meta.get("hidden_fields") or {})
        cid = str(paper["course_id"])
        clazz_id = str(paper["clazz_id"])
        exam_id = str(paper["exam_id"])
        attempt_id = str(paper["attempt_id"])
        cpi = str(paper.get("cpi") or hidden.get("cpi") or "0")

        query_params = {
            "classId": clazz_id,
            "courseId": cid,
            "testPaperId": exam_id,
            "testUserRelationId": attempt_id,
            "cpi": cpi,
            "tempSave": "true" if temp_save else "false",
            "version": "1",
            "view": "json",
        }
        payload: dict[str, object] = dict(hidden)
        payload["tempSave"] = "true" if temp_save else "false"
        payload["timeOver"] = "false"

        q_forms = form_meta.get("questions") or {}
        q_ids: list[str] = []
        for q in paper.get("questions") or []:
            qid = str(q["id"])
            q_ids.append(qid)
            q_form = q_forms.get(qid) or {}
            if q_form.get("type"):
                payload[f"type{qid}"] = str(q_form["type"])
            ans = answers.get(qid) or {}
            qtype = q["type"]
            if qtype == "single":
                opts = ans.get("option_ids") or []
                payload[f"answer{qid}"] = str(opts[0]) if opts else ""
            elif qtype == "multiple":
                opts = ans.get("option_ids") or []
                name = str(q_form.get("answer_name") or f"answers{qid}")
                payload[name] = "".join(str(x) for x in opts)
            elif qtype == "judgement":
                opts = ans.get("option_ids") or []
                opt_map = q_form.get("option_map") or {}
                mapped = opt_map.get(opts[0], "") if opts else ""
                payload[f"answer{qid}"] = mapped
            elif qtype == "completion":
                blanks = ans.get("blanks") or []
                blank_names = q_form.get("blank_names") or []
                for idx, name in enumerate(blank_names):
                    payload[name] = str(blanks[idx]) if idx < len(blanks) else ""
            elif qtype == "shortanswer":
                payload[f"answer{qid}"] = str(ans.get("text") or "")

        if len(q_ids) == 1:
            payload["questionId"] = q_ids[0]
        elif q_ids:
            payload["questionId"] = q_ids

        submit_url = trusted_url(str(form_meta.get("submit_url") or f"https://mooc1-api.chaoxing.com{SUBMIT_PATH}"))
        return submit_url, query_params, payload

    def _post_once(self, cookies, url: str, params: dict, payload: dict, *, referer: str = "https://mooc1-api.chaoxing.com/") -> dict:
        """使用独立 0 重试、不跟随重定向的会话发送一次保存或交卷请求。"""
        url = trusted_url(url)
        client = requests.Session()
        client.mount("https://", HTTPAdapter(max_retries=0))
        client.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": referer,
                "X-Requested-With": "XMLHttpRequest",
            }
        )
        if cookies is not None:
            client.cookies.update(cookies)
        try:
            response = client.post(url, params=params, data=payload, allow_redirects=False, timeout=20)
        finally:
            client.close()
        try:
            if response.status_code in (301, 302, 303, 307, 308):
                dest = urljoin(url, response.headers.get("Location", ""))
                if urlsplit(dest).hostname == "passport2.chaoxing.com":
                    raise PlatformResponseError("考试会话已失效，请重新登录。", "auth_error")
                raise PlatformResponseError("考试提交收到未预期的跳转响应。")
            if response.status_code != 200:
                raise PlatformResponseError(f"平台暂时不可用（HTTP {response.status_code}）。", "service_error")
            try:
                data = response.json()
            except ValueError:
                raise PlatformResponseError("平台未返回可识别的 JSON 提交结果。") from None
            if not isinstance(data, dict):
                raise PlatformResponseError("平台返回了无效的 JSON 结构。")
            return data
        finally:
            response.close()

    def save_once(self, cookies, paper: dict, form_meta: dict, answers: dict) -> dict:
        """0 重试临时保存答案到平台，并回读试卷核对已保存内容。"""
        questions = paper.get("questions") or []
        if form_meta.get("paginated") and len(questions) > 1:
            # 单题分页模式下，每题使用各自页面的表单隐藏域依次临时保存，并传递服务端返回的新签名
            rolling_enc: tuple[str, str, str] | None = None
            for q in questions:
                qid = str(q["id"])
                q_form = (form_meta.get("questions") or {}).get(qid) or {}
                q_hidden = dict(q_form.get("hidden_fields") or form_meta.get("hidden_fields") or {})
                if rolling_enc is not None:
                    q_hidden["encLastUpdateTime"], q_hidden["encRemainTime"], q_hidden["enc"] = rolling_enc
                sub_meta = {
                    "submit_url": form_meta.get("submit_url"),
                    "hidden_fields": q_hidden,
                    "questions": {qid: q_form},
                }
                url, params, payload = self._build_submit_request(
                    {**paper, "questions": [q]}, sub_meta, answers, temp_save=True
                )
                try:
                    data = self._post_once(
                        cookies,
                        url,
                        params,
                        payload,
                        referer=str(form_meta.get("start_url") or "https://mooc1-api.chaoxing.com/"),
                    )
                except (RequestException, PlatformResponseError) as exc:
                    return {"verified": False, "reason": f"保存到平台失败：{exc}", "platform_answers": {}}
                if data.get("status") == "error":
                    msg = plain_text(str(data.get("msg") or "平台拒绝保存"))[:200]
                    return {"verified": False, "reason": f"平台拒绝保存：{msg}", "platform_answers": {}}
                raw_data = str(data.get("data") or "")
                parts = raw_data.split("|")
                if len(parts) == 3 and all(parts):
                    rolling_enc = (parts[0], parts[1], parts[2])
        else:
            url, params, payload = self._build_submit_request(paper, form_meta, answers, temp_save=True)
            try:
                data = self._post_once(
                    cookies,
                    url,
                    params,
                    payload,
                    referer=str(form_meta.get("start_url") or "https://mooc1-api.chaoxing.com/"),
                )
            except (RequestException, PlatformResponseError) as exc:
                return {"verified": False, "reason": f"保存到平台失败：{exc}", "platform_answers": {}}

            if data.get("status") == "error":
                msg = plain_text(str(data.get("msg") or "平台拒绝保存"))[:200]
                return {"verified": False, "reason": f"平台拒绝保存：{msg}", "platform_answers": {}}

        # 回读核对平台已保存答案
        verify_session = requests.Session()
        if cookies is not None:
            verify_session.cookies.update(cookies)
        verify_session.headers.update({"User-Agent": "Mozilla/5.0"})
        try:
            reopened = self.open_exam(
                verify_session,
                {
                    "startUrl": form_meta.get("start_url", ""),
                    "courseTitle": paper.get("course_title", ""),
                    "title": paper.get("title", ""),
                },
            )
        except (RequestException, PlatformResponseError) as exc:
            return {"verified": False, "reason": f"已发送保存，但回读核对失败：{exc}", "platform_answers": {}}
        finally:
            verify_session.close()

        if not reopened.get("native_supported"):
            return {"verified": False, "reason": reopened.get("reason") or "回读核对失败。", "platform_answers": {}}

        platform_answers = reopened.get("platform_answers") or {}
        if _answers_match(paper.get("questions") or [], answers, platform_answers):
            return {"verified": True, "reason": "平台已保存并核对一致。", "platform_answers": platform_answers}
        return {
            "verified": False,
            "reason": "已发送保存请求，但回读结果与本地答案不完全一致，请检查或重试保存。",
            "platform_answers": platform_answers,
        }

    def submit_once(self, cookies, paper: dict, form_meta: dict, answers: dict) -> dict:
        """0 重试发送一次交卷请求，随后只读核验平台是否已收卷。"""
        url, params, payload = self._build_submit_request(paper, form_meta, answers, temp_save=False)
        try:
            data = self._post_once(
                cookies,
                url,
                params,
                payload,
                referer=str(form_meta.get("start_url") or "https://mooc1-api.chaoxing.com/"),
            )
        except PlatformResponseError as exc:
            if exc.kind == "auth_error":
                return {"state": "submit_unknown", "reason": f"{exc} 请在官方入口核验是否已收卷。"}
            return {"state": "submit_unknown", "reason": f"交卷响应异常：{exc} 请核验是否已收卷。"}
        except RequestException as exc:
            return {"state": "submit_unknown", "reason": f"交卷请求超时或断线：{exc} 请先核验交卷结果。"}

        status = str(data.get("status") or "").strip()
        msg = plain_text(str(data.get("msg") or ""))[:200]
        if status == "error":
            return {"state": "rejected", "reason": msg or "平台拒绝交卷。"}

        # 无论是否返回 submitted/forceSubmitted，均执行一次只读核验
        verify_session = requests.Session()
        if cookies is not None:
            verify_session.cookies.update(cookies)
        verify_session.headers.update({"User-Agent": "Mozilla/5.0"})
        try:
            verified = self.verify_submitted(
                verify_session,
                {
                    "courseId": paper["course_id"],
                    "clazzId": paper["clazz_id"],
                    "examId": paper["exam_id"],
                    "attemptId": paper["attempt_id"],
                    "cpi": params["cpi"],
                },
            )
        except (RequestException, PlatformResponseError):
            verified = {"confirmed": False, "reason": "交卷后只读核验请求失败，请点击核验或到官方入口确认。"}
        finally:
            verify_session.close()

        if verified.get("confirmed"):
            return {"state": "submitted", "reason": verified.get("reason") or "平台已确认收卷。"}
        if status in ("submitted", "forceSubmitted"):
            return {
                "state": "submit_unknown",
                "reason": "平台接口返回已提交状态，但只读查看页尚未确认收卷，请稍后核验或到官方入口确认。",
            }
        return {"state": "submit_unknown", "reason": verified.get("reason") or "交卷结果未知，请核验或到官方入口确认。"}

    def verify_submitted(self, session: requests.Session, exam_entry: dict) -> dict:
        """只读检查平台是否已收卷；仅在拿到明确收卷证据时返回 confirmed=True。"""
        cid = str(exam_entry.get("courseId") or "").strip()
        clazz_id = str(exam_entry.get("clazzId") or "").strip()
        exam_id = str(exam_entry.get("examId") or "").strip()
        attempt_id = str(exam_entry.get("attemptId") or "").strip()
        cpi = str(exam_entry.get("cpi") or "0").strip()
        if not (cid and clazz_id and exam_id and attempt_id):
            return {"confirmed": False, "reason": "缺少核验所需的考试或尝试标识。"}

        html_text, _ = self.read(
            session,
            EXAM_LOOK_URL,
            params={
                "courseId": cid,
                "classId": clazz_id,
                "examId": exam_id,
                "examAnswerId": attempt_id,
                "cpi": cpi,
            },
        )
        return self.parse_look_html(html_text)

    @staticmethod
    def parse_look_html(html_text: str) -> dict:
        """解析 /exam/test/look 只读核验页；无明确证据时绝不推断未交卷或已交卷。"""
        if "未提交考试" in html_text or "无权限访问" in html_text or "参数传递不正确" in html_text:
            return {
                "confirmed": False,
                "reason": "平台尚未显示已收卷记录，请稍后再次核验或前往官方入口确认。",
            }
        soup = BeautifulSoup(html_text, "lxml")
        has_receipt_text = bool(
            re.search(r"待批阅|最终成绩|已交卷", soup.get_text(" ", strip=True))
        )
        if has_receipt_text and soup.select_one(".mark_title, .mark_answer"):
            return {"confirmed": True, "reason": "平台只读查看页已确认收卷。"}
        return {
            "confirmed": False,
            "reason": "未在只读查看页中识别到明确收卷凭据，请前往官方入口确认。",
        }


def _answers_match(questions: list[dict], local_answers: dict, platform_answers: dict) -> bool:
    for q in questions:
        qid = str(q["id"])
        local = local_answers.get(qid) or {}
        plat = platform_answers.get(qid) or {}
        qtype = q["type"]
        if qtype in ("single", "multiple", "judgement"):
            if sorted(local.get("option_ids") or []) != sorted(plat.get("option_ids") or []):
                return False
        elif qtype == "completion":
            lb = [str(x).strip() for x in (local.get("blanks") or [])]
            pb = [str(x).strip() for x in (plat.get("blanks") or [])]
            if any(lb) and lb != pb:
                return False
        elif qtype == "shortanswer":
            lt = str(local.get("text") or "").strip()
            pt = str(plat.get("text") or "").strip()
            if lt and lt != pt:
                return False
    return True
