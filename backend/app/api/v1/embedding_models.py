"""Model version routes: see the versions, register the configured encoder, fill its collection,
switch (or roll back), retire."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Path, Request, status

from app.api.services import embedding_models as models_service
from app.bootstrap.state import get_application_state
from app.schemas.common import ErrorResponse
from app.schemas.embedding_models import (
    EmbeddingBuildResponse,
    EmbeddingModelActivateRequest,
    EmbeddingModelActivateResponse,
    EmbeddingModelRegisterRequest,
    EmbeddingModelResponse,
    EmbeddingModelRetireResponse,
    EmbeddingModelsResponse,
)

router = APIRouter(prefix="/api/embedding-models", tags=["embedding-models"])

_ID = Path(..., ge=1, le=2**31 - 1, description="embedding_models.id")
_CONFLICT = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}}


@router.get(
    "",
    response_model=EmbeddingModelsResponse,
    summary="List the embedding model versions",
    description=(
        "全部模型版本（旧的在前）：状态、collection、有 / 缺向量的图片数；EMBEDDING_CONFIG 配置的编码器是哪个版本"
        "（configured.model_id 为 null 表示配置的是还没登记的新模型）；检索正在用哪个版本；本进程最近一次生成向量的进度。"
    ),
)
def list_models(request: Request) -> Any:
    return models_service.list_models(get_application_state(request))


@router.post(
    "",
    response_model=EmbeddingModelResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register the configured encoder as a new version",
    description=(
        "把 EMBEDDING_CONFIG 现在配置的编码器登记为新版本（building），并新建它自己的空 collection；"
        "检索和入库仍用当前版本。它已经是某个未下线的版本时返回 EMBEDDING_MODEL_EXISTS（409）。"
    ),
    responses={409: {"model": ErrorResponse}},
)
def register_model(request: Request, body: EmbeddingModelRegisterRequest = Body(default_factory=EmbeddingModelRegisterRequest)) -> Any:
    return models_service.register_model(get_application_state(request), request, name=body.name, version=body.version)


@router.post(
    "/{model_id}/build",
    response_model=EmbeddingBuildResponse,
    summary="Fill a version's collection in the background",
    description=(
        "后台用这个版本的编码器，从对象存储读回每张还没有向量的图片的原图，生成向量写进它的 collection，"
        "结束后补推期间改过的过滤信息；没有图片落下时 building 变为 ready。进度看 GET 的 build。"
        "停止后再开始会接着做。同一时间只做一个（EMBEDDING_BUILD_RUNNING 409）；"
        "编码器没加载时 EMBEDDING_ENCODER_UNAVAILABLE（409）：把 EMBEDDING_CONFIG 指向它的权重。"
    ),
    responses=_CONFLICT,
)
def start_build(request: Request, model_id: int = _ID) -> Any:
    return models_service.start_build(get_application_state(request), request, model_id)


@router.post(
    "/{model_id}/build/stop",
    response_model=EmbeddingBuildResponse,
    summary="Stop a version's build",
    description="在当前这一批（最多 16 张）之后停下；已写入的向量保留。没有在做时原样返回。",
)
def stop_build(request: Request, model_id: int = _ID) -> Any:
    return models_service.stop_build(get_application_state(request), request, model_id)


@router.post(
    "/{model_id}/activate",
    response_model=EmbeddingModelActivateResponse,
    summary="Switch search and index to a version",
    description=(
        "先建好这个版本的检索（它的编码器 + collection），再在 PostgreSQL 里设为 current（原来的当前版本变为 ready，"
        "可以用同一个接口切回去回滚），然后检索和入库立即改用它。还有图片没有向量时返回 EMBEDDING_MODEL_INCOMPLETE（409），"
        "除非 allow_missing=true；正在生成向量时 EMBEDDING_BUILD_RUNNING（409）；"
        "它的编码器加载不了时 EMBEDDING_ENCODER_UNAVAILABLE（409）。多进程部署时其他进程要重启。"
    ),
    responses=_CONFLICT,
)
def activate_model(
    request: Request,
    model_id: int = _ID,
    body: EmbeddingModelActivateRequest = Body(default_factory=EmbeddingModelActivateRequest),
) -> Any:
    return models_service.activate_model(get_application_state(request), request, model_id, allow_missing=body.allow_missing)


@router.post(
    "/{model_id}/retire",
    response_model=EmbeddingModelRetireResponse,
    summary="Retire a version",
    description=(
        "删除这个版本的 collection 和写入记录，状态改为 retired，之后不能再切换到它（要用就重新登记）。"
        "当前版本不能下线（EMBEDDING_MODEL_STATE 409）。已经下线的原样返回。"
    ),
    responses=_CONFLICT,
)
def retire_model(request: Request, model_id: int = _ID) -> Any:
    return models_service.retire_model(get_application_state(request), request, model_id)
