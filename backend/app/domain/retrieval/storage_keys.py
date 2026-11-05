"""S3 object key helpers (contract §5). Keys never include scene/category."""

from __future__ import annotations

from uuid import UUID

from app.domain.retrieval import errors as err

THUMB_EXT = "webp"
THUMB_LONG_EDGE = 512


def _norm_uuid(image_id: UUID | str) -> str:
    if isinstance(image_id, UUID):
        return str(image_id)
    text = str(image_id).strip()
    if not text:
        raise err.validation_error("image_id is empty")
    return str(UUID(text))


def _norm_ext(ext: str | None, *, default: str = "jpg") -> str:
    raw = (ext or default).strip().lower()
    if raw.startswith("."):
        raw = raw[1:]
    if not raw or "/" in raw or "\\" in raw:
        raise err.validation_error(f"invalid object extension: {ext!r}")
    return raw


def original_key(image_id: UUID | str, ext: str) -> str:
    """original/{image_id}.{ext}"""
    return f"original/{_norm_uuid(image_id)}.{_norm_ext(ext)}"


def thumb_key(image_id: UUID | str) -> str:
    """thumb/{image_id}.webp (long-edge 512 WebP by contract)."""
    return f"thumb/{_norm_uuid(image_id)}.{THUMB_EXT}"


def staging_key(candidate_id: UUID | str, ext: str) -> str:
    """staging/{candidate_id}.{ext}"""
    return f"staging/{_norm_uuid(candidate_id)}.{_norm_ext(ext)}"


def public_url(public_base_url: str, key: str) -> str:
    base = (public_base_url or "").rstrip("/")
    k = key.lstrip("/")
    if not base:
        raise err.validation_error("public_base_url is required")
    return f"{base}/{k}"


def guess_ext_from_content_type(content_type: str | None) -> str:
    ct = (content_type or "").split(";")[0].strip().lower()
    mapping = {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "image/gif": "gif",
    }
    return mapping.get(ct, "jpg")


def guess_ext_from_bytes_header(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return "jpg"
