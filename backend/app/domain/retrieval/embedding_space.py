"""Resolve and compare embedding_space_id (SSOT §1.5).

维数相同 ≠ 可混搜。换权重 / 输出头 / 预处理后必须换 space_id。
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, Mapping, Sequence

logger = logging.getLogger("embedding_space")


def _norm_path(path: str) -> str:
    return str(path or "").replace("\\", "/").strip()


def _join_nums(values: Sequence[Any] | None, *, default: str = "") -> str:
    if values is None:
        return default
    return ",".join(str(v) for v in values)


def preprocess_canonical(emb: Mapping[str, Any]) -> str:
    """Stable preprocess text: scale|mean|std|img_interp (arrays comma-joined)."""
    scale = emb.get("scale", [224, 224])
    mean = emb.get("mean", [0.485, 0.456, 0.406])
    std = emb.get("std", [0.229, 0.224, 0.225])
    interp = emb.get("img_interp", "cubic")
    return "|".join(
        [
            _join_nums(scale if isinstance(scale, (list, tuple)) else [scale]),
            _join_nums(mean if isinstance(mean, (list, tuple)) else [mean]),
            _join_nums(std if isinstance(std, (list, tuple)) else [std]),
            str(interp).strip(),
        ]
    )


def resolve_model_file(model_path: str) -> str | None:
    """Return absolute path to ONNX if found; else None."""
    raw = (model_path or "").strip()
    if not raw:
        return None
    candidates = [raw, os.path.abspath(raw)]
    # Common: cwd is backend/; also try repo backend root next to this package.
    here = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    candidates.append(os.path.join(here, raw.replace("/", os.sep)))
    # backend/config/... when cwd is repo root
    candidates.append(os.path.join(here, "config", *raw.replace("\\", "/").split("/")[1:]))
    seen: set[str] = set()
    for c in candidates:
        if not c or c in seen:
            continue
        seen.add(c)
        if os.path.isfile(c):
            return c
    return None


def weights_fingerprint(model_path: str) -> str | None:
    """sha256(onnx bytes)[:16] hex; None if file missing."""
    path = resolve_model_file(model_path)
    if not path:
        logger.warning(
            "weights_fingerprint skipped: ONNX not found for model_path=%s "
            "(canonical will omit fingerprint segment; formal env should include it)",
            model_path,
        )
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()[:16]


def build_canonical(
    *,
    model_path: str,
    output_name: str,
    vector_dim: int,
    preprocess: str,
    fingerprint: str | None,
) -> str:
    parts = [
        _norm_path(model_path),
        str(output_name),
        str(int(vector_dim)),
        preprocess,
    ]
    if fingerprint:
        parts.append(fingerprint)
    return "|".join(parts)


def auto_space_id(canonical: str) -> str:
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    return f"es_{digest}"


def resolve_space_id(
    config: Mapping[str, Any],
    *,
    vector_dim: int | None = None,
) -> str:
    """Return explicit EMBEDDING_CONFIG.embedding_space_id or auto-generated es_* id."""
    emb = dict(config.get("EMBEDDING_CONFIG") or {})
    explicit = emb.get("embedding_space_id")
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip()

    q = dict(config.get("QDRANT_CONFIG") or {})
    dim = int(
        vector_dim
        if vector_dim is not None
        else q.get("vector_dim") or q.get("vector_size") or 1024
    )
    model_path = str(emb.get("model_path") or "")
    output_name = str(emb.get("output_name") or "cls_emb")
    preprocess = preprocess_canonical(emb)
    fp = weights_fingerprint(model_path) if model_path else None
    canonical = build_canonical(
        model_path=model_path,
        output_name=output_name,
        vector_dim=dim,
        preprocess=preprocess,
        fingerprint=fp,
    )
    space_id = auto_space_id(canonical)
    logger.info(
        "embedding_space_id auto-generated | id=%s | canonical=%s",
        space_id,
        canonical,
    )
    return space_id


def spaces_equal(configured: str, stored: str | None) -> bool:
    if stored is None:
        return True
    return str(configured) == str(stored)
