"""Live PostgreSQL checks for migrations + repositories.

Skipped unless TEST_DATABASE_URL points at an EMPTY throwaway database
(e.g. postgresql+psycopg://user:pw@localhost:5432/image_embedding_test).
Every test drops and recreates the schema through Alembic.
"""

import hashlib
import os
import unittest
import uuid
from pathlib import Path

TEST_URL = os.getenv("TEST_DATABASE_URL", "").strip()
BACKEND = Path(__file__).resolve().parents[1]


def _alembic(cmd: str, rev: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.attributes["database_url"] = TEST_URL  # alembic/env.py uses it instead of POSTGRES_CONFIG
    getattr(command, cmd)(cfg, rev)


def _sha(n: int) -> bytes:
    return hashlib.sha256(f"image-{n}".encode()).digest()


@unittest.skipUnless(TEST_URL, "TEST_DATABASE_URL not set")
class PgLiveTests(unittest.TestCase):
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

    def sql(self, statement, params=None):
        from sqlalchemy import text

        with self.engine.begin() as conn:
            return conn.execute(text(statement), params or {})

    def store(self):
        from app.repositories.metadata import PgMetadataStore

        return PgMetadataStore(url=TEST_URL)

    # ---- schema ---------------------------------------------------------

    def test_migrated_schema_matches_the_orm_models(self):
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext

        import app.models  # noqa: F401
        from app.db.base import Base

        with self.engine.connect() as conn:
            ctx = MigrationContext.configure(conn, opts={"compare_type": True})
            diff = compare_metadata(ctx, Base.metadata)
        self.assertEqual(diff, [])

    def test_downgrade_to_0001_and_back(self):
        _alembic("downgrade", "20261104_0001")
        _alembic("upgrade", "head")
        self.test_migrated_schema_matches_the_orm_models()

    def test_0002_drops_include_all_rows_and_flags_manually_excluded_images(self):
        _alembic("downgrade", "20261104_0001")
        ids = [uuid.UUID(bytes=_sha(i)[:16]) for i in (1, 2)]
        for i, iid in enumerate(ids, start=1):
            self.sql(
                "INSERT INTO images (id, sha256, original_url, thumb_url, width, height, "
                "source_type, license, label_status) VALUES "
                "(:id, :sha, 'https://x/o', 'https://x/t', 4, 3, 'user_upload', 'unknown', 'unlabeled')",
                {"id": iid, "sha": _sha(i)},
            )
        general = self.sql("SELECT id FROM scenes WHERE code = 'general'").scalar_one()
        self.sql(
            "INSERT INTO image_scenes (image_id, scene_id, origin, is_member) VALUES "
            "(:a, :s, 'rule', true), (:b, :s, 'manual', false)",
            {"a": ids[0], "b": ids[1], "s": general},
        )
        _alembic("upgrade", "head")
        self.assertEqual(self.sql("SELECT count(*) FROM image_scenes").scalar_one(), 0)
        versions = dict(self.sql("SELECT id, payload_version FROM images").fetchall())
        self.assertEqual(versions[ids[0]], 1)
        self.assertEqual(versions[ids[1]], 2)

    def test_check_constraints_reject_inconsistent_rows(self):
        from sqlalchemy.exc import IntegrityError

        with self.assertRaisesRegex(IntegrityError, "ck_images_label_matches_category"):
            self.sql(
                "INSERT INTO images (id, sha256, original_url, thumb_url, width, height, "
                "source_type, license, label_status) VALUES "
                "(:id, :sha, 'https://x/o', 'https://x/t', 4, 3, 'user_upload', 'unknown', 'auto')",
                {"id": uuid.UUID(bytes=_sha(9)[:16]), "sha": _sha(9)},
            )
        oven = self.sql("SELECT id FROM scenes WHERE code = 'oven'").scalar_one()
        us = self.sql("SELECT id FROM scenes WHERE code = 'us_diet'").scalar_one()
        cat = self.sql("INSERT INTO categories (code) VALUES ('dish') RETURNING id").scalar_one()
        label = self.sql(
            "INSERT INTO scene_labels (scene_id, code, name) VALUES (:s, 'x', 'x') RETURNING id",
            {"s": us},
        ).scalar_one()
        with self.assertRaisesRegex(IntegrityError, "fk_scene_rules_scene_id_label_id_scene_labels"):
            self.sql(
                "INSERT INTO scene_rules (scene_id, category_id, label_id) VALUES (:s, :c, :l)",
                {"s": oven, "c": cat, "l": label},
            )

    # ---- repositories ---------------------------------------------------

    def seed_catalog(self):
        hg = self.sql(
            "INSERT INTO heating_groups (code, name) VALUES ('grill', '烤') RETURNING id"
        ).scalar_one()
        dish = self.sql(
            "INSERT INTO categories (code, heating_group_id, cuisines) "
            "VALUES ('dish', :hg, '{american}') RETURNING id",
            {"hg": hg},
        ).scalar_one()
        steak = self.sql(
            "INSERT INTO categories (code, parent_id) VALUES ('dish.steak', :p) RETURNING id",
            {"p": dish},
        ).scalar_one()
        self.sql(
            "INSERT INTO category_names (category_id, lang, name, kind) VALUES (:c, 'zh', '牛排', 'primary')",
            {"c": steak},
        )
        oven = self.sql("SELECT id FROM scenes WHERE code = 'oven'").scalar_one()
        label = self.sql(
            "INSERT INTO scene_labels (scene_id, code, name) VALUES (:s, 'steak', '牛排') RETURNING id",
            {"s": oven},
        ).scalar_one()
        self.sql(
            "INSERT INTO scene_rules (scene_id, category_id, label_id) VALUES (:s, :c, :l)",
            {"s": oven, "c": dish, "l": label},
        )
        return {"dish": dish, "steak": steak, "label": label}

    def insert(self, store, n, **extra):
        from app.services.retrieval.image_ids import uuid_from_sha256

        sha = _sha(n)
        row = {
            "id": uuid_from_sha256(sha),
            "sha256": sha,
            "original_url": f"https://cdn/o/{n}.jpg",
            "thumb_url": f"https://cdn/t/{n}.webp",
            "width": 640,
            "height": 480,
        }
        row.update(extra)
        iid = store.insert_image(row)
        store.recompute_image_scenes(iid, bump_version=False)
        return str(iid)

    def test_index_list_and_scene_membership_flow(self):
        from app.core.errors import AppError

        cat = self.seed_catalog()
        store = self.store()
        steak_img = self.insert(store, 1, category_id=cat["steak"], label_status="auto", food_state="cooked")
        plain_img = self.insert(store, 2)

        # general holds every image without image_scenes rows; steak matches the oven rule
        rows = self.sql("SELECT scene_id FROM image_scenes WHERE image_id = :i", {"i": steak_img}).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(store.get_effective_scene_codes(steak_img), ["general", "oven"])
        self.assertEqual(store.get_effective_scene_codes(plain_img), ["general"])

        items, total = store.list_images(scene="general")
        self.assertEqual(total, 2)
        by_id = {i["id"]: i for i in items}
        self.assertEqual(by_id[steak_img]["category_name"], "牛排")
        self.assertEqual(by_id[steak_img]["heating_group"]["code"], "grill")
        self.assertEqual(store.list_images(scene="oven")[1], 1)
        self.assertEqual(store.list_images(scene="general", q="牛排")[1], 1)
        self.assertEqual(store.list_images(scene="nope"), ([], 0))

        hydrated = store.get_active_by_ids([steak_img, plain_img], scene="oven")
        self.assertEqual(list(hydrated), [steak_img])
        self.assertEqual(len(store.get_active_by_ids([steak_img, plain_img], scene="general")), 2)

        oven = store.get_scene_by_code("oven")["id"]
        labels = store.map_categories_to_scene_labels(oven, [cat["steak"], cat["dish"]])
        self.assertEqual({cid: label["code"] for cid, label in labels.items()}, {cat["steak"]: "steak", cat["dish"]: "steak"})

        with self.assertRaises(AppError):
            store.set_manual_scene_membership(steak_img, "general", is_member=False)
        out = store.set_manual_scene_membership(steak_img, "oven", is_member=False)
        self.assertEqual(out["scene_codes"], ["general"])
        self.assertEqual(out["payload_version"], 2)
        self.assertEqual(store.list_images(scene="oven")[1], 0)

    def test_same_bytes_keep_the_first_row(self):
        store = self.store()
        first = self.insert(store, 1)
        again = self.insert(store, 1, source_type="crawled", license="commercial")
        self.assertEqual(first, again)
        row = store.get_image_by_uuid(first)
        self.assertEqual((row["source_type"], row["license"]), ("user_upload", "unknown"))
        self.assertEqual(self.sql("SELECT count(*) FROM images").scalar_one(), 1)

    def test_update_image_validates_and_maps_constraint_errors(self):
        from app.core.errors import AppError

        cat = self.seed_catalog()
        store = self.store()
        iid = self.insert(store, 1)
        with self.assertRaises(AppError) as ctx:
            store.update_image(iid, {"food_state": "boiled"})
        self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")
        with self.assertRaises(AppError) as ctx:
            store.update_image(iid, {"category_id": cat["steak"]})  # still unlabeled
        self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")
        store.update_image(iid, {"category_id": cat["steak"], "label_status": "verified"})
        row = store.get_by_id(iid)
        self.assertEqual((row["label_status"], row["payload_version"]), ("verified", 2))
        self.assertEqual(row["scenes"], ["general", "oven"])

    def test_deletion_queue_and_current_model(self):
        store = self.store()
        iid = self.insert(store, 1)
        store.enqueue_image_deletion(iid)
        self.assertIsNone(store.get_image_by_uuid(iid))
        self.assertEqual([r["image_id"] for r in store.list_pending_deletions()], [iid])
        store.complete_deletion(iid)
        self.assertEqual(store.list_pending_deletions(), [])

        self.sql(
            "INSERT INTO embedding_models (name, version, dim, distance, input_size, weights_uri, "
            "collection, status) VALUES ('m', '1', 1024, 'cosine', 336, 's3://w', 'c1', 'current')"
        )
        model = store.get_current_embedding_model()
        self.assertEqual((model["vector_dim"], model["distance"]), (1024, "cosine"))
        status = self.sql("INSERT INTO embedding_models (name, version, dim, distance, input_size, "
                          "weights_uri, collection) VALUES ('m', '2', 1024, 'cosine', 336, 's3://w', 'c2') "
                          "RETURNING status").scalar_one()
        self.assertEqual(status, "building")

    # ---- one image as the gallery admin reads and edits it (patch_image / get_image_detail) ----

    def current_model(self, collection):
        return self.sql(
            "INSERT INTO embedding_models (name, version, dim, distance, input_size, weights_uri, collection, status) "
            "VALUES ('m', '1', 4, 'cosine', 336, 's3://w', :c, 'current') RETURNING id",
            {"c": collection},
        ).scalar_one()

    def test_image_detail_and_patch(self):
        from app.core.errors import AppError

        cat = self.seed_catalog()
        store = self.store()
        iid = self.insert(store, 1, tags=["带骨"], food_state="raw")
        detail = store.get_image_detail(iid)
        self.assertEqual(
            (detail["category_id"], detail["category_code"], detail["label_status"], detail["scenes"]),
            (None, None, "unlabeled", ["general"]),
        )
        self.assertEqual((detail["food_state"], detail["tags"], detail["embeddings"], detail["heating_group"]),
                         ("raw", ["带骨"], [], None))
        self.assertEqual(len(detail["sha256"]), 64)  # hex
        self.assertIsNone(store.get_image_detail(uuid.uuid4()))
        model_id = self.current_model("c_detail")
        store.upsert_image_embedding(model_id=model_id, image_id=iid, synced_version=1)
        self.assertEqual(
            [(e["model_id"], e["collection"], e["model_status"], e["synced_version"])
             for e in store.get_image_detail(iid)["embeddings"]],
            [(model_id, "c_detail", "current", 1)],
        )

        # A category picked by hand: verified, the rule memberships follow, one bump
        out = store.patch_image(iid, {"category_id": cat["steak"]})
        self.assertEqual(
            (out["label_status"], out["category_name"], out["category_code"], out["scenes"], out["payload_version"]),
            ("verified", "牛排", "dish.steak", ["general", "oven"], 2),
        )
        self.assertEqual(out["heating_group"]["code"], "grill")  # the nearest ancestor's
        # Fields outside the payload, and a food_state equal to the stored one: no bump
        out = store.patch_image(iid, {"tags": ["去骨"], "description": "煎", "total_mass_g": 300, "food_state": "raw"})
        self.assertEqual((out["tags"], out["description"], out["total_mass_g"], out["payload_version"]),
                         (["去骨"], "煎", 300, 2))
        out = store.patch_image(iid, {"food_state": "cooked", "label_status": "auto"})
        self.assertEqual((out["food_state"], out["label_status"], out["payload_version"]), ("cooked", "auto", 3))
        # Category cleared: unlabeled and out of the rule scene
        out = store.patch_image(iid, {"category_id": None})
        self.assertEqual((out["category_id"], out["label_status"], out["scenes"], out["payload_version"]),
                         (None, "unlabeled", ["general"], 4))
        self.assertEqual(
            self.sql("SELECT count(*) FROM image_scenes WHERE image_id = :i", {"i": uuid.UUID(iid)}).scalar_one(), 0
        )

        def refused(fields, code="VALIDATION_ERROR", image=iid):
            with self.assertRaises(AppError) as ctx:
                store.patch_image(image, fields)
            self.assertEqual(ctx.exception.code, code)

        refused({"label_status": "auto"})  # no category to go with it
        self.sql("UPDATE categories SET is_active = false WHERE id = :c", {"c": cat["dish"]})
        refused({"category_id": cat["dish"]})  # disabled: no longer used for labelling
        refused({"category_id": 999999})
        refused({"description": "x"}, "IMAGE_NOT_FOUND", image=str(uuid.uuid4()))
        self.assertEqual(store.get_image_detail(iid)["payload_version"], 4)  # nothing half-written

    def test_list_filters_on_a_category_subtree_and_the_label_status(self):
        cat = self.seed_catalog()  # dish > dish.steak
        store = self.store()
        dish = self.insert(store, 1, category_id=cat["dish"], label_status="auto")
        steak = self.insert(store, 2, category_id=cat["steak"], label_status="verified")
        plain = self.insert(store, 3)

        def ids(**kw):
            return sorted(item["id"] for item in store.list_images(scene="general", **kw)[0])

        self.assertEqual(ids(category_id=cat["dish"]), sorted([dish, steak]))  # the category and its subtree
        self.assertEqual(ids(category_id=cat["steak"]), [steak])
        self.assertEqual(ids(category_id=999999), [])
        self.assertEqual(ids(label_status="unlabeled"), [plain])
        self.assertEqual(ids(category_id=cat["dish"], label_status="verified"), [steak])
        self.assertEqual(store.list_images(scene="general", category_id=cat["dish"])[1], 2)  # the total honours it

    def test_a_deleted_image_stored_again_is_deleted_again(self):
        from app.core.errors import AppError

        store = self.store()
        with self.assertRaises(AppError) as ctx:
            store.enqueue_image_deletion(uuid.uuid4())
        self.assertEqual(ctx.exception.code, "IMAGE_NOT_FOUND")
        iid = self.insert(store, 1)
        first = store.enqueue_image_deletion(iid)
        self.assertEqual(store.enqueue_image_deletion(iid), first)  # still queued: the same row
        self.assertEqual(self.insert(store, 1), iid)  # the same bytes indexed again before the cleanup ran
        store.enqueue_image_deletion(iid)
        self.assertIsNone(store.get_image_by_uuid(iid))
        self.assertEqual([r["image_id"] for r in store.list_pending_deletions()], [iid])

    def test_the_image_admin_reads_edits_and_deletes_through_the_vector_store(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api.v1 import images as routes
        from app.bootstrap.state import ApplicationState
        from app.clients.qdrant import QdrantConnection, QdrantStore
        from app.core.exceptions import register_exception_handlers
        from app.core.middleware import TraceIdMiddleware
        from app.repositories.vectors import QdrantVectorStore
        from app.services.retrieval.active_model import ActiveModel
        from app.services.retrieval.delete_image import DeletionWorker
        from app.services.retrieval.manage_images import ManageImagesUseCase
        from app.services.retrieval.sync_scenes import SyncSceneMembershipsUseCase
        from app.services.retrieval.vector_status import ImageVectorStatusUseCase

        cat = self.seed_catalog()
        store = self.store()
        model_id = self.current_model("c_images")
        iid = self.insert(store, 1)
        orphan = self.insert(store, 2)  # in PG, never embedded into this collection
        vectors = QdrantVectorStore(QdrantStore(QdrantConnection(url=":memory:"), collection="c_images", vector_dim=4))
        vectors.ensure_collection()
        self.addCleanup(vectors.close)
        # A point whose payload is behind PostgreSQL (label_status)
        vectors.upsert(iid, [1, 0, 0, 0], {"scenes": ["general"], "category_id": None, "food_state": None, "label_status": "auto"})
        model = ActiveModel(id=model_id, name="m", version="1", collection="c_images", dim=4, distance="cosine")
        storage = Mock()
        runtime = ApplicationState(
            config={}, config_info={}, http_client=Mock(), settings=SimpleNamespace(),
            catalog=SimpleNamespace(images_uc=ManageImagesUseCase(store)),
            retrieval=SimpleNamespace(
                model=model,
                scene_sync_uc=SyncSceneMembershipsUseCase(store, vectors, model=model),
                vector_status_uc=ImageVectorStatusUseCase(vectors, model=model),
                deletion_worker=DeletionWorker(store, vectors, storage),
            ),
        )
        app = FastAPI()
        register_exception_handlers(app)
        app.add_middleware(TraceIdMiddleware)
        app.include_router(routes.router)
        client = TestClient(app)
        self.addCleanup(client.close)
        with patch.object(routes, "get_application_state", return_value=runtime):
            def vector(image):
                response = client.get(f"/api/images/{image}")
                self.assertEqual(response.status_code, 200, response.text)
                return response.json()["vector"]

            self.assertEqual(vector(iid)["status"], "stale")
            self.assertEqual(vector(orphan)["status"], "missing")
            body = client.post(f"/api/images/{iid}/vector-sync").json()
            self.assertEqual((body["vector_sync"], body["sync_required"]), ("pushed", False))
            state = vector(iid)
            self.assertEqual((state["status"], state["synced_version"], state["payload_version"]), ("synced", 1, 1))

            # A category edited in the gallery reaches the search filter in the same request
            body = client.patch(f"/api/images/{iid}", json={"category_id": cat["steak"]}).json()
            self.assertEqual((body["vector_sync"], body["image"]["scenes"], body["image"]["label_status"]),
                             ("pushed", ["general", "oven"], "verified"))
            self.assertEqual([hit["id"] for hit in vectors.search([1, 0, 0, 0], top_k=5, scene="oven")], [iid])
            self.assertEqual(vector(iid)["status"], "synced")
            body = client.patch(f"/api/images/{orphan}", json={"tags": ["x"]}).json()
            self.assertEqual((body["vector_sync"], body["sync_required"], body["image"]["tags"]), ("missing", True, ["x"]))

            # Deleted: gone from PostgreSQL, the point and both files removed in the same request
            body = client.delete(f"/api/images/{iid}").json()
            self.assertEqual((body["image_id"], body["cleanup"]), (iid, "done"))
            self.assertEqual(vectors.get_payloads([iid]), {})
            self.assertEqual(store.list_pending_deletions(), [])
            self.assertEqual(sorted(c.args[0] for c in storage.delete_by_url_or_key.call_args_list),
                             ["https://cdn/o/1.jpg", "https://cdn/t/1.webp"])
            self.assertEqual(client.get(f"/api/images/{iid}").status_code, 404)

    def test_a_value_added_to_the_enum_file_is_stored_and_blocks_downgrade(self):
        import shutil
        import tempfile

        from sqlalchemy.exc import IntegrityError

        from app.core.config.enum_store import EnumStore, set_enum_store
        from app.core.errors import AppError

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        enums_path = Path(tmp.name) / "enums.yaml"
        shutil.copyfile(BACKEND / "config" / "param" / "enums.yaml", enums_path)
        enum_file = EnumStore(enums_path)
        self.addCleanup(set_enum_store, set_enum_store(enum_file))

        store = self.store()
        with self.assertRaises(AppError) as ctx:
            self.insert(store, 1, food_state="thawed", source_type="partner_api")
        self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")

        enum_file.add_value("food_state", "thawed", "解冻")
        enum_file.add_value("source_type", "partner_api", "合作方接口")
        iid = self.insert(store, 1, food_state="thawed", source_type="partner_api")
        row = store.get_image_by_uuid(iid)
        self.assertEqual((row["food_state"], row["source_type"]), ("thawed", "partner_api"))

        # 0004's downgrade restores the old CHECK lists, so it refuses while such rows exist
        with self.assertRaisesRegex(IntegrityError, "ck_images_(source_type|food_state)"):
            _alembic("downgrade", "20261106_0003")
        self.sql("UPDATE images SET source_type = 'user_upload'")
        self.sql("UPDATE image_foods SET food_state = 'frozen'")  # 0006's downgrade copies it back to images
        _alembic("downgrade", "20261106_0003")
        _alembic("upgrade", "head")

    def test_food_annotation_lives_in_its_own_optional_table(self):
        from sqlalchemy.exc import IntegrityError

        from app.core.errors import AppError
        from app.services.retrieval.food_fields import FOOD_FIELDS

        store = self.store()
        bare = self.insert(store, 1)  # 非必填: nothing annotated -> no image_foods row
        self.assertEqual(self.sql("SELECT count(*) FROM image_foods").scalar_one(), 0)
        self.assertEqual({k: store.get_image_by_uuid(bare)[k] for k in FOOD_FIELDS}, dict.fromkeys(FOOD_FIELDS))

        food = {"food_state": "raw", "food_processing": "sliced", "food_count": 4,
                "total_mass_g": 350, "water_mass_g": 0, "main_ingredient_mass_g": 300}
        iid = self.insert(store, 2, **food)
        row = store.get_image_by_uuid(iid)
        self.assertEqual({k: row[k] for k in FOOD_FIELDS}, food)
        before = row["payload_version"]

        # a patch changes only the given columns; these are not in the Qdrant payload: no bump
        store.update_image(iid, {"total_mass_g": "1200", "food_count": None})
        row = store.get_image_by_uuid(iid)
        self.assertEqual((row["total_mass_g"], row["food_count"], row["water_mass_g"], row["payload_version"]),
                         (1200, None, 0, before))
        store.update_image(iid, {"food_state": "cooked"})  # food_state is in the payload
        self.assertEqual(store.get_image_by_uuid(iid)["payload_version"], before + 1)
        store.update_image(bare, {"water_mass_g": 80})  # first annotation of an image creates its row
        self.assertEqual(store.get_image_by_uuid(bare)["water_mass_g"], 80)

        items, _ = store.list_images(scene="general")  # list / search hydrate through the same join
        self.assertEqual({i["id"]: i["total_mass_g"] for i in items}, {bare: None, iid: 1200})
        self.assertEqual(store.get_active_by_ids([iid])[iid]["main_ingredient_mass_g"], 300)

        with self.assertRaises(AppError) as ctx:
            store.update_image(iid, {"main_ingredient_mass_g": 0})
        self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")
        # the database holds the same rule for writers that skip the repository
        with self.assertRaisesRegex(IntegrityError, "ck_image_foods_total_mass_positive"):
            self.sql("UPDATE image_foods SET total_mass_g = 0")
        store.enqueue_image_deletion(iid)  # deleting the image deletes its annotation
        self.assertEqual(self.sql("SELECT count(*) FROM image_foods").scalar_one(), 1)

    def test_0006_moves_the_food_columns_into_image_foods_and_back(self):
        _alembic("downgrade", "20261109_0005")
        ids = [uuid.UUID(bytes=_sha(i)[:16]) for i in (1, 2)]
        self.sql(
            "INSERT INTO images (id, sha256, original_url, thumb_url, width, height, source_type, license, "
            "label_status, food_state, food_processing, food_weight_g, food_count) VALUES "
            "(:a, :sa, 'https://x/o1', 'https://x/t1', 4, 3, 'user_upload', 'unknown', 'unlabeled', "
            " 'raw', 'sliced', 350, 4), "
            "(:b, :sb, 'https://x/o2', 'https://x/t2', 4, 3, 'user_upload', 'unknown', 'unlabeled', "
            " NULL, NULL, NULL, NULL)",
            {"a": ids[0], "sa": _sha(1), "b": ids[1], "sb": _sha(2)},
        )
        _alembic("upgrade", "head")
        rows = self.sql(
            "SELECT image_id, food_state, food_processing, food_count, total_mass_g, water_mass_g FROM image_foods"
        ).fetchall()
        self.assertEqual([tuple(r) for r in rows], [(ids[0], "raw", "sliced", 4, 350, None)])  # weight -> total mass

        _alembic("downgrade", "20261109_0005")
        self.assertEqual(dict(self.sql("SELECT id, food_weight_g FROM images").fetchall()), {ids[0]: 350, ids[1]: None})
        _alembic("upgrade", "head")

    def test_description_round_trip_without_payload_bump(self):
        store = self.store()
        default_id = self.insert(store, 1)
        self.assertEqual(store.get_image_by_uuid(default_id)["description"], "")
        iid = self.insert(store, 2, description="  酱色红烧肉 ")
        self.assertEqual(store.get_image_by_uuid(iid)["description"], "酱色红烧肉")
        before = store.get_image_by_uuid(iid)["payload_version"]
        store.update_image(iid, {"description": "改过的描述"})
        row = store.get_image_by_uuid(iid)
        self.assertEqual((row["description"], row["payload_version"]), ("改过的描述", before))
        items, _ = store.list_images(scene="general")
        self.assertEqual({i["id"]: i["description"] for i in items}, {default_id: "", iid: "改过的描述"})

    def test_tags_round_trip_filter_and_q_without_payload_bump(self):
        store = self.store()
        untagged = self.insert(store, 1)
        self.assertEqual(store.get_image_by_uuid(untagged)["tags"], [])
        iid = self.insert(store, 2, tags=[" 红薯叶 ", "带皮", "红薯叶"])
        self.assertEqual(store.get_image_by_uuid(iid)["tags"], ["红薯叶", "带皮"])

        def ids(**kw):
            return sorted(i["id"] for i in store.list_images(scene="general", **kw)[0])

        self.assertEqual(ids(tag="红薯叶"), [iid])
        self.assertEqual(ids(tag="红薯"), [])  # tag is an exact match
        self.assertEqual(ids(q="薯"), [iid])  # q matches any tag as a substring
        self.assertEqual(store.list_images(scene="general", tag="带皮")[1], 1)  # total honours the filter

        before = store.get_image_by_uuid(iid)["payload_version"]
        store.update_image(iid, {"tags": ["清蒸", " "]})
        row = store.get_image_by_uuid(iid)
        self.assertEqual((row["tags"], row["payload_version"]), (["清蒸"], before))
        self.assertEqual((ids(tag="红薯叶"), ids(tag="清蒸")), ([], [iid]))
        self.assertEqual(store.get_active_by_ids([iid], scene="general")[iid]["tags"], ["清蒸"])  # search hydrate

        store.update_image(iid, {"tags": []})
        self.assertEqual(store.get_image_by_uuid(iid)["tags"], [])

    # ---- category tree admin (app.repositories.categories) ---------------

    @staticmethod
    def zh(name):
        return {"lang": "zh", "name": name, "kind": "primary"}

    def assert_category_error(self, code, fn, *args):
        from app.core.errors import AppError

        with self.assertRaises(AppError) as ctx:
            fn(*args)
        self.assertEqual(ctx.exception.code, code, ctx.exception.detail)
        return ctx.exception.detail

    def seed_tree(self, store):
        """ingredient (sorted first), dish > dish.pizza > dish.pizza.slice"""
        dish = store.create_category({"code": "dish", "names": [self.zh("菜品")]})
        ingredient = store.create_category({"code": "ingredient", "names": [self.zh("食材")], "sort_order": -1})
        pizza = store.create_category({
            "code": "dish.pizza",
            "parent_id": dish["id"],
            "names": [
                {"lang": "en", "name": "Pizza", "kind": "primary"},
                {"lang": "zh", "name": "比萨", "kind": "alias"},
                self.zh("披萨"),
            ],
            "cuisines": ["italian", "american", "italian"],
        })
        piece = store.create_category(
            {"code": "dish.pizza.slice", "parent_id": pizza["id"], "names": [self.zh("披萨切片")]}
        )
        return dish, ingredient, pizza, piece

    def test_category_create_builds_paths_names_and_a_display_order(self):
        store = self.store()
        self.assertEqual(store.list_categories(), [])
        dish, ingredient, pizza, piece = self.seed_tree(store)

        self.assertEqual((dish["parent_id"], dish["path"], dish["name"]), (None, str(dish["id"]), "菜品"))
        self.assertEqual((pizza["parent_id"], pizza["path"]), (dish["id"], f"{dish['id']}.{pizza['id']}"))
        self.assertEqual(piece["path"], f"{dish['id']}.{pizza['id']}.{piece['id']}")
        # names: the display language first, in a language the primary first
        self.assertEqual(pizza["names"], [
            self.zh("披萨"),
            {"lang": "zh", "name": "比萨", "kind": "alias"},
            {"lang": "en", "name": "Pizza", "kind": "primary"},
        ])
        self.assertEqual((pizza["name"], pizza["cuisines"]), ("披萨", ["italian", "american"]))
        self.assertEqual(
            (pizza["sort_order"], pizza["is_active"], pizza["heating_group"], pizza["image_count"], pizza["scene_rule_count"]),
            (0, True, None, 0, 0),
        )
        from datetime import datetime

        self.assertIsNotNone(datetime.fromisoformat(pizza["created_at"]).tzinfo)  # ISO 8601 with an offset

        # parents before children; siblings by sort_order, then id
        listed = store.list_categories()
        self.assertEqual([n["code"] for n in listed], ["ingredient", "dish", "dish.pizza", "dish.pizza.slice"])
        self.assertEqual(listed[2], pizza)  # a write answers with the same node the list shows
        self.assertEqual(store.get_category_id_by_code("dish.pizza"), pizza["id"])  # indexing resolves it

    def test_category_patch_renames_relabels_and_replaces_names(self):
        store = self.store()
        dish, _, pizza, _ = self.seed_tree(store)
        hg = self.sql("INSERT INTO heating_groups (code, name) VALUES ('grill', '烤') RETURNING id").scalar_one()
        self.assertEqual(store.list_heating_groups(), [{"id": hg, "code": "grill", "name": "烤"}])
        self.sql("UPDATE categories SET updated_at = now() - interval '1 day' WHERE id = :id", {"id": pizza["id"]})
        stale = store.list_categories()[2]["updated_at"]

        patched = store.update_category(pizza["id"], {
            "code": "dish.pizza_pie", "names": [self.zh("意式披萨")], "cuisines": [],
            "heating_group_id": hg, "sort_order": 7, "is_active": False,
        })
        self.assertEqual(
            (patched["code"], patched["name"], patched["names"], patched["cuisines"]),
            ("dish.pizza_pie", "意式披萨", [self.zh("意式披萨")], []),
        )
        self.assertEqual(patched["heating_group"], {"id": hg, "code": "grill", "name": "烤"})
        self.assertEqual((patched["sort_order"], patched["is_active"], patched["path"]), (7, False, pizza["path"]))
        self.assertGreater(patched["updated_at"], stale)  # raw SQL has to set it itself
        self.assertEqual(
            self.sql("SELECT count(*) FROM category_names WHERE category_id = :c", {"c": pizza["id"]}).scalar_one(), 1
        )

        # only the given fields change; null clears the heating group
        cleared = store.update_category(pizza["id"], {"heating_group_id": None})
        self.assertIsNone(cleared["heating_group"])
        self.assertEqual((cleared["code"], cleared["sort_order"], cleared["names"]), ("dish.pizza_pie", 7, [self.zh("意式披萨")]))
        # a names-only patch still touches updated_at
        self.sql("UPDATE categories SET updated_at = now() - interval '1 day' WHERE id = :id", {"id": dish["id"]})
        before = store.list_categories()[1]["updated_at"]
        self.assertGreater(store.update_category(dish["id"], {"names": [self.zh("菜肴")]})["updated_at"], before)

    def test_category_move_takes_the_subtree_and_respects_the_tree_rules(self):
        store = self.store()
        dish, ingredient, pizza, piece = self.seed_tree(store)

        moved = store.update_category(pizza["id"], {"parent_id": ingredient["id"]})
        self.assertEqual((moved["parent_id"], moved["path"]), (ingredient["id"], f"{ingredient['id']}.{pizza['id']}"))
        by_code = {n["code"]: n for n in store.list_categories()}
        self.assertEqual(by_code["dish.pizza.slice"]["path"], f"{ingredient['id']}.{pizza['id']}.{piece['id']}")
        self.assertEqual(by_code["dish.pizza.slice"]["parent_id"], pizza["id"])

        update = store.update_category
        self.assertIn("descendants", self.assert_category_error("VALIDATION_ERROR", update, pizza["id"], {"parent_id": piece["id"]}))
        self.assertIn("descendants", self.assert_category_error("VALIDATION_ERROR", update, pizza["id"], {"parent_id": pizza["id"]}))
        self.assertIn("does not exist", self.assert_category_error("VALIDATION_ERROR", update, pizza["id"], {"parent_id": 987654}))
        # only a domain may be a root: by creating, by moving up, by renaming a root
        create = store.create_category
        self.assertIn("needs a parent", self.assert_category_error("VALIDATION_ERROR", create, {"code": "snack", "names": [self.zh("零食")]}))
        self.assertIn("needs a parent", self.assert_category_error("VALIDATION_ERROR", update, pizza["id"], {"parent_id": None}))
        self.assert_category_error("VALIDATION_ERROR", update, dish["id"], {"code": "dishes"})
        # ... and a domain under another node can go back up
        dessert = create({"code": "dessert", "parent_id": dish["id"], "names": [self.zh("甜点烘焙")]})
        self.assertEqual(update(dessert["id"], {"parent_id": None})["path"], str(dessert["id"]))
        # nothing above was half-applied
        self.assertEqual(
            {n["code"]: n["parent_id"] for n in store.list_categories()},
            {"ingredient": None, "dish": None, "dessert": None, "dish.pizza": ingredient["id"], "dish.pizza.slice": pizza["id"]},
        )

    def test_category_codes_are_unique_and_references_must_exist(self):
        store = self.store()
        dish, _, pizza, _ = self.seed_tree(store)
        duplicate = {"code": "dish.pizza", "parent_id": dish["id"], "names": [self.zh("重复")]}
        self.assertIn("'dish.pizza'", self.assert_category_error("CATEGORY_CODE_EXISTS", store.create_category, duplicate))
        self.assert_category_error("CATEGORY_CODE_EXISTS", store.update_category, pizza["id"], {"code": "dish"})
        self.assertEqual(store.update_category(pizza["id"], {"code": "dish.pizza"})["code"], "dish.pizza")  # its own code is fine

        self.assert_category_error("VALIDATION_ERROR", store.create_category, dict(duplicate, code="dish.x", parent_id=987654))
        self.assert_category_error("VALIDATION_ERROR", store.create_category, dict(duplicate, code="dish.x", heating_group_id=987654))
        self.assert_category_error("VALIDATION_ERROR", store.update_category, pizza["id"], {"heating_group_id": 987654})
        self.assert_category_error("CATEGORY_NOT_FOUND", store.update_category, 987654, {"sort_order": 1})
        self.assert_category_error("CATEGORY_NOT_FOUND", store.delete_category, 987654)
        self.assertEqual(self.sql("SELECT count(*) FROM categories").scalar_one(), 4)

    def test_category_inserted_by_sql_keeps_its_root_code_until_it_is_renamed(self):
        store = self.store()
        legacy = self.sql("INSERT INTO categories (code) VALUES ('legacy') RETURNING id").scalar_one()
        node = store.list_categories()[0]
        self.assertEqual((node["code"], node["name"], node["names"]), ("legacy", None, []))
        self.assertEqual(store.update_category(legacy, {"sort_order": 9, "names": [self.zh("旧根")]})["name"], "旧根")
        self.assert_category_error("VALIDATION_ERROR", store.update_category, legacy, {"code": "legacy2"})
        self.assertEqual(store.update_category(legacy, {"code": "beverage"})["code"], "beverage")

    def test_category_delete_refuses_a_node_in_use_and_removes_a_free_one(self):
        store = self.store()
        dish, ingredient, pizza, piece = self.seed_tree(store)
        delete = store.delete_category

        self.assertIn("child categories: 1", self.assert_category_error("CATEGORY_IN_USE", delete, pizza["id"]))
        image = self.insert(store, 1, category_id=piece["id"], label_status="auto")
        oven = self.sql("SELECT id FROM scenes WHERE code = 'oven'").scalar_one()
        self.sql("INSERT INTO scene_rules (scene_id, category_id) VALUES (:s, :c)", {"s": oven, "c": piece["id"]})
        counts = {n["code"]: (n["image_count"], n["scene_rule_count"]) for n in store.list_categories()}
        self.assertEqual(counts, {"ingredient": (0, 0), "dish": (0, 0), "dish.pizza": (0, 0), "dish.pizza.slice": (1, 1)})
        detail = self.assert_category_error("CATEGORY_IN_USE", delete, piece["id"])
        self.assertIn("images: 1", detail)
        self.assertIn("scene rules: 1", detail)
        self.assertEqual(self.sql("SELECT count(*) FROM scene_rules").scalar_one(), 1)  # the rule did not cascade away

        self.sql("DELETE FROM scene_rules")
        self.sql("DELETE FROM images WHERE id = :id", {"id": image})
        self.assertEqual(delete(piece["id"]), {"id": piece["id"], "code": "dish.pizza.slice"})
        self.assertEqual(
            self.sql("SELECT count(*) FROM category_names WHERE category_id = :c", {"c": piece["id"]}).scalar_one(), 0
        )
        self.assertEqual(delete(pizza["id"])["code"], "dish.pizza")  # no children left
        self.assertEqual([n["code"] for n in store.list_categories()], ["ingredient", "dish"])

    # ---- scene configuration (app.repositories.scene_config / scenes) -----

    def assert_app_error(self, code, fn, *args, **kwargs):
        from app.core.errors import AppError

        with self.assertRaises(AppError) as ctx:
            fn(*args, **kwargs)
        self.assertEqual(ctx.exception.code, code, ctx.exception.detail)
        return ctx.exception.detail

    def test_scene_requests_follow_the_scenes_table(self):
        from app.core import errors as err
        from app.services.retrieval.list_images import ListImagesUseCase
        from app.services.retrieval.scenes import lookup_scene, requested_scene_code, resolve_index_scenes

        store = self.store()
        image = self.insert(store, 1)
        list_uc = ListImagesUseCase(store)
        self.assertEqual(list_uc.execute()["scene"], "general")  # the default
        self.assertEqual(list_uc.execute(scene="normal")["scene"], "general")  # MVP code still maps
        detail = self.assert_app_error("LIST_UNKNOWN_SCENE", list_uc.execute, scene="steam_oven")
        self.assertIn("general, oven, range_hood, us_diet, cn_diet, jp_diet", detail)  # tells the client what exists

        # A scene created through the configuration is usable at once, everywhere
        store.create_scene({"code": "steam_oven", "name": "蒸烤箱", "include_all": True})
        self.assertEqual((list_uc.execute(scene="steam_oven")["total"]), 1)
        self.assertEqual(resolve_index_scenes(store, scenes=["steam_oven", "general"]), ["steam_oven", "general"])
        self.assertEqual(
            lookup_scene(store, requested_scene_code(" steam_oven "), unknown=err.search_unknown_scene,
                         failed=err.search_postgres_failed)["code"],
            "steam_oven",
        )
        # ... and refused once disabled
        steam = store.get_scene_by_code("steam_oven")["id"]
        store.update_scene(steam, {"is_active": False})
        self.assertIn("is inactive", self.assert_app_error("LIST_UNKNOWN_SCENE", list_uc.execute, scene="steam_oven"))
        self.assert_app_error("INDEX_UNKNOWN_SCENE", resolve_index_scenes, store, scene="steam_oven")
        self.assertEqual(store.get_effective_scene_codes(image), ["general"])

    def test_an_inactive_scene_has_no_members_anywhere(self):
        cat = self.seed_catalog()
        store = self.store()
        steak = self.insert(store, 1, category_id=cat["steak"], label_status="auto")
        oven = store.get_scene_by_code("oven")["id"]
        self.assertEqual(store.get_effective_scene_codes(steak), ["general", "oven"])

        store.update_scene(oven, {"is_active": False})
        self.assertEqual(store.get_effective_scene_codes(steak), ["general"])
        self.assertEqual(store.list_images(scene="oven"), ([], 0))
        self.assertEqual(store.get_active_by_ids([steak], scene="oven"), {})
        self.assertEqual(store.get_payload_rows([steak])[0]["scenes"], ["general"])  # what sync would push
        oven_row = next(s for s in store.list_scene_configs() if s["code"] == "oven")
        self.assertEqual(oven_row["member_count"], 0)

        store.update_scene(oven, {"is_active": True})  # rows were kept: members are back at once
        self.assertEqual(store.get_effective_scene_codes(steak), ["general", "oven"])

    def test_scene_configuration_crud_and_its_guards(self):
        cat = self.seed_catalog()
        store = self.store()
        general = store.get_scene_by_code("general")["id"]
        self.assertIn("cannot be deleted", self.assert_app_error("SCENE_PROTECTED", store.delete_scene, general))
        self.assert_app_error("SCENE_PROTECTED", store.update_scene, general, {"is_active": False})
        self.assert_app_error("SCENE_PROTECTED", store.update_scene, general, {"include_all": False})
        self.assertEqual(store.update_scene(general, {"name": "通用", "description": "  "})["description"], None)

        created = store.create_scene({"code": "steam_oven", "name": "蒸烤箱", "outputs_heating": True})
        self.assertEqual(
            {k: created[k] for k in ("code", "name", "include_all", "outputs_heating", "is_active", "member_count", "labels", "rules")},
            {"code": "steam_oven", "name": "蒸烤箱", "include_all": False, "outputs_heating": True, "is_active": True,
             "member_count": 0, "labels": [], "rules": []},
        )
        sid = created["id"]
        self.assert_app_error("SCENE_CODE_EXISTS", store.create_scene, {"code": "steam_oven", "name": "重复"})
        self.assert_app_error("VALIDATION_ERROR", store.create_scene, {"code": "device", "name": "旧编码"})
        self.assert_app_error("VALIDATION_ERROR", store.update_scene, sid, {"code": "steam"})

        # labels: unique per scene, deletable only when no rule uses them
        scene = store.create_scene_label(sid, {"code": "steak", "name": "牛排", "sort_order": 2})
        scene = store.create_scene_label(sid, {"code": "meat", "name": "肉类", "sort_order": 1})
        self.assertEqual([l["code"] for l in scene["labels"]], ["meat", "steak"])  # sort_order
        steak_label = next(l["id"] for l in scene["labels"] if l["code"] == "steak")
        meat_label = next(l["id"] for l in scene["labels"] if l["code"] == "meat")
        self.assert_app_error("SCENE_CODE_EXISTS", store.create_scene_label, sid, {"code": "steak", "name": "又一个"})
        self.assert_app_error("SCENE_CODE_EXISTS", store.update_scene_label, sid, meat_label, {"code": "steak"})
        oven_label = self.sql("SELECT id FROM scene_labels WHERE code = 'steak' AND scene_id <> :s", {"s": sid}).scalar_one()
        self.assert_app_error("SCENE_NOT_FOUND", store.update_scene_label, sid, oven_label, {"name": "别的场景的"})

        # rules: one per category / cuisine; labels only of this scene, only on category rules
        scene = store.create_scene_rule(sid, {"category_id": cat["steak"], "label_id": steak_label})
        scene = store.create_scene_rule(sid, {"cuisine": "american"})
        self.assertEqual(
            [(r["category"]["code"] if r["category"] else None, r["cuisine"], r["label"]["code"] if r["label"] else None) for r in scene["rules"]],
            [("dish.steak", None, "steak"), (None, "american", None)],
        )
        self.assertEqual(scene["rules"][0]["category"]["name"], "牛排")
        self.assert_app_error("SCENE_RULE_EXISTS", store.create_scene_rule, sid, {"category_id": cat["steak"]})
        self.assert_app_error("SCENE_RULE_EXISTS", store.create_scene_rule, sid, {"cuisine": "american"})
        self.assert_app_error("VALIDATION_ERROR", store.create_scene_rule, sid, {"category_id": cat["dish"], "label_id": oven_label})
        self.assert_app_error("VALIDATION_ERROR", store.create_scene_rule, sid, {"category_id": 987654})
        self.assert_app_error("VALIDATION_ERROR", store.create_scene_rule, sid, {"cuisine": "chinese", "label_id": meat_label})
        cuisine_rule = scene["rules"][1]["id"]
        self.assert_app_error("VALIDATION_ERROR", store.update_scene_rule, sid, cuisine_rule, {"label_id": meat_label})
        self.assert_app_error("SCENE_IN_USE", store.delete_scene_label, sid, steak_label)

        category_rule = scene["rules"][0]["id"]
        scene = store.update_scene_rule(sid, category_rule, {"label_id": meat_label})
        self.assertEqual(scene["rules"][0]["label"]["code"], "meat")
        self.assertEqual({l["code"]: l["rule_count"] for l in scene["labels"]}, {"meat": 1, "steak": 0})
        scene = store.delete_scene_label(sid, steak_label)
        self.assertEqual([l["code"] for l in scene["labels"]], ["meat"])
        scene = store.delete_scene_rule(sid, cuisine_rule)
        self.assertEqual(len(scene["rules"]), 1)
        self.assert_app_error("SCENE_NOT_FOUND", store.delete_scene_rule, sid, cuisine_rule)

        # the category admin sees the rule as a use
        self.assertEqual(next(n for n in store.list_categories() if n["code"] == "dish.steak")["scene_rule_count"], 1)

        # deleting a scene takes its labels, rules and memberships; its code is still in the
        # members' payloads, so the delete asks for a sync
        image = self.insert(store, 1, category_id=cat["steak"], label_status="auto")
        store.recompute_scene_memberships(limit=10)
        self.assertEqual(store.get_effective_scene_codes(image), ["general", "oven", "steam_oven"])
        self.assertEqual(store.delete_scene(sid), {"id": sid, "code": "steam_oven", "sync_required": True})
        empty = store.create_scene({"code": "air_fryer", "name": "空气炸锅"})
        self.assertEqual(store.delete_scene(empty["id"])["sync_required"], False)  # never had members
        for table in ("scene_labels", "scene_rules", "image_scenes"):
            self.assertEqual(self.sql(f"SELECT count(*) FROM {table} WHERE scene_id = :s", {"s": sid}).scalar_one(), 0, table)
        self.assertEqual(store.get_effective_scene_codes(image), ["general", "oven"])
        self.assert_app_error("SCENE_NOT_FOUND", store.update_scene, sid, {"name": "x"})

    def test_writes_say_whether_stored_images_need_a_sync(self):
        cat = self.seed_catalog()
        store = self.store()

        def flag(result):
            return result["sync_required"]

        # A new rule-based scene has no members yet; a new active include_all one has every image
        rule_based = store.create_scene({"code": "steam_oven", "name": "蒸烤箱"})
        sid = rule_based["id"]
        self.assertFalse(flag(rule_based))
        self.assertTrue(flag(store.create_scene({"code": "air_fryer", "name": "空气炸锅", "include_all": True})))
        self.assertFalse(flag(store.create_scene({"code": "grill", "name": "烤架", "include_all": True, "is_active": False})))

        # Read per request: names, descriptions, outputs_heating, labels
        self.assertFalse(flag(store.update_scene(sid, {"name": "蒸烤箱 2", "description": "x", "outputs_heating": True})))
        self.assertFalse(flag(store.update_scene(sid, {"is_active": True, "include_all": False})))  # unchanged values
        scene = store.create_scene_label(sid, {"code": "steak", "name": "牛排"})
        self.assertFalse(flag(scene))
        label = scene["labels"][0]["id"]
        self.assertFalse(flag(store.update_scene_label(sid, label, {"name": "牛排 2", "is_active": False})))

        # Rules of an active rule-based scene change membership; a label mapping does not
        scene = store.create_scene_rule(sid, {"category_id": cat["dish"]})
        self.assertTrue(flag(scene))
        rule = scene["rules"][0]["id"]
        self.assertFalse(flag(store.update_scene_rule(sid, rule, {"label_id": label})))
        self.assertFalse(flag(store.update_scene_rule(sid, rule, {"category_id": cat["dish"], "cuisine": None})))  # same target
        self.assertTrue(flag(store.update_scene_rule(sid, rule, {"category_id": cat["steak"]})))
        self.assertFalse(flag(store.delete_scene_label(sid, store.create_scene_label(sid, {"code": "x", "name": "x"})["labels"][-1]["id"])))

        # Turning the scene off or into include_all changes membership; while it is off, nothing does
        self.assertTrue(flag(store.update_scene(sid, {"is_active": False})))
        self.assertFalse(flag(store.create_scene_rule(sid, {"cuisine": "american"})))
        self.assertFalse(flag(store.update_scene(sid, {"include_all": True})))  # still off
        self.assertTrue(flag(store.update_scene(sid, {"is_active": True})))  # on, with every image
        self.assertFalse(flag(store.delete_scene_rule(sid, rule)))  # an include_all scene ignores its rules
        self.assertTrue(flag(store.update_scene(sid, {"include_all": False})))

    def test_a_rule_target_changes_in_place(self):
        cat = self.seed_catalog()  # oven: rule on dish with label steak
        store = self.store()
        oven = store.get_scene_by_code("oven")["id"]
        label = cat["label"]
        rule = self.sql("SELECT id FROM scene_rules WHERE scene_id = :s", {"s": oven}).scalar_one()

        def rules():
            return [(r["id"], r["category"]["code"] if r["category"] else None, r["cuisine"], r["label"]["code"] if r["label"] else None)
                    for r in next(s for s in store.list_scene_configs() if s["id"] == oven)["rules"]]

        # Another category: same rule id, label kept
        store.update_scene_rule(oven, rule, {"category_id": cat["steak"]})
        self.assertEqual(rules(), [(rule, "dish.steak", None, "steak")])

        # A cuisine rule cannot keep a label: refused (nothing changes) until the label is cleared too
        detail = self.assert_app_error("VALIDATION_ERROR", store.update_scene_rule, oven, rule, {"cuisine": "american"})
        self.assertIn("label_id: null", detail)
        self.assertEqual(rules(), [(rule, "dish.steak", None, "steak")])
        result = store.update_scene_rule(oven, rule, {"cuisine": "american", "label_id": None})
        self.assertTrue(result["sync_required"])
        self.assertEqual(rules(), [(rule, None, "american", None)])

        # Back to a category, with a label, in one request
        store.update_scene_rule(oven, rule, {"category_id": cat["dish"], "label_id": label})
        self.assertEqual(rules(), [(rule, "dish", None, "steak")])

        # The target must be free in this scene, the category must exist, exactly one target
        other = store.create_scene_rule(oven, {"cuisine": "american"})["rules"][-1]["id"]
        self.assert_app_error("SCENE_RULE_EXISTS", store.update_scene_rule, oven, other, {"category_id": cat["dish"]})
        self.assert_app_error("SCENE_RULE_EXISTS", store.update_scene_rule, oven, rule, {"cuisine": "american", "label_id": None})
        self.assert_app_error("VALIDATION_ERROR", store.update_scene_rule, oven, rule, {"category_id": 987654})
        self.assert_app_error("VALIDATION_ERROR", store.update_scene_rule, oven, rule, {"category_id": cat["dish"], "cuisine": "x"})
        self.assert_app_error("VALIDATION_ERROR", store.update_scene_rule, oven, rule, {"category_id": None})
        self.assert_app_error("VALIDATION_ERROR", store.update_scene_rule, oven, rule, {})
        self.assertEqual(rules(), [(rule, "dish", None, "steak"), (other, None, "american", None)])

        # Stored images follow the new target at the next recompute
        steak = self.insert(store, 1, category_id=cat["steak"], label_status="auto")
        self.assertEqual(store.get_effective_scene_codes(steak), ["general", "oven"])
        store.delete_scene_rule(oven, other)
        store.update_scene_rule(oven, rule, {"cuisine": "chinese", "label_id": None})
        store.recompute_scene_memberships(limit=10)
        self.assertEqual(store.get_effective_scene_codes(steak), ["general"])

    def test_the_ledger_records_only_current_payload_versions(self):
        store = self.store()
        model_id = self.sql(
            "INSERT INTO embedding_models (name, version, dim, distance, input_size, weights_uri, collection, status) "
            "VALUES ('m', '1', 4, 'cosine', 336, 's3://w', 'c_ledger', 'current') RETURNING id"
        ).scalar_one()
        a, b = self.insert(store, 1), self.insert(store, 2)
        self.sql("UPDATE images SET payload_version = 3 WHERE id = :i", {"i": uuid.UUID(b)})
        gone = str(uuid.uuid4())  # deleted meanwhile: no row, no FK failure

        recorded = store.mark_payloads_pushed(model_id=model_id, versions={a: 1, b: 2, gone: 1})
        self.assertEqual(recorded, [a])  # b moved on to 3 after its payload was read
        self.assertEqual(store.mark_payloads_pushed(model_id=model_id, versions={b: 3}), [b])
        self.assertEqual(store.mark_payloads_pushed(model_id=model_id, versions={}), [])
        ledger = dict(self.sql("SELECT image_id::text, synced_version FROM image_embeddings").fetchall())
        self.assertEqual(ledger, {a: 1, b: 3})

    def test_batch_recompute_follows_the_rules_and_bumps_only_what_changed(self):
        cat = self.seed_catalog()  # oven: rule on dish (cuisines {american}) with label steak
        store = self.store()
        steak = self.insert(store, 1, category_id=cat["steak"], label_status="auto")
        plain = self.insert(store, 2)
        us_diet = store.get_scene_by_code("us_diet")["id"]
        oven = store.get_scene_by_code("oven")["id"]

        def versions():
            return dict(self.sql("SELECT id::text, payload_version FROM images").fetchall())

        # A new rule does nothing to stored images until they are recomputed
        store.create_scene_rule(us_diet, {"cuisine": "american"})
        self.assertEqual(store.get_effective_scene_codes(steak), ["general", "oven"])

        # Paging in id order; the cursor ends with None
        ordered = sorted([steak, plain])
        first = store.recompute_scene_memberships(limit=1)
        self.assertEqual((first["image_ids"], first["last_id"]), ([ordered[0]], ordered[0]))
        second = store.recompute_scene_memberships(after=first["last_id"], limit=1)
        self.assertEqual(second["image_ids"], [ordered[1]])
        self.assertEqual(store.recompute_scene_memberships(after=second["last_id"], limit=1),
                         {"image_ids": [], "changed_ids": [], "last_id": None})
        self.assertEqual(sorted(first["changed_ids"] + second["changed_ids"]), [steak])
        self.assertEqual(store.get_effective_scene_codes(steak), ["general", "oven", "us_diet"])  # cuisine inherited from dish
        self.assertEqual(versions(), {steak: 2, plain: 1})

        # Nothing changed: a second pass bumps nobody
        self.assertEqual(store.recompute_scene_memberships(limit=10)["changed_ids"], [])
        self.assertEqual(versions(), {steak: 2, plain: 1})

        # Removing a rule removes its members; a manual override survives recomputes
        store.set_manual_scene_membership(plain, "oven", is_member=True)
        oven_rule = self.sql("SELECT id FROM scene_rules WHERE scene_id = :s", {"s": oven}).scalar_one()
        store.delete_scene_rule(oven, oven_rule)
        store.recompute_scene_memberships(limit=10)
        self.assertEqual(store.get_effective_scene_codes(steak), ["general", "us_diet"])
        self.assertEqual(store.get_effective_scene_codes(plain), ["general", "oven"])
        rows = self.sql("SELECT origin, count(*) FROM image_scenes GROUP BY origin ORDER BY origin").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("manual", 1), ("rule", 1)])

        # Moving a category changes its subtree's memberships the same way
        store.create_scene_rule(oven, {"category_id": cat["dish"]})
        ingredient = store.create_category({"code": "ingredient", "names": [self.zh("食材")]})
        store.update_category(cat["steak"], {"parent_id": ingredient["id"]})  # no longer under dish
        store.recompute_scene_memberships(limit=10)
        self.assertEqual(store.get_effective_scene_codes(steak), ["general"])  # nor american any more

    def test_scene_labels_come_from_the_deepest_rule_with_an_active_label(self):
        cat = self.seed_catalog()  # oven: dish -> label steak
        store = self.store()
        oven = store.get_scene_by_code("oven")["id"]
        scene = store.create_scene_label(oven, {"code": "beef", "name": "牛肉"})
        beef = next(l["id"] for l in scene["labels"] if l["code"] == "beef")
        store.create_scene_rule(oven, {"category_id": cat["steak"], "label_id": beef})

        def mapped(*ids):
            return {cid: label["code"] for cid, label in store.map_categories_to_scene_labels(oven, list(ids)).items()}

        self.assertEqual(mapped(cat["steak"], cat["dish"]), {cat["steak"]: "beef", cat["dish"]: "steak"})
        store.update_scene_label(oven, beef, {"is_active": False})  # falls back to the broader rule
        self.assertEqual(mapped(cat["steak"]), {cat["steak"]: "steak"})
        steak_label = self.sql("SELECT id FROM scene_labels WHERE code = 'steak'").scalar_one()
        store.update_scene_label(oven, steak_label, {"is_active": False})
        self.assertEqual(mapped(cat["steak"], cat["dish"]), {})
        self.assertEqual(mapped(), {})

    def test_sync_rewrites_payloads_and_the_ledger(self):
        import uuid as uuid_mod

        from app.clients.qdrant import QdrantConnection, QdrantStore
        from app.repositories.vectors import QdrantVectorStore
        from app.services.retrieval.active_model import ActiveModel
        from app.services.retrieval.sync_scenes import SyncSceneMembershipsUseCase

        cat = self.seed_catalog()
        store = self.store()
        model_id = self.sql(
            "INSERT INTO embedding_models (name, version, dim, distance, input_size, weights_uri, collection, status) "
            "VALUES ('m', '1', 4, 'cosine', 336, 's3://w', 'c_sync', 'current') RETURNING id"
        ).scalar_one()
        steak = self.insert(store, 1, category_id=cat["steak"], label_status="auto", food_state="cooked")
        plain = self.insert(store, 2)
        orphan = self.insert(store, 3)  # in PG, never embedded into this collection

        vectors = QdrantVectorStore(QdrantStore(QdrantConnection(url=":memory:"), collection="c_sync", vector_dim=4))
        vectors.ensure_collection()
        self.addCleanup(vectors.close)
        for image, unit in ((steak, [1, 0, 0, 0]), (plain, [0, 1, 0, 0])):
            vectors.upsert(image, unit, {"scenes": ["general"], "category_id": None, "food_state": None, "label_status": "x"})
        store.upsert_image_embedding(model_id=model_id, image_id=steak, synced_version=1)  # plain: ledger write "failed"
        self.sql("UPDATE image_embeddings SET embedded_at = now() - interval '1 day'")

        model = ActiveModel(id=model_id, name="m", version="1", collection="c_sync", dim=4, distance="cosine")
        uc = SyncSceneMembershipsUseCase(store, vectors, model=model)
        totals = uc.run_all(limit=2)
        self.assertEqual(
            {k: totals[k] for k in ("processed", "payload_pushed", "payload_missing", "batches")},
            {"processed": 3, "payload_pushed": 2, "payload_missing": [orphan], "batches": 2},
        )
        points = {p["id"]: p["payload"] for p in vectors.search([1, 0, 0, 0], top_k=10)}
        self.assertEqual(points[steak], {"category_id": cat["steak"], "scenes": ["general", "oven"], "food_state": "cooked", "label_status": "auto"})
        self.assertEqual(points[plain]["scenes"], ["general"])
        self.assertEqual(len(vectors.search([1, 0, 0, 0], top_k=10, scene="oven")), 1)  # the filter sees the new payload
        ledger = {r[0]: (r[1], r[2]) for r in self.sql(
            "SELECT image_id::text, synced_version, embedded_at < now() - interval '1 hour' FROM image_embeddings"
        ).fetchall()}
        self.assertEqual(ledger, {steak: (1, True), plain: (1, False)})  # plain's row is new; steak's embedded_at kept

        # A rule change reaches the vector filter through the next sync
        oven = store.get_scene_by_code("oven")["id"]
        rule = self.sql("SELECT id FROM scene_rules WHERE scene_id = :s", {"s": oven}).scalar_one()
        store.delete_scene_rule(oven, rule)
        first = uc.run_batch(limit=10)
        self.assertEqual((first["membership_changed"], first["done"], first["next_after"]), (1, True, None))
        self.assertEqual(vectors.search([1, 0, 0, 0], top_k=10, scene="oven"), [])
        self.assertEqual(self.sql("SELECT synced_version FROM image_embeddings WHERE image_id = :i", {"i": uuid_mod.UUID(steak)}).scalar_one(), 2)

    def test_a_manual_membership_reaches_the_vector_filter_at_once(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api.v1 import scene_memberships as routes
        from app.bootstrap.state import ApplicationState
        from app.clients.qdrant import QdrantConnection, QdrantStore
        from app.core.exceptions import register_exception_handlers
        from app.core.middleware import TraceIdMiddleware
        from app.repositories.vectors import QdrantVectorStore
        from app.services.retrieval.active_model import ActiveModel
        from app.services.retrieval.manage_scenes import ManageScenesUseCase
        from app.services.retrieval.sync_scenes import SyncSceneMembershipsUseCase

        store = self.store()
        model_id = self.sql(
            "INSERT INTO embedding_models (name, version, dim, distance, input_size, weights_uri, collection, status) "
            "VALUES ('m', '1', 4, 'cosine', 336, 's3://w', 'c_manual', 'current') RETURNING id"
        ).scalar_one()
        plain = self.insert(store, 1)
        orphan = self.insert(store, 2)  # in PG, never embedded into this collection
        vectors = QdrantVectorStore(QdrantStore(QdrantConnection(url=":memory:"), collection="c_manual", vector_dim=4))
        vectors.ensure_collection()
        self.addCleanup(vectors.close)
        vectors.upsert(plain, [1, 0, 0, 0], {"scenes": ["general"], "category_id": None, "food_state": None, "label_status": "unlabeled"})
        model = ActiveModel(id=model_id, name="m", version="1", collection="c_manual", dim=4, distance="cosine")

        runtime = ApplicationState(
            config={}, config_info={}, http_client=Mock(), settings=SimpleNamespace(),
            catalog=SimpleNamespace(scenes_uc=ManageScenesUseCase(store)),
            retrieval=SimpleNamespace(scene_sync_uc=SyncSceneMembershipsUseCase(store, vectors, model=model)),
        )
        app = FastAPI()
        register_exception_handlers(app)
        app.add_middleware(TraceIdMiddleware)
        app.include_router(routes.router)
        client = TestClient(app)
        self.addCleanup(client.close)
        with patch.object(routes, "get_application_state", return_value=runtime):
            oven = store.get_scene_by_code("oven")["id"]
            path = f"/api/images/{plain}/scene-memberships/{oven}"

            def oven_hits():
                return [hit["id"] for hit in vectors.search([1, 0, 0, 0], top_k=5, scene="oven")]

            def synced(image):
                return self.sql(
                    "SELECT e.synced_version = i.payload_version FROM images i "
                    "JOIN image_embeddings e ON e.image_id = i.id WHERE i.id = :i", {"i": uuid.UUID(image)}
                ).scalar_one()

            # Included by hand: the vector filter sees it in the same request, the ledger agrees
            response = client.put(path, json={"is_member": True})
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual((body["vector_sync"], body["sync_required"], body["scenes"]), ("pushed", False, ["general", "oven"]))
            self.assertEqual(oven_hits(), [plain])
            self.assertTrue(synced(plain))

            # Back to the rules (none match): out of the filter again
            body = client.delete(path).json()
            self.assertEqual((body["vector_sync"], body["sync_required"], body["scenes"]), ("pushed", False, ["general"]))
            self.assertEqual(oven_hits(), [])

            # No vector in the current collection: saved in PG, search cannot see it
            body = client.put(f"/api/images/{orphan}/scene-memberships/{oven}", json={"is_member": True}).json()
            self.assertEqual((body["vector_sync"], body["sync_required"]), ("missing", True))
            self.assertEqual(store.get_effective_scene_codes(orphan), ["general", "oven"])

            # Qdrant fails: still saved, the client is told to sync
            with patch.object(vectors, "set_payloads", side_effect=RuntimeError("qdrant down")):
                body = client.put(path, json={"is_member": True}).json()
            self.assertEqual((body["vector_sync"], body["sync_required"], body["scenes"]), ("failed", True, ["general", "oven"]))
            self.assertEqual(oven_hits(), [])
            self.assertFalse(synced(plain))  # the ledger tells the truth: the payload is behind

            # Retrieval stack not built: PostgreSQL alone still takes the change
            runtime.retrieval, runtime.retrieval_error = None, "embedding model unavailable"
            body = client.put(path, json={"is_member": False}).json()
            self.assertEqual((body["vector_sync"], body["sync_required"], body["scenes"]), ("not_ready", True, ["general"]))

    def test_a_payload_superseded_while_it_was_written_is_pushed_again(self):
        from unittest.mock import patch

        from app.clients.qdrant import QdrantConnection, QdrantStore
        from app.repositories.vectors import QdrantVectorStore
        from app.services.retrieval.active_model import ActiveModel
        from app.services.retrieval.sync_scenes import SyncSceneMembershipsUseCase

        store = self.store()
        model_id = self.sql(
            "INSERT INTO embedding_models (name, version, dim, distance, input_size, weights_uri, collection, status) "
            "VALUES ('m', '1', 4, 'cosine', 336, 's3://w', 'c_race', 'current') RETURNING id"
        ).scalar_one()
        image = self.insert(store, 1)
        vectors = QdrantVectorStore(QdrantStore(QdrantConnection(url=":memory:"), collection="c_race", vector_dim=4))
        vectors.ensure_collection()
        self.addCleanup(vectors.close)
        vectors.upsert(image, [1, 0, 0, 0], {"scenes": ["general"], "category_id": None, "food_state": None, "label_status": "unlabeled"})
        model = ActiveModel(id=model_id, name="m", version="1", collection="c_race", dim=4, distance="cosine")
        uc = SyncSceneMembershipsUseCase(store, vectors, model=model)
        oven = store.get_scene_by_code("oven")["id"]

        # Another writer includes the image in oven (and pushes it) between our read and our write:
        # our payload is the older one, so it must not stay in the collection
        real_set = vectors.set_payloads
        calls = []

        def racing_set(payloads):
            calls.append({k: v["scenes"] for k, v in payloads.items()})
            if len(calls) == 1:
                store.put_manual_scene_membership(image, oven, is_member=True)
                uc.push_images([image])  # the other writer's own push lands first
            return real_set(payloads)

        with patch.object(vectors, "set_payloads", side_effect=racing_set):
            result = uc.push_images([image])
        self.assertEqual(calls[0], {image: ["general"]})  # the stale write ...
        self.assertEqual(calls[-1], {image: ["general", "oven"]})  # ... is followed by the current one
        self.assertEqual((list(result.pushed), result.missing, result.unconfirmed), ([image], [], []))
        self.assertEqual([hit["id"] for hit in vectors.search([1, 0, 0, 0], top_k=5, scene="oven")], [image])

    def run_while_locked(self, action, *, shared):
        """Start action in a thread while this test holds the catalog lock; True if it waited."""
        import threading

        from sqlalchemy import text

        from app.repositories.catalog_lock import CATALOG_LOCK_KEY

        done = threading.Event()
        failure = []

        def run():
            try:
                action()
            except Exception as exc:  # surfaced by the assertion below
                failure.append(exc)
            done.set()

        lock = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
        with self.engine.connect() as holder:
            tx = holder.begin()
            holder.execute(text(f"SELECT {lock}(:key)"), {"key": CATALOG_LOCK_KEY})
            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            waited = not done.wait(0.6)
            tx.commit()
            self.assertTrue(done.wait(10), "the action never finished after the lock was released")
            worker.join(10)
        self.assertEqual(failure, [])
        return waited

    def test_catalog_writes_and_recomputes_wait_for_the_catalog_lock(self):
        store = self.store()
        image = self.insert(store, 1)
        writes = {
            "category write": lambda: store.create_category({"code": "dish", "names": [self.zh("菜品")]}),
            "scene write": lambda: store.create_scene({"code": "steam_oven", "name": "蒸烤箱"}),
            "membership recompute": lambda: store.recompute_image_scenes(image, bump_version=False),
            "batch recompute": lambda: store.recompute_scene_memberships(limit=10),
        }
        for name, action in writes.items():
            with self.subTest(name):
                self.assertTrue(self.run_while_locked(action, shared=False), f"{name} did not wait for an admin write")
        # Recomputes only read the catalog: they share the lock with other readers
        self.assertFalse(self.run_while_locked(lambda: store.recompute_image_scenes(image, bump_version=False), shared=True))
        # ... while an admin write waits for them
        self.assertTrue(self.run_while_locked(lambda: store.create_scene({"code": "air_fryer", "name": "空气炸锅"}), shared=True))
        self.assertEqual([n["code"] for n in store.list_categories()], ["dish"])


if __name__ == "__main__":
    unittest.main()
