"""Private JSON-lines transport for the native WinUI 3 frontend.

Qt Core owns the existing controllers/models, but no Qt window or QML engine
is created. All dialogs, clipboard operations and rendering belong to WinUI.
The pipe is inherited by the child; no port or persistent credential file.
"""
from __future__ import annotations

import json
import queue
import sys
import threading

from PySide6.QtCore import QCoreApplication, QTimer

from .qml_backend import Backend
from .instance import InstanceGuard
from .config import data_dir
from .release_info import RELEASE_URLS, verification_info


# Deliberately exclude event handlers and internal methods from RPC.
COMMANDS = frozenset("""
setValue save close discardAndClose loadDemo fetchCourses startTasks
confirmStartTasks cancelStartTasks pause stop browserLogin cancelBrowserLogin
addAccount switchAccount renameAccount removeAccount searchCourses selectCourse
selectVisible toggleProvider moveProvider checkAi checkBank offlineCheck
fileAction definitionAction finishDefinitionImport chooseLogDirectory clearLogs
deleteLogs filterLogs filterQuestions filterDiscussions filterActivities filterAttention
questionDetail discussionDetail activityDetail copyDiscussion copyActivity
previewDiscussion previewActivity reply verifyPending verifyRecords verifyAllRecords
listAiProfiles aiProfileAction loadRunHistory prepareRecovery listDraftCopies
openDraftCopy openSavedDraft setDraftContent setDraftAnswer saveDraftCopy
exportDraftCopy deleteSavedDraft draftAnswer clearUiFeedback releaseAction
""".split())
MAX_COMMAND_BYTES = 1024 * 1024


class PathDialogs:
    """A path already selected by the native Windows file picker."""
    path = ""

    def getOpenFileName(self, *_):
        return self.path, ""

    getSaveFileName = getOpenFileName

    def getExistingDirectory(self, *_):
        return self.path


class NativeBackend(Backend):
    def __init__(self, send):
        self.send = send
        self.file_dialogs = PathDialogs()
        super().__init__()

    def copy_text(self, text):
        self.send(dict(type="clipboard", text=text))

    def releaseAction(self, action):
        if action == "copy":
            self.copy_text(verification_info())
            self.feedback('release', '版本与校验说明已复制；不含账号、凭据或数据目录。', 'success')
        elif action in RELEASE_URLS:
            self.send(dict(type="openUrl", url=RELEASE_URLS[action]))


