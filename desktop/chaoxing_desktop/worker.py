from __future__ import annotations

from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import json
import os
from pathlib import Path
import re
import sys
import threading
import queue
import uuid
from .control import RunControl, Cancelled, ResumeFailed, checkpoint, wait as controlled_wait
from .image_cache import PAPER_IMAGE_ACTIONS, paper_image_cache, profile_image_cache, remove_image_cache
from .query_result import safe_record

from .config import Redactor, course_key, resource_root, select_courses


class Channel:
    def __init__(self, output, input_stream, redactor):
        self.output, self.input_stream, self.redactor = output, input_stream, redactor
        self.lock = threading.Lock()
        self.input_lock = threading.Lock()
        self.run_id = uuid.uuid4().hex
        self.control = None
        self.image_cache_dir = None

    def send(self, event_type: str, **values):
        if "message" in values:
            values["message"] = self.redactor(str(values["message"]))
            if event_type == "log" and len(values["message"]) > 4096:
                values["message"] = values["message"][:4075] + " …[日志过长，已截断]"
        if event_type == 'question_result':
            values = safe_record(values, self.redactor)
        if event_type == 'discussion_result':
            from .discussion_model import safe_record as safe_discussion
            values = dict(record=safe_discussion(values['record'], self.redactor))
        if event_type in ('activity_result', 'activity_preview'):
            from .activity_model import safe_record as safe_activity
            values = dict(record=safe_activity(values['record'], self.redactor))
        values.setdefault('run_id', self.run_id)
        with self.lock:
            self.output.write(json.dumps({"type": event_type, **values}, ensure_ascii=False) + "\n")
            self.output.flush()

    def enable_control(self):
        self.control = RunControl(self.send)
        threading.Thread(target=self.control.read, args=(self.input_stream,), daemon=True).start()

    def ask(self, prompt="", **context):
        with self.input_lock:
            request_id = uuid.uuid4().hex
            self.send("input", message=prompt, request_id=request_id, **context)
            if self.control:
                while True:
                    self.control.checkpoint()
                    try:
                        reply = self.control.replies.get(timeout=.1)
                    except queue.Empty:
                        if self.control.eof:
                            raise Cancelled()
                        continue
                    if reply.get('request_id') == request_id:
                        return str(reply.get('answer', ''))
            line = self.input_stream.readline()
            if not line:
                raise EOFError("交互已取消")
            reply = json.loads(line)
            if reply.get("cancel"):
                # Exit the whole worker: upstream catches EOFError in some manual modes.
                self.send("cancelled", message="用户取消交互，任务停止。")
                self.output.flush()
                os._exit(2)
            return str(reply.get("answer", ""))


class EventStream:
    encoding = "utf-8"
    def __init__(self, channel):
        self.channel = channel

    def write(self, text):
        if text.strip():
            self.channel.send("log", message=text.strip(), level="INFO")
        return len(text)

    def flush(self):
        pass

    def isatty(self):
        return False


def load_core(channel):
    core_path = resource_root() / "vendor/chaoxing"
    sys.path.insert(0, str(core_path))
    # Imports and upstream print calls must never corrupt the JSON protocol.
    sys.stdout = EventStream(channel)
    sys.stderr = EventStream(channel)
    from api.logger import logger
    logger.remove()
    logger.add(lambda msg: channel.send("log", message=msg.record["message"], level=msg.record["level"].name),
               level="INFO", colorize=False, backtrace=False, diagnose=False)
    from api.cxsecret_font import validate_font_resources
    validate_font_resources()
    from . import runtime
    return runtime


def _synthetic_png(color: int = 0x2563EB) -> bytes:
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QImage
    picture = QImage(32, 20, QImage.Format.Format_RGB32)
    picture.fill(color)
    stream = QBuffer()
    stream.open(QIODevice.OpenModeFlag.WriteOnly)
    if not picture.save(stream, "PNG"):
        raise RuntimeError("合成演示 PNG 失败")
    return bytes(stream.data())


def demo_exam_list() -> list[dict]:
    return [
        {
            "examId": "demo-exam-101",
            "courseId": "demo-101",
            "clazzId": "01",
            "courseTitle": "Python 程序设计 · 演示",
            "title": "Python 程序设计期中测试（演示）",
            "timeText": "进行中 · 60 分钟",
            "modeText": "线上开卷 · 允许 1 次",
            "status": "ready",
            "statusText": "可进入",
            "nativeSupported": True,
            "reason": "",
            "officialUrl": "https://mooc1.chaoxing.com/exam-ans/demo-101",
        },
        {
            "examId": "demo-exam-102",
            "courseId": "demo-102",
            "clazzId": "02",
            "courseTitle": "大学英语 · 演示",
            "title": "大学英语期末考试（演示）",
            "timeText": "未开始 · 12-20 09:00",
            "modeText": "限时闭卷 · 90 分钟",
            "status": "not_started",
            "statusText": "未开始",
            "nativeSupported": False,
            "reason": "考试尚未到达开放时间，暂不可进入。",
            "officialUrl": "https://mooc1.chaoxing.com/exam-ans/demo-102",
        },
        {
            "examId": "demo-exam-103",
            "courseId": "demo-103",
            "clazzId": "03",
            "courseTitle": "信息素养 · 演示",
            "title": "信息素养随堂测验（演示）",
            "timeText": "已结束 · 11-01 18:00",
            "modeText": "线上测试 · 已截止",
            "status": "ended",
            "statusText": "已结束",
            "nativeSupported": False,
            "reason": "该场考试已过截止时间。",
            "officialUrl": "https://mooc1.chaoxing.com/exam-ans/demo-103",
        },
        {
            "examId": "demo-exam-104",
            "courseId": "demo-101",
            "clazzId": "01",
            "courseTitle": "Python 程序设计 · 演示",
            "title": "Python 综合监考机考（演示）",
            "timeText": "进行中 · 120 分钟",
            "modeText": "指定客户端 · 人脸识别 + 切屏检测",
            "status": "proctored",
            "statusText": "需官方入口",
            "nativeSupported": False,
            "reason": "该场次要求指定考试客户端、人脸识别与切屏监考，不支持在桌面工作台内答题，请打开官方入口。",
            "officialUrl": "https://mooc1.chaoxing.com/exam-ans/demo-104",
        },
    ]


