"""Every failure leaves the API as one envelope:

    {"ok": false, "error": "<CODE>", "detail": "<message>", "trace_id": "<id>"}

plus "errors" (field-level list) for request validation. Endpoints raise
app.core.errors.AppError; nothing builds error JSON by hand.

What the client sees vs. what the log keeps (app.core.errors.client_detail):
internal codes (database, vector store, models) and unexpected exceptions get a
fixed public message; their detail and cause chain are logged once, here, with
the trace id.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import AppError, client_detail, code_for_status, internal_error, is_internal
from app.core.middleware import TRACE_HEADER, get_trace_id
from app.schemas.common import ErrorResponse

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
    body = ErrorResponse(error=code, detail=detail, trace_id=trace_id, errors=errors)
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(exclude_none=True),
        headers={**(headers or {}), TRACE_HEADER: trace_id},
    )


def _location(entry: Mapping[str, Any]) -> str:
    return ".".join(str(part) for part in entry.get("loc") or () if part != "body")


def app_error_response(request: Request, exc: AppError) -> JSONResponse:
    trace_id = get_trace_id(request)
    show = f" | show_text={exc.show_text}" if exc.show_text else ""
    if is_internal(exc):
        # The client gets the public text: the detail and its cause chain live here only.
        logger.error("%s %s -> %d %s: %s | trace_id=%s%s", request.method, request.url.path,
                     exc.http_status, exc.code, exc.detail, trace_id, show, exc_info=exc)
    else:
        level = logging.WARNING if exc.http_status >= 500 else logging.INFO
        logger.log(level, "%s %s -> %d %s: %s | trace_id=%s%s", request.method, request.url.path,
                   exc.http_status, exc.code, exc.detail, trace_id, show)
    return error_response(request, exc.code, client_detail(exc), exc.http_status)


def unexpected_error_response(request: Request, exc: BaseException) -> JSONResponse:
    # Details stay in the log (with the trace id); clients get a generic message.
    logger.error("unhandled error %s %s | trace_id=%s", request.method, request.url.path,
                 get_trace_id(request), exc_info=exc)
    generic = internal_error("unhandled exception")
    return error_response(request, generic.code, client_detail(generic), generic.http_status)


class UnhandledErrorMiddleware:
    """Unexpected exceptions -> the INTERNAL_ERROR envelope, from inside the CORS middleware.

    Starlette runs the `Exception` handler in ServerErrorMiddleware, which wraps every other
    middleware, so those 500s would reach browsers without CORS headers (shown as a CORS
    failure instead of the error body). register_middlewares adds this one innermost.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def send_tracking(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, send_tracking)
        except Exception as exc:
            if started:  # part of the response is already out; nothing can replace it
                raise
            await unexpected_error_response(Request(scope), exc)(scope, receive, send)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return app_error_response(request, exc)

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
        # Last resort for failures outside UnhandledErrorMiddleware (or apps without it).
        return unexpected_error_response(request, exc)
