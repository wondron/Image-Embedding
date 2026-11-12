"""Index transaction boundaries under concurrent requests, without external services."""

import copy
import hashlib
import io
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from uuid import UUID

from PIL import Image

from app.core.errors import AppError
from app.repositories.ports import ImageIndexResult
from app.services.retrieval.active_model import ActiveModel
from app.services.retrieval.index_image import IndexImageUseCase


MODEL = ActiveModel(
    id=3, name="dino", version="v1", collection="index-test", dim=4, distance="cosine"
)
WAIT_SECONDS = 5


def png_bytes():
    output = io.BytesIO()
    Image.new("RGB", (8, 6), (200, 30, 30)).save(output, format="PNG")
    return output.getvalue()


class Embedding:
    def embed_bytes(self, image_bytes):
        return [1.0, 0.0, 0.0, 0.0]


class Storage:
    def put_original(self, image_id, data, *, ext, content_type=None):
        return f"https://storage.example/original/{image_id}.{ext}"

    def put_thumb(self, image_id, data, *, content_type=None):
        return f"https://storage.example/thumb/{image_id}.webp"


class TransactionalMetadata:
    """Stage row and ledger changes behind the image lock until the caller succeeds."""

    def __init__(self):
        self.rows = {}
        self.ledger = {}
        self.deleted = []
        self.guard = threading.Lock()
        self.image_locks = {}
        self.active = {}
        self.attempts = 0
        self.second_attempted = threading.Event()
        self.patch_attempted = threading.Event()

    def image_lock(self, image_id):
        with self.guard:
            return self.image_locks.setdefault(str(image_id), threading.Lock())

    @contextmanager
    def index_image(self, row, *, model_id):
        image_id = str(row["id"])
        with self.guard:
            self.attempts += 1
            if self.attempts == 2:
                self.second_attempted.set()
        with self.image_lock(image_id):
            created = image_id not in self.rows
            image = copy.deepcopy(self.rows.get(image_id) or row)
            image["id"] = image_id
            image["label_status"] = str(image["label_status"])
            image.setdefault("payload_version", 1)
            image.setdefault("scenes", ["general", "oven"])
            with self.guard:
                self.active[threading.get_ident()] = image_id
            try:
                yield ImageIndexResult(image=copy.deepcopy(image), created=created)
                self.rows[image_id] = image
                self.ledger[(model_id, image_id)] = image["payload_version"]
            except BaseException:
                # A timed-out write may have reached the vector store. A prior
                # ledger row survives rollback but must be reconciled again.
                ledger_key = (model_id, image_id)
                if ledger_key in self.ledger:
                    self.ledger[ledger_key] = 0
                raise
            finally:
                with self.guard:
                    self.active.pop(threading.get_ident())

    def patch_image(self, image_id):
        self.patch_attempted.set()
        with self.image_lock(image_id):
            image = self.rows[str(image_id)]
            image.update(
                category_id=9,
                food_state="raw",
                scenes=["general", "grill"],
                payload_version=image["payload_version"] + 1,
            )
            return copy.deepcopy(image)

    def get_by_id(self, image_id):
        raise AssertionError("index must read the transaction snapshot")

    def recompute_image_scenes(self, *args, **kwargs):
        raise AssertionError("membership must be read from the transaction snapshot")

    def insert_image(self, row):
        raise AssertionError("index must use the atomic insert result")

    def upsert_image_embedding(self, **kwargs):
        raise AssertionError("the ledger belongs to the indexing transaction")

    def mark_deleted(self, image_id):
        self.deleted.append(str(image_id))
        raise AssertionError("a failed request must roll back its own transaction")


class Vectors:
    def __init__(self, metadata, *, block_first=False, fail_call=None):
        self.metadata = metadata
        self.block_first = block_first
        self.fail_call = fail_call
        self.calls = []
        self.points = {}
        self.guard = threading.Lock()
        self.entered = threading.Event()
        self.release = threading.Event()

    def upsert(self, image_id, vector, payload):
        image_id = str(image_id)
        with self.metadata.guard:
            if self.metadata.active.get(threading.get_ident()) != image_id:
                raise AssertionError(
                    "vector write must be inside the image transaction"
                )
        with self.guard:
            self.calls.append((image_id, copy.deepcopy(payload)))
            call_number = len(self.calls)
        if self.block_first and call_number == 1:
            self.entered.set()
            if not self.release.wait(WAIT_SECONDS):
                raise TimeoutError("test did not release the first vector write")
        if call_number == self.fail_call:
            raise RuntimeError("vector store unavailable")
        with self.guard:
            self.points[image_id] = copy.deepcopy(payload)


class IndexConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.data = png_bytes()
        self.image_id = str(UUID(bytes=hashlib.sha256(self.data).digest()[:16]))
        self.metadata = TransactionalMetadata()

    def use_case(self, vectors):
        return IndexImageUseCase(
            Embedding(), vectors, self.metadata, model=MODEL, storage=Storage()
        )

    def execute(self, vectors, *, category_id=7):
        return self.use_case(vectors).execute_one(
            image_bytes=self.data,
            filename="query.png",
            category_id=category_id,
            food_state="cooked",
        )

    def test_concurrent_duplicates_have_one_creator_and_keep_its_annotation(self):
        vectors = Vectors(self.metadata, block_first=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.execute, vectors, category_id=7)
            try:
                self.assertTrue(vectors.entered.wait(WAIT_SECONDS))
                second = pool.submit(self.execute, vectors, category_id=9)
                self.assertTrue(self.metadata.second_attempted.wait(WAIT_SECONDS))
                self.assertFalse(second.done())
                self.assertEqual(len(vectors.calls), 1)
            finally:
                vectors.release.set()
            first_result = first.result(timeout=WAIT_SECONDS)
            second_result = second.result(timeout=WAIT_SECONDS)

        self.assertEqual(
            [first_result["created"], second_result["created"]], [True, False]
        )
        self.assertEqual(first_result["id"], second_result["id"])
        self.assertEqual(second_result["category_id"], 7)
        self.assertEqual(len(self.metadata.rows), 1)
        self.assertEqual(self.metadata.ledger, {(MODEL.id, self.image_id): 1})
        self.assertTrue(
            all(payload["category_id"] == 7 for _, payload in vectors.calls)
        )

    def test_failed_creator_rolls_back_before_the_successful_follower_inserts(self):
        vectors = Vectors(self.metadata, block_first=True, fail_call=1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.execute, vectors, category_id=7)
            try:
                self.assertTrue(vectors.entered.wait(WAIT_SECONDS))
                second = pool.submit(self.execute, vectors, category_id=9)
                self.assertTrue(self.metadata.second_attempted.wait(WAIT_SECONDS))
                self.assertFalse(second.done())
            finally:
                vectors.release.set()
            with self.assertRaises(AppError) as raised:
                first.result(timeout=WAIT_SECONDS)
            success = second.result(timeout=WAIT_SECONDS)

        self.assertEqual(raised.exception.code, "INDEX_QDRANT_FAILED")
        self.assertTrue(success["created"])
        self.assertEqual(self.metadata.rows[self.image_id]["category_id"], 9)
        self.assertEqual(self.metadata.ledger, {(MODEL.id, self.image_id): 1})
        self.assertEqual(vectors.points[self.image_id]["category_id"], 9)
        self.assertEqual(self.metadata.deleted, [])

    def test_failed_reupload_keeps_the_image_and_marks_its_ledger_stale(self):
        vectors = Vectors(self.metadata, fail_call=2)
        self.execute(vectors, category_id=7)
        previous_rows = copy.deepcopy(self.metadata.rows)
        previous_points = copy.deepcopy(vectors.points)

        with self.assertRaises(AppError) as raised:
            self.execute(vectors, category_id=9)

        self.assertEqual(raised.exception.code, "INDEX_QDRANT_FAILED")
        self.assertEqual(self.metadata.rows, previous_rows)
        self.assertEqual(self.metadata.ledger, {(MODEL.id, self.image_id): 0})
        self.assertEqual(vectors.points, previous_points)
        self.assertEqual(self.metadata.deleted, [])

    def test_payload_uses_one_snapshot_and_a_later_patch_leaves_the_ledger_stale(self):
        vectors = Vectors(self.metadata, block_first=True)
        self.metadata.rows[self.image_id] = {
            "id": self.image_id,
            "category_id": 7,
            "label_status": "verified",
            "food_state": "cooked",
            "scenes": ["general", "oven"],
            "payload_version": 4,
            "original_url": "https://storage.example/original/stored.png",
            "thumb_url": "https://storage.example/thumb/stored.webp",
            "width": 8,
            "height": 6,
            "description": "stored annotation",
            "tags": ["stored"],
        }
        with ThreadPoolExecutor(max_workers=2) as pool:
            indexing = pool.submit(self.execute, vectors, category_id=9)
            try:
                self.assertTrue(vectors.entered.wait(WAIT_SECONDS))
                patching = pool.submit(self.metadata.patch_image, self.image_id)
                self.assertTrue(self.metadata.patch_attempted.wait(WAIT_SECONDS))
                self.assertFalse(patching.done())
            finally:
                vectors.release.set()
            response = indexing.result(timeout=WAIT_SECONDS)
            patched = patching.result(timeout=WAIT_SECONDS)

        self.assertFalse(response["created"])
        self.assertEqual(response["payload_version"], 4)
        self.assertEqual(response["category_id"], 7)
        self.assertEqual(response["scenes"], ["general", "oven"])
        self.assertEqual(response["description"], "stored annotation")
        self.assertEqual(response["tags"], ["stored"])
        self.assertEqual(
            vectors.points[self.image_id],
            {
                "category_id": 7,
                "label_status": "verified",
                "food_state": "cooked",
                "scenes": ["general", "oven"],
            },
        )
        self.assertEqual(patched["payload_version"], 5)
        self.assertEqual(self.metadata.ledger[(MODEL.id, self.image_id)], 4)
        self.assertLess(
            self.metadata.ledger[(MODEL.id, self.image_id)],
            self.metadata.rows[self.image_id]["payload_version"],
        )


if __name__ == "__main__":
    unittest.main()
