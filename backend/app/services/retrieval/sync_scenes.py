"""Bring stored images in line with the current scene configuration (contract §2.9 / §4.4).

Scene rules, scene flags (is_active, include_all) and the category tree decide which scenes an
image belongs to. Membership is materialized when an image is written, so after any of them
changes, stored images keep their old scenes until they are recomputed. One batch here:

1. recompute image_scenes for the next images in id order (PG; payload_version is bumped where
   the effective scene set changed);
2. write the payload of every image in the batch to the current model's collection, whether or
   not its version moved: toggling a scene changes the effective sets without bumping any
   image, and a full write also repairs payloads that drifted for any other reason;
3. record the pushed versions in image_embeddings.

The caller repeats with next_after until done: POST /api/scenes/sync (the admin page shows the
progress) or `python -m app.workers.sync_scenes`. A batch that fails half way is safe to run
again: PG changes are committed per batch, and a payload that was not written is written by
the next run.

push_images does steps 2-3 for a few images whose membership was just committed (a manual
override, app.api.services.scene_memberships), so search sees the change without a full run.

Both record a version only while it is still the image's current one
(MetadataStorePort.mark_payloads_pushed). An image written again between reading its payload
and recording it (a manual change, an index, another sync) is read and pushed again: whichever
writer pushes last then writes the newest payload, and the ledger never claims a payload the
collection does not have.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.core import errors as err
from app.core.scene_rules import SCENE_SYNC_BATCH_DEFAULT, SCENE_SYNC_BATCH_MAX
from app.repositories.ports import MetadataStorePort, VectorStorePort
from app.services.retrieval.active_model import ActiveModel
from app.services.retrieval.scene_membership import build_qdrant_payload

logger = logging.getLogger("retrieval.sync_scenes")

# Rounds of read -> write payload -> record for images changed by someone else in between
PUSH_ROUNDS = 3


def _after_id(value: Any) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return str(value if isinstance(value, UUID) else UUID(str(value).strip()))
    except (ValueError, AttributeError) as exc:
        raise err.validation_error(f"after must be an image id (uuid), got {value!r}") from exc


def _limit(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= SCENE_SYNC_BATCH_MAX:
        raise err.validation_error(f"limit must be an integer in 1..{SCENE_SYNC_BATCH_MAX}, got {value!r}")
    return value


@dataclass
class PushResult:
    """pushed: {image_id: payload_version} written and recorded; missing: no point in the current
    collection (needs embedding again); unconfirmed: still being changed by others after
    PUSH_ROUNDS (the writer that changed it pushes it, the next sync repairs it otherwise)."""

    pushed: dict[str, int] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    unconfirmed: list[str] = field(default_factory=list)


class SyncSceneMembershipsUseCase:
    def __init__(self, metadata: MetadataStorePort, vectors: VectorStorePort, *, model: ActiveModel) -> None:
        self._metadata = metadata
        self._vectors = vectors
        self._model = model

    def _push(self, image_ids: list[str]) -> PushResult:
        """Write these images' payloads (as PG has them now) to the current collection and record
        them. Images deleted meanwhile are left out of every list."""
        result = PushResult()
        pending = list(image_ids)
        for _ in range(PUSH_ROUNDS):
            if not pending:
                break
            payloads: dict[str, dict[str, Any]] = {}
            versions: dict[str, int] = {}
            for row in self._metadata.get_payload_rows(pending):
                payloads[row["id"]] = build_qdrant_payload(
                    category_id=row.get("category_id"),
                    scene_codes=row.get("scenes") or [],
                    food_state=row.get("food_state"),
                    label_status=row.get("label_status"),
                )
                versions[row["id"]] = int(row["payload_version"])
            absent = set(self._vectors.set_payloads(payloads)) if payloads else set()
            result.missing.extend(sorted(absent))
            written = {image_id: version for image_id, version in versions.items() if image_id not in absent}
            recorded = set(self._metadata.mark_payloads_pushed(model_id=self._model.id, versions=written))
            result.pushed.update({image_id: written[image_id] for image_id in recorded})
            pending = sorted(set(written) - recorded)
        result.unconfirmed = pending
        if pending:
            logger.warning(
                "payload push not confirmed after %d rounds (images changed meanwhile): %s collection=%s",
                PUSH_ROUNDS, pending, self._model.collection,
            )
        return result

    def push_images(self, image_ids: list[str]) -> PushResult:
        """Push the current payload of a few images whose membership was just committed."""
        result = self._push([str(image_id) for image_id in image_ids])
        logger.info(
            "payload push images=%d pushed=%d missing=%d unconfirmed=%d collection=%s",
            len(image_ids), len(result.pushed), len(result.missing), len(result.unconfirmed),
            self._model.collection,
        )
        return result

    def run_batch(self, *, after: Any = None, limit: Any = SCENE_SYNC_BATCH_DEFAULT) -> dict[str, Any]:
        """One batch after image id `after` (None: from the start).

        Returns {processed, membership_changed, payload_pushed, payload_missing, next_after, done};
        payload_missing lists images with no point in the current collection (they need to be
        embedded again, which this does not do).
        """
        start = _after_id(after)
        size = _limit(limit)
        batch = self._metadata.recompute_scene_memberships(after=start, limit=size)
        image_ids = batch["image_ids"]
        pushed = self._push(image_ids)
        done = len(image_ids) < size
        result = {
            "processed": len(image_ids),
            "membership_changed": len(batch["changed_ids"]),
            "payload_pushed": len(pushed.pushed),
            "payload_missing": pushed.missing,
            "next_after": None if done else batch["last_id"],
            "done": done,
        }
        logger.info(
            "scene sync batch after=%s processed=%d changed=%d pushed=%d missing=%d collection=%s",
            start, result["processed"], result["membership_changed"], result["payload_pushed"],
            len(pushed.missing), self._model.collection,
        )
        return result

    def run_all(self, *, limit: int = SCENE_SYNC_BATCH_DEFAULT) -> dict[str, Any]:
        """Every batch until done (the worker); totals of the batches."""
        totals: dict[str, Any] = {"processed": 0, "membership_changed": 0, "payload_pushed": 0, "payload_missing": [], "batches": 0}
        after: str | None = None
        while True:
            result = self.run_batch(after=after, limit=limit)
            totals["batches"] += 1
            for key in ("processed", "membership_changed", "payload_pushed"):
                totals[key] += result[key]
            totals["payload_missing"].extend(result["payload_missing"])
            if result["done"]:
                return totals
            after = result["next_after"]
