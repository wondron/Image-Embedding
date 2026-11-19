"""按 SSOT §1.2 做的运行时 embedding 检查。"""

from __future__ import annotations

import math
from typing import Sequence

from app.core import errors as err
from app.repositories.ports import VECTOR_DIM


def validate_embedding(
    vector: Sequence[float],
    *,
    for_search: bool = False,
    expected_dim: int = VECTOR_DIM,
) -> list[float]:
    """是数字、有限、非零，且长度为 expected_dim（= 当前模型的维度）。"""
    code_bad = err.search_bad_embedding if for_search else err.index_bad_embedding
    try:
        values = [float(x) for x in vector]
    except (TypeError, ValueError) as exc:
        raise code_bad(f"embedding not numeric: {exc}") from exc
    if len(values) != int(expected_dim):
        raise code_bad(f"embedding dim={len(values)} expected {expected_dim}")
    if not all(math.isfinite(x) for x in values):
        raise code_bad("embedding contains NaN or Inf")
    norm_sq = sum(x * x for x in values)
    if norm_sq <= 0.0:
        raise code_bad("embedding has zero norm")
    return values
