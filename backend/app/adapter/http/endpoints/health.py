"""Health and runtime diagnostic endpoints."""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from app.bootstrap.state import ApplicationState, get_application_state

logger = logging.getLogger("svc.health")
router = APIRouter(prefix="/api", tags=["health"])


def _runtime(request: Request) -> ApplicationState:
    return get_application_state(request)


@router.get("/healthz")
def healthz(request: Request) -> Dict[str, Any]:
    """轻量存活检查：不触发模型加载。"""
    return {"ok": True, **_runtime(request).runtime_info()}


@router.get("/livez")
def livez(request: Request) -> Dict[str, Any]:
    """进程活性检测（与 healthz 等价，但可单独用于容器 livenessProbe）。"""
    return {"ok": True, **_runtime(request).runtime_info()}


@router.get("/readyz")
def readyz(request: Request) -> Dict[str, Any]:
    """
    就绪检测：确保服务及模型可用。
    - FoodService 由 lifespan 在应用启动时加载
    - 返回 providers、模型路径、简要状态
    """
    try:
        runtime = _runtime(request)
        try:
            svc = runtime.require_food_service()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=f"not ready: {exc}") from exc
        cfg = runtime.config

        # 读取一些可选信息（不强依赖具体实现）
        seg = getattr(svc, "segmentor", None)
        clip = getattr(svc, "clip", None)

        seg_ok = seg is not None
        clip_ok = clip is not None

        seg_providers = []
        clip_providers = []
        try:
            if seg and getattr(seg, "sess", None):
                seg_providers = list(seg.sess.get_providers() or [])
        except Exception:
            pass
        try:
            if clip and getattr(clip, "sess", None):
                clip_providers = list(clip.sess.get_providers() or [])
        except Exception:
            pass

        seg_path = (cfg.get("SEGMENT_CONFIG") or {}).get("model_path")
        clip_path = (cfg.get("CLIP_CONFIG") or {}).get("model_path")
        clip_text_path = (cfg.get("CLIP_CONFIG") or {}).get("text_model_path")

        ok = runtime.ready and seg_ok and clip_ok

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
                "segmentor": {"ok": seg_ok, "providers": seg_providers, "model_path": seg_path},
                "clip": {
                    "ok": clip_ok,
                    "providers": clip_providers,
                    "model_path": clip_path,
                    "text_model_path": clip_text_path,
                },
            },
            "warmup_error": runtime.warmup_error,
            **runtime.runtime_info(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("readyz failed: %s", e)
        raise HTTPException(status_code=503, detail=f"not ready: {e}")


@router.get("/version")
def version(request: Request) -> Dict[str, Any]:
    """展示当前环境与模型路径（不触发模型加载）。"""
    runtime = _runtime(request)
    cfg = runtime.config
    seg_path = (cfg.get("SEGMENT_CONFIG") or {}).get("model_path")
    clip_path = (cfg.get("CLIP_CONFIG") or {}).get("model_path")
    clip_text_path = (cfg.get("CLIP_CONFIG") or {}).get("text_model_path")

    return {
        "ok": True,
        "app_env": runtime.settings.application.env,
        "models": {
            "segment": seg_path,
            "clip": clip_path,
            "clip_text": clip_text_path,
        },
        **runtime.runtime_info(),
    }


@router.get("/config_info")
def config_info(request: Request) -> Dict[str, Any]:
    """显示当前配置来源。"""
    runtime = _runtime(request)
    return {"ok": True, **runtime.config_info, **runtime.runtime_info()}
