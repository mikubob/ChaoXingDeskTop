"""Bounded independent discussion history and strict worker event contract."""
import json
from .record_stats import RecordStats
from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, QSortFilterProxyModel

ROLE = int(Qt.ItemDataRole.UserRole) + 1
STAGES = dict(discovered='已识别', checking='检查本人回复', generating='生成中', draft='草稿就绪',
              submitting='提交中', published='已发布', existing='本人已回复',
              publish_unknown='发布未知', pending='待处理', skipped='非任务点，已跳过')
TASKS = dict(pending='待确认', confirmed='已确认', not_applicable='不适用')
LEVELS = dict(concise='精简', medium='中等', detailed='详细', fixed_good='固定好')
SOURCES = {'ai': 'AI 生成', 'fixed': '固定回复', '': ''}
LIMITS = dict(id=256, run_id=128, task_id=256, course=500, chapter=500, title=2000,
              level=32, source=16, stage=32, task_status=32, reason=2000, content=1000)


def valid_record(record):
    return (isinstance(record, dict) and set(record) == set(LIMITS) | {'elapsed_ms'}
            and all(isinstance(record.get(k), str) and len(record[k]) <= n for k, n in LIMITS.items())
            and bool(record['id']) and record['stage'] in STAGES and record['level'] in LEVELS
            and record['source'] in ('', 'ai', 'fixed') and record['task_status'] in TASKS
            and type(record['elapsed_ms']) is int and 0 <= record['elapsed_ms'] <= 10**9)


def safe_record(record, redactor):
    # Retain exact full text in storage; UI receives only bounded, redacted text.
    clean = {k: redactor(str(record.get(k, '')))[:n] for k, n in LIMITS.items()}
    clean['elapsed_ms'] = max(0, min(10**9, int(record.get('elapsed_ms', 0))))
    if not valid_record(clean):
        raise ValueError('讨论事件格式无效。')
    return clean


def detail_text(record):
    """Presentation allowlist: identifiers remain in storage and RPC only."""
    fields = [('课程', record['course']), ('章节', record['chapter']),
              ('讨论主题', record['title']), ('处理阶段', STAGES[record['stage']]),
              ('任务点状态', TASKS[record['task_status']]),
              ('回复方式', LEVELS[record['level']]), ('内容来源', SOURCES[record['source']]),
              ('处理说明', record['reason']), ('回复正文', record['content'])]
    return '\n\n'.join(label + '：' + value for label, value in fields if value)


class DiscussionModel(QAbstractListModel):
    MAX_ROWS = 2000
    MAX_CHARS = 8 * 1024 * 1024

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows, self.indices = [], {}
        self.characters = self.evicted = 0
        self.stats = RecordStats()

    def roleNames(self):
        return {ROLE: b'modelData'}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=ROLE):
        if role == ROLE and index.isValid() and 0 <= index.row() < len(self.rows):
            r = self.rows[index.row()]
            return dict(r, stageText=STAGES[r['stage']], levelText=LEVELS[r['level']],
                        taskText=TASKS[r['task_status']], sourceText={'ai': 'AI', 'fixed': '固定回复', '': ''}[r['source']])

    def clear(self):
        self.beginResetModel()
        self.rows, self.indices = [], {}
        self.characters = self.evicted = 0
        self.stats = RecordStats()
        self.endResetModel()

    def upsert(self, record):
        if not valid_record(record):
            return False
        row = dict(record)
        size = len(json.dumps(row, ensure_ascii=False))
        i = self.indices.get(row['id'])
        if i is not None:
            self.stats.add(self.rows[i], -1)
            self.characters -= len(json.dumps(self.rows[i], ensure_ascii=False))
            self.rows[i] = row
            self.dataChanged.emit(self.index(i), self.index(i), [ROLE])
        else:
            i = len(self.rows)
            self.beginInsertRows(QModelIndex(), i, i)
            self.rows.append(row)
            self.indices[row['id']] = i
            self.endInsertRows()
        self.characters += size
        self.stats.add(row)
        while len(self.rows) > self.MAX_ROWS or self.characters > self.MAX_CHARS:
            self.beginRemoveRows(QModelIndex(), 0, 0)
            old = self.rows.pop(0)
            self.stats.add(old, -1)
            self.characters -= len(json.dumps(old, ensure_ascii=False))
            self.evicted += 1
            self.indices = {r['id']: n for n, r in enumerate(self.rows)}
            self.endRemoveRows()
        return True

    def detail(self, record_id):
        i = self.indices.get(record_id)
        if i is None:
            return {}
        record = self.data(self.index(i))
        return dict(record, detailText=detail_text(record))


class DiscussionFilter(QSortFilterProxyModel):
    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.query, self.stage = '', ''
        self.setSourceModel(model)

    def set_filter(self, query, stage):
        self.beginFilterChange()
        self.query, self.stage = query.casefold(), stage
        self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def filterAcceptsRow(self, row, parent):
        r = self.sourceModel().rows[row]
        return (not self.stage or r['stage'] == self.stage) and (not self.query or self.query in
                '\n'.join(r[k] for k in ('title', 'course', 'chapter', 'reason')).casefold())
