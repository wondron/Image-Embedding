"""Build health diagnostics from application resources."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.bootstrap.state import ApplicationState
from app.core import errors

if TYPE_CHECKING:
    from app.services.predict.food_service import FoodService


def model_paths(config: dict[str, Any]) -> dict[str, Any]:
    """Read model locations without inspecting or initializing model sessions."""
    embedding_config = config.get("EMBEDDING_CONFIG") or {}
    return {
        "embedding": embedding_config.get("model_path")
    }


def _providers(component: Any) -> list[str]:
    """Collect optional provider diagnostics without affecting readiness."""
    try:
        if component and getattr(component, "sess", None):
            return list(component.sess.get_providers() or [])
    except Exception:
        pass
    return []




def dependency_stubs(runtime: ApplicationState | None = None) -> dict[str, Any]:
    """Report Qdrant/PostgreSQL wiring; does not affect model readiness (/readyz)."""
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
    """Report loaded components and preserve runtime readiness failures."""
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
