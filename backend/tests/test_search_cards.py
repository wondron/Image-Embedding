"""Both search channels answer with the same result card (contract §6.1.1, 2026-11-12):
tags / description / original_url / similarity at the top level, everything else in details."""

import unittest
from unittest.mock import Mock, patch

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import predict as predict_routes
from app.api.v1 import retrieval as retrieval_routes
from app.core.exceptions import register_exception_handlers
from app.core.rules.food_fields import FOOD_FIELDS
from app.services.predict.flyb_search import FlyBSearch
from app.services.predict.pipeline.c_embedding import GlobalFilter
from app.services.retrieval import search_image

CARD_KEYS = {"tags", "description", "original_url", "similarity", "details"}
FLYB_CONFIG = {"FLAB_CONFIG": {
    "API_URL": "https://search.invalid/global_search", "API_KEY": "test-only-key", "DOC_COUNT": 5,
    "MAX_SNIPPET_LENGTH": 300, "THRESHOLD": 0.8, "EnableWaiting": False, "Timeout": 1,
}}
# Shaped like the FlyB results logged on 2026-11-12: HostInfo {Hostname, IconUrl}, size names capitalized
FISH = {
    "Rank": 1, "Title": "清蒸鲈鱼", "Url": "https://site.example/fish", "HostInfo": {"Hostname": "下厨房", "IconUrl": ""},
    "DocumentInfo": {"PublishTime": "2026-01-01"},
    "Snippet": [
        {"Type": "text", "Text": " 鲈鱼一条 "},
        {"Type": "image", "Image": {
            "ImageUrl": "https://img.example/fish.jpg", "ThumbnailUrl": {"Large": "https://img.example/fish-l.jpg"},
            "Alt": "鲈鱼", "Width": 800, "Height": 600,
        }},
        {"Type": "text", "Text": "清蒸十分钟"},
        {"Type": "text", "Text": "  "},
    ],
}
THUMB_ONLY = {"Rank": 2, "Snippet": [{"Type": "image", "Image": {
    "ThumbnailUrl": {"Tiny": "https://img.example/t.jpg", "Small": "https://img.example/s.jpg", "Large": "https://img.example/l.jpg"},
}}]}
ROW = {
    "id": "abc", "original_url": "https://cdn/o/abc.jpg", "thumb_url": "https://cdn/t/abc.webp",
    "category_id": 7, "category_name": "牛排", "scenes": ["general", "oven"], "label_status": "auto",
    "description": "煎", "tags": ["牛排", "带骨"], "width": 8, "height": 6,
    "food_state": "raw", "food_processing": None, "food_count": 1, "total_mass_g": 300,
    "water_mass_g": None, "main_ingredient_mass_g": None,
    "heating_group": {"id": 1, "code": "grill", "name": "烤"},
}


def flyb_cards(*documents):
    return FlyBSearch(FLYB_CONFIG).parse_results({"ResponseMetadata": {"RequestId": "r-1"}, "Result": {"Documents": list(documents)}})


def self_built_card(outputs_heating=True, scene_label=None):
    return search_image._hit_from_row(dict(ROW), 0.91, scene_meta={"outputs_heating": outputs_heating}, scene_label=scene_label)


class CardShapeTests(unittest.TestCase):
    def test_flyb_documents_become_cards(self):
        fish, thumb_only, bare = flyb_cards(FISH, THUMB_ONLY, {"Rank": 3, "Title": 42})
        self.assertEqual(fish, {
            "tags": "清蒸鲈鱼",
            "description": "鲈鱼一条 清蒸十分钟",
            "original_url": "https://img.example/fish.jpg",
            "similarity": None,
            "details": {
                "rank": 1, "page_url": "https://site.example/fish", "site": {"Hostname": "下厨房", "IconUrl": ""},
                "images": [{
                    "image_url": "https://img.example/fish.jpg", "thumbnail_url": {"Large": "https://img.example/fish-l.jpg"},
                    "alt": "鲈鱼", "width": 800, "height": 600,
                }],
                "document_info": {"PublishTime": "2026-01-01"},
            },
        })
        # Without an original the largest thumbnail is the card's image: a URL, never the size map
        # (and Large, not the Tiny that comes first in FlyB's map)
        self.assertEqual((thumb_only["tags"], thumb_only["description"], thumb_only["original_url"]), ("", "", "https://img.example/l.jpg"))
        self.assertEqual((bare["tags"], bare["original_url"], bare["details"]["images"]), ("", None, []))
        for card in (fish, thumb_only, bare):
            self.assertEqual(set(card), CARD_KEYS)

    def test_self_built_rows_become_the_same_card(self):
        card = self_built_card(scene_label={"name": "牛排类"})
        self.assertEqual(set(card), CARD_KEYS)
        self.assertEqual(
            {key: card[key] for key in ("tags", "description", "original_url", "similarity")},
            {"tags": "牛排、带骨", "description": "煎", "original_url": "https://cdn/o/abc.jpg", "similarity": 0.91},
        )
        details = card["details"]
        self.assertEqual(details["images"], [{
            "image_url": "https://cdn/o/abc.jpg", "thumbnail_url": "https://cdn/t/abc.webp", "width": 8, "height": 6,
        }])
        self.assertLessEqual(set(details["images"][0]), set(flyb_cards(FISH)[0]["details"]["images"][0]))  # FlyB's image keys
        self.assertEqual(
            (details["id"], details["category_id"], details["category"], details["scenes"], details["label_status"], details["scene_label"]),
            ("abc", 7, "牛排", ["general", "oven"], "auto", "牛排类"),
        )
        self.assertEqual({key: details[key] for key in (*FOOD_FIELDS, "heating_group")}, {key: ROW[key] for key in (*FOOD_FIELDS, "heating_group")})
        # Conditional fields stay absent outside heating scenes / without a label mapping
        plain = self_built_card(outputs_heating=False)["details"]
        self.assertFalse({*FOOD_FIELDS, "heating_group", "scene_label"} & set(plain))


