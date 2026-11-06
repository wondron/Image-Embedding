"""Image index / search / list / health routes (SSOT §3, §6.1 wire).

Routes only declare the HTTP surface; parsing, use-case calls and error mapping
live in app.api.services.retrieval. Multipart vs JSON is resolved there by
Content-Type (avoids FastAPI File+Body conflicts).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.api.services import retrieval as retrieval_service
from app.bootstrap.state import get_application_state
from app.schemas.retrieval import IndexResponse, ListImagesResponse, SearchResponse

router = APIRouter(prefix="/api", tags=["retrieval"])


@router.post(
    "/index",
    response_model=IndexResponse,
    summary="Index image(s)",
    description="multipart file/files or JSON {path,category,tags,scene,scenes}. SSOT §3.1.",
)
async def index_image(request: Request) -> Any:
    return await retrieval_service.index_from_request(get_application_state(request), request)


@router.post(
    "/search",
    response_model=SearchResponse,
    response_model_exclude_unset=True,  # §6.1: conditional fields stay absent
    summary="Search by image",
    description=(
        "multipart file OR JSON {img_url,scene}. Self-built-first; "
        "PG v0.1: empty self-built -> source=empty (FlyB OUT) and final list length==0. SSOT §3.2/§4."
    ),
)
async def search_image(request: Request) -> Any:
    return await retrieval_service.search_from_request(get_application_state(request), request)


@router.get(
    "/health",
    summary="MVP dependency health",
    description="Aggregate embedding/qdrant/metadata(PG); flyb key disabled. Does not replace healthz/readyz.",
)
def mvp_health(request: Request) -> JSONResponse:
    return retrieval_service.mvp_health(get_application_state(request))


@router.get(
    "/images",
    response_model=ListImagesResponse,
    response_model_exclude_unset=True,
    summary="List library images",
    description=(
        "Paginated images filtered by scene (default general). "
        "Optional q (category name / url / id substring) and category (code / name / id). SSOT §3.5."
    ),
)
def list_library_images(
    request: Request,
    scene: Optional[str] = Query(None, description="default general; unknown -> LIST_UNKNOWN_SCENE"),
    page: int = Query(1, description="1-based page"),
    page_size: int = Query(24, description="default 24, range 1..100"),
    q: Optional[str] = Query(None, description="optional category name / url / id substring"),
    category: Optional[str] = Query(None, description="optional category code, name or id"),
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
        trace_id=trace_id,
    )
