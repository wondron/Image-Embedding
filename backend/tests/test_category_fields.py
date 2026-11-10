"""Category tree write rules (app.services.retrieval.category_fields): no database needed."""

import unittest

from app.core.category_rules import CATEGORY_CODE_MAX_LEN, CATEGORY_MAX_CUISINES, CATEGORY_MAX_NAMES
from app.core.enums import CategoryDomain, NameKind
from app.core.errors import AppError
from app.services.retrieval.category_fields import (
    ROOT_CODES,
    check_root_code,
    display_name,
    normalize_category_code,
    normalize_category_fields,
    normalize_category_id,
    normalize_category_names,
    normalize_cuisines,
)

ZH = {"lang": "zh", "name": "披萨", "kind": "primary"}


class CategoryFieldsTests(unittest.TestCase):
    def assert_invalid(self, fn, *args, **kwargs):
        with self.assertRaises(AppError) as ctx:
            fn(*args, **kwargs)
        self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")
        return ctx.exception.detail

    def test_create_strips_text_and_fills_the_optional_fields(self):
        out = normalize_category_fields({"code": " dish.pizza ", "names": [dict(ZH, name=" 披萨 ")]}, partial=False)
        self.assertEqual(out, {
            "code": "dish.pizza", "parent_id": None, "names": [ZH], "cuisines": [],
            "heating_group_id": None, "sort_order": 0, "is_active": True,
        })
        full = {
            "code": "dish.pizza", "parent_id": 3, "names": [ZH], "cuisines": ["italian"],
            "heating_group_id": 2, "sort_order": -5, "is_active": False,
        }
        self.assertEqual(normalize_category_fields(full, partial=False), full)

    def test_create_requires_code_and_names(self):
        self.assertIn("code is required", self.assert_invalid(normalize_category_fields, {"names": [ZH]}, partial=False))
        self.assertIn("names is required", self.assert_invalid(normalize_category_fields, {"code": "dish"}, partial=False))

    def test_patch_keeps_only_the_given_keys(self):
        self.assertEqual(normalize_category_fields({"sort_order": 3}, partial=True), {"sort_order": 3})
        # null is a value for the two nullable columns: move to the root, clear the heating group
        self.assertEqual(
            normalize_category_fields({"parent_id": None, "heating_group_id": None}, partial=True),
            {"parent_id": None, "heating_group_id": None},
        )

    def test_patch_rejects_nothing_unknown_keys_and_null_for_required_columns(self):
        self.assertIn("no category fields", self.assert_invalid(normalize_category_fields, {}, partial=True))
        # path is maintained by the database; id never changes
        self.assertIn("path", self.assert_invalid(normalize_category_fields, {"path": "1.2"}, partial=True))
        self.assert_invalid(normalize_category_fields, {"id": 9, "sort_order": 1}, partial=True)
        self.assert_invalid(normalize_category_fields, ["code"], partial=True)
        for key in ("code", "names", "cuisines", "sort_order", "is_active"):
            with self.subTest(key=key):
                self.assert_invalid(normalize_category_fields, {key: None}, partial=True)

    def test_code_spelling(self):
        for good in ("dish", "dish.pizza", "dish.staple_food.rice2", "a.1"):
            with self.subTest(code=good):
                self.assertEqual(normalize_category_code(good), good)
        for bad in ("Dish", "dish..pizza", ".dish", "dish.", "dish pizza", "披萨", "1dish", "dish-pizza",
                    "", "   ", "x" * (CATEGORY_CODE_MAX_LEN + 1), 5, None):
            with self.subTest(code=bad):
                self.assert_invalid(normalize_category_code, bad)

    def test_names_keep_their_order_and_accept_the_enum_member(self):
        names = [
            {"lang": "en", "name": "Pizza", "kind": NameKind.PRIMARY},
            {"lang": "zh", "name": "比萨", "kind": "alias"},
            ZH,
        ]
        self.assertEqual(normalize_category_names(names), [
            {"lang": "en", "name": "Pizza", "kind": "primary"},
            {"lang": "zh", "name": "比萨", "kind": "alias"},
            ZH,
        ])

    def test_names_rules(self):
        cases = {
            "no zh primary": [{"lang": "en", "name": "Pizza", "kind": "primary"}],
            "zh only as alias": [dict(ZH, kind="alias")],
            "two primaries in one language": [ZH, dict(ZH, name="比萨")],
            "the same name twice": [ZH, dict(ZH, kind="alias")],
            "upper-case language": [ZH, {"lang": "EN", "name": "Pizza", "kind": "primary"}],
            "language spelled out": [ZH, {"lang": "english", "name": "Pizza", "kind": "primary"}],
            "unknown kind": [dict(ZH, kind="main")],
            "missing kind": [{"lang": "zh", "name": "披萨"}],
            "blank name": [dict(ZH, name="  ")],
            "name too long": [dict(ZH, name="长" * 129)],
            "not an object": [ZH, "披萨"],
            "empty": [],
            "not a list": {"zh": "披萨"},
            "too many": [ZH] + [{"lang": "zh", "name": f"别名{i}", "kind": "alias"} for i in range(CATEGORY_MAX_NAMES)],
        }
        for label, names in cases.items():
            with self.subTest(label):
                self.assert_invalid(normalize_category_names, names)
        self.assertIn("names[1]", self.assert_invalid(normalize_category_names, cases["two primaries in one language"]))

    def test_cuisines_are_deduplicated_in_order(self):
        self.assertEqual(normalize_cuisines([" italian ", "american", "italian"]), ["italian", "american"])
        self.assertEqual(normalize_cuisines([]), [])
        for bad in (["Italian"], [""], ["zh-cn"], [3], "italian", None,
                    [f"c{i}" for i in range(CATEGORY_MAX_CUISINES + 1)]):
            with self.subTest(cuisines=bad):
                self.assert_invalid(normalize_cuisines, bad)

    def test_ids_sort_order_and_flags_are_strict(self):
        self.assertEqual(normalize_category_id(7), 7)
        for bad in (0, -1, True, "7", 7.0, None, 2**31):
            with self.subTest(category_id=bad):
                self.assert_invalid(normalize_category_id, bad)
        for field, bad_values in (
            ("parent_id", (0, True, "3")),
            ("heating_group_id", (-2, 1.5)),
            ("sort_order", (True, 1.5, "2", 2**31)),
            ("is_active", (1, "true", 0)),
        ):
            for bad in bad_values:
                with self.subTest(field=field, value=bad):
                    self.assert_invalid(normalize_category_fields, {field: bad}, partial=True)

    def test_only_the_domains_may_be_roots(self):
        self.assertEqual(set(ROOT_CODES), {member.value for member in CategoryDomain})
        for code in ROOT_CODES:
            check_root_code(code)
        self.assertIn("needs a parent", self.assert_invalid(check_root_code, "dish.pizza"))
        self.assert_invalid(check_root_code, "snack")

    def test_display_name_is_the_zh_primary(self):
        self.assertEqual(display_name([{"lang": "en", "name": "Pizza", "kind": "primary"}, ZH]), "披萨")
        self.assertIsNone(display_name([{"lang": "en", "name": "Pizza", "kind": "primary"}, dict(ZH, kind="alias")]))
        self.assertIsNone(display_name([]))


if __name__ == "__main__":
    unittest.main()
