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

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
from PySide6.QtGui import QImage

from chaoxing_desktop.config import defaults, to_payload
from chaoxing_desktop.controller import Controller
from chaoxing_desktop.image_cache import image_cache_root, profile_image_cache
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
            temporary_root = data / 'temp'
            temporary_root.mkdir()
            with patch('chaoxing_desktop.image_cache.tempfile.gettempdir', return_value=str(temporary_root)):
                stale = profile_image_cache(data) / ('a' * 32) / 'image.png'
                other_profile = profile_image_cache(data / 'other') / ('b' * 32) / 'image.png'
                legacy = image_cache_root() / ('c' * 24 + '.png')
                unrelated = image_cache_root() / 'keep.txt'
            for file in (stale, other_profile, legacy, unrelated):
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(b'offline fixture')
            with tempfile.TemporaryFile(mode='w+b') as errors, subprocess.Popen(
                [sys.executable, str(DESKTOP / 'run.py'), '--bridge'],
                env=dict(os.environ, CHAOXING_DESKTOP_DATA=directory, TMP=str(temporary_root), TEMP=str(temporary_root)), cwd=DESKTOP,
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
                    self.assertFalse(stale.parent.exists())
                    self.assertFalse(legacy.exists())
                    self.assertTrue(other_profile.is_file())
                    self.assertTrue(unrelated.is_file())
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

    def test_run_image_cache_is_removed_when_worker_exits(self):
        app = QCoreApplication.instance() or QCoreApplication([])
        directory = self.enterContext(tempfile.TemporaryDirectory())
        with patch.dict(os.environ, TMP=directory, TEMP=directory), \
                patch('chaoxing_desktop.image_cache.tempfile.gettempdir', return_value=directory):
            for mode in ('complete', 'stop', 'crash'):
                with self.subTest(mode=mode):
                    controller = Controller(app)
                    loop = QEventLoop()
                    timeout = QTimer()
                    timeout.setSingleShot(True)
                    timeout.timeout.connect(loop.quit)
                    codes = []
                    controller.finished.connect(codes.append)
                    controller.finished.connect(loop.quit)
                    if mode == 'stop':
                        controller.process.started.connect(controller.stop)
                    elif mode == 'crash':
                        controller.process.started.connect(controller.process.kill)
                    controller.start(dict(action='run', demo=True, selected=[['demo-101', '01']],
                                          data_dir=directory, config=to_payload(defaults())))
                    cache = controller.image_cache_dir
                    (cache / 'image.png').write_bytes(b'offline fixture')
                    try:
                        if not codes:
                            timeout.start(10000)
                            loop.exec()
                        if mode == 'crash':
                            self.assertTrue(codes and codes[0] != 0, codes)
                        else:
                            self.assertEqual(codes, [0 if mode == 'complete' else 2])
                        self.assertFalse(cache.exists())
                    finally:
                        timeout.stop()
                        if controller.active:
                            controller.process.kill()
                            controller.process.waitForFinished(1000)
                        controller.deleteLater()


if __name__ == '__main__':
    unittest.main()
