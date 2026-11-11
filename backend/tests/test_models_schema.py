"""ORM schema (app.models) — static checks that need no database."""

import re
import unittest
from pathlib import Path

from sqlalchemy import CheckConstraint, Enum, String
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import configure_mappers
from sqlalchemy.schema import CreateTable

import app.models  # noqa: F401
from app.core import enums
from app.db.base import Base

BACKEND = Path(__file__).resolve().parents[1]

# The 12 tables of contract v0.1 + image_foods (0006) + index_tasks (0008); ensure_schema checks
# whatever app.models registers
TABLES = {
    "heating_groups", "categories", "category_names", "images", "image_deletions", "scenes",
    "scene_labels", "scene_rules", "image_scenes", "embedding_models", "image_embeddings",
    "external_candidates", "image_foods", "index_tasks",
}
# editable enum (app.core.enums.EDITABLE_ENUMS) -> the column that stores it
EDITABLE_COLUMNS = {
    "source_type": ("images", "source_type"),
    "license": ("images", "license"),
    "food_state": ("image_foods", "food_state"),
    "food_processing": ("image_foods", "food_processing"),
}


class ModelsSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        configure_mappers()  # relationship strings resolve
        cls.tables = Base.metadata.tables

    def test_models_register_exactly_the_contract_tables(self):
        self.assertEqual(set(self.tables), TABLES)

    def test_every_constraint_and_index_follows_the_naming_convention(self):
        prefixes = ("pk_", "fk_", "uq_", "ck_", "ix_")
        dialect = postgresql.dialect()
        for table in self.tables.values():
            ddl = str(CreateTable(table).compile(dialect=dialect))
            names = re.findall(r"CONSTRAINT (\w+)", ddl)
            self.assertTrue(names, table.name)
            names += [str(ix.name) for ix in table.indexes]
            for name in names:
                self.assertTrue(name.startswith(prefixes), f"{table.name}: {name}")
            # PG truncates identifiers past 63 bytes; keep names exact
            self.assertTrue(all(len(n) <= 63 for n in names), names)

    def test_system_enum_columns_store_enum_values_with_check_constraints(self):
        expected = {
            ("images", "label_status"): enums.LabelStatus,
            ("category_names", "kind"): enums.NameKind,
            ("image_scenes", "origin"): enums.SceneOrigin,
            ("embedding_models", "distance"): enums.Distance,
            ("embedding_models", "status"): enums.ModelStatus,
            ("external_candidates", "review_status"): enums.ReviewStatus,
            ("index_tasks", "status"): enums.IndexTaskStatus,
        }
        for (table, column), enum_cls in expected.items():
            col_type = self.tables[table].c[column].type
            self.assertIsInstance(col_type, Enum, f"{table}.{column}")
            self.assertFalse(col_type.native_enum)
            self.assertEqual(list(col_type.enums), [m.value for m in enum_cls])
            self.assertEqual(col_type.length, enums.ENUM_VALUE_MAX_LEN)

    def test_editable_enum_columns_are_varchar_without_a_check_list(self):
        # values live in config/param/enums.yaml and grow at runtime (migration 0004)
        self.assertEqual(set(EDITABLE_COLUMNS), set(enums.EDITABLE_ENUMS))
        for table, column in EDITABLE_COLUMNS.values():
            col_type = self.tables[table].c[column].type
            self.assertNotIsInstance(col_type, Enum, column)  # Enum subclasses String
            self.assertIsInstance(col_type, String, column)
            self.assertEqual(col_type.length, enums.ENUM_VALUE_MAX_LEN)
            ddl = str(CreateTable(self.tables[table]).compile(dialect=postgresql.dialect()))
            self.assertNotIn(f"ck_{table}_{column}", ddl)
        ddl = str(CreateTable(self.tables["images"]).compile(dialect=postgresql.dialect()))
        self.assertIn("ck_images_label_status", ddl)

    def test_images_invariants_are_declared(self):
        checks = {
            str(c.name): str(c.sqltext)
            for c in self.tables["images"].constraints
            if isinstance(c, CheckConstraint) and c.name
        }
        self.assertIn("(label_status = 'unlabeled') = (category_id IS NULL)", checks.values())
        self.assertIn("width > 0 AND height > 0", checks.values())
        self.assertFalse(self.tables["images"].c.width.nullable)
        # the food annotation lives in image_foods since 0006
        self.assertFalse({"food_state", "food_processing", "food_count"} & set(self.tables["images"].c.keys()))

    def test_image_foods_is_an_optional_one_row_child_of_images(self):
        from app.services.retrieval.food_fields import FOOD_FIELDS

        foods = self.tables["image_foods"]
        self.assertEqual([c.name for c in foods.primary_key.columns], ["image_id"])  # at most one row per image
        (fk,) = foods.c.image_id.foreign_keys
        self.assertEqual((fk.column.table.name, fk.ondelete), ("images", "CASCADE"))
        for column in FOOD_FIELDS:  # 非必填
            self.assertTrue(foods.c[column].nullable, column)
        checks = {str(c.sqltext) for c in foods.constraints if isinstance(c, CheckConstraint)}
        self.assertEqual(checks, {
            "food_count > 0", "total_mass_g > 0", "water_mass_g >= 0", "main_ingredient_mass_g > 0",
        })

    def test_compiled_ddl_names_constraints_like_migration_0002(self):
        ddl = str(CreateTable(self.tables["scene_rules"]).compile(dialect=postgresql.dialect()))
        self.assertIn("fk_scene_rules_scene_id_label_id_scene_labels", ddl)
        self.assertIn("uq_scene_rules_scene_id_category_id", ddl)
        self.assertIn("ck_scene_rules_one_target", ddl)


