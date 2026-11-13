"""Repository subtree updates with a transactional, unique-code-enforcing fake.

The fake rejects intermediate duplicate codes like PostgreSQL's non-deferrable
index, and restores its rows on rollback. No server or database is contacted.
"""

from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy.exc import IntegrityError

from app.core.errors import AppError
from tests.test_category_root_limit import Connection, Result, Store


class TreeConnection(Connection):
    def __init__(self, *, fail_final_id=None):
        super().__init__()
        self.rows = {
            1: {"id": 1, "parent_id": None, "code": "ingredient", "path": "1", "cuisines": []},
            20: {"id": 20, "parent_id": None, "code": "beverage", "path": "20", "cuisines": []},
            99: {"id": 99, "parent_id": 1, "code": "ingredient.YeTiLe", "path": "1.99", "cuisines": []},
            100: {"id": 100, "parent_id": 99, "code": "ingredient.BaiSYe", "path": "1.99.100", "cuisines": []},
            101: {"id": 101, "parent_id": 100, "code": "ingredient.DJiang", "path": "1.99.100.101", "cuisines": []},
        }
        self.fail_final_id = fail_final_id

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        if sql.startswith("SELECT code, parent_id, path::text AS path, cuisines"):
            self.queries.append(sql)
            return Result(dict(self.rows[params["id"]]))
        if sql.startswith("SELECT code, path <@"):
            self.queries.append(sql)
            parent = self.rows.get(params["id"])
            if parent is None:
                return Result(None)
            inside = parent["path"] == params["own"] or parent["path"].startswith(params["own"] + ".")
            return Result({"code": parent["code"], "inside": inside})
        if sql.startswith("SELECT id, parent_id, code FROM categories WHERE path <@"):
            self.queries.append(sql)
            self.events.append("plan_read")
            nodes = [dict(row) for row in self.rows.values()
                     if row["path"] == params["path"] or row["path"].startswith(params["path"] + ".")]
            return Result(sorted(nodes, key=lambda row: (row["path"].count("."), row["id"])))
        if sql.startswith("SELECT id, code FROM categories WHERE lower(code) = ANY"):
            self.queries.append(sql)
            self.events.append("plan_checked")
            return Result(next((dict(row) for row in self.rows.values()
                                if row["id"] not in params["ids"] and row["code"].lower() in params["codes"]), None))
        if sql.startswith("UPDATE categories SET"):
            self.queries.append(sql)
            self.writes.append((sql, deepcopy(params)))
            self.events.append("write")
            batches = params if isinstance(params, list) else [params]
            for item in batches:
                node_id = item.get("node_id", item.get("id"))
                code = item.get("code")
                if code is not None:
                    if (node_id == self.fail_final_id and isinstance(params, list)
                            and not code.startswith("__category_recode_")):
                        raise RuntimeError("simulated later code write failure")
                    if any(row["id"] != node_id and row["code"].lower() == code.lower() for row in self.rows.values()):
                        original = Exception("duplicate code")
                        original.diag = SimpleNamespace(constraint_name="uq_categories_code_lower")
                        raise IntegrityError(sql, item, original)
                self.rows[node_id].update({key: value for key, value in item.items() if key not in {"id", "node_id"}})
            return Result()
        return super().execute(statement, params)


class TransactionalStore(Store):
    @contextmanager
    def begin(self):
        snapshot = deepcopy(self.connection.rows)
        self.connection.events.append("begin")
        try:
            yield self.connection
        except Exception:
            self.connection.rows = snapshot
            self.connection.events.append("rollback")
            raise
        else:
            self.connection.events.append("commit")


