"""Persist confirmed chapter tasks independently of aggregate course statistics."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .course_progress import progress_key
from .storage import atomic_write, file_lock


class TaskCompletionCache:
    MAX_BYTES = 4 * 1024 * 1024
    MAX_TASKS = 10000

    def __init__(self, root, account, uid):
        self.root, self.account, self.uid = Path(root), account, uid

    def path(self, course):
        identity = [self.account, self.uid, *progress_key(course)]
        name = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        return self.root / 'task_completion' / (name + '.json')

    def read(self, course):
        path = self.path(course)
        try:
            if path.stat().st_size > self.MAX_BYTES:
                return {}
            data = json.loads(path.read_bytes())
            if not isinstance(data, dict) or data.get('version') != 1:
                return {}
            tasks = data['tasks']
            if (not isinstance(tasks, list) or len(tasks) > self.MAX_TASKS
                    or any(not isinstance(t, dict) or t.get('status') != 'success'
                           or any(not isinstance(t.get(k), str) or not t[k] or len(t[k]) > limit
                                  for k, limit in (('pointId', 256), ('title', 2000), ('checked_at', 256)))
                           for t in tasks)):
                return {}
            return {t['pointId']: t for t in tasks}
        except (OSError, ValueError, TypeError, KeyError):
            return {}

    def save(self, course, point):
        row = dict(pointId=str(point['id']), title=str(point['title'])[:2000], status='success',
                   checked_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))
        path = self.path(course)
        with file_lock(path):
            tasks = self.read(course)
            tasks[row['pointId']] = row
            rows = sorted(tasks.values(), key=lambda t: t['checked_at'], reverse=True)[:self.MAX_TASKS]
            body = json.dumps(dict(version=1, tasks=rows), ensure_ascii=False).encode('utf-8')
            if len(body) > self.MAX_BYTES:
                raise ValueError('任务完成缓存超过容量。')
            atomic_write(path, body)

    def remove(self, course, point_id):
        path = self.path(course)
        with file_lock(path):
            tasks = self.read(course)
            if tasks.pop(str(point_id), None) is None:
                return
            body = json.dumps(dict(version=1, tasks=list(tasks.values())), ensure_ascii=False).encode('utf-8')
            atomic_write(path, body)
