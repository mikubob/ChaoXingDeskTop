"""超星独立作业只读解析适配器（只读 GET，不提供任何平台保存或提交接口）。"""
from __future__ import annotations

from datetime import datetime, timezone
import re
import time
from urllib.parse import parse_qs, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter
from requests.exceptions import RequestException

from api.decode import decode_questions_info
from api.font_decoder import FontDecoder
from api.responses import PlatformResponseError, checked_page
from chaoxing_desktop.text import plain_text

ALLOWED_HOMEWORK_PATHS = (
    "/mooc-ans/mooc2/work/dowork",
    "/mooc2-ans/work/dowork",
    "/mooc-ans/mooc2/work/task",
    "/mooc2-ans/work/task",
    "/work/doHomeWorkNew",
    "/mooc-ans/work/doHomeWorkNew",
)
COURSE_MIDDLE_URL = "https://mooc1.chaoxing.com/visit/stucoursemiddle"
WORK_LIST_URL = "https://mooc1.chaoxing.com/mooc2/work/list"
_CREDENTIAL_QUERY_KEYS = {"password", "passwd", "pwd", "pass", "secret", "cookie", "cookies", "sid"}
MAX_HOMEWORK_QUESTIONS = 200


def validate_homework_url(url: str) -> str:
    """校验独立作业链接，只允许 HTTPS 下的已核实超星作业路径，拒绝凭据参数。"""
    raw = str(url or "").strip()
    if not raw:
        raise ValueError("请输入超星作业链接。")
    parsed = urlsplit(raw)
    if parsed.scheme != "https":
        raise ValueError("作业链接必须使用 HTTPS 协议。")
    host = (parsed.hostname or "").lower()
    if not (host.endswith(".chaoxing.com") and len(host) > len(".chaoxing.com")):
        raise ValueError("只支持超星平台（*.chaoxing.com）的作业链接。")
    if parsed.username or parsed.password:
        raise ValueError("作业链接不能包含明文账号或密码。")
    if parsed.port not in (None, 443):
        raise ValueError("作业链接端口不受支持。")
    path = parsed.path or ""
    if not any(path == p or path.startswith(p + "/") for p in ALLOWED_HOMEWORK_PATHS):
        raise ValueError("暂未识别该作业链接路径，请粘贴超星「作业作答」页面链接。")
    qs = parse_qs(parsed.query, keep_blank_values=True)
    for key in qs:
        if key.lower() in _CREDENTIAL_QUERY_KEYS:
            raise ValueError("作业链接包含敏感凭据参数，已拒绝读取。")
    return raw


