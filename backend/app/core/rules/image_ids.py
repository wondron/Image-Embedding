"""图片 id 规则（契约 A7）。

id = uuid_from_bytes(sha256(original)[0:16])；保留内容哈希的字节；
**不要**改写 RFC 4122 的 version/variant 位。
"""

from __future__ import annotations

import uuid

from app.core import errors as err


def uuid_from_sha256(sha256_bytes: bytes) -> uuid.UUID:
    if not isinstance(sha256_bytes, (bytes, bytearray)) or len(sha256_bytes) < 16:
        raise err.validation_error("sha256 must be >=16 bytes")
    return uuid.UUID(bytes=bytes(sha256_bytes[:16]))
