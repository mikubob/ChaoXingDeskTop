import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from chaoxing_desktop import exam_drafts
from chaoxing_desktop.exam_drafts import (
    ExamDraftStore,
    STATE_LOCAL_DRAFT,
    STATE_PLATFORM_SAVED,
    STATE_REJECTED,
    STATE_SIMULATED_SUBMITTED,
    STATE_SUBMIT_UNKNOWN,
    STATE_SUBMITTED,
    answer_summary,
    apply_manual_answer,
    apply_suggestion,
    exam_draft_key,
    merge_platform_answers,
    question_answered,
    source_text,
    state_text,
    validate_draft,
)


def sample_questions():
    return [
        {
            "id": "101",
            "no": 1,
            "type": "single",
            "title": "Python 中用于定义函数的关键字是？",
            "options": [
                {"id": "A", "text": "def"},
                {"id": "B", "text": "func"},
                {"id": "C", "text": "lambda"},
            ],
            "blank_count": 0,
            "score": "20",
        },
        {
            "id": "102",
            "no": 2,
            "type": "multiple",
            "title": "以下属于可变容器类型的有？",
            "options": [
                {"id": "A", "text": "list"},
                {"id": "B", "text": "dict"},
                {"id": "C", "text": "tuple"},
            ],
            "blank_count": 0,
        },
        {
            "id": "103",
            "no": 3,
            "type": "judgement",
            "title": "SHA-256 是一种对称加密算法。",
            "options": [
                {"id": "正确", "text": "正确"},
                {"id": "错误", "text": "错误"},
            ],
            "blank_count": 0,
        },
        {
            "id": "104",
            "no": 4,
            "type": "completion",
            "title": "HTTP 默认端口是 ____，HTTPS 默认端口是 ____。",
            "options": [],
            "blank_count": 2,
        },
        {
            "id": "105",
            "no": 5,
            "type": "shortanswer",
            "title": "简述乐观锁在本地文件持久化中的作用。",
            "options": [],
            "blank_count": 0,
            "images": ["file:///C:/temp/q105.png"],
        },
    ]


def sample_record(**overrides):
    rec = {
        "version": 1,
        "revision": 0,
        "account_id": "acc_0123456789abcdef0123456789ab",
        "uid": "20260001",
        "course_id": "course_9001",
        "clazz_id": "clazz_8001",
        "exam_id": "exam_7001",
        "attempt_id": "attempt_1",
        "demo": False,
        "title": "计算机基础期末考试",
        "deadline_utc": "2026-11-23T10:30:00+00:00",
        "state": STATE_LOCAL_DRAFT,
        "prev_state": "",
        "reason": "",
        "updated_at": "2026-11-23T09:00:00+00:00",
        "questions": sample_questions(),
        "answers": {},
    }
    rec.update(overrides)
    return rec


