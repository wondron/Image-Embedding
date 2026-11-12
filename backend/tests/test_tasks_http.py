"""Task center: the real routes, adapter, use case and deletion worker over in-memory stores
(the SQL is in test_pg_live).

- index records: newest first, a status filter, counts over everything, the image as it is now;
- a deleted image leaves its record (image_state=deleted), listed and cleared on its own;
- clearing needs a status or an image state; one record can be deleted by its id;
- the deletion queue is readable without the retrieval stack; cleaning it needs the stack, and a
  failure stays queued with a client-safe reason (the cause only in the log).
"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.router import router as api_router
from app.api.v1 import tasks as task_routes
from app.bootstrap.state import ApplicationState
from app.core import errors as err
from app.core.exceptions import register_exception_handlers
from app.core.middleware import TraceIdMiddleware
from app.services.retrieval.delete_image import DeletionWorker
from app.services.retrieval.task_center import TaskCenterUseCase

IMG_A = "0cf457e2-4a47-9f02-fd4d-34540389f720"
IMG_B = "11111111-2222-3333-4444-555555555555"
IMG_C = "99999999-8888-7777-6666-555555555555"

RECORDS = [  # as the repository returns them, newest first
    {"id": 3, "filename": "steak.jpg", "status": "succeeded", "image_id": IMG_A, "new_image": True,
     "error_code": None, "error_detail": None, "duration_ms": 812, "trace_id": "t-3",
     "created_at": "2026-11-11T09:00:03+00:00", "image_exists": True, "thumb_url": "https://cdn/t/a.webp",
     "category_id": 12, "image_label_status": "verified", "category_name": "牛排"},
    {"id": 2, "filename": "junk.png", "status": "failed", "image_id": None, "new_image": None,
     "error_code": "INDEX_BAD_IMAGE", "error_detail": "cannot decode image", "duration_ms": 4, "trace_id": "t-2",
     "created_at": "2026-11-11T09:00:02+00:00", "image_exists": False, "thumb_url": None,
     "category_id": None, "image_label_status": None, "category_name": None},
    {"id": 1, "filename": "gone.jpg", "status": "succeeded", "image_id": IMG_B, "new_image": False,
     "error_code": None, "error_detail": None, "duration_ms": 640, "trace_id": "t-1",
     "created_at": "2026-11-11T09:00:01+00:00", "image_exists": False, "thumb_url": None,
     "category_id": None, "image_label_status": None, "category_name": None},
]


class FakeTaskStore:
    """The task-center part of CatalogStorePort, plus what DeletionWorker needs."""

    def __init__(self):
        self.records = [dict(r) for r in RECORDS]
        self.queue = {
            IMG_B: {"image_id": IMG_B, "original_url": "https://cdn/o/b.jpg", "thumb_url": "https://cdn/t/b.webp",
                    "deleted_at": "2026-11-11T08:00:00+00:00"},
            IMG_C: {"image_id": IMG_C, "original_url": "https://cdn/o/c.jpg", "thumb_url": "https://cdn/t/c.webp",
                    "deleted_at": "2026-11-11T08:30:00+00:00"},
        }
        self.calls = []

    @staticmethod
    def matches(record, status, image_state):
        if status is not None and record["status"] != status:
            return False
        if image_state is not None:
            return record["status"] == "succeeded" and record["image_exists"] == (image_state == "present")
        return True

    def list_index_tasks(self, *, status=None, image_state=None, page=1, page_size=50):
        self.calls.append(("list", status, image_state, page, page_size))
        rows = [r for r in self.records if self.matches(r, status, image_state)]
        counts = {"succeeded": 0, "failed": 0, "image_deleted": 0}
        for r in self.records:
            counts[r["status"]] += 1
            counts["image_deleted"] += r["status"] == "succeeded" and not r["image_exists"]
        start = (page - 1) * page_size
        return {"items": rows[start:start + page_size], "total": len(rows), "counts": counts}

    def clear_index_tasks(self, *, status=None, image_state=None):
        self.calls.append(("clear", status, image_state))
        before = len(self.records)
        self.records = [r for r in self.records if not self.matches(r, status, image_state)]
        return before - len(self.records)

    def delete_index_task(self, task_id):
        self.calls.append(("delete", task_id))
        before = len(self.records)
        self.records = [r for r in self.records if r["id"] != task_id]
        return before - len(self.records)

    def list_pending_deletions(self, *, limit=50):
        rows = sorted(self.queue.values(), key=lambda r: r["deleted_at"])
        return [dict(r) for r in rows[:limit]]

    def count_pending_deletions(self):
        return len(self.queue)

    # -- DeletionWorker --
    def get_by_id(self, image_id):
        return None

    def complete_deletion(self, image_id):
        self.queue.pop(str(image_id), None)


class TaskEndpointTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeTaskStore()
        self.vectors, self.storage = Mock(), Mock()
        self.runtime = ApplicationState(
            config={}, config_info={}, http_client=Mock(), settings=SimpleNamespace(),
            catalog=SimpleNamespace(tasks_uc=TaskCenterUseCase(self.store)),
            retrieval=SimpleNamespace(deletion_worker=DeletionWorker(self.store, self.vectors, self.storage)),
        )
        app = FastAPI()
        register_exception_handlers(app)
        app.add_middleware(TraceIdMiddleware)
        app.include_router(task_routes.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        p = patch.object(task_routes, "get_application_state", return_value=self.runtime)
        p.start()
        self.addCleanup(p.stop)

    def ok(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.headers.get("X-Trace-Id"))
        body = response.json()
        self.assertNotIn("trace_id", body)
        return body

    def error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.json()["error"], code)
        return response.json()

    # ---- index records -------------------------------------------------------------

    def test_records_come_newest_first_with_the_image_as_it_is_now(self):
        body = self.ok(self.client.get("/api/index-tasks"))
        self.assertEqual((body["status"], body["image_state"], body["page"], body["page_size"], body["total"]), (None, None, 1, 50, 3))
        self.assertEqual(body["counts"], {"succeeded": 2, "failed": 1, "image_deleted": 1})
        stored, failed, deleted = body["items"]
        self.assertEqual(stored["image"], {"id": IMG_A, "thumb_url": "https://cdn/t/a.webp", "category_id": 12,
                                           "category": "牛排", "label_status": "verified"})
        self.assertEqual((stored["new_image"], stored["duration_ms"], stored["trace_id"]), (True, 812, "t-3"))
        self.assertEqual(stored["image_state"], "present")
        self.assertEqual((failed["error_code"], failed["error_detail"], failed["image"], failed["image_state"]), (
            "INDEX_BAD_IMAGE", "cannot decode image", None, None))
        # the image was deleted since: the record keeps its id and says so
        self.assertEqual((deleted["status"], deleted["image_id"], deleted["image"], deleted["image_state"]),
                         ("succeeded", IMG_B, None, "deleted"))
        self.assertEqual(self.store.calls[-1], ("list", None, None, 1, 50))

    def test_a_status_filter_and_paging_reach_the_store(self):
        body = self.ok(self.client.get("/api/index-tasks", params={"status": "failed", "page": 1, "page_size": 10}))
        self.assertEqual((body["status"], body["total"], [i["id"] for i in body["items"]]), ("failed", 1, [2]))
        self.assertEqual(body["counts"], {"succeeded": 2, "failed": 1, "image_deleted": 1})  # counts ignore the filter
        self.assertEqual(self.store.calls[-1], ("list", "failed", None, 1, 10))

    def test_the_image_state_splits_the_succeeded_records(self):
        for state, ids in (("deleted", [1]), ("present", [3])):
            with self.subTest(state=state):
                body = self.ok(self.client.get("/api/index-tasks", params={"image_state": state}))
                self.assertEqual((body["image_state"], body["total"], [i["id"] for i in body["items"]]), (state, 1, ids))
        body = self.ok(self.client.get("/api/index-tasks", params={"status": "succeeded", "image_state": "deleted"}))
        self.assertEqual((body["status"], body["image_state"], [i["id"] for i in body["items"]]), ("succeeded", "deleted", [1]))

    def test_bad_list_parameters_are_validation_errors(self):
        for params in ({"status": "running"}, {"page": 0}, {"page_size": 0}, {"page_size": 101}, {"page": "x"},
                       {"image_state": "gone"}):
            with self.subTest(params=params):
                self.error(self.client.get("/api/index-tasks", params=params), 422, "VALIDATION_ERROR")
        self.assertEqual(self.store.calls, [])

    def test_clearing_needs_a_filter_and_deletes_only_those_records(self):
        self.error(self.client.delete("/api/index-tasks"), 422, "VALIDATION_ERROR")  # never everything
        self.error(self.client.delete("/api/index-tasks", params={"status": "all"}), 422, "VALIDATION_ERROR")
        self.error(self.client.delete("/api/index-tasks", params={"image_state": "all"}), 422, "VALIDATION_ERROR")
        self.assertEqual(self.store.calls, [])
        body = self.ok(self.client.delete("/api/index-tasks", params={"status": "failed"}))
        self.assertEqual(body, {"ok": True, "status": "failed", "image_state": None, "deleted": 1})
        self.assertEqual([r["status"] for r in self.store.records], ["succeeded", "succeeded"])
        self.assertEqual(self.ok(self.client.delete("/api/index-tasks", params={"status": "failed"}))["deleted"], 0)

    def test_clearing_the_records_of_deleted_images_keeps_the_others(self):
        body = self.ok(self.client.delete("/api/index-tasks", params={"image_state": "deleted"}))
        self.assertEqual(body, {"ok": True, "status": None, "image_state": "deleted", "deleted": 1})
        self.assertEqual([r["id"] for r in self.store.records], [3, 2])  # the stored image's and the failure
        self.assertEqual(self.store.calls[-1], ("clear", None, "deleted"))

    def test_one_record_can_be_deleted_by_its_id(self):
        self.assertEqual(self.ok(self.client.delete("/api/index-tasks/2")), {"ok": True, "id": 2, "deleted": 1})
        self.assertEqual([r["id"] for r in self.store.records], [3, 1])
        self.assertEqual(self.ok(self.client.delete("/api/index-tasks/2"))["deleted"], 0)  # already gone
        for bad in ("0", "-1", "x", str(2**63)):
            with self.subTest(task_id=bad):
                self.error(self.client.delete(f"/api/index-tasks/{bad}"), 422, "VALIDATION_ERROR")
        self.assertEqual(self.store.calls, [("delete", 2), ("delete", 2)])

    # ---- deletion queue ------------------------------------------------------------

    def test_the_queue_is_readable_without_the_retrieval_stack(self):
        body = self.ok(self.client.get("/api/image-deletions", params={"limit": 1}))
        self.assertEqual((body["total"], body["cleanup_ready"]), (2, True))
        self.assertEqual([i["image_id"] for i in body["items"]], [IMG_B])  # oldest first
        self.assertEqual(body["items"][0]["thumb_url"], "https://cdn/t/b.webp")
        self.runtime.retrieval = None
        body = self.ok(self.client.get("/api/image-deletions"))
        self.assertEqual((body["total"], len(body["items"]), body["cleanup_ready"]), (2, 2, False))
        self.error(self.client.get("/api/image-deletions", params={"limit": 201}), 422, "VALIDATION_ERROR")

    def test_cleaning_removes_the_points_and_files_then_the_rows(self):
        body = self.ok(self.client.post("/api/image-deletions/process", json={"limit": 1}))
        self.assertEqual(body, {"ok": True, "scanned": 1, "processed": [IMG_B], "failed": [], "remaining": 1})
        self.vectors.delete.assert_called_once_with(IMG_B)
        self.assertEqual([c.args[0] for c in self.storage.delete_by_url_or_key.call_args_list],
                         ["https://cdn/o/b.jpg", "https://cdn/t/b.webp"])
        body = self.ok(self.client.post("/api/image-deletions/process"))  # no body: limit 50
        self.assertEqual((body["processed"], body["remaining"]), ([IMG_C], 0))

    def test_a_failure_stays_queued_with_a_client_safe_reason(self):
        self.storage.delete_by_url_or_key.side_effect = RuntimeError("s3 down at 10.0.0.5:9000")
        self.vectors.delete.side_effect = [None, err.index_qdrant_failed("delete: ConnectionError 10.0.0.6:6334")]
        with self.assertLogs("retrieval.delete", level="ERROR"):
            body = self.ok(self.client.post("/api/image-deletions/process", json={}))
        self.assertEqual((body["scanned"], body["processed"], body["remaining"]), (2, [], 2))
        self.assertEqual(body["failed"], [
            {"image_id": IMG_B, "error": "INTERNAL_ERROR", "detail": "cleanup failed"},
            {"image_id": IMG_C, "error": "INDEX_QDRANT_FAILED", "detail": "vector store write failed"},
        ])
        self.assertNotIn("10.0.0", str(body))
        self.assertEqual(set(self.store.queue), {IMG_B, IMG_C})

    def test_bad_process_bodies_reach_nothing(self):
        for payload in ({"limit": 0}, {"limit": 201}, {"limit": "5"}, {"limit": True}, {"limit": 5, "all": True}):
            with self.subTest(payload=payload):
                self.error(self.client.post("/api/image-deletions/process", json=payload), 422, "VALIDATION_ERROR")
        self.vectors.delete.assert_not_called()

    def test_cleaning_needs_the_retrieval_stack(self):
        self.runtime.retrieval, self.runtime.retrieval_error = None, "NOT_READY: qdrant unreachable"
        self.error(self.client.post("/api/image-deletions/process"), 503, "NOT_READY")
        self.assertEqual(len(self.store.queue), 2)

    def test_without_the_catalog_every_route_is_not_ready(self):
        self.runtime.catalog, self.runtime.catalog_error = None, "postgres down"
        self.error(self.client.get("/api/index-tasks"), 503, "NOT_READY")
        self.error(self.client.delete("/api/index-tasks", params={"status": "failed"}), 503, "NOT_READY")
        self.error(self.client.delete("/api/index-tasks/3"), 503, "NOT_READY")
        self.error(self.client.get("/api/image-deletions"), 503, "NOT_READY")
        self.error(self.client.post("/api/image-deletions/process"), 503, "NOT_READY")
        self.vectors.delete.assert_not_called()


class TaskCenterUseCaseTests(unittest.TestCase):
    def test_bounds_are_checked_whatever_the_caller(self):
        uc = TaskCenterUseCase(FakeTaskStore())
        for kwargs in ({"page": 0}, {"page": True}, {"page_size": 101}, {"page_size": "10"}, {"status": "running"},
                       {"image_state": "gone"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(err.AppError) as ctx:
                uc.list_index_tasks(**kwargs)
            self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")
        for kwargs in ({}, {"status": None}, {"status": ""}, {"status": "everything"}, {"image_state": "everything"}):
            with self.subTest(clear=kwargs), self.assertRaises(err.AppError):
                uc.clear_index_tasks(**kwargs)
        for task_id in (0, -1, True, "3", 2**63):
            with self.subTest(task_id=task_id), self.assertRaises(err.AppError):
                uc.delete_index_task(task_id)
        with self.assertRaises(err.AppError):
            uc.list_deletions(limit=0)


class RouteRegistrationTests(unittest.TestCase):
    def test_the_task_routes_are_served(self):
        routes = {(r.path, m) for r in api_router.routes for m in getattr(r, "methods", ())}
        for route in (("/api/index-tasks", "GET"), ("/api/index-tasks", "DELETE"), ("/api/index-tasks/{task_id}", "DELETE"),
                      ("/api/image-deletions", "GET"), ("/api/image-deletions/process", "POST")):
            self.assertIn(route, routes)


if __name__ == "__main__":
    unittest.main()
