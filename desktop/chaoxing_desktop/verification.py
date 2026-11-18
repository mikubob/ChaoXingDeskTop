"""只读核验待完成章节的平台进度。"""
from datetime import datetime, timezone
import time

from .control import checkpoint


def fatal_error(exc):
    return getattr(exc, 'kind', '') in ('auth_error', 'verification_required', 'identity_error', 'rate_limited')


class Progress:
    def __init__(self, channel, total):
        self.channel, self.total = channel, total
        self.completed = self.reads = self.failures = 0
        self.started = time.monotonic()

    def send(self, phase, message):
        checkpoint(self.channel)
        if time.monotonic() - self.started > 1200:
            raise TimeoutError('本次只读核验达到 20 分钟上限，请缩小范围后重试。')
        self.channel.send('verification_progress', phase=phase, message=message[:2000], completed=self.completed,
                          total=self.total, reads=self.reads, failures=self.failures,
                          elapsed_ms=min(10**9, round((time.monotonic() - self.started) * 1000)))


def verify_chapters(client, channel, courses, targets, completion_cache=None):
    """Re-read the allowlisted chapter endpoint, never infer submission status."""
    identities = {(str(c['courseId']), str(c['clazzId'])): c for c in courses}
    if not targets or len(targets) > 10000:
        raise ValueError('请选择本轮可定位的章节（最多 10000 项）。')
    if any((str(t['courseId']), str(t['clazzId'])) not in identities for t in targets):
        raise ValueError('课程身份已变化，请重新读取课程。')
    groups, seen = {}, set()
    for target in targets:
        identity = (str(target['courseId']), str(target['clazzId']), str(target['pointId']))
        if identity in seen:
            continue
        seen.add(identity)
        groups.setdefault((str(target['courseId']), str(target['clazzId'])), []).append(target)
    progress = Progress(channel, len(seen))
    for identity, items in groups.items():
        checkpoint(channel)
        course = identities[identity]
        progress.send('chapters', f'正在读取课程章节；已处理 {progress.completed}/{progress.total}，失败 {progress.failures}')
        try:
            progress.reads += 1
            result = client.get_course_point(course['courseId'], course['clazzId'], course['cpi'])
        except Exception as exc:
            if fatal_error(exc):
                raise
            import requests
            if not isinstance(exc, requests.RequestException) and getattr(exc, 'kind', '') != 'service_error':
                raise
            progress.failures += 1
            for target in items:
                checkpoint(channel)
                channel.send('verification_failed', id=target['id'], checked_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                             reason='本次课程读取失败，保留旧状态：' + channel.redactor(str(exc))[:1000])
                progress.completed += 1
            progress.send('chapters', f'课程读取失败，继续其他课程；已处理 {progress.completed}/{progress.total}，失败 {progress.failures}')
            continue
        if not isinstance(result, dict) or not isinstance(result.get('points'), list):
            raise ValueError('章节核验响应结构未知，保留原状态。')
        points = result['points']
        if any(not isinstance(p, dict) or 'id' not in p for p in points):
            raise ValueError('章节核验响应结构未知，保留原状态。')
        by_id = {str(p['id']): p for p in points}
        for target in items:
            checkpoint(channel)
            point = by_id.get(str(target['pointId']))
            confirmed = point is not None and point.get('has_finished') is True
            if completion_cache is not None:
                try:
                    if confirmed:
                        completion_cache.save(course, dict(point, title=point.get('title') or str(target['pointId'])))
                    else:
                        completion_cache.remove(course, target['pointId'])
                except (OSError, ValueError) as exc:
                    channel.send('log', level='WARNING', message=f'无法同步任务完成缓存：{exc}')
            channel.send('verification', id=target['id'], confirmed=confirmed,
                         checked_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                         reason='平台已确认章节完成' if confirmed else
                                '未取得章节完成证据；不判断答案正确、讨论发布或问卷提交。未决意图保留。')
            progress.completed += 1
            progress.send('chapters', f'已处理章节 {progress.completed}/{progress.total}；读取 {progress.reads}，失败 {progress.failures}')
    checkpoint(channel)
    channel.send('verification_done', message=f'只读核验结束：{progress.completed}/{progress.total} 章，读取 {progress.reads}，失败 {progress.failures}。未确认完成的章节仍待处理。')
