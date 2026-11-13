"""Root-cap checks through repository entry points, without a real database.

The fake SQL connection records lock/check/write ordering. It lets these tests
cover legacy/inactive roots even though new roots must use six unique codes.
"""

from __future__ import annotations

from contextlib import contextmanager
import unittest
from unittest.mock import patch

from app.core.errors import AppError
from app.repositories.categories import PgCategoriesMixin


ZH = {"lang": "zh", "name": "其他", "kind": "primary"}
ROOT_COUNT_SQL = "SELECT count(*) FROM categories WHERE parent_id IS NULL"


class Result:
    def __init__(self, value=None):
        self.value = value

    def scalar_one(self):
        return self.value

    def fetchone(self):
        return self.value

    def mappings(self):
        return self

    def fetchall(self):
        return self.value


class Connection:
    def __init__(self, root_count=6, *, current=None, duplicate=False):
        self.root_count = root_count
        self.current = current
        self.duplicate = duplicate
        self.events = []
        self.queries = []
        self.writes = []
        self.locked = False

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.queries.append(sql)
        if sql.startswith("SELECT pg_advisory_xact_lock("):
            self.locked = True
            self.events.append("lock")
            return Result()
        if sql == ROOT_COUNT_SQL:
            if not self.locked:
                raise AssertionError("root count must be checked under the catalog lock")
            self.events.append("count")
            return Result(self.root_count)
        if sql.startswith("SELECT 1 FROM categories WHERE lower(code)"):
            return Result((1,) if self.duplicate else None)
        if sql.startswith("SELECT 1 FROM categories WHERE id"):
            return Result((1,))
        if sql.startswith("SELECT code, parent_id, path::text AS path, cuisines"):
            return Result(self.current)
        if sql.startswith(("SELECT path <@", "SELECT code, path <@")):
            return Result({"inside": False, "code": "ingredient"})
        if sql.startswith("SELECT id, parent_id, code FROM categories WHERE path <@"):
            return Result([{"id": 99, "parent_id": self.current["parent_id"], "code": self.current["code"]}])
        if sql.startswith("SELECT id, code FROM categories WHERE lower(code) = ANY"):
            return Result(None)
        if sql.startswith(("INSERT ", "UPDATE ", "DELETE ")):
            self.events.append("write")
            self.writes.append((sql, params))
            return Result(99 if sql.startswith("INSERT INTO categories ") else None)
        raise AssertionError(f"Unexpected SQL: {sql}")


class Store(PgCategoriesMixin):
    def __init__(self, connection):
        self.connection = connection

    def ensure_schema(self):
        pass

    def _get_engine(self):
        return self

    @contextmanager
    def begin(self):
        self.connection.events.append("begin")
        try:
            yield self.connection
        except Exception:
            self.connection.events.append("rollback")
            raise
        else:
            self.connection.events.append("commit")


