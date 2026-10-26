import asyncio
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.adapter.http.endpoints import predict


class PredictEndpointTests(unittest.TestCase):
    def setUp(self):
        self.requests = []

        def image_response(request):
            self.requests.append(request)
            return httpx.Response(200, content=b"query-image")

        self.http_client = httpx.AsyncClient(transport=httpx.MockTransport(image_response))
        self.addCleanup(lambda: asyncio.run(self.http_client.aclose()))
        self.runtime = Mock()
        self.runtime.http_client = self.http_client
        self.runtime.http_timeout = 10
        self.runtime.max_image_bytes = 1024
        self.service = self.runtime.require_food_service.return_value
        self.service.detect.return_value = []
        app = FastAPI()
        app.include_router(predict.router)
        self.app = app
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        state_patch = patch.object(predict, "get_application_state", return_value=self.runtime)
        state_patch.start()
        self.addCleanup(state_patch.stop)

    def test_query_values_override_body_including_false_and_empty_strings(self):
        response = self.client.post(
            "/api/predict/searchImage_url",
            params={"img_url": "https://query.example/a.png", "machine_info": "", "trace_id": "query-trace", "save_res": "false"},
            json={"img_url": "https://body.example/b.png", "machine_info": "body-machine", "trace_id": "body-trace", "save_res": True},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"result": [], "trace_id": "query-trace"})
        self.assertEqual(str(self.requests[0].url), "https://query.example/a.png")
        self.service.detect.assert_called_once_with(b"query-image", machine_info="", trace_id="query-trace", save_result=False)

    def test_url_body_defaults_and_generated_trace_id_are_preserved(self):
        response = self.client.post("/api/predict/searchImage_url", json={"img_url": "//images.example/a.png"})
        self.assertEqual(response.status_code, 200, response.text)
        trace_id = response.json()["trace_id"]
        self.assertEqual(str(UUID(trace_id)), trace_id)
        self.assertEqual(str(self.requests[0].url), "https://images.example/a.png")
        self.service.detect.assert_called_once_with(b"query-image", machine_info=None, trace_id=trace_id, save_result=False)

    def test_url_body_fills_fields_not_provided_in_query(self):
        response = self.client.post(
            "/api/predict/searchImage_url",
            params={"img_url": "images.example/a.png"},
            json={"machine_info": "body-machine", "trace_id": "body-trace", "save_res": True},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.service.detect.assert_called_once_with(b"query-image", machine_info="body-machine", trace_id="body-trace", save_result=True)

    def test_invalid_or_missing_url_never_downloads_or_invokes_model(self):
        for body in (None, {}, {"img_url": ""}, {"img_url": "ftp://images.example/a.png"}, {"img_url": "https://[invalid"}):
            with self.subTest(body=body):
                response = self.client.post("/api/predict/searchImage_url", json=body)
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.requests, [])
        self.service.detect.assert_not_called()

    def test_file_search_preserves_list_and_envelope_results_and_nullable_trace(self):
        for result in ([{"title": "image", "custom": {"rank": 3}}], {"results": [], "status": "upstream_empty_response", "upstream_error": {"code": 500}}):
            self.service.detect.return_value = result
            with self.subTest(result=result):
                response = self.client.post("/api/predict/searchImage_file", files={"file": ("query.png", b"query-image", "image/png")})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), {"result": result, "trace_id": None})

    def test_file_input_errors_and_unavailable_service_keep_http_statuses(self):
        self.runtime.max_image_bytes = 4
        for data, status in ((b"", 400), (b"large", 413)):
            with self.subTest(status=status):
                response = self.client.post("/api/predict/searchImage_file", files={"file": ("query.png", data, "image/png")})
                self.assertEqual(response.status_code, status)
        self.service.detect.assert_not_called()
        self.runtime.require_food_service.side_effect = RuntimeError("not initialized")
        response = self.client.post("/api/predict/searchImage_file", files={"file": ("query.png", b"data", "image/png")})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "service unavailable: not initialized")

    def test_openapi_exposes_separate_request_and_response_contracts(self):
        schema = self.app.openapi()
        self.assertIn("PredictUrlBody", schema["components"]["schemas"])
        for route in ("/api/predict/searchImage_file", "/api/predict/searchImage_url"):
            response_schema = schema["paths"][route]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
            self.assertEqual(response_schema["$ref"], "#/components/schemas/PredictResponse")
