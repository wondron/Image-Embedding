"""为诊断和集成开放的底层模型工具。"""

from __future__ import annotations

from fastapi import APIRouter, File, Query, Request, UploadFile

from app.schemas.tools import EmbeddingResponse, GdinoResponse, SegmentResponse
from app.api.services import tools
from app.bootstrap.state import get_application_state


router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.post("/segment", response_model=SegmentResponse)
async def segment(
    request: Request,
    file: UploadFile = File(..., description="待分割的图片文件"),
    output_vis: bool = Query(default=False, description="是否返回 mask 叠加可视化图"),
) -> SegmentResponse:
    return await tools.segment(get_application_state(request), file, output_vis=output_vis)


@router.post("/gdino", response_model=GdinoResponse)
async def gdino(
    request: Request,
    file: UploadFile = File(..., description="待检测的图片文件"),
    text_range: list[str] | None = Query(
        default=None,
        description="检测什么：一个 caption（如 `food . dish`），或重复这个参数传多个类别"
        "（`text_range=food&text_range=dish`）。不传时用 GROUNDINGDINO_CONFIG.caption",
    ),
    box_threshold: float | None = Query(
        default=None, ge=0, le=1,
        description="检测框置信度阈值，0～1，低于它的框不要。不传时用 GROUNDINGDINO_CONFIG.box_threshold",
    ),
    text_threshold: float | None = Query(
        default=None, ge=0, le=1,
        description="caption 里的词算作和框匹配的阈值，0～1，决定 label。不传时用 GROUNDINGDINO_CONFIG.text_threshold",
    ),
) -> GdinoResponse:
    """用 GroundingDINO 在上传的图片里检测 caption 说的目标，返回每个框的词、置信度和原图上的像素坐标。

    检测和业务搜图的区域搜索用同一个模型实例、同样的解码和坐标换算，可以用来调 caption 和阈值；
    和区域搜索轮流使用模型。模型没有加载（GROUNDINGDINO_CONFIG 缺失、关闭或加载失败）时返回 503。
    """
    return await tools.gdino(
        get_application_state(request),
        file,
        text_range=text_range,
        box_threshold=box_threshold,
        text_threshold=text_threshold,
    )


@router.post("/embedding", response_model=EmbeddingResponse)
async def embedding(
    request: Request,
    file: UploadFile = File(..., description="待生成全局 embedding 的图片文件"),
) -> EmbeddingResponse:
    return await tools.embedding(get_application_state(request), file)
