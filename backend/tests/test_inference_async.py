"""同步的模型计算必须让请求的事件循环保持可用。

模型桩会等事件循环放行它们，所以这些检查验证的是进展，而不依赖推理速度或很短的
延迟阈值。
"""

import asyncio
import io
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from fastapi import UploadFile
from starlette.datastructures import Headers

from app.api.services import predict, tools
from app.core.errors import AppError


class BlockingInference:
    def __init__(self, *, result=None, error=None):
        self.loop = asyncio.get_running_loop()
        self.entered = asyncio.Event()
        self.release = threading.Event()
        self.thread_id = None
        self.args = None
        self.kwargs = None
        self.result = result
        self.error = error

    def __call__(self, *args, **kwargs):
        self.thread_id = threading.get_ident()
        self.args = args
        self.kwargs = kwargs
        self.loop.call_soon_threadsafe(self.entered.set)
        # 这个截止时间只是防止有问题的实现死锁。
        if not self.release.wait(timeout=3):
            raise RuntimeError("the event loop did not release the model stub")
        if self.error is not None:
            raise self.error
        return self.result


class AsyncInferenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = SimpleNamespace()
        self.runtime = SimpleNamespace(
            max_image_bytes=1024,
            settings=SimpleNamespace(allowed_content_types=("image/png",)),
            require_food_service=Mock(return_value=self.service),
        )
        log_patch = patch.object(predict, "show_text")
        log_patch.start()
        self.addCleanup(log_patch.stop)

    def upload(self):
        upload = UploadFile(
            file=io.BytesIO(b"query-image"),
            filename="query.png",
            headers=Headers({"content-type": "image/png"}),
        )
        self.addAsyncCleanup(upload.close)
        return upload

    async def while_model_is_blocked(self, model, operation):
        """在让推理结束之前，先运行一个不相关的协程。"""
        request = asyncio.create_task(operation)
        try:
            await asyncio.wait_for(model.entered.wait(), timeout=4)
            self.assertNotEqual(model.thread_id, threading.get_ident())
            self.assertFalse(request.done())

            async def health_probe():
                await asyncio.sleep(0)
                return {"ok": True}

            self.assertEqual(await health_probe(), {"ok": True})
            self.assertFalse(model.release.is_set())
            model.release.set()
            return await asyncio.wait_for(request, timeout=4)
        finally:
            # 即使断言失败也要放行后台线程，并吸收失败。
            model.release.set()
            await asyncio.gather(request, return_exceptions=True)

    def predict_request(self, *, search_num=None):
        return predict.run_predict(
            self.runtime,
            b"query-image",
            search_num=search_num,
            source="file",
            machine_info="oven",
            trace_id="trace-async",
            save_res=True,
        )

    async def test_predict_allows_other_requests_and_preserves_arguments(self):
        result = {"results": [{"title": "food"}], "result_count": 1}
        model = BlockingInference(result=result)
        self.service.detect = model

        response = await self.while_model_is_blocked(model, self.predict_request(search_num=7))

        self.assertEqual(response, {"result": result, "trace_id": "trace-async"})
        self.assertEqual(model.args, (b"query-image",))
        self.assertEqual(
            model.kwargs,
            # timer：接口的计时器（这里没有：run_predict 是单独调用的）
            {"search_num": 7, "machine_info": "oven", "trace_id": "trace-async", "save_result": True, "timer": None},
        )

    async def test_predict_maps_upstream_timeout_from_worker(self):
        model = BlockingInference(error=requests.Timeout("slow upstream"))
        self.service.detect = model

        with self.assertRaises(AppError) as raised:
            await self.while_model_is_blocked(model, self.predict_request())

        self.assertEqual(raised.exception.code, "UPSTREAM_TIMEOUT")
        self.assertEqual(raised.exception.http_status, 504)
        self.assertIs(raised.exception.__cause__, model.error)

    async def test_predict_preserves_application_error_from_worker(self):
        error = AppError("NOT_READY", "model is unavailable", http_status=503)
        model = BlockingInference(error=error)
        self.service.detect = model

        with self.assertRaises(AppError) as raised:
            await self.while_model_is_blocked(model, self.predict_request())

        self.assertIs(raised.exception, error)

    async def test_embedding_inference_and_conversion_run_off_the_event_loop(self):
        conversion_threads = []

        def tolist():
            conversion_threads.append(threading.get_ident())
            return [0.1, 0.2, 0.3]

        vector = SimpleNamespace(ndim=1, shape=(3,), tolist=tolist)
        model = BlockingInference(result=vector)
        self.service.embeddingTool = SimpleNamespace(detect=model)

        response = await self.while_model_is_blocked(
            model, tools.embedding(self.runtime, self.upload())
        )

        self.assertTrue(response.ok)
        self.assertEqual(response.filename, "query.png")
        self.assertEqual(response.embedding_dim, 3)
        self.assertEqual(response.embedding, [0.1, 0.2, 0.3])
        self.assertEqual(model.args, (b"query-image",))
        self.assertEqual(model.kwargs, {})
        self.assertEqual(len(conversion_threads), 1)
        self.assertNotEqual(conversion_threads[0], threading.get_ident())

    async def test_segment_invalid_image_is_nonblocking_and_keeps_error_mapping(self):
        model = BlockingInference(error=ValueError("invalid image bytes"))
        self.service.segmentor = SimpleNamespace(detect=model)

        with self.assertRaises(AppError) as raised:
            await self.while_model_is_blocked(
                model, tools.segment(self.runtime, self.upload(), output_vis=True)
            )

        self.assertEqual(model.args, (b"query-image",))
        self.assertEqual(model.kwargs, {"output_vis": True})
        self.assertEqual(raised.exception.code, "INVALID_IMAGE")
        self.assertEqual(raised.exception.http_status, 422)
        self.assertEqual(raised.exception.detail, "invalid image bytes")
        self.assertIs(raised.exception.__cause__, model.error)


if __name__ == "__main__":
    unittest.main()
