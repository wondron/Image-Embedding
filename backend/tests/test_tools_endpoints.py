import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.adapter.http.endpoints import tools


class ToolsEndpointTests(unittest.TestCase):
    def setUp(self):
        self.runtime = Mock()
        self.runtime.max_image_bytes = 8
        self.runtime.settings.allowed_content_types = ("image/png", "image/jpeg")
        self.service = self.runtime.require_food_service.return_value
        self.vector = Mock(ndim=1, shape=(3,))
        self.vector.tolist.return_value = [0.1, 0.2, 0.3]
        self.service.embedding.detect.return_value = self.vector

        app = FastAPI()
        app.include_router(tools.router)
        self.client = TestClient(app)
        state_patch = patch.object(tools, "get_application_state", return_value=self.runtime)
        state_patch.start()
        self.addCleanup(state_patch.stop)
        self.addCleanup(self.client.close)

    def upload(self, tool="embedding", *, content=b"image", content_type="image/png", **params):
        return self.client.post(
            f"/api/tools/{tool}",
            files={"file": ("query.png", content, content_type)},
            params=params,
        )

    def test_embedding_returns_vector_and_metadata(self):
        response = self.upload(content=b"12345678")
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertTrue(result["ok"])
        self.assertEqual(result["filename"], "query.png")
        self.assertEqual(result["embedding_dim"], 3)
        self.assertEqual(result["embedding"], [0.1, 0.2, 0.3])
        self.assertGreaterEqual(result["latency_ms"], 0)
        self.service.embedding.detect.assert_called_once_with(b"12345678")

    def test_tools_reject_unsupported_content_type_before_model_access(self):
        for tool in ("embedding", "segment"):
            with self.subTest(tool=tool):
                response = self.upload(tool, content_type="text/plain")
                self.assertEqual(response.status_code, 415)
                self.assertEqual(response.json()["detail"], "unsupported image content type: text/plain")
        self.runtime.require_food_service.assert_not_called()

    def test_tools_reject_empty_upload_before_model_access(self):
        for tool in ("embedding", "segment"):
            with self.subTest(tool=tool):
                response = self.upload(tool, content=b"")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["detail"], "uploaded file is empty")
        self.runtime.require_food_service.assert_not_called()

    def test_tools_reject_oversized_upload_before_model_access(self):
        for tool in ("embedding", "segment"):
            with self.subTest(tool=tool):
                response = self.upload(tool, content=b"123456789")
                self.assertEqual(response.status_code, 413)
                self.assertEqual(response.json()["detail"], "file too large (> 8 bytes)")
        self.runtime.require_food_service.assert_not_called()

    def test_tools_report_unavailable_service(self):
        self.runtime.require_food_service.side_effect = RuntimeError("models are loading")
        for tool in ("embedding", "segment"):
            with self.subTest(tool=tool):
                response = self.upload(tool)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["detail"], "service unavailable: models are loading")

    def test_embedding_reports_missing_model(self):
        self.service.embedding = None
        response = self.upload()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "embedding model is not initialized")

    def test_embedding_rejects_invalid_vector_shape(self):
        self.service.embedding.detect.return_value = Mock(ndim=2, shape=(1, 3))
        response = self.upload()
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "embedding model returned an invalid shape: (1, 3)")

    def test_embedding_preserves_model_value_error(self):
        self.service.embedding.detect.side_effect = ValueError("invalid image bytes")
        response = self.upload()
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "invalid image bytes")

    def test_embedding_reports_unexpected_model_failure(self):
        self.service.embedding.detect.side_effect = RuntimeError("inference failed")
        with self.assertLogs("svc.tools", level="ERROR"):
            response = self.upload()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "embedding failed: inference failed")

    def test_segment_reports_missing_segmentor(self):
        self.service.segmentor = None
        response = self.upload("segment")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "segmentor is not initialized")

    def test_segment_preserves_current_invalid_original_error(self):
        for output_vis in (False, True):
            with self.subTest(output_vis=output_vis):
                response = self.upload("segment", output_vis=output_vis)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["detail"], "segmentor returned an invalid original image")
        self.service.segmentor.detect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
