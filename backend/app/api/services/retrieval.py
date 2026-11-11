"""HTTP adapter for retrieval: parse the wire request and call a use case.

Business flow lives in app.services.retrieval (reached through RetrievalStack).
Functions return the response body as a dict ({ok, trace_id, ...result}); the
routes' response_model (app.schemas.retrieval) validates and serializes it.
Failures are raised as AppError (codes registered in app.core.errors) and rendered by
app.core.exceptions. Use cases block (ONNX / PG / Qdrant / S3), so async
handlers run them in the threadpool instead of on the event loop.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Type, TypeVar

from fastapi import Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.api.deps import require_retrieval
from app.api.uploads import form_upload, read_upload
from app.bootstrap.state import ApplicationState
from app.clients.image_fetch import read_url_bytes
from app.core.middleware import bind_trace_id
from app.schemas.retrieval import IndexJsonRequest, SearchJsonRequest
from app.core import errors as err

logger = logging.getLogger("svc.retrieval")

MAX_INDEX_FILES = 100
# Optional food annotation an index request may carry; the use case validates it. Same names
# as app.services.retrieval.food_fields.FOOD_FIELDS (not imported: use cases load with the stack)
FOOD_KEYS = (
    "food_state",
    "food_processing",
    "food_count",
    "total_mass_g",
    "water_mass_g",
    "main_ingredient_mass_g",
)
# Labelling an index request may set; omitted: label_status follows the category, source_type
# user_upload, license unknown (app.services.retrieval.index_image)
LABEL_KEYS = ("label_status", "source_type", "license")
M = TypeVar("M", bound=BaseModel)


# -- wire parsing -------------------------------------------------------------------

def _is_json(request: Request) -> bool:
    return "application/json" in (request.headers.get("content-type") or "").lower()


async def _json_body(request: Request, model: Type[M]) -> M:
    try:
        return model(**(await request.json()))
    except (ValueError, TypeError) as exc:  # bad JSON or schema (pydantic errors are ValueErrors)
        raise err.validation_error(str(exc)) from exc


def _form_text(raw: Any) -> Optional[str]:
    """A multipart text field; None when omitted or blank."""
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _json_array(raw: Any, field: str) -> Optional[List[Any]]:
    """Multipart field holding a JSON array; None when omitted."""
    if raw is None or raw == "":
        return None
    try:
        value = json.loads(str(raw))
    except json.JSONDecodeError as exc:
        raise err.validation_error(f"{field} JSON invalid: {exc}") from exc
    if not isinstance(value, list):
        raise err.validation_error(f"{field} must be a JSON array")
    return value


def _int_param(raw: Any, field: str) -> int:
    try:
        return int(str(raw).strip())
    except ValueError as exc:
        raise err.validation_error(f"{field} must be an integer, got {raw!r}") from exc


def parse_score_threshold(raw: Any) -> Optional[float]:
    """Wire score_threshold -> float | None (range rules live in the use case)."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise err.search_bad_score_threshold(f"score_threshold must be a finite float; got {raw!r}") from exc


# -- POST /api/index ------------------------------------------------------------------

async def index_from_upload(
    runtime: ApplicationState, request: Request, file: UploadFile
) -> Dict[str, Any]:
    """Read one uploaded image and run the existing indexing use case."""
    trace_id = bind_trace_id(request)
    data = await read_upload(file, runtime.max_image_bytes)
    stack = require_retrieval(runtime)
    indexed = await run_in_threadpool(
        stack.index_uc.execute_one,
        image_bytes=data,
        filename=file.filename or "upload.bin",
    )
    return {"ok": True, "trace_id": trace_id, "indexed": [indexed], "failed": []}


