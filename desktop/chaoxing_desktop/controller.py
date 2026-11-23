import json
from pathlib import Path
import sys
import uuid
import time

from PySide6.QtCore import QObject, QProcess, Signal, QTimer

from .image_cache import PAPER_IMAGE_ACTIONS, paper_image_cache, profile_image_cache, remove_image_cache


class Controller(QObject):
    MAX_FRAME_BYTES = 16 * 1024 * 1024
    EVENTS_PER_TICK = 128
    EVENT_BUDGET_SECONDS = .006
    event = Signal(dict)
    finished = Signal(int)
    busy_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.readyReadStandardError.connect(self.read_errors)
        self.process.finished.connect(self.on_finished)
        self.process.errorOccurred.connect(self.on_error)
        self.process.started.connect(self.on_started)
        self.buffer = bytearray()
        self._drain_timer = QTimer(self)
        self._drain_timer.setSingleShot(True)
        self._drain_timer.timeout.connect(self.read_output)
        self._exit_code = None
        self._protocol_failed = False
        self.payload = {}
        self.active = False
        self.stopping = False
        self.paused = False
        self.run_id = ''
        self.image_cache_dir = None
        self.keep_image_cache = False
        self.request_id = ''
        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.timeout.connect(self.process.kill)

    def start(self, payload):
        if self.active:
            raise RuntimeError("已有后台任务正在运行。")
        self.run_id = uuid.uuid4().hex
        self.payload = dict(payload, run_id=self.run_id)
        action = payload.get('action')
        self.keep_image_cache = action in PAPER_IMAGE_ACTIONS
        if action == 'run' or self.keep_image_cache:
            root = paper_image_cache if self.keep_image_cache else profile_image_cache
            directory = root(payload['data_dir']) / self.run_id
            try:
                directory.mkdir(parents=True)
                self.image_cache_dir = directory
            except OSError:
                self.image_cache_dir = None
        self.paused = False
        self.request_id = ''
        self._kill_timer.stop()
        self.buffer.clear()
        self._drain_timer.stop()
        self._exit_code = None
        self._protocol_failed = False
        self.stopping = False
        self.active = True
        self.busy_changed.emit(True)
        if getattr(sys, "frozen", False):
            # Console worker companion preserves redirected stdio on Windows.
            executable = str(Path(sys.executable).with_name("ChaoxingWorker.exe"))
            args = ["--worker"]
        else:
            executable = sys.executable
            args = ["-u", str(Path(__file__).resolve().parents[1] / "run.py"), "--worker"]
        self.process.start(executable, args)

    def on_started(self):
        self.process.write((json.dumps(self.payload, ensure_ascii=False) + "\n").encode("utf-8"))
        self.payload = {}  # do not retain secrets after transmission

    def read_output(self):
        # Read and parse in bounded batches so the GUI can render and stop a
        # noisy worker. Splitting the whole remaining byte string per line used
        # to copy the same large batch repeatedly.
        if self._protocol_failed:
            self.process.readAllStandardOutput()
            if self._exit_code is not None:
                self._finish()
            return
        self.buffer.extend(bytes(self.process.read(64 * 1024)))
        consumed = 0
        deadline = time.monotonic() + self.EVENT_BUDGET_SECONDS
        for _ in range(self.EVENTS_PER_TICK):
            end = self.buffer.find(b"\n", consumed)
            if end < 0:
                break
            if end - consumed > self.MAX_FRAME_BYTES:
                self._fail_protocol()
                return
            line = self.buffer[consumed:end]
            consumed = end + 1
            if line.strip():
                try:
                    event = json.loads(line.decode("utf-8"))
                    if not self._valid_event(event):
                        raise ValueError("invalid worker event")
                except (ValueError, UnicodeDecodeError, RecursionError):
                    self._fail_protocol()
                    return
                if event.get('run_id', self.run_id) == self.run_id:
                    if event.get('type') == 'input':
                        self.request_id = event.get('request_id', '')
                    self.event.emit(event)
            if time.monotonic() >= deadline:
                break
        if consumed:
            del self.buffer[:consumed]
        if len(self.buffer) > self.MAX_FRAME_BYTES:
            self._fail_protocol()
            return
        if b"\n" in self.buffer or self.process.bytesAvailable():
            self._drain_timer.start(0)
        elif self._exit_code is not None:
            if self.buffer.strip():
                self._fail_protocol()
            else:
                self._finish()

    @staticmethod
    def _valid_event(event):
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            return False
        kind = event["type"]
        if kind == 'course_progress':
            from .course_progress import valid_progress
            return valid_progress(event)
        if kind in ('course_progress_loading', 'course_progress_failed'):
            return all(isinstance(event.get(k), str) and 0 < len(event[k]) <= 256
                       for k in ('courseId', 'clazzId', 'cpi'))
        if kind in ("courses", "tasks"):
            items = event.get(kind)
            return isinstance(items, list) and all(
                isinstance(item, dict) and isinstance(item.get("title"), str) and (
                    all(isinstance(item.get(key), (str, int)) for key in ("courseId", "clazzId")) if kind == "courses" else
                    all(isinstance(item.get(key), str) for key in ("id", "course")))
                for item in items)
        if kind == 'question_result':
            from .question_model import valid_record
            return valid_record(event.get('record'))
        if kind == 'discussion_result':
            from .discussion_model import valid_record
            return valid_record(event.get('record'))
        if kind in ('activity_result', 'activity_preview'):
            from .activity_model import valid_record
            return valid_record(event.get('record'))
        if kind == 'discussion_preview':
            return (isinstance(event.get('content'), str) and 0 < len(event['content']) <= 1000
                    and event.get('level') in ('concise', 'medium', 'detailed', 'fixed_good')
                    and (event['level'] != 'fixed_good' or event['content'] == '好'))
        if kind == 'control':
            return type(event.get('paused')) is bool
        if kind == 'verification':
            return (type(event.get('confirmed')) is bool and
                    all(isinstance(event.get(k), str) and len(event[k]) <= 2000 for k in ('id', 'checked_at', 'reason')))
        if kind == 'verification_failed':
            return all(isinstance(event.get(k), str) and len(event[k]) <= 2000 for k in ('id', 'checked_at', 'reason'))
        if kind == 'job_scope':
            jobs = event.get('jobs')
            return (isinstance(event.get('id'), str) and 0 < len(event['id']) <= 256
                    and isinstance(jobs, list) and len(jobs) <= 1000
                    and all(isinstance(job, list) and len(job) == 3
                            and all(isinstance(v, str) and len(v) <= 256 for v in job) for job in jobs))
        if kind == 'verification_progress':
            return (event.get('phase') in ('session', 'courses', 'chapters')
                    and isinstance(event.get('message'), str) and len(event['message']) <= 2000
                    and all(type(event.get(k)) is int and 0 <= event[k] <= 10**9
                            for k in ('completed', 'total', 'reads', 'failures', 'elapsed_ms'))
                    and event['completed'] <= event['total'])
        if kind == 'verification_done':
            return isinstance(event.get('message'), str) and len(event['message']) <= 2000
        if kind == 'ai_usage':
            return (all(type(event.get(k)) is int and 0 <= event[k] <= 10**15
                        for k in ('requests', 'limit', 'known', 'unknown', 'tokens'))
                    and event['known'] + event['unknown'] == event['requests'])
        if kind == "task":
            return isinstance(event.get("id"), str) and isinstance(event.get("status"), str)
        if kind == "done":
            return all(type(event.get(key)) is int and event[key] >= 0 for key in ("success", "pending", "failed", "skipped"))
        if kind == "input":
            return isinstance(event.get("question", {}), dict) and isinstance(event.get("choices", []), list) and all(
                isinstance(choice, dict) and isinstance(choice.get("label"), str) and isinstance(choice.get("value"), str)
                for choice in event.get("choices", []))
        if kind == "exam_list":
            exams = event.get("exams")
            return (
                type(event.get("demo")) is bool
                and isinstance(exams, list)
                and len(exams) <= 500
                and all(
                    isinstance(item, dict)
                    and all(
                        isinstance(item.get(k), str) and len(item[k]) <= 1024
                        for k in ("examId", "courseId", "clazzId", "courseTitle", "title", "timeText", "modeText", "status", "statusText", "reason", "officialUrl")
                    )
                    and bool(item["examId"].strip())
                    and bool(item["title"].strip())
                    and type(item.get("nativeSupported")) is bool
                    for item in exams
                )
            )
        if kind == "exam_opened":
            from .exam_drafts import _validate_question, _validate_answer_for_question, MAX_QUESTIONS
            paper = event.get("paper")
            platform_answers = event.get("platform_answers")
            if (
                not isinstance(paper, dict)
                or not isinstance(event.get("server_now_utc"), str)
                or len(event["server_now_utc"]) > 100
                or not isinstance(platform_answers, dict)
            ):
                return False
            if (
                not all(isinstance(paper.get(k), str) and 0 < len(paper[k].strip()) <= 500 for k in ("course_id", "clazz_id", "exam_id", "attempt_id", "title"))
                or not isinstance(paper.get("course_title", ""), str)
                or len(paper.get("course_title", "")) > 500
                or not isinstance(paper.get("deadline_utc", ""), str)
                or len(paper.get("deadline_utc", "")) > 100
                or type(paper.get("demo")) is not bool
            ):
                return False
            questions = paper.get("questions")
            if not isinstance(questions, list) or not 1 <= len(questions) <= MAX_QUESTIONS:
                return False
            qmap = {}
            try:
                for q in questions:
                    _validate_question(q)
                    qmap[q["id"]] = q
                for qid, ans in platform_answers.items():
                    if not isinstance(qid, str) or qid not in qmap or not isinstance(ans, dict):
                        return False
                    norm = {
                        "option_ids": list(ans.get("option_ids", [])),
                        "text": str(ans.get("text", "")),
                        "blanks": list(ans.get("blanks", [])),
                        "source": str(ans.get("source", "platform") or "platform"),
                        "updated_at": str(ans.get("updated_at", "")),
                    }
                    _validate_answer_for_question(qmap[qid], norm)
            except (ValueError, TypeError):
                return False
            return True
        if kind == "exam_unavailable":
            return all(isinstance(event.get(k), str) and 0 < len(event[k]) <= 1024
                       for k in ("exam_id", "course_id", "clazz_id", "reason"))
        if kind in ("exam_suggestion", "homework_suggestion"):
            ans = event.get("answer")
            attempts = event.get("attempts")
            platform_saved = event.get("platform_saved", False)
            save_reason = event.get("save_reason", "")
            return (
                isinstance(event.get("question_id"), str)
                and 0 < len(event["question_id"]) <= 256
                and isinstance(ans, dict)
                and isinstance(ans.get("option_ids", []), list)
                and all(isinstance(v, str) and len(v) <= 256 for v in ans.get("option_ids", []))
                and isinstance(ans.get("blanks", []), list)
                and all(isinstance(v, str) and len(v) <= 12000 for v in ans.get("blanks", []))
                and isinstance(ans.get("text", ""), str)
                and len(ans.get("text", "")) <= 12000
                and isinstance(event.get("source"), str)
                and len(event["source"]) <= 128
                and isinstance(event.get("reason"), str)
                and len(event["reason"]) <= 2000
                and isinstance(attempts, list)
                and len(attempts) <= 32
                and all(isinstance(a, dict) for a in attempts)
                and type(platform_saved) is bool
                and isinstance(save_reason, str)
                and len(save_reason) <= 500
            )
        if kind in ("exam_assist_done", "homework_assist_done"):
            saved_count = event.get("saved_count", 0)
            rolling_enc = event.get("rolling_enc", [])
            return (
                all(type(event.get(k)) is int and 0 <= event[k] <= 10**6 for k in ("completed", "total", "resolved"))
                and event["completed"] <= event["total"]
                and type(saved_count) is int
                and 0 <= saved_count <= 10**6
                and isinstance(rolling_enc, list)
                and len(rolling_enc) <= 3
                and all(isinstance(v, str) and len(v) <= 256 for v in rolling_enc)
                and isinstance(event.get("message", ""), str)
                and len(event.get("message", "")) <= 2000
            )
        if kind == "exam_saved":
            return (
                all(isinstance(event.get(k), str) and 0 < len(event[k]) <= 256 for k in ("exam_id", "attempt_id"))
                and type(event.get("verified")) is bool
                and isinstance(event.get("reason"), str)
                and len(event["reason"]) <= 2000
            )
        if kind == "exam_submitted":
            return (
                all(isinstance(event.get(k), str) and 0 < len(event[k]) <= 256 for k in ("exam_id", "attempt_id"))
                and event.get("state") in ("submitted", "simulated_submitted", "submit_unknown", "rejected")
                and isinstance(event.get("reason"), str)
                and len(event["reason"]) <= 2000
            )
        if kind == "homework_list":
            works = event.get("homeworks")
            return (
                type(event.get("demo")) is bool
                and isinstance(works, list)
                and len(works) <= 1000
                and all(
                    isinstance(item, dict)
                    and all(
                        isinstance(item.get(k), str) and len(item[k]) <= 1024
                        for k in ("workId", "courseId", "clazzId", "courseTitle", "title", "status", "statusText", "timeText", "workUrl", "reason")
                    )
                    and bool(item["title"].strip())
                    and type(item.get("canOpen")) is bool
                    for item in works
                )
            )
        if kind == "homework_loaded":
            hw = event.get("homework")
            return (
                isinstance(hw, dict)
                and isinstance(hw.get("title"), str)
                and 0 < len(hw["title"]) <= 500
                and isinstance(hw.get("questions"), list)
                and 1 <= len(hw["questions"]) <= 200
                and type(event.get("demo")) is bool
            )
        if kind == "homework_saved":
            return (
                isinstance(event.get("work_id"), str)
                and len(event["work_id"]) <= 256
                and type(event.get("verified")) is bool
                and isinstance(event.get("reason"), str)
                and len(event["reason"]) <= 2000
            )
        return True

    def _fail_protocol(self):
        self._protocol_failed = True
        self.buffer.clear()
        self.event.emit({"type": "error", "message": "后台事件格式错误或数据过大，任务已停止，请重试。"})
        self.process.kill()
        if self._exit_code is not None:
            self._finish()

    def read_errors(self):
        # Raw stderr can contain unredacted traces. Report without displaying it.
        data = bytes(self.process.readAllStandardError())
        if data:
            self.event.emit({"type": "log", "level": "WARNING", "message": "后台产生诊断输出；若任务失败，请检查依赖安装。"})

    def reply(self, answer="", cancel=False):
        if self.active and not self.stopping:
            self.process.write((json.dumps({"command": "cancel" if cancel else "reply", "request_id": self.request_id, "answer": answer, "cancel": cancel}, ensure_ascii=False) + "\n").encode("utf-8"))

    def pause(self):
        if self.active and not self.stopping:
            self.paused = not self.paused
            self.process.write((json.dumps({'command': 'pause' if self.paused else 'resume'}) + '\n').encode('utf-8'))

    def stop(self):
        if self.active and not self.stopping:
            self.stopping = True
            self.process.write(b'{"command":"cancel"}\n')
            self._kill_timer.start(3000)

    def on_finished(self, code, _status):
        # 开卷与读作业的题图由工作台保留，答题和参考答案进程会继续读取。
        if code != 0 or not self.keep_image_cache:
            remove_image_cache(self.image_cache_dir)
        self.image_cache_dir = None
        # Deliver queued done/error events before announcing process exit.
        self._exit_code = code
        self.read_output()

    def _finish(self):
        self._kill_timer.stop()
        self.paused = False
        code, self._exit_code = self._exit_code, None
        self._drain_timer.stop()
        self.buffer.clear()
        self.active = False
        self.payload = {}
        self.busy_changed.emit(False)
        self.finished.emit(code)

    def on_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            remove_image_cache(self.image_cache_dir)
            self.image_cache_dir = None
            self.active = False
            self.payload = {}
            self.event.emit({"type": "error", "message": "后台无法启动，请确认软件目录完整且未被隔离。"})
            self.busy_changed.emit(False)
            self.finished.emit(-1)
