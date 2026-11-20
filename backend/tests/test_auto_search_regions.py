"""业务搜图的区域搜索（AUTO_SEARCH_REGION_CONFIG，app.api.services.region_search）。

它和整图搜索同时启动。整图没有结果时，裁剪 GroundingDINO 的检测框，每个框都用请求的
scene、top_k 和 score_threshold 搜索；所有框的结果合并（每张已存图片一条，取最高相似度），
从高到低取 top_k 条，每条在 details.region 里标明它的框。整图有结果时立即作为答案返回。

检测器和编码器都是假的：编码器按像素尺寸区分整图和裁剪图，向量库按向量回答。

    区域搜索完成 | 排队 … | 图像解码 … | 目标检测 … | 区域裁剪 … | 区域检索 … | 耗时 … | 检测框=2 | 结果=3 | 区域检索明细（各框累计）=embedding推理 …, vector搜索 …, 后处理 …
    业务搜图(图片链接) | 接收请求 … | 图像下载 … | 整图搜索 … | 等待区域搜索 … | 总耗时 … | 参数=…
"""

import asyncio
import gc
import logging
import re
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pathlib import Path

import cv2
import httpx
import numpy as np
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.services import auto_search as auto_search_service
from app.api.services.region_search import MIN_REGION_SIDE, Region, merge, regions_of
from app.api.v1 import auto_search
from app.bootstrap.state import ApplicationState
from app.core import errors as err
from app.core.config.settings import AutoSearchRegionSettings
from app.core.exceptions import register_exception_handlers
from app.core.middleware import TraceIdMiddleware
from app.services.predict.food_service import FoodService
from app.services.retrieval.active_model import ActiveModel
from app.services.retrieval.search_image import SearchImageUseCase

WIDTH, HEIGHT = 200, 100
ROW_A = "aaaaaaaa-0000-0000-0000-000000000001"
ROW_B = "bbbbbbbb-0000-0000-0000-000000000002"
ROW_C = "cccccccc-0000-0000-0000-000000000003"
ROW_D = "dddddddd-0000-0000-0000-000000000004"

# GroundingDINO 的检测框（归一化的 cx, cy, w, h），以及它们在查询图上的像素位置
LEFT = (0.25, 0.5, 0.5, 1.0)  # x 0..100, y 0..100
RIGHT = (0.875, 0.5, 0.25, 0.5)  # x 150..200, y 25..75
TINY = (0.5, 0.5, 0.0625, 0.0625)  # 大约 13 x 6 像素：丢掉
LEFT_REGION = {"label": "food", "score": 0.75, "box": [0, 0, 100, 100]}
RIGHT_REGION = {"label": "food", "score": 0.5, "box": [150, 25, 200, 75]}

# 编码器对这个尺寸（高, 宽）的图片给出的向量：整图、一张裁剪图
WHOLE, LEFT_CROP, RIGHT_CROP = (HEIGHT, WIDTH), (100, 100), (50, 50)
VECTORS = {WHOLE: [1.0, 0.0, 0.0, 0.0], LEFT_CROP: [0.0, 1.0, 0.0, 0.0], RIGHT_CROP: [0.0, 0.0, 1.0, 0.0]}

REGION_CONFIG = {"enabled": True, "caption": "food", "box_threshold": 0.4, "text_threshold": 0.4, "max_regions": 5}
RECIPE_URL = "http://recipe.test:8866/detect_recipe_onnx"
FOOD_CONFIG = {
    "FLAB_CONFIG": {
        "API_URL": "https://search.invalid/global_search", "API_KEY": "test-only-key", "DOC_COUNT": 5,
        "MAX_SNIPPET_LENGTH": 300, "THRESHOLD": 0.8, "EnableWaiting": False, "Timeout": 1,
    },
    "GLOBAL_FILTER": {"switch": True, "threshold": 0.5},
}
GLOBAL_CARD = {"tags": "番茄", "description": "", "original_url": "https://img.example/a.jpg", "similarity": 0.8, "details": {}}


