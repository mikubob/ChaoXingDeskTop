"""Behavioral contracts for the new Qt Quick state layer."""
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from chaoxing_desktop.qml_backend import Backend
from chaoxing_desktop.config import defaults
from chaoxing_desktop.qml_schema import SCHEMA


class QmlBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, CHAOXING_DESKTOP_DATA=self.directory.name)
        self.env.start()
        self.b = Backend()

    def tearDown(self):
        self.b.close()
        self.b.deleteLater()
        self.app.processEvents()
        self.env.stop()
        self.directory.cleanup()

    def test_all_preexisting_editable_settings_are_represented(self):
        represented = {(f["section"], f["key"]) for groups in SCHEMA.values() for g in groups for f in g["fields"]}
        represented |= {("common", "username"), ("common", "password"), ("common", "use_cookies")}
        represented |= {("question_bank", k) for k in ("providers", "yanxi_tokens", "like_tokens", "adapter_url", "go_authorization", "declarative_token")}
        config = defaults()
        expected = {(s, k) for s in config.sections() for k in config[s]} - {("tiku", "provider"), ("tiku", "cover_rate"), ("format", "version")}
        self.assertEqual(expected, represented)

    def test_bank_review_policy_save_reopen_and_busy_lock(self):
        b = self.b
        b.setValue('matching', 'bank_review_policy', 'ai_dependent')
        self.assertTrue(b.save())
        reopened = Backend()
        self.assertEqual(reopened.config['matching']['bank_review_policy'], 'ai_dependent')
        reopened.controller.active = True
        reopened.setValue('matching', 'bank_review_policy', 'options_only')
        self.assertEqual(reopened.config['matching']['bank_review_policy'], 'ai_dependent')
        reopened.controller.active = False
        reopened.close()
        reopened.deleteLater()

    def test_same_course_different_classes_and_hidden_selection(self):
        b = self.b
        b.on_event(dict(type="courses", demo=True, courses=[dict(courseId="1", clazzId="a", title="Python"), dict(courseId="1", clazzId="b", title="English")]))
        b.selectVisible(True)
        b.searchCourses("Python")
        self.assertEqual(b.state["hiddenCount"], 1)
        self.assertEqual(b.state["selectedCount"], 2)
        b.selectVisible(False)
        self.assertEqual(b.selected, {("1", "b")})

    def test_run_lock_and_ai_result_invalidation(self):
        b = self.b
        b.ai_status = "服务响应正常"
        b.setValue("ai", "model", "changed")
        self.assertIn("重新测试", b.ai_status)
        b.controller.active = True
        b.setValue("ai", "model", "locked")
        self.assertEqual(b.config["ai"]["model"], "changed")
        b.controller.active = False

    def test_provider_order_keeps_credentials(self):
        b = self.b
        b.setValue("question_bank", "yanxi_tokens", "SECRET")
        b.toggleProvider("TikuGo", True)
        b.toggleProvider("TikuYanxi", True)
        b.moveProvider(1, -1)
        self.assertEqual(b.enabled_providers(), ["TikuYanxi", "TikuGo"])
        self.assertEqual(b.config["question_bank"]["yanxi_tokens"], "SECRET")

    def test_custom_log_directory_redacts_and_delete_preserves_other_files(self):
        directory = Path(self.directory.name) / 'custom logs'
        self.b.setValue('logging', 'directory', str(directory))
        self.b.setValue('question_bank', 'yanxi_tokens', 'PRIVATE_TOKEN')
        self.b.append_log('Token PRIVATE_TOKEN')
        self.assertTrue(self.b.log_store.flush())
        self.app.processEvents()
        saved = list(directory.glob('chaoxing-*.log'))
        self.assertEqual(len(saved), 1)
        self.assertNotIn('PRIVATE_TOKEN', saved[0].read_text(encoding='utf-8'))
        self.b.clearLogs()
        self.assertTrue(saved[0].exists())
        unrelated = directory / 'important.log'
        unrelated.write_text('preserve', encoding='utf-8')
        self.b.controller.active = True
        self.b.deleteLogs()
        self.assertFalse(saved[0].exists())
        self.assertEqual(unrelated.read_text(encoding='utf-8'), 'preserve')
        self.b.append_log('new session')
        self.assertTrue(self.b.log_store.flush())
        self.app.processEvents()
        self.assertEqual(len(list(directory.glob('chaoxing-*.log'))), 1)
        self.b.controller.active = False
        self.assertTrue(self.b.save())
        reopened = Backend()
        self.assertEqual(reopened.config['logging']['directory'], str(directory))
        reopened.close()
        reopened.deleteLater()

    def test_log_save_and_delete_failure_keeps_visible_records(self):
        blocked = Path(self.directory.name) / 'file-not-directory'
        blocked.write_text('existing', encoding='utf-8')
        self.b.setValue('logging', 'directory', str(blocked))
        self.b.append_log('retained message')
        self.assertTrue(self.b.log_store.flush())
        self.app.processEvents()
        self.assertIn('日志保存失败', self.b.summary['logSaveError'])
        self.b.deleteLogs()
        self.assertIn('retained message', self.b.state['logs'])
        self.assertEqual(blocked.read_text(encoding='utf-8'), 'existing')

    def test_media_progress_is_per_task_clamped_and_stale_events_ignored(self):
        b = self.b
        b.on_event(dict(type='tasks', tasks=[dict(id='v', course='c', title='video'), dict(id='a', course='c', title='audio')]))
        for key, position, kind in [('v', 30, 'Video'), ('a', 500, 'Audio')]:
            b.on_event(dict(type='task', id=key, status='running'))
            b.on_event(dict(type='media', id=key, name=key, kind=kind, duration=120, position=position))
        self.assertEqual(b.tasks[0]['media']['percent'], 25)
        self.assertEqual(b.tasks[1]['media']['percent'], 100)
        self.assertFalse(b.tasks[1]['media']['verified'])
        self.assertEqual(b.tasks[0]['media']['statusText'], '播放中')
        self.assertEqual(b.tasks[1]['media']['statusText'], '等待平台核验')
        b.on_event(dict(type='task', id='a', status='pending'))
        self.assertEqual(b.tasks[1]['media']['statusText'], '未确认完成')
        b.on_event(dict(type='task', id='v', status='stopped'))
        b.on_event(dict(type='media', id='v', name='late', duration=120, position=100))
        self.assertEqual(b.tasks[0]['media']['position'], 30)
        self.assertFalse(b.tasks[0]['media']['active'])
        self.assertEqual(b.tasks[0]['media']['statusText'], '未确认完成')
        b.on_event(dict(type='media', id='absent', position=1))

    def test_verified_media_can_belong_to_a_pending_chapter(self):
        b = self.b
        b.on_event(dict(type='tasks', tasks=[dict(id='c', course='c', title='chapter')]))
        b.on_event(dict(type='task', id='c', status='running'))
        b.on_event(dict(type='media', id='c', name='second video', duration=60, position=60, verified=True))
        b.on_event(dict(type='task', id='c', status='pending'))
        self.assertEqual(b.tasks[0]['status'], 'pending')
        self.assertEqual(b.tasks[0]['media']['statusText'], '平台已确认')
        self.assertEqual(b.summary['success'], 0)
        self.assertEqual(b.summary['pending'], 1)

    def test_final_platform_chapter_confirmation_updates_pending_media(self):
        b = self.b
        b.on_event(dict(type='tasks', tasks=[dict(id='c', course='c', title='chapter')]))
        b.on_event(dict(type='task', id='c', status='running'))
        b.on_event(dict(type='media', id='c', name='video', duration=60, position=60, verified=False))
        b.on_event(dict(type='task', id='c', status='pending'))
        self.assertEqual(b.tasks[0]['media']['statusText'], '未确认完成')
        b.on_event(dict(type='task', id='c', status='success'))
        self.assertTrue(b.tasks[0]['media']['verified'])
        self.assertEqual(b.tasks[0]['media']['statusText'], '平台已确认')
        self.assertFalse(b.tasks[0]['media']['active'])
        self.assertEqual(b.summary['success'], 1)

    def test_failed_save_blocks_close_and_preserves_previous_file(self):
        b = self.b
        self.assertTrue(b.save())
        path = Path(self.directory.name) / "settings.ini"
        previous = path.read_bytes()
        b.setValue("common", "speed", "9")
        self.assertFalse(b.close())
        self.assertEqual(path.read_bytes(), previous)
        b.setValue("common", "speed", "1")

    def test_account_change_blocks_real_start(self):
        b = self.b
        b.setValue("common", "username", "account")
        b.setValue("common", "password", "password")
        b.on_event(dict(type="courses", courses=[dict(courseId="1", clazzId="a", title="Course")]))
        b.selectVisible(True)
        b.setValue("common", "username", "new-account")
        with patch.object(b.controller, "start") as start:
            b.startTasks()
            start.assert_not_called()
        self.assertIn("重新读取", b.message)

    def test_incomplete_exit_marks_tasks_stopped_and_redacts_logs(self):
        b = self.b
        b.setValue("ai", "key", "sensitive-secret")
        b.on_event(dict(type="tasks", tasks=[dict(id="1", course="c", title="t")]))
        b.on_event(dict(type="log", message="key sensitive-secret"))
        b.on_finished(1)
        self.assertEqual(b.state["tasks"][0]["status"], "stopped")
        self.assertNotIn("sensitive-secret", b.state["logs"])

    def test_manual_reply_sent_only_while_pending(self):
        b = self.b
        b.controller.active = True
        with patch.object(b.controller, "reply") as reply:
            b.reply("A", False)
            reply.assert_not_called()
            b.on_event(dict(type="input", kind="question", question=dict(type="single", title="Q", options="A. one\nB. two")))
            b.reply("B", False)
            reply.assert_called_once_with("B", False)
            self.assertFalse(b.input)
        b.controller.active = False

    def test_file_actions_import_cookie_and_migrate_config(self):
        b = self.b
        cookie = Path(self.directory.name) / "source-cookie.txt"
        cookie.write_text("name=value; other=test", encoding="utf-8")
        with patch("chaoxing_desktop.qml_backend.QFileDialog.getOpenFileName", return_value=(str(cookie), "")):
            b.fileAction("import_cookie")
        self.assertTrue(b.state["cookie"])
        self.assertEqual(b.config["common"]["use_cookies"], "true")
        b.fileAction("clear_cookie")
        self.assertFalse(b.state["cookie"])
        ini = Path(self.directory.name) / "source.ini"
        ini.write_text("[common]\nspeed=1.5\n[extension]\ncustom=value\n", encoding="utf-8")
        with patch("chaoxing_desktop.qml_backend.QFileDialog.getOpenFileName", return_value=(str(ini), "")):
            b.fileAction("import_config")
        self.assertEqual(b.config["common"]["speed"], "1.5")
        self.assertEqual(b.config["extension"]["custom"], "value")

    def test_export_logs_redacts_and_full_ini_retains_extension(self):
        b = self.b
        b.setValue("ai", "key", "SECRET_TO_REDACT")
        b.config.add_section("extension")
        b.config["extension"]["custom"] = "value"
        b.append_log("service SECRET_TO_REDACT")
        output = Path(self.directory.name) / "export.txt"
        with patch("chaoxing_desktop.qml_backend.QFileDialog.getSaveFileName", return_value=(str(output), "")):
            b.fileAction("export_logs")
            self.assertNotIn("SECRET_TO_REDACT", output.read_text(encoding="utf-8"))
            b.fileAction("export_config")
            self.assertIn("SECRET_TO_REDACT", output.read_text(encoding="utf-8"))
            self.assertIn("custom = value", output.read_text(encoding="utf-8"))

    def test_stopped_demo_run_offers_an_offline_only_continuation(self):
        b = self.b
        b.demo_mode = True
        b.operation = 'run'
        b._begin('run', True, [['demo-101', '01']])
        b.on_event(dict(type='tasks', tasks=[dict(id='demo-101:0', course='Demo', title='t',
                                                  courseId='demo-101', clazzId='01', pointId='0')]))
        b.controller.active = True
        b.controller.stopping = True
        b.controller.active = False
        b.on_finished(2)
        b.controller.stopping = False
        # The offline demo can be continued, but the retained row is marked so
        # the relaunch stays a demo and never builds a real session.
        state = b.resumeState
        self.assertTrue(state['available'], state['reason'])
        self.assertEqual(state['account'], '离线演示')
        self.assertTrue(b._resume['demo'])

    def test_resume_entry_states_and_repeat_trigger(self):
        b = self.b
        cookie = Path(self.directory.name) / 'synthetic-session'
        cookie.write_bytes(b'synthetic')
        b.config['common'].update(username='synthetic', password='', use_cookies='true')
        b.account_store = SimpleNamespace(active='a' * 32, rows=[], get=lambda _: dict(uid='u', label='Synthetic'),
                                         cookie_path=lambda _: cookie)
        b.course_identity = b.identity()
        b.courses = [dict(courseId='c', clazzId='l', cpi='p', title='Course')]
        self.assertFalse(b.summary['resumeScoped'])
        self.assertFalse(b.resumeState['available'])
        # Freeze the real launch scope while the test supplies the worker events.
        with patch.object(b.controller, 'start') as start:
            b._begin('run', False, [['c', 'l']])
        start.assert_called_once()
        b.on_event(dict(type='tasks', tasks=[
            dict(id='c:l:p1:0', course='Course', title='One', status='success',
                 courseId='c', clazzId='l', pointId='p1'),
            dict(id='c:l:p2:1', course='Course', title='Two', status='stopped',
                 courseId='c', clazzId='l', pointId='p2')]))
        b.controller.active = True
        b.controller.stopping = True
        b.controller.active = False   # Controller._finish clears this first
        b.on_finished(0)
        b.controller.stopping = False
        self.assertTrue(b.summary['resumeScoped'])
        reason, clickable, needs_confirmation = b.resume_blocker()
        self.assertTrue(clickable, reason)
        self.assertFalse(needs_confirmation)
        # A continuation only becomes legal once the controller finished; an
        # unfinished exit is not evidence that the old worker is gone.
        b._controller_exit = None
        self.assertFalse(b.resume_blocker()[1])
        b._controller_exit = 0
        with patch.object(b.controller, 'start') as start:
            b.resumeTasks()
        self.assertEqual(start.call_count, 1)
        command = start.call_args.args[0]
        self.assertEqual(command['action'], 'run')
        self.assertEqual(command['resume']['tasks'][0]['pointId'], 'p1')
        self.assertEqual(command['resume']['courses'][0]['cpi'], 'p')
        self.assertFalse(b.resumeState['enabled'])
        with patch.object(b.controller, 'start') as start:
            b.resumeTasks()
            start.assert_not_called()

    def test_unreadable_settings_preserved_on_close(self):
        path = Path(self.directory.name) / "settings.ini"
        path.write_text("not an INI file", encoding="utf-8")
        reopened = Backend()
        self.assertIsNotNone(reopened.startup_error)
        self.assertTrue(reopened.close())
        self.assertEqual(path.read_text(encoding="utf-8"), "not an INI file")
        path.unlink()
        reopened.deleteLater()


if __name__ == "__main__":
    unittest.main()