def demo_exam(channel, command, *, suggestion_event="exam_suggestion", done_event="exam_assist_done"):
    from datetime import datetime, timedelta, timezone
    action = command["action"]
    if action == "exam_list":
        controlled_wait(channel, 0.1)
        channel.send("exam_list", exams=demo_exam_list(), demo=True)
        return
    if action == "exam_open":
        exam_id = str(command.get("exam_id") or "demo-exam-101")
        entry = next((item for item in demo_exam_list() if item["examId"] == exam_id), None)
        if entry is None:
            raise ValueError("未找到对应的演示考试场次。")
        if not entry["nativeSupported"]:
            raise ValueError(entry["reason"] or "该场考试不支持在桌面工作台内作答。")
        now = datetime.now(timezone.utc)
        deadline = (now + timedelta(minutes=60)).isoformat(timespec="seconds")
        image_uris = []
        if channel.image_cache_dir is not None:
            img_path = channel.image_cache_dir / "demo_exam_q105.png"
            img_path.write_bytes(_synthetic_png())
            image_uris.append(img_path.as_uri())
        questions = [
            {
                "id": "101",
                "no": 1,
                "type": "single",
                "title": "在 Python 中，用于定义函数的关键字是哪一个？",
                "options": [
                    {"id": "A", "text": "def"},
                    {"id": "B", "text": "function"},
                    {"id": "C", "text": "func"},
                    {"id": "D", "text": "lambda"},
                ],
                "blank_count": 0,
                "score": "20",
            },
            {
                "id": "102",
                "no": 2,
                "type": "multiple",
                "title": "以下属于 Python 内置可变容器类型的有哪些？",
                "options": [
                    {"id": "A", "text": "list"},
                    {"id": "B", "text": "dict"},
                    {"id": "C", "text": "tuple"},
                    {"id": "D", "text": "set"},
                ],
                "blank_count": 0,
                "score": "20",
            },
            {
                "id": "103",
                "no": 3,
                "type": "judgement",
                "title": "Python 中的列表（list）可以作为字典（dict）的键。",
                "options": [
                    {"id": "正确", "text": "正确"},
                    {"id": "错误", "text": "错误"},
                ],
                "blank_count": 0,
                "score": "20",
            },
            {
                "id": "104",
                "no": 4,
                "type": "completion",
                "title": "HTTP 协议默认端口为 ____，HTTPS 协议默认端口为 ____。",
                "options": [],
                "blank_count": 2,
                "score": "20",
            },
            {
                "id": "105",
                "no": 5,
                "type": "single",
                "title": "观察附图所示的复杂度曲线，二分查找的平均时间复杂度为？",
                "options": [
                    {"id": "A", "text": "O(1)"},
                    {"id": "B", "text": "O(log n)"},
                    {"id": "C", "text": "O(n)"},
                    {"id": "D", "text": "O(n^2)"},
                ],
                "blank_count": 0,
                "score": "20",
                "images": image_uris,
            },
        ]
        paper = {
            "course_id": entry["courseId"],
            "clazz_id": entry["clazzId"],
            "course_title": entry["courseTitle"],
            "exam_id": entry["examId"],
            "attempt_id": "demo-attempt-1",
            "title": entry["title"],
            "deadline_utc": deadline,
            "demo": True,
            "questions": questions,
        }
        controlled_wait(channel, 0.1)
        channel.send(
            "exam_opened",
            paper=paper,
            server_now_utc=now.isoformat(timespec="seconds"),
            platform_answers={},
        )
        return
    if action == "exam_assist":
        config = command.get("config") or {}
        ai_cfg = config.get("ai") or {}
        bank_cfg = config.get("question_bank") or {}
        questions = command.get("questions") or []
        if not isinstance(questions, list):
            raise ValueError("待查询题目列表无效。")
        providers = [p.strip() for p in str(bank_cfg.get("providers", "")).split(",") if p.strip()]
        bank_provider = providers[0] if providers else "TikuDemo"
        bank_source = f"bank:{bank_provider}"
        policy = str(ai_cfg.get("image_confirm_policy", "ask")).strip().lower()
        vision_raw = str(ai_cfg.get("vision", "true")).strip().lower()
        vision_enabled = vision_raw != "false" or str(ai_cfg.get("enabled", "false")).strip().lower() != "true"
        image_questions = [q for q in questions if isinstance(q, dict) and (q.get("images") or "<img" in str(q.get("title", "")))]
        allow_images = True
        if image_questions:
            if not vision_enabled:
                allow_images = False
            elif policy == "always_skip":
                allow_images = False
            elif policy == "always_allow":
                allow_images = True
            else:
                endpoint = str(ai_cfg.get("endpoint") or "https://demo-ai.invalid/v1")
                img_count = sum(max(1, len(q.get("images") or [])) for q in image_questions)
                choice = channel.ask(
                    f"允许把本批 {len(image_questions)} 道含图考试题及 {img_count} 处图片发送给以下 AI 服务？\n{endpoint}",
                    kind="choice",
                    choices=[
                        {"label": "跳过图片题的 AI 补全", "value": "skip"},
                        {"label": "发送图片给 AI", "value": "allow-images"},
                    ],
                )
                allow_images = choice == "allow-images"
        resolved = 0
        ai_requests = 0
        limit = int(ai_cfg.get("request_limit") or 0)
        for q in questions:
            checkpoint(channel)
            if not isinstance(q, dict):
                continue
            qid = str(q.get("id", ""))
            qtype = str(q.get("type", ""))
            has_img = bool(q.get("images") or "<img" in str(q.get("title", "")))
            if qid == "101" or (qtype == "single" and not has_img):
                opt_id = "A" if any(o.get("id") == "A" for o in q.get("options", [])) else (q["options"][0]["id"] if q.get("options") else "A")
                channel.send(
                    suggestion_event,
                    question_id=qid,
                    answer={"option_ids": [opt_id], "blanks": [], "text": ""},
                    source=bank_source,
                    reason="演示题库命中，选项核验通过。",
                    attempts=[{"provider": bank_provider, "status": "hit", "reason": "演示题库命中"}],
                )
                resolved += 1
            elif qid == "102" or qtype == "multiple":
                opts = [o["id"] for o in q.get("options", []) if o.get("id") in ("A", "B", "D")] or [o["id"] for o in q.get("options", [])[:2]]
                channel.send(
                    suggestion_event,
                    question_id=qid,
                    answer={"option_ids": opts, "blanks": [], "text": ""},
                    source=bank_source,
                    reason="演示题库命中多选选项。",
                    attempts=[{"provider": bank_provider, "status": "hit", "reason": "演示题库命中"}],
                )
                resolved += 1
            elif qid == "103" or qtype == "judgement":
                channel.send(
                    suggestion_event,
                    question_id=qid,
                    answer={"option_ids": ["错误"], "blanks": [], "text": ""},
                    source=bank_source,
                    reason="演示题库判断核验通过。",
                    attempts=[{"provider": bank_provider, "status": "hit", "reason": "演示题库命中"}],
                )
                resolved += 1
            elif qid == "104" or qtype == "completion":
                blank_count = int(q.get("blank_count") or 2)
                blanks = ["80", "443"][:blank_count] + ["演示答案"] * max(0, blank_count - 2)
                ai_requests += 1
                channel.send("ai_usage", requests=ai_requests, limit=limit, known=ai_requests, unknown=0, tokens=ai_requests * 60)
                channel.send(
                    suggestion_event,
                    question_id=qid,
                    answer={"option_ids": [], "blanks": blanks, "text": ""},
                    source="ai",
                    reason="演示 AI 生成逐空参考答案，请核对。",
                    attempts=[{"provider": "AI", "status": "hit", "reason": "演示 AI 生成参考答案"}],
                )
                resolved += 1
            elif qid == "105" or has_img:
                if not allow_images:
                    reason = "视觉识图已关闭，未请求 AI。" if not vision_enabled else "已跳过向 AI 发送图片题。"
                    channel.send(
                        suggestion_event,
                        question_id=qid,
                        answer={"option_ids": [], "blanks": [], "text": ""},
                        source="",
                        reason=reason,
                        attempts=[{"provider": "AI", "status": "skipped", "reason": reason}],
                    )
                else:
                    opt_id = "B" if any(o.get("id") == "B" for o in q.get("options", [])) else (q["options"][0]["id"] if q.get("options") else "B")
                    ai_requests += 1
                    channel.send("ai_usage", requests=ai_requests, limit=limit, known=ai_requests, unknown=0, tokens=ai_requests * 60)
                    channel.send(
                        suggestion_event,
                        question_id=qid,
                        answer={"option_ids": [opt_id], "blanks": [], "text": ""},
                        source="ai",
                        reason="演示 AI 视觉识图返回参考答案，请核对。",
                        attempts=[{"provider": "AI", "status": "hit", "reason": "演示 AI 视觉识图命中"}],
                    )
                    resolved += 1
            else:
                channel.send(
                    suggestion_event,
                    question_id=qid,
                    answer={"option_ids": [], "blanks": [], "text": "演示简答参考答案"},
                    source="ai",
                    reason="演示 AI 生成简答参考。",
                    attempts=[{"provider": "AI", "status": "hit", "reason": "演示 AI 生成参考答案"}],
                )
                resolved += 1
        channel.send(
            done_event,
            completed=len(questions),
            total=len(questions),
            resolved=resolved,
            message=f"参考答案查询完成：共处理 {len(questions)} 题，生成 {resolved} 题参考答案。",
        )
        return
    if action == "exam_submit":
        exam_id = str(command.get("exam_id") or "demo-exam-101")
        attempt_id = str(command.get("attempt_id") or "demo-attempt-1")
        controlled_wait(channel, 0.1)
        channel.send(
            "exam_submitted",
            exam_id=exam_id,
            attempt_id=attempt_id,
            state="simulated_submitted",
            reason="离线演示模拟交卷完成，未向超星平台发送任何请求。",
        )
        return