class FakeEmbedding:
    """Query and "near" point the same way; "far" is orthogonal."""

    def detect(self, image):
        return np.array([0.0, 1.0]) if image == "far" else np.array([1.0, 0.0])


class GlobalFilterTests(unittest.TestCase):
    def test_scored_cards_keep_their_shape_and_show_the_compared_image(self):
        fish, thumb_only = flyb_cards(FISH, THUMB_ONLY)
        downloads = {  # the fish original failed: its large thumbnail was compared
            "https://img.example/fish.jpg": ("https://img.example/fish-l.jpg", "near"),
            "https://img.example/l.jpg": ("https://img.example/l.jpg", "far"),
        }
        filtering = GlobalFilter({"GLOBAL_FILTER": {"switch": True, "threshold": 0.5}}, FakeEmbedding())
        with patch("app.services.predict.pipeline.c_embedding.resolve_image_bgr", side_effect=lambda card: downloads[card["original_url"]]):
            kept = filtering.detect("query", [fish, thumb_only])
        self.assertEqual(len(kept), 1)  # "far" is under the threshold
        self.assertEqual(set(kept[0]), CARD_KEYS)
        self.assertEqual((kept[0]["original_url"], kept[0]["similarity"]), ("https://img.example/fish-l.jpg", 1.0))

    def test_filter_off_passes_the_cards_through_unscored(self):
        cards = flyb_cards(FISH)
        passed = GlobalFilter({"GLOBAL_FILTER": {"switch": False, "threshold": 0.5}}, FakeEmbedding()).detect("query", cards)
        self.assertEqual(passed, cards)
        self.assertIsNone(passed[0]["similarity"])


class SameWireTests(unittest.TestCase):
    """Through HTTP both endpoints serialize their hits with the same top-level keys."""

    def setUp(self):
        self.runtime = Mock(http_timeout=5, max_image_bytes=1024)
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(predict_routes.router)
        app.include_router(retrieval_routes.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        for module in (predict_routes, retrieval_routes):
            p = patch.object(module, "get_application_state", return_value=self.runtime)
            p.start()
            self.addCleanup(p.stop)

    def post(self, path):
        response = self.client.post(path, files={"file": ("q.png", b"query", "image/png")})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_self_built_and_flyb_hits_have_the_same_card_keys(self):
        self.runtime.require_retrieval.return_value.search_uc.execute.return_value = {
            "source": "self", "scene": "oven", "outputs_heating": True,
            "items": [self_built_card(scene_label={"name": "牛排类"}), self_built_card(outputs_heating=False)],
        }
        # FoodService output, plus a stray top-level key the wire must not carry
        self.runtime.require_food_service.return_value.detect.return_value = [{**flyb_cards(FISH)[0], "image_url": "legacy"}]

        hits = self.post("/api/search")["items"]
        documents = self.post("/api/predict/searchImage_file")["result"]
        for card in (*hits, *documents):
            self.assertEqual(set(card), CARD_KEYS)
        self.assertEqual(hits[0]["details"]["heating_group"], {"id": 1, "code": "grill", "name": "烤"})
        self.assertEqual(hits[0]["details"]["scene_label"], "牛排类")
        # exclude_unset reaches into details: no empty conditional keys
        self.assertFalse({*FOOD_FIELDS, "heating_group", "scene_label"} & set(hits[1]["details"]))
        self.assertEqual(documents[0]["details"]["images"][0]["thumbnail_url"], {"Large": "https://img.example/fish-l.jpg"})


if __name__ == "__main__":
    unittest.main()
