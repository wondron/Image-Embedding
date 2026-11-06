"""Every failure leaves the API as one envelope:

    {"ok": false, "error": "<CODE>", "detail": "<message>", "trace_id": "<id>"}

plus "errors" (field-level list) for request validation. Endpoints raise
app.core.errors.AppError (or a subclass); nothing builds error JSON by hand.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import AppError, code_for_status
from app.core.middleware import TRACE_HEADER, get_trace_id

logger = logging.getLogger("api.errors")


def error_response(
    request: Request,
    code: str,
    detail: str,
    status_code: int,
    *,
    errors: list[Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    trace_id = get_trace_id(request)
    content: dict[str, Any] = {"ok": False, "error": code, "detail": detail, "trace_id": trace_id}
    if errors is not None:
        content["errors"] = errors
    return JSONResponse(
        status_code=status_code,
        content=content,
        headers={**(headers or {}), TRACE_HEADER: trace_id},
    )


def _location(entry: Mapping[str, Any]) -> str:
    return ".".join(str(part) for part in entry.get("loc") or () if part != "body")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        level = logging.WARNING if exc.http_status >= 500 else logging.INFO
        logger.log(level, "%s %s -> %d %s: %s | trace_id=%s", request.method, request.url.path,
                   exc.http_status, exc.code, exc.detail, get_trace_id(request))
        return error_response(request, exc.code, exc.detail, exc.http_status)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return error_response(request, code_for_status(exc.status_code), detail, exc.status_code,
                              headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = jsonable_encoder(exc.errors())
        summary = "; ".join(
            f"{_location(e)}: {e.get('msg')}" if _location(e) else str(e.get("msg")) for e in errors
        ) or "invalid request"
        return error_response(request, "VALIDATION_ERROR", summary, 422, errors=errors)

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # Details stay in the log (with the trace id); clients get a generic message.
        logger.exception("unhandled error %s %s | trace_id=%s", request.method, request.url.path,
                         get_trace_id(request))
        return error_response(request, "INTERNAL_ERROR", "internal server error", 500)
