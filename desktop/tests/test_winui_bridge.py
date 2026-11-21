import configparser
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QImage

from chaoxing_desktop.question_model import NUMBERS, STRINGS
from chaoxing_desktop.winui_bridge import Bridge


DESKTOP = Path(__file__).resolve().parents[1]


class WinUIBridgeTests(unittest.TestCase):
    def test_settings_path_and_close_over_pipe(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            settings = data / 'settings.ini'
            settings.write_text('[desktop]\nauto_save = false\nauto_login = false\n', encoding='utf-8')
            diagnostics = data / 'diagnostics.ini'
            with tempfile.TemporaryFile(mode='w+b') as errors, subprocess.Popen(
                [sys.executable, str(DESKTOP / 'run.py'), '--bridge'],
                env=dict(os.environ, CHAOXING_DESKTOP_DATA=directory), cwd=DESKTOP,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
                text=True, encoding='utf-8',
            ) as process:
                frames = queue.Queue()

                def read_frames():
                    for line in process.stdout:
                        frames.put(line)
                    frames.put(None)

                reader = threading.Thread(target=read_frames, daemon=True)
                reader.start()

                def read_frame():
                    line = frames.get(timeout=15)
                    self.assertIsNotNone(line, '后台提前关闭了输出管道')
                    return json.loads(line)

                request_id = 0

                def call(method, *args, **extra):
                    nonlocal request_id
                    request_id += 1
                    frame = dict(id=request_id, method=method, args=args, **extra)
                    process.stdin.write(json.dumps(frame) + '\n')
                    process.stdin.flush()
                    messages = []
                    while (reply := read_frame())['type'] != 'response':
                        messages.append(reply)
                    self.assertEqual(reply['id'], request_id)
                    return reply, messages

                def result(method, *args, **extra):
                    reply, _ = call(method, *args, **extra)
                    self.assertNotIn('error', reply)
                    return reply['result']

                try:
                    ready, state = read_frame(), read_frame()
                    self.assertEqual((ready['type'], state['type']), ('ready', 'state'))
                    schema = ready['data']['schema']
                    policy = next(field for group in schema['ai'] for field in group['fields']
                                  if field['key'] == 'image_confirm_policy')
                    self.assertEqual(policy['kind'], 'choice')
                    self.assertEqual([choice['value'] for choice in policy['choices']],
                                     ['ask', 'always_allow', 'always_skip'])
                    self.assertEqual({key: policy['depends'][key] for key in ('section', 'key', 'value')},
                                     dict(section='ai', key='vision', value='true'))
                    self.assertTrue({'summary', 'values', 'courses'} <= state['data'].keys())
                    self.assertIn('resume', state['data']['summary'])
                    self.assertEqual(state['data']['values']['ai']['image_confirm_policy'], 'ask')
                    self.assertEqual(state['data']['values']['desktop']['auto_save'], 'false')
                    self.assertEqual(state['data']['courses'], [])

                    result('setValue', 'common', 'speed', '1.5')
                    self.assertIs(result('save'), True)
                    saved = settings.read_bytes()
                    config = configparser.ConfigParser(interpolation=None)
                    config.read_string(saved.decode('utf-8'))
                    self.assertEqual(config.get('common', 'speed'), '1.5')

                    result('fileAction', 'export_diagnostics', path=str(diagnostics))
                    self.assertIn('[common]', diagnostics.read_text(encoding='utf-8'))
                    diagnostics.write_bytes(b'keep')
                    result('fileAction', 'export_diagnostics')
                    self.assertEqual(diagnostics.read_bytes(), b'keep')
                    reply, _ = call('fileAction', 'export_diagnostics', path=123)
                    self.assertEqual(reply['error'], '文件路径无效')

                    result('setValue', 'common', 'speed', '3')
                    invalid_save, messages = call('save')
                    self.assertIs(invalid_save['result'], False)
                    self.assertIn(dict(type='event', name='fieldRequested', args=['common', 'speed']), messages)
                    self.assertEqual(messages[-1]['type'], 'state')
                    self.assertEqual(settings.read_bytes(), saved)

                    self.assertEqual(result('close'), 'confirm')
                    self.assertIs(result('saveAndClose'), False)
                    self.assertEqual(result('close'), 'confirm')
                    self.assertIsNone(result('cancelClose'))
                    self.assertIsNone(process.poll())
                    self.assertEqual(result('close'), 'confirm')
                    self.assertIs(result('discardAndClose'), True)
                    process.stdin.close()
                    self.assertEqual(process.wait(timeout=15), 0)
                    self.assertEqual(settings.read_bytes(), saved)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=5)
                    reader.join(timeout=5)

    def test_worker_images_reach_manual_input_and_question_detail(self):
        app = QCoreApplication.instance() or QCoreApplication([])
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, CHAOXING_DESKTOP_DATA=directory):
            path = Path(directory) / 'formula.png'
            image = QImage(2, 2, QImage.Format.Format_RGB32)
            image.fill(0xffffffff)
            self.assertTrue(image.save(str(path)))
            cached = path.as_uri()
            remote = 'https://formula.invalid/example.png'
            title = f'题干 <img src="{remote}">'
            messages = []
            bridge = Bridge(app, messages.append)
            try:
                bridge.backend.on_event(dict(type='input', question=dict(type='single', title=title,
                                                                         desktop_cached_images={remote: cached})))
                bridge.flush()
                self.assertEqual(messages[-1]['data']['input']['images'], [cached])

                record = {**dict.fromkeys(STRINGS, ''), **dict.fromkeys(NUMBERS, 0)}
                record.update(id='demo-question', title=title, stage='manual', attempts=[], score=0,
                              images=[cached, remote])
                bridge.backend.on_event(dict(type='question_result', record=record))
                bridge.dispatch(dict(id=1, method='questionDetail', args=[record['id']]))
                self.assertEqual(messages[-2]['data']['questions'][0]['images'], [cached])
                self.assertEqual(messages[-1]['result']['images'], [cached])
            finally:
                bridge.timer.stop()
                bridge.dispose()


if __name__ == '__main__':
    unittest.main()
