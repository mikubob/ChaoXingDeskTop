"""Application orchestration, independent of the reference CLI entry point."""
from enum import Enum
import threading

from api.base import Account, Chaoxing, StudyResult
from api.decode import attachment_kind
from api.logger import logger
from api.notification import Notification as Notification  # Exported for worker completion notifications.
from .answers import build_answers
from .control import checkpoint, control_of


class ChapterResult(Enum):
    SUCCESS = 0
    ERROR = 1
    NOT_OPEN = 2
    PENDING = 3


def create_client(config, channel, courses_only=False):
    from api.answer import DummyTiku
    common = config["common"]
    answers = DummyTiku() if courses_only else build_answers(config, channel)
    client = Chaoxing(account=Account(common["username"], common["password"]), tiku=answers,
                      query_delay=config["tiku"]["delay"], work_max_retries=common["work_max_retries"],
                      work_redo_enabled=common["work_redo_enabled"])
    client.control = control_of(channel)
    from api.base import SessionManager
    SessionManager.get_session().control = client.control
    client.desktop_work_lock = threading.Lock()
    client.desktop_media_context = threading.local()
    if not courses_only:
        from api.discussion import DiscussionAdapter
        from .discussion import DiscussionRunner
        from .config import data_dir
        client.desktop_discussion = DiscussionRunner(config, channel, answers, DiscussionAdapter(client), data_dir())
        from api.activity import ActivityAdapter
        from .activity import ActivityRunner
        client.desktop_activity = ActivityRunner(config, channel, answers, ActivityAdapter(client), data_dir())
    def media_progress(**values):
        task_id = getattr(client.desktop_media_context, 'task_id', None)
        if task_id:
            values.update(media_index=getattr(client.desktop_media_context, 'media_index', 1),
                          media_total=getattr(client.desktop_media_context, 'media_total', 1))
            channel.send('media', id=task_id, **values)
    client.desktop_media_progress = media_progress
    client.desktop_record_cards = lambda **values: channel.send('record_card_hint', **values)
    client.desktop_job_scope = lambda **values: channel.send('job_scope', **values)
    return client


def process_job(client, course, job, info, speed):
    checkpoint(client)
    info = job.get('_job_info', info)
    kind = job["type"]
    if kind == 'activity':
        context = client.desktop_media_context
        return client.desktop_activity.process(course, job, info, getattr(context, 'task_id', '') or '',
                                               getattr(context, 'chapter', '') or '')
    if kind == 'discussion':
        context = client.desktop_media_context
        return client.desktop_discussion.process(course, job, info, getattr(context, 'task_id', '') or '',
                                                  getattr(context, 'chapter', '') or '')
    if kind == 'video' and attachment_kind(job) == 'audio':
        kind = 'audio'
    if kind == 'audio':
        return client.study_video(course, job, info, _speed=speed, _type='Audio')
    if kind == "video":
        return client.study_video(course, job, info, _speed=speed, _type="Video")
    if kind == "document":
        return client.study_document(course, job, info)
    if kind == "read":
        return client.study_read(course, job, info)
    if kind == "workid":
        # Feedback and manual dialogs belong to one exercise at a time.
        with client.desktop_work_lock:
            if hasattr(client.tiku, 'set_context'):
                client.tiku.set_context(task_id=getattr(client.desktop_media_context, 'task_id', '') or '',
                                        work_id=str(job.get('jobid', '')))
            client.tiku.set_work_feedback(None)
            return client.study_work(course, job, info)
    if kind == "live":
        from api.live import Live
        from api.live_process import LiveProcessor
        live = Live(attachment=job, defaults={"userid": client.get_uid(), "clazzId": course["clazzId"],
                                             "knowledgeid": info.get("knowledgeid")}, course_id=course["courseId"])
        live.control = control_of(client)
        return StudyResult.SUCCESS if LiveProcessor.run_live(live, speed) else StudyResult.ERROR
    return StudyResult.ERROR


def process_chapter(client, course, point, speed, task_id=None, allowed_jobs=None):
    context = getattr(client, 'desktop_media_context', None)
    if context is not None:
        context.task_id = task_id
        context.chapter = point.get('title', '')
    try:
        return _process_chapter(client, course, point, speed, allowed_jobs)
    finally:
        if context is not None:
            context.task_id = None
            context.chapter = None
            context.media_index = 1
            context.media_total = 1


def job_identity(job):
    job_id = str(job.get('jobid') or '').strip()
    resource_id = str(job.get('objectid') or job.get('mid') or job.get('liveId') or '').strip()
    if job_id or resource_id:
        return (job['type'], job_id, resource_id)


