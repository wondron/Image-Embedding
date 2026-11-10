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
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.core import errors as err
from app.core.scene_rules import SCENE_SYNC_BATCH_DEFAULT, SCENE_SYNC_BATCH_MAX
from app.repositories.ports import MetadataStorePort, VectorStorePort
from app.services.retrieval.active_model import ActiveModel
from app.services.retrieval.scene_membership import build_qdrant_payload

logger = logging.getLogger("retrieval.sync_scenes")


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


class SyncSceneMembershipsUseCase:
    def __init__(self, metadata: MetadataStorePort, vectors: VectorStorePort, *, model: ActiveModel) -> None:
        self._metadata = metadata
        self._vectors = vectors
        self._model = model

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
        payloads: dict[str, dict[str, Any]] = {}
        versions: dict[str, int] = {}
        for row in self._metadata.get_payload_rows(image_ids) if image_ids else []:
            payloads[row["id"]] = build_qdrant_payload(
                category_id=row.get("category_id"),
                scene_codes=row.get("scenes") or [],
                food_state=row.get("food_state"),
                label_status=row.get("label_status"),
            )
            versions[row["id"]] = int(row["payload_version"])
        missing = self._vectors.set_payloads(payloads) if payloads else []
        pushed = {image_id: version for image_id, version in versions.items() if image_id not in set(missing)}
        self._metadata.mark_payloads_pushed(model_id=self._model.id, versions=pushed)
        done = len(image_ids) < size
        result = {
            "processed": len(image_ids),
            "membership_changed": len(batch["changed_ids"]),
            "payload_pushed": len(pushed),
            "payload_missing": missing,
            "next_after": None if done else batch["last_id"],
            "done": done,
        }
        logger.info(
            "scene sync batch after=%s processed=%d changed=%d pushed=%d missing=%d collection=%s",
            start, result["processed"], result["membership_changed"], result["payload_pushed"],
            len(missing), self._model.collection,
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