def encoded(width=WIDTH, height=HEIGHT, ext=".png"):
    ok, data = cv2.imencode(ext, np.full((height, width, 3), 127, dtype=np.uint8))
    assert ok
    return data.tobytes()


QUERY = encoded()


def steps(line):
    """The step names of a timing line in order, 总耗时 (or 耗时) last. Only the fields before the
    other details: the box breakdown of the region line is a detail (see breakdown).
    """
    return re.findall(r"(?:^|\| )([^|=]+?) \d+\.\d{3}s", line)


def seconds(line):
    """{step: seconds} of a timing line (the same fields as steps)."""
    return {step: float(value) for step, value in re.findall(r"(?:^|\| )([^|=]+?) (\d+\.\d{3})s", line)}


def breakdown(line):
    """The steps of 区域检索明细（各框累计）in the region line, in order; [] without it."""
    found = re.search(r"\| 区域检索明细（各框累计）=([^|]+?)(?: \||$)", line)
    return re.findall(r"(?:^|, )(.+?) \d+\.\d{3}s", found.group(1)) if found else []


class FakeDetector:
    def __init__(self):
        self.boxes = [(LEFT, 0.75, "food"), (RIGHT, 0.5, "food")]
        self.calls = []
        self.on_detect = lambda: None
        self.error = None

    def detect(self, image, text_range=None, box_threshold=None, text_threshold=None):
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


class FakeEmbedding:
    def __init__(self):
        self.sizes = []  # 每张计算过 embedding 的图片的（高, 宽），按顺序
        self.on_embed = lambda size: None

    def embed_bytes(self, image_bytes):
        image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("cannot decode")
        size = image.shape[:2]
        self.sizes.append(size)
        self.on_embed(size)
        return VECTORS.get(size, [0.0, 0.0, 0.0, 1.0])


class FakeVectors:
    def __init__(self):
        self.hits = {
            WHOLE: [],
            LEFT_CROP: [{"id": ROW_A, "score": 0.8}, {"id": ROW_B, "score": 0.6}],
            RIGHT_CROP: [{"id": ROW_B, "score": 0.7}, {"id": ROW_C, "score": 0.65}, {"id": ROW_D, "score": 0.5}],
        }
        self.on_search = lambda: None

    def search(self, vector, *, top_k, score_threshold, scene):
        self.on_search()
        size = {tuple(known): size for size, known in VECTORS.items()}.get(tuple(vector))
        return list(self.hits.get(size, []))[:top_k]


class FakeMetadata:
    def get_scene_by_code(self, code):
        return {"id": 1, "code": code, "outputs_heating": code == "oven", "is_active": True}

    def list_scene_codes(self, active_only=True):
        return ["general", "oven"]

    def get_active_by_ids(self, ids, scene=None):
        return {
            i: {"id": i, "category_id": None, "original_url": f"https://cdn/{i}.jpg", "tags": ["牛排"], "food_state": "raw"}
            for i in ids
        }

    def map_categories_to_scene_labels(self, scene_id, category_ids):
        return {}


