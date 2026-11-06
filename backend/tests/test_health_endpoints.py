import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import health
from app.bootstrap.state import ApplicationState
from app.core.exceptions import register_exception_handlers

NOT_WIRED_DEPS = {
    "qdrant": {"status": "not_wired", "detail": "not initialized"},
    "postgres": {"status": "not_wired", "detail": "not initialized"},
}


class HealthEndpointTests(unittest.TestCase):
    def setUp(self):
        self.service = SimpleNamespace(embeddingTool=SimpleNamespace(sess=Mock()))
        self.service.embeddingTool.sess.get_providers.return_value = [
            "CUDAExecutionProvider", "CPUExecutionProvider"
        ]
        self.runtime = ApplicationState(
            config={"EMBEDDING_CONFIG": {"model_path": "embedding.onnx"}},
            config_info={"source": "test"},
            http_client=Mock(),
            settings=SimpleNamespace(application=SimpleNamespace(env="test")),
            food_service=self.service,
        )
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(health.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        state_patch = patch.object(
            health, "get_application_state", return_value=self.runtime
        )
        state_patch.start()
        self.addCleanup(state_patch.stop)
        self.info = {"pid": 42, "uptime_s": 1.25}
        info_patch = patch.object(
            ApplicationState, "runtime_info", return_value=self.info
        )
        info_patch.start()
        self.addCleanup(info_patch.stop)

    def assert_not_ready(self, response, detail):
        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual((body["ok"], body["error"], body["detail"]), (False, "NOT_READY", detail))
        self.assertEqual(body["trace_id"], response.headers["x-trace-id"])

    def test_ready_service_reports_models_providers_and_runtime(self):
        response = self.client.get("/api/readyz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "ok": True,
            "components": {
                "embedding": {
                    "ok": True,
                    "providers": ["CUDAExecutionProvider", "CPUExecutionProvider"],
                    "model_path": "embedding.onnx",
                },
            },
            "deps": NOT_WIRED_DEPS,
            "warmup_error": None,
            **self.info,
        })

    def test_unavailable_service_preserves_initialization_error(self):
        self.runtime.food_service = None
        self.runtime.initialization_error = "controlled initialization failure"

        response = self.client.get("/api/readyz")

        self.assert_not_ready(response, "not ready: controlled initialization failure")

    def test_missing_model_components_are_not_ready(self):
        self.service.embeddingTool = None

        response = self.client.get("/api/readyz")

        self.assert_not_ready(response, "model components are not ready")

    def test_runtime_readiness_failures_preserve_error_precedence(self):
        for initialization_error, warmup_error, stopping, detail in (
            ("initialization failed", "warmup failed", False, "initialization failed"),
            (None, "warmup failed", False, "warmup failed"),
            (None, None, True, "model components are not ready"),
        ):
            with self.subTest(detail=detail):
                self.runtime.initialization_error = initialization_error
                self.runtime.warmup_error = warmup_error
                self.runtime.stopping = stopping

                response = self.client.get("/api/readyz")

                self.assert_not_ready(response, detail)

    def test_optional_provider_diagnostics_do_not_block_readiness(self):
        self.service.embeddingTool.sess.get_providers.side_effect = RuntimeError(
            "provider inspection failed"
        )

        response = self.client.get("/api/readyz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["components"]["embedding"]["providers"], [])
        self.assertTrue(response.json()["ok"])

    def test_unexpected_readiness_error_is_logged_and_mapped_to_503(self):
        with patch.object(
            health, "get_application_state", side_effect=RuntimeError("missing state")
        ):
            with self.assertLogs("svc.health", level="ERROR"):
                response = self.client.get("/api/readyz")

        self.assert_not_ready(response, "not ready: missing state")

    def test_liveness_version_and_config_do_not_require_models(self):
        self.runtime.food_service = None
        self.runtime.initialization_error = "models are unavailable"
        with patch.object(ApplicationState, "require_food_service") as require_service:
            response = self.client.get("/api/healthz")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"ok": True, "deps": NOT_WIRED_DEPS, **self.info})

            response = self.client.get("/api/livez")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"ok": True, **self.info})

            response = self.client.get("/api/version")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {
                "ok": True,
                "app_env": "test",
                "models": {"embedding": "embedding.onnx"},
                **self.info,
            })

            response = self.client.get("/api/config_info")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {
                "ok": True, "source": "test", **self.info
            })
            require_service.assert_not_called()
        self.service.embeddingTool.sess.get_providers.assert_not_called()

    def test_missing_optional_model_configuration_returns_null_paths(self):
        self.runtime.config = {"EMBEDDING_CONFIG": None}

        version = self.client.get("/api/version")
        readiness = self.client.get("/api/readyz")

        self.assertEqual(version.status_code, 200)
        self.assertEqual(version.json()["models"], {"embedding": None})
        self.assertEqual(readiness.status_code, 200)
        self.assertIsNone(readiness.json()["components"]["embedding"]["model_path"])

    def test_runtime_fields_keep_precedence_in_diagnostic_responses(self):
        self.runtime.config_info = {"ok": "config", "source": "test"}
        self.info.update({
            "ok": "runtime",
            "models": {"runtime": True},
            "components": {"runtime": True},
        })

        for path in (
            "/api/healthz", "/api/livez", "/api/readyz",
            "/api/version", "/api/config_info",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                for key, value in self.info.items():
                    self.assertEqual(response.json()[key], value)


if __name__ == "__main__":
    unittest.main()
