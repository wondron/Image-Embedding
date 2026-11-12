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

push_payloads does the same for many images at once (a category move): at most PUSH_NOW_MAX
of them during the request, in chunks; the others keep a payload_version above the ledger, so
the reconciler (app.services.retrieval.reconcile) or POST /api/scenes/sync pushes them later.
Its vector_sync adds `partial`: some were pushed, the rest are still pending.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any
from uuid import UUID

from app.bootstrap.state import ApplicationState
from app.core import errors as err
from app.core.scene_rules import SCENE_SYNC_BATCH_DEFAULT

logger = logging.getLogger("svc.vector_sync")

# Payloads pushed while the request waits; a bigger move leaves the rest to the reconciler
PUSH_NOW_MAX = 2000


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


def push_payloads(
    runtime: ApplicationState,
    image_ids: Iterable[UUID | str],
    *,
    limit: int | None = None,
    chunk: int = SCENE_SYNC_BATCH_DEFAULT,
) -> dict[str, Any]:
    """{pushed, pending, vector_sync, sync_required} after pushing many images' committed payloads.

    limit: at most this many now (None: PUSH_NOW_MAX). pending: not confirmed in the current
    collection (beyond the limit, no vector, still changing, or the push failed); they stay out
    of sync in the ledger until something pushes them.
    """
    ids = list(dict.fromkeys(str(image_id) for image_id in image_ids))
    if not ids:
        return {"pushed": 0, "pending": 0, "vector_sync": "pushed", "sync_required": False}
    try:
        stack = runtime.require_retrieval()
    except RuntimeError:
        return {"pushed": 0, "pending": len(ids), "vector_sync": "not_ready", "sync_required": True}
    pushed = 0
    status = "pushed"
    now = ids[: max(0, PUSH_NOW_MAX if limit is None else int(limit))]
    try:
        for start in range(0, len(now), chunk):
            pushed += len(stack.scene_sync_uc.push_images(now[start : start + chunk]).pushed)
    except err.AppError as exc:  # INDEX_QDRANT_FAILED / INDEX_POSTGRES_FAILED
        logger.warning("payload push failed images=%d pushed=%d | %s: %s", len(ids), pushed, exc.code, exc.detail)
        status = "failed"
    except Exception:
        logger.exception("payload push failed images=%d pushed=%d", len(ids), pushed)
        status = "failed"
    pending = len(ids) - pushed
    if status == "pushed" and pending:
        status = "partial"
    if len(ids) > len(now):
        logger.info("payload push deferred images=%d now=%d (the reconciler pushes the rest)", len(ids), len(now))
    return {"pushed": pushed, "pending": pending, "vector_sync": status, "sync_required": pending > 0}
