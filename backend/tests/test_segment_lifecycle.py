"""分割模型（SEGMENT_CONFIG）：启动时加载并预热、Nacos 重新加载时复用或替换，以及
/api/tools/segment 的调用。不需要模型文件或 GPU。
"""

import asyncio
import base64
import copy
import io
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import cv2
import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.api.v1 import tools as tools_router
from app.bootstrap import lifespan
from app.bootstrap.state import ApplicationState
from app.clients.inference import EmbeddingSpec
from app.core import errors as err
from app.core.config.settings import settings as real_settings
from app.core.exceptions import register_exception_handlers
from app.services.predict.pipeline import a_onnx_segment
from app.services.predict.pipeline.a_onnx_segment import DEFAULT_INPUT_SIZE, OnnxSegmentPy


CONFIG = {
    "TOTAL_CONFIG": {"device_id": 0},
    "EMBEDDING_CONFIG": {"model_path": "embedding.onnx", "scale": [224, 224]},
    "SEGMENT_CONFIG": {"model_path": "segment.onnx", "use_gpu": True, "mean": [0.485, 0.456, 0.406]},
    "GLOBAL_FILTER": {"switch": False, "threshold": 0.7},
    "FLAB_CONFIG": {"API_URL": "http://127.0.0.1:9/x", "API_KEY": "k", "DOC_COUNT": 1,
                    "MAX_SNIPPET_LENGTH": 10, "THRESHOLD": 0.5, "EnableWaiting": False, "Timeout": 1},
}


