import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage
import requests

from chaoxing_desktop.answers import AIClient, AnswerRouter
from chaoxing_desktop.config import defaults, Redactor, to_payload
from chaoxing_desktop.controller import Controller
from chaoxing_desktop.worker import Channel

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vendor' / 'chaoxing'))
from api.answer import TikuYanxi
from api.base import Chaoxing, StudyResult
from api.decode import _extract_choices, _extract_title


TITLE = 'https://formula.invalid/stem.png?token=synthetic'
B_IMAGE = 'https://formula.invalid/b.png'
A_IMAGE = 'https://formula.invalid/a.png'


def image(url):
    return f'<img src="{url}">'


def png(color):
    picture = QImage(24, 16, QImage.Format.Format_RGB32)
    picture.fill(color)
    output = QBuffer()
    output.open(QIODevice.OpenModeFlag.WriteOnly)
    assert picture.save(output, 'PNG')
    return bytes(output.data())


def response(body=None, *, content=b'', status=200):
    result = requests.Response()
    result.status_code = status
    result._content = content if body is None else json.dumps(body, ensure_ascii=False).encode()
    result._content_consumed = True
    return result


def formula(kind='single'):
    return dict(id='123', title='求 ' + image(TITLE) + ' 的导数。', type=kind,
                options=['A. ' + image(A_IMAGE), 'B. ' + image(B_IMAGE),
                         'C. ' + image(A_IMAGE), 'D. ' + image(TITLE)],
                answerField={'answertype123': '0'})


