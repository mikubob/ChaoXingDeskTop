"""Allowlisted chapter/personal evidence reads, independent of task runners."""
from datetime import datetime, timezone
import time

from .control import Cancelled, checkpoint


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


def read_cards(client, channel, course, point, indices=None, progress=None):
    """Read cards without get_job_list's empty-page completion side effect."""
    from api.base import SessionManager
    from api.decode import decode_course_card
    from api.responses import checked_page
    session = SessionManager.get_session()
    jobs, seen = [], set()
    if indices is None:
        indices = list(range(7))
        scope = '卡数未知，兼容扫描最多 7 卡'
    else:
        if (not isinstance(indices, list) or not 0 < len(indices) <= 64
                or any(type(i) is not int or not 0 <= i < 64 for i in indices)):
            raise ValueError('本人记录卡号提示无效，未发出请求。')
        indices = sorted(set(indices))
        scope = '复核本轮已发现卡；新活动请使用全量发现'
    errors = []
    for ordinal, index in enumerate(indices):
        checkpoint(channel)
        if progress:
            progress.send('cards', f'{scope} · 第 {ordinal + 1}/{len(indices)} 卡（卡号 {index}），读取 {progress.reads}，失败 {progress.failures}')
        try:
            if progress:
                progress.reads += 1
            with session.get('https://mooc1.chaoxing.com/mooc-ans/knowledge/cards',
                         params=dict(clazzid=course['clazzId'], courseid=course['courseId'],
                                     knowledgeid=point['id'], cpi=course['cpi'], ut='s', mooc2=1,
                                     num=str(index), v='2025-0424-1038-3'),
                         timeout=15, allow_redirects=False) as response:
                text = checked_page(response, 'cards')
                import re
                if not re.search(r'\bmArg\s*=', text):
                    raise ValueError('任务卡结构未知，未将其当作没有本人记录。')
                rows, info = decode_course_card(text)
        except Exception as exc:
            if fatal_error(exc):
                raise
            # Recover only transport/service failures; unknown identity or
            # structure must not be interpreted as an empty card.
            import requests
            if not isinstance(exc, requests.RequestException) and getattr(exc, 'kind', '') != 'service_error':
                raise
            errors.append(str(exc))
            if progress:
                progress.failures += 1
            channel.send('log', level='WARNING', message=f'卡号 {index} 本次读取失败，继续其他卡：' + channel.redactor(str(exc))[:500])
            continue
        checkpoint(channel)
        if info.get('notOpen'):
            raise ValueError('章节尚未开放；请在平台确认开放条件。')
        if str(info.get('knowledgeid')) != str(point['id']):
            from api.responses import PlatformResponseError
            raise PlatformResponseError('任务卡章节身份变化，请重新读取课程。', 'identity_error')
        for row in rows:
            if row.get('type') in ('discussion', 'activity'):
                identity = (row['type'], str(row.get('mid', '')), str(row.get('jobid', '')))
                if row.get('mid') and identity in seen:
                    continue
                seen.add(identity)
                jobs.append((row, dict(info, card_index=str(index))))
        if len(jobs) > 1000:
            raise ValueError('本章节讨论／活动超过核验容量。')
    # Keep successful cards usable while exposing incomplete discovery.
    if errors:
        channel.send('log', level='WARNING', message=f'{len(errors)} 张卡查询未完成；没有将其视为没有本人记录。')
    return jobs


def verify_records(client, channel, course, target, point, progress=None):
    """Only resolve/own lookup/task status; never instantiate a business runner."""
    import hashlib
    import json
    from api.discussion import DiscussionAdapter
    from api.activity import ActivityAdapter
    kwargs = dict(indices=target['record_cards']) if 'record_cards' in target else {}
    if progress:
        kwargs['progress'] = progress
    seen = set()
    jobs = read_cards(client, channel, course, point, **kwargs)
    if 'record_cards' in target and not jobs:
        raise ValueError('已发现卡的位置未取得本人目标；请使用全量发现重新定位，不将其视为没有参与。')
    for job, info in jobs:
        checkpoint(channel)
        kind = job['type']
        native = (kind, str(job.get('mid', '')), str(job.get('jobid', '')))
        if job.get('mid') and native in seen:
            continue
        seen.add(native)
        record_id = hashlib.sha256(json.dumps([target['id'], course['courseId'], course['clazzId'],
                                               job.get('mid', ''), info.get('cardid', job.get('cardid', ''))]).encode()).hexdigest()
        status, task_status = 'unknown', 'pending'
        try:
            if kind == 'discussion':
                adapter = DiscussionAdapter(client)
                context = adapter.resolve(course, job, info)
                checkpoint(channel)
                lookup = adapter.own_reply(context)
                status = 'existing' if lookup.reply_id else 'absent' if lookup.known else 'unknown'
            else:
                adapter = ActivityAdapter(client)
                context = adapter.resolve(course, job, info)
                status = context.participation
            checkpoint(channel)
            task_status = adapter.task_status(context)
            reason = {'existing': '已取得本人参与证据；不证明旧草稿已送达或成绩通过。',
                      'absent': '本次读取未发现本人记录；平台可能延迟，未决意图仍禁止重发。',
                      'unknown': '本人状态证据不足；请在平台检查，不能据此判定未提交。'}[status]
        except Cancelled:
            raise
        except Exception as exc:
            if fatal_error(exc):
                raise
            status, task_status = 'unknown', 'pending'
            if progress:
                progress.failures += 1
            reason = '只读核验失败，保留原记录：' + channel.redactor(str(exc))[:1000]
        checkpoint(channel)
        channel.send('record_verification', id=record_id, task_id=target['id'], kind=kind,
                     participation=status, task_status=task_status,
                     checked_at=datetime.now(timezone.utc).isoformat(timespec='seconds'), reason=reason)
        if progress:
            progress.send('records', f'已复核 {len(seen)} 个本人记录目标；章节 {progress.completed}/{progress.total}，失败 {progress.failures}')


def verify_chapters(client, channel, courses, targets, records=False, completion_cache=None):
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
            if records:
                if point is None:
                    progress.failures += 1
                    channel.send('log', level='WARNING', message='章节已不存在，保留讨论／活动原状态。')
                else:
                    before = progress.failures
                    try:
                        verify_records(client, channel, course, target, point, progress=progress)
                    except Cancelled:
                        raise
                    except Exception as exc:
                        if fatal_error(exc):
                            raise
                        progress.failures += 1
                        channel.send('log', level='WARNING', message='本人状态未核验：' + channel.redactor(str(exc))[:1000])
                    if progress.failures > before:
                        channel.send('verification_failed', id=target['id'], checked_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                                     reason='本人记录查询未完成或读取失败；旧记录保留，不能据此认定没有参与。')
            progress.completed += 1
            progress.send('chapters', f'已处理章节 {progress.completed}/{progress.total}；读取 {progress.reads}，失败 {progress.failures}')
    checkpoint(channel)
    channel.send('verification_done', message=f'只读核验结束：{progress.completed}/{progress.total} 章，读取 {progress.reads}，失败 {progress.failures}。证据不足或查询未完成的事项仍待处理；本人状态、任务点与章节分别判断，未决意图保留。')
