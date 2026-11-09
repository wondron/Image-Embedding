"""GET /api/enums, GET /api/enums/{name}, POST /api/enums/{name}/values."""

import os
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import enums as enums_api
from app.core.config.enum_store import EnumStore, read_catalog, set_enum_store
from app.core.exceptions import register_exception_handlers

SHIPPED = Path(__file__).resolve().parents[1] / "config" / "param" / "enums.yaml"


class EnumEndpointTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "enums.yaml"
        self.path.write_bytes(SHIPPED.read_bytes())
        self.addCleanup(set_enum_store, set_enum_store(EnumStore(self.path)))
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(enums_api.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        body = response.json()
        self.assertEqual((body["ok"], body["error"]), (False, code))
        return body

    def test_list_returns_every_enum_with_labels_and_editability(self):
        response = self.client.get("/api/enums")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertNotIn("file_error", body)
        by_name = {e["name"]: e for e in body["enums"]}
        self.assertEqual(set(by_name), set(read_catalog(SHIPPED).names()))
        food = by_name["food_state"]
        self.assertTrue(food["editable"])
        self.assertEqual(food["values"][0], {"value": "raw", "label": "生", "description": ""})
        self.assertFalse(by_name["label_status"]["editable"])

    def test_get_one_enum(self):
        response = self.client.get("/api/enums/license")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([v["value"] for v in response.json()["enum"]["values"]],
                         ["commercial", "research_only", "unknown"])
        self.assert_error(self.client.get("/api/enums/colour"), 404, "ENUM_NOT_FOUND")

    def test_add_value_is_saved_and_listed(self):
        response = self.client.post(
            "/api/enums/food_state/values", json={"value": " thawed ", "label": "解冻", "description": "化开后"}
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["enum"]["values"][-1],
                         {"value": "thawed", "label": "解冻", "description": "化开后"})
        listed = self.client.get("/api/enums/food_state").json()["enum"]
        self.assertIn("thawed", [v["value"] for v in listed["values"]])
        self.assertIn("thawed", read_catalog(self.path).get("food_state"))

    def test_add_value_errors(self):
        post = self.client.post
        self.assert_error(post("/api/enums/label_status/values", json={"value": "done", "label": "完成"}),
                          403, "ENUM_NOT_EDITABLE")
        self.assert_error(post("/api/enums/food_state/values", json={"value": "raw", "label": "生"}),
                          409, "ENUM_VALUE_EXISTS")
        self.assert_error(post("/api/enums/colour/values", json={"value": "red", "label": "红"}),
                          404, "ENUM_NOT_FOUND")
        body = self.assert_error(post("/api/enums/food_state/values", json={"value": "Thawed", "label": "解冻"}),
                                 422, "VALIDATION_ERROR")
        self.assertEqual(body["errors"][0]["loc"], ["body", "value"])
        self.assert_error(post("/api/enums/food_state/values", json={"value": "thawed", "label": " "}),
                          422, "VALIDATION_ERROR")
        self.assert_error(post("/api/enums/food_state/values", json={"value": "thawed", "lable": "解冻"}),
                          422, "VALIDATION_ERROR")

    def test_a_broken_file_still_lists_the_last_good_version_but_refuses_writes(self):
        self.assertEqual(self.client.get("/api/enums").status_code, 200)
        self.path.write_text("food_state: [", encoding="utf-8")
        st = os.stat(self.path)
        os.utime(self.path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
        with self.assertLogs("config.enums", level="ERROR"):
            body = self.client.get("/api/enums").json()
        self.assertIn("invalid YAML", body["file_error"])
        self.assertIn("food_state", [e["name"] for e in body["enums"]])
        self.assert_error(
            self.client.post("/api/enums/food_state/values", json={"value": "thawed", "label": "解冻"}),
            503, "ENUM_FILE_INVALID",
        )


if __name__ == "__main__":
    unittest.main()
