"""Low-level model tools exposed for diagnostics and integration."""

from __future__ import annotations

from fastapi import APIRouter, File, Query, Request, UploadFile

from app.adapter.http.schemas.tools import EmbeddingResponse, SegmentResponse
from app.adapter.http.services import tools
from app.bootstrap.state import get_application_state


router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.post("/segment", response_model=SegmentResponse)
async def segment(
    request: Request,
    file: UploadFile = File(..., description="待分割的图片文件"),
    output_vis: bool = Query(default=False, description="是否返回 mask 叠加可视化图"),
) -> SegmentResponse:
    return await tools.segment(get_application_state(request), file, output_vis=output_vis)


@router.post("/embedding", response_model=EmbeddingResponse)
async def embedding(
    request: Request,
    file: UploadFile = File(..., description="待生成全局 embedding 的图片文件"),
) -> EmbeddingResponse:
    return await tools.embedding(get_application_state(request), file)
