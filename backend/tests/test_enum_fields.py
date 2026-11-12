"""Enum coercion at the service boundary (bad values -> VALIDATION_ERROR, not a DB 500)."""

import tempfile
import unittest
from pathlib import Path

from app.core.config.enum_store import EnumStore, set_enum_store
from app.core.enums import DEFAULT_SOURCE_TYPE, FOOD_STATE, SOURCE_TYPE, LabelStatus
from app.core.rules.enum_fields import coerce_enum, coerce_optional_enum
from app.core.errors import AppError

SHIPPED = Path(__file__).resolve().parents[1] / "config" / "param" / "enums.yaml"


class SystemEnumFieldTests(unittest.TestCase):
    def test_valid_values_and_members_pass_through(self):
        self.assertIs(coerce_enum(LabelStatus, " auto ", field="label_status"), LabelStatus.AUTO)
        self.assertIs(coerce_enum(LabelStatus, LabelStatus.VERIFIED, field="x"), LabelStatus.VERIFIED)

    def test_blank_uses_default_or_fails_when_required(self):
        self.assertIs(
            coerce_enum(LabelStatus, "", field="label_status", default=LabelStatus.UNLABELED),
            LabelStatus.UNLABELED,
        )
        with self.assertRaises(AppError) as ctx:
            coerce_enum(LabelStatus, None, field="label_status")
        self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")

    def test_unknown_value_names_field_and_allowed_values(self):
        with self.assertRaises(AppError) as ctx:
            coerce_optional_enum(LabelStatus, "done", field="label_status")
        self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")
        self.assertIn("label_status", str(ctx.exception))
        self.assertIn("verified", str(ctx.exception))


class EditableEnumFieldTests(unittest.TestCase):
    """Editable enums are checked against the enums file as it is now."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "enums.yaml"
        path.write_bytes(SHIPPED.read_bytes())
        self.store = EnumStore(path)
        self.addCleanup(set_enum_store, set_enum_store(self.store))

    def test_values_from_the_file_come_back_as_plain_strings(self):
        value = coerce_enum(SOURCE_TYPE, " crawled ", field="source_type")
        self.assertEqual((value, type(value)), ("crawled", str))
        self.assertEqual(
            coerce_enum(SOURCE_TYPE, "", field="source_type", default=DEFAULT_SOURCE_TYPE), "user_upload"
        )

    def test_optional_blank_is_none(self):
        self.assertIsNone(coerce_optional_enum(FOOD_STATE, None, field="food_state"))
        self.assertIsNone(coerce_optional_enum(FOOD_STATE, "  ", field="food_state"))

    def test_unknown_value_names_field_and_allowed_values(self):
        with self.assertRaises(AppError) as ctx:
            coerce_optional_enum(FOOD_STATE, "boiled", field="food_state")
        self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")
        self.assertIn("food_state", str(ctx.exception))
        self.assertIn("cooked", str(ctx.exception))

    def test_a_value_added_at_runtime_is_accepted_right_away(self):
        with self.assertRaises(AppError):
            coerce_optional_enum(FOOD_STATE, "thawed", field="food_state")
        self.store.add_value("food_state", "thawed", "解冻")
        self.assertEqual(coerce_optional_enum(FOOD_STATE, "thawed", field="food_state"), "thawed")


if __name__ == "__main__":
    unittest.main()
