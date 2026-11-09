from __future__ import annotations

import configparser
import io
import json
import os
import re
import sys
from pathlib import Path

from .providers import PROVIDERS
BANKS = tuple((spec.title, spec.id) for spec in PROVIDERS)
CONFIG_VERSION = 1
DISCUSSION_LEVELS = (("精简（约 50–100 字）", "concise"), ("中等（约 150–250 字）", "medium"),
                     ("详细（约 350–600 字）", "detailed"), ("固定回复一个字：好", "fixed_good"))
ACTIVITY_CHOICES = (("AI 作答", "ai"), ("随机选择（本地）", "random"), ("AI 失败后随机", "ai_then_random"))
ACTIVITY_TEXTS = (("AI 回复", "ai"), ("固定一个字：好（本地）", "fixed_good"))
BANK_REVIEW_POLICIES = (("严格核验（默认）", "strict"),
                        ("跟随 AI 状态", "ai_dependent"),
                        ("选项优先（放宽题干）", "options_only"))
NOTIFICATIONS = ("ServerChan", "Qmsg", "Bark", "Telegram")
SECRET_KEYS = {"password", "tokens", "token", "key", "siliconflow_key", "go_authorization",
               "url", "adapter_url", "tg_chat_id", "http_proxy", "cookies", "cookie"}
BOOLEAN_KEYS = {
    "common": ("use_cookies", "work_redo_enabled", "add_learning_count", "ignore_completion_cache"),
    "tiku": ("submit",),
    "ai": ("enabled", "vision", "check_connection"),
    "discussion": ("enabled", "auto_submit"),
    "activity": ("enabled", "auto_submit"),
    "desktop": ("manual_fallback", "random_fallback"),
    "question_bank": ("likeapi_search", "likeapi_vision", "likeapi_retry", "yanxi_query_variants"),
    "matching": ("fuzzy_options", "reuse_similar"),
}


def resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


