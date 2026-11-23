"""独立作业 URL 校验、解析、本机草稿存储与只读边界单测。"""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

DESKTOP_DIR = Path(__file__).resolve().parents[1]
VENDOR_DIR = DESKTOP_DIR / "vendor" / "chaoxing"
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "homework" / "homework_mooc2.html"

for p in (str(DESKTOP_DIR), str(VENDOR_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from api.homework import HomeworkAdapter, validate_homework_url  # noqa: E402
from chaoxing_desktop.homework_drafts import (  # noqa: E402
    HomeworkDraftStore,
    MAX_DRAFTS_PER_ACCOUNT,
    apply_homework_manual_answer,
    apply_homework_suggestion,
    export_homework_text,
    homework_draft_id,
    homework_summary,
)


class HomeworkDraftsTests(unittest.TestCase):
    def test_url_whitelist_validation(self) -> None:
        valid = "https://mooc1-api.chaoxing.com/mooc-ans/mooc2/work/dowork?courseId=100001&workId=200001"
        self.assertEqual(validate_homework_url(valid), valid)

        for bad in [
            "",
            "http://mooc1-api.chaoxing.com/mooc-ans/mooc2/work/dowork?workId=1",
            "https://evil.example.com/mooc-ans/mooc2/work/dowork?workId=1",
            "https://user:pass@mooc1-api.chaoxing.com/mooc-ans/mooc2/work/dowork?workId=1",
            "https://mooc1-api.chaoxing.com/mooc-ans/mooc2/work/dowork?workId=1&password=123",
            "https://mooc1-api.chaoxing.com/exam-ans/exam/test",
        ]:
            with self.assertRaises(ValueError, msg=bad):
                validate_homework_url(bad)

    def test_parse_mooc2_homework_mixed_supported_and_unsupported(self) -> None:
        html = FIXTURE_PATH.read_text(encoding="utf-8")
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = html
        resp.url = "https://mooc1-api.chaoxing.com/mooc-ans/mooc2/work/dowork?courseId=100001&classId=200001&workId=300001"
        resp.headers = {}
        session.get.return_value = resp

        parsed = HomeworkAdapter().load_homework(session, resp.url)
        session.post.assert_not_called()
        self.assertEqual(parsed["title"], "脱敏测试作业")
        qs = parsed["questions"]
        self.assertEqual(len(qs), 5)
        self.assertEqual([q["type"] for q in qs], ["single", "multiple", "judgement", "completion", "unsupported"])
        self.assertTrue(all(q["supported"] for q in qs[:4]))
        self.assertFalse(qs[4]["supported"])
        self.assertIn("附件题", qs[4]["reason"])
        self.assertEqual(qs[3]["blank_count"], 2)
        self.assertEqual(parsed["image_urls"]["700002"], ["https://p.ananas.chaoxing.com/star4/sample_hw.jpg"])

    def test_manual_protection_export_capacity_and_corruption(self) -> None:
        html = FIXTURE_PATH.read_text(encoding="utf-8")
        parsed = HomeworkAdapter.parse_homework_html(
            html,
            url="https://mooc1-api.chaoxing.com/mooc-ans/mooc2/work/dowork?courseId=100001&workId=300001",
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = HomeworkDraftStore(tmp, "acc-1")
            did = homework_draft_id("acc-1", "100001", "300001")
            rec = {
                "draft_id": did,
                "account_id": "acc-1",
                "course_id": "100001",
                "work_id": "300001",
                "title": parsed["title"],
                "demo": False,
                "version": 0,
                "updated_at": "",
                "questions": parsed["questions"],
                "answers": {},
            }
            saved = store.save_draft(rec)
            self.assertEqual(saved["version"], 1)

            # 手动填写 Q1，写入参考答案（不覆盖 manual），并验证不支持题型拒绝手动写入
            apply_homework_manual_answer(saved, "700001", {"option_ids": ["B"]})
            self.assertFalse(apply_homework_suggestion(saved, "700001", {"option_ids": ["A"]}, "bank:TikuYanxi"))
            self.assertTrue(apply_homework_suggestion(saved, "700002", {"option_ids": ["A", "B"]}, "ai"))
            self.assertFalse(apply_homework_suggestion(saved, "700005", {"text": "x"}, "ai"))
            with self.assertRaises(ValueError):
                apply_homework_manual_answer(saved, "700005", {"text": "x"})

            saved2 = store.save_draft(saved)
            self.assertEqual(saved2["answers"]["700001"]["option_ids"], ["B"])
            self.assertEqual(saved2["answers"]["700001"]["source"], "manual")
            self.assertEqual(saved2["answers"]["700002"]["source"], "ai")

            summary = homework_summary(saved2)
            self.assertEqual(summary["answeredCount"], 2)
            self.assertEqual(summary["supportedCount"], 4)
            self.assertEqual(summary["totalCount"], 5)

            exported = export_homework_text(saved2)
            self.assertIn("未向超星平台保存或提交", exported)
            self.assertIn("手动填写", exported)
            self.assertIn("不支持自动作答", exported)

            self.assertTrue(store.delete_draft(did))
            self.assertEqual(store.list_drafts(), [])

            # 容量上限：每账号最多 50 份
            for i in range(MAX_DRAFTS_PER_ACCOUNT):
                d = dict(rec, draft_id=f"d-{i}", work_id=str(i), version=0, answers={})
                store.save_draft(d)
            with self.assertRaises(ValueError):
                store.save_draft(dict(rec, draft_id="d-overflow", work_id="999", version=0, answers={}))

            # 损坏文件 fail-closed，保留原文件
            store.path.write_text("{broken-json", encoding="utf-8")
            with self.assertRaises(ValueError):
                store.list_drafts()
            self.assertEqual(store.path.read_text(encoding="utf-8"), "{broken-json")

    def test_save_once_options_and_blanks_zero_retry(self) -> None:
        from unittest.mock import patch

        html = FIXTURE_PATH.read_text(encoding="utf-8")
        url = "https://mooc1-api.chaoxing.com/mooc-ans/mooc2/work/dowork?courseId=100001&classId=200001&workId=300001"
        parsed = HomeworkAdapter.parse_homework_html(html, url=url)
        answers = {
            "700001": {"option_ids": ["B"]},
            "700002": {"option_ids": ["A", "B"]},
            "700003": {"option_ids": ["正确"]},
            "700004": {"blanks": ["80", "443"]},
        }
        updated_html = (
            html
            .replace('name="answer700001" type="hidden" value=""', 'name="answer700001" type="hidden" value="B"')
            .replace('name="answer700002" type="hidden" value=""', 'name="answer700002" type="hidden" value="AB"')
            .replace('value="3"', 'value="3"/><input type="hidden" name="answer700003" value="true"')
            .replace('<textarea name="answerEditor7000041"></textarea>', '<textarea name="answerEditor7000041">80</textarea>')
            .replace('<textarea name="answerEditor7000042"></textarea>', '<textarea name="answerEditor7000042">443</textarea>')
        )
        with patch("api.homework.requests.Session") as mock_sess_cls:
            post_sess, get_sess = MagicMock(), MagicMock()
            mock_sess_cls.side_effect = [post_sess, get_sess]
            post_resp = MagicMock()
            post_resp.status_code = 200
            post_resp.json.return_value = {"status": True, "msg": "保存成功"}
            post_sess.post.return_value = post_resp

            get_resp = MagicMock()
            get_resp.status_code = 200
            get_resp.text = updated_html
            get_resp.url = url
            get_resp.headers = {}
            get_sess.get.return_value = get_resp

            res = HomeworkAdapter().save_once({"UID": "1"}, parsed, parsed["form_meta"], answers)
            self.assertTrue(res["verified"])
            adapter_arg = post_sess.mount.call_args[0][1]
            self.assertEqual(adapter_arg.max_retries.total, 0)
            post_data = post_sess.post.call_args[1]["data"]
            self.assertEqual(post_data["pyFlag"], "1")
            self.assertEqual(post_data["answer700001"], "B")
            self.assertEqual(post_data["answer700002"], "AB")
            self.assertEqual(post_data["answer700003"], "true")
            self.assertEqual(post_data["tiankongsize700004"], "2")
            self.assertEqual(post_data["answerEditor7000041"], "80")
            self.assertEqual(post_data["answerEditor7000042"], "443")

    def test_parse_work_list_html_and_task_url_validation(self) -> None:
        task_url = (
            "https://mooc1.chaoxing.com/mooc-ans/mooc2/work/task"
            "?courseId=263180377&classId=157348188&cpi=515542782&workId=57640157&answerId=0&enc=abc"
        )
        self.assertEqual(validate_homework_url(task_url), task_url)
        html = f"""
        <div class="bottomList"><ul>
          <li data="{task_url}" onclick="goTask(this);">
            <p class="overHidden2 fl">第一章行列式</p>
            <p class="status fl">未交</p>
            <div class="time notOver">剩余28小时52分钟</div>
          </li>
          <li data="https://mooc1.chaoxing.com/mooc-ans/mooc2/work/task?courseId=263180377&classId=157348188&workId=58596491&answerId=1&enc=def" onclick="goTask(this);">
            <p class="overHidden2 fl">第二章矩阵</p>
            <p class="status fl">待批阅</p>
          </li>
        </ul></div>
        """
        items = HomeworkAdapter.parse_work_list_html(
            html, course_id="263180377", clazz_id="157348188", course_title="线性代数"
        )
        self.assertEqual(len(items), 2)
        self.assertTrue(items[0]["canOpen"])
        self.assertEqual(items[0]["workId"], "57640157")
        self.assertEqual(items[0]["status"], "todo")
        self.assertFalse(items[1]["canOpen"])
        self.assertEqual(items[1]["status"], "submitted")


if __name__ == "__main__":
    unittest.main()
