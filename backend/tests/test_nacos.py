import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from app.core.config.constant import NACOS_DATA_ID_LIST
from app.core.config.nacos import NacosConfigClient, NacosConfigProvider
from app.core.config.settings import NacosSettings, load_env_file


ENABLED = {
    "NACOS_ENABLED": "true",
    "NACOS_SERVER_ADDR": "http://nacos.invalid:8848",
    "NACOS_NAMESPACE": "ns-test",
    "NACOS_GROUP": "test-group",
}


def nacos_settings(**overrides) -> NacosSettings:
    """开启、没有凭证、一个 dataId、一个永远不会被连接的地址（session 都是 mock）。"""
    options = {
        "enabled": True,
        "server_addr": "http://nacos.invalid:8848",
        "namespace": "ns-test",
        "group": "test-group",
        "data_ids": ("main_food",),
        **overrides,
    }
    return NacosSettings(**options)


def _response(text="", status=200):
    response = requests.Response()
    response.status_code = status
    response._content = text.encode("utf-8")
    response.encoding = "utf-8"
    return response


class NacosEnvironmentTests(unittest.TestCase):
    """NacosSettings.from_env：除了 dataId，其余都来自 NACOS_* 变量。"""

    def test_nothing_set_means_nacos_off_and_nothing_required(self):
        nacos = NacosSettings.from_env({})
        self.assertFalse(nacos.enabled)
        self.assertTrue(nacos.poll_enabled)
        self.assertEqual(nacos.data_ids, tuple(NACOS_DATA_ID_LIST))

    def test_enabled_reads_the_connection_from_the_environment(self):
        env = {
            **ENABLED,
            "NACOS_NAMESPACE": " ns-test ",
            "NACOS_USERNAME": "env-user",
            "NACOS_PASSWORD": "env-secret",
            "NACOS_POLL_ENABLED": "off",
            "NACOS_POLL_INTERVAL_SECONDS": "30",
            "NACOS_REQUEST_TIMEOUT_SECONDS": "4.5",
        }
        with patch.dict(os.environ, env):
            nacos = NacosSettings.from_env()  # 不传参数：用 os.environ，和 Settings() 一样
        self.assertEqual(
            (nacos.enabled, nacos.server_addr, nacos.namespace, nacos.group, nacos.username, nacos.password),
            (True, "http://nacos.invalid:8848", "ns-test", "test-group", "env-user", "env-secret"),
        )
        self.assertEqual(
            (nacos.poll_enabled, nacos.poll_interval_seconds, nacos.request_timeout_seconds, nacos.connect_timeout_seconds),
            (False, 30.0, 4.5, 2.0),
        )
        self.assertEqual(nacos.data_ids, tuple(NACOS_DATA_ID_LIST))  # dataId 留在 constant.py 里
        self.assertNotIn("env-secret", repr(nacos))

    def test_enabled_without_connection_names_every_missing_variable(self):
        with self.assertRaises(ValueError) as ctx:
            NacosSettings.from_env({"NACOS_ENABLED": "1", "NACOS_SERVER_ADDR": "http://nacos:8848"})
        message = str(ctx.exception)
        self.assertIn("NACOS_NAMESPACE", message)
        self.assertIn("NACOS_GROUP", message)
        self.assertNotIn("NACOS_SERVER_ADDR", message)

    def test_bad_values_name_the_variable(self):
        cases = (
            ({"NACOS_ENABLED": "maybe"}, "NACOS_ENABLED"),
            ({"NACOS_POLL_INTERVAL_SECONDS": "10s"}, "NACOS_POLL_INTERVAL_SECONDS"),
            ({"NACOS_CONNECT_TIMEOUT_SECONDS": "0"}, "NACOS_CONNECT_TIMEOUT_SECONDS"),
            ({"NACOS_RETRY_INTERVAL_SECONDS": "inf"}, "NACOS_RETRY_INTERVAL_SECONDS"),
        )
        for env, name in cases:
            with self.subTest(env=env), self.assertRaisesRegex(ValueError, name):
                NacosSettings.from_env(env)

    def test_env_file_fills_in_but_never_overrides_the_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("NACOS_GROUP=from-file\nNACOS_NAMESPACE=from-file\n", encoding="utf-8")
            with patch.dict(os.environ, {"NACOS_NAMESPACE": "from-shell"}):
                os.environ.pop("NACOS_GROUP", None)
                load_env_file(path)
                self.assertEqual(
                    (os.environ["NACOS_GROUP"], os.environ["NACOS_NAMESPACE"]), ("from-file", "from-shell")
                )


