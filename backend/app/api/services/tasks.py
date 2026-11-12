"""HTTP adapter for the task center: index records and the deletion queue.

Reads and clears go to PostgreSQL through the catalog (app.bootstrap.catalog; NOT_READY 503 while
it is not built), so the history stays readable while the encoder or Qdrant are down. Draining
the deletion queue needs the vector store and the object storage: it runs the retrieval stack's
DeletionWorker (NOT_READY 503 without the stack). Routes are sync: FastAPI runs them in the
threadpool.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request

from app.api.deps import require_catalog, require_retrieval
from app.bootstrap.state import ApplicationState
from app.core.middleware import get_trace_id
from app.schemas.tasks import ImageDeletionProcessRequest

logger = logging.getLogger("svc.tasks")


def list_index_tasks(
    runtime: ApplicationState, *, status: str | None, image_state: str | None, page: int, page_size: int
) -> dict[str, Any]:
    result = require_catalog(runtime).tasks_uc.list_index_tasks(
        status=status, image_state=image_state, page=page, page_size=page_size
    )
    return {"ok": True, **result}


def clear_index_tasks(
    runtime: ApplicationState, request: Request, *, status: str | None, image_state: str | None
) -> dict[str, Any]:
    result = require_catalog(runtime).tasks_uc.clear_index_tasks(status=status, image_state=image_state)
    logger.info(
        "index task records cleared status=%s image_state=%s deleted=%s | trace_id=%s",
        result["status"], result["image_state"], result["deleted"], get_trace_id(request),
    )
    return {"ok": True, **result}


def delete_index_task(runtime: ApplicationState, request: Request, *, task_id: int) -> dict[str, Any]:
    result = require_catalog(runtime).tasks_uc.delete_index_task(task_id)
    logger.info("index task record deleted id=%s deleted=%s | trace_id=%s", result["id"], result["deleted"], get_trace_id(request))
    return {"ok": True, **result}


def list_deletions(runtime: ApplicationState, *, limit: int) -> dict[str, Any]:
    result = require_catalog(runtime).tasks_uc.list_deletions(limit=limit)
    return {"ok": True, **result, "cleanup_ready": runtime.retrieval is not None}


def process_deletions(
    runtime: ApplicationState, request: Request, body: ImageDeletionProcessRequest
) -> dict[str, Any]:
    """Clean the oldest queued deletions now; what fails stays queued (the cause is logged)."""
    tasks = require_catalog(runtime).tasks_uc
    stack = require_retrieval(runtime)
    result = stack.deletion_worker.run_once(limit=body.limit)
    failed = [
        {"image_id": item["image_id"], "error": item["code"], "detail": item["detail"]}
        for item in result["failed"]
    ]
    remaining = tasks.count_deletions()
    logger.info(
        "deletion queue processed scanned=%s processed=%s failed=%s remaining=%s | trace_id=%s",
        result["scanned"], len(result["processed"]), len(failed), remaining, get_trace_id(request),
    )
    return {
        "ok": True,
        "scanned": result["scanned"],
        "processed": list(result["processed"]),
        "failed": failed,
        "remaining": remaining,
    }
