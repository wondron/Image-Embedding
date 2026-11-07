"""app.db: layering, error codes, row conversion and the expected migration (no database needed)."""

import subprocess
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.errors import RetrievalError
from app.db import errors as db_errors
from app.db.common import _row_to_dict
from app.db.session import expected_revision
from app.services.retrieval import errors as err

BACKEND = Path(__file__).resolve().parents[1]


class LayeringTests(unittest.TestCase):
    def test_db_layer_imports_nothing_from_services(self):
        # Fresh interpreter: importing app.db.session first used to loop back through app.services
        probe = (
            "import sys, app.db.session, app.db.errors, app.db.common; "
            "print(sorted(m for m in sys.modules if m.startswith('app.services')))"
        )
        out = subprocess.run(
            [sys.executable, "-c", probe], cwd=BACKEND, capture_output=True, text=True, timeout=120
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "[]")


class ErrorCodeTests(unittest.TestCase):
    def test_postgres_failures_are_retrieval_errors_with_postgres_codes(self):
        cases = {
            db_errors.index_postgres_failed: "INDEX_POSTGRES_FAILED",
            db_errors.search_postgres_failed: "SEARCH_POSTGRES_FAILED",
            db_errors.list_postgres_failed: "LIST_POSTGRES_FAILED",
        }
        for factory, code in cases.items():
            exc = factory("boom")
            # services catch err.RetrievalError (e.g. per-item failures in batch indexing)
            self.assertIsInstance(exc, err.RetrievalError)
            self.assertEqual((exc.code, exc.http_status, exc.detail), (code, 502, "boom"))
        self.assertIs(err.RetrievalError, RetrievalError)


class RowToDictTests(unittest.TestCase):
    def test_converts_by_value_type_not_column_name(self):
        uid = uuid.UUID("12345678-1234-5678-1234-567812345678")
        naive = datetime(2026, 11, 7, 8, 30)
        aware = datetime(2026, 11, 7, 16, 30, tzinfo=timezone(timedelta(hours=8)))
        out = _row_to_dict(
            {
                "id": uid,
                "some_new_ref": uid,
                "created_at": naive,
                "some_new_time": aware,
                "sha256": b"\x01\xff",
                "preprocess": {"mean": [0.5]},
                "width": 640,
                "description": "",
                "category_id": None,
            }
        )
        self.assertEqual(out["id"], str(uid))
        self.assertEqual(out["some_new_ref"], str(uid))
        self.assertEqual(out["created_at"], "2026-11-07T08:30:00+00:00")  # naive is UTC
        self.assertEqual(out["some_new_time"], "2026-11-07T16:30:00+08:00")
        self.assertEqual(out["sha256"], "01ff")
        self.assertEqual(
            (out["preprocess"], out["width"], out["description"], out["category_id"]),
            ({"mean": [0.5]}, 640, "", None),
        )
        self.assertEqual(_row_to_dict(None), {})


class ExpectedRevisionTests(unittest.TestCase):
    def test_is_the_head_of_the_shipped_migrations(self):
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config(str(BACKEND / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND / "alembic"))
        self.assertEqual(expected_revision(), ScriptDirectory.from_config(cfg).get_current_head())


if __name__ == "__main__":
    unittest.main()