async def index_from_request(runtime: ApplicationState, request: Request) -> Dict[str, Any]:
    """multipart file/files (<= 100) or JSON {path, description, category, tags, scene, scenes,
    label_status, source_type, license, and the optional food fields FOOD_KEYS}."""
    trace_id = bind_trace_id(request)
    if _is_json(request):
        raw = await _json_body(request, IndexJsonRequest)
        items = [{
            "source_path": raw.path,
            "filename": raw.path,
            "category": raw.category,
            "tags": raw.tags,
            "scene": raw.scene,
            "scenes": raw.scenes,
            "description": raw.description,
            **{key: getattr(raw, key) for key in LABEL_KEYS},
            **{key: getattr(raw, key) for key in FOOD_KEYS},
        }]
    else:
        items = await _index_items_from_form(request, runtime.max_image_bytes)

    stack = require_retrieval(runtime)
    outcome = await run_in_threadpool(stack.index_uc.execute_batch, items)
    _raise_if_batch_failed_uniformly(outcome)
    return {"ok": True, "trace_id": trace_id, "indexed": outcome["indexed"], "failed": outcome["failed"]}


async def _index_items_from_form(request: Request, max_bytes: int) -> List[Dict[str, Any]]:
    form = await request.form()
    category = form.get("category")
    scene = form.get("scene")
    shared = {
        "category": str(category) if category is not None else None,
        "tags": _json_array(form.get("tags"), "tags"),
        "scene": str(scene).strip() if scene not in (None, "") else None,
        "scenes": _json_array(form.get("scenes"), "scenes"),
        "description": form.get("description"),  # normalized (and validated) by the use case
        **{key: _form_text(form.get(key)) for key in LABEL_KEYS},  # enum values; checked by the use case
        **{key: form.get(key) for key in FOOD_KEYS},  # strings; validated by the use case
    }
    uploads = []
    for value in [form.get("file"), *form.getlist("files"), *form.getlist("file")]:
        upload = form_upload(value)
        if upload is not None and upload not in uploads:
            uploads.append(upload)
    if len(uploads) > MAX_INDEX_FILES:
        raise err.validation_error(f"too many files (max {MAX_INDEX_FILES})")
    if not uploads:
        raise err.index_empty_request("no file and no path")

    items = []
    for upload in uploads:
        data = await read_upload(upload, max_bytes)
        items.append({"image_bytes": data, "filename": upload.filename or "upload.bin", **shared})
    return items


def _raise_if_batch_failed_uniformly(outcome: Dict[str, Any]) -> None:
    """Nothing indexed and every item failed the same way -> report that error."""
    if outcome["indexed"]:
        return
    failed = outcome["failed"]
    for code, fallback in (
        ("INDEX_UNKNOWN_SCENE", "unknown scene"),
        ("VALIDATION_ERROR", "validation error"),
    ):
        if failed and all(f.get("error") == code for f in failed):
            raise err.AppError(code, failed[0].get("detail") or fallback)
    if all(f.get("error") in {"INDEX_EMPTY_REQUEST", "INDEX_BAD_IMAGE"} for f in failed):
        raise err.index_empty_request("no decodable image in request")


# -- POST /api/search -------------------------------------------------------------------

@dataclass
class _SearchParams:
    image_bytes: Optional[bytes] = None
    image_url: Optional[str] = None
    scene: Optional[str] = None
    top_k: int = 10
    score_threshold: Optional[float] = None
    trace_id: Optional[str] = None


async def search_from_request(runtime: ApplicationState, request: Request) -> Dict[str, Any]:
    """multipart file / img_url or JSON {img_url}; query params override body fields."""
    params = await _search_params(request, runtime.max_image_bytes)
    trace_id = bind_trace_id(request, params.trace_id)
    if params.image_bytes is None and not params.image_url:
        raise err.validation_error("file or img_url is required")
    if not 1 <= params.top_k <= 50:
        raise err.validation_error("top_k must be in 1..50")

    stack = require_retrieval(runtime)
    image_bytes = params.image_bytes
    if image_bytes is None:
        image_bytes = await read_url_bytes(
            params.image_url,
            max_image_bytes=runtime.max_image_bytes,
            timeout_s=runtime.http_timeout,
        )
    result = await run_in_threadpool(
        stack.search_uc.execute,
        image_bytes=image_bytes,
        scene=params.scene,
        top_k=params.top_k,
        score_threshold=params.score_threshold,
    )
    return {"ok": True, "trace_id": trace_id, **result}


