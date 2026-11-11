"""Image scene membership routes: manual include / exclude, saved in PG, then pushed to Qdrant."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Path, Request

from app.api.services import scene_memberships as membership_service
from app.bootstrap.state import get_application_state
from app.schemas.common import ErrorResponse
from app.schemas.scene_memberships import (
    ManualSceneMembershipRequest,
    SceneMembershipSnapshot,
    SceneMembershipWriteResponse,
)

router = APIRouter(prefix="/api/images", tags=["scene memberships"])
_SceneId = Path(..., gt=0, le=2_147_483_647, description="scenes.id")
_WRITE_NOTE = (
    "保存到 PostgreSQL 并重算该图后，检索栈可用时立即把这张图的 payload 写进当前向量库"
    "（vector_sync=pushed，sync_required=false）；检索栈未就绪、图片没有向量或推送失败时仍返回 200，"
    "sync_required=true，之后用 POST /api/scenes/sync 补上。"
)


@router.get(
    "/{image_id}/scene-memberships",
    response_model=SceneMembershipSnapshot,
    summary="Read stored scene membership",
    description="全目录快照（含停用、全包含、无归属项），不重算规则，不确认向量同步。图片不存在返回 VALIDATION_ERROR 422。",
)
def get_memberships(request: Request, image_id: UUID) -> Any:
    return membership_service.get_memberships(get_application_state(request), image_id)


@router.put(
    "/{image_id}/scene-memberships/{scene_id}",
    response_model=SceneMembershipWriteResponse,
    summary="Set manual scene membership",
    description="人工纳入/排除，优先于规则；停用或全包含场景禁止写入。" + _WRITE_NOTE,
    responses={404: {"model": ErrorResponse}},
)
def set_membership(
    request: Request, image_id: UUID, body: ManualSceneMembershipRequest, scene_id: int = _SceneId
) -> Any:
    return membership_service.set_membership(get_application_state(request), request, image_id, scene_id, body)


@router.delete(
    "/{image_id}/scene-memberships/{scene_id}",
    response_model=SceneMembershipWriteResponse,
    summary="Clear manual scene override",
    description="仅清人工覆盖并恢复当前规则；允许清停用普通场景，不允许全包含场景；无覆盖仍幂等成功。" + _WRITE_NOTE,
    responses={404: {"model": ErrorResponse}},
)
def clear_membership(request: Request, image_id: UUID, scene_id: int = _SceneId) -> Any:
    return membership_service.clear_membership(get_application_state(request), request, image_id, scene_id)
