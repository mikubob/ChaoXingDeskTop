"""Incremental Qt models: task updates and retained logs never reset the view."""
from collections import Counter, deque

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, QTimer, QSortFilterProxyModel

STATUS = {"waiting": "等待", "running": "处理中", "processed": "验收中", "retry": "等待重试",
          "success": "已完成", "failed": "失败", "skipped": "未开放 / 跳过", "pending": "待完成 / 未核验", "stopped": "已停止"}
TERMINAL = {"success", "failed", "skipped", "pending", "stopped"}
DATA_ROLE = int(Qt.ItemDataRole.UserRole) + 1


def media_status_text(media):
    if media['verified']:
        return '平台已确认'
    if not media['active']:
        return '未确认完成'
    return '等待平台核验' if media['percent'] >= 100 else '播放中'


class TaskModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.indices = {}
        self.counts = Counter()

    def roleNames(self):
        return {DATA_ROLE: b"modelData"}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=DATA_ROLE):
        if role == DATA_ROLE and index.isValid() and 0 <= index.row() < len(self.rows):
            task = self.rows[index.row()]
            return dict(task, statusText=STATUS.get(task["status"], task["status"]))
        return None

    def reset(self, tasks):
        self.beginResetModel()
        self.rows = [dict(task) for task in tasks]
        self.indices = {task["id"]: i for i, task in enumerate(self.rows)}
        self.counts = Counter(task["status"] for task in self.rows)
        self.endResetModel()

    def update_status(self, task_id, status):
        row = self.indices.get(task_id)
        if row is None or self.rows[row]["status"] == status:
            return False
        task = self.rows[row]
        self.counts[task["status"]] -= 1
        task["status"] = status
        if status != 'running' and 'media' in task:
            task['media']['active'] = False
            if status == 'success':
                # Worker success is issued only after platform chapter proof;
                # all media tasks in that chapter are therefore confirmed too.
                task['media']['verified'] = True
            task['media']['statusText'] = media_status_text(task['media'])
        self.counts[status] += 1
        index = self.index(row)
        self.dataChanged.emit(index, index, [DATA_ROLE])
        return True

    def update_media(self, task_id, event):
        row = self.indices.get(task_id)
        if row is None or self.rows[row]['status'] != 'running':
            return False
        duration = max(0, float(event.get('duration', 0)))
        position = min(duration, max(0, float(event.get('position', 0))))
        self.rows[row]['media'] = dict(name=str(event.get('name', '')), kind=event.get('kind', 'Video'),
                                       duration=duration, position=position, active=True,
                                       verified=bool(event.get('verified', False)),
                                       percent=round(100 * position / duration) if duration else 0)
        self.rows[row]['media']['statusText'] = media_status_text(self.rows[row]['media'])
        index = self.index(row)
        self.dataChanged.emit(index, index, [DATA_ROLE])
        return True


class LogModel(QAbstractListModel):
    MAX_ROWS = 2500
    MAX_CHARS = 512 * 1024
    MAX_LINE_CHARS = 4096

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = deque()
        self.pending = deque()
        self.characters = 0
        self.pending_characters = 0
        self.evicted = 0
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.flush)

    def roleNames(self):
        return {DATA_ROLE: b"modelData"}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=DATA_ROLE):
        if role == DATA_ROLE and index.isValid() and 0 <= index.row() < len(self.rows):
            return self.rows[index.row()]
        return None

    def append(self, text):
        if len(text) > self.MAX_LINE_CHARS:
            suffix = " …[日志过长，已截断]"
            text = text[:self.MAX_LINE_CHARS - len(suffix)] + suffix
        self.pending.append(text)
        self.pending_characters += len(text)
        while len(self.pending) > self.MAX_ROWS or self.pending_characters > self.MAX_CHARS:
            self.pending_characters -= len(self.pending.popleft())
            self.evicted += 1
        if not self.timer.isActive():
            self.timer.start()

    def flush(self):
        self.timer.stop()
        if not self.pending:
            return
        removed = 0
        remaining_chars = self.characters
        while removed < len(self.rows) and (len(self.rows) - removed + len(self.pending) > self.MAX_ROWS or
                                            remaining_chars + self.pending_characters > self.MAX_CHARS):
            # Signal before mutating storage; Qt views may query during removal.
            remaining_chars -= len(self.rows[removed])
            removed += 1
        if removed:
            self.evicted += removed
            self.beginRemoveRows(QModelIndex(), 0, removed - 1)
            for _ in range(removed):
                self.rows.popleft()
            self.characters = remaining_chars
            self.endRemoveRows()
        start = len(self.rows)
        self.beginInsertRows(QModelIndex(), start, start + len(self.pending) - 1)
        self.rows.extend(self.pending)
        self.characters += self.pending_characters
        self.pending.clear()
        self.pending_characters = 0
        self.endInsertRows()

    def snapshot(self):
        # Include the newest batch for immediate export and diagnostic callers.
        lines = deque(self.rows)
        lines.extend(self.pending)
        chars = self.characters + self.pending_characters
        while len(lines) > self.MAX_ROWS or chars > self.MAX_CHARS:
            chars -= len(lines.popleft())
        return list(lines)

    def clear(self):
        self.timer.stop()
        self.beginResetModel()
        self.rows.clear()
        self.pending.clear()
        self.characters = self.pending_characters = 0
        self.evicted = 0
        self.endResetModel()


class LogFilter(QSortFilterProxyModel):
    def __init__(self, source, parent=None):
        super().__init__(parent)
        self.level, self.query = '', ''
        self.setSourceModel(source)

    def set_filter(self, level, query):
        self.beginFilterChange()
        self.level, self.query = level, query.casefold()
        self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def filterAcceptsRow(self, row, parent):
        text = self.sourceModel().data(self.sourceModel().index(row))
        parts = text.split(maxsplit=2)
        level = parts[1] if len(parts) > 1 else ''
        return (not self.level or level == self.level or self.level == 'WARNING' and level == 'ERROR') and self.query in text.casefold()
