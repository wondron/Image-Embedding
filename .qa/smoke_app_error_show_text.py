# -*- coding: utf-8 -*-
"""Smoke: AppError.show_text logging vs client envelope."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta

from starlette.requests import Request

from app.core.errors import AppError, is_internal
from app.core.exceptions import app_error_response

SH = timezone(timedelta(hours=8))


def fake_request(path="/api/search", method="POST") -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    return Request(scope)


def capture_logs(fn):
    logger = logging.getLogger("api.errors")
    records: list[logging.LogRecord] = []

    class H(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = H()
    prev = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        result = fn()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev)
    return result, records


def body_of(resp) -> dict:
    return json.loads(bytes(resp.body).decode("utf-8"))


items: list[dict] = []


def check(item_id: str, ok: bool, detail: str) -> None:
    items.append({"id": item_id, "result": "PASS" if ok else "FAIL", "detail": detail})
    print(("PASS" if ok else "FAIL"), item_id + ":", detail)


ALLOWED = {"ok", "error", "detail", "trace_id", "errors"}


def envelope_ok(body: dict) -> bool:
    return set(body) <= ALLOWED and "show_text" not in body and body.get("ok") is False and "error" in body and "detail" in body and "trace_id" in body


req = fake_request()

e = AppError("SEARCH_BAD_IMAGE", "unsupported image format", show_text="请上传 jpg/png")
check("ST1", e.show_text == "请上传 jpg/png", repr(e.show_text))

e2 = AppError("SEARCH_BAD_IMAGE", "unsupported image format")
check("ST2", e2.show_text == "", "omitted -> " + repr(e2.show_text))

e3 = AppError("SEARCH_BAD_IMAGE", "x", show_text="")
check("ST3", e3.show_text == "", "empty -> " + repr(e3.show_text))

e4 = AppError("SEARCH_BAD_IMAGE", "x", show_text=None)
check("ST4", e4.show_text == "", "None -> " + repr(e4.show_text))

exc = AppError("SEARCH_BAD_IMAGE", "unsupported image format", show_text="请上传 jpg/png")
resp, recs = capture_logs(lambda: app_error_response(req, exc))
msg = recs[0].getMessage() if recs else ""
body = body_of(resp)
check("L1", "| show_text=请上传 jpg/png" in msg, msg)
check("L1_level", bool(recs) and recs[0].levelno == logging.INFO, recs[0].levelname if recs else "no-log")
check("E1", envelope_ok(body) and body.get("error") == "SEARCH_BAD_IMAGE", json.dumps(body, ensure_ascii=False))

exc = AppError("SEARCH_BAD_IMAGE", "unsupported image format")
resp, recs = capture_logs(lambda: app_error_response(req, exc))
msg = recs[0].getMessage() if recs else ""
body = body_of(resp)
check("L2", "show_text=" not in msg and "unsupported image format" in msg, msg)
check("E2", envelope_ok(body), json.dumps(body, ensure_ascii=False))

exc = AppError("SEARCH_BAD_IMAGE", "unsupported image format", show_text="")
resp, recs = capture_logs(lambda: app_error_response(req, exc))
msg = recs[0].getMessage() if recs else ""
check("L3", "show_text=" not in msg, msg)

exc = AppError("SEARCH_BAD_IMAGE", "unsupported image format", show_text=None)
resp, recs = capture_logs(lambda: app_error_response(req, exc))
msg = recs[0].getMessage() if recs else ""
check("L4", "show_text=" not in msg, msg)

try:
    raise AppError("INTERNAL_ERROR", "db boom", show_text="请稍后重试")
except AppError as raised:
    resp, recs = capture_logs(lambda: app_error_response(req, raised))
r = recs[0] if recs else None
check("I1_level", r is not None and r.levelno == logging.ERROR, getattr(r, "levelname", "no-log"))
check("I1_stack", r is not None and bool(r.exc_info), "exc_info=" + str(bool(r.exc_info) if r else None))
check("I1_show", r is not None and "| show_text=请稍后重试" in r.getMessage(), r.getMessage() if r else "")
body = body_of(resp)
check("I1_envelope", envelope_ok(body) and body.get("error") == "INTERNAL_ERROR" and "show_text" not in body, json.dumps(body, ensure_ascii=False))

try:
    raise AppError("INTERNAL_ERROR", "db boom")
except AppError as raised:
    resp, recs = capture_logs(lambda: app_error_response(req, raised))
msg = recs[0].getMessage() if recs else ""
check("I2", "show_text=" not in msg and recs[0].levelno == logging.ERROR, msg)

exc = AppError("VECTOR_DIM_MISMATCH", "dim mismatch", show_text="向量维度不一致")
resp, recs = capture_logs(lambda: app_error_response(req, exc))
r = recs[0] if recs else None
check(
    "W1_level",
    r is not None and r.levelno == logging.WARNING and not is_internal(exc) and exc.http_status >= 500,
    f"level={getattr(r, 'levelname', None)} internal={is_internal(exc)} status={exc.http_status}",
)
check("W1_nostack", r is not None and not r.exc_info, "exc_info=" + str(getattr(r, "exc_info", None)))
check("W1_show", r is not None and "| show_text=向量维度不一致" in r.getMessage(), r.getMessage() if r else "")

passed = sum(1 for i in items if i["result"] == "PASS")
failed = sum(1 for i in items if i["result"] == "FAIL")
print("SUMMARY", passed, "/", len(items), "FAIL", failed)