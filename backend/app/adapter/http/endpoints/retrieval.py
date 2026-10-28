"""Image index / search / list / health / file (SSOT §3).

Keeps existing predict/searchImage_* untouched.
Multipart vs JSON resolved by Content-Type (avoids FastAPI File+Body conflict).
Pydantic v1 compatible.
"""

from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.adapter.http.schemas.retrieval import IndexJsonRequest, SearchJsonRequest
from app.adapter.http.services import retrieval as retrieval_service
from app.bootstrap.state import get_application_state
from app.domain.retrieval.errors import RetrievalError

router = APIRouter(prefix="/api", tags=["retrieval"])


def _as_upload(value: Any) -> Optional[UploadFile]:
    if value is None:
        return None
    if isinstance(value, (UploadFile, StarletteUploadFile)):
        return value  # type: ignore[return-value]
    return None


@router.post(
    "/index",
    summary="Index image(s)",
    description="multipart file/files or JSON {path,category,tags,scene,scenes}. SSOT §3.1.",
)
async def index_image(request: Request) -> JSONResponse:
    runtime = get_application_state(request)
    base = retrieval_service.public_base(request)
    content_type = (request.headers.get("content-type") or "").lower()
    items: List[dict] = []

    try:
        if "application/json" in content_type:
            payload = await request.json()
            raw = IndexJsonRequest(**payload)
            items.append(
                {
                    "source_path": raw.path,
                    "filename": raw.path,
                    "category": raw.category,
                    "tags": raw.tags,
                    "scene": raw.scene,
                    "scenes": raw.scenes,
                }
            )
        else:
            form = await request.form()
            category = form.get("category")
            category_s = str(category) if category is not None else None
            tags_raw = form.get("tags")
            tag_list = retrieval_service.parse_tags(
                str(tags_raw) if tags_raw is not None else None
            )
            scene_raw = form.get("scene")
            scene_s = str(scene_raw).strip() if scene_raw not in (None, "") else None
            scenes_raw = form.get("scenes")
            scenes_list = retrieval_service.parse_scenes_json(
                str(scenes_raw) if scenes_raw is not None else None
            )

            uploads: List[UploadFile] = []
            single = _as_upload(form.get("file"))
            if single is not None:
                uploads.append(single)
            multi = form.getlist("files") if hasattr(form, "getlist") else []
            for item in multi:
                up = _as_upload(item)
                if up is not None:
                    uploads.append(up)
            # also accept repeated "file"
            for item in (form.getlist("file") if hasattr(form, "getlist") else []):
                up = _as_upload(item)
                if up is not None and up not in uploads:
                    uploads.append(up)

            if len(uploads) > 100:
                return retrieval_service.error_response(
                    "VALIDATION_ERROR",
                    "too many files (max 100)",
                    status_code=422,
                )
            if not uploads:
                return retrieval_service.error_response(
                    "INDEX_EMPTY_REQUEST",
                    "no file and no path",
                    status_code=400,
                )
            for up in uploads:
                data = await retrieval_service.read_upload(up, runtime.max_image_bytes)
                items.append(
                    {
                        "image_bytes": data,
                        "filename": up.filename or "upload.bin",
                        "category": category_s,
                        "tags": tag_list,
                        "scene": scene_s,
                        "scenes": scenes_list,
                    }
                )
    except RetrievalError as exc:
        return retrieval_service.retrieval_error_response(exc)
    except Exception as exc:
        return retrieval_service.error_response(
            "VALIDATION_ERROR",
            str(exc),
            status_code=422,
        )

    return retrieval_service.run_index(runtime, items, public_file_base=base)


