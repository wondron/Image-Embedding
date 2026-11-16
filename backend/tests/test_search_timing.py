"""Search timing: every layer records its steps on the interface's timer_tool, and each search
interface writes one line for the request, through its logger and through show_text.

    自建库搜图(图片链接) | 图像下载 0.120s | embedding推理 0.045s | vector搜索 0.012s | 后处理 0.003s | 总耗时 0.185s
"""

import logging
import re
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import auto_search, batch_search, predict, retrieval
from app.bootstrap.state import ApplicationState
from app.core import errors as err
from app.core.exceptions import register_exception_handlers
from app.core.middleware import TraceIdMiddleware
from app.core.observability import log_helper
from app.services.predict.food_service import FoodService
from app.services.retrieval.active_model import ActiveModel
from app.services.retrieval.search_image import SearchImageUseCase
from app.utils import timer as timer_module
from app.utils.timer import timer_tool

ROW_ID = "11111111-1111-1111-1111-111111111111"
GLOBAL_CARD = {"tags": "番茄", "description": "", "original_url": "https://img.example/a.jpg", "similarity": 0.8, "details": {}}
FOOD_CONFIG = {
    "FLAB_CONFIG": {
        "API_URL": "https://search.invalid/global_search", "API_KEY": "test-only-key", "DOC_COUNT": 5,
        "MAX_SNIPPET_LENGTH": 300, "THRESHOLD": 0.8, "EnableWaiting": False, "Timeout": 1,
    },
    "GLOBAL_FILTER": {"switch": True, "threshold": 0.5},
}


def steps(line):
    """The step names of a timing line in order, 总耗时 last (title, parameters and trace id left out)."""
    return re.findall(r"(?:^|\| )([^|]+?) \d+\.\d{3}s", line)


class FakeEmbedding:
    def embed_bytes(self, image_bytes):
        if image_bytes == b"broken":
            raise ValueError("cannot decode")
        return [1.0, 0.0, 0.0, 0.0]


class FakeVectors:
    def __init__(self):
        self.hits = [{"id": ROW_ID, "score": 0.9}]

    def search(self, vector, *, top_k, score_threshold, scene):
        return list(self.hits)


class FakeMetadata:
    def get_scene_by_code(self, code):
        return {"id": 1, "code": code, "outputs_heating": False, "is_active": True} if code != "missing" else None

    def list_scene_codes(self, active_only=True):
        return ["general"]

    def get_active_by_ids(self, ids, scene=None):
        return {i: {"id": i, "category_id": None, "original_url": "https://cdn/o.jpg"} for i in ids}

    def map_categories_to_scene_labels(self, scene_id, category_ids):
        return {}


def search_use_case(vectors=None):
    model = ActiveModel(id=1, name="m", version="v", collection="c", dim=4, distance="cosine")
    return SearchImageUseCase(FakeEmbedding(), vectors or FakeVectors(), FakeMetadata(), model=model)


def food_service():
    """FoodService with both stages faked: the upstream search and the similarity filter."""
    service = FoodService(FOOD_CONFIG, Mock())
    service.searchTool = Mock()
    service.searchTool.detect.return_value = [dict(GLOBAL_CARD)]
    service.searchFilter = Mock()
    service.searchFilter.detect.return_value = [dict(GLOBAL_CARD)]
    return service


