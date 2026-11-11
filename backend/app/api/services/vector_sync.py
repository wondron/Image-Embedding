"""After a PostgreSQL write commits, bring one image's vector payload along (best effort).

Used by the manual scene memberships and the image patch / push (app.api.services
scene_memberships, images). The write itself needs the catalog only (PostgreSQL), so it is saved
while the encoder or Qdrant are down; right after the commit the image's payload is pushed to the
current collection when the retrieval stack is up, so search sees the change without a full
POST /api/scenes/sync. The push never fails the request:

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

from app.bootstrap.state import ApplicationState
from app.core import errors as err

logger = logging.getLogger("svc.vector_sync")


def push_image_payload(runtime: ApplicationState, image_id: UUID | str) -> dict[str, Any]:
    """{vector_sync, sync_required} after pushing the image's committed payload."""
    try:
        stack = runtime.require_retrieval()
    except RuntimeError:
        return {"vector_sync": "not_ready", "sync_required": True}
    # The PostgreSQL change is committed either way; the client is told to sync, the cause is logged.
    try:
        result = stack.scene_sync_uc.push_images([str(image_id)])
    except err.AppError as exc:  # INDEX_QDRANT_FAILED / INDEX_POSTGRES_FAILED
        logger.warning("payload push failed image=%s | %s: %s", image_id, exc.code, exc.detail)
        return {"vector_sync": "failed", "sync_required": True}
    except Exception:
        logger.exception("payload push failed image=%s", image_id)
        return {"vector_sync": "failed", "sync_required": True}
    if result.pushed:
        return {"vector_sync": "pushed", "sync_required": False}
    if result.missing:
        return {"vector_sync": "missing", "sync_required": True}
    return {"vector_sync": "failed", "sync_required": True}
