"""Exercise the actual native transport and worker in an isolated profile."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


class WinUIBridgeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        (self.root / 'build').mkdir(exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix="chaoxing-winui-test-", dir=self.root / 'build')
        self.process = subprocess.Popen(
            [sys.executable, "-u", str(self.root / "run.py"), "--bridge"],
            cwd=self.root, env=dict(os.environ, CHAOXING_DESKTOP_DATA=self.directory.name,
                                   PYTHONIOENCODING="utf-8"),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.messages = queue.Queue()
        self.state = {}
        self.sequence = 0

        def read():
            for line in self.process.stdout:
                self.messages.put(json.loads(line))
            self.messages.put(dict(type="eof"))

        threading.Thread(target=read, daemon=True).start()
        try:
            self.wait(lambda m: m['type'] == 'ready')
            self.wait(lambda m: m['type'] == 'state')
        except Exception:
            self.tearDown()
            raise

    def tearDown(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.process.stdout.close()
        self.process.stderr.close()
        self.directory.cleanup()

    def wait(self, predicate, timeout=15):
        if predicate({'type': 'cached'}):
            return {'type': 'cached'}
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                message = self.messages.get(timeout=max(.01, deadline - time.monotonic()))
            except queue.Empty:
                self.fail('Native backend timed out')
            if message['type'] == 'eof':
                self.fail('Native backend exited unexpectedly')
            if message['type'] in ('ready', 'state'):
                self.state.update(message['data'])
            if predicate(message):
                return message
        self.fail('Native backend timed out')

    def call(self, method, *args, path=''):
        self.sequence += 1
        self.process.stdin.write((json.dumps(dict(id=self.sequence, method=method,
                                  args=args, path=path), ensure_ascii=False) + '\n').encode())
        self.process.stdin.flush()
        return self.wait(lambda m: m['type'] == 'response' and m['id'] == self.sequence)

    def test_schema_and_restricted_commands(self):
        self.assertIn('learning', self.state['schema'])
        self.assertIn('advanced', self.state['schema'])
        self.assertIsInstance(self.state['logs'], list)
        self.assertIn('error', self.call('__getattribute__', 'config'))
        self.assertIn('error', self.call('on_event', {'type': 'done'}))
        self.assertTrue(self.call('save')['result'])
        # Issue #5: the continuation state is part of the summary and the
        # command whitelist, but internal helpers stay unreachable.
        self.assertIn('resumeScoped', self.state['summary'])
        self.assertIn('resume', self.state['summary'])
        self.assertEqual(self.state['summary']['resume']['available'], False)
        self.assertIn('error', self.call('_remember_resume'))
        self.assertIn('error', self.call('on_finished', 0))
        # Nothing was stopped in this session, so the command reports that and
        # does not start a worker.
        self.assertIsNone(self.call('resumeTasks')['result'])
        self.wait(lambda m: '没有' in self.state['summary']['message'] or '停止' in self.state['summary']['message'])
        self.assertFalse(self.state['summary']['busy'])

    def test_unicode_account_label_roundtrip(self):
        cookie = Path(self.directory.name) / 'synthetic-cookie.txt'
        cookie.write_text('_uid=test-uid; session=synthetic', encoding='utf-8')
        self.call('fileAction', 'import_cookie', path=str(cookie))
        self.call('renameAccount', '中文备注 · 验收账号')
        self.assertEqual(self.state['accountRows'][0]['label'], '中文备注 · 验收账号')

    def test_native_file_picker_path_and_clipboard(self):
        path = Path(self.directory.name) / '诊断.ini'
        self.call('fileAction', 'export_diagnostics', path=str(path))
        self.assertTrue(path.exists())
        self.assertNotIn('password', path.read_text(encoding='utf-8'))
        # Clipboard content is an event for WinUI; no QApplication is needed.
        self.sequence += 1
        self.process.stdin.write((json.dumps(dict(id=self.sequence, method='releaseAction', args=['copy'])) + '\n').encode())
        self.process.stdin.flush()
        message = self.wait(lambda m: m['type'] == 'clipboard')
        self.assertIn('0.5.0', message['text'])

    def test_demo_course_search_selection_and_worker(self):
        self.assertFalse(self.state['summary']['coursesReady'])
        self.call('loadDemo')
        self.wait(lambda _: bool(self.state.get('courses')) and self.state.get('summary', {}).get('courseCount', 0) > 0 and not self.state['summary']['busy'])
        self.assertTrue(self.state['courses'])
        self.assertTrue(self.state['summary']['coursesReady'])
        self.call('selectVisible', True)
        self.wait(lambda _: self.state['summary']['selectedCount'] > 0)
        selected = self.state['summary']['selectedCount']
        self.call('searchCourses', 'no-such-course')
        self.wait(lambda _: self.state['courses'] == [])
        self.assertEqual(selected, self.state['summary']['hiddenCount'])
        self.call('startTasks')
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            self.wait(lambda m: m['type'] == 'state', timeout=30)
            if self.state.get('input'):
                self.call('reply', '', False)
            if self.state.get('tasks') and not self.state['summary']['busy']:
                break
        self.assertTrue(self.state['tasks'])
        self.assertTrue(all(isinstance(row, dict) and 'statusText' in row for row in self.state['tasks']))
        self.assertFalse(self.state['summary']['busy'])
        self.assertTrue(self.state['logs'])
        self.call('addAccount')
        self.assertFalse(self.state['summary']['coursesReady'])

    def test_validation_prevents_close_and_preserves_input(self):
        self.call('setValue', 'common', 'speed', 'invalid')
        self.assertFalse(self.call('close')['result'])
        self.assertIsNone(self.process.poll())
        self.call('setValue', 'common', 'speed', '1.5')
        self.assertTrue(self.call('save')['result'])

    def test_close_saves_and_acknowledges_while_worker_is_active(self):
        self.call('loadDemo')
        self.wait(lambda _: self.state['summary']['coursesReady'] and not self.state['summary']['busy'])
        self.call('selectVisible', True)
        self.call('startTasks')
        self.assertTrue(self.state['summary']['busy'])
        started = time.monotonic()
        self.assertTrue(self.call('close')['result'])
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertTrue((Path(self.directory.name) / 'settings.ini').is_file())
        # Cleanup still runs to completion after acknowledging the native UI.
        self.process.stdin.close()
        self.process.wait(timeout=8)
        self.assertEqual(self.process.returncode, 0)


class BridgeStateNotificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from chaoxing_desktop.winui_bridge import Bridge
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        env = patch.dict(os.environ, CHAOXING_DESKTOP_DATA=self.directory.name)
        env.start()
        self.addCleanup(env.stop)
        network = patch('requests.sessions.Session.request', side_effect=AssertionError('offline only'))
        network.start()
        self.addCleanup(network.stop)
        self.state, self.messages = {}, []

        def send(message):
            self.messages.append(message)
            if message['type'] == 'state':
                self.state.update(message['data'])

        self.bridge = Bridge(self.app, send)
        self.b = self.bridge.backend

    def tearDown(self):
        self.b.controller.active = False
        self.bridge.timer.stop()
        self.bridge.dispose()
        self.b.deleteLater()
        self.bridge.guard.deleteLater()
        self.app.processEvents()

    def test_saved_draft_unlocks_on_worker_completion_and_locks_on_source_change(self):
        from chaoxing_desktop.discussion_model import safe_record
        source = safe_record(dict(id='review-draft', run_id='review-run', task_id='review-task',
                                  title='合成讨论', course='合成课程', chapter='合成章节', level='medium',
                                  source='ai', stage='draft', content='合成草稿', task_status='pending',
                                  reason='', elapsed_ms=0), lambda text: text)
        self.b.demo_mode = True
        self.b._discussion_model.upsert(source)
        self.b.openDraftCopy('discussion', source['id'])
        self.assertTrue(self.b.draftEditable)
        self.assertTrue(self.b.saveDraftCopy())
        copy_id = self.b._draft['id']
        self.b.operation = 'ai_check'
        self.b.controller.active = True
        self.b.controller.busy_changed.emit(True)
        self.assertTrue(self.b.openSavedDraft(copy_id))
        self.bridge.flush()
        self.assertFalse(self.state['draftEditable'])

        self.messages.clear()
        self.b.controller.active = False
        self.b.controller.busy_changed.emit(False)
        self.b.controller.finished.emit(0)
        self.bridge.flush()
        self.assertTrue(self.b.draftEditable)
        self.assertTrue(self.state['draftEditable'])
        # Completion must send editability without a draftChanged notification.
        self.assertNotIn('draftFeedback', self.messages[-1]['data'])

        self.b._discussion_model.upsert(dict(source, stage='publish_unknown'))
        self.b.changed.emit()
        self.bridge.flush()
        self.assertFalse(self.state['draftEditable'])

    def test_resume_confirmation_gate_over_the_real_bridge(self):
        """The native transport must not start a writing continuation unconfirmed."""
        from types import SimpleNamespace
        b = self.b
        cookie = Path(self.directory.name) / 'bridge-cookie'
        cookie.write_bytes(b'synthetic')
        b.config['common'].update(username='synthetic', password='', use_cookies='true')
        b.account_store = SimpleNamespace(active='a' * 32, rows=[], get=lambda _: dict(uid='u', label='Synthetic'),
                                          cookie_path=lambda _: cookie)
        b.course_identity = b.identity()
        b.courses = [dict(courseId='c', clazzId='l', cpi='p', title='Synthetic')]
        # Start through the real path so the launch scope is frozen, then stop.
        b._begin('run', False, [['c', 'l']])
        b._task_model.reset([dict(id='c:l:p1:0', title='One', course='Synthetic', status='stopped',
                                  courseId='c', clazzId='l', pointId='p1')])
        b.controller.active = True
        b.controller.stopping = True
        b.controller.active = False
        b.controller.finished.emit(0)
        b.controller.stopping = False
        self.bridge.mark('summary')
        self.bridge.flush()
        state = self.state['summary']
        self.assertTrue(state['resumeScoped'])
        self.assertTrue(state['resume']['enabled'], state['resume']['reason'])

        # Turning on a write policy must demand confirmation, not start. The
        # control stays clickable so the user can actually open that gate.
        b.config['tiku']['submit'] = 'true'
        self.bridge.mark('summary')
        self.bridge.flush()
        self.assertTrue(self.state['summary']['resume']['clickable'],
                        'a pending confirmation must still be reachable')
        self.assertTrue(self.state['summary']['resume']['needsConfirmation'])
        with patch.object(b.controller, 'start') as start:
            b.resumeTasks()
            self.bridge.flush()
            start.assert_not_called()
            self.assertTrue(any(m['type'] == 'event' and m['name'] == 'startConfirmationRequested'
                                for m in self.messages))
            b.confirmStartTasks()
        start.assert_called_once()
        self.assertEqual(start.call_args.args[0]['resume']['tasks'][0]['pointId'], 'p1')

    def test_questions_reset_and_ai_usage_reach_the_native_transport(self):
        """Scope switches must reach the native filters, and AI usage must be sent."""
        b = self.b
        self.bridge.mark('summary')
        self.bridge.flush()
        self.assertEqual(set(self.state['summary']['aiUsage']),
                         {'requests', 'limit', 'known', 'unknown', 'tokens'})
        b.ai_usage.update(requests=3, limit=5, known=1, unknown=1, tokens=42)
        self.bridge.mark('summary')
        self.bridge.flush()
        self.assertEqual(self.state['summary']['aiUsage'],
                         dict(requests=3, limit=5, known=1, unknown=1, tokens=42))

        self.messages.clear()
        b.filterQuestions(True, 'task-1')
        self.bridge.flush()
        self.assertFalse(any(m['type'] == 'event' and m['name'] == 'questionsReset' for m in self.messages),
                         'plain filtering must not reset the native controls')

        b.reset_question_filters()
        self.bridge.flush()
        resets = [m for m in self.messages if m['type'] == 'event' and m['name'] == 'questionsReset']
        self.assertEqual(len(resets), 1)
        self.assertEqual(list(resets[0]['args']), [])
        # The reset stays an event, never an unsupported RPC method.
        from chaoxing_desktop.winui_bridge import COMMANDS
        self.assertNotIn('reset_question_filters', COMMANDS)

    def test_cleared_filters_after_reset_are_accepted_not_stale(self):
        """Re-applying the cleared filters after a reset must be a valid, empty filter."""
        b = self.b
        b.filterQuestions(True, 'task-1')
        b.filterDiscussions('query', 'draft')
        b.filterActivities('query', 'draft')
        b.filterAttention('query', 'manual')
        b.reset_question_filters()
        self.bridge.flush()
        self.assertTrue(any(m['type'] == 'event' and m['name'] == 'questionsReset'
                            for m in self.messages))
        for method, args in (('filterQuestions', [False, '']),
                             ('filterDiscussions', ['', '']),
                             ('filterActivities', ['', '']),
                             ('filterAttention', ['', ''])):
            with self.subTest(method=method):
                self.messages.clear()
                self.bridge.dispatch({'id': 900, 'method': method, 'args': args, 'path': ''})
                responses = [m for m in self.messages if m['type'] == 'response']
                self.assertTrue(responses)
                self.assertNotIn('error', responses[-1])
        self.assertEqual(self.state['summary']['questionCount'], 0)


    def test_bank_edits_expire_health_without_resending_providers(self):
        for key, value in (('go_endpoint', 'https://example.invalid/bank'),
                           ('go_authorization', 'synthetic-review-credential')):
            with self.subTest(key=key):
                self.b.remember_check('TikuGo', '连接可用')
                self.bridge.mark('summary')
                self.bridge.flush()
                self.assertIn('连接可用', self.state['serviceHealth']['TikuGo'])
                self.messages.clear()
                self.b.setValue('question_bank', key, value)
                self.bridge.flush()
                self.assertIn('已过期', self.state['serviceHealth']['TikuGo'])
                self.assertNotIn('providers', self.messages[-1]['data'])


if __name__ == '__main__':
    unittest.main()
