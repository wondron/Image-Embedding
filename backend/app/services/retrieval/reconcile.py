"""Reconciliation: bring the vector store back in line with PostgreSQL, one bounded round at a time.

PostgreSQL is the source of truth. Every write that changes what a vector payload is built from
bumps images.payload_version, and the ledger (image_embeddings.synced_version) records the version
the current collection has. Writers push right after their commit, best effort; whatever did not
make it stays behind in the ledger: Qdrant was down, the process stopped in between, a category
move had more images than one request pushes. Deleted images wait in image_deletions the same way.

One round:

1. push the payloads of the images whose ledger row is behind (SyncSceneMembershipsUseCase
   .push_images), in id order starting after the cursor the previous round returned and wrapping
   around once, at most max_payloads images. Images that keep failing (no point in the collection:
   they need a new vector) are therefore visited once per lap and never starve the others;
2. check a slice of the current model's ledger against the collection's real points
   (verify_ledger, at most max_verifications rows from where the last round stopped): a row whose
   point is gone is forgotten, so the image counts as without a vector again;
3. embed images the current model has no vector for (BuildEmbeddingsUseCase.run_batch with the
   current encoder, at most max_embeddings images, in id order from where the last round
   stopped), each batch behind the vector write gate: images written to the old collection while
   a switch was about to happen, by a process that had not followed it yet, or whose point was
   lost. Images whose original cannot be read are tried again on the next lap;
4. drain the deletion queue (DeletionWorker.run_once), oldest first, at most max_deletions rows;
   it stops at a batch that cleaned nothing (the oldest rows keep failing).

Steps 2 and 3 need the current model's stack (step 3 its encoder): the CLI worker, which has no
encoder, checks the ledger but embeds nothing.

Rounds run in the API process (app.bootstrap.reconciler, RECONCILE_CONFIG) or from cron
(`python -m app.workers.reconcile`). A PostgreSQL advisory lock keeps two processes from running
a round at once: the second one skips its round.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.repositories.ports import MetadataStorePort
from app.services.retrieval.build_embeddings import BUILD_BATCH, BuildEmbeddingsUseCase, verify_ledger
from app.services.retrieval.delete_image import DeletionWorker
from app.services.retrieval.sync_scenes import SyncSceneMembershipsUseCase

logger = logging.getLogger("retrieval.reconcile")

# pg_try_advisory_lock key of a round ("RECS")
RECONCILE_LOCK_KEY = 0x52454353


@dataclass(frozen=True)
class ReconcileLimits:
    payload_batch: int = 200
    max_payloads: int = 2000
    deletion_batch: int = 50
    max_deletions: int = 200
    max_verifications: int = 2000  # ledger rows checked against the collection per round (0: off)
    max_embeddings: int = 100  # images without a vector embedded per round (0: off)


@dataclass
class PayloadPass:
    scanned: int = 0
    pushed: int = 0
    missing: int = 0
    unconfirmed: int = 0


@dataclass
class VectorPass:
    verified: int = 0
    forgotten: int = 0
    embedded: int = 0
    failed: int = 0
    gone: int = 0


@dataclass
class DeletionPass:
    processed: int = 0
    failed: list[str] = field(default_factory=list)
    remaining: int = 0


@dataclass
class RoundResult:
    """skipped: None when the round ran, else why not ('locked'). cursor: where the next round
    starts its payload pass (None: from the first image)."""

    skipped: str | None
    cursor: str | None
    payloads: PayloadPass = field(default_factory=PayloadPass)
    deletions: DeletionPass = field(default_factory=DeletionPass)
    duration_ms: int = 0
    vectors: VectorPass = field(default_factory=VectorPass)
    verify_cursor: str | None = None
    embed_cursor: str | None = None

    @property
    def did_something(self) -> bool:
        return bool(
            self.payloads.scanned or self.deletions.processed or self.deletions.failed
            or self.vectors.forgotten or self.vectors.embedded or self.vectors.failed
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "skipped": self.skipped,
            "cursor": self.cursor,
            "payloads": vars(self.payloads),
            "vectors": vars(self.vectors),
            "deletions": {**vars(self.deletions), "failed": list(self.deletions.failed)},
            "duration_ms": self.duration_ms,
        }


def _never() -> bool:
    return False


class ReconcileUseCase:
    def __init__(
        self,
        metadata: MetadataStorePort,
        *,
        sync: SyncSceneMembershipsUseCase,
        deletions: DeletionWorker,
        limits: ReconcileLimits = ReconcileLimits(),
        ledger: tuple[int, Any, Any] | None = None,
        fill: BuildEmbeddingsUseCase | None = None,
        write_gate: Callable[[], Any] | None = None,
    ) -> None:
        """ledger: (current model id, its collection, the EmbeddingModelStorePort) to check
        against the collection's points; fill: the current model's BuildEmbeddingsUseCase (None
        where there is no encoder); write_gate: held around each fill batch (the API's
        VectorWriteGate.shared), so a model version switch never races it."""
        self._metadata = metadata
        self._sync = sync
        self._deletions = deletions
        self._limits = limits
        self._ledger = ledger
        self._fill = fill
        self._write_gate = write_gate

    def run_once(
        self,
        *,
        cursor: str | None = None,
        should_stop: Callable[[], bool] = _never,
        verify_cursor: str | None = None,
        embed_cursor: str | None = None,
    ) -> RoundResult:
        """One round. should_stop is checked between batches (the API is shutting down)."""
        started = time.monotonic()
        with self._metadata.try_maintenance_lock(RECONCILE_LOCK_KEY) as held:
            if not held:
                logger.info("reconcile round skipped: another process holds the lock")
                return RoundResult(skipped="locked", cursor=cursor, verify_cursor=verify_cursor, embed_cursor=embed_cursor)
            payloads, cursor = self._push_payloads(cursor, should_stop)
            vectors, verify_cursor, embed_cursor = self._vector_pass(verify_cursor, embed_cursor, should_stop)
            deletions = self._drain_deletions(should_stop)
        result = RoundResult(
            skipped=None, cursor=cursor, payloads=payloads, deletions=deletions,
            duration_ms=int((time.monotonic() - started) * 1000),
            vectors=vectors, verify_cursor=verify_cursor, embed_cursor=embed_cursor,
        )
        level = logging.INFO if result.did_something else logging.DEBUG
        logger.log(
            level,
            "reconcile round payloads: scanned=%d pushed=%d missing=%d unconfirmed=%d | "
            "vectors: verified=%d forgotten=%d embedded=%d failed=%d | "
            "deletions: processed=%d failed=%d remaining=%d | %dms",
            payloads.scanned, payloads.pushed, payloads.missing, payloads.unconfirmed,
            vectors.verified, vectors.forgotten, vectors.embedded, vectors.failed,
            deletions.processed, len(deletions.failed), deletions.remaining, result.duration_ms,
        )
        return result

    def _vector_pass(
        self, verify_cursor: str | None, embed_cursor: str | None, should_stop: Callable[[], bool]
    ) -> tuple[VectorPass, str | None, str | None]:
        """Steps 2 and 3: a slice of the ledger checked, then a few missing vectors written."""
        limits = self._limits
        done = VectorPass()
        if self._ledger is not None and limits.max_verifications > 0 and not should_stop():
            model_id, vectors, models = self._ledger
            check = verify_ledger(
                model_id, vectors, models, after=verify_cursor, limit=limits.max_verifications, should_stop=should_stop
            )
            done.verified, done.forgotten, verify_cursor = check.checked, check.forgotten, check.last_id
        if self._fill is None or limits.max_embeddings <= 0:
            return done, verify_cursor, embed_cursor
        tried = 0
        while tried < limits.max_embeddings and not should_stop():
            gate = self._write_gate() if self._write_gate is not None else contextlib.nullcontext()
            with gate:
                batch = self._fill.run_batch(after=embed_cursor, limit=min(BUILD_BATCH, limits.max_embeddings - tried))
            tried += len(batch.embedded) + len(batch.failures) + len(batch.gone)
            done.embedded += len(batch.embedded)
            done.failed += len(batch.failures)
            done.gone += len(batch.gone)
            embed_cursor = None if batch.done else batch.last_id
            if batch.done:
                break  # the end of the id range: the next round starts from the first image
        if done.embedded or done.failed:
            logger.info(
                "reconcile: %d image(s) had no vector in the current collection and got one; %d could not be embedded",
                done.embedded, done.failed,
            )
        return done, verify_cursor, embed_cursor

    def _push_payloads(self, start: str | None, should_stop: Callable[[], bool]) -> tuple[PayloadPass, str | None]:
        """(start, end] first, then [first, start]: one lap at most, max_payloads images at most."""
        limits = self._limits
        done = PayloadPass()
        after: str | None = start
        until: str | None = None  # second leg: up to the start, inclusive
        while done.scanned < limits.max_payloads and not should_stop():
            ids = self._metadata.list_unsynced_payload_ids(
                after=after, until=until, limit=min(limits.payload_batch, limits.max_payloads - done.scanned)
            )
            if not ids:
                if start is None or until is not None:
                    return done, None  # a full lap: the next round starts from the first image
                after, until = None, start
                continue
            result = self._sync.push_images(ids)
            done.scanned += len(ids)
            done.pushed += len(result.pushed)
            done.missing += len(result.missing)
            done.unconfirmed += len(result.unconfirmed)
            if result.missing and self._ledger is not None:
                # a ledger row without its point: forget it, the vector pass embeds the image again
                model_id, _vectors, models = self._ledger
                models.forget_embeddings(model_id, list(result.missing))
            after = ids[-1]
        if done.missing:
            logger.warning(
                "reconcile: %d image(s) have a ledger row but no point in the current collection; "
                "their rows are checked and forgotten so they get a new vector", done.missing,
            )
        return done, after

    def _drain_deletions(self, should_stop: Callable[[], bool]) -> DeletionPass:
        limits = self._limits
        done = DeletionPass()
        failing: dict[str, None] = {}
        scanned = 0
        while scanned < limits.max_deletions and not should_stop():
            run = self._deletions.run_once(limit=min(limits.deletion_batch, limits.max_deletions - scanned))
            scanned += int(run.get("scanned") or 0)
            processed = [str(image_id) for image_id in run.get("processed") or []]
            done.processed += len(processed)
            for image_id in processed:
                failing.pop(image_id, None)
            for item in run.get("failed") or []:
                failing[str(item.get("image_id"))] = None
            if not processed:
                break  # empty, or the oldest rows keep failing: the next batch would retry the same
        done.failed = list(failing)
        done.remaining = self._metadata.count_pending_deletions()
        return done
