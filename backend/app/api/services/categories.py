"""HTTP adapter for the category tree: call the use case, shape the body, log each write.

The use case is reached through the catalog (PostgreSQL only; NOT_READY 503 while it is not built).
Failures are AppError from the rules and the store: VALIDATION_ERROR 422,
CATEGORY_NOT_FOUND 404, CATEGORY_CODE_EXISTS 409, CATEGORY_IN_USE 409,
CATEGORY_POSTGRES_FAILED 502. Routes are sync, so FastAPI runs them in the threadpool.

A move or a cuisines change recomputes the scenes of the images below the node in the store's
transaction; after the commit their payloads are pushed when the retrieval stack is up
(vector_sync.push_payloads), which never fails the request.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request

from app.api.deps import require_catalog
from app.api.services.vector_sync import push_payloads
from app.bootstrap.state import ApplicationState
from app.core.middleware import get_trace_id
from app.schemas.categories import CategoryCreateRequest, CategoryUpdateRequest

logger = logging.getLogger("svc.categories")


def list_categories(runtime: ApplicationState) -> dict[str, Any]:
    return {"ok": True, **require_catalog(runtime).categories_uc.tree()}


def create_category(
    runtime: ApplicationState, request: Request, body: CategoryCreateRequest
) -> dict[str, Any]:
    category = require_catalog(runtime).categories_uc.create(body.model_dump())
    logger.info(
        "category created id=%s code=%s parent_id=%s | trace_id=%s",
        category.get("id"), category.get("code"), category.get("parent_id"), get_trace_id(request),
    )
    return {"ok": True, "category": category}


def update_category(
    runtime: ApplicationState, request: Request, category_id: int, body: CategoryUpdateRequest
) -> dict[str, Any]:
    fields = body.model_dump(exclude_unset=True)  # absent stays unchanged; an explicit null is kept
    result = require_catalog(runtime).categories_uc.update(category_id, fields)
    category = result["category"]
    membership = result.get("scene_membership")
    scene_sync = None
    if membership is not None:
        # Committed already: the push is best effort, what is left stays out of sync in the ledger
        changed = membership["changed_ids"]
        scene_sync = {"images": membership["images"], "changed": len(changed), **push_payloads(runtime, changed)}
    logger.info(
        "category updated id=%s code=%s fields=%s%s | trace_id=%s",
        category.get("id"), category.get("code"), ",".join(sorted(fields)),
        "" if scene_sync is None else (
            " scenes: images={images} changed={changed} pushed={pushed} pending={pending} "
            "vector_sync={vector_sync}".format(**scene_sync)
        ),
        get_trace_id(request),
    )
    return {"ok": True, "category": category, "scene_sync": scene_sync}


def delete_category(runtime: ApplicationState, request: Request, category_id: int) -> dict[str, Any]:
    deleted = require_catalog(runtime).categories_uc.delete(category_id)
    logger.info(
        "category deleted id=%s code=%s | trace_id=%s",
        deleted.get("id"), deleted.get("code"), get_trace_id(request),
    )
    return {"ok": True, "deleted": deleted}