@router.post(
    "/search",
    summary="Search by image",
    description=(
        "multipart file OR JSON {img_url,scene}. Self-built-first; "
        "FlyB only when scene=normal and final list length==0. SSOT §3.2/§4."
    ),
)
async def search_image(request: Request) -> JSONResponse:
    runtime = get_application_state(request)
    base = retrieval_service.public_base(request)
    content_type = (request.headers.get("content-type") or "").lower()

    image_bytes = None
    image_url = None
    scene = None
    k = 10
    thr = None
    tid = None

    try:
        if "application/json" in content_type:
            payload = await request.json()
            raw = SearchJsonRequest(**payload)
            image_url = raw.img_url
            scene = raw.scene
            k = int(raw.top_k)
            thr = raw.score_threshold
            tid = raw.trace_id
        else:
            form = await request.form()
            up = _as_upload(form.get("file"))
            if up is not None:
                image_bytes = await retrieval_service.read_upload(
                    up, runtime.max_image_bytes
                )
            img_url_val = form.get("img_url")
            if img_url_val and image_bytes is None:
                image_url = str(img_url_val)
            scene_val = form.get("scene")
            if scene_val not in (None, ""):
                scene = str(scene_val)
            if form.get("top_k") is not None:
                k = int(str(form.get("top_k")))
            if form.get("score_threshold") not in (None, ""):
                thr = float(str(form.get("score_threshold")))
            if form.get("trace_id") is not None:
                tid = str(form.get("trace_id"))

        # query overrides
        qp = request.query_params
        if qp.get("scene") not in (None, ""):
            scene = qp.get("scene")
        if qp.get("top_k") is not None:
            k = int(qp.get("top_k"))
        if qp.get("score_threshold") not in (None, ""):
            thr = float(qp.get("score_threshold"))
        if qp.get("trace_id"):
            tid = qp.get("trace_id")

        if image_bytes is None and not image_url:
            return retrieval_service.error_response(
                "VALIDATION_ERROR",
                "file or img_url is required",
                status_code=422,
                trace_id=tid,
            )
        if not (1 <= k <= 50):
            return retrieval_service.error_response(
                "VALIDATION_ERROR",
                "top_k must be in 1..50",
                status_code=422,
                trace_id=tid,
            )
    except RetrievalError as exc:
        return retrieval_service.retrieval_error_response(exc, trace_id=tid)
    except Exception as exc:
        return retrieval_service.error_response(
            "VALIDATION_ERROR",
            str(exc),
            status_code=422,
            trace_id=tid,
        )

    return retrieval_service.run_search(
        runtime,
        image_bytes=image_bytes,
        image_url=image_url,
        scene=scene,
        top_k=k,
        score_threshold=thr,
        public_file_base=base,
        trace_id=tid,
    )


@router.get(
    "/health",
    summary="MVP dependency health",
    description="Aggregate embedding/qdrant/mysql/flyb. Does not replace healthz/readyz. SSOT §3.3.",
)
def mvp_health(request: Request) -> JSONResponse:
    return retrieval_service.mvp_health(get_application_state(request))



@router.get(
    "/images",
    summary="List library images",
    description=(
        "Paginated active images filtered by scene (default normal). "
        "Optional q (title/tags substring) and category exact match. SSOT §3.5."
    ),
)
def list_library_images(
    request: Request,
    scene: Optional[str] = Query(None, description="default normal; unknown -> LIST_UNKNOWN_SCENE"),
    page: int = Query(1, description="1-based page"),
    page_size: int = Query(24, description="default 24, range 1..100"),
    q: Optional[str] = Query(None, description="optional title/tags substring"),
    category: Optional[str] = Query(None, description="optional exact category"),
    trace_id: Optional[str] = Query(None),
) -> JSONResponse:
    runtime = get_application_state(request)
    base = retrieval_service.public_base(request)
    return retrieval_service.run_list_images(
        runtime,
        scene=scene,
        page=page,
        page_size=page_size,
        q=q,
        category=category,
        public_file_base=base,
        trace_id=trace_id,
    )

@router.get(
    "/images/file",
    summary="Serve indexed image bytes",
    description="GET /api/images/file?id= — active rows only. SSOT §3.4.",
)
def images_file(
    request: Request,
    id: int = Query(..., ge=1, description="images.id"),
) -> Any:
    return retrieval_service.serve_image_file(get_application_state(request), id)
