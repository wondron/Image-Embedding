"""存原图 + 缩略图并返回数据库 URL 的轻量辅助函数（给下一刀的入库用）。

不是对 IndexImageUseCase 的完整重写——准备好后从 M4+ / 入库流程调用。
"""

from __future__ import annotations

import io
import logging
from typing import Any
from uuid import UUID

from app.core import errors as err
from app.repositories.ports import ImageStoragePort
from app.utils.storage_keys import (
    THUMB_LONG_EDGE,
    different_objects,
    guess_ext_from_bytes_header,
    guess_ext_from_content_type,
)

logger = logging.getLogger("retrieval.storage_upload")


def make_thumb_webp(image_bytes: bytes, *, long_edge: int = THUMB_LONG_EDGE) -> bytes:
    """缩放到长边等于 long_edge，并编码成 WebP。"""
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
    except err.AppError:
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
    """上传原图 + 缩略图；返回 {original_url, thumb_url, ext, thumb_bytes}。

    key：original/{shard}/{id}.{ext}、thumb/{shard}/{id}.webp，shard = SHA-256[0:2] 的十六进制
    （id 的前两个字符）——key 里没有场景或类别；可选的 IMAGE_STORAGE_CONFIG.key_prefix 由存储端口通过 storage_keys 加上。
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


def drop_unused_upload(
    storage: ImageStoragePort,
    image_id: UUID | str,
    *,
    written: dict[str, Any],
    stored: dict[str, Any],
) -> None:
    """重复上传保留了已存的行之后：删除刚写入、但那一行没有引用的文件，尽力而为（失败写日志）。

    那一行保留它自己的 URL，所以当它们在其他 key 下时（在分片目录或 key_prefix 变更之前
    存的图片），新的副本不属于任何行。可能属于那一行的文件（相对 key 相同，或者 URL 形式
    没有相对 key）总是保留。
    """
    for field in ("original_url", "thumb_url"):
        url = str(written.get(field) or "")
        if not different_objects(url, stored.get(field)):
            continue
        try:
            storage.delete_by_url_or_key(url)
        except Exception:
            logger.warning("re-upload id=%s: unused %s not removed: %s", image_id, field, url, exc_info=True)
        else:
            logger.info("re-upload id=%s kept its stored %s; removed the unused copy %s", image_id, field, url)