def _hidden_val(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        node = soup.find("input", attrs={"id": name}) or soup.find("input", attrs={"name": name})
        if node is not None:
            return str(node.get("value") or "").strip()
    return ""


def _trusted_chaoxing_url(url: str) -> str:
    parsed = urlsplit(str(url or "").strip())
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not host.endswith(".chaoxing.com")
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise PlatformResponseError("作业跳转地址不在受信超星域名范围。")
    return url


def _decode_cxsecret(html_text: str, soup: BeautifulSoup) -> None:
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
            node.string = decoder.decode(raw)
        except Exception:
            continue


def _extract_stem_and_images(q_node: Tag, base_url: str) -> tuple[str, list[str]]:
    """提取 mooc2/work/dowork 题干（位于 h3.mark_name 与 .stem_answer 之间）及题图 URL。"""
    parts: list[str] = []
    image_urls: list[str] = []
    h3 = q_node.select_one("h3.mark_name")
    if h3 is not None:
        for sib in h3.next_siblings:
            if not isinstance(sib, Tag):
                txt = plain_text(str(sib))
                if txt:
                    parts.append(txt)
                continue
            cls = sib.get("class") or []
            if "stem_answer" in cls or "clear" in cls or sib.name == "input":
                if "stem_answer" in cls:
                    break
                continue
            for img in sib.select("img"):
                src = str(img.get("src") or "").strip()
                if src:
                    abs_src = urljoin(base_url, src)
                    if abs_src.startswith("https://"):
                        image_urls.append(abs_src)
            txt = plain_text(sib.get_text(" ", strip=True))
            if txt:
                parts.append(txt)
    if not parts and h3 is not None:
        clone = BeautifulSoup(str(h3), "lxml")
        for sp in clone.select("span.colorShallow"):
            sp.decompose()
        txt = plain_text(clone.get_text(" ", strip=True))
        txt = re.sub(r"^\d+\s*[.、．]\s*", "", txt).strip()
        if txt:
            parts.append(txt)
    return " ".join(parts).strip(), image_urls


def _normalize_judgement(raw_val: str, text: str) -> str:
    val = str(raw_val or "").strip().lower()
    txt = plain_text(text)
    if val in ("true", "1", "ri", "right", "正确", "对", "√") or txt in ("对", "正确", "√", "T", "True"):
        return "正确"
    if val in ("false", "0", "wr", "wrong", "错误", "错", "×") or txt in ("错", "错误", "×", "F", "False"):
        return "错误"
    return ""


def _parse_mooc2_question(q_node: Tag, index: int, base_url: str) -> tuple[dict, dict | None, dict, list[str]]:
    qid = str(q_node.get("data") or "").strip() or f"hw-q-{index}"
    type_name = str(q_node.get("typename") or "").strip()
    type_inp = q_node.find("input", attrs={"name": f"answertype{qid}"})
    raw_type = str(type_inp.get("value") or "").strip() if type_inp else ""
    title, image_urls = _extract_stem_and_images(q_node, base_url)
    if not title:
        title = f"第 {index} 题（含图题或无文字题干）" if image_urls else f"第 {index} 题"

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    form_info = {"type": raw_type, "option_map": {}, "blank_names": []}

    stem = q_node.select_one(".stem_answer")
    if stem is None:
        return {
            "id": qid,
            "no": index,
            "type": "unsupported",
            "type_name": type_name or "未知题型",
            "title": title[:200],
            "options": [],
            "blank_count": 0,
            "supported": False,
            "reason": "未识别到作答区域，已保留题干摘要。",
        }, None, form_info, image_urls

    if stem.select("input[type='file']") or stem.select(".line_answer") or raw_type in ("11", "14"):
        return {
            "id": qid,
            "no": index,
            "type": "unsupported",
            "type_name": type_name or f"type={raw_type}",
            "title": title[:200],
            "options": [],
            "blank_count": 0,
            "supported": False,
            "reason": f"暂不支持「{type_name or '该题型'}」在本机草稿中作答，请按原题单独处理。",
        }, None, form_info, image_urls

    choice_spans = stem.select(f"span.choice{qid}") or stem.select("span.num_option, span.num_option_dx")
    blank_editors = stem.select(f"textarea[name^='answerEditor{qid}']")
    blank_inputs = [
        inp for inp in stem.select(f"input[name^='answer{qid}']")
        if inp.get("type", "text") in ("text", "")
    ]
    short_textarea = stem.select_one(f"textarea[name='answer{qid}']")

    is_judgement = ("判断" in type_name) or raw_type == "3"
    is_multiple = ("多选" in type_name) or raw_type == "1"

    if is_multiple and choice_spans:
        options = []
        for sp in choice_spans:
            oid = str(sp.get("data") or "").strip()
            p_node = sp.find_next_sibling(class_="answer_p") or sp.parent.select_one(".answer_p")
            otext = plain_text(p_node.get_text(" ", strip=True)) if p_node else oid
            if oid:
                options.append({"id": oid, "text": otext})
        if options:
            ans_inp = q_node.find("input", attrs={"name": f"answer{qid}"})
            saved_raw = str(ans_inp.get("value") or "").strip() if ans_inp else ""
            sel = [o["id"] for o in options if o["id"] in saved_raw] if saved_raw else []
            plat = {"option_ids": sel, "text": "", "blanks": [], "source": "platform", "updated_at": now_iso} if sel else None
            return {
                "id": qid,
                "no": index,
                "type": "multiple",
                "type_name": type_name or "多选题",
                "title": title,
                "options": options,
                "blank_count": 0,
                "supported": True,
                "reason": "",
            }, plat, form_info, image_urls

    if is_judgement and choice_spans:
        options = []
        option_map = {}
        reverse_map = {}
        for sp in choice_spans:
            oid = str(sp.get("data") or "").strip()
            p_node = sp.find_next_sibling(class_="answer_p") or sp.parent.select_one(".answer_p")
            otext = plain_text(p_node.get_text(" ", strip=True)) if p_node else oid
            norm = _normalize_judgement(oid, otext)
            if norm:
                options.append({"id": norm, "text": norm})
                option_map[norm] = oid
                reverse_map[oid] = norm
        if len(options) >= 2:
            form_info["option_map"] = option_map
            ans_inp = q_node.find("input", attrs={"name": f"answer{qid}"})
            saved_raw = str(ans_inp.get("value") or "").strip() if ans_inp else ""
            mapped = reverse_map.get(saved_raw) or (_normalize_judgement(saved_raw, "") if saved_raw else "")
            plat = {"option_ids": [mapped], "text": "", "blanks": [], "source": "platform", "updated_at": now_iso} if mapped else None
            return {
                "id": qid,
                "no": index,
                "type": "judgement",
                "type_name": type_name or "判断题",
                "title": title,
                "options": options,
                "blank_count": 0,
                "supported": True,
                "reason": "",
            }, plat, form_info, image_urls

    if (raw_type == "0" or "单选" in type_name) and choice_spans:
        options = []
        for sp in choice_spans:
            oid = str(sp.get("data") or "").strip()
            p_node = sp.find_next_sibling(class_="answer_p") or sp.parent.select_one(".answer_p")
            otext = plain_text(p_node.get_text(" ", strip=True)) if p_node else oid
            if oid:
                options.append({"id": oid, "text": otext})
        if options:
            ans_inp = q_node.find("input", attrs={"name": f"answer{qid}"})
            saved_raw = str(ans_inp.get("value") or "").strip() if ans_inp else ""
            plat = (
                {"option_ids": [saved_raw], "text": "", "blanks": [], "source": "platform", "updated_at": now_iso}
                if saved_raw and any(o["id"] == saved_raw for o in options)
                else None
            )
            return {
                "id": qid,
                "no": index,
                "type": "single",
                "type_name": type_name or "单选题",
                "title": title,
                "options": options,
                "blank_count": 0,
                "supported": True,
                "reason": "",
            }, plat, form_info, image_urls

    if blank_editors or blank_inputs or raw_type in ("2", "9", "10"):
        fields = blank_editors or blank_inputs
        if fields:
            form_info["blank_names"] = [str(f.get("name") or f"answerEditor{qid}{i+1}") for i, f in enumerate(fields)]
            blanks = [plain_text(f.get_text()) if f.name == "textarea" else str(f.get("value") or "") for f in fields]
            plat = {"option_ids": [], "text": "", "blanks": blanks, "source": "platform", "updated_at": now_iso} if any(b.strip() for b in blanks) else None
            return {
                "id": qid,
                "no": index,
                "type": "completion",
                "type_name": type_name or "填空题",
                "title": title,
                "options": [],
                "blank_count": len(fields),
                "supported": True,
                "reason": "",
            }, plat, form_info, image_urls

    if short_textarea is not None or raw_type in ("4", "5", "6", "7", "8"):
        if short_textarea is not None:
            val = short_textarea.get_text() or ""
            plat = {"option_ids": [], "text": val.strip(), "blanks": [], "source": "platform", "updated_at": now_iso} if val.strip() else None
            return {
                "id": qid,
                "no": index,
                "type": "shortanswer",
                "type_name": type_name or "简答题",
                "title": title,
                "options": [],
                "blank_count": 0,
                "supported": True,
                "reason": "",
            }, plat, form_info, image_urls

    return {
        "id": qid,
        "no": index,
        "type": "unsupported",
        "type_name": type_name or f"type={raw_type or 'unknown'}",
        "title": title[:200],
        "options": [],
        "blank_count": 0,
        "supported": False,
        "reason": f"暂不支持「{type_name or '该题型'}」在本机草稿中作答。",
    }, None, form_info, image_urls


class HomeworkAdapter:
    """超星独立作业只读解析适配器。"""

    def read(self, session: requests.Session, url: str, params: dict | None = None, *,
             retry_throttled: bool = False) -> tuple[str, str]:
        current = _trusted_chaoxing_url(url)
        throttled = 0
        redirects = 0
        while redirects < 6:
            response = session.get(
                current,
                params=params,
                allow_redirects=False,
                timeout=20,
                headers={"Referer": "https://mooc1.chaoxing.com/"},
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
                dest = urljoin(current, response.headers.get("Location", ""))
                response.close()
                if urlsplit(dest).hostname == "passport2.chaoxing.com":
                    raise PlatformResponseError("作业会话已失效，请重新登录。", "auth_error")
                current = _trusted_chaoxing_url(dest)
                redirects += 1
                continue
            try:
                return checked_page(response, "cards"), _trusted_chaoxing_url(str(response.url or current))
            finally:
                response.close()
        raise PlatformResponseError("作业页面重定向次数超过上限。")

    def list_homeworks(self, session: requests.Session, courses: list[dict]) -> list[dict]:
        """按课程读取账号的独立作业列表，返回标准化作业条目。"""
        works: list[dict] = []
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
            work_enc = _hidden_val(mid_soup, "workEnc")
            openc = _hidden_val(mid_soup, "openc")
            t_val = _hidden_val(mid_soup, "t")
            stu_enc = _hidden_val(mid_soup, "enc")
            if not (work_enc and openc):
                continue
            list_text, _ = self.read(
                session,
                WORK_LIST_URL,
                params={
                    "courseId": cid,
                    "classId": clazz_id,
                    "cpi": cpi,
                    "ut": "s",
                    "openc": openc,
                    "enc": work_enc,
                    "t": t_val,
                    "stuenc": stu_enc,
                    "isdisplaytable": "2",
                },
                retry_throttled=True,
            )
            if "参数传递不正确" in list_text or "没有此页面访问权限" in list_text or "无权限" in list_text:
                continue
            works.extend(
                self.parse_work_list_html(
                    list_text,
                    course_id=cid,
                    clazz_id=clazz_id,
                    course_title=course_title,
                    seen_keys=seen_keys,
                )
            )
        return works

    @staticmethod
    def parse_work_list_html(
        html_text: str,
        *,
        course_id: str,
        clazz_id: str,
        course_title: str = "",
        seen_keys: set[tuple[str, str, str]] | None = None,
    ) -> list[dict]:
        """解析单个课程的 mooc2/work/list HTML。"""
        soup = BeautifulSoup(html_text, "lxml")
        results: list[dict] = []
        for li in soup.select("div.bottomList ul li[data], ul li[data]"):
            raw_url = str(li.get("data") or "").strip()
            if not raw_url:
                continue
            try:
                work_url = validate_homework_url(raw_url)
            except ValueError:
                continue
            qs = parse_qs(urlsplit(work_url).query)
            work_id = (qs.get("workId") or qs.get("workid") or [""])[0]
            key = (course_id, clazz_id, work_id or work_url)
            if seen_keys is not None:
                if key in seen_keys:
                    continue
                seen_keys.add(key)

            title_el = li.select_one("p.overHidden2")
            status_el = li.select_one("p.status")
            time_el = li.select_one(".time")

            title = plain_text(title_el.get_text(" ", strip=True)) if title_el else f"作业 {work_id or len(results) + 1}"
            status_text = plain_text(status_el.get_text(" ", strip=True)) if status_el else ""
            time_text = plain_text(time_el.get_text(" ", strip=True)) if time_el else ""

            can_open = True
            status = "todo"
            reason = ""
            if any(k in status_text for k in ("已过期", "已结束", "已截止")) or "已截止" in time_text:
                can_open = False
                status = "ended"
                reason = "已过截止时间，请在官方入口查看。"
            elif any(k in status_text for k in ("已完成", "待批阅", "已批阅", "已提交")):
                can_open = False
                status = "submitted"
                reason = f"当前状态为「{status_text}」，请在官方入口查看。"

            results.append(
                {
                    "workId": str(work_id),
                    "courseId": str(course_id),
                    "clazzId": str(clazz_id),
                    "courseTitle": course_title,
                    "title": title,
                    "status": status,
                    "statusText": status_text or "未交",
                    "timeText": time_text,
                    "workUrl": work_url,
                    "canOpen": can_open,
                    "reason": reason,
                }
            )
        return results

    def load_homework(self, session: requests.Session, url: str) -> dict:
        clean_url = validate_homework_url(url)
        html_text, final_url = self.read(session, clean_url)
        return self.parse_homework_html(html_text, url=final_url)

    @staticmethod
    def parse_homework_html(html_text: str, *, url: str) -> dict:
        if "无权限访问" in html_text or "参数传递不正确" in html_text or "您长时间没有操作" in html_text:
            raise PlatformResponseError("无权限访问该作业链接或链接已过期，请重新复制最新作业链接。")

        soup = BeautifulSoup(html_text, "lxml")
        _decode_cxsecret(html_text, soup)

        title_el = soup.select_one("h2.mark_title, .mark_title")
        title = plain_text(title_el.get_text(" ", strip=True)) if title_el else "独立作业草稿"
        qs = parse_qs(urlsplit(url).query)
        work_id = (qs.get("workId") or qs.get("workid") or ["unknown"])[0]
        course_id = (qs.get("courseId") or qs.get("courseid") or [""])[0]
        clazz_id = (qs.get("classId") or qs.get("clazzId") or [""])[0]

        q_nodes = soup.select(".questionLi")
        questions: list[dict] = []
        image_urls: dict[str, list[str]] = {}
        platform_answers: dict[str, dict] = {}
        form_questions: dict[str, dict] = {}
        form_meta: dict = {}

        if q_nodes:
            if len(q_nodes) > MAX_HOMEWORK_QUESTIONS:
                raise PlatformResponseError(f"作业题目数超过 {MAX_HOMEWORK_QUESTIONS} 题上限。")
            for idx, q_node in enumerate(q_nodes, start=1):
                q_dict, plat_ans, q_form, imgs = _parse_mooc2_question(q_node, idx, url)
                questions.append(q_dict)
                form_questions[q_dict["id"]] = q_form
                if plat_ans is not None:
                    platform_answers[q_dict["id"]] = plat_ans
                if imgs:
                    image_urls[q_dict["id"]] = imgs
            form = soup.select_one("form#submitForm")
            if form is not None and form.get("action"):
                submit_url = _trusted_chaoxing_url(urljoin(url, str(form["action"])))
                hidden_fields: dict[str, str] = {}
                for inp in form.select("input[type='hidden'][name]"):
                    name = str(inp.get("name") or "").strip()
                    if not name or re.fullmatch(r"answers?\d+", name):
                        continue
                    hidden_fields[name] = str(inp.get("value") or "")
                form_meta = {
                    "submit_url": submit_url,
                    "start_url": url,
                    "hidden_fields": hidden_fields,
                    "questions": form_questions,
                }
        elif soup.select_one("#workLibraryId") and soup.select(".TiMu"):
            legacy = decode_questions_info(html_text)
            raw_qs = legacy.get("questions") or []
            if len(raw_qs) > MAX_HOMEWORK_QUESTIONS:
                raise PlatformResponseError(f"作业题目数超过 {MAX_HOMEWORK_QUESTIONS} 题上限。")
            for idx, rq in enumerate(raw_qs, start=1):
                qtype = str(rq.get("type") or "unknown")
                qid = str(rq.get("id") or f"hw-q-{idx}")
                if qtype in ("single", "multiple", "judgement", "completion", "shortanswer"):
                    opts = []
                    if qtype in ("single", "multiple"):
                        for line in str(rq.get("options") or "").splitlines():
                            m = re.match(r"^\s*([A-Z])[\.．、]\s*(.+)$", line)
                            if m:
                                opts.append({"id": m.group(1), "text": m.group(2).strip()})
                    elif qtype == "judgement":
                        opts = [{"id": "正确", "text": "正确"}, {"id": "错误", "text": "错误"}]
                    questions.append(
                        {
                            "id": qid,
                            "no": idx,
                            "type": qtype,
                            "type_name": qtype,
                            "title": plain_text(str(rq.get("title") or "")) or f"第 {idx} 题",
                            "options": opts,
                            "blank_count": len(rq.get("answerField") or {}) if qtype == "completion" else 0,
                            "supported": True,
                            "reason": "",
                        }
                    )
                else:
                    questions.append(
                        {
                            "id": qid,
                            "no": idx,
                            "type": "unsupported",
                            "type_name": qtype,
                            "title": plain_text(str(rq.get("title") or ""))[:200],
                            "options": [],
                            "blank_count": 0,
                            "supported": False,
                            "reason": f"暂不支持「{qtype}」在本机草稿中作答。",
                        }
                    )
        else:
            raise PlatformResponseError("未识别到独立作业题目结构，当前页面暂未适配。")

        return {
            "title": title,
            "url": url,
            "course_id": str(course_id),
            "clazz_id": str(clazz_id),
            "work_id": str(work_id),
            "questions": questions,
            "image_urls": image_urls,
            "platform_answers": platform_answers,
            "form_meta": form_meta,
        }

    def save_once(self, cookies, homework_data: dict, form_meta: dict, answers: dict) -> dict:
        """0 重试暂时保存作业答案（pyFlag=1，绝不提交），并回读页面核对选项与填空。"""
        submit_url = str(form_meta.get("submit_url") or "").strip()
        start_url = str(form_meta.get("start_url") or "").strip()
        if not submit_url or not start_url:
            return {"verified": False, "reason": "当前作业未识别到平台保存表单，仅保存在本机草稿。"}
        submit_url = _trusted_chaoxing_url(submit_url)

        payload = dict(form_meta.get("hidden_fields") or {})
        payload["pyFlag"] = "1"
        q_forms = form_meta.get("questions") or {}
        q_ids: list[str] = []
        for q in homework_data.get("questions") or []:
            if not q.get("supported", True) or q.get("type") == "unsupported":
                continue
            qid = str(q["id"])
            q_ids.append(qid)
            q_form = q_forms.get(qid) or {}
            if q_form.get("type"):
                payload[f"answertype{qid}"] = str(q_form["type"])
            ans = answers.get(qid) or {}
            qtype = q["type"]
            if qtype == "single":
                opts = ans.get("option_ids") or []
                payload[f"answer{qid}"] = str(opts[0]) if opts else ""
            elif qtype == "multiple":
                opts = ans.get("option_ids") or []
                payload[f"answer{qid}"] = "".join(str(x) for x in opts)
            elif qtype == "judgement":
                opts = ans.get("option_ids") or []
                opt_map = q_form.get("option_map") or {}
                payload[f"answer{qid}"] = opt_map.get(opts[0], "") if opts else ""
            elif qtype == "completion":
                blanks = ans.get("blanks") or []
                blank_names = q_form.get("blank_names") or []
                payload[f"tiankongsize{qid}"] = str(len(blank_names) or q.get("blank_count") or 0)
                for idx, name in enumerate(blank_names):
                    payload[name] = str(blanks[idx]) if idx < len(blanks) else ""
            elif qtype == "shortanswer":
                payload[f"answer{qid}"] = str(ans.get("text") or "")

        payload["answerwqbid"] = ",".join(q_ids) + ("," if q_ids else "")

        client = requests.Session()
        client.mount("https://", HTTPAdapter(max_retries=0))
        client.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": start_url,
                "X-Requested-With": "XMLHttpRequest",
            }
        )
        if cookies is not None:
            client.cookies.update(cookies)
        try:
            resp = client.post(
                submit_url,
                params={"pyFlag": "1", "ua": "pc", "formType": "post", "saveStatus": "1", "version": "1"},
                data=payload,
                allow_redirects=False,
                timeout=20,
            )
            try:
                if resp.status_code != 200:
                    return {"verified": False, "reason": f"平台暂时保存失败（HTTP {resp.status_code}）。"}
                data = resp.json()
            except ValueError:
                return {"verified": False, "reason": "平台未返回可识别的保存结果，请先保留本机草稿。"}
            finally:
                resp.close()
        except RequestException as exc:
            return {"verified": False, "reason": f"保存到平台失败：{exc}"}
        finally:
            client.close()

        if not isinstance(data, dict) or not data.get("status"):
            msg = plain_text(str((data or {}).get("msg") or "平台拒绝保存"))[:200]
            return {"verified": False, "reason": f"平台拒绝保存：{msg}"}

        # 回读作业页面核对已保存的选项与填空
        verify_session = requests.Session()
        if cookies is not None:
            verify_session.cookies.update(cookies)
        verify_session.headers.update({"User-Agent": "Mozilla/5.0"})
        try:
            reloaded = self.load_homework(verify_session, start_url)
        except (RequestException, PlatformResponseError) as exc:
            return {"verified": False, "reason": f"已发送保存，但回读核对失败：{exc}"}
        finally:
            verify_session.close()

        plat_answers = reloaded.get("platform_answers") or {}
        if _homework_answers_match(homework_data.get("questions") or [], answers, plat_answers):
            return {"verified": True, "reason": "作业已暂存到平台并核对一致（未提交）。"}
        return {"verified": False, "reason": "已发送暂存请求，但回读结果与本地答案不完全一致，请检查或重试保存。"}


def _homework_answers_match(questions: list[dict], local_answers: dict, platform_answers: dict) -> bool:
    for q in questions:
        if not q.get("supported", True) or q.get("type") == "unsupported":
            continue
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
