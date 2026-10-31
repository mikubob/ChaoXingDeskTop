"""Qt Quick application state; the worker and INI contracts stay unchanged."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer
from PySide6.QtWidgets import QFileDialog

from . import __version__
from .answers import option_pairs, plain_text, question_images
from .config import BANKS, Redactor, course_key, data_dir, defaults, is_secret, parse_text, serialize, to_payload, validate
from .controller import Controller
from .qml_schema import SCHEMA
from .settings import restore_credentials, serialize_settings
from .models import LogModel, LogFilter, TaskModel, STATUS, TERMINAL
from .log_store import LogStore, AsyncLogStore
from .question_model import QuestionModel, QuestionFilter
from .discussion_model import DiscussionModel, DiscussionFilter, safe_record as safe_discussion
from .activity_model import ActivityModel, ActivityFilter, safe_record as safe_activity, detail_text
from .attention_model import AttentionModel, AttentionFilter, chapter_issue, record_issue
from .query_result import safe_record
from .providers import PROVIDER_SPECS
from .accounts import AccountStore, cookie_uid, parse_cookie, write_cookie
from .browser_login import BrowserLogin
from .storage import revision, write_if_unchanged, atomic_write
from .diagnostic_export import diagnostic_config
from .run_history import RunHistory
from .draft_copies import DraftCopies, make_copy, fingerprint, editable_source, constraints, export_text
from .release_info import RELEASE_URLS, verification_info
from .ai_profiles import AIProfiles
from .course_progress import CourseProgressCache, progress_key, valid_progress


class Backend(QObject):
    # Native frontends replace these adapters; business operations stay shared.
    file_dialogs = QFileDialog

    def copy_text(self, text):
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(text)

    changed = Signal()
    busyChanged = Signal()
    configChanged = Signal()
    inputChanged = Signal()
    providersChanged = Signal()
    coursesChanged = Signal()
    coursesLoaded = Signal()
    pageRequested = Signal(int)
    questionsReset = Signal()
    startConfirmationRequested = Signal(str)
    fieldRequested = Signal(str, str)
    definitionPreviewRequested = Signal(str, bool)
    draftOpened = Signal('QVariantMap')
    draftChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = defaults()
        self._closed = False
        self._browser_login = None
        self._browser_sessions = []
        self.startup_error = None
        self.message = "准备就绪。连接账户，或通过离线演示体验学习工作台。"
        self.tone = "info"
        self._ui_feedback = {}
        self.save_state = "设置已同步"
        settings = data_dir() / "settings.ini"
        try:
            self._settings_revision = revision(settings)
        except OSError:
            self._settings_revision = None
        if settings.exists():
            try:
                self.config = parse_text(settings.read_text(encoding="utf-8-sig"))
                validate(self.config)
                restore_credentials(self.config)
            except Exception as exc:
                self.startup_error = str(exc)
                # Keep the original file until explicitly saved, including DPAPI failure.
                try:
                    validate(self.config)
                except ValueError:
                    self.config = defaults()
                self.message = "本地设置读取失败：" + self.redact(exc)
                self.tone = "danger"
                self.save_state = "读取失败 · 原文件已保留"
        self.courses = []
        self.account_store = None
        self.account_error = ""
        self.operation_account = ""
        try:
            self.account_store = AccountStore(data_dir())
            self.account_store.migrate(self.config['common']['username'].strip())
            current = self.account_store.get(self.account_store.active)
            if current:
                self.config['common']['username'] = current['username']
                self.config['common']['password'] = ""
                if self.cookie_path().exists():
                    self.config['common']['use_cookies'] = 'true'
            elif self.account_store.path.exists():
                self.config['common'].update(username='', password='', use_cookies='false')
        except Exception as exc:
            self.account_store = None
            self.account_error = "账号读取失败，原文件已保留：" + self.redact(exc)
            self.message, self.tone = self.account_error, 'danger'
        self.selected = set()
        self.search = ""
        self.demo_mode = False
        self.course_identity = None
        self._start_confirmation = None
        self._approved_policy = None
        self.service_checks = {}
        self.check_target = ''
        self._definition_import = None
        self.ai_usage = dict(requests=0, limit=0, known=0, unknown=0, tokens=0)
        self._ai_token_warning = 0
        self.history_rows = []
        self.history_error = ''
        self._history_account = ''
        self._history_run = ''
        self._history_time = ''
        self._recovery = None
        self._draft = None
        self._draft_account = ''
        self._draft_feedback = ''
        self._draft_saved = False
        self._draft_dirty = False
        self._draft_input_errors = {}
        self._draft_sources_uncertain = False
        self._run_completed = False
        self._history_timer = QTimer(self)
        self._history_timer.setSingleShot(True)
        self._history_timer.setInterval(2000)
        self._history_timer.timeout.connect(lambda: self.save_run_summary('运行中 / 若已退出则为中断快照'))
        self._task_model = TaskModel(self)
        self._log_model = LogModel(self)
        self._log_filter = LogFilter(self._log_model, self)
        self._question_model = QuestionModel(self)
        self._question_filter = QuestionFilter(self._question_model, self)
        self._question_revision = 0
        self._discussion_model = DiscussionModel(self)
        self._discussion_filter = DiscussionFilter(self._discussion_model, self)
        self.discussion_preview = dict(content='', level='', reason='输入示例主题，预览不会发布到课程。')
        self._activity_model = ActivityModel(self)
        self._activity_filter = ActivityFilter(self._activity_model, self)
        self._attention_model = AttentionModel(self)
        self._attention_filter = AttentionFilter(self._attention_model, self)
        self.activity_preview = dict(content='', reason='选择策略后预览混合问卷；不会读取课程或提交。')
        self.bank_status = "尚未检查"
        self.log_store = AsyncLogStore(self)
        self.log_store.failed.connect(self.on_log_failure)
        self.log_store.written.connect(self.on_log_written)
        self.log_save_error = ""
        self._progress_timer = QTimer(self)
        self._progress_timer.setSingleShot(True)
        self._progress_timer.setInterval(100)
        self._progress_timer.timeout.connect(self.changed.emit)
        self.operation = None
        self.outcome = False
        self.mode = "本地工作台"
        self.ai_status = "尚未测试连接"
        self.interaction = {}
        self.provider_order = []
        self.sync_providers()
        self.log_redactor = Redactor({})
        self.controller = Controller(self)
        self.controller.event.connect(self.on_event)
        self.controller.finished.connect(self.on_finished)
        self.controller.busy_changed.connect(lambda _: self.busyChanged.emit())
        self.controller.busy_changed.connect(lambda busy: self.changed.emit() if busy else None)
        self._course_progress = {}
        self._course_progress_identity = None
        self._course_refresh_pending = False
        self._course_controller = Controller(self)
        self._course_controller.event.connect(self.on_course_progress_event)
        self._course_controller.finished.connect(self.on_course_progress_finished)
        self.configChanged.connect(self.refresh_redactor)
        self._account_rows_cache, self._cookie_exists = [], False
        self.verification_progress = {}
        self._record_cards = {}
        self.configChanged.connect(self.refresh_account_status)
        self.refresh_account_status()
        self.refresh_redactor()

    def refresh_redactor(self):
        self._redactor = Redactor({s: dict(self.config[s]) for s in self.config.sections()})
        self._redactor_signature = self.secret_signature()

    def secret_signature(self):
        return tuple((s, k, v) for s in self.config.sections() for k, v in self.config[s].items() if is_secret(k))

    def redact(self, message):
        if getattr(self, '_redactor_signature', None) != self.secret_signature():
            self.refresh_redactor()
        return self._redactor(str(message))

    @Property(str, constant=True)
    def version(self):
        return __version__

    @Property(str, constant=True)
    def releaseInfo(self):
        return verification_info() + '\n\n当前数据 profile：' + str(data_dir()) + '\n\n检查版本将主动打开固定的官方发行网页；不会自动下载或执行。升级前退出程序并备份 settings、accounts、Cookie、discussions、activities、run_summaries、draft_copies 和 ai_profiles.json。DPAPI 绑定原 Windows 用户与本机。回退旧程序前应核对数据格式，保留完整备份。'

    @Slot(str)
    def releaseAction(self, action):
        if action == 'copy':
            from PySide6.QtGui import QGuiApplication
            self.copy_text(verification_info())
            self.feedback('release', '版本与校验说明已复制；不含账号、凭据或数据目录。', 'success')
        elif action in RELEASE_URLS:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            if not QDesktopServices.openUrl(QUrl(RELEASE_URLS[action])):
                self.feedback('release', '未能打开系统浏览器，请稍后重试。', 'warning')

    @Property("QVariantMap", constant=True)
    def schema(self):
        return SCHEMA

    @Property("QVariantMap", notify=configChanged)
    def values(self):
        return {s: dict(self.config[s]) for s in self.config.sections()}

    @Property("QVariantMap", notify=changed)
    def state(self):
        """Complete snapshot for tests/tools; QML uses the small summary below."""
        return dict(self.summary, courses=self.courseRows,
                    tasks=[dict(t, statusText=STATUS.get(t["status"], t["status"])) for t in self.tasks],
                    logs="\n".join(self.logs), providers=self.providers)

    @property
    def tasks(self):
        return self._task_model.rows

    @property
    def logs(self):
        return self._log_model.snapshot()

    @Property(QObject, constant=True)
    def taskModel(self):
        return self._task_model

    @Property(QObject, constant=True)
    def questionModel(self):
        return self._question_filter

    @Property(QObject, constant=True)
    def discussionModel(self):
        return self._discussion_filter

    @Property('QVariantMap', notify=changed)
    def discussionPreview(self):
        return dict(self.discussion_preview)

    @Property(QObject, constant=True)
    def activityModel(self):
        return self._activity_filter

    @Property('QVariantMap', notify=changed)
    def activityPreview(self):
        return dict(self.activity_preview)

    @Property(QObject, constant=True)
    def logModel(self):
        return self._log_filter

    @Slot(str, str)
    def filterLogs(self, level, query):
        self._log_filter.set_filter(level, query)

    @Property("QVariantList", notify=coursesChanged)
    def courseRows(self):
        return [dict(c, selected=course_key(c) in self.selected,
                     progress=self._course_progress.get(progress_key(c), dict(loading=False))) for c in self.courses
                if self.course_visible(c)]

    def course_progress_cache(self):
        if self.demo_mode or not self.account_store or not self.account_store.active:
            return None
        account = self.account_store.get(self.account_store.active)
        if account and account['uid']:
            return CourseProgressCache(data_dir(), account['id'], account['uid'])
        return None

    def load_course_progress(self):
        cache = self.course_progress_cache()
        rows = cache.read() if cache else {}
        self._course_progress = {
            progress_key(c): dict(rows.get(progress_key(c), {}), loading=not self.demo_mode,
                                  cached=progress_key(c) in rows, failed=False)
            for c in self.courses}

    def cancel_course_progress(self):
        self._course_refresh_pending = False
        self._course_progress_identity = None
        self._course_controller.stop()
        for row in self._course_progress.values():
            row['loading'] = False
        self.coursesChanged.emit()

    def start_course_progress(self):
        if self._closed or self.demo_mode or self.busy or not self.courses:
            return
        try:
            if self.identity() != self.course_identity or self.course_progress_cache() is None:
                return
            if self._course_controller.active:
                self._course_refresh_pending = True
                return
            config = defaults()
            config['common'].update(username=self.config['common']['username'], password='', use_cookies='true')
            self._course_progress_identity = self.identity()
            account = self.account_store.get(self.account_store.active)
            for row in self._course_progress.values():
                row.update(loading=True, failed=False)
            self.coursesChanged.emit()
            self._course_controller.start(dict(
                action='course_progress', demo=False, config=to_payload(config),
                selected=[list(course_key(c)) for c in self.courses], data_dir=str(data_dir()),
                account_id=account['id'], account_uid=account['uid'],
                cookie_mtime_ns=self._course_progress_identity[-1]))
        except (OSError, ValueError):
            self.on_course_progress_finished(-1)

    def apply_course_progress(self, event):
        key = progress_key(event)
        if key not in {progress_key(c) for c in self.courses}:
            return
        row = self._course_progress.setdefault(key, {})
        kind = event['type']
        if kind == 'course_progress_loading':
            row.update(loading=True, failed=False)
        elif kind == 'course_progress_failed':
            row.update(loading=False, failed=True)
        elif kind == 'course_progress' and valid_progress(event):
            row.update({k: event[k] for k in ('completed', 'total', 'checked_at')})
            row.update(loading=False, cached=False, failed=False)
            cache = self.course_progress_cache()
            if cache:
                try:
                    cache.save(event)
                except (OSError, ValueError):
                    pass  # A cache write failure must not interrupt learning.
        self.coursesChanged.emit()

    def on_course_progress_event(self, event):
        try:
            if self._course_progress_identity is None or self.identity() != self._course_progress_identity:
                return
        except OSError:
            return
        if event['type'] in ('course_progress', 'course_progress_loading', 'course_progress_failed'):
            self.apply_course_progress(event)
        elif event['type'] == 'input':
            # Background statistics never interrupt the user with a login prompt.
            self._course_controller.stop()

    def on_course_progress_finished(self, code):
        if self._course_progress_identity is not None:
            for row in self._course_progress.values():
                if row.get('loading'):
                    row.update(loading=False, failed=True)
            self.coursesChanged.emit()
        self._course_progress_identity = None
        pending, self._course_refresh_pending = self._course_refresh_pending, False
        if pending:
            QTimer.singleShot(0, self.start_course_progress)

    def course_visible(self, course):
        return not self.search or self.search.casefold() in (course["title"] + str(course["courseId"])).casefold()

    @Property("QVariantMap", notify=changed)
    def summary(self):
        hidden = sum(course_key(c) in self.selected and not self.course_visible(c) for c in self.courses) if self.search else 0
        counts = self._task_model.counts
        total = len(self.tasks)
        return dict(message=self.message, tone=self.tone, busy=self.busy,
                    browserLogin=self._browser_login is not None,
                    stopping=self.controller.stopping and self.controller.active,
                    paused=self.controller.paused, bankStatus=self.bank_status,
                    questionCount=len(self._question_model.rows), questionEvicted=self._question_model.evicted,
                    attentionEvicted=self._attention_model.evicted,
                    questionRevision=self._question_revision,
                    questionStats=dict(self._question_model.stats),
                    discussionCount=len(self._discussion_model.rows), discussionEvicted=self._discussion_model.evicted,
                    discussionStats={key: self._discussion_model.stats.stages[key]
                                     for key in ('draft', 'published', 'existing', 'publish_unknown', 'pending', 'skipped')},
                    discussionConfirmed=self._discussion_model.stats.confirmed,
                    activityCount=len(self._activity_model.rows), activityEvicted=self._activity_model.evicted,
                    activityStats={key: self._activity_model.stats.stages[key]
                                   for key in ('draft', 'submitted', 'existing', 'submit_unknown', 'pending', 'rejected')},
                    activitySources={key: self._activity_model.stats.sources[key] for key in ('ai', 'random', 'fixed')},
                    activityConfirmed=self._activity_model.stats.confirmed,
                    verificationProgress=dict(self.verification_progress),
                    mode=self.mode, demo=self.demo_mode, courseCount=len(self.courses),
                    selectedCount=len(self.selected), hiddenCount=hidden,
                    courseSearch=self.search,
                    coursesReady=self.course_identity is not None,
                    total=total, success=counts["success"], pending=counts["pending"] + counts["processed"],
                    failed=counts["failed"], progress=round(100 * sum(counts[s] for s in TERMINAL) / total) if total else 0,
                    saveState=self.save_state, aiStatus=self.ai_status,
                    cookie=self._cookie_exists,
                    activeAccount=self.account_store.active if self.account_store else '',
                    accountCount=len(self.account_store.rows) if self.account_store else 0,
                    accountError=self.account_error,
                    logDirectory=self._log_directory,
                    logSaveError=self.log_save_error)

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self.controller.active or self._browser_login is not None

    @Property('QVariantMap', notify=changed)
    def aiUsage(self):
        return dict(self.ai_usage)

    @Slot(result='QVariantList')
    def listAiProfiles(self):
        try:
            return [dict(id=r['id'], name=r['name']) for r in AIProfiles(data_dir()).read()[0]]
        except Exception as exc:
            self.feedback('profiles', exc, 'danger')
            return []

    @Slot(str, str, result=bool)
    def aiProfileAction(self, action, value):
        def run():
            if self.busy:
                raise ValueError('运行中锁定 AI 档案。')
            store = AIProfiles(data_dir())
            if action == 'save':
                store.save(value, self.config)
                self.feedback('profiles', '当前 AI 设置已保存为命名档案，配置与凭据均使用 DPAPI 加密。', 'success')
            elif action == 'load':
                values = store.load(value)
                candidate = parse_text(serialize(self.config)); candidate['ai'].update(values); validate(candidate)
                self.config = candidate
                self.save_state = '有修改 · 未保存'
                self.ai_status = '已切换档案 · 请主动测试；视觉仅为用户声明'
                self.configChanged.emit()
                self.feedback('profiles', '已显式切换 AI 档案；没有联网。请检查目标、模型与视觉声明。', 'success')
            elif action == 'delete':
                store.delete(value)
                self.feedback('profiles', '档案已删除；当前已加载设置副本保持，可重新命名保存。', 'success')
        return self.execute(run, 'profiles')

    @Property(str, notify=changed)
    def capacityInfo(self):
        metrics = self.log_store.metrics()
        lines = ['容量只约束显示／摘要；不会删除任何未决提交意图。',
                 f'日志显示：保留 {len(self._log_model.rows)}，待显示 {len(self._log_model.pending)}，已淘汰 {self._log_model.evicted}；上限 2500 行 / 512 Ki 字符。',
                 f'日志写盘：积压 {metrics["pending_bytes"]} 字节，峰值 {metrics["peak_bytes"]} / 2097152 字节；失败批次 {metrics["failed_batches"]}，队列拒绝行数 {metrics["dropped_lines"]}。',
                 '导出日志只含当前有界显示快照，包括待显示的最新批次；不是完整磁盘历史。',
                 '磁盘日志：每文件约 10 MiB，总计约 100 MiB，保留 14 天。']
        for name, model in [('逐题', self._question_model), ('讨论', self._discussion_model), ('活动', self._activity_model), ('待处理', self._attention_model)]:
            lines.append(f'{name}显示：保留 {len(model.rows)}，已淘汰 {model.evicted}；上限 2000 项 / 8 Mi 文本容量。')
        lines.extend(['运行摘要：按账号最多 20 轮 / 2 MiB，单轮前 1000 个未完成章节定位。',
                      '命名 AI 档案：最多 20 项 / 256 KiB，单份解密前配置最多 16 KiB；满容量阻止新保存。',
                      '人工副本：按账号最多 20 项 / 2 MiB；容量满时阻止新保存，不自动删除人工编辑。',
                      '活动意图存储容量满、记录损坏或磁盘失败会阻止新提交；不能通过清理显示解除去重。'])
        return '\n\n'.join(lines)

    @Slot(str, result='QVariantMap')
    def draftAnswer(self, question_id):
        return next((dict(a) for a in self._draft['answers'] if a['question_id'] == question_id), {}) if self._draft else {}

    def draft_account(self):
        return 'offline-demo' if self.demo_mode else self.account_store.active if self.account_store else ''

    def draft_source(self, kind, record_id):
        model = self._discussion_model if kind == 'discussion' else self._activity_model
        index = model.indices.get(record_id)
        return model.rows[index] if index is not None else None

    @Property(bool, notify=changed)
    def draftEditable(self):
        if not self._draft or self.controller.active or self._draft_sources_uncertain or self._draft_account != self.draft_account():
            return False
        source = self.draft_source(self._draft['kind'], self._draft['source']['id'])
        return bool(editable_source(self._draft['kind'], source) and fingerprint(source) == self._draft['source_hash'])

    @Property(str, notify=draftChanged)
    def draftFeedback(self):
        return self._draft_feedback

    @Property(bool, notify=draftChanged)
    def draftDirty(self):
        return self._draft_dirty

    @Property(str, notify=draftChanged)
    def draftFeedbackTone(self):
        if self._draft and (self._draft_input_errors or constraints(self._draft)):
            return 'danger'
        return 'success' if self._draft_saved else 'warning'

    def update_draft_feedback(self):
        errors = list(self._draft_input_errors.values()) + constraints(self._draft)
        self._draft_feedback = ('已保存本机副本' if self._draft_saved else '尚未保存本机副本') + ' v' + str(self._draft['version']) + '\n' + (
            '\n'.join(errors[:100]) if errors else '本地约束通过；平台当前限制、本人状态和提交资格尚未重新核验。')
        self.draftChanged.emit()

    @Slot(str, str)
    def openDraftCopy(self, kind, record_id):
        def run():
            account = self.draft_account()
            if not account or self.controller.active:
                raise ValueError('请先结束当前操作并选择原账号。')
            if self._draft_sources_uncertain:
                raise ValueError('上轮停止或异常退出，显示草稿可能早于已保存意图；仅允许回看／导出已保存副本。')
            source = self.draft_source(kind, record_id)
            row = make_copy(kind, source)
            saved, _ = DraftCopies(data_dir(), account).read()
            self._draft = next((r for r in saved if r['id'] == row['id']), row)
            self._draft_account = account
            self._draft_saved = self._draft['version'] > 0
            self._draft_dirty = False
            self._draft_input_errors = {}
            self.clearUiFeedback('draft')
            self.update_draft_feedback()
            self.draftOpened.emit(self._draft)
            self.changed.emit()
        self.execute(run)

    @Slot(str)
    def setDraftContent(self, text):
        if self.draftEditable and self._draft['kind'] == 'discussion':
            self.clearUiFeedback('draft')
            self._draft_dirty = self._draft_dirty or text != self._draft['content']
            if len(text) > 12000:
                self._draft_input_errors['content'] = '讨论输入超过 12000 字符的本地编辑容量；该输入未进入副本，请缩短后再保存／导出。'
            else:
                self._draft_input_errors.pop('content', None)
                self._draft['content'] = text
            self._draft_saved = False
            self.update_draft_feedback()

    @Slot(str, 'QVariantMap')
    def setDraftAnswer(self, question_id, answer):
        if not self.draftEditable or self._draft['kind'] != 'activity':
            return
        if question_id not in {q['id'] for q in self._draft['source']['questions']}:
            return
        candidate = dict(question_id=question_id, **{k: v for k, v in answer.items() if k in ('text', 'option_ids')})
        self.clearUiFeedback('draft')
        self._draft_dirty = True
        import copy
        test = copy.deepcopy(self._draft)
        test['answers'] = [a for a in test['answers'] if a['question_id'] != question_id] + [candidate]
        from .draft_copies import validate_copy
        try:
            validate_copy(test)
        except ValueError as exc:
            self._draft_input_errors[question_id] = str(exc) + '；该输入未进入副本，请修正后再保存／导出。'
            self._draft_saved = False
            self.update_draft_feedback()
            return
        self._draft_input_errors.pop(question_id, None)
        self._draft = test
        self._draft_saved = False
        self.update_draft_feedback()

    @Slot(result=bool)
    def saveDraftCopy(self):
        def run():
            if not self.draftEditable:
                raise ValueError('来源或账号已变化，副本保持只读；请导出保留内容。')
            if self._draft_input_errors:
                raise ValueError('存在未进入副本的超容量／非法输入，请先修正。')
            self._draft = DraftCopies(data_dir(), self._draft_account).save(self._draft)
            self._draft_saved = True
            self._draft_dirty = False
            self.update_draft_feedback()
            self.feedback('draft', '人工副本已保存到本机；未提交到平台。', 'success')
        return self.execute(run, 'draft')

    @Slot(bool)
    def exportDraftCopy(self, clipboard):
        def run():
            if not self._draft or self._draft_account != self.draft_account():
                return
            if self._draft_input_errors:
                raise ValueError('存在未进入副本的超容量／非法输入，请先修正后再复制／导出。')
            text = export_text(self._draft)
            if clipboard:
                from PySide6.QtGui import QGuiApplication
                self.copy_text(text)
                self.feedback('draft', '人工编辑副本已复制，未触发提交。', 'success')
            else:
                name, _ = self.file_dialogs.getSaveFileName(None, '导出人工编辑副本（含正文，仅供自行保管）', 'draft-copy.txt', '文本 (*.txt)')
                if name:
                    atomic_write(Path(name), text.encode('utf-8'))
                    self.feedback('draft', '编辑副本已导出，包含正文；未触发提交。', 'success')
        self.execute(run, 'draft')

    @Slot(result='QVariantList')
    def listDraftCopies(self):
        try:
            rows, _ = DraftCopies(data_dir(), self.draft_account()).read() if self.draft_account() else ([], None)
            return [dict(id=r['id'], title=r['source']['title'], version=r['version']) for r in rows]
        except Exception as exc:
            self.feedback('savedDrafts', exc, 'danger')
            return []

    @Slot(str, int, result=bool)
    def deleteSavedDraft(self, copy_id, version):
        def run():
            if self.busy or not self.draft_account():
                raise ValueError('请先结束当前操作并选择原账号。')
            DraftCopies(data_dir(), self.draft_account()).delete(copy_id, version)
            if self._draft and self._draft['id'] == copy_id:
                self._draft = None
            self.feedback('savedDrafts', '人工副本已删除；原始草稿与所有防重意图保持。', 'success')
        return self.execute(run, 'savedDrafts')

    @Slot(str, result=bool)
    def openSavedDraft(self, copy_id):
        def run():
            account = self.draft_account()
            if not account:
                raise ValueError('请先选择原账号，再查看其人工副本。')
            rows, _ = DraftCopies(data_dir(), account).read()
            row = next((r for r in rows if r['id'] == copy_id), None)
            if row:
                self._draft, self._draft_account, self._draft_saved = row, account, True
                self._draft_dirty = False
                self.clearUiFeedback('draft')
                self._draft_input_errors = {}
                self.update_draft_feedback()
                self.draftOpened.emit(row)
                self.changed.emit()
            else:
                raise ValueError('所选副本已不存在，请重新打开列表。')
        return self.execute(run, 'savedDrafts')

    @Property(str, notify=changed)
    def aiWarning(self):
        if self.ai_usage['limit'] and self.ai_usage['requests'] >= self.ai_usage['limit']:
            return '本次操作 AI 请求已达硬上限；后续请求将留待处理。'
        if self._ai_token_warning and self.ai_usage['tokens'] >= self._ai_token_warning:
            return '服务返回 token 已达告警值，未知用量尚未计入；可暂停后续或停止。'
        return ''

    @Property('QVariantList', notify=changed)
    def runHistory(self):
        return self.history_rows

    @Property(str, notify=changed)
    def historyError(self):
        return self.history_error

    @Slot()
    def loadRunHistory(self):
        account = 'offline-demo' if self.demo_mode else self.account_store.active if self.account_store else ''
        self.history_rows = []
        self.history_error = ''
        if account:
            try:
                self.history_rows = RunHistory(data_dir(), account).read()[0]
            except Exception as exc:
                self.history_error = self.redact(exc)
        self.changed.emit()

    def save_run_summary(self, state):
        if not self._history_run:
            return False
        try:
            counts = dict(self._task_model.counts)
            self.refresh_redactor()
            tasks = [dict({k: self._redactor(str(t[k]))[:200] for k in ('title', 'course', 'status')},
                          **{k: str(t[k])[:256] for k in ('courseId', 'clazzId', 'pointId') if t.get(k)})
                     for t in self.tasks if t['status'] != 'success'][:1000]
            summary = dict(id=self._history_run, time=self._history_time, state=state,
                           counts=counts, total=len(self.tasks), retained=len(tasks), tasks=tasks,
                           courses=[dict(title=self._redactor(c['title'])[:200], courseId=str(c['courseId']), clazzId=str(c['clazzId'])) for c in self.courses
                                    if course_key(c) in self.selected][:1000],
                           policy=diagnostic_config(defaults() if self.demo_mode else self.config), ai=dict(self.ai_usage))
            RunHistory(data_dir(), self._history_account).save(summary)
            self.history_error = ''
            return True
        except Exception as exc:
            self.history_error = '运行摘要未保存：' + self.redact(exc)
            self.changed.emit()
            return False

    @Slot(str, result=bool)
    def prepareRecovery(self, run_id):
        def run():
            if self.busy:
                raise ValueError('请先结束当前操作。')
            self.loadRunHistory()
            summary = next((r for r in self.history_rows if r['id'] == run_id), None)
            if not summary:
                raise ValueError('摘要已变化，请重新打开运行摘要。')
            wanted = {(t['courseId'], t['clazzId']) for t in summary['tasks'] if all(k in t for k in ('courseId', 'clazzId'))}
            if not wanted:
                raise ValueError('该摘要没有可定位的未完成课程；旧摘要仅供回看，请手动重新读课。')
            account = self.account_store.active if self.account_store else ''
            uid = self.account_store.get(account)['uid'] if account else ''
            if not self.demo_mode and (not account or not uid):
                raise ValueError('请先连接原账号并核实身份，再读取课程。')
            self._recovery = dict(account=account, uid=uid, wanted=wanted, demo=self.demo_mode)
            try:
                self.begin('courses', self.demo_mode)
            except Exception:
                self._recovery = None
                raise
            self.pageRequested.emit(0)
        return self.execute(run, 'recovery')

    @Property('QVariantMap', notify=changed)
    def serviceHealth(self):
        return {key: self.service_health(key) for key in (*self.provider_order, 'ai')}

    @Property(QObject, constant=True)
    def attentionModel(self):
        return self._attention_filter

    @Slot(str, str)
    def filterAttention(self, query, bucket):
        self._attention_filter.set_filter(query, bucket)

    @Property("QVariantMap", notify=inputChanged)
    def input(self):
        return self.interaction

    @Property("QVariantList", notify=providersChanged)
    def providers(self):
        enabled = self.enabled_providers()
        return [dict(key=k, title=PROVIDER_SPECS[k].title, enabled=k in enabled,
                     secret=PROVIDER_SPECS[k].secret,
                     configured=bool(self.config['question_bank'].get(PROVIDER_SPECS[k].secret, '')),
                     health=self.service_health(k)) for k in self.provider_order]

    def service_fingerprint(self, key):
        import hashlib
        sections = ('ai',) if key == 'ai' else ('question_bank',)
        return hashlib.sha256(repr([(s, sorted(self.config[s].items())) for s in sections]).encode()).hexdigest()

    def service_health(self, key):
        check = self.service_checks.get(key)
        if not check:
            return '尚未测试'
        return (check['time'] + ' · ' + check['message'] if check['fingerprint'] == self.service_fingerprint(key)
                else '配置已变化 · 上次测试已过期')

    @Property(str, notify=configChanged)
    def aiDestination(self):
        from urllib.parse import urlsplit
        try:
            host = urlsplit(self.config['ai']['endpoint']).hostname or '未配置服务地址'
        except ValueError:
            host = '服务地址格式需检查'
        return self.redact(self.config['ai']['provider'] + ' · ' + host)

    def remember_check(self, key, message):
        self.service_checks[key] = dict(time=datetime.now().strftime('%m-%d %H:%M:%S'),
                                       fingerprint=self.service_fingerprint(key), message=self.redact(message))
        self.providersChanged.emit()

    @Property(str, notify=changed)
    def strategySummary(self):
        c = defaults() if self.demo_mode else self.config
        on = lambda s, k: c[s][k].lower() == 'true'
        row = self.account_store.get(self.account_store.active) if self.account_store else None
        banks = [PROVIDER_SPECS[k].title for k in c['question_bank']['providers'].split(',') if k in PROVIDER_SPECS]
        lines = [f"账号：{'离线示例' if self.demo_mode else row['label'] if row else '未选择'}",
                 f"课程：已选 {len(self.selected)} 门（搜索隐藏 {self.summary['hiddenCount']} 门）",
                 f"章节并发 {c['common']['jobs']} · 倍速 {c['common']['speed']} · 重做 {'开启，最多 ' + c['common']['work_max_retries'] + ' 次' if on('common', 'work_redo_enabled') else '关闭'}",
                 '完成缓存：' + ('忽略缓存，重新检查所选任务' if on('common', 'ignore_completion_cache') else '跳过缓存已完成任务'),
                 '题库顺序：' + (' → '.join(banks) or '关闭'),
                 f"AI {'开启' if on('ai', 'enabled') else '关闭'} · 图片识别 {'开启' if on('ai', 'vision') else '关闭'} · 人工 {'开启' if on('desktop', 'manual_fallback') else '关闭'} · 随机 {'开启' if on('desktop', 'random_fallback') else '关闭'}",
                 'AI 发送目标：' + (self.aiDestination if not self.demo_mode and on('ai', 'enabled') else '不发送'),
                 '测验：' + ('完整作答后自动提交' if on('tiku', 'submit') else '仅保存'),
                 '讨论：' + ('自动发布' if on('discussion', 'auto_submit') else '仅本地草稿') if on('discussion', 'enabled') else '讨论：关闭',
                 '投票／问卷：' + ('自动提交' if on('activity', 'auto_submit') else '仅本地草稿') if on('activity', 'enabled') else '投票／问卷：关闭',
                 '结束通知：' + (c['notification']['provider'] or '关闭'),
                 '离线演示：零平台写入、零服务请求' if self.demo_mode else '外部请求：平台读取与学习上报；启用的题库／AI 接收题干、选项及可能的图片链接；通知发送运行计数。']
        return self.redact('\n'.join(lines))

    @Property(str, constant=True)
    def dataExplanation(self):
        return ('当前数据 profile：' + str(data_dir()) + '\n\n'
                'settings.ini：策略与受 DPAPI 保护的服务凭据；登录密码不持久化。\n'
                'accounts.json 与 accounts/：账号索引与受保护的会话。DPAPI 绑定 Windows 用户和设备，复制文件不保证跨机解密。\n'
                'discussions/ 与 activities/：草稿、正文／答案及发布／提交意图。内容未使用凭据加密封装；未决意图用于防重复，不能当缓存删除。\n'
                'run_summaries/：按账号隔离的最近 20 轮摘要，每账号最多 2 MiB，每轮最多 1000 个未完成章节标题。包含课程名，不含题干或答案；每 2 秒最多保存一次。摘要不能作为提交证据，也不会自动重放请求。\n'
                'course_progress/：按账号与平台用户隔离的课程进度统计，每份最多 2000 门课程 / 1 MiB，只含课程标识、章节计数和更新时间。可安全删除，读课时后台只读更新。\n'
                'task_completion/：已核验完成的章节任务缓存，按账号、用户、课程和班级隔离，每门课程最多 10000 项 / 4 MiB，保存章节 ID、标题和确认时间。下次启动直接跳过对应任务的处理与逐章核验，可安全删除，不作为讨论／活动提交凭据。\n'
                'draft_copies/：按账号隔离的人工编辑副本，最多 20 项 / 2 MiB，含正文和原题，未加密。与生产草稿／意图独立，不能作为提交证据。\n'
                'ai_profiles.json：最多 20 个命名 AI 配置；配置与独立凭据用 DPAPI 加密，不会自动切换服务。完整 INI 不包含这些档案。\n'
                'logs/（或自选目录）：已脱敏日志，单文件约 10 MiB、总量 100 MiB、保留 14 天。课程与题目内容未自动匿名。日志导出仅含当前保留快照。\n\n'
                '备份清单：关闭应用后保留整个 profile，包括 settings、accounts、discussions、activities、run_summaries、draft_copies、ai_profiles.json。INI 不是完整备份。恢复前保留原件；跨机凭据需重新配置。\n'
                '排障配置仅含允许的非敏感策略和版本，不含地址、账号、密钥、正文或未知扩展字段。完整 INI 含明文凭据，仅用于可信的个人备份。')

    def notify(self, message, tone="info"):
        self.message, self.tone = self.redact(message), tone
        self.changed.emit()

    @Property('QVariantMap', notify=changed)
    def uiFeedback(self):
        return {key: dict(value) for key, value in self._ui_feedback.items()}

    @Slot(str)
    def clearUiFeedback(self, scope):
        if self._ui_feedback.pop(scope, None) is not None:
            self.changed.emit()

    def feedback(self, scope, message, tone='info'):
        if scope not in ('profiles', 'draft', 'savedDrafts', 'recovery', 'release', 'record'):
            raise ValueError('Unknown UI feedback scope')
        self._ui_feedback[scope] = dict(message=self.redact(message)[:6000], tone=tone)
        self.notify(message, tone)

    def sync_providers(self):
        enabled = self.enabled_providers()
        self.provider_order = enabled + [k for _, k in BANKS if k not in enabled]

    def enabled_providers(self):
        return [s for s in self.config["question_bank"]["providers"].split(",") if s]

    @Property("QVariantList", notify=changed)
    def accountRows(self):
        return [dict(row) for row in self._account_rows_cache]

    def refresh_account_status(self):
        # data_dir() creates/checks the profile directory. Keep this filesystem
        # work out of the thousands of QML summary property reads per refresh.
        self._log_directory = str(LogStore.resolve(self.config['logging']['directory']))
        if not self.account_store:
            self._account_rows_cache, self._cookie_exists = [], False
            return
        self._account_rows_cache = []
        self._cookie_exists = False
        for row in self.account_store.rows:
            exists = self.account_store.cookie_path(row['id']).exists()
            self._account_rows_cache.append(dict(id=row['id'], label=row['label'], username=row['username'],
                display=row['label'] + (' · Cookie 已保存' if exists else ' · 需登录')))
            if row['id'] == self.account_store.active:
                self._cookie_exists = exists

    def cookie_path(self):
        if not self.account_store or not self.account_store.active:
            raise ValueError(self.account_error or '请先添加或选择账号。')
        return self.account_store.cookie_path(self.account_store.active)

    def reset_account_view(self):
        self.cancel_course_progress()
        self._course_progress.clear()
        self._record_cards.clear()
        self.verification_progress = {}
        self._recovery = None
        self._draft = None
        self._draft_account = ''
        self._attention_model.clear()
        self._attention_filter.set_filter('', '')
        self.history_rows = []
        self.history_error = ''
        self._approved_policy = None
        self._start_confirmation = None
        self.ai_usage = dict(requests=0, limit=0, known=0, unknown=0, tokens=0)
        self._discussion_model.clear()
        self._activity_model.clear()
        self.courses, self.selected, self.course_identity = [], set(), None
        self.search, self.demo_mode = '', False
        self.operation_account = ''
        self._task_model.reset([])
        self._question_model.clear()
        self.reset_question_filters()
        self.clear_input()
        self.mode = '本地工作台'
        self.coursesChanged.emit()
        self.configChanged.emit()
        self.changed.emit()

    def require_account_store(self):
        if not self.account_store:
            raise ValueError(self.account_error or '账号存储不可用。')
        return self.account_store

    def ensure_account(self):
        store = self.require_account_store()
        if not store.active:
            username = self.config['common']['username'].strip()
            row = store.add(username)
            self.config['common']['username'] = row['username']
            self.configChanged.emit()
            self.changed.emit()
        return store.get(store.active)

    @Slot()
    def browserLogin(self):
        if self.busy:
            return
        self.cancel_course_progress()
        def run():
            self.require_account_store()
            account_id = self.account_store.active
            login = BrowserLogin(self)
            self._browser_sessions.append(login)
            self._browser_login = login
            login.released.connect(lambda: self._release_browser(login))
            login.finished.connect(lambda reason: self._browser_finished(login, reason))
            login.succeeded.connect(lambda cookies: self._browser_cookies(account_id, cookies))
            try:
                login.start()
            except Exception:
                self._browser_finished(login, '')
                login.shutdown()
                self._release_browser(login)
                raise
            self.busyChanged.emit()
            self.changed.emit()
            self.notify('请在弹出的浏览器中完成登录（可扫码）。进入个人空间后将自动获取 Cookie 并读取课程；可点击「取消浏览器登录」。')
        self.execute(run)

    def _release_browser(self, login):
        if login in self._browser_sessions:
            self._browser_sessions.remove(login)
            login.deleteLater()

    def _browser_finished(self, login, reason):
        if self._browser_login is not login:
            return
        self._browser_login = None
        self.busyChanged.emit()
        self.changed.emit()
        if reason:
            self.notify(reason, 'warning')

    def _browser_cookies(self, account_id, cookies):
        def run():
            if self._closed or self.busy or self.account_store.active != account_id:
                raise ValueError('当前账号已变化，请重新进行浏览器登录。')
            # Check identity before writing so another user's login cannot replace
            # a saved session. The worker still verifies the session remotely.
            row = self.account_store.get(account_id)
            uid = cookie_uid(cookies)
            if row and row['uid'] and row['uid'] != uid:
                raise ValueError('浏览器登录的用户与当前账号不一致。请点击「添加账号」后重新登录。')
            row = row or self.ensure_account()
            write_cookie(self.cookie_path(), cookies, row['uid'])
            self.config['common'].update(password='', use_cookies='true')
            self.reset_account_view()
            self.refresh_account_status()
            self.configChanged.emit()
            self.notify('已获取并加密保存 Cookie，正在验证登录并读取课程…')
            self.fetchCourses()
        self.execute(run)

    @Slot()
    def cancelBrowserLogin(self):
        if self._browser_login:
            self._browser_login.cancel()

    @Slot()
    def addAccount(self):
        if self.busy:
            return
        def run():
            store = self.require_account_store()
            store.commit(store.rows, '')
            self.config['common'].update(username='', password='', use_cookies='false')
            self.reset_account_view()
            self.notify('填写新账号并登录，或导入该账号的 Cookie。原有账号会话已保留。')
        self.execute(run)

    @Slot(str)
    def switchAccount(self, account_id):
        if self.busy:
            self.notify('请先停止当前任务，再切换账号。', 'warning')
            return
        def run():
            store = self.require_account_store()
            row = store.get(account_id)
            if not row:
                raise ValueError('账号不存在。')
            if store.active == account_id:
                return
            store.commit(store.rows, account_id)
            self.config['common'].update(username=row['username'], password='',
                                         use_cookies='true' if self.cookie_path().exists() else 'false')
            self.reset_account_view()
            self.notify('已切换到 ' + row['label'] + '，选择登录方式进入该账号课程。')
        self.execute(run)

    @Slot(str)
    def renameAccount(self, label):
        if self.busy:
            return
        def run():
            store = self.require_account_store()
            if not label.strip():
                raise ValueError('账号备注不能为空。')
            store.update(store.active, label=label.strip()[:80])
            self.refresh_account_status()
            self.changed.emit()
        self.execute(run)

    @Slot()
    def removeAccount(self):
        if self.busy:
            return
        def run():
            store = self.require_account_store()
            store.remove(store.active)
            self.config['common'].update(username='', password='', use_cookies='false')
            self.reset_account_view()
            self.notify('已删除当前账号及其本机 Cookie。其他账号会话已保留。')
        self.execute(run)

    @Slot(str, str, str)
    def setValue(self, section, key, value):
        if self.busy or not self.config.has_option(section, key):
            return
        if self.config[section][key] == value:
            return
        if section == 'common' and key == 'username' and self.account_store and self.account_store.active:
            self.notify('修改登录账号请点击「添加账号」，或切换到已有账号。', 'warning')
            return
        self.config[section][key] = value
        self._closed = False
        if section == "ai":
            self.ai_status = "参数已修改 · 请重新测试"
            if key == "provider" and value == "SiliconFlow":
                self.config["ai"]["endpoint"] = "https://api.siliconflow.cn/v1"
                self.config["ai"]["model"] = "deepseek-ai/DeepSeek-R1"
        self.save_state = "有修改 · 未保存"
        self.configChanged.emit()
        self.changed.emit()

    @Slot(str, bool)
    def toggleProvider(self, key, checked):
        if self.busy or key not in self.provider_order:
            return
        enabled = set(self.enabled_providers())
        enabled.add(key) if checked else enabled.discard(key)
        self.setValue("question_bank", "providers", ",".join(k for k in self.provider_order if k in enabled))
        self.providersChanged.emit()

    @Slot(int, int)
    def moveProvider(self, index, delta):
        target = index + delta
        if self.busy or not (0 <= index < len(self.provider_order) and 0 <= target < len(self.provider_order)):
            return
        self.provider_order[index], self.provider_order[target] = self.provider_order[target], self.provider_order[index]
        enabled = self.enabled_providers()
        self.setValue("question_bank", "providers", ",".join(k for k in self.provider_order if k in enabled))
        self.changed.emit()
        self.providersChanged.emit()

    @Slot(str)
    def searchCourses(self, value):
        self.search = value
        self.coursesChanged.emit()
        self.changed.emit()

    @Slot(str, str, bool)
    def selectCourse(self, course, clazz, checked):
        key = (course, clazz)
        if self.busy or key not in {course_key(c) for c in self.courses}:
            return
        self.selected.add(key) if checked else self.selected.discard(key)
        self.coursesChanged.emit()
        self.changed.emit()

    @Slot(bool)
    def selectVisible(self, checked):
        if self.busy:
            return
        keys = {course_key(c) for c in self.courses if self.course_visible(c)}
        self.selected.update(keys) if checked else self.selected.difference_update(keys)
        self.coursesChanged.emit()
        self.changed.emit()

    def identity(self):
        c = self.config["common"]
        cookie = self.cookie_path() if self.account_store and self.account_store.active else None
        return (self.account_store.active if self.account_store else '', c["username"], c["password"],
                c["use_cookies"], cookie.stat().st_mtime_ns if cookie and cookie.exists() else None)

    def begin(self, action, demo=False, selected=None, topic=''):
        if self.busy:
            raise ValueError("请先停止当前任务。")
        config = defaults() if demo else self.config
        if action == "courses" and not demo:
            account = defaults()
            for key in ('username', 'password', 'use_cookies'):
                account['common'][key] = self.config['common'][key]
            cookie = self.cookie_path() if self.account_store and self.account_store.active else data_dir() / 'no-account-cookie'
            validate(account, login=True, cookie_path=cookie)
            self.ensure_account()
            account['common']['username'] = self.config['common']['username']
            config = account
        else:
            validate(config)
        if action == "run" and not demo:
            if self.identity() != self.course_identity:
                raise ValueError("登录信息已改变，请重新读取课程（包含本机会话文件变化）。")
            validate(config, login=True, cookie_path=self.cookie_path())
        if action == "check_bank":
            target = selected[0]
            self.check_target = target
            isolated = parse_text(serialize(config))
            isolated["question_bank"]["providers"] = target
            isolated["ai"]["enabled"] = "false"
            isolated["notification"]["provider"] = ""
            validate(isolated, services=True)
            config = isolated
        if action == "check_ai":
            ai_only = parse_text(serialize(config))
            ai_only["ai"]["enabled"] = "true"
            ai_only["question_bank"]["providers"] = ""
            ai_only["notification"]["provider"] = ""
            validate(ai_only, services=True)
        if action == 'discussion_preview':
            isolated = parse_text(serialize(config))
            isolated['question_bank']['providers'] = ''
            isolated['notification']['provider'] = ''
            if isolated['ai']['enabled'] != 'true':
                raise ValueError('请先启用并配置 AI 服务，或选择固定回复好。')
            validate(isolated, services=True)
            config = isolated
        if action == 'activity_preview':
            isolated = parse_text(serialize(config))
            isolated['question_bank']['providers'] = ''
            isolated['notification']['provider'] = ''
            if isolated['ai']['enabled'] != 'true':
                raise ValueError('请先启用并配置 AI，或选择随机选择与固定好。')
            validate(isolated, services=True)
            config = isolated
        self.cancel_course_progress()
        self.operation, self.outcome = action, False
        self.ai_usage = dict(requests=0, limit=int(config['ai']['request_limit']), known=0, unknown=0, tokens=0)
        self._ai_token_warning = int(config['ai']['token_warning'])
        self.operation_account = self.account_store.active if self.account_store and not demo and action in ('courses', 'run') else ''
        payload = to_payload(config)
        self.log_redactor = Redactor(payload)
        if action == "courses":
            self.courses, self.selected = [], set()
            self.demo_mode = demo
            self.coursesChanged.emit()
        if action == "run":
            self._run_completed = False
            self._attention_model.clear()
            self._task_model.reset([])
            self._question_model.clear()
            self._discussion_model.clear()
            self._activity_model.clear()
            self.reset_question_filters()
            self.pageRequested.emit(4)
        self.mode = "离线演示 · 示例数据" if demo else "检查 AI 连接" if action == "check_ai" else "正在连接超星"
        self.notify("正在读取课程…" if action == "courses" else "正在检查 AI 连接…" if action == "check_ai" else "正在准备任务…")
        command = dict(action=action, demo=demo, config=payload, selected=selected or [], data_dir=str(data_dir()))
        if action == 'discussion_preview':
            command['topic'] = topic
            self.mode = '讨论预览 · 请求已配置 AI 服务'
            self.notify('正在生成讨论预览，将请求已配置 AI 服务，不访问课程或发布接口。')
        if action == 'activity_preview':
            command['topic'] = topic
            self.mode = '投票／问卷预览 · 请求已配置 AI 服务'
            self.notify('正在生成活动示例预览，不读取课程、不提交、不写入活动记录。')
        if self.operation_account:
            command.update(account_id=self.operation_account,
                           account_uid=self.account_store.get(self.operation_account)['uid'])
        self.controller.start(command)
        if action == 'run':
            self._history_account = 'offline-demo' if demo else self.operation_account
            self._history_run = self.controller.run_id
            self._history_time = datetime.now().astimezone().isoformat(timespec='seconds')
            self.history_error = ''
            self.save_run_summary('运行中 / 若已退出则为中断快照')

    @Slot()
    def loadDemo(self):
        self.execute(lambda: self.begin("courses", True))

    @Slot()
    def fetchCourses(self):
        self.execute(lambda: self.begin("courses"))

    @Slot()
    def startTasks(self):
        def start():
            if not self.selected:
                raise ValueError("请至少选择一门课程。")
            validate(defaults() if self.demo_mode else self.config)
            policy = serialize(self.config)
            writing = not self.demo_mode and (self.config['tiku']['submit'].lower() == 'true' or any(
                self.config[s]['enabled'].lower() == 'true' and self.config[s]['auto_submit'].lower() == 'true'
                for s in ('discussion', 'activity')))
            if writing and self._approved_policy != policy:
                self._start_confirmation = (policy, self.identity(), tuple(sorted(self.selected)))
                self.startConfirmationRequested.emit(self.strategySummary)
                return
            self.begin("run", self.demo_mode, [list(k) for k in sorted(self.selected)])
        self.execute(start)

    @Slot()
    def confirmStartTasks(self):
        pending, self._start_confirmation = self._start_confirmation, None
        if not pending or pending != (serialize(self.config), self.identity(), tuple(sorted(self.selected))):
            self.notify('账号、课程或策略已变化，请重新查看摘要后开始。', 'warning')
            return
        self._approved_policy = pending[0]
        self.startTasks()

    @Slot()
    def cancelStartTasks(self):
        self._start_confirmation = None

    @Slot()
    def verifyPending(self):
        self._verify(False)

    @Slot()
    def verifyRecords(self):
        self._verify(True)

    @Slot()
    def verifyAllRecords(self):
        self._verify(True, all_records=True)

    def _verify(self, records, all_records=False):
        def run():
            if self.busy:
                raise ValueError('请先结束当前操作。')
            if self.demo_mode:
                self.notify('离线演示不读取平台；只读核验须先读取真实课程。')
                return
            if self.identity() != self.course_identity:
                raise ValueError('账号或会话已变化，请重新读取课程。')
            related = {r['task_id'] for model in (self._discussion_model, self._activity_model) for r in model.rows}
            targets = [{k: t[k] for k in ('id', 'courseId', 'clazzId', 'pointId')}
                       for t in self.tasks if (all_records or t['status'] != 'success' or records and t['id'] in related)
                       and all(k in t for k in ('courseId', 'clazzId', 'pointId'))]
            if not targets:
                raise ValueError('当前没有可定位的待核验章节，请先读取并运行课程。')
            if records and not all_records:
                for target in targets:
                    if target['id'] in self._record_cards:
                        target['record_cards'] = self._record_cards[target['id']]
            config = defaults()
            config['common'].update(username=self.config['common']['username'], password='', use_cookies='true')
            validate(config, login=True, cookie_path=self.cookie_path())
            self.cancel_course_progress()
            self.operation, self.outcome = 'verify', False
            self.verification_progress = {}
            self.operation_account = self.account_store.active
            payload = to_payload(config)
            self.log_redactor = Redactor(payload)
            self.mode = '本人记录全量发现' if all_records else '本人记录待处理核验' if records else '只读章节核验'
            self.controller.start(dict(action='verify', config=payload, demo=False, targets=targets,
                                       verify_records=records,
                                       data_dir=str(data_dir()), account_id=self.operation_account,
                                       cookie_mtime_ns=self.cookie_path().stat().st_mtime_ns,
                                       account_uid=self.account_store.get(self.operation_account)['uid']))
            self.notify(('全量发现所有可定位章节，卡数未知时每章最多读取 7 卡。' if all_records else
                         '范围：待处理章节及本轮已有讨论／活动的关联章节。' if records else '范围：待核验章节。') + '只读查询已启动，未决意图保留。')
        self.execute(run)

    @Slot(bool, str)
    def filterQuestions(self, pending_only, task):
        self._question_filter.set_filter(pending_only, task)

    def reset_question_filters(self):
        self._question_filter.set_filter(False, '')
        self._question_revision += 1
        self.questionsReset.emit()

    @Slot(str, result='QVariantMap')
    def questionDetail(self, record_id):
        row = self._question_model.row_index(record_id)
        return self._question_model.data(self._question_model.index(row)) if row is not None else {}

    @Slot(str, str)
    def filterDiscussions(self, query, stage):
        self._discussion_filter.set_filter(query, stage)

    @Slot(str, str)
    def filterActivities(self, query, stage):
        self._activity_filter.set_filter(query, stage)

    @Slot(str, result='QVariantMap')
    def activityDetail(self, record_id):
        return self._activity_model.detail(record_id)

    @Slot(str)
    def copyActivity(self, record_id):
        from PySide6.QtGui import QGuiApplication
        record = self._activity_model.detail(record_id)
        if record:
            self.copy_text(record['detailText'])
            self.feedback('record', '活动逐题草稿已复制。', 'success')

    @Slot(str)
    def previewActivity(self, topic):
        def run():
            if self.busy:
                raise ValueError('请先结束当前任务，再预览。')
            if not topic.strip() or len(topic) > 2000:
                raise ValueError('请填写不超过 2000 字符的示例文本题干。')
            validate(self.config)
            cfg = self.config['activity']
            if cfg['choice_strategy'] == 'random' and cfg['text_strategy'] == 'fixed_good':
                self.ai_usage = dict(requests=0, limit=0, known=0, unknown=0, tokens=0)
                from types import SimpleNamespace
                from .activity import ActivityGenerator, preview_context
                from .answers import AnswerRouter
                ctx = preview_context(topic.strip())
                answers = ActivityGenerator(AnswerRouter(to_payload(self.config), SimpleNamespace(control=None))).generate(ctx, cfg)
                record = dict(title=ctx.title, course='', chapter='', submit_status='draft', task_status='pending',
                              reason='完全本地 · AI 调用 0 次 · 不读取课程或提交', questions=ctx.questions, answers=answers)
                self.activity_preview = dict(content=detail_text(record), reason=record['reason'])
                self.changed.emit()
                return
            self.begin('activity_preview', topic=topic.strip())
        self.execute(run)

    @Slot(str, result='QVariantMap')
    def discussionDetail(self, record_id):
        return self._discussion_model.detail(record_id)

    @Slot(str)
    def previewDiscussion(self, topic):
        def run():
            if self.busy:
                raise ValueError('请先结束当前任务，再生成预览。')
            if not topic.strip() or len(topic) > 12000:
                raise ValueError('请填写不超过 12000 字符的示例讨论主题。')
            if self.config['discussion']['reply_level'] == 'fixed_good':
                self.ai_usage = dict(requests=0, limit=0, known=0, unknown=0, tokens=0)
                self.discussion_preview = dict(content='好', level='fixed_good', reason='本地固定回复 · 模型调用 0 次 · 不发布')
                self.changed.emit()
                return
            self.begin('discussion_preview', topic=topic.strip())
        self.execute(run)

    @Slot(str)
    def copyDiscussion(self, record_id):
        from PySide6.QtGui import QGuiApplication
        record = self._discussion_model.detail(record_id)
        if record.get('content'):
            self.copy_text(record['content'])
            self.feedback('record', '讨论正文已复制。', 'success')

    @Slot()
    def pause(self):
        self.controller.pause()
        self.notify('暂停后续调度与答题写入；在途请求和当前媒体可能继续。' if self.controller.paused else '已请求恢复，后续任务将复核会话。')

    @Slot()
    def offlineCheck(self):
        if self.busy:
            return
        def run():
            from .diagnostics import offline_check
            self.bank_status = offline_check()
            self.notify(self.bank_status, 'success')
        self.execute(run)

    @Slot(str)
    def checkBank(self, provider):
        if provider in PROVIDER_SPECS:
            self.execute(lambda: self.begin('check_bank', selected=[provider]))

    @Slot(str)
    def definitionAction(self, action):
        if self.busy:
            return
        def run():
            from .declarative import TEMPLATE, validate_definition, preview_definition
            import json
            if action == 'import':
                name, _ = self.file_dialogs.getOpenFileName(None, '导入声明式 JSON（不执行 handler）', '', 'JSON (*.json)')
                if not name:
                    return
                with Path(name).open('rb') as source:
                    raw = source.read(65540)
                if len(raw) > 65539:
                    raise ValueError('题库定义超过 64 KiB')
                definition, preview = preview_definition(raw.decode('utf-8-sig'))
                self._definition_import = (json.dumps(definition, ensure_ascii=False),
                                           self.config['question_bank']['declarative_definition']) if definition else None
                self.definitionPreviewRequested.emit(self.redact(preview), definition is not None)
            else:
                text = self.config['question_bank']['declarative_definition']
                definition = validate_definition(text) if text.strip() else TEMPLATE
                name, _ = self.file_dialogs.getSaveFileName(None, '导出不含 Token 的 JSON 模板', 'question-bank.json', 'JSON (*.json)')
                if name:
                    Path(name).write_text(json.dumps(definition, ensure_ascii=False, indent=2), encoding='utf-8')
        self.execute(run)

    @Slot(bool)
    def finishDefinitionImport(self, accepted):
        pending, self._definition_import = self._definition_import, None
        if not accepted or not pending:
            return
        if self.busy or self.config['question_bank']['declarative_definition'] != pending[1]:
            self.notify('当前定义或运行状态已变化，请重新导入预览。', 'warning')
            return
        self.setValue('question_bank', 'declarative_definition', pending[0])
        self.notify('已导入受限定义，Token 需单独填写并保存。', 'success')

    @Slot()
    def checkAi(self):
        self.execute(lambda: self.begin("check_ai"))

    def execute(self, command, scope=''):
        if scope:
            self.clearUiFeedback(scope)
        try:
            command()
            return True
        except Exception as exc:
            if scope:
                self.feedback(scope, exc, 'danger')
            else:
                self.notify(exc, "danger")
            if hasattr(exc, 'section'):
                self.locate_invalid_field(exc)
            return False

    @Slot()
    def stop(self):
        self.cancelBrowserLogin()
        if not self.controller.active:
            return
        self.clear_input()
        self.controller.stop()
        self.notify("正在停止后台任务…", "warning")

    def append_log(self, message, level="INFO"):
        line = f"{datetime.now():%H:%M:%S}  {str(level)[:10]:7}  {self.redact(self.log_redactor(str(message)))}"
        if len(line) > LogModel.MAX_LINE_CHARS:
            line = line[:LogModel.MAX_LINE_CHARS - 20] + " …[日志过长，已截断]"
        self._log_model.append(line)
        if not self._progress_timer.isActive():
            self._progress_timer.start()
        try:
            self.log_store.append(self.config['logging']['directory'], line)
        except (OSError, ValueError) as exc:
            self.on_log_failure(str(exc))

    @Slot(str)
    def on_log_failure(self, message):
        error = self.redact('日志保存失败：' + message)
        if error != self.log_save_error:
            self.log_save_error = error
            self.notify(error, 'danger')

    @Slot()
    def on_log_written(self):
        if self.log_save_error:
            self.log_save_error = ''
        if not self._progress_timer.isActive():
            self._progress_timer.start()

    def on_event(self, event):
        if event.get('run_id', self.controller.run_id) != self.controller.run_id:
            return
        kind = event.get("type")
        if kind == 'record_card_hint':
            if self.operation == 'run' and len(self._record_cards) < 10000:
                self._record_cards[event['id']] = sorted(set(event['indices']))
            return
        if kind == 'verification_failed':
            if (self.operation == 'verify' and self.operation_account and self.account_store
                    and self.account_store.active == self.operation_account):
                index = self._task_model.indices.get(event['id'])
                if index is not None:
                    task = self.tasks[index]
                    task.update(checked_at=event['checked_at'], evidence=event['reason'])
                    self._attention_model.upsert('核验失败:' + task['id'], dict(id=task['id'], task=task['id'], kind='核验',
                        title=task['title'], course=task['course'], status='本次读取失败', bucket='failed',
                        reason=event['reason'], next='旧状态保留，不代表本次核验成功；稍后重试。'))
                    if not self._progress_timer.isActive():
                        self._progress_timer.start()
            return
        if kind == 'verification_progress':
            if (self.operation == 'verify' and self.operation_account and self.account_store
                    and self.account_store.active == self.operation_account):
                self.verification_progress = {k: event[k] for k in ('phase', 'completed', 'total', 'reads', 'failures', 'elapsed_ms')}
                if not self.controller.stopping:
                    self.message = event['message']
                if not self._progress_timer.isActive():
                    self._progress_timer.start()
            return
        if kind == 'ai_usage':
            self.ai_usage = {k: event[k] for k in ('requests', 'limit', 'known', 'unknown', 'tokens')}
            if not self._progress_timer.isActive():
                self._progress_timer.start()
            return
        if kind == 'verification':
            if (self.operation != 'verify' or not self.operation_account or not self.account_store
                    or self.account_store.active != self.operation_account):
                return
            row = self._task_model.indices.get(event['id'])
            if row is not None:
                task = self.tasks[row]
                self._attention_model.upsert('核验失败:' + task['id'], None)
                task.update(checked_at=event['checked_at'], evidence=event['reason'])
                if event['confirmed']:
                    self._task_model.update_status(event['id'], 'success')
                elif task['status'] == 'success':
                    self._task_model.update_status(event['id'], 'pending')
                self._attention_model.upsert('章节:' + task['id'], chapter_issue(task))
                if not self._progress_timer.isActive():
                    self._progress_timer.start()
            return
        if kind == 'verification_done':
            if (self.operation != 'verify' or not self.operation_account or not self.account_store
                    or self.account_store.active != self.operation_account):
                return
            self.outcome = True
            self.mode = '只读核验已结束'
            self._progress_timer.stop()
            self.notify(event['message'], 'info')
            return
        if kind == 'record_verification':
            if (self.operation != 'verify' or not self.operation_account or not self.account_store
                    or self.account_store.active != self.operation_account):
                return
            label = '讨论' if event['kind'] == 'discussion' else '投票／问卷'
            model = self._discussion_model if event['kind'] == 'discussion' else self._activity_model
            index = model.indices.get(event['id'])
            detail = model.rows[index] if index is not None else {}
            task_index = self._task_model.indices.get(event['task_id'])
            if task_index is None:
                return
            task = self.tasks[task_index]
            # Evidence is a separate view, never overwrite drafts or intent states.
            self._attention_model.upsert('核验:' + label + ':' + event['id'],
                dict(id=event['id'], task=event['task_id'], kind=label, title=detail.get('title', task['title']),
                     course=task['course'], status={'existing': '本人已参与', 'absent': '本次未发现', 'unknown': '证据不足'}[event['participation']],
                     bucket='platform', reason=event['checked_at'] + '\n' + event['reason'] + '\n任务点：' +
                     ('已确认' if event['task_status'] == 'confirmed' else '待确认'),
                     next='在平台检查当前本人记录。历史与本地未决意图保持，不提供强制重发。'))
            if not self._progress_timer.isActive():
                self._progress_timer.start()
            return
        if kind in ('activity_result', 'activity_preview'):
            if event['record']['run_id'] != self.controller.run_id:
                return
            record = safe_activity(event['record'], lambda text: self.redact(self.log_redactor(text)))
            if kind == 'activity_preview':
                self.outcome = True
                self.activity_preview = dict(content=detail_text(record), reason=record['reason'])
                self.mode = '投票／问卷预览已完成'
                self.notify('预览已完成，请逐题检查来源与校验结果。', 'success')
            elif self._activity_model.upsert(record):
                self._attention_model.upsert('投票／问卷:' + record['id'], record_issue('投票／问卷', record))
                if not self._progress_timer.isActive():
                    self._progress_timer.start()
            return
        if kind == 'discussion_result':
            if event['record']['run_id'] != self.controller.run_id:
                return
            record = safe_discussion(event['record'], lambda text: self.redact(self.log_redactor(text)))
            if self._discussion_model.upsert(record):
                self._attention_model.upsert('讨论:' + record['id'], record_issue('讨论', record))
                if not self._progress_timer.isActive():
                    self._progress_timer.start()
            return
        if kind == 'discussion_preview':
            self.outcome = True
            self.discussion_preview = dict(content=event['content'], level=event['level'],
                                           reason='已配置 AI 服务生成 · 仅预览，不会发布')
            self.mode = '讨论预览已完成'
            self.notify('讨论预览已生成，请核对内容。', 'success')
            return
        if kind == 'session_ready':
            if (not self.account_store or event.get('account_id') != self.operation_account
                    or self.operation_account != self.account_store.active):
                return
            def remember():
                uid = str(event['uid'])
                row = self.account_store.get(self.operation_account)
                if not uid or not row or (row['uid'] and row['uid'] != uid):
                    raise ValueError('平台会话与当前账号不一致，请重新读取课程。')
                identity = self.identity()
                self.account_store.update(self.operation_account, uid=uid)
                self.config['common']['use_cookies'] = 'true'
                # A verified worker login rewrites its Cookie file even when the
                # user did not change accounts. Trust only that exact revision;
                # external edits and changed login settings still invalidate courses.
                if (self.operation == 'run' and self.controller.active and not self.demo_mode
                        and self.course_identity is not None
                        and identity[:4] == self.course_identity[:4]
                        and identity[-1] is not None
                        and event.get('cookie_mtime_ns') == identity[-1]):
                    self.course_identity = (*identity[:3], 'true', identity[-1])
                self.configChanged.emit()
                self.changed.emit()
            self.execute(remember)
            return
        if kind == 'question_result':
            # Refresh once per record, rather than scanning every setting for
            # each string in the nested result and its provider attempts.
            if self._redactor_signature != self.secret_signature():
                self.refresh_redactor()
            redactor = self._redactor
            record = safe_record(event['record'], lambda text: redactor(self.log_redactor(text)))
            if self._question_model.upsert(record):
                self._attention_model.upsert('题目:' + record['id'], record_issue('题目', record))
                self._question_revision += 1
                if not self._progress_timer.isActive():
                    self._progress_timer.start()
            return
        if kind == 'control':
            self.controller.paused = event['paused']
            self.changed.emit()
            return
        if kind == 'bank_check':
            self.outcome = True
            self.bank_status = event['message']
            self.remember_check(self.check_target, event['message'])
            self.notify(self.bank_status, 'success' if event.get('ok') else 'warning')
            return
        if kind == "log":
            self.append_log(event.get("message", ""), event.get("level", "INFO"))
            return
        elif kind == "courses":
            self.outcome = True
            self.courses = event["courses"]
            self.demo_mode = event.get("demo", False)
            self.course_identity = self.identity()
            self.coursesLoaded.emit()
            self.load_course_progress()
            self.coursesChanged.emit()
            self.mode = "离线演示 · 示例数据" if self.demo_mode else "课程已读取"
            self.notify(f"已读取 {len(self.courses)} 门{'演示' if self.demo_mode else '真实'}课程。", "success")
            recovery, self._recovery = self._recovery, None
            if recovery:
                account = self.account_store.active if self.account_store else ''
                uid = self.account_store.get(account)['uid'] if account else ''
                if recovery['demo'] == self.demo_mode and (self.demo_mode or (account, uid) == (recovery['account'], recovery['uid'])):
                    live = {course_key(c) for c in self.courses}
                    self.selected = recovery['wanted'] & live
                    missing = len(recovery['wanted'] - live)
                    self.coursesChanged.emit()
                    self.notify(f'已重新读课并选择 {len(self.selected)} 门可定位课程，另有 {missing} 门未找到。请检查策略后手动开始；新轮将读取章节目录，跳过缓存已完成的任务。', 'warning')
                else:
                    self.notify('账号身份已变化，未恢复课程选择。请重新读取原账号课程。', 'warning')
        elif kind in ('course_progress', 'course_progress_loading', 'course_progress_failed'):
            if (not self.demo_mode and self.account_store and
                    self.operation_account == self.account_store.active):
                self.apply_course_progress(event)
            return
        elif kind == "tasks":
            self._attention_model.clear()
            self._task_model.reset([dict(t, status="waiting") for t in event["tasks"]])
            self.notify("正在处理章节。本轮进度不表示课程学分完成比例。")
        elif kind == "task":
            if self._task_model.update_status(event["id"], event["status"]) and not self._progress_timer.isActive():
                self._progress_timer.start()
            row = self._task_model.indices.get(event['id'])
            if row is not None:
                self._attention_model.upsert('章节:' + event['id'], chapter_issue(self.tasks[row]))
            if self.operation == 'run' and self._history_run and not self._history_timer.isActive():
                self._history_timer.start()
            return
        elif kind == "media":
            self._task_model.update_media(event['id'], event)
            return
        elif kind == "ai_check":
            self.outcome = True
            self.ai_status = "服务响应正常 · 图片识别仍需实测"
            self.remember_check('ai', self.ai_status)
            self.notify("AI 连接测试通过。", "success")
        elif kind == "input":
            if self.controller.stopping:
                return
            question = event.get("question", {})
            pairs = [("正确", "正确"), ("错误", "错误")] if question.get("type") == "judgement" else option_pairs(question)
            self.interaction = dict(kind=event.get("kind", "question"), type=question.get("type", "text"),
                                    message=plain_text(event.get("message", "")), title=plain_text(question.get("title", "")),
                                    options=[dict(value=k, label=plain_text(v)) for k, v in pairs],
                                    images=question_images(question), choices=event.get("choices", []),
                                    blankCount=question.get('blank_count') if type(question.get('blank_count')) is int else 0,
                                    random=event.get("random_fallback", False))
            self.inputChanged.emit()
            self.notify("等待人工处理，请检查题目后使用答案或跳过。", "warning")
        elif kind == "done":
            self._run_completed = True
            self.outcome = True
            self.mode = "离线演示 · 示例数据" if event.get("demo") else "任务已结束"
            text = f"{'演示结束' if event.get('demo') else '任务结束'} · 已完成章节 {event['success']}，待完成章节 {event['pending']}，失败章节 {event['failed']}，跳过章节 {event['skipped']}"
            self.append_log(text)
            self.notify(text + ("。待完成 / 未核验章节请在平台确认。" if event['pending'] else ""), "danger" if event['failed'] else "warning" if event['pending'] or event['skipped'] else "success")
        elif kind in ("error", "cancelled"):
            self._recovery = None
            self.outcome = True
            self.mode = "已停止" if kind == "cancelled" else "运行失败"
            if self.operation == "check_ai":
                self.ai_status = "测试未通过 · 请检查参数"
                self.remember_check('ai', event.get('message', self.ai_status))
            elif self.operation == 'check_bank':
                self.remember_check(self.check_target, event.get('message', '测试失败'))
            self.append_log(event.get("message", "后台运行失败"), "ERROR")
            self.notify(event.get("message", "后台运行失败"), "warning" if kind == "cancelled" else "danger")

    def clear_input(self):
        self.interaction = {}
        self.inputChanged.emit()

    @Slot(str, bool)
    def reply(self, answer, cancel):
        if not self.interaction or not self.controller.active or self.controller.stopping:
            return
        self.controller.reply(answer, cancel)
        self.clear_input()

    def on_finished(self, code):
        self.refresh_account_status()
        self._recovery = None
        if self.operation == 'run':
            self._draft_sources_uncertain = not (self._run_completed and code == 0 and not self.controller.stopping)
        self.clear_input()
        if self.operation != 'verify':
            for task in self.tasks:
                if task["status"] not in TERMINAL:
                    self._task_model.update_status(task["id"], "stopped")
                    self._attention_model.upsert('章节:' + task['id'], chapter_issue(task))
        self._progress_timer.stop()
        self._log_model.flush()
        self._history_timer.stop()
        if self.controller.stopping:
            self.mode = "已停止"
            self.message, self.tone = "后台已停止。已发送到平台的请求不会撤销，可重新开始。", 'warning'
        elif not self.outcome:
            self.mode = "后台异常退出"
            self.message, self.tone = f"后台退出（{code}），未收到完成结果。请检查软件目录及日志。", 'danger'
        if self.operation == 'run':
            saved = self.save_run_summary(self.mode)
            error = self.history_error
            self.loadRunHistory()
            if not saved:
                self.history_error = error
            self._history_run = ''
        if code == 0 and self.outcome and self.operation in ('courses', 'verify') and not self.controller.stopping:
            QTimer.singleShot(0, self.start_course_progress)
        self.changed.emit()

    @Slot(result=bool)
    def save(self):
        if self.busy:
            return False
        try:
            self._closed = False
            validate(self.config)
            path = data_dir() / "settings.ini"
            self._settings_revision = write_if_unchanged(path, serialize_settings(self.config).encode('utf-8'), self._settings_revision)
            self.startup_error = None
            self.save_state = "设置已保存"
            self.notify("设置及服务凭据已在本机保存。登录密码仅用于当前会话。", "success")
            return True
        except Exception as exc:
            self.save_state = "保存失败 · 未保存"
            self.notify(exc, "danger")
            self.locate_invalid_field(exc)
            return False

    def locate_invalid_field(self, error=None):
        import math
        if hasattr(error, 'section'):
            for page, groups in SCHEMA.items():
                if any(f['section'] == error.section and f['key'] == error.key for g in groups for f in g['fields']):
                    self.pageRequested.emit(1 if page == 'learning' else 3 if page == 'advanced' else 2)
                    self.fieldRequested.emit(error.section, error.key)
                    return
            if error.section == 'question_bank':
                self.pageRequested.emit(2)
                self.fieldRequested.emit(error.section, error.key)
            return
        for page, groups in SCHEMA.items():
            for group in groups:
                for spec in group['fields']:
                    if spec['kind'] not in ('integer', 'number'):
                        continue
                    try:
                        raw = self.config[spec['section']][spec['key']]
                        value = int(raw) if spec['kind'] == 'integer' else float(raw)
                        if not math.isfinite(value) or not spec['low'] <= value <= spec['high']:
                            raise ValueError()
                    except ValueError:
                        self.pageRequested.emit(1 if page == 'learning' else 3 if page == 'advanced' else 2)
                        self.fieldRequested.emit(spec['section'], spec['key'])
                        return

    @Slot(result=bool)
    def close(self):
        return self._close(False)

    @Slot(result=bool)
    def discardAndClose(self):
        return self._close(True)

    def _prepare_close(self, discard):
        if self._closed:
            return True
        # Validate/save before stopping, so a failed save preserves the live window.
        if not self.startup_error and not discard:
            try:
                validate(self.config)
                path = data_dir() / "settings.ini"
                self._settings_revision = write_if_unchanged(path, serialize_settings(self.config).encode('utf-8'), self._settings_revision)
            except Exception as exc:
                self.save_state = "保存失败 · 未保存"
                self.notify(exc, "danger")
                return False
        return True

    def _close(self, discard):
        if self._closed:
            return True
        if not self._prepare_close(discard):
            return False
        self.stop()
        self.cancel_course_progress()
        if self._course_controller.active:
            if not self._course_controller.process.waitForFinished(1000):
                self._course_controller.process.kill()
                self._course_controller.process.waitForFinished(1000)
        for login in list(self._browser_sessions):
            login.shutdown()
        if self.busy:
            if not self.controller.process.waitForFinished(3000):
                self.controller.process.kill()
                self.controller.process.waitForFinished(1000)
        if not self.log_store.close(2, force=discard) and not discard:
            self.notify('日志尚未写完，可稍后重试或放弃修改并退出。', 'warning')
            return False
        self._closed = True
        return True

    @Slot(str)
    def fileAction(self, action):
        if self.busy and action not in ("export_logs",):
            return
        def run():
            if action in ("import_config", "import_cookie"):
                title = "导入 INI 配置" if action == "import_config" else "导入 Cookie（name=value; 格式）"
                name, _ = self.file_dialogs.getOpenFileName(None, title, "", "配置 / 文本 (*.ini *.txt);;所有文件 (*)")
                if not name:
                    return
                text = Path(name).read_text(encoding="utf-8-sig")
                if action == "import_config":
                    new = parse_text(text)
                    restore_credentials(new)
                    validate(new)
                    store = self.require_account_store()
                    if new['common']['username'] != self.config['common']['username']:
                        store.commit(store.rows, '')
                    self.config = new
                    self.reset_account_view()
                    self.sync_providers()
                    self.providersChanged.emit()
                    self.ai_status = "配置已导入 · 请重新测试"
                    self.save_state = "有修改 · 未保存"
                    self.configChanged.emit()
                else:
                    cookies = parse_cookie(text)
                    row = self.ensure_account()
                    write_cookie(self.cookie_path(), cookies, row['uid'])
                    self.setValue("common", "use_cookies", "true")
                    self.reset_account_view()
                self.notify("已导入，请检查设置。", "success")
            elif action == "clear_cookie":
                self.cookie_path().unlink(missing_ok=True)
                self.setValue('common', 'use_cookies', 'false')
                self.reset_account_view()
                self.notify("当前账号的本机 Cookie 已清除，其他账号会话已保留。")
            elif action == 'export_diagnostics':
                name, _ = self.file_dialogs.getSaveFileName(None, '导出排障配置（不含凭据）', 'diagnostics.ini', 'INI (*.ini)')
                if name:
                    atomic_write(Path(name), diagnostic_config(self.config).encode('utf-8'))
                    self.notify('排障配置已导出；仅包含策略与版本，不含会话或内容。', 'success')
            elif action in ("export_config", "export_logs"):
                is_config = action == "export_config"
                if is_config:
                    validate(self.config)
                name, _ = self.file_dialogs.getSaveFileName(None, "导出完整 INI（包含密码与密钥）" if is_config else "导出已脱敏日志", "config.ini" if is_config else "chaoxing-desktop.log", "INI (*.ini)" if is_config else "日志 (*.log)")
                if name:
                    atomic_write(Path(name), (serialize(self.config) if is_config else self.redact(self.log_redactor("\n".join(self.logs)))).encode('utf-8'))
                    self.notify("文件已导出。", "success")
        self.execute(run)

    @Slot()
    def clearLogs(self):
        self._log_model.clear()

    @Slot()
    def chooseLogDirectory(self):
        if self.busy:
            return
        directory = self.file_dialogs.getExistingDirectory(None, "选择日志保存目录", self.summary['logDirectory'])
        if directory:
            self.setValue('logging', 'directory', directory)

    @Slot()
    def deleteLogs(self):
        def run():
            count = self.log_store.delete(self.config['logging']['directory'])
            self._log_model.clear()
            self.log_save_error = ""
            self.notify(f"已删除当前日志目录中的 {count} 个应用日志文件，并清空显示。后续日志会新建文件。", "success")
        self.execute(run)