def app_settings():
    return SimpleNamespace(
        warmup=SimpleNamespace(enabled=True, embedding_runs=2, groundingdino_runs=4, segment_runs=5),
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


class FakeSegmentor:
    def __init__(self, config):
        self.config = copy.deepcopy(config)
        self.warmups = []
        self.warmup_error = None

    def warmup(self, runs=3):
        self.warmups.append(runs)
        if self.warmup_error is not None:
            raise self.warmup_error


class SegmentorLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.created = []

        def create_segmentor(config):
            segmentor = FakeSegmentor(config)
            self.created.append(segmentor)
            return segmentor

        for name, replacement in (
            ("create_segmentor", Mock(side_effect=create_segmentor)),
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
        disabled = {"enabled": "false", "model_path": "segment.onnx"}
        for section in ("absent", {}, None, {"enabled": False}, disabled):
            with self.subTest(section=section):
                runtime = self.make_runtime()
                if section == "absent":
                    runtime.config.pop("SEGMENT_CONFIG")
                else:
                    runtime.config["SEGMENT_CONFIG"] = section
                self.assertIsNone(lifespan.initialize_segmentor(runtime))
                self.assertIsNone(runtime.segmentor_error)
                self.assertTrue(runtime.ready)
                with self.assertRaisesRegex(err.NotReadyError, "disabled or not initialized"):
                    runtime.require_segmentor()
        lifespan.create_segmentor.assert_not_called()

    def test_startup_loads_and_warms_one_shared_segmentor(self):
        segmentor = lifespan.initialize_segmentor(self.runtime)
        self.assertIs(self.runtime.require_segmentor(), segmentor)
        self.assertEqual(segmentor.warmups, [5])
        self.assertIsNone(self.runtime.segmentor_error)
        lifespan.create_segmentor.assert_called_once()
        self.assertEqual(
            segmentor.config,
            {"SEGMENT_CONFIG": CONFIG["SEGMENT_CONFIG"], "TOTAL_CONFIG": {"device_id": 0}},
        )

    def test_disabled_warmup_still_loads_the_model(self):
        self.runtime.settings.warmup.enabled = False
        segmentor = lifespan.initialize_segmentor(self.runtime)
        self.assertIs(self.runtime.require_segmentor(), segmentor)
        self.assertEqual(segmentor.warmups, [])

    def test_startup_load_and_warmup_failures_only_take_the_segment_tool_down(self):
        for stage in ("load", "warmup"):
            with self.subTest(stage=stage):
                runtime = self.make_runtime()
                segmentor = FakeSegmentor(runtime.config)
                if stage == "load":
                    failure = FileNotFoundError("segment 模型文件不存在: segment.onnx")
                else:
                    failure = segmentor.warmup_error = RuntimeError("CUDA out of memory")
                with patch.object(lifespan, "create_segmentor", side_effect=failure if stage == "load" else None,
                                  return_value=segmentor):
                    with self.assertLogs("lifespan", level="ERROR"):
                        self.assertIsNone(lifespan.initialize_segmentor(runtime))
                self.assertIsNone(runtime.segmentor)
                self.assertIn(str(failure), runtime.segmentor_error)
                self.assertIsNone(runtime.initialization_error)
                self.assertIsNone(runtime.warmup_error)
                self.assertTrue(runtime.ready)
                with self.assertRaises(err.AppError) as caught:
                    runtime.require_segmentor()
                self.assertEqual((caught.exception.code, caught.exception.http_status), ("NOT_READY", 503))
                self.assertIn(str(failure), caught.exception.detail)

    def test_reload_with_the_same_segment_configuration_does_not_reload_or_rewarm(self):
        segmentor = lifespan.initialize_segmentor(self.runtime)
        updated = copy.deepcopy(CONFIG)
        updated["GLOBAL_FILTER"]["threshold"] = 0.9
        self.assertTrue(self.reload(updated))
        self.assertIs(self.runtime.config, updated)
        self.assertIs(self.runtime.require_segmentor(), segmentor)
        self.assertEqual(segmentor.warmups, [5])
        self.assertEqual(len(self.created), 1)

    def test_segment_or_device_change_loads_and_warms_a_replacement(self):
        for key, value in (("model_path", "segment-v2.onnx"), ("use_gpu", False),
                           ("std", [0.5, 0.5, 0.5]), ("device_id", 1)):
            with self.subTest(key=key):
                self.runtime = self.make_runtime()
                original = lifespan.initialize_segmentor(self.runtime)
                updated = copy.deepcopy(CONFIG)
                section = updated["TOTAL_CONFIG"] if key == "device_id" else updated["SEGMENT_CONFIG"]
                section[key] = value
                self.assertTrue(self.reload(updated))
                replacement = self.runtime.require_segmentor()
                self.assertIsNot(replacement, original)
                self.assertEqual(replacement.config["SEGMENT_CONFIG"], updated["SEGMENT_CONFIG"])
                self.assertEqual(replacement.config["TOTAL_CONFIG"], {"device_id": updated["TOTAL_CONFIG"]["device_id"]})
                self.assertEqual(replacement.warmups, [5])
                self.assertEqual(original.warmups, [5])

    def test_a_failed_replacement_keeps_the_previous_model_service_and_config(self):
        original = lifespan.initialize_segmentor(self.runtime)
        original_config = self.runtime.config
        original_service = self.runtime.food_service
        updated = copy.deepcopy(CONFIG)
        updated["SEGMENT_CONFIG"]["model_path"] = "segment-v2.onnx"
        for stage in ("load", "warmup"):
            with self.subTest(stage=stage):
                replacement = FakeSegmentor(updated)
                failure = RuntimeError(f"replacement {stage} failed")
                if stage == "warmup":
                    replacement.warmup_error = failure
                with patch.object(lifespan, "create_segmentor", return_value=replacement,
                                  side_effect=failure if stage == "load" else None):
                    with self.assertLogs("lifespan", level="ERROR"):
                        self.assertFalse(self.reload(updated))
                self.assertIs(self.runtime.require_segmentor(), original)
                self.assertIs(self.runtime.config, original_config)
                self.assertIs(self.runtime.food_service, original_service)
                self.assertIn(str(failure), self.runtime.config_info["last_reload_error"])
                self.assertTrue(self.runtime.ready)

    def test_a_startup_failure_is_retried_by_the_next_reload(self):
        with patch.object(lifespan, "create_segmentor", side_effect=FileNotFoundError("segment.onnx")):
            with self.assertLogs("lifespan", level="ERROR"):
                lifespan.initialize_segmentor(self.runtime)
        self.assertIsNotNone(self.runtime.segmentor_error)

        self.assertTrue(self.reload(copy.deepcopy(CONFIG)))
        segmentor = self.runtime.require_segmentor()
        self.assertEqual(segmentor.warmups, [5])
        self.assertIsNone(self.runtime.segmentor_error)

    def test_removal_and_explicit_disable_unload_the_model_on_reload(self):
        for section in ("absent", None, {}, {"enabled": False}):
            with self.subTest(section=section):
                self.runtime = self.make_runtime()
                lifespan.initialize_segmentor(self.runtime)
                updated = copy.deepcopy(CONFIG)
                if section == "absent":
                    updated.pop("SEGMENT_CONFIG")
                else:
                    updated["SEGMENT_CONFIG"] = section
                count = len(self.created)
                self.assertTrue(self.reload(updated))
                self.assertIsNone(self.runtime.segmentor)
                self.assertIsNone(self.runtime.segmentor_error)
                self.assertEqual(len(self.created), count)
                self.assertTrue(self.runtime.ready)

    def test_lifespan_warms_the_segmentor_before_serving_and_releases_it_on_shutdown(self):
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
                    self.assertIs(runtime.require_segmentor(), self.created[0])
                    self.assertEqual(self.created[0].warmups, [5])
                    self.assertTrue(runtime.ready)
                return runtime

            runtime = asyncio.run(serve())
        lifespan.create_segmentor.assert_called_once()
        self.assertIsNone(runtime.segmentor)
        with self.assertRaises(err.NotReadyError):
            runtime.require_segmentor()


class SegmentModelLoadedOnceTests(unittest.TestCase):
    """真实的 create_segmentor 和 FoodService：配置重新加载不会再读一遍分割模型的权重。"""

    def test_food_service_and_config_reloads_do_not_load_the_segment_model_again(self):
        loaded = []

        class CountingSegmentor(FakeSegmentor):
            def __init__(self, config):
                super().__init__(config)
                loaded.append(self)

        runtime = ApplicationState(
            config=copy.deepcopy(CONFIG), config_info={}, http_client=Mock(), settings=app_settings()
        )
        provider = Mock()
        provider.source_info.return_value = {}
        updated = copy.deepcopy(CONFIG)
        updated["GLOBAL_FILTER"]["threshold"] = 0.9
        with patch.object(a_onnx_segment, "OnnxSegmentPy", CountingSegmentor), \
                patch.object(lifespan, "create_embedder", side_effect=FakeEmbedder):
            embedder = lifespan.initialize_embedder(runtime)
            lifespan.initialize_segmentor(runtime)
            lifespan.initialize_food_service(runtime, embedder)
            snapshot = SimpleNamespace(config=updated, data_ids=())
            self.assertTrue(lifespan.apply_nacos_config_update(runtime, provider, snapshot))

        self.assertEqual(len(loaded), 1)
        self.assertIs(runtime.require_segmentor(), loaded[0])
        self.assertEqual(loaded[0].warmups, [5])
        self.assertEqual(runtime.food_service.searchFilter.threshold, 0.9)
        self.assertFalse(hasattr(runtime.food_service, "segmentor"))


class HalfAndHalfSession:
    """假的 CPU session：输出 2 类 logits，右半边是类别 1，左半边是类别 0。"""

    def __init__(self, size=8):
        self.size = size
        self.runs = []

    def get_providers(self):
        return ["CPUExecutionProvider"]

    def run(self, outputs, feeds):
        self.runs.append(feeds["image"].shape)
        logits = np.zeros((1, 2, self.size, self.size), dtype=np.float32)
        logits[0, 1, :, self.size // 2:] = 1.0
        return [logits]


def model_with(session=None, size=8):
    with patch.object(OnnxSegmentPy, "load_model"):
        model = OnnxSegmentPy({"SEGMENT_CONFIG": {"model_path": "segment.onnx"}})
    model.sess = session or HalfAndHalfSession(size)
    model.input_name, model.output_name = "image", "seg_logits"
    model.input_h = model.input_w = size
    return model


def png_bytes(image_bgr):
    ok, encoded = cv2.imencode(".png", image_bgr)
    assert ok
    return encoded.tobytes()


def decode_png(data):
    return np.array(Image.open(io.BytesIO(base64.b64decode(data))))


class OnnxSegmentPyTests(unittest.TestCase):
    def test_bytes_and_arrays_give_a_mask_at_the_original_size(self):
        model = model_with()
        image = np.full((30, 40, 3), 90, dtype=np.uint8)
        for data in (png_bytes(image), image):
            with self.subTest(input=type(data).__name__):
                mask, visual = model.detect(data)
                self.assertEqual((mask.shape, mask.dtype), ((30, 40), np.uint8))
                self.assertEqual(set(np.unique(mask[:, :20])), {0})
                self.assertEqual(set(np.unique(mask[:, 20:])), {1})
                self.assertIsNone(visual)
        self.assertEqual(model.sess.runs, [(1, 3, 8, 8)] * 2)

    def test_invalid_images_fail_with_value_error_before_inference(self):
        model = model_with()
        for data in (b"", b"definitely not an image", np.zeros((30, 40), dtype=np.uint8),
                     np.zeros((0, 0, 3), dtype=np.uint8)):
            with self.subTest(data=data[:8] if isinstance(data, bytes) else data.shape):
                with self.assertRaises(ValueError):
                    model.detect(data)
        self.assertEqual(model.sess.runs, [])

    def test_unsupported_model_output_is_a_runtime_error(self):
        class FiveDims(HalfAndHalfSession):
            def run(self, outputs, feeds):
                return [np.zeros((1, 1, 2, 8, 8), dtype=np.float32)]

        with self.assertRaisesRegex(RuntimeError, "输出维度"):
            model_with(FiveDims()).detect(np.zeros((30, 40, 3), dtype=np.uint8))

    def test_warmup_runs_the_whole_detect_at_the_model_input_size(self):
        model = model_with()
        model.warmup(2)
        self.assertEqual(model.sess.runs, [(1, 3, 8, 8)] * 2)

    def test_a_missing_model_path_or_file_fails_the_construction(self):
        with self.assertRaisesRegex(ValueError, "model_path"):
            OnnxSegmentPy({"SEGMENT_CONFIG": {}})
        with self.assertRaisesRegex(FileNotFoundError, "missing-segment.onnx"):
            OnnxSegmentPy({"SEGMENT_CONFIG": {"model_path": "missing-segment.onnx"}})

    def test_cuda_failure_falls_back_to_cpu_and_a_dynamic_input_size_is_fixed_at_load(self):
        cpu_session = Mock()
        cpu_session.get_inputs.return_value = [SimpleNamespace(name="x", shape=["N", 3, "H", "W"])]
        cpu_session.get_outputs.return_value = [SimpleNamespace(name="seg_logits")]
        cpu_session.get_providers.return_value = ["CPUExecutionProvider"]
        with tempfile.TemporaryDirectory() as folder:
            model_path = os.path.join(folder, "segment.onnx")
            with open(model_path, "wb") as fh:
                fh.write(b"onnx")
            with patch.object(a_onnx_segment.ort, "get_available_providers",
                              return_value=["CUDAExecutionProvider", "CPUExecutionProvider"]), \
                    patch.object(a_onnx_segment.ort, "InferenceSession",
                                 side_effect=[RuntimeError("CUDA driver is too old"), cpu_session]) as create, \
                    self.assertLogs("segment", level="WARNING") as logs:
                model = OnnxSegmentPy({"SEGMENT_CONFIG": {"model_path": model_path, "use_gpu": True}})

        self.assertIs(model.sess, cpu_session)
        self.assertEqual(create.call_args_list[1].kwargs["providers"], [("CPUExecutionProvider", {})])
        self.assertIn("CUDA driver is too old", "\n".join(logs.output))
        self.assertEqual((model.input_h, model.input_w), (DEFAULT_INPUT_SIZE, DEFAULT_INPUT_SIZE))
        self.assertEqual((model.input_name, model.output_name), ("x", "seg_logits"))


class SegmentToolTests(unittest.TestCase):
    """/api/tools/segment：上传 → 共享的分割模型 → 原图尺寸的 mask（PNG）。"""

    def setUp(self):
        self.runtime = ApplicationState(config={}, config_info={}, http_client=Mock(), settings=real_settings)
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(tools_router.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        state_patch = patch.object(tools_router, "get_application_state", return_value=self.runtime)
        state_patch.start()
        self.addCleanup(state_patch.stop)
        self.image = np.full((30, 40, 3), (200, 100, 50), dtype=np.uint8)  # 高 30、宽 40 的 BGR 图

    def post(self, data, *, output_vis=False):
        return self.client.post(
            "/api/tools/segment",
            params={"output_vis": output_vis},
            files={"file": ("dish.png", data, "image/png")},
        )

    def test_an_upload_is_segmented_at_its_original_size(self):
        self.runtime.segmentor = model_with()
        response = self.post(png_bytes(self.image), output_vis=True)

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual((body["ok"], body["filename"], body["width"], body["height"]), (True, "dish.png", 40, 30))
        self.assertGreaterEqual(body["latency_ms"], 0)
        mask = decode_png(body["mask_png_base64"])
        self.assertEqual(mask.shape, (30, 40))
        self.assertEqual((set(np.unique(mask[:, :20])), set(np.unique(mask[:, 20:]))), ({0}, {1}))
        visual = decode_png(body["visual_png_base64"])  # RGB
        self.assertEqual(visual.shape, (30, 40, 3))
        np.testing.assert_array_equal(visual[0, 0], self.image[0, 0, ::-1])  # 背景不变
        self.assertGreater(int(visual[0, -1, 1]), int(self.image[0, -1, 1]))  # 前景叠了绿色

    def test_without_output_vis_there_is_no_overlay(self):
        self.runtime.segmentor = model_with()
        body = self.post(png_bytes(self.image)).json()
        self.assertIsNone(body["visual_png_base64"])
        self.assertEqual(decode_png(body["mask_png_base64"]).shape, (30, 40))

    def test_a_model_that_did_not_load_is_not_ready_and_says_why(self):
        self.runtime.segmentor_error = "FileNotFoundError: segment 模型文件不存在: segment.onnx"
        response = self.post(png_bytes(self.image))
        self.assertEqual(response.status_code, 503)
        self.assertEqual((response.json()["error"], response.json()["detail"]),
                         ("NOT_READY", self.runtime.segmentor_error))

    def test_bytes_that_are_not_an_image_are_unprocessable(self):
        self.runtime.segmentor = model_with()
        response = self.post(b"definitely not an image")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"], "INVALID_IMAGE")
        self.assertEqual(self.runtime.segmentor.sess.runs, [])

    def test_a_bad_model_output_is_a_server_error(self):
        self.runtime.segmentor = SimpleNamespace(
            detect=Mock(return_value=(np.zeros((30, 40, 3), dtype=np.uint8), None))
        )
        with self.assertLogs("svc.tools", level="ERROR"):
            response = self.post(png_bytes(self.image))
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"], "TOOL_FAILED")


if __name__ == "__main__":
    unittest.main()
