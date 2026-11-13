"""Plan complete category-subtree codes before any database writes."""

import unittest

from app.core.errors import AppError
from app.core.rules.category_paths import plan_subtree_codes


class CategoryPathPlanTests(unittest.TestCase):
    def test_full_ancestry_is_rebuilt_from_parent_ids_with_case_preserved(self):
        # Input ordering and the old textual prefixes must not decide ancestry.
        nodes = [
            {"id": 4, "parent_id": 3, "code": "ingredient.DJiang"},
            {"id": 2, "parent_id": 1, "code": "ingredient.YeTiLe"},
            {"id": 6, "parent_id": 5, "code": "unrelated.SuanNi"},
            {"id": 3, "parent_id": 2, "code": "ingredient.BaiSYe"},
            {"id": 5, "parent_id": 3, "code": "ingredient.SNaiLe"},
        ]
        self.assertEqual(plan_subtree_codes(nodes, 2, "beverage.YeTiLe"), {
            2: "beverage.YeTiLe",
            3: "beverage.YeTiLe.BaiSYe",
            4: "beverage.YeTiLe.BaiSYe.DJiang",
            5: "beverage.YeTiLe.BaiSYe.SNaiLe",
            6: "beverage.YeTiLe.BaiSYe.SNaiLe.SuanNi",
        })
        self.assertEqual(nodes[0]["code"], "ingredient.DJiang")  # plan does not mutate input

    def test_renaming_only_the_parent_code_rewrites_all_descendants(self):
        nodes = [
            {"id": 2, "parent_id": 1, "code": "ingredient.YeTiLe"},
            {"id": 3, "parent_id": 2, "code": "ingredient.YeTiLe.BaiSYe"},
            {"id": 4, "parent_id": 3, "code": "ingredient.YeTiLe.BaiSYe.DJiang"},
        ]
        self.assertEqual(plan_subtree_codes(nodes, 2, "Ingredient.yETilE"), {
            2: "Ingredient.yETilE",
            3: "Ingredient.yETilE.BaiSYe",
            4: "Ingredient.yETilE.BaiSYe.DJiang",
        })

    def test_case_equivalent_leaf_segments_are_distinct_under_different_parents(self):
        nodes = [
            {"id": 1, "parent_id": None, "code": "ingredient"},
            {"id": 2, "parent_id": 1, "code": "ingredient.ParentA"},
            {"id": 3, "parent_id": 1, "code": "ingredient.ParentB"},
            {"id": 4, "parent_id": 2, "code": "anything.LeafXX"},
            {"id": 5, "parent_id": 3, "code": "anything.leafxx"},
        ]
        plan = plan_subtree_codes(nodes, 1, "Ingredient")
        self.assertEqual(plan[4], "Ingredient.ParentA.LeafXX")
        self.assertEqual(plan[5], "Ingredient.ParentB.leafxx")

    def test_case_equivalent_sibling_segments_are_rejected(self):
        nodes = [
            {"id": 1, "parent_id": None, "code": "ingredient"},
            {"id": 2, "parent_id": 1, "code": "old.ChildX"},
            {"id": 3, "parent_id": 1, "code": "other.childx"},
        ]
        with self.assertRaises(AppError) as caught:
            plan_subtree_codes(nodes, 1, "ingredient")
        self.assertEqual(caught.exception.code, "CATEGORY_CODE_EXISTS")

    def test_descendant_length_is_validated_before_a_plan_is_returned(self):
        nodes = [
            {"id": 1, "parent_id": None, "code": "ingredient"},
            {"id": 2, "parent_id": 1, "code": "old.ChildX"},
        ]
        with self.assertRaises(AppError) as caught:
            plan_subtree_codes(nodes, 1, "a" * 125)
        self.assertEqual(caught.exception.code, "VALIDATION_ERROR")
        self.assertIn("too long", caught.exception.detail)

    def test_root_only_and_root_code_validation(self):
        nodes = [{"id": 1, "parent_id": None, "code": "ingredient"}]
        self.assertEqual(plan_subtree_codes(nodes, 1, "Ingredient"), {1: "Ingredient"})
        with self.assertRaises(AppError) as caught:
            plan_subtree_codes(nodes, 1, "not a code")
        self.assertEqual(caught.exception.code, "VALIDATION_ERROR")

    def test_missing_root_disconnected_nodes_and_cycles_are_rejected(self):
        invalid = (
            ([{"id": 2, "parent_id": 1, "code": "old.ChildX"}], 1),
            ([{"id": 1, "parent_id": None, "code": "ingredient"},
              {"id": 2, "parent_id": 88, "code": "old.ChildX"}], 1),
            ([{"id": 1, "parent_id": None, "code": "ingredient"},
              {"id": 2, "parent_id": 3, "code": "old.ChildX"},
              {"id": 3, "parent_id": 2, "code": "old.ChildY"}], 1),
        )
        for nodes, root_id in invalid:
            with self.subTest(nodes=nodes):
                with self.assertRaises(AppError) as caught:
                    plan_subtree_codes(nodes, root_id, "ingredient")
                self.assertEqual(caught.exception.code, "VALIDATION_ERROR")


if __name__ == "__main__":
    unittest.main()
