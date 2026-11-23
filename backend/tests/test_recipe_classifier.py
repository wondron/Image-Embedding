"""菜谱识别模型（app.clients.recipe_classifier）：预处理、底库比对和底库文件的读取，以及启动时
加载、Nacos 重新加载时复用或替换（app.bootstrap.lifespan）。

模型和底库在 config/model/recipe（不在 git 里）；有这些文件时，用模型自带的
robam_recipe_classifier.py 核对：预处理逐位一致，识别结果相同。其余测试不需要模型文件或 GPU。
"""

import asyncio
import copy
import importlib.util
import os
import pickle
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import cv2
import numpy as np
from fastapi import FastAPI

from app.bootstrap import lifespan
from app.bootstrap.state import ApplicationState
from app.clients import recipe_classifier
from app.clients.inference import EmbeddingSpec
from app.clients.recipe_classifier import RecipeClassifier, RecipeMatch, letterbox, load_gallery
from app.core import errors as err

BACKEND = Path(__file__).resolve().parents[1]
MODEL_DIR = BACKEND / "config" / "model" / "recipe"
MODEL, LABELS = MODEL_DIR / "robam_model.onnx", MODEL_DIR / "robam_base_featrures_labels.pkl"
REFERENCE = MODEL_DIR / "robam_recipe_classifier.py"
IMAGES = BACKEND / "config" / "images"

# 一个 4 维的小底库：清蒸鲈鱼有两条特征
GALLERY = {
    "robam_base_featrures": np.asarray(
        [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0.6, 0.8, 0], [0, 0, 0, 2]], dtype=np.float32
    ),
    "robam_base_labels": np.asarray(["红烧肉", "清蒸鲈鱼", " 清蒸鲈鱼 ", "凉拌猪耳"]),
}


