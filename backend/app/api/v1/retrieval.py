"""Image index / search / list / health routes (SSOT §3, §6.1 wire).

Routes only declare the HTTP surface; parsing, use-case calls and error mapping
live in app.api.services.retrieval. Multipart vs JSON is resolved there by
Content-Type (avoids FastAPI File+Body conflicts).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from app.api.services import retrieval as retrieval_service
from app.bootstrap.state import get_application_state
from app.schemas.retrieval import IndexResponse, ListImagesResponse, SearchResponse

router = APIRouter(prefix="/api", tags=["retrieval"])


@router.post(
    "/index",
    response_model=IndexResponse,
    summary="Index image(s)",
    description=(
        "通过 multipart/form-data 上传单张 file 或多张 files，"
        "或使用 JSON 提交 {path,category,tags,scene,scenes}；"
        "multipart 的 tags 是 JSON 字符串数组，如 [\"红薯叶\"]。"
        "可选 description、label_status、source_type、license 和六个食物字段。"
        "indexed[].created=false 表示相同图片已在库中，沿用首次入库的标注。参见 SSOT §3.1。"
    ),
)
async def index_image(request: Request) -> Any:
    return await retrieval_service.index_from_request(get_application_state(request), request)


@router.post(
    "/index/file",
    response_model=IndexResponse,
    summary="单张图像入库",
    description="通过 multipart/form-data 上传单张 file，生成 embedding 并保存图像、元数据和向量。",
)
async def index_image_file(
    request: Request,
    file: UploadFile = File(..., description="待入库的单张图像文件"),
) -> Any:
    return await retrieval_service.index_from_upload(get_application_state(request), request, file)


@router.post(
    "/search",
    response_model=SearchResponse,
    response_model_exclude_unset=True,  # §6.1: conditional fields stay absent
    summary="Search by image",
    description=(
        "通过 multipart/form-data 上传 file，或使用 JSON 提交 {img_url,scene}。"
        "优先检索自建图库；PG v0.1 中，自建图库无匹配结果时返回 source=empty 和空结果列表，"
        "不调用 FlyB。参见 SSOT §3.2/§4。"
    ),
)
async def search_image(request: Request) -> Any:
    return await retrieval_service.search_from_request(get_application_state(request), request)


@router.get(
    "/health",
    summary="MVP dependency health",
    description=(
        "汇总 embedding 模型、Qdrant 和 PostgreSQL 元数据存储的健康状态；flyb 字段已禁用。"
        "此接口不替代 /api/healthz 和 /api/readyz。"
    ),
)
def mvp_health(request: Request) -> JSONResponse:
    return retrieval_service.mvp_health(get_application_state(request))


@router.get(
    "/images",
    response_model=ListImagesResponse,
    response_model_exclude_unset=True,
    summary="List library images",
    description=(
        "按场景分页查询图库图片，默认场景为 general。"
        "可通过 q 按分类名称、标签、图片 URL 或 ID 进行子串匹配，"
        "通过 category 按分类代码、名称或 ID 筛选，通过 category_id 按类别及其所有下级类别筛选，"
        "通过 tag 按单个标签精确筛选，通过 label_status 按标注状态筛选。参见 SSOT §3.5。"
        "只读 PostgreSQL，编码器或向量库未就绪时照常返回；每张图带 vector_status（当前 collection 里的状态，"
        "一页只读一次向量库，读不到时为 unavailable）。"
    ),
)
def list_library_images(
    request: Request,
    scene: Optional[str] = Query(None, description="场景代码，默认为 general；未知场景返回 LIST_UNKNOWN_SCENE"),
    page: int = Query(1, description="页码，从 1 开始"),
    page_size: int = Query(24, description="每页数量，默认 24，范围 1～100"),
    q: Optional[str] = Query(None, description="可选搜索关键词，按分类名称、标签、图片 URL 或 ID 进行子串匹配"),
    category: Optional[str] = Query(None, description="可选分类筛选，支持分类代码、名称或 ID（只匹配这一个类别）"),
    tag: Optional[str] = Query(None, description="可选标签筛选：tags 里有这个标签（精确匹配，区分大小写）"),
    category_id: Optional[int] = Query(
        None, gt=0, le=2_147_483_647, description="可选类别筛选（categories.id）：这个类别及其所有下级类别；未标注的图片不匹配"
    ),
    label_status: Optional[str] = Query(None, description="可选标注状态筛选：unlabeled / auto / verified"),
    out_of_sync: Optional[bool] = Query(
        None,
        description=(
            "true：只列需要处理的图片——当前模型的写入记录（image_embeddings）缺失或落后于 payload_version，"
            "即向量库里的 payload 待同步或没有向量。按记账判断，不读向量库"
        ),
    ),
    trace_id: Optional[str] = Query(None),
) -> Any:
    return retrieval_service.run_list_images(
        get_application_state(request),
        request,
        scene=scene,
        page=page,
        page_size=page_size,
        q=q,
        category=category,
        tag=tag,
        category_id=category_id,
        label_status=label_status,
        out_of_sync=out_of_sync,
        trace_id=trace_id,
    )
