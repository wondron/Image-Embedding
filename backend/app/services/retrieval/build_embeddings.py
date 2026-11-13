"""Fill one model version's collection from the stored originals (POST /api/embedding-models/{id}/build).

One batch (run_batch):

    the next images in id order with no ledger row for the version (no vector in its collection)
    -> read each original from object storage -> the version's encoder -> upsert the point with
    the payload PostgreSQL gives now -> one ledger row per point (record_embeddings)

What fails for one image (its original cannot be read or decoded, the vector is invalid) is a
failure of that image: the batch goes on, and the caller leaves the id out of the rest of its
run. What fails for every image (Qdrant, PostgreSQL) raises, so the caller stops. An image
deleted while it was embedded has its point removed again, so the collection keeps no orphan.

push_payloads catches the version's payloads up: images changed after their vector was written
(edits only push to the current collection) have a ledger row behind their payload_version.

The use case holds no progress: the ledger is the progress, so a stopped build picks up where it
stopped and a second one never writes an image twice in the same pass.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.core import errors as err
from app.core.rules.scene_membership import build_qdrant_payload
from app.repositories.ports import EmbeddingModelStorePort, ImageStoragePort, MetadataStorePort, VectorStorePort
from app.services.retrieval.active_model import ActiveModel
from app.services.retrieval.embedding_validate import validate_embedding
from app.services.retrieval.sync_scenes import SyncSceneMembershipsUseCase

logger = logging.getLogger("retrieval.build")

BUILD_BATCH = 16
VERIFY_BATCH = 256
# Codes that belong to one image: the build records them and goes on
IMAGE_FAILURES = frozenset({"IMAGE_FILE_UNAVAILABLE", "INDEX_BAD_IMAGE", "INDEX_BAD_EMBEDDING"})


@dataclass
class LedgerCheck:
    """checked: ledger rows looked at; forgotten: rows deleted because their point is not in the
    collection; last_id: where the next slice starts (None: the end was reached)."""

    checked: int = 0
    forgotten: int = 0
    last_id: str | None = None
    done: bool = False


def verify_ledger(
    model_id: int,
    vectors: Any,
    models: EmbeddingModelStorePort,
    *,
    after: str | None = None,
    limit: int | None = None,
    batch: int = VERIFY_BATCH,
    should_stop: Any = None,
) -> LedgerCheck:
    """The ledger says which images have a vector in a version's collection; check that their
    points really are there, and forget the rows whose point is gone (a collection lost and made
    anew, points removed by hand), so the images count as without a vector again and a build or
    the reconciler embeds them. limit: at most this many rows from `after` (a slice); None: all.

    Points are written before their ledger row, so a row is never checked ahead of its point.
    """
    result = LedgerCheck(last_id=after)
    while not (should_stop and should_stop()):
        size = batch if limit is None else min(batch, limit - result.checked)
        if size <= 0:
            break
        ids = models.list_embedded_ids(model_id, after=result.last_id, limit=size)
        present = vectors.existing_ids(ids) if ids else set()
        gone = [image_id for image_id in ids if image_id not in present]
        if gone:
            result.forgotten += models.forget_embeddings(model_id, gone)
            logger.warning("version %s: %d image(s) in the ledger have no point in the collection; listed for embedding again", model_id, len(gone))
        result.checked += len(ids)
        if len(ids) < size:
            result.last_id, result.done = None, True
            break
        result.last_id = ids[-1]
    return result


@dataclass
class BuildBatch:
    """embedded: ids now in the collection; failures: [{image_id, code, detail}]; gone: deleted
    meanwhile; last_id: where the next batch starts (None: nothing was left); done: no image
    after last_id lacks a vector."""

    embedded: list[str] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)
    gone: list[str] = field(default_factory=list)
    last_id: str | None = None
    done: bool = True


class BuildEmbeddingsUseCase:
    def __init__(
        self,
        *,
        model: ActiveModel,
        encoder: Any,
        vectors: VectorStorePort,
        metadata: MetadataStorePort,
        models: EmbeddingModelStorePort,
        storage: ImageStoragePort,
    ) -> None:
        self.model = model
        self._encoder = encoder
        self._vectors = vectors
        self._metadata = metadata
        self._models = models
        self._storage = storage

    def _embed(self, row: dict[str, Any]) -> list[float]:
        url = str(row.get("original_url") or "")
        if not url:
            raise err.image_file_unavailable(f"image {row['id']} has no original_url")
        data = self._storage.read_by_url_or_key(url)
        try:
            vector = self._encoder.embed_bytes(data)
        except err.AppError:
            raise
        except Exception as exc:
            raise err.index_bad_image(f"embed failed: {exc}") from exc
        return validate_embedding(vector, for_search=False, expected_dim=self.model.dim)

    def run_batch(self, *, after: str | None = None, limit: int = BUILD_BATCH, exclude: list[str] | None = None) -> BuildBatch:
        rows = self._models.list_embedding_backlog(self.model.id, after=after, limit=limit, exclude=exclude)
        if not rows:
            return BuildBatch(done=True)
        result = BuildBatch(last_id=str(rows[-1]["id"]), done=len(rows) < limit)
        payload_rows = {row["id"]: row for row in self._metadata.get_payload_rows([str(r["id"]) for r in rows])}
        versions: dict[str, int] = {}
        for row in rows:
            image_id = str(row["id"])
            current = payload_rows.get(image_id)
            if current is None:  # deleted since the backlog was read
                result.gone.append(image_id)
                continue
            try:
                vector = self._embed(row)
            except err.AppError as exc:
                if exc.code not in IMAGE_FAILURES:
                    raise
                result.failures.append({"image_id": image_id, "code": exc.code, "detail": err.client_detail(exc)})
                logger.warning("build %s: image %s skipped | %s: %s", self.model.space_id, image_id, exc.code, exc.detail)
                continue
            payload = build_qdrant_payload(
                category_id=current.get("category_id"),
                scene_codes=current.get("scenes") or [],
                food_state=current.get("food_state"),
                label_status=current.get("label_status"),
            )
            self._vectors.upsert(image_id, vector, payload)  # INDEX_QDRANT_FAILED stops the build
            versions[image_id] = int(current["payload_version"])
        recorded = set(self._models.record_embeddings(model_id=self.model.id, versions=versions))
        orphans = sorted(set(versions) - recorded)
        for image_id in orphans:  # deleted after its point was written: take the point out again
            self._vectors.delete(image_id)
        result.gone.extend(orphans)
        result.embedded = sorted(recorded)
        return result

    def push_payloads(self, *, limit: int = 200, should_stop: Any = None) -> int:
        """Push the payloads that changed after their vector was written; returns how many."""
        sync = SyncSceneMembershipsUseCase(self._metadata, self._vectors, model=self.model)
        pushed, after = 0, None
        while not (should_stop and should_stop()):
            ids = self._metadata.list_unsynced_payload_ids(after=after, limit=limit, model_id=self.model.id)
            if not ids:
                break
            pushed += len(sync.push_images(ids).pushed)
            after = ids[-1]
            if len(ids) < limit:
                break
        return pushed
