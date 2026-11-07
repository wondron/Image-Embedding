"""Image storage: original images, thumbnails and staging files (contract §5).

Any S3-compatible service (AWS S3, MinIO, Aliyun OSS) behind ImageStoragePort.
Default: MockImageStorage — builds https URLs from public_base_url + key, no upload.
IMAGE_STORAGE_CONFIG.use_boto3 true: S3ImageStorage uploads through boto3, with the
credentials from the same section (Nacos / local YAML).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.config.settings import ImageStorageSettings
from app.services.retrieval import errors as err
from app.repositories.ports import ImageStoragePort
from app.services.retrieval.storage_keys import (
    original_key,
    public_url,
    staging_key,
    thumb_key,
)

logger = logging.getLogger("object_store")


def _key_from_url_or_key(url_or_key: str, public_base_url: str) -> str:
    raw = (url_or_key or "").strip()
    if not raw:
        raise err.validation_error("url_or_key is empty")
    base = (public_base_url or "").rstrip("/")
    if raw.startswith("http://") or raw.startswith("https://"):
        if base and raw.startswith(base + "/"):
            return raw[len(base) + 1 :]
        parts = raw.split("/")
        for i, p in enumerate(parts):
            if p in {"original", "thumb", "staging"} and i + 1 < len(parts):
                return "/".join(parts[i:])
        raise err.validation_error(f"cannot derive object key from url: {raw!r}")
    return raw.lstrip("/")


class MockImageStorage(ImageStoragePort):
    """In-process mock: records puts; optional mirror dir for local bytes."""

    def __init__(self, settings: ImageStorageSettings) -> None:
        self.settings = settings
        self.public_base_url = settings.public_base_url
        self.bucket = settings.bucket
        self._objects: dict[str, bytes] = {}
        self._deleted: list[str] = []
        self._mirror = Path(settings.mock_mirror_dir) if settings.mock_mirror_dir else None

    def _put(self, key: str, data: bytes) -> str:
        if not isinstance(data, (bytes, bytearray)):
            raise err.validation_error("object data must be bytes")
        blob = bytes(data)
        self._objects[key] = blob
        if self._mirror is not None:
            target = self._mirror / key
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
        url = public_url(self.public_base_url, key)
        logger.info("mock put key=%s bytes=%s url=%s", key, len(blob), url)
        return url

    def put_original(
        self,
        image_id: UUID | str,
        data: bytes,
        *,
        ext: str,
        content_type: str | None = None,
    ) -> str:
        return self._put(original_key(image_id, ext), data)

    def put_thumb(
        self,
        image_id: UUID | str,
        data: bytes,
        *,
        content_type: str | None = "image/webp",
    ) -> str:
        return self._put(thumb_key(image_id), data)

    def put_staging(
        self,
        candidate_id: UUID | str,
        data: bytes,
        *,
        ext: str,
        content_type: str | None = None,
    ) -> str:
        return self._put(staging_key(candidate_id, ext), data)

    def delete_by_url_or_key(self, url_or_key: str) -> None:
        key = _key_from_url_or_key(url_or_key, self.public_base_url)
        self._objects.pop(key, None)
        self._deleted.append(key)
        if self._mirror is not None:
            p = self._mirror / key
            try:
                p.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("mock mirror unlink failed key=%s: %s", key, exc)
        logger.info("mock delete key=%s", key)

    def ping(self) -> dict[str, Any]:
        return {
            "ok": True,
            "driver": "mock",
            "bucket": self.bucket,
            "public_base_url": self.public_base_url,
            "objects": len(self._objects),
        }


class S3ImageStorage(ImageStoragePort):
    """Real S3 via boto3 when IMAGE_STORAGE_CONFIG.use_boto3 is true."""

    def __init__(self, settings: ImageStorageSettings) -> None:
        self.settings = settings
        self.public_base_url = settings.public_base_url
        self.bucket = settings.bucket
        try:
            import boto3
            from botocore.client import Config
        except ImportError as exc:
            raise err.not_ready(
                "boto3 not installed; pip install boto3 or set IMAGE_STORAGE_CONFIG.use_boto3: false"
            ) from exc
        access, secret = settings.access_key_id, settings.secret_access_key
        if not access or not secret:
            raise err.not_ready(
                "missing credentials: IMAGE_STORAGE_CONFIG.access_key_id / secret_access_key"
            )
        kwargs: dict[str, Any] = {
            "service_name": "s3",
            "aws_access_key_id": access,
            "aws_secret_access_key": secret,
            "region_name": settings.region,
        }
        if settings.endpoint_url:
            kwargs["endpoint_url"] = settings.endpoint_url
            kwargs["config"] = Config(s3={"addressing_style": "path"})
        self._client = boto3.client(**kwargs)

    def _put(self, key: str, data: bytes, content_type: str | None) -> str:
        extra: dict[str, Any] = {}
        if content_type:
            extra["ContentType"] = content_type
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data, **extra)
        return public_url(self.public_base_url, key)

    def put_original(
        self,
        image_id: UUID | str,
        data: bytes,
        *,
        ext: str,
        content_type: str | None = None,
    ) -> str:
        return self._put(original_key(image_id, ext), data, content_type)

    def put_thumb(
        self,
        image_id: UUID | str,
        data: bytes,
        *,
        content_type: str | None = "image/webp",
    ) -> str:
        return self._put(thumb_key(image_id), data, content_type)

    def put_staging(
        self,
        candidate_id: UUID | str,
        data: bytes,
        *,
        ext: str,
        content_type: str | None = None,
    ) -> str:
        return self._put(staging_key(candidate_id, ext), data, content_type)

    def delete_by_url_or_key(self, url_or_key: str) -> None:
        key = _key_from_url_or_key(url_or_key, self.public_base_url)
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def ping(self) -> dict[str, Any]:
        try:
            self._client.head_bucket(Bucket=self.bucket)
            return {
                "ok": True,
                "driver": "boto3",
                "bucket": self.bucket,
                "public_base_url": self.public_base_url,
            }
        except Exception as exc:
            return {
                "ok": False,
                "driver": "boto3",
                "detail": f"{type(exc).__name__}: {exc}",
                "bucket": self.bucket,
            }


def build_image_storage(settings: ImageStorageSettings) -> ImageStoragePort:
    if settings.use_boto3:
        logger.info("object storage: boto3 driver bucket=%s", settings.bucket)
        return S3ImageStorage(settings)
    logger.info(
        "object storage: mock driver base=%s bucket=%s",
        settings.public_base_url,
        settings.bucket,
    )
    return MockImageStorage(settings)
