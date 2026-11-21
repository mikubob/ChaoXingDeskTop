"""Bounded, strictly validated activity result events and Qt history model."""
import json
from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, QSortFilterProxyModel

from .activity_store import valid_answers
from .record_stats import RecordStats

ROLE = int(Qt.ItemDataRole.UserRole) + 1
STAGES = dict(discovered='已发现', checking='核实参与状态', generating='生成答案', draft='本地草稿',
              submitting='提交中', submitted='已确认提交', existing='本人已参与', submit_unknown='提交未知',
              rejected='平台拒绝', pending='待处理', skipped='已跳过')
SUBMITS = dict(none='尚未提交', draft='本地草稿', intent='提交中', confirmed='已确认', existing='本人已参与',
               unknown='结果未知', rejected='平台拒绝')
TASKS = dict(pending='待确认', confirmed='已确认', not_applicable='不适用')
SOURCES = dict(ai='AI', random='随机选择', fixed='固定好')
TYPES = dict(single='单选', multiple='多选', text='文本', unsupported='未适配')
LIMITS = dict(id=256, run_id=128, task_id=256, course=500, chapter=500, title=2000,
              kind=16, stage=32, submit_status=32, task_status=32, reason=2000)


def valid_record(r):
    if (not isinstance(r, dict) or set(r) != set(LIMITS) | {'elapsed_ms', 'questions', 'answers'}
            or not all(isinstance(r.get(k), str) and len(r[k]) <= n for k, n in LIMITS.items())
            or not r['id'] or r['stage'] not in STAGES or r['submit_status'] not in SUBMITS
            or r['task_status'] not in TASKS or r['kind'] not in ('poll', 'survey')
            or type(r['elapsed_ms']) is not int or not 0 <= r['elapsed_ms'] <= 10**9
            or not valid_answers(r['answers']) or not isinstance(r['questions'], list) or len(r['questions']) > 100):
        return False
    seen = set()
    for q in r['questions']:
        if (not isinstance(q, dict) or set(q) != {'id', 'type', 'title', 'required', 'options', 'minimum',
                                               'maximum', 'min_length', 'max_length', 'reason'}
                or not isinstance(q['id'], str) or not 0 < len(q['id']) <= 256 or q['id'] in seen
                or q['type'] not in ('single', 'multiple', 'text', 'unsupported')
                or not isinstance(q['title'], str) or len(q['title']) > 2000
                or not isinstance(q['reason'], str) or len(q['reason']) > 2000
                or type(q['required']) is not bool or not isinstance(q['options'], list) or len(q['options']) > 100
                or any(type(q[k]) is not int or not 0 <= q[k] <= 1000
                       for k in ('minimum', 'maximum', 'min_length', 'max_length'))):
            return False
        seen.add(q['id'])
        ids = set()
        for option in q['options']:
            if (not isinstance(option, dict) or set(option) != {'id', 'text'}
                    or not isinstance(option['id'], str) or not 0 < len(option['id']) <= 256
                    or option['id'] in ids or not isinstance(option['text'], str) or len(option['text']) > 500):
                return False
            ids.add(option['id'])
    return len(json.dumps(r, ensure_ascii=False).encode()) <= 512*1024


def safe_record(record, redactor):
    if not isinstance(record, dict):
        raise ValueError('活动事件结构无效。')
    clean = {k: redactor(str(record.get(k, '')))[:n] for k, n in LIMITS.items()}
    clean['elapsed_ms'] = max(0, min(10**9, int(record.get('elapsed_ms', 0))))
    clean['questions'] = []
    for q in record.get('questions', [])[:100]:
        out = {k: redactor(str(q.get(k, '')))[:n] for k, n in dict(id=256, type=32, title=2000, reason=2000).items()}
        out.update(required=q.get('required', False), minimum=q.get('minimum', 1), maximum=q.get('maximum', 1),
                   min_length=q.get('min_length', 0), max_length=q.get('max_length', 1000),
                   options=[dict(id=redactor(str(o['id']))[:256], text=redactor(str(o['text']))[:500])
                            for o in q.get('options', [])[:100]])
        clean['questions'].append(out)
    clean['answers'] = []
    for a in record.get('answers', [])[:100]:
        out = dict(question_id=redactor(str(a.get('question_id', '')))[:256], source=a.get('source', ''),
                   valid=a.get('valid', False), reason=redactor(str(a.get('reason', '')))[:2000])
        if 'option_ids' in a:
            out['option_ids'] = [redactor(str(v))[:256] for v in a['option_ids'][:100]]
        else:
            out['text'] = redactor(str(a.get('text', '')))[:1000]
        clean['answers'].append(out)
    if not valid_record(clean):
        raise ValueError('活动事件格式无效或超过大小上限。')
    return clean


def detail_text(record):
    lines = [record['title'], record['course']+' / '+record['chapter'],
             '提交：'+SUBMITS[record['submit_status']]+'；任务点：'+TASKS[record['task_status']],
             '章节状态请查看任务列表的最终平台复核结果。', '原因：'+record['reason'], '']
    answers = {a['question_id']: a for a in record['answers']}
    for i, q in enumerate(record['questions']):
        a = answers.get(q['id'], {})
        lines.extend([f"{i+1}. {q['title']}", f"题型：{TYPES[q['type']]} · {'必答' if q['required'] else '可选'}"])
        if q['options']:
            values = set(a.get('option_ids', []))
            lines.extend(('[已选] ' if o['id'] in values else '       ')+o['text'] for o in q['options'])
        else:
            lines.append(a.get('text', '') or '尚无正文')
        lines.extend(['来源：'+SOURCES.get(a.get('source'), '未生成')+' · '+('校验通过' if a.get('valid') else a.get('reason') or q['reason']), ''])
    return '\n'.join(lines)


class ActivityModel(QAbstractListModel):
    MAX_ROWS, MAX_CHARS = 2000, 8*1024*1024

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows, self.indices, self.characters, self.evicted = [], {}, 0, 0
        self.stats = RecordStats()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=ROLE):
        if role == ROLE and index.isValid() and 0 <= index.row() < len(self.rows):
            r = json.loads(json.dumps(self.rows[index.row()]))
            return dict(r, stageText=STAGES[r['stage']], submitText=SUBMITS[r['submit_status']],
                        taskText=TASKS[r['task_status']], questionCount=len(r['questions']),
                        solved=sum(a['valid'] for a in r['answers']), requiredCount=sum(q['required'] for q in r['questions']),
                        sourceText=' + '.join(SOURCES[s] for s in SOURCES if any(a['source'] == s for a in r['answers'])),
                        detailText=detail_text(r))

    def clear(self):
        self.beginResetModel()
        self.rows, self.indices, self.characters, self.evicted = [], {}, 0, 0
        self.stats = RecordStats()
        self.endResetModel()

    def upsert(self, record):
        if not valid_record(record):
            return False
        row = json.loads(json.dumps(record))
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
        return self.data(self.index(i)) if i is not None else {}


class ActivityFilter(QSortFilterProxyModel):
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
