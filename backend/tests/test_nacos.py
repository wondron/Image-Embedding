import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from app.core.config.nacos import NacosConfigClient, NacosConfigProvider
from app.core.config.settings import NacosSettings


PROFILES = tuple(
    {
        "profile": name,
        "server_addresses": "http://nacos.invalid:8848",
        "namespace": name,
        "username": "",
        "password": "",
        "group": "test-group",
        "data_id_list": ["main_food"],
    }
    for name in ("prod", "test")
)


class NacosEnvironmentTests(unittest.TestCase):
    def test_unset_and_empty_environment_default_to_prod(self):
        with patch.dict(os.environ):
            os.environ.pop("NACOS_ENV", None)
            self.assertEqual(NacosSettings().profile, "prod")
            os.environ["NACOS_ENV"] = ""
            self.assertEqual(NacosSettings().profile, "prod")

    def test_explicit_environment_is_normalized(self):
        with patch.dict(os.environ, {"NACOS_ENV": " TEST "}):
            self.assertEqual(NacosSettings().profile, "test")

    def test_invalid_environment_is_rejected(self):
        with patch.dict(os.environ, {"NACOS_ENV": "staging"}):
            with self.assertRaisesRegex(ValueError, "NACOS_ENV"):
                NacosSettings()


class NacosLoadingTests(unittest.TestCase):
    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.yaml_path = Path(temporary_directory.name) / "config.yaml"
        self.yaml_path.write_text("marker: local\n", encoding="utf-8")
        platform_log = patch("app.core.config.nacos.show_text")
        platform_log.start()
        self.addCleanup(platform_log.stop)

    def make_provider(self, profile, text="marker: remote\n", status=200, error=None, **kwargs):
        response = requests.Response()
        response.status_code = status
        response._content = text.encode("utf-8")
        response.encoding = "utf-8"
        session = Mock(spec=requests.Session)
        session.request.return_value = response
        session.request.side_effect = error
        factory = Mock(
            side_effect=lambda selected, settings: NacosConfigClient(selected, settings, session)
        )
        provider = NacosConfigProvider(
            NacosSettings(profile=profile, profiles=PROFILES, **kwargs), factory
        )
        self.addCleanup(provider.close)
        return provider, factory, session

    def assert_only_selected_profile_requested(self, factory, session, selected):
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(factory.call_args.args[0]["profile"], selected)
        for request_call in session.request.call_args_list:
            self.assertEqual(request_call.kwargs["params"]["tenant"], selected)

    def test_success_uses_only_selected_environment(self):
        for selected in ("prod", "test"):
            with self.subTest(profile=selected):
                provider, factory, session = self.make_provider(selected)
                # Remote success must not depend on the local YAML file existing.
                config, source = provider.load(str(self.yaml_path.with_name("missing.yaml")))
                self.assertEqual(config, {"marker": "remote"})
                self.assertEqual(source["source"], "nacos")
                self.assertEqual(source["nacos_profile"], selected)
                self.assert_only_selected_profile_requested(factory, session, selected)

    def test_remote_failures_fall_back_locally_without_trying_other_environment(self):
        failures = (
            {"error": requests.Timeout("simulated timeout")},
            {"status": 500},
            {"status": 404},
            {"text": ""},
            {"text": "invalid: ["},
            {"text": "- not-a-mapping"},
        )
        for selected in ("prod", "test"):
            for index, failure in enumerate(failures):
                with self.subTest(profile=selected, failure=index):
                    provider, factory, session = self.make_provider(selected, **failure)
                    config, source = provider.load(str(self.yaml_path))
                    self.assertEqual(config, {"marker": "local"})
                    self.assertEqual(source["source"], "yaml")
                    self.assertEqual(source["nacos_status"], "fallback")
                    self.assertEqual(source["nacos_profile"], selected)
                    # polling keeps running after a fallback so Nacos is picked up once it recovers
                    self.assertTrue(source["nacos_polling"])
                    self.assert_only_selected_profile_requested(factory, session, selected)

    def test_disabled_nacos_reads_local_yaml_without_remote_requests(self):
        provider, factory, session = self.make_provider("test", enabled=False)
        config, source = provider.load(str(self.yaml_path))
        self.assertEqual(config, {"marker": "local"})
        self.assertEqual(source["nacos_status"], "disabled")
        factory.assert_not_called()
        session.request.assert_not_called()

    def test_missing_selected_profile_falls_back_locally(self):
        factory = Mock()
        provider = NacosConfigProvider(NacosSettings(profile="test", profiles=PROFILES[:1]), factory)
        self.addCleanup(provider.close)
        config, source = provider.load(str(self.yaml_path))
        self.assertEqual(config, {"marker": "local"})
        self.assertEqual(source["nacos_status"], "fallback")
        factory.assert_not_called()

    def test_polling_stays_in_selected_environment(self):
        provider, factory, session = self.make_provider("test")
        provider.load(str(self.yaml_path))
        provider._stop_event = Mock()
        provider._stop_event.is_set.return_value = False
        provider._stop_event.wait.side_effect = [False, True]
        on_update = Mock()
        provider._poll_loop(on_update)
        self.assertEqual(session.request.call_count, 2)
        self.assert_only_selected_profile_requested(factory, session, "test")
        on_update.assert_not_called()

    def test_local_yaml_failure_is_reported(self):
        provider, _, _ = self.make_provider("prod", error=requests.Timeout())
        with self.assertRaises(FileNotFoundError):
            provider.load(str(self.yaml_path.with_name("missing.yaml")))


def _response(text="", status=200):
    response = requests.Response()
    response.status_code = status
    response._content = text.encode("utf-8")
    response.encoding = "utf-8"
    return response


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
        options = {"enabled": True, "poll_enabled": True, **settings}
        provider = NacosConfigProvider(
            NacosSettings(profile="test", profiles=PROFILES, **options),
            lambda selected, nacos_settings: NacosConfigClient(selected, nacos_settings, session),
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

    def test_polling_survives_a_transient_failure_and_applies_the_next_change(self):
        provider, _ = self.provider(
            _response("marker: remote\n"),          # startup
            requests.ConnectionError("blip"),        # poll 1 fails
            _response("marker: changed\n"),         # poll 2 (after the retry wait)
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


class NacosEnvironmentOverrideTests(unittest.TestCase):
    def test_credentials_and_data_ids_come_from_environment(self):
        env = {"NACOS_ENV": "test", "NACOS_USERNAME": "env-user", "NACOS_PASSWORD": "env-secret",
               "NACOS_DATA_IDS": "a, b", "NACOS_ENABLED": "0"}
        with patch.dict(os.environ, env):
            nacos = NacosSettings()
        selected = next(p for p in nacos.profiles if p["profile"] == "test")
        self.assertEqual((selected["username"], selected["password"]), ("env-user", "env-secret"))
        self.assertEqual(selected["data_id_list"], ["a", "b"])
        self.assertFalse(nacos.enabled)


if __name__ == "__main__":
    unittest.main()
