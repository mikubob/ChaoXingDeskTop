import configparser
import unittest

from chaoxing_desktop.config import (
    ConfigValidationError,
    defaults,
    exam_answer_config,
    parse_text,
    to_payload,
    validate,
    validate_exam_services,
)
from chaoxing_desktop.diagnostic_export import diagnostic_config
from chaoxing_desktop.settings import restore_credentials, serialize_settings
from chaoxing_desktop.settings_schema import SCHEMA


class ExamConfigTests(unittest.TestCase):
    def _configured_base(self):
        config = defaults()
        config["ai"].update(
            enabled="true",
            provider="AI",
            endpoint="https://shared-ai.example.com/v1",
            key="sk-shared-ai-secret",
            model="shared-model",
            vision="true",
            image_confirm_policy="ask",
        )
        config["exam_ai"].update(
            enabled="true",
            provider="SiliconFlow",
            endpoint="https://custom-ai.example.com/v1",
            key="sk-custom-exam-secret",
            model="custom-exam-model",
            vision="false",
            image_confirm_policy="always_skip",
        )
        config["question_bank"].update(
            providers="TikuGo,TikuYanxi",
            yanxi_tokens="shared-yanxi-token",
            go_authorization="shared-go-auth",
        )
        config["exam_question_bank"].update(
            providers="TikuLike,TikuAdapter",
            like_tokens="custom-like-token",
            adapter_url="https://custom-adapter.example.com/search",
        )
        return config

    def test_four_source_combinations_produce_expected_effective_config(self):
        cases = (
            ("shared", "shared", "shared-model", "TikuGo,TikuYanxi", {"ai": "ai", "question_bank": "question_bank"}),
            ("custom", "shared", "custom-exam-model", "TikuGo,TikuYanxi", {"ai": "exam_ai", "question_bank": "question_bank"}),
            ("shared", "custom", "shared-model", "TikuLike,TikuAdapter", {"ai": "ai", "question_bank": "exam_question_bank"}),
            ("custom", "custom", "custom-exam-model", "TikuLike,TikuAdapter", {"ai": "exam_ai", "question_bank": "exam_question_bank"}),
        )
        for ai_source, bank_source, expected_model, expected_providers, expected_map in cases:
            with self.subTest(ai_source=ai_source, bank_source=bank_source):
                config = self._configured_base()
                config["exam"]["ai_source"] = ai_source
                config["exam"]["bank_source"] = bank_source
                effective, section_map = exam_answer_config(config)
                self.assertEqual(section_map, expected_map)
                self.assertEqual(effective["ai"]["model"], expected_model)
                self.assertEqual(effective["question_bank"]["providers"], expected_providers)
                for removed in ("exam", "exam_ai", "exam_question_bank"):
                    self.assertFalse(effective.has_section(removed))
                # 原配置不被修改
                self.assertEqual(config["ai"]["model"], "shared-model")
                self.assertEqual(config["exam_ai"]["model"], "custom-exam-model")

    def test_exam_effective_config_forces_safe_non_submitting_policies(self):
        config = self._configured_base()
        config["desktop"]["manual_fallback"] = "true"
        config["desktop"]["random_fallback"] = "true"
        config["tiku"]["submit"] = "true"
        effective, _ = exam_answer_config(config)
        self.assertEqual(effective["desktop"]["manual_fallback"], "false")
        self.assertEqual(effective["desktop"]["random_fallback"], "false")
        self.assertEqual(effective["tiku"]["submit"], "false")
        payload = to_payload(effective)
        self.assertEqual(payload["desktop"]["manual_fallback"], "false")
        self.assertEqual(payload["desktop"]["random_fallback"], "false")
        self.assertEqual(payload["tiku"]["submit"], "false")

    def test_credentials_dpapi_roundtrip_and_diagnostic_export_redaction(self):
        config = self._configured_base()
        config["exam"]["assist_enabled"] = "true"
        config["exam"]["ai_source"] = "custom"
        config["exam"]["bank_source"] = "custom"
        config["exam_ai"]["http_proxy"] = "http://proxy-secret.example.com:8080"
        config["exam_question_bank"].update(
            providers="TikuYanxi,TikuLike,TikuAdapter,TikuGo,TikuDeclarative",
            yanxi_tokens="exam-yanxi-secret-1, exam-yanxi-secret-2",
            like_tokens="exam-like-secret",
            adapter_url="https://exam-adapter-secret.example.com/query",
            go_authorization="Bearer exam-go-secret",
            declarative_token="exam-decl-secret",
        )

        serialized = serialize_settings(config)
        for secret in (
            "sk-shared-ai-secret",
            "sk-custom-exam-secret",
            "proxy-secret.example.com",
            "exam-yanxi-secret-1",
            "exam-like-secret",
            "exam-adapter-secret.example.com",
            "exam-go-secret",
            "exam-decl-secret",
        ):
            self.assertNotIn(secret, serialized)

        loaded = parse_text(serialized)
        restore_credentials(loaded)
        self.assertEqual(loaded["exam_ai"]["key"], "sk-custom-exam-secret")
        self.assertEqual(loaded["exam_ai"]["http_proxy"], "http://proxy-secret.example.com:8080")
        self.assertEqual(loaded["exam_question_bank"]["yanxi_tokens"], "exam-yanxi-secret-1, exam-yanxi-secret-2")
        self.assertEqual(loaded["exam_question_bank"]["like_tokens"], "exam-like-secret")
        self.assertEqual(loaded["exam_question_bank"]["adapter_url"], "https://exam-adapter-secret.example.com/query")
        self.assertEqual(loaded["exam_question_bank"]["go_authorization"], "Bearer exam-go-secret")
        self.assertEqual(loaded["exam_question_bank"]["declarative_token"], "exam-decl-secret")

        # 注入未知 provider 以确认排障导出只保留合法 ID 且不含凭据与 URL
        loaded["exam_question_bank"]["providers"] = "TikuYanxi,InvalidProvider,TikuLike"
        diag_text = diagnostic_config(loaded)
        for secret in (
            "sk-custom-exam-secret",
            "proxy-secret.example.com",
            "exam-yanxi-secret-1",
            "exam-like-secret",
            "exam-adapter-secret.example.com",
            "custom-ai.example.com",
        ):
            self.assertNotIn(secret, diag_text)
        diag = configparser.ConfigParser(interpolation=None)
        diag.read_string(diag_text)
        self.assertEqual(diag.get("exam", "assist_enabled"), "true")
        self.assertEqual(diag.get("exam", "ai_source"), "custom")
        self.assertEqual(diag.get("exam", "bank_source"), "custom")
        self.assertEqual(diag.get("exam_question_bank", "providers"), "TikuYanxi,TikuLike")

    def test_validation_maps_custom_exam_errors_to_exam_sections(self):
        # 1. 单独配置缺少 API Key
        config = self._configured_base()
        config["exam"]["ai_source"] = "custom"
        config["exam_ai"]["key"] = "   "
        with self.assertRaises(ConfigValidationError) as ctx:
            validate_exam_services(config, services=True)
        self.assertEqual((ctx.exception.section, ctx.exception.key), ("exam_ai", "key"))

        # 2. 单独配置 AI 服务地址非法
        config = self._configured_base()
        config["exam"]["ai_source"] = "custom"
        config["exam_ai"]["endpoint"] = "ftp://invalid.example.com"
        with self.assertRaises(ConfigValidationError) as ctx:
            validate_exam_services(config, services=True)
        self.assertEqual((ctx.exception.section, ctx.exception.key), ("exam_ai", "endpoint"))

        # 3. 单独配置题库缺少 Token
        config = self._configured_base()
        config["exam"]["bank_source"] = "custom"
        config["exam_question_bank"]["providers"] = "TikuYanxi"
        config["exam_question_bank"]["yanxi_tokens"] = ""
        with self.assertRaises(ConfigValidationError) as ctx:
            validate_exam_services(config, services=True)
        self.assertEqual((ctx.exception.section, ctx.exception.key), ("exam_question_bank", "yanxi_tokens"))

        # 4. 单独配置题库接口地址非法
        config = self._configured_base()
        config["exam"]["bank_source"] = "custom"
        config["exam_question_bank"]["providers"] = "TikuGo"
        config["exam_question_bank"]["go_endpoint"] = "not-a-url"
        with self.assertRaises(ConfigValidationError) as ctx:
            validate_exam_services(config, services=True)
        self.assertEqual((ctx.exception.section, ctx.exception.key), ("exam_question_bank", "go_endpoint"))

        # 5. 单独配置数值越界（即使在保存设置 validate(config) 时也能捕获并定位到 exam_ai / exam_question_bank）
        config = self._configured_base()
        config["exam_ai"]["timeout"] = "999"
        with self.assertRaises(ConfigValidationError) as ctx:
            validate(config)
        self.assertEqual((ctx.exception.section, ctx.exception.key), ("exam_ai", "timeout"))

        # 6. 单独配置题库名无效或声明式 JSON 损坏，在保存设置 validate(config) 时被拦截
        config = self._configured_base()
        config["exam"]["bank_source"] = "custom"
        config["exam_question_bank"]["providers"] = "TikuDeclarative"
        config["exam_question_bank"]["declarative_definition"] = "{bad json"
        with self.assertRaises(ConfigValidationError) as ctx:
            validate(config)
        self.assertEqual((ctx.exception.section, ctx.exception.key), ("exam_question_bank", "declarative_definition"))

        # 7. exam.ai_source / bank_source 非法值校验
        config = self._configured_base()
        config["exam"]["ai_source"] = "invalid"
        with self.assertRaises(ConfigValidationError) as ctx:
            validate(config)
        self.assertEqual((ctx.exception.section, ctx.exception.key), ("exam", "ai_source"))

    def test_schema_clones_rewrite_section_and_depends(self):
        self.assertIn("exam_service", SCHEMA)
        self.assertIn("exam_ai", SCHEMA)
        for field in SCHEMA["exam_ai"][0]["fields"]:
            self.assertEqual(field["section"], "exam_ai")
            if field["depends"]:
                self.assertEqual(field["depends"]["section"], "exam_ai")
        for key in ("exam_bank_go", "exam_bank_yanxi", "exam_bank_like", "exam_bank_adapter", "exam_bank_declarative"):
            self.assertIn(key, SCHEMA)
            for group in SCHEMA[key]:
                for field in group["fields"]:
                    self.assertEqual(field["section"], "exam_question_bank")


if __name__ == "__main__":
    unittest.main()
