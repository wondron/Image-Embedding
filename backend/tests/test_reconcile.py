"""Reconciliation rounds (app.services.retrieval.reconcile), the API's background thread
(app.bootstrap.reconciler) and RECONCILE_CONFIG, over in-memory fakes. The SQL (the ledger query
and the advisory lock) is checked in test_pg_live."""

import asyncio
import threading
import time
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.bootstrap.reconciler import BackgroundReconciler, start_reconciler
from app.bootstrap.state import ApplicationState
from app.core.config.settings import ReconcileSettings
from app.services.retrieval.reconcile import RECONCILE_LOCK_KEY, ReconcileLimits, ReconcileUseCase
from app.services.retrieval.sync_scenes import PushResult


def ids(*numbers):
    return [f"00000000-0000-4000-8000-{n:012d}" for n in numbers]


class FakeMetadata:
    """The ledger as a sorted list of ids that are behind, the deletion queue as a count."""

    def __init__(self, behind=(), pending=0):
        self.behind = sorted(behind)
        self.pending = pending
        self.lock_free = True
        self.locks = []
        self.queries = []

    def list_unsynced_payload_ids(self, *, after=None, until=None, limit=200):
        self.queries.append((after, until, limit))
        rows = [i for i in self.behind if (after is None or i > after) and (until is None or i <= until)]
        return rows[:limit]

    @contextmanager
    def try_maintenance_lock(self, key):
        self.locks.append(key)
        yield self.lock_free

    def count_pending_deletions(self):
        return self.pending


class FakeSync:
    """Pushes catch an image up, except `missing` ones (no point in the collection)."""

    def __init__(self, metadata, missing=()):
        self.metadata = metadata
        self.missing = set(missing)
        self.calls = []

    def push_images(self, image_ids):
        self.calls.append(list(image_ids))
        pushed = {i: 2 for i in image_ids if i not in self.missing}
        self.metadata.behind = [i for i in self.metadata.behind if i not in pushed]
        return PushResult(pushed=pushed, missing=[i for i in image_ids if i in self.missing])


class FakeDeletions:
    """A queue, oldest first; `failing` rows stay."""

    def __init__(self, metadata, queue=(), failing=()):
        self.metadata = metadata
        self.queue = list(queue)
        self.failing = set(failing)
        self.calls = []
        metadata.pending = len(self.queue)

    def run_once(self, *, limit=50):
        self.calls.append(limit)
        batch = self.queue[:limit]
        processed = [i for i in batch if i not in self.failing]
        self.queue = [i for i in self.queue if i not in processed]
        self.metadata.pending = len(self.queue)
        failed = [{"image_id": i, "code": "IMAGE_STORAGE_FAILED", "detail": "storage down"} for i in batch if i in self.failing]
        return {"ok": not failed, "scanned": len(batch), "processed": processed, "failed": failed}


def use_case(metadata, *, missing=(), queue=(), failing=(), **limits):
    sync = FakeSync(metadata, missing)
    deletions = FakeDeletions(metadata, queue, failing)
    return ReconcileUseCase(metadata, sync=sync, deletions=deletions, limits=ReconcileLimits(**limits)), sync, deletions