def _process_chapter(client, course, point, speed, allowed_jobs=None):
    if point.get("has_finished"):
        return ChapterResult.SUCCESS
    jobs, info = client.get_job_list(course, point)
    if info.get("notOpen"):
        return ChapterResult.NOT_OPEN
    task_id = getattr(getattr(client, 'desktop_media_context', None), 'task_id', None)
    if allowed_jobs is not None:
        # A chapter that was already scanned before the stop keeps exactly the
        # task points that existed then; newly added ones stay out.
        wanted = {tuple(row) for row in allowed_jobs}
        kept, unidentified = [], 0
        for job in jobs:
            identity = job_identity(job)
            if identity is not None and identity in wanted:
                kept.append(job)
            elif identity is None:
                # No stable id: this job cannot be proven to be part of the
                # stopped scope, so it is skipped and reported instead of
                # being matched loosely.
                unidentified += 1
        dropped = len(jobs) - len(kept)
        if dropped:
            logger.warning('续跑：章节「{}」有 {} 个任务点不在原范围内（含缺少稳定标识的 {} 个），本轮不处理。',
                           point.get('title', ''), dropped, unidentified)
        jobs = kept
    hint = getattr(client, 'desktop_record_cards', None)
    if hint and task_id:
        indices = {str(j.get('_job_info', {}).get('card_index', '')) for j in jobs
                   if j.get('type') in ('discussion', 'activity')}
        if indices and all(i.isdigit() and 0 <= int(i) < 64 for i in indices):
            hint(id=task_id, indices=sorted({int(i) for i in indices}))
    observe = getattr(client, 'desktop_job_scope', None)
    if observe and task_id:
        # Record what this chapter contains so a later continuation can keep
        # the same set of task points instead of adopting new ones. Jobs with no
        # stable identity are not recorded: they could never be matched safely.
        observe(id=task_id, jobs=[list(identity) for identity in map(job_identity, jobs)
                                  if identity is not None])
    title = point.get('title', point.get('id', ''))
    logger.info('章节「{}」检测到 {} 个待处理任务点。', title, len(jobs))
    # A chapter may contain several independent media. One unconfirmed job
    # must not hide or prevent the remaining jobs from being processed.
    outcome = ChapterResult.SUCCESS
    media_total = sum(j.get('type') in ('video', 'audio') for j in jobs)
    media_index = 0
    for index, job in enumerate(jobs):
        # A resumed run must obey pause/cancel between task points, not only
        # between chapters, so it never reports work it is not allowed to do.
        checkpoint(client)
        if job.get('type') in ('video', 'audio'):
            media_index += 1
            context = getattr(client, 'desktop_media_context', None)
            if context is not None:
                context.media_index, context.media_total = media_index, media_total
        name = job.get('name') or job.get('title') or job.get('jobid') or job['type']
        logger.info('章节「{}」任务点 {}/{}：{}', title, index + 1, len(jobs), name)
        try:
            result = process_job(client, course, job, info, speed)
        except Exception as exc:
            # Cancellation and failed session resumption derive from
            # BaseException and must still interrupt the whole run.
            logger.warning('任务点「{}」处理异常：{}: {}', name, type(exc).__name__, exc)
            result = StudyResult.ERROR
        from .discussion import DiscussionOutcome
        from .activity import ActivityOutcome
        if isinstance(result, (DiscussionOutcome, ActivityOutcome)):
            if result.verification:
                return ChapterResult.PENDING
            if result.task_status not in ('confirmed', 'not_applicable'):
                outcome = ChapterResult.PENDING
            continue
        if result == StudyResult.VERIFICATION_REQUIRED:
            logger.warning('会话需要人工验证，章节「{}」保留待完成；还有 {} 个任务点未执行。',
                           title, len(jobs) - index - 1)
            return ChapterResult.PENDING
        if result == StudyResult.PENDING:
            # Unknown submission or unverifiable state: report as pending, never
            # as a completed chapter.
            logger.warning('任务点「{}」需要人工核验，章节保留待完成。', name)
            outcome = ChapterResult.PENDING
            continue
        if result in (StudyResult.FORBIDDEN, StudyResult.TIMEOUT):
            logger.warning('任务点「{}」未获平台确认（{}），继续处理本章其他任务点。', name, result.name)
            outcome = ChapterResult.PENDING
        elif result.is_failure():
            logger.warning('任务点「{}」处理失败，继续处理本章其他任务点。', name)
            if outcome != ChapterResult.PENDING:
                outcome = ChapterResult.ERROR
    return outcome


def increase_learning_count_for_course(client, course, config):
    points = client.get_course_point(course["courseId"], course["clazzId"], course["cpi"])["points"]
    client.increase_chapter_learning_count(course, points, config["target_count"])