class AutoSearchRegionSettingsTests(unittest.TestCase):
    def test_without_the_section_the_step_is_off(self):
        self.assertEqual(AutoSearchRegionSettings.from_config({}), AutoSearchRegionSettings())
        self.assertEqual(
            AutoSearchRegionSettings(),
            AutoSearchRegionSettings(enabled=False, caption="food", box_threshold=0.4, text_threshold=0.4, max_regions=5),
        )

    def test_yaml_scalars_are_coerced(self):
        settings = AutoSearchRegionSettings.from_config({"AUTO_SEARCH_REGION_CONFIG": {
            "enabled": "true", "caption": " food . dish ", "box_threshold": "0.3", "text_threshold": 0.25, "max_regions": "3",
        }})
        self.assertEqual(
            settings,
            AutoSearchRegionSettings(enabled=True, caption="food . dish", box_threshold=0.3, text_threshold=0.25, max_regions=3),
        )

    def test_the_values_are_only_checked_when_it_is_on(self):
        off = AutoSearchRegionSettings.from_config({"AUTO_SEARCH_REGION_CONFIG": {"enabled": False, "box_threshold": 7}})
        self.assertFalse(off.enabled)
        for section, key in (
            ({"enabled": True, "box_threshold": 1.5}, "box_threshold"),
            ({"enabled": True, "text_threshold": -0.1}, "text_threshold"),
            ({"enabled": True, "text_threshold": "nan"}, "text_threshold"),
            ({"enabled": True, "box_threshold": "high"}, "box_threshold"),
            ({"enabled": True, "max_regions": 0}, "max_regions"),
            ({"enabled": True, "max_regions": 21}, "max_regions"),
            ({"enabled": "maybe"}, "enabled"),
        ):
            with self.subTest(section=section), self.assertRaises(ValueError) as raised:
                AutoSearchRegionSettings.from_config({"AUTO_SEARCH_REGION_CONFIG": section})
            self.assertIn(f"AUTO_SEARCH_REGION_CONFIG.{key}", str(raised.exception))

    def test_the_shipped_config_has_the_section(self):
        path = Path(__file__).resolve().parents[1] / "config" / "param" / "config.yaml"
        settings = AutoSearchRegionSettings.from_config(yaml.safe_load(path.read_text(encoding="utf-8")))
        self.assertEqual(
            (settings.enabled, settings.caption, settings.box_threshold, settings.text_threshold), (True, "food", 0.4, 0.4)
        )


class RegionsAndMergeTests(unittest.TestCase):
    def test_boxes_become_pixels_clipped_to_the_image_highest_score_first(self):
        found = regions_of(
            [LEFT, RIGHT, TINY, (0.9375, 0.5, 0.25, 0.5)],
            [0.5, 0.75, 0.9, 0.6],
            [" food ", "food", "crumb", "plate"],
            width=WIDTH, height=HEIGHT, limit=5,
        )
        self.assertEqual(found, [
            Region("food", 0.75, (150, 25, 200, 75)),
            Region("plate", 0.6, (162, 25, 200, 75)),  # x2 原本是 212.5：裁到图内
            Region("food", 0.5, (0, 0, 100, 100)),
        ])  # TINY 有一边小于 MIN_REGION_SIDE
        self.assertGreater(MIN_REGION_SIDE, 0.0625 * HEIGHT)
        self.assertEqual(
            regions_of([LEFT, RIGHT], [0.5, 0.75], ["a", "b"], width=WIDTH, height=HEIGHT, limit=1),
            [Region("b", 0.75, (150, 25, 200, 75))],
        )
        self.assertEqual(regions_of([], [], [], width=WIDTH, height=HEIGHT, limit=5), [])

    def test_merge_keeps_one_hit_per_image_its_best_highest_first(self):
        left, right = Region("food", 0.75, (0, 0, 100, 100)), Region("dish", 0.5, (150, 25, 200, 75))

        def hit(row, similarity):
            return {"tags": "", "description": "", "original_url": None, "similarity": similarity, "details": {"id": row}}

        entries = [
            {"ok": True, "items": [hit(ROW_A, 0.8), hit(ROW_B, 0.6)]},
            {"ok": True, "items": [hit(ROW_B, 0.7), hit(ROW_C, 0.65)]},
            {"ok": False, "error": "SEARCH_BAD_IMAGE"},
        ]
        merged = merge([left, right, left], entries, top_k=10)
        self.assertEqual(
            [(card["details"]["id"], card["similarity"], card["details"]["region"]["label"]) for card in merged],
            [(ROW_A, 0.8, "food"), (ROW_B, 0.7, "dish"), (ROW_C, 0.65, "dish")],
        )
        self.assertEqual(merged[0]["details"]["region"], {"label": "food", "score": 0.75, "box": [0, 0, 100, 100]})
        self.assertNotIn("region", entries[0]["items"][0]["details"])  # 条目保持原样
        self.assertEqual([card["details"]["id"] for card in merge([left, right], entries, top_k=2)], [ROW_A, ROW_B])


