"""独立考试 HTTP 协议适配层（api/exam.py）离线单测。"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

from requests.exceptions import ReadTimeout

DESKTOP_DIR = Path(__file__).resolve().parents[1]
VENDOR_DIR = DESKTOP_DIR / "vendor" / "chaoxing"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "exam"

for p in (str(DESKTOP_DIR), str(VENDOR_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from api.exam import ExamAdapter, trusted_url  # noqa: E402
from api.homework import HomeworkAdapter  # noqa: E402
from api.responses import PlatformResponseError  # noqa: E402


def _read_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def _mock_response(text: str = "", *, status_code: int = 200, url: str = "https://mooc1-api.chaoxing.com/exam-ans/test", headers: dict | None = None, json_data: dict | None = None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.url = url
    resp.headers = headers or {}
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("not json")
    resp.close = MagicMock()
    return resp


class ExamProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = ExamAdapter()

    def test_throttled_read_retries_only_for_lists(self):
        url = "https://mooc1.chaoxing.com/demo"
        for adapter in (ExamAdapter(), HomeworkAdapter()):
            with self.subTest(adapter=type(adapter).__name__):
                session = MagicMock()
                session.get.side_effect = [
                    _mock_response(status_code=202, url=url),
                    _mock_response("<p>列表</p>", url=url),
                ]
                with self.assertRaises(PlatformResponseError):
                    adapter.read(session, url)
                self.assertEqual(session.get.call_count, 1)

                session.reset_mock()
                session.get.side_effect = [
                    _mock_response(status_code=429, url=url),
                    _mock_response("<p>列表</p>", url=url),
                ]
                with patch(f"{type(adapter).__module__}.time.sleep") as wait:
                    self.assertEqual(adapter.read(session, url, retry_throttled=True), ("<p>列表</p>", url))
                self.assertEqual(session.get.call_count, 2)
                wait.assert_called_once_with(2.0)

    def test_proctoring_checks_each_flag_and_receipt_requires_result(self):
        notes = _read_fixture("examnotes_ok.html").replace(
            'id="webNeedFaceRecognition" value="0"', 'id="webNeedFaceRecognition" value="1"'
        )
        result = self.adapter.parse_examnotes_html(notes)
        self.assertFalse(result["nativeSupported"])
        self.assertIn("人脸识别", result["reason"])
        self.assertFalse(self.adapter.parse_look_html(
            '<div class="mark_title">试卷领取时间：2026-11-23 11:00</div><div class="mark_answer"></div>'
        )["confirmed"])

    def test_trusted_url_and_auth_redirect(self) -> None:
        self.assertTrue(trusted_url("https://mooc1-api.chaoxing.com/exam-ans/exam/test"))
        with self.assertRaises(PlatformResponseError):
            trusted_url("http://mooc1-api.chaoxing.com/exam-ans/exam/test")
        with self.assertRaises(PlatformResponseError):
            trusted_url("https://evil.example.com/exam")
        with self.assertRaises(PlatformResponseError):
            trusted_url("https://user:pass@mooc1.chaoxing.com/exam")

        session = MagicMock()
        session.get.return_value = _mock_response(
            status_code=302,
            headers={"Location": "https://passport2.chaoxing.com/login?refer=x"},
        )
        with self.assertRaises(PlatformResponseError) as ctx:
            self.adapter.read(session, "https://mooc1.chaoxing.com/exam-ans/mooc2/exam/exam-list")
        self.assertEqual(ctx.exception.kind, "auth_error")

    def test_parse_exam_list_html(self) -> None:
        items = self.adapter.parse_exam_list_html(
            _read_fixture("exam_list.html"),
            course_id="100001",
            clazz_id="200001",
            cpi="300001",
            course_title="测试课程",
        )
        self.assertEqual(len(items), 4)
        by_id = {x["examId"]: x for x in items}
        self.assertTrue(by_id["400001"]["nativeSupported"])
        self.assertEqual(by_id["400001"]["status"], "ready")
        self.assertEqual(by_id["400001"]["limitMinutes"], 60)

        self.assertTrue(by_id["400002"]["nativeSupported"])
        self.assertEqual(by_id["400002"]["status"], "ready")
        self.assertIn("任务点", by_id["400002"]["reason"])

        self.assertFalse(by_id["400003"]["nativeSupported"])
        self.assertEqual(by_id["400003"]["status"], "ended")

        self.assertEqual(by_id["400004"]["status"], "submitted")

    def test_parse_exam_list_html_unquoted_stop_exam(self) -> None:
        html = """
        <div class="bottomList"><ul><li>
          <div onclick="goTest('269158889',11160375,172224156,'2027-02-13 23:59:59.0',583835232,true,'$currentEnc',0);">
            <p class="overHidden2 fl">名侦探柯南与化学探秘</p>
            <p class="status fl">已完成</p>
          </div>
          <div class="time">剩余1975小时29分钟</div>
        </li></ul></div>
        """
        items = self.adapter.parse_exam_list_html(
            html, course_id="269158889", clazz_id="159666930", cpi="515542782", course_title="化学"
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["examId"], "11160375")
        self.assertEqual(items[0]["status"], "submitted")
        self.assertTrue(items[0]["isRetest"])

    def test_parse_examnotes_unsupported_flags(self) -> None:
        ok_html = _read_fixture("examnotes_ok.html")
        ok = self.adapter.parse_examnotes_html(ok_html, official_url="https://mooc1.chaoxing.com/notes")
        self.assertTrue(ok["nativeSupported"])
        self.assertEqual(ok["attemptId"], "500001")

        for field, marker, expected_text in [
            ("isChaoxingExamPc", 'value="1"', "专用考试客户端"),
            ("needFaceRecognition", 'value="1"', "人脸识别"),
            ("snapshotMonitor", 'value="1"', "抓拍监考"),
            ("needCode", 'value="1"', "考试码"),
        ]:
            mutated = ok_html.replace(f'id="{field}" value="0"', f'id="{field}" {marker}').replace(
                f'id="{field}" value=""', f'id="{field}" {marker}'
            )
            res = self.adapter.parse_examnotes_html(mutated)
            self.assertFalse(res["nativeSupported"], field)
            self.assertIn(expected_text, res["reason"])

        captcha_html = ok_html.replace('id="captchaCheck" value="0"', 'id="captchaCheck" value="1"').replace(
            "enc=eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", "enc=?"
        )
        res_cap = self.adapter.parse_examnotes_html(captcha_html)
        self.assertFalse(res_cap["nativeSupported"])
        self.assertIn("验证码", res_cap["reason"])

    def test_open_exam_pagination_and_all_types(self) -> None:
        q0_html = _read_fixture("exam_paper_q0.html")
        q1_html = _read_fixture("exam_paper_q1.html")
        session = MagicMock()
        session.get.side_effect = [
            _mock_response(q0_html, url="https://mooc1-api.chaoxing.com/exam-ans/exam/test/reVersionTestStartNew?start=0"),
            _mock_response(q1_html, url="https://mooc1-api.chaoxing.com/exam-ans/exam/test/reVersionTestStartNew?start=1"),
        ]
        res = self.adapter.open_exam(
            session,
            {
                "courseTitle": "测试课程",
                "title": "模拟期末考试",
                "startUrl": "https://mooc1-api.chaoxing.com/exam-ans/exam/test/reVersionTestStartNew?start=0&enc=e",
            },
        )
        self.assertTrue(res["native_supported"])
        self.assertEqual(len(res["paper"]["questions"]), 2)
        self.assertEqual(res["platform_answers"]["800001"]["option_ids"], ["B"])
        self.assertEqual(res["platform_answers"]["800002"]["option_ids"], ["B", "C"])

        all_html = _read_fixture("exam_paper_all_types.html")
        parsed = self.adapter.parse_exam_page_html(all_html)
        self.assertTrue(parsed["native_supported"])
        qs = parsed["paper"]["questions"]
        self.assertEqual([q["type"] for q in qs], ["single", "multiple", "judgement", "completion", "shortanswer"])
        self.assertEqual(qs[3]["blank_count"], 2)
        self.assertEqual(parsed["image_urls"]["900001"], ["https://p.ananas.chaoxing.com/star3/origin/sample.png"])
        self.assertEqual(parsed["platform_answers"]["900003"]["option_ids"], ["正确"])
        self.assertEqual(parsed["platform_answers"]["900004"]["blanks"], ["hello", ""])
        self.assertEqual(parsed["platform_answers"]["900005"]["text"], "要点一")

    def test_open_exam_unsupported_proctoring_and_unknown_type(self) -> None:
        all_html = _read_fixture("exam_paper_all_types.html")
        monitored = all_html.replace('id="switchScreenControl" name="switchScreenControl" value="0"', 'id="switchScreenControl" name="switchScreenControl" value="1"')
        res_mon = self.adapter.parse_exam_page_html(monitored)
        self.assertFalse(res_mon["native_supported"])
        self.assertIn("切屏", res_mon["reason"])

        unknown_q = all_html.replace('name="type900005" value="4"', 'name="type900005" value="11"')
        res_unk = self.adapter.parse_exam_page_html(unknown_q)
        self.assertFalse(res_unk["native_supported"])

    def test_cxsecret_font_decoding(self) -> None:
        html = _read_fixture("exam_paper_q0.html").replace(
            "<p>1+1等于？</p>", '<p class="font-cxsecret">加密文本</p>'
        )
        fake_decoder = MagicMock()
        fake_decoder.decode.return_value = "解密后题干"
        with patch("api.exam.FontDecoder", return_value=fake_decoder):
            parsed = self.adapter.parse_exam_page_html(html)
        self.assertTrue(parsed["native_supported"])
        self.assertIn("解密后题干", parsed["paper"]["questions"][0]["title"])

    def test_save_once_zero_retry_and_readback(self) -> None:
        all_html = _read_fixture("exam_paper_all_types.html")
        parsed = self.adapter.parse_exam_page_html(all_html)
        paper = parsed["paper"]
        form_meta = parsed["form_meta"]
        answers = {
            "900001": {"option_ids": ["A"], "text": "", "blanks": []},
            "900002": {"option_ids": ["A", "C"], "text": "", "blanks": []},
            "900003": {"option_ids": ["正确"], "text": "", "blanks": []},
            "900004": {"option_ids": [], "text": "", "blanks": ["hello", "world"]},
            "900005": {"option_ids": [], "text": "要点一", "blanks": []},
        }
        updated_html = (
            all_html
            .replace('name="answer900001" value=""', 'name="answer900001" value="A"')
            .replace('name="answers900002" value=""', 'name="answers900002" value="AC"')
            .replace('<textarea name="answerEditor9000042"></textarea>', '<textarea name="answerEditor9000042">world</textarea>')
        )
        with patch("api.exam.requests.Session") as mock_sess_cls:
            post_sess = MagicMock()
            get_sess = MagicMock()
            mock_sess_cls.side_effect = [post_sess, get_sess]
            post_sess.post.return_value = _mock_response(json_data={"status": "success"})
            get_sess.get.return_value = _mock_response(updated_html)

            res = self.adapter.save_once({"UID": "1"}, paper, form_meta, answers)
            self.assertTrue(res["verified"])
            adapter_arg = post_sess.mount.call_args[0][1]
            self.assertEqual(adapter_arg.max_retries.total, 0)
            self.assertFalse(post_sess.post.call_args[1]["allow_redirects"])
            self.assertEqual(post_sess.post.call_args[1]["params"]["tempSave"], "true")

    def test_submit_once_timeout_rejected_and_verify(self) -> None:
        all_html = _read_fixture("exam_paper_all_types.html")
        parsed = self.adapter.parse_exam_page_html(all_html)
        paper, form_meta = parsed["paper"], parsed["form_meta"]

        # 1. 超时转 submit_unknown
        with patch("api.exam.requests.Session") as mock_sess_cls:
            post_sess = MagicMock()
            mock_sess_cls.return_value = post_sess
            post_sess.post.side_effect = ReadTimeout("timeout")
            res_timeout = self.adapter.submit_once({"UID": "1"}, paper, form_meta, {})
            self.assertEqual(res_timeout["state"], "submit_unknown")

        # 2. 业务拒绝转 rejected
        with patch("api.exam.requests.Session") as mock_sess_cls:
            post_sess = MagicMock()
            mock_sess_cls.return_value = post_sess
            post_sess.post.return_value = _mock_response(json_data={"status": "error", "msg": "未到交卷时间"})
            res_rej = self.adapter.submit_once({"UID": "1"}, paper, form_meta, {})
            self.assertEqual(res_rej["state"], "rejected")
            self.assertIn("未到交卷时间", res_rej["reason"])

        # 3. 提交后只读核验确认 -> submitted；未确认 -> submit_unknown
        with patch("api.exam.requests.Session") as mock_sess_cls:
            post_sess, get_sess = MagicMock(), MagicMock()
            mock_sess_cls.side_effect = [post_sess, get_sess]
            post_sess.post.return_value = _mock_response(json_data={"status": "submitted"})
            get_sess.get.return_value = _mock_response(_read_fixture("exam_look_submitted.html"))
            local_paper = {key: value for key, value in paper.items() if key != "cpi"}
            res_ok = self.adapter.submit_once({"UID": "1"}, local_paper, form_meta, {})
            self.assertEqual(res_ok["state"], "submitted")
            self.assertEqual(get_sess.get.call_args.kwargs["params"]["cpi"], form_meta["hidden_fields"]["cpi"])

        with patch("api.exam.requests.Session") as mock_sess_cls:
            post_sess, get_sess = MagicMock(), MagicMock()
            mock_sess_cls.side_effect = [post_sess, get_sess]
            post_sess.post.return_value = _mock_response(json_data={"status": "submitted"})
            get_sess.get.return_value = _mock_response(_read_fixture("exam_look_unsubmitted.html"))
            res_unk = self.adapter.submit_once({"UID": "1"}, paper, form_meta, {})
            self.assertEqual(res_unk["state"], "submit_unknown")

    def test_worker_real_exam_action_dispatch(self) -> None:
        from chaoxing_desktop.worker import real_exam_action

        channel = MagicMock()
        cx = MagicMock()
        cx.get_course_list.return_value = [{"courseId": "100001", "clazzId": "200001", "cpi": "300001", "title": "课程"}]
        session = MagicMock()
        session.cookies = {"UID": "1"}

        with patch("api.exam.ExamAdapter") as mock_cls:
            inst = mock_cls.return_value
            inst.list_exams.return_value = [{"examId": "400001"}]
            real_exam_action(channel, {"action": "exam_list"}, cx, session)
            channel.send.assert_called_with("exam_list", exams=[{"examId": "400001"}], demo=False)

            inst.verify_submitted.return_value = {"confirmed": True, "reason": "已收卷"}
            real_exam_action(
                channel,
                {"action": "exam_verify", "exam_id": "400001", "attempt_id": "500001", "course_id": "100001", "clazz_id": "200001"},
                cx,
                session,
            )
            channel.send.assert_called_with(
                "exam_submitted",
                exam_id="400001",
                attempt_id="500001",
                state="submitted",
                reason="已收卷",
            )

    def test_save_question_once_and_submit_with_rolling_enc(self) -> None:
        all_html = _read_fixture("exam_paper_all_types.html")
        parsed = self.adapter.parse_exam_page_html(all_html)
        paper = parsed["paper"]
        form_meta = parsed["form_meta"]
        q0 = paper["questions"][0]
        answers = {q0["id"]: {"option_ids": ["A"], "text": "", "blanks": []}}

        with patch("api.exam.requests.Session") as mock_sess_cls:
            post_sess = MagicMock()
            mock_sess_cls.return_value = post_sess
            post_sess.post.return_value = _mock_response(json_data={"status": "success", "data": "t1|r1|enc1"})
            res = self.adapter.save_question_once({"UID": "1"}, paper, form_meta, q0, answers)
            self.assertTrue(res["saved"])
            self.assertEqual(res["rolling_enc"], ["t1", "r1", "enc1"])
            self.assertEqual(post_sess.post.call_args.kwargs["params"]["tempSave"], "true")

        form_meta["rolling_enc"] = ["t1", "r1", "enc1"]
        with patch("api.exam.requests.Session") as mock_sess_cls:
            post_sess, get_sess = MagicMock(), MagicMock()
            mock_sess_cls.side_effect = [post_sess, get_sess]
            post_sess.post.return_value = _mock_response(json_data={"status": "submitted"})
            get_sess.get.return_value = _mock_response(_read_fixture("exam_look_submitted.html"))
            res_sub = self.adapter.submit_once({"UID": "1"}, paper, form_meta, answers)
            self.assertEqual(res_sub["state"], "submitted")
            sent_data = post_sess.post.call_args.kwargs["data"]
            self.assertEqual(sent_data["encLastUpdateTime"], "t1")
            self.assertEqual(sent_data["encRemainTime"], "r1")
            self.assertEqual(sent_data["enc"], "enc1")

    def test_load_homework_ensures_vendor_path_and_local_error_feedback(self) -> None:
        import os
        import tempfile
        from chaoxing_desktop.workbench_backend import WorkbenchBackend

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"CHAOXING_DATA_DIR": tmp}):
                backend = WorkbenchBackend(lambda *_a, **_kw: None)
                backend.demo_mode = False
                # 模拟主进程未预先把 vendor/chaoxing 加入 sys.path 的场景
                vendor_str = str(VENDOR_DIR.resolve())
                removed = [p for p in sys.path if Path(p).resolve() == VENDOR_DIR.resolve()]
                for p in removed:
                    sys.path.remove(p)
                try:
                    ok = backend.loadHomework("http://invalid.example.com/bad")
                    self.assertFalse(ok)
                    self.assertEqual(backend._homework_feedback["tone"], "danger")
                    self.assertIn("HTTPS", backend._homework_feedback["message"])
                    self.assertIn(vendor_str, [str(Path(p).resolve()) for p in sys.path])
                finally:
                    if vendor_str not in sys.path:
                        sys.path.insert(0, vendor_str)

    def test_auto_exam_state_and_optional_auto_submit(self) -> None:
        import os
        import tempfile
        from chaoxing_desktop.workbench_backend import WorkbenchBackend

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"CHAOXING_DATA_DIR": tmp}):
                backend = WorkbenchBackend(lambda *_a, **_kw: None)
                backend.demo_mode = True
                backend._exam_current = {
                    "version": 1,
                    "revision": 1,
                    "account_id": "offline-demo",
                    "uid": "demo-uid",
                    "course_id": "demo-101",
                    "clazz_id": "demo-clazz-1",
                    "exam_id": "demo-exam-101",
                    "attempt_id": "demo-attempt-1",
                    "demo": True,
                    "title": "演示考试",
                    "deadline_utc": "2099-01-01T00:00:00+00:00",
                    "state": "local_draft",
                    "prev_state": "",
                    "reason": "",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                    "questions": [
                        {"id": "101", "no": 1, "type": "single", "title": "题1", "options": [{"id": "A", "text": "A"}], "blank_count": 0},
                        {"id": "102", "no": 2, "type": "single", "title": "题2", "options": [{"id": "B", "text": "B"}], "blank_count": 0},
                    ],
                    "answers": {},
                }
                preview = backend.startAutoExam(confirmed=False, sync_to_platform=True, auto_submit=True)
                self.assertEqual(preview["questionCount"], 2)
                self.assertTrue(preview["autoSubmit"])

                # 模拟逐题事件推进 active_question_id 并在未全部答完时暂停自动交卷
                backend._exam_operation = "exam_auto"
                backend._exam_assist_state.update(
                    completed=0,
                    total=2,
                    saved_count=0,
                    auto_mode=True,
                    sync_to_platform=True,
                    auto_submit=True,
                    pending_auto_submit=False,
                    active_question_id="101",
                    target_question_ids=["101", "102"],
                )
                with patch.object(backend, "_exam_store") as mock_store:
                    mock_store.return_value.save.side_effect = lambda rec, raw: (rec, b"{}")
                    backend.on_exam_event(
                        {
                            "type": "exam_suggestion",
                            "run_id": backend._exam_controller.run_id,
                            "question_id": "101",
                            "answer": {"option_ids": ["A"], "blanks": [], "text": ""},
                            "source": "bank:TikuDemo",
                            "reason": "命中",
                            "attempts": [],
                            "platform_saved": True,
                        }
                    )
                    self.assertEqual(backend._exam_assist_state["active_question_id"], "102")
                    self.assertEqual(backend._exam_assist_state["saved_count"], 1)

                    # 第 2 题未答完即结束 -> 必须暂停自动交卷
                    backend.on_exam_event(
                        {
                            "type": "exam_assist_done",
                            "run_id": backend._exam_controller.run_id,
                            "completed": 2,
                            "total": 2,
                            "resolved": 1,
                            "saved_count": 1,
                            "message": "自动答题完成。",
                        }
                    )
                    self.assertFalse(backend._exam_assist_state["pending_auto_submit"])
                    self.assertEqual(backend._exam_feedback["tone"], "warning")

                    # 第 2 题也答完后结束 -> 标记 pending_auto_submit 并在 on_exam_finished 触发 submitExam
                    backend.on_exam_event(
                        {
                            "type": "exam_suggestion",
                            "run_id": backend._exam_controller.run_id,
                            "question_id": "102",
                            "answer": {"option_ids": ["B"], "blanks": [], "text": ""},
                            "source": "bank:TikuDemo",
                            "reason": "命中",
                            "attempts": [],
                            "platform_saved": True,
                        }
                    )
                    backend.on_exam_event(
                        {
                            "type": "exam_assist_done",
                            "run_id": backend._exam_controller.run_id,
                            "completed": 2,
                            "total": 2,
                            "resolved": 2,
                            "saved_count": 2,
                            "message": "自动答题完成。",
                        }
                    )
                    self.assertTrue(backend._exam_assist_state["pending_auto_submit"])
                    with patch.object(backend, "submitExam") as mock_submit:
                        backend.on_exam_finished(0)
                        mock_submit.assert_called_once_with("demo-exam-101", "demo-attempt-1")


if __name__ == "__main__":
    unittest.main()
