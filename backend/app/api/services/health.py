"""根据应用资源生成健康诊断信息。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.bootstrap.state import ApplicationState
from app.core import errors

if TYPE_CHECKING:
    from app.services.predict.food_service import FoodService


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
