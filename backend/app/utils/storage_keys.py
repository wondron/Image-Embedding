"""S3 对象 key 的辅助函数（契约 §5）。key 里从不包含场景或类别。

一张图的原图和缩略图放在一个分片目录里：原图 SHA-256 的前两个十六进制字符，
也就是图片 id 的前两个字符（契约 A7：id 是 SHA-256[0:16]），例如
original/a3/{image_id}.jpg 和 thumb/a3/{image_id}.webp。暂存区的 key
没有分片目录（候选 id 是随机的，不是内容哈希）。

可选的 IMAGE_STORAGE_CONFIG.key_prefix 规范化后不为空时，加在相对的
original / thumb / staging key 前面。
public_base_url 仍是存储桶 / CDN 的 URL 根，不是对象 key 的前缀。

有分片目录之前存的对象（original/{image_id}.{ext}）保留原来的 key：读取和删除
都按存下来的 URL，从不重新计算 key。
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
    """前缀不为空时拼成 ``{key_prefix}/{rel}``，否则就是 ``rel``。

    ``key_prefix`` 必须已经规范化（见 :func:`normalize_key_prefix`）。
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
    """一张图的对象所在的分片目录：图片 id 的前两个十六进制字符，也就是原图
    SHA-256 的前两个字符（契约 A7），小写。
    """
    return _norm_uuid(image_id)[:SHARD_HEX_CHARS]


def _image_key(directory: str, image_id: UUID | str, ext: str, key_prefix: str) -> str:
    uid = _norm_uuid(image_id)
    return apply_key_prefix(key_prefix, f"{directory}/{shard_dir(uid)}/{uid}.{ext}")


def original_key(image_id: UUID | str, ext: str, *, key_prefix: str = "") -> str:
    """original/{shard}/{image_id}.{ext}，可选地放在 key_prefix 下。"""
    return _image_key("original", image_id, _norm_ext(ext), key_prefix)


def thumb_key(image_id: UUID | str, *, key_prefix: str = "") -> str:
    """thumb/{shard}/{image_id}.webp（按契约是长边 512 的 WebP），可选地放在 key_prefix 下。"""
    return _image_key("thumb", image_id, THUMB_EXT, key_prefix)


def staging_key(candidate_id: UUID | str, ext: str, *, key_prefix: str = "") -> str:
    """staging/{candidate_id}.{ext}，可选地放在 key_prefix 下（没有分片：id 是随机的）。"""
    return apply_key_prefix(key_prefix, f"staging/{_norm_uuid(candidate_id)}.{_norm_ext(ext)}")


def relative_key(url_or_key: str | None) -> str | None:
    """一个存储 URL 或对象 key 的相对 key（契约 §5）：从 original / thumb / staging
    目录开始的部分，不含 URL 根和 key_prefix，例如 ``original/a3/{image_id}.jpg``。
    路径里没有这类目录时为 None。

    取最后一个这类目录：key_prefix 里不会出现这些目录名，URL 根在它前面。
    所以一个对象总是只有一个相对 key，不同的相对 key 就是不同的对象。
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
    """两个存储 URL（或 key）是否一定是不同的对象：两者都有相对 key 且不相同。
    相对 key 相同，或者有一方没有相对 key，都可能是同一个对象。
    """
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
