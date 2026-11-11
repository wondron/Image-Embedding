"""HTTP adapter for one stored image: detail, patch, delete, payload push.

PostgreSQL first, through the catalog (app.bootstrap.catalog; NOT_READY 503 while it is not
built), so edits and deletions work while the encoder or Qdrant are down. The vector side follows
when the retrieval stack is up and never fails a request whose PostgreSQL part succeeded:

- GET adds vector: the image in the current collection (services.retrieval.vector_status), or
  status=unavailable with the reason;
- PATCH and POST .../vector-sync push the committed payload: vector_sync / sync_required as for
  the scene memberships (app.api.services.vector_sync);
- DELETE removes the point and the two files at once: cleanup=done, or queued when that is not
  possible now (the image_deletions worker finishes it). The image leaves the gallery and the
  search results either way: both read PostgreSQL.
- POST .../reembed writes the point again from the stored original (services.retrieval
  reembed_image): the one write here that needs the whole retrieval stack.

Failures are AppError: IMAGE_NOT_FOUND 404, VALIDATION_ERROR 422, SEARCH_POSTGRES_FAILED /
INDEX_POSTGRES_FAILED / IMAGE_FILE_UNAVAILABLE 502, NOT_READY 503. Routes are sync, so FastAPI
runs them in the threadpool.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import Request

from app.api.deps import require_catalog, require_retrieval
from app.api.services.vector_sync import push_image_payload
from app.bootstrap.state import ApplicationState
from app.core import errors as err
from app.core.middleware import get_trace_id
from app.schemas.images import ImageUpdateRequest

logger = logging.getLogger("svc.images")


def _vector_state(runtime: ApplicationState, image: dict[str, Any]) -> dict[str, Any]:
    try:
        stack = runtime.require_retrieval()
    except RuntimeError as exc:
        return {"status": "unavailable", "detail": f"retrieval stack not ready: {exc}"}
    try:
        return stack.vector_status_uc.inspect(image)
    except Exception as exc:
        if isinstance(exc, err.AppError):
            logger.warning("vector status read failed image=%s | %s: %s", image["id"], exc.code, exc.detail)
            detail = err.client_detail(exc)
        else:
            logger.exception("vector status read failed image=%s", image["id"])
            detail = "vector store read failed"
        model = stack.model
        return {
            "status": "unavailable",
            "collection": model.collection,
            "embedding_space_id": model.space_id,
            "detail": detail,
        }


def get_image(runtime: ApplicationState, image_id: UUID) -> dict[str, Any]:
    image = require_catalog(runtime).images_uc.get(image_id)
    return {"ok": True, "image": image, "vector": _vector_state(runtime, image)}


def _after_push(runtime: ApplicationState, image_id: UUID, image: dict[str, Any], sync: dict[str, Any]) -> dict[str, Any]:
    """The image as it stands after the push: a push moves its ledger row (synced_version), so
    read it again; keep the given one when there was no push or the image went away meanwhile."""
    if sync["vector_sync"] == "not_ready":
        return image
    try:
        return require_catalog(runtime).images_uc.get(image_id)
    except err.AppError:
        return image


def update_image(
    runtime: ApplicationState, request: Request, image_id: UUID, body: ImageUpdateRequest
) -> dict[str, Any]:
    fields = body.model_dump(exclude_unset=True)  # absent stays unchanged; an explicit null is kept
    image = require_catalog(runtime).images_uc.update(image_id, fields)
    sync = push_image_payload(runtime, image_id)
    image = _after_push(runtime, image_id, image, sync)
    logger.info(
        "image updated id=%s fields=%s payload_version=%s vector_sync=%s | trace_id=%s",
        image_id, ",".join(sorted(fields)), image.get("payload_version"), sync["vector_sync"], get_trace_id(request),
    )
    # vector: what the detail would show now, so the page needs no second request
    return {"ok": True, "image": image, "vector": _vector_state(runtime, image), **sync}


def push_vector(runtime: ApplicationState, request: Request, image_id: UUID) -> dict[str, Any]:
    """Write the image's current payload to the current collection again (repairs stale)."""
    image = require_catalog(runtime).images_uc.get(image_id)  # IMAGE_NOT_FOUND before any push
    sync = push_image_payload(runtime, image_id)
    image = _after_push(runtime, image_id, image, sync)
    logger.info(
        "image payload pushed id=%s payload_version=%s vector_sync=%s | trace_id=%s",
        image_id, image.get("payload_version"), sync["vector_sync"], get_trace_id(request),
    )
    return {
        "ok": True,
        "image_id": image["id"],
        "payload_version": image["payload_version"],
        "vector": _vector_state(runtime, image),
        **sync,
    }


def reembed_image(runtime: ApplicationState, request: Request, image_id: UUID) -> dict[str, Any]:
    """Embed the stored original again and write the point (repairs vector.status=missing).
    Needs the whole retrieval stack: encoder, Qdrant and object storage (NOT_READY otherwise)."""
    require_catalog(runtime).images_uc.get(image_id)  # IMAGE_NOT_FOUND before anything else
    stack = require_retrieval(runtime)
    stack.reembed_uc.execute(image_id)
    image = require_catalog(runtime).images_uc.get(image_id)  # the ledger row moved
    logger.info(
        "image re-embedded id=%s payload_version=%s | trace_id=%s",
        image_id, image.get("payload_version"), get_trace_id(request),
    )
    return {
        "ok": True,
        "image_id": image["id"],
        "payload_version": image["payload_version"],
        "vector": _vector_state(runtime, image),
    }


def delete_image(runtime: ApplicationState, request: Request, image_id: UUID) -> dict[str, Any]:
    queued = require_catalog(runtime).images_uc.delete(image_id)
    try:
        stack = runtime.require_retrieval()
    except RuntimeError:
        stack = None
    cleanup = "done" if stack is not None and stack.deletion_worker.clean_up(queued) else "queued"
    logger.info(
        "image deleted id=%s cleanup=%s | trace_id=%s", queued.get("image_id"), cleanup, get_trace_id(request)
    )
    return {"ok": True, "image_id": str(queued["image_id"]), "deleted_at": queued.get("deleted_at"), "cleanup": cleanup}