class TimerToolTests(unittest.TestCase):
    def clock(self, *ticks):
        """timer_tool reads these perf_counter values, in order."""
        clock = patch.object(timer_module, "time")
        clock.start().perf_counter.side_effect = ticks
        self.addCleanup(clock.stop)

    def test_a_step_times_its_block_only_and_the_summary_is_one_line(self):
        self.clock(0.0, 1.0, 1.5, 2.0, 2.25, 4.0)
        timer = timer_tool()
        with timer.step("图像下载"):
            pass
        with timer.step("embedding推理"):
            pass
        # the time between and around the steps is in the total only
        self.assertEqual(timer.summary(), "图像下载 0.500s | embedding推理 0.250s | 总耗时 4.000s")

    def test_a_repeated_step_adds_up_and_moves_to_its_latest_place(self):
        self.clock(0.0, 1.0, 1.5, 2.0, 2.25, 3.0, 3.25, 4.0)
        timer = timer_tool()
        for name in ("后处理", "global搜索", "后处理"):
            with timer.step(name):
                pass
        self.assertEqual(timer.summary(), "global搜索 0.250s | 后处理 0.750s | 总耗时 4.000s")

    def test_a_step_that_raises_is_recorded_as_failed_and_the_error_goes_on(self):
        self.clock(0.0, 1.0, 1.5, 2.0)
        timer = timer_tool()
        with self.assertRaises(ValueError):
            with timer.step("vector搜索"):
                raise ValueError("refused")
        self.assertEqual(timer.summary(), "vector搜索失败 0.500s | 总耗时 2.000s")

    def test_mark_times_from_the_previous_record(self):
        self.clock(0.0, 1.0, 1.5, 2.5, 3.0)
        timer = timer_tool()
        with timer.step("图像下载"):
            pass
        timer.mark("后处理")  # since the step ended
        self.assertEqual(timer.summary(), "图像下载 0.500s | 后处理 1.000s | 总耗时 3.000s")


class RecordedStepsTests(unittest.TestCase):
    """What each layer records on the timer it is given; none of them writes a timing line."""

    def test_self_built_search_records_embedding_vector_search_and_post_processing(self):
        timer = timer_tool()
        with self.assertNoLogs(level="INFO"):  # the platform log (show_text) would show up here too
            found = search_use_case().execute(image_bytes=b"query", timer=timer)
        self.assertEqual(found["source"], "self")
        self.assertEqual(steps(timer.summary()), ["embedding推理", "vector搜索", "后处理", "总耗时"])

    def test_self_built_search_names_the_step_that_failed(self):
        timer = timer_tool()
        with self.assertRaises(err.AppError) as raised:
            search_use_case().execute(image_bytes=b"broken", timer=timer)
        self.assertEqual(raised.exception.code, "SEARCH_BAD_IMAGE")
        self.assertEqual(steps(timer.summary()), ["embedding推理失败", "总耗时"])

    def test_a_request_refused_before_the_first_step_records_none(self):
        timer = timer_tool()
        with self.assertRaises(err.AppError) as raised:
            search_use_case().execute(image_bytes=b"query", scene="missing", timer=timer)
        self.assertEqual(raised.exception.code, "SEARCH_UNKNOWN_SCENE")
        self.assertEqual(steps(timer.summary()), ["总耗时"])

    def test_self_built_search_still_runs_without_a_timer(self):
        self.assertEqual(search_use_case().execute(image_bytes=b"query")["source"], "self")

    def test_global_search_records_the_upstream_search_and_the_filter(self):
        timer = timer_tool()
        service = food_service()
        with self.assertNoLogs(level="INFO"):
            self.assertEqual(service.detect(b"query", search_num=3, timer=timer), [GLOBAL_CARD])
        self.assertEqual(steps(timer.summary()), ["global搜索", "后处理", "总耗时"])

        service.searchTool.detect.side_effect = requests.Timeout("slow upstream")
        timer = timer_tool()
        with self.assertRaises(requests.Timeout):
            service.detect(b"query", timer=timer)
        self.assertEqual(steps(timer.summary()), ["global搜索失败", "总耗时"])


