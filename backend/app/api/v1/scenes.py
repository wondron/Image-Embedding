"""Scene configuration routes (scenes, their labels and rules) and the membership sync."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Request

from app.api.services import scenes as scenes_service
from app.bootstrap.state import get_application_state
from app.schemas.common import ErrorResponse
from app.schemas.scenes import (
    SceneCreateRequest,
    SceneDeleteResponse,
    SceneLabelCreateRequest,
    SceneLabelUpdateRequest,
    SceneListResponse,
    SceneResponse,
    SceneRuleCreateRequest,
    SceneRuleUpdateRequest,
    SceneSyncRequest,
    SceneSyncResponse,
    SceneUpdateRequest,
)

router = APIRouter(prefix="/api/scenes", tags=["scenes"])

_NOT_FOUND = {404: {"model": ErrorResponse}}
_CONFLICT = {409: {"model": ErrorResponse}}
_PROTECTED = {403: {"model": ErrorResponse}}
_SceneId = Path(..., gt=0, description="scenes.id")
_LabelId = Path(..., gt=0, description="scene_labels.id")
_RuleId = Path(..., gt=0, description="scene_rules.id")
_SYNC_NOTE = "规则改动要等「重算场景归属」（POST /api/scenes/sync）后才作用到已入库的图片。"


@router.get(
    "",
    response_model=SceneListResponse,
    summary="List scenes",
    description="返回全部场景（含停用的）及各自的场景类别和分配规则，以及请求没给场景时用的默认场景。",
)
def list_scenes(request: Request) -> Any:
    return scenes_service.list_scenes(get_application_state(request))


@router.post(
    "/sync",
    response_model=SceneSyncResponse,
    summary="Recompute scene memberships (one batch)",
    description=(
        "按图片 id 顺序处理一批已入库图片：按当前的场景、规则和类别树重算场景归属，并把这批图片的 payload "
        "写进当前向量库。返回 next_after，从它继续，直到 done 为 true。可以随时中断后再继续；需要检索栈可用。"
    ),
)
def sync_memberships(request: Request, body: SceneSyncRequest) -> Any:
    return scenes_service.sync_memberships(get_application_state(request), request, body)


@router.post(
    "",
    response_model=SceneResponse,
    status_code=201,
    summary="Create a scene",
    description="新建场景；编码已被占用返回 SCENE_CODE_EXISTS（409）。" + _SYNC_NOTE,
    responses=_CONFLICT,
)
def create_scene(request: Request, body: SceneCreateRequest) -> Any:
    return scenes_service.create_scene(get_application_state(request), request, body)


@router.patch(
    "/{scene_id}",
    response_model=SceneResponse,
    summary="Update a scene",
    description=(
        "只改请求里给了的字段；编码不能改。默认场景不能停用、也不能取消「包含全部图片」（SCENE_PROTECTED，403）。"
        + _SYNC_NOTE
    ),
    responses={**_NOT_FOUND, **_PROTECTED},
)
def update_scene(request: Request, body: SceneUpdateRequest, scene_id: int = _SceneId) -> Any:
    return scenes_service.update_scene(get_application_state(request), request, scene_id, body)


@router.delete(
    "/{scene_id}",
    response_model=SceneDeleteResponse,
    summary="Delete a scene",
    description="删除场景及其场景类别、规则和图片归属记录；默认场景不能删除（SCENE_PROTECTED，403）。",
    responses={**_NOT_FOUND, **_PROTECTED},
)
def delete_scene(request: Request, scene_id: int = _SceneId) -> Any:
    return scenes_service.delete_scene(get_application_state(request), request, scene_id)


@router.post(
    "/{scene_id}/labels",
    response_model=SceneResponse,
    status_code=201,
    summary="Add a scene label",
    description="编码在场景内唯一，重复返回 SCENE_CODE_EXISTS（409）。",
    responses={**_NOT_FOUND, **_CONFLICT},
)
def create_label(request: Request, body: SceneLabelCreateRequest, scene_id: int = _SceneId) -> Any:
    return scenes_service.create_label(get_application_state(request), request, scene_id, body)


@router.patch(
    "/{scene_id}/labels/{label_id}",
    response_model=SceneResponse,
    summary="Update a scene label",
    responses={**_NOT_FOUND, **_CONFLICT},
)
def update_label(
    request: Request, body: SceneLabelUpdateRequest, scene_id: int = _SceneId, label_id: int = _LabelId
) -> Any:
    return scenes_service.update_label(get_application_state(request), request, scene_id, label_id, body)


@router.delete(
    "/{scene_id}/labels/{label_id}",
    response_model=SceneResponse,
    summary="Delete a scene label",
    description="还有规则映射到它时返回 SCENE_IN_USE（409）。",
    responses={**_NOT_FOUND, **_CONFLICT},
)
def delete_label(request: Request, scene_id: int = _SceneId, label_id: int = _LabelId) -> Any:
    return scenes_service.delete_label(get_application_state(request), request, scene_id, label_id)


@router.post(
    "/{scene_id}/rules",
    response_model=SceneResponse,
    status_code=201,
    summary="Add a scene rule",
    description=(
        "category_id 和 cuisine 二选一；label_id 只能配合 category_id。同一场景同一类别或菜系只能有一条，"
        "重复返回 SCENE_RULE_EXISTS（409）。" + _SYNC_NOTE
    ),
    responses={**_NOT_FOUND, **_CONFLICT},
)
def create_rule(request: Request, body: SceneRuleCreateRequest, scene_id: int = _SceneId) -> Any:
    return scenes_service.create_rule(get_application_state(request), request, scene_id, body)


@router.patch(
    "/{scene_id}/rules/{rule_id}",
    response_model=SceneResponse,
    summary="Change a rule's label",
    description="规则的目标（类别或菜系）不能改，只能改映射到的场景类别；label_id 为 null 时清空。",
    responses=_NOT_FOUND,
)
def update_rule(
    request: Request, body: SceneRuleUpdateRequest, scene_id: int = _SceneId, rule_id: int = _RuleId
) -> Any:
    return scenes_service.update_rule(get_application_state(request), request, scene_id, rule_id, body)


@router.delete(
    "/{scene_id}/rules/{rule_id}",
    response_model=SceneResponse,
    summary="Delete a scene rule",
    description=_SYNC_NOTE,
    responses=_NOT_FOUND,
)
def delete_rule(request: Request, scene_id: int = _SceneId, rule_id: int = _RuleId) -> Any:
    return scenes_service.delete_rule(get_application_state(request), request, scene_id, rule_id)
