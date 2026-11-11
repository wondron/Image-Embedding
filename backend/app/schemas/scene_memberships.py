"""Image membership wire; implicit is derived, never a database enum.

A write is saved in PostgreSQL, then the image's vector payload is pushed when the retrieval
stack is up (app.api.services.scene_memberships): vector_sync says how that went and
sync_required whether search still waits for POST /api/scenes/sync.
"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ManualSceneMembershipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_member: bool = Field(..., strict=True)


class SceneMembershipItem(BaseModel):
    scene_id: int
    scene_code: str
    is_active: bool
    include_all: bool
    is_member: bool
    origin: Literal["implicit", "rule", "manual"] | None = Field(...)
    manual_override: bool | None = Field(...)


class SceneMembershipSnapshot(BaseModel):
    ok: Literal[True] = True
    image_id: UUID
    payload_version: int
    scenes: list[str]
    memberships: list[SceneMembershipItem]


class SceneMembershipWriteResponse(SceneMembershipSnapshot):
    vector_sync: Literal["pushed", "missing", "not_ready", "failed"] = Field(
        ...,
        description=(
            "pushed：当前向量库已是新 payload；missing：这张图在当前向量库里没有向量（需要重新入库）；"
            "not_ready：检索栈未就绪，没有推送；failed：推送或记账失败，或图片在推送期间一直被改（见日志）"
        ),
    )
    sync_required: bool = Field(
        ...,
        description="false：检索已能看到这次修改；true：要等 POST /api/scenes/sync（或修好后重试保存）",
    )
