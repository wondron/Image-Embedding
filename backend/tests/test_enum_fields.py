"""Enum coercion at the service boundary (bad values -> VALIDATION_ERROR, not a DB 500)."""

import unittest

from app.core.enums import FoodState, LabelStatus, SourceType
from app.services.retrieval.enum_fields import coerce_enum, coerce_optional_enum
from app.core.errors import AppError


class EnumFieldTests(unittest.TestCase):
    def test_valid_values_and_members_pass_through(self):
        self.assertIs(coerce_enum(LabelStatus, " auto ", field="label_status"), LabelStatus.AUTO)
        self.assertIs(coerce_enum(LabelStatus, LabelStatus.VERIFIED, field="x"), LabelStatus.VERIFIED)
        self.assertEqual(str(SourceType.USER_UPLOAD), "user_upload")

    def test_blank_uses_default_or_fails_when_required(self):
        self.assertIs(
            coerce_enum(SourceType, "", field="source_type", default=SourceType.USER_UPLOAD),
            SourceType.USER_UPLOAD,
        )
        with self.assertRaises(AppError) as ctx:
            coerce_enum(LabelStatus, None, field="label_status")
        self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")

    def test_optional_blank_is_none(self):
        self.assertIsNone(coerce_optional_enum(FoodState, None, field="food_state"))
        self.assertIsNone(coerce_optional_enum(FoodState, "  ", field="food_state"))

    def test_unknown_value_names_field_and_allowed_values(self):
        with self.assertRaises(AppError) as ctx:
            coerce_optional_enum(FoodState, "boiled", field="food_state")
        self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")
        self.assertIn("food_state", str(ctx.exception))
        self.assertIn("cooked", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
