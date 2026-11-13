"""scripts.seed_data / scripts.reset_data.

Offline: the base data passes the same rules as API writes, and every table is classified as
reset or kept. Live (TEST_DATABASE_URL, an EMPTY throwaway database; the schema is dropped and
recreated through Alembic): seeding is idempotent, reset clears and restores.
"""

import os
import unittest
from pathlib import Path

TEST_URL = os.getenv("TEST_DATABASE_URL", "").strip()
BACKEND = Path(__file__).resolve().parents[1]


class BaseDataDefinitionTests(unittest.TestCase):
    def test_scenes_are_the_three_defaults_and_pass_the_api_rules(self):
        from app.core.scene_rules import DEFAULT_SCENE
        from scripts.seed_data import BASE_SCENES, _scene_fields

        self.assertEqual([s.code for s in BASE_SCENES], ["general", "oven", "range_hood"])
        self.assertEqual([s.name for s in BASE_SCENES], ["通用", "一体机", "抽油烟机"])
        for spec in BASE_SCENES:
            _scene_fields(spec)  # raises VALIDATION_ERROR otherwise
        default = next(s for s in BASE_SCENES if s.code == DEFAULT_SCENE)
        self.assertTrue(default.include_all and default.is_active)
        self.assertTrue(next(s for s in BASE_SCENES if s.code == "oven").outputs_heating)

    def test_roots_are_the_six_domains_in_order_and_pass_the_api_rules(self):
        import yaml

        from app.core.enums import CategoryDomain
        from scripts.seed_data import ROOT_CATEGORIES, _root_fields

        self.assertEqual([r.code for r in ROOT_CATEGORIES], [d.value for d in CategoryDomain])
        labels = {
            v["value"]: v["label"]
            for v in yaml.safe_load((BACKEND / "config/param/enums.yaml").read_text(encoding="utf-8"))[
                "category_domain"
            ]["values"]
        }
        self.assertEqual([r.zh_name for r in ROOT_CATEGORIES], [labels[r.code] for r in ROOT_CATEGORIES])
        for order, spec in enumerate(ROOT_CATEGORIES):
            self.assertEqual(_root_fields(spec, order)["sort_order"], order)

    def test_every_table_is_either_reset_or_kept(self):
        import app.models  # noqa: F401
        from app.db.base import Base
        from scripts.reset_data import CATALOG_TABLES, IMAGE_TABLES, KEPT_TABLES, RESET_TABLES

        self.assertFalse(set(RESET_TABLES) & set(KEPT_TABLES))
        self.assertEqual(set(IMAGE_TABLES) | set(CATALOG_TABLES), set(RESET_TABLES))
        # A new table must be classified here before reset_data knows what to do with it
        self.assertEqual(set(RESET_TABLES) | set(KEPT_TABLES), set(Base.metadata.tables))

    def test_reset_refuses_prod_before_touching_anything(self):
        import contextlib
        import io
        from unittest.mock import patch

        from scripts import reset_data
        from scripts.seed_data import Target

        target = Target(config={}, pg=None, profile="prod", nacos_enabled=True)
        target.describe = lambda: "prod target"  # type: ignore[method-assign]
        with patch.object(reset_data, "load_target", return_value=target), \
                patch.object(reset_data, "create_db_engine") as engine, \
                contextlib.redirect_stderr(io.StringIO()) as err, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(reset_data.main(["--yes"]), 2)
        engine.assert_not_called()
        self.assertIn("NACOS_ENV=test", err.getvalue())


class QdrantClearTests(unittest.TestCase):
    def test_delete_all_points_keeps_the_collection(self):
        from app.clients.qdrant import QdrantConnection, QdrantStore

        store = QdrantStore(QdrantConnection(url=":memory:"), collection="seed_reset_qa", vector_dim=4)
        store.ensure_collection()
        for i in range(3):
            store.upsert(f"00000000-0000-0000-0000-00000000000{i}", [1, 0, 0, i], {"scenes": ["general"]})
        self.assertEqual(store.count(), 3)
        store.delete_all_points()
        self.assertEqual(store.count(), 0)
        self.assertTrue(store.collection_exists())
        store.close()


