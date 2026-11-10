"""Food annotation fields (stored in image_foods): rules and where they show up."""

import unittest
from types import SimpleNamespace

from app.core.errors import AppError
from app.services.retrieval import search_image
from app.services.retrieval.food_fields import (
    FOOD_FIELDS,
    PG_INT_MAX,
    coerce_food_field,
    coerce_optional_int,
)
from app.services.retrieval.list_images import ListImagesUseCase


class WholeNumberTests(unittest.TestCase):
    def test_whole_numbers_from_json_or_forms(self):
        for raw, expected in ((250, 250), ("250", 250), (" 3 ", 3), (250.0, 250), (PG_INT_MAX, PG_INT_MAX)):
            with self.subTest(raw=raw):
                self.assertEqual(coerce_optional_int(raw, field="total_mass_g", minimum=1), expected)

    def test_blank_means_unknown(self):
        for raw in (None, "", "   "):
            self.assertIsNone(coerce_optional_int(raw, field="food_count", minimum=1))

    def test_rejects_below_minimum_fractions_bools_and_junk(self):
        for raw in (0, -1, "0", 2.5, "2.5", True, False, "abc", PG_INT_MAX + 1, float("nan"), [3], b"3"):
            with self.subTest(raw=raw):
                with self.assertRaises(AppError) as ctx:
                    coerce_optional_int(raw, field="food_count", minimum=1)
                self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")
                self.assertIn("food_count", ctx.exception.detail)


class FoodFieldTests(unittest.TestCase):
    def test_the_six_fields_in_the_requested_order(self):
        self.assertEqual(FOOD_FIELDS, (
            "food_state", "food_processing", "food_count",
            "total_mass_g", "water_mass_g", "main_ingredient_mass_g",
        ))

    def test_every_field_is_optional(self):
        self.assertEqual({key: coerce_food_field(key, None) for key in FOOD_FIELDS}, dict.fromkeys(FOOD_FIELDS))

    def test_water_mass_may_be_zero_the_other_amounts_may_not(self):
        self.assertEqual(coerce_food_field("water_mass_g", 0), 0)
        for key in ("food_count", "total_mass_g", "main_ingredient_mass_g"):
            with self.subTest(key=key):
                with self.assertRaises(AppError):
                    coerce_food_field(key, 0)
        with self.assertRaises(AppError):
            coerce_food_field("water_mass_g", -1)

    def test_enum_values_come_from_the_enum_file(self):
        self.assertEqual(coerce_food_field("food_processing", " sliced "), "sliced")
        with self.assertRaises(AppError) as ctx:
            coerce_food_field("food_processing", "minced")
        self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")
        self.assertIn("shredded", ctx.exception.detail)  # names the allowed values

    def test_unknown_key_is_a_programming_error(self):
        with self.assertRaises(KeyError):
            coerce_food_field("food_colour", "red")


ROW = {
    "id": "i1", "original_url": "https://cdn/o/i1.jpg", "thumb_url": "https://cdn/t/i1.webp",
    "label_status": "auto", "food_state": "frozen", "food_processing": "whole", "food_count": 1,
    "total_mass_g": 1200, "water_mass_g": 300, "main_ingredient_mass_g": 900,
    "heating_group": {"id": 1, "code": "grill", "name": "烤"},
}
FOOD = {key: ROW[key] for key in FOOD_FIELDS}


class HeatingOutputTests(unittest.TestCase):
    """The food annotation goes with heating_group: only when the scene outputs_heating (§6.1 P9)."""

    def list_item(self, outputs_heating):
        meta = SimpleNamespace(
            list_images=lambda **kw: ([dict(ROW)], 1),
            get_scene_by_code=lambda code: {"id": 2, "code": code, "outputs_heating": outputs_heating, "is_active": True},
        )
        return ListImagesUseCase(meta).execute(scene="oven")["items"][0]

    def test_list_items(self):
        self.assertEqual({key: self.list_item(True)[key] for key in FOOD_FIELDS}, FOOD)
        self.assertFalse(set(FOOD_FIELDS) & set(self.list_item(False)))

    def test_search_hits(self):
        def hit(outputs_heating):
            return search_image._hit_from_row(
                dict(ROW), 0.9, scene_meta={"outputs_heating": outputs_heating}, scene_label=None
            )

        self.assertEqual({key: hit(True)[key] for key in FOOD_FIELDS}, FOOD)
        self.assertFalse(set(FOOD_FIELDS) & set(hit(False)))


if __name__ == "__main__":
    unittest.main()
