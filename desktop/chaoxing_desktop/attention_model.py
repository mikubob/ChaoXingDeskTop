"""Incremental, bounded action list; UI eviction never touches intent stores."""
from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, QSortFilterProxyModel

from .models import STATUS
from .question_model import STAGES as QUESTION_STAGES
from .discussion_model import STAGES as DISCUSSION_STAGES
from .activity_model import STAGES as ACTIVITY_STAGES

ROLE = int(Qt.ItemDataRole.UserRole) + 1
ACTIONS = dict(skipped='章节可能未开放或被跳过；在平台确认开放条件后重新读课。',
               failed='处理失败；查看本轮日志，确认会话、验证要求和网络后重新读课。',
               stopped='本轮已停止；检查平台当前状态后再决定是否重新运行。',
               pending='尚无章节完成证据；可只读核验平台进度。',
               processed='本轮已处理，仍需核验平台完成状态。')


def chapter_issue(task):
    status = task['status']
    if status not in ACTIONS:
        return None
    evidence = '\n最近核验：' + task['checked_at'] + '\n' + task['evidence'] if task.get('checked_at') else ''
    return dict(id=task['id'], task=task['id'], kind='章节', title=task['title'], course=task['course'],
                status=STATUS[status], reason=ACTIONS[status] + evidence, next=ACTIONS[status],
                bucket='platform' if status in ('processed', 'pending') else 'failed')


def record_issue(kind, record):
    stage = record['stage']
    unknown = stage in ('publish_unknown', 'submit_unknown', 'intent')
    if ((kind == '题目' and stage not in ('manual', 'pending', 'rejected', 'error')) or
            (kind != '题目' and ((record['task_status'] in ('confirmed', 'not_applicable') and not unknown)
                               or stage in ('discovered', 'checking', 'generating', 'submitting')))):
        return None
    stages = QUESTION_STAGES if kind == '题目' else DISCUSSION_STAGES if kind == '讨论' else ACTIVITY_STAGES
    return dict(id=record['id'], task=record.get('task_id', ''), kind=kind, title=record['title'],
                course=record.get('course', ''), status=stages.get(stage, stage),
                bucket='unknown' if unknown else 'failed' if stage in ('rejected', 'error') else
                       'manual' if stage in ('draft', 'manual') else 'platform',
                reason=record.get('reason', '') or '等待平台或人工确认。',
                next='结果未知：请在平台检查本人状态，保留意图；禁止重复发送。' if unknown else
                     '查看原记录与来源；草稿、答案采用和平台完成分别确认。')


class AttentionModel(QAbstractListModel):
    MAX_ROWS = 2000
    MAX_BYTES = 8 * 1024 * 1024

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.indices = {}
        self.evicted = 0
        self.sizes = {}
        self.bytes = 0

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=ROLE):
        if role == ROLE and index.isValid() and 0 <= index.row() < len(self.rows):
            return dict(self.rows[index.row()])

    def clear(self):
        self.beginResetModel()
        self.rows, self.indices, self.evicted = [], {}, 0
        self.sizes, self.bytes = {}, 0
        self.endResetModel()

    def remove(self, index):
        self.beginRemoveRows(QModelIndex(), index, index)
        old = self.rows.pop(index)
        self.bytes -= self.sizes.pop(old['key'])
        self.indices = {row['key']: i for i, row in enumerate(self.rows)}
        self.endRemoveRows()

    def upsert(self, key, issue):
        index = self.indices.get(key)
        if issue is None:
            if index is not None:
                self.remove(index)
            return
        issue = {k: str(v)[:4000] for k, v in issue.items()}
        issue['key'] = key
        size = sum(len(v.encode('utf-8')) for v in issue.values())
        if index is not None and self.bytes - self.sizes[key] + size > self.MAX_BYTES:
            self.remove(index)
            index = None
        if index is not None:
            if self.rows[index] != issue:
                self.bytes += size - self.sizes[key]
                self.sizes[key] = size
                self.rows[index] = issue
                self.dataChanged.emit(self.index(index), self.index(index), [ROLE])
            return
        while self.rows and (len(self.rows) >= self.MAX_ROWS or self.bytes + size > self.MAX_BYTES):
            self.remove(0)
            self.evicted += 1
        index = len(self.rows)
        self.beginInsertRows(QModelIndex(), index, index)
        self.rows.append(issue)
        self.indices[key] = index
        self.sizes[key] = size
        self.bytes += size
        self.endInsertRows()


class AttentionFilter(QSortFilterProxyModel):
    def __init__(self, source, parent=None):
        super().__init__(parent)
        self.query = self.bucket = ''
        self.setSourceModel(source)

    def set_filter(self, query, bucket):
        self.beginFilterChange()
        self.query, self.bucket = query.casefold(), bucket
        self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def filterAcceptsRow(self, row, parent):
        issue = self.sourceModel().rows[row]
        return ((not self.bucket or issue['bucket'] == self.bucket) and
                (not self.query or self.query in (issue['title'] + issue['course'] + issue['reason']).casefold()))