class TimingLineTests(unittest.TestCase):
    """One timing line per request, the same text through the route's logger and show_text."""

    def setUp(self):
        self.vectors = FakeVectors()
        self.food = food_service()
        self.runtime = ApplicationState(
            config={}, config_info={}, http_client=Mock(),
            settings=SimpleNamespace(predict=SimpleNamespace(max_image_bytes=1024, http_timeout=3.0)),
            retrieval=SimpleNamespace(search_uc=search_use_case(self.vectors)),
            food_service=self.food,
        )
        app = FastAPI()
        register_exception_handlers(app)
        app.add_middleware(TraceIdMiddleware)
        for module in (retrieval, auto_search, batch_search, predict):
            app.include_router(module.router)
            state = patch.object(module, "get_application_state", return_value=self.runtime)
            state.start()
            self.addCleanup(state.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        fetch = patch("app.clients.image_fetch.public_image.fetch_image", return_value=(b"query", "image/png"))
        self.fetch = fetch.start()
        self.addCleanup(fetch.stop)

    def post(self, path, *, status=200, **request):
        """POST; returns (the request's one timing line, its log record, the response)."""
        with patch.object(log_helper, "show_text") as platform, self.assertLogs(level="INFO") as logs:
            response = self.client.post(path, **request)
        self.assertEqual(response.status_code, status, response.text)
        records = [record for record in logs.records if "总耗时" in record.getMessage()]
        self.assertEqual(len(records), 1, [record.getMessage() for record in logs.records])
        record = records[0]
        # The logger's line ends with the trace id; show_text gets the same text and the id as a field
        line, _, trace_id = record.getMessage().rpartition(" | trace_id=")
        self.assertEqual(trace_id, response.headers["X-Trace-Id"])
        platform.assert_called_once_with(line, trace_id=trace_id, logger_level=record.levelno)
        return line, record, response

    def upload(self, name="q.png", data=b"query"):
        return {"file": (name, data, "image/png")}

    def test_self_built_search_by_url_times_the_download_and_the_three_search_steps(self):
        line, record, _ = self.post("/api/search", json={"img_url": "https://x.example/a.png", "trace_id": "t-1"})
        self.assertRegex(
            line,
            r"^自建库搜图\(图片链接\) \| 图像下载 \d+\.\d{3}s \| embedding推理 \d+\.\d{3}s"
            r" \| vector搜索 \d+\.\d{3}s \| 后处理 \d+\.\d{3}s \| 总耗时 \d+\.\d{3}s$",
        )
        self.assertEqual((record.name, record.levelno), ("svc.retrieval", logging.INFO))
        self.assertTrue(record.getMessage().endswith(" | trace_id=t-1"))

    def test_an_upload_has_no_download_step(self):
        line, _, _ = self.post("/api/search", files=self.upload())
        self.assertTrue(line.startswith("自建库搜图(上传文件) | "), line)
        self.assertEqual(steps(line), ["embedding推理", "vector搜索", "后处理", "总耗时"])

    def test_a_failed_request_is_a_warning_that_names_the_failed_step(self):
        self.fetch.side_effect = err.download_failed("Could not download the remote image.")
        line, record, _ = self.post("/api/search", status=502, json={"img_url": "https://x.example/a.png"})
        self.assertTrue(line.startswith("自建库搜图(图片链接)失败 | "), line)
        self.assertEqual(steps(line), ["图像下载失败", "总耗时"])
        self.assertEqual(record.levelno, logging.WARNING)

        # Refused before any step (unknown scene): still one line, with the total only
        line, record, _ = self.post("/api/search", status=422, files=self.upload(), data={"scene": "missing"})
        self.assertTrue(line.startswith("自建库搜图(上传文件)失败 | "), line)
        self.assertEqual(steps(line), ["总耗时"])
        self.assertEqual(record.levelno, logging.WARNING)

    def test_business_search_keeps_the_request_parameters_that_were_given(self):
        line, record, response = self.post(
            "/api/search/auto/file", files=self.upload(), data={"top_k": "5", "machine_info": "KQ-01"}
        )
        self.assertEqual(response.json()["source"], "self")
        self.assertTrue(line.startswith("业务搜图(上传文件) | "), line)
        self.assertEqual(steps(line), ["embedding推理", "vector搜索", "后处理", "总耗时"])
        # scene and score_threshold were not sent: left out. machine_info is only ever seen here
        self.assertTrue(line.endswith(" | 参数=top_k=5, global_fallback=False, machine_info=KQ-01"), line)
        self.assertEqual((record.name, record.levelno), ("autosearch", logging.INFO))
        self.food.searchTool.detect.assert_not_called()

    def test_business_search_adds_the_global_steps_when_it_falls_back(self):
        self.vectors.hits = []
        line, _, response = self.post(
            "/api/search/auto/url", json={"image_url": "https://x.example/a.png", "global_fallback": True}
        )
        self.assertEqual(response.json()["source"], "global")
        self.assertTrue(line.startswith("业务搜图(图片链接) | "), line)
        # 后处理: the self-built one plus the global filter, added up
        self.assertEqual(steps(line), ["图像下载", "embedding推理", "vector搜索", "global搜索", "后处理", "总耗时"])
        self.assertTrue(line.endswith(" | 参数=image_url=https://x.example/a.png, top_k=10, global_fallback=True"), line)

    def test_a_failed_fallback_is_named_in_the_line_of_the_request_that_still_succeeds(self):
        self.vectors.hits = []
        self.food.searchTool.detect.side_effect = requests.Timeout("slow upstream")
        line, record, response = self.post("/api/search/auto/file", files=self.upload(), data={"global_fallback": "true"})
        self.assertEqual(response.json()["fallback"]["status"], "failed")
        self.assertTrue(line.startswith("业务搜图(上传文件) | "), line)
        self.assertEqual(steps(line), ["embedding推理", "vector搜索", "后处理", "global搜索失败", "总耗时"])
        self.assertEqual(record.levelno, logging.INFO)

    def test_batch_search_adds_each_step_up_over_its_images(self):
        line, record, response = self.post("/api/search/batch/urls", json={
            "image_urls": ["https://x.example/a.png", "https://x.example/b.png"],
        })
        self.assertEqual(response.json()["succeeded"], 2)
        self.assertTrue(line.startswith("批量搜图(图片链接) | "), line)
        self.assertEqual(steps(line), ["图像下载", "embedding推理", "vector搜索", "后处理", "总耗时"])
        self.assertTrue(line.endswith(
            " | 参数=scene=general, top_k=10, score_threshold=0.55, images=2, succeeded=2, matched=2, failed=0"
        ), line)
        self.assertEqual((record.name, record.levelno), ("svc.batch_search", logging.INFO))

    def test_batch_search_of_uploads_has_no_download_step_and_warns_about_a_failed_image(self):
        line, record, response = self.post("/api/search/batch/files", files=[
            ("files", ("a.png", b"query", "image/png")), ("files", ("b.png", b"broken", "image/png")),
        ])
        self.assertEqual((response.json()["succeeded"], response.json()["failed"]), (1, 1))
        self.assertTrue(line.startswith("批量搜图(上传文件) | "), line)  # the request did not fail
        self.assertEqual(steps(line), ["embedding推理", "vector搜索", "后处理", "embedding推理失败", "总耗时"])
        self.assertEqual(record.levelno, logging.WARNING)

    def test_global_search_times_the_upstream_search_and_the_filter(self):
        line, record, _ = self.post("/api/search/global/file", files=self.upload())
        self.assertTrue(line.startswith("Global搜图(上传文件) | "), line)
        self.assertEqual(steps(line), ["global搜索", "后处理", "总耗时"])
        self.assertEqual((record.name, record.levelno), ("svc.predict", logging.INFO))

        line, _, _ = self.post("/api/search/global/url", json={"img_url": "https://x.example/a.png"})
        self.assertTrue(line.startswith("Global搜图(图片链接) | "), line)
        self.assertEqual(steps(line), ["图像下载", "global搜索", "后处理", "总耗时"])

    def test_global_search_that_fails_upstream_is_a_warning(self):
        self.food.searchTool.detect.side_effect = requests.Timeout("slow upstream")
        line, record, _ = self.post("/api/search/global/file", status=504, files=self.upload())
        self.assertTrue(line.startswith("Global搜图(上传文件)失败 | "), line)
        self.assertEqual(steps(line), ["global搜索失败", "总耗时"])
        self.assertEqual(record.levelno, logging.WARNING)


if __name__ == "__main__":
    unittest.main()
