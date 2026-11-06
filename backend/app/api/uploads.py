"""Reading uploaded files (the one bounded reader every endpoint uses)."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from fastapi import UploadFile
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.core import errors


async def read_upload(
    file: UploadFile,
    max_bytes: int,
    *,
    allowed_content_types: Collection[str] | None = None,
    empty_detail: str = "Uploaded file is empty.",
    too_large_detail: str | None = None,
) -> bytes:
    """Read at most max_bytes + 1 bytes; optionally check the declared MIME type.

    EMPTY_FILE (400) / FILE_TOO_LARGE (413) / UNSUPPORTED_MEDIA_TYPE (415).
    The caller owns and closes the UploadFile.
    """
    if allowed_content_types is not None and file.content_type and file.content_type not in allowed_content_types:
        raise errors.unsupported_media_type(f"unsupported image content type: {file.content_type}")
    data = await file.read(max_bytes + 1)
    if not data:
        raise errors.empty_file(empty_detail)
    if len(data) > max_bytes:
        raise errors.file_too_large(too_large_detail or f"File too large (> {max_bytes} bytes).")
    return data


def form_upload(value: Any) -> UploadFile | None:
    """A form field value if it is an uploaded file, else None."""
    return value if isinstance(value, (UploadFile, StarletteUploadFile)) else None
