"""score_threshold resolve + hard validate (SSOT section 3.2.1).

Omitted / null -> 0.55 (rejection ON). Explicit 0 -> OFF.
Legal set: {0} union [0.30, 0.95]. Out of range -> SEARCH_BAD_SCORE_THRESHOLD.
"""

from __future__ import annotations

import math
from typing import Optional, Union

from app.domain.retrieval import errors as err

DEFAULT_SCORE_THRESHOLD = 0.55
MIN_SCORE_THRESHOLD = 0.30
MAX_SCORE_THRESHOLD = 0.95


def resolve_score_threshold(value: Optional[Union[float, int]] = None) -> float:
    """Normalize request score_threshold to effective T.

    - None (omit / JSON null) -> DEFAULT_SCORE_THRESHOLD (0.55)
    - 0 -> 0.0 (disable rejection; no score filter)
    - otherwise must be finite and in [0.30, 0.95]
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

    # Sentinel: explicit 0 disables rejection
    if t == 0.0:
        return 0.0

    if MIN_SCORE_THRESHOLD <= t <= MAX_SCORE_THRESHOLD:
        return float(t)

    raise err.search_bad_score_threshold(
        "score_threshold must be 0 (disable) or in "
        f"[{MIN_SCORE_THRESHOLD}, {MAX_SCORE_THRESHOLD}]; got {t!r}"
    )


def qdrant_score_threshold(effective_t: float) -> Optional[float]:
    """Native Qdrant threshold: None when rejection disabled (T==0)."""
    if effective_t > 0.0:
        return float(effective_t)
    return None
