"""Retrieval HTTP surface with a fake stack: parsing, threadpool offload, health."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import retrieval as routes
from app.core.exceptions import register_exception_handlers
from app.bootstrap.state import ApplicationState
from app.services.retrieval.active_model import ActiveModel
from app.services.retrieval.food_fields import FOOD_FIELDS
from app.core.errors import AppError

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

        def execute_batch(items):
            self.calls["index"] = {"items": items, "off_loop": _off_event_loop()}
            return {
                "indexed": [{
                    "id": "abc", "filename": "a.png", "original_url": "https://cdn/o/abc.png",
                    "thumb_url": "https://cdn/t/abc.webp", "scenes": ["general"],
                    "label_status": "unlabeled", "width": 8, "height": 6,
                }],
                "failed": [],
            }

        def execute_one(**kwargs):
            self.calls["index_file"] = {**kwargs, "off_loop": _off_event_loop()}
            return {
                "id": "single", "filename": kwargs["filename"],
                "original_url": "https://cdn/o/single.png", "thumb_url": "https://cdn/t/single.webp",
                "scenes": ["general"], "label_status": "unlabeled", "width": 8, "height": 6,
            }

        def search(**kwargs):
            self.calls["search"] = {**kwargs, "off_loop": _off_event_loop()}
            return {"source": "empty", "scene": "general", "outputs_heating": False, "items": []}

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
            index_uc=SimpleNamespace(execute_batch=execute_batch, execute_one=execute_one),
            search_uc=SimpleNamespace(execute=search),
        )
        self.runtime = ApplicationState(
            config={},
            config_info={},
            http_client=Mock(),
            settings=SimpleNamespace(
                predict=SimpleNamespace(max_image_bytes=1024 * 1024, http_timeout=5.0),
                qdrant=SimpleNamespace(collection=""),
            ),
            retrieval=self.stack,
        )
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(routes.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        p = patch.object(routes, "get_application_state", return_value=self.runtime)
        p.start()
        self.addCleanup(p.stop)
        # img_url inputs are downloaded by the HTTP layer (bounded fetcher), not the use case
        self.fetch = AsyncMock(return_value=b"downloaded")
        p = patch("app.api.services.retrieval.read_url_bytes", self.fetch)
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

    def test_index_file_upload_runs_the_single_image_use_case_off_the_event_loop(self):
        response = self.client.post("/api/index/file", files={"file": ("single.png", b"image-bytes", "image/png")})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["trace_id"])
        self.assertEqual(len(body["indexed"]), 1)
        self.assertEqual(body["indexed"][0]["id"], "single")
        self.assertEqual(body["indexed"][0]["filename"], "single.png")
        self.assertEqual(body["failed"], [])
        self.assertEqual(self.calls["index_file"], {
            "image_bytes": b"image-bytes", "filename": "single.png", "off_loop": True,
        })
        self.assertNotIn("index", self.calls)

    def test_index_file_requires_the_file_field(self):
        response = self.client.post("/api/index/file")
        self.assertEqual(response.status_code, 422, response.text)
        body = response.json()
        self.assertEqual(body["error"], "VALIDATION_ERROR")
        self.assertTrue(any(e["loc"] == ["body", "file"] for e in body["errors"]))
        self.assertNotIn("index_file", self.calls)

    def test_index_file_rejects_an_empty_upload(self):
        response = self.client.post("/api/index/file", files={"file": ("empty.png", b"", "image/png")})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["error"], "EMPTY_FILE")
        self.assertNotIn("index_file", self.calls)

    def test_index_file_rejects_an_upload_over_the_size_limit(self):
        self.runtime.settings.predict.max_image_bytes = 3
        response = self.client.post("/api/index/file", files={"file": ("large.png", b"abcd", "image/png")})
        self.assertEqual(response.status_code, 413, response.text)
        self.assertEqual(response.json()["error"], "FILE_TOO_LARGE")
        self.assertNotIn("index_file", self.calls)

    def test_index_file_without_a_retrieval_stack_is_503(self):
        self.runtime.retrieval = None
        self.runtime.retrieval_error = "no current model"
        response = self.client.post("/api/index/file", files={"file": ("single.png", b"image", "image/png")})
        self.assertEqual(response.status_code, 503, response.text)
        body = response.json()
        self.assertEqual((body["error"], body["detail"]), ("NOT_READY", "no current model"))
        self.assertNotIn("index_file", self.calls)

    def test_index_file_preserves_use_case_error_codes_and_statuses(self):
        # A client error keeps its detail; an internal code (vector store) shows its public message.
        for code, status, detail in (
            ("INDEX_BAD_IMAGE", 422, "index failed"),
            ("INDEX_QDRANT_FAILED", 502, "vector store write failed"),
        ):
            with self.subTest(code=code):
                execute_one = Mock(side_effect=AppError(code, "index failed", http_status=status))
                self.stack.index_uc.execute_one = execute_one
                response = self.client.post("/api/index/file", files={"file": ("single.png", b"image", "image/png")})
                self.assertEqual(response.status_code, status, response.text)
                body = response.json()
                self.assertEqual((body["error"], body["detail"]), (code, detail))
                execute_one.assert_called_once_with(image_bytes=b"image", filename="single.png")

    def test_index_file_openapi_declares_only_one_required_binary_file(self):
        document = self.client.get("/openapi.json").json()
        operation = document["paths"]["/api/index/file"]["post"]
        self.assertEqual(operation.get("parameters", []), [])
        request_body = operation["requestBody"]
        self.assertTrue(request_body["required"])
        self.assertEqual(set(request_body["content"]), {"multipart/form-data"})
        schema = request_body["content"]["multipart/form-data"]["schema"]
        if "$ref" in schema:
            schema = document["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
        self.assertEqual(schema["required"], ["file"])
        self.assertEqual(set(schema["properties"]), {"file"})
        file_schema = schema["properties"]["file"]
        self.assertEqual((file_schema["type"], file_schema["format"]), ("string", "binary"))

    def test_search_json_with_query_override(self):
        response = self.client.post("/api/search?top_k=5", json={"img_url": "https://x/a.jpg", "scene": "oven"})
        self.assertEqual(response.status_code, 200, response.text)
        call = self.calls["search"]
        self.assertEqual((call["image_bytes"], call["scene"], call["top_k"]), (b"downloaded", "oven", 5))
        self.assertEqual(self.fetch.await_args.args[0], "https://x/a.jpg")
        self.assertEqual(self.fetch.await_args.kwargs["max_image_bytes"], 1024 * 1024)
        self.assertTrue(call["off_loop"])

    def test_search_trace_id_from_body_is_used_everywhere(self):
        response = self.client.post("/api/search", json={"img_url": "https://x/a.jpg", "trace_id": "t-42"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["trace_id"], "t-42")
        self.assertNotIn("trace_id", self.calls["search"])  # use cases know nothing about the wire

    def test_bad_score_threshold_literal_has_its_own_code(self):
        response = self.client.post("/api/search", data={"img_url": "https://x/a.jpg", "score_threshold": "high"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"], "SEARCH_BAD_SCORE_THRESHOLD")

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
        body = response.json()
        self.assertEqual((body["error"], body["detail"]), ("NOT_READY", "NOT_READY: no current model"))

    def test_health_reports_the_active_model(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["components"]["embedding"]["vector_dim"], 1024)
        self.assertEqual(body["components"]["embedding"]["embedding_space_id"], "dino:v1")
        self.assertEqual(body["components"]["qdrant"]["points_count"], 3)
        self.assertTrue(body["components"]["postgres"]["ok"])
        self.assertTrue(body["components"]["flyb"]["disabled"])

    def test_health_without_stack_is_503_with_reason(self):
        self.runtime.retrieval = None
        self.runtime.retrieval_error = "OperationalError: refused"
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 503)
        comps = response.json()["components"]
        self.assertEqual(comps["postgres"], {"ok": False, "detail": "OperationalError: refused"})
        self.assertFalse(comps["embedding"]["ok"])

    def test_legacy_file_route_is_gone(self):
        self.assertEqual(self.client.get("/api/images/file?id=1").status_code, 404)


    def test_index_description_from_form_and_json_reaches_the_use_case(self):
        self.client.post("/api/index", files=[("file", ("a.png", b"aaa", "image/png"))], data={"description": "红烧肉"})
        self.assertEqual(self.calls["index"]["items"][0]["description"], "红烧肉")

        response = self.client.post("/api/index", files=[("file", ("a.png", b"aaa", "image/png"))])
        self.assertIsNone(self.calls["index"]["items"][0]["description"])  # use case stores ""
        self.assertEqual(response.json()["indexed"][0]["description"], "")

        self.client.post("/api/index", json={"path": "/data/a.png", "description": "清蒸鱼"})
        self.assertEqual(self.calls["index"]["items"][0]["description"], "清蒸鱼")
        self.client.post("/api/index", json={"path": "/data/a.png"})
        self.assertEqual(self.calls["index"]["items"][0]["description"], "")

    def test_index_tags_from_form_and_json_reach_the_use_case(self):
        upload = [("file", ("a.png", b"aaa", "image/png"))]
        self.client.post("/api/index", files=upload, data={"tags": '["红薯叶", "带皮"]'})
        self.assertEqual(self.calls["index"]["items"][0]["tags"], ["红薯叶", "带皮"])
        response = self.client.post("/api/index", files=upload)
        self.assertIsNone(self.calls["index"]["items"][0]["tags"])  # use case stores []
        self.assertEqual(response.json()["indexed"][0]["tags"], [])

        self.client.post("/api/index", json={"path": "/data/a.png", "tags": ["清蒸"]})
        self.assertEqual(self.calls["index"]["items"][0]["tags"], ["清蒸"])

    def test_index_rejects_tags_that_are_not_a_string_array(self):
        upload = [("file", ("a.png", b"aaa", "image/png"))]
        for form in ({"tags": "红薯叶"}, {"tags": '{"a": 1}'}):  # multipart: must be a JSON array
            with self.subTest(form=form):
                response = self.client.post("/api/index", files=upload, data=form)
                self.assertEqual((response.status_code, response.json()["error"]), (422, "VALIDATION_ERROR"))
        for tags in ("红薯叶", [1], [["nested"]]):
            with self.subTest(tags=tags):
                response = self.client.post("/api/index", json={"path": "/data/a.png", "tags": tags})
                self.assertEqual((response.status_code, response.json()["error"]), (422, "VALIDATION_ERROR"))

    def test_list_forwards_the_tag_filter_and_returns_tags(self):
        def list_execute(**kwargs):
            self.calls["list"] = kwargs
            return {
                "scene": "general", "page": 1, "page_size": 24, "total": 1,
                "items": [{"id": "abc", "tags": ["红薯叶"]}],
            }

        self.stack.list_uc = SimpleNamespace(execute=list_execute)
        response = self.client.get("/api/images", params={"tag": "红薯叶", "q": "薯"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual((self.calls["list"]["tag"], self.calls["list"]["q"]), ("红薯叶", "薯"))
        self.assertEqual(response.json()["items"][0]["tags"], ["红薯叶"])
        self.client.get("/api/images")
        self.assertIsNone(self.calls["list"]["tag"])

    def test_index_food_attributes_from_form_and_json_reach_the_use_case(self):
        def food_of_last_item():
            item = self.calls["index"]["items"][0]
            return {key: item[key] for key in FOOD_FIELDS}

        # every food field the use case knows is forwarded (form values stay strings)
        form = {
            "food_state": "raw", "food_processing": "sliced", "food_count": "4",
            "total_mass_g": "350", "water_mass_g": "50", "main_ingredient_mass_g": "300",
        }
        self.assertEqual(set(form), set(FOOD_FIELDS))
        self.client.post("/api/index", files=[("file", ("a.png", b"aaa", "image/png"))], data=form)
        self.assertEqual(food_of_last_item(), form)

        body = {
            "food_state": "cooked", "food_processing": "whole", "food_count": 1,
            "total_mass_g": 500, "water_mass_g": 0, "main_ingredient_mass_g": 450,
        }
        self.client.post("/api/index", json={"path": "/data/a.png", **body})
        self.assertEqual(food_of_last_item(), body)
        self.client.post("/api/index", json={"path": "/data/a.png"})  # all optional
        self.assertEqual(food_of_last_item(), dict.fromkeys(FOOD_FIELDS))

    def test_index_labelling_from_form_and_json_reaches_the_use_case(self):
        def labels_of_last_item():
            item = self.calls["index"]["items"][0]
            return {key: item[key] for key in ("label_status", "source_type", "license")}

        form = {"label_status": " verified ", "source_type": "crawled", "license": "commercial"}
        self.client.post("/api/index", files=[("file", ("a.png", b"aaa", "image/png"))], data=form)
        self.assertEqual(labels_of_last_item(), {"label_status": "verified", "source_type": "crawled", "license": "commercial"})
        self.client.post("/api/index", files=[("file", ("a.png", b"aaa", "image/png"))], data={"license": " "})
        self.assertEqual(labels_of_last_item(), dict.fromkeys(("label_status", "source_type", "license")))  # defaults apply

        self.client.post("/api/index", json={"path": "/data/a.png", "label_status": "auto", "source_type": "device"})
        self.assertEqual(labels_of_last_item(), {"label_status": "auto", "source_type": "device", "license": None})

    def test_index_json_rejects_out_of_range_fractional_or_boolean_amounts(self):
        for bad in ({"total_mass_g": 0}, {"water_mass_g": -1}, {"food_count": True},
                    {"main_ingredient_mass_g": 1.5}, {"food_count": -2}):
            with self.subTest(body=bad):
                response = self.client.post("/api/index", json={"path": "/data/a.png", **bad})
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["error"], "VALIDATION_ERROR")


if __name__ == "__main__":
    unittest.main()
