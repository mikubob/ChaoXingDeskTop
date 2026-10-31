"""Cached course statistics and completed-course startup behavior."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vendor/chaoxing'))
from PySide6.QtWidgets import QApplication
from chaoxing_desktop.config import Redactor, defaults, to_payload
from chaoxing_desktop.control import Cancelled
from chaoxing_desktop.controller import Controller
from chaoxing_desktop.course_progress import CourseProgressCache, chapter_progress, progress_key, refresh_progress
from chaoxing_desktop.qml_backend import Backend
from chaoxing_desktop.worker import Channel, real


class CourseProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, CHAOXING_DESKTOP_DATA=self.tmp.name)
        self.env.start()
        self.course = dict(courseId='c', clazzId='a', cpi='p', title='Python')
        self.points = [dict(id='1', has_finished=True), dict(id='2', has_finished=False)]
        self.row = chapter_progress(self.course, self.points)
        self.b = None

    def tearDown(self):
        if self.b:
            self.b.discardAndClose()
            self.b.deleteLater()
        self.app.processEvents()
        self.env.stop()
        self.tmp.cleanup()

    def backend(self):
        self.b = Backend()
        self.b.config['common']['username'] = 'fixture'
        account = self.b.ensure_account()
        self.b.account_store.update(account['id'], uid='uid')
        self.b.cookie_path().parent.mkdir(parents=True, exist_ok=True)
        self.b.cookie_path().write_bytes(b'fixture-cookie')
        self.b.config['common']['use_cookies'] = 'true'
        return self.b

    def test_cache_survives_reopen_and_isolates_accounts_users_classes_and_cpi(self):
        cache = CourseProgressCache(self.tmp.name, 'account', 'uid')
        cache.save(self.row)
        other = dict(self.course, clazzId='b')
        cache.save(chapter_progress(other, [dict(id='1', has_finished=False)]))
        rows = CourseProgressCache(self.tmp.name, 'account', 'uid').read()
        self.assertEqual(rows[progress_key(self.course)]['completed'], 1)
        self.assertEqual(rows[progress_key(other)]['completed'], 0)
        self.assertNotIn(progress_key(dict(self.course, cpi='other')), rows)
        self.assertEqual(CourseProgressCache(self.tmp.name, 'other', 'uid').read(), {})
        self.assertEqual(CourseProgressCache(self.tmp.name, 'account', 'other-uid').read(), {})
        self.assertNotIn(b'fixture-cookie', cache.path.read_bytes())

    def test_corrupt_or_invalid_cache_is_a_miss(self):
        cache = CourseProgressCache(self.tmp.name, 'a', 'u')
        cache.save(self.row)
        for raw in (b'{', b'[]', b'{"version":1,"courses":[{}]}'):
            cache.path.write_bytes(raw)
            self.assertEqual(cache.read(), {})
        cache.save(self.row)
        self.assertEqual(len(cache.read()), 1)

    def test_counts_deduplicate_and_require_explicit_platform_status(self):
        self.assertEqual(chapter_progress(self.course, self.points + self.points)['total'], 2)
        self.assertEqual(chapter_progress(self.course, [])['total'], 0)
        with self.assertRaises(ValueError):
            chapter_progress(self.course, [dict(id='1', has_finished='true')])
        self.assertFalse(Controller._valid_event(dict(type='course_progress', **dict(self.row, completed=3))))
        self.assertFalse(Controller._valid_event(dict(type='course_progress', **dict(self.row, completed=True))))

    def test_cached_progress_is_visible_before_background_request_and_refresh_does_not_lock_ui(self):
        b = self.backend()
        b.course_progress_cache().save(self.row)
        b.config['common']['password'] = 'should-not-be-sent'
        b.on_event(dict(type='courses', demo=False, courses=[self.course]))
        progress = b.courseRows[0]['progress']
        self.assertEqual((progress['completed'], progress['total']), (1, 2))
        self.assertTrue(progress['cached'])
        self.assertTrue(progress['loading'])
        with patch.object(b._course_controller, 'start') as start:
            b.start_course_progress()
        payload = start.call_args.args[0]
        self.assertEqual(payload['config']['common']['password'], '')
        self.assertEqual(payload['account_uid'], 'uid')
        self.assertFalse(b.busy)
        b.selectVisible(True)
        self.assertEqual(b.selected, {('c', 'a')})
        b.on_course_progress_event(dict(type='course_progress', **dict(self.row, completed=2)))
        self.assertEqual(b.courseRows[0]['progress']['completed'], 2)
        self.assertFalse(b.courseRows[0]['progress']['cached'])
        self.assertFalse(b.courseRows[0]['progress']['loading'])
        self.assertEqual(b.course_progress_cache().read()[progress_key(self.course)]['completed'], 2)

    def test_no_cache_shows_loading_and_failure_preserves_cached_counts_silently(self):
        b = self.backend()
        b.on_event(dict(type='courses', demo=False, courses=[self.course]))
        self.assertTrue(b.courseRows[0]['progress']['loading'])
        self.assertNotIn('completed', b.courseRows[0]['progress'])
        with patch.object(b._course_controller, 'start'):
            b.start_course_progress()
        b.on_course_progress_event(dict(type='course_progress', **self.row))
        message = b.message
        b.on_course_progress_event(dict(type='course_progress_loading', **self.course))
        b.on_course_progress_finished(1)
        progress = b.courseRows[0]['progress']
        self.assertEqual(progress['completed'], 1)
        self.assertTrue(progress['failed'])
        self.assertFalse(progress['loading'])
        self.assertEqual(b.message, message)

    def test_account_reset_and_cookie_changes_reject_stale_results(self):
        b = self.backend()
        b.on_event(dict(type='courses', demo=False, courses=[self.course]))
        with patch.object(b._course_controller, 'start'):
            b.start_course_progress()
        b.config['common']['username'] = 'changed'
        b.on_course_progress_event(dict(type='course_progress', **self.row))
        self.assertNotIn('completed', b.courseRows[0]['progress'])
        b.reset_account_view()
        b.on_course_progress_event(dict(type='course_progress', **self.row))
        self.assertEqual(b._course_progress, {})

    def test_demo_never_reads_cache_or_starts_background_http(self):
        b = self.backend()
        with patch('chaoxing_desktop.qml_backend.CourseProgressCache') as cache, \
                patch.object(b._course_controller, 'start') as start:
            b.on_event(dict(type='courses', demo=True, courses=[self.course]))
            b.start_course_progress()
        cache.assert_not_called()
        start.assert_not_called()

    def test_background_reader_continues_after_failure_and_propagates_cancel(self):
        client = Mock(spec_set=['get_course_point'])
        client.get_course_point.side_effect = [TimeoutError(), dict(points=self.points)]
        output = io.StringIO()
        channel = Channel(output, io.StringIO(), Redactor({}))
        refresh_progress(client, channel, [self.course, dict(self.course, clazzId='b')])
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([e['type'] for e in events], ['course_progress_loading', 'course_progress_failed',
                                                      'course_progress_loading', 'course_progress'])
        client.get_course_point.side_effect = Cancelled
        with self.assertRaises(Cancelled):
            refresh_progress(client, channel, [self.course])

    def test_course_list_renders_loading_cached_complete_and_failed_progress(self):
        from PySide6.QtTest import QTest
        from shiboken6 import delete
        from chaoxing_desktop.qml_app import create_engine
        b = self.backend()
        courses = [dict(self.course, clazzId=str(i), title=title) for i, title in enumerate(
            ('大学体育', '职业生涯发展与就业指导', '大学英语', '信息素养'))]
        b.on_event(dict(type='courses', demo=False, courses=courses))
        b.apply_course_progress(dict(type='course_progress', **chapter_progress(courses[1], self.points)))
        b._course_progress[progress_key(courses[1])].update(cached=True, loading=True)
        b.apply_course_progress(dict(type='course_progress', **chapter_progress(courses[2], [self.points[0]])))
        b.apply_course_progress(dict(type='course_progress', **chapter_progress(courses[3], self.points)))
        b.apply_course_progress(dict(type='course_progress_failed', **courses[3]))
        engine = create_engine(b)
        try:
            self.assertTrue(engine.rootObjects(), engine.load_warnings)
            root = engine.rootObjects()[0]
            QTest.qWait(150)
            self.assertFalse(root.grabWindow().isNull())
            root.setProperty('width', 980)
            root.setProperty('height', 700)
            QTest.qWait(100)
            self.assertFalse(root.grabWindow().isNull())
            self.assertEqual(engine.load_warnings, [])
        finally:
            delete(engine)

    def test_worker_background_action_only_reads_platform_and_does_not_save_cookies(self):
        from api.base import SessionManager
        cookie = Path(self.tmp.name) / 'cookie'
        cookie.write_bytes(b'fixture-cookie')
        store = SimpleNamespace(get=lambda _: dict(username='fixture', uid='uid'), cookie_path=lambda _: cookie)
        client = Mock(spec_set=['login', 'get_course_list', 'get_course_point'])
        client.login.return_value = dict(status=True)
        client.get_course_list.return_value = [self.course]
        client.get_course_point.return_value = dict(points=self.points)
        core = SimpleNamespace(create_client=Mock(return_value=client))
        config = to_payload(defaults())
        config['common'].update(username='fixture', password='', use_cookies=True)
        command = dict(action='course_progress', config=config, account_id='a' * 32,
                       account_uid='uid', cookie_mtime_ns=cookie.stat().st_mtime_ns,
                       data_dir=self.tmp.name, selected=[['c', 'a']])
        session = SimpleNamespace(cookies=SimpleNamespace(get_dict=lambda: dict(_uid='uid')))
        output = io.StringIO()
        with patch('chaoxing_desktop.worker.load_core', return_value=core), \
                patch('chaoxing_desktop.accounts.AccountStore', return_value=store), \
                patch('api.cookies.configure_account'), patch('api.cookies.save_cookies') as save, \
                patch.object(SessionManager, 'get_session', return_value=session):
            real(Channel(output, io.StringIO(), Redactor({})), command)
        self.assertTrue(core.create_client.call_args.kwargs['courses_only'])
        save.assert_not_called()
        client.get_course_point.assert_called_once_with('c', 'a', 'p')
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([e for e in events if e['type'] == 'course_progress'][0]['completed'], 1)
        self.assertEqual(cookie.read_bytes(), b'fixture-cookie')



if __name__ == '__main__':
    unittest.main()
