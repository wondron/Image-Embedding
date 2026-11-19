"""每个失败都以同一种结构离开 API：

    {"ok": false, "error": "<CODE>", "detail": "<message>", "trace_id": "<id>"}

请求校验失败时另有 "errors"（字段级的列表）。接口抛出 app.core.errors.AppError；
没有任何地方手工拼错误 JSON。

客户端看到的和日志保留的（app.core.errors.client_detail）：
内部错误码（数据库、向量库、模型）和意外异常给固定的公开信息；它们的 detail
和异常链只在这里记一次日志，带上 trace id。
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
        # 客户端拿到的是公开文字：detail 和它的异常链只留在这里。
        logger.error("%s %s -> %d %s: %s | trace_id=%s%s", request.method, request.url.path,
                     exc.http_status, exc.code, exc.detail, trace_id, show, exc_info=exc)
    else:
        level = logging.WARNING if exc.http_status >= 500 else logging.INFO
        logger.log(level, "%s %s -> %d %s: %s | trace_id=%s%s", request.method, request.url.path,
                   exc.http_status, exc.code, exc.detail, trace_id, show)
    return error_response(request, exc.code, client_detail(exc), exc.http_status)


def unexpected_error_response(request: Request, exc: BaseException) -> JSONResponse:
    # 细节留在日志里（带 trace id）；客户端拿到通用信息。
    logger.error("unhandled error %s %s | trace_id=%s", request.method, request.url.path,
                 get_trace_id(request), exc_info=exc)
    generic = internal_error("unhandled exception")
    return error_response(request, generic.code, client_detail(generic), generic.http_status)


class UnhandledErrorMiddleware:
    """意外异常 -> INTERNAL_ERROR 结构，在 CORS 中间件内部处理。

    Starlette 在 ServerErrorMiddleware 里执行 `Exception` 的处理器，而它包在所有其他
    中间件外面，所以这些 500 到达浏览器时没有 CORS 响应头（显示成 CORS 失败，
    而不是错误内容）。register_middlewares 把这个中间件加在最里层。
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
            if started:  # 响应已经发出一部分了；无法再替换
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
        # UnhandledErrorMiddleware 之外的失败（或者没有它的应用）的最后一道处理。
        return unexpected_error_response(request, exc)
