"""带菜谱识别的业务搜图（RECIPE_DETECT_CONFIG）。

识别和整图搜索同时启动。整图有结果时立即作为答案返回，识别被丢弃；否则识别出的菜名
作为一张结果卡（source=recipe）排在区域结果之后（tests/test_auto_search_regions.py），
都没有结果时才走 Global 兜底。识别的结果被用到时写它那一行（调用的各阶段、耗时和结果），
识别失败从不让请求失败。这些测试里区域搜索是关闭的。

结果来自哪里写在每张结果卡上（它的 source，第一个字段），不在外层；自建库结果卡的
details 说明是否带加热信息（outputs_heating）。

    整图搜索完成 | embedding推理 0.045s | vector搜索 0.012s | 后处理 0.003s | 耗时 0.061s | 结果=0
    菜谱识别完成 | 发送请求 0.001s | 等待响应 1.682s | 接收响应 0.000s | 耗时 1.684s | 结果=["荷塘小炒"] | 输入=图片链接
    业务搜图(图片链接) | 接收请求 0.001s | 图像下载 0.120s | 整图搜索 0.061s | 等待菜谱识别 1.623s | 总耗时 1.810s | 参数=…

调用的各阶段来自 httpcore 的 trace 事件，只有真实的网络传输才有：用 httpx.MockTransport 的
测试里那一行只有耗时，各阶段在本机起一个服务来测（recipe_service_on_the_network）。
"""

import asyncio
import json
import logging
import re
import threading
import time
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import auto_search
from app.bootstrap.state import ApplicationState
from app.clients.recipe_detect import detect_recipes
from app.core import errors as err
from app.core.config.settings import RecipeDetectSettings
from app.core.exceptions import register_exception_handlers
from app.core.middleware import TraceIdMiddleware
from app.services.predict.food_service import FoodService
from app.services.retrieval.active_model import ActiveModel
from app.services.retrieval.search_image import SearchImageUseCase
from app.utils.timer import timer_tool