class FormulaImagesTest(unittest.TestCase):
    def setUp(self):
        self.config = to_payload(defaults())
        self.config['ai'].update(vision='true', check_connection='false', retries='0',
                                 endpoint='https://model.invalid:8443/v1',
                                 key='internal-ai-key', model='synthetic', min_interval_seconds='0')
        self.channel = Channel(io.StringIO(), io.StringIO(), Redactor(self.config))
        self.channel.ask = Mock(return_value='allow-images')
        self.ai = AIClient(self.config['ai'])
        self.images = dict(zip((TITLE, A_IMAGE, B_IMAGE), (png(0x112233), png(0x995544), png(0x887766))))
        self.get = self.start_patch('chaoxing_desktop.http.service_request',
                                    side_effect=lambda _owner, _method, url, **_: response(content=self.images[url]))
        self.post = self.start_patch('chaoxing_desktop.answers.service_request')
        self.set_reply('{"Answer":["B"]}')

    def start_patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def set_reply(self, text):
        self.post.return_value = response({'choices': [{'message': {'content': text}}]})

    def records(self):
        events = [json.loads(line) for line in self.channel.output.getvalue().splitlines()]
        records = {}
        for event in events:
            if event['type'] == 'question_result':
                self.assertTrue(Controller._valid_event(event))
                records[event['record']['question_id']] = event['record']
        return records

    def test_consent_order_cache_and_platform_form(self):
        q = formula()
        router = AnswerRouter(self.config, self.channel, ai=self.ai)
        platform = SimpleNamespace(tiku=router, kwargs={}, desktop_quiz_guard=None,
                                   _post_work_once=Mock(return_value=response({'status': True})))
        with patch('api.base.SessionManager.get_session', return_value=SimpleNamespace(get=Mock(return_value=response(content=b'fixture')))), \
                patch('api.base.decode_questions_info', return_value={'questions': [q]}):
            result = Chaoxing.study_work(platform, {'clazzId': 'demo', 'courseId': 'demo'},
                                         {'jobid': 'work-demo', 'enc': ''},
                                         {'knowledgeid': '1', 'ktoken': '', 'cpi': ''})
        self.assertIs(result, StudyResult.SUCCESS)
        form = platform._post_work_once.call_args.args[1]
        self.assertEqual(form['answer123'], 'B')
        self.assertEqual(form['pyFlag'], '1')
        prompt = self.channel.ask.call_args.args[0]
        self.assertIn('https://model.invalid:8443', prompt)
        self.assertIn('1 道章节测验题', prompt)
        self.assertIn('5 处图片', prompt)
        self.assertNotIn('formula.invalid', prompt)
        payload = self.post.call_args.kwargs
        content = payload['json']['messages'][-1]['content']
        self.assertEqual([item['type'] for item in content], ['text', 'image_url'] * 5)
        self.assertEqual([item['text'].splitlines()[-1].strip() for item in content[::2]],
                         ['题干：求', 'A.', 'B.', 'C.', 'D.'])
        self.assertEqual(content[3], content[7])
        self.assertEqual(content[1], content[9])
        self.assertNotEqual(content[3], content[5])
        self.assertTrue(all(item['image_url']['url'].startswith('data:image/png;base64,') for item in content[1::2]))
        self.assertEqual([call.args[2] for call in self.get.call_args_list], [TITLE, A_IMAGE, B_IMAGE])
        for call in self.get.call_args_list:
            self.assertNotIn('Cookie', call.kwargs['headers'])
            self.assertNotIn('Authorization', call.kwargs['headers'])
        self.assertEqual(payload['headers']['Authorization'], 'Bearer internal-ai-key')
        row = self.records()['123']
        self.assertEqual((row['source'], row['answer_letters'], row['answer']), ('ai', 'B', '[图片]'))
        self.assertIn('B. [图片]', row['options'])
        self.assertNotIn('formula.invalid', self.channel.output.getvalue())
        old = dict(row)
        old.pop('answer_letters')
        self.assertTrue(Controller._valid_event(dict(type='question_result', record=old)))

    def test_mixed_option_dom_order_and_multiple_validation(self):
        item = BeautifulSoup('<li aria-label="B. x + y 选择"><b>B.</b>x<img src="//formula.invalid/b.png">+ y</li>', 'html.parser').li
        self.assertEqual(_extract_choices(item), 'B.x<img src="//formula.invalid/b.png">+ y')
        q = formula('multiple')
        q['options'][1] = _extract_choices(item)
        self.set_reply('{"Answer":["d","B"]}')
        router = AnswerRouter(self.config, self.channel, ai=self.ai)
        answer = router.query(q)
        self.assertEqual(router.choice_letters(answer, q), 'BD')
        content = self.post.call_args.kwargs['json']['messages'][-1]['content']
        self.assertTrue(content[4]['text'].endswith('B.x'))
        self.assertTrue(content[6]['text'].startswith('+ y'))
        for output, reason in (
            ('{"Answer":["B","b"]}', '编号无效'),
            ('{"Answer":["B","Z"]}', '编号无效'),
            ('{"Answer":[]}', '空答案'),
            ('', '空响应'),
            ('{"Answer":[1]}', '选项编号数组'),
            ('bad-json', '有效的 JSON'),
        ):
            with self.subTest(output=output):
                self.set_reply(output)
                self.assertIsNone(router.query(q))
                self.assertIn(reason, self.records()['123']['attempts'][-1]['reason'])
        self.set_reply('{"Answer":["B","D"]}')
        self.assertIsNone(router.query(formula()))
        self.post.reset_mock()
        for options in (['Brass', 'Copper', 'Zinc'], ['A. 第一项', 'B. ']):
            q = dict(formula(), options=options)
            self.assertIsNone(router.query(q))
            self.assertIn('选项编号缺失', self.records()['123']['attempts'][-1]['reason'])
        self.post.assert_not_called()

    def test_image_failures_and_random_retain_service_reasons(self):
        cases = [
            (response(status=404), 'HTTPStatusError' if False else 'HTTPError'),
            (response(content=b'not an image'), 'ValueError'),
            (response(content=b'0' * 65), 'ImageTooLarge'),
        ]
        self.ai.image_encoder.MAX_BYTES = 64
        for reply, _ in cases:
            with self.subTest(reply=reply.status_code):
                self.get.side_effect = None
                self.get.return_value = reply
                self.post.reset_mock()
                router = AnswerRouter(self.config, self.channel, ai=self.ai)
                self.assertEqual(router.query(formula()), f'<img src="{B_IMAGE}">')
                content = self.post.call_args.kwargs['json']['messages'][-1]['content']
                self.assertEqual(content[1]['image_url']['url'], TITLE)
        bad_inline = dict(formula(), title='求 ' + image('data:image/png;base64,bm90LWEtcG5n') + ' 的导数。')
        bank = TikuYanxi()
        bank.config_set({})
        bank._tokens = ['synthetic']
        bank._image_encoder = SimpleNamespace(encode=lambda _: 'data:image/png;base64,dGVzdA==')
        bank.request = Mock(return_value=response({'code': 0, 'data': {'answer':
            '请在言溪个人中心启用 AI 搜题。 https://bank.invalid/private?key=internal-ai-key'}}))
        self.config['desktop']['random_fallback'] = 'true'
        router = AnswerRouter(self.config, self.channel, [('TikuYanxi', bank)], self.ai)
        self.post.reset_mock()
        with patch('chaoxing_desktop.answers.random.choice', side_effect=lambda pairs: pairs[1]):
            answer = router.query(bad_inline)
        self.assertEqual(router.choice_letters(answer, bad_inline), 'B')
        row = self.records()['123']
        self.assertEqual((row['source'], row['answer_letters']), ('random', 'B'))
        self.assertIn('启用 AI 搜题', row['attempts'][0]['reason'])
        self.assertIn('无法解码', row['attempts'][1]['reason'])
        self.assertNotIn('bank.invalid', self.channel.output.getvalue())
        self.assertNotIn('internal-ai-key', self.channel.output.getvalue())
        self.post.assert_not_called()

    def test_skipped_images_do_not_block_text_or_contact_services(self):
        for vision, reply, reason in [('false', 'allow-images', '识图已关闭'), ('true', 'skip', '已跳过')]:
            with self.subTest(vision=vision):
                self.config['ai']['vision'] = vision
                self.channel.ask.reset_mock()
                self.channel.ask.return_value = reply
                self.post.reset_mock()
                self.set_reply('{"Answer":["第二项"]}')
                router = AnswerRouter(self.config, self.channel, ai=self.ai)
                plain = dict(id='text', title='这道题的答案是第二项。', options='A.第一项\nB.第二项', type='single')
                html = '<div>x&lt;y <img src="//formula.invalid/private-image?token=synthetic"> 求值</div>'
                mixed = dict(id='inequality', type='single', options=plain['options'],
                             title=_extract_title(BeautifulSoup(html, 'html.parser').div))
                self.assertEqual(router.query_all([formula(), mixed, plain]), [None, None, '第二项'])
                rows = self.records()
                self.assertIn(reason, rows['123']['attempts'][-1]['reason'])
                self.assertIn(reason, rows['inequality']['attempts'][-1]['reason'])
                self.assertEqual(rows['inequality']['title'], 'x<y [图片] 求值')
                self.assertEqual(self.channel.ask.call_count, int(vision == 'true'))
                self.post.assert_called_once()
                self.assertNotIn('image_url', str(self.post.call_args.kwargs['json']))
        self.get.assert_not_called()

    def test_bare_image_keeps_surrounding_text(self):
        self.channel.ask.return_value = 'skip'
        router = AnswerRouter(self.config, self.channel, ai=self.ai)
        router.query(dict(formula(), title=f'左{A_IMAGE}+右'))
        self.assertEqual(self.records()['123']['title'], '左[图片]+右')
        self.get.assert_not_called()
        self.post.assert_not_called()

    def test_text_judgement_completion_and_bank_review(self):
        q = dict(id='text', title='以下哪项是正确答案？', options='A.第一项\nB.第二项', type='single')
        bank = SimpleNamespace(_query=lambda _: 'B', last_result_question='另一道完全不同的题干',
                               last_result_options='A.第一项\nB.第三项')
        router = AnswerRouter(self.config, self.channel, [('bank', bank)], self.ai)
        self.set_reply('{"Answer":["第二项"]}')
        self.assertEqual(router.query(q), '第二项')
        self.assertIn('题干关键内容不同', self.records()['text']['attempts'][0]['reason'])
        router = AnswerRouter(self.config, self.channel, ai=self.ai)
        self.set_reply('{"Answer":["正确"]}')
        self.assertEqual(router.query(dict(id='judge', title='一加一等于二。', type='judgement')), '正确')
        self.set_reply('{"Answer":["甲","乙"]}')
        completion = dict(id='blank', title='两个空', type='completion', blank_count=2)
        self.assertEqual(router.query(completion), '["甲", "乙"]')
        self.set_reply('{"Answer":["甲"]}')
        self.assertIsNone(router.query(completion))
        self.assertIn('预期 2 空', self.records()['blank']['attempts'][-1]['reason'])
        self.get.assert_not_called()

    def test_image_confirm_policy_and_redirect_and_short_stem(self):
        from chaoxing_desktop.matching import title_similarity
        self.assertGreater(title_similarity(f'求{image(TITLE)}', f'求 {image(TITLE)}'), 0.99)
        plain = dict(id='text', title='这道题的答案是第二项。', options='A.第一项\nB.第二项', type='single')
        self.config['ai']['image_confirm_policy'] = 'always_skip'
        self.channel.ask.reset_mock()
        self.post.reset_mock()
        self.set_reply('{"Answer":["第二项"]}')
        router = AnswerRouter(self.config, self.channel, ai=self.ai)
        self.assertEqual(router.query_all([formula(), plain]), [None, '第二项'])
        self.channel.ask.assert_not_called()
        self.post.assert_called_once()
        self.config['ai']['image_confirm_policy'] = 'always_allow'
        self.channel.ask.reset_mock()
        self.post.reset_mock()
        self.set_reply('{"Answer":["B"]}')
        redirect = response(status=302)
        redirect.headers['Location'] = B_IMAGE
        self.get.side_effect = [
            redirect, response(content=self.images[B_IMAGE]),
            response(status=403), response(content=self.images[A_IMAGE]),
            response(content=self.images[B_IMAGE]),
        ]
        router = AnswerRouter(self.config, self.channel, ai=self.ai)
        self.assertEqual(router.query(formula()), f'<img src="{B_IMAGE}">')
        self.channel.ask.assert_not_called()
        self.post.assert_called_once()
