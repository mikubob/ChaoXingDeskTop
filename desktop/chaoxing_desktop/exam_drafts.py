"""按账号、平台 UID、课程、班级、考试与尝试 ID 隔离的本地考试答卷存储与交卷防重状态机。"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .storage import file_lock, revision, write_if_unchanged

STATE_LOCAL_DRAFT = "local_draft"
STATE_PLATFORM_SAVED = "platform_saved"
STATE_SUBMIT_UNKNOWN = "submit_unknown"
STATE_SUBMITTED = "submitted"
STATE_SIMULATED_SUBMITTED = "simulated_submitted"
STATE_REJECTED = "rejected"

STATES = (
    STATE_LOCAL_DRAFT,
    STATE_PLATFORM_SAVED,
    STATE_SUBMIT_UNKNOWN,
    STATE_SUBMITTED,
    STATE_SIMULATED_SUBMITTED,
    STATE_REJECTED,
)

EDITABLE_STATES = (
    STATE_LOCAL_DRAFT,
    STATE_PLATFORM_SAVED,
    STATE_REJECTED,
)

FINISHED_STATES = (
    STATE_SUBMITTED,
    STATE_SIMULATED_SUBMITTED,
)

STATE_TEXT = {
    STATE_LOCAL_DRAFT: "已保存到本机",
    STATE_PLATFORM_SAVED: "平台已保存",
    STATE_SUBMIT_UNKNOWN: "提交结果待核验",
    STATE_SUBMITTED: "已交卷",
    STATE_SIMULATED_SUBMITTED: "已交卷（模拟）",
    STATE_REJECTED: "平台拒绝交卷",
}

MAX_FILE_BYTES = 512 * 1024
MAX_QUESTIONS = 200
MAX_FILES = 100
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_TEXT_CHARS = 12000

QUESTION_TYPES = ("single", "multiple", "judgement", "completion", "shortanswer")

RECORD_FIELDS = {
    "version",
    "revision",
    "account_id",
    "uid",
    "course_id",
    "clazz_id",
    "exam_id",
    "attempt_id",
    "demo",
    "title",
    "deadline_utc",
    "state",
    "prev_state",
    "reason",
    "updated_at",
    "questions",
    "answers",
}

QUESTION_REQUIRED_FIELDS = {"id", "no", "type", "title", "options", "blank_count"}
QUESTION_OPTIONAL_FIELDS = {"score", "images"}
ANSWER_REQUIRED_FIELDS = {"option_ids", "text", "blanks", "source", "updated_at"}
ANSWER_OPTIONAL_FIELDS = {"reason"}


def _utc_now(now_utc: str = "") -> str:
    if isinstance(now_utc, str) and now_utc.strip():
        return now_utc.strip()[:100]
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def state_text(state: str) -> str:
    return STATE_TEXT.get(state, "未知状态")


def source_text(source: str) -> str:
    if not source:
        return "未答"
    if source == "manual":
        return "手动填写"
    if source == "platform":
        return "平台已存"
    if source == "ai":
        return "参考答案 · AI"
    if source.startswith("bank:"):
        return f"参考答案 · {source[5:]}"
    return source


def _valid_source(source: object) -> bool:
    if not isinstance(source, str) or len(source) > 128:
        return False
    if source in ("", "manual", "ai", "platform"):
        return True
    if source.startswith("bank:"):
        provider = source[5:]
        return bool(provider) and all(c.isalnum() or c in ("_", "-") for c in provider)
    return False


def exam_draft_key(account_id, uid, course_id, clazz_id, exam_id, attempt_id) -> str:
    """生成考试草稿的隔离存储键；任一身份字段缺失或空白均拒绝。"""
    raw_values = (account_id, uid, course_id, clazz_id, exam_id, attempt_id)
    if any(v is None or isinstance(v, bool) for v in raw_values):
        raise ValueError("考试草稿身份不完整，无法定位或保存答卷。")
    values = [str(v).strip() for v in raw_values]
    if not all(values) or any(len(v) > 256 for v in values):
        raise ValueError("考试草稿身份不完整，无法定位或保存答卷。")
    payload = json.dumps(values, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def empty_answer() -> dict:
    return {
        "option_ids": [],
        "text": "",
        "blanks": [],
        "source": "",
        "updated_at": "",
    }


def _validate_question(question: object) -> None:
    if not isinstance(question, dict):
        raise ValueError("考试题目结构无效。")
    keys = set(question)
    if not QUESTION_REQUIRED_FIELDS.issubset(keys) or not keys.issubset(QUESTION_REQUIRED_FIELDS | QUESTION_OPTIONAL_FIELDS):
        raise ValueError("考试题目字段无效。")
    qid = question.get("id")
    no = question.get("no")
    qtype = question.get("type")
    title = question.get("title")
    options = question.get("options")
    blank_count = question.get("blank_count")
    if (
        not isinstance(qid, str)
        or not qid.strip()
        or len(qid) > 256
        or type(no) is not int
        or not 1 <= no <= MAX_QUESTIONS
        or qtype not in QUESTION_TYPES
        or not isinstance(title, str)
        or not title.strip()
        or len(title) > MAX_TEXT_CHARS
        or not isinstance(options, list)
        or len(options) > 50
        or type(blank_count) is not int
        or not 0 <= blank_count <= 50
    ):
        raise ValueError("考试题目字段内容无效。")

    opt_ids = []
    for opt in options:
        if (
            not isinstance(opt, dict)
            or set(opt) != {"id", "text"}
            or not isinstance(opt.get("id"), str)
            or not opt["id"].strip()
            or len(opt["id"]) > 256
            or not isinstance(opt.get("text"), str)
            or len(opt["text"]) > MAX_TEXT_CHARS
        ):
            raise ValueError("考试题目选项格式无效。")
        opt_ids.append(opt["id"])
    if len(opt_ids) != len(set(opt_ids)):
        raise ValueError("考试题目选项 ID 重复。")

    if qtype in ("single", "multiple"):
        if not options or blank_count != 0:
            raise ValueError("选择题必须包含选项且填空数为 0。")
    elif qtype == "judgement":
        if blank_count != 0:
            raise ValueError("判断题填空数必须为 0。")
        if options and set(opt_ids) != {"正确", "错误"}:
            raise ValueError("判断题选项必须为「正确」和「错误」。")
    elif qtype == "completion":
        if blank_count < 1 or options:
            raise ValueError("填空题必须指定至少 1 个空且不包含选项。")
    elif qtype == "shortanswer":
        if blank_count != 0 or options:
            raise ValueError("简答题不包含选项且填空数为 0。")

    if "score" in question and (not isinstance(question["score"], str) or len(question["score"]) > 64):
        raise ValueError("题目分值字段无效。")
    if "images" in question:
        images = question["images"]
        if (
            not isinstance(images, list)
            or len(images) > 20
            or any(not isinstance(img, str) or len(img) > 1024 for img in images)
        ):
            raise ValueError("题目图片字段无效。")


def _validate_answer_for_question(question: dict, answer: object) -> None:
    if not isinstance(answer, dict):
        raise ValueError("考试答案格式无效。")
    keys = set(answer)
    if not ANSWER_REQUIRED_FIELDS.issubset(keys) or not keys.issubset(ANSWER_REQUIRED_FIELDS | ANSWER_OPTIONAL_FIELDS):
        raise ValueError("考试答案字段集合无效。")

    option_ids = answer.get("option_ids")
    text = answer.get("text")
    blanks = answer.get("blanks")
    source = answer.get("source")
    updated_at = answer.get("updated_at")
    if (
        not isinstance(option_ids, list)
        or len(option_ids) > 50
        or any(not isinstance(v, str) or not v.strip() or len(v) > 256 for v in option_ids)
        or len(option_ids) != len(set(option_ids))
        or not isinstance(text, str)
        or len(text) > MAX_TEXT_CHARS
        or not isinstance(blanks, list)
        or len(blanks) > 50
        or any(not isinstance(v, str) or len(v) > MAX_TEXT_CHARS for v in blanks)
        or not _valid_source(source)
        or not isinstance(updated_at, str)
        or len(updated_at) > 100
    ):
        raise ValueError("考试答案内容格式无效。")
    if "reason" in answer and (not isinstance(answer["reason"], str) or len(answer["reason"]) > 1000):
        raise ValueError("考试答案提示原因格式无效。")

    qtype = question["type"]
    valid_opt_ids = {opt["id"] for opt in question["options"]}
    if qtype == "single":
        if len(option_ids) > 1 or not set(option_ids).issubset(valid_opt_ids) or text or blanks:
            raise ValueError("单选题答案不合法。")
    elif qtype == "multiple":
        if not set(option_ids).issubset(valid_opt_ids) or text or blanks:
            raise ValueError("多选题答案不合法。")
    elif qtype == "judgement":
        allowed_judge = valid_opt_ids or {"正确", "错误"}
        if len(option_ids) > 1 or not set(option_ids).issubset(allowed_judge) or text or blanks:
            raise ValueError("判断题答案不合法。")
    elif qtype == "completion":
        if option_ids or text or (blanks and len(blanks) != question["blank_count"]):
            raise ValueError("填空题答案空数不匹配。")
    elif qtype == "shortanswer":
        if option_ids or blanks:
            raise ValueError("简答题答案不合法。")


def validate_draft(record: object) -> None:
    """严格校验考试草稿记录结构、题目定义与答案约束。"""
    if not isinstance(record, dict) or set(record) != RECORD_FIELDS:
        raise ValueError("考试草稿顶层字段无效。")
    if record.get("version") != 1 or type(record.get("version")) is not int:
        raise ValueError("考试草稿版本不支持。")
    if type(record.get("revision")) is not int or not 0 <= record["revision"] <= 10**9:
        raise ValueError("考试草稿修订号无效。")
    if type(record.get("demo")) is not bool:
        raise ValueError("考试草稿演示标志无效。")

    # 校验身份字段
    exam_draft_key(
        record.get("account_id"),
        record.get("uid"),
        record.get("course_id"),
        record.get("clazz_id"),
        record.get("exam_id"),
        record.get("attempt_id"),
    )

    if not isinstance(record.get("title"), str) or not record["title"].strip() or len(record["title"]) > 500:
        raise ValueError("考试标题无效。")
    if not isinstance(record.get("deadline_utc"), str) or len(record["deadline_utc"]) > 100:
        raise ValueError("考试截止时间无效。")
    if record.get("state") not in STATES:
        raise ValueError("考试草稿状态无效。")
    if record.get("prev_state") not in ("",) + STATES:
        raise ValueError("考试草稿前置状态无效。")
    if not isinstance(record.get("reason"), str) or len(record["reason"]) > 1000:
        raise ValueError("考试草稿原因字段无效。")
    if not isinstance(record.get("updated_at"), str) or len(record["updated_at"]) > 100:
        raise ValueError("考试草稿更新时间无效。")

    questions = record.get("questions")
    if not isinstance(questions, list) or not 1 <= len(questions) <= MAX_QUESTIONS:
        raise ValueError("考试草稿题目列表无效或超出上限。")

    questions_by_id = {}
    seen_nos = set()
    for q in questions:
        _validate_question(q)
        if q["id"] in questions_by_id or q["no"] in seen_nos:
            raise ValueError("考试草稿题目 ID 或题号重复。")
        questions_by_id[q["id"]] = q
        seen_nos.add(q["no"])

    answers = record.get("answers")
    if not isinstance(answers, dict) or not set(answers).issubset(set(questions_by_id)):
        raise ValueError("考试草稿答案与题目不匹配。")
    for qid, ans in answers.items():
        if not isinstance(qid, str):
            raise ValueError("考试草稿答案题目 ID 无效。")
        _validate_answer_for_question(questions_by_id[qid], ans)


def question_answered(question: dict, answer: dict | None) -> bool:
    """判断单题是否已作答：选择题 option_ids 非空，填空题 blanks 全部非空，简答题 text.strip() 非空。"""
    if not isinstance(answer, dict):
        return False
    qtype = question.get("type")
    if qtype in ("single", "multiple", "judgement"):
        option_ids = answer.get("option_ids") or []
        return bool(option_ids) and all(isinstance(v, str) and bool(v.strip()) for v in option_ids)
    if qtype == "completion":
        blank_count = question.get("blank_count", 0)
        blanks = answer.get("blanks") or []
        return blank_count > 0 and len(blanks) == blank_count and all(isinstance(v, str) and bool(v.strip()) for v in blanks)
    if qtype == "shortanswer":
        text = answer.get("text", "")
        return isinstance(text, str) and bool(text.strip())
    return False


def answer_summary(record: dict) -> dict:
    """计算已答题数、总题数和未答题号列表。"""
    questions = record.get("questions") or []
    answers = record.get("answers") or {}
    unanswered = []
    answered_count = 0
    for q in sorted(questions, key=lambda item: item.get("no", 0)):
        if question_answered(q, answers.get(q["id"])):
            answered_count += 1
        else:
            unanswered.append(q["no"])
    return {
        "answeredCount": answered_count,
        "totalCount": len(questions),
        "unansweredNumbers": unanswered,
    }


def _normalize_incoming_answer(question: dict, answer: dict, source: str, now_utc: str) -> dict:
    if not isinstance(answer, dict):
        raise ValueError("答案必须为字典。")
    qtype = question["type"]
    normalized = {
        "option_ids": [],
        "text": "",
        "blanks": [],
        "source": source,
        "updated_at": _utc_now(now_utc),
    }
    if "reason" in answer and isinstance(answer["reason"], str):
        normalized["reason"] = answer["reason"][:1000]

    if qtype in ("single", "multiple", "judgement"):
        raw_opts = answer.get("option_ids", [])
        if not isinstance(raw_opts, list):
            raise ValueError("选择题答案 option_ids 必须为列表。")
        normalized["option_ids"] = list(raw_opts)
    elif qtype == "completion":
        raw_blanks = answer.get("blanks", [])
        if not isinstance(raw_blanks, list):
            raise ValueError("填空题答案 blanks 必须为列表。")
        normalized["blanks"] = list(raw_blanks)
    elif qtype == "shortanswer":
        raw_text = answer.get("text", "")
        if not isinstance(raw_text, str):
            raise ValueError("简答题答案 text 必须为字符串。")
        normalized["text"] = raw_text

    _validate_answer_for_question(question, normalized)
    return normalized


def apply_manual_answer(record: dict, question_id: str, answer: dict, now_utc: str = "") -> dict:
    """在允许编辑的状态下更新题目答案并标记 source='manual'，状态迁为 local_draft。"""
    validate_draft(record)
    if record["state"] not in EDITABLE_STATES:
        raise ValueError(f"当前答卷状态为「{state_text(record['state'])}」，禁止修改答案。")
    question = next((q for q in record["questions"] if q["id"] == question_id), None)
    if question is None:
        raise ValueError("未找到对应的考试题目。")

    ts = _utc_now(now_utc)
    normalized = _normalize_incoming_answer(question, answer, "manual", ts)
    if not question_answered(question, normalized):
        # 用户清空本题内容时保留 manual 来源或置空，但仍按 manual 记录本次编辑时间
        normalized["source"] = "manual"

    record["answers"][question_id] = normalized
    if record["state"] != STATE_LOCAL_DRAFT:
        record["prev_state"] = record["state"]
        record["state"] = STATE_LOCAL_DRAFT
    record["updated_at"] = ts
    validate_draft(record)
    return normalized


def apply_suggestion(
    record: dict,
    question_id: str,
    answer: dict,
    source: str,
    now_utc: str = "",
    *,
    replace_manual: bool = False,
) -> bool:
    """写入题库或 AI 的参考答案；当原答案为 manual 且 replace_manual=False 时跳过并返回 False。"""
    validate_draft(record)
    if record["state"] not in EDITABLE_STATES:
        raise ValueError(f"当前答卷状态为「{state_text(record['state'])}」，禁止写入参考答案。")
    if not (source == "ai" or (isinstance(source, str) and source.startswith("bank:") and _valid_source(source))):
        raise ValueError("参考答案来源必须为 'ai' 或 'bank:<服务ID>'。")
    question = next((q for q in record["questions"] if q["id"] == question_id), None)
    if question is None:
        raise ValueError("未找到对应的考试题目。")

    current = record["answers"].get(question_id)
    if current and current.get("source") == "manual" and not replace_manual:
        return False

    ts = _utc_now(now_utc)
    normalized = _normalize_incoming_answer(question, answer, source, ts)
    record["answers"][question_id] = normalized
    if record["state"] != STATE_LOCAL_DRAFT:
        record["prev_state"] = record["state"]
        record["state"] = STATE_LOCAL_DRAFT
    record["updated_at"] = ts
    validate_draft(record)
    return True


def merge_platform_answers(
    local_record: dict | None,
    platform_paper: dict,
    platform_answers: dict | None,
    now_utc: str = "",
) -> tuple[dict, bool]:
    """合并平台开卷数据与本地草稿。

    返回 ``(merged_record, restored_unsynced_local)``：
    - 若本地无草稿，则由平台试卷与平台已存答案生成初始草稿（有平台答案时状态为 platform_saved，否则为 local_draft）。
    - 若本地草稿处于 submit_unknown / submitted / simulated_submitted，保留本地状态与答案，仅同步题目列表与截止时间。
    - 若本地草稿为 local_draft（有未同步到平台的修改），优先保留本地已答或 manual 的答案；对于各题，
      当平台答案与本地答案同时存在且均带时间戳时，若本地为 local_draft 则保留本地未同步修改并返回 restored_unsynced_local=True；
      若本地为 platform_saved，则以更新时间较新的一方为准（平台无时间戳或更新时采用平台答案）。
    """
    ts = _utc_now(now_utc)
    platform_answers = platform_answers or {}

    if local_record is None:
        questions = copy.deepcopy(platform_paper["questions"])
        questions_by_id = {q["id"]: q for q in questions}
        answers = {}
        has_platform_ans = False
        for qid, raw_ans in platform_answers.items():
            if qid not in questions_by_id or not isinstance(raw_ans, dict):
                continue
            q = questions_by_id[qid]
            ans_ts = raw_ans.get("updated_at") if isinstance(raw_ans.get("updated_at"), str) and raw_ans.get("updated_at") else ts
            ans_src = raw_ans.get("source") if _valid_source(raw_ans.get("source", "")) and raw_ans.get("source") else "platform"
            norm = _normalize_incoming_answer(q, raw_ans, ans_src, ans_ts)
            if question_answered(q, norm):
                has_platform_ans = True
                answers[qid] = norm
        record = {
            "version": 1,
            "revision": 0,
            "account_id": str(platform_paper["account_id"]).strip(),
            "uid": str(platform_paper["uid"]).strip(),
            "course_id": str(platform_paper["course_id"]).strip(),
            "clazz_id": str(platform_paper["clazz_id"]).strip(),
            "exam_id": str(platform_paper["exam_id"]).strip(),
            "attempt_id": str(platform_paper["attempt_id"]).strip(),
            "demo": bool(platform_paper.get("demo", False)),
            "title": str(platform_paper["title"]).strip(),
            "deadline_utc": str(platform_paper.get("deadline_utc", "")).strip(),
            "state": STATE_PLATFORM_SAVED if has_platform_ans else STATE_LOCAL_DRAFT,
            "prev_state": "",
            "reason": "",
            "updated_at": ts,
            "questions": questions,
            "answers": answers,
        }
        validate_draft(record)
        return record, False

    validate_draft(local_record)
    merged = copy.deepcopy(local_record)
    if "questions" in platform_paper and platform_paper["questions"]:
        merged["questions"] = copy.deepcopy(platform_paper["questions"])
    if "title" in platform_paper and str(platform_paper["title"]).strip():
        merged["title"] = str(platform_paper["title"]).strip()
    if "deadline_utc" in platform_paper:
        merged["deadline_utc"] = str(platform_paper.get("deadline_utc", "")).strip()

    questions_by_id = {q["id"]: q for q in merged["questions"]}
    # 清理不再存在于试卷中的题目答案
    merged["answers"] = {qid: ans for qid, ans in merged["answers"].items() if qid in questions_by_id}

    if merged["state"] in (STATE_SUBMIT_UNKNOWN, STATE_SUBMITTED, STATE_SIMULATED_SUBMITTED):
        validate_draft(merged)
        return merged, False

    restored_unsynced = merged["state"] == STATE_LOCAL_DRAFT and bool(merged["answers"])

    for qid, raw_plat in platform_answers.items():
        if qid not in questions_by_id or not isinstance(raw_plat, dict):
            continue
        q = questions_by_id[qid]
        plat_ts = raw_plat.get("updated_at") if isinstance(raw_plat.get("updated_at"), str) and raw_plat.get("updated_at") else ""
        plat_src = raw_plat.get("source") if _valid_source(raw_plat.get("source", "")) and raw_plat.get("source") else "platform"
        norm_plat = _normalize_incoming_answer(q, raw_plat, plat_src, plat_ts or ts)
        if not question_answered(q, norm_plat):
            continue

        local_ans = merged["answers"].get(qid)
        if local_ans is None or not question_answered(q, local_ans):
            merged["answers"][qid] = norm_plat
            continue

        # 本地与平台均有答案：
        # 1. 若本地草稿处于 local_draft（本机有未同步修改），且平台没有比本地更新的明确时间戳，则保留本地未同步草稿
        local_ts = local_ans.get("updated_at", "")
        if merged["state"] == STATE_LOCAL_DRAFT:
            if plat_ts and local_ts and plat_ts > local_ts:
                merged["answers"][qid] = norm_plat
            else:
                restored_unsynced = True
        else:
            # 本地处于 platform_saved 或 rejected：比较更新时间，以较新的一方为准
            if not local_ts or (plat_ts and plat_ts >= local_ts):
                merged["answers"][qid] = norm_plat

    validate_draft(merged)
    return merged, restored_unsynced


class ExamDraftStore:
    """管理数据目录 exam_drafts/ 下的答卷持久化、乐观锁与交卷防重状态机。"""

    CORRUPT_MESSAGE = "考试草稿损坏或不可读；原文件已保留，请先在官方入口核验。"

    def __init__(self, root: Path | str):
        self.root = Path(root) / "exam_drafts"

    def path(self, key: str) -> Path:
        if not isinstance(key, str) or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("考试草稿存储键格式无效。")
        return self.root / f"{key}.json"

    def _existing_files(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(p for p in self.root.glob("*.json") if p.is_file() and not p.is_symlink())

    def read(self, key: str) -> tuple[dict | None, bytes | None]:
        """读取单份考试草稿，返回 ``(record, raw_bytes)``；文件不存在返回 ``(None, None)``；损坏或超限时 fail-closed。"""
        path = self.path(key)
        raw = revision(path)
        if raw is None:
            return None, None
        try:
            if len(raw) > MAX_FILE_BYTES:
                raise ValueError()
            record = json.loads(raw.decode("utf-8"))
            validate_draft(record)
            expected_key = exam_draft_key(
                record["account_id"],
                record["uid"],
                record["course_id"],
                record["clazz_id"],
                record["exam_id"],
                record["attempt_id"],
            )
            if expected_key != key:
                raise ValueError()
            return record, raw
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            raise ValueError(self.CORRUPT_MESSAGE) from None

    def _check_capacity_before_write(self, target_path: Path, new_data_len: int) -> None:
        if new_data_len > MAX_FILE_BYTES:
            raise ValueError("单份考试草稿超过 512 KiB 容量上限；原文件已保留。")
        files = self._existing_files()
        is_new = not target_path.exists()
        if is_new and len(files) >= MAX_FILES:
            raise ValueError("考试草稿数量已达 100 份上限；请先备份并清理已交卷的旧草稿。")
        total_bytes = new_data_len
        for p in files:
            if p != target_path:
                try:
                    total_bytes += p.stat().st_size
                except OSError:
                    continue
        if total_bytes > MAX_TOTAL_BYTES:
            raise ValueError("考试草稿总容量已达 16 MiB 上限；请先备份并清理已交卷的旧草稿。")

    def save(self, record: dict, expected_raw: bytes | None) -> tuple[dict, bytes]:
        """带乐观锁与容量检查地保存考试草稿，自动递增 revision。"""
        validate_draft(record)
        key = exam_draft_key(
            record["account_id"],
            record["uid"],
            record["course_id"],
            record["clazz_id"],
            record["exam_id"],
            record["attempt_id"],
        )
        path = self.path(key)
        existing, current_raw = self.read(key)
        if current_raw != expected_raw:
            raise ValueError("考试草稿已被另一个窗口或进程修改，原文件已保留。请重新打开试卷后重试。")
        expected_rev = existing["revision"] if existing is not None else 0
        if record["revision"] != expected_rev:
            raise ValueError("考试草稿版本冲突，原文件已保留。请重新打开试卷后重试。")
        if existing is not None and existing["state"] == STATE_SUBMIT_UNKNOWN and record["state"] != STATE_SUBMIT_UNKNOWN:
            raise ValueError("该答卷处于「提交结果待核验」状态，禁止通过普通保存覆盖。")

        updated = copy.deepcopy(record)
        updated["revision"] = expected_rev + 1
        updated["updated_at"] = _utc_now(updated.get("updated_at", ""))
        validate_draft(updated)

        data = json.dumps(updated, ensure_ascii=False).encode("utf-8")
        self._check_capacity_before_write(path, len(data))
        write_if_unchanged(path, data, expected_raw)
        return updated, data

    def begin_submit(self, key: str, expected_raw: bytes | None, now_utc: str = "") -> tuple[dict, bytes]:
        """交卷前原子写入 state='submit_unknown' 并记录 prev_state。

        仅允许从 local_draft、platform_saved 或 rejected 发起交卷；
        若当前已是 submit_unknown、submitted 或 simulated_submitted，立即拒绝并抛 ValueError。
        """
        path = self.path(key)
        with file_lock(path):
            record, current_raw = self.read(key)
            if record is None:
                raise ValueError("未找到对应的本机考试草稿，无法记录交卷意图。")
            if current_raw != expected_raw:
                raise ValueError("考试草稿已被修改，未发送交卷请求；请重新核对答卷后再试。")
            if record["state"] == STATE_SUBMIT_UNKNOWN:
                raise ValueError("上次交卷结果尚待核验，禁止重复提交；请先点击「核验交卷结果」或到官方入口确认。")
            if record["state"] in FINISHED_STATES:
                raise ValueError("该试卷已交卷，禁止重复提交。")
            if record["state"] not in EDITABLE_STATES:
                raise ValueError(f"当前状态「{state_text(record['state'])}」不允许交卷。")

            updated = copy.deepcopy(record)
            updated["prev_state"] = record["state"]
            updated["state"] = STATE_SUBMIT_UNKNOWN
            updated["reason"] = "交卷请求已发出，正在等待或核验平台回执。"
            updated["revision"] = record["revision"] + 1
            updated["updated_at"] = _utc_now(now_utc)
            validate_draft(updated)

            data = json.dumps(updated, ensure_ascii=False).encode("utf-8")
            self._check_capacity_before_write(path, len(data))
            from .storage import atomic_write
            atomic_write(path, data)
            return updated, data

    def settle_submit(self, key: str, outcome: str, reason: str = "", now_utc: str = "") -> tuple[dict, bytes]:
        """根据交卷响应更新状态：outcome 取值为 'submitted'、'simulated_submitted'、'rejected' 或 'unknown'。"""
        if outcome not in ("submitted", "simulated_submitted", "rejected", "unknown"):
            raise ValueError("未知的交卷结算结果类型。")
        path = self.path(key)
        with file_lock(path):
            record, _ = self.read(key)
            if record is None:
                raise ValueError("未找到对应的考试交卷意图记录。")
            if record["state"] != STATE_SUBMIT_UNKNOWN:
                raise ValueError(f"当前状态为「{state_text(record['state'])}」，不存在待结算的交卷意图。")

            updated = copy.deepcopy(record)
            if outcome == "submitted":
                updated["state"] = STATE_SUBMITTED
                updated["reason"] = reason[:1000] if reason else ""
            elif outcome == "simulated_submitted":
                updated["state"] = STATE_SIMULATED_SUBMITTED
                updated["reason"] = reason[:1000] if reason else "离线演示模拟交卷完成。"
            elif outcome == "rejected":
                updated["state"] = STATE_REJECTED
                updated["reason"] = reason[:1000] if reason else "平台明确拒绝本次交卷，请检查答卷或时限。"
            else:
                updated["state"] = STATE_SUBMIT_UNKNOWN
                updated["reason"] = (
                    reason[:1000]
                    if reason
                    else "交卷响应超时或未确认，请点击「核验交卷结果」或到官方入口确认。"
                )
            updated["revision"] = record["revision"] + 1
            updated["updated_at"] = _utc_now(now_utc)
            validate_draft(updated)

            data = json.dumps(updated, ensure_ascii=False).encode("utf-8")
            self._check_capacity_before_write(path, len(data))
            from .storage import atomic_write
            atomic_write(path, data)
            return updated, data

    def settle_verify(self, key: str, confirmed: bool, reason: str = "", now_utc: str = "") -> tuple[dict, bytes]:
        """只读核验交卷结果：仅当 confirmed=True 时将 submit_unknown 转为 submitted；confirmed=False 时保持 submit_unknown。"""
        path = self.path(key)
        with file_lock(path):
            record, _ = self.read(key)
            if record is None:
                raise ValueError("未找到对应的考试草稿记录。")
            if record["state"] == STATE_SUBMITTED and confirmed:
                data = path.read_bytes()
                return record, data
            if record["state"] != STATE_SUBMIT_UNKNOWN:
                raise ValueError(f"只有「提交结果待核验」的答卷需要核验（当前：{state_text(record['state'])}）。")

            updated = copy.deepcopy(record)
            if confirmed:
                updated["state"] = STATE_SUBMITTED
                updated["reason"] = reason[:1000] if reason else "已通过只读核验确认平台收卷。"
            else:
                updated["state"] = STATE_SUBMIT_UNKNOWN
                updated["reason"] = (
                    reason[:1000]
                    if reason
                    else "只读核验未发现明确收卷回执；为防重复交卷，状态保持待核验，请到官方入口确认。"
                )
            updated["revision"] = record["revision"] + 1
            updated["updated_at"] = _utc_now(now_utc)
            validate_draft(updated)

            data = json.dumps(updated, ensure_ascii=False).encode("utf-8")
            self._check_capacity_before_write(path, len(data))
            from .storage import atomic_write
            atomic_write(path, data)
            return updated, data

    def delete_finished(self, key: str, expected_raw: bytes | None) -> None:
        """仅允许删除已交卷（submitted 或 simulated_submitted）的旧记录以释放容量；拒绝删除未决或未交卷草稿。"""
        path = self.path(key)
        with file_lock(path):
            record, current_raw = self.read(key)
            if record is None:
                raise ValueError("要删除的考试草稿不存在。")
            if current_raw != expected_raw:
                raise ValueError("考试草稿已变化，未执行删除；请刷新后重试。")
            if record["state"] not in FINISHED_STATES:
                raise ValueError(
                    f"仅允许清理已交卷的旧记录；当前状态为「{state_text(record['state'])}」，禁止删除。"
                )
            path.unlink()
