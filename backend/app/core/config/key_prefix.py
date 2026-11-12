"""Optional IMAGE_STORAGE_CONFIG.key_prefix normalization (OSS key-prefix contract §2)."""

from __future__ import annotations

import re

RESERVED_KEY_SEGMENTS = frozenset({"original", "thumb", "staging"})
KEY_PREFIX_MAX_LEN = 128
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_SLASH_TABLE = str.maketrans({"\\": "/", "／": "/", "＼": "/"})


def normalize_key_prefix(
    value: str | None,
    *,
    field: str = "IMAGE_STORAGE_CONFIG.key_prefix",
) -> str:
    """Return the stored prefix; empty means current-production keys (no prefix).

    Leading/trailing whitespace and slashes are stripped; ASCII backslashes and
    fullwidth slashes become `/`; repeated slashes collapse. Case is preserved.
    Illegal values raise ValueError and are not truncated or repaired.
    ``ImageEmbedding`` is not a code default.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是字符串，实际类型为 {type(value).__name__}")
    text = value.strip().translate(_SLASH_TABLE)
    text = re.sub(r"/+", "/", text).strip("/")
    if not text:
        return ""
    for segment in text.split("/"):
        if segment in {"", ".", ".."}:
            raise ValueError(
                f"{field} 不得包含空段、'.' 或 '..' 路径段，输入为 {value!r}"
            )
        if segment in RESERVED_KEY_SEGMENTS:
            raise ValueError(
                f"{field} 不得使用保留路径段 {segment!r}，输入为 {value!r}"
            )
        if _SEGMENT_RE.fullmatch(segment) is None:
            raise ValueError(
                f"{field} 含有非法路径段 {segment!r}，输入为 {value!r}"
            )
    if len(text) > KEY_PREFIX_MAX_LEN:
        raise ValueError(
            f"{field} 规范化后长度不得超过 {KEY_PREFIX_MAX_LEN} 个字符，实际长度为 {len(text)}"
        )
    return text
