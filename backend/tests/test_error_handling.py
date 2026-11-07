"""One error envelope and one trace id for every failure (core.exceptions + core.middleware)."""

import unittest

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core import errors
from app.core.errors import AppError
from app.core.exceptions import register_exception_handlers
from app.core.middleware import TraceIdMiddleware, bind_trace_id, register_middlewares

SQL_DETAIL = (
    "insert_image: IntegrityError: duplicate key value violates unique constraint\n"
    "[SQL: INSERT INTO images (id, sha256) VALUES (%(id)s, %(sha)s)]\n[parameters: {'sha': b'secret-bytes'}]"
)


def build_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(TraceIdMiddleware)

    @app.get("/app-error")
    def app_error():
        raise AppError("SOMETHING_FAILED", "went wrong", http_status=409)

    @app.get("/domain-error")
    def domain_error():
        raise errors.search_unknown_scene("unknown scene: 'x'")

    @app.get("/internal-error")
    def internal():
        try:
            raise KeyError("driver state")
        except KeyError as exc:
            raise errors.index_postgres_failed(SQL_DETAIL) from exc

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret internals")

    @app.get("/typed")
    def typed(n: int):
        return {"n": n}

    @app.get("/rebind")
    def rebind(request: Request, trace_id: str):
        bind_trace_id(request, trace_id)
        raise AppError("SOMETHING_FAILED", "went wrong", http_status=400)

    @app.get("/ok")
    def ok():
        return {"ok": True}

    return app


class ErrorEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(build_app(), raise_server_exceptions=False)
        self.addCleanup(self.client.close)

    def assert_envelope(self, response, status, code, detail=None):
        self.assertEqual(response.status_code, status)
        body = response.json()
        self.assertEqual((body["ok"], body["error"]), (False, code))
        if detail is not None:
            self.assertEqual(body["detail"], detail)
        self.assertTrue(body["trace_id"])
        self.assertEqual(body["trace_id"], response.headers["x-trace-id"])
        return body

    def test_app_error_and_domain_errors_share_the_envelope(self):
        self.assert_envelope(self.client.get("/app-error"), 409, "SOMETHING_FAILED", "went wrong")
        self.assert_envelope(self.client.get("/domain-error"), 422, "SEARCH_UNKNOWN_SCENE", "unknown scene: 'x'")

    def test_unexpected_errors_are_logged_but_not_leaked(self):
        with self.assertLogs("api.errors", level="ERROR") as logs:
            response = self.client.get("/boom")
        body = self.assert_envelope(response, 500, "INTERNAL_ERROR", "internal server error")
        self.assertNotIn("secret", response.text)
        self.assertIn(body["trace_id"], "\n".join(logs.output))

    def test_internal_codes_keep_sql_and_cause_in_the_log_only(self):
        with self.assertLogs("api.errors", level="ERROR") as logs:
            response = self.client.get("/internal-error")
        body = self.assert_envelope(response, 502, "INDEX_POSTGRES_FAILED", "metadata database error")
        self.assertNotIn("SQL", response.text)
        self.assertNotIn("secret-bytes", response.text)
        log = "\n".join(logs.output)
        self.assertIn("[SQL: INSERT INTO images", log)
        self.assertIn("KeyError: 'driver state'", log)  # the cause chain is logged too
        self.assertIn(body["trace_id"], log)

    def test_request_validation_summarizes_fields_without_echoing_the_body(self):
        body = self.assert_envelope(self.client.get("/typed?n=abc"), 422, "VALIDATION_ERROR")
        self.assertTrue(body["detail"].startswith("query.n: "))
        self.assertEqual(body["errors"][0]["loc"], ["query", "n"])
        self.assertNotIn("body", body)

    def test_framework_http_errors_get_codes_and_keep_headers(self):
        self.assert_envelope(self.client.get("/missing"), 404, "NOT_FOUND")
        response = self.client.post("/ok")
        self.assert_envelope(response, 405, "METHOD_NOT_ALLOWED")
        self.assertIn("GET", response.headers["allow"])


class TraceIdTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(build_app(), raise_server_exceptions=False)
        self.addCleanup(self.client.close)

    def test_every_response_carries_a_trace_id(self):
        first = self.client.get("/ok").headers["x-trace-id"]
        second = self.client.get("/ok").headers["x-trace-id"]
        self.assertRegex(first, r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
        self.assertNotEqual(first, second)

    def test_valid_client_trace_id_is_adopted(self):
        response = self.client.get("/app-error", headers={"X-Trace-Id": "client-abc.123"})
        self.assertEqual(response.json()["trace_id"], "client-abc.123")
        self.assertEqual(response.headers["x-trace-id"], "client-abc.123")

    def test_unsafe_client_trace_id_is_replaced(self):
        for bad in ("has space", "x" * 65, "semi;colon"):
            with self.subTest(bad=bad):
                trace_id = self.client.get("/app-error", headers={"X-Trace-Id": bad}).json()["trace_id"]
                self.assertNotEqual(trace_id, bad)
                self.assertRegex(trace_id, r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")

    def test_endpoint_can_rebind_the_trace_id(self):
        response = self.client.get("/rebind?trace_id=t-77")
        self.assertEqual(response.json()["trace_id"], "t-77")
        self.assertEqual(response.headers["x-trace-id"], "t-77")


class FullMiddlewareStackTests(unittest.TestCase):
    """register_middlewares + register_exception_handlers, as create_app wires them."""

    def setUp(self):
        app = FastAPI()
        register_middlewares(app)
        register_exception_handlers(app)

        @app.get("/boom")
        def boom():
            raise RuntimeError("secret internals")

        @app.get("/app-error")
        def app_error():
            raise errors.upstream_timeout("upstream timed out")

        self.client = TestClient(app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)

    def test_unexpected_500_keeps_cors_headers_and_the_envelope(self):
        with self.assertLogs("api.errors", level="ERROR"):
            response = self.client.get("/boom", headers={"Origin": "http://localhost:5173"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.headers.get("access-control-allow-origin"), "*")
        body = response.json()
        self.assertEqual((body["error"], body["detail"]), ("INTERNAL_ERROR", "internal server error"))
        self.assertEqual(body["trace_id"], response.headers["x-trace-id"])
        self.assertNotIn("secret", response.text)

    def test_app_errors_keep_cors_headers(self):
        response = self.client.get("/app-error", headers={"Origin": "http://localhost:5173"})
        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.headers.get("access-control-allow-origin"), "*")


if __name__ == "__main__":
    unittest.main()