class ExamDraftTests(unittest.TestCase):
    def test_exam_draft_key_requires_all_six_identity_parts(self):
        key1 = exam_draft_key("acc1", "uid1", "c1", "cl1", "ex1", "att1")
        self.assertEqual(len(key1), 64)
        # 任一身份字段改变时哈希必须不同
        key2 = exam_draft_key("acc1", "uid1", "c1", "cl1", "ex1", "att2")
        self.assertNotEqual(key1, key2)

        for bad_args in (
            ("", "uid1", "c1", "cl1", "ex1", "att1"),
            ("acc1", "   ", "c1", "cl1", "ex1", "att1"),
            ("acc1", "uid1", None, "cl1", "ex1", "att1"),
            ("acc1", "uid1", "c1", False, "ex1", "att1"),
            ("acc1", "uid1", "c1", "cl1", "", "att1"),
            ("acc1", "uid1", "c1", "cl1", "ex1", ""),
        ):
            with self.subTest(bad_args=bad_args):
                with self.assertRaises(ValueError):
                    exam_draft_key(*bad_args)

    def test_validate_draft_and_answer_summary(self):
        record = sample_record()
        validate_draft(record)
        self.assertEqual(
            answer_summary(record),
            {"answeredCount": 0, "totalCount": 5, "unansweredNumbers": [1, 2, 3, 4, 5]},
        )

        apply_manual_answer(record, "101", {"option_ids": ["A"]}, "2026-11-23T09:01:00+00:00")
        apply_manual_answer(record, "102", {"option_ids": ["A", "B"]}, "2026-11-23T09:02:00+00:00")
        apply_manual_answer(record, "103", {"option_ids": ["错误"]}, "2026-11-23T09:03:00+00:00")
        # 填空题有一空为空白字符串时仍算未答完
        apply_manual_answer(record, "104", {"blanks": ["80", "   "]}, "2026-11-23T09:04:00+00:00")
        self.assertFalse(question_answered(record["questions"][3], record["answers"]["104"]))
        self.assertEqual(
            answer_summary(record),
            {"answeredCount": 3, "totalCount": 5, "unansweredNumbers": [4, 5]},
        )

        # 补齐第 4 题与第 5 题
        apply_manual_answer(record, "104", {"blanks": ["80", "443"]}, "2026-11-23T09:05:00+00:00")
        apply_manual_answer(record, "105", {"text": "防止并发覆盖。"}, "2026-11-23T09:06:00+00:00")
        self.assertEqual(
            answer_summary(record),
            {"answeredCount": 5, "totalCount": 5, "unansweredNumbers": []},
        )

        # 非法选项、多选进单选、填空数不匹配、超长简答均应被拒绝
        with self.assertRaises(ValueError):
            apply_manual_answer(record, "101", {"option_ids": ["A", "B"]}, "2026-11-23T09:07:00+00:00")
        with self.assertRaises(ValueError):
            apply_manual_answer(record, "101", {"option_ids": ["Z"]}, "2026-11-23T09:07:00+00:00")
        with self.assertRaises(ValueError):
            apply_manual_answer(record, "104", {"blanks": ["80"]}, "2026-11-23T09:07:00+00:00")
        with self.assertRaises(ValueError):
            apply_manual_answer(record, "105", {"text": "x" * 12001}, "2026-11-23T09:07:00+00:00")

    def test_manual_source_protection_and_state_transition_on_edit(self):
        record = sample_record(state=STATE_PLATFORM_SAVED)
        # 1. 首次写入题库参考答案
        applied = apply_suggestion(
            record,
            "101",
            {"option_ids": ["B"]},
            "bank:TikuYanxi",
            "2026-11-23T09:05:00+00:00",
        )
        self.assertTrue(applied)
        self.assertEqual(record["state"], STATE_LOCAL_DRAFT)
        self.assertEqual(record["prev_state"], STATE_PLATFORM_SAVED)
        self.assertEqual(record["answers"]["101"]["source"], "bank:TikuYanxi")
        self.assertEqual(source_text(record["answers"]["101"]["source"]), "参考答案 · TikuYanxi")

        # 2. 非 manual 的题可被新一次参考答案更新
        applied_ai = apply_suggestion(
            record,
            "101",
            {"option_ids": ["C"]},
            "ai",
            "2026-11-23T09:06:00+00:00",
        )
        self.assertTrue(applied_ai)
        self.assertEqual(record["answers"]["101"]["option_ids"], ["C"])
        self.assertEqual(record["answers"]["101"]["source"], "ai")

        # 3. 用户手动修改为 A，source 变为 manual
        apply_manual_answer(record, "101", {"option_ids": ["A"]}, "2026-11-23T09:07:00+00:00")
        self.assertEqual(record["answers"]["101"]["source"], "manual")
        self.assertEqual(source_text(record["answers"]["101"]["source"]), "手动填写")

        # 4. 整卷再次获取参考答案（replace_manual=False）时不得覆盖 manual
        skipped = apply_suggestion(
            record,
            "101",
            {"option_ids": ["B"]},
            "bank:TikuGo",
            "2026-11-23T09:08:00+00:00",
            replace_manual=False,
        )
        self.assertFalse(skipped)
        self.assertEqual(record["answers"]["101"]["option_ids"], ["A"])
        self.assertEqual(record["answers"]["101"]["source"], "manual")

        # 5. 单题显式确认替换（replace_manual=True）时允许覆盖
        replaced = apply_suggestion(
            record,
            "101",
            {"option_ids": ["B"]},
            "bank:TikuGo",
            "2026-11-23T09:09:00+00:00",
            replace_manual=True,
        )
        self.assertTrue(replaced)
        self.assertEqual(record["answers"]["101"]["option_ids"], ["B"])
        self.assertEqual(record["answers"]["101"]["source"], "bank:TikuGo")

    def test_merge_platform_answers_with_local_draft(self):
        paper = sample_record()
        # 场景 A：本地无草稿，平台有第 1 题已保存答案
        merged, restored_unsynced = merge_platform_answers(
            None,
            paper,
            {"101": {"option_ids": ["A"], "updated_at": "2026-11-23T09:01:00+00:00"}},
            "2026-11-23T09:02:00+00:00",
        )
        self.assertFalse(restored_unsynced)
        self.assertEqual(merged["state"], STATE_PLATFORM_SAVED)
        self.assertEqual(merged["answers"]["101"]["option_ids"], ["A"])

        # 场景 B：本地处于 local_draft（有未同步到平台的手动答案），重开卷时保留本地未同步修改并给出提示标志
        local = copy.deepcopy(merged)
        apply_manual_answer(local, "101", {"option_ids": ["C"]}, "2026-11-23T09:05:00+00:00")
        apply_manual_answer(local, "102", {"option_ids": ["A", "B"]}, "2026-11-23T09:05:00+00:00")
        self.assertEqual(local["state"], STATE_LOCAL_DRAFT)

        merged2, restored_unsynced2 = merge_platform_answers(
            local,
            paper,
            {
                "101": {"option_ids": ["A"], "updated_at": "2026-11-23T09:01:00+00:00"},
                "103": {"option_ids": ["错误"], "updated_at": "2026-11-23T09:01:00+00:00"},
            },
            "2026-11-23T09:06:00+00:00",
        )
        self.assertTrue(restored_unsynced2)
        self.assertEqual(merged2["state"], STATE_LOCAL_DRAFT)
        self.assertEqual(merged2["answers"]["101"]["option_ids"], ["C"])
        self.assertEqual(merged2["answers"]["101"]["source"], "manual")
        self.assertEqual(merged2["answers"]["102"]["option_ids"], ["A", "B"])
        # 本地未答的第 3 题吸收平台已保存答案
        self.assertEqual(merged2["answers"]["103"]["option_ids"], ["错误"])

        # 场景 C：本地处于 platform_saved，平台返回更新时间更晚的答案时以较新一方为准
        saved_local = copy.deepcopy(merged)
        merged3, restored_unsynced3 = merge_platform_answers(
            saved_local,
            paper,
            {"101": {"option_ids": ["B"], "updated_at": "2026-11-23T09:10:00+00:00"}},
            "2026-11-23T09:11:00+00:00",
        )
        self.assertFalse(restored_unsynced3)
        self.assertEqual(merged3["answers"]["101"]["option_ids"], ["B"])

    def test_submit_state_machine_blocks_reentry_edit_and_unconfirmed_downgrade(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExamDraftStore(tmp)
            rec = sample_record()
            apply_manual_answer(rec, "101", {"option_ids": ["A"]}, "2026-11-23T09:01:00+00:00")
            key = exam_draft_key(
                rec["account_id"], rec["uid"], rec["course_id"], rec["clazz_id"], rec["exam_id"], rec["attempt_id"]
            )
            saved, raw = store.save(rec, None)
            self.assertEqual(saved["revision"], 1)

            # 1. 发起交卷：原子写入 submit_unknown 并记录 prev_state
            submitting, raw_submitting = store.begin_submit(key, raw, "2026-11-23T09:10:00+00:00")
            self.assertEqual(submitting["state"], STATE_SUBMIT_UNKNOWN)
            self.assertEqual(submitting["prev_state"], STATE_LOCAL_DRAFT)
            self.assertEqual(state_text(submitting["state"]), "提交结果待核验")

            # 2. submit_unknown 状态下禁止重复 begin_submit、禁止编辑、禁止写入参考答案、禁止普通保存覆盖回 local_draft、禁止删除
            with self.assertRaises(ValueError):
                store.begin_submit(key, raw_submitting, "2026-11-23T09:11:00+00:00")
            with self.assertRaises(ValueError):
                apply_manual_answer(submitting, "101", {"option_ids": ["B"]}, "2026-11-23T09:11:00+00:00")
            with self.assertRaises(ValueError):
                apply_suggestion(submitting, "102", {"option_ids": ["A"]}, "ai", "2026-11-23T09:11:00+00:00")
            forged = copy.deepcopy(submitting)
            forged["state"] = STATE_LOCAL_DRAFT
            with self.assertRaises(ValueError):
                store.save(forged, raw_submitting)
            with self.assertRaises(ValueError):
                store.delete_finished(key, raw_submitting)

            # 3. 交卷超时结算为 unknown：保持 submit_unknown
            unknown_rec, _ = store.settle_submit(key, "unknown", "请求超时", "2026-11-23T09:12:00+00:00")
            self.assertEqual(unknown_rec["state"], STATE_SUBMIT_UNKNOWN)
            self.assertIn("超时", unknown_rec["reason"])

            # 4. 只读核验 confirmed=False 时绝不退回未交卷，保持 submit_unknown
            unconfirmed_rec, _ = store.settle_verify(
                key, False, "列表页尚未显示已交卷标志", "2026-11-23T09:13:00+00:00"
            )
            self.assertEqual(unconfirmed_rec["state"], STATE_SUBMIT_UNKNOWN)
            self.assertIn("尚未显示", unconfirmed_rec["reason"])

            # 5. 只读核验 confirmed=True 时转为 submitted
            confirmed_rec, raw_confirmed = store.settle_verify(
                key, True, "平台已确认收卷", "2026-11-23T09:14:00+00:00"
            )
            self.assertEqual(confirmed_rec["state"], STATE_SUBMITTED)

            # 6. 已交卷后禁止再次交卷或修改，但允许 delete_finished 清理容量
            with self.assertRaises(ValueError):
                store.begin_submit(key, raw_confirmed, "2026-11-23T09:15:00+00:00")
            with self.assertRaises(ValueError):
                apply_manual_answer(confirmed_rec, "101", {"option_ids": ["B"]}, "2026-11-23T09:15:00+00:00")

            store.delete_finished(key, raw_confirmed)
            self.assertEqual(store.read(key), (None, None))

    def test_rejected_submission_allows_edit_and_resubmit(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExamDraftStore(tmp)
            rec = sample_record()
            key = exam_draft_key(
                rec["account_id"], rec["uid"], rec["course_id"], rec["clazz_id"], rec["exam_id"], rec["attempt_id"]
            )
            _, raw = store.save(rec, None)
            _, raw = store.begin_submit(key, raw, "2026-11-23T09:10:00+00:00")
            rejected, raw_rejected = store.settle_submit(
                key, "rejected", "还有必答题未完成", "2026-11-23T09:10:05+00:00"
            )
            self.assertEqual(rejected["state"], STATE_REJECTED)

            # 平台明确业务拒绝后，允许用户修改答案并重新确认交卷
            apply_manual_answer(rejected, "101", {"option_ids": ["A"]}, "2026-11-23T09:11:00+00:00")
            self.assertEqual(rejected["state"], STATE_LOCAL_DRAFT)
            self.assertEqual(rejected["prev_state"], STATE_REJECTED)
            _, raw_saved = store.save(rejected, raw_rejected)
            _, raw_sub2 = store.begin_submit(key, raw_saved, "2026-11-23T09:12:00+00:00")
            sim_rec, raw_sim = store.settle_submit(
                key, "simulated_submitted", "演示交卷成功", "2026-11-23T09:12:02+00:00"
            )
            self.assertEqual(sim_rec["state"], STATE_SIMULATED_SUBMITTED)
            store.delete_finished(key, raw_sim)
            self.assertEqual(store.read(key), (None, None))

    def test_corrupt_file_fails_closed_and_optimistic_lock_prevents_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExamDraftStore(tmp)
            rec = sample_record()
            key = exam_draft_key(
                rec["account_id"], rec["uid"], rec["course_id"], rec["clazz_id"], rec["exam_id"], rec["attempt_id"]
            )
            saved1, raw1 = store.save(rec, None)

            # 乐观锁冲突：expected_raw 不匹配或 revision 过期均拒绝覆盖
            with self.assertRaises(ValueError):
                store.save(rec, None)
            stale = copy.deepcopy(saved1)
            stale["revision"] = 0
            with self.assertRaises(ValueError):
                store.save(stale, raw1)

            # 文件损坏时 fail-closed，且原损坏文件必须保留在磁盘上不被静默抹掉
            path = store.path(key)
            corrupt_bytes = b"{not-valid-json"
            path.write_bytes(corrupt_bytes)
            with self.assertRaises(ValueError) as ctx:
                store.read(key)
            self.assertEqual(str(ctx.exception), ExamDraftStore.CORRUPT_MESSAGE)
            with self.assertRaises(ValueError):
                store.save(saved1, corrupt_bytes)
            self.assertEqual(path.read_bytes(), corrupt_bytes)

    def test_capacity_limits_and_delete_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExamDraftStore(tmp)
            rec1 = sample_record(attempt_id="att_1")
            key1 = exam_draft_key(
                rec1["account_id"], rec1["uid"], rec1["course_id"], rec1["clazz_id"], rec1["exam_id"], rec1["attempt_id"]
            )
            saved1_rec, raw1 = store.save(rec1, None)

            # 未交卷的 local_draft 和 platform_saved 均不可通过 delete_finished 删除
            with self.assertRaises(ValueError):
                store.delete_finished(key1, raw1)

            # 文件数上限检查
            with mock.patch.object(exam_drafts, "MAX_FILES", 1):
                rec2 = sample_record(attempt_id="att_2")
                with self.assertRaises(ValueError) as ctx:
                    store.save(rec2, None)
                self.assertIn("100 份上限", str(ctx.exception))

            # 总字节数上限检查
            with mock.patch.object(exam_drafts, "MAX_TOTAL_BYTES", len(raw1) + 10):
                rec2 = sample_record(attempt_id="att_2")
                with self.assertRaises(ValueError) as ctx:
                    store.save(rec2, None)
                self.assertIn("16 MiB 上限", str(ctx.exception))

            # 单文件字节数上限检查（写入超大内容，读取时原文件小于上限但新写入内容超限）
            big_rec = copy.deepcopy(saved1_rec)
            apply_manual_answer(big_rec, "105", {"text": "中" * 5000}, "2026-11-23T09:20:00+00:00")
            with mock.patch.object(exam_drafts, "MAX_FILE_BYTES", len(raw1) + 100):
                with self.assertRaises(ValueError) as ctx:
                    store.save(big_rec, raw1)
                self.assertIn("512 KiB", str(ctx.exception))

            # 读取超单文件上限的已有文件时 fail-closed
            with mock.patch.object(exam_drafts, "MAX_FILE_BYTES", 64):
                with self.assertRaises(ValueError) as ctx:
                    store.read(key1)
                self.assertEqual(str(ctx.exception), ExamDraftStore.CORRUPT_MESSAGE)


if __name__ == "__main__":
    unittest.main()
