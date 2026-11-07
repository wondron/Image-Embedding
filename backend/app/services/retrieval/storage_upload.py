"""Thin helpers to put original + thumb and return DB URLs (for next index knife).

Not a full IndexImageUseCase rewrite — call from M4+/index when ready.
"""

from __future__ import annotations

import io
import logging
from typing import Any
from uuid import UUID

from app.services.retrieval import errors as err
from app.repositories.ports import ImageStoragePort
from app.services.retrieval.storage_keys import (
    THUMB_LONG_EDGE,
    guess_ext_from_bytes_header,
    guess_ext_from_content_type,
)

logger = logging.getLogger("retrieval.storage_upload")


def make_thumb_webp(image_bytes: bytes, *, long_edge: int = THUMB_LONG_EDGE) -> bytes:
    """Resize so long edge == long_edge and encode WebP."""
    try:
        from PIL import Image
    except ImportError as exc:
        raise err.index_bad_image("Pillow required for thumb generation") from exc
    try:
        with Image.open(io.BytesIO(image_bytes)) as im:
            im = im.convert("RGB")
            w, h = im.size
            if w <= 0 or h <= 0:
                raise err.index_bad_image("invalid image size for thumb")
            scale = float(long_edge) / float(max(w, h))
            if scale < 1.0:
                nw = max(1, int(round(w * scale)))
                nh = max(1, int(round(h * scale)))
                im = im.resize((nw, nh), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            im.save(out, format="WEBP", quality=80, method=4)
            return out.getvalue()
    except err.RetrievalError:
        raise
    except Exception as exc:
        raise err.index_bad_image(f"thumb encode failed: {exc}") from exc


def put_original_and_thumb(
    storage: ImageStoragePort,
    image_id: UUID | str,
    image_bytes: bytes,
    *,
    ext: str | None = None,
    content_type: str | None = None,
) -> dict[str, Any]:
    """Upload original + thumb; return {original_url, thumb_url, ext, thumb_bytes}.

    Keys: original/{id}.{ext}, thumb/{id}.webp — no scene/category in key.
    """
    if not image_bytes:
        raise err.index_bad_image("empty image bytes")
    resolved_ext = ext or guess_ext_from_content_type(content_type)
    if ext is None and content_type is None:
        resolved_ext = guess_ext_from_bytes_header(image_bytes)
    original_url = storage.put_original(
        image_id, image_bytes, ext=resolved_ext, content_type=content_type
    )
    thumb_bytes = make_thumb_webp(image_bytes)
    thumb_url = storage.put_thumb(image_id, thumb_bytes, content_type="image/webp")
    logger.info(
        "stored original+thumb id=%s ext=%s",
        image_id,
        resolved_ext,
    )
    return {
        "original_url": original_url,
        "thumb_url": thumb_url,
        "ext": resolved_ext,
        "thumb_bytes": thumb_bytes,
    }
