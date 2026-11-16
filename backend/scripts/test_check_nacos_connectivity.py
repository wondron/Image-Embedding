"""Nacos probe tests using fake clients and a local Nacos HTTP stub."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import check_nacos_connectivity as probe

PROFILE = {
    "profile": "test", "server_addresses": "http://nacos.invalid:8848", "namespace": "test-namespace",
    "username": "test-user", "password": "private-password", "group": "test-group", "data_id_list": ["first", "second"],
}


def settings(profile=PROFILE, **overrides):
    return SimpleNamespace(profile="test", profiles=(profile,), enabled=True, poll_enabled=True,
                           connect_timeout_seconds=1, request_timeout_seconds=1, **overrides)


class NacosConnectivityTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(os.chdir, os.getcwd())
        self.profile = copy.deepcopy(PROFILE)
        self.settings = settings(self.profile)
        self.client = Mock()
        self.client._access_token = "private-token"
        self.client.get_config.return_value = "PASSWORD: remote-config-secret\nmarker: remote\n"

    def check(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch("app.core.config.nacos.NacosConfigClient", return_value=self.client):
            result = probe.check_connection(self.settings, self.profile)
        return result, output.getvalue()

    def test_settings_take_profiles_and_selected_environment_from_the_script(self):
        with patch.dict(os.environ, {"NACOS_ENV": "test", "NACOS_SERVER_ADDR": "http://ignored:8848", "NACOS_PASSWORD": "ignored", "NACOS_ENABLED": "true"}):
            loaded = probe.load_nacos_settings()
        self.assertEqual(loaded.profile, probe.NACOS_PROFILE)
        self.assertIs(loaded.profiles, probe.NACOS_CONFIG_PROFILES)

    def test_offline_validates_without_constructing_the_client(self):
        output = io.StringIO()
        with patch.object(probe, "load_nacos_settings", return_value=self.settings), contextlib.redirect_stdout(output):
            with patch("app.core.config.nacos.NacosConfigClient") as constructor:
                result = probe.main(["--offline"])
        self.assertEqual(result, probe.EXIT_OK)
        self.assertIn("OFFLINE", output.getvalue())
        self.assertNotIn("PASS", output.getvalue())
        constructor.assert_not_called()

    def test_missing_disabled_or_invalid_profiles_fail_prerequisites(self):
        self.assertIn("NACOS_PROFILE", " ".join(probe.prerequisite_failures(self.settings, None)))
        self.settings.enabled = False
        self.assertIn("NACOS_ENABLED=false", " ".join(probe.prerequisite_failures(self.settings, self.profile)))
        self.settings.enabled = True
        for key, value in (("server_addresses", "http://bad:70000"), ("group", ""), ("data_id_list", []), ("password", "")):
            profile = {**self.profile, key: value}
            with self.subTest(key=key):
                self.assertTrue(probe.prerequisite_failures(self.settings, profile))
        self.settings.connect_timeout_seconds = float("inf")
        self.assertIn("connect_timeout_seconds", " ".join(probe.prerequisite_failures(self.settings, self.profile)))

    def test_anonymous_access_and_public_namespace_are_valid(self):
        self.profile.update(username="", password="", namespace="")
        self.assertEqual(probe.prerequisite_failures(self.settings, self.profile), [])
        result, _ = self.check()
        self.assertEqual(result, probe.EXIT_OK)
        self.client._authenticate.assert_not_called()

    def test_summary_shows_selected_connection_and_masks_password(self):
        output = "\n".join(probe.summarize_effective(self.settings, self.profile))
        for value in ("NACOS_CONFIG_PROFILES", "test-namespace", "test-group", "test-user", "first", "second"):
            self.assertIn(value, output)
        self.assertNotIn("private-password", output)
        self.assertEqual(json.loads(output.split("NACOS_CONFIG=", 1)[1])["password"], "***")

    def test_production_address_normalization_is_used(self):
        self.profile["server_addresses"] = "[http://nacos.invalid:8848/nacos](http://ignored)"
        self.assertEqual(probe.server_address(self.profile), "http://nacos.invalid:8848/nacos")

    def test_password_token_bearer_and_url_credentials_are_masked(self):
        text = 'password=private-password accessToken=private-token&x=1 {"accessToken":"other-token"} Bearer bearer-token https://u:url-password@nacos.invalid'
        output = probe.sanitize_error_text(text, self.profile, "private-token")
        for secret in ("private-password", "private-token", "other-token", "bearer-token", "url-password"):
            self.assertNotIn(secret, output)

    def test_success_authenticates_reads_all_documents_and_closes(self):
        result, output = self.check()
        self.assertEqual(result, probe.EXIT_OK)
        self.assertIn("PASS", output)
        self.assertNotIn("remote-config-secret", output)
        self.client._authenticate.assert_called_once_with()
        self.assertEqual([call.args[0] for call in self.client.get_config.call_args_list], ["first", "second"])
        self.client.close.assert_called_once_with()

    def test_authentication_failure_reports_message_without_tokens(self):
        self.client._authenticate.side_effect = RuntimeError('HTTP 403 password=private-password {"accessToken":"other-token"}')
        result, output = self.check()
        self.assertEqual(result, probe.EXIT_CONNECT)
        self.assertIn("HTTP 403", output)
        self.assertNotIn("private-password", output)
        self.assertNotIn("other-token", output)
        self.client.get_config.assert_not_called()
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
                probe.check_connection(self.settings, self.profile)
        self.client.close.assert_called_once_with()

    def test_real_client_authenticates_and_reads_a_local_http_server(self):
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
            self.profile["server_addresses"] = f"http://127.0.0.1:{server.server_port}"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = probe.check_connection(self.settings, self.profile)
            self.assertEqual(result, probe.EXIT_OK, output.getvalue())
            self.assertEqual([call[0] for call in calls], ["POST", "GET", "GET"])
            self.assertEqual(calls[0][1], "/nacos/v1/auth/login")
            self.assertEqual(calls[0][2], {"username": ["test-user"], "password": ["private-password"]})
            for call, data_id in zip(calls[1:], self.profile["data_id_list"]):
                self.assertEqual(call[1], "/nacos/v1/cs/configs")
                self.assertEqual(call[2], {"dataId": [data_id], "group": ["test-group"], "tenant": ["test-namespace"], "accessToken": ["local-test-token"]})
                self.assertEqual(call[3], "Bearer local-test-token")
            self.assertNotIn("should-not-be-printed", output.getvalue())
            self.assertNotIn("local-test-token", output.getvalue())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