ROW_ID = "11111111-1111-1111-1111-111111111111"
RECIPE_URL = "http://recipe.test:8866/detect_recipe_onnx"
DISHES = ["荷塘小炒", "凉拌猪耳", "清蒸鲈鱼"]
# 识别加上的那一张卡：菜名作为 tags，其余卡片字段为空
RECIPE_CARD = {
    "source": "recipe", "tags": "荷塘小炒、凉拌猪耳、清蒸鲈鱼", "description": "", "original_url": None,
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
PNG = b"\x89PNG\r\n\x1a\n" + b"pixels"


def recipe_answer(data, *, status=200, success=True, message="识别成功"):
    """菜谱识别服务的回复（失败时为非 2xx 状态码和 success=false）。"""
    return httpx.Response(status, json={
        "code": str(status), "success": success, "message": message, "traceId": "t", "model": "detect_recipe_onnx",
        "data": data,
    })


def steps(line):
    """The step names of a timing line in order, 总耗时 (or 耗时) last."""
    return re.findall(r"(?:^|\| )([^|]+?) \d+\.\d{3}s", line)


@contextmanager
def recipe_service_on_the_network(names, *, hold=None):
    """本机上真的监听一个端口的菜谱识别服务：httpx 走真实的网络传输，才有 trace 事件。
    它回答 names；给了 hold（threading.Event）时，读完请求后先等它再回答。产出服务的地址。
    支持 keep-alive：同一个客户端的下一次调用复用连接。
    """

    class Recipes(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            if hold is not None:
                hold.wait(5)
            body = json.dumps({"code": "200", "success": True, "message": "识别成功", "data": names}).encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except ConnectionError:  # 客户端已经超时走了
                self.close_connection = True

        def log_message(self, *args):  # 不写访问日志
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Recipes)
    # 关闭时 shutdown() 最多等一个 poll_interval（默认 0.5 秒），而它常在事件循环上调用
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/detect_recipe_onnx"
    finally:
        if hold is not None:
            hold.set()
        server.shutdown()
        server.server_close()
        thread.join(5)


class RecipeDetectSettingsTests(unittest.TestCase):
    def test_without_the_section_the_step_is_off(self):
        self.assertEqual(RecipeDetectSettings.from_config({}), RecipeDetectSettings())
        self.assertEqual(RecipeDetectSettings(), RecipeDetectSettings(enabled=False, url="", timeout=5.0))

    def test_yaml_scalars_are_coerced(self):
        settings = RecipeDetectSettings.from_config(
            {"RECIPE_DETECT_CONFIG": {"enabled": "true", "url": f" {RECIPE_URL} ", "timeout": "3"}}
        )
        self.assertEqual(settings, RecipeDetectSettings(enabled=True, url=RECIPE_URL, timeout=3.0))

    def test_the_address_and_the_timeout_are_only_required_when_it_is_on(self):
        off = RecipeDetectSettings.from_config({"RECIPE_DETECT_CONFIG": {"enabled": False, "url": "", "timeout": 0}})
        self.assertFalse(off.enabled)
        for section, key in (
            ({"enabled": True}, "RECIPE_DETECT_CONFIG.url"),
            ({"enabled": True, "url": "118.31.105.171:8866/detect_recipe_onnx"}, "RECIPE_DETECT_CONFIG.url"),
            ({"enabled": True, "url": "ftp://recipe.test/detect"}, "RECIPE_DETECT_CONFIG.url"),
            ({"enabled": True, "url": RECIPE_URL, "timeout": 0}, "RECIPE_DETECT_CONFIG.timeout"),
            ({"enabled": True, "url": RECIPE_URL, "timeout": "soon"}, "RECIPE_DETECT_CONFIG.timeout"),
            ({"enabled": "maybe", "url": RECIPE_URL}, "RECIPE_DETECT_CONFIG.enabled"),
        ):
            with self.subTest(section=section), self.assertRaises(ValueError) as raised:
                RecipeDetectSettings.from_config({"RECIPE_DETECT_CONFIG": section})
            self.assertIn(key, str(raised.exception))


class RecipeClientTests(unittest.IsolatedAsyncioTestCase):
    """在假的传输层上测试 app.clients.recipe_detect：它发送什么，以及怎样读取回复。"""

    async def detect(self, answer, *, timeout=2.0, **image):
        """（菜名, 服务收到的请求）；answer：一个响应、一个要抛出的异常，或者一个以请求为参数的
        协程函数。
        """
        received = []

        async def service(request):
            received.append(request)
            if isinstance(answer, Exception):
                raise answer
            return await answer(request) if callable(answer) else answer

        async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as client:
            try:
                return await detect_recipes(client, RECIPE_URL, trace_id="t-1", timeout=timeout, **image), received
            except err.AppError as exc:
                exc.received = received
                raise

    async def test_a_link_is_sent_as_json_and_the_service_downloads_it(self):
        names, (request,) = await self.detect(recipe_answer(DISHES), image_url="https://x.example/a.jpg", image_bytes=PNG)
        self.assertEqual(names, DISHES)
        self.assertEqual((request.method, str(request.url)), ("POST", RECIPE_URL))
        self.assertEqual(request.headers["content-type"], "application/json")
        self.assertEqual(json.loads(request.content), {"image_url": "https://x.example/a.jpg", "traceId": "t-1"})

    async def test_an_image_without_a_link_is_uploaded_as_a_file(self):
        for data, filename, media_type in (
            (PNG, "query.png", "image/png"),
            (b"\xff\xd8\xff\xe0jpeg", "query.jpg", "image/jpeg"),
            (b"RIFF\x10\x00\x00\x00WEBPVP8 ", "query.webp", "image/webp"),
            (b"BM\x00bmp", "query.bmp", "image/bmp"),
            (b"unknown bytes", "query", "application/octet-stream"),
        ):
            with self.subTest(media_type=media_type):
                names, (request,) = await self.detect(recipe_answer(DISHES), image_bytes=data)
                self.assertEqual(names, DISHES)
                self.assertTrue(request.headers["content-type"].startswith("multipart/form-data; boundary="))
                body = request.content
                self.assertIn(f'name="file"; filename="{filename}"\r\nContent-Type: {media_type}\r\n\r\n'.encode() + data, body)
                self.assertIn(b'name="traceId"\r\n\r\nt-1\r\n', body)

    async def test_names_are_stripped_and_blanks_repeats_and_non_strings_left_out(self):
        names, _ = await self.detect(recipe_answer([" 清蒸鲈鱼 ", "", "清蒸鲈鱼", 3, None, "凉拌猪耳"]), image_url="https://x.example/a.jpg")
        self.assertEqual(names, ["清蒸鲈鱼", "凉拌猪耳"])
        names, _ = await self.detect(recipe_answer([]), image_url="https://x.example/a.jpg")  # 没有识别出菜名
        self.assertEqual(names, [])

    async def test_an_answer_that_is_not_a_success_is_an_upstream_error_with_the_reason(self):
        for answer, said in (
            (recipe_answer([], status=503, success=False, message="上游识别模型不可用"), "HTTP 503，code=503）：上游识别模型不可用"),
            (recipe_answer([], success=False, message="图片下载失败"), "HTTP 200，code=200）：图片下载失败"),
            (recipe_answer(DISHES, success="true", message=""), "code=200）：没有给出原因"),  # success 必须是布尔值
            (recipe_answer(None), "data 不是列表"),
            (httpx.Response(200, text="<html>gateway</html>"), "不是 JSON 对象（HTTP 200）"),
            (httpx.Response(200, json=DISHES), "不是 JSON 对象（HTTP 200）"),
            (httpx.Response(502, text="Bad Gateway"), "不是 JSON 对象（HTTP 502）"),
            (httpx.ConnectError("connection refused"), "无法连接菜谱识别服务（ConnectError）：connection refused"),
        ):
            with self.subTest(said=said), self.assertRaises(err.AppError) as raised:
                await self.detect(answer, image_url="https://x.example/a.jpg")
            self.assertEqual(raised.exception.code, "UPSTREAM_ERROR")
            self.assertIn(said, raised.exception.detail)

    async def test_the_timeout_bounds_the_whole_call(self):
        async def never(request):
            await asyncio.sleep(30)

        started = time.monotonic()
        for answer in (never, httpx.ReadTimeout("no data")):
            with self.subTest(answer=answer), self.assertRaises(err.AppError) as raised:
                await self.detect(answer, timeout=0.05, image_url="https://x.example/a.jpg")
            self.assertEqual(raised.exception.code, "UPSTREAM_TIMEOUT")
            self.assertIn("0.05 秒内没有返回", raised.exception.detail)
        self.assertLess(time.monotonic() - started, 5)

    async def test_without_a_link_or_bytes_nothing_is_sent(self):
        with self.assertRaises(err.AppError) as raised:
            await self.detect(recipe_answer(DISHES), image_url=None, image_bytes=b"")
        self.assertEqual(raised.exception.code, "UPSTREAM_ERROR")
        self.assertEqual(raised.exception.received, [])

    async def test_a_transport_without_network_events_records_no_stage(self):
        timer = timer_tool()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: recipe_answer(DISHES))) as client:
            await detect_recipes(client, RECIPE_URL, image_url="https://x.example/a.jpg", timeout=2, timer=timer)
        self.assertEqual(timer.items(), [])


