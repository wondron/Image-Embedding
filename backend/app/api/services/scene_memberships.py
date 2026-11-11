"""Membership HTTP adapter: save in PostgreSQL, then bring the image's vector payload along.

The manual change itself needs the catalog only (PostgreSQL), so it is saved while the encoder
or Qdrant are down. Right after the commit the image's payload is pushed to the current
collection when the retrieval stack is up (app.api.services.vector_sync, which lists what
vector_sync / sync_required mean); the push never fails the request.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import Request

from app.api.deps import require_catalog
from app.api.services.vector_sync import push_image_payload
from app.bootstrap.state import ApplicationState
from app.core.middleware import get_trace_id
from app.schemas.scene_memberships import ManualSceneMembershipRequest

logger = logging.getLogger("svc.scene_memberships")


def _written(
    runtime: ApplicationState, request: Request, image_id: UUID, scene_id: int, action: str, snapshot: dict[str, Any]
) -> dict[str, Any]:
    sync = push_image_payload(runtime, image_id)
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