def data_dir() -> Path:
    override = os.environ.get("CHAOXING_DESKTOP_DATA")
    path = Path(override) if override else Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ChaoxingDesktop"
    if not override and getattr(sys, "frozen", False):
        marker = Path(sys.executable).resolve().with_name("installation.json")
        if marker.is_file():
            profile = json.loads(marker.read_text(encoding="utf-8"))["profile"]
            if not isinstance(profile, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", profile):
                raise ValueError("安装配置标识无效，请重新安装。")
            path = path / "profiles" / profile
    path.mkdir(parents=True, exist_ok=True)
    return path


def parser() -> configparser.ConfigParser:
    return configparser.ConfigParser(interpolation=None)


def defaults() -> configparser.ConfigParser:
    config = parser()
    config.read_dict({
        "common": dict(username="", password="", use_cookies="false", speed="1.0", jobs="4",
                       notopen_action="retry", retry_interval="1.0", work_redo_enabled="false",
                       work_max_retries="3", add_learning_count="false", target_count="100",
                       ignore_completion_cache="false"),
        "tiku": dict(provider="", submit="false", cover_rate="1.0", delay="1.0",
                     true_list="正确,对,√,是", false_list="错误,错,×,否,不对,不正确"),
        "question_bank": dict(providers="", yanxi_tokens="", like_tokens="", adapter_url="",
                              go_authorization="", go_min_interval="1.0", go_retry_times="3",
                              go_retry_backoff="1.2", likeapi_search="false", likeapi_vision="true",
                              likeapi_model="glm-4.5-air", likeapi_retry="true", likeapi_retry_times="3"),
        "ai": dict(enabled="false", provider="AI", endpoint="", key="", model="",
                   vision="false", min_interval_seconds="3.0", http_proxy="",
                   check_connection="true", timeout="60", retries="2", request_limit="0", token_warning="0"),
        "desktop": dict(manual_fallback="false", random_fallback="false"),
        "discussion": dict(enabled="false", reply_level="medium", auto_submit="false"),
        "activity": dict(enabled="false", choice_strategy="ai", text_strategy="ai", reply_level="medium", auto_submit="false"),
        "logging": dict(directory=""),
        "matching": dict(bank_review_policy="strict", fuzzy_options="true", min_similarity="0.92", min_margin="0.08",
                         reuse_similar="true", title_similarity="0.96"),
        "notification": dict(provider="", url="", tg_chat_id=""),
    })
    bank = config["question_bank"]
    bank['yanxi_query_variants'] = 'true'
    bank['yanxi_variant_limit'] = '2'
    for prefix, address, timeout in (("yanxi", "https://tk.enncy.cn/query", "30"),
                                     ("go", "https://q.icodef.com/wyn-nb?v=4", "15"),
                                     ("like", "https://app.datam.site/api/v1/query", "300"),
                                     ("adapter", "", "30")):
        if prefix != "adapter":
            bank[prefix + "_endpoint"] = address
        bank[prefix + "_timeout"] = timeout
        if prefix != "go":
            bank[prefix + "_min_interval"] = "1.0"
        if prefix in ("yanxi", "adapter"):
            bank[prefix + "_retries"] = "2"
    config['format'] = {'version': str(CONFIG_VERSION)}
    config['question_bank'].update(declarative_definition='', declarative_token='',
                                  declarative_timeout='30', declarative_min_interval='1.0',
                                  declarative_retries='2', request_budget='12')
    return config


def parse_text(text: str) -> configparser.ConfigParser:
    config = defaults()
    incoming = parser()
    incoming.read_string(text.lstrip("\ufeff"))
    try:
        version = int(incoming.get('format', 'version', fallback='0'))
    except ValueError:
        raise ValueError('配置格式版本无效') from None
    if version < 0 or version > CONFIG_VERSION:
        raise ValueError('配置来自更新版本的软件，请先升级；原文件未修改。')
    for section in incoming.sections():
        if not config.has_section(section):
            config.add_section(section)
        config[section].update(incoming[section])
    # Preserve the old global interval when importing configurations predating
    # independent bank settings. Newly saved settings always take precedence.
    if incoming.has_option("tiku", "delay"):
        for prefix in ("yanxi", "like", "adapter"):
            key = prefix + "_min_interval"
            if not incoming.has_option("question_bank", key):
                config["question_bank"][key] = incoming["tiku"]["delay"]
    # Releases through 0.2.2 redacted this switch as if it were a Cookie secret.
    # Recover the default for the lost switch without discarding other settings.
    if not config["common"]["use_cookies"].strip():
        config["common"]["use_cookies"] = "false"
    # Migrate old INI files once; desktop sections take precedence on re-import.
    if incoming.has_section("tiku"):
        old = incoming["tiku"]
        names = [x.strip() for x in old.get("provider", "").split(",") if x.strip()]
        if not incoming.has_section("question_bank"):
            bank = config["question_bank"]
            bank["providers"] = ",".join(x for x in names if x not in ("AI", "SiliconFlow", "TikuManual"))
            for key in bank:
                source = {"yanxi_tokens": "tokens", "like_tokens": "tokens", "adapter_url": "url"}.get(key, key)
                if source in old:
                    bank[key] = old[source]
        if not incoming.has_section("ai"):
            ai = config["ai"]
            ai["enabled"] = "true" if any(x in names for x in ("AI", "SiliconFlow")) else "false"
            ai["provider"] = next((x for x in names if x in ("AI", "SiliconFlow")), "AI")
            for key in (k for k in ai if k not in ("enabled", "provider")):
                source = "check_llm_connection" if key == "check_connection" else key
                if source in old:
                    ai[key] = old[source]
            if ai["provider"] == "SiliconFlow":
                for key in ("key", "model", "endpoint"):
                    ai[key] = old.get("siliconflow_" + key, {"key": "", "model": "deepseek-ai/DeepSeek-R1",
                                                          "endpoint": "https://api.siliconflow.cn/v1"}[key])
        if not incoming.has_section("desktop"):
            config["desktop"]["manual_fallback"] = "true" if "TikuManual" in names else "false"
        config["tiku"]["provider"] = ""  # Never execute a legacy provider chain.
    # Remove migrated keys so exports cannot contain stale, conflicting secrets.
    legacy_keys = {"tokens", "url", "endpoint", "key", "model", "min_interval_seconds", "http_proxy",
                   "check_llm_connection", "siliconflow_key", "siliconflow_model", "siliconflow_endpoint",
                   "manual_mode_default", "manual_mode_separator"} | set(config["question_bank"])
    for key in list(config["tiku"]):
        if key in legacy_keys:
            del config["tiku"][key]
    config['format']['version'] = str(CONFIG_VERSION)
    return config


def serialize(config: configparser.ConfigParser, include_secrets: bool = True) -> str:
    copy = parser()
    for section in config.sections():
        copy[section] = {k: v if include_secrets or not is_secret(k) else "" for k, v in config[section].items()}
    out = io.StringIO()
    copy.write(out)
    return out.getvalue()


def is_secret(key: str) -> bool:
    key = key.lower()
    if key in ("use_cookies", "token_warning"):
        return False
    return key in SECRET_KEYS or any(s in key for s in ("password", "token", "secret", "authorization", "cookie", "api_key")) or key.endswith("_key")


class ConfigValidationError(ValueError):
    def __init__(self, message, section, key):
        super().__init__(message)
        self.section, self.key = section, key


def validate(config: configparser.ConfigParser, login: bool = False, services: bool = False,
             cookie_path: Path | None = None) -> None:
    if config.get('format', 'version', fallback='') != str(CONFIG_VERSION):
        raise ValueError('配置格式版本无效或来自更新版本')
    c, t, n = (config[s] for s in ("common", "tiku", "notification"))
    # One numeric contract for UI edits, INI imports and workers.
    from .qml_schema import SCHEMA
    fields = {(f['section'], f['key']): f for groups in SCHEMA.values() for group in groups for f in group['fields']}
    numeric = [(config[section], key, spec['low'], spec['high'], int if spec['kind'] == 'integer' else float)
               for (section, key), spec in fields.items() if spec['kind'] in ('integer', 'number')]
    numeric.append((t, 'cover_rate', 0, 1, float))
    if config['question_bank']['declarative_definition'].strip():
        from .declarative import validate_definition
        try:
            validate_definition(config['question_bank']['declarative_definition'])
        except (ValueError, TypeError, AttributeError) as exc:
            raise ConfigValidationError(str(exc), 'question_bank', 'declarative_definition') from None
    if 'TikuDeclarative' in config['question_bank']['providers'].split(',') and not config['question_bank']['declarative_definition'].strip():
        raise ConfigValidationError('请先导入声明式 JSON 题库定义', 'question_bank', 'declarative_definition')
    for section, key, low, high, cast in numeric:
        try:
            value = cast(section[key])
            if not low <= value <= high:
                raise ValueError
        except (ValueError, KeyError):
            title = fields.get((section.name, key), {}).get('title', key)
            raise ConfigValidationError(f"{title}（{key}）必须在 {low}–{high} 范围内。", section.name, key) from None
    for section, keys in BOOLEAN_KEYS.items():
        for key in keys:
            if config[section][key].lower() not in ("true", "false"):
                raise ConfigValidationError(f"{key} 应填写 true 或 false。", section, key)
    if config['matching']['bank_review_policy'] not in {value for _, value in BANK_REVIEW_POLICIES}:
        raise ConfigValidationError('题库答案审核策略无效，请选择严格核验、跟随 AI 状态或选项优先。', 'matching', 'bank_review_policy')
    if config['discussion']['reply_level'] not in {value for _, value in DISCUSSION_LEVELS}:
        raise ConfigValidationError('讨论回复水平无效，请选择精简、中等、详细或固定好。', 'discussion', 'reply_level')
    for key, choices in (('choice_strategy', ACTIVITY_CHOICES), ('text_strategy', ACTIVITY_TEXTS),
                         ('reply_level', DISCUSSION_LEVELS[:3])):
        if config['activity'][key] not in {value for _, value in choices}:
            raise ConfigValidationError('投票／问卷配置无效：' + key, 'activity', key)
    if c["notopen_action"] not in ("retry", "ask", "continue"):
        raise ConfigValidationError("关闭章节策略应为 retry、ask 或 continue。", 'common', 'notopen_action')
    names = [s.strip() for s in config["question_bank"]["providers"].split(",") if s.strip()]
    if t["provider"] or any(s not in dict((value, title) for title, value in BANKS) for s in names) or len(names) != len(set(names)):
        raise ValueError("题库配置无效，请在题库列表中选择服务。")
    if config["ai"]["provider"] not in ("AI", "SiliconFlow"):
        raise ConfigValidationError("AI 服务类型无效。", 'ai', 'provider')
    if n["provider"] and n["provider"] not in NOTIFICATIONS:
        raise ConfigValidationError("通知服务名称无效。", 'notification', 'provider')
    if login and c["use_cookies"].lower() != "true" and (not c["username"].strip() or not c["password"].strip()):
        raise ValueError("请填写账号和密码，或选择 Cookie 登录。")
    if login and c["use_cookies"].lower() == "true" and not (cookie_path if cookie_path is not None else data_dir() / "cookies.txt").is_file():
        raise ValueError("请先导入 Cookie 文件。")
    if login or services:
        bank, ai = config["question_bank"], config["ai"]
        for name, key in (("TikuYanxi", "yanxi_tokens"), ("TikuLike", "like_tokens")):
            if name in names and not bank[key].strip():
                raise ConfigValidationError(f"{dict((v, k) for k, v in BANKS)[name]}需要填写 Token。", 'question_bank', key)
        if ai["enabled"].lower() == "true" and not all(ai[k].strip() for k in ("endpoint", "key", "model")):
            key = next(k for k in ('endpoint', 'key', 'model') if not ai[k].strip())
            raise ConfigValidationError("启用 AI 时需要填写服务地址、API Key 和模型。", 'ai', key)
        if "TikuAdapter" in names and not bank["adapter_url"].strip():
            raise ConfigValidationError("Adapter 题库需要填写 URL。", 'question_bank', 'adapter_url')
        from urllib.parse import urlparse
        for provider, prefix in (("TikuYanxi", "yanxi"), ("TikuGo", "go"), ("TikuLike", "like")):
            address = bank[prefix + "_endpoint"].strip()
            if provider in names and (urlparse(address).scheme not in ("http", "https") or not urlparse(address).netloc):
                raise ConfigValidationError(f"{dict((v, k) for k, v in BANKS)[provider]}地址应为完整的 HTTP 或 HTTPS 地址。", 'question_bank', prefix + '_endpoint')
        for title, address, section, key in [("AI 服务地址", ai["endpoint"] if ai["enabled"].lower() == "true" else "", 'ai', 'endpoint'),
                               ("Adapter 地址", bank["adapter_url"] if "TikuAdapter" in names else "", 'question_bank', 'adapter_url'),
                               ("通知地址", n["url"] if n["provider"] else "", 'notification', 'url'), ("AI 代理", ai["http_proxy"], 'ai', 'http_proxy')]:
            if address and (urlparse(address).scheme not in ("http", "https") or not urlparse(address).netloc):
                raise ConfigValidationError(f"{title}应为完整的 HTTP 或 HTTPS 地址。", section, key)
        if n["provider"] and not n["url"].strip():
            raise ConfigValidationError("启用通知时需要填写通知 URL。", 'notification', 'url')


def to_payload(config: configparser.ConfigParser) -> dict:
    payload = {s: dict(config[s]) for s in config.sections()}
    for section, keys in BOOLEAN_KEYS.items():
        for key in keys:
            payload[section][key] = payload[section][key].lower()
    payload["question_bank"]["providers"] = ",".join(x.strip() for x in payload["question_bank"]["providers"].split(",") if x.strip())
    for key in ("yanxi_tokens", "like_tokens"):
        payload["question_bank"][key] = ",".join(x.strip() for x in payload["question_bank"][key].split(",") if x.strip())
    for key in ("endpoint", "key", "model", "http_proxy"):
        payload["ai"][key] = payload["ai"][key].strip()
    c, t = payload["common"], payload["tiku"]
    for key in ("speed", "retry_interval"):
        c[key] = float(c[key])
    for key in ("jobs", "work_max_retries", "target_count"):
        c[key] = int(c[key])
    for key in BOOLEAN_KEYS['common']:
        c[key] = c[key].lower() == "true"
    for key in ("cover_rate", "delay"):
        t[key] = float(t[key])
    return payload


def course_key(course: dict) -> tuple[str, str]:
    return str(course["courseId"]), str(course["clazzId"])


def select_courses(courses: list[dict], keys: list[list[str]]) -> list[dict]:
    wanted = {tuple(map(str, key)) for key in keys}
    if not wanted:
        raise ValueError("请至少选择一门课程。")
    found = {course_key(c): c for c in courses if course_key(c) in wanted}
    if set(found) != wanted:
        raise ValueError("选中课程已发生变化，请重新读取课程并选择。")
    return list(found.values())


class Redactor:
    def __init__(self, config: dict):
        secrets = {str(v) for section in config.values() if isinstance(section, dict)
                   for k, v in section.items() if is_secret(k) and v}
        for section in config.values():
            if isinstance(section, dict):
                for key, value in section.items():
                    if key.lower() == "tokens" or key.lower().endswith("_tokens"):
                        secrets.update(x.strip() for x in str(value or "").split(",") if x.strip())
        self.secrets = sorted(secrets, key=len, reverse=True)

    def __call__(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, "[已隐藏]")
        text = re.sub(r"(?i)(?:Cookie|Authorization|Bearer)\s*[:= ].*", "[会话信息已隐藏]", text)
        text = re.sub(r"(?i)((?:token|password|api_key|key|_uid|UID|fid|cpi|enc|jtoken|dtoken)=)[^\s&;,]+", r"\1[已隐藏]", text)
        return text
