"""S3 object key helpers (contract §5). Keys never include scene/category.

An image's original and thumbnail sit in a shard directory: the first two hex characters of
the original's SHA-256, which are the first two of the image id (contract A7: the id is
SHA-256[0:16]), e.g. original/a3/{image_id}.jpg and thumb/a3/{image_id}.webp. Staging keys
have none (a candidate id is random, not a content hash).

Optional IMAGE_STORAGE_CONFIG.key_prefix is prepended to the relative
original / thumb / staging key when non-empty after normalization.
public_base_url remains the bucket/CDN URL root and is not the object-key prefix.

Objects stored before the shard directory (original/{image_id}.{ext}) keep their keys: reads
and deletes go by the stored URL, never by a key computed again.
"""

from __future__ import annotations

from urllib.parse import unquote, urlsplit
from uuid import UUID

from app.core import errors as err
from app.core.config.key_prefix import RESERVED_KEY_SEGMENTS, normalize_key_prefix

THUMB_EXT = "webp"
THUMB_LONG_EDGE = 512
SHARD_HEX_CHARS = 2

__all__ = (
    "SHARD_HEX_CHARS",
    "THUMB_EXT",
    "THUMB_LONG_EDGE",
    "apply_key_prefix",
    "different_objects",
    "guess_ext_from_bytes_header",
    "guess_ext_from_content_type",
    "normalize_key_prefix",
    "original_key",
    "public_url",
    "relative_key",
    "shard_dir",
    "staging_key",
    "thumb_key",
)


def apply_key_prefix(key_prefix: str, rel: str) -> str:
    """Compose ``{key_prefix}/{rel}`` when the prefix is non-empty; otherwise ``rel``.

    ``key_prefix`` must already be normalized (see :func:`normalize_key_prefix`).
    """
    prefix = key_prefix or ""
    relative = (rel or "").lstrip("/")
    if not prefix:
        return relative
    return f"{prefix}/{relative}"


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


def shard_dir(image_id: UUID | str) -> str:
    """The shard directory of an image's objects: the first two hex characters of its id, which
    are the first two of the original's SHA-256 (contract A7), in lowercase."""
    return _norm_uuid(image_id)[:SHARD_HEX_CHARS]


def _image_key(directory: str, image_id: UUID | str, ext: str, key_prefix: str) -> str:
    uid = _norm_uuid(image_id)
    return apply_key_prefix(key_prefix, f"{directory}/{shard_dir(uid)}/{uid}.{ext}")


def original_key(image_id: UUID | str, ext: str, *, key_prefix: str = "") -> str:
    """original/{shard}/{image_id}.{ext}, optionally under key_prefix."""
    return _image_key("original", image_id, _norm_ext(ext), key_prefix)


def thumb_key(image_id: UUID | str, *, key_prefix: str = "") -> str:
    """thumb/{shard}/{image_id}.webp (long-edge 512 WebP by contract), optionally under key_prefix."""
    return _image_key("thumb", image_id, THUMB_EXT, key_prefix)


def staging_key(candidate_id: UUID | str, ext: str, *, key_prefix: str = "") -> str:
    """staging/{candidate_id}.{ext}, optionally under key_prefix (no shard: the id is random)."""
    return apply_key_prefix(key_prefix, f"staging/{_norm_uuid(candidate_id)}.{_norm_ext(ext)}")


def relative_key(url_or_key: str | None) -> str | None:
    """The relative key (contract §5) of a stored URL or object key: the key from its
    original / thumb / staging directory on, without URL root or key_prefix, e.g.
    ``original/a3/{image_id}.jpg``. None when the path has no such directory.

    The last such directory is the key's own: key_prefix may not name one, and the URL root
    comes before it. So one object always has one relative key, and different relative keys
    are different objects.
    """
    path = (url_or_key or "").strip()
    if path.startswith(("http://", "https://")):
        path = urlsplit(path).path
    parts = [part for part in unquote(path).split("/") if part]
    for i in range(len(parts) - 2, -1, -1):
        if parts[i] in RESERVED_KEY_SEGMENTS:
            return "/".join(parts[i:])
    return None


def different_objects(url_or_key: str | None, other: str | None) -> bool:
    """Whether two stored URLs (or keys) are certainly different objects: both have a relative
    key and the keys differ. Same relative keys, or a form without one, may be one object."""
    key, other_key = relative_key(url_or_key), relative_key(other)
    return key is not None and other_key is not None and key != other_key


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
