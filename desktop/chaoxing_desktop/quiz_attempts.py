"""Durable chapter-quiz attempts that outlive a worker process.

A worker can be stopped (or lose its response) between sending
``addStudentWorkNew`` and learning whether the platform stored it. Records are
scoped by account + platform user + course + class, and keyed by the platform
*write* identity (the work id), because the platform stores submissions per work
rather than per chapter: the same work referenced by two chapters must share one
outcome, never produce two unknowns.

States:

* ``intent``   - a POST was sent and its outcome is unknown. It blocks every
                 later attempt, in any run and any continuation chain. No
                 read-only read can prove the request did not land, so only a
                 human can resolve it.
* ``accepted`` - the platform explicitly answered ``status=true``. It remembers
                 which run *lineage* accepted it, so the continuation of that
                 same run skips it while a genuinely independent start keeps its
                 historical behaviour (the platform itself decides).

A definitive rejection (``status=false``) is the only answer that proves nothing
was stored. It restores a previously accepted attempt rather than erasing that
proof, so a rejected redo cannot unlock an earlier accepted submission.

Limits that are deliberate:

* No "absent" state and no weak read-only receipt: a record list or answerable
  page is compatible with a delayed or lost write, so neither proves absence.
* Unresolved records are never evicted to make room; capacity fails closed.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .storage import atomic_write, file_lock

STATE_INTENT = 'intent'
STATE_ACCEPTED = 'accepted'
STATES = (STATE_INTENT, STATE_ACCEPTED)
FIELDS = {'workId', 'state', 'lineage', 'prevState', 'prevLineage', 'updated_at'}
MAX_RECORDS = 5000


def quiz_key(account, uid, course, clazz):
    values = (account, uid, course, clazz)
    if not all(values):
        raise ValueError('章节测验身份不完整，不能记录或复核提交。')
    return hashlib.sha256(json.dumps(list(map(str, values)), ensure_ascii=False).encode('utf-8')).hexdigest()


class QuizAttemptStore:
    MAX_BYTES = 1024 * 1024

    def __init__(self, root, account, uid):
        self.root = Path(root) / 'quiz_attempts'
        self.account, self.uid = account, uid

    def path(self, course):
        key = quiz_key(self.account, self.uid, course.get('courseId', ''), course.get('clazzId', ''))
        return self.root / (key + '.json')

    def read(self, course):
        """Raise on a damaged record: an unreadable record must fail closed."""
        path = self.path(course)
        if not path.exists():
            return {}
        try:
            if path.stat().st_size > self.MAX_BYTES:
                raise ValueError
            body = json.loads(path.read_bytes().decode('utf-8'))
            if (not isinstance(body, dict) or set(body) != {'version', 'attempts'} or body['version'] != 1
                    or not isinstance(body['attempts'], list) or len(body['attempts']) > MAX_RECORDS):
                raise ValueError
            rows = {}
            for row in body['attempts']:
                if (not isinstance(row, dict) or set(row) != FIELDS or row['state'] not in STATES
                        or row['prevState'] not in ('',) + STATES
                        or any(not isinstance(row[k], str) or len(row[k]) > 256 for k in FIELDS)
                        or not row['workId'] or not row['lineage'] or not row['updated_at']):
                    raise ValueError
                rows[row['workId']] = row
            return rows
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            raise ValueError('章节测验提交记录损坏或不可读；未重复提交，请先在平台核验。') from None

    def _write(self, path, rows):
        body = json.dumps(dict(version=1, attempts=list(rows.values())), ensure_ascii=False).encode('utf-8')
        if len(body) > self.MAX_BYTES:
            raise ValueError('章节测验记录超过容量上限，未提交任何新尝试；请先在平台核验。')
        atomic_write(path, body)

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat(timespec='seconds')

    def begin(self, course, work, lineage, *, redo=False):
        """Atomically make this attempt the recorded intent.

        An unresolved attempt blocks every run. Only an explicit redo in the
        same worker can replace an acceptance from the same run.
        """
        if not str(work or '') or not str(lineage or ''):
            raise ValueError('章节测验身份不完整，不能记录或复核提交。')
        work, lineage = str(work)[:256], str(lineage)[:256]
        path = self.path(course)
        with file_lock(path):
            rows = self.read(course)
            row = rows.get(work)
            if redo and (row is None or row['state'] != STATE_ACCEPTED or row['lineage'] != lineage):
                return False
            if row is not None and (row['state'] == STATE_INTENT or (row['lineage'] == lineage and not redo)):
                return False
            rows[work] = dict(workId=work, state=STATE_INTENT, lineage=lineage,
                              prevState=row['state'] if row else '',
                              prevLineage=row['lineage'] if row else '',
                              updated_at=self._now())
            if len(rows) > MAX_RECORDS:
                raise ValueError('章节测验待核验记录已达上限，未提交任何新尝试；请先在平台核验。')
            self._write(path, rows)
        return True

    def accept(self, course, work, lineage):
        """Record an explicit platform acceptance for this attempt."""
        work, lineage = str(work)[:256], str(lineage)[:256]
        if not work or not lineage:
            raise ValueError('章节测验身份不完整，不能记录或复核提交。')
        path = self.path(course)
        with file_lock(path):
            rows = self.read(course)
            row = rows.get(work)
            if row is None or row['state'] != STATE_INTENT or row['lineage'] != lineage:
                raise ValueError('找不到本次测验的提交意图。')
            rows[work] = dict(workId=work, state=STATE_ACCEPTED, lineage=lineage,
                              prevState='', prevLineage='', updated_at=self._now())
            if len(rows) > MAX_RECORDS:
                raise ValueError('章节测验记录已达上限；请先在平台核验。')
            self._write(path, rows)

    def reject(self, course, work, lineage):
        """Apply an explicit rejection without erasing earlier accepted proof."""
        work = str(work)[:256]
        path = self.path(course)
        with file_lock(path):
            rows = self.read(course)
            row = rows.get(work)
            if row is None or row['state'] != STATE_INTENT or row['lineage'] != lineage:
                raise ValueError('找不到本次测验的提交意图。')
            if row['prevState'] == STATE_ACCEPTED and row['prevLineage']:
                rows[work] = dict(workId=work, state=STATE_ACCEPTED, lineage=row['prevLineage'],
                                  prevState='', prevLineage='', updated_at=self._now())
            else:
                rows.pop(work)
            self._write(path, rows)


class QuizGuard:
    """Consulted by the platform adapter around the chapter-quiz POST."""

    def __init__(self, store, lineage, channel=None):
        self.store, self.channel = store, channel
        self.lineage = lineage

    def log(self, message, level='WARNING'):
        if self.channel is not None:
            self.channel.send('log', level=level, message=str(message)[:4000])

    def unresolved(self, course, work):
        """Blocking reason for an earlier attempt, or '' to allow the POST."""
        if not work or len(work) > 256:
            return '章节测验身份不完整，无法核验上次提交结果；本轮不提交，请人工核验。'
        try:
            row = self.store.read(course).get(work)
        except ValueError as exc:
            return str(exc)
        if row is None:
            return ''
        if row['state'] == STATE_INTENT:
            return '上次章节测验提交结果未知（停止或响应丢失时可能已送达平台）；未决记录会一直阻止重交，请在平台人工核验。'
        if row['lineage'] == self.lineage:
            return '该章节测验在停止前已被平台接受（成功响应与记录之间被停止），续跑不重复提交，请在平台核验成绩。'
        return ''

    def start(self, course, work, *, redo):
        """Atomically record this attempt; refuse when it may not be sent.

        The adapter sets ``redo`` only inside its configured low-score retry loop.
        """
        if not work or len(work) > 256:
            raise ValueError('章节测验身份不完整，未提交；请重新读取课程。')
        if not self.lineage:
            raise ValueError('缺少本次运行的来源标识，未提交；请重新开始任务。')
        try:
            created = self.store.begin(course, work, self.lineage, redo=redo)
        except (OSError, ValueError) as exc:
            raise ValueError(f'无法记录本次提交意图，未提交：{exc}') from None
        if not created:
            raise ValueError('该章节测验已有提交记录或缺少可重做的回执；请先在平台核验。')

    def settle(self, course, work, *, stored):
        """Apply a definitive platform answer."""
        try:
            if stored:
                self.store.accept(course, work, self.lineage)
            else:
                # An explicit rejection never erases an earlier acceptance.
                self.store.reject(course, work, self.lineage)
        except (OSError, ValueError) as exc:
            self.log(f'无法更新章节测验提交记录：{exc}')
        if not stored:
            self.log('平台已明确拒绝本次章节测验提交，记录已按原状态更新。')
