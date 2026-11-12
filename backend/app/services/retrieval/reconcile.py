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
2. drain the deletion queue (DeletionWorker.run_once), oldest first, at most max_deletions rows;
   it stops at a batch that cleaned nothing (the oldest rows keep failing).

Images with no ledger row for the current model are left alone: they need a vector, which a
payload push cannot give (the library's 「待同步或没有向量」 filter lists them).

Rounds run in the API process (app.bootstrap.reconciler, RECONCILE_CONFIG) or from cron
(`python -m app.workers.reconcile`). A PostgreSQL advisory lock keeps two processes from running
a round at once: the second one skips its round.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.repositories.ports import MetadataStorePort
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


@dataclass
class PayloadPass:
    scanned: int = 0
    pushed: int = 0
    missing: int = 0
    unconfirmed: int = 0


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

    @property
    def did_something(self) -> bool:
        return bool(self.payloads.scanned or self.deletions.processed or self.deletions.failed)

    def as_dict(self) -> dict[str, Any]:
        return {
            "skipped": self.skipped,
            "cursor": self.cursor,
            "payloads": vars(self.payloads),
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
    ) -> None:
        self._metadata = metadata
        self._sync = sync
        self._deletions = deletions
        self._limits = limits

    def run_once(self, *, cursor: str | None = None, should_stop: Callable[[], bool] = _never) -> RoundResult:
        """One round. should_stop is checked between batches (the API is shutting down)."""
        started = time.monotonic()
        with self._metadata.try_maintenance_lock(RECONCILE_LOCK_KEY) as held:
            if not held:
                logger.info("reconcile round skipped: another process holds the lock")
                return RoundResult(skipped="locked", cursor=cursor)
            payloads, cursor = self._push_payloads(cursor, should_stop)
            deletions = self._drain_deletions(should_stop)
        result = RoundResult(
            skipped=None, cursor=cursor, payloads=payloads, deletions=deletions,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        level = logging.INFO if result.did_something else logging.DEBUG
        logger.log(
            level,
            "reconcile round payloads: scanned=%d pushed=%d missing=%d unconfirmed=%d | "
            "deletions: processed=%d failed=%d remaining=%d | %dms",
            payloads.scanned, payloads.pushed, payloads.missing, payloads.unconfirmed,
            deletions.processed, len(deletions.failed), deletions.remaining, result.duration_ms,
        )
        return result

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
            after = ids[-1]
        if done.missing:
            logger.warning(
                "reconcile: %d image(s) have a ledger row but no point in the current collection; "
                "they need a new vector (POST /api/images/{id}/reembed)", done.missing,
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
