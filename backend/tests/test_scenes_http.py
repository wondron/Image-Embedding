"""Scene configuration and manual membership: the real routes, adapters, use cases and field rules
over in-memory stores (what needs SQL or Qdrant is in test_pg_live).

- which writes say sync_required (scene_fields.scene_flags_need_sync / rule_write_needs_sync);
- a rule patch may replace its target (scene_fields.normalize_rule_fields);
- a manual membership write pushes the image's payload when retrieval is up (vector_sync);
- the payload push records only current versions and pushes superseded ones again.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import scene_memberships as membership_routes
from app.api.v1 import scenes as scene_routes
from app.bootstrap.state import ApplicationState
from app.core import errors as err
from app.core.exceptions import register_exception_handlers
from app.core.middleware import TraceIdMiddleware
from app.services.retrieval.active_model import ActiveModel
from app.services.retrieval.manage_scenes import ManageScenesUseCase
from app.services.retrieval.scene_fields import (
    check_rule_label,
    normalize_rule_fields,
    rule_write_needs_sync,
    scene_flags_need_sync,
)
from app.services.retrieval.sync_scenes import PUSH_ROUNDS, PushResult, SyncSceneMembershipsUseCase

IMAGE = "0cf457e2-4a47-9f02-fd4d-34540389f720"
SCENE = {
    "id": 7, "code": "steam_oven", "name": "蒸烤箱", "description": None, "include_all": False,
    "outputs_heating": False, "is_active": True, "created_at": None, "member_count": 0, "labels": [], "rules": [],
}
SNAPSHOT = {
    "image_id": IMAGE, "payload_version": 2, "scenes": ["general", "steam_oven"],
    "memberships": [
        {"scene_id": 1, "scene_code": "general", "is_active": True, "include_all": True, "is_member": True,
         "origin": "implicit", "manual_override": None},
        {"scene_id": 7, "scene_code": "steam_oven", "is_active": True, "include_all": False, "is_member": True,
         "origin": "manual", "manual_override": True},
    ],
}


def flags(active, include_all):
    return {"is_active": active, "include_all": include_all}


class SyncRuleTests(unittest.TestCase):
    def test_scene_flags(self):
        self.assertTrue(scene_flags_need_sync(None, flags(True, True)))  # every image at once
        self.assertFalse(scene_flags_need_sync(None, flags(True, False)))  # no rules yet
        self.assertFalse(scene_flags_need_sync(None, flags(False, True)))
        self.assertFalse(scene_flags_need_sync(flags(True, False), flags(True, False)))
        self.assertTrue(scene_flags_need_sync(flags(True, False), flags(False, False)))
        self.assertTrue(scene_flags_need_sync(flags(False, False), flags(True, False)))
        self.assertTrue(scene_flags_need_sync(flags(True, False), flags(True, True)))
        self.assertFalse(scene_flags_need_sync(flags(False, False), flags(False, True)))  # still off

    def test_rule_writes(self):
        self.assertTrue(rule_write_needs_sync(flags(True, False)))
        self.assertFalse(rule_write_needs_sync(flags(True, False), target_changed=False))  # a label only
        self.assertFalse(rule_write_needs_sync(flags(False, False)))  # no members while off
        self.assertFalse(rule_write_needs_sync(flags(True, True)))  # rules ignored

    def test_a_rule_patch_may_replace_the_target(self):
        self.assertEqual(normalize_rule_fields({"label_id": None}, partial=True), {"label_id": None})
        self.assertEqual(
            normalize_rule_fields({"category_id": 5}, partial=True), {"category_id": 5, "cuisine": None}
        )
        self.assertEqual(
            normalize_rule_fields({"cuisine": "chinese", "label_id": None}, partial=True),
            {"category_id": None, "cuisine": "chinese", "label_id": None},
        )
        self.assertEqual(
            normalize_rule_fields({"category_id": 5, "cuisine": None, "label_id": 3}, partial=True),
            {"category_id": 5, "cuisine": None, "label_id": 3},
        )
        for bad in ({}, {"category_id": None}, {"category_id": 5, "cuisine": "x"}, {"cuisine": "Chinese"},
                    {"category_id": True}, {"scene_id": 1}):
            with self.subTest(bad=bad), self.assertRaises(err.AppError) as ctx:
                normalize_rule_fields(bad, partial=True)
            self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")
        # a create still checks the label against its own target
        with self.assertRaises(err.AppError):
            normalize_rule_fields({"cuisine": "chinese", "label_id": 3}, partial=False)

    def test_a_label_needs_a_category_rule(self):
        check_rule_label({"category_id": 5, "label_id": 3})
        check_rule_label({"cuisine": "x", "category_id": None, "label_id": None})
        with self.assertRaises(err.AppError) as ctx:
            check_rule_label({"category_id": None, "cuisine": "x", "label_id": 3}, label_sent=False)
        self.assertIn("label_id: null", ctx.exception.detail)


class FakeMetadata:
    """payload rows from a dict; the ledger takes a version only while it is the current one."""

    def __init__(self, versions):
        self.versions = dict(versions)
        self.ledger = {}
        self.on_read = None  # called after each read (to change an image in between)

    def get_payload_rows(self, image_ids):
        rows = [
            {"id": i, "payload_version": self.versions[i], "category_id": None, "label_status": "unlabeled",
             "food_state": None, "scenes": ["general"] + (["oven"] if self.versions[i] > 1 else [])}
            for i in sorted(image_ids) if i in self.versions
        ]
        if self.on_read:
            self.on_read()
        return rows

    def mark_payloads_pushed(self, *, model_id, versions):
        recorded = sorted(i for i, v in versions.items() if self.versions.get(i) == v)
        self.ledger.update({i: versions[i] for i in recorded})
        return recorded


class FakeVectors:
    def __init__(self, points):
        self.points = dict.fromkeys(points)
        self.writes = []

    def set_payloads(self, payloads):
        self.writes.append({i: p["scenes"] for i, p in payloads.items()})
        for i, payload in payloads.items():
            if i in self.points:
                self.points[i] = payload
        return sorted(i for i in payloads if i not in self.points)


MODEL = ActiveModel(id=1, name="m", version="1", collection="c", dim=4, distance="cosine")


class PayloadPushTests(unittest.TestCase):
    def test_pushes_and_reports_missing_points(self):
        metadata, vectors = FakeMetadata({"a": 1, "b": 1}), FakeVectors(["a"])
        result = SyncSceneMembershipsUseCase(metadata, vectors, model=MODEL).push_images(["a", "b", "gone"])
        self.assertEqual((result.pushed, result.missing, result.unconfirmed), ({"a": 1}, ["b"], []))
        self.assertEqual(metadata.ledger, {"a": 1})

    def test_a_superseded_payload_is_read_and_pushed_again(self):
        metadata, vectors = FakeMetadata({"a": 1}), FakeVectors(["a"])

        def bump_once():
            metadata.on_read = None
            metadata.versions["a"] = 2  # another writer, after our read

        metadata.on_read = bump_once
        result = SyncSceneMembershipsUseCase(metadata, vectors, model=MODEL).push_images(["a"])
        self.assertEqual(vectors.writes, [{"a": ["general"]}, {"a": ["general", "oven"]}])
        self.assertEqual(vectors.points["a"]["scenes"], ["general", "oven"])
        self.assertEqual((result.pushed, metadata.ledger), ({"a": 2}, {"a": 2}))

    def test_gives_up_after_the_rounds_without_claiming_anything(self):
        metadata, vectors = FakeMetadata({"a": 1}), FakeVectors(["a"])
        metadata.on_read = lambda: metadata.versions.__setitem__("a", metadata.versions["a"] + 1)
        result = SyncSceneMembershipsUseCase(metadata, vectors, model=MODEL).push_images(["a"])
        self.assertEqual((result.pushed, result.unconfirmed), ({}, ["a"]))
        self.assertEqual(len(vectors.writes), PUSH_ROUNDS)
        self.assertEqual(metadata.ledger, {})


class FakeCatalog:
    """Scene writes answer like the real store: the scene plus its sync_required."""

    def __init__(self):
        self.calls = []

    def _write(self, op, *args, sync_required=True):
        self.calls.append((op, *args))
        return {**SCENE, "sync_required": sync_required}

    def update_scene(self, scene_id, fields):
        return self._write("update_scene", scene_id, fields, sync_required="is_active" in fields)

    def update_scene_label(self, scene_id, label_id, fields):
        return self._write("update_scene_label", scene_id, label_id, fields, sync_required=False)

    def update_scene_rule(self, scene_id, rule_id, fields):
        return self._write("update_scene_rule", scene_id, rule_id, fields, sync_required="cuisine" in fields)

    def delete_scene(self, scene_id):
        self.calls.append(("delete_scene", scene_id))
        return {"id": scene_id, "code": "steam_oven", "sync_required": False}

    def put_manual_scene_membership(self, image_id, scene_id, *, is_member):
        self.calls.append(("put", str(image_id), scene_id, is_member))
        return dict(SNAPSHOT)

    def clear_manual_scene_membership(self, image_id, scene_id):
        self.calls.append(("clear", str(image_id), scene_id))
        return dict(SNAPSHOT)


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.catalog = FakeCatalog()
        self.sync_uc = Mock()
        self.sync_uc.push_images.return_value = PushResult(pushed={IMAGE: 2})
        self.runtime = ApplicationState(
            config={}, config_info={}, http_client=Mock(), settings=SimpleNamespace(),
            catalog=SimpleNamespace(scenes_uc=ManageScenesUseCase(self.catalog)),
            retrieval=SimpleNamespace(scene_sync_uc=self.sync_uc),
        )
        app = FastAPI()
        register_exception_handlers(app)
        app.add_middleware(TraceIdMiddleware)
        app.include_router(scene_routes.router)
        app.include_router(membership_routes.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        for routes in (scene_routes, membership_routes):
            p = patch.object(routes, "get_application_state", return_value=self.runtime)
            p.start()
            self.addCleanup(p.stop)

    def ok(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    # ---- configuration writes --------------------------------------------------------

    def test_configuration_writes_say_whether_a_sync_is_needed(self):
        body = self.ok(self.client.patch("/api/scenes/7", json={"is_active": False}))
        self.assertEqual((body["sync_required"], body["scene"]["code"]), (True, "steam_oven"))
        self.assertNotIn("sync_required", body["scene"])  # top level only
        body = self.ok(self.client.patch("/api/scenes/7/labels/3", json={"name": "牛排"}))
        self.assertFalse(body["sync_required"])
        body = self.ok(self.client.delete("/api/scenes/7"))
        self.assertEqual(body, {"ok": True, "deleted": {"id": 7, "code": "steam_oven"}, "sync_required": False})

    def test_a_rule_patch_passes_only_the_fields_sent(self):
        self.ok(self.client.patch("/api/scenes/7/rules/4", json={"label_id": None}))
        body = self.ok(self.client.patch("/api/scenes/7/rules/4", json={"cuisine": " chinese ", "label_id": None}))
        self.assertTrue(body["sync_required"])
        self.assertEqual(
            [call[-1] for call in self.catalog.calls if call[0] == "update_scene_rule"],
            [{"label_id": None}, {"cuisine": "chinese", "label_id": None}],
        )
        response = self.client.patch("/api/scenes/7/rules/4", json={"category_id": "5"})
        self.assertEqual(response.status_code, 422)  # strict int, as for a create

    # ---- manual membership -----------------------------------------------------------

    def test_a_manual_write_is_pushed_when_retrieval_is_up(self):
        body = self.ok(self.client.put(f"/api/images/{IMAGE}/scene-memberships/7", json={"is_member": True}))
        self.assertEqual((body["vector_sync"], body["sync_required"], body["scenes"]), ("pushed", False, SNAPSHOT["scenes"]))
        self.sync_uc.push_images.assert_called_once_with([IMAGE])
        body = self.ok(self.client.delete(f"/api/images/{IMAGE}/scene-memberships/7"))
        self.assertEqual(body["vector_sync"], "pushed")
        self.assertEqual([c[0] for c in self.catalog.calls], ["put", "clear"])

    def test_a_manual_write_is_saved_whatever_the_push_does(self):
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
                body = self.ok(self.client.put(f"/api/images/{IMAGE}/scene-memberships/7", json={"is_member": False}))
                self.assertEqual((body["vector_sync"], body["sync_required"]), (expected, True))
                self.assertNotIn("boom", str(body))

        self.runtime.retrieval, self.runtime.retrieval_error = None, "embedding model unavailable"
        body = self.ok(self.client.put(f"/api/images/{IMAGE}/scene-memberships/7", json={"is_member": True}))
        self.assertEqual((body["vector_sync"], body["sync_required"]), ("not_ready", True))

    def test_a_failed_save_pushes_nothing(self):
        self.catalog.put_manual_scene_membership = Mock(side_effect=err.scene_not_found("scene 9 does not exist"))
        response = self.client.put(f"/api/images/{IMAGE}/scene-memberships/9", json={"is_member": True})
        self.assertEqual(response.status_code, 404, response.text)
        self.sync_uc.push_images.assert_not_called()


if __name__ == "__main__":
    unittest.main()
