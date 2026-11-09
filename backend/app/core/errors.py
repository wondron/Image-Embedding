"""Every error code of the service, and the exception that carries one.

ERROR_CODES is the one registry: code -> HTTP status, and whether its detail may reach
the client. Raise AppError through the constructors below (or AppError(code, detail) for a
registered code); app.core.exceptions renders every failure as one envelope:

    {"ok": false, "error": "<CODE>", "detail": "<message>", "trace_id": "<id>"}

Detail policy: codes registered with a public message wrap internal failures (database,
vector store, models) and their detail may carry exception text, SQL or paths. Clients
get the public message; the log keeps the detail and the cause, with the trace id. Every
other code writes its detail for the client. Framework-free: any layer may raise these.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    http_status: int
    # Set for codes whose detail is internal: clients get this text instead of the detail.
    public_detail: str | None = None


ERROR_CODES: dict[str, ErrorSpec] = {
    # -- generic; the first six are also the codes of the framework's own HTTP errors
    "BAD_REQUEST": ErrorSpec(400),
    "UNAUTHORIZED": ErrorSpec(401),
    "FORBIDDEN": ErrorSpec(403),
    "NOT_FOUND": ErrorSpec(404),
    "METHOD_NOT_ALLOWED": ErrorSpec(405),
    "TOO_MANY_REQUESTS": ErrorSpec(429),
    "VALIDATION_ERROR": ErrorSpec(422),
    "NOT_READY": ErrorSpec(503),
    "INTERNAL_ERROR": ErrorSpec(500, "internal server error"),
    # -- uploads and image URLs
    "EMPTY_FILE": ErrorSpec(400),
    "FILE_TOO_LARGE": ErrorSpec(413),
    "UNSUPPORTED_MEDIA_TYPE": ErrorSpec(415),
    "INVALID_URL": ErrorSpec(422),
    "FORBIDDEN_HOST": ErrorSpec(403),
    "DOWNLOAD_FAILED": ErrorSpec(502),
    "DOWNLOAD_TIMEOUT": ErrorSpec(504),
    # -- upstream services (global search)
    "UPSTREAM_ERROR": ErrorSpec(502),
    "UPSTREAM_TIMEOUT": ErrorSpec(504),
    # -- predict and diagnostic tools (raised inline in app.api.services.predict / tools)
    "PREDICT_FAILED": ErrorSpec(500, "image search failed"),
    "INVALID_IMAGE": ErrorSpec(422),
    "TOOL_FAILED": ErrorSpec(500, "tool failed"),
    # -- retrieval: index (contract §4.2)
    "INDEX_EMPTY_REQUEST": ErrorSpec(400),
    "INDEX_BAD_IMAGE": ErrorSpec(422),
    "INDEX_BAD_EMBEDDING": ErrorSpec(422),
    "INDEX_UNKNOWN_SCENE": ErrorSpec(422),
    "INDEX_QDRANT_FAILED": ErrorSpec(502, "vector store write failed"),
    "INDEX_POSTGRES_FAILED": ErrorSpec(502, "metadata database error"),
    # -- retrieval: search
    "SEARCH_BAD_IMAGE": ErrorSpec(422),
    "SEARCH_UNKNOWN_SCENE": ErrorSpec(422),
    "SEARCH_BAD_SCORE_THRESHOLD": ErrorSpec(422),
    "SEARCH_BAD_EMBEDDING": ErrorSpec(422),
    "SEARCH_QDRANT_FAILED": ErrorSpec(502, "vector search failed"),
    "SEARCH_POSTGRES_FAILED": ErrorSpec(502, "metadata lookup failed"),
    "VECTOR_DIM_MISMATCH": ErrorSpec(503),
    # -- retrieval: gallery list
    "LIST_UNKNOWN_SCENE": ErrorSpec(422),
    "LIST_POSTGRES_FAILED": ErrorSpec(502, "metadata list query failed"),
    # -- enums (config/param/enums.yaml, app.core.config.enum_store)
    "ENUM_NOT_FOUND": ErrorSpec(404),
    "ENUM_NOT_EDITABLE": ErrorSpec(403),
    "ENUM_VALUE_EXISTS": ErrorSpec(409),
    "ENUM_FILE_INVALID": ErrorSpec(503),
    "ENUM_FILE_WRITE_FAILED": ErrorSpec(500, "enum file write failed"),
}

_UNREGISTERED_5XX = ERROR_CODES["INTERNAL_ERROR"].public_detail or "internal server error"


class AppError(Exception):
    """A failure with a stable code; http_status defaults to the code's registered status."""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        http_status: int | None = None,
        show_text: str | None = None,
    ) -> None:
        super().__init__(detail)
        spec = ERROR_CODES.get(code)
        self.code = code
        self.detail = detail
        self.http_status = int(http_status if http_status is not None else (spec.http_status if spec else 500))
        # log-only extra context; never sent to the client envelope
        self.show_text = show_text or ""

    @property
    def status_code(self) -> int:
        """Same attribute name as Starlette's HTTPException."""
        return self.http_status

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.code!r}, {self.detail!r}, http_status={self.http_status})"


def is_internal(exc: AppError) -> bool:
    """True when the detail must stay in the log (registered as internal, or an unregistered 5xx)."""
    spec = ERROR_CODES.get(exc.code)
    if spec is None:
        return exc.http_status >= 500
    return spec.public_detail is not None


