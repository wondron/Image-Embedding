"""Retrieval HTTP surface with a fake stack: parsing, threadpool offload, health."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import retrieval as routes
from app.bootstrap.state import ApplicationState
from app.services.retrieval.active_model import ActiveModel

MODEL = ActiveModel(id=1, name="dino", version="v1", collection="c1", dim=1024, distance="cosine")


def _off_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return False
    except RuntimeError:
        return True


class RetrievalHttpTests(unittest.TestCase):
    def setUp(self):
        self.calls = {}

        def execute_batch(items, public_file_base=None):
            self.calls["index"] = {"items": items, "off_loop": _off_event_loop()}
            return {"indexed": [{"id": "abc"}], "failed": []}

        def search(**kwargs):
            self.calls["search"] = {**kwargs, "off_loop": _off_event_loop()}
            return {"ok": True, "source": "empty", "scene": "general", "items": []}

        infra = MagicMock()
        infra.ping.return_value = {
            "postgres": {"ok": True, "host": "h", "database": "db"},
            "qdrant": {"ok": True, "collection": "c1", "points_count": 3},
            "storage": {"ok": True},
        }
        self.stack = SimpleNamespace(
            infra=infra,
            model=MODEL,
            embedding=SimpleNamespace(model_path="config/model/dino/multitask_cls.onnx"),
            index_uc=SimpleNamespace(execute_batch=execute_batch),
            search_uc=SimpleNamespace(execute=search),
        )
        self.runtime = ApplicationState(
            config={},
            config_info={},
            http_client=Mock(),
            settings=SimpleNamespace(
                predict=SimpleNamespace(max_image_bytes=1024 * 1024),
                qdrant=SimpleNamespace(collection=""),
            ),
            retrieval=self.stack,
        )
        app = FastAPI()
        app.include_router(routes.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        p = patch.object(routes, "get_application_state", return_value=self.runtime)
        p.start()
        self.addCleanup(p.stop)

    def test_index_multipart_runs_the_use_case_off_the_event_loop(self):
        response = self.client.post(
            "/api/index",
            files=[("files", ("a.png", b"aaa", "image/png")), ("files", ("b.png", b"bbb", "image/png"))],
            data={"category": "dish.steak"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["indexed"][0]["id"], "abc")
        items = self.calls["index"]["items"]
        self.assertEqual([i["image_bytes"] for i in items], [b"aaa", b"bbb"])
        self.assertEqual({i["category"] for i in items}, {"dish.steak"})
        self.assertTrue(self.calls["index"]["off_loop"])

    def test_index_without_files_is_400(self):
        response = self.client.post("/api/index", data={"category": "x"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "INDEX_EMPTY_REQUEST")
        self.assertNotIn("index", self.calls)

    def test_search_json_with_query_override(self):
        response = self.client.post("/api/search?top_k=5", json={"img_url": "https://x/a.jpg", "scene": "oven"})
        self.assertEqual(response.status_code, 200, response.text)
        call = self.calls["search"]
        self.assertEqual((call["image_url"], call["scene"], call["top_k"]), ("https://x/a.jpg", "oven", 5))
        self.assertTrue(call["off_loop"])

    def test_search_rejects_bad_top_k_and_missing_image(self):
        self.assertEqual(self.client.post("/api/search?top_k=99", json={"img_url": "https://x"}).status_code, 422)
        missing = self.client.post("/api/search", data={"scene": "general"})
        self.assertEqual(missing.status_code, 422)
        self.assertEqual(missing.json()["error"], "VALIDATION_ERROR")
        self.assertNotIn("search", self.calls)

    def test_retrieval_not_ready_is_503(self):
        self.runtime.retrieval = None
        self.runtime.retrieval_error = "NOT_READY: no current model"
        response = self.client.post("/api/index", files=[("file", ("a.png", b"a", "image/png"))])
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["error"], "NOT_READY")

    def test_health_reports_the_active_model(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["components"]["embedding"]["vector_dim"], 1024)
        self.assertEqual(body["components"]["embedding"]["embedding_space_id"], "dino:v1")
        self.assertEqual(body["components"]["qdrant"]["points_count"], 3)
        self.assertTrue(body["components"]["mysql"]["ok"])
        self.assertTrue(body["components"]["flyb"]["disabled"])

    def test_health_without_stack_is_503_with_reason(self):
        self.runtime.retrieval = None
        self.runtime.retrieval_error = "OperationalError: refused"
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 503)
        comps = response.json()["components"]
        self.assertEqual(comps["mysql"], {"ok": False, "detail": "OperationalError: refused"})
        self.assertFalse(comps["embedding"]["ok"])

    def test_legacy_file_route_is_gone(self):
        self.assertEqual(self.client.get("/api/images/file?id=1").status_code, 404)


if __name__ == "__main__":
    unittest.main()
