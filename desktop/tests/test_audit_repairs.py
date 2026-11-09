"""Regression coverage for the offline/real-account audit boundaries."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[1]/'vendor/chaoxing')]
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
import requests
from api.base import Account, Chaoxing, SessionManager
from api.answer import DummyTiku
from api.responses import PlatformResponseError, checked_page
from chaoxing_desktop.accounts import AccountStore
from chaoxing_desktop.answers import AIClient, AnswerRouter, plain_text
from chaoxing_desktop.config import defaults, to_payload, validate
from chaoxing_desktop.control import RunControl, Cancelled
from chaoxing_desktop.instance import InstanceGuard
from chaoxing_desktop.qml_backend import Backend
from chaoxing_desktop.query_result import safe_text
from scripts.release_support import validate_app_tree

COURSES = '''<div class="course" id="c" info="i" roleid="s"><input class="clazzId" value="b">
<input class="courseId" value="c"><a href="?cpi=s&amp;x=1"></a><span class="course-name" title="Login API"></span>
<p class="color3" title="Teacher"></p></div>'''


def response(text, status=200):
    reply = requests.Response()
    reply.status_code = status
    reply._content = text.encode('utf-8')
    reply._content_consumed = True
    reply.encoding = 'utf-8'
    reply.url = 'https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata'
    return reply


class PlatformBoundaryTests(unittest.TestCase):
    def client(self):
        return Chaoxing(account=Account('', ''), tiku=DummyTiku())

    def test_failed_or_unknown_pages_are_not_empty_lists(self):
        client = self.client()
        for status, text in [(503, '<html>Gateway unavailable</html>'), (200, '<html>Maintenance</html>'),
                             (200, '<form><input type="password"></form>')]:
            with self.subTest(status=status, text=text):
                session = Mock(post=Mock(return_value=response(text,status)),get=Mock(return_value=response(text,status)))
                with patch.object(SessionManager,'get_session',return_value=session):
                    with self.assertRaises(PlatformResponseError):client.get_course_list()
                    with self.assertRaises(PlatformResponseError):client.get_course_point('c','b','s')

    def test_explicit_empty_pages_remain_valid(self):
        checked_page(response('<div class="course-list">暂无课程</div>'),'courses')
        checked_page(response('<div id="coursetree">暂无章节</div>'),'chapters')

    def test_cookie_validation_reuses_root_without_matching_title_word_login(self):
        client = self.client()
        session = requests.Session()
        session.cookies.set('_uid','synthetic')
        session.post = Mock(return_value=response(COURSES))
        session.get = Mock(return_value=response('<ul class="file-list"></ul>'))
        with patch.object(SessionManager,'get_session',return_value=session), \
             patch.object(SessionManager,'get_instance',return_value=SimpleNamespace(_session=session)), \
             patch.object(SessionManager,'update_cookies'):
            self.assertTrue(client.login(login_with_cookies=True)['status'])
            self.assertEqual(client.get_course_list()[0]['title'],'Login API')
            self.assertEqual(session.post.call_count,1)
            self.assertEqual(session.get.call_count,1)  # folders, no eager name lookup

    def test_network_problem_does_not_trigger_password_fallback(self):
        client = self.client()
        client.account = Account('synthetic','synthetic-password')
        with patch.object(SessionManager,'update_cookies'), \
             patch.object(client,'_validate_cookie_session',side_effect=PlatformResponseError('retry','network_error')):
            result=client.login(login_with_cookies=True)
        self.assertFalse(result['status'])
        self.assertEqual(result['kind'],'network_error')

    def test_malformed_login_errors_are_actionable(self):
        for body,status,kind in [('{"status":false,"msg":"denied"}',200,'auth_error'),
                                 ('<html>Gateway</html>',502,'service_error'),
                                 ('<html>Verification</html>',200,'protocol_error'),
                                 ('{}',200,'protocol_error')]:
            with self.subTest(body=body), patch('api.base.requests.Session') as factory:
                factory.return_value.post.return_value=response(body,status)
                result=self.client().login()
                self.assertFalse(result['status'])
                self.assertEqual(result['kind'],kind)

    def test_later_card_failure_is_retryable_and_does_not_execute_empty_page(self):
        client = self.client()
        client.rate_limiter=Mock()
        client.study_emptypage=Mock()
        session=Mock(get=Mock(side_effect=[response('mArg={};'),response('failure',503)]))
        with patch.object(SessionManager,'get_session',return_value=session), \
             patch('api.base.decode_course_card',return_value=([dict(type='video')],{})):
            with self.assertRaises(PlatformResponseError):
                client.get_job_list(dict(courseId='c',clazzId='b',cpi='s'),dict(id='p'))
        client.study_emptypage.assert_not_called()

    def test_pause_interrupts_learning_count_before_next_write(self):
        control=RunControl(lambda *a,**k:None)
        paused=threading.Event()
        stopped=threading.Event()
        client=SimpleNamespace(control=control,rate_limiter=Mock(),
            _access_chapter_for_count=Mock(return_value='html'),_extract_and_send_setlog=Mock(),_send_monitor_heartbeat=Mock())
        def wait_once(owner, seconds):
            control.command({'command':'pause'})
            paused.set()
            control.checkpoint()
        def run():
            try:Chaoxing.increase_chapter_learning_count(client,{},[dict(id='a',title='a'),dict(id='b',title='b')],2)
            except Cancelled:stopped.set()
        with patch('api.base.controlled_wait',side_effect=wait_once):
            thread=threading.Thread(target=run)
            thread.start()
            try:
                self.assertTrue(paused.wait(2))
                self.assertEqual(client._access_chapter_for_count.call_count,1)
                self.assertEqual(client._send_monitor_heartbeat.call_count,1)
            finally:
                control.command({'command':'cancel'})
                thread.join(2)
            self.assertTrue(stopped.is_set())
            self.assertFalse(thread.is_alive())

    def test_resume_check_can_use_checkpoint_without_deadlocking(self):
        control=RunControl(lambda *a,**k:None)
        control.resume_check=lambda:control.checkpoint()
        control.command({'command':'resume'})
        thread=threading.Thread(target=control.checkpoint,daemon=True)
        thread.start();thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(control.validated_epoch,1)


class LocalBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_stale_account_writes_preserve_newer_account(self):
        with tempfile.TemporaryDirectory() as folder:
            first,second=AccountStore(Path(folder)),AccountStore(Path(folder))
            first.add('synthetic-first')
            with self.assertRaisesRegex(ValueError,'另一个实例'):second.add('synthetic-second')
            self.assertEqual([r['username'] for r in AccountStore(Path(folder)).rows],['synthetic-first'])

    def test_same_profile_has_one_owner_and_independent_profile_can_start(self):
        with tempfile.TemporaryDirectory() as folder:
            first=InstanceGuard(Path(folder)/'one')
            second=InstanceGuard(Path(folder)/'one')
            other=InstanceGuard(Path(folder)/'two')
            try:
                self.assertTrue(first.acquire())
                self.assertFalse(second.acquire())
                self.assertTrue(other.acquire())
            finally:
                first.close();second.close();other.close()

    def test_stale_remove_does_not_delete_newer_session(self):
        with tempfile.TemporaryDirectory() as folder:
            first=AccountStore(Path(folder));row=first.add('synthetic')
            cookie=first.cookie_path(row['id']);cookie.parent.mkdir(parents=True);cookie.write_bytes(b'encrypted fixture')
            second=AccountStore(Path(folder))
            first.update(row['id'],label='updated')
            with self.assertRaises(ValueError):second.remove(row['id'])
            self.assertEqual(cookie.read_bytes(),b'encrypted fixture')

    def test_profile_guard_also_blocks_a_separate_process(self):
        with tempfile.TemporaryDirectory() as folder:
            guard=InstanceGuard(folder)
            self.assertTrue(guard.acquire())
            try:
                program='from PySide6.QtCore import QCoreApplication; from chaoxing_desktop.instance import InstanceGuard; import sys; app=QCoreApplication([]); guard=InstanceGuard(sys.argv[1]); assert not guard.acquire()'
                result=subprocess.run([sys.executable,'-c',program,folder],cwd=Path(__file__).resolve().parents[1],capture_output=True,timeout=5)
                self.assertEqual(result.returncode,0)
            finally:guard.close()

    def test_math_and_code_survive_all_display_boundaries(self):
        for value in ('Given x < y and y > 0','vector<int>','x <= y && y >= 0'):
            self.assertEqual(plain_text(value),value)
            self.assertEqual(safe_text(value),value)
            self.assertIn(value,AIClient(dict(vision='false')).messages(dict(title=value))[-1]['content'][0]['text'])
        self.assertEqual(plain_text('<B>A &amp; B</B><br><img src="https://example.test/a.png">'),'A & B\n')

    def test_declarative_retry_bounds_and_budget_survive_large_run(self):
        for value in ('-1','6','bad'):
            config=defaults();config['question_bank']['declarative_retries']=value
            with self.assertRaises(ValueError):validate(config)
        router=AnswerRouter(to_payload(defaults()),SimpleNamespace(send=lambda *a,**k:None))
        for _ in range(12):router.consume_budget(('old',))
        for i in range(5000):router.consume_budget(('new',i))
        with self.assertRaises(RuntimeError):router.consume_budget(('old',))

    def test_account_reset_clears_filters_and_discard_close_preserves_settings(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,CHAOXING_DESKTOP_DATA=folder):
            backend=Backend()
            try:
                self.assertTrue(backend.save())
                before=(Path(folder)/'settings.ini').read_bytes()
                backend.filterQuestions(True,'old-chapter')
                backend.reset_account_view()
                self.assertFalse(backend._question_filter.pending_only)
                self.assertEqual(backend._question_filter.task,'')
                backend.setValue('common','speed','')
                self.assertFalse(backend.close())
                self.assertTrue(backend.discardAndClose())
                self.assertEqual((Path(folder)/'settings.ini').read_bytes(),before)
            finally:backend.discardAndClose();backend.deleteLater()

    def test_release_entries_reject_private_files_and_allow_ca(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            ca=root/'_internal/certifi/cacert.pem';ca.parent.mkdir(parents=True);ca.write_text('public CA')
            self.assertEqual(validate_app_tree(root),[ca])
            for name in ('.env.production','exported.ini','cookies-2.txt','session.log','private.pem','key.key'):
                path=root/name;path.write_text('synthetic')
                try:
                    with self.subTest(name=name),self.assertRaises(ValueError):validate_app_tree(root)
                finally:path.unlink()


if __name__=='__main__':unittest.main()