class ReconcileRoundTests(unittest.TestCase):
    def test_a_round_pushes_what_the_ledger_has_behind_and_drains_the_deletion_queue(self):
        metadata = FakeMetadata(behind=ids(1, 2, 3))
        uc, sync, deletions = use_case(metadata, queue=ids(7, 8))
        with self.assertLogs("retrieval.reconcile", level="INFO") as logs:
            result = uc.run_once()
        self.assertEqual((result.skipped, result.cursor), (None, None))  # a whole lap: start over next time
        self.assertEqual(vars(result.payloads), {"scanned": 3, "pushed": 3, "missing": 0, "unconfirmed": 0})
        self.assertEqual((result.deletions.processed, result.deletions.failed, result.deletions.remaining), (2, [], 0))
        self.assertEqual((metadata.behind, deletions.queue, metadata.locks), ([], [], [RECONCILE_LOCK_KEY]))
        self.assertIn("payloads: scanned=3 pushed=3", logs.output[-1])

    def test_a_quiet_round_logs_at_debug_only(self):
        uc, _, _ = use_case(FakeMetadata())
        with self.assertLogs("retrieval.reconcile", level="DEBUG") as logs:
            self.assertFalse(uc.run_once().did_something)
        self.assertTrue(all(line.startswith("DEBUG") for line in logs.output))

    def test_rounds_are_bounded_and_carry_on_where_the_last_one_stopped(self):
        metadata = FakeMetadata(behind=ids(1, 2, 3, 4, 5))
        uc, sync, _ = use_case(metadata, payload_batch=2, max_payloads=2)
        first = uc.run_once()
        self.assertEqual((first.payloads.pushed, first.cursor), (2, ids(2)[0]))
        second = uc.run_once(cursor=first.cursor)
        self.assertEqual((second.payloads.pushed, second.cursor), (2, ids(4)[0]))
        third = uc.run_once(cursor=second.cursor)
        self.assertEqual((third.payloads.pushed, third.cursor), (1, None))  # the rest, then the lap ends
        self.assertEqual(sync.calls, [ids(1, 2), ids(3, 4), ids(5)])

    def test_images_that_keep_failing_never_starve_the_others(self):
        # 1 and 2 have no point in the collection: every push of them comes back missing
        metadata = FakeMetadata(behind=ids(1, 2, 3, 4, 5, 6))
        uc, sync, _ = use_case(metadata, missing=ids(1, 2), payload_batch=2, max_payloads=2)
        cursor = None
        with self.assertLogs("retrieval.reconcile", level="WARNING"):
            for _ in range(3):
                cursor = uc.run_once(cursor=cursor).cursor
        self.assertEqual(metadata.behind, ids(1, 2))
        # The next lap wraps around to them once, up to where it started
        with self.assertLogs("retrieval.reconcile", level="WARNING"):
            result = uc.run_once(cursor=cursor)
        self.assertEqual((result.payloads.scanned, result.payloads.missing), (2, 2))
        self.assertEqual(metadata.queries[-2:], [(ids(6)[0], None, 2), (None, ids(6)[0], 2)])

    def test_a_round_another_process_is_running_is_skipped(self):
        metadata = FakeMetadata(behind=ids(1))
        metadata.lock_free = False
        uc, sync, deletions = use_case(metadata, queue=ids(9))
        result = uc.run_once(cursor="keep")
        self.assertEqual((result.skipped, result.cursor), ("locked", "keep"))
        self.assertEqual((sync.calls, deletions.calls), ([], []))

    def test_a_stop_request_ends_the_round_at_the_next_batch(self):
        metadata = FakeMetadata(behind=ids(1, 2, 3, 4))
        uc, sync, deletions = use_case(metadata, queue=ids(9), payload_batch=1)
        result = uc.run_once(should_stop=lambda: len(sync.calls) >= 2)
        self.assertEqual((result.payloads.pushed, result.cursor), (2, ids(2)[0]))
        self.assertEqual(deletions.calls, [])

    def test_deletions_stop_at_a_batch_that_cleans_nothing(self):
        metadata = FakeMetadata()
        queue = ids(11, 12, 13, 14, 15)
        uc, _, deletions = use_case(metadata, queue=queue, failing=ids(11, 12), deletion_batch=2, max_deletions=100)
        result = uc.run_once()
        # [11, 12] fail: nothing cleaned in the first batch, so the round does not retry them
        self.assertEqual(deletions.calls, [2])
        self.assertEqual((result.deletions.processed, result.deletions.failed, result.deletions.remaining), (0, ids(11, 12), 5))

        uc, _, deletions = use_case(metadata, queue=queue, failing=ids(12), deletion_batch=2, max_deletions=100)
        result = uc.run_once()
        self.assertEqual((result.deletions.processed, result.deletions.failed, result.deletions.remaining), (4, ids(12), 1))
        # [11, 12] [12, 13] [12, 14] [12, 15] each clean one; [12] alone cleans nothing: stop
        self.assertEqual(deletions.calls, [2, 2, 2, 2, 2])


