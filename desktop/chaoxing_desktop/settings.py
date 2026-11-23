"""Local settings retain service credentials in an encrypted INI section."""
from __future__ import annotations

import base64
import configparser
import json

from . import credentials
from .config import is_secret, parser, serialize

CREDENTIAL_SECTION = "desktop_credentials"
STORED_SECRETS = {
    "question_bank": ("yanxi_tokens", "like_tokens", "adapter_url", "go_authorization", "declarative_token"),
    "ai": ("key", "http_proxy"),
    "notification": ("url", "tg_chat_id"),
}
STORED_SECRETS["exam_ai"] = STORED_SECRETS["ai"]
STORED_SECRETS["exam_question_bank"] = STORED_SECRETS["question_bank"]


def _service_credentials(config):
    secrets = {section: {key: config[section][key] for key in keys if config[section][key]}
               for section, keys in STORED_SECRETS.items()}
    return {section: values for section, values in secrets.items() if values}


def settings_snapshot(config):
    # Account selection and Cookie files have their own persistence.
    return ({section: {key: value for key, value in config[section].items()
                       if not is_secret(key) and (section != 'common' or key not in ('username', 'use_cookies'))}
             for section in config.sections() if section != CREDENTIAL_SECTION},
            _service_credentials(config))


def serialize_settings(config: configparser.ConfigParser) -> str:
    public = parser()
    public.read_string(serialize(config, include_secrets=False))
    public.remove_section(CREDENTIAL_SECTION)
    secrets = _service_credentials(config)
    if secrets:
        try:
            encrypted = credentials.protect(json.dumps(secrets, ensure_ascii=False).encode("utf-8"))
        except OSError:
            raise ValueError("服务凭据加密失败，设置未保存。请检查 Windows 用户环境后重试。") from None
        public[CREDENTIAL_SECTION] = {
            "format": "dpapi-v1", "data": base64.b64encode(encrypted).decode("ascii"),
        }
    return serialize(public)


def restore_credentials(config: configparser.ConfigParser) -> None:
    if not config.has_section(CREDENTIAL_SECTION):
        return
    stored = dict(config[CREDENTIAL_SECTION])
    # The encrypted storage envelope must never leak into exports or worker payloads.
    config.remove_section(CREDENTIAL_SECTION)
    try:
        if stored.get("format") != "dpapi-v1":
            raise ValueError
        encrypted = base64.b64decode(stored["data"], validate=True)
        secrets = json.loads(credentials.unprotect(encrypted).decode("utf-8"))
        if not isinstance(secrets, dict):
            raise ValueError
        restored = {}
        for section, keys in STORED_SECRETS.items():
            values = secrets.get(section, {})
            if not isinstance(values, dict):
                raise ValueError
            for key in keys:
                value = values.get(key, "")
                if not isinstance(value, str):
                    raise ValueError
                restored[(section, key)] = value
    except (OSError, ValueError, KeyError, UnicodeError):
        raise ValueError("本机服务凭据无法解密，其他设置已保留。请使用保存时的 Windows 账户，或重新填写凭据并保存。") from None
    for (section, key), value in restored.items():
        config[section][key] = value