class RecipeCallStageTests(unittest.IsolatedAsyncioTestCase):
    """真实的网络传输上（本机的服务），一次调用的各阶段：建立连接 / 发送请求 / 等待响应 / 接收响应。"""

    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(trust_env=False)  # 不走环境里配置的代理

    async def asyncTearDown(self):
        await self.client.aclose()

    async def stages(self, url, *, timeout=5.0, **image):
        timer = timer_tool()
        try:
            await detect_recipes(self.client, url, trace_id="t-1", timeout=timeout, timer=timer, **image)
        finally:
            self.timer = timer
        return [step for step, _ in timer.items()]

    async def test_a_call_is_split_into_its_stages_and_a_reused_connection_has_no_connecting(self):
        with recipe_service_on_the_network(DISHES) as url:
            self.assertEqual(
                await self.stages(url, image_url="https://x.example/a.jpg"), ["建立连接", "发送请求", "等待响应", "接收响应"]
            )
            # 同一个连接上的下一次调用（这次上传图片）
            self.assertEqual(await self.stages(url, image_bytes=PNG), ["发送请求", "等待响应", "接收响应"])

    async def test_the_stage_cut_off_by_the_timeout_is_marked_failed(self):
        with recipe_service_on_the_network(DISHES, hold=threading.Event()) as url:
            with self.assertRaises(err.AppError) as raised:
                await self.stages(url, timeout=0.5, image_bytes=PNG)
        self.assertEqual(raised.exception.code, "UPSTREAM_TIMEOUT")
        recorded = dict(self.timer.items())
        self.assertEqual(list(recorded), ["建立连接", "发送请求", "等待响应失败"])  # 请求发完了，服务没有回答
        # 时限里剩下的时间都在等：客户端第一次调用前的准备（不属于任何阶段）在 debug 模式下约 0.1 秒
        self.assertGreater(recorded["等待响应失败"], 0.25)

    async def test_a_service_that_cannot_be_reached_fails_while_connecting(self):
        with recipe_service_on_the_network(DISHES) as url:
            pass  # 服务已经关了，端口没人监听
        with self.assertRaises(err.AppError) as raised:
            await self.stages(url, image_url="https://x.example/a.jpg")
        self.assertEqual(raised.exception.code, "UPSTREAM_ERROR")
        self.assertEqual([step for step, _ in self.timer.items()], ["建立连接失败"])


