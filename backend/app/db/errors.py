"""PostgreSQL metadata-store failures (contract §4.2), raised by app.db and the PG repositories.

They are RetrievalError, so every `except err.RetrievalError` in the services still
catches them; the class lives in app.core.errors, which keeps the persistence layer
free of app.services imports.
"""

from __future__ import annotations

from app.core.errors import RetrievalError


def index_postgres_failed(detail: str) -> RetrievalError:
    """Write / connection / schema failure (ingest, deletions, scenes, startup check)."""
    return RetrievalError("INDEX_POSTGRES_FAILED", detail, http_status=502)


def search_postgres_failed(detail: str) -> RetrievalError:
    """Metadata lookup for search hits failed."""
    return RetrievalError("SEARCH_POSTGRES_FAILED", detail, http_status=502)


def list_postgres_failed(detail: str) -> RetrievalError:
    """Gallery list query failed."""
    return RetrievalError("LIST_POSTGRES_FAILED", detail, http_status=502)
