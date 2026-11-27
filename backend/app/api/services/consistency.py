"""数据核对的 HTTP 适配层（app.bootstrap.consistency）。

核对没创建（应用还在启动）或者没有 PostgreSQL 时为 NOT_READY 503。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request

from app.bootstrap.state import ApplicationState
from app.core import errors
from app.core.middleware import get_trace_id
from app.services.retrieval.consistency import CheckOptions

logger = logging.getLogger("svc.consistency")


def _checker(runtime: ApplicationState) -> Any:
    checker = runtime.consistency
    if checker is None:
        raise errors.not_ready("the consistency check is not available")
    return checker


def get_status(runtime: ApplicationState) -> dict[str, Any]:
    return {"ok": True, "check": _checker(runtime).status()}


def start_check(runtime: ApplicationState, request: Request, options: CheckOptions) -> dict[str, Any]:
    if options.content and not options.files:
        raise errors.validation_error("content needs files: the originals are checked while the files are")
    if not (options.vectors or options.files or options.encoders):
        raise errors.validation_error("choose at least one of vectors, files and encoders")
    check = _checker(runtime).start(options)
    logger.info("consistency check started options=%s | trace_id=%s", options.as_dict(), get_trace_id(request))
    return {"ok": True, "check": check}


def stop_check(runtime: ApplicationState, request: Request) -> dict[str, Any]:
    check = _checker(runtime).stop()
    logger.info("consistency check stop requested | trace_id=%s", get_trace_id(request))
    return {"ok": True, "check": check}
