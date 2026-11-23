"""/api/tools/recipe：业务搜图的菜谱识别摊开来看——检测框、每个框最相近的几道菜、哪些框过了阈值、
业务搜图会给出的菜名，另附整张图的识别结果。

不需要模型或 GPU：GdinoDetect 是真的（caption、NMS），只有模型那一步换成固定结果；菜谱识别模型
换成按调用顺序给出固定候选的假模型。检测框和业务搜图的目标检测（region_search.detect_regions）对照：
工具里看到的就是业务搜图用的框。
"""

import asyncio
import copy
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.services.region_search import detect_regions
from app.api.v1 import tools as tools_router
from app.bootstrap.state import ApplicationState
from app.clients.ground_dino import GdinoDetect
from app.clients.recipe_classifier import RecipeMatch
from app.core.config.settings import AutoSearchRegionSettings
from app.core.config.settings import settings as real_settings
from app.core.exceptions import register_exception_handlers

GDINO_CONFIG = {"GROUNDINGDINO_CONFIG": {
    "model_path": "gdino.onnx", "tokenizer_path": "bert-base-uncased", "caption": "object .",
    "box_threshold": 0.35, "text_threshold": 0.25, "nms_threshold": 0.5, "container_min_children": 0,
}}
# 200 × 100 的图上（归一化的 cx, cy, w, h，都是 float32 能精确表示的值）：food [25, 25, 75, 75]、
# dish [112, 18, 188, 82]、一个太小的 food（8 × 4 像素，丢掉）、food [81, 37, 119, 63]
RESULT = {
    "boxes": [[0.25, 0.5, 0.25, 0.5], [0.75, 0.5, 0.375, 0.625], [0.9, 0.1, 0.04, 0.04], [0.5, 0.5, 0.1875, 0.25]],
    "logits": [0.9, 0.5, 0.7, 0.45],
    "phrases": ["food", "dish", "food", "food"],
}
BOXES = [[25, 25, 75, 75], [112, 18, 188, 82], [81, 37, 119, 63]]
# 业务搜图的检测参数：区域搜索关着，菜谱识别照样按它检测
CONFIG = {
    "AUTO_SEARCH_REGION_CONFIG": {
        "enabled": False, "caption": "food . dish", "box_threshold": 0.4, "text_threshold": 0.3, "max_regions": 3,
    },
    "RECIPE_DETECT_CONFIG": {"enabled": True, "threshold": 0.9},
}
# 每个框（按检测置信度）和整张图的候选
CANDIDATES = [
    [RecipeMatch("清蒸鲈鱼", 0.93), RecipeMatch("红烧肉", 0.5)],
    [RecipeMatch("红烧肉", 0.97), RecipeMatch("清蒸鲈鱼", 0.6)],
    [RecipeMatch("凉拌猪耳", 0.9), RecipeMatch("红烧肉", 0.88)],  # 等于阈值：不算
    [RecipeMatch("红烧肉", 0.88), RecipeMatch("清蒸鲈鱼", 0.7)],  # 整张图
]


class CannedDetector(GdinoDetect):
    """真的 GdinoDetect，只把模型推理（predict_with_metadata）换成固定结果。"""

    def __init__(self, result=None):
        with patch.object(GdinoDetect, "load_model"):
            super().__init__(copy.deepcopy(GDINO_CONFIG))
        self.sess = object()  # detect() 只检查它不是 None
        self.result = result if result is not None else RESULT
        self.calls = []

    def predict_with_metadata(self, image, caption, box_threshold, text_threshold, *, validate_thresholds=True):
        self.calls.append({"shape": image.shape, "caption": caption,
                           "box_threshold": box_threshold, "text_threshold": text_threshold})
        return copy.deepcopy(self.result), {}


class FakeClassifier:
    """按调用顺序给出 candidates 里的候选（截到 k 个）；记下每张图的形状、k，以及当时 GroundingDINO
    的轮次有没有被占着（识别不该占着它）。
    """

    def __init__(self, runtime, candidates=CANDIDATES, *, error=None):
        self.runtime = runtime
        self.candidates = list(candidates)
        self.error = error
        self.calls = []

    def rank(self, image, k):
        self.calls.append({"shape": image.shape, "k": k, "turn_held": self.runtime.detector_turn.locked()})
        if self.error is not None:
            raise self.error
        return self.candidates.pop(0)[:k]


