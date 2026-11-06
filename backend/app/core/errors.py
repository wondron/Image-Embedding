"""Application errors: a stable machine code, a human detail, the HTTP status.

Framework-free, so services, repositories and clients can raise it.
app.core.exceptions renders every failure (AppError, HTTPException, request
validation, unexpected exceptions) as one envelope:

    {"ok": false, "error": "<CODE>", "detail": "<message>", "trace_id": "<id>"}

Domain modules may subclass (services.retrieval.errors.RetrievalError) and add
their own codes; the generic codes below are shared by every endpoint.
"""

from __future__ import annotations


class AppError(Exception):
    def __init__(self, code: str, detail: str, *, http_status: int = 500) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.http_status = int(http_status)

    @property
    def status_code(self) -> int:
        """Same attribute name as Starlette's HTTPException."""
        return self.http_status

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.code!r}, {self.detail!r}, http_status={self.http_status})"


# -- generic codes ---------------------------------------------------------------

def validation_error(detail: str) -> AppError:
    return AppError("VALIDATION_ERROR", detail, http_status=422)


def not_ready(detail: str) -> AppError:
    return AppError("NOT_READY", detail, http_status=503)


def internal_error(detail: str) -> AppError:
    return AppError("INTERNAL_ERROR", detail, http_status=500)


def empty_file(detail: str) -> AppError:
    return AppError("EMPTY_FILE", detail, http_status=400)


def file_too_large(detail: str) -> AppError:
    return AppError("FILE_TOO_LARGE", detail, http_status=413)


def unsupported_media_type(detail: str) -> AppError:
    return AppError("UNSUPPORTED_MEDIA_TYPE", detail, http_status=415)


def invalid_url(detail: str) -> AppError:
    return AppError("INVALID_URL", detail, http_status=422)


def forbidden_host(detail: str) -> AppError:
    return AppError("FORBIDDEN_HOST", detail, http_status=403)


def download_failed(detail: str, *, http_status: int = 502) -> AppError:
    return AppError("DOWNLOAD_FAILED", detail, http_status=http_status)


def download_timeout(detail: str) -> AppError:
    return AppError("DOWNLOAD_TIMEOUT", detail, http_status=504)


def upstream_error(detail: str, *, http_status: int = 502) -> AppError:
    return AppError("UPSTREAM_ERROR", detail, http_status=http_status)


def upstream_timeout(detail: str) -> AppError:
    return AppError("UPSTREAM_TIMEOUT", detail, http_status=504)


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
