"""Incremental bounded question history; filters do not copy the retained data."""
import json
import math
import re
from collections import Counter
from .providers import PROVIDERS
from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, QSortFilterProxyModel

ROLE = int(Qt.ItemDataRole.UserRole) + 1
STAGES = dict(waiting='等待查询', searching='查询中', manual='等待人工', resolved='已采用',
              rejected='候选被拒绝', pending='待处理', error='服务失败')
SOURCES = dict(cache='本轮缓存', ai='AI', manual='人工', random='随机')
SOURCES.update({spec.id: spec.title for spec in PROVIDERS})
STRINGS = ('id', 'run_id', 'task_id', 'work_id', 'question_id', 'attempt', 'title', 'options',
           'kind', 'stage', 'source', 'reason', 'answer', 'match_mode')
NUMBERS = ('query_attempts', 'candidates', 'requests', 'retries', 'elapsed_ms')
OPTIONAL_NUMBERS = ('ai_requests', 'ai_retries', 'ai_elapsed_ms', 'manual_elapsed_ms', 'total_elapsed_ms')
FIELDS = frozenset(STRINGS + NUMBERS + OPTIONAL_NUMBERS + ('attempts', 'score', 'answer_letters', 'images'))


def valid_record(record):
    if not isinstance(record, dict) or record.keys() - FIELDS:
        return False
    if not all(isinstance(record.get(k), str) and len(record[k]) <= 1200 for k in STRINGS):
        return False
    if not record['id'] or record['stage'] not in STAGES:
        return False
    letters = record.get('answer_letters', '')
    if not isinstance(letters, str) or not re.fullmatch(r'[A-Z]{0,26}', letters):
        return False
    images = record.get('images', [])
    if not isinstance(images, list) or len(images) > 16 or not all(
        isinstance(u, str) and len(u) <= 512 and u.startswith('file://') for u in images
    ):
        return False
    if not all(type(record.get(k)) is int and 0 <= record[k] <= 10**9 for k in NUMBERS):
        return False
    if not all(type(record.get(k, 0)) is int and 0 <= record.get(k, 0) <= 10**9 for k in OPTIONAL_NUMBERS):
        return False
    score = record.get('score')
    if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
        return False
    attempts = record.get('attempts')
    if not isinstance(attempts, list) or len(attempts) > 16:
        return False
    for item in attempts:
        if not isinstance(item, dict) or set(item) != {'provider', 'status', 'reason', 'candidate', 'returned_title', 'elapsed_ms'}:
            return False
        if not all(isinstance(item[k], str) and len(item[k]) <= 1200 for k in item if k != 'elapsed_ms'):
            return False
        if type(item['elapsed_ms']) is not int or not 0 <= item['elapsed_ms'] <= 10**9:
            return False
    return True


class QuestionModel(QAbstractListModel):
    MAX_ROWS = 2000
    MAX_CHARS = 8 * 1024 * 1024

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows, self.indices, self.sizes = [], {}, {}
        self._offset = 0
        self.characters, self.evicted = 0, 0
        self.stats = Counter()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=ROLE):
        if role == ROLE and index.isValid() and 0 <= index.row() < len(self.rows):
            row = self.rows[index.row()]
            return dict(row, stageText=STAGES[row['stage']], sourceText=SOURCES.get(row['source'], row['source']))

    def clear(self):
        self.beginResetModel()
        self.rows, self.indices, self.sizes = [], {}, {}
        self._offset = 0
        self.characters = self.evicted = 0
        self.stats.clear()
        self.endResetModel()

    def account(self, row, sign):
        for key in NUMBERS + OPTIONAL_NUMBERS:
            self.stats[key] += sign * row.get(key, 0)
        self.stats['resolved'] += sign * (row['stage'] == 'resolved')
        self.stats[row['source']] += sign * (row['stage'] == 'resolved')

    def upsert(self, record):
        if not valid_record(record):
            return False
        encoded = json.dumps(record, ensure_ascii=False)
        # Validated fields are scalars, except for a list of scalar-only maps.
        # Preserve snapshot ownership without parsing the JSON a second time.
        record = dict(record, attempts=[dict(item) for item in record['attempts']])
        key = record['id']
        size = len(encoded)
        if key in self.indices:
            row = self.row_index(key)
            self.account(self.rows[row], -1)
            self.characters -= self.sizes[key]
            self.rows[row] = record
            self.dataChanged.emit(self.index(row), self.index(row), [ROLE])
        else:
            row = len(self.rows)
            self.beginInsertRows(QModelIndex(), row, row)
            self.rows.append(record)
            self.indices[key] = row + self._offset
            self.endInsertRows()
        self.sizes[key] = size
        self.characters += size
        self.account(record, 1)
        removed, remaining = 0, self.characters
        while removed < len(self.rows) and (len(self.rows) - removed > self.MAX_ROWS or remaining > self.MAX_CHARS):
            remaining -= self.sizes[self.rows[removed]['id']]
            removed += 1
        if removed:
            self.beginRemoveRows(QModelIndex(), 0, removed - 1)
            for old in self.rows[:removed]:
                self.sizes.pop(old['id'])
                self.account(old, -1)
                self.indices.pop(old['id'])
            del self.rows[:removed]
            self.characters = remaining
            self._offset += removed
            self.evicted += removed
            self.endRemoveRows()
        return True

    def row_index(self, record_id):
        absolute = self.indices.get(record_id)
        return absolute - self._offset if absolute is not None else None


class QuestionFilter(QSortFilterProxyModel):
    def __init__(self, source, parent=None):
        super().__init__(parent)
        self.pending_only, self.task = False, ''
        self.setSourceModel(source)
        self.setDynamicSortFilter(True)

    def set_filter(self, pending_only, task):
        self.beginFilterChange()
        self.pending_only, self.task = pending_only, task
        self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def filterAcceptsRow(self, row, parent):
        record = self.sourceModel().rows[row]
        return (not self.pending_only or record['stage'] != 'resolved') and (not self.task or record['task_id'] == self.task)
