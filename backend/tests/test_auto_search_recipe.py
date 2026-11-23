"""带菜谱识别的业务搜图（RECIPE_DETECT_CONFIG，app.api.services.recipe_detect）。

菜谱识别和整图搜索同时启动：GroundingDINO 在查询图里检测（参数在 AUTO_SEARCH_REGION_CONFIG，和区域搜索
共用一次检测），每个检测框的裁剪图交给菜谱识别模型，相似度高于 threshold 的菜名（同名只留一个，按相似度
从高到低）作为一张结果卡（source=recipe）。整图有结果时立即作为答案返回，识别被丢弃；否则菜谱卡排在区域
结果之后（tests/test_auto_search_regions.py），都没有结果时才走 Global 兜底。识别的结果被用到时写它那一行，
识别失败从不让请求失败。这些测试里区域搜索是关闭的：检测照样按它的参数做。

检测器和菜谱识别模型都是假的：检测器给出两个框，模型按裁剪图的尺寸回答。

    整图搜索完成 | … | 结果=0
    菜谱识别完成 | 排队 … | 图像解码 … | 目标检测 … | 区域裁剪 … | 菜谱推理 … | 耗时 … | 检测框=2 | 结果=["荷塘小炒", "清蒸鲈鱼"] | 阈值=0.95 | 各框最相近=荷塘小炒 0.9700、清蒸鲈鱼 0.9600
    业务搜图(图片链接) | 接收请求 … | 图像下载 … | 整图搜索 … | 等待菜谱识别 … | 总耗时 … | 参数=…
"""

import logging
import re
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.services import auto_search as auto_search_service
from app.api.services import region_search
from app.api.services.recipe_detect import dish_names
from app.api.v1 import auto_search
from app.bootstrap.state import ApplicationState
from app.clients.recipe_classifier import RecipeMatch
from app.core import errors as err
from app.core.config.settings import RecipeDetectSettings
from app.core.exceptions import register_exception_handlers
from app.core.middleware import TraceIdMiddleware
from app.services.predict.food_service import FoodService
from app.services.retrieval.active_model import ActiveModel
from app.services.retrieval.search_image import SearchImageUseCase

WIDTH, HEIGHT = 200, 100
ROW_ID = "11111111-1111-1111-1111-111111111111"
# GroundingDINO 的检测框（归一化的 cx, cy, w, h），以及它们裁出来的尺寸（高, 宽）
LEFT, LEFT_CROP = (0.25, 0.5, 0.5, 1.0), (100, 100)  # x 0..100, y 0..100
RIGHT, RIGHT_CROP = (0.875, 0.5, 0.25, 0.5), (50, 50)  # x 150..200, y 25..75
TINY = (0.5, 0.5, 0.0625, 0.0625)  # 大约 13 x 6 像素：丢掉

# 区域搜索关着，检测照样用这些参数
REGION_CONFIG = {"enabled": False, "caption": "food . dish", "box_threshold": 0.3, "text_threshold": 0.25, "max_regions": 5}
RECIPE_CONFIG = {"enabled": True, "threshold": 0.95}
# 识别加上的那一张卡：菜名作为 tags，其余卡片字段为空
RECIPE_CARD = {
    "source": "recipe", "tags": "荷塘小炒、清蒸鲈鱼", "description": "", "original_url": None,
    "similarity": None, "details": {},
}
CARD_KEYS = ["source", "tags", "description", "original_url", "similarity", "details"]  # 按 wire 上的顺序
GLOBAL_CARD = {"tags": "番茄", "description": "", "original_url": "https://img.example/a.jpg", "similarity": 0.8, "details": {}}
FOOD_CONFIG = {
    "FLAB_CONFIG": {
        "API_URL": "https://search.invalid/global_search", "API_KEY": "test-only-key", "DOC_COUNT": 5,
        "MAX_SNIPPET_LENGTH": 300, "THRESHOLD": 0.8, "EnableWaiting": False, "Timeout": 1,
    },
    "GLOBAL_FILTER": {"switch": True, "threshold": 0.5},
}


def encoded(width=WIDTH, height=HEIGHT):
    ok, data = cv2.imencode(".png", np.full((height, width, 3), 127, dtype=np.uint8))
    assert ok
    return data.tobytes()