def demo_homework(channel, command):
    action = command["action"]
    if action == "homework_list":
        channel.send(
            "homework_list",
            demo=True,
            homeworks=[
                {
                    "workId": "demo-work-101",
                    "courseId": "demo-course-1",
                    "clazzId": "demo-clazz-1",
                    "courseTitle": "数据结构与算法（演示）",
                    "title": "第三次课后作业：二叉树与图",
                    "status": "todo",
                    "statusText": "未交",
                    "timeText": "剩余 48 小时",
                    "workUrl": "https://mooc1.chaoxing.com/mooc-ans/mooc2/work/dowork?courseId=demo-course-1&workId=demo-work-101",
                    "canOpen": True,
                    "reason": "",
                },
                {
                    "workId": "demo-work-102",
                    "courseId": "demo-course-1",
                    "clazzId": "demo-clazz-1",
                    "courseTitle": "数据结构与算法（演示）",
                    "title": "第二次课后作业：栈与队列",
                    "status": "submitted",
                    "statusText": "待批阅",
                    "timeText": "",
                    "workUrl": "https://mooc1.chaoxing.com/mooc-ans/mooc2/work/task?courseId=demo-course-1&workId=demo-work-102",
                    "canOpen": False,
                    "reason": "当前状态为「待批阅」，请在官方入口查看。",
                },
            ],
        )
        return
    if action == "homework_load":
        image_uris = []
        if channel.image_cache_dir is not None:
            img_path = channel.image_cache_dir / "demo_hw_q5.png"
            img_path.write_bytes(_synthetic_png())
            image_uris.append(img_path.as_uri())
        questions = [
            {
                "id": "hw-101",
                "no": 1,
                "type": "single",
                "type_name": "单选题",
                "title": "在 HTTP 协议中，表示资源未找到的状态码是？",
                "options": [
                    {"id": "A", "text": "200"},
                    {"id": "B", "text": "404"},
                    {"id": "C", "text": "500"},
                ],
                "blank_count": 0,
                "supported": True,
                "reason": "",
            },
            {
                "id": "hw-102",
                "no": 2,
                "type": "multiple",
                "type_name": "多选题",
                "title": "以下属于传输层协议的有哪些？",
                "options": [
                    {"id": "A", "text": "TCP"},
                    {"id": "B", "text": "UDP"},
                    {"id": "C", "text": "ICMP"},
                ],
                "blank_count": 0,
                "supported": True,
                "reason": "",
            },
            {
                "id": "hw-103",
                "no": 3,
                "type": "judgement",
                "type_name": "判断题",
                "title": "HTTPS 在 HTTP 之上增加了 TLS/SSL 加密传输层。",
                "options": [
                    {"id": "正确", "text": "正确"},
                    {"id": "错误", "text": "错误"},
                ],
                "blank_count": 0,
                "supported": True,
                "reason": "",
            },
            {
                "id": "hw-104",
                "no": 4,
                "type": "completion",
                "type_name": "填空题",
                "title": "IPv4 地址由 ____ 位二进制组成，IPv6 地址由 ____ 位二进制组成。",
                "options": [],
                "blank_count": 2,
                "supported": True,
                "reason": "",
            },
            {
                "id": "hw-105",
                "no": 5,
                "type": "single",
                "type_name": "单选题",
                "title": "观察附图，该网络拓扑属于哪一类？",
                "options": [
                    {"id": "A", "text": "星型拓扑"},
                    {"id": "B", "text": "总线拓扑"},
                    {"id": "C", "text": "环型拓扑"},
                ],
                "blank_count": 0,
                "images": image_uris,
                "supported": True,
                "reason": "",
            },
            {
                "id": "hw-106",
                "no": 6,
                "type": "unsupported",
                "type_name": "附件题",
                "title": "请上传本次实验的抓包分析报告（.pcapng / .pdf）。",
                "options": [],
                "blank_count": 0,
                "supported": False,
                "reason": "暂不支持「附件题」在本机草稿中作答，请按原题单独处理。",
            },
        ]
        controlled_wait(channel, 0.1)
        channel.send(
            "homework_loaded",
            demo=True,
            homework={
                "title": "计算机网络独立作业 · 离线演示",
                "url": "",
                "course_id": "demo-101",
                "clazz_id": "01",
                "work_id": "demo-hw-1",
                "questions": questions,
            },
        )
        return
    if action == "homework_assist":
        demo_exam(
            channel,
            {
                **command,
                "action": "exam_assist",
                "questions": [q for q in (command.get("questions") or []) if q.get("supported", True) and q.get("type") != "unsupported"],
            },
            suggestion_event="homework_suggestion",
            done_event="homework_assist_done",
        )
        return


