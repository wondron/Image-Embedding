"""One stored image: the real routes, adapters, use cases and field rules over in-memory stores
(what needs SQL or Qdrant is in test_pg_live).

- a patch carries only the fields sent, with their null meanings (image_fields);
- label_status follows the category unless it is sent (resolve_label_status);
- the detail compares the point's payload with PostgreSQL (vector_status);
- writes are pushed / cleaned up when retrieval is up and are saved whatever that does.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.router import router as api_router
from app.api.v1 import images as image_routes
from app.bootstrap.state import ApplicationState
from app.core import errors as err
from app.core.enums import LabelStatus
from app.core.exceptions import register_exception_handlers
from app.core.middleware import TraceIdMiddleware
from app.services.retrieval.active_model import ActiveModel
from app.services.retrieval.delete_image import DeletionWorker
from app.services.retrieval.image_fields import normalize_image_patch, resolve_label_status
from app.services.retrieval.manage_images import ManageImagesUseCase
from app.services.retrieval.sync_scenes import PushResult
from app.services.retrieval.vector_status import ImageVectorStatusUseCase, payload_matches

IMAGE = "0cf457e2-4a47-9f02-fd4d-34540389f720"
OTHER = "11111111-2222-3333-4444-555555555555"
MODEL = ActiveModel(id=3, name="dino", version="v2", collection="c_dino_v2", dim=4, distance="cosine")
FOOD = {"food_state": "cooked", "food_processing": None, "food_count": 2, "total_mass_g": 350,
        "water_mass_g": None, "main_ingredient_mass_g": None}
ROW = {
    "id": IMAGE, "sha256": "ab" * 32, "original_url": "https://cdn/o.jpg", "thumb_url": "https://cdn/t.webp",
    "width": 640, "height": 480, "category_id": 12, "category_name": "牛排", "category_code": "dish.steak",
    "label_status": "auto", "scenes": ["general", "oven"], "description": "煎牛排", "tags": ["带骨"],
    "source_type": "user_upload", "license": "unknown", **FOOD,
    "heating_group": {"id": 1, "code": "grill", "name": "烤"}, "payload_version": 4,
    "created_at": "2026-11-10T08:00:00+00:00", "updated_at": "2026-11-11T08:00:00+00:00",
    "embeddings": [
        {"model_id": 2, "name": "dino", "version": "v1", "collection": "c_dino_v1", "model_status": "retired",
         "synced_version": 1, "embedded_at": "2026-11-01T00:00:00+00:00"},
        {"model_id": 3, "name": "dino", "version": "v2", "collection": "c_dino_v2", "model_status": "current",
         "synced_version": 4, "embedded_at": "2026-11-10T08:00:01+00:00"},
    ],
}
SYNCED_PAYLOAD = {"category_id": 12, "scenes": ["oven", "general"], "food_state": "cooked", "label_status": "auto"}


class FieldRuleTests(unittest.TestCase):
    def test_a_patch_keeps_only_the_fields_sent_with_their_null_meaning(self):
        patch_ = normalize_image_patch(
            {"category_id": None, "description": None, "tags": [" 清蒸 ", "清蒸", ""], "food_count": "3",
             "food_state": None}
        )
        self.assertEqual(
            patch_, {"category_id": None, "description": "", "tags": ["清蒸"], "food_state": None, "food_count": 3}
        )
        self.assertEqual(normalize_image_patch({"tags": None}), {"tags": []})
        self.assertEqual(normalize_image_patch({"label_status": "verified"}), {"label_status": LabelStatus.VERIFIED})

    def test_bad_patches_are_validation_errors(self):
        for bad in ({}, {"scenes": ["oven"]}, {"category_id": 0}, {"category_id": True}, {"category_id": "12"},
                    {"label_status": None}, {"label_status": "done"}, {"tags": "a,b"}, {"description": 3},
                    {"food_count": 0}, {"water_mass_g": -1}, {"food_state": "boiled"}):
            with self.subTest(bad=bad), self.assertRaises(err.AppError) as ctx:
                normalize_image_patch(bad)
            self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")

    def test_the_label_follows_the_category_unless_it_is_sent(self):
        def label(current, current_cat, new_cat, requested=None):
            return resolve_label_status(
                current_label=current, current_category_id=current_cat, new_category_id=new_cat, requested=requested
            ).value

        self.assertEqual(label("auto", 5, None), "unlabeled")  # category cleared
        self.assertEqual(label("unlabeled", None, 5), "verified")  # a person picked it
        self.assertEqual(label("auto", 5, 6), "verified")  # a person changed it
        self.assertEqual(label("auto", 5, 5), "auto")  # unchanged
        self.assertEqual(label("verified", 5, 6, "auto"), "auto")  # a label sent wins when it fits
        self.assertEqual(label("unlabeled", None, None), "unlabeled")
        for current, current_cat, new_cat, requested in (("auto", 5, 5, "unlabeled"), ("unlabeled", None, None, "auto"),
                                                         ("auto", 5, None, "verified")):
            with self.subTest(requested=requested, new_cat=new_cat), self.assertRaises(err.AppError) as ctx:
                label(current, current_cat, new_cat, requested)
            self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")


class FakeVectors:
    def __init__(self, points=None, *, fail=None):
        self.points = dict(points or {})
        self.fail = fail

    def get_payloads(self, point_ids):
        if self.fail:
            raise self.fail
        return {str(i): dict(self.points[str(i)]) for i in point_ids if str(i) in self.points}


class VectorStatusTests(unittest.TestCase):
    def inspect(self, points, image=None):
        image = image or ManageImagesUseCase(FakeImageStore()).get(IMAGE)
        return ImageVectorStatusUseCase(FakeVectors(points), model=MODEL).inspect(image)

    def test_scenes_compare_as_a_set_and_extra_keys_do_not_count(self):
        expected = {"category_id": 1, "scenes": ["general", "oven"], "food_state": None, "label_status": "auto"}
        self.assertTrue(payload_matches({**expected, "scenes": ["oven", "general"], "legacy": 1}, expected))
        self.assertFalse(payload_matches({**expected, "scenes": ["general"]}, expected))
        self.assertFalse(payload_matches({**expected, "category_id": 2}, expected))
        self.assertFalse(payload_matches({"scenes": ["general", "oven"], "label_status": "auto", "category_id": 1},
                                         {**expected, "food_state": "raw"}))  # a missing key reads as null

    def test_synced_stale_and_missing(self):
        state = self.inspect({IMAGE: SYNCED_PAYLOAD})
        self.assertEqual((state["status"], state["exists"], state["payload_matches"]), ("synced", True, True))
        self.assertEqual((state["collection"], state["embedding_space_id"]), ("c_dino_v2", "dino:v2"))
        # the ledger row of the current model, not of an older one
        self.assertEqual((state["synced_version"], state["payload_version"]), (4, 4))
        self.assertEqual(state["expected_payload"]["scenes"], ["general", "oven"])

        state = self.inspect({IMAGE: {**SYNCED_PAYLOAD, "scenes": ["general"]}})
        self.assertEqual((state["status"], state["payload_matches"]), ("stale", False))
        self.assertEqual(state["payload"]["scenes"], ["general"])

        state = self.inspect({})
        self.assertEqual((state["status"], state["exists"], state["payload"], state["payload_matches"]),
                         ("missing", False, None, None))

    def test_no_ledger_row_for_the_current_model(self):
        image = ManageImagesUseCase(FakeImageStore()).get(IMAGE)
        image["embeddings"] = image["embeddings"][:1]  # only the retired model's row
        state = self.inspect({IMAGE: SYNCED_PAYLOAD}, image)
        self.assertEqual((state["status"], state["synced_version"], state["embedded_at"]), ("synced", None, None))


class FakeImageStore:
    """The catalog store's image methods, answering like the repository."""

    def __init__(self):
        self.rows = {IMAGE: {**ROW, "embeddings": [dict(e) for e in ROW["embeddings"]]}}
        self.queue = {}
        self.calls = []

    def get_image_detail(self, image_id):
        row = self.rows.get(str(image_id))
        return {**row} if row else None

    def patch_image(self, image_id, fields):
        self.calls.append(("patch", str(image_id), fields))
        if str(image_id) not in self.rows:
            raise err.image_not_found(f"image {image_id} does not exist")
        row = self.rows[str(image_id)]
        row.update({k: (v.value if isinstance(v, LabelStatus) else v) for k, v in fields.items()})
        row["payload_version"] += 1
        return {**row}

    def enqueue_image_deletion(self, image_id):
        self.calls.append(("delete", str(image_id)))
        uid = str(image_id)
        if uid in self.rows:
            row = self.rows.pop(uid)
            self.queue[uid] = {"image_id": uid, "original_url": row["original_url"], "thumb_url": row["thumb_url"],
                               "deleted_at": "2026-11-11T09:00:00+00:00"}
        if uid not in self.queue:
            raise err.image_not_found(f"image {uid} does not exist")
        return dict(self.queue[uid])


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeImageStore()
        self.vectors = FakeVectors({IMAGE: SYNCED_PAYLOAD})
        self.sync_uc = Mock()
        self.sync_uc.push_images.return_value = PushResult(pushed={IMAGE: 5})
        self.deletion_worker = Mock()
        self.deletion_worker.clean_up.return_value = True
        self.runtime = ApplicationState(
            config={}, config_info={}, http_client=Mock(), settings=SimpleNamespace(),
            catalog=SimpleNamespace(images_uc=ManageImagesUseCase(self.store)),
            retrieval=SimpleNamespace(
                model=MODEL,
                scene_sync_uc=self.sync_uc,
                vector_status_uc=ImageVectorStatusUseCase(self.vectors, model=MODEL),
                deletion_worker=self.deletion_worker,
            ),
        )
        app = FastAPI()
        register_exception_handlers(app)
        app.add_middleware(TraceIdMiddleware)
        app.include_router(image_routes.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        p = patch.object(image_routes, "get_application_state", return_value=self.runtime)
        p.start()
        self.addCleanup(p.stop)

    def ok(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.json()["error"], code)
        return response.json()

    def retrieval_down(self):
        self.runtime.retrieval, self.runtime.retrieval_error = None, "NOT_READY: embedding model unavailable"

    # ---- GET -----------------------------------------------------------------------

    def test_the_detail_has_every_field_and_the_vector_state(self):
        body = self.ok(self.client.get(f"/api/images/{IMAGE}"))
        image = body["image"]
        self.assertEqual((image["category"], image["category_code"], image["label_status"]), ("牛排", "dish.steak", "auto"))
        self.assertEqual({k: image[k] for k in FOOD}, FOOD)  # food fields whatever the scene
        self.assertEqual(image["heating_group"]["code"], "grill")
        self.assertEqual([e["model_status"] for e in image["embeddings"]], ["retired", "current"])
        self.assertNotIn("trace_id", body)
        vector = body["vector"]
        self.assertEqual((vector["status"], vector["collection"], vector["synced_version"]), ("synced", "c_dino_v2", 4))

    def test_an_unknown_or_malformed_id(self):
        self.error(self.client.get(f"/api/images/{OTHER}"), 404, "IMAGE_NOT_FOUND")
        self.error(self.client.get("/api/images/not-a-uuid"), 422, "VALIDATION_ERROR")

    def test_the_vector_state_is_unavailable_when_it_cannot_be_read(self):
        self.vectors.fail = err.search_qdrant_failed("get_payloads: ConnectionError: 10.0.0.5:6334 refused")
        vector = self.ok(self.client.get(f"/api/images/{IMAGE}"))["vector"]
        self.assertEqual((vector["status"], vector["collection"]), ("unavailable", "c_dino_v2"))
        self.assertEqual(vector["detail"], "vector search failed")  # the public message, no address
        self.vectors.fail = RuntimeError("boom")
        vector = self.ok(self.client.get(f"/api/images/{IMAGE}"))["vector"]
        self.assertEqual((vector["status"], vector["detail"]), ("unavailable", "vector store read failed"))

        self.retrieval_down()
        body = self.ok(self.client.get(f"/api/images/{IMAGE}"))
        self.assertEqual(body["vector"]["status"], "unavailable")
        self.assertIn("embedding model unavailable", body["vector"]["detail"])
        self.assertEqual(body["image"]["id"], IMAGE)  # PostgreSQL still answers

    def test_without_the_catalog_it_is_not_ready(self):
        self.runtime.catalog, self.runtime.catalog_error = None, "postgres down"
        self.error(self.client.get(f"/api/images/{IMAGE}"), 503, "NOT_READY")

    # ---- PATCH ---------------------------------------------------------------------

    def test_a_patch_passes_only_the_fields_sent_and_pushes_the_payload(self):
        body = self.ok(self.client.patch(f"/api/images/{IMAGE}", json={"category_id": None, "tags": [" 去皮 "]}))
        self.assertEqual(self.store.calls[-1], ("patch", IMAGE, {"category_id": None, "tags": ["去皮"]}))
        self.assertEqual((body["vector_sync"], body["sync_required"]), ("pushed", False))
        self.assertEqual((body["image"]["tags"], body["image"]["payload_version"]), (["去皮"], 5))
        self.sync_uc.push_images.assert_called_once_with([IMAGE])

    def test_a_patch_is_saved_whatever_the_push_does(self):
        cases = [
            (PushResult(missing=[IMAGE]), "missing"),
            (PushResult(unconfirmed=[IMAGE]), "failed"),
            (err.index_qdrant_failed("set_payloads: timeout"), "failed"),
            (RuntimeError("boom"), "failed"),
        ]
        for outcome, expected in cases:
            with self.subTest(expected=expected, outcome=outcome):
                if isinstance(outcome, Exception):
                    self.sync_uc.push_images.side_effect = outcome
                else:
                    self.sync_uc.push_images.side_effect = None
                    self.sync_uc.push_images.return_value = outcome
                body = self.ok(self.client.patch(f"/api/images/{IMAGE}", json={"description": "x"}))
                self.assertEqual((body["vector_sync"], body["sync_required"]), (expected, True))
                self.assertNotIn("boom", str(body))
        self.retrieval_down()
        body = self.ok(self.client.patch(f"/api/images/{IMAGE}", json={"description": "y"}))
        self.assertEqual((body["vector_sync"], body["sync_required"], body["image"]["description"]), ("not_ready", True, "y"))

    def test_bad_patches_reach_nothing(self):
        for payload in ({}, {"scenes": ["oven"]}, {"category_id": "12"}, {"category_id": 0}, {"label_status": None},
                        {"label_status": "done"}, {"food_count": True}, {"tags": [1]}):
            with self.subTest(payload=payload):
                self.error(self.client.patch(f"/api/images/{IMAGE}", json=payload), 422, "VALIDATION_ERROR")
        self.error(self.client.patch(f"/api/images/{OTHER}", json={"description": "x"}), 404, "IMAGE_NOT_FOUND")
        self.sync_uc.push_images.assert_not_called()

    # ---- POST vector-sync ----------------------------------------------------------

    def test_a_push_repairs_one_image(self):
        body = self.ok(self.client.post(f"/api/images/{IMAGE}/vector-sync"))
        self.assertEqual(body, {"ok": True, "image_id": IMAGE, "payload_version": 4,
                                "vector_sync": "pushed", "sync_required": False})
        self.error(self.client.post(f"/api/images/{OTHER}/vector-sync"), 404, "IMAGE_NOT_FOUND")
        self.sync_uc.push_images.assert_called_once_with([IMAGE])  # nothing pushed for the unknown id

    # ---- DELETE --------------------------------------------------------------------

    def test_a_delete_cleans_up_at_once_when_it_can(self):
        body = self.ok(self.client.delete(f"/api/images/{IMAGE}"))
        self.assertEqual((body["image_id"], body["cleanup"], body["deleted_at"]), (IMAGE, "done", "2026-11-11T09:00:00+00:00"))
        (queued,), _ = self.deletion_worker.clean_up.call_args
        self.assertEqual((queued["image_id"], queued["original_url"]), (IMAGE, "https://cdn/o.jpg"))
        self.error(self.client.get(f"/api/images/{IMAGE}"), 404, "IMAGE_NOT_FOUND")

        self.deletion_worker.clean_up.return_value = False  # still queued: deleting again is fine
        self.assertEqual(self.ok(self.client.delete(f"/api/images/{IMAGE}"))["cleanup"], "queued")
        self.retrieval_down()
        self.assertEqual(self.ok(self.client.delete(f"/api/images/{IMAGE}"))["cleanup"], "queued")
        self.error(self.client.delete(f"/api/images/{OTHER}"), 404, "IMAGE_NOT_FOUND")


class RouteOrderTests(unittest.TestCase):
    def test_the_download_route_comes_before_the_image_id_route(self):
        gets = [r.path for r in api_router.routes if "GET" in getattr(r, "methods", ())]
        self.assertLess(gets.index("/api/images/download"), gets.index("/api/images/{image_id}"))


class FakeQueueStore:
    def __init__(self, *, stored_again=False):
        self.stored_again = stored_again
        self.completed = []

    def get_by_id(self, image_id):
        return {"id": image_id} if self.stored_again else None

    def complete_deletion(self, image_id):
        self.completed.append(image_id)


class DeletionCleanupTests(unittest.TestCase):
    ROW = {"image_id": IMAGE, "original_url": "https://cdn/o.jpg", "thumb_url": "https://cdn/t.webp"}

    def test_the_point_the_files_then_the_row(self):
        store, vectors, storage = FakeQueueStore(), Mock(), Mock()
        self.assertTrue(DeletionWorker(store, vectors, storage).clean_up(dict(self.ROW)))
        vectors.delete.assert_called_once_with(IMAGE)
        self.assertEqual([c.args[0] for c in storage.delete_by_url_or_key.call_args_list],
                         ["https://cdn/o.jpg", "https://cdn/t.webp"])
        self.assertEqual(store.completed, [IMAGE])

    def test_an_image_stored_again_keeps_its_point_and_files(self):
        store, vectors, storage = FakeQueueStore(stored_again=True), Mock(), Mock()
        self.assertTrue(DeletionWorker(store, vectors, storage).clean_up(dict(self.ROW)))
        vectors.delete.assert_not_called()
        storage.delete_by_url_or_key.assert_not_called()
        self.assertEqual(store.completed, [IMAGE])

    def test_a_failure_leaves_the_row_queued(self):
        store, vectors, storage = FakeQueueStore(), Mock(), Mock()
        storage.delete_by_url_or_key.side_effect = RuntimeError("s3 down")
        self.assertFalse(DeletionWorker(store, vectors, storage).clean_up(dict(self.ROW)))
        self.assertEqual(store.completed, [])


if __name__ == "__main__":
    unittest.main()
