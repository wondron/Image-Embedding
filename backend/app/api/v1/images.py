"""One stored image: detail (with its state in the vector store), its stored files, patch, delete,
payload push.

Registered after app.api.v1.image_download, so GET /api/images/download keeps its own route.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response

from app.api.services import images as images_service
from app.bootstrap.state import get_application_state
from app.schemas.common import ErrorResponse
from app.schemas.images import (
    ImageDeleteResponse,
    ImageDetailResponse,
    ImageReembedResponse,
    ImageUpdateRequest,
    ImageUpdateResponse,
    ImageVectorSyncResponse,
)

router = APIRouter(prefix="/api/images", tags=["images"])

_NOT_FOUND = {404: {"model": ErrorResponse}}
_PUSH_NOTE = (
    "PostgreSQL 提交后，检索栈可用时立即把这张图的 payload 写进当前向量库（vector_sync=pushed，sync_required=false）；"
    "检索栈未就绪、图片没有向量或推送失败时仍返回 200，sync_required=true。"
)


@router.get(
    "/{image_id}",
    response_model=ImageDetailResponse,
    summary="Read one image",
    description=(
        "图片的全部字段（含食物标注、加热组、向量写入记录 embeddings），以及它在当前模型 collection 里的状态 vector："
        "synced / stale / missing；检索栈未就绪或读向量库失败时 status=unavailable，接口仍返回 200。"
        "图片不存在返回 IMAGE_NOT_FOUND（404）。"
    ),
    responses=_NOT_FOUND,
)
def get_image(request: Request, image_id: UUID) -> Any:
    return images_service.get_image(get_application_state(request), image_id)


@router.get(
    "/{image_id}/file",
    response_class=Response,
    summary="Read the stored thumbnail or original",
    description=(
        "按图片 id 从对象存储读回缩略图（variant=thumb，默认；没有缩略图时给原图）或原图（variant=original），"
        "供 thumb_url / original_url 在浏览器里打不开时使用（例如 mock 存储的占位域名、私有桶）。"
        "已删除但还在等待清理的图片也能读到。按字节判断类型，只返回 JPEG / PNG / WebP / GIF / BMP，可长期缓存。"
        "图片不存在 IMAGE_NOT_FOUND（404）；存储里读不到或不是图片 IMAGE_FILE_UNAVAILABLE（502）；"
        "对象存储配置不可用 NOT_READY（503）。"
    ),
    responses={
        200: {"content": {media: {} for media in ("image/webp", "image/jpeg", "image/png", "image/gif", "image/bmp")}},
        **_NOT_FOUND,
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def get_image_file(
    request: Request,
    image_id: UUID,
    variant: Literal["thumb", "original"] = Query(default="thumb", description="thumb 缩略图 / original 原图"),
) -> Response:
    return images_service.image_file(get_application_state(request), image_id, variant)


@router.patch(
    "/{image_id}",
    response_model=ImageUpdateResponse,
    summary="Update one image",
    description=(
        "只改请求里给了的字段：category_id、label_status、description、tags 和六个食物字段，null 表示清空"
        "（label_status 不能为 null）。换类别时重算这张图的场景归属；类别必须存在且启用。"
        + _PUSH_NOTE
    ),
    responses=_NOT_FOUND,
)
def update_image(request: Request, image_id: UUID, body: ImageUpdateRequest) -> Any:
    return images_service.update_image(get_application_state(request), request, image_id, body)


@router.post(
    "/{image_id}/vector-sync",
    response_model=ImageVectorSyncResponse,
    summary="Push one image's payload",
    description="按 PostgreSQL 现在的数据重写这张图在当前向量库里的 payload，用来修复 vector.status=stale。" + _PUSH_NOTE,
    responses=_NOT_FOUND,
)
def push_vector(request: Request, image_id: UUID) -> Any:
    return images_service.push_vector(get_application_state(request), request, image_id)


@router.post(
    "/{image_id}/reembed",
    response_model=ImageReembedResponse,
    summary="Embed one image again from its stored original",
    description=(
        "从对象存储读回这张图的原图，用当前模型重新算向量，按 PostgreSQL 现在的数据写 payload，写进当前 collection "
        "并记账 image_embeddings，用来修复 vector.status=missing（不用重新上传，标注不变）。"
        "原图读不到返回 IMAGE_FILE_UNAVAILABLE（502）；编码器、向量库或对象存储未就绪返回 NOT_READY（503）；"
        "图片不存在返回 IMAGE_NOT_FOUND（404）。"
    ),
    responses={**_NOT_FOUND, 502: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def reembed_image(request: Request, image_id: UUID) -> Any:
    return images_service.reembed_image(get_application_state(request), request, image_id)


@router.delete(
    "/{image_id}",
    response_model=ImageDeleteResponse,
    summary="Delete one image",
    description=(
        "从 PostgreSQL 删除图片（场景归属、向量写入记录、食物标注一起删），图库和检索结果里立即消失；"
        "检索栈可用时同一请求里删掉当前 collection 的向量点和原图、缩略图（cleanup=done），"
        "否则留在 image_deletions 由 worker 补删（cleanup=queued）。已删除、仍在队列里的图片再删返回同样结果；"
        "不存在返回 IMAGE_NOT_FOUND（404）。"
    ),
    responses=_NOT_FOUND,
)
def delete_image(request: Request, image_id: UUID) -> Any:
    return images_service.delete_image(get_application_state(request), request, image_id)
