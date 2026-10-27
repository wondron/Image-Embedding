"""Runtime embedding checks per SSOT §1.2."""

from __future__ import annotations

import math
from typing import Sequence

from app.domain.retrieval import errors as err
from app.domain.retrieval.ports import VECTOR_DIM


def validate_embedding(vector: Sequence[float], *, for_search: bool = False) -> list[float]:
    code_bad = err.search_bad_embedding if for_search else err.index_bad_embedding
    try:
        values = [float(x) for x in vector]
    except (TypeError, ValueError) as exc:
        raise code_bad(f"embedding not numeric: {exc}") from exc
    if len(values) != VECTOR_DIM:
        raise code_bad(f"embedding dim={len(values)} expected {VECTOR_DIM}")
    if not all(math.isfinite(x) for x in values):
        raise code_bad("embedding contains NaN or Inf")
    norm_sq = sum(x * x for x in values)
    if norm_sq <= 0.0:
        raise code_bad("embedding has zero norm")
    return values
