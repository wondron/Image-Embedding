"""HTTP adapter for retrieval: parse the wire request, call a use case, map errors.

Business flow lives in app.services.retrieval (reached through RetrievalStack).
Use cases block (ONNX / PG / Qdrant / S3), so async handlers run them in the
threadpool instead of on the event loop.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.schemas.retrieval import (
    ErrorBody,
    IndexJsonRequest,
    IndexResponse,
    SearchJsonRequest,
    dump_model,
)
from app.bootstrap.state import ApplicationState
from app.services.retrieval.errors import RetrievalError

logger = logging.getLogger("svc.retrieval")


def _trace_id(explicit: Optional[str] = None) -> str:
    return (explicit or "").strip() or str(uuid.uuid4())


def error_response(
    code: str,
    detail: str,
    *,
    status_code: int,
    trace_id: Optional[str] = None,
) -> JSONResponse:
    body = ErrorBody(ok=False, error=code, detail=detail, trace_id=trace_id or _trace_id())
    return JSONResponse(status_code=status_code, content=dump_model(body))


def retrieval_error_response(
    exc: RetrievalError, *, trace_id: Optional[str] = None
) -> JSONResponse:
    return error_response(
        exc.code,
        exc.detail,
        status_code=exc.http_status,
        trace_id=trace_id,
    )


def public_base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def require_retrieval(runtime: ApplicationState):
    try:
        return runtime.require_retrieval()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "error": "NOT_READY", "detail": str(exc)},
        ) from exc


def parse_tags(raw: Optional[str]) -> Optional[List[Any]]:
    if raw is None or raw == "":
        return None
    try:
        val = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "ok": False,
                "error": "VALIDATION_ERROR",
                "detail": f"tags JSON invalid: {exc}",
            },
        ) from exc
    if not isinstance(val, list):
        raise HTTPException(
            status_code=422,
            detail={
                "ok": False,
                "error": "VALIDATION_ERROR",
                "detail": "tags must be a JSON array",
            },
        )
    return val


def parse_scenes_json(raw: Optional[str]) -> Optional[List[Any]]:
    """Parse multipart `scenes` JSON array; None if omitted. Domain validates registry."""
    if raw is None or raw == "":
        return None
    try:
        val = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "ok": False,
                "error": "VALIDATION_ERROR",
                "detail": f"scenes JSON invalid: {exc}",
            },
        ) from exc
    if not isinstance(val, list):
        raise HTTPException(
            status_code=422,
            detail={
                "ok": False,
                "error": "VALIDATION_ERROR",
                "detail": "scenes must be a JSON array",
            },
        )
    return val


def parse_score_threshold_raw(raw: Any) -> Optional[float]:
    """Parse wire score_threshold; invalid literal -> SEARCH_BAD_SCORE_THRESHOLD.

    Empty / missing -> None (application default 0.55). Range checks stay in usecase.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if s == "":
        return None
    try:
        return float(s)
    except (TypeError, ValueError) as exc:
        from app.services.retrieval import errors as err

        raise err.search_bad_score_threshold(
            f"score_threshold must be a finite float; got {raw!r}"
        ) from exc


async def read_upload(file: UploadFile, max_bytes: int) -> bytes:
    data = await file.read(max_bytes + 1)
    if not data:
        raise RetrievalError("INDEX_BAD_IMAGE", "Uploaded file is empty", http_status=422)
    if len(data) > max_bytes:
        raise RetrievalError(
            "INDEX_BAD_IMAGE",
            f"File too large (> {max_bytes} bytes)",
            http_status=422,
        )
    return data


def _as_upload(value: Any) -> Optional[UploadFile]:
    if isinstance(value, (UploadFile, StarletteUploadFile)):
        return value  # type: ignore[return-value]
    return None


