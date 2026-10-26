"""Health and runtime diagnostic endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.adapter.http.services.health import model_paths, readiness_info
from app.bootstrap.state import get_application_state

logger = logging.getLogger("svc.health")
router = APIRouter(prefix="/api", tags=["health"])


@router.get("/healthz")
def healthz(request: Request) -> dict[str, Any]:
    """轻量存活检查：不触发模型加载。"""
    return {"ok": True, **get_application_state(request).runtime_info()}


@router.get("/livez")
def livez(request: Request) -> dict[str, Any]:
    """进程活性检测（与 healthz 等价，但可单独用于容器 livenessProbe）。"""
    return {"ok": True, **get_application_state(request).runtime_info()}


@router.get("/readyz")
def readyz(request: Request) -> dict[str, Any]:
    """
    就绪检测：确保服务及模型可用。
    - FoodService 由 lifespan 在应用启动时加载
    - 返回 providers、模型路径、简要状态
    """
    try:
        runtime = get_application_state(request)
        try:
            service = runtime.require_food_service()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=f"not ready: {exc}") from exc
        return readiness_info(runtime, service)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("readyz failed: %s", exc)
        raise HTTPException(status_code=503, detail=f"not ready: {exc}") from exc


@router.get("/version")
def version(request: Request) -> dict[str, Any]:
    """展示当前环境与模型路径（不触发模型加载）。"""
    runtime = get_application_state(request)
    return {
        "ok": True,
        "app_env": runtime.settings.application.env,
        "models": model_paths(runtime.config),
        **runtime.runtime_info(),
    }


@router.get("/config_info")
def config_info(request: Request) -> dict[str, Any]:
    """显示当前配置来源。"""
    runtime = get_application_state(request)
    return {"ok": True, **runtime.config_info, **runtime.runtime_info()}
