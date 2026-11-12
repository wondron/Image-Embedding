"""Indexing transaction boundaries over in-memory SQL, without PostgreSQL or vectors."""

import json
import unittest
import uuid
from contextlib import contextmanager
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, text

from app.core.errors import AppError
from app.repositories import images
from app.services.retrieval import index_image
from app.services.retrieval.active_model import ActiveModel


class SqliteIndexStore(images.PgImagesMixin):
    """Use real SQL transactions while replacing PostgreSQL-specific statements."""

    def __init__(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        self.scenes = ["general"]
        self.ledger_error = None
        self.events = []
        self.mark_deleted = Mock()
        self.enqueue_image_deletion = Mock()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE images (id TEXT PRIMARY KEY, payload_version INTEGER NOT NULL, "
                    "label_status TEXT NOT NULL, scenes TEXT NOT NULL)"
                )
            )
            conn.execute(
                text(
                    "CREATE TABLE image_embeddings (image_id TEXT PRIMARY KEY, model_id INTEGER, "
                    "synced_version INTEGER)"
                )
            )

    def ensure_schema(self):
        pass

    def _get_engine(self):
        return self.engine

    def insert(self, conn, values, food):
        row = conn.execute(
            text(
                "INSERT INTO images VALUES (:id, 1, :label_status, '[]') "
                "ON CONFLICT DO NOTHING RETURNING id"
            ),
            values,
        ).fetchone()
        return uuid.UUID(values["id"]), row is not None

    def lock(self, conn, uid):
        return dict(
            conn.execute(text("SELECT * FROM images WHERE id = :id"), {"id": uid})
            .mappings()
            .one()
        )

    def recompute(self, conn, rows, *, bump):
        image = rows[0]
        changed = json.loads(image["scenes"]) != self.scenes
        version = image["payload_version"] + int(changed and bump)
        conn.execute(
            text(
                "UPDATE images SET scenes = :scenes, payload_version = :version WHERE id = :id"
            ),
            {"scenes": json.dumps(self.scenes), "version": version, "id": image["id"]},
        )

    def _image_detail(self, conn, uid):
        image = self.lock(conn, uid)
        image["scenes"] = json.loads(image["scenes"])
        return image

    def write_ledger(self, conn, *, model_id, image_id, synced_version):
        self.events.append("ledger")
        if self.ledger_error:
            raise self.ledger_error
        conn.execute(
            text(
                "INSERT INTO image_embeddings VALUES (:id, :model, :version) "
                "ON CONFLICT (image_id) DO UPDATE SET synced_version = excluded.synced_version"
            ),
            {"id": str(image_id), "model": model_id, "version": synced_version},
        )

    def _mark_index_embedding_stale(self, *, model_id, image_id):
        self.events.append("stale")
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE image_embeddings SET synced_version = 0 "
                    "WHERE model_id = :model AND image_id = :id"
                ),
                {"model": model_id, "id": str(image_id)},
            )

    def state(self):
        with self.engine.connect() as conn:
            return (
                [
                    dict(row)
                    for row in conn.execute(text("SELECT * FROM images")).mappings()
                ],
                [
                    dict(row)
                    for row in conn.execute(
                        text("SELECT * FROM image_embeddings")
                    ).mappings()
                ],
            )


class IndexRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.store = SqliteIndexStore()
        self.addCleanup(self.store.engine.dispose)
        self.row = {"id": str(uuid.uuid4()), "label_status": "unlabeled"}
        for name, replacement in (
            ("_prepare_image_insert", lambda row: (dict(row), {})),
            ("_insert_image", self.store.insert),
            ("lock_catalog_for_read", lambda conn: None),
            ("_lock_image", self.store.lock),
            ("_recompute", self.store.recompute),
            ("_upsert_image_embedding", self.store.write_ledger),
        ):
            patcher = patch.object(images, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def index(self):
        return self.store.index_image(self.row, model_id=3)

    def test_success_commits_the_image_and_its_exact_snapshot_version(self):
        with self.index() as result:
            self.assertTrue(result.created)
            self.assertEqual(result.image["scenes"], ["general"])
            self.assertEqual(result.image["payload_version"], 1)
            self.assertEqual(self.store.events, ["ledger"])
            self.store.events.append("vector")

        rows, ledger = self.store.state()
        self.assertEqual(len(rows), 1)
        self.assertEqual(ledger[0]["synced_version"], 1)
        self.assertEqual(self.store.events, ["ledger", "vector"])

    def test_duplicate_keeps_first_labels_and_versions_new_scene_membership(self):
        with self.index():
            pass
        self.row["label_status"] = "auto"
        self.store.scenes = ["general", "oven"]
        with self.index() as result:
            self.assertFalse(result.created)
            self.assertEqual(result.image["label_status"], "unlabeled")
            self.assertEqual(result.image["scenes"], ["general", "oven"])
            self.assertEqual(result.image["payload_version"], 2)

        rows, ledger = self.store.state()
        self.assertEqual(len(rows), 1)
        self.assertEqual(ledger[0]["synced_version"], 2)

    def test_failed_vector_write_rolls_back_a_new_image_without_deleting(self):
        error = RuntimeError("uncertain vector write")
        with self.assertRaises(RuntimeError) as raised:
            with self.index():
                raise error
        self.assertIs(raised.exception, error)
        self.assertEqual(self.store.state(), ([], []))
        self.assertEqual(self.store.events, ["ledger", "stale"])
        self.store.mark_deleted.assert_not_called()
        self.store.enqueue_image_deletion.assert_not_called()

    def test_failed_duplicate_preserves_the_image_and_marks_its_ledger_stale(self):
        with self.index():
            pass
        self.store.scenes = ["general", "oven"]
        with self.assertRaises(ValueError):
            with self.index():
                raise ValueError("vector timeout")

        rows, ledger = self.store.state()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["payload_version"], 1)
        self.assertEqual(json.loads(rows[0]["scenes"]), ["general"])
        self.assertEqual(ledger[0]["synced_version"], 0)
        self.store.mark_deleted.assert_not_called()

    def test_ledger_failure_rolls_back_before_any_external_write_is_attempted(self):
        self.store.ledger_error = RuntimeError("ledger unavailable")
        with self.assertRaises(AppError) as raised:
            with self.index():
                self.fail("the vector write must not begin after ledger SQL failed")

        self.assertEqual(raised.exception.code, "INDEX_POSTGRES_FAILED")
        self.assertEqual(self.store.state(), ([], []))
        self.assertEqual(self.store.events, ["ledger"])

    def test_commit_failure_after_vector_write_maps_error_and_marks_the_ledger_stale(
        self,
    ):
        commit_error = RuntimeError("commit response was lost")

        class LostCommitEngine:
            """Commit once, then simulate losing the acknowledgement to that commit."""

            def __init__(self, engine):
                self.engine = engine
                self.fail_once = True
                self.transactions = 0

            def __getattr__(self, name):
                return getattr(self.engine, name)

            @contextmanager
            def begin(self):
                self.transactions += 1
                with self.engine.begin() as conn:
                    yield conn
                if self.fail_once:
                    self.fail_once = False
                    raise commit_error

        self.store.engine = LostCommitEngine(self.store.engine)
        vectors = Mock()
        vectors.upsert.side_effect = lambda *args: self.store.events.append("vector")
        embedding = Mock()
        embedding.embed_bytes.return_value = [1.0, 0.0, 0.0, 0.0]
        model = ActiveModel(
            id=3, name="test", version="1", collection="test", dim=4, distance="cosine"
        )
        use_case = index_image.IndexImageUseCase(
            embedding, vectors, self.store, model=model, storage=Mock()
        )
        with (
            patch.object(index_image, "_decode_size", return_value=(8, 6)),
            patch.object(
                index_image,
                "put_original_and_thumb",
                return_value={
                    "original_url": "https://cdn/o",
                    "thumb_url": "https://cdn/t",
                },
            ),
            patch.object(
                images,
                "_prepare_image_insert",
                side_effect=lambda row: (
                    {"id": str(row["id"]), "label_status": str(row["label_status"])},
                    {},
                ),
            ),
            self.assertRaises(AppError) as raised,
        ):
            use_case.execute_one(image_bytes=b"query-image")

        self.assertEqual(raised.exception.code, "INDEX_POSTGRES_FAILED")
        self.assertIs(raised.exception.__cause__, commit_error)
        vectors.upsert.assert_called_once()
        self.assertEqual(self.store.events, ["ledger", "vector", "stale"])
        self.assertEqual(self.store.engine.transactions, 2)
        rows, ledger = self.store.state()
        self.assertEqual(len(rows), 1)
        self.assertEqual(ledger[0]["synced_version"], 0)
        self.store.mark_deleted.assert_not_called()
        self.store.enqueue_image_deletion.assert_not_called()


if __name__ == "__main__":
    unittest.main()
