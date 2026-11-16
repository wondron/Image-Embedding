"""Startup diagnostics identify the dependency and never print its credentials."""

import copy
import logging
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import quote

from app.bootstrap import catalog, lifespan
from app.bootstrap.dependency_logging import config_summary, log_startup_exception, redact_text
from app.core.errors import AppError


CONFIG = {
    "POSTGRES_CONFIG": {
        "host": "pg.internal", "user": "image_user", "database": "images", "password": "pg@secret/pass",
    },
    "QDRANT_CONFIG": {
        "host": "qdrant.internal", "port": 6333, "api_key": "private-api-key",
    },
}


class DependencyLoggingTests(unittest.TestCase):
    def test_summary_uses_the_actual_defaults_and_host_port_endpoint(self):
        pg = config_summary(CONFIG, "POSTGRES_CONFIG")
        self.assertEqual((pg["host"], pg["port"], pg["connect_timeout"]), ("pg.internal", 5432, 5))
        self.assertEqual(pg["password"], "***")
        qdrant = config_summary(CONFIG, "QDRANT_CONFIG")
        self.assertEqual(qdrant["url"], "http://qdrant.internal:6333")
        self.assertEqual((qdrant["timeout"], qdrant["prefer_grpc"]), (30.0, True))
        self.assertEqual(qdrant["api_key"], "***")

    def test_invalid_values_and_sections_are_reported_without_hiding_the_original_problem(self):
        config = copy.deepcopy(CONFIG)
        config["POSTGRES_CONFIG"]["port"] = "bad-port"
        self.assertEqual(config_summary(config, "POSTGRES_CONFIG")["port"], "bad-port")
        config["QDRANT_CONFIG"]["timeout"] = "bad-timeout"
        self.assertEqual(config_summary(config, "QDRANT_CONFIG")["timeout"], "bad-timeout")
        self.assertEqual(config_summary({}, "QDRANT_CONFIG"), {"status": "missing"})
        for name in CONFIG:
            with self.subTest(name=name):
                self.assertIn("expected mapping", config_summary({name: ["private-value"]}, name)["status"])

    def test_absent_qdrant_endpoint_is_not_replaced_by_localhost_in_diagnostics(self):
        summary = config_summary({"QDRANT_CONFIG": {"api_key": "private-api-key"}}, "QDRANT_CONFIG")
        self.assertIsNone(summary["url"])
        self.assertEqual(summary["api_key"], "***")

    def test_url_auth_query_tokens_and_echoed_credentials_are_masked(self):
        config = copy.deepcopy(CONFIG)
        config["QDRANT_CONFIG"]["url"] = "https://admin:url-pass@q.internal:6333/path?token=url-token&mode=test"
        url = config_summary(config, "QDRANT_CONFIG")["url"]
        self.assertEqual(url, "https://***@q.internal:6333/path?token=***&mode=test")
        text = redact_text(
            "failed password=pg@secret/pass encoded=" + quote("pg@secret/pass", safe="")
            + " api_key=private-api-key at " + config["QDRANT_CONFIG"]["url"], config,
        )
        for secret in ("pg@secret/pass", "private-api-key", "url-pass", "url-token", quote("pg@secret/pass", safe="")):
            self.assertNotIn(secret, text)

    def test_exception_chain_traceback_is_retained_and_redacted(self):
        try:
            try:
                raise ValueError("driver echoed private-api-key")
            except ValueError as cause:
                raise RuntimeError("connection failed with pg@secret/pass") from cause
        except RuntimeError as exc:
            with self.assertLogs("lifespan", level="ERROR") as logs:
                log_startup_exception(logging.getLogger("lifespan"), "startup failed", exc, CONFIG)
        message = "\n".join(logs.output)
        self.assertIn("ValueError", message)
        self.assertIn("RuntimeError", message)
        self.assertNotIn("private-api-key", message)
        self.assertNotIn("pg@secret/pass", message)

    def test_catalog_timeout_logs_connection_and_keeps_existing_degraded_startup(self):
        failure = AppError("INDEX_POSTGRES_FAILED", "connection timeout expired", http_status=502)
        store = Mock()
        store.ensure_schema.side_effect = failure
        runtime = SimpleNamespace(config=CONFIG)
        with patch.object(catalog, "PgMetadataStore", return_value=store):
            with self.assertLogs(level="WARNING") as logs:
                lifespan.initialize_catalog(runtime)
        message = "\n".join(logs.output)
        self.assertIn("POSTGRES_CONFIG", message)
        self.assertIn("connection timeout expired", message)
        self.assertIn("pg.internal", message)
        self.assertNotIn("pg@secret/pass", message)
        self.assertIsNone(runtime.catalog)
        self.assertEqual(runtime.catalog_error, "INDEX_POSTGRES_FAILED: metadata database error")
        store.close.assert_called_once()

    def test_catalog_invalid_config_logs_fields_without_creating_a_store(self):
        config = copy.deepcopy(CONFIG)
        config["POSTGRES_CONFIG"]["port"] = "not-a-number"
        runtime = SimpleNamespace(config=config)
        with patch.object(catalog, "PgMetadataStore") as store:
            with self.assertLogs(level="ERROR") as logs:
                lifespan.initialize_catalog(runtime)
        message = "\n".join(logs.output)
        self.assertIn("POSTGRES_CONFIG.port must be a number", message)
        self.assertIn('"port": "not-a-number"', message)
        self.assertNotIn("pg@secret/pass", message)
        self.assertIsNone(runtime.catalog)
        store.assert_not_called()

    def test_unserializable_or_overflowing_values_do_not_replace_the_configuration_error(self):
        for value, expected in ((date(2026, 11, 16), ValueError), (float("inf"), OverflowError)):
            config = copy.deepcopy(CONFIG)
            config["POSTGRES_CONFIG"]["port"] = value
            with self.subTest(value=value), self.assertLogs("catalog", level="ERROR") as logs:
                with self.assertRaises(expected):
                    catalog.build_catalog(config)
            self.assertIn("POSTGRES_CONFIG", logs.output[0])
            self.assertIn(expected.__name__, logs.output[0])


if __name__ == "__main__":
    unittest.main()
