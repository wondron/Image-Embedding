"""app.core.errors is the one registry of error codes, and it decides what a client may see."""

import inspect
import re
import unittest
from pathlib import Path

from app.core import errors
from app.core.errors import ERROR_CODES, AppError, client_detail, code_for_status, is_internal

APP = Path(__file__).resolve().parents[1] / "app"
# Where a code appears as a literal: AppError("X", ...), error_response(request, "X", ...), {"error": "X"}
INLINE_CODE = re.compile(
    r"""\bAppError\(\s*["']([A-Z][A-Z0-9_]+)["']"""
    r"""|error_response\(\s*\w+,\s*["']([A-Z][A-Z0-9_]+)["']"""
    r"""|["']error["']\s*:\s*["']([A-Z][A-Z0-9_]+)["']"""
)


def constructors():
    for name, fn in inspect.getmembers(errors, inspect.isfunction):
        if fn.__module__ == errors.__name__ and not name.startswith("_") and name not in {
            "client_detail", "is_internal", "code_for_status",
        }:
            yield name, fn


class RegistryTests(unittest.TestCase):
    def test_every_constructor_raises_a_registered_code_with_its_status(self):
        for name, fn in constructors():
            with self.subTest(constructor=name):
                exc = fn("detail") if inspect.signature(fn).parameters else fn()
                self.assertIn(exc.code, ERROR_CODES)
                self.assertEqual(exc.http_status, ERROR_CODES[exc.code].http_status)

    def test_every_code_written_inline_in_the_app_is_registered(self):
        found = set()
        for path in APP.rglob("*.py"):
            for match in INLINE_CODE.finditer(path.read_text(encoding="utf-8")):
                found.add(next(group for group in match.groups() if group))
        self.assertTrue({"PREDICT_FAILED", "TOOL_FAILED", "INVALID_IMAGE"} <= found)
        self.assertEqual(sorted(found - set(ERROR_CODES)), [])

    def test_framework_status_codes_map_to_registered_codes(self):
        for status in (400, 401, 403, 404, 405, 413, 415, 422, 429, 500, 502, 503, 504):
            with self.subTest(status=status):
                self.assertIn(code_for_status(status), ERROR_CODES)

    def test_status_defaults_to_the_registry_and_can_be_overridden(self):
        self.assertEqual(AppError("INDEX_BAD_IMAGE", "x").http_status, 422)
        self.assertEqual(errors.upstream_error("x", http_status=503).http_status, 503)
        self.assertEqual(AppError("NOT_REGISTERED", "x").http_status, 500)


class ClientDetailTests(unittest.TestCase):
    def test_internal_codes_show_their_public_message(self):
        for code in ("INDEX_POSTGRES_FAILED", "SEARCH_QDRANT_FAILED", "PREDICT_FAILED", "TOOL_FAILED"):
            with self.subTest(code=code):
                exc = AppError(code, "IntegrityError: [SQL: INSERT ...] [parameters: ...]")
                self.assertTrue(is_internal(exc))
                self.assertEqual(client_detail(exc), ERROR_CODES[code].public_detail)

    def test_other_codes_show_their_detail(self):
        for exc in (errors.validation_error("top_k must be in 1..50"), errors.not_ready("models are loading"),
                    errors.download_failed("Image download failed (upstream HTTP 404).")):
            with self.subTest(code=exc.code):
                self.assertFalse(is_internal(exc))
                self.assertEqual(client_detail(exc), exc.detail)

    def test_unregistered_codes_are_trusted_only_below_500(self):
        self.assertEqual(client_detail(AppError("SOMETHING_FAILED", "went wrong", http_status=409)), "went wrong")
        self.assertEqual(client_detail(AppError("SOMETHING_FAILED", "secret", http_status=500)), "internal server error")


if __name__ == "__main__":
    unittest.main()