def reference_module():
    """模型自带的推理脚本（它不是项目的包，按文件导入）。"""
    spec = importlib.util.spec_from_file_location("robam_recipe_classifier_reference", REFERENCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # 它的 dataclass 要在 sys.modules 里找到自己的模块
    spec.loader.exec_module(module)
    return module


class FakeSession:
    """假的 ONNX session：每次 run 依次返回 outputs 里的一个特征。"""

    def __init__(self, outputs=(), *, input_shape=("batchsize", 3, 224, 224), output_shape=("batchsize", 4)):
        self.outputs = [np.asarray(output, dtype=np.float32) for output in outputs]
        self.input_shape, self.output_shape = list(input_shape), list(output_shape)
        self.runs = []

    def get_inputs(self):
        return [SimpleNamespace(name="input", shape=self.input_shape)]

    def get_outputs(self):
        return [SimpleNamespace(name="output", shape=self.output_shape)]

    def get_providers(self):
        return ["CPUExecutionProvider"]

    def run(self, names, feeds):
        (batch,) = feeds.values()
        self.runs.append((names, batch.shape, batch.dtype))
        output = self.outputs.pop(0) if self.outputs else np.zeros(4, dtype=np.float32)
        return [output.reshape(1, -1)]


class ModelFiles:
    """临时目录里的模型文件（内容无所谓，session 是假的）和底库文件。"""

    def __init__(self, test, gallery=GALLERY):
        folder = tempfile.TemporaryDirectory()
        test.addCleanup(folder.cleanup)
        self.model = os.path.join(folder.name, "recipe.onnx")
        self.labels = os.path.join(folder.name, "labels.pkl")
        with open(self.model, "wb") as file:
            file.write(b"onnx")
        with open(self.labels, "wb") as file:
            pickle.dump(gallery, file)

    def config(self, **section):
        return {"RECIPE_DETECT_CONFIG": {"model_path": self.model, "labels_path": self.labels, **section}}


def classifier_with(test, session, **section):
    files = ModelFiles(test)
    with patch.object(recipe_classifier, "create_session", return_value=session) as create:
        classifier = RecipeClassifier(files.config(**section))
    return classifier, create


class LetterboxTests(unittest.TestCase):
    def test_the_long_side_fills_the_square_and_the_rest_is_black(self):
        wide = np.full((50, 100, 3), 200, dtype=np.uint8)
        padded = letterbox(wide, 224)
        self.assertEqual((padded.shape, padded.dtype), ((224, 224, 3), np.uint8))
        self.assertTrue((padded[56:168] == 200).all())  # 缩放后 224 x 112，上下各补 56 行
        self.assertTrue((padded[:56] == 0).all() and (padded[168:] == 0).all())
        tall = letterbox(np.full((300, 30, 3), 90, dtype=np.uint8), 224)  # 22 x 224，左右各补 101 列
        self.assertTrue((tall[:, 101:123] == 90).all())
        self.assertTrue((tall[:, :101] == 0).all() and (tall[:, 123:] == 0).all())
        # 很扁的图：短边至少 1 像素
        self.assertEqual(int((letterbox(np.full((1, 1000, 3), 255, dtype=np.uint8), 224) > 0).any(axis=2).sum()), 224)

    @unittest.skipUnless(REFERENCE.is_file(), "config/model/recipe/robam_recipe_classifier.py is not here")
    def test_the_input_is_bit_for_bit_what_the_model_script_feeds_it(self):
        reference = reference_module()
        classifier, _ = classifier_with(self, FakeSession())
        rng = np.random.default_rng(7)
        for height, width in ((720, 1280), (1280, 720), (333, 333), (17, 401), (401, 17), (16, 16)):
            with self.subTest(size=(height, width)):
                image = rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8)
                ours = classifier.preprocess(image)
                theirs = np.stack([reference.preprocess_image(image, 224)], axis=0).astype(np.float32, copy=False)
                self.assertEqual((ours.shape, ours.dtype), ((1, 3, 224, 224), np.float32))
                self.assertTrue(ours.flags.c_contiguous)
                np.testing.assert_array_equal(ours, theirs)
        # 裁剪图是查询图的视图（不连续）：结果和复制出来的一样
        image = rng.integers(0, 256, size=(100, 200, 3), dtype=np.uint8)
        np.testing.assert_array_equal(
            classifier.preprocess(image[10:90, 30:150]), classifier.preprocess(image[10:90, 30:150].copy())
        )


class GalleryTests(unittest.TestCase):
    def write(self, data, *, raw=False):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = os.path.join(folder.name, "labels.pkl")
        with open(path, "wb") as file:
            file.write(data if raw else pickle.dumps(data))
        return path

    def test_features_are_normalized_and_names_stripped(self):
        features, labels = load_gallery(self.write(GALLERY))
        self.assertEqual((features.shape, features.dtype), ((4, 4), np.float32))
        np.testing.assert_allclose(np.linalg.norm(features, axis=1), 1.0, rtol=1e-6)
        np.testing.assert_allclose(features[3], [0, 0, 0, 1])  # 原来长度是 2
        self.assertEqual(labels, ["红烧肉", "清蒸鲈鱼", "清蒸鲈鱼", "凉拌猪耳"])
        # 列表也可以（不需要导入任何类型）
        features, labels = load_gallery(self.write({
            "robam_base_featrures": [[3.0, 4.0]], "robam_base_labels": ["番茄炒蛋"],
        }))
        np.testing.assert_allclose(features, [[0.6, 0.8]])
        self.assertEqual(labels, ["番茄炒蛋"])

    def test_anything_but_arrays_is_refused_before_it_runs(self):
        ran = []

        class Payload:
            def __reduce__(self):
                return (ran.append, ("pickle ran code",))

        with self.assertRaisesRegex(pickle.UnpicklingError, "不允许的对象 builtins.*只能包含 numpy 数组"):
            load_gallery(self.write({**GALLERY, "extra": Payload()}))
        self.assertEqual(ran, [])

    def test_a_file_of_the_wrong_shape_says_what_is_wrong(self):
        features, labels = GALLERY["robam_base_featrures"], GALLERY["robam_base_labels"]
        for data, said in (
            ([features, labels], "要是一个包含 robam_base_featrures 和 robam_base_labels 的 dict"),
            ({"robam_base_featrures": features}, "robam_base_labels"),
            ({"robam_base_featrures": features[0], "robam_base_labels": labels[:1]}, "非空的二维数组"),
            ({"robam_base_featrures": np.zeros((0, 4)), "robam_base_labels": labels[:0]}, "非空的二维数组"),
            ({"robam_base_featrures": features, "robam_base_labels": labels[:3]}, "和特征一样多（4 条）"),
            ({"robam_base_featrures": np.zeros((1, 4)), "robam_base_labels": labels[:1]}, "全零"),
        ):
            with self.subTest(said=said), self.assertRaisesRegex(ValueError, said):
                load_gallery(self.write(data))
        with self.assertRaisesRegex(FileNotFoundError, "底库文件不存在"):
            load_gallery(os.path.join(tempfile.gettempdir(), "no-such-recipe-gallery.pkl"))


