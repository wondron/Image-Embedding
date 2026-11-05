"""Custom PG types (ltree, …)."""
from __future__ import annotations

from typing import Any

from sqlalchemy.types import UserDefinedType


class Ltree(UserDefinedType):
    """PostgreSQL ltree (categories.path). Bound/returned as text."""

    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "LTREE"

    def bind_processor(self, dialect):
        def process(value):
            if value is None:
                return None
            return str(value)

        return process

    def result_processor(self, dialect, coltype):
        def process(value):
            return value

        return process
