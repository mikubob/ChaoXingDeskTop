import unittest

from chaoxing_desktop.config import (BOOLEAN_KEYS, Redactor, defaults, is_secret, parse_text, select_courses,
                                    serialize, to_payload, validate)


class ConfigTests(unittest.TestCase):
    def test_ignore_completion_cache_is_opt_in_and_round_trips_as_boolean(self):
        legacy = parse_text('[common]\njobs = 2\n')
        self.assertFalse(to_payload(legacy)['common']['ignore_completion_cache'])
        legacy['common']['ignore_completion_cache'] = 'true'
        restored = parse_text(serialize(legacy))
        validate(restored)
        self.assertIs(to_payload(restored)['common']['ignore_completion_cache'], True)
        restored['common']['ignore_completion_cache'] = 'invalid'
        with self.assertRaises(ValueError):
            validate(restored)

    def test_import_bom_percent_and_unknown_fields(self):
        c = parse_text("\ufeff[common]\nusername = 测试\n[tiku]\nendpoint = https://example.test/v1?q=50%25\n[custom]\nfuture = preserved\n")
        self.assertEqual(c["common"]["username"], "测试")
        self.assertIn("50%25", c["ai"]["endpoint"])
        self.assertEqual(parse_text(serialize(c))["custom"]["future"], "preserved")

    def test_save_excludes_secrets(self):
        c = defaults()
        c["common"]["password"] = "PASSWORD_123"
        c["ai"]["key"] = "API_KEY_456"
        c["question_bank"]["yanxi_tokens"] = "TOKEN_A,TOKEN_B"
        c["notification"]["url"] = "https://example.test/secret"
        c["custom"] = {"new_token": "CUSTOM_SECRET", "future": "kept"}
        text = serialize(c, include_secrets=False)
        for value in ("PASSWORD_123", "API_KEY_456", "TOKEN_A", "CUSTOM_SECRET", "example.test/secret"):
            self.assertNotIn(value, text)
        self.assertIn("kept", text)

    def test_validation_rejects_invalid_values(self):
        for section, key, value in [("common", "speed", "nan"), ("common", "jobs", "0"),
                                    ("common", "jobs", "1.5"), ("common", "use_cookies", "sometimes"),
                                    ("tiku", "provider", "invalid"), ("tiku", "cover_rate", "1.1")]:
            with self.subTest(key=key, value=value):
                c = defaults()
                c[section][key] = value
                with self.assertRaises(ValueError):
                    validate(c)

    def test_saved_settings_keep_every_boolean_switch(self):
        for value in ("true", "false"):
            with self.subTest(value=value):
                c = defaults()
                for section, keys in BOOLEAN_KEYS.items():
                    for key in keys:
                        c[section][key] = value
                restored = parse_text(serialize(c, include_secrets=False))
                validate(restored)
                for section, keys in BOOLEAN_KEYS.items():
                    for key in keys:
                        self.assertEqual(restored[section][key], value)

    def test_recover_old_redacted_cookie_switch_preserves_other_settings(self):
        c = parse_text("[common]\nuse_cookies = \nspeed = 1.5\njobs = 8\n"
                       "[ai]\nenabled = true\nmodel = saved-model\n"
                       "[desktop]\nrandom_fallback = true\n")
        validate(c)
        self.assertEqual(c["common"]["use_cookies"], "false")
        self.assertEqual(c["common"]["speed"], "1.5")
        self.assertEqual(c["common"]["jobs"], "8")
        self.assertEqual(c["ai"]["model"], "saved-model")
        self.assertEqual(c["desktop"]["random_fallback"], "true")

    def test_cookie_switch_is_public_but_cookie_secrets_stay_redacted(self):
        self.assertFalse(is_secret("use_cookies"))
        c = defaults()
        c["common"]["use_cookies"] = "true"
        c["custom"] = {key: "COOKIE_SECRET" for key in ("cookies", "cookie", "session_cookie")}
        restored = parse_text(serialize(c, include_secrets=False))
        self.assertEqual(restored["common"]["use_cookies"], "true")
        for key in c["custom"]:
            self.assertTrue(is_secret(key))
            self.assertEqual(restored["custom"][key], "")

    def test_defaults_and_payload(self):
        c = defaults()
        validate(c)
        p = to_payload(c)
        self.assertFalse(p["common"]["use_cookies"])
        self.assertEqual(p["common"]["jobs"], 4)
        self.assertEqual(p["tiku"]["provider"], "")
        self.assertEqual(p["notification"]["provider"], "")

    def test_precise_course_and_class_selection(self):
        courses = [dict(courseId="1", clazzId="a"), dict(courseId="1", clazzId="b"), dict(courseId="2", clazzId="a")]
        self.assertEqual(select_courses(courses, [["1", "b"]]), [courses[1]])
        for keys in ([], [["missing", "b"]], [["1", "a"], ["missing", "b"]]):
            with self.assertRaises(ValueError):
                select_courses(courses, keys)

    def test_redaction(self):
        redact = Redactor({"common": {"password": "PASSWORD"}, "tiku": {"tokens": "SECRET_A,SECRET_B", "key": "SECRET_KEY"}})
        output = redact("PASSWORD SECRET_A SECRET_B SECRET_KEY https://example.test/?enc=session&x=1\nCookie: token-session\nBearer token-auth")
        for secret in ("PASSWORD", "SECRET_A", "SECRET_B", "SECRET_KEY", "session", "token-auth", "token-session"):
            self.assertNotIn(secret, output)

    def test_redaction_hides_each_desktop_bank_token(self):
        redact = Redactor({"question_bank": {"yanxi_tokens": "YANXI_A, YANXI_B",
                                            "like_tokens": "LIKE_A,LIKE_B"}})
        output = redact("YANXI_A YANXI_B LIKE_A LIKE_B")
        for secret in ("YANXI_A", "YANXI_B", "LIKE_A", "LIKE_B"):
            self.assertNotIn(secret, output)

    def test_payload_normalizes_all_valid_boolean_values(self):
        c = defaults()
        for section, key in (("ai", "enabled"), ("ai", "vision"), ("tiku", "submit"),
                             ("desktop", "manual_fallback"), ("desktop", "random_fallback"),
                             ("question_bank", "likeapi_search")):
            c[section][key] = "TRUE"
        validate(c)
        p = to_payload(c)
        for section, key in (("ai", "enabled"), ("ai", "vision"), ("tiku", "submit"),
                             ("desktop", "manual_fallback"), ("desktop", "random_fallback"),
                             ("question_bank", "likeapi_search")):
            self.assertEqual(p[section][key], "true")

    def test_uppercase_enabled_ai_still_requires_credentials(self):
        c = defaults()
        c["ai"]["enabled"] = "TRUE"
        with self.assertRaisesRegex(ValueError, "API Key"):
            validate(c, services=True)


if __name__ == "__main__":
    unittest.main()
