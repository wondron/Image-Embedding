"""images.tags on the read side: the list tag filter and the tags field of list items / search hits."""

import unittest
from types import SimpleNamespace

from app.services.retrieval import search_image
from app.services.retrieval.list_images import ListImagesUseCase


class ListTagFilterTests(unittest.TestCase):
    def run_list(self, **kwargs):
        calls = {}

        def list_images(**kw):
            calls.update(kw)
            return [{"id": "i1", "tags": ["红薯叶"]}, {"id": "i2", "tags": None}], 2

        meta = SimpleNamespace(
            list_images=list_images, get_scene_by_code=lambda code: {"id": 1, "code": code, "is_active": True}
        )
        return ListImagesUseCase(meta).execute(**kwargs), calls

    def test_tag_is_stripped_and_blank_means_no_filter(self):
        self.assertEqual(self.run_list(tag="  红薯叶 ")[1]["tag"], "红薯叶")
        for blank in (None, "", "   "):  # a blank tag would otherwise match nothing
            with self.subTest(tag=blank):
                self.assertIsNone(self.run_list(tag=blank)[1]["tag"])

    def test_items_always_carry_a_tags_list(self):
        result, _ = self.run_list()
        self.assertEqual([item["tags"] for item in result["items"]], [["红薯叶"], []])


class SearchHitTagsTests(unittest.TestCase):
    def test_hits_always_carry_a_tags_list(self):
        def hit(row):
            return search_image._hit_from_row(row, 0.9, scene_meta=None, scene_label=None)["tags"]

        self.assertEqual(hit({"id": "i1", "tags": ["带皮"]}), ["带皮"])
        self.assertEqual(hit({"id": "i2"}), [])


if __name__ == "__main__":
    unittest.main()