def client_detail(exc: AppError) -> str:
    """The detail a client may see: an internal code's public message, otherwise the detail."""
    if not is_internal(exc):
        return exc.detail
    spec = ERROR_CODES.get(exc.code)
    return spec.public_detail if spec is not None and spec.public_detail else _UNREGISTERED_5XX


# -- generic ---------------------------------------------------------------------

def validation_error(detail: str) -> AppError:
    return AppError("VALIDATION_ERROR", detail)


def not_ready(detail: str) -> AppError:
    return AppError("NOT_READY", detail)


def internal_error(detail: str) -> AppError:
    return AppError("INTERNAL_ERROR", detail)


# -- uploads and image URLs ------------------------------------------------------

def empty_file(detail: str) -> AppError:
    return AppError("EMPTY_FILE", detail)


def file_too_large(detail: str) -> AppError:
    return AppError("FILE_TOO_LARGE", detail)


def unsupported_media_type(detail: str) -> AppError:
    return AppError("UNSUPPORTED_MEDIA_TYPE", detail)


def invalid_url(detail: str) -> AppError:
    return AppError("INVALID_URL", detail)


def forbidden_host(detail: str) -> AppError:
    return AppError("FORBIDDEN_HOST", detail)


def download_failed(detail: str) -> AppError:
    """The remote image server failed; always 502, never the remote status."""
    return AppError("DOWNLOAD_FAILED", detail)


def download_timeout(detail: str) -> AppError:
    return AppError("DOWNLOAD_TIMEOUT", detail)


# -- upstream services -----------------------------------------------------------

def upstream_error(detail: str, *, http_status: int = 502) -> AppError:
    """http_status may be a gateway status (502 / 503 / 504) the upstream itself returned."""
    return AppError("UPSTREAM_ERROR", detail, http_status=http_status)


def upstream_timeout(detail: str) -> AppError:
    return AppError("UPSTREAM_TIMEOUT", detail)


# -- retrieval: index ------------------------------------------------------------

def index_empty_request(detail: str = "no file and no path") -> AppError:
    return AppError("INDEX_EMPTY_REQUEST", detail)


def index_bad_image(detail: str) -> AppError:
    return AppError("INDEX_BAD_IMAGE", detail)


def index_bad_embedding(detail: str) -> AppError:
    return AppError("INDEX_BAD_EMBEDDING", detail)


def index_unknown_scene(detail: str) -> AppError:
    return AppError("INDEX_UNKNOWN_SCENE", detail)


def index_qdrant_failed(detail: str) -> AppError:
    return AppError("INDEX_QDRANT_FAILED", detail)


def index_postgres_failed(detail: str) -> AppError:
    """Write / connection failure of the metadata database (ingest, deletions, scenes, startup)."""
    return AppError("INDEX_POSTGRES_FAILED", detail)


# -- retrieval: search -----------------------------------------------------------

def search_bad_image(detail: str) -> AppError:
    return AppError("SEARCH_BAD_IMAGE", detail)


def search_unknown_scene(detail: str) -> AppError:
    return AppError("SEARCH_UNKNOWN_SCENE", detail)


def search_bad_score_threshold(detail: str) -> AppError:
    return AppError("SEARCH_BAD_SCORE_THRESHOLD", detail)


def search_bad_embedding(detail: str) -> AppError:
    return AppError("SEARCH_BAD_EMBEDDING", detail)


def search_qdrant_failed(detail: str) -> AppError:
    return AppError("SEARCH_QDRANT_FAILED", detail)


def search_postgres_failed(detail: str) -> AppError:
    return AppError("SEARCH_POSTGRES_FAILED", detail)


def vector_dim_mismatch(detail: str) -> AppError:
    return AppError("VECTOR_DIM_MISMATCH", detail)


# -- retrieval: gallery list -----------------------------------------------------

def list_unknown_scene(detail: str) -> AppError:
    return AppError("LIST_UNKNOWN_SCENE", detail)


def list_postgres_failed(detail: str) -> AppError:
    return AppError("LIST_POSTGRES_FAILED", detail)


# -- enums -----------------------------------------------------------------------

def enum_not_found(detail: str) -> AppError:
    return AppError("ENUM_NOT_FOUND", detail)


def enum_not_editable(detail: str) -> AppError:
    """A system enum, or an editable one locked with editable: false."""
    return AppError("ENUM_NOT_EDITABLE", detail)


def enum_value_exists(detail: str) -> AppError:
    return AppError("ENUM_VALUE_EXISTS", detail)


def enum_file_invalid(detail: str) -> AppError:
    """The enums file cannot be read or breaks a rule; the detail says where, for whoever fixes it."""
    return AppError("ENUM_FILE_INVALID", detail)


def enum_file_write_failed(detail: str) -> AppError:
    return AppError("ENUM_FILE_WRITE_FAILED", detail)


# Codes for HTTPExceptions raised by the framework itself (404 route, 405 method, ...)
_STATUS_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    413: "FILE_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_ERROR",
    429: "TOO_MANY_REQUESTS",
    500: "INTERNAL_ERROR",
    502: "UPSTREAM_ERROR",
    503: "NOT_READY",
    504: "UPSTREAM_TIMEOUT",
}


def code_for_status(status: int) -> str:
    return _STATUS_CODES.get(int(status), f"HTTP_{int(status)}")
