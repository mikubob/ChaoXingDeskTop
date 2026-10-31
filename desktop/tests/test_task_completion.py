"""Resume individual completed tasks, including successes before interruption."""
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vendor/chaoxing'))
from api.base import SessionManager
from chaoxing_desktop.config import Redactor, defaults, to_payload
from chaoxing_desktop.control import Cancelled
from chaoxing_desktop.course_progress import CourseProgressCache, chapter_progress
from chaoxing_desktop.runtime import ChapterResult
from chaoxing_desktop.task_completion import TaskCompletionCache
from chaoxing_desktop.worker import Channel, real


class TaskCompletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.course = dict(courseId='c', clazzId='class', cpi='p', title='大学体育')
        self.points = [dict(id='1', title='2.1 运动安全', has_finished=True),
                       dict(id='2', title='2.2 科学运动', has_finished=False)]
        self.account = 'a' * 32
        self.cache = TaskCompletionCache(self.tmp.name, self.account, 'uid')

    def tearDown(self):
        self.tmp.cleanup()

    def run_worker(self, points=None, process=None, responses=None, verify=False, add_learning_count=True,
                   ignore_completion_cache=False):
        points = self.points if points is None else points
        client = Mock(spec_set=['login', 'get_course_list', 'get_course_point'])
        client.login.return_value = dict(status=True)
        client.get_course_list.return_value = [self.course]
        client.get_course_point.return_value = dict(points=points)
        if responses:
            client.get_course_point.side_effect = responses
        process = process or Mock(return_value=ChapterResult.PENDING)
        core = SimpleNamespace(create_client=lambda *a, **kw: client, process_chapter=process,
                               ChapterResult=ChapterResult, increase_learning_count_for_course=Mock())
        cookie = Path(self.tmp.name) / 'cookie'
        cookie.write_bytes(b'fixture')
        store = SimpleNamespace(get=lambda _: dict(username='fixture', uid='uid'), cookie_path=lambda _: cookie)
        session = SimpleNamespace(cookies=SimpleNamespace(get_dict=lambda: dict(_uid='uid')))
        config = to_payload(defaults())
        config['common'].update(username='fixture', password='', use_cookies=True, jobs=1,
                                add_learning_count=add_learning_count, ignore_completion_cache=ignore_completion_cache)
        output = io.StringIO()
        with patch('chaoxing_desktop.worker.load_core', return_value=core), \
                patch('chaoxing_desktop.accounts.AccountStore', return_value=store), \
                patch('api.cookies.configure_account'), patch('api.cookies.save_cookies'), \
                patch.object(SessionManager, 'get_session', return_value=session):
            command = dict(action='run', config=config, data_dir=self.tmp.name, account_id=self.account,
                           selected=[['c', 'class']])
            if verify:
                command.update(action='verify', account_uid='uid', cookie_mtime_ns=cookie.stat().st_mtime_ns,
                               targets=[dict(id='chapter-1', courseId='c', clazzId='class', pointId='1')])
            real(Channel(output, io.StringIO(), Redactor({})), command)
        return client, core, [json.loads(line) for line in output.getvalue().splitlines()]

    def test_resume_skips_completed_task_with_real_title_and_starts_first_unfinished(self):
        self.run_worker()
        self.assertEqual(set(self.cache.read(self.course)), {'1'})
        self.assertEqual(self.cache.read(self.course)['1']['title'], self.points[0]['title'])
        # The cache controls resumption even if a later directory response lags.
        points = [dict(self.points[0], has_finished=False), dict(self.points[1], has_finished=True)]
        client, core, events = self.run_worker(points)
        self.assertEqual([c.args[2]['id'] for c in core.process_chapter.call_args_list], ['2'])
        self.assertEqual(client.get_course_point.call_count, 3)  # directory, second task, final read
        task_rows = next(e['tasks'] for e in events if e['type'] == 'tasks')
        self.assertEqual([t['title'] for t in task_rows], [p['title'] for p in self.points])
        first_id = task_rows[0]['id']
        self.assertEqual([e['status'] for e in events if e['type'] == 'task' and e['id'] == first_id], ['success'])
        self.assertEqual(events[-1]['success'], 2)
        self.assertEqual(set(self.cache.read(self.course)), {'1', '2'})
        client, core, events = self.run_worker(points)
        core.process_chapter.assert_not_called()
        core.increase_learning_count_for_course.assert_called_once()
        self.assertEqual(core.increase_learning_count_for_course.call_args.args[1], self.course)
        self.assertEqual(client.get_course_point.call_count, 1)  # discover newly added chapters only
        self.assertEqual(events[-1]['success'], 2)

    def test_success_is_saved_before_later_task_is_stopped(self):
        process = Mock(side_effect=[ChapterResult.SUCCESS, Cancelled()])
        with self.assertRaises(Cancelled):
            self.run_worker(process=process)
        self.assertEqual(set(self.cache.read(self.course)), {'1'})

    def test_late_confirmation_is_persisted_but_pending_is_not(self):
        point = dict(self.points[0], has_finished=False)
        self.run_worker(points=[point])
        self.assertEqual(self.cache.read(self.course), {})
        self.run_worker(points=[point], responses=[dict(points=[point]), dict(points=[point]),
                                                 dict(points=[dict(point, has_finished=True)])])
        self.assertEqual(set(self.cache.read(self.course)), {'1'})

    def test_aggregate_course_cache_alone_does_not_skip_individual_tasks(self):
        CourseProgressCache(self.tmp.name, self.account, 'uid').save(
            chapter_progress(self.course, [dict(p, has_finished=True) for p in self.points]))
        _, core, _ = self.run_worker()
        self.assertEqual(core.process_chapter.call_count, 2)

    def test_cache_isolation_and_corruption_fallback(self):
        self.cache.save(self.course, self.points[0])
        for key in ('courseId', 'clazzId', 'cpi'):
            self.assertEqual(self.cache.read(dict(self.course, **{key: 'other'})), {})
        self.assertEqual(TaskCompletionCache(self.tmp.name, 'other', 'uid').read(self.course), {})
        self.assertEqual(TaskCompletionCache(self.tmp.name, self.account, 'other').read(self.course), {})
        self.cache.path(self.course).write_bytes(b'broken')
        self.assertEqual(self.cache.read(self.course), {})
        _, core, _ = self.run_worker()
        self.assertEqual(core.process_chapter.call_count, 2)

    def test_manual_confirmation_is_cached_and_used_on_next_run(self):
        _, core, events = self.run_worker(verify=True)
        self.assertEqual(set(self.cache.read(self.course)), {'1'})
        self.assertTrue(next(e for e in events if e['type'] == 'verification')['confirmed'])
        core.process_chapter.assert_not_called()
        _, core, _ = self.run_worker(points=[dict(p, has_finished=False) for p in self.points])
        self.assertEqual([c.args[2]['id'] for c in core.process_chapter.call_args_list], ['2'])

    def test_manual_negative_confirmation_invalidates_only_target_cache(self):
        for point in self.points:
            self.cache.save(self.course, point)
        _, _, events = self.run_worker(points=[dict(p, has_finished=False) for p in self.points], verify=True)
        self.assertFalse(next(e for e in events if e['type'] == 'verification')['confirmed'])
        self.assertEqual(set(self.cache.read(self.course)), {'2'})
        _, core, _ = self.run_worker(points=[dict(p, has_finished=False) for p in self.points])
        self.assertEqual([c.args[2]['id'] for c in core.process_chapter.call_args_list], ['1'])

    def test_manual_missing_chapter_invalidates_cache_but_transport_failure_preserves_it(self):
        import requests
        self.cache.save(self.course, self.points[0])
        _, _, events = self.run_worker(verify=True, responses=[requests.Timeout('fixture')])
        self.assertTrue(any(e['type'] == 'verification_failed' for e in events))
        self.assertEqual(set(self.cache.read(self.course)), {'1'})
        self.run_worker(points=[self.points[1]], verify=True)
        self.assertEqual(self.cache.read(self.course), {})

    def test_all_cached_courses_still_honor_learning_count_switch(self):
        for point in self.points:
            self.cache.save(self.course, point)
        _, core, _ = self.run_worker(add_learning_count=False)
        core.process_chapter.assert_not_called()
        core.increase_learning_count_for_course.assert_not_called()
        _, core, _ = self.run_worker(add_learning_count=True)
        core.process_chapter.assert_not_called()
        core.increase_learning_count_for_course.assert_called_once()
        self.assertEqual(core.increase_learning_count_for_course.call_args.args[1], self.course)

    def test_ignore_cache_rechecks_completed_tasks_and_removes_stale_successes(self):
        for point in self.points:
            self.cache.save(self.course, point)
        points = [dict(p, has_finished=False) for p in self.points]
        _, core, events = self.run_worker(points=points, ignore_completion_cache=True)
        self.assertEqual([c.args[2]['id'] for c in core.process_chapter.call_args_list], ['1', '2'])
        self.assertEqual(events[-1]['pending'], 2)
        self.assertEqual(self.cache.read(self.course), {})
        _, core, _ = self.run_worker(points=points)
        self.assertEqual(core.process_chapter.call_count, 2)

    def test_ignore_cache_preserves_completion_when_requests_fail(self):
        import requests
        self.cache.save(self.course, self.points[0])
        point = self.points[0]
        self.run_worker(points=[point], ignore_completion_cache=True,
                        responses=[dict(points=[point]), requests.Timeout('fixture'), requests.Timeout('fixture')])
        self.assertEqual(set(self.cache.read(self.course)), {'1'})

    def test_ignore_cache_can_confirm_and_cache_again(self):
        for point in self.points:
            self.cache.save(self.course, point)
        _, core, events = self.run_worker(points=[dict(p, has_finished=True) for p in self.points],
                                          ignore_completion_cache=True)
        self.assertEqual(core.process_chapter.call_count, 2)
        self.assertEqual(events[-1]['success'], 2)
        self.assertEqual(set(self.cache.read(self.course)), {'1', '2'})


if __name__ == '__main__':
    unittest.main()
