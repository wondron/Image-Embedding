"""Retrieval domain errors mapped to contract §4.2 codes.

Metadata-database failures (INDEX_/SEARCH_/LIST_POSTGRES_FAILED) are raised by the
persistence layer and constructed in app.db.errors.
"""

from __future__ import annotations

from app.core.errors import RetrievalError  # also used by callers as err.RetrievalError


# Convenience constructors matching §4.2
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


def search_bad_image(detail: str) -> RetrievalError:
    return RetrievalError("SEARCH_BAD_IMAGE", detail, http_status=422)


def search_unknown_scene(detail: str) -> RetrievalError:
    return RetrievalError("SEARCH_UNKNOWN_SCENE", detail, http_status=422)


def search_bad_score_threshold(detail: str) -> RetrievalError:
    return RetrievalError("SEARCH_BAD_SCORE_THRESHOLD", detail, http_status=422)


def search_bad_embedding(detail: str) -> RetrievalError:
    return RetrievalError("SEARCH_BAD_EMBEDDING", detail, http_status=422)


def search_qdrant_failed(detail: str) -> RetrievalError:
    return RetrievalError("SEARCH_QDRANT_FAILED", detail, http_status=502)


def search_flyb_failed(detail: str) -> RetrievalError:
    return RetrievalError("SEARCH_FLYB_FAILED", detail, http_status=502)


def vector_dim_mismatch(detail: str) -> RetrievalError:
    return RetrievalError("VECTOR_DIM_MISMATCH", detail, http_status=503)



def not_ready(detail: str) -> RetrievalError:
    return RetrievalError("NOT_READY", detail, http_status=503)


def list_unknown_scene(detail: str) -> RetrievalError:
    return RetrievalError("LIST_UNKNOWN_SCENE", detail, http_status=422)


def validation_error(detail: str) -> RetrievalError:
    return RetrievalError("VALIDATION_ERROR", detail, http_status=422)


def internal_error(detail: str) -> RetrievalError:
    return RetrievalError("INTERNAL_ERROR", detail, http_status=500)

