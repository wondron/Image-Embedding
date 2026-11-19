from __future__ import annotations

import re
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.core.config.settings import settings

TRACE_HEADER = "X-Trace-Id"
_TRACE_HEADER_KEY = TRACE_HEADER.lower().encode("latin-1")
# 客户端传来的 id 会进入响应头和日志：保持简短且无害。
_TRACE_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,64}")


def _clean_trace_id(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if _TRACE_ID_RE.fullmatch(text) else None


def _new_trace_id() -> str:
    return str(uuid.uuid4())  # 和 show_text / 历史 trace id 的格式相同


def get_trace_id(request: Request) -> str:
    """这个请求的 trace id（由 TraceIdMiddleware 设置，否则第一次使用时生成）。"""
    trace_id = getattr(request.state, "trace_id", None)
    if not trace_id:
        trace_id = _new_trace_id()
        request.state.trace_id = trace_id
    return trace_id


def bind_trace_id(request: Request, explicit: Any = None) -> str:
    """采用请求体 / 查询参数里传来的 id（合规时）作为这个请求的 trace id。"""
    trace_id = _clean_trace_id(explicit)
    if trace_id:
        request.state.trace_id = trace_id
        return trace_id
    return get_trace_id(request)


class TraceIdMiddleware:
    """每个请求一个 trace id：X-Trace-Id 请求头合规时用它，否则生成一个。

    存在 request.state.trace_id 里（错误结构、日志用），并回写到 X-Trace-Id 响应头
    （如果接口重新绑定过，用最终的值）。
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope.get("headers") or []).get(_TRACE_HEADER_KEY, b"").decode("latin-1")
        state = scope.setdefault("state", {})
        state["trace_id"] = _clean_trace_id(incoming) or _new_trace_id()

        async def send_with_trace_id(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                if not any(key.lower() == _TRACE_HEADER_KEY for key, _ in headers):
                    headers.append((_TRACE_HEADER_KEY, str(state.get("trace_id", "")).encode("latin-1")))
                    message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_trace_id)


def register_middlewares(app: FastAPI) -> None:
    """先加最里层的：每次 add_middleware 都包住之前加的中间件。"""
    # 放在 CORS 里面，这样意外异常产生的 500 也带 CORS 响应头。
    # 在这里导入：app.core.exceptions 导入了本模块。
    from app.core.exceptions import UnhandledErrorMiddleware

    app.add_middleware(UnhandledErrorMiddleware)
    origins = list(settings.cors.origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if origins == ["*"] else origins,
        allow_credentials=settings.cors.allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[TRACE_HEADER],
    )
    app.add_middleware(TraceIdMiddleware)