class ReconcileSettingsTests(unittest.TestCase):
    def test_defaults_and_overrides(self):
        self.assertEqual(ReconcileSettings.from_config({}), ReconcileSettings())
        settings = ReconcileSettings.from_config({"RECONCILE_CONFIG": {"enabled": "false", "interval_seconds": 60, "max_payloads": "500"}})
        self.assertEqual((settings.enabled, settings.interval_seconds, settings.max_payloads), (False, 60.0, 500))

    def test_values_that_would_hammer_the_stores_are_refused(self):
        for bad in ({"interval_seconds": 1}, {"max_payloads": 0}, {"max_deletions": -1}, {"initial_delay_seconds": -5}, {"interval_seconds": "soon"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ReconcileSettings.from_config({"RECONCILE_CONFIG": bad})


def runtime_with(stack=None, **config):
    runtime = ApplicationState(
        config={"RECONCILE_CONFIG": config} if config else {}, config_info={}, http_client=AsyncMock(),
        settings=SimpleNamespace(), retrieval=stack, retrieval_error=None if stack else "retrieval stack is not initialized",
    )
    return runtime


def fake_stack(behind=(), queue=()):
    metadata = FakeMetadata(behind=behind)
    return SimpleNamespace(
        infra=SimpleNamespace(metadata=metadata),
        scene_sync_uc=FakeSync(metadata),
        deletion_worker=FakeDeletions(metadata, queue),
    )


class BackgroundReconcilerTests(unittest.TestCase):
    def test_a_round_needs_the_retrieval_stack(self):
        reconciler = BackgroundReconciler(runtime_with(), ReconcileSettings())
        self.assertIsNone(reconciler.run_round())
        self.assertEqual(reconciler.last_round["skipped"], "not_ready")

    def test_rounds_carry_the_cursor_and_a_new_stack_starts_over(self):
        stack = fake_stack(behind=ids(1, 2, 3))
        runtime = runtime_with(stack)
        reconciler = BackgroundReconciler(runtime, ReconcileSettings(max_payloads=2))
        self.assertEqual(reconciler.run_round().cursor, ids(2)[0])
        self.assertEqual(reconciler.last_round["payloads"]["pushed"], 2)
        runtime.retrieval = fake_stack(behind=ids(1, 2))
        reconciler.run_round()
        self.assertEqual(runtime.retrieval.scene_sync_uc.calls, [ids(1, 2)])  # from the first image again

    def test_a_switch_made_by_another_process_is_followed_before_the_round(self):
        old = fake_stack(behind=ids(1))
        old.model = SimpleNamespace(id=1, space_id="dino:v1")
        old.infra.metadata.get_current_embedding_model = Mock(return_value={"id": 2, "name": "dino", "version": "v2"})
        new = fake_stack(behind=ids(1, 2))
        new.model = SimpleNamespace(id=2, space_id="dino:v2")
        runtime = runtime_with(old)

        def activate(model_id, *, allow_missing):
            runtime.retrieval = new
            return {"switched": True}

        runtime.models = SimpleNamespace(activate=Mock(side_effect=activate))
        reconciler = BackgroundReconciler(runtime, ReconcileSettings())
        with self.assertLogs("reconciler", level="WARNING"):
            reconciler.run_round()
        runtime.models.activate.assert_called_once_with(2, allow_missing=True)
        self.assertEqual(new.scene_sync_uc.calls, [ids(1, 2)])  # the round ran on the followed stack
        self.assertEqual(old.scene_sync_uc.calls, [])
        # in step with PostgreSQL: nothing to follow
        new.infra.metadata.get_current_embedding_model = Mock(return_value={"id": 2})
        reconciler.run_round()
        runtime.models.activate.assert_called_once()

    def test_a_switch_that_cannot_be_followed_leaves_the_round_on_the_old_stack(self):
        old = fake_stack(behind=ids(1))
        old.model = SimpleNamespace(id=1, space_id="dino:v1")
        old.infra.metadata.get_current_embedding_model = Mock(return_value={"id": 2})
        runtime = runtime_with(old)
        runtime.models = SimpleNamespace(activate=Mock(side_effect=RuntimeError("weights gone")))
        reconciler = BackgroundReconciler(runtime, ReconcileSettings())
        with self.assertLogs("reconciler", level="ERROR"):
            reconciler.run_round()
        self.assertEqual(old.scene_sync_uc.calls, [ids(1)])

    def test_a_failing_round_is_logged_and_the_next_one_tries_again(self):
        stack = fake_stack(behind=ids(1))
        stack.scene_sync_uc.push_images = Mock(side_effect=RuntimeError("qdrant gone"))
        reconciler = BackgroundReconciler(runtime_with(stack), ReconcileSettings())
        with self.assertLogs("reconciler", level="ERROR"):
            self.assertIsNone(reconciler.run_round())
        self.assertEqual(reconciler.last_round["error"], "RuntimeError: qdrant gone")

    def test_the_thread_runs_a_first_round_and_stops_promptly(self):
        stack = fake_stack(behind=ids(1), queue=ids(9))
        reconciler = BackgroundReconciler(runtime_with(stack), ReconcileSettings(initial_delay_seconds=0, interval_seconds=3600))
        with self.assertLogs("reconciler", level="INFO"):
            reconciler.start()
            deadline = time.monotonic() + 5
            while reconciler.last_round is None and time.monotonic() < deadline:
                time.sleep(0.01)
            started = time.monotonic()
            reconciler.stop()
        self.assertLess(time.monotonic() - started, 2)  # the hour-long wait is interrupted
        self.assertEqual(stack.infra.metadata.behind, [])
        self.assertEqual(stack.deletion_worker.queue, [])
        self.assertFalse(any(t.name == "reconciler" and t.is_alive() for t in threading.enumerate()))

    def test_start_follows_the_config_and_needs_retrieval(self):
        with self.assertLogs("reconciler", level="INFO") as logs:
            self.assertIsNone(start_reconciler(runtime_with(fake_stack(), enabled=False)))
            self.assertIsNone(start_reconciler(runtime_with(None)))
        self.assertIn("RECONCILE_CONFIG.enabled is false", logs.output[0])
        with self.assertLogs("reconciler", level="ERROR"):
            self.assertIsNone(start_reconciler(runtime_with(fake_stack(), interval_seconds=0)))
        runtime = runtime_with(fake_stack(), initial_delay_seconds=3600)
        with self.assertLogs("reconciler", level="INFO"):
            reconciler = start_reconciler(runtime)
            self.assertIsInstance(reconciler, BackgroundReconciler)
            reconciler.stop()

    def test_closing_the_application_stops_the_reconciler_before_the_stack(self):
        order = []
        stack = Mock()
        stack.close.side_effect = lambda: order.append("stack closed")
        runtime = runtime_with(stack)
        runtime.reconciler = Mock()
        runtime.reconciler.stop.side_effect = lambda: order.append(f"reconciler stopped (stopping={runtime.stopping})")
        asyncio.run(runtime.close())
        self.assertEqual(order, ["reconciler stopped (stopping=True)", "stack closed"])
        self.assertIsNone(runtime.reconciler)


if __name__ == "__main__":
    unittest.main()
