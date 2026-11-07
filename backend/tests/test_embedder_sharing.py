"""One embedding model per process: lifespan wiring and Nacos reload reuse."""

import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.bootstrap import lifespan
from app.bootstrap.state import ApplicationState
from app.clients.inference import EmbeddingSpec
from app.services.retrieval.active_model import ActiveModel

CONFIG = {
    "TOTAL_CONFIG": {"device_id": 0},
    "EMBEDDING_CONFIG": {"model_path": "config/model/dino/multitask_cls.onnx", "scale": [224, 224]},
    "GLOBAL_FILTER": {"switch": False, "threshold": 0.7},
    "FLAB_CONFIG": {"API_URL": "http://127.0.0.1:9/x", "API_KEY": "k", "DOC_COUNT": 1,
                    "MAX_SNIPPET_LENGTH": 10, "THRESHOLD": 0.5, "EnableWaiting": False, "Timeout": 1},
}
MODEL = ActiveModel(id=1, name="dino", version="v1", collection="c1", dim=1024, distance="cosine")


class FakeEmbedder:
    def __init__(self, config):
        self.spec = EmbeddingSpec.from_config(config)
        self.model_path = self.spec.model_path
        self.warmups = []

    def warmup(self, runs=3):
        self.warmups.append(runs)


def settings():
    return SimpleNamespace(
        warmup=SimpleNamespace(enabled=True, embedding_runs=2),
        application=SimpleNamespace(env="test"),
        config=SimpleNamespace(yaml_path="config/param/config.yaml"),
    )


class EmbedderSharingTests(unittest.TestCase):
    def setUp(self):
        self.created = []

        def create(config):
            embedder = FakeEmbedder(config)
            self.created.append(embedder)
            return embedder

        def build_stack(config, app_settings, *, embedder=None, indexed_dir=None):
            return SimpleNamespace(embedding=embedder, model=MODEL, close=Mock())

        embedder_patch = patch.object(lifespan, "create_embedder", side_effect=create)
        stack_patch = patch("app.bootstrap.retrieval_stack.build_retrieval_stack", side_effect=build_stack)
        embedder_patch.start()
        self.build_stack = stack_patch.start()
        self.addCleanup(embedder_patch.stop)
        self.addCleanup(stack_patch.stop)
        self.runtime = ApplicationState(
            config=copy.deepcopy(CONFIG), config_info={}, http_client=Mock(), settings=settings()
        )
        self.provider = Mock()
        self.provider.source_info.return_value = {}

    def start(self):
        embedder = lifespan.initialize_embedder(self.runtime)
        lifespan.initialize_food_service(self.runtime, embedder)
        lifespan.initialize_retrieval(self.runtime, embedder)
        return embedder

    def reload(self, config):
        snapshot = SimpleNamespace(config=config, profile={"profile": "test", "data_id_list": []})
        return lifespan.apply_nacos_config_update(self.runtime, self.provider, snapshot)

    def test_startup_loads_one_model_for_predict_and_retrieval(self):
        embedder = self.start()
        self.assertEqual(len(self.created), 1)
        self.assertIs(self.runtime.food_service.embeddingTool, embedder)
        self.assertIs(self.runtime.food_service.searchFilter.embedding, embedder)
        self.assertIs(self.runtime.retrieval.embedding, embedder)
        self.assertEqual(embedder.warmups, [2])

    def test_reload_without_model_change_reuses_the_loaded_model(self):
        embedder = self.start()
        config = copy.deepcopy(CONFIG)
        config["GLOBAL_FILTER"]["threshold"] = 0.9
        self.assertTrue(self.reload(config))
        self.assertEqual(len(self.created), 1)  # no ONNX reload
        self.assertIs(self.runtime.food_service.embeddingTool, embedder)
        self.assertEqual(self.runtime.food_service.searchFilter.threshold, 0.9)
        self.assertEqual(embedder.warmups, [2])  # not re-warmed

    def test_model_change_switches_predict_only_and_switching_back_reuses(self):
        original = self.start()
        changed = copy.deepcopy(CONFIG)
        changed["EMBEDDING_CONFIG"]["model_path"] = "config/model/other.onnx"
        self.assertTrue(self.reload(changed))
        self.assertEqual(len(self.created), 2)
        self.assertIsNot(self.runtime.food_service.embeddingTool, original)
        self.assertIs(self.runtime.retrieval.embedding, original)  # stays paired with its collection

        self.assertTrue(self.reload(copy.deepcopy(CONFIG)))
        self.assertEqual(len(self.created), 2)  # back to the model retrieval still holds
        self.assertIs(self.runtime.food_service.embeddingTool, original)

    def test_model_load_failure_disables_both_with_reasons(self):
        lifespan.create_embedder.side_effect = FileNotFoundError("no onnx")
        self.start()
        self.assertIsNone(self.runtime.food_service)
        self.assertIsNone(self.runtime.retrieval)
        self.assertIn("no onnx", self.runtime.initialization_error)
        self.assertEqual(self.runtime.retrieval_error_code, "NOT_READY")
        self.assertIn("no onnx", self.runtime.retrieval_error)

    def test_retrieval_can_be_switched_off_in_config_without_touching_predict(self):
        self.runtime.config["TOTAL_CONFIG"]["retrieval_enabled"] = False
        embedder = self.start()
        self.assertIsNone(self.runtime.retrieval)
        self.assertEqual(self.runtime.retrieval_error_code, "RETRIEVAL_DISABLED")
        self.build_stack.assert_not_called()
        self.assertIs(self.runtime.food_service.embeddingTool, embedder)

    def test_bad_retrieval_switch_degrades_retrieval_only(self):
        self.runtime.config["TOTAL_CONFIG"]["retrieval_enabled"] = "flase"
        embedder = self.start()  # must not raise out of startup
        self.assertIsNone(self.runtime.retrieval)
        self.assertEqual(self.runtime.retrieval_error_code, "NOT_READY")
        self.assertIn("TOTAL_CONFIG.retrieval_enabled", self.runtime.retrieval_error)
        self.assertIs(self.runtime.food_service.embeddingTool, embedder)


class EmbeddingSpecTests(unittest.TestCase):
    def test_defaults_and_equality(self):
        a = EmbeddingSpec.from_config({"EMBEDDING_CONFIG": {"model_path": "config/model/dino/x.onnx"}})
        b = EmbeddingSpec.from_config(
            {"EMBEDDING_CONFIG": {"model_path": "config/model/dino/./x.onnx", "scale": [224, 224], "img_interp": "CUBIC"}}
        )
        self.assertEqual(a, b)
        self.assertNotEqual(a, EmbeddingSpec.from_config({"EMBEDDING_CONFIG": {"model_path": "config/model/dino/x.onnx", "use_gpu": True}}))
        self.assertIsNone(EmbeddingSpec.try_from_config({"EMBEDDING_CONFIG": {"scale": "big"}}))


if __name__ == "__main__":
    unittest.main()
