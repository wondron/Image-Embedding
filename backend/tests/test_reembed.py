"""Re-embedding one stored image from its stored original (services.retrieval.reembed_image) and
reading objects back from storage (clients.image_storage), over in-memory fakes."""

import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

from app.clients.image_storage import MockImageStorage, S3ImageStorage
from app.core import errors as err
from app.core.config.settings import ImageStorageSettings
from app.services.retrieval.active_model import ActiveModel
from app.services.retrieval.reembed_image import ReembedImageUseCase
from app.services.retrieval.vector_status import expected_payload

IMAGE = "0cf457e2-4a47-9f02-fd4d-34540389f720"
MODEL = ActiveModel(id=3, name="dino", version="v2", collection="c_dino_v2", dim=4, distance="cosine")
BASE = "https://cdn.test/image-embedding"
ROW = {
    "id": IMAGE, "original_url": f"{BASE}/original/{IMAGE}.png", "thumb_url": f"{BASE}/thumb/{IMAGE}.webp",
    "category_id": 12, "scenes": ["general", "oven"], "food_state": "cooked", "label_status": "auto",
    "payload_version": 7,
}


class StorageReadTests(unittest.TestCase):
    def test_the_mock_reads_what_it_stored_then_what_it_mirrored(self):
        with tempfile.TemporaryDirectory() as mirror:
            settings = ImageStorageSettings(public_base_url=BASE, mock_mirror_dir=mirror)
            storage = MockImageStorage(settings)
            url = storage.put_original(IMAGE, b"png-bytes", ext="png")
            self.assertEqual(storage.read_by_url_or_key(url), b"png-bytes")
            # A restarted process keeps only the mirror
            again = MockImageStorage(settings)
            self.assertEqual(again.read_by_url_or_key(url), b"png-bytes")
            self.assertEqual(again.read_by_url_or_key(f"original/{IMAGE}.png"), b"png-bytes")  # a key works too
            Path(mirror, "original", f"{IMAGE}.png").unlink()
            with self.assertRaises(err.AppError) as ctx:
                again.read_by_url_or_key(url)
            self.assertEqual(ctx.exception.code, "IMAGE_FILE_UNAVAILABLE")

    def test_without_a_mirror_nothing_survives_and_foreign_urls_cannot_be_read(self):
        storage = MockImageStorage(ImageStorageSettings(public_base_url=BASE))
        for url in (f"{BASE}/original/{IMAGE}.png", "https://elsewhere.test/a/b.png", ""):
            with self.subTest(url=url), self.assertRaises(err.AppError) as ctx:
                storage.read_by_url_or_key(url)
            self.assertEqual(ctx.exception.code, "IMAGE_FILE_UNAVAILABLE")
            self.assertEqual(ctx.exception.http_status, 502)

    def test_s3_reads_the_object_of_the_url_and_reports_any_failure_as_unavailable(self):
        storage = S3ImageStorage.__new__(S3ImageStorage)  # no boto3 client: a fake one below
        storage.public_base_url, storage.bucket = BASE, "image-embedding"
        body = Mock()
        body.read.return_value = b"original"
        storage._client = Mock()
        storage._client.get_object.return_value = {"Body": body}
        self.assertEqual(storage.read_by_url_or_key(f"{BASE}/original/{IMAGE}.png"), b"original")
        storage._client.get_object.assert_called_once_with(Bucket="image-embedding", Key=f"original/{IMAGE}.png")
        storage._client.get_object.side_effect = RuntimeError("NoSuchKey")
        with self.assertRaises(err.AppError) as ctx:
            storage.read_by_url_or_key(f"{BASE}/original/{IMAGE}.png")
        self.assertEqual(ctx.exception.code, "IMAGE_FILE_UNAVAILABLE")
        self.assertNotIn("NoSuchKey", err.client_detail(ctx.exception))  # the bucket and key stay in the log


class FakeStore:
    """The image row, its ledger rows, and the row lock that a deletion waits for (like
    PgMetadataStore.lock_image_for_vector and enqueue_image_deletion)."""

    def __init__(self, row=None):
        self.row = dict(row or ROW)
        self.ledger = []
        self.row_lock = threading.Lock()

    def get_by_id(self, image_id):
        return dict(self.row) if self.row and str(image_id) == self.row["id"] else None

    @contextmanager
    def lock_image_for_vector(self, image_id, *, model_id):
        with self.row_lock:
            current = self.get_by_id(image_id)
            yield current  # an exception from the caller leaves the ledger as it was
            if current is not None:
                self.ledger.append((model_id, str(image_id), current["payload_version"]))

    def delete(self, image_id):
        """DELETE /api/images/{id}: waits for the row, then the image goes with its ledger rows."""
        with self.row_lock:
            self.row = None
            self.ledger = [entry for entry in self.ledger if entry[1] != str(image_id)]


class ReembedUseCaseTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.storage = Mock()
        self.storage.read_by_url_or_key.return_value = b"original-bytes"
        self.embedding = Mock()
        self.embedding.embed_bytes.return_value = [0.5, 0.5, 0.5, 0.5]
        self.vectors = Mock()

    def run_it(self, storage="default"):
        storage = self.storage if storage == "default" else storage
        return ReembedImageUseCase(self.embedding, self.vectors, self.store, model=MODEL, storage=storage).execute(IMAGE)

    def test_the_stored_original_is_embedded_and_written_with_the_payload_the_database_gives(self):
        self.assertEqual(self.run_it(), {"image_id": IMAGE, "payload_version": 7})
        self.storage.read_by_url_or_key.assert_called_once_with(ROW["original_url"])
        self.embedding.embed_bytes.assert_called_once_with(b"original-bytes")
        (point, vector, payload), _ = self.vectors.upsert.call_args
        self.assertEqual((point, payload), (IMAGE, expected_payload(ROW)))
        self.assertEqual(len(vector), MODEL.dim)
        self.assertEqual(self.store.ledger, [(MODEL.id, IMAGE, 7)])  # the current model's row, at this version

    def test_nothing_is_written_when_a_step_before_the_upsert_fails(self):
        cases = [
            ("storage", lambda: setattr(self.storage.read_by_url_or_key, "side_effect", err.image_file_unavailable("gone")), "IMAGE_FILE_UNAVAILABLE"),
            ("embed", lambda: setattr(self.embedding.embed_bytes, "side_effect", ValueError("not an image")), "INDEX_BAD_IMAGE"),
            ("dim", lambda: setattr(self.embedding.embed_bytes, "return_value", [0.1, 0.2]), "INDEX_BAD_EMBEDDING"),
        ]
        for name, arrange, code in cases:
            with self.subTest(step=name):
                self.setUp()
                arrange()
                with self.assertRaises(err.AppError) as ctx:
                    self.run_it()
                self.assertEqual(ctx.exception.code, code)
                self.vectors.upsert.assert_not_called()
                self.assertEqual(self.store.ledger, [])

    def test_a_failed_upsert_leaves_the_ledger_alone(self):
        self.vectors.upsert.side_effect = ConnectionError("qdrant down")
        with self.assertRaises(err.AppError) as ctx:
            self.run_it()
        self.assertEqual(ctx.exception.code, "INDEX_QDRANT_FAILED")
        self.assertEqual(self.store.ledger, [])

    def collection(self):
        """The current collection as a dict the fake vector store writes to."""
        points = {}

        def upsert(point_id, vector, payload):
            points[str(point_id)] = payload

        self.vectors.upsert.side_effect = upsert
        self.vectors.delete.side_effect = lambda point_id: points.pop(str(point_id), None)
        return points

    def test_an_image_deleted_while_it_is_embedded_gets_no_point_back(self):
        points = self.collection()
        points[IMAGE] = expected_payload(ROW)  # the point the deletion's cleanup is about to remove

        def embed_while_deleted(data):
            self.store.delete(IMAGE)  # the deletion commits ...
            self.vectors.delete(IMAGE)  # ... and its cleanup runs, before the vector is ready
            return [0.5, 0.5, 0.5, 0.5]

        self.embedding.embed_bytes.side_effect = embed_while_deleted
        with self.assertRaises(err.AppError) as ctx:
            self.run_it()
        self.assertEqual(ctx.exception.code, "IMAGE_NOT_FOUND")
        self.vectors.upsert.assert_not_called()
        self.assertEqual(points, {})  # nothing would ever remove a point written now
        self.assertEqual(self.store.ledger, [])

    def test_a_deletion_that_comes_while_the_point_is_written_waits_and_then_removes_it(self):
        points = self.collection()
        deleted = threading.Event()

        def delete_image():  # DELETE /api/images/{id}: the queue row, then the cleanup of the point
            self.store.delete(IMAGE)
            self.vectors.delete(IMAGE)
            deleted.set()

        deletion = threading.Thread(target=delete_image, daemon=True)
        waited = []

        def upsert_while_deleting(point_id, vector, payload):
            deletion.start()
            waited.append(not deleted.wait(0.3))  # the deletion is held at the image row
            points[str(point_id)] = payload

        self.vectors.upsert.side_effect = upsert_while_deleting
        self.assertEqual(self.run_it(), {"image_id": IMAGE, "payload_version": 7})
        self.assertTrue(deleted.wait(5), "the deletion never went on after the write")
        deletion.join(5)
        self.assertEqual(waited, [True])
        self.assertEqual(points, {})  # its cleanup came after the write and took the point out
        self.assertIsNone(self.store.row)
        self.assertEqual(self.store.ledger, [])  # gone with the image

    def test_the_payload_and_version_are_the_image_as_it_is_when_the_point_is_written(self):
        edited = {**ROW, "category_id": 13, "scenes": ["general"], "label_status": "verified", "payload_version": 8}

        def embed_while_edited(data):
            self.store.row = dict(edited)  # an edit committed while the original was embedded
            return [0.5, 0.5, 0.5, 0.5]

        self.embedding.embed_bytes.side_effect = embed_while_edited
        self.assertEqual(self.run_it(), {"image_id": IMAGE, "payload_version": 8})
        (_, _, payload), _ = self.vectors.upsert.call_args
        self.assertEqual(payload, expected_payload(edited))
        self.assertEqual(self.store.ledger, [(MODEL.id, IMAGE, 8)])

    def test_unknown_images_and_missing_storage_or_url(self):
        self.store.row = None
        with self.assertRaises(err.AppError) as ctx:
            self.run_it()
        self.assertEqual(ctx.exception.code, "IMAGE_NOT_FOUND")
        for store_row, storage in (({**ROW, "original_url": None}, self.storage), (ROW, None)):
            with self.subTest(url=store_row["original_url"], storage=storage):
                self.store = FakeStore(store_row)
                with self.assertRaises(err.AppError) as ctx:
                    self.run_it(storage=storage)
                self.assertEqual(ctx.exception.code, "IMAGE_FILE_UNAVAILABLE")
        self.embedding.embed_bytes.assert_not_called()


if __name__ == "__main__":
    unittest.main()
