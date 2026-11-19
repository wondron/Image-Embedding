"""场景配置的路由（场景及其标签和规则）、配置文件导入，以及场景归属同步。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Path, Query, Request

from app.api.services import scenes as scenes_service
from app.bootstrap.state import get_application_state
from app.schemas.common import ErrorResponse
from app.schemas.config_import import DRY_RUN_DOC, PRUNE_DOC, SceneImportResponse
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
_SYNC_NOTE = (
    "响应的 sync_required 为 true 时（规则、启用、包含全部图片的改动），要等「重算场景归属」"
    "（POST /api/scenes/sync）后才作用到已入库的图片；为 false 时已经生效。"
)


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
    "/import",
    response_model=SceneImportResponse,
    summary="Import a scene configuration file",
    description=(
        "上传场景配置文件做批量修改或恢复备份。请求体就是文件内容（JSON 对象）：「场景管理」页下载的场景配置，"
        "或 GET /api/scenes 的响应体（scenes 列表，每个场景带 labels 和 rules），可以手工编辑，也可以只保留要改的部分。"
        "场景按编码匹配，场景类别按场景内的编码匹配，规则按目标匹配（类别用类别编码，或菜系）；没有的新建，"
        "已有的把文件里给了且不同的字段更新（规则只能改映射的场景类别，label 为 null 清空映射）。"
        "文件里没给的字段、没带的 labels / rules 列表都不动。规则里的类别必须已在类别树里（先导入类别树）。"
        "默认不删除任何东西；prune=true 时，对文件列出的场景，删除文件里没有列出的规则和场景类别。"
        "场景本身从不删除，文件里没列出的场景在 not_in_file 里列出。文件里的 id 不读取，所以可以导入另一套库。"
        "dry_run 默认 true，只返回将要发生的变更；dry_run=false 才写入。逐项写入（各自一个事务），"
        "某一项失败不影响其他项。sync_required 为 true 时要「重算场景归属」后才作用到已入库图片。详见契约 §6.8。"
    ),
)
def import_scenes(
    request: Request,
    document: dict[str, Any] = Body(..., description="场景配置文件的内容"),
    dry_run: bool = Query(True, description=DRY_RUN_DOC),
    prune: bool = Query(False, description=PRUNE_DOC),
) -> Any:
    return scenes_service.import_scenes(
        get_application_state(request), request, document, dry_run=dry_run, prune=prune
    )


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
    description=(
        "删除场景及其场景类别、规则和图片归属记录；默认场景不能删除（SCENE_PROTECTED，403）。"
        "场景原来有成员时 sync_required 为 true：向量 payload 里的旧编码要重算后才清掉。"
    ),
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
    summary="Change a rule's target and / or label",
    description=(
        "只改请求里给了的字段，在一个事务里完成，规则 id 不变。给 category_id 或 cuisine（恰好一个非空）就把目标"
        "换成它，另一个清空；目标和本场景其他规则重复返回 SCENE_RULE_EXISTS（409）。label_id 为 null 时清空映射；"
        "按菜系的规则不能映射场景类别（换成菜系时一并传 label_id: null）。只改映射时 sync_required 为 false。"
        + _SYNC_NOTE
    ),
    responses={**_NOT_FOUND, **_CONFLICT},
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
