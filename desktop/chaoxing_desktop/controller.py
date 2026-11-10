import json
from pathlib import Path
import sys
import uuid
import time

from PySide6.QtCore import QObject, QProcess, Signal, QTimer


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
        self.request_id = ''
        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.timeout.connect(self.process.kill)

    def start(self, payload):
        if self.active:
            raise RuntimeError("已有后台任务正在运行。")
        self.run_id = uuid.uuid4().hex
        self.payload = dict(payload, run_id=self.run_id)
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
        if kind == 'record_card_hint':
            return (isinstance(event.get('id'), str) and len(event['id']) <= 256
                    and isinstance(event.get('indices'), list) and 0 < len(event['indices']) <= 64
                    and all(type(i) is int and 0 <= i < 64 for i in event['indices']))
        if kind == 'job_scope':
            jobs = event.get('jobs')
            return (isinstance(event.get('id'), str) and 0 < len(event['id']) <= 256
                    and isinstance(jobs, list) and len(jobs) <= 1000
                    and all(isinstance(job, list) and len(job) == 3
                            and all(isinstance(v, str) and len(v) <= 256 for v in job) for job in jobs))
        if kind == 'record_verification':
            return (event.get('kind') in ('discussion', 'activity') and
                    event.get('participation') in ('existing', 'absent', 'unknown') and
                    event.get('task_status') in ('confirmed', 'pending') and
                    all(isinstance(event.get(k), str) and len(event[k]) <= 2000
                        for k in ('id', 'task_id', 'checked_at', 'reason')))
        if kind == 'verification_progress':
            return (event.get('phase') in ('session', 'courses', 'chapters', 'cards', 'records')
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
            self.active = False
            self.payload = {}
            self.event.emit({"type": "error", "message": "后台无法启动，请确认软件目录完整且未被隔离。"})
            self.busy_changed.emit(False)
            self.finished.emit(-1)
