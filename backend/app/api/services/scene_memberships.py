"""Membership HTTP adapter: save in PostgreSQL, then bring the image's vector payload along.

The manual change itself needs the catalog only (PostgreSQL), so it is saved while the encoder
or Qdrant are down. Right after the commit the image's payload is pushed to the current
collection when the retrieval stack is up, so search sees the change without a full
POST /api/scenes/sync. The push is best effort and never fails the request:

    vector_sync   sync_required   meaning
    pushed        false           the current collection has the new payload
    missing       true            the image has no vector in the current collection (index it again)
    not_ready     true            the retrieval stack is not built (NOT_READY); run the sync later
    failed        true            Qdrant or the ledger failed, or the image kept changing (logged)
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import Request

from app.api.deps import require_catalog
from app.bootstrap.state import ApplicationState
from app.core import errors as err
from app.core.middleware import get_trace_id
from app.schemas.scene_memberships import ManualSceneMembershipRequest

logger = logging.getLogger("svc.scene_memberships")


def _push_payload(runtime: ApplicationState, image_id: UUID) -> dict[str, Any]:
    """{vector_sync, sync_required} after pushing the image's committed payload (best effort)."""
    try:
        stack = runtime.require_retrieval()
    except RuntimeError:
        return {"vector_sync": "not_ready", "sync_required": True}
    # The PostgreSQL change is committed either way; the client is told to sync, the cause is logged.
    try:
        result = stack.scene_sync_uc.push_images([str(image_id)])
    except err.AppError as exc:  # INDEX_QDRANT_FAILED / INDEX_POSTGRES_FAILED
        logger.warning("scene membership payload push failed image=%s | %s: %s", image_id, exc.code, exc.detail)
        return {"vector_sync": "failed", "sync_required": True}
    except Exception:
        logger.exception("scene membership payload push failed image=%s", image_id)
        return {"vector_sync": "failed", "sync_required": True}
    if result.pushed:
        return {"vector_sync": "pushed", "sync_required": False}
    if result.missing:
        return {"vector_sync": "missing", "sync_required": True}
    return {"vector_sync": "failed", "sync_required": True}


def _written(
    runtime: ApplicationState, request: Request, image_id: UUID, scene_id: int, action: str, snapshot: dict[str, Any]
) -> dict[str, Any]:
    sync = _push_payload(runtime, image_id)
    logger.info(
        "scene membership %s image=%s scene_id=%s scenes=%s payload_version=%s vector_sync=%s | trace_id=%s",
        action, image_id, scene_id, ",".join(snapshot.get("scenes") or []), snapshot.get("payload_version"),
        sync["vector_sync"], get_trace_id(request),
    )
    return {"ok": True, **snapshot, **sync}


def get_memberships(runtime: ApplicationState, image_id: UUID) -> dict[str, Any]:
    return {"ok": True, **require_catalog(runtime).scenes_uc.memberships(image_id)}


def set_membership(
    runtime: ApplicationState, request: Request, image_id: UUID, scene_id: int, body: ManualSceneMembershipRequest
) -> dict[str, Any]:
    snapshot = require_catalog(runtime).scenes_uc.set_membership(image_id, scene_id, is_member=body.is_member)
    action = "include" if body.is_member else "exclude"
    return _written(runtime, request, image_id, scene_id, action, snapshot)


def clear_membership(runtime: ApplicationState, request: Request, image_id: UUID, scene_id: int) -> dict[str, Any]:
    snapshot = require_catalog(runtime).scenes_uc.clear_membership(image_id, scene_id)
    return _written(runtime, request, image_id, scene_id, "clear", snapshot)
