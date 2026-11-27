"""图片存储：原图、缩略图和暂存文件（契约 §5）。

ImageStoragePort 背后可以是任何兼容 S3 的服务（AWS S3、MinIO、阿里云 OSS）。
默认：MockImageStorage——用 public_base_url + key 拼出 https URL，不上传。
IMAGE_STORAGE_CONFIG.use_boto3 为 true：S3ImageStorage 通过 boto3 上传，凭证取自同一个
配置段（Nacos / 本地 YAML）。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from app.core.config.settings import ImageStorageSettings
from app.core import errors as err
from app.repositories.ports import ImageStoragePort
from app.utils.storage_keys import (
    apply_key_prefix,
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


def _not_found(exc: Exception) -> bool:
    """botocore 的 ClientError 是不是"没有这个对象"（HEAD 没有响应体，错误码就是 404）。"""
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    code = str((response.get("Error") or {}).get("Code") or "")
    status = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
    return code in {"404", "NoSuchKey", "NotFound"} or status == 404


def _readable_key(url_or_key: str, public_base_url: str) -> str:
    """要读取的 key；不是本存储对象的 URL 读不回来。"""
    try:
        return _key_from_url_or_key(url_or_key, public_base_url)
    except err.AppError as exc:
        raise err.image_file_unavailable(f"not a stored object: {exc.detail}") from exc



def s3_client_config(*, endpoint_url: str = "") -> Any:
    """给兼容 S3 的存储（包括阿里云 OSS）用的 botocore Config。

    较新的 boto3/botocore 默认把请求校验和设为 when_supported，会发送
    STREAMING-UNSIGNED-PAYLOAD-TRAILER（aws-chunked）格式的请求体。阿里云 OSS
    以 NotImplemented 拒绝这种编码。when_required 关闭可选的校验和，
    所以 PutObject 用普通的请求体。

    阿里云 OSS 的 endpoint 使用虚拟主机寻址和 V2 签名。
    其他自定义 endpoint 保持路径寻址。不接受校验和字段的旧版 botocore
    保持同样的寻址方式和签名。
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
    """进程内的 mock：记录上传；可选的镜像目录保存本地字节。"""

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
        """先从内存读，否则从镜像目录读：重启后还在的只有镜像过的内容。"""
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

    def inventory_unavailable(self) -> str | None:
        if self._mirror is None:
            return "mock storage keeps the bytes in memory only; set IMAGE_STORAGE_CONFIG.mock_mirror_dir to check files"
        return None

    def head(self, url_or_key: str) -> int | None:
        key = _readable_key(url_or_key, self.public_base_url)
        if key in self._objects:
            return len(self._objects[key])
        if self._mirror is None:
            raise err.image_file_unavailable(f"mock storage keeps no bytes for {key!r} (set mock_mirror_dir to keep them)")
        path = self._mirror / key
        try:
            return path.stat().st_size if path.is_file() else None
        except OSError as exc:
            raise err.image_file_unavailable(f"mock storage: stat {key!r} failed: {exc}") from exc

    def list_keys(self, directory: str) -> Iterator[tuple[str, int]]:
        prefix = apply_key_prefix(self.settings.key_prefix, directory.strip("/") + "/")
        seen: set[str] = set()
        for key, data in sorted(self._objects.items()):
            if key.startswith(prefix):
                seen.add(key)
                yield key, len(data)
        if self._mirror is None:
            return
        root = self._mirror / prefix
        if not root.is_dir():
            return
        try:
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    key = path.relative_to(self._mirror).as_posix()
                    if key not in seen:
                        yield key, path.stat().st_size
        except OSError as exc:
            raise err.image_file_unavailable(f"mock storage: listing {prefix!r} failed: {exc}") from exc

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
    """IMAGE_STORAGE_CONFIG.use_boto3 为 true 时，通过 boto3 使用真实的 S3。

    客户端 Config 关闭可选的请求 / 响应校验和（when_required），这样阿里云 OSS
    之类的存储不会看到 AWS 灵活校验和的 trailer。
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
        except Exception as exc:  # NoSuchKey、无权访问、网络问题：都表示"现在读不了"
            raise err.image_file_unavailable(f"get_object {self.bucket}/{key}: {type(exc).__name__}: {exc}") from exc

    def delete_by_url_or_key(self, url_or_key: str) -> None:
        key = _key_from_url_or_key(url_or_key, self.public_base_url)
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def inventory_unavailable(self) -> str | None:
        return None

    def head(self, url_or_key: str) -> int | None:
        key = _readable_key(url_or_key, self.public_base_url)
        try:
            response = self._client.head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            if _not_found(exc):
                return None
            # 403 也在这里：没有 ListBucket 权限时 S3 对不存在的 key 回 403，说不清在不在
            raise err.image_file_unavailable(f"head_object {self.bucket}/{key}: {type(exc).__name__}: {exc}") from exc
        return int(response.get("ContentLength") or 0)

    def list_keys(self, directory: str) -> Iterator[tuple[str, int]]:
        prefix = apply_key_prefix(self.settings.key_prefix, directory.strip("/") + "/")
        try:
            # ListObjects（v1）：兼容 S3 的服务（含阿里云 OSS）都支持；分页器按最后一个 key 续页
            for page in self._client.get_paginator("list_objects").paginate(Bucket=self.bucket, Prefix=prefix):
                for item in page.get("Contents") or []:
                    yield str(item["Key"]), int(item.get("Size") or 0)
        except Exception as exc:
            raise err.image_file_unavailable(f"list_objects {self.bucket}/{prefix}: {type(exc).__name__}: {exc}") from exc

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
        logger.info("对象存储：boto3 驱动 | bucket=%s", settings.bucket)
        return S3ImageStorage(settings)
    logger.info(
        "对象存储：mock 驱动 | base=%s | bucket=%s",
        settings.public_base_url,
        settings.bucket,
    )
    return MockImageStorage(settings)