async def index_from_request(runtime: ApplicationState, request: Request) -> JSONResponse:
    """POST /api/index: multipart file/files (<=100) or JSON {path, category, tags, scene(s)}."""
    base = public_base(request)
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
            tag_list = parse_tags(str(tags_raw) if tags_raw is not None else None)
            scene_raw = form.get("scene")
            scene_s = str(scene_raw).strip() if scene_raw not in (None, "") else None
            scenes_raw = form.get("scenes")
            scenes_list = parse_scenes_json(str(scenes_raw) if scenes_raw is not None else None)

            uploads: List[UploadFile] = []
            for item in [form.get("file"), *form.getlist("files"), *form.getlist("file")]:
                up = _as_upload(item)
                if up is not None and up not in uploads:
                    uploads.append(up)

            if len(uploads) > 100:
                return error_response(
                    "VALIDATION_ERROR",
                    "too many files (max 100)",
                    status_code=422,
                )
            if not uploads:
                return error_response(
                    "INDEX_EMPTY_REQUEST",
                    "no file and no path",
                    status_code=400,
                )
            for up in uploads:
                data = await read_upload(up, runtime.max_image_bytes)
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
        return retrieval_error_response(exc)
    except Exception as exc:
        return error_response("VALIDATION_ERROR", str(exc), status_code=422)

    return await run_in_threadpool(run_index, runtime, items, public_file_base=base)


async def search_from_request(runtime: ApplicationState, request: Request) -> JSONResponse:
    """POST /api/search: multipart file / img_url or JSON {img_url}; query params override."""
    base = public_base(request)
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
            thr = parse_score_threshold_raw(raw.score_threshold)
            tid = raw.trace_id
        else:
            form = await request.form()
            up = _as_upload(form.get("file"))
            if up is not None:
                image_bytes = await read_upload(up, runtime.max_image_bytes)
            img_url_val = form.get("img_url")
            if img_url_val and image_bytes is None:
                image_url = str(img_url_val)
            scene_val = form.get("scene")
            if scene_val not in (None, ""):
                scene = str(scene_val)
            if form.get("top_k") is not None:
                k = int(str(form.get("top_k")))
            if form.get("score_threshold") not in (None, ""):
                thr = parse_score_threshold_raw(form.get("score_threshold"))
            if form.get("trace_id") is not None:
                tid = str(form.get("trace_id"))

        # query overrides
        qp = request.query_params
        if qp.get("scene") not in (None, ""):
            scene = qp.get("scene")
        if qp.get("top_k") is not None:
            k = int(qp.get("top_k"))
        if qp.get("score_threshold") not in (None, ""):
            thr = parse_score_threshold_raw(qp.get("score_threshold"))
        if qp.get("trace_id"):
            tid = qp.get("trace_id")

        if image_bytes is None and not image_url:
            return error_response(
                "VALIDATION_ERROR",
                "file or img_url is required",
                status_code=422,
                trace_id=tid,
            )
        if not (1 <= k <= 50):
            return error_response(
                "VALIDATION_ERROR",
                "top_k must be in 1..50",
                status_code=422,
                trace_id=tid,
            )
    except RetrievalError as exc:
        return retrieval_error_response(exc, trace_id=tid)
    except Exception as exc:
        return error_response("VALIDATION_ERROR", str(exc), status_code=422, trace_id=tid)

    return await run_in_threadpool(
        run_search,
        runtime,
        image_bytes=image_bytes,
        image_url=image_url,
        scene=scene,
        top_k=k,
        score_threshold=thr,
        public_file_base=base,
        trace_id=tid,
    )