class NacosLoadingTests(unittest.TestCase):
    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.yaml_path = Path(temporary_directory.name) / "config.yaml"
        self.yaml_path.write_text("marker: local\n", encoding="utf-8")
        platform_log = patch("app.core.config.nacos.show_text")
        platform_log.start()
        self.addCleanup(platform_log.stop)

    def make_provider(self, text="marker: remote\n", status=200, error=None, **settings):
        session = Mock(spec=requests.Session)
        session.request.return_value = _response(text, status)
        session.request.side_effect = error
        factory = Mock(side_effect=lambda nacos: NacosConfigClient(nacos, session))
        provider = NacosConfigProvider(nacos_settings(**settings), factory)
        self.addCleanup(provider.close)
        return provider, factory, session

    def assert_requested_configured_location(self, session):
        self.assertTrue(session.request.call_args_list)
        for request_call in session.request.call_args_list:
            self.assertEqual(request_call.args[1], "http://nacos.invalid:8848/nacos/v1/cs/configs")
            params = request_call.kwargs["params"]
            self.assertEqual(
                (params["dataId"], params["group"], params["tenant"]), ("main_food", "test-group", "ns-test")
            )

    def test_success_reads_the_configured_namespace_and_group(self):
        provider, factory, session = self.make_provider()
        # 远程成功不能依赖本地 YAML 文件是否存在。
        config, source = provider.load(str(self.yaml_path.with_name("missing.yaml")))
        self.assertEqual(config, {"marker": "remote"})
        self.assertEqual(
            (source["source"], source["nacos_namespace"], source["nacos_group"], source["nacos_data_ids"]),
            ("nacos", "ns-test", "test-group", ["main_food"]),
        )
        factory.assert_called_once()
        self.assert_requested_configured_location(session)

    def test_remote_failures_fall_back_locally(self):
        failures = (
            {"error": requests.Timeout("simulated timeout")},
            {"status": 500},
            {"status": 404},
            {"text": ""},
            {"text": "invalid: ["},
            {"text": "- not-a-mapping"},
        )
        for index, failure in enumerate(failures):
            with self.subTest(failure=index):
                provider, _, session = self.make_provider(**failure)
                config, source = provider.load(str(self.yaml_path))
                self.assertEqual(config, {"marker": "local"})
                self.assertEqual((source["source"], source["nacos_status"]), ("yaml", "fallback"))
                self.assertEqual(source["nacos_namespace"], "ns-test")
                # 退回 YAML 之后轮询继续运行，这样 Nacos 恢复后会被重新用上
                self.assertTrue(source["nacos_polling"])
                self.assert_requested_configured_location(session)

    def test_disabled_nacos_reads_local_yaml_without_remote_requests(self):
        provider, factory, session = self.make_provider(enabled=False)
        config, source = provider.load(str(self.yaml_path))
        self.assertEqual(config, {"marker": "local"})
        self.assertEqual(source["nacos_status"], "disabled")
        self.assertFalse(source["nacos_polling"])
        provider.start_polling(Mock())
        self.assertIsNone(provider._thread)
        factory.assert_not_called()
        session.request.assert_not_called()

    def test_polling_reuses_the_client_and_reads_the_configured_location(self):
        provider, factory, session = self.make_provider()
        provider.load(str(self.yaml_path))
        provider._stop_event = Mock()
        provider._stop_event.wait.side_effect = [False, True]
        on_update = Mock()
        provider._poll_loop(on_update)
        self.assertEqual(session.request.call_count, 2)
        factory.assert_called_once()
        self.assert_requested_configured_location(session)
        on_update.assert_not_called()  # 没变的配置不会再次提交

    def test_credentials_log_in_and_the_token_goes_with_the_request(self):
        session = Mock(spec=requests.Session)
        session.post.return_value = _response('{"accessToken": "tok"}')
        session.request.return_value = _response("marker: remote\n")
        client = NacosConfigClient(nacos_settings(username="u", password="p"), session)
        self.assertEqual(client.fetch_documents(), {"main_food": "marker: remote\n"})
        self.assertEqual(session.post.call_args.args[0], "http://nacos.invalid:8848/nacos/v1/auth/login")
        self.assertEqual(session.post.call_args.kwargs["data"], {"username": "u", "password": "p"})
        self.assertEqual(session.request.call_args.kwargs["params"]["accessToken"], "tok")

    def test_local_yaml_failure_is_reported(self):
        provider, _, _ = self.make_provider(error=requests.Timeout())
        with self.assertRaises(FileNotFoundError):
            provider.load(str(self.yaml_path.with_name("missing.yaml")))


