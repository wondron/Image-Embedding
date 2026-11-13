"""Index transactions against PostgreSQL; TEST_DATABASE_URL must be a throwaway DB.

Each test drops and recreates the schema through Alembic. Vector writes are
local callbacks; no Qdrant, object storage, Nacos, or model is used.
"""

import hashlib
import os
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

TEST_URL = os.getenv("TEST_DATABASE_URL", "").strip()
BACKEND = Path(__file__).resolve().parents[1]


def _alembic(command_name, revision):
    from alembic import command
    from alembic.config import Config

    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.attributes["database_url"] = TEST_URL
    getattr(command, command_name)(config, revision)


@unittest.skipUnless(TEST_URL, "TEST_DATABASE_URL not set")
class IndexPgConcurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sqlalchemy import create_engine

        cls.engine = create_engine(TEST_URL)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        _alembic("downgrade", "base")
        _alembic("upgrade", "head")
        # Migrations create no rows; these are the two scenes the tests use
        self.sql(
            "INSERT INTO scenes (code, name, include_all, outputs_heating) VALUES "
            "('general', '通用', true, false), ('oven', '一体机', false, true)"
        )
        self.model_id = self.sql(
            "INSERT INTO embedding_models "
            "(name, version, dim, distance, input_size, weights_uri, collection, status) "
            "VALUES ('qa', 'v1', 4, 'cosine', 224, 'qa.onnx', 'qa_collection', 'current') "
            "RETURNING id"
        ).scalar_one()
        self.first_category = self.sql(
            "INSERT INTO categories (code) VALUES ('dish') RETURNING id"
        ).scalar_one()
        self.second_category = self.sql(
            "INSERT INTO categories (code) VALUES ('ingredient') RETURNING id"
        ).scalar_one()
        self.oven_id = self.sql(
            "SELECT id FROM scenes WHERE code = 'oven'"
        ).scalar_one()
        self.sql(
            "INSERT INTO scene_rules (scene_id, category_id) VALUES (:scene, :category)",
            {"scene": self.oven_id, "category": self.first_category},
        )

    def sql(self, statement, parameters=None):
        from sqlalchemy import text

        with self.engine.begin() as conn:
            return conn.execute(text(statement), parameters or {})

    def store(self):
        from app.repositories.metadata import PgMetadataStore

        store = PgMetadataStore(url=TEST_URL)
        store.ensure_schema()
        self.addCleanup(store.close)
        return store

    def row(self, *, category=None, description="first"):
        digest = hashlib.sha256(b"concurrent-index-image").digest()
        return {
            "id": uuid.UUID(bytes=digest[:16]),
            "sha256": digest,
            "original_url": "https://cdn.example/original/image.png",
            "thumb_url": "https://cdn.example/thumb/image.webp",
            "width": 8,
            "height": 6,
            "category_id": category or self.first_category,
            "label_status": "auto",
            "food_state": "raw",
            "description": description,
        }

    def start_worker(self, pool, store, operation):
        """Observe the real connection used by the worker before its first SQL."""
        from sqlalchemy import event

        started = threading.Event()
        backend_pid = []

        def before_execute(conn, cursor, statement, parameters, context, executemany):
            if not started.is_set():
                backend_pid.append(conn.connection.driver_connection.info.backend_pid)
                started.set()

        engine = store._get_engine()
        event.listen(engine, "before_cursor_execute", before_execute)
        self.addCleanup(event.remove, engine, "before_cursor_execute", before_execute)
        return pool.submit(operation), started, backend_pid

    def assert_worker_is_blocked(self, future, started, backend_pid):
        """Wait for a database-reported lock, without inferring it from elapsed time."""
        from sqlalchemy import text

        self.assertTrue(started.wait(8), "worker did not reach PostgreSQL")
        deadline = time.monotonic() + 8
        poll = threading.Event()
        with self.engine.connect() as conn:
            while time.monotonic() < deadline:
                blocked = conn.execute(
                    text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"),
                    {"pid": backend_pid[0]},
                ).scalar_one()
                if blocked:
                    self.assertFalse(future.done())
                    return
                if future.done():
                    future.result()  # expose a worker error rather than a timeout
                    self.fail("worker completed while the first transaction was held")
                poll.wait(0.01)  # throttle observation; timing is not an assertion
        self.fail("PostgreSQL did not report the expected lock wait")

    def ledger_version(self, image_id):
        return self.sql(
            "SELECT synced_version FROM image_embeddings "
            "WHERE model_id = :model AND image_id = :image",
            {"model": self.model_id, "image": image_id},
        ).scalar_one()

    def test_duplicate_waits_and_keeps_the_first_committed_annotation(self):
        owner, follower = self.store(), self.store()
        first = self.row()
        duplicate = self.row(category=self.second_category, description="second")

        def index_duplicate():
            with follower.index_image(duplicate, model_id=self.model_id) as result:
                return result

        # Keep the executor outside the transaction: a failed assertion releases
        # the database locks before executor shutdown waits for the worker.
        with ThreadPoolExecutor(max_workers=1) as pool:
            with owner.index_image(first, model_id=self.model_id) as initial:
                self.assertTrue(initial.created)
                future, started, pid = self.start_worker(
                    pool, follower, index_duplicate
                )
                self.assert_worker_is_blocked(future, started, pid)
            repeated = future.result(timeout=8)

        self.assertFalse(repeated.created)
        self.assertEqual(repeated.image["id"], str(first["id"]))
        self.assertEqual(repeated.image["category_id"], self.first_category)
        self.assertEqual(repeated.image["description"], "first")
        self.assertEqual(repeated.image["scenes"], ["general", "oven"])
        self.assertEqual(self.sql("SELECT count(*) FROM images").scalar_one(), 1)
        self.assertEqual(
            self.ledger_version(first["id"]), repeated.image["payload_version"]
        )

    def test_failed_creator_rolls_back_without_deleting_the_successful_follower(self):
        owner, follower = self.store(), self.store()
        first = self.row()
        duplicate = self.row(category=self.second_category, description="successful")

        def index_duplicate():
            with follower.index_image(duplicate, model_id=self.model_id) as result:
                return result

        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.assertRaisesRegex(RuntimeError, "local vector failure"):
                with owner.index_image(first, model_id=self.model_id):
                    future, started, pid = self.start_worker(
                        pool, follower, index_duplicate
                    )
                    self.assert_worker_is_blocked(future, started, pid)
                    raise RuntimeError("local vector failure")
            successful = future.result(timeout=8)

        self.assertTrue(successful.created)
        self.assertEqual(successful.image["category_id"], self.second_category)
        self.assertEqual(successful.image["description"], "successful")
        stored = owner.get_by_id(first["id"])
        self.assertIsNotNone(stored)
        self.assertEqual(stored["description"], "successful")
        # A late uncertainty marker may conservatively leave the follower stale;
        # it must preserve its row/ledger and never create a deletion job.
        self.assertIn(self.ledger_version(first["id"]), (0, stored["payload_version"]))
        self.assertEqual(
            self.sql("SELECT count(*) FROM image_deletions").scalar_one(), 0
        )

    def test_annotation_change_waits_for_the_snapshot_then_advances_the_version(self):
        owner, editor = self.store(), self.store()
        row = self.row()
        with owner.index_image(row, model_id=self.model_id):
            pass

        def change_annotation():
            return editor.patch_image(row["id"], {"category_id": self.second_category})

        with ThreadPoolExecutor(max_workers=1) as pool:
            with owner.index_image(row, model_id=self.model_id) as snapshot:
                future, started, pid = self.start_worker(
                    pool, editor, change_annotation
                )
                self.assert_worker_is_blocked(future, started, pid)
                self.assertEqual(snapshot.image["category_id"], self.first_category)
                self.assertEqual(snapshot.image["scenes"], ["general", "oven"])
                self.assertEqual(snapshot.image["payload_version"], 1)
            edited = future.result(timeout=8)

        self.assertEqual(edited["category_id"], self.second_category)
        self.assertEqual(edited["scenes"], ["general"])
        self.assertGreater(edited["payload_version"], self.ledger_version(row["id"]))

        # A changed rule must also advance an existing image's version on a
        # re-upload; otherwise an older payload could be confirmed at that version.
        self.sql(
            "INSERT INTO scene_rules (scene_id, category_id) VALUES (:scene, :category)",
            {"scene": self.oven_id, "category": self.second_category},
        )
        with owner.index_image(row, model_id=self.model_id) as refreshed:
            self.assertFalse(refreshed.created)
            self.assertEqual(refreshed.image["category_id"], self.second_category)
            self.assertEqual(refreshed.image["scenes"], ["general", "oven"])
            self.assertGreater(
                refreshed.image["payload_version"], edited["payload_version"]
            )
        self.assertEqual(
            self.ledger_version(row["id"]), refreshed.image["payload_version"]
        )

    def test_bad_ledger_model_fails_before_the_vector_callback(self):
        from app.core.errors import AppError

        store = self.store()
        yielded = False
        with self.assertRaises(AppError) as raised:
            with store.index_image(self.row(), model_id=self.model_id + 999):
                yielded = True

        self.assertFalse(yielded)
        self.assertEqual(raised.exception.code, "INDEX_POSTGRES_FAILED")
        for table in (
            "images",
            "image_foods",
            "image_scenes",
            "image_embeddings",
            "image_deletions",
        ):
            self.assertEqual(self.sql(f"SELECT count(*) FROM {table}").scalar_one(), 0)

    def test_payload_read_keeps_one_snapshot_when_annotation_commits_between_queries(
        self,
    ):
        reader, editor = self.store(), self.store()
        row = self.row()
        with reader.index_image(row, model_id=self.model_id) as initial:
            original_version = initial.image["payload_version"]

        read_scene_codes = reader._scene_codes_by_image
        committed_edits = []

        def edit_then_read_scenes(conn, image_ids):
            # get_payload_rows has already read the image/version. Commit a
            # change on another connection before its scene query starts.
            committed_edits.append(
                editor.patch_image(row["id"], {"category_id": self.second_category})
            )
            return read_scene_codes(conn, image_ids)

        with patch.object(
            reader, "_scene_codes_by_image", side_effect=edit_then_read_scenes
        ):
            (old_payload,) = reader.get_payload_rows([str(row["id"])])

        self.assertEqual(len(committed_edits), 1)
        self.assertGreater(committed_edits[0]["payload_version"], original_version)
        self.assertEqual(old_payload["category_id"], self.first_category)
        self.assertEqual(old_payload["payload_version"], original_version)
        self.assertEqual(old_payload["scenes"], ["general", "oven"])

        (new_payload,) = reader.get_payload_rows([str(row["id"])])
        self.assertEqual(new_payload["category_id"], self.second_category)
        self.assertEqual(
            new_payload["payload_version"], committed_edits[0]["payload_version"]
        )
        self.assertEqual(new_payload["scenes"], ["general"])


if __name__ == "__main__":
    unittest.main()
