"""config/param/enums.yaml: file rules, checks against the code, reload on change, append."""

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from app.core import enums
from app.core.config import enum_store
from app.core.config.enum_store import (
    EnumFileError,
    EnumStore,
    dump_catalog,
    parse_catalog,
    read_catalog,
)
from app.core.errors import AppError

SHIPPED = Path(__file__).resolve().parents[1] / "config" / "param" / "enums.yaml"


def shipped_doc() -> dict:
    return yaml.safe_load(SHIPPED.read_text(encoding="utf-8"))


def write_and_touch(path: Path, text: str) -> None:
    """Rewrite path and move its mtime forward, so coarse mtime clocks still see a change."""
    path.write_bytes(text.encode("utf-8"))
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))


class ShippedFileTests(unittest.TestCase):
    def test_shipped_file_passes_every_rule(self):
        catalog = read_catalog(SHIPPED)  # also checks system values against app.core.enums
        self.assertEqual(set(catalog.names()), set(enums.SYSTEM_ENUMS) | set(enums.EDITABLE_ENUMS))
        for name in enums.EDITABLE_ENUMS:
            self.assertTrue(catalog.get(name).editable, name)

    def test_dump_round_trips(self):
        catalog = read_catalog(SHIPPED)
        text = dump_catalog(catalog)
        self.assertTrue(text.startswith(enum_store.FILE_HEADER))
        self.assertEqual(parse_catalog(yaml.safe_load(text), "dumped"), catalog)


class FileRuleTests(unittest.TestCase):
    def assert_rejected(self, doc, *fragments):
        with self.assertRaises(EnumFileError) as ctx:
            parse_catalog(doc, "enums.yaml")
        for fragment in fragments:
            self.assertIn(fragment, str(ctx.exception))

    def test_system_enum_values_come_from_the_code(self):
        doc = shipped_doc()
        doc["label_status"]["values"].append({"value": "rejected", "label": "已驳回"})
        self.assert_rejected(doc, "label_status", "app.core.enums.LabelStatus")
        doc = shipped_doc()
        doc["label_status"]["editable"] = True
        self.assert_rejected(doc, "label_status.editable", "must be false")

    def test_labels_and_order_of_a_system_enum_may_change(self):
        doc = shipped_doc()
        doc["label_status"]["label"] = "状态"
        doc["label_status"]["values"].reverse()
        catalog = parse_catalog(doc, "enums.yaml")
        self.assertEqual(catalog.get("label_status").value_list(), ["verified", "auto", "unlabeled"])

    def test_values_the_code_uses_cannot_be_removed(self):
        doc = shipped_doc()
        doc["license"]["values"] = [v for v in doc["license"]["values"] if v["value"] != "unknown"]
        self.assert_rejected(doc, "license.values", "unknown")

    def test_enums_and_keys_must_be_known(self):
        doc = shipped_doc()
        doc["colour"] = {"label": "颜色", "values": [{"value": "red", "label": "红"}]}
        self.assert_rejected(doc, "unknown enum 'colour'")
        doc = shipped_doc()
        del doc["food_state"]
        self.assert_rejected(doc, "missing enums ['food_state']")
        doc = shipped_doc()
        doc["food_state"]["values"][0]["lable"] = "生"
        self.assert_rejected(doc, "food_state.values[0]", "lable")

    def test_value_format(self):
        for bad in ("Thawed", "1st", "half-cooked", "a" * 33, "", False, None):
            with self.subTest(value=bad):
                doc = shipped_doc()
                doc["food_state"]["values"].append({"value": bad, "label": "x"})
                self.assert_rejected(doc, "food_state.values[3].value")

    def test_unquoted_yes_no_in_yaml_is_explained(self):
        text = SHIPPED.read_text(encoding="utf-8").replace("{value: raw,", "{value: no,")
        self.assert_rejected(yaml.safe_load(text), "food_state.values[0].value", "quote yes / no")

    def test_duplicates_blank_labels_and_wrong_types(self):
        doc = shipped_doc()
        doc["food_state"]["values"].append({"value": "raw", "label": "又一个生"})
        self.assert_rejected(doc, "duplicate value 'raw'")
        doc = shipped_doc()
        doc["food_state"]["values"][0]["label"] = "  "
        self.assert_rejected(doc, "food_state.values[0].label: is required")
        doc = shipped_doc()
        doc["food_state"]["editable"] = "yes please"
        self.assert_rejected(doc, "food_state.editable: must be true or false")
        self.assert_rejected([], "must be a mapping")


class EnumStoreTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "enums.yaml"
        self.path.write_bytes(SHIPPED.read_bytes())
        self.store = EnumStore(self.path)

    def assert_app_error(self, code, fn, *args):
        with self.assertRaises(AppError) as ctx:
            fn(*args)
        self.assertEqual(ctx.exception.code, code)
        return ctx.exception

    def test_add_value_writes_the_file_and_is_visible_at_once(self):
        food = self.store.add_value("food_state", "thawed", " 解冻 ", "冷冻后化开")
        self.assertEqual(food.value_list(), ["raw", "cooked", "frozen", "thawed"])
        self.assertIn("thawed", self.store.get("food_state"))
        on_disk = read_catalog(self.path).get("food_state")  # what a fresh process would read
        self.assertEqual((on_disk.values[-1].label, on_disk.values[-1].description), ("解冻", "冷冻后化开"))
        self.assertEqual(self.path.read_text(encoding="utf-8"), dump_catalog(read_catalog(self.path)))
        self.assertEqual(list(self.dir.glob(".*.tmp")), [])

    def test_add_value_rejections_leave_the_file_alone(self):
        before = self.path.read_bytes()
        self.assert_app_error("ENUM_NOT_FOUND", self.store.add_value, "colour", "red", "红")
        error = self.assert_app_error("ENUM_NOT_EDITABLE", self.store.add_value, "label_status", "done", "完成")
        self.assertIn("app.core.enums.LabelStatus", error.detail)
        self.assert_app_error("ENUM_VALUE_EXISTS", self.store.add_value, "food_state", "raw", "生")
        self.assert_app_error("VALIDATION_ERROR", self.store.add_value, "food_state", "Thawed", "解冻")
        self.assert_app_error("VALIDATION_ERROR", self.store.add_value, "food_state", "thawed", "  ")
        self.assertEqual(self.path.read_bytes(), before)

    def test_editable_false_locks_an_editable_enum(self):
        doc = shipped_doc()
        doc["license"]["editable"] = False
        write_and_touch(self.path, yaml.safe_dump(doc, allow_unicode=True, sort_keys=False))
        error = self.assert_app_error("ENUM_NOT_EDITABLE", self.store.add_value, "license", "cc_by", "CC BY")
        self.assertIn("editable: false", error.detail)

    def test_value_count_is_capped(self):
        doc = shipped_doc()
        doc["license"]["values"] += [
            {"value": f"lic_{i}", "label": f"许可 {i}"} for i in range(enums.ENUM_MAX_VALUES - 3)
        ]
        write_and_touch(self.path, yaml.safe_dump(doc, allow_unicode=True, sort_keys=False))
        self.assert_app_error("VALIDATION_ERROR", self.store.add_value, "license", "one_more", "多一个")

    def test_manual_edits_are_picked_up_without_restart(self):
        self.assertNotIn("chilled", self.store.get("food_state"))
        doc = shipped_doc()
        doc["food_state"]["values"].append({"value": "chilled", "label": "冷藏"})
        write_and_touch(self.path, yaml.safe_dump(doc, allow_unicode=True, sort_keys=False))
        self.assertIn("chilled", self.store.get("food_state"))
        self.assertIsNone(self.store.error)

    def test_a_broken_edit_keeps_the_last_good_version_and_blocks_writes(self):
        self.store.catalog()
        with self.assertLogs("config.enums", level="ERROR"):
            write_and_touch(self.path, "food_state: [")
            self.assertIn("raw", self.store.get("food_state"))
        self.assertIn("invalid YAML", self.store.error)
        self.assert_app_error("ENUM_FILE_INVALID", self.store.add_value, "food_state", "thawed", "解冻")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "food_state: [")  # not clobbered
        write_and_touch(self.path, SHIPPED.read_text(encoding="utf-8"))  # fixed again
        self.assertIsNone(self.store.error)

    def test_without_a_good_version_the_store_is_not_ready(self):
        with self.assertLogs("config.enums", level="ERROR"):
            store = EnumStore(self.dir / "missing.yaml")
            error = self.assert_app_error("ENUM_FILE_INVALID", store.catalog)
        self.assertIn("missing.yaml", error.detail)
        self.assert_app_error("ENUM_FILE_INVALID", store.get, "food_state")  # no log spam: same file state

    def test_a_file_that_cannot_be_renamed_onto_is_overwritten_in_place(self):
        # docker bind-mounts of a single file refuse os.replace with EBUSY
        with patch.object(enum_store.os, "replace", side_effect=OSError(16, "Device or resource busy")):
            with self.assertLogs("config.enums", level="WARNING"):
                self.store.add_value("license", "cc_by", "CC BY")
        self.assertIn("cc_by", read_catalog(self.path).get("license"))
        self.assertEqual(list(self.dir.glob(".*.tmp")), [])

    def test_write_failure_is_reported_and_keeps_the_snapshot(self):
        with patch.object(enum_store, "_write_bytes", side_effect=PermissionError(13, "Permission denied")):
            with self.assertLogs("config.enums", level="WARNING"):
                self.assert_app_error("ENUM_FILE_WRITE_FAILED", self.store.add_value, "license", "cc_by", "CC BY")
        self.assertNotIn("cc_by", self.store.get("license"))

    def test_concurrent_adds_in_one_process_all_land(self):
        values = [f"state_{i}" for i in range(12)]
        errors = []

        def add(value):
            try:
                self.store.add_value("food_state", value, value)
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=add, args=(v,)) for v in values]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(set(read_catalog(self.path).get("food_state").value_list()) - {"raw", "cooked", "frozen"},
                         set(values))


if __name__ == "__main__":
    unittest.main()
