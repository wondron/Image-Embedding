"""Retrieval domain errors mapped to contract 搂4.2 codes."""

from __future__ import annotations


class RetrievalError(Exception):
    """Business/infra error with HTTP status + wire error code."""

    def __init__(self, code: str, detail: str, *, http_status: int = 500) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.http_status = http_status


# Convenience constructors matching 搂4.2
def index_empty_request(detail: str = "no file and no path") -> RetrievalError:
    return RetrievalError("INDEX_EMPTY_REQUEST", detail, http_status=400)


def index_bad_image(detail: str) -> RetrievalError:
    return RetrievalError("INDEX_BAD_IMAGE", detail, http_status=422)


def index_bad_embedding(detail: str) -> RetrievalError:
    return RetrievalError("INDEX_BAD_EMBEDDING", detail, http_status=422)


def index_duplicate_path(detail: str) -> RetrievalError:
    return RetrievalError("INDEX_DUPLICATE_PATH", detail, http_status=409)


def index_path_too_long(detail: str) -> RetrievalError:
    return RetrievalError("INDEX_PATH_TOO_LONG", detail, http_status=422)


def index_unknown_scene(detail: str) -> RetrievalError:
    return RetrievalError("INDEX_UNKNOWN_SCENE", detail, http_status=422)


def index_qdrant_failed(detail: str) -> RetrievalError:
    return RetrievalError("INDEX_QDRANT_FAILED", detail, http_status=502)


def index_mysql_failed(detail: str) -> RetrievalError:
    return RetrievalError("INDEX_MYSQL_FAILED", detail, http_status=502)


def search_bad_image(detail: str) -> RetrievalError:
    return RetrievalError("SEARCH_BAD_IMAGE", detail, http_status=422)


def search_unknown_scene(detail: str) -> RetrievalError:
    return RetrievalError("SEARCH_UNKNOWN_SCENE", detail, http_status=422)


def search_bad_embedding(detail: str) -> RetrievalError:
    return RetrievalError("SEARCH_BAD_EMBEDDING", detail, http_status=422)


def search_qdrant_failed(detail: str) -> RetrievalError:
    return RetrievalError("SEARCH_QDRANT_FAILED", detail, http_status=502)


def search_mysql_failed(detail: str) -> RetrievalError:
    return RetrievalError("SEARCH_MYSQL_FAILED", detail, http_status=502)


def search_flyb_failed(detail: str) -> RetrievalError:
    return RetrievalError("SEARCH_FLYB_FAILED", detail, http_status=502)


def vector_dim_mismatch(detail: str) -> RetrievalError:
    return RetrievalError("VECTOR_DIM_MISMATCH", detail, http_status=503)


def vector_space_mismatch(
    detail: str,
    *,
    configured: str | None = None,
    stored: str | None = None,
) -> RetrievalError:
    """Configured embedding_space_id != collection meta (SSOT §1.5)."""
    exc = RetrievalError("VECTOR_SPACE_MISMATCH", detail, http_status=503)
    exc.configured = configured  # type: ignore[attr-defined]
    exc.stored = stored  # type: ignore[attr-defined]
    return exc


def not_ready(detail: str) -> RetrievalError:
    return RetrievalError("NOT_READY", detail, http_status=503)


def list_unknown_scene(detail: str) -> RetrievalError:
    return RetrievalError("LIST_UNKNOWN_SCENE", detail, http_status=422)


def list_mysql_failed(detail: str) -> RetrievalError:
    return RetrievalError("LIST_MYSQL_FAILED", detail, http_status=502)


def validation_error(detail: str) -> RetrievalError:
    return RetrievalError("VALIDATION_ERROR", detail, http_status=422)


def internal_error(detail: str) -> RetrievalError:
    return RetrievalError("INTERNAL_ERROR", detail, http_status=500)