class CategoryRootLimitTests(unittest.TestCase):
    def setUp(self):
        self.read = self.enter_patch("app.repositories.categories._read_node", return_value={"id": 99})
        self.recompute = self.enter_patch("app.repositories.categories._recompute_subtree", return_value=(0, []))

    def enter_patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def assert_error(self, expected_code, operation, *args):
        with self.assertRaises(AppError) as caught:
            operation(*args)
        self.assertEqual(caught.exception.code, expected_code)
        return caught.exception.detail

    @staticmethod
    def current(*, code="other", parent_id=1):
        return {"code": code, "parent_id": parent_id, "path": "1.99", "cuisines": []}

    def test_sixth_root_is_allowed_and_lock_precedes_count_and_insert(self):
        conn = Connection(root_count=5)
        result = Store(conn).create_category({"code": "other", "names": [ZH]})
        self.assertEqual(result, {"id": 99})
        self.assertEqual(conn.events, ["begin", "lock", "count", "write", "write", "commit"])
        self.assertIsNone(conn.writes[0][1]["parent_id"])

    def test_six_or_more_roots_blocks_new_root_before_any_write(self):
        # SQL deliberately has no active/domain-code filter: all existing roots count.
        for count in (6, 7):
            with self.subTest(root_count=count):
                conn = Connection(root_count=count)
                detail = self.assert_error(
                    "VALIDATION_ERROR", Store(conn).create_category, {"code": "other", "names": [ZH]}
                )
                self.assertIn("at most 6", detail)
                self.assertEqual(conn.events, ["begin", "lock", "count", "rollback"])
                self.assertEqual(conn.writes, [])
                self.assertIn(ROOT_COUNT_SQL, conn.queries)

    def test_duplicate_root_keeps_existing_conflict_error_at_capacity(self):
        conn = Connection(root_count=6, duplicate=True)
        self.assert_error("CATEGORY_CODE_EXISTS", Store(conn).create_category, {"code": "other", "names": [ZH]})
        self.assertNotIn("count", conn.events)
        self.assertEqual(conn.writes, [])

    def test_seventh_domain_is_rejected_even_if_there_is_capacity(self):
        conn = Connection(root_count=0)
        detail = self.assert_error(
            "VALIDATION_ERROR", Store(conn).create_category, {"code": "seventh", "names": [ZH]}
        )
        self.assertIn("needs a parent", detail)
        self.assertEqual(conn.writes, [])

    def test_new_child_is_allowed_at_capacity_without_counting_roots(self):
        conn = Connection(root_count=6)
        Store(conn).create_category({"code": "other.child", "parent_id": 1, "names": [ZH]})
        self.assertNotIn("count", conn.events)
        self.assertEqual(conn.writes[0][1]["parent_id"], 1)
        self.assertEqual(conn.events[-1], "commit")

    def test_child_promotion_at_capacity_rolls_back_before_write_or_scene_recompute(self):
        conn = Connection(root_count=6, current=self.current())
        detail = self.assert_error("VALIDATION_ERROR", Store(conn).update_category, 99, {"parent_id": None})
        self.assertIn("at most 6", detail)
        self.assertEqual(conn.events, ["begin", "lock", "count", "rollback"])
        self.assertEqual(conn.writes, [])
        self.recompute.assert_not_called()

    def test_child_promotion_into_sixth_slot_checks_capacity_before_update(self):
        conn = Connection(root_count=5, current=self.current())
        result = Store(conn).update_category(99, {"parent_id": None})
        self.assertEqual(conn.events, ["begin", "lock", "count", "write", "commit"])
        self.assertIsNone(conn.writes[0][1]["parent_id"])
        self.assertEqual(result["scene_membership"], {"images": 0, "changed_ids": []})
        self.recompute.assert_called_once_with(conn, 99)

    def test_root_edits_renames_and_legacy_metadata_do_not_consume_another_slot(self):
        cases = (
            ("other", {"sort_order": 2}),
            ("other", {"parent_id": None}),
            ("legacy", {"names": [ZH]}),
            ("ingredient", {"code": "other"}),
        )
        for code, changes in cases:
            with self.subTest(code=code, changes=changes):
                conn = Connection(root_count=6, current=self.current(code=code, parent_id=None))
                Store(conn).update_category(99, changes)
                self.assertNotIn("count", conn.events)
                self.assertTrue(conn.writes)
                self.assertEqual(conn.events[-1], "commit")
        self.recompute.assert_not_called()

    def test_moving_root_beneath_parent_can_free_a_slot_at_capacity(self):
        conn = Connection(root_count=6, current=self.current(parent_id=None))
        Store(conn).update_category(99, {"parent_id": 1})
        self.assertNotIn("count", conn.events)
        self.assertEqual(conn.writes[-1][1]["parent_id"], 1)
        self.assertEqual(conn.writes[-1][1]["code"], "ingredient.other")
        self.recompute.assert_called_once_with(conn, 99)


if __name__ == "__main__":
    unittest.main()
