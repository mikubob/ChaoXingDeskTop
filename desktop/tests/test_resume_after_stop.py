"""Issue #5: continue an explicitly stopped run, offline and write-free.

Covers the exit gate, the confirmation gate, scope preservation (chapters and
observed task points), completed-task skipping, unknown/accepted quiz attempts
without a second POST, account/course/cpi invalidation, duplicate triggers and
pause semantics. No real account, course or platform request is used.
"""
import io
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[1] / 'vendor/chaoxing')]
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from api.base import SessionManager, StudyResult
from chaoxing_desktop.config import Redactor, defaults, to_payload
from chaoxing_desktop.control import Cancelled, RunControl
from chaoxing_desktop.qml_backend import Backend
from chaoxing_desktop.quiz_attempts import QuizAttemptStore, QuizGuard
from chaoxing_desktop.runtime import ChapterResult
from chaoxing_desktop.task_completion import TaskCompletionCache
from chaoxing_desktop.worker import Channel, real


def channel():
    return Channel(io.StringIO(), io.StringIO(), lambda text: text)


class QuizAttemptStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.course = dict(courseId='c', clazzId='l', cpi='p')
        self.job = dict(jobid='work-1', enc='fixture')
        self.info = dict(knowledgeid='point-1', ktoken='fixture', cpi='p')
        self.store = QuizAttemptStore(self.tmp.name, 'a' * 32, 'uid')

    def test_begin_is_durable_cross_worker_and_refuses_a_second_attempt(self):
        self.assertTrue(self.store.begin(self.course, '1', 'run-1'))
        restarted = QuizAttemptStore(self.tmp.name, 'a' * 32, 'uid')
        self.assertEqual(restarted.read(self.course)['1']['state'], 'intent')
        # The lock sees the existing intent: no new record, no permission to POST.
        self.assertFalse(restarted.begin(self.course, '1', 'run-1'))

    def test_accept_keeps_proof_and_only_rejection_clears(self):
        self.store.begin(self.course, '1', 'run-1')
        self.store.accept(self.course, '1', 'run-1')
        self.assertEqual(self.store.read(self.course)['1']['state'], 'accepted')
        # Accepted must survive a new worker so a continuation never resends.
        self.assertEqual(QuizAttemptStore(self.tmp.name, 'a' * 32, 'uid')
                         .read(self.course)['1']['state'], 'accepted')
        self.assertFalse(self.store.begin(self.course, '1', 'run-1'))
        self.assertTrue(self.store.begin(self.course, '1', 'run-1', redo=True))
        self.store.reject(self.course, '1', 'run-1')
        self.assertEqual(self.store.read(self.course)['1']['state'], 'accepted')

    def test_damaged_record_blocks_and_is_never_overwritten(self):
        self.store.begin(self.course, '1', 'run-1')
        self.store.path(self.course).write_bytes(b'broken')
        with self.assertRaises(ValueError):
            self.store.read(self.course)
        with self.assertRaises(ValueError):
            self.store.begin(self.course, '2', 'run-1')
        self.assertEqual(self.store.path(self.course).read_bytes(), b'broken')

    def test_incomplete_identity_is_refused(self):
        for point, job in (('', 'work-1'), ('point-1', '')):
            with self.subTest(point=point, job=job), self.assertRaises(ValueError):
                self.store.begin(self.course, point, job)

    def test_records_are_isolated_by_account_user_course_and_class(self):
        self.store.begin(self.course, '1', 'run-1')
        for course in (dict(self.course, courseId='other'), dict(self.course, clazzId='other')):
            self.assertEqual(self.store.read(course), {})
        self.assertEqual(QuizAttemptStore(self.tmp.name, 'b' * 32, 'uid').read(self.course), {})
        self.assertEqual(QuizAttemptStore(self.tmp.name, 'a' * 32, 'other').read(self.course), {})

    def test_capacity_never_evicts_an_unresolved_record(self):
        from chaoxing_desktop import quiz_attempts
        original = quiz_attempts.MAX_RECORDS
        quiz_attempts.MAX_RECORDS = 2
        try:
            for i in range(2):
                self.assertTrue(self.store.begin(self.course, f'p{i}', 'work-1'))
            with self.assertRaises(ValueError):
                self.store.begin(self.course, 'extra', 'work-1')
            self.assertEqual(len(self.store.read(self.course)), 2)
        finally:
            quiz_attempts.MAX_RECORDS = original


