"""Display-only course progress; task completion has a separate cache."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .control import checkpoint
from .storage import atomic_write, file_lock


def valid_progress(row):
    return (isinstance(row, dict)
            and all(isinstance(row.get(k), str) and 0 < len(row[k]) <= 256
                    for k in ('courseId', 'clazzId', 'cpi', 'checked_at'))
            and all(type(row.get(k)) is int for k in ('completed', 'total'))
            and 0 <= row['completed'] <= row['total'] <= 100000)


def progress_key(course):
    return tuple(str(course.get(k, '')) for k in ('courseId', 'clazzId', 'cpi'))


def progress_identity(course):
    return dict(zip(('courseId', 'clazzId', 'cpi'), progress_key(course)))


def chapter_progress(course, points):
    if (not isinstance(points, list) or any(not isinstance(p, dict) or 'id' not in p
                                          or type(p.get('has_finished')) is not bool for p in points)):
        raise ValueError('章节进度响应结构未知。')
    unique = {str(p['id']): p for p in points}
    return dict(progress_identity(course),
                completed=sum(p['has_finished'] for p in unique.values()), total=len(unique),
                checked_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))


def send_progress(channel, course, points):
    row = chapter_progress(course, points)
    if not valid_progress(row):
        raise ValueError('课程进度格式无效。')
    channel.send('course_progress', **row)


def refresh_progress(client, channel, courses):
    from .verification import fatal_error
    for course in courses:
        checkpoint(channel)
        identity = progress_identity(course)
        channel.send('course_progress_loading', **identity)
        try:
            result = client.get_course_point(course['courseId'], course['clazzId'], course['cpi'])
            checkpoint(channel)
            send_progress(channel, course, result['points'])
        except Exception as exc:
            if fatal_error(exc):
                raise
            channel.send('course_progress_failed', **identity)


class CourseProgressCache:
    MAX_BYTES = 1024 * 1024
    MAX_COURSES = 2000

    def __init__(self, root, account, uid):
        name = hashlib.sha256(json.dumps([account, uid]).encode()).hexdigest()
        self.path = Path(root) / 'course_progress' / (name + '.json')

    def read(self):
        try:
            if self.path.stat().st_size > self.MAX_BYTES:
                return {}
            body = json.loads(self.path.read_bytes())
            if not isinstance(body, dict):
                return {}
            rows = body['courses']
            if (body.get('version') != 1 or not isinstance(rows, list) or len(rows) > self.MAX_COURSES
                    or any(not valid_progress(row) for row in rows)):
                return {}
            return {progress_key(row): row for row in rows}
        except (OSError, ValueError, KeyError, TypeError):
            return {}

    def save(self, row):
        if not valid_progress(row):
            raise ValueError('课程进度格式无效。')
        with file_lock(self.path):
            rows = self.read()
            rows[progress_key(row)] = {k: row[k] for k in
                                      ('courseId', 'clazzId', 'cpi', 'completed', 'total', 'checked_at')}
            latest = sorted(rows.values(), key=lambda r: r['checked_at'], reverse=True)[:self.MAX_COURSES]
            body = json.dumps(dict(version=1, courses=latest)).encode()
            if len(body) > self.MAX_BYTES:
                raise ValueError('课程进度缓存超过容量。')
            atomic_write(self.path, body)
