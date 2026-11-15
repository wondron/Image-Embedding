"""Configuration-import routes and planners: dry runs, portable references and partial writes."""

import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import categories as category_routes, scenes as scene_routes
from app.bootstrap.state import ApplicationState
from app.core import errors as err
from app.core.exceptions import register_exception_handlers
from app.core.middleware import TraceIdMiddleware
from app.services.retrieval.import_categories import ImportCategoriesUseCase
from app.services.retrieval.import_report import ImportReport
from app.services.retrieval.import_scenes import ImportScenesUseCase


def category(category_id, code, parent_id=None, **fields):
    return {
        "id": category_id, "code": code, "parent_id": parent_id, "name": code,
        "names": [{"lang": "zh", "name": code, "kind": "primary"}], "cuisines": [],
        "heating_group": None, "sort_order": 0, "is_active": True, **fields,
    }


def scene(scene_id=7, code="steam_oven", **fields):
    return {
        "id": scene_id, "code": code, "name": "蒸烤箱", "description": None,
        "include_all": False, "outputs_heating": False, "is_active": True, "labels": [], "rules": [], **fields,
    }


class ConfigImportTests(unittest.TestCase):
    def setUp(self):
        self.nodes = [category(20, "dish")]
        self.categories = Mock()
        self.categories.tree.side_effect = lambda: {"categories": copy.deepcopy(self.nodes), "heating_groups": []}

        def create(fields):
            node = category(len(self.nodes) + 20, fields["code"], **{
                key: value for key, value in fields.items() if key not in ("code", "heating_group_id")
            })
            self.nodes.append(node)
            return node

        self.categories.create.side_effect = create
        self.scenes = Mock()
        self.scenes.list.return_value = {"scenes": [], "default_scene": "general"}
        runtime = ApplicationState(
            config={}, config_info={}, http_client=Mock(), settings=SimpleNamespace(),
            catalog=SimpleNamespace(categories_uc=self.categories, scenes_uc=self.scenes),
        )
        app = FastAPI()
        register_exception_handlers(app)
        app.add_middleware(TraceIdMiddleware)
        app.include_router(category_routes.router)
        app.include_router(scene_routes.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        for routes in (category_routes, scene_routes):
            state = patch.object(routes, "get_application_state", return_value=runtime)
            state.start()
            self.addCleanup(state.stop)

    def test_category_import_defaults_to_preview_and_round_trips_get_shape(self):
        document = {"ok": True, "categories": [self.nodes[0], {
            "id": 1, "parent_id": 20, "code": "dish.pizza", "name": "披萨",
        }]}
        response = self.client.post("/api/categories/import", json=document)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["dry_run"])
        self.assertEqual((body["summary"]["total"], body["summary"]["create"], body["summary"]["unchanged"]), (2, 1, 1))
        self.assertEqual(body["rows"][0]["key"], "dish.pizza")
        self.assertIsNone(body["scene_sync"])
        self.categories.create.assert_not_called()
        self.categories.update.assert_not_called()

    def test_category_commit_resolves_file_parent_ids_and_repeat_is_unchanged(self):
        self.nodes.clear()
        document = {"categories": [
            {"id": 2, "parent_id": 1, "code": "dish.pizza", "name": "披萨"},
            {"id": 1, "code": "dish", "name": "菜品"},
        ]}
        response = self.client.post("/api/categories/import?dry_run=false", json=document)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["summary"]["create"], 2)
        calls = [call.args[0] for call in self.categories.create.call_args_list]
        self.assertEqual([entry["code"] for entry in calls], ["dish", "dish.pizza"])
        self.assertEqual(calls[1]["parent_id"], self.nodes[0]["id"])
        response = self.client.post("/api/categories/import?dry_run=false", json=document)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["summary"]["unchanged"], 2)
        self.assertEqual(response.json()["rows"], [])

    def test_category_business_error_does_not_prevent_valid_siblings_and_no_deletions(self):
        self.nodes.append(category(21, "dish.existing", 20))
        document = {"categories": [{"code": "dish", "children": [
            {"code": "dish.bad", "names": []}, {"code": "dish.new", "name": "新菜"},
        ]}]}
        response = self.client.post("/api/categories/import?dry_run=false", json=document)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual((body["summary"]["create"], body["summary"]["error"], body["summary"]["not_in_file"]), (1, 1, 1))
        self.assertEqual(body["not_in_file"][0]["code"], "dish.existing")
        self.categories.delete.assert_not_called()

    def test_category_move_is_reported_and_does_not_update_the_node(self):
        self.nodes.extend([category(21, "dish.pizza", 20), category(22, "dessert")])
        result = ImportCategoriesUseCase(self.categories).execute({"categories": [
            {"code": "dish.pizza", "parent_code": "dessert", "sort_order": 9},
        ]}, dry_run=False)
        self.assertEqual(result["summary"]["error"], 1)
        self.assertIn("does not move", result["rows"][0]["detail"])
        self.categories.update.assert_not_called()

    def test_optional_envelope_metadata_is_strict_when_present_for_both_routes(self):
        for endpoint, key, kind in (("categories", "categories", "category_tree"), ("scenes", "scenes", "scene_config")):
            for invalid in (None, True, 1.0, "1", 2):
                with self.subTest(endpoint=endpoint, version=invalid):
                    response = self.client.post(f"/api/{endpoint}/import", json={key: [], "schema_version": invalid})
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual(response.json()["error"], "VALIDATION_ERROR")
            for invalid in (None, True, "other"):
                with self.subTest(endpoint=endpoint, kind=invalid):
                    response = self.client.post(f"/api/{endpoint}/import", json={key: [], "kind": invalid})
                    self.assertEqual(response.status_code, 422, response.text)
            response = self.client.post(f"/api/{endpoint}/import", json={key: [], "schema_version": 1, "kind": kind})
            self.assertEqual(response.status_code, 200, response.text)

    def test_scene_preview_builds_labels_before_rules_without_writing(self):
        document = {"scenes": [{"code": "steam_oven", "name": "蒸烤箱", "labels": [
            {"code": "pizza", "name": "披萨"},
        ], "rules": [{"category": {"code": "dish"}, "label": {"code": "pizza"}}]}]}
        original = copy.deepcopy(document)
        response = self.client.post("/api/scenes/import", json=document)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["summary"]["create"], 3)
        self.assertTrue(body["sync_required"])
        self.assertFalse(body["prune"])
        self.assertEqual(document, original)
        self.scenes.create.assert_not_called()
        self.scenes.create_label.assert_not_called()
        self.scenes.create_rule.assert_not_called()

    def test_scene_commit_maps_category_and_label_codes_to_current_database_ids(self):
        base = scene()
        labelled = scene(labels=[{"id": 80, "code": "pizza", "name": "披萨", "sort_order": 0, "is_active": True}])
        ruled = scene(labels=labelled["labels"], rules=[{
            "id": 90, "category": {"id": 20, "code": "dish"}, "cuisine": None,
            "label": {"id": 80, "code": "pizza"},
        }])
        self.scenes.create.return_value = {"scene": base, "sync_required": False}
        self.scenes.create_label.return_value = {"scene": labelled, "sync_required": False}
        self.scenes.create_rule.return_value = {"scene": ruled, "sync_required": True}
        response = self.client.post("/api/scenes/import?dry_run=false", json={"scenes": [{
            "id": 999, "code": "steam_oven", "name": "蒸烤箱",
            "labels": [{"id": 998, "code": "pizza", "name": "披萨"}],
            "rules": [{"id": 997, "category": {"id": 996, "code": "DISH"}, "label": {"id": 995, "code": "pizza"}}],
        }]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["summary"]["create"], 3)
        self.assertTrue(response.json()["sync_required"])
        self.scenes.create_rule.assert_called_once_with(7, {"category_id": 20, "label_id": 80})

    def test_prune_requires_explicit_lists_and_never_mutates_preview_snapshot(self):
        stored = scene(labels=[{"id": 80, "code": "pizza", "name": "披萨", "sort_order": 0, "is_active": True}], rules=[{
            "id": 90, "category": {"id": 20, "code": "dish"}, "cuisine": None, "label": {"code": "pizza"},
        }])
        original = copy.deepcopy(stored)
        self.scenes.list.return_value = {"scenes": [stored]}
        use_case = ImportScenesUseCase(self.scenes, self.categories)
        result = use_case.execute({"scenes": [{"code": "steam_oven"}]}, prune=True)
        self.assertEqual(result["summary"]["delete"], 0)
        result = use_case.execute({"scenes": [{"code": "steam_oven", "labels": []}]}, prune=True)
        self.assertEqual(result["summary"]["error"], 1)
        self.assertEqual(result["rows"][0]["error"], "SCENE_IN_USE")
        result = use_case.execute({"scenes": [{"code": "steam_oven", "labels": [], "rules": []}]}, prune=True)
        self.assertEqual(result["summary"]["delete"], 2)
        self.assertEqual([row["kind"] for row in result["rows"]], ["rule", "label"])
        self.assertTrue(result["sync_required"])
        self.assertEqual(stored, original)
        self.scenes.delete_rule.assert_not_called()
        self.scenes.delete_label.assert_not_called()

    def test_import_database_failure_stops_after_preceding_commits(self):
        self.categories.create.side_effect = [category(30, "dish.first", 20), err.category_postgres_failed("unavailable")]
        with self.assertRaises(err.AppError) as failure:
            ImportCategoriesUseCase(self.categories).execute({"categories": [{"code": "dish", "children": [
                {"code": "dish.first", "name": "先写入"}, {"code": "dish.second", "name": "失败"},
                {"code": "dish.third", "name": "未执行"},
            ]}]}, dry_run=False)
        self.assertEqual(failure.exception.code, "CATEGORY_POSTGRES_FAILED")
        self.assertEqual(self.categories.create.call_count, 2)

    def test_default_scene_protection_is_reported_but_existing_scene_labels_are_processed(self):
        self.scenes.list.return_value = {"scenes": [scene(scene_id=1, code="general", include_all=True)]}
        response = self.client.post("/api/scenes/import", json={"scenes": [{
            "code": "general", "include_all": False, "labels": [{"code": "pizza", "name": "披萨"}],
        }]})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual((body["summary"]["error"], body["summary"]["create"]), (1, 1))
        self.assertEqual(body["rows"][0]["error"], "SCENE_PROTECTED")
        self.assertEqual(body["rows"][1]["kind"], "label")
        self.scenes.update.assert_not_called()

    def test_report_truncation_keeps_errors_and_full_counts(self):
        report = ImportReport(dry_run=True)
        for index in range(1005):
            report.add("category", str(index), "create")
        report.fail("category", "bad", "invalid item")
        body = report.body(total=1006, not_in_file=[])
        self.assertTrue(body["rows_truncated"])
        self.assertEqual(len(body["rows"]), 1000)
        self.assertEqual(body["rows"][-1]["key"], "bad")
        self.assertEqual((body["summary"]["create"], body["summary"]["error"]), (1005, 1))
