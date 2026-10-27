"""Retrieval domain errors mapped to contract §4.2 codes."""

from __future__ import annotations


class RetrievalError(Exception):
    """Business/infra error with HTTP status + wire error code."""

    def __init__(self, code: str, detail: str, *, http_status: int = 500) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.http_status = http_status


# Convenience constructors matching §4.2
def index_empty_request(detail: str = "no file and no path") -> RetrievalError:
    return RetrievalError("INDEX_EMPTY_REQUEST", detail, http_status=400)


def index_bad_image(detail: str) -> RetrievalError:
    return RetrievalError("INDEX_BAD_IMAGE", detail, http_status=422)


def index_bad_embedding(detail: str) -> RetrievalError:
    return RetrievalError("INDEX_BAD_EMBEDDING", detail, http_status=422)


def index_duplicate_path(detail: str) -> RetrievalError:
    return RetrievalError("INDEX_DUPLICATE_PATH", detail, http_status=409)


def index_qdrant_failed(detail: str) -> RetrievalError:
    return RetrievalError("INDEX_QDRANT_FAILED", detail, http_status=502)


def index_mysql_failed(detail: str) -> RetrievalError:
    return RetrievalError("INDEX_MYSQL_FAILED", detail, http_status=502)


def search_bad_image(detail: str) -> RetrievalError:
    return RetrievalError("SEARCH_BAD_IMAGE", detail, http_status=422)


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


def not_ready(detail: str) -> RetrievalError:
    return RetrievalError("NOT_READY", detail, http_status=503)


def validation_error(detail: str) -> RetrievalError:
    return RetrievalError("VALIDATION_ERROR", detail, http_status=422)


def internal_error(detail: str) -> RetrievalError:
    return RetrievalError("INTERNAL_ERROR", detail, http_status=500)
