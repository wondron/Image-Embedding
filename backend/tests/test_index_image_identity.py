"""IndexImageUseCase vs the images identity rule: same bytes -> same row, first upload wins."""

import hashlib
import io
import tempfile
import unittest
import uuid

from PIL import Image as PILImage

from app.repositories.ports import VECTOR_DIM
from app.services.retrieval.errors import RetrievalError
from app.services.retrieval.index_image import IndexImageUseCase


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
        self.categories = categories or {}

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
                "food_state": str(row["food_state"]) if row["food_state"] else None,
                "label_status": str(row["label_status"]),
                "source_type": str(row["source_type"]),
                "license": str(row["license"]),
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
            FakeEmbedding(), vectors or self.vectors, self.meta, indexed_dir=self.tmp.name
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
        self.use_case().execute_one(image_bytes=data)  # first: unlabeled
        out = self.use_case().execute_one(image_bytes=data, category="dish.steak", food_state="raw")
        _, payload = self.vectors.upserts[-1]
        self.assertIsNone(payload["category_id"])
        self.assertEqual(payload["label_status"], "unlabeled")
        self.assertIsNone(payload["food_state"])
        self.assertEqual(out["label_status"], "unlabeled")

    def test_failed_upsert_on_reupload_keeps_the_existing_image(self):
        data = _png()
        self.use_case().execute_one(image_bytes=data)
        with self.assertRaises(RetrievalError) as ctx:
            self.use_case(FakeVectors(fail=True)).execute_one(image_bytes=data)
        self.assertEqual(ctx.exception.code, "INDEX_QDRANT_FAILED")
        self.assertEqual(self.meta.deleted, [])
        self.assertEqual(len(self.meta.rows), 1)

    def test_failed_upsert_on_new_image_rolls_back(self):
        with self.assertRaises(RetrievalError):
            self.use_case(FakeVectors(fail=True)).execute_one(image_bytes=_png((1, 2, 3)))
        self.assertEqual(len(self.meta.deleted), 1)
        self.assertEqual(self.meta.rows, {})

    def test_bad_enum_value_is_a_validation_error_before_any_write(self):
        with self.assertRaises(RetrievalError) as ctx:
            self.use_case().execute_one(image_bytes=_png(), source_type="scraped")
        self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")
        self.assertEqual(self.meta.rows, {})

    def test_labeled_status_without_any_category_is_rejected(self):
        with self.assertRaises(RetrievalError) as ctx:
            self.use_case().execute_one(image_bytes=_png(), label_status="verified")
        self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")
        self.assertEqual(self.meta.rows, {})

    def test_unknown_category_code_is_stored_unlabeled(self):
        out = self.use_case().execute_one(image_bytes=_png(), category="no.such.code", label_status="auto")
        self.assertEqual(out["label_status"], "unlabeled")
        self.assertIsNone(out["category_id"])


if __name__ == "__main__":
    unittest.main()