class CategorySubtreeUpdateTests(unittest.TestCase):
    def setUp(self):
        read = patch("app.repositories.categories._read_node", side_effect=lambda conn, cid: dict(conn.rows[cid]))
        read.start()
        self.addCleanup(read.stop)
        recompute = patch("app.repositories.categories._recompute_subtree", return_value=(0, []))
        self.recompute = recompute.start()
        self.addCleanup(recompute.stop)

    def assert_error(self, expected, operation, *args):
        with self.assertRaises(AppError) as caught:
            operation(*args)
        self.assertEqual(caught.exception.code, expected, caught.exception.detail)

    def test_move_cascades_to_grandchildren_before_parent_update(self):
        conn = TreeConnection()
        response = TransactionalStore(conn).update_category(99, {"parent_id": 20})
        self.assertEqual(response["category"]["code"], "beverage.YeTiLe")
        self.assertEqual(conn.rows[99]["parent_id"], 20)
        self.assertEqual(conn.rows[100]["code"], "beverage.YeTiLe.BaiSYe")
        self.assertEqual(conn.rows[101]["code"], "beverage.YeTiLe.BaiSYe.DJiang")
        self.assertEqual(conn.rows[100]["parent_id"], 99)
        self.assertEqual(conn.rows[101]["parent_id"], 100)
        self.assertLess(conn.events.index("lock"), conn.events.index("plan_read"))
        self.assertLess(conn.events.index("plan_checked"), conn.events.index("write"))
        self.assertEqual(conn.writes[-1][1]["parent_id"], 20)
        self.recompute.assert_called_once_with(conn, 99)

    def test_external_grandchild_case_conflict_aborts_before_any_write(self):
        conn = TreeConnection()
        conn.rows[200] = {"id": 200, "parent_id": 20, "code": "BEVERAGE.YETILE.BAISYE.DJIANG", "path": "20.200", "cuisines": []}
        before = deepcopy(conn.rows)
        self.assert_error("CATEGORY_CODE_EXISTS", TransactionalStore(conn).update_category, 99, {"parent_id": 20})
        self.assertEqual(conn.rows, before)
        self.assertEqual(conn.writes, [])
        self.assertEqual(conn.events[-1], "rollback")
        self.recompute.assert_not_called()

    def test_internal_case_conflict_and_descendant_length_fail_before_writes(self):
        for failure in ("duplicate", "length"):
            with self.subTest(failure=failure):
                conn = TreeConnection()
                if failure == "duplicate":
                    conn.rows[101].update(parent_id=99, code="other.baisye", path="1.99.101")
                    error = "CATEGORY_CODE_EXISTS"
                else:
                    conn.rows[20]["code"] = "a" * 116  # new root fits; its descendants exceed 128
                    error = "VALIDATION_ERROR"
                before = deepcopy(conn.rows)
                self.assert_error(error, TransactionalStore(conn).update_category, 99, {"parent_id": 20})
                self.assertEqual(conn.rows, before)
                self.assertEqual(conn.writes, [])

    def test_later_write_failure_rolls_back_all_temporary_and_final_codes(self):
        conn = TreeConnection(fail_final_id=101)
        before = deepcopy(conn.rows)
        self.assert_error("CATEGORY_POSTGRES_FAILED", TransactionalStore(conn).update_category, 99, {"parent_id": 20})
        self.assertEqual(conn.rows, before)
        self.assertEqual(conn.events[-1], "rollback")
        self.assertTrue(conn.writes)
        self.assertTrue(any(isinstance(params, list) for _, params in conn.writes))
        self.recompute.assert_not_called()

    def test_staging_allows_a_valid_code_temporarily_owned_by_own_child(self):
        conn = TreeConnection()
        del conn.rows[101]
        conn.rows[99]["code"] = "ingredient.A"
        conn.rows[100]["code"] = "ingredient.A.B"
        TransactionalStore(conn).update_category(99, {"code": "ingredient.A.B"})
        self.assertEqual(conn.rows[99]["code"], "ingredient.A.B")
        self.assertEqual(conn.rows[100]["code"], "ingredient.A.B.B")
        self.assertEqual(conn.events[-1], "commit")
        self.assertFalse(any(row["code"].startswith("__category_recode_") for row in conn.rows.values()))
        self.recompute.assert_not_called()

    def test_code_only_rename_cascades_prefix_case_without_changing_parent(self):
        conn = TreeConnection()
        TransactionalStore(conn).update_category(99, {"code": "Ingredient.yETilE"})
        self.assertEqual(conn.rows[99]["parent_id"], 1)
        self.assertEqual(conn.rows[101]["code"], "Ingredient.yETilE.BaiSYe.DJiang")
        self.recompute.assert_not_called()

    def test_metadata_only_patch_does_not_recode_descendants(self):
        conn = TreeConnection()
        before_codes = {cid: row["code"] for cid, row in conn.rows.items()}
        TransactionalStore(conn).update_category(99, {"sort_order": 7})
        self.assertEqual({cid: row["code"] for cid, row in conn.rows.items()}, before_codes)
        self.assertNotIn("plan_read", conn.events)
        self.assertEqual(len(conn.writes), 1)
        self.recompute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