def demo(channel, command):
    if str(command.get("action", "")).startswith("exam_"):
        demo_exam(channel, command)
        return
    if str(command.get("action", "")).startswith("homework_"):
        demo_homework(channel, command)
        return
    courses = [dict(courseId="demo-101", clazzId="01", cpi="demo", title="Python 程序设计 · 演示"),
               dict(courseId="demo-102", clazzId="02", cpi="demo", title="大学英语 · 演示"),
               dict(courseId="demo-103", clazzId="03", cpi="demo", title="信息素养 · 演示")]
    channel.send("log", message="离线演示模式：所有课程与任务均为示例，没有连接超星。", level="INFO")
    if command["action"] == "courses":
        controlled_wait(channel, .2)
        channel.send("courses", courses=courses, demo=True)
        return
    chosen = select_courses(courses, command.get("selected", []))
    tasks = [dict(id=f"{c['courseId']}:{i}", course=c["title"], title=t,
                  courseId=c["courseId"], clazzId=c["clazzId"], pointId=str(i))
             for c in chosen for i, t in enumerate(("01 · 音频学习" if c['courseId'] == 'demo-102' else "01 · 视频学习",
                                                     "02 · 阅读材料", "03 · 章节练习"))]
    resume = command.get('resume')
    completed = set()
    if resume is not None:
        # Mirror the real continuation contract so the offline flow exercises
        # the same narrowing: only the retained task ids run.
        if (not isinstance(resume, dict) or set(resume) != {'tasks', 'courses', 'account', 'uid'}
                or not isinstance(resume['tasks'], list)):
            raise ValueError('续跑范围无效，请重新读取课程后手动开始。')
        wanted = {row['taskId'] for row in resume['tasks']}
        completed = {row['taskId'] for row in resume['tasks'] if row['completed']}
        if wanted:
            tasks = [task for task in tasks if task['id'] in wanted]
    channel.send("tasks", tasks=tasks)
    for task in tasks:
        checkpoint(channel)
        if task['id'] in completed:
            channel.send("task", id=task["id"], status="success")
            continue
        channel.send("task", id=task["id"], status="running")
        channel.send("log", message=f"正在处理 {task['course']} / {task['title']}", level="INFO")
        if task['pointId'] == '0':
            audio = task['courseId'] == 'demo-102'
            duration = 75 if audio else 120
            for position in (0, duration // 3, 2 * duration // 3, duration):
                channel.send('media', id=task['id'], name='离线演示音频' if audio else '离线演示视频',
                             kind='Audio' if audio else 'Video', duration=duration, position=position,
                             verified=position == duration)
                controlled_wait(channel, .15)
        else:
            controlled_wait(channel, .6)
        if '练习' in task['title']:
            from types import SimpleNamespace
            from .answers import AIError, AnswerRouter, ChoiceMatch, option_pairs
            config = command['config']
            demo_bank = [('演示题库', SimpleNamespace(_query=lambda q: '第二项'))]
            question = dict(id='demo-question', title='以下哪项是演示答案？', type='single', options='A.第一项\nB.第二项')
            ai = None
            if config['desktop']['manual_fallback'] == 'true':
                demo_bank = []
            elif task['courseId'] in ('demo-101', 'demo-103'):
                def bank_miss(_):
                    bank.last_query_status = 'error'
                    bank.last_query_reason = '请在言溪个人中心启用 AI 搜题。'

                def model_answer(q):
                    if task['courseId'] == 'demo-103':
                        raise AIError('image_decode', 'AI 图片无法解码或转换。')
                    return ChoiceMatch([option_pairs(q)[1]], 'letter')

                config = dict(config, ai=dict(config['ai'], vision='true', check_connection='false'),
                              desktop=dict(config['desktop'], random_fallback='true'))
                bank = SimpleNamespace(_query=bank_miss)
                demo_bank = [('言溪题库（模拟）', bank)]
                ai = SimpleNamespace(query=model_answer)
                question.update(title='求图中函数 <img src="https://formula.invalid/title.png"> 的导数。',
                                options=[f'{key}. <img src="https://formula.invalid/{key}.png">' for key in 'ABCD'])
            router = AnswerRouter(config, channel, demo_bank, ai)
            router.set_context(task_id=task['id'], work_id='demo-work', attempt=0)
            router.query_all([question])
            channel.send('discussion_result', record=dict(
                id=task['id']+':discussion', run_id=channel.run_id, task_id=task['id'],
                course=task['course'], chapter='章节讨论 · 离线演示',
                title='大学生如何在学习与实践中承担社会责任？', level='medium', source='ai',
                stage='draft', task_status='pending', elapsed_ms=0,
                reason='离线示例草稿，未调用 AI、未发布。开启真实讨论功能后按独立配置处理。',
                content='大学生可以通过扎实学习专业知识、参与社会实践与志愿服务，在具体行动中提升能力、承担责任。'))
            from .activity import ActivityGenerator, preview_context
            context = preview_context('你对本课程有什么建议？')
            local_cfg = dict(choice_strategy='random', text_strategy='fixed_good', reply_level='medium')
            answers = ActivityGenerator(AnswerRouter(command['config'], channel)).generate(context, local_cfg)
            channel.send('activity_result', record=dict(
                id=task['id']+':activity', run_id=channel.run_id, task_id=task['id'], course=task['course'],
                chapter='混合问卷 · 离线演示', title=context.title, kind='survey', stage='draft',
                submit_status='draft', task_status='pending', elapsed_ms=0,
                reason='离线示例：随机选择 + 固定好，AI 调用 0 次，未访问课程或提交。',
                questions=context.questions, answers=answers))
        channel.send("task", id=task["id"], status="success")
    channel.send("done", success=len(tasks), failed=0, skipped=0, pending=0, demo=True)


def run_tasks(channel, tasks, process, config, result_enum, verify=None):
    """有解锁顺序的课程逐章推进，其余章节可并发处理。"""
    pending = list(tasks)
    statuses = {}
    for attempt in range(5):
        checkpoint(channel)
        retry = []
        groups = {}
        for task in pending:
            groups.setdefault(task.get('serial_course', task['id']), []).append(task)
        ready = deque((group[0], iter(group[1:])) for group in groups.values())
        with ThreadPoolExecutor(max_workers=config["jobs"]) as executor:
            def execute(task):
                checkpoint(channel)
                channel.send("task", id=task["id"], status="running")
                return process(task)
            futures = {}
            def fill_slots():
                while ready and len(futures) < config["jobs"]:
                    checkpoint(channel)
                    task, following = ready.popleft()
                    futures[executor.submit(execute, task)] = (task, following)
            fill_slots()
            while futures:
                checkpoint(channel)
                completed, _ = wait(futures, timeout=.1, return_when=FIRST_COMPLETED)
                # Pop completed futures promptly; do not retain every result or
                # exception traceback until thousands of chapters have finished.
                for future in completed:
                    task, following = futures.pop(future)
                    handle_result(future, task, attempt, retry, statuses, channel, config, result_enum)
                    if verify is not None and statuses[task['id']] in ('processed', 'pending'):
                        checkpoint(channel)
                        statuses[task['id']] = verify(task)
                        channel.send('task', id=task['id'], status=statuses[task['id']])
                    next_task = next(following, None)
                    if next_task is not None:
                        ready.appendleft((next_task, following))
                fill_slots()
        if not retry:
            break
        channel.send("log", message=f"等待重试 {len(retry)} 个章节（第 {attempt + 2}/5 轮）。", level="WARNING")
        controlled_wait(channel, config["retry_interval"])
        pending = retry
    return statuses


def handle_result(future, task, attempt, retry, statuses, channel, config, result_enum):
    try:
        result = future.result()
    except Exception as exc:
        channel.send("log", message=f"{task['title']}：{type(exc).__name__}: {exc}", level="ERROR")
        result = result_enum.ERROR
    if result == result_enum.SUCCESS:
        status = "processed"
    elif getattr(result_enum, 'PENDING', None) is not None and result == result_enum.PENDING:
        status = "pending"
    elif result == result_enum.NOT_OPEN:
        action = config["notopen_action"]
        if action == "ask":
            answer = channel.ask(f"章节「{task['title']}」未开放。", kind="choice",
                                 choices=[{"label": "跳过当前章节", "value": "y"},
                                          {"label": "停止任务", "value": "n"}])
            if answer.strip().lower() not in ("y", "yes"):
                channel.send("cancelled", message="用户停止了未开放章节的处理。")
                os._exit(2)
        if action in ("ask", "continue"):
            status = "skipped"
        elif attempt < 4:
            status = "retry"
            retry.append(task)
        else:
            status = "skipped"
    elif attempt < 4:
        status = "retry"
        retry.append(task)
    else:
        status = "failed"
    statuses[task["id"]] = status
    channel.send("task", id=task["id"], status=status)


def _download_exam_images(session, channel, image_urls: dict) -> dict[str, list[str]]:
    """把试卷题图缓存到运行期 image_cache_dir，返回 qid -> [file:// URI]。"""
    if channel.image_cache_dir is None or not image_urls:
        return {}
    cached: dict[str, list[str]] = {}
    for qid, urls in image_urls.items():
        uris: list[str] = []
        for url in urls:
            try:
                resp = session.get(url, timeout=15)
                if resp.status_code == 200 and len(resp.content) <= 8 * 1024 * 1024:
                    ext = ".png"
                    ctype = str(resp.headers.get("Content-Type") or "").lower()
                    if "jpeg" in ctype or "jpg" in ctype:
                        ext = ".jpg"
                    elif "webp" in ctype:
                        ext = ".webp"
                    img_path = channel.image_cache_dir / f"{uuid.uuid4().hex}{ext}"
                    img_path.write_bytes(resp.content)
                    uris.append(img_path.as_uri())
                resp.close()
            except Exception:
                continue
        if uris:
            cached[str(qid)] = uris
    return cached


class _ExamAssistChannel:
    """拦截 AnswerRouter 发出的 question_result，记录每题实际命中的来源与原因。"""

    def __init__(self, inner):
        self._inner = inner
        self.results: dict[str, dict] = {}

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def send(self, event_type, **kwargs):
        if event_type == "question_result":
            rec = kwargs.get("record") or {}
            qid = str(rec.get("question_id") or "")
            if qid:
                self.results[qid] = {
                    "source": str(rec.get("source") or ""),
                    "reason": str(rec.get("reason") or ""),
                    "attempts": list(rec.get("attempts") or []),
                }
            return
        self._inner.send(event_type, **kwargs)


def _map_choice_ids(q: dict, rq: dict, raw_ans) -> list[str]:
    letters = rq.get("desktop_answer_letters") or []
    if isinstance(letters, str):
        letters = list(letters)
    if letters:
        return [str(x) for x in letters if str(x)]
    options = q.get("options") or []
    by_text = {str(o.get("text") or "").strip(): str(o.get("id") or "") for o in options}
    by_id = {str(o.get("id") or "").strip(): str(o.get("id") or "") for o in options}
    items = raw_ans if isinstance(raw_ans, list) else [s.strip() for s in str(raw_ans).split("#") if s.strip()]
    mapped = []
    for item in items:
        if item in by_id:
            mapped.append(by_id[item])
        elif item in by_text:
            mapped.append(by_text[item])
    if not mapped and isinstance(raw_ans, str) and len(raw_ans.strip()) <= len(options):
        for ch in raw_ans.strip():
            if ch in by_id:
                mapped.append(by_id[ch])
    return list(dict.fromkeys(mapped))


def _exam_image_src(uri: str, cache_dir: Path | None) -> str:
    """将本地缓存的 file:// 题图转为 data:image/...;base64 URI，供 AnswerRouter 识图。"""
    if uri.startswith(("https://", "http://", "data:image/")):
        return uri
    if uri.startswith("file://") and cache_dir is not None:
        from urllib.parse import unquote, urlsplit
        import base64
        parsed = urlsplit(uri)
        raw_path = unquote(parsed.path or "")
        if len(raw_path) >= 3 and raw_path[0] == "/" and raw_path[2] == ":":
            raw_path = raw_path[1:]
        p = Path(raw_path).resolve()
        root = cache_dir.resolve()
        if p.is_file() and (p == root or root in p.parents):
            ext = p.suffix.lower()
            mime = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/webp" if ext == ".webp" else "image/png"
            return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode("ascii")
    return ""


def real_exam_assist(channel, command, *, suggestion_event="exam_suggestion", done_event="exam_assist_done"):
    """使用 Exam 生效配置（题库 + AI）为指定考试或作业题目生成参考答案。"""
    from .answers import build_answers

    raw_questions = command.get("questions") or []
    if not isinstance(raw_questions, list):
        raise ValueError("待查询题目列表无效。")
    questions = [q for q in raw_questions if isinstance(q, dict) and q.get("supported", True) and q.get("type") != "unsupported"]
    assist_channel = _ExamAssistChannel(channel)
    router = build_answers(command["config"], assist_channel)
    router.set_context(task_id="exam", work_id=str(command.get("exam_id") or "exam"))

    router_questions = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        qtype = str(q.get("type") or "")
        title = str(q.get("title") or "")
        for uri in q.get("images") or []:
            src = _exam_image_src(str(uri or ""), paper_image_cache(command["data_dir"]))
            if src and src not in title:
                title += f'<img src="{src}">'
        opts = []
        if qtype in ("single", "multiple"):
            for opt in q.get("options") or []:
                oid = str(opt.get("id") or "")
                otext = str(opt.get("text") or "")
                opts.append(f"{oid}.{otext}" if oid else otext)
        rq = {
            "id": str(q.get("id") or ""),
            "type": qtype,
            "title": title,
            "options": opts,
        }
        if qtype == "completion":
            rq["completion_parts"] = int(q.get("blank_count") or 1)
        router_questions.append(rq)

    results = router.query_all(router_questions, query_delay=0)
    resolved = 0
    for q, rq, raw_ans in zip(questions, router_questions, results):
        checkpoint(channel)
        qid = str(q.get("id") or "")
        qtype = str(q.get("type") or "")
        meta = assist_channel.results.get(qid) or {}
        src = meta.get("source") or str(rq.get("desktop_answer_source") or "")
        reason = meta.get("reason") or str(rq.get("desktop_rejection_reason") or "")
        attempts = meta.get("attempts") or []
        answer_obj = {"option_ids": [], "blanks": [], "text": ""}
        if raw_ans is not None and src:
            if qtype in ("single", "multiple"):
                answer_obj["option_ids"] = _map_choice_ids(q, rq, raw_ans)
            elif qtype == "judgement":
                val = str(raw_ans).strip()
                if val in ("正确", "对", "True", "true", "1", "√"):
                    answer_obj["option_ids"] = ["正确"]
                elif val in ("错误", "错", "False", "false", "0", "×"):
                    answer_obj["option_ids"] = ["错误"]
            elif qtype == "completion":
                parts = rq.get("desktop_completion_parts")
                if isinstance(parts, list):
                    answer_obj["blanks"] = [str(x) for x in parts]
                else:
                    blank_count = int(q.get("blank_count") or 1)
                    answer_obj["blanks"] = [str(raw_ans)] + [""] * max(0, blank_count - 1)
            elif qtype == "shortanswer":
                answer_obj["text"] = str(raw_ans)
            if any((answer_obj["option_ids"], answer_obj["text"].strip(), any(b.strip() for b in answer_obj["blanks"]))):
                resolved += 1
                if not reason:
                    reason = "已生成参考答案，请核对。"
            else:
                src = ""
                if not reason:
                    reason = "未能匹配到可用参考答案。"
        else:
            src = ""
            if not reason:
                reason = "题库与 AI 均未命中可用参考答案。"

        channel.send(
            suggestion_event,
            question_id=qid,
            answer=answer_obj,
            source=src,
            reason=reason,
            attempts=attempts,
        )

    channel.send(
        done_event,
        completed=len(questions),
        total=len(questions),
        resolved=resolved,
        message=f"参考答案查询完成：共处理 {len(questions)} 题，生成 {resolved} 题参考答案。",
    )


def real_exam_action(channel, command, cx, session):
    """在真实会话下执行考试列表、开卷、保存、交卷与只读核验。"""
    from api.exam import ExamAdapter

    action = command["action"]
    adapter = ExamAdapter()
    if action == "exam_list":
        courses = list({(str(c["courseId"]), str(c["clazzId"])): c for c in cx.get_course_list()}.values())
        exams = adapter.list_exams(session, courses)
        channel.send("exam_list", exams=exams, demo=False)
        return
    if action == "exam_open":
        entry = command.get("exam_entry") or {"examId": command.get("exam_id", "")}
        res = adapter.open_exam(session, entry)
        if not res.get("native_supported"):
            channel.send(
                "exam_unavailable",
                exam_id=entry["examId"], course_id=entry["courseId"], clazz_id=entry["clazzId"],
                reason=res.get("reason") or "该场考试不支持在桌面工作台内作答。",
            )
            return
        paper = dict(res["paper"])
        cached_imgs = _download_exam_images(session, channel, res.get("image_urls") or {})
        for q in paper.get("questions") or []:
            qid = str(q.get("id") or "")
            if qid in cached_imgs:
                q["images"] = cached_imgs[qid]
        channel.send(
            "exam_opened",
            paper=paper,
            server_now_utc=res.get("server_now_utc", ""),
            platform_answers=res.get("platform_answers") or {},
            form_meta=res.get("form_meta") or {},
        )
        return
    if action == "exam_save":
        paper = command.get("paper") or {}
        form_meta = command.get("form_meta") or {}
        answers = command.get("answers") or {}
        if not form_meta:
            raise ValueError("缺少试卷提交上下文，请重新打开试卷后再保存到平台。")
        res = adapter.save_once(session.cookies, paper, form_meta, answers)
        channel.send(
            "exam_saved",
            exam_id=str(paper.get("exam_id") or command.get("exam_id") or ""),
            attempt_id=str(paper.get("attempt_id") or command.get("attempt_id") or ""),
            verified=bool(res.get("verified")),
            reason=str(res.get("reason") or ""),
        )
        return
    if action == "exam_submit":
        paper = command.get("paper") or {}
        form_meta = command.get("form_meta") or {}
        answers = command.get("answers") or {}
        if not form_meta:
            channel.send(
                "exam_submitted",
                exam_id=str(paper.get("exam_id") or command.get("exam_id") or ""),
                attempt_id=str(paper.get("attempt_id") or command.get("attempt_id") or ""),
                state="submit_unknown",
                reason="缺少试卷提交上下文，请点击核验或在官方入口确认交卷状态。",
            )
            return
        res = adapter.submit_once(session.cookies, paper, form_meta, answers)
        channel.send(
            "exam_submitted",
            exam_id=str(paper.get("exam_id") or command.get("exam_id") or ""),
            attempt_id=str(paper.get("attempt_id") or command.get("attempt_id") or ""),
            state=str(res.get("state") or "submit_unknown"),
            reason=str(res.get("reason") or ""),
        )
        return
    if action == "exam_verify":
        entry = command.get("exam_entry") or {
            "courseId": command.get("course_id", ""),
            "clazzId": command.get("clazz_id", ""),
            "examId": command.get("exam_id", ""),
            "attemptId": command.get("attempt_id", ""),
            "cpi": command.get("cpi", "0"),
        }
        res = adapter.verify_submitted(session, entry)
        channel.send(
            "exam_submitted",
            exam_id=str(entry.get("examId") or command.get("exam_id") or ""),
            attempt_id=str(entry.get("attemptId") or command.get("attempt_id") or ""),
            state="submitted" if res.get("confirmed") else "submit_unknown",
            reason=str(res.get("reason") or ""),
        )
        return


    if action == "homework_list":
        from api.homework import HomeworkAdapter
        courses = list({(str(c["courseId"]), str(c["clazzId"])): c for c in cx.get_course_list()}.values())
        works = HomeworkAdapter().list_homeworks(session, courses)
        channel.send("homework_list", homeworks=works, demo=False)
        return

    if action == "homework_load":
        from api.homework import HomeworkAdapter
        url = str(command.get("url") or "").strip()
        parsed = HomeworkAdapter().load_homework(session, url)
        cached_imgs = _download_exam_images(session, channel, parsed.get("image_urls") or {})
        for q in parsed.get("questions") or []:
            qid = str(q.get("id") or "")
            if qid in cached_imgs:
                q["images"] = cached_imgs[qid]
        channel.send(
            "homework_loaded",
            demo=False,
            platform_answers=parsed.get("platform_answers") or {},
            form_meta=parsed.get("form_meta") or {},
            homework={
                "title": parsed["title"],
                "url": parsed["url"],
                "course_id": parsed["course_id"],
                "clazz_id": parsed["clazz_id"],
                "work_id": parsed["work_id"],
                "questions": parsed["questions"],
            },
        )
        return
    if action == "homework_save":
        from api.homework import HomeworkAdapter
        hw = command.get("homework") or {}
        form_meta = command.get("form_meta") or {}
        answers = command.get("answers") or {}
        if not form_meta:
            raise ValueError("缺少作业平台保存上下文，请重新通过链接读取作业后再保存到平台。")
        res = HomeworkAdapter().save_once(session.cookies, hw, form_meta, answers)
        channel.send(
            "homework_saved",
            work_id=str(hw.get("work_id") or ""),
            verified=bool(res.get("verified")),
            reason=str(res.get("reason") or ""),
        )
        return


def real(channel, command):
    checkpoint(channel)
    if command.get("action") == "exam_assist":
        real_exam_assist(channel, command)
        return
    if command.get("action") == "homework_assist":
        real_exam_assist(channel, command, suggestion_event="homework_suggestion", done_event="homework_assist_done")
        return
    core = load_core(channel)
    checkpoint(channel)
    account_id = command.get('account_id')
    resume = command.get('resume')
    if resume is not None and (not isinstance(resume, dict) or not account_id
                               or resume.get('account') != account_id):
        raise ValueError('续跑账号与当前会话不一致，请重新读取原账号课程。')
    verifying = command['action'] in ('verify', 'course_progress')
    if verifying and not account_id:
        raise ValueError('只读核验需要明确的账号会话。')
    if account_id:
        from .accounts import AccountStore
        from api.cookies import configure_account
        store = AccountStore(Path(command['data_dir']))
        row = store.get(account_id)
        if not row or row['username'] != command['config']['common']['username']:
            raise ValueError('账号信息已改变，请重新选择账号。')
        if resume is not None and store.active != account_id:
            raise ValueError('续跑账号已切换，请重新读取原账号课程。')
        if resume is not None and (not row['uid'] or resume.get('uid') != row['uid']
                                   or command.get('account_uid') != row['uid']):
            raise ValueError('续跑账号的平台身份已变化，请重新读取原账号课程。')
        if verifying and (not row['uid'] or command.get('account_uid') != row['uid']):
            raise ValueError('核验身份不完整或已变化，请重新读取课程。')
        if verifying and command.get('cookie_mtime_ns') != store.cookie_path(account_id).stat().st_mtime_ns:
            raise ValueError('核验会话文件已变化，请重新读取课程。')
        configure_account(store.cookie_path(account_id), row['uid'], command['config']['common']['use_cookies'])
    common, notification_config = (command["config"][s] for s in ("common", "notification"))
    # Listing courses must not initialize paid question-bank services.
    if verifying and (not common['use_cookies'] or common['password']):
        raise ValueError('只读核验仅使用已读取课程的 Cookie 会话，请先重新读课。')
    if verifying:
        channel.send('verification_progress', phase='session', message='正在校验账号会话…', completed=0, total=0, reads=0, failures=0, elapsed_ms=0)
    exam_actions = ('exam_list', 'exam_open', 'exam_save', 'exam_submit', 'exam_verify', 'homework_list', 'homework_load', 'homework_save')
    cx = core.create_client(command["config"], channel,
                            courses_only=command["action"] in ('courses', 'verify', 'course_progress') + exam_actions)
    checkpoint(channel)
    login = cx.login(login_with_cookies=common["use_cookies"])
    if not login.get("status"):
        raise RuntimeError(login.get("msg", "登录失败"))
    if account_id:
        from .accounts import cookie_uid
        from api.base import SessionManager
        from api.cookies import save_cookies
        session = SessionManager.get_session()
        uid = cookie_uid(session.cookies.get_dict())
        if not uid or (row['uid'] and row['uid'] != uid):
            raise ValueError('平台会话与当前账号不一致，请重新登录或添加为其他账号。')
        if not verifying:
            save_cookies(session)
            channel.send('session_ready', account_id=account_id, uid=uid,
                         cookie_mtime_ns=store.cookie_path(account_id).stat().st_mtime_ns)
        if command['action'] == 'run' and hasattr(cx, 'desktop_quiz_guard'):
            from .quiz_attempts import QuizAttemptStore, QuizGuard
            lineage = command.get('lineage') or uuid.uuid4().hex
            cx.desktop_quiz_guard = QuizGuard(QuizAttemptStore(command['data_dir'], account_id, uid), lineage, channel)
    checkpoint(channel)
    if command["action"] in exam_actions:
        from api.base import SessionManager
        real_exam_action(channel, command, cx, SessionManager.get_session())
        return
    if verifying:
        channel.send('verification_progress', phase='courses', message='会话已校验，正在读取课程身份…', completed=0, total=0, reads=0, failures=0, elapsed_ms=0)
    courses = list({(str(c["courseId"]), str(c["clazzId"])): c for c in cx.get_course_list()}.values())
    if command["action"] == "courses":
        channel.send("courses", courses=courses, demo=False)
        return
    if command['action'] == 'course_progress':
        from .course_progress import refresh_progress
        if command.get('selected'):
            refresh_progress(cx, channel, select_courses(courses, command['selected']))
        return
    if verifying:
        from .verification import verify_chapters
        from .task_completion import TaskCompletionCache
        verify_chapters(cx, channel, courses, command.get('targets', []),
                        completion_cache=TaskCompletionCache(command['data_dir'], account_id, uid))
        return
    chosen = select_courses(courses, command.get("selected", []))
    # A continuation may only keep the exact scope that was stopped. Every part
    # of it comes from the command, never from a fresh listing, so new items,
    # other courses or a re-read list cannot silently widen the resumed run.
    resume_scope = None
    resume_courses = []
    if resume is not None:
        if (not isinstance(resume, dict)
                or set(resume) != {'tasks', 'courses', 'account', 'uid'}
                or not isinstance(resume['tasks'], list) or len(resume['tasks']) > 10000
                or not isinstance(resume['courses'], list) or not resume['courses']
                or len(resume['courses']) > 64):
            raise ValueError('续跑范围无效，请重新读取课程后手动开始。')
        for course in resume['courses']:
            if (not isinstance(course, dict) or set(course) != {'courseId', 'clazzId', 'cpi', 'title'}
                    or any(not isinstance(course.get(k), str) or len(course[k]) > 256
                           for k in ('courseId', 'clazzId', 'cpi', 'title'))
                    or not all(course[k] for k in ('courseId', 'clazzId', 'cpi'))):
                raise ValueError('续跑课程范围无效，请重新读取课程后手动开始。')
            resume_courses.append(course)
        if len({(c['courseId'], c['clazzId']) for c in resume_courses}) != len(resume_courses):
            raise ValueError('续跑课程范围重复，请重新读取课程后手动开始。')
        resume_scope = {}
        for row in resume['tasks']:
            if (not isinstance(row, dict)
                    or set(row) != {'taskId', 'courseId', 'clazzId', 'pointId', 'jobs', 'scanned', 'completed'}
                    or any(not isinstance(row.get(k), str) or not 0 < len(row[k]) <= 256
                           for k in ('taskId', 'courseId', 'clazzId', 'pointId'))
                    or type(row['scanned']) is not bool
                    or type(row['completed']) is not bool
                    or not isinstance(row['jobs'], list)):
                raise ValueError('续跑任务点范围无效，请重新读取课程后手动开始。')
            key = (row['courseId'], row['clazzId'])
            # The task id encodes course, class, chapter and its list position;
            # a mismatched pair would otherwise address a different chapter.
            head = f"{row['courseId']}:{row['clazzId']}:{row['pointId']}:"
            if not row['taskId'].startswith(head):
                raise ValueError('续跑任务点标识与课程或章节不符，请重新读取课程后手动开始。')
            jobs = []
            for job in row['jobs']:
                if (not isinstance(job, list) or len(job) != 3
                        or any(not isinstance(v, str) or len(v) > 256 for v in job)):
                    raise ValueError('续跑任务点范围无效，请重新读取课程后手动开始。')
                jobs.append(tuple(job))
            scope = resume_scope.setdefault(key, {})
            if row['pointId'] in scope:
                raise ValueError('续跑范围包含重复章节，请重新读取课程后手动开始。')
            scope[row['pointId']] = dict(jobs=jobs, scanned=row['scanned'], completed=row['completed'])
        # Every course in the frozen scope must still be a live course with the
        # same class context; a vanished or re-classed course is not resumable.
        live = {course_key(course): course for course in chosen}
        wanted = {(c['courseId'], c['clazzId']) for c in resume_courses}
        if wanted - set(live):
            raise ValueError('续跑的课程不在当前已读取课程中，请重新读取原账号课程。')
        for course in resume_courses:
            current = live[(course['courseId'], course['clazzId'])]
            if str(current.get('cpi', '')) != course['cpi']:
                raise ValueError('续跑课程的班级上下文（cpi）已变化，请重新读取课程后手动开始。')
        if set(resume_scope) - wanted:
            raise ValueError('续跑任务点不在所保留的课程范围内，请重新读取课程后手动开始。')
        chosen = [course for course in chosen
                  if (str(course['courseId']), str(course['clazzId'])) in wanted]
    selected_courses = list(chosen)
    from .course_progress import progress_identity, send_progress
    from .task_completion import TaskCompletionCache
    completion_cache = TaskCompletionCache(command["data_dir"], account_id, uid) if account_id else None
    ignore_completion_cache = common.get('ignore_completion_cache', False)
    if resume_scope is not None and ignore_completion_cache:
        # A continuation always honours the completion cache: re-checking and
        # reprocessing every confirmed chapter would be a new, wider run.
        ignore_completion_cache = False
        channel.send('log', level='WARNING',
                     message='续跑固定使用任务完成缓存；本次未按“忽略完成缓存”重新检查。')
    cached_ids = set()
    if channel.control:
        def check_resumed_session():
            for course in chosen:
                result = cx.get_course_point(course['courseId'], course['clazzId'], course['cpi'])
                if not isinstance(result, dict) or not isinstance(result.get('points'), list):
                    raise ValueError('无法复核课程会话')
        channel.control.resume_check = check_resumed_session
    tasks = []
    tasks_by_course = {}
    for course in chosen:
        checkpoint(channel)
        course_tasks = []
        tasks_by_course[(str(course["courseId"]), str(course["clazzId"]))] = course_tasks
        channel.send('course_progress_loading', **progress_identity(course))
        catalog = cx.get_course_point(course["courseId"], course["clazzId"], course["cpi"])
        points = catalog['points']
        completed = completion_cache.read(course) if completion_cache else {}
        try:
            send_progress(channel, course, points)
        except ValueError:
            channel.send('course_progress_failed', **progress_identity(course))
        wanted = resume_scope.get(course_key(course), {}) if resume_scope else None
        if wanted is not None:
            if set(wanted) - {str(point['id']) for point in points}:
                raise ValueError('续跑的原章节已不在该课程中（可能班级或章节已变化），未启动任何任务；'
                                 '请重新读取课程后手动开始。')
        for i, point in enumerate(points):
            if wanted is not None and str(point['id']) not in wanted:
                # Newly discovered chapters and other courses stay out of a
                # resumed run, even when the platform list grew since the stop.
                continue
            task = dict(id=f"{course['courseId']}:{course['clazzId']}:{point['id']}:{i}",
                        title=point["title"], course=course["title"], course_data=course, point=point)
            if catalog.get('hasLocked'):
                task['serial_course'] = course_key(course)
            if wanted is not None:
                scope = wanted[str(point['id'])]
                # Only chapters actually scanned before the stop constrain the
                # job set; unscanned ones are discovered as in a normal run.
                task['resume_jobs'] = scope['jobs'] if scope['scanned'] else None
            tasks.append(task)
            course_tasks.append(task)
            # A continuation always honours the cache (forced above); a normal
            # run keeps the existing "ignore completed" switch.
            if ((wanted is not None and scope['completed'])
                    or (not ignore_completion_cache and str(point['id']) in completed)
                    or (resume is not None and point.get('has_finished') is True)):
                cached_ids.add(task['id'])
    chosen = [course for course in chosen
              if any(t['id'] not in cached_ids
                     for t in tasks_by_course[(str(course['courseId']), str(course['clazzId']))])]
    channel.send("tasks", tasks=[dict({k: t[k] for k in ("id", "title", "course")},
                                    courseId=str(t['course_data']['courseId']),
                                    clazzId=str(t['course_data']['clazzId']), pointId=str(t['point']['id'])) for t in tasks])
    for task in tasks:
        if task['id'] in cached_ids:
            channel.send('task', id=task['id'], status='success')

    def remember_result(task, confirmed):
        if completion_cache:
            try:
                if confirmed:
                    completion_cache.save(task['course_data'], task['point'])
                elif ignore_completion_cache:
                    completion_cache.remove(task['course_data'], task['point']['id'])
            except (OSError, ValueError) as exc:
                channel.send('log', level='WARNING', message=f"无法更新任务完成缓存：{exc}")

    def verify_task(task):
        # Accept each chapter immediately after processing; saving answers alone
        # is not proof of completion, even when the runner reports success.
        course = task['course_data']
        channel.send('course_progress_loading', **progress_identity(course))
        try:
            points = cx.get_course_point(course["courseId"], course["clazzId"], course["cpi"])["points"]
            try:
                send_progress(channel, course, points)
            except ValueError:
                channel.send('course_progress_failed', **progress_identity(course))
            confirmed = any(str(p['id']) == str(task['point']['id']) and p.get('has_finished') is True
                            for p in points)
            if not confirmed and ignore_completion_cache:
                remember_result(task, False)
        except Exception as exc:
            channel.send('course_progress_failed', **progress_identity(course))
            channel.send("log", message=f"无法核验章节进度：{exc}", level="WARNING")
            confirmed = False
        checkpoint(channel)
        if confirmed:
            remember_result(task, True)
        return 'success' if confirmed else 'pending'

    def run_chapter(task):
        # Only pass the job filter when a continuation actually has one, so the
        # existing adapter call signature is untouched for normal runs.
        if task.get('resume_jobs') is None:
            return core.process_chapter(cx, task["course_data"], task["point"], common["speed"], task['id'])
        return core.process_chapter(cx, task["course_data"], task["point"], common["speed"], task['id'],
                                    allowed_jobs=task['resume_jobs'])

    statuses = run_tasks(channel, [t for t in tasks if t['id'] not in cached_ids],
                         run_chapter, common, core.ChapterResult, verify=verify_task)
    statuses.update({task_id: 'success' for task_id in cached_ids})
    # Retain the final read for media whose platform confirmation arrives late.
    for course in chosen:
        checkpoint(channel)
        channel.send('course_progress_loading', **progress_identity(course))
        try:
            points = cx.get_course_point(course["courseId"], course["clazzId"], course["cpi"])["points"]
            try:
                send_progress(channel, course, points)
            except ValueError:
                channel.send('course_progress_failed', **progress_identity(course))
            finished = {str(p["id"]): p.get("has_finished") is True for p in points}
            if ignore_completion_cache:
                for task in tasks_by_course[(str(course['courseId']), str(course['clazzId']))]:
                    if not finished.get(str(task['point']['id']), False):
                        remember_result(task, False)
        except Exception as exc:
            channel.send('course_progress_failed', **progress_identity(course))
            channel.send("log", message=f"无法核验章节进度：{exc}", level="WARNING")
            finished = {}
        for task in tasks_by_course[(str(course["courseId"]), str(course["clazzId"]))]:
            if statuses[task["id"]] in ("processed", "pending"):
                status = "success" if finished.get(str(task["point"]["id"]), False) else "pending"
                if status == 'success':
                    remember_result(task, True)
                statuses[task["id"]] = status
                channel.send("task", id=task["id"], status=status)
    if common["add_learning_count"] and resume_scope is None:
        # Appending learning counts walks every chapter of the course, which
        # would touch confirmed and new chapters outside the stopped scope.
        channel.send("log", message="开始执行章节学习次数追加。", level="INFO")
        for course in selected_courses:
            core.increase_learning_count_for_course(cx, course, common)
    elif common["add_learning_count"]:
        channel.send('log', level='WARNING', message='续跑不执行整课学习次数追加；如需追加请手动新开一轮。')
    counts = {s: list(statuses.values()).count(s) for s in ("success", "failed", "skipped", "pending")}
    checkpoint(channel)
    if notification_config.get("provider"):
        notification = core.Notification()
        notification.config_set(notification_config)
        notification = notification.get_notification_from_config()
        notification.init_notification()
        try:
            notification.send(f"超星桌面版任务结束：{counts}")
        except Exception as exc:
            channel.send("log", message=f"外部通知发送失败：{exc}", level="WARNING")
    channel.send("done", **counts, demo=False)


def main() -> int:
    output, input_stream = sys.stdout, sys.stdin
    if output is None or input_stream is None:
        return 1
    for stream in (output, input_stream):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    command = json.loads(input_stream.readline())
    channel = Channel(output, input_stream, Redactor(command.get("config", {})))
    channel.run_id = str(command.get("run_id") or channel.run_id)
    channel.enable_control()
    try:
        path = Path(command["data_dir"]).resolve()
        path.mkdir(parents=True, exist_ok=True)
        os.chdir(path)
        action = command.get('action')
        if (action == 'run' or action in PAPER_IMAGE_ACTIONS) and re.fullmatch(r'[0-9a-f]{32}', channel.run_id):
            root = paper_image_cache if action in PAPER_IMAGE_ACTIONS else profile_image_cache
            directory = root(path) / channel.run_id
            if directory.is_dir() and directory.resolve() == directory:
                channel.image_cache_dir = directory
        if command.get("action") == "probe":
            load_core(channel)
            from api.cxsecret_font import fonthash_dao
            # Exercise the packaged v2 helper and Qt image codec without any
            # credential or network request (the worker has no QApplication).
            import base64
            from PySide6.QtCore import QBuffer, QIODevice
            from PySide6.QtGui import QImage
            from .bank_question import ImageEncoder, yanxi_environment
            picture = QImage(1, 1, QImage.Format.Format_RGB32)
            picture.fill(0xFFFFFF)
            stream = QBuffer()
            stream.open(QIODevice.OpenModeFlag.WriteOnly)
            if not picture.save(stream, 'PNG'):
                raise RuntimeError('PNG 编码器不可用')
            source = 'data:image/png;base64,' + base64.b64encode(bytes(stream.data())).decode('ascii')
            encoder = ImageEncoder()
            env = yanxi_environment(dict(title='1+1=?', options='a.1 b.2'), encoder)
            if env['options'] != '1\n2' or env['suggestion_title'] or not encoder.encode(source).startswith('data:image/png;base64,'):
                raise RuntimeError('题库 v2 参数或图片编码检查失败')
            from .matching import match_choices
            match = match_choices('需求牵引#\n改革攻坚#', [('A', '需求牵引'), ('B', '改革攻坚')], 'multiple')
            if match is None or {key for key, _ in match.pairs} != {'A', 'B'}:
                raise RuntimeError('相似匹配模块检查失败')
            quantity = match_choices('1万亿元', [('A', '10000亿元'), ('B', '8000亿元')], 'single')
            if quantity is None or quantity.pairs[0][0] != 'A':
                raise RuntimeError('数值单位匹配检查失败')
            from types import SimpleNamespace
            from .answers import AnswerRouter
            from .config import defaults, to_payload
            review_config = to_payload(defaults())
            review_config['matching']['bank_review_policy'] = 'ai_dependent'
            policy_router = AnswerRouter(review_config, SimpleNamespace(send=lambda *args, **kwargs: None))
            review_question = dict(title='2026年产业占比', type='single', options='A.70%\nB.80%')
            review_bank = SimpleNamespace(last_result_question='2025年产业占比')
            if policy_router.normalize_bank_answer('80%', review_question, review_bank) != '80%':
                raise RuntimeError('AI 关闭时的题库审核策略检查失败')
            review_config['ai']['enabled'] = 'true'
            if policy_router.normalize_bank_answer('80%', review_question, review_bank) is not None:
                raise RuntimeError('AI 开启时的题库审核策略检查失败')
            # Exercise the actual frozen OCR model and native ONNX runtime,
            # not just import availability or developer-environment packages.
            import io
            from PIL import Image, ImageDraw, ImageFont
            from api.captcha import ocr_init
            captcha = Image.new('RGB', (160, 54), 'white')
            ImageDraw.Draw(captcha).text((8, 6), '1234', font=ImageFont.load_default(size=32), fill='black')
            captcha_bytes = io.BytesIO()
            captcha.save(captcha_bytes, format='PNG')
            ocr = ocr_init()
            if ocr is None or ocr.classification(captcha_bytes.getvalue()).strip() != '1234':
                raise RuntimeError('验证码识别模型或运行库检查失败')
            from .diagnostics import offline_check
            offline_check()
            from api.discussion import DiscussionAdapter
            from .discussion import DiscussionGenerator
            from .discussion_store import topic_key
            if (DiscussionGenerator(policy_router).generate('离线讨论检查', '', 'fixed_good', ()) != '好'
                    or len(topic_key('probe', 'course', 'class', 'space', 'topic')) != 64
                    or not callable(DiscussionAdapter.publish_once)):
                raise RuntimeError('讨论生成、存储或平台适配模块检查失败')
            from api.activity import ActivityAdapter
            from .activity import ActivityGenerator, preview_context
            from .activity_store import activity_key
            local_answers = ActivityGenerator(policy_router).generate(preview_context('离线问卷检查'),
                dict(choice_strategy='random', text_strategy='fixed_good', reply_level='medium'))
            if (local_answers[-1]['text'] != '好' or not all(a['valid'] for a in local_answers)
                    or len(activity_key('probe', 'course', 'class', 'space', 'activity')) != 64
                    or not callable(ActivityAdapter.submit_once)):
                raise RuntimeError('活动生成、存储或平台适配模块检查失败')
            from .config import exam_answer_config
            from .exam_drafts import exam_draft_key, validate_draft, STATE_LOCAL_DRAFT
            exam_eff, exam_map = exam_answer_config(defaults())
            probe_draft = {
                "version": 1,
                "revision": 0,
                "account_id": "probe_account",
                "uid": "probe_uid",
                "course_id": "probe_course",
                "clazz_id": "probe_clazz",
                "exam_id": "probe_exam",
                "attempt_id": "probe_attempt",
                "demo": True,
                "title": "探针自检试卷",
                "deadline_utc": "2026-11-23T10:30:00+00:00",
                "state": STATE_LOCAL_DRAFT,
                "prev_state": "",
                "reason": "",
                "updated_at": "2026-11-23T09:00:00+00:00",
                "questions": [
                    {
                        "id": "1",
                        "no": 1,
                        "type": "single",
                        "title": "1+1=?",
                        "options": [{"id": "A", "text": "2"}],
                        "blank_count": 0,
                    }
                ],
                "answers": {},
            }
            validate_draft(probe_draft)
            if (
                len(exam_draft_key("probe", "uid", "course", "clazz", "exam", "attempt")) != 64
                or exam_eff["desktop"]["random_fallback"] != "false"
                or exam_eff["desktop"]["manual_fallback"] != "false"
                or exam_eff["tiku"]["submit"] != "false"
                or set(exam_map) != {"ai", "question_bank"}
            ):
                raise RuntimeError("考试草稿与答题服务配置模块检查失败")
            from api.exam import ExamAdapter
            if not all(
                callable(getattr(ExamAdapter, m, None))
                for m in ("list_exams", "open_exam", "save_once", "submit_once", "verify_submitted")
            ):
                raise RuntimeError("考试协议适配模块检查失败")
            from api.homework import HomeworkAdapter, validate_homework_url
            from .homework_drafts import HomeworkDraftStore, export_homework_text
            if (
                not callable(getattr(HomeworkAdapter, "load_homework", None))
                or not callable(validate_homework_url)
                or not callable(export_homework_text)
                or not callable(getattr(HomeworkDraftStore, "save_draft", None))
            ):
                raise RuntimeError("独立作业草稿与协议模块检查失败")
            channel.send("probe", ocs_ready=True, message="核心、字体资源、题库 v2 参数、图片编码及验证码识别正常",
                         font_entries=len(fonthash_dao.char_map), bank_protocol='v2-text-and-images',
                         matching_ready=True, captcha_ready=True, review_policy_ready=True, discussion_ready=True, activity_ready=True, exam_ready=True, homework_ready=True)
        elif command.get('action') == 'check_bank':
            load_core(channel)
            from .answers import build_answers
            from .query_result import query_bank, STATE_TEXT
            router = build_answers(command['config'], channel)
            if not router.banks:
                raise ValueError('题库初始化失败，请检查配置')
            name, bank = router.banks[0]
            bank.control = channel.control
            bank.budget_check = lambda: router.consume_budget(('connection-check', name))
            result = query_bank(name, bank, dict(title='1+1=？', type='single', options='A.1\nB.2'))
            channel.send('bank_check', ok=result.status in ('hit', 'miss'),
                         message='真实连接检查：' + STATE_TEXT[result.status] + '；不代表后续命中率或答案正确率。')
        elif command.get("action") == "check_ai":
            from .answers import AIClient
            ai = AIClient(command['config']['ai'])
            ai.attach_channel(channel)
            ai.check_connection()
            channel.send("ai_check")
        elif command.get('action') == 'discussion_preview':
            from .answers import AnswerRouter, AIClient
            from .discussion import DiscussionGenerator
            cfg = command['config']
            ai = AIClient(cfg['ai']) if cfg['ai']['enabled'] == 'true' else None
            router = AnswerRouter(cfg, channel, ai=ai)
            content = DiscussionGenerator(router).generate(str(command.get('topic', '')),
                '', cfg['discussion']['reply_level'], ('preview',))
            channel.send('discussion_preview', content=channel.redactor(content),
                         level=cfg['discussion']['reply_level'])
        elif command.get('action') == 'activity_preview':
            load_core(channel)
            from .answers import AnswerRouter, AIClient
            from .activity import ActivityGenerator, preview_context
            cfg = command['config']
            local = cfg['activity']['choice_strategy'] == 'random' and cfg['activity']['text_strategy'] == 'fixed_good'
            ai = AIClient(cfg['ai']) if not local and cfg['ai']['enabled'] == 'true' else None
            context = preview_context(str(command.get('topic', '')))
            answers = ActivityGenerator(AnswerRouter(cfg, channel, ai=ai)).generate(context, cfg['activity'])
            channel.send('activity_preview', record=dict(id='preview', run_id=channel.run_id, task_id='', course='', chapter='',
                         title=context.title, kind='survey', stage='draft', submit_status='draft', task_status='pending',
                         reason='示例预览，不读取课程、不提交、不写入生产去重记录。', elapsed_ms=0,
                         questions=context.questions, answers=answers))
        elif command.get("demo"):
            demo(channel, command)
        else:
            real(channel, command)
        return 0
    except ResumeFailed:
        channel.send("error", message="恢复前无法核验平台会话，任务已停止；请重新登录后再开始。")
        return 1
    except Cancelled:
        if channel.control and channel.control.resume_failed:
            channel.send("error", message="恢复前无法核验平台会话，任务已停止；请重新登录后再开始。")
            return 1
        channel.send("cancelled", message="任务已停止；已送达平台的请求不会撤销。")
        return 2
    except Exception as exc:
        channel.send("error", message=f"{type(exc).__name__}: {exc}")
        return 1
    finally:
        if command.get('action') not in PAPER_IMAGE_ACTIONS:
            remove_image_cache(channel.image_cache_dir)
