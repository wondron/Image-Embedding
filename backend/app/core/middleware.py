from __future__ import annotations

import re
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.core.config.settings import settings

TRACE_HEADER = "X-Trace-Id"
_TRACE_HEADER_KEY = TRACE_HEADER.lower().encode("latin-1")
# Client-supplied ids end up in headers and logs: keep them short and inert.
_TRACE_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,64}")


def _clean_trace_id(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if _TRACE_ID_RE.fullmatch(text) else None


def _new_trace_id() -> str:
    return str(uuid.uuid4())  # same format as show_text / historical trace ids


def get_trace_id(request: Request) -> str:
    """The request's trace id (set by TraceIdMiddleware, else created on first use)."""
    trace_id = getattr(request.state, "trace_id", None)
    if not trace_id:
        trace_id = _new_trace_id()
        request.state.trace_id = trace_id
    return trace_id


def bind_trace_id(request: Request, explicit: Any = None) -> str:
    """Adopt an id passed in the request body/query (if valid) for this request."""
    trace_id = _clean_trace_id(explicit)
    if trace_id:
        request.state.trace_id = trace_id
        return trace_id
    return get_trace_id(request)


class TraceIdMiddleware:
    """One trace id per request: from the X-Trace-Id header if valid, else generated.

    Stored in request.state.trace_id (error envelopes, logs) and echoed in the
    X-Trace-Id response header (the final value, if an endpoint re-bound it).
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
    """Innermost first: each add_middleware wraps the ones added before it."""
    # Inside CORS, so 500s from unexpected exceptions still carry the CORS headers.
    # Imported here: app.core.exceptions imports this module.
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
