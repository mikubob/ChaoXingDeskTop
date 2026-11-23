"""独立作业本机草稿存储（按账号哈希分文件，仅本机编辑与导出，不包含任何平台提交接口）。"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .exam_drafts import QUESTION_TYPES, empty_answer, source_text
from .storage import file_lock, revision, write_if_unchanged

MAX_DRAFTS_PER_ACCOUNT = 50
MAX_ACCOUNT_BYTES = 4 * 1024 * 1024
MAX_QUESTIONS = 200
MAX_TEXT_CHARS = 12000

HOMEWORK_QUESTION_TYPES = QUESTION_TYPES + ("unsupported",)


def _utc_now(now_utc: str = "") -> str:
    if isinstance(now_utc, str) and now_utc.strip():
        return now_utc.strip()[:100]
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def homework_draft_id(account_id: str, course_id: str, work_id: str, url: str = "") -> str:
    """生成稳定的本机作业草稿 ID；优先使用 course_id + work_id。"""
    acc = str(account_id or "").strip()
    wid = str(work_id or "").strip()
    cid = str(course_id or "").strip()
    if not acc:
        raise ValueError("缺少账号标识，无法生成作业草稿。")
    if wid and wid != "unknown":
        raw = json.dumps([acc, cid, wid], ensure_ascii=False)
    else:
        raw = json.dumps([acc, str(url or "").strip()], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def validate_homework_draft(record: dict) -> None:
    if not isinstance(record, dict):
        raise ValueError("作业草稿格式无效。")
    for key in ("draft_id", "account_id", "title"):
        val = record.get(key)
        if not isinstance(val, str) or not val.strip() or len(val) > 500:
            raise ValueError(f"作业草稿缺少有效字段：{key}")
    if type(record.get("version")) is not int or record["version"] < 0:
        raise ValueError("作业草稿版本号无效。")
    questions = record.get("questions")
    if not isinstance(questions, list) or not 1 <= len(questions) <= MAX_QUESTIONS:
        raise ValueError(f"作业题目数量必须在 1 到 {MAX_QUESTIONS} 之间。")
    qmap = {}
    for q in questions:
        if not isinstance(q, dict):
            raise ValueError("作业题目结构无效。")
        qid = q.get("id")
        qtype = q.get("type")
        if not isinstance(qid, str) or not qid.strip() or qid in qmap:
            raise ValueError("作业题目 ID 无效或重复。")
        if qtype not in HOMEWORK_QUESTION_TYPES:
            raise ValueError(f"作业题目类型无效：{qtype}")
        if type(q.get("no")) is not int or q["no"] < 1:
            raise ValueError("作业题号无效。")
        if not isinstance(q.get("title"), str) or len(q["title"]) > MAX_TEXT_CHARS:
            raise ValueError("作业题干无效或超长。")
        qmap[qid] = q
    answers = record.get("answers")
    if not isinstance(answers, dict):
        raise ValueError("作业答案集合无效。")
    for qid, ans in answers.items():
        if qid not in qmap or not isinstance(ans, dict):
            raise ValueError("作业答案与题目不匹配。")
        if len(str(ans.get("text", ""))) > MAX_TEXT_CHARS:
            raise ValueError("作业简答文本超过 12000 字符上限。")


def homework_question_answered(question: dict, answer: dict | None) -> bool:
    if not question.get("supported", True) or not isinstance(answer, dict):
        return False
    qtype = question.get("type")
    if qtype in ("single", "multiple", "judgement"):
        return bool(answer.get("option_ids"))
    if qtype == "completion":
        blanks = answer.get("blanks") or []
        return bool(blanks) and all(isinstance(b, str) and bool(b.strip()) for b in blanks)
    if qtype == "shortanswer":
        return bool(str(answer.get("text") or "").strip())
    return False


def homework_summary(record: dict) -> dict:
    questions = sorted(record.get("questions") or [], key=lambda q: q.get("no", 0))
    answers = record.get("answers") or {}
    supported_qs = [q for q in questions if q.get("supported", True) and q.get("type") != "unsupported"]
    answered = sum(1 for q in supported_qs if homework_question_answered(q, answers.get(q["id"])))
    unanswered = [q["no"] for q in supported_qs if not homework_question_answered(q, answers.get(q["id"]))]
    return {
        "answeredCount": answered,
        "totalCount": len(questions),
        "supportedCount": len(supported_qs),
        "unansweredNumbers": unanswered,
    }


def _normalize_hw_answer(question: dict, raw_answer: dict, source: str, now_utc: str) -> dict:
    qtype = question.get("type")
    if not question.get("supported", True) or qtype == "unsupported":
        raise ValueError("该题型暂不支持在本机草稿中作答。")
    norm = empty_answer()
    norm["source"] = source
    norm["updated_at"] = _utc_now(now_utc)
    if isinstance(raw_answer.get("reason"), str):
        norm["reason"] = raw_answer["reason"][:1000]
    if qtype in ("single", "multiple", "judgement"):
        opts = raw_answer.get("option_ids") or []
        if not isinstance(opts, list):
            raise ValueError("选项答案格式无效。")
        valid_ids = {o["id"] for o in (question.get("options") or [])}
        cleaned = [str(x) for x in opts if str(x) in valid_ids]
        if qtype in ("single", "judgement") and len(cleaned) > 1:
            cleaned = cleaned[:1]
        norm["option_ids"] = list(dict.fromkeys(cleaned))
    elif qtype == "completion":
        blank_count = int(question.get("blank_count") or 1)
        blanks = [str(x) for x in (raw_answer.get("blanks") or [])][:blank_count]
        while len(blanks) < blank_count:
            blanks.append("")
        norm["blanks"] = blanks
    elif qtype == "shortanswer":
        text = str(raw_answer.get("text") or "")
        if len(text) > MAX_TEXT_CHARS:
            raise ValueError(f"简答题答案不能超过 {MAX_TEXT_CHARS} 字符。")
        norm["text"] = text
    return norm


def apply_homework_manual_answer(record: dict, question_id: str, answer: dict, now_utc: str = "") -> dict:
    q = next((item for item in record.get("questions", []) if item.get("id") == question_id), None)
    if q is None:
        raise ValueError("未找到对应的作业题目。")
    norm = _normalize_hw_answer(q, answer, "manual", now_utc)
    record.setdefault("answers", {})[question_id] = norm
    record["updated_at"] = norm["updated_at"]
    return record


def apply_homework_suggestion(
    record: dict,
    question_id: str,
    answer: dict,
    source: str,
    now_utc: str = "",
    *,
    replace_manual: bool = False,
) -> bool:
    q = next((item for item in record.get("questions", []) if item.get("id") == question_id), None)
    if q is None or not q.get("supported", True) or q.get("type") == "unsupported":
        return False
    existing = (record.get("answers") or {}).get(question_id)
    if existing and existing.get("source") == "manual" and not replace_manual:
        return False
    norm = _normalize_hw_answer(q, answer, source, now_utc)
    record.setdefault("answers", {})[question_id] = norm
    record["updated_at"] = norm["updated_at"]
    return True


def export_homework_text(record: dict) -> str:
    """导出可读纯文本，仅含题目、答案与来源说明。"""
    return export_answer_text(record, "独立作业本机草稿", "说明：本文件仅由桌面端本机草稿导出，未向超星平台保存或提交。")


def export_answer_text(record: dict, heading: str, description: str) -> str:
    lines = [
        f"{heading}：{record.get('title', '')}",
        f"更新时间：{record.get('updated_at', '')}",
        description,
        "",
    ]
    answers = record.get("answers") or {}
    for q in sorted(record.get("questions") or [], key=lambda item: item.get("no", 0)):
        no = q.get("no", "")
        tname = q.get("type_name") or q.get("type") or ""
        lines.append(f"{no}. [{tname}] {q.get('title', '')}")
        if not q.get("supported", True) or q.get("type") == "unsupported":
            lines.append(f"   [不支持自动作答] {q.get('reason') or ''}")
            lines.append("")
            continue
        for opt in q.get("options") or []:
            lines.append(f"   {opt.get('id')}. {opt.get('text')}")
        ans = answers.get(q["id"]) or {}
        src = source_text(ans.get("source", ""))
        qtype = q.get("type")
        if qtype in ("single", "multiple", "judgement"):
            ans_str = "、".join(ans.get("option_ids") or []) or "（未答）"
        elif qtype == "completion":
            blanks = ans.get("blanks") or []
            ans_str = " | ".join(b if b.strip() else "（空）" for b in blanks) if blanks else "（未答）"
        else:
            ans_str = (ans.get("text") or "").strip() or "（未答）"
        lines.append(f"   答案：{ans_str}（来源：{src}）")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


class HomeworkDraftStore:
    """按账号哈希隔离的本机独立作业草稿存储。"""

    def __init__(self, root: str | Path, account_id: str):
        self.root = Path(root)
        self.account_id = str(account_id or "").strip()
        if not self.account_id:
            raise ValueError("缺少账号标识，无法初始化作业草稿存储。")
        digest = hashlib.sha256(self.account_id.encode("utf-8")).hexdigest()
        self.path = self.root / "homework_drafts" / f"{digest}.json"

    def _read_all_unlocked(self) -> tuple[list[dict], bytes | None]:
        raw = revision(self.path)
        if raw is None:
            return [], None
        try:
            if len(raw) > MAX_ACCOUNT_BYTES:
                raise ValueError()
            body = json.loads(raw.decode("utf-8"))
            if not isinstance(body, dict) or body.get("version") != 1 or not isinstance(body.get("drafts"), list):
                raise ValueError()
            drafts = body["drafts"]
            if len(drafts) > MAX_DRAFTS_PER_ACCOUNT:
                raise ValueError()
            for d in drafts:
                validate_homework_draft(d)
            return drafts, raw
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise ValueError("作业草稿文件损坏或超出容量限制；原文件已保留，禁止覆盖。") from None

    def list_drafts(self) -> list[dict]:
        drafts, _ = self._read_all_unlocked()
        rows = []
        for d in drafts:
            summary = homework_summary(d)
            rows.append(
                {
                    "draftId": d["draft_id"],
                    "title": d["title"],
                    "courseId": d.get("course_id", ""),
                    "workId": d.get("work_id", ""),
                    "updatedAt": d.get("updated_at", ""),
                    "demo": bool(d.get("demo", False)),
                    **summary,
                }
            )
        rows.sort(key=lambda r: r.get("updatedAt", ""), reverse=True)
        return rows

    def get_draft(self, draft_id: str) -> tuple[dict | None, bytes | None]:
        with file_lock(self.path):
            drafts, raw = self._read_all_unlocked()
        found = next((copy.deepcopy(d) for d in drafts if d.get("draft_id") == draft_id), None)
        return found, raw

    def save_draft(self, record: dict, *, merge_existing_manual: bool = False) -> dict:
        validate_homework_draft(record)
        drafts, expected_raw = self._read_all_unlocked()
        updated = copy.deepcopy(record)
        updated["updated_at"] = _utc_now(updated.get("updated_at", ""))
        idx = next((i for i, d in enumerate(drafts) if d.get("draft_id") == updated["draft_id"]), None)
        if idx is None:
            if len(drafts) >= MAX_DRAFTS_PER_ACCOUNT:
                raise ValueError(f"当前账号本机作业草稿已达 {MAX_DRAFTS_PER_ACCOUNT} 份上限，请先删除旧草稿。")
            updated["version"] = 1
            drafts.append(updated)
        else:
            old = drafts[idx]
            if merge_existing_manual:
                old_answers = old.get("answers") or {}
                new_qids = {q["id"] for q in updated.get("questions") or []}
                for qid, ans in old_answers.items():
                    if qid in new_qids and ans.get("source") == "manual":
                        updated.setdefault("answers", {})[qid] = copy.deepcopy(ans)
                updated["version"] = old["version"] + 1
            else:
                if updated["version"] != old["version"]:
                    raise ValueError("作业草稿已被其他操作修改，请重新打开后再保存。")
                updated["version"] = old["version"] + 1
            drafts[idx] = updated

        payload = json.dumps({"version": 1, "drafts": drafts}, ensure_ascii=False, indent=2).encode("utf-8")
        if len(payload) > MAX_ACCOUNT_BYTES:
            raise ValueError("当前账号本机作业草稿总容量已达 4 MiB 上限，请先删除旧草稿。")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_if_unchanged(self.path, payload, expected_raw)
        return updated

    def delete_draft(self, draft_id: str) -> bool:
        drafts, expected_raw = self._read_all_unlocked()
        remaining = [d for d in drafts if d.get("draft_id") != draft_id]
        if len(remaining) == len(drafts):
            return False
        payload = json.dumps({"version": 1, "drafts": remaining}, ensure_ascii=False, indent=2).encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_if_unchanged(self.path, payload, expected_raw)
        return True