QUERY = encoded()


def steps(line):
    """一行耗时里的各步骤名，按顺序，总耗时（或耗时）在最后；其余字段（检测框=…）不算。
    timer_tool.summary 的两种写法都认：「a 0.100s | b 0.200s | 耗时 0.300s」和「耗时 0.300s a_0.100s b_0.200s」。
    """
    total_first = re.search(r"(?:^|\| )((?:总)?耗时) \d+\.\d{3}s((?: [^ |=]+?_\d+\.\d{3}s)*)(?= \||$)", line)
    if total_first and total_first.group(2):
        return re.findall(r" ([^ |=]+?)_\d+\.\d{3}s", total_first.group(2)) + [total_first.group(1)]
    return re.findall(r"(?:^|\| )([^|=]+?) \d+\.\d{3}s", line)


class FakeDetector:
    def __init__(self):
        self.boxes = [(LEFT, 0.75, "food"), (RIGHT, 0.5, "dish")]
        self.calls = []
        self.on_detect = lambda: None
        self.error = None
        self.done = threading.Event()  # 最近一次检测跑完了

    def detect(self, image, text_range=None, box_threshold=None, text_threshold=None):
        self.done.clear()
        try:
            self.calls.append({
                "shape": image.shape, "dtype": image.dtype, "text_range": text_range,
                "box_threshold": box_threshold, "text_threshold": text_threshold,
            })
            self.on_detect()
            if self.error is not None:
                raise self.error
            boxes = np.asarray([box for box, _, _ in self.boxes], dtype=np.float32).reshape(-1, 4)
            scores = np.asarray([score for _, score, _ in self.boxes], dtype=np.float32)
            return boxes, scores, [label for _, _, label in self.boxes]
        finally:
            self.done.set()


class FakeClassifier:
    """按裁剪图的尺寸（高, 宽）回答最相近的菜品。"""

    def __init__(self):
        self.matches = {LEFT_CROP: RecipeMatch("荷塘小炒", 0.97), RIGHT_CROP: RecipeMatch("清蒸鲈鱼", 0.96)}
        self.calls = []  # 每次调用传进来的图：（形状, dtype）
        self.on_classify = lambda: None
        self.error = None

    def classify(self, images):
        self.calls.append([(image.shape, image.dtype) for image in images])
        self.on_classify()
        if self.error is not None:
            raise self.error
        return [self.matches.get(image.shape[:2], RecipeMatch("没见过的菜", 0.2)) for image in images]


class FakeEmbedding:
    def __init__(self):
        self.sizes = []
        self.on_embed = lambda: None

    def embed_bytes(self, image_bytes):
        image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("cannot decode")
        self.sizes.append(image.shape[:2])
        self.on_embed()
        return [1.0, 0.0, 0.0, 0.0]


class FakeVectors:
    def __init__(self):
        self.hits = []  # 整图没有结果：答案要等识别
        self.on_search = lambda: None

    def search(self, vector, *, top_k, score_threshold, scene):
        self.on_search()
        return list(self.hits)


class FakeMetadata:
    def get_scene_by_code(self, code):
        return {"id": 1, "code": code, "outputs_heating": code == "oven", "is_active": True}

    def list_scene_codes(self, active_only=True):
        return ["general", "oven"]

    def get_active_by_ids(self, ids, scene=None):
        return {
            i: {"id": i, "category_id": None, "original_url": "https://cdn/o.jpg", "tags": ["牛排"], "food_state": "raw"}
            for i in ids
        }

    def map_categories_to_scene_labels(self, scene_id, category_ids):
        return {}


