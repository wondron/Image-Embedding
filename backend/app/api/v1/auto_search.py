"""Business search routes: the self-built library first, the global search as an optional
fallback (app.api.services.auto_search). One route per input, like the global search routes:
an uploaded file (multipart) or an image URL (JSON)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, Request, UploadFile

from app.api.deps import require_retrieval
from app.api.services.auto_search import run_auto_search
from app.api.uploads import read_upload
from app.bootstrap.state import get_application_state
from app.clients.image_fetch import read_url_bytes
from app.core.middleware import bind_trace_id
from app.schemas.auto_search import (
    GLOBAL_FALLBACK_DOC,
    MACHINE_INFO_DOC,
    MACHINE_INFO_MAX_LENGTH,
    SCENE_DOC,
    SCORE_THRESHOLD_DOC,
    TOP_K_DEFAULT,
    TOP_K_DOC,
    TOP_K_MAX,
    TRACE_ID_DOC,
    AutoSearchResponse,
    AutoSearchUrlRequest,
)

router = APIRouter(prefix="/api/search/auto", tags=["business-search"])

_DESCRIPTION = (
    "给业务方用的搜图接口：先查自建库（按检测场景过滤，取相似度达到阈值的前 top_k 条）；"
    "自建库没有结果且 global_fallback=true 时，再用现网 Global 搜图兜底（取 top_k 条），返回最终结果。"
    "两个通道的结果是同一种结果卡，source 说明来自哪里（self / global / empty），"
    "fallback.status 说明兜底的情况。Global 兜底失败不会让请求失败：返回空结果，fallback.status=failed 并给出原因。"
    "自建库出错（未知场景 SEARCH_UNKNOWN_SCENE、图片无法解码 SEARCH_BAD_IMAGE、检索服务未就绪 NOT_READY 等）"
    "按统一错误格式返回，不走兜底。详见契约 §6.6。"
)


@router.post(
    "/file",
    response_model=AutoSearchResponse,
    response_model_exclude_unset=True,  # fallback.error / detail and message only when set
    summary="业务搜图（上传文件）：自建库优先，可选 Global 兜底",
    description=_DESCRIPTION,
)
async def auto_search_by_file(
    request: Request,
    file: UploadFile = File(..., description="查询图片（JPG / PNG / WebP / BMP，最大 15 MB）"),
    scene: str | None = Form(default=None, description=SCENE_DOC),
    top_k: int = Form(default=TOP_K_DEFAULT, ge=1, le=TOP_K_MAX, description=TOP_K_DOC),
    score_threshold: float | None = Form(default=None, description=SCORE_THRESHOLD_DOC),
    global_fallback: bool = Form(default=False, description=GLOBAL_FALLBACK_DOC),
    trace_id: str | None = Form(default=None, description=TRACE_ID_DOC),
    machine_info: str | None = Form(default=None, max_length=MACHINE_INFO_MAX_LENGTH, description=MACHINE_INFO_DOC),
) -> Any:
    """multipart/form-data：file 加上其余表单字段。"""
    runtime = get_application_state(request)
    trace_id = bind_trace_id(request, trace_id)
    require_retrieval(runtime)  # NOT_READY before reading the upload
    image_bytes = await read_upload(file, runtime.max_image_bytes)
    return await run_auto_search(
        runtime,
        image_bytes=image_bytes,
        scene=scene,
        top_k=top_k,
        score_threshold=score_threshold,
        global_fallback=global_fallback,
        machine_info=machine_info,
        trace_id=trace_id,
        input_label=f"file:{file.filename or 'upload'}",
    )


@router.post(
    "/url",
    response_model=AutoSearchResponse,
    response_model_exclude_unset=True,
    summary="业务搜图（图片链接）：自建库优先，可选 Global 兜底",
    description=_DESCRIPTION + " 请求体为 JSON，未知字段返回 422。",
)
async def auto_search_by_url(request: Request, body: AutoSearchUrlRequest) -> Any:
    """application/json：{image_url, scene, top_k, score_threshold, global_fallback, trace_id, machine_info}。"""
    runtime = get_application_state(request)
    trace_id = bind_trace_id(request, body.trace_id)
    require_retrieval(runtime)  # NOT_READY before downloading anything
    image_bytes = await read_url_bytes(
        body.image_url,
        max_image_bytes=runtime.max_image_bytes,
        timeout_s=runtime.http_timeout,
    )
    return await run_auto_search(
        runtime,
        image_bytes=image_bytes,
        scene=body.scene,
        top_k=body.top_k,
        score_threshold=body.score_threshold,
        global_fallback=body.global_fallback,
        machine_info=body.machine_info,
        trace_id=trace_id,
        input_label=f"url:{body.image_url}",
    )
