import unittest
from unittest.mock import Mock, patch
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import predict
from app.core.exceptions import register_exception_handlers


class PredictEndpointTests(unittest.TestCase):
    def setUp(self):
        self.requests = []

        def fetch_image(url, **limits):  # the public-host fetcher behind read_url_bytes
            self.requests.append(url)
            return b"query-image", "image/png"

        fetch_patch = patch("app.clients.image_fetch.public_image.fetch_image", side_effect=fetch_image)
        fetch_patch.start()
        self.addCleanup(fetch_patch.stop)
        self.runtime = Mock()
        self.runtime.http_timeout = 10
        self.runtime.max_image_bytes = 1024
        self.service = self.runtime.require_food_service.return_value
        self.service.detect.return_value = []
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(predict.router)
        self.app = app
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        state_patch = patch.object(predict, "get_application_state", return_value=self.runtime)
        state_patch.start()
        self.addCleanup(state_patch.stop)

    def test_query_values_override_body_including_false_and_empty_strings(self):
        response = self.client.post(
            "/api/search/global/url",
            params={"img_url": "https://query.example/a.png", "search_num": 7, "machine_info": "", "trace_id": "query-trace", "save_res": "false"},
            json={"img_url": "https://body.example/b.png", "search_num": 5, "machine_info": "body-machine", "trace_id": "body-trace", "save_res": True},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"ok": True, "result": [], "trace_id": "query-trace"})
        self.assertEqual(self.requests, ["https://query.example/a.png"])
        self.service.detect.assert_called_once_with(b"query-image", search_num=7, machine_info="", trace_id="query-trace", save_result=False)

    def test_url_body_defaults_and_generated_trace_id_are_preserved(self):
        response = self.client.post("/api/search/global/url", json={"img_url": "//images.example/a.png"})
        self.assertEqual(response.status_code, 200, response.text)
        trace_id = response.json()["trace_id"]
        self.assertEqual(str(UUID(trace_id)), trace_id)
        self.assertEqual(self.requests, ["https://images.example/a.png"])
        self.service.detect.assert_called_once_with(b"query-image", search_num=None, machine_info=None, trace_id=trace_id, save_result=False)

    def test_url_body_fills_fields_not_provided_in_query(self):
        response = self.client.post(
            "/api/search/global/url",
            params={"img_url": "images.example/a.png"},
            json={"search_num": 5, "machine_info": "body-machine", "trace_id": "body-trace", "save_res": True},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.service.detect.assert_called_once_with(b"query-image", search_num=5, machine_info="body-machine", trace_id="body-trace", save_result=True)

    def test_file_query_search_num_reaches_the_model(self):
        response = self.client.post(
            "/api/search/global/file",
            params={"search_num": 7, "machine_info": "oven", "trace_id": "file-trace", "save_res": "true"},
            files={"file": ("query.png", b"query-image", "image/png")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"ok": True, "result": [], "trace_id": "file-trace"})
        self.service.detect.assert_called_once_with(
            b"query-image", search_num=7, machine_info="oven", trace_id="file-trace", save_result=True
        )
        self.assertEqual(self.requests, [])

    def test_url_query_search_num_reaches_the_model_without_a_body(self):
        response = self.client.post(
            "/api/search/global/url",
            params={"img_url": "https://images.example/a.png", "search_num": 7, "trace_id": "url-trace"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.requests, ["https://images.example/a.png"])
        self.service.detect.assert_called_once_with(
            b"query-image", search_num=7, machine_info=None, trace_id="url-trace", save_result=False
        )

    def test_invalid_or_missing_url_never_downloads_or_invokes_model(self):
        for body in (None, {}, {"img_url": ""}, {"img_url": "ftp://images.example/a.png"}, {"img_url": "https://[invalid"}):
            with self.subTest(body=body):
                response = self.client.post("/api/search/global/url", json=body)
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.requests, [])
        self.service.detect.assert_not_called()

    def test_file_search_returns_result_cards_and_the_empty_envelope_with_generated_trace(self):
        card = {
            "tags": "番茄", "description": "新鲜番茄", "original_url": "https://img.example/a.jpg", "similarity": 0.8,
            "details": {
                "rank": 3, "page_url": "https://site.example/a", "site": {"Name": "食材百科"},
                "images": [{"image_url": "https://img.example/a.jpg", "thumbnail_url": {"large": "https://img.example/l.jpg"},
                            "alt": None, "width": 600, "height": 400}],
                "document_info": {"PublishTime": "2026-01-01"},
            },
        }
        empty = {
            "request_id": "upstream-1", "total_doc_count": 0, "result_count": 0, "results": [],
            "status": "upstream_empty_response", "upstream_error": {"code": 500, "message": "empty"},
        }
        for result in ([card], empty):
            self.service.detect.reset_mock()
            self.service.detect.return_value = result
            with self.subTest(result=result):
                response = self.client.post("/api/search/global/file", files={"file": ("query.png", b"query-image", "image/png")})
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertEqual((body["ok"], body["result"]), (True, result))
                self.assertEqual(str(UUID(body["trace_id"])), body["trace_id"])
                self.service.detect.assert_called_once_with(
                    b"query-image", search_num=None, machine_info=None, trace_id=body["trace_id"], save_result=False
                )

    def test_file_input_errors_and_unavailable_service_keep_http_statuses(self):
        self.runtime.max_image_bytes = 4
        for data, status in ((b"", 400), (b"large", 413)):
            with self.subTest(status=status):
                response = self.client.post("/api/search/global/file", files={"file": ("query.png", data, "image/png")})
                self.assertEqual(response.status_code, status)
        self.service.detect.assert_not_called()
        self.runtime.require_food_service.side_effect = RuntimeError("not initialized")
        response = self.client.post("/api/search/global/file", files={"file": ("query.png", b"data", "image/png")})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "service unavailable: not initialized")

    def test_openapi_exposes_separate_request_and_response_contracts(self):
        schema = self.app.openapi()
        self.assertIn("PredictUrlBody", schema["components"]["schemas"])
        for route, summary in (
            ("/api/search/global/file", "现网 Global 搜图（上传文件）"),
            ("/api/search/global/url", "现网 Global 搜图（图片链接）"),
        ):
            self.assertEqual(schema["paths"][route]["post"]["summary"], summary)
            response_schema = schema["paths"][route]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
            self.assertEqual(response_schema["$ref"], "#/components/schemas/PredictResponse")
        self.assertNotIn("/api/predict/searchImage_file", schema["paths"])
        self.assertNotIn("/api/predict/searchImage_url", schema["paths"])

    def test_legacy_file_route_preserves_the_same_search_behavior(self):
        for route in ("/api/search/global/file", "/api/predict/searchImage_file"):
            with self.subTest(route=route):
                self.service.detect.reset_mock()
                response = self.client.post(
                    route,
                    params={"search_num": 7, "trace_id": "file-alias"},
                    files={"file": ("query.png", b"query-image", "image/png")},
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), {"ok": True, "result": [], "trace_id": "file-alias"})
                self.service.detect.assert_called_once_with(
                    b"query-image", search_num=7, machine_info=None, trace_id="file-alias", save_result=False
                )
        self.assertEqual(self.requests, [])

    def test_legacy_url_route_preserves_the_same_search_behavior(self):
        for route in ("/api/search/global/url", "/api/predict/searchImage_url"):
            with self.subTest(route=route):
                self.service.detect.reset_mock()
                self.requests.clear()
                response = self.client.post(
                    route,
                    json={"img_url": "https://images.example/a.png", "search_num": 5, "trace_id": "url-alias"},
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), {"ok": True, "result": [], "trace_id": "url-alias"})
                self.assertEqual(self.requests, ["https://images.example/a.png"])
                self.service.detect.assert_called_once_with(
                    b"query-image", search_num=5, machine_info=None, trace_id="url-alias", save_result=False
                )
