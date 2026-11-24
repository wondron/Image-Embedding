"""用假客户端和本地只读 HTTP 服务器检查 Qdrant 探测。"""

from __future__ import annotations

import contextlib
import io
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from unittest.mock import Mock, patch

import check_qdrant_connectivity as probe
from app.clients.qdrant import QdrantConnection

CONFIG = {"QDRANT_CONFIG": {"url": "http://q.internal:6333", "api_key": "private-api-key", "timeout": 2, "prefer_grpc": False}}
SOURCE = {"source": "nacos", "nacos_profile": "test", "yaml_base": True}


class QdrantConnectivityTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(os.chdir, os.getcwd())
        self.client = Mock()
        self.client.get_collections.return_value = SimpleNamespace(collections=[SimpleNamespace(name="images")])
        self.connection = Mock()
        self.connection.create_client.return_value = self.client

    def run_main(self, args, config=CONFIG):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(probe, "load_effective_qdrant_config", return_value=(config, SOURCE)):
            with patch.object(probe, "parse_connection", wraps=probe.parse_connection), patch.object(
                QdrantConnection, "create_client", return_value=self.client,
            ) as create:
                result = probe.main(args)
        return result, output.getvalue(), create

    def test_uses_production_connection_parameters_without_overrides(self):
        connection = probe.parse_connection(CONFIG)
        self.assertEqual((connection.url, connection.api_key, connection.timeout, connection.prefer_grpc),
                         ("http://q.internal:6333", "private-api-key", 2.0, False))
        with patch("qdrant_client.QdrantClient") as constructor:
            connection.create_client()
        constructor.assert_called_once_with(url=connection.url, api_key=connection.api_key, timeout=2.0,
                                            prefer_grpc=False, check_compatibility=False)

    def test_host_port_config_matches_production_parsing(self):
        connection = probe.parse_connection({"QDRANT_CONFIG": {"host": "qdrant", "port": 6333}})
        self.assertEqual(connection.url, "http://qdrant:6333")
        self.assertTrue(connection.prefer_grpc)

    def test_missing_invalid_and_memory_configs_are_rejected(self):
        cases = [None, [], {}, {"api_key": "private-api-key"}, {"url": ":memory:"},
                 {"url": "not-a-url"}, {"url": "http://q:0"}, {"url": "http://q:70000"}]
        cases.extend({"url": "http://q:6333", "timeout": value} for value in (0, -1, float("inf"), float("nan"), "bad"))
        for section in cases:
            with self.subTest(section=section), self.assertRaises((ValueError, TypeError)):
                probe.parse_connection({"QDRANT_CONFIG": section})

    def test_summary_redacts_key_and_url_credentials(self):
        config = {"QDRANT_CONFIG": {**CONFIG["QDRANT_CONFIG"], "url": "https://user:url-password@q:6333?token=url-token"}}
        output = "\n".join(probe.summarize_effective(config, SOURCE))
        self.assertIn("config_source=nacos", output)
        self.assertIn("nacos_profile=test", output)
        self.assertIn('"api_key": "***"', output)
        for secret in ("private-api-key", "url-password", "url-token"):
            self.assertNotIn(secret, output)

    def test_config_loader_uses_the_api_provider_and_always_closes_it(self):
        from app.core.config.settings import settings

        provider = Mock()
        provider.load.return_value = (CONFIG, SOURCE)
        with patch("app.core.config.nacos.NacosConfigProvider", return_value=provider) as constructor:
            self.assertEqual(probe.load_effective_qdrant_config(), (CONFIG, SOURCE))
        constructor.assert_called_once_with(settings.nacos)
        provider.load.assert_called_once_with(settings.config.yaml_path)
        provider.close.assert_called_once()
        provider.reset_mock()
        provider.load.side_effect = ValueError("bad YAML")
        with patch("app.core.config.nacos.NacosConfigProvider", return_value=provider), self.assertRaises(ValueError):
            probe.load_effective_qdrant_config()
        provider.close.assert_called_once()

    def test_offline_does_not_construct_a_qdrant_client(self):
        result, output, create = self.run_main(["--offline"])
        self.assertEqual(result, probe.EXIT_OK)
        self.assertIn("OFFLINE", output)
        self.assertNotIn("PASS", output)
        create.assert_not_called()

    def test_success_queries_collections_and_closes_client(self):
        result, output, _ = self.run_main([])
        self.assertEqual(result, probe.EXIT_OK)
        self.assertIn('collection_names=["images"]', output)
        self.assertIn("PASS", output)
        self.assertNotIn("private-api-key", output)
        self.client.get_collections.assert_called_once_with()
        self.client.get_collection.assert_not_called()
        self.client.close.assert_called_once_with()

    def test_empty_server_is_a_successful_connection(self):
        self.client.get_collections.return_value = SimpleNamespace(collections=[])
        result, output, _ = self.run_main([])
        self.assertEqual(result, probe.EXIT_OK)
        self.assertIn("collections=0", output)

    def test_optional_collection_is_read_without_writes(self):
        self.client.get_collection.return_value = SimpleNamespace(status=SimpleNamespace(value="green"), points_count=42)
        result, output, _ = self.run_main(["--collection", " images "])
        self.assertEqual(result, probe.EXIT_OK)
        self.assertIn("status=green points_count=42", output)
        self.client.get_collection.assert_called_once_with(collection_name="images")
        self.assertEqual([call[0] for call in self.client.method_calls], ["get_collections", "get_collection", "close"])

    def test_timeout_failure_reports_message_and_masks_echoed_key(self):
        self.client.get_collections.side_effect = TimeoutError("timeout api_key=private-api-key")
        result, output, _ = self.run_main([])
        self.assertEqual(result, probe.EXIT_CONNECT)
        self.assertIn("TimeoutError", output)
        self.assertNotIn("private-api-key", output)
        self.assertNotIn("PASS", output)
        self.client.close.assert_called_once_with()

    def test_authentication_failure_has_a_specific_hint(self):
        self.client.get_collections.side_effect = RuntimeError("UNAUTHENTICATED")
        result, output, _ = self.run_main([])
        self.assertEqual(result, probe.EXIT_CONNECT)
        self.assertIn("QDRANT_CONFIG.api_key", output)

    def test_missing_collection_has_a_distinct_exit_code(self):
        self.client.get_collection.side_effect = RuntimeError("collection not found")
        result, output, _ = self.run_main(["--collection", "missing"])
        self.assertEqual(result, probe.EXIT_COLLECTION)
        self.assertIn("collection not found", output)
        self.assertNotIn("PASS", output)
        self.client.close.assert_called_once_with()

    def test_bad_config_prints_diagnostics_and_never_connects(self):
        result, output, create = self.run_main([], {"QDRANT_CONFIG": {"timeout": "bad"}})
        self.assertEqual(result, probe.EXIT_CONFIG)
        self.assertIn('"timeout": "bad"', output)
        create.assert_not_called()

    def test_yaml_errors_do_not_echo_source_credentials(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(probe, "load_effective_qdrant_config", side_effect=ValueError("api_key: secret-from-yaml")):
            result = probe.main([])
        self.assertEqual(result, probe.EXIT_CONFIG)
        self.assertNotIn("secret-from-yaml", output.getvalue())

    def test_client_constructor_failure_has_a_config_exit_code(self):
        self.connection.create_client.side_effect = RuntimeError("qdrant-client is not installed")
        with contextlib.redirect_stdout(io.StringIO()):
            result = probe.check_connection(self.connection, CONFIG, None)
        self.assertEqual(result, probe.EXIT_CONFIG)

    def test_interruption_still_closes_the_client(self):
        self.client.get_collections.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            probe.check_connection(self.connection, CONFIG, None)
        self.client.close.assert_called_once_with()

    def test_real_sdk_reads_a_local_http_server(self):
        paths = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                paths.append(self.path)
                body = json.dumps({"result": {"collections": [{"name": "local_test"}]}, "status": "ok", "time": 0.001}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            config = {"QDRANT_CONFIG": {"url": f"http://127.0.0.1:{server.server_port}", "prefer_grpc": False, "timeout": 2}}
            output = io.StringIO()
            # 本机设了系统代理（Windows 注册表）时 httpx 会把 127.0.0.1 也转给代理
            with contextlib.redirect_stdout(output), patch.dict(os.environ, {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}):
                result = probe.check_connection(probe.parse_connection(config), config, None)
            self.assertEqual(result, probe.EXIT_OK, output.getvalue())
            self.assertEqual(paths, ["/collections"])
            self.assertIn("local_test", output.getvalue())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