class RecipeClassifierTests(unittest.TestCase):
    def test_each_image_gets_its_closest_dish_one_inference_at_a_time(self):
        session = FakeSession([[0, 1, 1, 0], [5, 0, 0, 0], [0, 0, 0, -1]])
        classifier, create = classifier_with(self, session)
        images = [np.zeros((40, 60, 3), dtype=np.uint8), np.zeros((60, 40, 3), dtype=np.uint8), np.zeros((9, 9, 3), dtype=np.uint8)]
        matches = classifier.classify(images)
        self.assertEqual([match.label for match in matches], ["清蒸鲈鱼", "红烧肉", "红烧肉"])
        self.assertAlmostEqual(matches[0].score, 1.4 / 2 ** 0.5, places=6)  # 清蒸鲈鱼的第二条特征最近
        self.assertAlmostEqual(matches[1].score, 1.0, places=6)
        self.assertAlmostEqual(matches[2].score, 0.0, places=6)  # 都不像时也给最近的（相似度很低，过不了阈值）
        self.assertIsInstance(matches[0], RecipeMatch)
        self.assertEqual(session.runs, [(["output"], (1, 3, 224, 224), np.float32)] * 3)
        self.assertEqual(classifier.classify([]), [])
        # CPU，除非开了 use_gpu；设备号来自 TOTAL_CONFIG
        self.assertEqual(create.call_args.args[1:], ("cpu",))
        self.assertEqual(create.call_args.kwargs, {"device_id": 0})

    def test_use_gpu_and_the_device_come_from_the_configuration(self):
        files = ModelFiles(self)
        config = {**files.config(use_gpu="true"), "TOTAL_CONFIG": {"device_id": 1}}
        with patch.object(recipe_classifier, "create_session", return_value=FakeSession()) as create:
            RecipeClassifier(config)
        self.assertEqual((create.call_args.args[1], create.call_args.kwargs), ("cuda", {"device_id": 1}))
        with self.assertRaisesRegex(ValueError, "RECIPE_DETECT_CONFIG.use_gpu"):
            RecipeClassifier(files.config(use_gpu="maybe"))

    def test_bad_images_and_bad_model_outputs_fail_loudly(self):
        classifier, _ = classifier_with(self, FakeSession([[1, 2, 3]]))
        for image in (np.zeros((10, 10), dtype=np.uint8), np.zeros((10, 10, 4), dtype=np.uint8),
                      np.zeros((10, 10, 3), dtype=np.float32), np.zeros((0, 10, 3), dtype=np.uint8), b"jpeg"):
            with self.subTest(image=getattr(image, "shape", image)), self.assertRaises(ValueError):
                classifier.match(image)
        with self.assertRaisesRegex(RuntimeError, r"输出的形状不对：\(1, 3\)"):
            classifier.match(np.zeros((10, 10, 3), dtype=np.uint8))

    def test_warmup_runs_the_whole_match_at_the_model_input_size(self):
        session = FakeSession(input_shape=("batchsize", 3, 96, 96))
        classifier, _ = classifier_with(self, session)
        classifier.warmup(2)
        self.assertEqual([shape for _, shape, _ in session.runs], [(1, 3, 96, 96)] * 2)

    def test_a_model_that_does_not_fit_the_gallery_or_the_files_fails_the_construction(self):
        with self.assertRaisesRegex(ValueError, "输出 256 维特征，底库是 4 维"):
            classifier_with(self, FakeSession(output_shape=("batchsize", 256)))
        with self.assertRaisesRegex(ValueError, "输入要是正方形"):
            classifier_with(self, FakeSession(input_shape=("batchsize", 3, 224, 112)))
        # 动态的高宽：按 224
        classifier, _ = classifier_with(self, FakeSession(input_shape=("n", 3, "h", "w")))
        self.assertEqual(classifier.input_size, 224)

        files = ModelFiles(self)
        for section, error, said in (
            ({"model_path": ""}, ValueError, "RECIPE_DETECT_CONFIG.model_path 缺失"),
            ({"labels_path": None}, ValueError, "RECIPE_DETECT_CONFIG.labels_path 缺失"),
            ({"model_path": files.model + ".missing"}, FileNotFoundError, "菜谱识别模型文件不存在"),
            ({"labels_path": files.labels + ".missing"}, FileNotFoundError, "菜谱识别底库文件不存在"),
        ):
            with self.subTest(section=section), self.assertRaisesRegex(error, said):
                with patch.object(recipe_classifier, "create_session", return_value=FakeSession()):
                    RecipeClassifier(files.config(**section))
        with self.assertRaisesRegex(ValueError, "model_path 缺失"):
            RecipeClassifier({})


