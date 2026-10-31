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
        self.assertIn('0.4.2', message['text'])

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


if __name__ == '__main__':
    unittest.main()
