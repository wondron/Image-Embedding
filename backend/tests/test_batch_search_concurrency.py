"""search_images，多图搜索中被其他流程复用的部分：多张查询图同时搜索，最多 `concurrency` 张，
按传入的顺序返回。

搜索桩会一直等到测试放行它的图片，所以这些检查跟踪的是哪些在同时运行，而不依赖
任何东西的快慢。
"""

import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from app.api.services.batch_search import QueryImage, search_images
from app.core import errors as err
from app.utils.timer import timer_tool

GUARD_S = 5.0  # 桩的防死锁保护；测试自己的等待会在它之前放弃


def step_names(timer):
    """The step names of a timer's summary, in order, 总耗时 last."""
    return [part.rsplit(" ", 1)[0] for part in timer.summary().split(" | ")]


class HeldSearch:
    """search_uc.execute 的替身：开始时通知事件循环，等测试放行它的图片，然后在传给它的计时器上
    计时一步（和真实的搜索一样），并通知事件循环它完成了。
    """

    def __init__(self, test, loop, images, *, broken=()):
        self.loop = loop
        self.started = asyncio.Queue()
        self.finished = asyncio.Queue()
        self.gates = {image: threading.Event() for image in images}
        self.broken = set(broken)
        # 中途失败的测试不能留下还在等待的线程
        test.addCleanup(lambda: [gate.set() for gate in self.gates.values()])

    def __call__(self, **kwargs):
        image = kwargs["image_bytes"]
        self.loop.call_soon_threadsafe(self.started.put_nowait, image)
        if not self.gates[image].wait(timeout=GUARD_S):
            raise RuntimeError("the test never released this search")
        try:
            with kwargs["timer"].step("embedding推理"):
                if image in self.broken:
                    raise err.search_bad_image("cannot decode")
            return {"source": "empty", "items": [], "message": "no matches"}
        finally:
            self.loop.call_soon_threadsafe(self.finished.put_nowait, image)

    async def next_started(self):
        return await asyncio.wait_for(self.started.get(), timeout=GUARD_S - 2)

    async def next_finished(self):
        return await asyncio.wait_for(self.finished.get(), timeout=GUARD_S - 2)

    def stack(self):
        return SimpleNamespace(search_uc=SimpleNamespace(execute=self))


class SearchImagesTests(unittest.IsolatedAsyncioTestCase):
    async def test_at_most_concurrency_images_are_read_and_searched_at_a_time(self):
        search = HeldSearch(self, asyncio.get_running_loop(), [b"a", b"b", b"c"])
        reads = []

        def query(label):
            async def read():
                reads.append(label)
                return label.encode()

            return QueryImage(label=label, read=read)

        batch = asyncio.create_task(search_images(
            search.stack(), [query("a"), query("b"), query("c")],
            scene="general", top_k=10, score_threshold=None, trace_id="t-1", concurrency=2,
        ))
        self.assertEqual({await search.next_started(), await search.next_started()}, {b"a", b"b"})
        self.assertEqual(reads, ["a", "b"])  # 第三张在等名额，还没有读取
        search.gates[b"b"].set()
        self.assertEqual(await search.next_started(), b"c")  # 它拿到了 b 让出来的名额
        search.gates[b"a"].set()
        search.gates[b"c"].set()
        entries = await batch
        # 按传入的顺序，虽然 b 先完成
        self.assertEqual([(entry["index"], entry["input"]) for entry in entries], [(0, "a"), (1, "b"), (2, "c")])
        self.assertTrue(all(entry["ok"] for entry in entries))

    async def test_crops_in_memory_keep_their_order_in_the_entries_and_in_the_timing(self):
        crops = [b"crop-0", b"crop-1"]
        search = HeldSearch(self, asyncio.get_running_loop(), crops, broken={b"crop-1"})
        timer = timer_tool()
        batch = asyncio.create_task(search_images(
            search.stack(), [QueryImage.from_bytes(f"box-{index}", crop) for index, crop in enumerate(crops)],
            scene="general", top_k=5, score_threshold=0.6, trace_id="t-2", timer=timer,
        ))
        self.assertEqual({await search.next_started(), await search.next_started()}, set(crops))
        search.gates[b"crop-1"].set()  # 第二张裁剪图先失败……
        self.assertEqual(await search.next_finished(), b"crop-1")
        search.gates[b"crop-0"].set()  # ……然后才搜索第一张
        entries = await batch
        self.assertEqual(
            [(entry["input"], entry["ok"], entry.get("error")) for entry in entries],
            [("box-0", True, None), ("box-1", False, "SEARCH_BAD_IMAGE")],
        )
        # 不管谁先完成，第一张裁剪图的步骤排在前面；没有下载任何东西
        self.assertEqual(step_names(timer), ["embedding推理", "embedding推理失败", "总耗时"])

    async def test_the_images_may_come_from_a_generator(self):
        execute = Mock(return_value={"source": "empty", "items": []})
        crops = (QueryImage.from_bytes(f"box-{index}", b"crop") for index in range(3))
        found = await search_images(
            SimpleNamespace(search_uc=SimpleNamespace(execute=execute)), crops,
            scene=None, top_k=10, score_threshold=None, trace_id="t-4",
        )
        self.assertEqual([entry["input"] for entry in found], ["box-0", "box-1", "box-2"])

    async def test_no_image_no_entry(self):
        execute = Mock()
        found = await search_images(
            SimpleNamespace(search_uc=SimpleNamespace(execute=execute)), [],
            scene=None, top_k=10, score_threshold=None, trace_id="t-3",
        )
        self.assertEqual(found, [])
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
