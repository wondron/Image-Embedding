"""Build health diagnostics from application resources."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from app.bootstrap.state import ApplicationState

if TYPE_CHECKING:
    from app.domain.food_service import FoodService


def model_paths(config: dict[str, Any]) -> dict[str, Any]:
    """Read model locations without inspecting or initializing model sessions."""
    segment_config = config.get("SEGMENT_CONFIG") or {}
    clip_config = config.get("CLIP_CONFIG") or {}
    return {
        "segment": segment_config.get("model_path"),
        "clip": clip_config.get("model_path"),
        "clip_text": clip_config.get("text_model_path"),
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
    """Report Qdrant/MySQL wiring; does not affect model readiness (/readyz)."""
    retrieval = getattr(runtime, "retrieval", None) if runtime is not None else None
    if retrieval is None:
        detail = None
        if runtime is not None:
            detail = getattr(runtime, "retrieval_error", None) or "not initialized"
        return {
            "qdrant": {"status": "not_wired", "detail": detail or "unchecked"},
            "mysql": {"status": "not_wired", "detail": detail or "unchecked"},
        }
    q = retrieval.vectors.ping() if hasattr(retrieval.vectors, "ping") else {"ok": False}
    m = retrieval.metadata.ping() if hasattr(retrieval.metadata, "ping") else {"ok": False}
    return {
        "qdrant": {
            "status": "ok" if q.get("ok") else "error",
            "detail": q.get("detail"),
            "collection": q.get("collection"),
            "points_count": q.get("points_count"),
        },
        "mysql": {
            "status": "ok" if m.get("ok") else "error",
            "detail": m.get("detail"),
        },
    }


def readiness_info(runtime: ApplicationState, service: FoodService) -> dict[str, Any]:
    """Report loaded components and preserve runtime readiness failures."""
    segmentor = getattr(service, "segmentor", None)
    clip = getattr(service, "clip", None)
    segmentor_ok = segmentor is not None
    clip_ok = clip is not None
    segmentor_providers = _providers(segmentor)
    clip_providers = _providers(clip)
    paths = model_paths(runtime.config)
    ok = runtime.ready and segmentor_ok and clip_ok

    if not ok:
        raise HTTPException(
            status_code=503,
            detail=(
                runtime.initialization_error
                or runtime.warmup_error
                or "model components are not ready"
            ),
        )

    return {
        "ok": ok,
        "components": {
            "segmentor": {
                "ok": segmentor_ok,
                "providers": segmentor_providers,
                "model_path": paths["segment"],
            },
            "clip": {
                "ok": clip_ok,
                "providers": clip_providers,
                "model_path": paths["clip"],
                "text_model_path": paths["clip_text"],
            },
        },
        "deps": dependency_stubs(runtime),
        "warmup_error": runtime.warmup_error,
        **runtime.runtime_info(),
    }