def _alembic(cmd: str, rev: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.attributes["database_url"] = TEST_URL
    getattr(command, cmd)(cfg, rev)


@unittest.skipUnless(TEST_URL, "TEST_DATABASE_URL not set")
class SeedResetLiveTests(unittest.TestCase):
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

    def seed(self):
        from scripts.seed_data import seed_base_data

        with self.engine.begin() as conn:
            return seed_base_data(conn)

    def scenes(self):
        return [tuple(r) for r in self.sql(
            "SELECT id, code, name, include_all, outputs_heating, is_active FROM scenes ORDER BY id")]

    def roots(self):
        return [tuple(r) for r in self.sql(
            "SELECT c.code, n.name, c.sort_order, c.path::text FROM categories c "
            "JOIN category_names n ON n.category_id = c.id AND n.lang = 'zh' AND n.kind = 'primary' "
            "WHERE c.parent_id IS NULL ORDER BY c.sort_order, c.id")]

    def test_migrations_create_no_rows(self):
        self.assertEqual(self.sql("SELECT count(*) FROM scenes").scalar_one(), 0)
        self.assertEqual(self.sql("SELECT count(*) FROM categories").scalar_one(), 0)

    def test_seed_fills_an_empty_database_and_is_idempotent(self):
        report = self.seed()
        self.assertEqual(len(report.created), 9)
        self.assertTrue(report.complete)
        self.assertEqual(self.scenes(), [
            (1, "general", "通用", True, False, True),
            (2, "oven", "一体机", False, True, True),
            (3, "range_hood", "抽油烟机", False, False, True),
        ])
        self.assertEqual(self.roots(), [
            ("ingredient", "食材", 0, "1"), ("dish", "菜品", 1, "2"), ("dessert", "甜点烘焙", 2, "3"),
            ("beverage", "饮品", 3, "4"), ("mixed", "混合", 4, "5"), ("other", "其他", 5, "6"),
        ])
        again = self.seed()
        self.assertEqual(again.created, [])
        self.assertEqual(len(again.kept), 9)
        self.assertEqual(again.warnings, [])
        self.assertEqual(len(self.scenes()), 3)
        self.assertEqual(len(self.roots()), 6)

    def test_seed_only_fills_gaps_and_never_overwrites(self):
        self.seed()
        self.sql("UPDATE scenes SET name = '一体机 Pro' WHERE code = 'oven'")
        self.sql("DELETE FROM scenes WHERE code = 'range_hood'")
        self.sql("INSERT INTO scenes (code, name) VALUES ('steam_oven', '蒸烤箱')")
        self.sql("DELETE FROM categories WHERE code = 'mixed'")
        report = self.seed()
        self.assertEqual(report.created, ["场景 range_hood（抽油烟机）", "根类别 mixed（混合）"])
        self.assertEqual(len(report.warnings), 1)  # oven's name differs; kept
        self.assertEqual(self.sql("SELECT name FROM scenes WHERE code = 'oven'").scalar_one(), "一体机 Pro")
        self.assertEqual(self.sql("SELECT count(*) FROM scenes WHERE code = 'steam_oven'").scalar_one(), 1)

    def test_seed_reports_what_it_cannot_fill(self):
        self.sql("INSERT INTO categories (code) VALUES ('Dish')")  # same code ignoring case
        dish = self.sql("SELECT id FROM categories WHERE code = 'Dish'").scalar_one()
        self.sql("INSERT INTO categories (code, parent_id) VALUES ('dessert', :p)", {"p": dish})
        for code in ("legacy_a", "legacy_b", "legacy_c", "legacy_d"):
            self.sql("INSERT INTO categories (code) VALUES (:c)", {"c": code})
        report = self.seed()
        self.assertFalse(report.complete)
        # Dish kept; dessert is not a root; 5 roots + ingredient = 6, so beverage / mixed / other blocked
        self.assertEqual(len(report.problems), 4)
        self.assertEqual(self.sql("SELECT count(*) FROM categories WHERE parent_id IS NULL").scalar_one(), 6)

    def test_reset_clears_business_data_and_restores_base_data(self):
        from scripts.reset_data import RESET_TABLES, truncate_and_seed

        self.seed()
        dish = self.sql("SELECT id FROM categories WHERE code = 'dish'").scalar_one()
        self.sql("INSERT INTO categories (code, parent_id) VALUES ('dish.steak', :p)", {"p": dish})
        self.sql("INSERT INTO scenes (code, name) VALUES ('steam_oven', '蒸烤箱')")
        self.sql("INSERT INTO heating_groups (code, name) VALUES ('grill', '烤')")
        self.sql("INSERT INTO index_tasks (filename, status, error_code) VALUES ('a.jpg', 'failed', 'X')")
        with self.engine.begin() as conn:
            report = truncate_and_seed(conn, RESET_TABLES)
        self.assertEqual(len(report.created), 9)
        self.assertEqual([s[:2] for s in self.scenes()], [(1, "general"), (2, "oven"), (3, "range_hood")])
        self.assertEqual([r[0] for r in self.roots()], ["ingredient", "dish", "dessert", "beverage", "mixed", "other"])
        self.assertEqual(self.sql("SELECT count(*) FROM categories").scalar_one(), 6)
        self.assertEqual(self.sql("SELECT count(*) FROM index_tasks").scalar_one(), 0)
        self.assertEqual(self.sql("SELECT count(*) FROM heating_groups").scalar_one(), 1)  # kept

    def test_reset_keep_catalog_keeps_the_tree_and_scene_config(self):
        from scripts.reset_data import IMAGE_TABLES, truncate_and_seed

        self.seed()
        dish = self.sql("SELECT id FROM categories WHERE code = 'dish'").scalar_one()
        self.sql("INSERT INTO categories (code, parent_id) VALUES ('dish.steak', :p)", {"p": dish})
        self.sql("INSERT INTO index_tasks (filename, status, error_code) VALUES ('a.jpg', 'failed', 'X')")
        with self.engine.begin() as conn:
            report = truncate_and_seed(conn, IMAGE_TABLES)
        self.assertEqual(report.created, [])
        self.assertEqual(self.sql("SELECT count(*) FROM categories").scalar_one(), 7)
        self.assertEqual(self.sql("SELECT count(*) FROM index_tasks").scalar_one(), 0)


if __name__ == "__main__":
    unittest.main()