class QuizAdapterTests(unittest.TestCase):
    """The real adapter must refuse a second POST across workers."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.course = dict(courseId='c', clazzId='class', cpi='p')
        self.job = dict(jobid='work-1', enc='fixture')
        self.info = dict(knowledgeid='point-1', ktoken='fixture', cpi='p')
        self.store = QuizAttemptStore(self.tmp.name, 'a' * 32, 'uid')
        self.question = dict(type='single', id='9', title='Synthetic', answerField={'answertype9': '0'},
                             options='A. one\nB. two')

    def build(self, guard, outcome=None):
        from api.base import Chaoxing
        from chaoxing_desktop.answers import AnswerRouter
        router = AnswerRouter(to_payload(defaults()), Mock())
        router.SUBMIT = True
        router.DISABLE = False
        # Answer through the real router contract so the form really submits.
        router.query_all = Mock(side_effect=lambda rows, query_delay=0: self.fill(rows))
        client = Chaoxing(tiku=router)
        client.desktop_quiz_guard = guard
        session = Mock()
        session.get.return_value = SimpleNamespace(status_code=200, text='exercise')
        if outcome is None:
            session.post.return_value = Mock(status_code=200, json=Mock(return_value=dict(status=True, msg='ok')))
        else:
            session.post.return_value = outcome
        patches = [patch.object(SessionManager, 'get_session', return_value=session),
                   patch('api.base.decode_questions_info',
                         side_effect=lambda _: dict(questions=[dict(self.question,
                                                                    answerField=dict(self.question['answerField']))])),
                   patch.object(client, '_check_work_result',
                                return_value=dict(score=100, all_correct=True))]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return client, session

    @staticmethod
    def fill(rows):
        for row in rows:
            row['desktop_answer_letters'] = 'A'
            row['desktop_answer_source'] = 'cover'
        return ['one' for _ in rows]

    def test_unresolved_intent_never_posts_twice_across_two_stops(self):
        """The exact reported bypass: unknown intent, stop, resume, stop, resume."""
        self.store.begin(self.course, '1', 'run-1')
        for round_index in range(3):
            client, session = self.build(QuizGuard(QuizAttemptStore(self.tmp.name, 'a' * 32, 'uid'), 'run-1'))
            result = client.study_work(self.course, self.job, self.info)
            self.assertEqual(session.post.call_count, 0, f'round {round_index} must not POST')
            self.assertEqual(result, StudyResult.PENDING)
        self.assertEqual(self.store.read(self.course)['1']['state'], 'intent')

    def test_begin_false_means_zero_post(self):
        self.store.begin(self.course, '1', 'run-1')
        guard = QuizGuard(self.store, 'run-1')
        client, session = self.build(guard)
        # Even bypassing unresolved() must not send: begin() refuses the write.
        with patch.object(QuizGuard, 'unresolved', return_value=''):
            with self.assertRaises(ValueError):
                client.study_work(self.course, self.job, self.info)
        self.assertEqual(session.post.call_count, 0)

    def test_submission_transport_disables_adapter_retries(self):
        from api.base import WORK_SUBMIT_URL
        session = SessionManager.get_session()
        self.assertEqual(session.get_adapter(WORK_SUBMIT_URL).max_retries.total, 0)
        self.assertEqual(session.get_adapter('https://mooc1.chaoxing.com/').max_retries.total, 1)

    def test_accepted_attempt_is_skipped_after_a_stop_between_response_and_cache(self):
        """POST accepted, progress/cache delayed, then stop and resume."""
        first, first_session = self.build(QuizGuard(QuizAttemptStore(self.tmp.name, 'a' * 32, 'uid'), 'run-1'))
        self.assertEqual(first.study_work(self.course, self.job, self.info), StudyResult.SUCCESS)
        self.assertEqual(first_session.post.call_count, 1)
        # The stop happened before the completion cache recorded anything.
        self.assertEqual(self.store.read(self.course)['1']['state'], 'accepted')
        second, second_session = self.build(QuizGuard(QuizAttemptStore(self.tmp.name, 'a' * 32, 'uid'), 'run-1'))
        self.assertEqual(second.study_work(self.course, self.job, self.info), StudyResult.PENDING)
        self.assertEqual(second_session.post.call_count, 0)
        self.assertEqual(self.store.read(self.course)['1']['state'], 'accepted')

    def test_explicit_same_run_redo_is_preserved(self):
        """The configured same-run low-score redo must keep working."""
        from api.base import Chaoxing
        from chaoxing_desktop.answers import AnswerRouter
        router = AnswerRouter(to_payload(defaults()), Mock())
        router.SUBMIT = True
        router.DISABLE = False
        router.query_all = Mock(side_effect=lambda rows, query_delay=0: self.fill(rows))
        client = Chaoxing(tiku=router)
        client.desktop_quiz_guard = QuizGuard(QuizAttemptStore(self.tmp.name, 'a' * 32, 'uid'), 'run-1')
        client.kwargs.update(work_redo_enabled=True, work_max_retries=1)
        session = Mock()
        session.get.return_value = SimpleNamespace(status_code=200, text='exercise')
        session.post.return_value = Mock(status_code=200, json=Mock(return_value=dict(status=True)))
        with patch.object(SessionManager, 'get_session', return_value=session), \
                patch('api.base.decode_questions_info',
                      side_effect=lambda text: dict(questions=[dict(self.question)])), \
                patch.object(client, '_check_work_result', return_value=dict(score=0, all_correct=False)), \
                patch('api.base.time.sleep'):
            client.study_work(self.course, self.job, self.info)
        self.assertEqual(session.post.call_count, 2, 'explicit redo submits twice in one run')

    def test_save_only_pass_never_creates_a_record(self):
        from api.base import Chaoxing
        from chaoxing_desktop.answers import AnswerRouter
        router = AnswerRouter(to_payload(defaults()), Mock())
        router.SUBMIT = False
        router.DISABLE = False
        router.query_all = Mock(side_effect=lambda rows, query_delay=0: self.fill(rows))
        client = Chaoxing(tiku=router)
        client.desktop_quiz_guard = QuizGuard(self.store, 'run-1')
        session = Mock()
        session.get.return_value = SimpleNamespace(status_code=200, text='exercise')
        session.post.return_value = Mock(status_code=200, json=Mock(return_value=dict(status=True)))
        with patch.object(SessionManager, 'get_session', return_value=session), \
                patch('api.base.decode_questions_info', return_value=dict(questions=[dict(self.question)])):
            client.study_work(self.course, self.job, self.info)
        self.assertEqual(session.post.call_args.kwargs['data']['pyFlag'], '1')
        self.assertEqual(self.store.read(self.course), {})

    def test_transport_failure_keeps_unknown_and_never_turns_into_a_rejection(self):
        guard = QuizGuard(self.store, 'run-1')
        client, session = self.build(guard)
        session.post.side_effect = TimeoutError('connection reset after send')
        with self.assertRaises(TimeoutError):
            client.study_work(self.course, self.job, self.info)
        self.assertEqual(session.post.call_count, 1)
        # A hard kill is not an explicit rejection: the record stays unresolved.
        self.assertEqual(self.store.read(self.course)['1']['state'], 'intent')
        again, again_session = self.build(QuizGuard(QuizAttemptStore(self.tmp.name, 'a' * 32, 'uid'), 'run-1'))
        self.assertEqual(again.study_work(self.course, self.job, self.info), StudyResult.PENDING)
        self.assertEqual(again_session.post.call_count, 0)

    def test_unreadable_and_server_error_keep_the_intent(self):
        for index, (status, body) in enumerate(((500, 'gateway'), (200, '{"status":"true"}'))):
            with self.subTest(status=status):
                self.course = dict(courseId='c', clazzId=f'class-{index}', cpi='p')
                self.store = QuizAttemptStore(self.tmp.name, 'a' * 32, 'uid')
                guard = QuizGuard(self.store, 'run-1')
                client, session = self.build(
                    guard, outcome=Mock(status_code=status, text=body,
                                        json=Mock(side_effect=ValueError)))
                self.assertEqual(client.study_work(self.course, self.job, self.info), StudyResult.PENDING)
                self.assertEqual(session.post.call_count, 1)
                self.assertEqual(self.store.read(self.course)['1']['state'], 'intent')
                # The next worker must not resend this unresolved attempt.
                again, again_session = self.build(QuizGuard(QuizAttemptStore(self.tmp.name, 'a' * 32, 'uid'), 'run-1'))
                self.assertEqual(again.study_work(self.course, self.job, self.info), StudyResult.PENDING)
                self.assertEqual(again_session.post.call_count, 0)

    def test_explicit_rejection_clears_and_fails(self):
        guard = QuizGuard(self.store, 'run-1')
        client, session = self.build(
            guard, outcome=Mock(status_code=200, json=Mock(return_value=dict(status=False, msg='拒绝'))))
        self.assertEqual(client.study_work(self.course, self.job, self.info), StudyResult.ERROR)
        self.assertEqual(session.post.call_count, 1)
        self.assertEqual(self.store.read(self.course), {})

    def test_unknown_work_is_blocked_even_when_referenced_by_another_chapter(self):
        guard = QuizGuard(self.store, 'run-1')
        client, session = self.build(guard)
        other = dict(self.info, knowledgeid='point-2')
        session.post.side_effect = TimeoutError('response lost')
        form = dict(workId='1', questions=[dict(self.question)])
        with patch('api.base.decode_questions_info', return_value=form), self.assertRaises(TimeoutError):
            client.study_work(self.course, self.job, self.info)
        new_run, new_session = self.build(QuizGuard(self.store, 'run-2'))
        form = dict(workId='work-1', questions=[dict(self.question)])
        with patch('api.base.decode_questions_info', return_value=form):
            self.assertEqual(new_run.study_work(self.course, dict(self.job, jobid='alias'), other), StudyResult.PENDING)
        self.assertEqual(session.post.call_count, 1)
        new_session.post.assert_not_called()

    def test_accepted_old_run_leaves_independent_start_to_platform(self):
        self.store.begin(self.course, '1', 'run-1')
        self.store.accept(self.course, '1', 'run-1')
        client, session = self.build(QuizGuard(self.store, 'run-2'))
        self.assertEqual(client.study_work(self.course, self.job, self.info), StudyResult.SUCCESS)
        session.post.assert_called_once()
        self.assertEqual(self.store.read(self.course)['1']['lineage'], 'run-2')

    def test_lost_redo_response_blocks_new_worker(self):
        from requests.exceptions import Timeout
        client, session = self.build(QuizGuard(self.store, 'run-1'))
        client.kwargs.update(work_redo_enabled=True, work_max_retries=1)
        session.post.side_effect = [session.post.return_value, Timeout('redo response lost')]
        with patch.object(client, '_check_work_result', return_value=dict(score=0, all_correct=False)), \
                patch('api.base.time.sleep'):
            self.assertEqual(client.study_work(self.course, self.job, self.info), StudyResult.PENDING)
        self.assertEqual(session.post.call_count, 2)
        self.assertEqual(self.store.read(self.course)['1']['state'], 'intent')
        resumed, new_session = self.build(QuizGuard(self.store, 'run-1'))
        self.assertEqual(resumed.study_work(self.course, self.job, self.info), StudyResult.PENDING)
        new_session.post.assert_not_called()


class ResumeScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.course = dict(courseId='c', clazzId='class', cpi='p', title='大学体育')
        self.account = 'a' * 32
        self.cache = TaskCompletionCache(self.tmp.name, self.account, 'uid')
        self.points = [dict(id='1', title='2.1 运动安全', has_finished=False),
                       dict(id='2', title='2.2 科学运动', has_finished=False),
                       dict(id='3', title='2.3 新发现章节', has_finished=False)]

    def row(self, point_id, index, jobs=None, scanned=True, completed=False):
        return dict(taskId=f'c:class:{point_id}:{index}', courseId='c', clazzId='class',
                    pointId=point_id, scanned=scanned, completed=completed,
                    jobs=[list(job) for job in (jobs or [])])

    def payload(self, tasks, courses=None, uid='uid', account=None):
        return dict(tasks=tasks,
                    courses=[dict(courseId='c', clazzId='class', cpi='p', title='大学体育')]
                    if courses is None else courses,
                    account=account or self.account, uid=uid)

    def run_worker(self, resume=None, points=None, process=None, add_learning_count=False,
                   ignore_completion_cache=False, login_uid='uid'):
        client = Mock(spec_set=['login', 'get_course_list', 'get_course_point', 'desktop_quiz_guard'])
        client.login.return_value = dict(status=True)
        client.get_course_list.return_value = [self.course]
        client.get_course_point.return_value = dict(points=points or self.points)
        core = SimpleNamespace(create_client=lambda *a, **kw: client,
                               process_chapter=process or Mock(return_value=ChapterResult.SUCCESS),
                               ChapterResult=ChapterResult, increase_learning_count_for_course=Mock())
        cookie = Path(self.tmp.name) / 'cookie'
        cookie.write_bytes(b'fixture')
        store = SimpleNamespace(active=self.account, get=lambda _: dict(username='fixture', uid='uid'),
                                cookie_path=lambda _: cookie)
        config = to_payload(defaults())
        config['common'].update(username='fixture', password='', use_cookies=True, jobs=1,
                                add_learning_count=add_learning_count,
                                ignore_completion_cache=ignore_completion_cache)
        output = io.StringIO()
        command = dict(action='run', config=config, data_dir=self.tmp.name, account_id=self.account,
                       account_uid='uid', selected=[['c', 'class']])
        if resume is not None:
            command['resume'] = resume
        session = SimpleNamespace(cookies=SimpleNamespace(get_dict=lambda: dict(_uid=login_uid)))
        with patch('chaoxing_desktop.worker.load_core', return_value=core), \
                patch('chaoxing_desktop.accounts.AccountStore', return_value=store), \
                patch('api.cookies.configure_account'), patch('api.cookies.save_cookies'), \
                patch.object(SessionManager, 'get_session', return_value=session):
            real(Channel(output, io.StringIO(), Redactor({})), command)
        return core, [json.loads(line) for line in output.getvalue().splitlines()]

    def test_resume_keeps_scope_skips_completed_and_never_widens(self):
        self.cache.save(self.course, self.points[0])
        core, rows = self.run_worker(resume=self.payload([self.row('1', 0), self.row('2', 1)]))
        processed = [call.args[2]['id'] for call in core.process_chapter.call_args_list]
        self.assertEqual(processed, ['2'], 'completed chapter 1 and new chapter 3 stay out')
        task_rows = next(row['tasks'] for row in rows if row['type'] == 'tasks')
        self.assertEqual([row['pointId'] for row in task_rows], ['1', '2'])

    def test_task_points_added_to_a_scanned_chapter_are_not_processed(self):
        seen = []

        def process(client, course, point, speed, task_id, allowed_jobs=None):
            seen.append((point['id'], allowed_jobs))
            return ChapterResult.SUCCESS

        self.run_worker(resume=self.payload([self.row('1', 0, jobs=[['video', 'v-old', '']])]),
                        process=process)
        self.assertEqual(seen, [('1', [('video', 'v-old', '')])])

    def test_scanned_chapter_keeps_job_identity_even_when_resource_is_reused(self):
        from chaoxing_desktop import runtime
        original = dict(type='video', jobid='old', objectid='shared-video')
        added = dict(original, jobid='new')
        unidentified = dict(type='video')
        client = SimpleNamespace(get_job_list=lambda *_: ([added, unidentified, original], {}))
        with patch.object(runtime, 'process_job', return_value=StudyResult.SUCCESS) as process:
            runtime.process_chapter(client, self.course, self.points[0], 1,
                                    allowed_jobs=[runtime.job_identity(original)])
        process.assert_called_once_with(client, self.course, original, {}, 1)

    def test_unscanned_chapter_discovers_its_task_points(self):
        seen = []

        def process(client, course, point, speed, task_id, allowed_jobs=None):
            seen.append((point['id'], allowed_jobs))
            return ChapterResult.SUCCESS

        self.run_worker(resume=self.payload([self.row('1', 0, jobs=[], scanned=False)]), process=process)
        self.assertEqual(seen, [('1', None)])

    def test_confirmed_snapshot_is_used_when_cache_and_platform_lag(self):
        core, _ = self.run_worker(resume=self.payload([self.row('1', 0, completed=True), self.row('2', 1)]))
        self.assertEqual([call.args[2]['id'] for call in core.process_chapter.call_args_list], ['2'])

    def test_stop_before_the_task_list_keeps_course_scope_and_prepares_again(self):
        """A stop during login/preparation must be resumable, not silently empty."""
        core, rows = self.run_worker(resume=self.payload([]))
        # No chapter whitelist existed yet, so the whole course is prepared as
        # a normal run, but only that frozen course.
        self.assertEqual(core.process_chapter.call_count, 3)
        task_rows = next(row['tasks'] for row in rows if row['type'] == 'tasks')
        self.assertEqual([row['pointId'] for row in task_rows], ['1', '2', '3'])

    def test_second_stop_continues_to_inherit_the_narrowed_whitelist(self):
        """Stopping a continuation must not widen it back to the whole course."""
        first = self.payload([self.row('2', 1)])
        core, rows = self.run_worker(resume=first)
        self.assertEqual([c.args[2]['id'] for c in core.process_chapter.call_args_list], ['2'])
        # The resumed run reports only the retained chapter, which is what the
        # next stop would preserve.
        task_rows = next(row['tasks'] for row in rows if row['type'] == 'tasks')
        self.assertEqual([row['pointId'] for row in task_rows], ['2'])

    def test_missing_chapter_or_other_course_is_rejected_before_any_task_starts(self):
        process = Mock(return_value=ChapterResult.SUCCESS)
        cases = [
            self.payload([self.row('missing', 0)]),
            self.payload([self.row('1', 0)],
                         courses=[dict(courseId='other', clazzId='class', cpi='p', title='x')]),
            self.payload([dict(self.row('1', 0), taskId='c:class:9:0')]),
            self.payload([dict(self.row('1', 0), scanned='yes')]),
            self.payload([self.row('1', 0)], courses=[]),
            self.payload([self.row('1', 0)], uid='someone-else'),
            self.payload([self.row('1', 0)], account='b' * 32),
            dict(tasks=[], account=self.account),
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                self.run_worker(resume=case, process=process)
        process.assert_not_called()

    def test_changed_class_context_is_rejected(self):
        process = Mock(return_value=ChapterResult.SUCCESS)
        with self.assertRaises(ValueError):
            self.run_worker(resume=self.payload(
                [self.row('1', 0)],
                courses=[dict(courseId='c', clazzId='class', cpi='other', title='大学体育')]),
                process=process)
        process.assert_not_called()

    def test_platform_identity_change_is_rejected(self):
        process = Mock(return_value=ChapterResult.SUCCESS)
        with self.assertRaises(ValueError):
            self.run_worker(resume=self.payload([self.row('1', 0)]), process=process,
                            login_uid='different-user')
        process.assert_not_called()

    def test_continuation_always_uses_the_completion_cache(self):
        self.cache.save(self.course, self.points[0])
        core, _ = self.run_worker(resume=self.payload([self.row('1', 0), self.row('2', 1)]),
                                  ignore_completion_cache=True)
        core.process_chapter.assert_called_once()
        self.assertEqual(core.process_chapter.call_args.args[2]['id'], '2')

    def test_continuation_never_runs_the_course_wide_learning_count_write(self):
        core, rows = self.run_worker(resume=self.payload([self.row('1', 0)]), add_learning_count=True)
        core.increase_learning_count_for_course.assert_not_called()
        self.assertTrue(any('学习次数' in json.dumps(row, ensure_ascii=False)
                            for row in rows if row['type'] == 'log'))
        core, _ = self.run_worker(add_learning_count=True)
        core.increase_learning_count_for_course.assert_called_once()

    def test_pause_and_cancel_still_stop_a_resumed_run(self):
        control = RunControl(lambda kind, **values: None)
        calls = []

        def process(client, course, point, speed, task_id, allowed_jobs=None):
            calls.append(point['id'])
            control.command({'command': 'pause'})
            threading.Timer(.2, lambda: control.command({'command': 'cancel'})).start()
            return ChapterResult.SUCCESS

        client = Mock(spec_set=['login', 'get_course_list', 'get_course_point', 'desktop_quiz_guard'])
        client.login.return_value = dict(status=True)
        client.get_course_list.return_value = [self.course]
        client.get_course_point.return_value = dict(points=self.points)
        core = SimpleNamespace(create_client=lambda *a, **kw: client, process_chapter=process,
                               ChapterResult=ChapterResult, increase_learning_count_for_course=Mock())
        cookie = Path(self.tmp.name) / 'cookie'
        cookie.write_bytes(b'fixture')
        store = SimpleNamespace(active=self.account, get=lambda _: dict(username='fixture', uid='uid'),
                                cookie_path=lambda _: cookie)
        config = to_payload(defaults())
        config['common'].update(username='fixture', password='', use_cookies=True, jobs=1)
        ch = Channel(io.StringIO(), io.StringIO(), Redactor({}))
        ch.control = control
        command = dict(action='run', config=config, data_dir=self.tmp.name, account_id=self.account,
                       account_uid='uid', selected=[['c', 'class']],
                       resume=self.payload([self.row('1', 0), self.row('2', 1)]))
        session = SimpleNamespace(cookies=SimpleNamespace(get_dict=lambda: dict(_uid='uid')))
        with patch('chaoxing_desktop.worker.load_core', return_value=core), \
                patch('chaoxing_desktop.accounts.AccountStore', return_value=store), \
                patch('api.cookies.configure_account'), patch('api.cookies.save_cookies'), \
                patch.object(SessionManager, 'get_session', return_value=session):
            with self.assertRaises(Cancelled):
                real(ch, command)
        self.assertEqual(len(calls), 1, 'paused work must not keep processing chapters')


class ResumeUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, CHAOXING_DESKTOP_DATA=self.tmp.name)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.network = patch('requests.sessions.Session.request', side_effect=AssertionError('offline only'))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.b = Backend()
        self.addCleanup(self.cleanup_backend)
        process = patch.object(self.b.controller.process, 'start')
        process.start()
        self.addCleanup(process.stop)
        self.cookie = Path(self.tmp.name) / 'fixture-cookie'
        self.cookie.write_bytes(b'fixture')
        self.b.config['common'].update(username='fixture', password='', use_cookies='true')
        self.b.account_store = SimpleNamespace(active='a' * 32, rows=[],
                                               get=lambda _: dict(uid='uid', label='学习账号'),
                                               cookie_path=lambda _: self.cookie)
        self.b.course_identity = self.b.identity()
        self.b.courses = [dict(courseId='c', clazzId='l', cpi='p', title='合成课程')]
        self.b.selected = {('c', 'l')}
        self.b.operation = 'run'

    def cleanup_backend(self):
        self.b.controller.active = False
        self.b.discardAndClose()
        self.b.deleteLater()
        self.app.processEvents()

    def launch(self):
        """Simulate the real start path so the frozen launch scope exists."""
        self.b._begin('run', False, [['c', 'l']])
        self.b._task_model.reset([
            dict(id='c:l:p1:0', title='第一章', course='合成课程', status='success',
                 courseId='c', clazzId='l', pointId='p1'),
            dict(id='c:l:p2:1', title='第二章', course='合成课程', status='stopped',
                 courseId='c', clazzId='l', pointId='p2')])
        self.b._job_scope = {'c:l:p1:0': [['video', 'v1', '']]}
        self.b.controller.active = True
        self.b.controller.stopping = True
        self.b.controller.active = False   # Controller._finish clears this first
        self.b.on_finished(0)
        self.b.controller.stopping = False

    def test_entry_appears_only_after_a_confirmed_explicit_stop(self):
        self.assertFalse(self.b.resumeState['available'])
        self.assertFalse(self.b.summary['resumeScoped'])
        self.launch()
        self.assertTrue(self.b.summary['resumeScoped'])
        state = self.b.resumeState
        self.assertTrue(state['available'])
        self.assertTrue(state['enabled'])
        self.assertTrue(state['clickable'])
        self.assertFalse(state['needsConfirmation'])
        self.assertIn('第二章', state['scope'])
        self.assertEqual(self.b._resume['tasks'][0]['jobs'], [['video', 'v1', '']])
        # The full class context is retained, not just course/class ids.
        self.assertEqual(self.b._resume['courses'],
                         [dict(courseId='c', clazzId='l', cpi='p', title='合成课程')])

    def test_clickable_when_confirmation_is_required(self):
        """The reported blocker: the button must stay clickable to open the gate."""
        self.launch()
        self.b.config['tiku']['submit'] = 'true'
        reason, clickable, needs = self.b.resume_blocker()
        self.assertTrue(clickable, reason)
        self.assertTrue(needs)
        state = self.b.resumeState
        self.assertTrue(state['clickable'])
        self.assertTrue(state['enabled'], 'UI clickability must follow clickable, not readiness')
        requested = []
        self.b.startConfirmationRequested.connect(requested.append)
        with patch.object(self.b.controller, 'start') as start:
            self.b.resumeTasks()
            start.assert_not_called()
        self.assertEqual(len(requested), 1)

    def test_crash_or_finished_run_leaves_nothing_to_continue(self):
        for code in (0, 7):
            with self.subTest(code=code):
                self.b._resume = None
                self.b.controller.active = True
                self.b.controller.stopping = False
                self.b.controller.active = False
                self.b.on_finished(code)
                self.assertFalse(self.b.resumeState['available'])

    def test_stopping_is_not_clickable_until_finished(self):
        self.launch()
        self.b.controller.active = True
        self.b.controller.stopping = True
        self.assertFalse(self.b.resume_blocker()[1])
        self.assertIn('停止中', self.b.resume_blocker()[0])
        with patch.object(self.b.controller, 'start') as start:
            self.b.resumeTasks()
            start.assert_not_called()
        # The worker has now exited, but no finished signal was processed.
        self.b.controller.active = False
        self.b.controller.stopping = False
        self.b._controller_exit = None
        self.assertFalse(self.b.resume_blocker()[1])
        self.assertIn('退出', self.b.resume_blocker()[0])
        self.b._controller_exit = 0
        self.assertTrue(self.b.resume_blocker()[1])

    def test_confirmation_binds_the_full_policy_not_only_write_switches(self):
        self.launch()
        self.b.config['tiku']['submit'] = 'true'
        self.b.resumeTasks()
        pending = self.b._start_confirmation
        self.assertIsNotNone(pending)
        # A change that leaves write booleans identical must still invalidate it.
        self.b.config['discussion']['reply_level'] = 'detailed'
        with patch.object(self.b.controller, 'start') as start:
            self.b.confirmStartTasks()
            start.assert_not_called()
        self.assertIn('已变化', self.b.message)
        # Re-requesting and confirming the current policy starts exactly once.
        self.b.resumeTasks()
        with patch.object(self.b.controller, 'start') as start:
            self.b.confirmStartTasks()
            self.b.confirmStartTasks()
        self.assertEqual(start.call_count, 1)
        command = start.call_args.args[0]
        self.assertEqual([row['pointId'] for row in command['resume']['tasks']], ['p1', 'p2'])
        self.assertEqual(command['resume']['courses'][0]['cpi'], 'p')
        self.assertEqual(command['resume']['uid'], 'uid')

    def test_repeated_resume_clicks_do_not_stack_confirmations(self):
        self.launch()
        self.b.config['tiku']['submit'] = 'true'
        requested = []
        self.b.startConfirmationRequested.connect(requested.append)
        self.b.resumeTasks()
        self.b.resumeTasks()
        self.b.resumeTasks()
        self.assertEqual(len(requested), 1)

    def test_save_only_stop_resumes_without_confirmation(self):
        self.launch()
        requested = []
        self.b.startConfirmationRequested.connect(requested.append)
        with patch.object(self.b.controller, 'start') as start:
            self.b.resumeTasks()
        self.assertEqual(requested, [])
        self.assertEqual(start.call_count, 1)

    def test_repeated_clicks_start_at_most_once(self):
        self.launch()
        with patch.object(self.b.controller, 'start') as start:
            self.b.resumeTasks()
            self.b.resumeTasks()
            self.b.resumeTasks()
        self.assertEqual(start.call_count, 1)

    def test_account_change_and_course_loss_block_the_entry(self):
        self.launch()
        original = self.b.config['common']['username']
        self.b.config['common']['username'] = 'other-account'
        self.assertFalse(self.b.resume_blocker()[1])
        self.assertIn('账号', self.b.resume_blocker()[0])
        self.b.config['common']['username'] = original
        self.b.courses = []
        self.assertFalse(self.b.resume_blocker()[1])
        self.assertIn('课程', self.b.resume_blocker()[0])

    def test_cpi_and_session_changes_invalidate(self):
        self.launch()
        self.b.courses = [dict(courseId='c', clazzId='l', cpi='other', title='合成课程')]
        self.assertFalse(self.b.resume_blocker()[1])
        self.assertIn('cpi', self.b.resume_blocker()[0])
        self.b.courses = [dict(courseId='c', clazzId='l', cpi='p', title='合成课程')]
        self.cookie.write_bytes(b'different-session')
        os.utime(self.cookie, (self.cookie.stat().st_atime, self.cookie.stat().st_mtime + 60))
        self.assertFalse(self.b.resume_blocker()[1])
        self.assertIn('会话', self.b.resume_blocker()[0])

    def test_stop_during_preparation_keeps_course_scope(self):
        """No task list yet: the frozen launch courses must remain resumable."""
        self.b._begin('run', False, [['c', 'l']])   # stop before any task event
        self.b.operation = 'run'
        self.b.controller.active = True
        self.b.controller.stopping = True
        self.b.controller.active = False
        self.b.on_finished(0)
        self.b.controller.stopping = False
        state = self.b.resumeState
        self.assertTrue(state['available'], state['reason'])
        self.assertEqual(self.b._resume['tasks'], [])
        self.assertEqual(self.b._resume['courses'][0]['cpi'], 'p')
        with patch.object(self.b.controller, 'start') as start:
            self.b.resumeTasks()
        self.assertEqual(start.call_count, 1)
        self.assertEqual(start.call_args.args[0]['resume']['courses'][0]['cpi'], 'p')
        self.assertEqual(start.call_args.args[0]['resume']['tasks'], [])

    def test_temporary_failure_keeps_the_record_for_retry(self):
        self.launch()
        with patch.object(self.b.controller, 'start', side_effect=RuntimeError('后台暂时无法启动')):
            self.b.resumeTasks()
        # A failed launch must not consume the continuation.
        self.assertIsNotNone(self.b._resume)
        self.assertTrue(self.b.resumeState['available'])
        with patch.object(self.b.controller, 'start') as start:
            self.b.resumeTasks()
        self.assertEqual(start.call_count, 1)

    def test_async_preparation_failure_restores_snapshot_for_retry(self):
        self.launch()
        snapshot = self.b._resume
        self.b.resumeTasks()
        self.assertTrue(self.b.controller.active)
        self.assertIsNone(self.b._resume)
        self.b.controller.active = False
        self.b.on_finished(1)
        self.assertIs(self.b._resume, snapshot)
        self.assertTrue(self.b.resumeState['enabled'])

    def test_second_stop_during_preparation_keeps_scope_and_lineage(self):
        self.launch()
        snapshot = self.b._resume
        self.b.resumeTasks()
        self.b.stop()
        self.b.controller.active = False
        self.b.on_finished(0)
        self.assertEqual(self.b._resume['tasks'], snapshot['tasks'])
        self.assertEqual(self.b._resume['lineage'], snapshot['lineage'])
        with patch.object(self.b.controller, 'start') as start:
            self.b.resumeTasks()
        command = start.call_args.args[0]
        self.assertEqual(command['lineage'], snapshot['lineage'])
        self.assertEqual(set(command['resume']['tasks'][0]),
                         {'taskId', 'courseId', 'clazzId', 'pointId', 'jobs', 'scanned', 'completed'})

    def test_reordered_unscanned_chapter_keeps_previous_job_scope(self):
        self.launch()
        self.b.resumeTasks()
        self.b.on_event(dict(type='tasks', tasks=[
            dict(id='c:l:p1:5', title='第一章', course='合成课程',
                 courseId='c', clazzId='l', pointId='p1'),
            dict(id='c:l:p2:6', title='第二章', course='合成课程',
                 courseId='c', clazzId='l', pointId='p2')]))
        self.b.stop()
        self.b.controller.active = False
        self.b.on_finished(0)
        task = self.b._resume['tasks'][0]
        self.assertEqual(task['taskId'], 'c:l:p1:5')
        self.assertTrue(task['scanned'])
        self.assertEqual(task['jobs'], [['video', 'v1', '']])

    def test_new_run_clears_the_previous_continuation(self):
        self.launch()
        self.b._begin('run', False, [['c', 'l']])
        self.assertIsNone(self.b._resume)
        self.assertFalse(self.b.summary['resumeScoped'])

    def test_demo_stop_offers_an_offline_continuation(self):
        """The offline demo must exercise the real stop/continue flow."""
        self.b.demo_mode = True
        self.b.courses = [dict(courseId='demo-101', clazzId='01', cpi='demo', title='演示课程')]
        self.b.selected = {('demo-101', '01')}
        self.b._begin('run', True, [['demo-101', '01']])
        self.b._task_model.reset([
            dict(id='demo-101:0', title='01 · 视频学习', course='演示课程', status='success',
                 courseId='demo-101', clazzId='01', pointId='0'),
            dict(id='demo-101:1', title='02 · 阅读材料', course='演示课程', status='stopped',
                 courseId='demo-101', clazzId='01', pointId='1')])
        self.b._job_scope = {'demo-101:0': [['video', 'demo-1', '']]}
        self.b.controller.active = True
        self.b.controller.stopping = True
        self.b.controller.active = False
        self.b.on_finished(0)
        self.b.controller.stopping = False
        state = self.b.resumeState
        self.assertTrue(state['available'], state['reason'])
        self.assertTrue(state['enabled'])
        self.assertEqual(state['account'], '离线演示')
        # A continuation stays offline: no cookie/session validation is needed.
        with patch.object(self.b.controller, 'start') as start:
            self.b.resumeTasks()
        self.assertEqual(start.call_count, 1)
        command = start.call_args.args[0]
        self.assertTrue(command['demo'])
        self.assertEqual([row['pointId'] for row in command['resume']['tasks']], ['0', '1'])
        self.assertEqual(command['resume']['courses'][0]['cpi'], 'demo')


class RealProcessStopTests(unittest.TestCase):
    """Real transport + real demo worker: an actual stop and continue."""

    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        (self.root / 'build').mkdir(exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix='chaoxing-resume-test-', dir=self.root / 'build')
        self.process = subprocess.Popen(
            [sys.executable, '-u', str(self.root / 'run.py'), '--bridge'],
            cwd=self.root, env=dict(os.environ, CHAOXING_DESKTOP_DATA=self.directory.name,
                                    PYTHONIOENCODING='utf-8'),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.messages = queue.Queue()
        self.state = {}
        self.sequence = 0

        def read():
            for line in self.process.stdout:
                self.messages.put(json.loads(line))
            self.messages.put(dict(type='eof'))

        threading.Thread(target=read, daemon=True).start()
        self.wait(lambda m: m['type'] == 'ready')
        self.wait(lambda m: m['type'] == 'state')

    def tearDown(self):
        try:
            self.process.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.process.stdout.close()
        self.process.stderr.close()
        self.directory.cleanup()

    def wait(self, predicate, timeout=30):
        if predicate({'type': 'cached'}):
            return {'type': 'cached'}
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                message = self.messages.get(timeout=max(.01, deadline - time.monotonic()))
            except queue.Empty:
                self.fail('transport timed out')
            if message['type'] == 'eof':
                self.fail('backend exited unexpectedly')
            if message['type'] in ('ready', 'state'):
                self.state.update(message['data'])
            if predicate(message):
                return message
        self.fail('transport timed out')

    def call(self, method, *args):
        self.sequence += 1
        self.process.stdin.write((json.dumps(dict(id=self.sequence, method=method, args=args,
                                                  path=''), ensure_ascii=False) + '\n').encode())
        self.process.stdin.flush()
        return self.wait(lambda m: m['type'] == 'response' and m['id'] == self.sequence)

    def test_demo_stop_then_continue_reports_progress(self):
        """A real stop, a real continuation, and real progress events."""
        self.call('loadDemo')
        self.wait(lambda _: self.state['summary']['coursesReady'] and not self.state['summary']['busy'])
        self.call('selectVisible', True)
        self.call('startTasks')
        self.wait(lambda m: m['type'] == 'state' and self.state['summary']['busy'], timeout=10)
        self.assertFalse(self.state['summary']['resumeScoped'])
        # Wait until the worker has reported chapters and made progress.
        self.wait(lambda m: m['type'] == 'state'
                  and self.state['summary'].get('total', 0) > 0
                  and self.state['summary'].get('success', 0) > 0, timeout=30)
        self.call('stop')
        self.wait(lambda m: m['type'] == 'state' and not self.state['summary']['busy'], timeout=30)
        # The explicit stop left a real, clickable continuation.
        self.assertTrue(self.state['summary']['resumeScoped'])
        resume = self.state['summary']['resume']
        self.assertTrue(resume['available'], resume['reason'])
        self.assertTrue(resume['clickable'], resume['reason'])
        self.assertFalse(resume['needsConfirmation'])
        self.assertGreater(resume['taskCount'], 0)

        before = self.state['summary'].get('success', 0)
        self.call('resumeTasks')
        self.wait(lambda m: m['type'] == 'state' and self.state['summary']['busy'], timeout=10)
        # Progress really resumes from the retained scope and moves forward.
        self.wait(lambda m: m['type'] == 'state' and self.state['summary'].get('total', 0) > 0, timeout=20)
        self.wait(lambda m: m['type'] == 'state'
                  and not self.state['summary']['busy'], timeout=40)
        self.assertGreaterEqual(self.state['summary'].get('success', 0), before)
        # The continuation was consumed: a new run is required after this one.
        self.assertFalse(self.state['summary']['resumeScoped'])
        self.assertIsNone(self.process.poll())

if __name__ == '__main__':
    unittest.main()