class RecipeDetectSettingsTests(unittest.TestCase):
    def test_without_the_section_the_step_is_off(self):
        self.assertEqual(RecipeDetectSettings.from_config({}), RecipeDetectSettings())
        self.assertEqual(RecipeDetectSettings(), RecipeDetectSettings(enabled=False, threshold=0.95))

    def test_yaml_scalars_are_coerced_and_the_model_keys_are_not_its_business(self):
        settings = RecipeDetectSettings.from_config({"RECIPE_DETECT_CONFIG": {
            "enabled": "true", "threshold": "0.9", "model_path": "x.onnx", "labels_path": "x.pkl", "use_gpu": True,
        }})
        self.assertEqual(settings, RecipeDetectSettings(enabled=True, threshold=0.9))

    def test_the_threshold_is_only_checked_when_it_is_on(self):
        self.assertFalse(RecipeDetectSettings.from_config({"RECIPE_DETECT_CONFIG": {"enabled": False, "threshold": 7}}).enabled)
        for section, key in (
            ({"enabled": True, "threshold": 1.5}, "RECIPE_DETECT_CONFIG.threshold"),
            ({"enabled": True, "threshold": -0.1}, "RECIPE_DETECT_CONFIG.threshold"),
            ({"enabled": True, "threshold": "nan"}, "RECIPE_DETECT_CONFIG.threshold"),
            ({"enabled": True, "threshold": "high"}, "RECIPE_DETECT_CONFIG.threshold"),
            ({"enabled": "maybe"}, "RECIPE_DETECT_CONFIG.enabled"),
        ):
            with self.subTest(section=section), self.assertRaises(ValueError) as raised:
                RecipeDetectSettings.from_config({"RECIPE_DETECT_CONFIG": section})
            self.assertIn(key, str(raised.exception))

    def test_the_shipped_config_has_the_section(self):
        path = Path(__file__).resolve().parents[1] / "config" / "param" / "config.yaml"
        section = yaml.safe_load(path.read_text(encoding="utf-8"))["RECIPE_DETECT_CONFIG"]
        self.assertEqual(RecipeDetectSettings.from_config({"RECIPE_DETECT_CONFIG": section}), RecipeDetectSettings(True, 0.95))
        self.assertEqual(
            (section["model_path"], section["labels_path"]),
            ("config/model/recipe/robam_model.onnx", "config/model/recipe/robam_base_featrures_labels.pkl"),
        )
        self.assertNotIn("url", section)


class DishNameTests(unittest.TestCase):
    def test_names_above_the_threshold_once_each_highest_first(self):
        matches = [
            RecipeMatch("红烧肉", 0.96), RecipeMatch("清蒸鲈鱼", 0.99), RecipeMatch("红烧肉", 0.98),
            RecipeMatch("凉拌猪耳", 0.95), RecipeMatch("荷塘小炒", 0.4), RecipeMatch("", 0.999),
        ]
        # 红烧肉按它最高的 0.98；0.95 不高于阈值；空菜名不算
        self.assertEqual(dish_names(matches, 0.95), ["清蒸鲈鱼", "红烧肉"])
        self.assertEqual(dish_names(matches, 0.0), ["清蒸鲈鱼", "红烧肉", "凉拌猪耳", "荷塘小炒"])
        self.assertEqual(dish_names(matches, 1.0), [])
        self.assertEqual(dish_names([], 0.5), [])
        # 相似度相同时按框的顺序
        self.assertEqual(dish_names([RecipeMatch("b", 0.97), RecipeMatch("a", 0.97)], 0.9), ["b", "a"])