async def _search_params(request: Request, max_bytes: int) -> _SearchParams:
    params = _SearchParams()
    if _is_json(request):
        raw = await _json_body(request, SearchJsonRequest)
        params.image_url = raw.img_url
        params.scene = raw.scene
        params.top_k = int(raw.top_k)
        params.score_threshold = parse_score_threshold(raw.score_threshold)
        params.trace_id = raw.trace_id
    else:
        form = await request.form()
        upload = form_upload(form.get("file"))
        if upload is not None:
            params.image_bytes = await read_upload(upload, max_bytes)
        if form.get("img_url") and params.image_bytes is None:
            params.image_url = str(form.get("img_url"))
        if form.get("scene") not in (None, ""):
            params.scene = str(form.get("scene"))
        if form.get("top_k") is not None:
            params.top_k = _int_param(form.get("top_k"), "top_k")
        if form.get("score_threshold") not in (None, ""):
            params.score_threshold = parse_score_threshold(form.get("score_threshold"))
        if form.get("trace_id") is not None:
            params.trace_id = str(form.get("trace_id"))

    query = request.query_params
    if query.get("scene") not in (None, ""):
        params.scene = query.get("scene")
    if query.get("top_k") is not None:
        params.top_k = _int_param(query.get("top_k"), "top_k")
    if query.get("score_threshold") not in (None, ""):
        params.score_threshold = parse_score_threshold(query.get("score_threshold"))
    if query.get("trace_id"):
        params.trace_id = query.get("trace_id")
    return params


# -- GET /api/images ---------------------------------------------------------------------

def run_list_images(
    runtime: ApplicationState,
    request: Request,
    *,
    scene: Optional[str] = None,
    page: int = 1,
    page_size: int = 24,
    q: Optional[str] = None,
    category: Optional[str] = None,
    tag: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    trace_id = bind_trace_id(request, trace_id)
    stack = require_retrieval(runtime)
    result = stack.list_uc.execute(
        scene=scene, page=page, page_size=page_size, q=q, category=category, tag=tag
    )
    return {"ok": True, "trace_id": trace_id, **result}


# -- GET /api/health ---------------------------------------------------------------------

_FLYB_DISABLED = {"ok": False, "optional": True, "disabled": True, "detail": "out of scope"}


def mvp_health(runtime: ApplicationState) -> JSONResponse:
    """GET /api/health: embedding / qdrant / metadata status of the retrieval stack.

    "postgres" is the metadata database; "flyb" is always disabled.
    Does not replace /api/healthz or /api/readyz.
    """
    ts = datetime.now().astimezone().isoformat(timespec="seconds")
    stack = runtime.retrieval
    if stack is None:
        detail = runtime.retrieval_error or "not wired"
        components: Dict[str, Any] = {
            "api": {"ok": True},
            "embedding": {"ok": False, "detail": detail, "embedding_space_id": None},
            "qdrant": {
                "ok": False,
                "detail": detail,
                "collection": None,  # the real one comes from the current embedding model
                "embedding_space_id": None,
            },
            "postgres": {"ok": False, "detail": detail},
            "flyb": dict(_FLYB_DISABLED),
        }
        return JSONResponse(status_code=503, content={"ok": False, "ts": ts, "components": components})

    model = stack.model
    pings = stack.infra.ping()
    q, pg = pings["qdrant"], pings["postgres"]
    qdrant: Dict[str, Any] = {
        "ok": bool(q.get("ok")),
        "collection": q.get("collection") or model.collection,
        "points_count": q.get("points_count"),
        "embedding_space_id": model.space_id,
    }
    if not qdrant["ok"]:
        qdrant["detail"] = q.get("detail")
    metadata: Dict[str, Any] = {
        "ok": bool(pg.get("ok")),
        "driver": pg.get("driver") or "postgresql",
        "host": pg.get("host"),
        "database": pg.get("database"),
    }
    if not metadata["ok"]:
        metadata["detail"] = pg.get("detail")
    components = {
        "api": {"ok": True},
        "embedding": {
            "ok": True,
            "model_path": stack.embedding.model_path,
            "vector_dim": model.dim,
            "embedding_space_id": model.space_id,
            "current_model": model.name,
            "current_collection": model.collection,
        },
        "qdrant": qdrant,
        "postgres": metadata,
        "flyb": dict(_FLYB_DISABLED),
    }
    ok = qdrant["ok"] and metadata["ok"]
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"ok": ok, "ts": ts, "components": components},
    )
