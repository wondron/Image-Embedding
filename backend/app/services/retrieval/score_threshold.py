"""score_threshold 的解析和严格校验（SSOT 3.2.1 节）。

没传 / null -> 0.55（开启拒绝）。显式 0 -> 关闭。
合法取值：{0} ∪ [0.30, 1]。超出范围 -> SEARCH_BAD_SCORE_THRESHOLD。
"""

from __future__ import annotations

import math
from typing import Optional, Union

from app.core import errors as err

DEFAULT_SCORE_THRESHOLD = 0.55
MIN_SCORE_THRESHOLD = 0.30
MAX_SCORE_THRESHOLD = 1.0


def resolve_score_threshold(value: Optional[Union[float, int]] = None) -> float:
    """把请求的 score_threshold 规范化成生效的阈值 T。

    - None（没传 / JSON null）-> DEFAULT_SCORE_THRESHOLD（0.55）
    - 0 -> 0.0（关闭拒绝；不按分数过滤）
    - 其他值必须是有限数，且在 [0.30, 1] 内
    """
    if value is None:
        return float(DEFAULT_SCORE_THRESHOLD)

    try:
        t = float(value)
    except (TypeError, ValueError) as exc:
        raise err.search_bad_score_threshold(
            f"score_threshold must be a finite float; got {value!r}"
        ) from exc

    if not math.isfinite(t):
        raise err.search_bad_score_threshold(
            f"score_threshold must be finite; got {t!r}"
        )

    # 哨兵值：显式 0 关闭拒绝
    if t == 0.0:
        return 0.0

    if MIN_SCORE_THRESHOLD <= t <= MAX_SCORE_THRESHOLD:
        return float(t)

    raise err.search_bad_score_threshold(
        "score_threshold must be 0 (disable) or in "
        f"[{MIN_SCORE_THRESHOLD}, {MAX_SCORE_THRESHOLD}]; got {t!r}"
    )


def qdrant_score_threshold(effective_t: float) -> Optional[float]:
    """Qdrant 原生阈值：关闭拒绝时（T==0）为 None。"""
    if effective_t > 0.0:
        return float(effective_t)
    return None