class FakeEmbedding:
    def __init__(self):
        self.on_embed = lambda: None

    def embed_bytes(self, image_bytes):
        self.on_embed()
        if image_bytes == b"broken":
            raise ValueError("cannot decode")
        return [1.0, 0.0, 0.0, 0.0]


class FakeVectors:
    def __init__(self):
        self.hits = [{"id": ROW_ID, "score": 0.9}]
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


class BusinessSearchRecipeTests(unittest.TestCase):
    """开启这一步时的 POST /api/search/auto/*；菜谱识别服务是假的传输层。"""

    def setUp(self):
        self.embedding = FakeEmbedding()
        self.vectors = FakeVectors()
        model = ActiveModel(id=1, name="m", version="v", collection="c", dim=4, distance="cosine")
        search = SearchImageUseCase(self.embedding, self.vectors, FakeMetadata(), model=model)
        # 两个阶段都是假的 Global 搜图：上游搜索和相似度过滤
        self.food = FoodService(FOOD_CONFIG, Mock())
        self.food.searchTool = Mock()
        self.food.searchTool.detect.return_value = [dict(GLOBAL_CARD)]
        self.food.searchFilter = Mock()
        self.food.searchFilter.detect.return_value = [dict(GLOBAL_CARD)]

        self.received = []  # 菜谱识别服务收到的请求
        self.answer = recipe_answer(DISHES)  # 一个响应、一个要抛出的异常，或者一个协程函数
        self.runtime = ApplicationState(
            config={"RECIPE_DETECT_CONFIG": {"enabled": True, "url": RECIPE_URL, "timeout": 2}}, config_info={},
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(self.recipe_service)),
            settings=SimpleNamespace(predict=SimpleNamespace(max_image_bytes=1024, http_timeout=3.0)),
            retrieval=SimpleNamespace(search_uc=search),
            food_service=self.food,
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
        fetch = patch("app.clients.image_fetch.public_image.fetch_image", return_value=(b"query", "image/png"))
        self.fetch = fetch.start()
        self.addCleanup(fetch.stop)

    async def recipe_service(self, request):
        self.received.append(request)
        if isinstance(self.answer, Exception):
            raise self.answer
        return await self.answer(request) if callable(self.answer) else self.answer

    def post(self, path, *, status=200, **request):
        """发送 POST；返回（响应体, 这次请求的日志记录）。"""
        with self.assertLogs(level="INFO") as logs:
            response = self.client.post(path, **request)
        self.assertEqual(response.status_code, status, response.text)
        return response.json(), logs.records

    def by_url(self, **fields):
        return self.post("/api/search/auto/url", json={"image_url": "https://x.example/a.png", **fields})

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
        """服务写的、以 title 开头的那一行（只有一行）：它的日志记录。"""
        (record,) = [r for r in records if r.name == "svc.auto_search" and r.getMessage().startswith(title)]
        return record

    # -- 合并后的答案 -----------------------------------------------------------------

    def test_whole_image_hits_are_the_answer_without_the_dish_names(self):
        body, records = self.by_url(trace_id="t-9", global_fallback=True)
        self.assertEqual(body["fallback"], {"status": "not_needed"})
        (hit,) = body["items"]
        self.assertEqual((hit["source"], hit["tags"], hit["similarity"]), ("self", "牛排", 0.9))
        self.assertEqual(hit["details"]["id"], ROW_ID)
        self.assertNotIn("region", hit["details"])  # 整图的，不是区域的
        self.assertEqual(list(hit), CARD_KEYS)  # source 在最前面
        self.assertNotIn("message", body)
        self.food.searchTool.detect.assert_not_called()
        # 识别不属于答案：没有它那一行，请求那一行也不等它
        self.assertEqual(self.recipe_logs(records), [])
        self.assertEqual(steps(self.timing_line(records).getMessage()), ["接收请求", "图像下载", "整图搜索", "总耗时"])
        whole = self.service_line(records, "整图搜索完成").getMessage()
        self.assertEqual(steps(whole), ["embedding推理", "vector搜索", "后处理", "耗时"])
        self.assertIn(" | 结果=1", whole)

    def test_whole_image_hits_do_not_wait_for_the_detection(self):
        detecting = threading.Event()
        calls = {"started": 0, "cancelled": 0}

        async def answer(request):
            calls["started"] += 1
            detecting.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                calls["cancelled"] += 1
                raise

        self.answer = answer
        self.embedding.on_embed = lambda: detecting.wait(2)  # 结果返回时识别还在进行
        with TestClient(self.app) as client, self.assertLogs(level="INFO") as logs:  # 一个事件循环，和下面一样
            started = time.monotonic()
            response = client.post("/api/search/auto/file", files={"file": ("q.png", PNG, "image/png")})
            took = time.monotonic() - started
            left = dict(calls)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([item["source"] for item in response.json()["items"]], ["self"])
        self.assertLess(took, 5)
        self.assertEqual(left, {"started": 1, "cancelled": 1})
        self.assertEqual(self.recipe_logs(logs.records), [])
        # 整图那一行说明识别还在进行、被取消了
        whole = self.service_line(logs.records, "整图搜索完成").getMessage()
        self.assertRegex(whole, r" \| 结果=1 \| 已取消=菜谱识别 \| trace_id=")

    def test_the_recipe_service_gets_the_link_and_the_trace_id(self):
        self.vectors.hits = []
        self.by_url(trace_id="t-9")
        (request,) = self.received
        self.assertEqual(str(request.url), RECIPE_URL)
        self.assertEqual(
            json.loads(request.content), {"image_url": "https://x.example/a.png", "traceId": "t-9"}
        )

    def test_an_upload_is_sent_to_the_recipe_service_as_a_file(self):
        self.vectors.hits = []
        body, _ = self.post(
            "/api/search/auto/file", files={"file": ("q.png", PNG, "image/png")}, headers={"X-Trace-Id": "t-file"}
        )
        self.assertEqual(body["items"], [RECIPE_CARD])
        (request,) = self.received
        self.assertTrue(request.headers["content-type"].startswith("multipart/form-data"))
        self.assertIn(b'name="file"; filename="query.png"\r\nContent-Type: image/png\r\n\r\n' + PNG, request.content)
        self.assertIn(b'name="traceId"\r\n\r\nt-file\r\n', request.content)

    def test_dish_names_alone_are_an_answer_and_the_fallback_stays_off(self):
        self.vectors.hits = []
        body, _ = self.by_url(scene="oven", global_fallback=True)
        self.assertEqual(body["items"], [RECIPE_CARD])  # 场景输出加热信息，菜谱卡不带
        self.assertEqual(body["fallback"], {"status": "not_needed"})
        self.assertNotIn("message", body)
        self.food.searchTool.detect.assert_not_called()

    def test_the_envelope_leaves_the_source_and_the_heating_flag_to_the_cards(self):
        body, _ = self.by_url(trace_id="t-9")
        self.assertEqual(list(body), ["ok", "trace_id", "scene", "items", "fallback"])
        self.assertEqual((body["ok"], body["trace_id"], body["scene"]), (True, "t-9", "general"))
        (hit,) = body["items"]
        # 不输出加热信息的场景：标志如实说明，加热字段不出现
        self.assertIs(hit["details"]["outputs_heating"], False)
        self.assertEqual(list(hit["details"])[-1], "outputs_heating")
        self.assertFalse({"food_state", "heating_group"} & set(hit["details"]))

        body, _ = self.by_url(scene="oven")
        self.assertEqual(body["scene"], "oven")
        (hit,) = body["items"]
        self.assertIs(hit["details"]["outputs_heating"], True)
        self.assertEqual((hit["details"]["food_state"], hit["details"]["heating_group"]), ("raw", None))

        self.vectors.hits = []
        body, _ = self.by_url(scene="oven")
        self.assertEqual(body["items"], [RECIPE_CARD])  # 只有自建库结果卡带这个标志

        # 接口文档也是这么说的
        schemas = self.app.openapi()["components"]["schemas"]
        self.assertEqual(
            list(schemas["AutoSearchResponse"]["properties"]), ["ok", "trace_id", "scene", "items", "fallback", "message"]
        )
        self.assertEqual(list(schemas["AutoSearchHit"]["properties"]), CARD_KEYS)
        self.assertEqual(schemas["AutoSearchHit"]["required"], ["source"])

    def test_nothing_from_either_goes_on_as_before(self):
        self.vectors.hits = []
        self.answer = recipe_answer([])

        body, _ = self.by_url()
        self.assertEqual((body["items"], body["fallback"]), ([], {"status": "disabled"}))
        self.assertEqual(body["message"], "自建库没有达到阈值的结果，菜谱识别没有识别到菜名，未开启 Global 兜底")
        self.assertEqual(list(body), ["ok", "trace_id", "scene", "items", "fallback", "message"])

        body, records = self.by_url(global_fallback=True)
        self.assertEqual(body["fallback"], {"status": "hit"})
        self.assertEqual(body["items"], [{"source": "global", **GLOBAL_CARD}])  # Global 搜图的卡，保持原样
        self.assertEqual(list(body["items"][0]), CARD_KEYS)
        self.assertEqual(
            steps(self.timing_line(records).getMessage()),
            ["接收请求", "图像下载", "整图搜索", "等待菜谱识别", "Global兜底", "总耗时"],
        )
        # 每一步一行，按固定的顺序：整图、识别、兜底
        lines = [r.getMessage() for r in records if r.name == "svc.auto_search"]
        self.assertEqual([line.split(" | ")[0] for line in lines], ["整图搜索完成", "菜谱识别完成", "Global兜底完成"])
        self.assertEqual(steps(lines[2]), ["global搜索", "后处理", "耗时"])

        self.food.searchTool.detect.return_value = []
        self.food.searchFilter.detect.return_value = []
        body, _ = self.by_url(global_fallback=True)
        self.assertEqual((body["items"], body["fallback"]), ([], {"status": "empty"}))
        self.assertEqual(body["message"], "自建库没有达到阈值的结果，菜谱识别没有识别到菜名，Global 也没有找到结果")

    # -- 日志里的耗时和结果 -----------------------------------------------------------

    def test_the_time_and_the_result_are_logged(self):
        self.vectors.hits = []
        body, records = self.by_url(trace_id="t-9")
        self.assertEqual(body["items"], [RECIPE_CARD])
        ((level, text),) = self.recipe_logs(records)
        self.assertEqual(level, logging.INFO)
        # MockTransport 没有网络事件：只有耗时（各阶段见 RecipeCallStageTests）
        self.assertRegex(
            text,
            r'^菜谱识别完成 \| 耗时 \d+\.\d{3}s \| 结果=\["荷塘小炒", "凉拌猪耳", "清蒸鲈鱼"\] \| 输入=图片链接 \| trace_id=t-9$',
        )
        # ……请求那一行在整图搜索之后等它
        line = self.timing_line(records)
        self.assertEqual(steps(line.getMessage()), ["接收请求", "图像下载", "整图搜索", "等待菜谱识别", "总耗时"])
        self.assertEqual(line.levelno, logging.INFO)

        self.answer = recipe_answer([])
        _, records = self.post("/api/search/auto/file", files={"file": ("q.png", PNG, "image/png")})
        ((level, text),) = self.recipe_logs(records)
        self.assertRegex(text, r"^菜谱识别完成 \| 耗时 \d+\.\d{3}s \| 结果=\[\] \| 输入=图片内容 \| trace_id=")

    def test_the_line_has_the_stages_of_a_call_over_the_network(self):
        self.vectors.hits = []
        client = httpx.AsyncClient(trust_env=False, limits=httpx.Limits(max_keepalive_connections=0))
        self.addCleanup(asyncio.run, client.aclose())
        self.runtime.http_client = client
        with recipe_service_on_the_network(DISHES) as url:
            self.runtime.config = {"RECIPE_DETECT_CONFIG": {"enabled": True, "url": url, "timeout": 5}}
            body, records = self.by_url(trace_id="t-9")
        self.assertEqual(body["items"], [RECIPE_CARD])
        line = self.service_line(records, "菜谱识别完成").getMessage()
        self.assertEqual(steps(line), ["建立连接", "发送请求", "等待响应", "接收响应", "耗时"])
        self.assertTrue(line.endswith(' | 结果=["荷塘小炒", "凉拌猪耳", "清蒸鲈鱼"] | 输入=图片链接 | trace_id=t-9'), line)

    # -- 失败或关闭的识别 ---------------------------------------------------------------

    def test_a_failed_detection_is_logged_and_does_not_fail_the_search(self):
        self.vectors.hits = []
        self.answer = recipe_answer([], status=503, success=False, message="上游识别模型不可用")
        body, records = self.by_url(trace_id="t-9")
        self.assertEqual((body["items"], body["fallback"]), ([], {"status": "disabled"}))
        ((level, text),) = self.recipe_logs(records)
        self.assertEqual(level, logging.WARNING)
        self.assertRegex(
            text,
            r"^菜谱识别失败 \| 耗时 \d+\.\d{3}s \| 错误=UPSTREAM_ERROR: 菜谱识别服务报错（HTTP 503，code=503）：上游识别模型不可用"
            r" \| 输入=图片链接 \| trace_id=t-9$",
        )
        line = self.timing_line(records)  # 请求本身成功了：它只是等了识别
        self.assertEqual(steps(line.getMessage()), ["接收请求", "图像下载", "整图搜索", "等待菜谱识别", "总耗时"])
        self.assertEqual(line.levelno, logging.INFO)

        self.answer = httpx.ReadTimeout("no data")
        body, records = self.by_url()
        self.assertEqual((body["items"], body["fallback"]), ([], {"status": "disabled"}))
        self.assertEqual(body["message"], "自建库没有达到阈值的结果，菜谱识别失败，未开启 Global 兜底")
        ((level, text),) = self.recipe_logs(records)
        self.assertIn(" | 错误=UPSTREAM_TIMEOUT: 菜谱识别服务 2 秒内没有返回 | ", text)

        body, _ = self.by_url(global_fallback=True)  # ……兜底照样执行
        self.assertEqual(([item["source"] for item in body["items"]], body["fallback"]), (["global"], {"status": "hit"}))

    def test_the_switch_turns_the_step_off(self):
        for config in ({"RECIPE_DETECT_CONFIG": {"enabled": False, "url": RECIPE_URL}}, {}):
            with self.subTest(config=config):
                self.runtime.config = config
                body, records = self.by_url()
                self.assertEqual([item["source"] for item in body["items"]], ["self"])
                self.assertEqual(self.received, [])
                self.assertEqual(self.recipe_logs(records), [])
                self.assertEqual(
                    steps(self.timing_line(records).getMessage()), ["接收请求", "图像下载", "整图搜索", "总耗时"]
                )
                # 没有启动识别：整图那一行没有「已取消」
                self.assertNotIn("已取消", self.service_line(records, "整图搜索完成").getMessage())
        self.vectors.hits = []
        body, _ = self.by_url()  # 没有这一步的搜索，信息还是原来的那些
        self.assertEqual(body["message"], "自建库没有达到阈值的结果，未开启 Global 兜底")

    def test_the_section_is_read_on_every_request(self):
        self.vectors.hits = []
        self.runtime.config = {}
        body, _ = self.by_url()
        self.assertEqual((self.received, body["items"]), ([], []))
        self.runtime.config = {"RECIPE_DETECT_CONFIG": {"enabled": True, "url": RECIPE_URL}}  # 和 Nacos 重新加载时一样
        body, _ = self.by_url()
        self.assertEqual(len(self.received), 1)
        self.assertEqual(body["items"], [RECIPE_CARD])

    def test_a_section_that_cannot_be_used_skips_the_step_with_a_warning(self):
        self.runtime.config = {"RECIPE_DETECT_CONFIG": {"enabled": True, "url": ""}}
        body, records = self.by_url()
        self.assertEqual([item["source"] for item in body["items"]], ["self"])
        self.assertEqual(self.received, [])
        ((level, text),) = self.recipe_logs(records)
        self.assertEqual(level, logging.WARNING)
        self.assertIn("菜谱识别已跳过：RECIPE_DETECT_CONFIG 配置有误 | RECIPE_DETECT_CONFIG.url", text)

    # -- 同时进行 ---------------------------------------------------------------------

    def test_the_detection_runs_while_the_whole_image_search_does(self):
        searching, detecting = threading.Event(), threading.Event()
        seen = {}
        self.vectors.hits = []  # 这样答案会等识别结束

        def search():  # 在整图搜索的工作线程里
            searching.set()
            seen["detection started during the search"] = detecting.wait(2)

        async def answer(request):  # 在事件循环上
            detecting.set()
            deadline = time.monotonic() + 2
            while not searching.is_set() and time.monotonic() < deadline:
                await asyncio.sleep(0.005)
            seen["search started during the detection"] = searching.is_set()
            return recipe_answer(DISHES)

        self.vectors.on_search = search
        self.answer = answer
        body, _ = self.by_url()
        self.assertEqual(seen, {"detection started during the search": True, "search started during the detection": True})
        self.assertEqual(body["items"], [RECIPE_CARD])

    def test_a_failed_self_built_search_fails_the_request_and_drops_the_detection(self):
        detecting = threading.Event()
        calls = {"started": 0, "cancelled": 0}

        async def answer(request):
            calls["started"] += 1
            detecting.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                calls["cancelled"] += 1
                raise

        self.answer = answer
        self.embedding.on_embed = lambda: detecting.wait(2)  # 搜索在识别进行期间失败
        # 整个代码块用一个事件循环（self.client 每个请求都会结束它的事件循环，以及还在上面运行的
        # 东西）：留下来继续运行的识别在这里仍然会在运行
        with TestClient(self.app) as client, self.assertLogs(level="INFO") as logs:
            started = time.monotonic()
            response = client.post("/api/search/auto/file", files={"file": ("q.png", b"broken", "image/png")})
            took = time.monotonic() - started
            left = dict(calls)
        self.assertEqual((response.status_code, response.json()["error"]), (422, "SEARCH_BAD_IMAGE"))
        self.assertLess(took, 5)  # 它没有等识别
        self.assertEqual(left, {"started": 1, "cancelled": 1})
        self.assertEqual(self.recipe_logs(logs.records), [])  # 没什么要报告的：没人读它的结果
        line = self.timing_line(logs.records)
        self.assertEqual(steps(line.getMessage()), ["接收请求", "读取上传", "整图搜索失败", "总耗时"])
        self.assertEqual(line.levelno, logging.WARNING)
        # 整图那一行说明是哪个阶段失败的
        whole = self.service_line(logs.records, "整图搜索失败")
        self.assertEqual(steps(whole.getMessage()), ["embedding推理失败", "耗时"])
        self.assertIn(" | 错误=SEARCH_BAD_IMAGE: embed failed: cannot decode | trace_id=", whole.getMessage())
        self.assertEqual(whole.levelno, logging.WARNING)


if __name__ == "__main__":
    unittest.main()
