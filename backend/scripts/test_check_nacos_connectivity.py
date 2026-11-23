"""用假客户端和本地 Nacos HTTP 桩测试 Nacos 探测。"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import check_nacos_connectivity as probe

if str(probe.BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(probe.BACKEND_ROOT))

from app.core.config.constant import NACOS_DATA_ID_LIST
from app.core.config.nacos import NacosConfigError
from app.core.config.settings import NacosSettings


class NacosConnectivityTests(unittest.TestCase):
    def setUp(self):
        self.settings = NacosSettings(
            enabled=True,
            server_addr="http://nacos.invalid:8848",
            namespace="test-namespace",
            username="test-user",
            password="private-password",
            group="test-group",
            data_ids=("first", "second"),
        )
        self.client = Mock()
        self.client.get_config.return_value = "PASSWORD: remote-config-secret\nmarker: remote\n"

    def check(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch("app.core.config.nacos.NacosConfigClient", return_value=self.client) as constructor:
            result = probe.check_connection(self.settings)
        constructor.assert_called_once_with(self.settings)
        return result, output.getvalue()

    def offline(self, nacos):
        output = io.StringIO()
        settings = SimpleNamespace(nacos=nacos, application=SimpleNamespace(env="test"))
        with patch("app.core.config.settings.settings", settings), contextlib.redirect_stdout(output):
            with patch("app.core.config.nacos.NacosConfigClient") as constructor:
                result = probe.main(["--offline"])
        constructor.assert_not_called()
        return result, output.getvalue()

    def test_offline_validates_and_prints_summary_without_constructing_client(self):
        nacos = replace(self.settings, server_addr="[http://user:url-password@nacos.invalid:8848/nacos](http://ignored)")
        result, output = self.offline(nacos)
        self.assertEqual(result, probe.EXIT_OK)
        self.assertIn("OFFLINE", output)
        self.assertNotIn("PASS", output)
        for value in ("backend/.env", "test-namespace", "test-group", "first", "second"):
            self.assertIn(value, output)
        for secret in ("private-password", "url-password", "test-user"):
            self.assertNotIn(secret, output)
        summary = next(line.removeprefix("NACOS_CONFIG=") for line in output.splitlines() if line.startswith("NACOS_CONFIG="))
        self.assertEqual(json.loads(summary)["server_address"], "http://nacos.invalid:8848/nacos")

    def test_disabled_or_invalid_address_fails_preflight(self):
        result, output = self.offline(replace(self.settings, enabled=False))
        self.assertEqual(result, probe.EXIT_CONFIG)
        self.assertIn("NACOS_ENABLED=false", output)
        result, output = self.offline(replace(self.settings, server_addr="http://bad:70000"))
        self.assertEqual(result, probe.EXIT_CONFIG)
        self.assertIn("NACOS_SERVER_ADDR", output)

    def test_success_reads_all_documents_and_closes(self):
        result, output = self.check()
        self.assertEqual(result, probe.EXIT_OK)
        self.assertIn("PASS", output)
        self.assertNotIn("remote-config-secret", output)
        self.assertEqual([call.args[0] for call in self.client.get_config.call_args_list], ["first", "second"])
        self.client.close.assert_called_once_with()

    def test_authentication_failure_reports_http_status_without_response_body(self):
        self.client.get_config.side_effect = NacosConfigError(
            "Nacos authentication failed with HTTP 403 | response=private-password other-token"
        )
        result, output = self.check()
        self.assertEqual(result, probe.EXIT_CONNECT)
        self.assertIn("HTTP 403", output)
        self.assertNotIn("private-password", output)
        self.assertNotIn("other-token", output)
        self.client.close.assert_called_once_with()

    def test_failed_read_does_not_stop_later_data_ids_or_fall_back_to_yaml(self):
        self.client.get_config.side_effect = [TimeoutError("timeout accessToken=private-token"), "valid: mapping\n"]
        result, output = self.check()
        self.assertEqual(result, probe.EXIT_CONNECT)
        self.assertIn("TimeoutError", output)
        self.assertIn("dataId=second", output)
        self.assertNotIn("private-token", output)
        self.assertNotIn("PASS", output)
        self.assertEqual(self.client.get_config.call_count, 2)

    def test_missing_document_has_a_distinct_failure_code(self):
        self.client.get_config.side_effect = [None, "valid: mapping\n"]
        result, output = self.check()
        self.assertEqual(result, probe.EXIT_DOCUMENT)
        self.assertIn("namespace", output)
        self.assertNotIn("PASS", output)

    def test_invalid_yaml_and_non_mapping_documents_never_echo_source_lines(self):
        self.client.get_config.side_effect = ["secret: [remote-secret", "- remote-secret"]
        result, output = self.check()
        self.assertEqual(result, probe.EXIT_DOCUMENT)
        self.assertIn("line=", output)
        self.assertNotIn("remote-secret", output)
        self.assertNotIn("PASS", output)

    def test_interruption_still_closes_the_client(self):
        self.client.get_config.side_effect = KeyboardInterrupt()
        with patch("app.core.config.nacos.NacosConfigClient", return_value=self.client), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                probe.check_connection(self.settings)
        self.client.close.assert_called_once_with()

    def test_cli_uses_process_environment_and_project_data_ids_with_real_client(self):
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, content):
                body = content.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                data = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
                calls.append(("POST", self.path, data))
                self.respond(json.dumps({"accessToken": "local-test-token"}))

            def do_GET(self):
                parsed = urlsplit(self.path)
                calls.append(("GET", parsed.path, parse_qs(parsed.query), self.headers.get("Authorization")))
                self.respond("secret: should-not-be-printed\nvalid: mapping\n")

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = subprocess.run(
                [sys.executable, str(probe.BACKEND_ROOT / "scripts/check_nacos_connectivity.py")],
                cwd=probe.BACKEND_ROOT.parent,
                env={
                    **os.environ,
                    "NACOS_ENV": "test",
                    "NACOS_ENABLED": "true",
                    "NACOS_SERVER_ADDR": f"http://127.0.0.1:{server.server_port}",
                    "NACOS_NAMESPACE": self.settings.namespace,
                    "NACOS_GROUP": self.settings.group,
                    "NACOS_USERNAME": self.settings.username,
                    "NACOS_PASSWORD": self.settings.password,
                },
                capture_output=True,
                encoding="utf-8",
                timeout=15,
            )
            self.assertEqual(result.returncode, probe.EXIT_OK, result.stdout + result.stderr)
            self.assertEqual([call[0] for call in calls], ["POST"] + ["GET"] * len(NACOS_DATA_ID_LIST))
            self.assertEqual(calls[0][1], "/nacos/v1/auth/login")
            self.assertEqual(calls[0][2], {"username": ["test-user"], "password": ["private-password"]})
            for call, data_id in zip(calls[1:], NACOS_DATA_ID_LIST):
                self.assertEqual(call[1], "/nacos/v1/cs/configs")
                self.assertEqual(call[2], {"dataId": [data_id], "group": ["test-group"], "tenant": ["test-namespace"], "accessToken": ["local-test-token"]})
                self.assertEqual(call[3], "Bearer local-test-token")
            self.assertNotIn("should-not-be-printed", result.stdout)
            self.assertNotIn("local-test-token", result.stdout)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
