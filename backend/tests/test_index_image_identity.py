"""IndexImageUseCase vs the images identity rule: same bytes -> same row, first upload wins."""

import hashlib
import io
import tempfile
import threading
import unittest
import uuid
from contextlib import contextmanager
from copy import deepcopy

from PIL import Image as PILImage

from app.repositories.ports import ImageIndexResult, VECTOR_DIM
from app.services.retrieval.active_model import ActiveModel
from app.core.errors import AppError
from app.core.rules.food_fields import FOOD_FIELDS
from app.services.retrieval.index_image import IndexImageUseCase

MODEL = ActiveModel(id=3, name="dino", version="v1", collection="c_dino_v1", dim=VECTOR_DIM, distance="cosine")


def _png(color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", (8, 6), color).save(buf, format="PNG")
    return buf.getvalue()


class FakeEmbedding:
    def embed_bytes(self, image_bytes):
        return [1.0] * VECTOR_DIM

    def embed_url(self, image_url):
        raise NotImplementedError


class FakeVectors:
    def __init__(self, fail=False):
        self.fail = fail
        self.upserts = []

    def upsert(self, point_id, vector, payload):
        if self.fail:
            raise RuntimeError("qdrant down")
        self.upserts.append((str(point_id), payload))

    def search(self, *a, **k):
        return []

    def delete(self, point_id):
        pass


class FakeMetadata:
    """Just enough of PgMetadataStore: insert keeps the first row per id."""

    def __init__(self, categories=None):
        self.rows = {}
        self.deleted = []
        self.ledger = {}
        self.ledger_fails = False
        self.categories = categories or {}
        self.tasks = []  # index_tasks rows, in write order
        self.tasks_fail = False
        self._write_lock = threading.RLock()

    @contextmanager
    def index_image(self, row, *, model_id):
        with self._write_lock:
            previous_rows, previous_ledger = deepcopy(self.rows), dict(self.ledger)
            iid = uuid.UUID(str(row["id"]))
            created = iid not in self.rows
            attempted_vector = False
            try:
                self.insert_image(row)
                self.recompute_image_scenes(iid, bump_version=not created)
                snapshot = self.get_by_id(iid)
                self.upsert_image_embedding(
                    model_id=model_id, image_id=iid,
                    synced_version=snapshot["payload_version"],
                )
                attempted_vector = True
                yield ImageIndexResult(image=snapshot, created=created)
            except Exception:
                self.rows, self.ledger = previous_rows, previous_ledger
                key = (model_id, str(iid))
                if attempted_vector and key in self.ledger:
                    self.ledger[key] = 0
                raise

    def record_index_tasks(self, rows):
        if self.tasks_fail:
            raise AppError("INDEX_POSTGRES_FAILED", "index_tasks down", http_status=502)
        self.tasks.extend(dict(row) for row in rows)

    def upsert_image_embedding(self, *, model_id, image_id, synced_version=None):
        if self.ledger_fails:
            raise AppError("INDEX_POSTGRES_FAILED", "ledger down", http_status=502)
        self.ledger[(model_id, str(image_id))] = synced_version

    def get_category_id_by_code(self, code):
        return self.categories.get(code)

    def insert_image(self, row):
        iid = uuid.UUID(str(row["id"]))
        if iid not in self.rows:
            self.rows[iid] = {
                "id": str(iid),
                "original_url": row["original_url"],
                "thumb_url": row["thumb_url"],
                "width": row["width"],
                "height": row["height"],
                "category_id": row["category_id"],
                **{key: row.get(key) for key in FOOD_FIELDS},  # image_foods, LEFT JOINed on read
                "label_status": str(row["label_status"]),
                "source_type": str(row["source_type"]),
                "license": str(row["license"]),
                "description": row.get("description", ""),
                "tags": row.get("tags", []),
                "payload_version": 1,
            }
        return iid

    def recompute_image_scenes(self, image_id, *, bump_version=True):
        return {"scene_codes": ["general"], "payload_version": 1}

    def get_by_id(self, image_id):
        row = self.rows.get(uuid.UUID(str(image_id)))
        return dict(row, scenes=["general"]) if row else None

    def mark_deleted(self, image_id):
        self.deleted.append(str(image_id))
        self.rows.pop(uuid.UUID(str(image_id)), None)


class IndexImageIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.meta = FakeMetadata(categories={"dish.steak": 7})
        self.vectors = FakeVectors()

    def use_case(self, vectors=None):
        return IndexImageUseCase(
            FakeEmbedding(), vectors or self.vectors, self.meta, model=MODEL, indexed_dir=self.tmp.name
        )

    def test_id_is_sha256_prefix_and_label_follows_category(self):
        data = _png()
        out = self.use_case().execute_one(image_bytes=data, category="dish.steak", food_state="cooked")
        self.assertEqual(out["id"], str(uuid.UUID(bytes=hashlib.sha256(data).digest()[:16])))
        self.assertEqual(out["label_status"], "auto")
        _, payload = self.vectors.upserts[-1]
        self.assertEqual(
            payload,
            {"category_id": 7, "scenes": ["general"], "food_state": "cooked", "label_status": "auto"},
        )

    def test_reupload_pushes_the_stored_row_not_the_request(self):
        data = _png()
        first = self.use_case().execute_one(image_bytes=data)  # first: unlabeled
        self.assertTrue(first["created"])
        out = self.use_case().execute_one(image_bytes=data, category="dish.steak", food_state="raw")
        self.assertFalse(out["created"])  # the client is told its labels were not written
        _, payload = self.vectors.upserts[-1]
        self.assertIsNone(payload["category_id"])
        self.assertEqual(payload["label_status"], "unlabeled")
        self.assertIsNone(payload["food_state"])
        self.assertEqual(out["label_status"], "unlabeled")

    def test_failed_upsert_on_reupload_keeps_the_existing_image(self):
        data = _png()
        self.use_case().execute_one(image_bytes=data)
        with self.assertRaises(AppError) as ctx:
            self.use_case(FakeVectors(fail=True)).execute_one(image_bytes=data)
        self.assertEqual(ctx.exception.code, "INDEX_QDRANT_FAILED")
        self.assertEqual(self.meta.deleted, [])
        self.assertEqual(len(self.meta.rows), 1)

    def test_failed_upsert_on_new_image_rolls_back_without_compensation(self):
        with self.assertRaises(AppError):
            self.use_case(FakeVectors(fail=True)).execute_one(image_bytes=_png((1, 2, 3)))
        self.assertEqual(self.meta.deleted, [])
        self.assertEqual(self.meta.rows, {})

    def test_bad_enum_value_is_a_validation_error_before_any_write(self):
        with self.assertRaises(AppError) as ctx:
            self.use_case().execute_one(image_bytes=_png(), source_type="scraped")
        self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")
        self.assertEqual(self.meta.rows, {})

    def test_labeled_status_without_any_category_is_rejected(self):
        with self.assertRaises(AppError) as ctx:
            self.use_case().execute_one(image_bytes=_png(), label_status="verified")
        self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")
        self.assertEqual(self.meta.rows, {})

    def test_ledger_records_the_pushed_payload_version_for_the_active_model(self):
        out = self.use_case().execute_one(image_bytes=_png())
        self.assertEqual(self.meta.ledger, {(MODEL.id, out["id"]): 1})

    def test_ledger_failure_rolls_back_before_the_vector_write(self):
        self.meta.ledger_fails = True
        with self.assertRaises(AppError) as ctx:
            self.use_case().execute_one(image_bytes=_png())
        self.assertEqual(ctx.exception.code, "INDEX_POSTGRES_FAILED")
        self.assertEqual(self.meta.deleted, [])
        self.assertEqual(self.meta.rows, {})
        self.assertEqual(self.vectors.upserts, [])

    def test_vector_of_wrong_dim_for_the_active_model_is_rejected(self):
        small = ActiveModel(id=4, name="m", version="v", collection="c", dim=8, distance="cosine")
        uc = IndexImageUseCase(FakeEmbedding(), self.vectors, self.meta, model=small, indexed_dir=self.tmp.name)
        with self.assertRaises(AppError) as ctx:
            uc.execute_one(image_bytes=_png())
        self.assertEqual(ctx.exception.code, "INDEX_BAD_EMBEDDING")
        self.assertEqual(self.vectors.upserts, [])
        self.assertEqual(self.meta.rows, {})  # embedding runs before any write

    def test_description_defaults_to_empty_and_is_stored_stripped(self):
        self.assertEqual(self.use_case().execute_one(image_bytes=_png((9, 9, 9)))["description"], "")
        out = self.use_case().execute_one(image_bytes=_png((8, 8, 8)), description="  红烧肉，酱色  ")
        self.assertEqual(out["description"], "红烧肉，酱色")
        self.assertEqual(self.meta.rows[uuid.UUID(out["id"])]["description"], "红烧肉，酱色")

    def test_reupload_keeps_the_first_description(self):
        data = _png()
        self.use_case().execute_one(image_bytes=data, description="first")
        out = self.use_case().execute_one(image_bytes=data, description="second")
        self.assertEqual(out["description"], "first")

    def test_too_long_description_is_rejected_before_any_write(self):
        with self.assertRaises(AppError) as ctx:
            self.use_case().execute_one(image_bytes=_png(), description="x" * 1001)
        self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")
        self.assertEqual((self.meta.rows, self.vectors.upserts), ({}, []))

    def test_tags_default_to_empty_and_are_stored_normalized(self):
        self.assertEqual(self.use_case().execute_one(image_bytes=_png((5, 5, 5)))["tags"], [])
        out = self.use_case().execute_one(
            image_bytes=_png((4, 4, 4)), tags=[" 红薯叶 ", "带皮", "", "红薯叶", "  "]
        )
        self.assertEqual(out["tags"], ["红薯叶", "带皮"])  # stripped, blanks dropped, first duplicate kept
        self.assertEqual(self.meta.rows[uuid.UUID(out["id"])]["tags"], ["红薯叶", "带皮"])
        _, payload = self.vectors.upserts[-1]  # tags stay in PG, like description
        self.assertNotIn("tags", payload)

    def test_reupload_keeps_the_first_tags(self):
        data = _png()
        self.use_case().execute_one(image_bytes=data, tags=["first"])
        out = self.use_case().execute_one(image_bytes=data, tags=["second"])
        self.assertEqual(out["tags"], ["first"])

    def test_bad_tags_are_rejected_before_any_write(self):
        for bad in ("红薯叶", ["ok", 3], [None], ["x" * 65], [f"t{i}" for i in range(33)]):
            with self.subTest(tags=bad):
                with self.assertRaises(AppError) as ctx:
                    self.use_case().execute_one(image_bytes=_png(), tags=bad)
                self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")
        self.assertEqual((self.meta.rows, self.vectors.upserts), ({}, []))

    def test_food_annotation_is_validated_stored_and_returned(self):
        out = self.use_case().execute_one(
            image_bytes=_png((7, 7, 7)), food_state="raw", food_processing="sliced", food_count=4,
            total_mass_g="350", water_mass_g=0, main_ingredient_mass_g=300,
        )
        expected = {
            "food_state": "raw", "food_processing": "sliced", "food_count": 4,
            "total_mass_g": 350, "water_mass_g": 0, "main_ingredient_mass_g": 300,
        }
        self.assertEqual({key: out[key] for key in FOOD_FIELDS}, expected)
        stored = self.meta.rows[uuid.UUID(out["id"])]
        self.assertEqual({key: stored[key] for key in FOOD_FIELDS}, expected)
        _, payload = self.vectors.upserts[-1]  # only food_state goes to Qdrant
        self.assertEqual(set(payload), {"category_id", "scenes", "food_state", "label_status"})

    def test_no_food_annotation_is_fine(self):
        out = self.use_case().execute_one(image_bytes=_png((6, 6, 6)))
        self.assertEqual({key: out[key] for key in FOOD_FIELDS}, dict.fromkeys(FOOD_FIELDS))

    def test_bad_food_field_is_rejected_before_any_write(self):
        for kwargs in ({"total_mass_g": 0}, {"water_mass_g": -1}, {"food_count": "two"},
                       {"main_ingredient_mass_g": 1.5}, {"food_processing": "minced"}):
            with self.subTest(**kwargs):
                with self.assertRaises(AppError) as ctx:
                    self.use_case().execute_one(image_bytes=_png(), **kwargs)
                self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")
        self.assertEqual((self.meta.rows, self.vectors.upserts), ({}, []))

    def test_reupload_keeps_the_first_food_annotation(self):
        data = _png()
        self.use_case().execute_one(image_bytes=data, total_mass_g=100)
        out = self.use_case().execute_one(image_bytes=data, total_mass_g=999, food_count=2)
        self.assertEqual((out["total_mass_g"], out["food_count"]), (100, None))

    def test_unknown_category_code_is_stored_unlabeled(self):
        out = self.use_case().execute_one(image_bytes=_png(), category="no.such.code", label_status="auto")
        self.assertEqual(out["label_status"], "unlabeled")
        self.assertIsNone(out["category_id"])


class IndexTaskRecordTests(unittest.TestCase):
    """Each file of a batch / single upload leaves one index_tasks record (task center)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.meta = FakeMetadata(categories={"dish.steak": 7})

    def use_case(self, vectors=None):
        return IndexImageUseCase(
            FakeEmbedding(), vectors or FakeVectors(), self.meta, model=MODEL, indexed_dir=self.tmp.name
        )

    def test_batch_records_each_file_in_order_with_the_trace_id(self):
        out = self.use_case().execute_batch(
            [{"image_bytes": _png((1, 1, 1)), "filename": "ok.png"}, {"image_bytes": b"junk", "filename": "bad.png"}],
            trace_id="t-1",
        )
        self.assertEqual((len(out["indexed"]), len(out["failed"])), (1, 1))
        ok, bad = self.meta.tasks
        self.assertEqual(
            (ok["filename"], ok["status"], ok["image_id"], ok["new_image"], ok["trace_id"]),
            ("ok.png", "succeeded", out["indexed"][0]["id"], True, "t-1"),
        )
        self.assertNotIn("error_code", ok)
        self.assertEqual(
            (bad["filename"], bad["status"], bad["error_code"], bad["error_detail"], bad["trace_id"]),
            ("bad.png", "failed", "INDEX_BAD_IMAGE", "cannot decode image", "t-1"),
        )
        self.assertNotIn("image_id", bad)
        for row in self.meta.tasks:
            self.assertIsInstance(row["duration_ms"], int)
            self.assertGreaterEqual(row["duration_ms"], 0)

    def test_the_same_bytes_again_are_recorded_as_not_new(self):
        data = _png((2, 2, 2))
        self.use_case().execute_batch([{"image_bytes": data, "filename": "a.png"}])
        self.use_case().execute_batch([{"image_bytes": data, "filename": "again.png"}])
        self.assertEqual([row["new_image"] for row in self.meta.tasks], [True, False])
        self.assertEqual(len({row["image_id"] for row in self.meta.tasks}), 1)

    def test_an_internal_failure_is_recorded_with_its_public_text_only(self):
        with self.assertLogs("retrieval.index", level="ERROR"):
            out = self.use_case(FakeVectors(fail=True)).execute_batch(
                [{"image_bytes": _png((3, 3, 3)), "filename": "q.png"}]
            )
        (row,) = self.meta.tasks
        self.assertEqual((row["error_code"], row["error_detail"]), ("INDEX_QDRANT_FAILED", "vector store write failed"))
        self.assertEqual(out["failed"][0]["detail"], "vector store write failed")  # same text as the response
        self.assertNotIn("qdrant down", repr(self.meta.tasks))

    def test_execute_file_records_and_still_raises_its_failure(self):
        uc = self.use_case()
        out = uc.execute_file({"image_bytes": _png((4, 4, 4)), "filename": "one.png"}, trace_id="t-2")
        with self.assertRaises(AppError) as caught:
            uc.execute_file({"image_bytes": b"junk", "filename": "junk.png"}, trace_id="t-3")
        self.assertEqual(caught.exception.code, "INDEX_BAD_IMAGE")
        self.assertEqual(
            [(r["filename"], r["status"], r["trace_id"]) for r in self.meta.tasks],
            [("one.png", "succeeded", "t-2"), ("junk.png", "failed", "t-3")],
        )
        self.assertEqual(self.meta.tasks[0]["image_id"], out["id"])

    def test_a_record_that_cannot_be_written_does_not_change_the_index_result(self):
        self.meta.tasks_fail = True
        with self.assertLogs("retrieval.index", level="ERROR") as logs:
            out = self.use_case().execute_batch([{"image_bytes": _png((5, 5, 5)), "filename": "kept.png"}])
            single = self.use_case().execute_file({"image_bytes": _png((6, 6, 6)), "filename": "kept2.png"})
        self.assertEqual(len(out["indexed"]), 1)
        self.assertTrue(single["id"])
        self.assertEqual(len(self.meta.rows), 2)  # both images stored
        self.assertTrue(any("index task record not written" in line for line in logs.output))

    def test_execute_one_records_nothing(self):
        self.use_case().execute_one(image_bytes=_png((7, 7, 7)))
        self.assertEqual(self.meta.tasks, [])


if __name__ == "__main__":
    unittest.main()
