"""Batch-search routes with a fake retrieval stack: partial success and request failures.

The images of a request are read and searched at the same time, so the fakes answer by the image
(its bytes, its URL), never by the order of the calls."""

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.services import batch_search as services
from app.api.v1 import batch_search as routes
from app.bootstrap.state import ApplicationState
from app.core import errors as err
from app.core.exceptions import register_exception_handlers
from app.core.middleware import TraceIdMiddleware


def by_input(outcomes, key):
    """A side effect answering outcomes[key(call)], raising it when it is an error."""

    def answer(*args, **kwargs):
        outcome = outcomes[key(*args, **kwargs)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return answer


def searched(outcomes):
    """search_uc.execute answering by the query image's bytes."""
    return by_input(outcomes, lambda **kwargs: kwargs["image_bytes"])


def downloaded(outcomes):
    """read_url_bytes answering by the URL."""
    return by_input(outcomes, lambda url, **kwargs: url)


class BatchSearchHttpTests(unittest.TestCase):
    def setUp(self):
        self.search = Mock()
        self.search.check.return_value = {
            "scene": "general", "outputs_heating": False, "score_threshold": 0.55,
        }
        self.search.execute.return_value = {"source": "empty", "items": [], "message": "no matches"}
        self.runtime = ApplicationState(
            config={}, config_info={}, http_client=Mock(),
            settings=SimpleNamespace(predict=SimpleNamespace(max_image_bytes=8, http_timeout=3.0)),
            retrieval=SimpleNamespace(search_uc=self.search),
        )
        app = FastAPI()
        register_exception_handlers(app)
        app.add_middleware(TraceIdMiddleware)
        app.include_router(routes.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        state = patch.object(routes, "get_application_state", return_value=self.runtime)
        state.start()
        self.addCleanup(state.stop)
        self.fetch = AsyncMock(return_value=b"image")
        fetch = patch.object(routes, "read_url_bytes", self.fetch)
        fetch.start()
        self.addCleanup(fetch.stop)

    def test_files_keep_input_order_and_isolate_empty_oversize_and_bad_images(self):
        self.search.execute.side_effect = searched({
            b"valid": {"source": "empty", "items": []}, b"bad": err.search_bad_image("cannot decode"),
        })
        response = self.client.post("/api/search/batch/files", files=[
            ("files", ("valid.png", b"valid", "image/png")),
            ("files", ("empty.png", b"", "image/png")),
            ("files", ("large.png", b"123456789", "image/png")),
            ("files", ("bad.png", b"bad", "image/png")),
        ])
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual((body["total"], body["succeeded"], body["matched"], body["failed"]), (4, 1, 0, 3))
        self.assertEqual([row["index"] for row in body["results"]], [0, 1, 2, 3])
        self.assertEqual([row["input"] for row in body["results"]], ["valid.png", "empty.png", "large.png", "bad.png"])
        self.assertEqual([row["error"] for row in body["results"][1:]], ["EMPTY_FILE", "FILE_TOO_LARGE", "SEARCH_BAD_IMAGE"])
        self.assertNotIn("error", body["results"][0])
        self.assertNotIn("items", body["results"][1])
        self.assertNotIn("source", body["results"][1])
        # only the images that could be read are searched
        searched_bytes = sorted(call.kwargs["image_bytes"] for call in self.search.execute.call_args_list)
        self.assertEqual(searched_bytes, [b"bad", b"valid"])

    def test_urls_keep_download_failure_per_image_and_preserve_trace_id(self):
        self.fetch.side_effect = downloaded({
            "http://localhost/a.png": err.invalid_url("private host"), "https://example.com/b.png": b"image",
        })
        response = self.client.post("/api/search/batch/urls", json={
            "image_urls": ["http://localhost/a.png", "https://example.com/b.png"], "trace_id": "batch.test-1",
        })
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual((body["succeeded"], body["failed"]), (1, 1))
        self.assertEqual(body["results"][0]["error"], "INVALID_URL")
        self.assertEqual(body["results"][1]["source"], "empty")
        self.assertEqual(body["trace_id"], "batch.test-1")
        self.assertEqual(response.headers["X-Trace-Id"], "batch.test-1")
        self.assertEqual(self.fetch.await_args.kwargs, {"max_image_bytes": 8, "timeout_s": 3.0})
        self.assertEqual(self.search.execute.call_args.kwargs["trace_id"], "batch.test-1")

    def test_matched_count_and_result_cards_keep_conditional_fields_absent(self):
        card = {
            "tags": "披萨", "description": "", "original_url": "https://example.com/pizza.png",
            "similarity": 0.95, "details": {"id": "pizza-image", "images": []},
        }
        self.fetch.side_effect = downloaded({
            "https://example.com/pizza.png": b"pizza", "https://example.com/other.png": b"other",
        })
        self.search.execute.side_effect = searched({
            b"pizza": {"source": "self", "items": [card]}, b"other": {"source": "empty", "items": []},
        })
        response = self.client.post("/api/search/batch/urls", json={
            "image_urls": ["https://example.com/pizza.png", "https://example.com/other.png"],
        })
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual((body["succeeded"], body["matched"], body["failed"]), (2, 1, 0))
        self.assertEqual(body["results"][0]["items"], [card])
        self.assertNotIn("heating_group", body["results"][0]["items"][0]["details"])
        self.assertNotIn("scene_label", body["results"][0]["items"][0]["details"])

    def test_shared_validation_fails_before_any_download(self):
        for failure in (err.search_unknown_scene("missing"), err.search_bad_score_threshold("bad threshold")):
            with self.subTest(error=failure.code):
                self.search.check.side_effect = failure
                response = self.client.post("/api/search/batch/urls", json={"image_urls": ["https://example.com/a.png"]})
                self.assertEqual(response.status_code, failure.http_status, response.text)
                self.assertEqual(response.json()["error"], failure.code)
        self.fetch.assert_not_awaited()
        self.search.execute.assert_not_called()

    def test_identical_service_failure_for_every_image_is_a_request_failure(self):
        self.search.execute.side_effect = err.search_qdrant_failed("connection refused")
        response = self.client.post("/api/search/batch/urls", json={"image_urls": ["https://example.com/a.png"] * 2})
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(response.json()["error"], "SEARCH_QDRANT_FAILED")
        self.assertEqual(response.json()["detail"], "vector search failed")
        self.assertNotIn("results", response.json())

    def test_a_service_failure_and_success_still_returns_the_batch(self):
        self.fetch.side_effect = downloaded({"https://example.com/a.png": b"down", "https://example.com/b.png": b"up"})
        self.search.execute.side_effect = searched({
            b"down": err.search_qdrant_failed("temporarily down"), b"up": {"source": "empty", "items": []},
        })
        response = self.client.post("/api/search/batch/urls", json={
            "image_urls": ["https://example.com/a.png", "https://example.com/b.png"],
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["failed"], 1)
        self.assertEqual(response.json()["results"][0]["detail"], "vector search failed")

    def test_the_images_of_a_request_are_searched_at_the_same_time_up_to_the_limit(self):
        limit = services.SEARCH_CONCURRENCY
        # Passes once `limit` searches wait at it together, then holds them a moment: time enough
        # for one more to start, were nothing keeping it out. The timeout guards a sequential
        # implementation, whose first search would wait there for ever
        together = threading.Barrier(limit, action=lambda: time.sleep(0.05), timeout=5)
        lock = threading.Lock()
        running = most = 0

        def execute(**kwargs):
            nonlocal running, most
            with lock:
                running += 1
                most = max(most, running)
            try:
                together.wait()
                return {"source": "empty", "items": []}
            finally:
                with lock:
                    running -= 1

        self.search.execute.side_effect = execute
        urls = [f"https://example.com/{index}.png" for index in range(2 * limit)]
        response = self.client.post("/api/search/batch/urls", json={"image_urls": urls})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["succeeded"], 2 * limit)
        self.assertEqual([row["input"] for row in response.json()["results"]], urls)
        self.assertEqual(most, limit)  # never more than SEARCH_CONCURRENCY at once

    def test_unready_stack_is_rejected_before_downloading(self):
        self.runtime.retrieval = None
        response = self.client.post("/api/search/batch/urls", json={"image_urls": ["https://example.com/a.png"]})
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["error"], "NOT_READY")
        self.fetch.assert_not_awaited()

    def test_json_shape_unknown_fields_and_image_count_are_rejected(self):
        for body in ({"image_urls": []}, {"image_urls": ["https://example.com/a.png"] * 31},
                     {"image_urls": ["https://example.com/a.png"], "unknown": True},
                     {"image_urls": ["https://example.com/a.png"], "top_k": 51}):
            with self.subTest(body=body):
                response = self.client.post("/api/search/batch/urls", json=body)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["error"], "VALIDATION_ERROR")
        self.fetch.assert_not_awaited()
        self.search.check.assert_not_called()

    def test_multipart_image_count_is_checked_before_reading_uploads(self):
        with patch.object(routes, "read_upload", new_callable=AsyncMock) as read:
            response = self.client.post("/api/search/batch/files", files=[
                ("files", (f"{index}.png", b"image", "image/png")) for index in range(31)
            ])
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"], "VALIDATION_ERROR")
        read.assert_not_awaited()
        self.search.check.assert_not_called()
