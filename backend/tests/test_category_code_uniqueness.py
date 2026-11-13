"""Case-insensitive category-code checks through the repository, without PostgreSQL."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy.exc import IntegrityError

from app.core.errors import AppError
from app.repositories import categories
from tests.test_category_root_limit import Connection, Result, Store


class CodeConnection(Connection):
    def __init__(self, codes, **kwargs):
        super().__init__(**kwargs)
        self.codes = codes
        self.code_checks = []

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        if sql.startswith("SELECT 1 FROM categories WHERE lower(code)"):
            self.queries.append(sql)
            self.code_checks.append(dict(params))
            matched = any(
                code.lower() == params["code"].lower() and cid != params.get("exclude_id")
                for cid, code in self.codes.items()
            )
            return Result((1,) if matched else None)
        return super().execute(statement, params)


class CategoryCodeUniquenessTests(unittest.TestCase):
    def setUp(self):
        self.read = patch.object(categories, "_read_node", side_effect=lambda conn, cid: {"id": cid})
        self.read.start()
        self.addCleanup(self.read.stop)

    def assert_code_conflict(self, operation, *args, **kwargs):
        with self.assertRaises(AppError) as caught:
            operation(*args, **kwargs)
        self.assertEqual(caught.exception.code, "CATEGORY_CODE_EXISTS")

    def test_create_rejects_another_spelling_before_any_write(self):
        conn = CodeConnection({12: "ingredient.HongSu"})
        self.assert_code_conflict(
            Store(conn).create_category,
            {"code": "Ingredient.hONGsU", "parent_id": 1,
             "names": [{"lang": "zh", "kind": "primary", "name": "红薯"}]},
        )
        self.assertEqual(conn.code_checks, [{"code": "Ingredient.hONGsU"}])
        self.assertEqual(conn.writes, [])
        self.assertEqual(conn.events[-1], "rollback")

    def test_update_allows_case_only_change_of_its_own_code(self):
        current = {"code": "ingredient.HongSu", "parent_id": 1, "path": "1.99", "cuisines": []}
        conn = CodeConnection({99: current["code"]}, current=current)
        Store(conn).update_category(99, {"code": "Ingredient.hONGsU"})
        self.assertEqual(conn.code_checks, [{"code": "Ingredient.hONGsU", "exclude_id": 99}])
        self.assertEqual(conn.writes[0][1]["code"], "Ingredient.hONGsU")
        self.assertEqual(conn.events[-1], "commit")

    def test_update_excludes_itself_but_still_rejects_another_node(self):
        current = {"code": "ingredient.ZiShuu", "parent_id": 1, "path": "1.99", "cuisines": []}
        conn = CodeConnection({99: current["code"], 12: "ingredient.HongSu"}, current=current)
        self.assert_code_conflict(Store(conn).update_category, 99, {"code": "Ingredient.hONGsU"})
        self.assertEqual(conn.writes, [])
        self.assertEqual(conn.code_checks[0]["exclude_id"], 99)

    def test_unique_constraint_and_expression_index_have_the_same_conflict_error(self):
        for constraint in ("uq_categories_code", "uq_categories_code_lower"):
            with self.subTest(constraint=constraint):
                original = Exception("duplicate")
                original.diag = SimpleNamespace(constraint_name=constraint)
                exc = IntegrityError("INSERT INTO categories ...", {}, original)
                self.assertEqual(categories._constraint_error("create_category", exc).code, "CATEGORY_CODE_EXISTS")


if __name__ == "__main__":
    unittest.main()
