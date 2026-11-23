"""生效的 PostgreSQL / Qdrant 配置的启动诊断。"""

from __future__ import annotations

import json
import logging
import re
import traceback
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import MISSING, asdict, fields
from typing import Any
from urllib.parse import quote, quote_plus

from app.clients.qdrant import QdrantConnection
from app.core.config.settings import PostgresSettings

_SETTINGS = {"POSTGRES_CONFIG": PostgresSettings, "QDRANT_CONFIG": QdrantConnection}
_NAMES = {"POSTGRES_CONFIG": "PostgreSQL", "QDRANT_CONFIG": "Qdrant"}
_URL_AUTH = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^\s/]*@")
_URL_SECRET = re.compile(r"(?i)([?&](?:api[_-]?key|token|access_token|password|secret)=)[^&\s\"'<>]*")


def redact_url_credentials(text: str) -> str:
    """遮盖 URL 里的账号密码（scheme://user:pass@）和 token、api_key 这类查询参数。"""
    text = _URL_AUTH.sub(r"\1***@", text)
    return _URL_SECRET.sub(r"\1***", text)


def redact_text(text: str, config: Mapping[str, Any]) -> str:
    """驱动在错误信息里回显凭证时也要遮盖。"""
    text = redact_url_credentials(text)
    secrets: set[str] = set()
    for name in _SETTINGS:
        section = config.get(name)
        if isinstance(section, Mapping):
            for key in ("password", "api_key"):
                value = section.get(key)
                if value is not None and str(value):
                    secret = str(value)
                    secrets.update((secret, quote(secret, safe=""), quote_plus(secret), json.dumps(secret)[1:-1]))
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, "***")
    return text


def config_summary(config: Mapping[str, Any], name: str) -> dict[str, Any]:
    """包含默认值和非法值，但不输出无关的应用配置。"""
    raw = config.get(name)
    if raw is None:
        return {"status": "missing"}
    if not isinstance(raw, Mapping):
        return {"status": f"expected mapping, got {type(raw).__name__}"}
    cls = _SETTINGS[name]
    summary = {f.name: None if f.default is MISSING else f.default for f in fields(cls)}
    if name == "QDRANT_CONFIG":
        summary["url"] = None  # 启动时要求显式给出 endpoint。
    summary.update({key: raw[key] for key in summary if key in raw})
    try:
        if name == "POSTGRES_CONFIG":
            summary = asdict(PostgresSettings.from_config(config))
        elif raw.get("url") or (raw.get("host") and raw.get("port")):
            summary = asdict(QdrantConnection.from_mapping(raw))
    except (TypeError, ValueError, OverflowError):
        pass  # 配置错误时也必须显示传入的字段。
    if name == "QDRANT_CONFIG":
        summary.update({key: raw[key] for key in ("host", "port") if key in raw})
    for key, value in summary.items():
        if key in ("password", "api_key"):
            summary[key] = "***" if value else value
        elif isinstance(value, str):
            summary[key] = redact_text(value, config)
        elif isinstance(value, (Mapping, list, tuple)):
            summary[key] = f"<invalid {type(value).__name__}>"
    return summary


@contextmanager
def dependency_initialization(
    logger: logging.Logger, config: Mapping[str, Any], name: str, *, stage: str, **context: Any
) -> Iterator[None]:
    """在出问题的依赖自己的边界上记日志，保留原始异常。"""
    try:
        yield
    except Exception as exc:
        logger.error(
            "%s 初始化失败 | stage=%s | message=%s | %s=%s | context=%s",
            _NAMES[name], stage, redact_text(f"{type(exc).__name__}: {exc}", config), name,
            json.dumps(config_summary(config, name), ensure_ascii=False, default=str),
            redact_text(json.dumps(context, ensure_ascii=False, default=str), config),
        )
        raise


def log_startup_exception(logger: logging.Logger, title: str, exc: Exception, config: Mapping[str, Any]) -> None:
    """保留原有的启动 traceback，同时遮盖整个异常链里的凭证。"""
    logger.error("%s\n%s", title, redact_text("".join(traceback.format_exception(exc)), config))
