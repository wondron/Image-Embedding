"""/api/tools/gdino：上传 → 共享的 GroundingDINO → 每个框的词、置信度和原图上的像素坐标。
不需要模型或 GPU：GdinoDetect 是真的（caption、默认阈值、NMS），只有模型那一步换成固定结果。
"""

import asyncio
import copy
import io
import threading
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np
from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from app.api.services import tools
from app.api.v1 import tools as tools_router
from app.bootstrap.state import ApplicationState
from app.clients.ground_dino import GdinoDetect
from app.core.config.settings import settings as real_settings
from app.core.exceptions import register_exception_handlers


GDINO_CONFIG = {"GROUNDINGDINO_CONFIG": {
    "model_path": "gdino.onnx", "tokenizer_path": "bert-base-uncased", "caption": "food .",
    "box_threshold": 0.35, "text_threshold": 0.25, "nms_threshold": 0.5,
}}
# 模型输出（归一化的 cx, cy, w, h）：dish 超出图片，food 有一个完全重叠、分数更低的框（NMS 去掉）
RESULT = {
    "boxes": [[0.75, 0.5, 0.625, 1.25], [0.25, 0.5, 0.25, 0.5], [0.25, 0.5, 0.25, 0.5]],
    "logits": [0.5, 0.9, 0.6],
    "phrases": ["dish", "food", "food"],
}


class CannedDetector(GdinoDetect):
    """真的 GdinoDetect，只把模型推理（predict_with_metadata）换成固定结果。"""

    def __init__(self, result=None, *, error=None):
        with patch.object(GdinoDetect, "load_model"):
            super().__init__(copy.deepcopy(GDINO_CONFIG))
        self.sess = object()  # detect() 只检查它不是 None
        self.result = result if result is not None else {"boxes": [], "logits": [], "phrases": []}
        self.error = error
        self.calls = []

    def predict_with_metadata(self, image, caption, box_threshold, text_threshold, *, validate_thresholds=True):
        self.calls.append({"shape": image.shape, "caption": caption,
                           "box_threshold": box_threshold, "text_threshold": text_threshold})
        if self.error is not None:
            raise self.error
        return copy.deepcopy(self.result), {}


def png_bytes(width=200, height=100):
    ok, encoded = cv2.imencode(".png", np.full((height, width, 3), 120, dtype=np.uint8))
    assert ok
    return encoded.tobytes()


class GdinoToolHttpTests(unittest.TestCase):
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

    def post(self, data=None, **params):
        return self.client.post(
            "/api/tools/gdino", params=params,
            files={"file": ("dish.png", png_bytes() if data is None else data, "image/png")},
        )

    def test_detections_are_pixel_boxes_on_the_original_image_ranked_by_score(self):
        self.runtime.detector = detector = CannedDetector(RESULT)
        response = self.post()

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual((body["ok"], body["filename"], body["width"], body["height"]), (True, "dish.png", 200, 100))
        self.assertEqual((body["caption"], body["box_threshold"], body["text_threshold"]), ("food .", 0.35, 0.25))
        self.assertEqual(body["detections"], [
            {"label": "food", "score": 0.9, "box": [25, 25, 75, 75]},
            {"label": "dish", "score": 0.5, "box": [87, 0, 200, 100]},  # 裁到图内
        ])
        self.assertGreaterEqual(body["latency_ms"], 0)
        self.assertEqual(detector.calls, [
            {"shape": (100, 200, 3), "caption": "food .", "box_threshold": 0.35, "text_threshold": 0.25},
        ])
        self.assertFalse(self.runtime.detector_turn.locked())

    def test_text_range_and_thresholds_override_the_configuration(self):
        for text_range, caption in (
            (["food", "dish"], "food . dish ."),  # 多个类别
            (["food . dish"], "food . dish"),     # 一个 caption，和 AUTO_SEARCH_REGION_CONFIG.caption 一样
            ([" "], "food ."),                    # 空白：用 GROUNDINGDINO_CONFIG.caption
        ):
            with self.subTest(text_range=text_range):
                self.runtime.detector = detector = CannedDetector()
                response = self.post(text_range=text_range, box_threshold=0.5, text_threshold=0.3)
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertEqual((body["caption"], body["box_threshold"], body["text_threshold"]), (caption, 0.5, 0.3))
                self.assertEqual(body["detections"], [])
                self.assertEqual(
                    (detector.calls[0]["caption"], detector.calls[0]["box_threshold"], detector.calls[0]["text_threshold"]),
                    (caption, 0.5, 0.3),
                )

    def test_thresholds_outside_zero_to_one_are_rejected_before_detection(self):
        self.runtime.detector = detector = CannedDetector()
        for params in ({"box_threshold": 1.5}, {"text_threshold": -0.1}):
            with self.subTest(params=params):
                response = self.post(**params)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["error"], "VALIDATION_ERROR")
        self.assertEqual(detector.calls, [])

    def test_a_detector_that_did_not_load_is_not_ready_and_says_why(self):
        self.runtime.detector_error = "FileNotFoundError: groundingdino_swint_ogc.onnx"
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual((response.json()["error"], response.json()["detail"]),
                         ("NOT_READY", self.runtime.detector_error))

    def test_bytes_that_are_not_an_image_are_unprocessable(self):
        self.runtime.detector = detector = CannedDetector()
        response = self.post(b"definitely not an image")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"], "INVALID_IMAGE")
        self.assertEqual(detector.calls, [])

    def test_a_model_or_configuration_error_after_decoding_is_a_server_error(self):
        self.runtime.detector = CannedDetector(error=ValueError("Graph input images expects [1, 3, 800, 800]"))
        with self.assertLogs("svc.tools", level="ERROR"):
            response = self.post()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"], "TOOL_FAILED")
        self.assertFalse(self.runtime.detector_turn.locked())