class Bridge:
    def __init__(self, app, send):
        self.app, self.send = app, send
        self.backend = NativeBackend(send)
        self.guard = InstanceGuard(data_dir(), app)
        self.guard.activate = lambda: send(dict(type="activate"))
        self.dirty = set()
        self.timer = QTimer()
        self.timer.setSingleShot(True)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.flush)
        b = self.backend
        groups = {
            "summary": b.changed, "values": b.configChanged,
            "courses": b.coursesChanged, "providers": b.providersChanged,
            "input": b.inputChanged, "draft": b.draftChanged,
        }
        for group, signal in groups.items():
            signal.connect(lambda g=group: self.mark(g))
        b.busyChanged.connect(lambda: self.mark("summary"))
        for name in ("tasks", "questions", "discussions", "activities", "attention", "logs"):
            model = self.model(name)
            for signal in (model.modelReset, model.rowsInserted, model.rowsRemoved, model.dataChanged):
                signal.connect(lambda *_, n=name: self.mark(n))
        for name in ("pageRequested", "fieldRequested", "startConfirmationRequested", "coursesLoaded",
                     "definitionPreviewRequested", "draftOpened"):
            getattr(b, name).connect(lambda *args, n=name: send(dict(type="event", name=n, args=args)))

    def model(self, name):
        return getattr(self.backend, dict(tasks="taskModel", questions="questionModel",
                       discussions="discussionModel", activities="activityModel",
                       attention="attentionModel", logs="logModel")[name])

    def mark(self, name):
        self.dirty.add(name)
        if not self.timer.isActive():
            self.timer.start()

    def snapshot(self, names):
        b = self.backend
        state = {}
        for name in names:
            if name in ("tasks", "questions", "discussions", "activities", "attention", "logs"):
                model = self.model(name)
                state[name] = [model.data(model.index(i, 0), 257) for i in range(model.rowCount())]
            elif name == "summary":
                state.update(summary=b.summary, accountRows=b.accountRows, serviceHealth=b.serviceHealth,
                             strategySummary=b.strategySummary, capacityInfo=b.capacityInfo,
                             discussionPreview=b.discussionPreview, activityPreview=b.activityPreview,
                             runHistory=b.runHistory, historyError=b.historyError, uiFeedback=b.uiFeedback,
                             aiWarning=b.aiWarning)
            elif name == "courses":
                state["courses"] = b.courseRows
            elif name == "draft":
                state.update(draftFeedback=b.draftFeedback, draftDirty=b.draftDirty,
                             draftEditable=b.draftEditable, draftFeedbackTone=b.draftFeedbackTone)
            else:
                state[name] = getattr(b, name)
        return state

    def flush(self):
        names, self.dirty = self.dirty, set()
        if names:
            self.send(dict(type="state", data=self.snapshot(names)))

    def start(self):
        if not self.guard.acquire():
            self.send(dict(type="alreadyRunning"))
            return False
        self.send(dict(type="ready", data=dict(version=self.backend.version,
                  schema=self.backend.schema, releaseInfo=self.backend.releaseInfo,
                  dataExplanation=self.backend.dataExplanation)))
        self.dirty.update(("summary", "values", "courses", "providers", "input", "draft",
                           "tasks", "questions", "discussions", "activities", "attention", "logs"))
        self.flush()
        return True

    def dispatch(self, request):
        request_id = request.get("id") if isinstance(request, dict) else None
        try:
            if not isinstance(request, dict) or request.get("method") not in COMMANDS:
                raise ValueError("不支持的本机命令")
            args = request.get("args", [])
            if not isinstance(args, list) or len(args) > 4:
                raise ValueError("命令参数无效")
            path = request.get("path", "")
            if not isinstance(path, str) or len(path) > 32768:
                raise ValueError("文件路径无效")
            self.backend.file_dialogs.path = path
            if request['method'] in ('close', 'discardAndClose'):
                # Acknowledge a successful save before waiting for workers.
                # WinUI hides the window, then awaits this process's cleanup.
                if args:
                    raise ValueError("命令参数无效")
                result = self.backend._prepare_close(request['method'] == 'discardAndClose')
            else:
                result = getattr(self.backend, request["method"])(*args)
            if request['method'] == 'loadRunHistory':
                result = self.backend.runHistory
            # Make synchronous mutations visible before resolving the command.
            # Native dialogs can then open against the committed state.
            self.mark("summary")
            self.flush()
            self.send(dict(type="response", id=request_id, result=result))
            if request["method"] in ("close", "discardAndClose") and result:
                self.app.quit()
        except Exception as exc:
            # Do not echo commands, arguments, paths or tracebacks with secrets.
            self.send(dict(type="response", id=request_id, error=self.backend.redact(str(exc))))
        finally:
            self.backend.file_dialogs.path = ""

    def dispose(self):
        self.backend.discardAndClose()
        self.guard.close()


def main():
    app = QCoreApplication(sys.argv)
    app.setApplicationName("ChaoxingDesktop")
    output = sys.stdout

    def send(message):
        output.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
        output.flush()

    # Incidental library prints must never corrupt the protocol.
    sys.stdout = sys.stderr
    bridge = Bridge(app, send)
    if not bridge.start():
        bridge.dispose()
        return 0
    inbox = queue.Queue(maxsize=64)

    def read():
        try:
            while True:
                line = sys.stdin.buffer.readline(MAX_COMMAND_BYTES + 1)
                if not line:
                    break
                if len(line) > MAX_COMMAND_BYTES:
                    break
                try:
                    inbox.put(json.loads(line))
                except (ValueError, UnicodeError):
                    break
        finally:
            inbox.put(None)

    threading.Thread(target=read, daemon=True).start()
    poll = QTimer()
    poll.setInterval(10)

    def drain():
        for _ in range(16):
            try:
                request = inbox.get_nowait()
            except queue.Empty:
                return
            if request is None:
                app.quit()
                return
            bridge.dispatch(request)

    poll.timeout.connect(drain)
    poll.start()
    try:
        return app.exec()
    finally:
        bridge.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
