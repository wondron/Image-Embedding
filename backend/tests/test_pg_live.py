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

        label = store.map_category_to_scene_label("oven", cat["steak"])
        self.assertEqual(label["code"], "steak")

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

if __name__ == "__main__":
    unittest.main()