@unittest.skipUnless(
    MODEL.is_file() and LABELS.is_file() and REFERENCE.is_file(), "the recipe model files are not in config/model/recipe"
)
class RealModelTests(unittest.TestCase):
    """真实的模型和底库（CPU）：和模型自带的脚本给出同样的菜名和相似度。"""

    def test_it_agrees_with_the_script_that_ships_with_the_model(self):
        reference = reference_module()
        theirs = reference.RobamV3Classifier(model_path=MODEL, labels_path=LABELS, topk=1, device="cpu")
        ours = RecipeClassifier({"RECIPE_DETECT_CONFIG": {"model_path": str(MODEL), "labels_path": str(LABELS)}})
        self.assertEqual(ours.sess.get_providers(), ["CPUExecutionProvider"])
        self.assertEqual((len(ours.labels), ours.input_size), (len(theirs.base_labels), 224))

        rng = np.random.default_rng(3)
        images = [rng.integers(0, 256, size=(300, 500, 3), dtype=np.uint8)]
        photo = IMAGES / "segment_0902_0892.jpg"
        if photo.is_file():
            picture = cv2.imdecode(np.fromfile(str(photo), dtype=np.uint8), cv2.IMREAD_COLOR)
            images += [picture, picture[1:718, 51:1198]]  # 整张图和 GroundingDINO 在上面找到的框
        expected = [predictions[0] for predictions in theirs.predict_images(images)]
        found = ours.classify(images)
        self.assertEqual([match.label for match in found], [prediction.label for prediction in expected])
        for match, prediction in zip(found, expected):
            self.assertAlmostEqual(match.score, prediction.score, delta=1e-5)