class BusinessSearchRecipeTests(unittest.TestCase):
    """开启菜谱识别、关闭区域搜索时的 POST /api/search/auto/*。"""

    def setUp(self):
        self.embedding = FakeEmbedding()
        self.vectors = FakeVectors()
        self.detector = FakeDetector()
        self.classifier = FakeClassifier()
        model = ActiveModel(id=1, name="m", version="v", collection="c", dim=4, distance="cosine")
        search = SearchImageUseCase(self.embedding, self.vectors, FakeMetadata(), model=model)
        # 两个阶段都是假的 Global 搜图：上游搜索和相似度过滤
        self.food = FoodService(FOOD_CONFIG, Mock())
        self.food.searchTool = Mock()
        self.food.searchTool.detect.return_value = [dict(GLOBAL_CARD)]
        self.food.searchFilter = Mock()
        self.food.searchFilter.detect.return_value = [dict(GLOBAL_CARD)]
        self.runtime = ApplicationState(
            config={"AUTO_SEARCH_REGION_CONFIG": dict(REGION_CONFIG), "RECIPE_DETECT_CONFIG": dict(RECIPE_CONFIG)},
            config_info={},
            http_client=Mock(),
            settings=SimpleNamespace(predict=SimpleNamespace(max_image_bytes=1 << 20, http_timeout=3.0)),
            retrieval=SimpleNamespace(search_uc=search),
            food_service=self.food,
            detector=self.detector,
            recipe_classifier=self.classifier,
        )
        self.app = FastAPI()
        register_exception_handlers(self.app)
        self.app.add_middleware(TraceIdMiddleware)
        self.app.include_router(auto_search.router)
        state = patch.object(auto_search, "get_application_state", return_value=self.runtime)
        state.start()
        self.addCleanup(state.stop)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        fetch = patch("app.clients.image_fetch.public_image.fetch_image", return_value=(QUERY, "image/png"))
        fetch.start()
        self.addCleanup(fetch.stop)

    def post(self, path, *, status=200, **request):
        """发送 POST；返回（响应体, 这次请求的日志记录）。"""
        with self.assertLogs(level="INFO") as logs:
            response = self.client.post(path, **request)
        self.assertEqual(response.status_code, status, response.text)
        return response.json(), logs.records

    def by_url(self, **fields):
        return self.post("/api/search/auto/url", json={"image_url": "https://x.example/a.png", **fields})

    def by_file(self, data=QUERY, **fields):
        return self.post("/api/search/auto/file", files={"file": ("q.png", data, "image/png")}, data=fields)

    def timing_line(self, records):
        (record,) = [record for record in records if record.name == "autosearch" and "总耗时" in record.getMessage()]
        return record

    def recipe_logs(self, records):
        """服务自己关于识别的日志行（以「菜谱识别」开头）：每行一个（级别, 文本）。"""
        return [
            (record.levelno, record.getMessage()) for record in records
            if record.name == "svc.auto_search" and record.getMessage().startswith("菜谱识别")
        ]

    def service_line(self, records, title):
        """服务写的、以 title 开头的那一行（只有一行）：它的文本。"""
        (record,) = [r for r in records if r.name == "svc.auto_search" and r.getMessage().startswith(title)]
        return record.getMessage()

    # -- 合并后的答案 -----------------------------------------------------------------

    def test_the_dish_names_come_from_the_boxes_of_the_detection(self):
        with patch.object(region_search, "encode", wraps=region_search.encode) as encode:
            body, _ = self.by_url(trace_id="t-9")
        self.assertEqual(body["items"], [RECIPE_CARD])  # 按相似度：荷塘小炒 0.97、清蒸鲈鱼 0.96
        self.assertEqual(list(body["items"][0]), CARD_KEYS)
        self.assertEqual(body["fallback"], {"status": "not_needed"})
        self.assertNotIn("message", body)
        # GroundingDINO 拿到了解码后的查询图，以及 AUTO_SEARCH_REGION_CONFIG 的参数（区域搜索关着也一样）
        self.assertEqual(self.detector.calls, [{
            "shape": (HEIGHT, WIDTH, 3), "dtype": np.uint8, "text_range": "food . dish",
            "box_threshold": 0.3, "text_threshold": 0.25,
        }])
        # 模型一次拿到所有框的裁剪图（BGR），按检测置信度从高到低
        self.assertEqual(self.classifier.calls, [[((100, 100, 3), np.uint8), ((50, 50, 3), np.uint8)]])
        self.assertEqual(self.embedding.sizes, [(HEIGHT, WIDTH)])  # 只有整图：没有区域搜索
        encode.assert_not_called()  # 没有区域搜索，裁剪图不编码成 JPEG

    def test_fewer_names_than_boxes_when_some_are_below_the_threshold_or_the_same_dish(self):
        self.classifier.matches[RIGHT_CROP] = RecipeMatch("清蒸鲈鱼", 0.90)
        body, records = self.by_file()
        self.assertEqual(body["items"], [{**RECIPE_CARD, "tags": "荷塘小炒"}])
        ((_, text),) = self.recipe_logs(records)
        self.assertIn(" | 检测框=2 | 结果=[\"荷塘小炒\"] | 阈值=0.95 | 各框最相近=荷塘小炒 0.9700、清蒸鲈鱼 0.9000 | ", text)

        self.classifier.matches[RIGHT_CROP] = RecipeMatch("荷塘小炒", 0.99)  # 两个框是同一道菜
        body, _ = self.by_file()
        self.assertEqual(body["items"], [{**RECIPE_CARD, "tags": "荷塘小炒"}])

    def test_the_threshold_is_read_on_every_request_and_must_be_exceeded(self):
        for threshold, tags in ((0.95, "荷塘小炒、清蒸鲈鱼"), (0.965, "荷塘小炒"), (0.97, None)):
            with self.subTest(threshold=threshold):
                self.runtime.config["RECIPE_DETECT_CONFIG"] = {"enabled": True, "threshold": threshold}  # 和 Nacos 重读时一样
                body, records = self.by_url()
                self.assertEqual(body["items"], [{**RECIPE_CARD, "tags": tags}] if tags else [])
                ((_, text),) = self.recipe_logs(records)
                self.assertIn(f" | 阈值={threshold:g} | ", text)
        self.assertEqual(body["message"], "自建库没有达到阈值的结果，菜谱识别没有识别到菜名，未开启 Global 兜底")
        self.assertIs(self.runtime.recipe_classifier, self.classifier)

    def test_without_boxes_there_are_no_dish_names(self):
        self.detector.boxes = [(TINY, 0.9, "crumb")]
        body, records = self.by_url()
        self.assertEqual((body["items"], body["fallback"]), ([], {"status": "disabled"}))
        self.assertEqual(body["message"], "自建库没有达到阈值的结果，菜谱识别没有识别到菜名，未开启 Global 兜底")
        self.assertEqual(self.classifier.calls, [])  # 不会拿整张图去识别
        ((level, text),) = self.recipe_logs(records)
        self.assertEqual(level, logging.INFO)
        self.assertRegex(text, r"^菜谱识别完成 \| .* \| 检测框=0 \| 结果=\[\] \| 阈值=0\.95 \| trace_id=")
        self.assertEqual(steps(text), ["排队", "图像解码", "目标检测", "区域裁剪", "耗时"])

    def test_whole_image_hits_are_the_answer_without_the_dish_names(self):
        self.vectors.hits = [{"id": ROW_ID, "score": 0.9}]
        body, records = self.by_url(trace_id="t-9", global_fallback=True)
        self.assertEqual(body["fallback"], {"status": "not_needed"})
        (hit,) = body["items"]
        self.assertEqual((hit["source"], hit["tags"], hit["similarity"]), ("self", "牛排", 0.9))
        self.assertNotIn("region", hit["details"])
        self.assertEqual(list(hit), CARD_KEYS)
        self.food.searchTool.detect.assert_not_called()
        # 识别不属于答案：没有它那一行，请求那一行也不等它
        self.assertEqual(self.recipe_logs(records), [])
        self.assertEqual(steps(self.timing_line(records).getMessage()), ["接收请求", "图像下载", "整图搜索", "总耗时"])
        self.assertIn(" | 结果=1", self.service_line(records, "整图搜索完成"))

    def test_whole_image_hits_do_not_wait_for_the_detection(self):
        self.vectors.hits = [{"id": ROW_ID, "score": 0.9}]
        detecting, release = threading.Event(), threading.Event()

        def detect():  # 在检测的工作线程里：整图有结果时还在跑
            detecting.set()
            release.wait(5)

        self.detector.on_detect = detect
        self.embedding.on_embed = lambda: detecting.wait(2)
        # 整个代码块用一个事件循环：被丢弃的检测仍在上面运行
        with TestClient(self.app) as client, self.assertLogs(level="INFO") as logs:
            started = time.monotonic()
            response = client.post("/api/search/auto/file", files={"file": ("q.png", QUERY, "image/png")})
            took = time.monotonic() - started
            release.set()
            self.assertTrue(self.detector.done.wait(5))
            deadline = time.monotonic() + 5
            while auto_search_service._LEFT_RUNNING and time.monotonic() < deadline:
                time.sleep(0.01)
            time.sleep(0.05)  # 让事件循环处理检测的结束
            left_running = len(auto_search_service._LEFT_RUNNING)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([item["source"] for item in response.json()["items"]], ["self"])
        self.assertLess(took, 4)  # 返回时 GroundingDINO 还被占着
        self.assertEqual(left_running, 0)
        self.assertEqual(self.classifier.calls, [])  # 没有为没人要的结果推理
        self.assertEqual(self.recipe_logs(logs.records), [])
        self.assertRegex(self.service_line(logs.records, "整图搜索完成"), r" \| 结果=1 \| 已取消=菜谱识别 \| trace_id=")

    def test_the_envelope_leaves_the_source_and_the_heating_flag_to_the_cards(self):
        self.vectors.hits = [{"id": ROW_ID, "score": 0.9}]
        body, _ = self.by_url(trace_id="t-9")
        self.assertEqual(list(body), ["ok", "trace_id", "scene", "items", "fallback"])
        self.assertEqual((body["ok"], body["trace_id"], body["scene"]), (True, "t-9", "general"))
        (hit,) = body["items"]
        self.assertIs(hit["details"]["outputs_heating"], False)
        self.assertFalse({"food_state", "heating_group"} & set(hit["details"]))

        self.vectors.hits = []
        body, _ = self.by_url(scene="oven")
        self.assertEqual(body["items"], [RECIPE_CARD])  # 只有自建库结果卡带加热标志

        schemas = self.app.openapi()["components"]["schemas"]
        self.assertEqual(
            list(schemas["AutoSearchResponse"]["properties"]), ["ok", "trace_id", "scene", "items", "fallback", "message"]
        )
        self.assertEqual(list(schemas["AutoSearchHit"]["properties"]), CARD_KEYS)
        self.assertEqual(schemas["AutoSearchHit"]["required"], ["source"])

    def test_dish_names_alone_are_an_answer_and_without_them_the_fallback_runs(self):
        body, _ = self.by_url(global_fallback=True)
        self.assertEqual((body["items"], body["fallback"]), ([RECIPE_CARD], {"status": "not_needed"}))
        self.food.searchTool.detect.assert_not_called()

        self.classifier.matches = {}  # 每个框都不像任何一道菜
        body, records = self.by_url(global_fallback=True)
        self.assertEqual(body["fallback"], {"status": "hit"})
        self.assertEqual(body["items"], [{"source": "global", **GLOBAL_CARD}])
        self.assertEqual(
            steps(self.timing_line(records).getMessage()),
            ["接收请求", "图像下载", "整图搜索", "等待菜谱识别", "Global兜底", "总耗时"],
        )
        lines = [r.getMessage() for r in records if r.name == "svc.auto_search"]
        self.assertEqual([line.split(" | ")[0] for line in lines], ["整图搜索完成", "菜谱识别完成", "Global兜底完成"])

        self.food.searchTool.detect.return_value = []
        self.food.searchFilter.detect.return_value = []
        body, _ = self.by_url(global_fallback=True)
        self.assertEqual((body["items"], body["fallback"]), ([], {"status": "empty"}))
        self.assertEqual(body["message"], "自建库没有达到阈值的结果，菜谱识别没有识别到菜名，Global 也没有找到结果")

    def test_without_a_threshold_the_dish_names_still_come_from_the_boxes(self):
        body, _ = self.by_url(score_threshold=0)  # 库里没有图：整图也没有结果
        self.assertEqual(body["items"], [RECIPE_CARD])
        self.assertEqual(len(self.detector.calls), 1)

    # -- 日志里的耗时和结果 -----------------------------------------------------------

    def test_the_time_and_the_result_are_logged(self):
        body, records = self.by_url(trace_id="t-9")
        self.assertEqual(body["items"], [RECIPE_CARD])
        ((level, text),) = self.recipe_logs(records)
        self.assertEqual(level, logging.INFO)
        self.assertRegex(
            text,
            r'^菜谱识别完成 \| .* \| 检测框=2 \| 结果=\["荷塘小炒", "清蒸鲈鱼"\] \| 阈值=0\.95'
            r" \| 各框最相近=荷塘小炒 0\.9700、清蒸鲈鱼 0\.9600 \| trace_id=t-9$",
        )
        self.assertEqual(steps(text), ["排队", "图像解码", "目标检测", "区域裁剪", "菜谱推理", "耗时"])
        # ……请求那一行在整图搜索之后等它
        line = self.timing_line(records)
        self.assertEqual(steps(line.getMessage()), ["接收请求", "图像下载", "整图搜索", "等待菜谱识别", "总耗时"])
        self.assertEqual(line.levelno, logging.INFO)
        # 整图那一行在前，识别那一行在后
        lines = [r.getMessage() for r in records if r.name == "svc.auto_search"]
        self.assertEqual([line.split(" | ")[0] for line in lines], ["整图搜索完成", "菜谱识别完成"])

    # -- 失败或关闭的识别 ---------------------------------------------------------------

    def test_a_failed_recognition_is_logged_and_does_not_fail_the_search(self):
        self.classifier.error = RuntimeError("CUDA error: an illegal memory access")
        body, records = self.by_url(trace_id="t-9")
        self.assertEqual((body["items"], body["fallback"]), ([], {"status": "disabled"}))
        self.assertEqual(body["message"], "自建库没有达到阈值的结果，菜谱识别失败，未开启 Global 兜底")
        ((level, text),) = self.recipe_logs(records)
        self.assertEqual(level, logging.WARNING)
        self.assertRegex(
            text, r"^菜谱识别失败 \| .* \| 检测框=2 \| 错误=RuntimeError: CUDA error: an illegal memory access \| trace_id=t-9$"
        )
        self.assertEqual(steps(text), ["排队", "图像解码", "目标检测", "区域裁剪", "菜谱推理失败", "耗时"])
        line = self.timing_line(records)  # 请求本身成功了：它只是等了识别
        self.assertEqual(steps(line.getMessage()), ["接收请求", "图像下载", "整图搜索", "等待菜谱识别", "总耗时"])
        self.assertEqual(line.levelno, logging.INFO)

        body, _ = self.by_url(global_fallback=True)  # ……兜底照样执行
        self.assertEqual(([item["source"] for item in body["items"]], body["fallback"]), (["global"], {"status": "hit"}))

    def test_a_failed_detection_fails_the_recognition_but_not_the_search(self):
        self.detector.error = RuntimeError("gpu fell over")
        body, records = self.by_url()
        self.assertEqual((body["items"], body["fallback"]), ([], {"status": "disabled"}))
        self.assertEqual(body["message"], "自建库没有达到阈值的结果，菜谱识别失败，未开启 Global 兜底")
        ((level, text),) = self.recipe_logs(records)
        self.assertEqual(level, logging.WARNING)
        self.assertRegex(text, r"^菜谱识别失败 \| .* \| 错误=RuntimeError: gpu fell over \| trace_id=")
        self.assertNotIn("检测框=", text)
        self.assertEqual(steps(text), ["排队", "图像解码", "目标检测失败", "耗时"])
        self.assertEqual(self.classifier.calls, [])

    def test_a_model_that_is_not_ready_skips_the_step(self):
        self.runtime.recipe_classifier = None
        self.runtime.recipe_classifier_error = "FileNotFoundError: 菜谱识别模型文件不存在: robam_model.onnx"
        body, records = self.by_url()
        self.assertEqual(body["items"], [])
        self.assertEqual(body["message"], "自建库没有达到阈值的结果，菜谱识别没有运行，未开启 Global 兜底")
        self.assertEqual(self.detector.calls, [])  # 区域搜索也关着：不用检测
        ((level, text),) = self.recipe_logs(records)
        self.assertEqual(level, logging.INFO)
        self.assertRegex(
            text, r"^菜谱识别已跳过：菜谱识别模型未就绪：FileNotFoundError: 菜谱识别模型文件不存在: robam_model\.onnx \| trace_id="
        )
        self.assertEqual(steps(self.timing_line(records).getMessage()), ["接收请求", "图像下载", "整图搜索", "总耗时"])

        self.runtime.recipe_classifier = self.classifier
        self.runtime.detector = None
        self.runtime.detector_error = "RuntimeError: no CUDA device"
        body, records = self.by_url()
        self.assertEqual(body["message"], "自建库没有达到阈值的结果，菜谱识别没有运行，未开启 Global 兜底")
        ((level, text),) = self.recipe_logs(records)
        self.assertEqual(level, logging.INFO)
        self.assertIn("菜谱识别已跳过：GroundingDINO 未就绪：RuntimeError: no CUDA device", text)
        self.assertEqual(self.classifier.calls, [])

    def test_the_switch_turns_the_step_off(self):
        for config in ({"RECIPE_DETECT_CONFIG": {**RECIPE_CONFIG, "enabled": False}}, {}):
            with self.subTest(config=config):
                self.runtime.config = {"AUTO_SEARCH_REGION_CONFIG": dict(REGION_CONFIG), **config}
                body, records = self.by_url()
                self.assertEqual(body["items"], [])
                self.assertEqual(body["message"], "自建库没有达到阈值的结果，未开启 Global 兜底")  # 不提识别
                self.assertEqual((self.detector.calls, self.classifier.calls), ([], []))
                self.assertEqual(self.recipe_logs(records), [])
                self.assertEqual(
                    steps(self.timing_line(records).getMessage()), ["接收请求", "图像下载", "整图搜索", "总耗时"]
                )

    def test_a_section_that_cannot_be_used_skips_the_step_with_a_warning(self):
        for config, said in (
            ({"RECIPE_DETECT_CONFIG": {"enabled": True, "threshold": 1.5}},
             "菜谱识别已跳过：RECIPE_DETECT_CONFIG 配置有误 | RECIPE_DETECT_CONFIG.threshold"),
            # 区域搜索关着，它的检测参数还是要检查：菜谱识别要用
            ({"AUTO_SEARCH_REGION_CONFIG": {**REGION_CONFIG, "box_threshold": 2}},
             "菜谱识别已跳过：AUTO_SEARCH_REGION_CONFIG 配置有误 | AUTO_SEARCH_REGION_CONFIG.box_threshold"),
        ):
            with self.subTest(said=said):
                self.runtime.config = {
                    "AUTO_SEARCH_REGION_CONFIG": dict(REGION_CONFIG), "RECIPE_DETECT_CONFIG": dict(RECIPE_CONFIG), **config,
                }
                body, records = self.by_url()
                self.assertEqual(body["items"], [])
                self.assertEqual(self.detector.calls, [])
                ((level, text),) = self.recipe_logs(records)
                self.assertEqual(level, logging.WARNING)
                self.assertIn(said, text)

    # -- 同时进行 ---------------------------------------------------------------------

    def test_the_detection_runs_while_the_whole_image_search_does(self):
        searching, detecting = threading.Event(), threading.Event()
        seen = {}

        def search():  # 在整图搜索的工作线程里
            searching.set()
            seen["detection started during the search"] = detecting.wait(2)

        def detect():  # 在检测的工作线程里
            detecting.set()
            seen["search started during the detection"] = searching.wait(2)

        self.vectors.on_search = search
        self.detector.on_detect = detect
        body, _ = self.by_url()
        self.assertEqual(seen, {"detection started during the search": True, "search started during the detection": True})
        self.assertEqual(body["items"], [RECIPE_CARD])

    def test_a_failed_self_built_search_fails_the_request_and_drops_the_detection(self):
        detecting, release = threading.Event(), threading.Event()

        def detect():
            detecting.set()
            release.wait(5)

        def embed():  # 整图：在检测进行期间失败
            detecting.wait(2)
            raise err.search_bad_image("cannot read it")

        self.detector.on_detect = detect
        self.embedding.on_embed = embed
        with TestClient(self.app) as client, self.assertLogs(level="INFO") as logs:
            started = time.monotonic()
            response = client.post("/api/search/auto/file", files={"file": ("q.png", QUERY, "image/png")})
            took = time.monotonic() - started
            release.set()
            self.assertTrue(self.detector.done.wait(5))
            time.sleep(0.05)
        self.assertEqual((response.status_code, response.json()["error"]), (422, "SEARCH_BAD_IMAGE"))
        self.assertLess(took, 4)  # 它没有等检测
        self.assertEqual(self.classifier.calls, [])
        self.assertEqual(self.recipe_logs(logs.records), [])  # 没什么要报告的：没人读它的结果
        line = self.timing_line(logs.records)
        self.assertEqual(steps(line.getMessage()), ["接收请求", "读取上传", "整图搜索失败", "总耗时"])
        self.assertEqual(line.levelno, logging.WARNING)


if __name__ == "__main__":
    unittest.main()