def png_bytes(width=200, height=100):
    ok, encoded = cv2.imencode(".png", np.full((height, width, 3), 120, dtype=np.uint8))
    assert ok
    return encoded.tobytes()


class RecipeToolHttpTests(unittest.TestCase):
    def setUp(self):
        self.runtime = ApplicationState(
            config=copy.deepcopy(CONFIG), config_info={}, http_client=Mock(), settings=real_settings
        )
        self.runtime.detector = self.detector = CannedDetector()
        self.runtime.recipe_classifier = self.classifier = FakeClassifier(self.runtime)
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(tools_router.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        state_patch = patch.object(tools_router, "get_application_state", return_value=self.runtime)
        state_patch.start()
        self.addCleanup(state_patch.stop)

    def post(self, data=None, **params):
        return self.client.post(
            "/api/tools/recipe", params=params,
            files={"file": ("dish.png", png_bytes() if data is None else data, "image/png")},
        )

    def test_it_shows_each_box_the_business_search_would_recognise_and_the_names_it_would_return(self):
        response = self.post()

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual((body["ok"], body["filename"], body["width"], body["height"]), (True, "dish.png", 200, 100))
        # 检测参数来自 AUTO_SEARCH_REGION_CONFIG（区域搜索关着也是），阈值来自 RECIPE_DETECT_CONFIG
        self.assertEqual(
            (body["caption"], body["box_threshold"], body["text_threshold"], body["max_regions"], body["threshold"]),
            ("food . dish", 0.4, 0.3, 3, 0.9),
        )
        self.assertEqual(self.detector.calls, [
            {"shape": (100, 200, 3), "caption": "food . dish", "box_threshold": 0.4, "text_threshold": 0.3},
        ])
        # GroundingDINO 给了 4 个框，太小的那个丢掉，按置信度排
        self.assertEqual(body["detected"], 4)
        self.assertEqual([(r["label"], r["score"], r["box"]) for r in body["regions"]], [
            ("food", 0.9, BOXES[0]), ("dish", 0.5, BOXES[1]), ("food", 0.45, BOXES[2]),
        ])
        self.assertEqual([r["candidates"] for r in body["regions"]], [
            [{"label": "清蒸鲈鱼", "score": 0.93}, {"label": "红烧肉", "score": 0.5}],
            [{"label": "红烧肉", "score": 0.97}, {"label": "清蒸鲈鱼", "score": 0.6}],
            [{"label": "凉拌猪耳", "score": 0.9}, {"label": "红烧肉", "score": 0.88}],
        ])
        self.assertEqual([r["accepted"] for r in body["regions"]], [True, True, False])
        # 业务搜图会给出的菜名：按相似度从高到低
        self.assertEqual(body["names"], ["红烧肉", "清蒸鲈鱼"])
        self.assertEqual(body["whole_image"], [{"label": "红烧肉", "score": 0.88}, {"label": "清蒸鲈鱼", "score": 0.7}])
        self.assertEqual(body["top_k"], 5)
        self.assertGreaterEqual(body["latency_ms"], body["classify_ms"])
        self.assertGreaterEqual(body["classify_ms"], 0)

        # 识别的是每个框的裁剪图，再是整张图；都在交出 GroundingDINO 的轮次之后
        self.assertEqual([call["shape"] for call in self.classifier.calls],
                         [(50, 50, 3), (64, 76, 3), (26, 38, 3), (100, 200, 3)])
        self.assertEqual({call["k"] for call in self.classifier.calls}, {5})
        self.assertFalse(any(call["turn_held"] for call in self.classifier.calls))
        self.assertFalse(self.runtime.detector_turn.locked())

    def test_the_boxes_are_the_ones_the_business_search_detects(self):
        body = self.post().json()
        settings = AutoSearchRegionSettings.from_config(CONFIG)
        found = asyncio.run(detect_regions(self.runtime, png_bytes(), settings, jpeg=False))
        self.assertIsNone(found.error)
        self.assertEqual([list(region.box) for region in found.regions], [r["box"] for r in body["regions"]])
        self.assertEqual([crop.shape for crop in found.crops], [tuple(call["shape"]) for call in self.classifier.calls[:3]])

    def test_the_request_overrides_detection_the_threshold_and_the_candidate_count(self):
        response = self.post(
            text_range=["food", "dish"], box_threshold=0.5, text_threshold=0.2, max_regions=1, threshold=0.95, top_k=1,
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(
            (body["caption"], body["box_threshold"], body["text_threshold"], body["max_regions"], body["threshold"]),
            ("food . dish .", 0.5, 0.2, 1, 0.95),
        )
        self.assertEqual(self.detector.calls[0]["caption"], "food . dish .")
        self.assertEqual([r["box"] for r in body["regions"]], [BOXES[0]])
        self.assertEqual(body["regions"][0]["candidates"], [{"label": "清蒸鲈鱼", "score": 0.93}])
        self.assertEqual((body["regions"][0]["accepted"], body["names"]), (False, []))  # 0.93 没过 0.95
        self.assertEqual(body["top_k"], 1)
        self.assertEqual([call["k"] for call in self.classifier.calls], [1, 1])  # 一个框，加上整张图

    def test_without_boxes_there_are_no_names_but_the_whole_image_is_still_shown(self):
        self.runtime.detector = CannedDetector({"boxes": [], "logits": [], "phrases": []})
        self.runtime.recipe_classifier = classifier = FakeClassifier(self.runtime, [CANDIDATES[3]])
        body = self.post().json()
        self.assertEqual((body["detected"], body["regions"], body["names"]), (0, [], []))
        self.assertEqual(body["whole_image"][0], {"label": "红烧肉", "score": 0.88})
        self.assertEqual([call["shape"] for call in classifier.calls], [(100, 200, 3)])

    def test_it_is_not_ready_without_either_model_and_says_why(self):
        self.runtime.recipe_classifier = None
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual((response.json()["error"], response.json()["detail"]),
                         ("NOT_READY", "recipe model is disabled or not initialized"))

        self.runtime.recipe_classifier = FakeClassifier(self.runtime)
        self.runtime.detector, self.runtime.detector_error = None, "FileNotFoundError: groundingdino_swint_ogc.onnx"
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], self.runtime.detector_error)

    def test_a_detection_configuration_it_cannot_use_is_not_ready_and_names_the_key(self):
        for section, said in (
            ({"max_regions": 0}, "AUTO_SEARCH_REGION_CONFIG.max_regions"),       # 区域搜索关着，只有用到时才检查
            ({"box_threshold": "high"}, "AUTO_SEARCH_REGION_CONFIG.box_threshold"),
        ):
            with self.subTest(section=section):
                self.runtime.config = copy.deepcopy(CONFIG)
                self.runtime.config["AUTO_SEARCH_REGION_CONFIG"].update(section)
                response = self.post()
                self.assertEqual(response.status_code, 503, response.text)
                self.assertEqual(response.json()["error"], "NOT_READY")
                self.assertIn(said, response.json()["detail"])
        self.assertEqual(self.detector.calls, [])
        # 请求里给了 max_regions，配置里那个不对的值就用不到
        self.runtime.config = copy.deepcopy(CONFIG)
        self.runtime.config["AUTO_SEARCH_REGION_CONFIG"]["max_regions"] = 0
        response = self.post(max_regions=2)
        self.assertEqual(response.status_code, 200, response.text)

    def test_parameters_out_of_range_are_rejected_before_anything_runs(self):
        for params in ({"threshold": 1.5}, {"max_regions": 0}, {"max_regions": 21}, {"top_k": 0}, {"top_k": 11},
                       {"box_threshold": -0.1}):
            with self.subTest(params=params):
                response = self.post(**params)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["error"], "VALIDATION_ERROR")
        self.assertEqual((self.detector.calls, self.classifier.calls), ([], []))

    def test_bytes_that_are_not_an_image_are_unprocessable(self):
        response = self.post(b"definitely not an image")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"], "INVALID_IMAGE")
        self.assertEqual((self.detector.calls, self.classifier.calls), ([], []))
        self.assertFalse(self.runtime.detector_turn.locked())

    def test_a_failing_recipe_model_is_a_server_error(self):
        self.runtime.recipe_classifier = FakeClassifier(self.runtime, error=RuntimeError("菜谱识别模型输出的形状不对"))
        with self.assertLogs("svc.tools", level="ERROR"):
            response = self.post()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"], "TOOL_FAILED")
        self.assertFalse(self.runtime.detector_turn.locked())


if __name__ == "__main__":
    unittest.main()
