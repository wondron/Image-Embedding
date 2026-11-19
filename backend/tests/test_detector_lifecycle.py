"""Optional GroundingDINO startup, shared access and atomic configuration reload."""

import asyncio
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI

from app.bootstrap import lifespan
from app.bootstrap.state import ApplicationState
from app.clients.inference import EmbeddingSpec
from app.core import errors as err


CONFIG = {
    "TOTAL_CONFIG": {"device_id": 0},
    "EMBEDDING_CONFIG": {"model_path": "embedding.onnx", "scale": [224, 224]},
    "GROUNDINGDINO_CONFIG": {
        "model_path": "groundingdino.onnx",
        "tokenizer_path": "tokenizer",
        "caption": "food .",
    },
}


def app_settings():
    return SimpleNamespace(
        warmup=SimpleNamespace(enabled=True, embedding_runs=2, groundingdino_runs=4),
        application=SimpleNamespace(env="test"),
        config=SimpleNamespace(yaml_path="config/param/config.yaml"),
        nacos=Mock(),
    )


class FakeEmbedder:
    def __init__(self, config):
        self.spec = EmbeddingSpec.from_config(config)
        self.model_path = self.spec.model_path

    def warmup(self, runs=3):
        pass


class FakeDetector:
    def __init__(self, config):
        self.config = copy.deepcopy(config)
        self.model_path = config["GROUNDINGDINO_CONFIG"]["model_path"]
        self.warmups = []
        self.warmup_error = None

    def warmup(self, runs=3):
        self.warmups.append(runs)
        if self.warmup_error is not None:
            raise self.warmup_error


class DetectorLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.created = []

        def create_detector(config):
            detector = FakeDetector(config)
            self.created.append(detector)
            return detector

        for name, replacement in (
            ("create_detector", Mock(side_effect=create_detector)),
            ("create_embedder", Mock(side_effect=FakeEmbedder)),
            ("create_food_service", Mock(side_effect=lambda config, embedding: SimpleNamespace(embeddingTool=embedding))),
        ):
            patched = patch.object(lifespan, name, replacement)
            patched.start()
            self.addCleanup(patched.stop)
        self.runtime = self.make_runtime()
        self.provider = Mock()
        self.provider.source_info.return_value = {}

    def make_runtime(self):
        config = copy.deepcopy(CONFIG)
        embedding = FakeEmbedder(config)
        return ApplicationState(
            config=config,
            config_info={"source": "test"},
            http_client=Mock(),
            settings=app_settings(),
            embedder=embedding,
            food_service=SimpleNamespace(embeddingTool=embedding),
        )

    def reload(self, config):
        return lifespan.apply_nacos_config_update(
            self.runtime, self.provider, SimpleNamespace(config=config, data_ids=())
        )

    def test_absent_empty_null_and_disabled_configuration_skip_loading(self):
        for section in ("absent", {}, None, {"enabled": False}, {"enabled": "false"}):
            with self.subTest(section=section):
                runtime = self.make_runtime()
                if section == "absent":
                    runtime.config.pop("GROUNDINGDINO_CONFIG")
                else:
                    runtime.config["GROUNDINGDINO_CONFIG"] = section
                self.assertIsNone(lifespan.initialize_detector(runtime))
                self.assertIsNone(runtime.detector)
                self.assertIsNone(runtime.detector_error)
                self.assertTrue(runtime.ready)
        lifespan.create_detector.assert_not_called()

    def test_startup_loads_and_warms_the_shared_detector(self):
        detector = lifespan.initialize_detector(self.runtime)
        self.assertIs(self.runtime.detector, detector)
        self.assertIs(self.runtime.require_detector(), detector)
        self.assertIs(self.runtime.require_detector(), detector)
        self.assertEqual(detector.warmups, [4])
        self.assertIsNone(self.runtime.detector_error)
        lifespan.create_detector.assert_called_once()
        loaded_config = lifespan.create_detector.call_args.args[0]
        self.assertEqual(loaded_config["GROUNDINGDINO_CONFIG"], CONFIG["GROUNDINGDINO_CONFIG"])
        self.assertEqual(loaded_config["TOTAL_CONFIG"]["device_id"], 0)

    def test_disabled_warmup_keeps_the_model_available(self):
        self.runtime.settings.warmup.enabled = False
        detector = lifespan.initialize_detector(self.runtime)
        self.assertIs(self.runtime.require_detector(), detector)
        self.assertEqual(detector.warmups, [])

    def test_startup_load_and_warmup_failures_preserve_embedding_readiness(self):
        for stage in ("load", "warmup"):
            with self.subTest(stage=stage):
                runtime = self.make_runtime()
                detector = FakeDetector(runtime.config)
                failure = RuntimeError(f"detector {stage} failed")
                if stage == "warmup":
                    detector.warmup_error = failure
                with patch.object(lifespan, "create_detector", side_effect=failure if stage == "load" else None,
                                  return_value=detector):
                    with self.assertLogs("lifespan", level="ERROR"):
                        self.assertIsNone(lifespan.initialize_detector(runtime))
                self.assertIsNone(runtime.detector)
                self.assertIn(str(failure), runtime.detector_error)
                self.assertIsNone(runtime.initialization_error)
                self.assertIsNone(runtime.warmup_error)
                self.assertTrue(runtime.ready)
                with self.assertRaisesRegex(err.AppError, str(failure)) as caught:
                    runtime.require_detector()
                self.assertEqual((caught.exception.code, caught.exception.http_status), ("NOT_READY", 503))

    def test_successful_retry_clears_the_previous_detector_error(self):
        self.runtime.detector_error = "previous load failed"
        detector = lifespan.initialize_detector(self.runtime)
        self.assertIs(self.runtime.require_detector(), detector)
        self.assertIsNone(self.runtime.detector_error)

    def test_reload_of_unrelated_configuration_reuses_without_rewarming(self):
        detector = lifespan.initialize_detector(self.runtime)
        updated = copy.deepcopy(CONFIG)
        updated["UNRELATED_CONFIG"] = {"threshold": 0.9}
        self.assertTrue(self.reload(updated))
        self.assertIs(self.runtime.require_detector(), detector)
        self.assertEqual(detector.warmups, [4])
        self.assertEqual(len(self.created), 1)

    def test_detector_or_device_change_replaces_only_after_success(self):
        for changed in ("model_path", "caption", "device_id"):
            with self.subTest(changed=changed):
                self.runtime = self.make_runtime()
                original = lifespan.initialize_detector(self.runtime)
                updated = copy.deepcopy(CONFIG)
                if changed == "device_id":
                    updated["TOTAL_CONFIG"][changed] = 1
                else:
                    updated["GROUNDINGDINO_CONFIG"][changed] = "changed"
                self.assertTrue(self.reload(updated))
                replacement = self.runtime.require_detector()
                self.assertIsNot(replacement, original)
                self.assertEqual(replacement.config["GROUNDINGDINO_CONFIG"], updated["GROUNDINGDINO_CONFIG"])
                self.assertEqual(replacement.config["TOTAL_CONFIG"]["device_id"], updated["TOTAL_CONFIG"]["device_id"])
                self.assertEqual(replacement.warmups, [4])
                self.assertEqual(original.warmups, [4])

    def test_failed_reload_preserves_the_previous_detector_service_and_config(self):
        original = lifespan.initialize_detector(self.runtime)
        original_config = self.runtime.config
        original_service = self.runtime.food_service
        updated = copy.deepcopy(CONFIG)
        updated["GROUNDINGDINO_CONFIG"]["model_path"] = "new.onnx"
        for stage in ("load", "warmup", "service"):
            with self.subTest(stage=stage):
                replacement = FakeDetector(updated)
                failure = RuntimeError(f"replacement {stage} failed")
                if stage == "warmup":
                    replacement.warmup_error = failure
                with patch.object(lifespan, "create_detector", return_value=replacement,
                                  side_effect=failure if stage == "load" else None):
                    service_failure = patch.object(lifespan, "create_food_service", side_effect=failure)
                    if stage == "service":
                        service_failure.start()
                    try:
                        with self.assertLogs("lifespan", level="ERROR"):
                            self.assertFalse(self.reload(updated))
                    finally:
                        if stage == "service":
                            service_failure.stop()
                self.assertIs(self.runtime.detector, original)
                self.assertIs(self.runtime.config, original_config)
                self.assertIs(self.runtime.food_service, original_service)
                self.assertIn(str(failure), self.runtime.config_info["last_reload_error"])
                self.assertTrue(self.runtime.ready)

    def test_removal_and_explicit_disable_clear_the_detector_on_reload(self):
        for section in ("absent", None, {}, {"enabled": False}):
            with self.subTest(section=section):
                self.runtime = self.make_runtime()
                lifespan.initialize_detector(self.runtime)
                updated = copy.deepcopy(CONFIG)
                if section == "absent":
                    updated.pop("GROUNDINGDINO_CONFIG")
                else:
                    updated["GROUNDINGDINO_CONFIG"] = section
                count = len(self.created)
                self.assertTrue(self.reload(updated))
                self.assertIsNone(self.runtime.detector)
                self.assertIsNone(self.runtime.detector_error)
                self.assertEqual(len(self.created), count)
                self.assertTrue(self.runtime.ready)

    def test_lifespan_entrypoint_initializes_one_detector_before_serving(self):
        application = FastAPI()
        http_client = Mock()
        http_client.aclose = AsyncMock()
        provider = Mock()
        settings = app_settings()
        with patch.object(lifespan, "settings", settings), \
                patch.object(lifespan, "setup_logger"), \
                patch.object(lifespan, "NacosConfigProvider", return_value=provider), \
                patch.object(lifespan, "load_application_config", return_value=(copy.deepcopy(CONFIG), {})), \
                patch.object(lifespan, "create_http_client", return_value=http_client), \
                patch.object(lifespan, "apply_download_policy"), \
                patch.object(lifespan, "load_enum_file"), \
                patch.object(lifespan, "initialize_catalog"), \
                patch.object(lifespan, "initialize_retrieval"), \
                patch.object(lifespan, "initialize_models"), \
                patch.object(lifespan, "start_reconciler", return_value=None):

            async def serve():
                async with lifespan.lifespan(application):
                    runtime = application.state.runtime
                    self.assertIsInstance(runtime, ApplicationState)
                    self.assertIs(runtime.require_detector(), self.created[0])
                    self.assertEqual(self.created[0].warmups, [4])
                    self.assertTrue(runtime.ready)

            asyncio.run(serve())
        lifespan.create_detector.assert_called_once()
        provider.start_polling.assert_called_once()
        provider.close.assert_called_once()
        http_client.aclose.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
