"""Exercise QML backend persistence with isolated user data."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from chaoxing_desktop.config import defaults, parse_text, serialize, to_payload, validate
from chaoxing_desktop.qml_backend import Backend
from chaoxing_desktop.settings import CREDENTIAL_SECTION, STORED_SECRETS


class SettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, CHAOXING_DESKTOP_DATA=self.directory.name)
        self.environment.start()
        self.path = Path(self.directory.name) / "settings.ini"
        self.backends = []

    def tearDown(self):
        for backend in self.backends:
            backend.close()
            backend.deleteLater()
        self.app.processEvents()
        self.environment.stop()
        self.directory.cleanup()

    def open_backend(self):
        backend = Backend()
        self.backends.append(backend)
        return backend

    def configure(self, backend):
        backend.setValue("common", "username", "saved-account")
        backend.setValue("common", "use_cookies", "true")
        backend.setValue("common", "speed", "1.5")
        backend.setValue("common", "jobs", "8")
        backend.setValue("desktop", "random_fallback", "true")
        backend.setValue("question_bank", "providers", "TikuGo,TikuYanxi")
        backend.setValue("ai", "enabled", "true")
        backend.setValue("ai", "endpoint", "https://example.test/v1")
        backend.setValue("ai", "model", "saved-model")
        backend.setValue("ai", "timeout", "90")
        backend.setValue("common", "password", "PASSWORD_SECRET")
        backend.setValue("ai", "key", "API_SECRET")
        backend.setValue("question_bank", "yanxi_tokens", "TOKEN_SECRET")
        backend.setValue("question_bank", "like_tokens", "LIKE_SECRET")
        backend.setValue("question_bank", "go_authorization", "GO_SECRET")
        backend.setValue("question_bank", "adapter_url", "https://example.test/adapter?token=ADAPTER_SECRET")
        backend.setValue("notification", "url", "https://example.test/notify?token=NOTIFY_SECRET")

    def assert_restored(self, backend):
        self.assertIsNone(backend.startup_error)
        config = to_payload(backend.config)
        self.assertEqual(config["common"]["username"], "saved-account")
        self.assertTrue(config["common"]["use_cookies"])
        self.assertEqual(config["common"]["speed"], 1.5)
        self.assertEqual(config["common"]["jobs"], 8)
        self.assertEqual(config["desktop"]["random_fallback"], "true")
        self.assertEqual(config["question_bank"]["providers"], "TikuGo,TikuYanxi")
        self.assertEqual(config["ai"]["enabled"], "true")
        self.assertEqual(config["ai"]["endpoint"], "https://example.test/v1")
        self.assertEqual(config["ai"]["model"], "saved-model")
        self.assertEqual(config["ai"]["timeout"], "90")
        self.assertEqual(config["common"]["password"], "")
        self.assertEqual(config["ai"]["key"], "API_SECRET")
        self.assertEqual(config["question_bank"]["yanxi_tokens"], "TOKEN_SECRET")
        self.assertEqual(config["question_bank"]["like_tokens"], "LIKE_SECRET")
        self.assertEqual(config["question_bank"]["go_authorization"], "GO_SECRET")
        self.assertIn("ADAPTER_SECRET", config["question_bank"]["adapter_url"])
        self.assertIn("NOTIFY_SECRET", config["notification"]["url"])
        self.assertNotIn(CREDENTIAL_SECTION, config)
        saved = self.path.read_text(encoding="utf-8")
        for secret in ("PASSWORD_SECRET", "API_SECRET", "TOKEN_SECRET", "LIKE_SECRET",
                       "GO_SECRET", "ADAPTER_SECRET", "NOTIFY_SECRET"):
            self.assertNotIn(secret, saved)
        validate(parse_text(saved))

    def test_explicit_save_then_reopen_restores_settings(self):
        backend = self.open_backend()
        self.configure(backend)
        self.assertTrue(backend.save())
        self.assertTrue(self.path.is_file())
        self.assert_restored(self.open_backend())

    def test_close_without_explicit_save_then_reopen_restores_settings(self):
        backend = self.open_backend()
        self.configure(backend)
        self.assertTrue(backend.close())
        self.assert_restored(self.open_backend())

    def test_reopen_old_file_with_empty_cookie_switch(self):
        config = defaults()
        config["common"]["use_cookies"] = ""
        config["common"]["jobs"] = "8"
        config["ai"]["model"] = "saved-model"
        self.path.write_text(serialize(config), encoding="utf-8")
        backend = self.open_backend()
        self.assertIsNone(backend.startup_error)
        self.assertEqual(backend.config["common"]["jobs"], "8")
        self.assertEqual(backend.config["ai"]["model"], "saved-model")
        self.assertTrue(backend.close())
        self.assertEqual(parse_text(self.path.read_text(encoding="utf-8"))["common"]["use_cookies"], "false")
        self.assertIsNone(self.open_backend().startup_error)

    def test_failed_save_keeps_previous_file_and_blocks_close(self):
        self.path.write_text(serialize(defaults()), encoding="utf-8")
        original = self.path.read_bytes()
        backend = self.open_backend()
        self.configure(backend)
        with patch.object(Path, "replace", side_effect=OSError("cannot replace settings")):
            self.assertFalse(backend.close())
        self.assertEqual(self.path.read_bytes(), original)
        self.assertIn("cannot replace settings", backend.message)
        self.assertTrue(backend.close())

    def test_cleared_credentials_stay_cleared_after_restart(self):
        backend = self.open_backend()
        self.configure(backend)
        self.assertTrue(backend.close())
        reopened = self.open_backend()
        for section, keys in STORED_SECRETS.items():
            for key in keys:
                reopened.setValue(section, key, "")
        self.assertTrue(reopened.close())
        restored = self.open_backend()
        for section, keys in STORED_SECRETS.items():
            for key in keys:
                self.assertEqual(restored.config[section][key], "")
        self.assertNotIn(CREDENTIAL_SECTION, parse_text(self.path.read_text(encoding="utf-8")))

    def test_encryption_failure_keeps_previous_file(self):
        backend = self.open_backend()
        self.configure(backend)
        self.assertTrue(backend.save())
        original = self.path.read_bytes()
        backend.setValue("ai", "key", "replacement-key")
        with patch("chaoxing_desktop.credentials.protect", side_effect=OSError("failure")):
            backend._settings_timer.timeout.emit()
            self.assertTrue(backend.summary["settingsDirty"])
            self.assertIn("自动保存失败", backend.summary["saveState"])
            self.assertFalse(backend.close())
        self.assertEqual(self.path.read_bytes(), original)
        self.assertIn("加密失败", backend.message)

    def test_bad_ciphertext_preserves_public_settings_and_original_file(self):
        backend = self.open_backend()
        self.configure(backend)
        self.assertTrue(backend.close())
        config = parse_text(self.path.read_text(encoding="utf-8"))
        config[CREDENTIAL_SECTION]["data"] = "invalid ciphertext"
        original = serialize(config)
        self.path.write_text(original, encoding="utf-8")
        reopened = self.open_backend()
        self.assertIn("无法解密", reopened.startup_error)
        self.assertEqual(reopened.config["common"]["jobs"], "8")
        self.assertEqual(reopened.config["ai"]["model"], "saved-model")
        self.assertEqual(reopened.config["ai"]["key"], "")
        self.assertTrue(reopened.close())
        self.assertEqual(self.path.read_text(encoding="utf-8"), original)

    def test_unreadable_config_is_preserved_on_close(self):
        original = "[common]\nuse_cookies = invalid\nspeed = 1.5\n"
        self.path.write_text(original, encoding="utf-8")
        backend = self.open_backend()
        self.assertIsNotNone(backend.startup_error)
        self.assertTrue(backend.close())
        self.assertEqual(self.path.read_text(encoding="utf-8"), original)
        # An explicit save replaces the invalid configuration and enables autosave.
        self.assertTrue(backend.save())
        self.assertIsNone(backend.startup_error)
        backend.setValue("common", "jobs", "8")
        self.assertTrue(backend.close())
        self.assertEqual(self.open_backend().config["common"]["jobs"], "8")


if __name__ == "__main__":
    unittest.main()
