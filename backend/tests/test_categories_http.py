"""GET / POST /api/categories, PATCH / DELETE /api/categories/{id}: the real routes, adapter,
use case and field rules over an in-memory store (the tree rules that need SQL are in test_pg_live)."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import categories as routes
from app.bootstrap.state import ApplicationState
from app.core import errors as err
from app.core.exceptions import register_exception_handlers
from app.core.middleware import TraceIdMiddleware
from app.services.retrieval.category_fields import display_name, normalize_category_fields
from app.services.retrieval.manage_categories import ManageCategoriesUseCase

ZH = {"lang": "zh", "name": "披萨", "kind": "primary"}
GRILL = {"id": 2, "code": "grill", "name": "烤"}


class FakeStore:
    """Keeps nodes in a dict. Like the real store it normalizes what it is given; unlike it,
    it knows nothing about parents, roots or uses beyond what a test sets up."""

    def __init__(self):
        self.nodes = {}
        self.calls = []
        self.fail = None  # an AppError the next write raises

    def _node(self, category_id, fields, previous=None):
        node = dict(previous or {
            "id": category_id, "path": str(category_id), "heating_group": None,
            "image_count": 0, "scene_rule_count": 0,
            "created_at": "2026-11-10T08:00:00+00:00", "updated_at": "2026-11-10T08:00:00+00:00",
        })
        fields = dict(fields)
        if "heating_group_id" in fields:
            group_id = fields.pop("heating_group_id")
            node["heating_group"] = dict(GRILL, id=group_id) if group_id is not None else None
        node.update(fields)
        node["name"] = display_name(node["names"])
        return node

    def _write(self, op, *args):
        self.calls.append((op, *args))
        if self.fail is not None:
            raise self.fail

    def list_categories(self):
        return list(self.nodes.values())

    def list_heating_groups(self):
        return [GRILL]

    def create_category(self, fields):
        self._write("create", fields)
        category_id = len(self.nodes) + 1
        self.nodes[category_id] = self._node(category_id, normalize_category_fields(fields, partial=False))
        return self.nodes[category_id]

    def update_category(self, category_id, fields):
        self._write("update", category_id, fields)
        if category_id not in self.nodes:
            raise err.category_not_found(f"category {category_id} does not exist")
        changes = normalize_category_fields(fields, partial=True)
        self.nodes[category_id] = self._node(category_id, changes, self.nodes[category_id])
        return self.nodes[category_id]

    def delete_category(self, category_id):
        self._write("delete", category_id)
        if category_id not in self.nodes:
            raise err.category_not_found(f"category {category_id} does not exist")
        return {"id": category_id, "code": self.nodes.pop(category_id)["code"]}


class CategoryEndpointTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        # The catalog only: categories need PostgreSQL, not the retrieval stack
        self.runtime = ApplicationState(
            config={}, config_info={}, http_client=Mock(), settings=SimpleNamespace(),
            catalog=SimpleNamespace(categories_uc=ManageCategoriesUseCase(self.store)),
            retrieval_error="embedding model unavailable",
        )
        app = FastAPI()
        register_exception_handlers(app)
        app.add_middleware(TraceIdMiddleware)
        app.include_router(routes.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        p = patch.object(routes, "get_application_state", return_value=self.runtime)
        p.start()
        self.addCleanup(p.stop)

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        body = response.json()
        self.assertEqual((body["ok"], body["error"]), (False, code))
        self.assertTrue(body["trace_id"])
        return body

    def create(self, **fields):
        body = {"code": "dish", "names": [ZH], **fields}
        response = self.client.post("/api/categories", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["category"]

    # ---- GET ---------------------------------------------------------------

    def test_list_returns_the_nodes_the_heating_groups_and_the_root_codes(self):
        self.create()
        response = self.client.get("/api/categories")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(set(body), {"ok", "categories", "heating_groups", "root_codes"})  # trace id: header only
        self.assertTrue(response.headers["X-Trace-Id"])
        self.assertEqual(body["heating_groups"], [GRILL])
        self.assertEqual(body["root_codes"], ["ingredient", "dish", "dessert", "beverage"])
        self.assertEqual(body["categories"], [{
            "id": 1, "code": "dish", "parent_id": None, "path": "1", "name": "披萨", "names": [ZH],
            "cuisines": [], "heating_group": None, "sort_order": 0, "is_active": True,
            "image_count": 0, "scene_rule_count": 0,
            "created_at": "2026-11-10T08:00:00+00:00", "updated_at": "2026-11-10T08:00:00+00:00",
        }])

    # ---- POST --------------------------------------------------------------

    def test_create_forwards_the_body_with_defaults_and_answers_201(self):
        with self.assertLogs("svc.categories", level="INFO") as logs:
            response = self.client.post(
                "/api/categories",
                json={"code": " dish ", "names": [dict(ZH, name=" 披萨 ")], "cuisines": ["italian"], "heating_group_id": 2},
                headers={"X-Trace-Id": "trace-create"},
            )
        self.assertEqual(response.status_code, 201, response.text)
        category = response.json()["category"]
        self.assertEqual((category["id"], category["code"], category["name"]), (1, "dish", "披萨"))
        self.assertEqual((category["cuisines"], category["heating_group"]), (["italian"], GRILL))
        op, fields = self.store.calls[-1]
        self.assertEqual(op, "create")
        self.assertEqual(
            {key: fields[key] for key in ("code", "parent_id", "cuisines", "heating_group_id", "sort_order", "is_active")},
            {"code": "dish", "parent_id": None, "cuisines": ["italian"], "heating_group_id": 2, "sort_order": 0, "is_active": True},
        )
        # every write is logged with the request's trace id
        self.assertIn("category created id=1 code=dish parent_id=None | trace_id=trace-create", logs.output[0])

    def test_create_rejects_a_bad_body_before_the_store_is_called(self):
        post = lambda body: self.client.post("/api/categories", json=body)  # noqa: E731
        good = {"code": "dish", "names": [ZH]}
        cases = {
            "code": dict(good, code="Dish"),
            "names": dict(good, names=[]),
            "parent_id": dict(good, parent_id=True),  # strict: true is not 1
            "sort_order": dict(good, sort_order="3"),
            "is_active": dict(good, is_active="yes"),
            "cuisines": dict(good, cuisines=["Italian"]),
            "heating_group_id": dict(good, heating_group_id=0),
            "path": dict(good, path="1.2"),  # not writable
        }
        for field, body in cases.items():
            with self.subTest(field=field):
                error = self.assert_error(post(body), 422, "VALIDATION_ERROR")
                self.assertEqual(error["errors"][0]["loc"][:2], ["body", field])
        error = self.assert_error(post(dict(good, names=[dict(ZH, lang="ZH")])), 422, "VALIDATION_ERROR")
        self.assertEqual(error["errors"][0]["loc"], ["body", "names", 0, "lang"])
        self.assert_error(post({"names": [ZH]}), 422, "VALIDATION_ERROR")
        self.assertEqual(self.store.calls, [])

    def test_rules_a_schema_cannot_say_are_validation_errors_too(self):
        body = {"code": "dish", "names": [{"lang": "en", "name": "Pizza", "kind": "primary"}]}
        error = self.assert_error(self.client.post("/api/categories", json=body), 422, "VALIDATION_ERROR")
        self.assertIn("zh primary name", error["detail"])

    # ---- PATCH -------------------------------------------------------------

    def test_patch_changes_only_the_fields_sent(self):
        self.create(cuisines=["italian"], heating_group_id=2)
        response = self.client.patch("/api/categories/1", json={"sort_order": 5, "is_active": False})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.store.calls[-1], ("update", 1, {"sort_order": 5, "is_active": False}))
        category = response.json()["category"]
        self.assertEqual((category["sort_order"], category["is_active"]), (5, False))
        self.assertEqual((category["cuisines"], category["heating_group"]), (["italian"], GRILL))  # untouched

    def test_patch_null_moves_to_the_root_or_clears_the_heating_group(self):
        self.create(heating_group_id=2)
        response = self.client.patch("/api/categories/1", json={"parent_id": None, "heating_group_id": None})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.store.calls[-1], ("update", 1, {"parent_id": None, "heating_group_id": None}))
        self.assertIsNone(response.json()["category"]["heating_group"])

    def test_patch_rejects_an_empty_body_null_for_a_required_field_and_bad_ids(self):
        self.create()
        patch_ = self.client.patch
        self.assertIn("no category fields", self.assert_error(patch_("/api/categories/1", json={}), 422, "VALIDATION_ERROR")["detail"])
        self.assert_error(patch_("/api/categories/1", json={"code": None}), 422, "VALIDATION_ERROR")
        self.assert_error(patch_("/api/categories/1", json={"names": None}), 422, "VALIDATION_ERROR")
        self.assert_error(patch_("/api/categories/1", json={"id": 2}), 422, "VALIDATION_ERROR")
        self.assert_error(patch_("/api/categories/0", json={"sort_order": 1}), 422, "VALIDATION_ERROR")
        self.assert_error(patch_("/api/categories/abc", json={"sort_order": 1}), 422, "VALIDATION_ERROR")
        self.assert_error(patch_("/api/categories/9", json={"sort_order": 1}), 404, "CATEGORY_NOT_FOUND")

    # ---- DELETE ------------------------------------------------------------

    def test_delete_answers_with_the_deleted_node(self):
        self.create()
        with self.assertLogs("svc.categories", level="INFO") as logs:
            response = self.client.delete("/api/categories/1")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"ok": True, "deleted": {"id": 1, "code": "dish"}})
        self.assertIn("category deleted id=1 code=dish", logs.output[0])
        self.assert_error(self.client.delete("/api/categories/1"), 404, "CATEGORY_NOT_FOUND")

    # ---- failures ----------------------------------------------------------

    def test_store_errors_keep_their_code_and_status(self):
        self.create()
        self.store.fail = err.category_code_exists("another category already uses the code 'dish'")
        error = self.assert_error(self.client.post("/api/categories", json={"code": "dish", "names": [ZH]}), 409, "CATEGORY_CODE_EXISTS")
        self.assertIn("'dish'", error["detail"])
        self.store.fail = err.category_in_use("category 'dish' is still in use (images: 3)")
        error = self.assert_error(self.client.delete("/api/categories/1"), 409, "CATEGORY_IN_USE")
        self.assertIn("images: 3", error["detail"])

    def test_database_failures_do_not_leak_their_detail(self):
        self.store.fail = err.category_postgres_failed("create_category: OperationalError: password=hunter2")
        with self.assertLogs("api.errors", level="ERROR") as logs:
            error = self.assert_error(self.client.post("/api/categories", json={"code": "dish", "names": [ZH]}), 502, "CATEGORY_POSTGRES_FAILED")
        self.assertEqual(error["detail"], "category database error")
        self.assertIn("hunter2", logs.output[0])  # the log keeps the cause

    def test_the_routes_work_without_the_retrieval_stack(self):
        self.assertIsNone(self.runtime.retrieval)  # setUp has no encoder / Qdrant at all
        self.assertEqual(self.client.get("/api/categories").status_code, 200)

    def test_without_the_catalog_every_route_is_503(self):
        self.runtime.catalog = None
        self.runtime.catalog_error = "postgres unreachable"
        for response in (
            self.client.get("/api/categories"),
            self.client.post("/api/categories", json={"code": "dish", "names": [ZH]}),
            self.client.patch("/api/categories/1", json={"sort_order": 1}),
            self.client.delete("/api/categories/1"),
        ):
            self.assertIn("postgres unreachable", self.assert_error(response, 503, "NOT_READY")["detail"])


if __name__ == "__main__":
    unittest.main()