class TextRangeTests(unittest.TestCase):
    def test_blank_terms_are_dropped_and_a_single_term_is_one_caption(self):
        for value, expected in (
            (None, None), ("", None), ([], None), (["", "  "], None),
            ("  food . dish ", "food . dish"), (["food"], "food"), (["food", " dish "], ["food", "dish"]),
        ):
            with self.subTest(value=value):
                self.assertEqual(tools._text_range(value), expected)


class BlockingDetector(CannedDetector):
    """推理开始时通知测试，等测试放行才结束。"""

    def __init__(self, loop):
        super().__init__()
        self.loop = loop
        self.entered = asyncio.Event()
        self.release = threading.Event()

    def predict_with_metadata(self, *args, **kwargs):
        self.loop.call_soon_threadsafe(self.entered.set)
        if not self.release.wait(5):
            raise RuntimeError("the test did not release the detector")
        return super().predict_with_metadata(*args, **kwargs)


class GdinoToolTurnTests(unittest.IsolatedAsyncioTestCase):
    """和区域搜索轮流使用 GroundingDINO（ApplicationState.detector_turn）。"""

    def setUp(self):
        self.runtime = ApplicationState(config={}, config_info={}, http_client=Mock(), settings=real_settings)

    def upload(self):
        upload = UploadFile(file=io.BytesIO(png_bytes()), filename="dish.png",
                            headers=Headers({"content-type": "image/png"}))
        self.addAsyncCleanup(upload.close)
        return upload

    async def test_it_waits_while_a_region_search_has_the_detector(self):
        self.runtime.detector = detector = BlockingDetector(asyncio.get_running_loop())
        detector.release.set()
        turn = self.runtime.detector_turn
        await turn.acquire()  # 一次区域搜索正在用模型
        request = asyncio.create_task(tools.gdino(self.runtime, self.upload()))
        try:
            await asyncio.sleep(0.05)
            self.assertFalse(request.done())
            self.assertEqual(detector.calls, [])
        finally:
            turn.release()
        response = await asyncio.wait_for(request, 5)
        self.assertEqual(len(detector.calls), 1)
        self.assertEqual(response.caption, "food .")
        self.assertFalse(turn.locked())

    async def test_a_request_cancelled_during_detection_keeps_the_turn_until_the_model_is_done(self):
        self.runtime.detector = detector = BlockingDetector(asyncio.get_running_loop())
        turn = self.runtime.detector_turn
        request = asyncio.create_task(tools.gdino(self.runtime, self.upload()))
        try:
            await asyncio.wait_for(detector.entered.wait(), 5)
            request.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await request
            self.assertTrue(turn.locked())  # 模型还在跑，轮次没有交出去
        finally:
            detector.release.set()
        await asyncio.wait_for(turn.acquire(), 5)  # 这次运行结束后才轮到下一个
        turn.release()
        self.assertEqual(len(detector.calls), 1)


if __name__ == "__main__":
    unittest.main()
