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
from urllib.parse import urlparse
from uuid import UUID

from app.core.config.settings import ImageStorageSettings
from app.core import errors as err
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


def _readable_key(url_or_key: str, public_base_url: str) -> str:
    """The key to read; a URL that is not one of this storage's objects cannot be read back."""
    try:
        return _key_from_url_or_key(url_or_key, public_base_url)
    except err.AppError as exc:
        raise err.image_file_unavailable(f"not a stored object: {exc.detail}") from exc



def s3_client_config(*, endpoint_url: str = "") -> Any:
    """botocore Config for S3-compatible stores, including Aliyun OSS.

    Newer boto3/botocore defaults request checksums to when_supported, which
    sends a STREAMING-UNSIGNED-PAYLOAD-TRAILER (aws-chunked) body. Aliyun OSS
    rejects that encoding with NotImplemented. when_required keeps optional
    checksums off, so PutObject uses a regular payload.

    Aliyun OSS endpoints use virtual-hosted addressing and V2 signatures.
    Other custom endpoints retain path-style addressing. Older botocore that
    does not accept checksum fields retains the same addressing and signature.
    """
    from botocore.client import Config

    host = (urlparse(endpoint_url).hostname or "").lower()
    is_aliyun_oss = host.endswith(".aliyuncs.com") and (
        host.startswith("oss-") or host.startswith("s3.oss-")
    )

    kwargs: dict[str, Any] = {
        "request_checksum_calculation": "when_required",
        "response_checksum_validation": "when_required",
    }
    if is_aliyun_oss:
        kwargs["signature_version"] = "s3"
        kwargs["s3"] = {"addressing_style": "virtual"}
    elif endpoint_url:
        kwargs["s3"] = {"addressing_style": "path"}
    try:
        return Config(**kwargs)
    except TypeError:
        kwargs.pop("request_checksum_calculation", None)
        kwargs.pop("response_checksum_validation", None)
        return Config(**kwargs)


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
        return self._put(original_key(image_id, ext, key_prefix=self.settings.key_prefix), data)

    def put_thumb(
        self,
        image_id: UUID | str,
        data: bytes,
        *,
        content_type: str | None = "image/webp",
    ) -> str:
        return self._put(thumb_key(image_id, key_prefix=self.settings.key_prefix), data)

    def put_staging(
        self,
        candidate_id: UUID | str,
        data: bytes,
        *,
        ext: str,
        content_type: str | None = None,
    ) -> str:
        return self._put(staging_key(candidate_id, ext, key_prefix=self.settings.key_prefix), data)

    def read_by_url_or_key(self, url_or_key: str) -> bytes:
        """From memory, else from the mirror dir: what survives a restart is what was mirrored."""
        key = _readable_key(url_or_key, self.public_base_url)
        if key in self._objects:
            return self._objects[key]
        if self._mirror is not None:
            path = self._mirror / key
            try:
                return path.read_bytes()
            except OSError as exc:
                raise err.image_file_unavailable(f"mock storage: {key!r} not in memory, mirror read failed: {exc}") from exc
        raise err.image_file_unavailable(f"mock storage keeps no bytes for {key!r} (set mock_mirror_dir to keep them)")

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
            "key_prefix": self.settings.key_prefix,
            "objects": len(self._objects),
        }


class S3ImageStorage(ImageStoragePort):
    """Real S3 via boto3 when IMAGE_STORAGE_CONFIG.use_boto3 is true.

    Client Config disables optional request/response checksums (when_required)
    so Aliyun OSS and similar stores do not see AWS flexible-checksum trailers.
    """

    def __init__(self, settings: ImageStorageSettings) -> None:
        self.settings = settings
        self.public_base_url = settings.public_base_url
        self.bucket = settings.bucket
        try:
            import boto3
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
            "config": s3_client_config(endpoint_url=settings.endpoint_url),
        }
        if settings.endpoint_url:
            kwargs["endpoint_url"] = settings.endpoint_url
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
        return self._put(original_key(image_id, ext, key_prefix=self.settings.key_prefix), data, content_type)

    def put_thumb(
        self,
        image_id: UUID | str,
        data: bytes,
        *,
        content_type: str | None = "image/webp",
    ) -> str:
        return self._put(thumb_key(image_id, key_prefix=self.settings.key_prefix), data, content_type)

    def put_staging(
        self,
        candidate_id: UUID | str,
        data: bytes,
        *,
        ext: str,
        content_type: str | None = None,
    ) -> str:
        return self._put(staging_key(candidate_id, ext, key_prefix=self.settings.key_prefix), data, content_type)

    def read_by_url_or_key(self, url_or_key: str) -> bytes:
        key = _readable_key(url_or_key, self.public_base_url)
        try:
            return self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except Exception as exc:  # NoSuchKey, access denied, network: all mean "cannot read it now"
            raise err.image_file_unavailable(f"get_object {self.bucket}/{key}: {type(exc).__name__}: {exc}") from exc

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
                "key_prefix": self.settings.key_prefix,
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
