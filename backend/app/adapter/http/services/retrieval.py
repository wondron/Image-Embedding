"""HTTP helpers for index/search/health/file. Orchestration in domain.retrieval.

Pydantic v1 compatible: dump via dump_model / .dict() — never bare model_dump (D1).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from app.adapter.http.schemas.retrieval import dump_model, ErrorBody, IndexResponse
from app.bootstrap.state import ApplicationState, get_application_state
from app.domain.retrieval.errors import RetrievalError

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


def retrieval_error_response(exc: RetrievalError, *, trace_id: Optional[str] = None) -> JSONResponse:
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
            detail={"ok": False, "error": "VALIDATION_ERROR", "detail": f"tags JSON invalid: {exc}"},
        ) from exc
    if not isinstance(val, list):
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "error": "VALIDATION_ERROR", "detail": "tags must be a JSON array"},
        )
    return val


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


def run_index(
    runtime: ApplicationState,
    items: List[Dict[str, Any]],
    *,
    public_file_base: str,
    trace_id: Optional[str] = None,
) -> JSONResponse:
    tid = _trace_id(trace_id)
    stack = require_retrieval(runtime)
    try:
        outcome = stack.index_uc.execute_batch(items, public_file_base=public_file_base)
    except RetrievalError as exc:
        return retrieval_error_response(exc, trace_id=tid)
    except Exception as exc:
        logger.exception("index failed: %s", exc)
        return error_response("INTERNAL_ERROR", str(exc), status_code=500, trace_id=tid)

    if outcome.get("all_failed") and not outcome.get("indexed"):
        failed = outcome.get("failed") or []
        # No decodable image at all → 400 INDEX_EMPTY_REQUEST per §3.1
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
    top_k: int = 10,
    score_threshold: Optional[float] = None,
    public_file_base: str,
    trace_id: Optional[str] = None,
) -> JSONResponse:
    tid = _trace_id(trace_id)
    stack = require_retrieval(runtime)
    try:
        payload = stack.search_uc.execute(
            image_bytes=image_bytes,
            image_url=image_url,
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


def mvp_health(runtime: ApplicationState) -> JSONResponse:
    """GET /api/health aggregate (§3.3). Does not replace healthz/readyz."""
    ts = datetime.now().astimezone().isoformat(timespec="seconds")
    components: Dict[str, Any] = {"api": {"ok": True}}

    retrieval = getattr(runtime, "retrieval", None)
    retrieval_error = getattr(runtime, "retrieval_error", None)

    # embedding
    emb_ok = False
    emb_info: Dict[str, Any] = {"ok": False}
    if retrieval is not None and getattr(retrieval, "embedding", None) is not None:
        emb = retrieval.embedding
        emb_ok = True
        emb_info = {
            "ok": True,
            "model_path": getattr(emb, "model_path", None),
            "vector_dim": getattr(emb, "embedding_dim", 1024),
        }
    elif retrieval_error:
        emb_info = {"ok": False, "detail": retrieval_error}
    components["embedding"] = emb_info

    # qdrant
    q_info: Dict[str, Any] = {"ok": False, "detail": "not wired"}
    if retrieval is not None and getattr(retrieval, "vectors", None) is not None:
        ping = retrieval.vectors.ping()
        q_info = {
            "ok": bool(ping.get("ok")),
            "collection": ping.get("collection") or "image_embeddings",
            "points_count": ping.get("points_count"),
        }
        if not q_info["ok"]:
            q_info["detail"] = ping.get("detail")
    components["qdrant"] = q_info

    # mysql
    m_info: Dict[str, Any] = {"ok": False, "detail": "not wired"}
    if retrieval is not None and getattr(retrieval, "metadata", None) is not None:
        ping = retrieval.metadata.ping()
        m_info = {"ok": bool(ping.get("ok"))}
        if not m_info["ok"]:
            m_info["detail"] = ping.get("detail")
    components["mysql"] = m_info

    # flyb optional
    f_info: Dict[str, Any] = {"ok": False, "optional": True, "detail": "not wired"}
    if retrieval is not None and getattr(retrieval, "flyb", None) is not None:
        flyb = retrieval.flyb
        if hasattr(flyb, "ping"):
            f_info = flyb.ping()
        else:
            f_info = {"ok": bool(getattr(flyb, "wired", False)), "optional": True}
    components["flyb"] = f_info

    ok = bool(emb_ok and q_info.get("ok") and m_info.get("ok"))
    status_code = 200 if ok else 503
    return JSONResponse(
        status_code=status_code,
        content={"ok": ok, "ts": ts, "components": components},
    )


def serve_image_file(runtime: ApplicationState, image_id: int) -> FileResponse:
    stack = require_retrieval(runtime)
    row = stack.metadata.get_by_id(int(image_id))
    if not row or row.get("status") != "active":
        raise HTTPException(
            status_code=404,
            detail={"ok": False, "error": "NOT_FOUND", "detail": f"image id={image_id}"},
        )
    path = row.get("path")
    if not path:
        raise HTTPException(
            status_code=404,
            detail={"ok": False, "error": "NOT_FOUND", "detail": "path missing"},
        )
    from pathlib import Path

    p = Path(path)
    if not p.is_file():
        raise HTTPException(
            status_code=404,
            detail={"ok": False, "error": "NOT_FOUND", "detail": f"file missing: {path}"},
        )
    media = "application/octet-stream"
    suffix = p.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        media = "image/jpeg"
    elif suffix == ".png":
        media = "image/png"
    elif suffix == ".webp":
        media = "image/webp"
    elif suffix == ".gif":
        media = "image/gif"
    return FileResponse(path=str(p), media_type=media, filename=p.name)
