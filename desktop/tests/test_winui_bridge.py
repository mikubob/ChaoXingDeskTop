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

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer, QUrl
from PySide6.QtGui import QImage

from chaoxing_desktop.config import defaults, to_payload
from chaoxing_desktop.controller import Controller
from chaoxing_desktop.image_cache import image_cache_root, profile_image_cache
from chaoxing_desktop.question_model import NUMBERS, STRINGS
from chaoxing_desktop.worker import _exam_image_src
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

    def test_offline_exam_workflow_and_service_config_commands(self):
        app = QCoreApplication.instance() or QCoreApplication([])
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, CHAOXING_DESKTOP_DATA=directory):
            messages = []
            bridge = Bridge(app, messages.append)
            self.assertTrue(bridge.start())
            try:
                initial_state = next(m['data'] for m in messages if m.get('type') == 'state')
                self.assertIn('exam', initial_state)
                self.assertIn('examProviders', initial_state)

                next_id = 0

                def call_bridge(method, *args):
                    nonlocal next_id
                    next_id += 1
                    messages.clear()
                    bridge.dispatch(dict(id=next_id, method=method, args=list(args)))
                    bridge.flush()
                    resp = next(m for m in messages if m.get('type') == 'response' and m.get('id') == next_id)
                    return resp

                def wait_for(predicate, timeout_ms=10000):
                    loop = QEventLoop()
                    timer = QTimer()
                    timer.setSingleShot(True)
                    timer.timeout.connect(loop.quit)
                    poll = QTimer()
                    poll.setInterval(20)
                    poll.timeout.connect(lambda: loop.quit() if predicate() else None)
                    timer.start(timeout_ms)
                    poll.start()
                    while not predicate() and timer.isActive():
                        loop.exec()
                    poll.stop()
                    timer.stop()
                    self.assertTrue(predicate(), '等待后台异步状态超时')

                # 1. 服务配置命令测试：copyExamServiceConfig / toggleProvider / moveProvider / aiProfileAction
                call_bridge('setValue', 'ai', 'endpoint', 'https://shared-ai.invalid/v1')
                call_bridge('setValue', 'ai', 'key', 'sk-shared-key')
                call_bridge('setValue', 'ai', 'model', 'shared-model')
                call_bridge('setValue', 'question_bank', 'providers', 'TikuYanxi')
                call_bridge('setValue', 'question_bank', 'yanxi_tokens', 'yanxi-token-1')

                self.assertIs(call_bridge('copyExamServiceConfig', 'ai')['result'], True)
                self.assertEqual(bridge.backend.config['exam_ai']['model'], 'shared-model')
                self.assertEqual(bridge.backend.config['exam_ai']['key'], 'sk-shared-key')

                self.assertIs(call_bridge('copyExamServiceConfig', 'bank')['result'], True)
                self.assertEqual(bridge.backend.config['exam_question_bank']['providers'], 'TikuYanxi')
                self.assertEqual(bridge.backend.config['exam_question_bank']['yanxi_tokens'], 'yanxi-token-1')

                call_bridge('toggleProvider', 'TikuGo', True, 'exam_question_bank')
                self.assertIn('TikuGo', bridge.backend.config['exam_question_bank']['providers'].split(','))
                self.assertNotIn('TikuGo', bridge.backend.config['question_bank']['providers'].split(','))

                go_idx = bridge.backend.exam_provider_order.index('TikuGo')
                call_bridge('moveProvider', go_idx, 1 if go_idx == 0 else -1, 'exam_question_bank')
                self.assertNotEqual(bridge.backend.exam_provider_order.index('TikuGo'), go_idx)

                call_bridge('setValue', 'ai', 'model', 'profile-saved-model')
                self.assertIs(call_bridge('aiProfileAction', 'save', '考试专用档案')['result'], True)
                profiles = call_bridge('listAiProfiles')['result']
                self.assertEqual(len(profiles), 1)
                profile_id = profiles[0]['id']
                self.assertIs(call_bridge('aiProfileAction', 'load', profile_id, 'exam_ai')['result'], True)
                self.assertEqual(bridge.backend.config['exam_ai']['model'], 'profile-saved-model')

                # 2. 离线考试完整链路：loadDemo -> loadExams -> openExam -> requestExamSuggestions -> setExamAnswer ->
                #    再次 requestExamSuggestions（验证手动题不被覆盖）-> saveExamDraft -> closeExam -> 重新 openExam -> submitExam
                call_bridge('loadDemo')
                wait_for(lambda: not bridge.backend.busy and bridge.backend.demo_mode)

                self.assertIs(call_bridge('loadExams')['result'], True)
                wait_for(lambda: not bridge.backend.exam['busy'] and len(bridge.backend.exam['list']) == 4)
                exam_list = bridge.backend.exam['list']
                statuses = {item['examId']: (item['status'], item['nativeSupported']) for item in exam_list}
                self.assertEqual(
                    statuses,
                    {
                        'demo-exam-101': ('ready', True),
                        'demo-exam-102': ('not_started', False),
                        'demo-exam-103': ('ended', False),
                        'demo-exam-104': ('proctored', False),
                    },
                )

                # openExam(confirmed=False) 返回进入须知
                preview = call_bridge('openExam', 'demo-exam-101', False)['result']
                self.assertEqual(preview['examId'], 'demo-exam-101')
                self.assertFalse(preview['hasLocalDraft'])
                self.assertTrue(preview['startsTimer'])

                # openExam(confirmed=True) 启动开卷
                self.assertIs(call_bridge('openExam', 'demo-exam-101', True)['result'], True)
                wait_for(lambda: not bridge.backend.exam['busy'] and bool(bridge.backend.exam['current']))
                current = bridge.backend.exam['current']
                self.assertEqual(current['examId'], 'demo-exam-101')
                self.assertEqual(current['totalCount'], 5)
                self.assertEqual(current['answeredCount'], 0)
                q105 = next(q for q in current['questions'] if q['id'] == '105')
                self.assertTrue(q105['images'] and q105['images'][0].startswith('file://'))
                image_path = Path(QUrl(q105['images'][0]).toLocalFile())
                self.assertTrue(image_path.is_file())
                self.assertTrue(_exam_image_src(q105['images'][0], image_path.parent.parent).startswith('data:image/png;'))

                # 考试打开期间锁定账号切换
                call_bridge('addAccount')
                self.assertIn('请先关闭当前考试', bridge.backend.summary['message'])

                # 首次 requestExamSuggestions（image_confirm_policy = ask，验证发图确认交互 replyExamInput）
                assist_preview = call_bridge('requestExamSuggestions', 'all', False, False)['result']
                self.assertEqual(assist_preview['questionCount'], 5)
                self.assertGreaterEqual(assist_preview['imageCount'], 1)

                self.assertIs(call_bridge('requestExamSuggestions', 'all', True, False)['result'], True)
                wait_for(lambda: bool(bridge.backend.exam['input']) or not bridge.backend.exam['busy'])
                if bridge.backend.exam['input']:
                    self.assertIs(call_bridge('replyExamInput', 'allow-images', False)['result'], True)
                wait_for(lambda: not bridge.backend.exam['busy'])

                current = bridge.backend.exam['current']
                self.assertEqual(current['answeredCount'], 5)
                q101 = next(q for q in current['questions'] if q['id'] == '101')
                self.assertEqual(q101['answer']['option_ids'], ['A'])
                self.assertTrue(q101['source'].startswith('bank:'))

                # 手动修改第 1 题为 C，标记 source='manual' 与 dirty=True
                self.assertIs(call_bridge('setExamAnswer', '101', {'option_ids': ['C']})['result'], True)
                current = bridge.backend.exam['current']
                self.assertTrue(current['dirty'])
                q101 = next(q for q in current['questions'] if q['id'] == '101')
                self.assertEqual(q101['answer']['option_ids'], ['C'])
                self.assertEqual(q101['source'], 'manual')

                # 有未保存草稿时 closeExam(discard=False) 与窗口 close() 均返回 'confirm'
                self.assertEqual(call_bridge('closeExam', False)['result'], 'confirm')
                self.assertEqual(call_bridge('close')['result'], 'confirm')
                self.assertIsNone(call_bridge('cancelClose')['result'])

                # 再次整卷 requestExamSuggestions（replaceManual=False），验证手动修改的题不被覆盖
                call_bridge('setValue', 'ai', 'image_confirm_policy', 'always_allow')
                after_manual_preview = call_bridge('requestExamSuggestions', 'all', False, False)['result']
                self.assertEqual(after_manual_preview['questionCount'], 4)
                self.assertIs(call_bridge('requestExamSuggestions', 'all', True, False)['result'], True)
                wait_for(lambda: not bridge.backend.exam['busy'])
                q101_after = next(q for q in bridge.backend.exam['current']['questions'] if q['id'] == '101')
                self.assertEqual(q101_after['answer']['option_ids'], ['C'])
                self.assertEqual(q101_after['source'], 'manual')

                # 保存草稿 -> 关闭试卷 -> 重新打开试卷（验证草稿恢复）
                self.assertIs(call_bridge('saveExamDraft', False)['result'], True)
                self.assertFalse(bridge.backend.exam['current']['dirty'])
                self.assertIs(call_bridge('closeExam', False)['result'], True)
                self.assertEqual(bridge.backend.exam['current'], {})

                reopen_preview = call_bridge('openExam', 'demo-exam-101', False)['result']
                self.assertTrue(reopen_preview['hasLocalDraft'])
                self.assertIs(call_bridge('openExam', 'demo-exam-101', True)['result'], True)
                wait_for(lambda: not bridge.backend.exam['busy'] and bool(bridge.backend.exam['current']))
                restored = bridge.backend.exam['current']
                self.assertEqual(restored['answeredCount'], 5)
                q101_restored = next(q for q in restored['questions'] if q['id'] == '101')
                self.assertEqual(q101_restored['answer']['option_ids'], ['C'])
                self.assertEqual(q101_restored['source'], 'manual')

                # 交卷：submitExam -> 验证状态变为 simulated_submitted
                self.assertIs(
                    call_bridge('submitExam', restored['examId'], restored['attemptId'])['result'],
                    True,
                )
                wait_for(
                    lambda: not bridge.backend.exam['busy']
                    and bridge.backend.exam['current'].get('state') == 'simulated_submitted'
                )
                self.assertEqual(bridge.backend.exam['current']['state'], 'simulated_submitted')
                self.assertEqual(bridge.backend.exam['current']['stateText'], '已交卷（模拟）')
                self.assertIn('已交卷（模拟）', call_bridge('exportExam')['result'])
                self.assertIn('答案：C', call_bridge('exportExam')['result'])
                list_item = next(i for i in bridge.backend.exam['list'] if i['examId'] == 'demo-exam-101')
                self.assertEqual(list_item['status'], 'simulated_submitted')
                self.assertIs(call_bridge('closeExam', False)['result'], True)
                with patch.object(bridge.backend._exam_controller, 'start') as start:
                    self.assertIs(call_bridge('openExam', restored['examId'], True)['result'], True)
                    start.assert_not_called()
                self.assertIs(call_bridge('closeExam', False)['result'], True)

                self.assertIs(call_bridge('loadHomework', '')['result'], True)
                wait_for(lambda: not bridge.backend.homework['busy'] and bool(bridge.backend.homework['current']))
                homework = bridge.backend.homework['current']
                self.assertEqual(homework['supportedCount'], 5)
                self.assertFalse(homework['questions'][-1]['supported'])
                self.assertIs(call_bridge('requestHomeworkSuggestions', 'all', True, False)['result'], True)
                wait_for(lambda: not bridge.backend.homework['busy'])
                self.assertEqual(bridge.backend.homework['assist']['completed'], 5)
                self.assertEqual(bridge.backend.homework['current']['answeredCount'], 5)
                self.assertIs(call_bridge('setHomeworkAnswer', 'hw-104', {'blanks': ['32', '128']})['result'], True)
                self.assertEqual(bridge.close_request('close'), 'confirm')
                self.assertIs(bridge.close_request('saveAndClose'), True)
                saved, _ = bridge.backend._homework_store().get_draft(homework['draftId'])
                self.assertEqual(saved['answers']['hw-104']['blanks'], ['32', '128'])
                self.assertEqual(saved['answers']['hw-104']['source'], 'manual')
            finally:
                bridge.timer.stop()
                bridge.dispose()
            self.assertFalse(image_path.exists())

    def test_real_exam_submit_unknown_verify_and_identity_drop(self):
        app = QCoreApplication.instance() or QCoreApplication([])
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, CHAOXING_DESKTOP_DATA=directory):
            messages = []
            bridge = Bridge(app, messages.append)
            self.assertTrue(bridge.start())
            try:
                backend = bridge.backend
                backend.demo_mode = False
                # 配置一个隔离的真实账号上下文（不发起任何网络请求）
                row = backend.account_store.add('13800138000', '')
                acc_id = row['id']
                backend.account_store.update(acc_id, uid='10086')
                cp = backend.account_store.cookie_path(acc_id)
                cp.parent.mkdir(parents=True, exist_ok=True)
                cp.write_text('# Netscape HTTP Cookie File\n', encoding='utf-8')
                backend.config['common']['username'] = '13800138000'
                backend.config['common']['use_cookies'] = 'true'

                # 注入一条真实开卷事件
                backend._exam_operation = 'exam_open'
                backend._exam_identity = backend._exam_identity_snapshot()
                opened = {
                    'type': 'exam_opened',
                    'run_id': backend._exam_controller.run_id,
                    'server_now_utc': '2026-11-23T09:00:00+00:00',
                    'platform_answers': {},
                    'form_meta': {'submit_url': 'https://mooc1-api.chaoxing.com/exam-ans/exam/test/reVersionSubmitTestNew'},
                    'paper': {
                        'course_id': '100001',
                        'clazz_id': '200001',
                        'course_title': '测试课',
                        'exam_id': '400001',
                        'attempt_id': '500001',
                        'title': '模拟考试',
                        'deadline_utc': '2026-11-23T10:00:00+00:00',
                        'demo': False,
                        'questions': [
                            {'id': '800001', 'no': 1, 'type': 'single', 'title': '1+1=?', 'options': [{'id': 'A', 'text': '2'}], 'blank_count': 0},
                        ],
                    },
                }
                with patch('chaoxing_desktop.workbench_backend.ExamDraftStore.save', side_effect=ValueError('考试草稿容量已满')):
                    backend.on_exam_event(opened)
                self.assertEqual(backend.exam['current'], {})
                self.assertEqual(backend.exam['feedback']['message'], '考试草稿容量已满')

                backend.on_exam_event(opened)
                self.assertEqual(backend.exam['current']['state'], 'local_draft')
                with patch.object(backend, '_exam_remaining_seconds', return_value=0), patch.object(backend._exam_controller, 'start') as start:
                    self.assertFalse(backend.setExamAnswer('800001', {'option_ids': ['A']}))
                    self.assertFalse(backend.saveExamDraft(True))
                    self.assertFalse(backend.submitExam('400001', '500001'))
                    start.assert_not_called()
                self.assertEqual(backend.exam['current']['state'], 'local_draft')

                # 平台保存成功 -> platform_saved
                backend.setExamAnswer('800001', {'option_ids': ['A']})
                with patch.object(backend._exam_controller, 'start'):
                    backend.saveExamDraft(True)
                    backend._exam_operation = 'exam_save'
                    backend.on_exam_event({
                        'type': 'exam_saved',
                        'run_id': backend._exam_controller.run_id,
                        'exam_id': '400001',
                        'attempt_id': '500001',
                        'verified': True,
                        'reason': '平台已保存并核对一致。',
                    })
                self.assertEqual(backend.exam['current']['state'], 'platform_saved')

                # 交卷先写 submit_unknown；worker 异常退出后保持 submit_unknown，且拒绝重复提交
                with patch.object(backend._exam_controller, 'start'):
                    backend.submitExam('400001', '500001')
                    self.assertEqual(backend.exam['current']['state'], 'submit_unknown')
                    backend.on_exam_finished(1)
                self.assertEqual(backend.exam['current']['state'], 'submit_unknown')
                backend.submitExam('400001', '500001')
                self.assertIn('禁止重复提交', backend.summary['message'])

                # 只读核验确认收卷后转为 submitted
                with patch.object(backend._exam_controller, 'start'):
                    backend.verifyExamSubmit('400001', '500001')
                    backend._exam_operation = 'exam_verify'
                    backend.on_exam_event({
                        'type': 'exam_submitted',
                        'run_id': backend._exam_controller.run_id,
                        'exam_id': '400001',
                        'attempt_id': '500001',
                        'state': 'submitted',
                        'reason': '平台只读查看页已确认收卷。',
                    })
                self.assertEqual(backend.exam['current']['state'], 'submitted')

                # 账号／Cookie 变化时丢弃迟到事件
                backend._exam_identity = ('other-acc', '999', 0)
                backend.on_exam_event({
                    'type': 'exam_list',
                    'run_id': backend._exam_controller.run_id,
                    'exams': [{'examId': 'polluted'}],
                    'demo': False,
                })
                self.assertEqual(backend.exam['list'], [])
            finally:
                bridge.timer.stop()
                bridge.dispose()


if __name__ == '__main__':
    unittest.main()