class NacosLayeringAndPollingTests(unittest.TestCase):
    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.yaml_path = Path(temporary_directory.name) / "config.yaml"
        self.yaml_path.write_text(
            "marker: local\nonly_local: {a: 1, b: 2}\nlocal_list: [1, 2]\n", encoding="utf-8"
        )
        platform_log = patch("app.core.config.nacos.show_text")
        platform_log.start()
        self.addCleanup(platform_log.stop)

    def provider(self, *results, **settings):
        session = Mock(spec=requests.Session)
        session.request.side_effect = list(results)
        provider = NacosConfigProvider(
            nacos_settings(**settings),
            lambda nacos: NacosConfigClient(nacos, session),
        )
        self.addCleanup(provider.close)
        return provider, session

    def run_polls(self, provider, on_update, waits):
        provider._stop_event = Mock()
        provider._stop_event.wait.side_effect = waits
        provider._poll_loop(on_update)
        return [c.args[0] for c in provider._stop_event.wait.call_args_list]

    def test_nacos_values_override_yaml_key_by_key(self):
        provider, _ = self.provider(_response("marker: remote\nonly_local: {b: 20}\nlocal_list: [9]\n"))
        config, source = provider.load(str(self.yaml_path))
        self.assertEqual(config, {"marker": "remote", "only_local": {"a": 1, "b": 20}, "local_list": [9]})
        self.assertTrue(source["yaml_base"])

    def test_later_data_ids_override_earlier_ones(self):
        provider, session = self.provider(
            _response("marker: first\nonly_first: 1\n"), _response("marker: second\n"),
            data_ids=("first", "second"),
        )
        config, source = provider.load(str(self.yaml_path))
        self.assertEqual((config["marker"], config["only_first"], config["only_local"]), ("second", 1, {"a": 1, "b": 2}))
        self.assertEqual([c.kwargs["params"]["dataId"] for c in session.request.call_args_list], ["first", "second"])
        self.assertEqual(source["nacos_data_ids"], ["first", "second"])

    def test_polling_survives_a_transient_failure_and_applies_the_next_change(self):
        provider, _ = self.provider(
            _response("marker: remote\n"),          # 启动时
            requests.ConnectionError("blip"),        # 第 1 次轮询失败
            _response("marker: changed\n"),         # 第 2 次轮询（重试等待之后）
        )
        provider.load(str(self.yaml_path))
        on_update = Mock(return_value=True)
        waits = self.run_polls(provider, on_update, [False, False, True])
        self.assertEqual(waits, [provider.poll_interval_seconds, provider.retry_interval_seconds,
                                 provider.poll_interval_seconds])
        on_update.assert_called_once()
        self.assertEqual(on_update.call_args.args[0].config["marker"], "changed")
        self.assertEqual(on_update.call_args.args[0].config["only_local"], {"a": 1, "b": 2})

    def test_after_yaml_fallback_polling_switches_to_nacos_when_it_recovers(self):
        provider, _ = self.provider(requests.Timeout("down"), _response("marker: remote\n"))
        config, source = provider.load(str(self.yaml_path))
        self.assertEqual((config["marker"], source["source"]), ("local", "yaml"))
        on_update = Mock(return_value=True)
        self.run_polls(provider, on_update, [False, True])
        on_update.assert_called_once()
        self.assertEqual(on_update.call_args.args[0].config["marker"], "remote")
        self.assertIs(provider._active, on_update.call_args.args[0])

    def test_rejected_config_is_not_offered_again_until_it_changes(self):
        provider, _ = self.provider(
            _response("marker: remote\n"),
            _response("marker: broken\n"),
            _response("marker: broken\n"),
            _response("marker: fixed\n"),
        )
        provider.load(str(self.yaml_path))
        on_update = Mock(side_effect=lambda snapshot: snapshot.config["marker"] != "broken")
        self.run_polls(provider, on_update, [False, False, False, True])
        self.assertEqual([c.args[0].config["marker"] for c in on_update.call_args_list], ["broken", "fixed"])
        self.assertEqual(provider._active.config["marker"], "fixed")

    def test_disabled_polling_does_not_start_a_thread(self):
        provider, _ = self.provider(_response("marker: remote\n"), poll_enabled=False)
        provider.load(str(self.yaml_path))
        provider.start_polling(Mock())
        self.assertIsNone(provider._thread)


if __name__ == "__main__":
    unittest.main()
