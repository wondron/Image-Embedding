"""验证应用资源不可用时的统一异常及 HTTP 错误响应。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.bootstrap.state import ApplicationState, get_application_state
from app.core import errors as err
from app.core.exceptions import register_exception_handlers
from app.core.middleware import TraceIdMiddleware


class ApplicationStateErrorTests(unittest.TestCase):
    def make_runtime(self):
        return ApplicationState(
            config={}, config_info={}, http_client=Mock(), settings=SimpleNamespace()
        )

    def make_client(self, application):
        register_exception_handlers(application)
        application.add_middleware(TraceIdMiddleware)
        client = TestClient(application)
        self.addCleanup(client.close)
        return client

    def assert_not_ready(self, response, detail):
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {
            "ok": False,
            "error": "NOT_READY",
            "detail": detail,
            "trace_id": "state-error-test",
        })
        self.assertEqual(response.headers["x-trace-id"], "state-error-test")

    def test_missing_or_invalid_application_state_returns_the_not_ready_envelope(self):
        application = FastAPI()

        @application.get("/state")
        def state_probe(request: Request):
            get_application_state(request)
            return {"ok": True}

        client = self.make_client(application)
        for runtime in (None, {"ready": True}):
            with self.subTest(runtime=runtime):
                if runtime is not None:
                    application.state.runtime = runtime
                response = client.get("/state", headers={"X-Trace-Id": "state-error-test"})
                self.assert_not_ready(
                    response, "application state is not initialized; lifespan must be running"
                )

    def test_unavailable_resources_preserve_their_failure_details(self):
        for method, error_field, default_detail in (
            ("require_food_service", "initialization_error", "food service is not initialized"),
            ("require_retrieval", "retrieval_error", "retrieval stack is not initialized"),
            ("require_detector", "detector_error", "GroundingDINO is disabled or not initialized"),
            ("require_catalog", "catalog_error", "catalog is not initialized"),
        ):
            for failure in (None, f"{error_field}: controlled startup failure"):
                with self.subTest(method=method, failure=failure):
                    runtime = self.make_runtime()
                    setattr(runtime, error_field, failure)
                    with self.assertRaises(err.AppError) as caught:
                        getattr(runtime, method)()
                    self.assertEqual(caught.exception.code, "NOT_READY")
                    self.assertEqual(caught.exception.http_status, 503)
                    self.assertEqual(caught.exception.detail, failure or default_detail)

    def test_resource_failures_reach_the_http_error_handler(self):
        application = FastAPI()
        application.state.runtime = self.make_runtime()

        @application.get("/resource/{name}")
        def resource_probe(request: Request, name: str):
            runtime = get_application_state(request)
            getattr(runtime, f"require_{name}")()
            return {"ok": True}

        client = self.make_client(application)
        for resource, error_field in (
            ("food_service", "initialization_error"),
            ("retrieval", "retrieval_error"),
            ("detector", "detector_error"),
            ("catalog", "catalog_error"),
        ):
            with self.subTest(resource=resource):
                detail = f"{resource} failed to initialize"
                setattr(application.state.runtime, error_field, detail)
                response = client.get(
                    f"/resource/{resource}", headers={"X-Trace-Id": "state-error-test"}
                )
                self.assert_not_ready(response, detail)

    def test_stopping_application_rejects_an_already_loaded_detector(self):
        runtime = self.make_runtime()
        runtime.detector = Mock()
        runtime.stopping = True
        with self.assertRaises(err.AppError) as caught:
            runtime.require_detector()
        self.assertEqual((caught.exception.code, caught.exception.http_status), ("NOT_READY", 503))


if __name__ == "__main__":
    unittest.main()
