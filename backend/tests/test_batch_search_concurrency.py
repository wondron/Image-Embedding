"""search_images, the part of the batch search other flows reuse: several query images searched at
the same time, at most `concurrency` of them, answered in the order they were given.

The search stub waits until the test releases its image, so these checks follow what runs at the
same time without depending on how fast anything is.
"""

import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from app.api.services.batch_search import QueryImage, search_images
from app.core import errors as err
from app.utils.timer import timer_tool

GUARD_S = 5.0  # deadlock guard of the stub; the test's own waits give up before it


def step_names(timer):
    """The step names of a timer's summary, in order, 总耗时 last."""
    return [part.rsplit(" ", 1)[0] for part in timer.summary().split(" | ")]


class HeldSearch:
    """search_uc.execute that tells the event loop when it starts, waits until the test releases
    its image, then times one step on the timer it is given (as the real search does) and tells
    the event loop it is done."""

    def __init__(self, test, loop, images, *, broken=()):
        self.loop = loop
        self.started = asyncio.Queue()
        self.finished = asyncio.Queue()
        self.gates = {image: threading.Event() for image in images}
        self.broken = set(broken)
        # a test that fails half way must not leave threads waiting
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
        self.assertEqual(reads, ["a", "b"])  # the third waits for a slot, not even read yet
        search.gates[b"b"].set()
        self.assertEqual(await search.next_started(), b"c")  # it takes the slot b gave back
        search.gates[b"a"].set()
        search.gates[b"c"].set()
        entries = await batch
        # in the order given, though b finished first
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
        search.gates[b"crop-1"].set()  # the second crop fails first...
        self.assertEqual(await search.next_finished(), b"crop-1")
        search.gates[b"crop-0"].set()  # ...and only then is the first one searched
        entries = await batch
        self.assertEqual(
            [(entry["input"], entry["ok"], entry.get("error")) for entry in entries],
            [("box-0", True, None), ("box-1", False, "SEARCH_BAD_IMAGE")],
        )
        # the first crop's steps come first, whichever finished first; nothing was downloaded
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
