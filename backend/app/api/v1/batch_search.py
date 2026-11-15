"""Batch search routes: several query images against the self-built library in one request
(app.api.services.batch_search). One route per input, like the other search routes: uploaded
files (multipart) or image URLs (JSON)."""

from __future__ import annotations

from functools import partial
from typing import Any

from fastapi import APIRouter, File, Form, Request, UploadFile

from app.api.deps import require_retrieval
from app.api.services.batch_search import QueryImage, run_batch_search
from app.api.uploads import read_upload
from app.bootstrap.state import get_application_state
from app.clients.image_fetch import read_url_bytes
from app.core.middleware import bind_trace_id
from app.schemas.batch_search import (
    BATCH_MAX_IMAGES,
    SCENE_DOC,
    SCORE_THRESHOLD_DOC,
    TOP_K_DEFAULT,
    TOP_K_DOC,
    TOP_K_MAX,
    TRACE_ID_DOC,
    BatchSearchResponse,
    BatchSearchUrlsRequest,
)

router = APIRouter(prefix="/api/search/batch", tags=["retrieval"])

_DESCRIPTION = (
    f"多图搜索：一次请求带多张查询图（最多 {BATCH_MAX_IMAGES} 张），逐张在自建库里检索，整批共用一个检测场景、"
    "top_k 和相似度阈值；不调用现网 Global。results 每张查询图一项，顺序同请求：ok=true 时是它的结果卡"
    "（同 POST /api/search 的 items，没有达到阈值的结果时 source=empty、items 为空），ok=false 时是这张图"
    "没能检索的原因（读不出、下载失败、无法解码等），不影响其他图片，请求仍返回 200。"
    "整个请求失败的情况：场景不存在（SEARCH_UNKNOWN_SCENE）、阈值非法（SEARCH_BAD_SCORE_THRESHOLD）、"
    "图片数量超限（VALIDATION_ERROR）、检索服务未就绪（NOT_READY），以及所有图片都因为同一个服务故障"
    "（向量库或数据库不可用）而失败。详见契约 §6.7。"
)


@router.post(
    "/files",
    response_model=BatchSearchResponse,
    response_model_exclude_unset=True,  # per image: only the fields of its case; hits as on /api/search
    summary="多图搜索（上传文件）：自建库",
    description=_DESCRIPTION,
)
async def batch_search_by_files(
    request: Request,
    files: list[UploadFile] = File(
        ...,
        description=f"查询图片，同一个字段名 files 重复 1～{BATCH_MAX_IMAGES} 次（JPG / PNG / WebP / BMP，每张最大 15 MB）",
    ),
    scene: str | None = Form(default=None, description=SCENE_DOC),
    top_k: int = Form(default=TOP_K_DEFAULT, ge=1, le=TOP_K_MAX, description=TOP_K_DOC),
    score_threshold: float | None = Form(default=None, description=SCORE_THRESHOLD_DOC),
    trace_id: str | None = Form(default=None, description=TRACE_ID_DOC),
) -> Any:
    """multipart/form-data：files（可重复）加上其余表单字段。"""
    runtime = get_application_state(request)
    trace_id = bind_trace_id(request, trace_id)
    require_retrieval(runtime)  # NOT_READY before reading any upload
    max_bytes = runtime.max_image_bytes
    images = [
        QueryImage(label=upload.filename or f"file-{index + 1}", read=partial(read_upload, upload, max_bytes))
        for index, upload in enumerate(files)
    ]
    return await run_batch_search(
        runtime,
        images=images,
        scene=scene,
        top_k=top_k,
        score_threshold=score_threshold,
        trace_id=trace_id,
        input_type="上传文件",
    )


@router.post(
    "/urls",
    response_model=BatchSearchResponse,
    response_model_exclude_unset=True,
    summary="多图搜索（图片链接）：自建库",
    description=_DESCRIPTION + " 请求体为 JSON，未知字段返回 422。",
)
async def batch_search_by_urls(request: Request, body: BatchSearchUrlsRequest) -> Any:
    """application/json：{image_urls, scene, top_k, score_threshold, trace_id}。"""
    runtime = get_application_state(request)
    trace_id = bind_trace_id(request, body.trace_id)
    require_retrieval(runtime)  # NOT_READY before downloading anything
    images = [
        QueryImage(
            label=url,
            read=partial(
                read_url_bytes, url, max_image_bytes=runtime.max_image_bytes, timeout_s=runtime.http_timeout
            ),
        )
        for url in body.image_urls
    ]
    return await run_batch_search(
        runtime,
        images=images,
        scene=body.scene,
        top_k=body.top_k,
        score_threshold=body.score_threshold,
        trace_id=trace_id,
        input_type="图片链接",
    )
