"""场景配置和场景归属同步的 HTTP 适配层。

配置通过 catalog 访问（只用 PostgreSQL）；同步通过检索服务，因为它要写向量 payload
（两者有一个没构建时 NOT_READY 503）。
失败都是规则和存储抛出的 AppError：VALIDATION_ERROR 422、SCENE_NOT_FOUND 404、
SCENE_CODE_EXISTS / SCENE_RULE_EXISTS / SCENE_IN_USE 409、SCENE_PROTECTED 403、
SCENE_POSTGRES_FAILED 502；同步还可能是 INDEX_POSTGRES_FAILED / INDEX_QDRANT_FAILED 502。
路由是同步函数，所以 FastAPI 在线程池里执行它们。
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
from app.services.retrieval.import_scenes import ImportScenesUseCase

logger = logging.getLogger("svc.scenes")


def _logged(request: Request, action: str, result: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """记录一次配置写入；result 是用例返回的 {scene, sync_required}。"""
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
    fields = body.model_dump(exclude_unset=True)  # 没传的保持不变；显式传 null 会保留
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
    fields = body.model_dump(exclude_unset=True)  # 没传的保持不变；label_id 为 null 时清空
    result = require_catalog(runtime).scenes_uc.update_rule(scene_id, rule_id, fields)
    extra: dict[str, Any] = {"rule_id": rule_id, "fields": ",".join(sorted(fields))}
    if "category_id" in fields or "cuisine" in fields:
        extra["target"] = _target(fields)
    return _logged(request, "rule updated", result, **extra)


def delete_rule(runtime: ApplicationState, request: Request, scene_id: int, rule_id: int) -> dict[str, Any]:
    result = require_catalog(runtime).scenes_uc.delete_rule(scene_id, rule_id)
    return _logged(request, "rule deleted", result, rule_id=rule_id)


def import_scenes(
    runtime: ApplicationState, request: Request, document: dict[str, Any], *, dry_run: bool, prune: bool
) -> dict[str, Any]:
    """应用（dry_run：预览）一个场景配置文件；响应体就是导入报告。
    只需要 catalog：这里不向向量库推送任何东西（sync_required 说明什么时候需要
    接着调用 POST /api/scenes/sync）。
    """
    catalog = require_catalog(runtime)
    result = ImportScenesUseCase(catalog.scenes_uc, catalog.categories_uc).execute(
        document, dry_run=dry_run, prune=prune
    )
    summary = result["summary"]
    logger.info(
        "scene import dry_run=%s prune=%s total=%s create=%s update=%s delete=%s unchanged=%s error=%s "
        "not_in_file=%s sync_required=%s | trace_id=%s",
        dry_run, prune, summary["total"], summary["create"], summary["update"], summary["delete"],
        summary["unchanged"], summary["error"], summary["not_in_file"], result["sync_required"],
        get_trace_id(request),
    )
    return {"ok": True, **result}


def sync_memberships(runtime: ApplicationState, request: Request, body: SceneSyncRequest) -> dict[str, Any]:
    result = require_retrieval(runtime).scene_sync_uc.run_batch(after=body.after, limit=body.limit)
    logger.info(
        "scene sync batch after=%s processed=%s changed=%s pushed=%s missing=%s done=%s | trace_id=%s",
        body.after, result["processed"], result["membership_changed"], result["payload_pushed"],
        len(result["payload_missing"]), result["done"], get_trace_id(request),
    )
    return {"ok": True, **result}
