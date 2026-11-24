"""用假客户端和本地 HTTP 服务器检查直接给地址的 Qdrant 连通检查。"""

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

import check_qdrant_endpoint as probe
from app.clients.qdrant import QdrantConnection

URL = "http://q.internal:6333"
KEY = "private-api-key"


def fake_client() -> Mock:
    client = Mock()
    client.info.return_value = SimpleNamespace(version="1.13.2")
    client.get_collections.return_value = SimpleNamespace(collections=[SimpleNamespace(name="images")])
    return client


class EndpointCheckTests(unittest.TestCase):
    def setUp(self):
        self.rest, self.grpc = fake_client(), fake_client()
        self.connections = []

    def create(self, connection):
        self.connections.append(connection)
        return self.grpc if connection.prefer_grpc else self.rest

    def run_main(self, args, env=None):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.dict(os.environ, env or {}), patch.object(
            QdrantConnection, "create_client", autospec=True, side_effect=self.create,
        ):
            result = probe.main(args)
        return result, output.getvalue()

    def test_checks_rest_then_grpc_with_the_given_address_and_key(self):
        result, output = self.run_main([URL, "--api-key", KEY])
        self.assertEqual(result, probe.base.EXIT_OK, output)
        self.assertEqual([(c.url, c.api_key, c.timeout, c.prefer_grpc) for c in self.connections],
                         [(URL, KEY, 5.0, False), (URL, KEY, 5.0, True)])
        self.assertIn("端口 6333", output)
        self.assertIn("端口 6334", output)
        self.assertIn("Qdrant 1.13.2", output)
        self.assertIn("images", output)
        self.assertIn("PASS", output)
        self.assertNotIn(KEY, output)
        for client in (self.rest, self.grpc):
            client.get_collections.assert_called_once_with()
            client.get_collection.assert_not_called()
            client.close.assert_called_once_with()

    def test_key_can_come_from_the_environment_and_is_optional(self):
        self.run_main([URL], env={"QDRANT_API_KEY": KEY})
        self.assertEqual({c.api_key for c in self.connections}, {KEY})
        self.connections.clear()
        with patch.dict(os.environ, {}, clear=True):
            self.run_main([URL])
        self.assertEqual({c.api_key for c in self.connections}, {None})

    def test_rest_port_follows_the_address(self):
        _, output = self.run_main(["http://q.internal:16333/"])
        self.assertIn("端口 16333", output)
        self.assertEqual(self.connections[0].url, "http://q.internal:16333")

    def test_grpc_unreachable_says_the_backend_will_not_connect(self):
        self.grpc.info.side_effect = RuntimeError("StatusCode.UNAVAILABLE failed to connect")
        result, output = self.run_main([URL, "--api-key", KEY])
        self.assertEqual(result, probe.base.EXIT_CONNECT)
        self.assertIn("REST 通、gRPC 不通", output)
        self.assertIn("prefer_grpc: false", output)
        self.assertNotIn("PASS", output)
        self.grpc.close.assert_called_once_with()

    def test_wrong_key_has_the_key_hint_and_never_echoes_it(self):
        for client in (self.rest, self.grpc):
            client.get_collections.side_effect = RuntimeError(f"UNAUTHENTICATED api-key {KEY}")
        result, output = self.run_main([URL, "--api-key", KEY])
        self.assertEqual(result, probe.base.EXIT_CONNECT)
        self.assertIn("api_key", output)
        self.assertNotIn(KEY, output)

    def test_collection_is_read_on_both_transports(self):
        for client in (self.rest, self.grpc):
            client.get_collection.return_value = SimpleNamespace(status=SimpleNamespace(value="green"), points_count=42)
        result, output = self.run_main([URL, "--collection", " images "])
        self.assertEqual(result, probe.base.EXIT_OK)
        self.assertEqual(output.count("images：status=green points_count=42"), 2)
        for client in (self.rest, self.grpc):
            client.get_collection.assert_called_once_with(collection_name="images")

    def test_missing_collection_has_its_own_exit_code(self):
        for client in (self.rest, self.grpc):
            client.get_collection.side_effect = RuntimeError("Not found: Collection `missing` doesn't exist!")
        result, output = self.run_main([URL, "--collection", "missing"])
        self.assertEqual(result, probe.base.EXIT_COLLECTION)
        self.assertIn("doesn't exist", output)

    def test_connection_failure_wins_over_collection_failure(self):
        self.rest.get_collection.side_effect = RuntimeError("missing")
        self.grpc.info.side_effect = TimeoutError("timed out")
        result, _ = self.run_main([URL, "--collection", "images"])
        self.assertEqual(result, probe.base.EXIT_CONNECT)

    def test_bad_address_never_connects(self):
        for url in ("10.0.0.8:6333", "http://q:70000", ":memory:"):
            with self.subTest(url=url):
                result, output = self.run_main([url])
                self.assertEqual(result, probe.base.EXIT_CONFIG)
                self.assertIn("地址不对", output)
        self.assertEqual(self.connections, [])

    def test_bad_arguments_are_rejected(self):
        for args in ([URL, "--timeout", "0"], [URL, "--timeout", "nan"], [URL, "--collection", " "]):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                probe.main(args)

    def test_real_sdk_reads_version_and_collections_over_rest(self):
        paths = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                paths.append((self.path, self.headers.get("api-key")))
                result = {"collections": [{"name": "local_test"}]} if self.path == "/collections" else None
                body = {"result": result, "status": "ok", "time": 0.001} if result else {"title": "qdrant", "version": "1.13.2"}
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            config = {"QDRANT_CONFIG": {"url": f"http://127.0.0.1:{server.server_port}", "api_key": KEY,
                                        "prefer_grpc": False, "timeout": 2}}
            output = io.StringIO()
            with contextlib.redirect_stdout(output), patch.dict(os.environ, {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}):
                result = probe.probe(probe.base.parse_connection(config), config, None)
            self.assertEqual(result, probe.base.EXIT_OK, output.getvalue())
            self.assertEqual(paths, [("/", KEY), ("/collections", KEY)])
            self.assertIn("Qdrant 1.13.2", output.getvalue())
            self.assertIn("local_test", output.getvalue())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
