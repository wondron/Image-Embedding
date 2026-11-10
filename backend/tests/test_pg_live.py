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

    def test_category_writes_wait_for_the_tree_lock(self):
        import threading

        from sqlalchemy import text

        from app.repositories.categories import _TREE_LOCK_KEY

        store = self.store()
        done = threading.Event()

        def create():
            store.create_category({"code": "dish", "names": [self.zh("菜品")]})
            done.set()

        with self.engine.connect() as holder:
            tx = holder.begin()
            holder.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _TREE_LOCK_KEY})
            worker = threading.Thread(target=create, daemon=True)
            worker.start()
            self.assertFalse(done.wait(0.6), "the write did not wait for the tree lock")
            tx.commit()
            self.assertTrue(done.wait(10), "the write never finished after the lock was released")
            worker.join(10)
        self.assertEqual([n["code"] for n in store.list_categories()], ["dish"])


if __name__ == "__main__":
    unittest.main()
