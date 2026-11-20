import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / 'vendor' / 'chaoxing') not in sys.path:
    sys.path.insert(0, str(ROOT / 'vendor' / 'chaoxing'))

from chaoxing_desktop.answers import AIClient, AnswerRouter
from chaoxing_desktop.config import Redactor, defaults, to_payload
from chaoxing_desktop.controller import Controller
from chaoxing_desktop.query_result import safe_record
from chaoxing_desktop.worker import Channel


def json_response(body, status=200, headers=None):
    resp = requests.Response()
    resp.status_code = status
    resp.headers.update(headers or {'Content-Type': 'application/json'})
    resp._content = json.dumps(body, ensure_ascii=False).encode('utf-8')
    resp._content_consumed = True
    return resp


def sse_response(chunks, status=200):
    resp = requests.Response()
    resp.status_code = status
    resp.headers['Content-Type'] = 'text/event-stream'
    lines = [f'data: {json.dumps(c, ensure_ascii=False)}\n\n' for c in chunks]
    lines.append('data: [DONE]\n\n')
    resp._content = ''.join(lines).encode('utf-8')
    resp._content_consumed = True
    return resp


class AIResilienceTest(unittest.TestCase):
    def setUp(self):
        self.config = to_payload(defaults())
        self.config['ai'].update(
            enabled='true',
            vision='true',
            check_connection='false',
            retries='1',
            endpoint='https://model.invalid:8443/v1',
            key='internal-ai-key',
            model='synthetic',
            min_interval_seconds='0',
        )
        self.channel = Channel(io.StringIO(), io.StringIO(), Redactor(self.config))
        self.ai = AIClient(self.config['ai'])

    def test_protocol_error_does_not_disable_subsequent_questions(self):
        q1 = dict(id='1', title='第一题', type='judgement', options=['正确', '错误'])
        q2 = dict(id='2', title='第二题', type='judgement', options=['正确', '错误'])
        calls = []

        def fake_post(_owner, _method, _url, **kwargs):
            calls.append(kwargs['json'])
            user_text = kwargs['json']['messages'][-1]['content'][0]['text']
            if '第一题' in user_text:
                return json_response({'choices': [{'message': {'content': 'not-json'}, 'finish_reason': 'stop'}]})
            return json_response({'choices': [{'message': {'content': '{"thought":"ok","Answer":["正确"]}'}, 'finish_reason': 'stop'}]})

        with patch('chaoxing_desktop.answers.service_request', side_effect=fake_post):
            router = AnswerRouter(self.config, self.channel, ai=self.ai)
            results = router.query_all([q1, q2])

        self.assertIsNone(results[0])
        self.assertEqual(results[1], '正确')
        self.assertEqual(router.ai_unavailable, '')
        self.assertTrue(len(calls) >= 3)  # q1 retried with feedback, q2 still ran

    def test_feedback_self_correction_and_json_mode_400_fallback(self):
        q = dict(id='1', title='1+1=2', type='judgement', options=['正确', '错误'])
        seen_payloads = []

        def fake_post(_owner, _method, _url, **kwargs):
            seen_payloads.append(dict(kwargs['json']))
            if 'response_format' in kwargs['json']:
                return json_response({'error': 'unsupported response_format'}, status=400)
            if len(seen_payloads) == 2:
                return json_response({'choices': [{'message': {'content': '{"Answer":[]}'}, 'finish_reason': 'stop'}]})
            return sse_response([
                {'choices': [{'delta': {'content': '分析完成：\n{"thought":"1+1=2","Answer":['}}]},
                {'choices': [{'delta': {'content': '"正确"]}'}, 'finish_reason': 'stop'}]},
            ])

        with patch('chaoxing_desktop.answers.service_request', side_effect=fake_post):
            router = AnswerRouter(self.config, self.channel, ai=self.ai)
            results = router.query_all([q])

        self.assertEqual(results[0], '正确')
        self.assertIn('response_format', seen_payloads[0])
        self.assertNotIn('response_format', seen_payloads[1])
        self.assertTrue(any('上轮错误反馈' in m['content'] for m in seen_payloads[-1]['messages'] if m['role'] == 'system'))

    def test_safe_record_preserves_local_file_images_and_validates(self):
        rec = {
            'id': '["local","","","1","0:1"]',
            'run_id': 'local',
            'task_id': '',
            'work_id': '',
            'question_id': '1',
            'attempt': '0:1',
            'title': '求<img src="https://formula.invalid/q.png">的极值',
            'options': 'A. <img src="https://formula.invalid/a.png">',
            'kind': 'single',
            'stage': 'resolved',
            'source': 'ai',
            'reason': 'AI 返回答案，请核对',
            'answer': 'A',
            'match_mode': 'letter',
            'score': 1.0,
            'answer_letters': 'A',
            'query_attempts': 0,
            'candidates': 0,
            'requests': 0,
            'retries': 0,
            'elapsed_ms': 0,
            'ai_requests': 1,
            'ai_retries': 0,
            'ai_elapsed_ms': 100,
            'manual_elapsed_ms': 0,
            'total_elapsed_ms': 100,
            'attempts': [],
            'images': ['file:///C:/tmp/abc.png', 'https://formula.invalid/leak.png'],
        }
        cleaned = safe_record(rec)
        self.assertEqual(cleaned['images'], ['file:///C:/tmp/abc.png'])
        self.assertIn('[图片]', cleaned['title'])
        self.assertNotIn('formula.invalid', json.dumps(cleaned))
        self.assertTrue(Controller._valid_event({'type': 'question_result', 'record': cleaned}))


if __name__ == '__main__':
    unittest.main()