class MigrationChainTests(unittest.TestCase):
    def test_migration_chain_is_linear(self):
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config(str(BACKEND / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND / "alembic"))
        script = ScriptDirectory.from_config(cfg)
        self.assertEqual(script.get_heads(), ["20261111_0008"])
        self.assertEqual(script.get_revision("20261111_0008").down_revision, "20261109_0007")
        self.assertEqual(script.get_revision("20261109_0007").down_revision, "20261109_0006")
        self.assertEqual(script.get_revision("20261109_0006").down_revision, "20261109_0005")
        self.assertEqual(script.get_revision("20261109_0005").down_revision, "20261109_0004")
        self.assertEqual(script.get_revision("20261109_0004").down_revision, "20261106_0003")
        self.assertEqual(script.get_revision("20261106_0003").down_revision, "20261105_0002")
        self.assertEqual(script.get_revision("20261105_0002").down_revision, "20261104_0001")

    def test_0002_downgrade_clears_every_comment_it_sets(self):
        import importlib.util

        path = BACKEND / "alembic" / "versions" / "20261105_0002_align_with_orm_models.py"
        spec = importlib.util.spec_from_file_location("mig_0002", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        set_count = mod.UPGRADE_SQL.count("COMMENT ON ")
        self.assertGreater(set_count, 80)
        self.assertEqual(mod.CLEAR_COMMENTS_SQL.count("IS NULL;"), set_count)

    def test_every_orm_comment_is_written_by_some_migration(self):
        import importlib.util

        upgrade_sql = ""
        for path in sorted((BACKEND / "alembic" / "versions").glob("*.py")):
            spec = importlib.util.spec_from_file_location(f"mig_{path.stem}", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            upgrade_sql += getattr(mod, "UPGRADE_SQL", "")
        for table in Base.metadata.tables.values():
            self.assertIn(f"COMMENT ON TABLE {table.name} IS", upgrade_sql)
            for col in table.columns:
                if col.comment:
                    self.assertIn(f"COMMENT ON COLUMN {table.name}.{col.name} IS", upgrade_sql)

    def test_images_description_defaults_to_empty_string(self):
        column = Base.metadata.tables["images"].c.description
        self.assertFalse(column.nullable)
        self.assertEqual(str(column.server_default.arg), "''")

    def test_images_tags_is_a_text_array_defaulting_to_empty_with_a_gin_index(self):
        images = Base.metadata.tables["images"]
        column = images.c.tags
        self.assertIsInstance(column.type, postgresql.ARRAY)
        self.assertFalse(column.nullable)
        self.assertEqual(str(column.server_default.arg), "'{}'")
        (index,) = [ix for ix in images.indexes if ix.name == "ix_images_tags_gin"]
        self.assertEqual([c.name for c in index.columns], ["tags"])
        self.assertEqual(index.dialect_options["postgresql"]["using"], "gin")


if __name__ == "__main__":
    unittest.main()