CONFIG = {
    "TOTAL_CONFIG": {"device_id": 0},
    "EMBEDDING_CONFIG": {"model_path": "embedding.onnx", "scale": [224, 224]},
    "RECIPE_DETECT_CONFIG": {
        "enabled": True, "threshold": 0.95, "model_path": "recipe.onnx", "labels_path": "labels.pkl", "use_gpu": True,
    },
    "GLOBAL_FILTER": {"switch": False, "threshold": 0.7},
    "FLAB_CONFIG": {"API_URL": "http://127.0.0.1:9/x", "API_KEY": "k", "DOC_COUNT": 1,
                    "MAX_SNIPPET_LENGTH": 10, "THRESHOLD": 0.5, "EnableWaiting": False, "Timeout": 1},
}
MODEL_SECTION = {"model_path": "recipe.onnx", "labels_path": "labels.pkl", "use_gpu": True}


def app_settings():
    return SimpleNamespace(
        warmup=SimpleNamespace(enabled=True, embedding_runs=2, groundingdino_runs=4, segment_runs=5, recipe_runs=6),
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


class FakeRecipeClassifier:
    def __init__(self, config):
        self.config = copy.deepcopy(config)
        self.warmups = []
        self.warmup_error = None

    def warmup(self, runs=3):
        self.warmups.append(runs)
        if self.warmup_error is not None:
            raise self.warmup_error


class RecipeClassifierLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.created = []

        def create(config):
            classifier = FakeRecipeClassifier(config)
            self.created.append(classifier)
            return classifier

        for name, replacement in (
            ("create_recipe_classifier", Mock(side_effect=create)),
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
            config=config, config_info={"source": "test"}, http_client=Mock(), settings=app_settings(),
            embedder=embedding, food_service=SimpleNamespace(embeddingTool=embedding),
        )

    def reload(self, config):
        return lifespan.apply_nacos_config_update(self.runtime, self.provider, SimpleNamespace(config=config, data_ids=()))

    def test_without_the_step_the_model_is_not_loaded(self):
        for section in ("absent", None, {}, {"enabled": False, **MODEL_SECTION}, {"enabled": "off"}, dict(MODEL_SECTION)):
            with self.subTest(section=section):
                runtime = self.make_runtime()
                if section == "absent":
                    runtime.config.pop("RECIPE_DETECT_CONFIG")
                else:
                    runtime.config["RECIPE_DETECT_CONFIG"] = section
                self.assertIsNone(lifespan.initialize_recipe_classifier(runtime))
                self.assertIsNone(runtime.recipe_classifier_error)
                self.assertTrue(runtime.ready)
                with self.assertRaisesRegex(err.NotReadyError, "disabled or not initialized"):
                    runtime.require_recipe_classifier()
        lifespan.create_recipe_classifier.assert_not_called()

    def test_startup_loads_and_warms_one_shared_model_with_only_the_model_keys(self):
        classifier = lifespan.initialize_recipe_classifier(self.runtime)
        self.assertIs(self.runtime.require_recipe_classifier(), classifier)
        self.assertEqual(classifier.warmups, [6])
        self.assertEqual(classifier.config, {"RECIPE_DETECT_CONFIG": MODEL_SECTION, "TOTAL_CONFIG": {"device_id": 0}})

    def test_load_and_warmup_failures_only_take_the_recipe_step_down(self):
        for stage in ("load", "warmup"):
            with self.subTest(stage=stage):
                runtime = self.make_runtime()
                classifier = FakeRecipeClassifier(runtime.config)
                if stage == "load":
                    failure = FileNotFoundError("菜谱识别模型文件不存在: recipe.onnx")
                else:
                    failure = classifier.warmup_error = RuntimeError("CUDA out of memory")
                with patch.object(lifespan, "create_recipe_classifier", side_effect=failure if stage == "load" else None,
                                  return_value=classifier):
                    with self.assertLogs("lifespan", level="ERROR"):
                        self.assertIsNone(lifespan.initialize_recipe_classifier(runtime))
                self.assertIsNone(runtime.recipe_classifier)
                self.assertIn(str(failure), runtime.recipe_classifier_error)
                self.assertTrue(runtime.ready)
                with self.assertRaises(err.AppError) as caught:
                    runtime.require_recipe_classifier()
                self.assertEqual(caught.exception.code, "NOT_READY")
                self.assertIn(str(failure), caught.exception.detail)

    def test_a_new_threshold_does_not_reload_the_model(self):
        classifier = lifespan.initialize_recipe_classifier(self.runtime)
        updated = copy.deepcopy(CONFIG)
        updated["RECIPE_DETECT_CONFIG"]["threshold"] = 0.8
        updated["GLOBAL_FILTER"]["threshold"] = 0.9
        self.assertTrue(self.reload(updated))
        self.assertIs(self.runtime.config, updated)
        self.assertIs(self.runtime.require_recipe_classifier(), classifier)
        self.assertEqual((classifier.warmups, len(self.created)), ([6], 1))

    def test_a_model_or_device_change_loads_and_warms_a_replacement(self):
        for key, value in (("model_path", "recipe-v2.onnx"), ("labels_path", "labels-v2.pkl"), ("use_gpu", False),
                           ("device_id", 1)):
            with self.subTest(key=key):
                self.runtime = self.make_runtime()
                original = lifespan.initialize_recipe_classifier(self.runtime)
                updated = copy.deepcopy(CONFIG)
                (updated["TOTAL_CONFIG"] if key == "device_id" else updated["RECIPE_DETECT_CONFIG"])[key] = value
                self.assertTrue(self.reload(updated))
                replacement = self.runtime.require_recipe_classifier()
                self.assertIsNot(replacement, original)
                self.assertEqual(replacement.warmups, [6])
                self.assertEqual(replacement.config["TOTAL_CONFIG"], {"device_id": updated["TOTAL_CONFIG"]["device_id"]})

    def test_a_failed_replacement_keeps_the_previous_model_and_config(self):
        original = lifespan.initialize_recipe_classifier(self.runtime)
        original_config = self.runtime.config
        updated = copy.deepcopy(CONFIG)
        updated["RECIPE_DETECT_CONFIG"]["model_path"] = "recipe-v2.onnx"
        with patch.object(lifespan, "create_recipe_classifier", side_effect=RuntimeError("replacement failed")):
            with self.assertLogs("lifespan", level="ERROR"):
                self.assertFalse(self.reload(updated))
        self.assertIs(self.runtime.require_recipe_classifier(), original)
        self.assertIs(self.runtime.config, original_config)
        self.assertIn("replacement failed", self.runtime.config_info["last_reload_error"])

    def test_turning_the_step_on_later_loads_it_and_turning_it_off_unloads_it(self):
        self.runtime.config["RECIPE_DETECT_CONFIG"]["enabled"] = False
        self.assertIsNone(lifespan.initialize_recipe_classifier(self.runtime))
        self.assertTrue(self.reload(copy.deepcopy(CONFIG)))  # Nacos 打开了这一步
        self.assertEqual(self.runtime.require_recipe_classifier().warmups, [6])
        off = copy.deepcopy(CONFIG)
        off["RECIPE_DETECT_CONFIG"]["enabled"] = False
        self.assertTrue(self.reload(off))
        self.assertIsNone(self.runtime.recipe_classifier)
        self.assertEqual(len(self.created), 1)

    def test_the_lifespan_loads_it_before_serving_and_releases_it_on_shutdown(self):
        application = FastAPI()
        http_client = Mock()
        http_client.aclose = AsyncMock()
        with patch.object(lifespan, "settings", app_settings()), \
                patch.object(lifespan, "setup_logger"), \
                patch.object(lifespan, "NacosConfigProvider", return_value=Mock()), \
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
                    self.assertIs(runtime.require_recipe_classifier(), self.created[0])
                    self.assertEqual(self.created[0].warmups, [6])
                return runtime

            runtime = asyncio.run(serve())
        self.assertIsNone(runtime.recipe_classifier)


if __name__ == "__main__":
    unittest.main()
