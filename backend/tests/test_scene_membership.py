"""Pure scene-membership rules (include_all, rules, manual overrides, payload)."""

import unittest

from app.core.enums import LabelStatus
from app.core.rules.scene_membership import (
    apply_manual_overrides,
    build_qdrant_payload,
    compute_rule_member_scene_ids,
    include_all_scene_ids,
)

SCENES = [
    {"id": 1, "code": "general", "include_all": True},
    {"id": 2, "code": "oven", "include_all": False},
    {"id": 3, "code": "us_diet", "include_all": False},
]
RULES = {
    2: [{"id": 10, "scene_id": 2, "category_id": 7, "cuisine": None, "label_id": None}],
    3: [{"id": 11, "scene_id": 3, "category_id": None, "cuisine": "american", "label_id": None}],
}
PATHS = {7: "1.7"}


class SceneMembershipTests(unittest.TestCase):
    def members(self, *, category_id, path, cuisines=()):
        return compute_rule_member_scene_ids(
            scenes=SCENES,
            rules_by_scene=RULES,
            rule_cat_paths=PATHS,
            image_category_id=category_id,
            image_path=path,
            image_cuisines=list(cuisines),
        )

    def test_include_all_scenes_are_never_rule_members_to_persist(self):
        self.assertEqual(self.members(category_id=None, path=None), set())
        self.assertNotIn(1, self.members(category_id=9, path="1.7.9", cuisines=["american"]))

    def test_category_rule_matches_subtree_and_cuisine_rule_matches_cuisine(self):
        self.assertEqual(self.members(category_id=9, path="1.7.9"), {2})
        self.assertEqual(self.members(category_id=8, path="1.8", cuisines=["american"]), {3})
        # "1.70" is not under "1.7"
        self.assertEqual(self.members(category_id=70, path="1.70"), set())

    def test_include_all_ids(self):
        self.assertEqual(include_all_scene_ids(SCENES), {1})

    def test_manual_rows_override_rules_both_ways(self):
        effective = apply_manual_overrides(
            {2}, [{"scene_id": 2, "is_member": False}, {"scene_id": 3, "is_member": True}]
        )
        self.assertEqual(effective, {3})

    def test_payload_holds_plain_strings(self):
        payload = build_qdrant_payload(
            category_id=7,
            scene_codes=["general", "oven"],
            food_state="cooked",  # editable enum: a plain str from config/param/enums.yaml
            label_status=LabelStatus.AUTO,
        )
        self.assertEqual(
            payload,
            {"category_id": 7, "scenes": ["general", "oven"], "food_state": "cooked", "label_status": "auto"},
        )
        self.assertIs(type(payload["label_status"]), str)
        self.assertIs(type(payload["food_state"]), str)
        empty = build_qdrant_payload(category_id=None, scene_codes=[], food_state=None, label_status=None)
        self.assertEqual(empty["label_status"], "unlabeled")
        self.assertIsNone(empty["food_state"])


if __name__ == "__main__":
    unittest.main()
