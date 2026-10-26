import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.adapter.http.endpoints import health
from app.bootstrap.state import ApplicationState


class HealthEndpointTests(unittest.TestCase):
    def setUp(self):
        self.service = SimpleNamespace(
            segmentor=SimpleNamespace(sess=Mock()),
            clip=SimpleNamespace(sess=Mock()),
        )
        self.service.segmentor.sess.get_providers.return_value = [
            "CUDAExecutionProvider", "CPUExecutionProvider"
        ]
        self.service.clip.sess.get_providers.return_value = ["CPUExecutionProvider"]
        self.runtime = ApplicationState(
            config={
                "SEGMENT_CONFIG": {"model_path": "segment.onnx"},
                "CLIP_CONFIG": {
                    "model_path": "clip.onnx",
                    "text_model_path": "clip-text.onnx",
                },
            },
            config_info={"source": "test"},
            http_client=Mock(),
            settings=SimpleNamespace(application=SimpleNamespace(env="test")),
            food_service=self.service,
        )
        app = FastAPI()
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

    def test_ready_service_reports_models_providers_and_runtime(self):
        response = self.client.get("/api/readyz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "ok": True,
            "components": {
                "segmentor": {
                    "ok": True,
                    "providers": ["CUDAExecutionProvider", "CPUExecutionProvider"],
                    "model_path": "segment.onnx",
                },
                "clip": {
                    "ok": True,
                    "providers": ["CPUExecutionProvider"],
                    "model_path": "clip.onnx",
                    "text_model_path": "clip-text.onnx",
                },
            },
            "warmup_error": None,
            **self.info,
        })

    def test_unavailable_service_preserves_initialization_error(self):
        self.runtime.food_service = None
        self.runtime.initialization_error = "controlled initialization failure"

        response = self.client.get("/api/readyz")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {
            "detail": "not ready: controlled initialization failure"
        })

    def test_missing_model_components_are_not_ready(self):
        for component in ("segmentor", "clip"):
            with self.subTest(component=component):
                original = getattr(self.service, component)
                setattr(self.service, component, None)
                try:
                    response = self.client.get("/api/readyz")
                finally:
                    setattr(self.service, component, original)

                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {
                    "detail": "model components are not ready"
                })

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

                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {"detail": detail})

    def test_optional_provider_diagnostics_do_not_block_readiness(self):
        self.service.segmentor.sess.get_providers.side_effect = RuntimeError(
            "provider inspection failed"
        )
        self.service.clip.sess.get_providers.return_value = None

        response = self.client.get("/api/readyz")

        self.assertEqual(response.status_code, 200)
        components = response.json()["components"]
        self.assertEqual(components["segmentor"]["providers"], [])
        self.assertEqual(components["clip"]["providers"], [])
        self.assertTrue(response.json()["ok"])

    def test_unexpected_readiness_error_is_logged_and_mapped_to_503(self):
        with patch.object(
            health, "get_application_state", side_effect=RuntimeError("missing state")
        ):
            with self.assertLogs("svc.health", level="ERROR"):
                response = self.client.get("/api/readyz")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "not ready: missing state"})

    def test_liveness_version_and_config_do_not_require_models(self):
        self.runtime.food_service = None
        self.runtime.initialization_error = "models are unavailable"
        with patch.object(ApplicationState, "require_food_service") as require_service:
            for path in ("/api/healthz", "/api/livez"):
                with self.subTest(path=path):
                    response = self.client.get(path)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json(), {"ok": True, **self.info})

            response = self.client.get("/api/version")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {
                "ok": True,
                "app_env": "test",
                "models": {
                    "segment": "segment.onnx",
                    "clip": "clip.onnx",
                    "clip_text": "clip-text.onnx",
                },
                **self.info,
            })

            response = self.client.get("/api/config_info")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {
                "ok": True, "source": "test", **self.info
            })
            require_service.assert_not_called()
        self.service.segmentor.sess.get_providers.assert_not_called()
        self.service.clip.sess.get_providers.assert_not_called()

    def test_missing_optional_model_configuration_returns_null_paths(self):
        self.runtime.config = {"SEGMENT_CONFIG": None}

        version = self.client.get("/api/version")
        readiness = self.client.get("/api/readyz")

        self.assertEqual(version.status_code, 200)
        self.assertEqual(version.json()["models"], {
            "segment": None, "clip": None, "clip_text": None
        })
        self.assertEqual(readiness.status_code, 200)
        self.assertIsNone(readiness.json()["components"]["segmentor"]["model_path"])
        self.assertIsNone(readiness.json()["components"]["clip"]["text_model_path"])

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
