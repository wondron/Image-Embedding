"""根据应用资源生成健康诊断信息。"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from app.bootstrap.dependency_logging import redact_url_credentials
from app.bootstrap.state import ApplicationState
from app.core import errors

if TYPE_CHECKING:
    from app.services.predict.food_service import FoodService

SECRET_MASK = "***"
# 按键名里的词认密钥：password、secret、token、API_KEY / apiKey、access_key_id、secret_access_key……
# tokenizer_path、key_prefix、max_tokens 这类不算
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SECRET_KEY = re.compile(
    r"(?:^|_)(?:passwords?|passwd|pwd|passphrase|secrets?|token|credentials?|authorization|bearer|cookie|ak|sk)"
    r"(?:_|$)"
    r"|(?:^|_)(?:api|access|secret|private|app|auth|client|sign|signing|master|encryption|hmac|ssh|license)"
    r"_?keys?(?:_|$)"
)


def _is_secret_key(key: Any) -> bool:
    words = re.sub(r"[^a-z0-9]+", "_", _CAMEL.sub("_", str(key)).lower())
    return _SECRET_KEY.search(words) is not None


def _redacted(value: Any, secret: bool) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _redacted(item, secret or _is_secret_key(key)) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_redacted(item, secret) for item in value]
    if value is None or isinstance(value, bool) or (isinstance(value, str) and not value):
        return value  # 没填的密钥照常显示为空，方便看出漏配
    if secret:
        return SECRET_MASK
    if isinstance(value, str):
        return redact_url_credentials(value)
    if isinstance(value, int) or (isinstance(value, float) and math.isfinite(value)):
        return value
    return redact_url_credentials(str(value))  # YAML 的日期、.inf / .nan 转成文本，JSON 才写得出来


def redacted_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """生效配置的展示版：结构和取值照原样，密钥类的键（见 _SECRET_KEY，键下面整个子树都算）的值
    换成 ***，其余字符串里 URL 带的账号密码和 token 查询参数也遮盖。

    只按键名认：密钥拼在别的字符串里（例如写成一整条 DSN）认不出来，要单独成一个键。
    """
    return _redacted(config, False)


def model_paths(config: dict[str, Any]) -> dict[str, Any]:
    """读取模型的位置，不检查也不初始化模型 session。"""
    embedding_config = config.get("EMBEDDING_CONFIG") or {}
    return {
        "embedding": embedding_config.get("model_path")
    }


def _providers(component: Any) -> list[str]:
    """收集可选的 provider 诊断信息，不影响就绪状态。"""
    try:
        if component and getattr(component, "sess", None):
            return list(component.sess.get_providers() or [])
    except Exception:
        pass
    return []




def dependency_stubs(runtime: ApplicationState | None = None) -> dict[str, Any]:
    """报告 Qdrant / PostgreSQL 的接入情况；不影响模型的就绪状态（/readyz）。"""
    retrieval = getattr(runtime, "retrieval", None) if runtime is not None else None
    if retrieval is None:
        detail = None
        if runtime is not None:
            detail = getattr(runtime, "retrieval_error", None) or "not initialized"
        return {
            "qdrant": {"status": "not_wired", "detail": detail or "unchecked"},
            "postgres": {"status": "not_wired", "detail": detail or "unchecked"},
        }
    pings = retrieval.infra.ping()
    q, m = pings["qdrant"], pings["postgres"]
    model = retrieval.model
    return {
        "qdrant": {
            "status": "ok" if q.get("ok") else "error",
            "detail": q.get("detail"),
            "collection": q.get("collection") or model.collection,
            "points_count": q.get("points_count"),
        },
        "postgres": {
            "status": "ok" if m.get("ok") else "error",
            "detail": m.get("detail"),
            "host": m.get("host"),
            "database": m.get("database"),
            "current_model": model.name,
            "current_collection": model.collection,
        },
    }


def readiness_info(runtime: ApplicationState, service: FoodService) -> dict[str, Any]:
    """报告已加载的组件，并保留运行时的就绪失败信息。"""
    embedding = getattr(service, "embeddingTool", None)
    embedding_ok = embedding is not None
    embedding_providers = _providers(embedding)
    paths = model_paths(runtime.config)
    ok = runtime.ready and embedding_ok

    if not ok:
        raise errors.not_ready(
            runtime.initialization_error
            or runtime.warmup_error
            or "model components are not ready"
        )

    return {
        "ok": ok,
        "components": {
            "embedding": {
                "ok": embedding_ok,
                "providers": embedding_providers,
                "model_path": paths["embedding"]
            },
        },
        "deps": dependency_stubs(runtime),
        "warmup_error": runtime.warmup_error,
        **runtime.runtime_info(),
    }