class BusinessSearchRegionTests(unittest.TestCase):
    """开启区域搜索、关闭菜谱识别时的 POST /api/search/auto/*。"""

    def setUp(self):
        self.embedding = FakeEmbedding()
        self.vectors = FakeVectors()
        self.detector = FakeDetector()
        model = ActiveModel(id=1, name="m", version="v", collection="c", dim=4, distance="cosine")
        search = SearchImageUseCase(self.embedding, self.vectors, FakeMetadata(), model=model)
        self.food = FoodService(FOOD_CONFIG, Mock())
        self.food.searchTool = Mock()
        self.food.searchTool.detect.return_value = [dict(GLOBAL_CARD)]
        self.food.searchFilter = Mock()
        self.food.searchFilter.detect.return_value = [dict(GLOBAL_CARD)]
        self.runtime = ApplicationState(
            config={"AUTO_SEARCH_REGION_CONFIG": dict(REGION_CONFIG)}, config_info={},
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(self.recipe_service)),
            settings=SimpleNamespace(predict=SimpleNamespace(max_image_bytes=1 << 20, http_timeout=3.0)),
            retrieval=SimpleNamespace(search_uc=search),
            food_service=self.food,
            detector=self.detector,
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

    async def recipe_service(self, request):
        return httpx.Response(200, json={"code": "200", "success": True, "message": "", "data": ["清蒸鲈鱼"]})

    def post(self, path, *, status=200, **request):
        with self.assertLogs(level="INFO") as logs:
            response = self.client.post(path, **request)
        self.assertEqual(response.status_code, status, response.text)
        return response.json(), logs.records

    def by_url(self, **fields):
        return self.post("/api/search/auto/url", json={"image_url": "https://x.example/a.png", **fields})

    def by_file(self, data=QUERY, *, status=200, **fields):
        return self.post("/api/search/auto/file", status=status, files={"file": ("q.png", data, "image/png")}, data=fields)

    def timing_steps(self, records):
        (record,) = [r for r in records if r.name == "autosearch" and "总耗时" in r.getMessage()]
        return steps(record.getMessage())

    def region_logs(self, records):
        """服务关于区域搜索的日志行（以「区域搜索」开头）：每行一个（级别, 文本）。"""
        return [
            (r.levelno, r.getMessage()) for r in records
            if r.name == "svc.auto_search" and r.getMessage().startswith("区域搜索")
        ]

    def service_line(self, records, title):
        """服务写的、以 title 开头的那一行（只有一行）：它的文本。"""
        (record,) = [r for r in records if r.name == "svc.auto_search" and r.getMessage().startswith(title)]
        return record.getMessage()

    def ids(self, body):
        return [item["details"].get("id") for item in body["items"]]

    # -- 整图没有结果：区域的结果 ----------------------------------------------------------

    def test_the_regions_hits_are_merged_when_the_whole_image_has_none(self):
        body, records = self.by_url(trace_id="t-9")
        self.assertEqual(body["fallback"], {"status": "not_needed"})
        self.assertNotIn("message", body)
        self.assertEqual(
            [(item["source"], item["details"]["id"], item["similarity"]) for item in body["items"]],
            [("self", ROW_A, 0.8), ("self", ROW_B, 0.7), ("self", ROW_C, 0.65)],  # B：取它最好的；D：低于 0.55
        )
        self.assertEqual(
            [item["details"]["region"] for item in body["items"]], [LEFT_REGION, RIGHT_REGION, RIGHT_REGION]
        )
        self.assertEqual(list(body["items"][0]), ["source", "tags", "description", "original_url", "similarity", "details"])
        self.assertIs(body["items"][0]["details"]["outputs_heating"], False)

        # GroundingDINO 拿到了解码后的查询图，以及配置的 caption 和阈值
        (call,) = self.detector.calls
        self.assertEqual(call, {
            "shape": (HEIGHT, WIDTH, 3), "dtype": np.uint8, "text_range": "food",
            "box_threshold": 0.4, "text_threshold": 0.4,
        })
        # 先是整图，然后是每个框的裁剪图
        self.assertEqual(self.embedding.sizes[0], WHOLE)
        self.assertEqual(sorted(self.embedding.sizes[1:]), sorted([LEFT_CROP, RIGHT_CROP]))

        self.assertEqual(self.timing_steps(records), ["接收请求", "图像下载", "整图搜索", "等待区域搜索", "总耗时"])
        ((level, text),) = self.region_logs(records)
        self.assertEqual(level, logging.INFO)
        self.assertRegex(text, r"^区域搜索完成 \| .* \| 耗时 \d+\.\d{3}s \| 检测框=2 \| 结果=3 \| 区域检索明细（各框累计）=.* \| trace_id=t-9$")
        self.assertEqual(steps(text), ["排队", "图像解码", "目标检测", "区域裁剪", "区域检索", "耗时"])
        # 两个框的检索，各步骤加在一起
        self.assertEqual(breakdown(text), ["embedding推理", "vector搜索", "后处理"])
        # 整图那一行在前，区域那一行在后
        lines = [r.getMessage() for r in records if r.name == "svc.auto_search"]
        self.assertEqual([line.split(" | ")[0] for line in lines], ["整图搜索完成", "区域搜索完成"])
        self.assertIn(" | 结果=0 | trace_id=t-9", lines[0])

    def test_top_k_bounds_every_box_and_the_merged_list(self):
        body, _ = self.by_url(top_k=1)
        self.assertEqual(self.ids(body), [ROW_A])  # LEFT 最好的（0.8）胜过 RIGHT 最好的（0.7）

    def test_the_heating_flag_follows_the_scene(self):
        body, _ = self.by_file(scene="oven")
        self.assertEqual(body["scene"], "oven")
        self.assertTrue(all(item["details"]["outputs_heating"] is True for item in body["items"]))

    def test_the_dish_names_follow_the_regions_hits_and_do_not_count_towards_top_k(self):
        self.runtime.config["RECIPE_DETECT_CONFIG"] = {"enabled": True, "url": RECIPE_URL, "timeout": 2}
        body, records = self.by_url(top_k=1)
        self.assertEqual([item["source"] for item in body["items"]], ["self", "recipe"])
        self.assertEqual(body["items"][1]["tags"], "清蒸鲈鱼")
        self.assertEqual(
            self.timing_steps(records), ["接收请求", "图像下载", "整图搜索", "等待区域搜索", "等待菜谱识别", "总耗时"]
        )
        lines = [r.getMessage() for r in records if r.name == "svc.auto_search"]
        self.assertEqual([line.split(" | ")[0] for line in lines], ["整图搜索完成", "区域搜索完成", "菜谱识别完成"])

    def test_max_regions_keeps_the_highest_scores(self):
        self.runtime.config["AUTO_SEARCH_REGION_CONFIG"]["max_regions"] = 1
        body, _ = self.by_url()
        self.assertEqual(self.embedding.sizes, [WHOLE, LEFT_CROP])
        self.assertEqual(self.ids(body), [ROW_A, ROW_B])

    # -- 整图有结果：立即返回 ------------------------------------------------------------

    def test_a_whole_image_hit_is_the_answer_without_waiting_for_the_regions(self):
        self.vectors.hits[WHOLE] = [{"id": ROW_D, "score": 0.9}]
        detecting, release = threading.Event(), threading.Event()
        seen = {}

        def detect():  # 在区域搜索的工作线程里
            detecting.set()
            release.wait(5)

        def embed(size):  # 整图：在识别进行期间开始
            if size == WHOLE:
                seen["detection started beside the whole-image search"] = detecting.wait(2)

        self.detector.on_detect = detect
        self.embedding.on_embed = embed
        # 整个代码块用一个事件循环：被丢弃的区域搜索仍在上面运行
        with TestClient(self.app) as client, self.assertLogs(level="INFO") as logs:
            started = time.monotonic()
            response = client.post("/api/search/auto/file", files={"file": ("q.png", QUERY, "image/png")})
            took = time.monotonic() - started
            release.set()
            deadline = time.monotonic() + 5
            while auto_search_service._LEFT_RUNNING and time.monotonic() < deadline:
                time.sleep(0.01)
            left_running = len(auto_search_service._LEFT_RUNNING)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(seen, {"detection started beside the whole-image search": True})
        self.assertLess(took, 4)  # 返回时 GroundingDINO 还被占着
        self.assertEqual([(item["details"]["id"], item["similarity"]) for item in body["items"]], [(ROW_D, 0.9)])
        self.assertNotIn("region", body["items"][0]["details"])
        self.assertEqual(left_running, 0)  # 被丢弃的搜索结束了……
        self.assertEqual(self.embedding.sizes, [WHOLE])  # ……没有搜索它的检测框
        self.assertEqual(self.region_logs(logs.records), [])  # 没人读它的结果
        self.assertEqual(self.timing_steps(logs.records), ["接收请求", "读取上传", "整图搜索", "总耗时"])
        self.assertRegex(
            self.service_line(logs.records, "整图搜索完成"), r" \| 结果=1 \| 已取消=区域搜索 \| trace_id="
        )

    def test_a_detection_that_fails_after_its_search_was_dropped_is_not_reported_as_unretrieved(self):
        """请求失败、区域搜索被丢弃之后，还在跑的检测才失败：没人再等这次运行，它的异常也要
        被取出，否则 asyncio 会报告 "Task exception was never retrieved"。
        """
        detecting, dropped, finished = threading.Event(), threading.Event(), threading.Event()

        def detect():  # 在区域搜索的工作线程里：请求失败之后才失败
            detecting.set()
            try:
                dropped.wait(5)
                raise RuntimeError("gpu fell over")
            finally:
                finished.set()

        def embed(size):  # 整图：在检测进行期间失败
            if size == WHOLE:
                detecting.wait(2)
                raise err.search_bad_image("cannot read it")

        self.detector.on_detect = detect
        self.embedding.on_embed = embed
        with TestClient(self.app) as client, self.assertLogs(level="INFO") as logs:  # 一个事件循环：运行在上面结束
            response = client.post("/api/search/auto/file", files={"file": ("q.png", QUERY, "image/png")})
            dropped.set()
            self.assertTrue(finished.wait(5))
            time.sleep(0.1)  # 让事件循环处理这次运行的结束
            gc.collect()
        self.assertEqual((response.status_code, response.json()["error"]), (422, "SEARCH_BAD_IMAGE"))
        self.assertEqual([r.getMessage() for r in logs.records if "never retrieved" in r.getMessage()], [])

    def test_searches_dropped_while_waiting_for_the_model_never_run_it(self):
        """每个业务搜图都会启动区域搜索，而 GroundingDINO 一次只处理一张图：
        整图已经有结果的那些离开队列，所以不会耽误需要它的那个。
        """

        class SlowDetector(FakeDetector):  # 真实的检测器在锁里推理，每张图大约 0.44 秒
            def __init__(self):
                super().__init__()
                self.lock = threading.Lock()
                self.runs = 0

            def detect(self, image, **kwargs):
                with self.lock:
                    time.sleep(0.3)
                    self.runs += 1
                    return super().detect(image, **kwargs)

        self.runtime.detector = detector = SlowDetector()
        self.vectors.hits[WHOLE] = [{"id": ROW_D, "score": 0.9}]  # QUERY：整图有结果
        miss = encoded(300, 150)  # 整图没有结果：它的答案要等它自己的检测

        async def post(client, data):
            started = time.perf_counter()
            response = await client.post("/api/search/auto/file", files={"file": ("q.png", data, "image/png")})
            self.assertEqual(response.status_code, 200, response.text)
            return time.perf_counter() - started

        async def burst():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                hits = [asyncio.create_task(post(client, QUERY)) for _ in range(5)]
                await asyncio.sleep(0.05)
                waited = await post(client, miss)
                return await asyncio.gather(*hits), waited

        with self.assertLogs(level="INFO"):
            hit_times, waited = asyncio.run(burst())
        self.assertLess(max(hit_times), 0.3)  # 没有一个等了检测
        self.assertEqual(detector.runs, 2)  # 命中返回时正在进行的那一次，以及没命中的那个自己的一次
        self.assertLess(waited, 1.2)  # 那一次加上它自己的；如果排在每个命中请求的检测后面，要多 5 x 0.3 秒

    def test_waiting_for_another_search_to_finish_detecting_is_queueing(self):
        """两个整图都没有结果的请求同时来：后一个先等前一个的检测，这段时间记为排队，
        不算进它的目标检测。
        """

        class SlowDetector(FakeDetector):
            def detect(self, image, **kwargs):
                time.sleep(0.2)
                return super().detect(image, **kwargs)

        self.runtime.detector = SlowDetector()

        async def both():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await asyncio.gather(*(
                    client.post("/api/search/auto/file", files={"file": ("q.png", QUERY, "image/png")}) for _ in range(2)
                ))

        with self.assertLogs(level="INFO") as logs:
            responses = asyncio.run(both())
        self.assertEqual([response.status_code for response in responses], [200, 200])
        lines = [seconds(text) for _, text in self.region_logs(logs.records)]
        self.assertEqual(len(lines), 2)
        first, second = sorted(lines, key=lambda line: line["排队"])
        self.assertLess(first["排队"], 0.1)
        self.assertGreater(second["排队"], 0.15)  # 等了前一个的检测
        for line in (first, second):
            self.assertGreaterEqual(line["目标检测"], 0.19)
            self.assertLess(line["目标检测"], 0.35)

    def test_a_failed_whole_image_search_fails_the_request(self):
        body, records = self.by_file(data=b"broken", status=422)
        self.assertEqual(body["error"], "SEARCH_BAD_IMAGE")
        self.assertEqual(self.region_logs(records), [])

    # -- 区域没有结果 -------------------------------------------------------------------

    def test_no_box_means_no_region_hit(self):
        self.detector.boxes = [(TINY, 0.9, "crumb")]
        body, records = self.by_url()
        self.assertEqual((body["items"], body["fallback"]), ([], {"status": "disabled"}))
        self.assertEqual(body["message"], "自建库没有达到阈值的结果，未开启 Global 兜底")
        ((level, text),) = self.region_logs(records)
        self.assertEqual(level, logging.INFO)
        self.assertRegex(text, r"^区域搜索完成 \| .* \| 耗时 \d+\.\d{3}s \| 检测框=0 \| 结果=0 \| trace_id=")
        self.assertEqual(steps(text), ["排队", "图像解码", "目标检测", "区域裁剪", "耗时"])  # 没有可检索的框
        self.assertEqual(breakdown(text), [])
        self.assertEqual(self.timing_steps(records), ["接收请求", "图像下载", "整图搜索", "等待区域搜索", "总耗时"])

    def test_without_region_hits_the_fallback_runs(self):
        self.vectors.hits[LEFT_CROP] = self.vectors.hits[RIGHT_CROP] = [{"id": ROW_D, "score": 0.5}]
        body, _ = self.by_url(global_fallback=True)
        self.assertEqual(body["fallback"], {"status": "hit"})
        self.assertEqual(body["items"], [{"source": "global", **GLOBAL_CARD}])

    def test_a_detector_that_is_not_loaded_skips_the_step(self):
        self.runtime.detector = None
        self.runtime.detector_error = "RuntimeError: no CUDA device"
        body, records = self.by_url()
        self.assertEqual(body["items"], [])
        ((level, text),) = self.region_logs(records)
        self.assertEqual(level, logging.INFO)
        self.assertIn("区域搜索已跳过：GroundingDINO 未就绪：RuntimeError: no CUDA device", text)
        self.assertEqual(self.timing_steps(records), ["接收请求", "图像下载", "整图搜索", "等待区域搜索", "总耗时"])

    def test_a_failed_detection_is_logged_and_does_not_fail_the_request(self):
        self.detector.error = RuntimeError("gpu fell over")
        body, records = self.by_url()
        self.assertEqual((body["items"], body["fallback"]), ([], {"status": "disabled"}))
        ((level, text),) = self.region_logs(records)
        self.assertEqual(level, logging.WARNING)
        self.assertRegex(text, r"^区域搜索失败 \| .* \| 耗时 \d+\.\d{3}s \| 错误=RuntimeError: gpu fell over \| trace_id=")
        self.assertEqual(steps(text), ["排队", "图像解码", "目标检测失败", "耗时"])
        self.assertEqual(self.timing_steps(records), ["接收请求", "图像下载", "整图搜索", "等待区域搜索", "总耗时"])

    def test_a_box_that_cannot_be_searched_leaves_the_others(self):
        def embed(size):
            if size == RIGHT_CROP:
                raise ValueError("broken crop")

        self.embedding.on_embed = embed
        body, records = self.by_url()
        self.assertEqual(self.ids(body), [ROW_A, ROW_B])  # 只有 LEFT 的结果
        ((level, text),) = self.region_logs(records)
        self.assertEqual(level, logging.WARNING)
        self.assertRegex(text, r"^区域搜索完成 \| .* \| 耗时 \d+\.\d{3}s \| 检测框=2 \| 结果=2 \| 失败=1 \| 区域检索明细")
        self.assertEqual(steps(text), ["排队", "图像解码", "目标检测", "区域裁剪", "区域检索", "耗时"])  # 有框成功
        self.assertEqual(breakdown(text), ["embedding推理", "vector搜索", "后处理", "embedding推理失败"])

    # -- 关闭 ------------------------------------------------------------------------------

    def test_the_switch_and_the_threshold_turn_the_step_off(self):
        for config, fields in (
            ({"AUTO_SEARCH_REGION_CONFIG": {**REGION_CONFIG, "enabled": False}}, {}),
            ({}, {}),
            ({"AUTO_SEARCH_REGION_CONFIG": dict(REGION_CONFIG)}, {"score_threshold": 0}),  # 整图有结果
        ):
            with self.subTest(config=config, fields=fields):
                self.runtime.config = config
                self.vectors.hits[WHOLE] = [] if not fields else [{"id": ROW_D, "score": 0.1}]
                body, records = self.by_url(**fields)
                self.assertEqual(self.detector.calls, [])
                self.assertEqual(self.region_logs(records), [])
                self.assertEqual(self.ids(body), [ROW_D] if fields else [])

    def test_a_section_that_cannot_be_used_skips_the_step_with_a_warning(self):
        self.runtime.config = {"AUTO_SEARCH_REGION_CONFIG": {**REGION_CONFIG, "box_threshold": 2}}
        body, records = self.by_url()
        self.assertEqual(self.detector.calls, [])
        ((level, text),) = self.region_logs(records)
        self.assertEqual(level, logging.WARNING)
        self.assertIn("区域搜索已跳过：AUTO_SEARCH_REGION_CONFIG 配置有误 | AUTO_SEARCH_REGION_CONFIG.box_threshold", text)


if __name__ == "__main__":
    unittest.main()
