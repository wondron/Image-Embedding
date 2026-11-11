"""HTTP adapter for the scene configuration and the membership sync.

The configuration is reached through the catalog (PostgreSQL only); the sync through the
retrieval stack, because it writes vector payloads (NOT_READY 503 while either is not built).
Failures are AppError from the rules and the stores: VALIDATION_ERROR 422, SCENE_NOT_FOUND 404,
SCENE_CODE_EXISTS / SCENE_RULE_EXISTS / SCENE_IN_USE 409, SCENE_PROTECTED 403,
SCENE_POSTGRES_FAILED 502; the sync also INDEX_POSTGRES_FAILED / INDEX_QDRANT_FAILED 502.
Routes are sync, so FastAPI runs them in the threadpool.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request

from app.api.deps import require_catalog, require_retrieval
from app.bootstrap.state import ApplicationState
from app.core.middleware import get_trace_id
from app.schemas.scenes import (
    SceneCreateRequest,
    SceneLabelCreateRequest,
    SceneLabelUpdateRequest,
    SceneRuleCreateRequest,
    SceneRuleUpdateRequest,
    SceneSyncRequest,
    SceneUpdateRequest,
)

logger = logging.getLogger("svc.scenes")


def _logged(request: Request, action: str, result: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """Log a configuration write; result is the use case's {scene, sync_required}."""
    scene = result["scene"]
    details = " ".join(f"{key}={value}" for key, value in extra.items())
    logger.info(
        "scene %s id=%s code=%s %ssync_required=%s | trace_id=%s",
        action, scene.get("id"), scene.get("code"), f"{details} " if details else "",
        result["sync_required"], get_trace_id(request),
    )
    return {"ok": True, **result}


def _target(fields: dict[str, Any]) -> str:
    if fields.get("category_id") is not None:
        return f"category:{fields['category_id']}"
    return f"cuisine:{fields.get('cuisine')}"


def list_scenes(runtime: ApplicationState) -> dict[str, Any]:
    return {"ok": True, **require_catalog(runtime).scenes_uc.list()}


def create_scene(runtime: ApplicationState, request: Request, body: SceneCreateRequest) -> dict[str, Any]:
    return _logged(request, "created", require_catalog(runtime).scenes_uc.create(body.model_dump()))


def update_scene(
    runtime: ApplicationState, request: Request, scene_id: int, body: SceneUpdateRequest
) -> dict[str, Any]:
    fields = body.model_dump(exclude_unset=True)  # absent stays unchanged; an explicit null is kept
    result = require_catalog(runtime).scenes_uc.update(scene_id, fields)
    return _logged(request, "updated", result, fields=",".join(sorted(fields)))


def delete_scene(runtime: ApplicationState, request: Request, scene_id: int) -> dict[str, Any]:
    result = require_catalog(runtime).scenes_uc.delete(scene_id)
    deleted = result["deleted"]
    logger.info(
        "scene deleted id=%s code=%s sync_required=%s | trace_id=%s",
        deleted.get("id"), deleted.get("code"), result["sync_required"], get_trace_id(request),
    )
    return {"ok": True, **result}


def create_label(
    runtime: ApplicationState, request: Request, scene_id: int, body: SceneLabelCreateRequest
) -> dict[str, Any]:
    result = require_catalog(runtime).scenes_uc.create_label(scene_id, body.model_dump())
    return _logged(request, "label created", result, label=body.code)


def update_label(
    runtime: ApplicationState, request: Request, scene_id: int, label_id: int, body: SceneLabelUpdateRequest
) -> dict[str, Any]:
    fields = body.model_dump(exclude_unset=True)
    result = require_catalog(runtime).scenes_uc.update_label(scene_id, label_id, fields)
    return _logged(request, "label updated", result, label_id=label_id, fields=",".join(sorted(fields)))


def delete_label(runtime: ApplicationState, request: Request, scene_id: int, label_id: int) -> dict[str, Any]:
    result = require_catalog(runtime).scenes_uc.delete_label(scene_id, label_id)
    return _logged(request, "label deleted", result, label_id=label_id)


def create_rule(
    runtime: ApplicationState, request: Request, scene_id: int, body: SceneRuleCreateRequest
) -> dict[str, Any]:
    fields = body.model_dump(exclude_unset=True)
    result = require_catalog(runtime).scenes_uc.create_rule(scene_id, fields)
    return _logged(request, "rule created", result, target=_target(fields))


def update_rule(
    runtime: ApplicationState, request: Request, scene_id: int, rule_id: int, body: SceneRuleUpdateRequest
) -> dict[str, Any]:
    fields = body.model_dump(exclude_unset=True)  # absent stays unchanged; label_id null clears it
    result = require_catalog(runtime).scenes_uc.update_rule(scene_id, rule_id, fields)
    extra: dict[str, Any] = {"rule_id": rule_id, "fields": ",".join(sorted(fields))}
    if "category_id" in fields or "cuisine" in fields:
        extra["target"] = _target(fields)
    return _logged(request, "rule updated", result, **extra)


def delete_rule(runtime: ApplicationState, request: Request, scene_id: int, rule_id: int) -> dict[str, Any]:
    result = require_catalog(runtime).scenes_uc.delete_rule(scene_id, rule_id)
    return _logged(request, "rule deleted", result, rule_id=rule_id)


def sync_memberships(runtime: ApplicationState, request: Request, body: SceneSyncRequest) -> dict[str, Any]:
    result = require_retrieval(runtime).scene_sync_uc.run_batch(after=body.after, limit=body.limit)
    logger.info(
        "scene sync batch after=%s processed=%s changed=%s pushed=%s missing=%s done=%s | trace_id=%s",
        body.after, result["processed"], result["membership_changed"], result["payload_pushed"],
        len(result["payload_missing"]), result["done"], get_trace_id(request),
    )
    return {"ok": True, **result}