def run_index(
    runtime: ApplicationState,
    items: List[Dict[str, Any]],
    *,
    public_file_base: str,
    trace_id: Optional[str] = None,
) -> JSONResponse:
    """Invoke IndexImageUseCase; map batch outcome to HTTP."""
    tid = _trace_id(trace_id)
    stack = require_retrieval(runtime)
    try:
        outcome = stack.index_uc.execute_batch(items, public_file_base=public_file_base)
    except RetrievalError as exc:
        return retrieval_error_response(exc, trace_id=tid)
    except Exception as exc:
        logger.exception("index failed: %s", exc)
        return error_response("INTERNAL_ERROR", str(exc), status_code=500, trace_id=tid)

    failed = outcome.get("failed") or []
    indexed = outcome.get("indexed") or []
    # Uniform whole-batch failures -> top-level error (HTTP mapping only)
    if (not indexed) and failed and all(f.get("error") == "INDEX_PATH_TOO_LONG" for f in failed):
        return error_response(
            "INDEX_PATH_TOO_LONG",
            (failed[0].get("message") or "path too long after normalize"),
            status_code=422,
            trace_id=tid,
        )
    if (not indexed) and failed and all(f.get("error") == "INDEX_UNKNOWN_SCENE" for f in failed):
        return error_response(
            "INDEX_UNKNOWN_SCENE",
            (failed[0].get("message") or "unknown scene"),
            status_code=422,
            trace_id=tid,
        )
    if (not indexed) and failed and all(f.get("error") == "VALIDATION_ERROR" for f in failed):
        return error_response(
            "VALIDATION_ERROR",
            (failed[0].get("message") or "validation error"),
            status_code=422,
            trace_id=tid,
        )
    if outcome.get("all_failed") and not indexed:
        if not failed or all(
            f.get("error") in {"INDEX_EMPTY_REQUEST", "INDEX_BAD_IMAGE"} for f in failed
        ):
            return error_response(
                "INDEX_EMPTY_REQUEST",
                "no decodable image in request",
                status_code=400,
                trace_id=tid,
            )

    resp = IndexResponse(
        ok=True,
        trace_id=tid,
        indexed=outcome.get("indexed") or [],
        failed=outcome.get("failed") or [],
    )
    return JSONResponse(status_code=200, content=dump_model(resp))


def run_search(
    runtime: ApplicationState,
    *,
    image_bytes: Optional[bytes] = None,
    image_url: Optional[str] = None,
    scene: Optional[str] = None,
    top_k: int = 10,
    score_threshold: Optional[float] = None,
    public_file_base: str,
    trace_id: Optional[str] = None,
) -> JSONResponse:
    """Invoke SearchImageUseCase; return envelope as-is."""
    tid = _trace_id(trace_id)
    stack = require_retrieval(runtime)
    try:
        payload = stack.search_uc.execute(
            image_bytes=image_bytes,
            image_url=image_url,
            scene=scene,
            top_k=top_k,
            score_threshold=score_threshold,
            public_file_base=public_file_base,
            trace_id=tid,
        )
        return JSONResponse(status_code=200, content=payload)
    except RetrievalError as exc:
        return retrieval_error_response(exc, trace_id=tid)
    except Exception as exc:
        logger.exception("search failed: %s", exc)
        return error_response("INTERNAL_ERROR", str(exc), status_code=500, trace_id=tid)


def run_list_images(
    runtime: ApplicationState,
    *,
    scene: Optional[str] = None,
    page: int = 1,
    page_size: int = 24,
    q: Optional[str] = None,
    category: Optional[str] = None,
    public_file_base: str,
    trace_id: Optional[str] = None,
) -> JSONResponse:
    """Invoke ListImagesUseCase."""
    tid = _trace_id(trace_id)
    stack = require_retrieval(runtime)
    try:
        payload = stack.list_uc.execute(
            scene=scene,
            page=page,
            page_size=page_size,
            q=q,
            category=category,
            public_file_base=public_file_base,
            trace_id=tid,
        )
        return JSONResponse(status_code=200, content=payload)
    except RetrievalError as exc:
        return retrieval_error_response(exc, trace_id=tid)
    except Exception as exc:
        logger.exception("list images failed: %s", exc)
        return error_response("INTERNAL_ERROR", str(exc), status_code=500, trace_id=tid)


_FLYB_DISABLED = {"ok": False, "optional": True, "disabled": True, "detail": "out of scope"}


def mvp_health(runtime: ApplicationState) -> JSONResponse:
    """GET /api/health: embedding / qdrant / metadata status of the retrieval stack.

    Wire keys kept for compatibility: "mysql" = PostgreSQL metadata; "flyb" always
    disabled. Does not replace /api/healthz or /api/readyz.
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
                "collection": runtime.settings.qdrant.collection,
                "embedding_space_id": None,
            },
            "mysql": {"ok": False, "detail": detail},
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
        "mysql": metadata,
        "flyb": dict(_FLYB_DISABLED),
    }
    ok = qdrant["ok"] and metadata["ok"]
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"ok": ok, "ts": ts, "components": components},
    )
