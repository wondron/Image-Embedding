"""Search-image use case (SSOT §3.2 / §4.1).

Self-built-first:
  embed → Qdrant top-k → score_threshold filter → MySQL active join.
FlyB ONLY when final list length == 0.
Infra errors (embed/qdrant/mysql) NEVER fall back to FlyB.
"""

from __future__ import annotations

import logging
from typing import Any

from app.domain.retrieval import errors as err
from app.domain.retrieval.embedding_validate import validate_embedding
from app.domain.retrieval.ports import (
    EmbeddingPort,
    FlyBFallbackPort,
    MetadataStorePort,
    VectorStorePort,
)

logger = logging.getLogger("retrieval.search")


def _file_url(public_file_base: str | None, image_id: int, stored_url: str | None) -> str | None:
    if stored_url:
        return stored_url
    if public_file_base:
        return f"{public_file_base.rstrip('/')}/api/images/file?id={image_id}"
    return None


def _hit_from_row(
    row: dict[str, Any],
    score: float | None,
    *,
    public_file_base: str | None,
) -> dict[str, Any]:
    image_id = int(row["id"])
    image_url = _file_url(public_file_base, image_id, row.get("url"))
    title = row.get("title") or Path_name(row.get("path"))
    width = row.get("width")
    height = row.get("height")
    return {
        "id": str(image_id),
        "title": title,
        "image_url": image_url,
        "thumbnail_url": None,
        "similarity": score,
        "category": row.get("category"),
        "page_url": None,
        "site_name": "local",
        "text_snippets": [],
        "width": width,
        "height": height,
        "details": {
            "page_url": None,
            "site": "local",
            "text_snippets": [],
            "images": [
                {
                    "image_url": image_url,
                    "thumbnail_url": None,
                    "alt": title,
                    "width": width,
                    "height": height,
                }
            ],
        },
    }


def Path_name(path: str | None) -> str:
    if not path:
        return ""
    from pathlib import Path

    return Path(path).name


def _normalize_flyb(raw: Any) -> tuple[str, Any]:
    """Return (source, result_payload) for FlyB outcome."""
    if isinstance(raw, dict) and raw.get("status") == "upstream_empty_response":
        return "empty", {
            "results": [],
            "status": "upstream_empty_response",
            "message": "no local matches; flyb also empty",
            "upstream_error": raw.get("upstream_error"),
        }
    if isinstance(raw, list):
        if not raw:
            return "empty", {
                "results": [],
                "status": "upstream_empty_response",
                "message": "no local matches; flyb also empty",
                "upstream_error": None,
            }
        # Map FlyB docs toward FE SearchImage-ish wire
        mapped = []
        for i, doc in enumerate(raw):
            if not isinstance(doc, dict):
                continue
            details = doc.get("details") or {}
            mapped.append(
                {
                    "id": str(doc.get("id") or f"flyb-{i}"),
                    "title": doc.get("title"),
                    "image_url": doc.get("image_url"),
                    "thumbnail_url": None,
                    "similarity": doc.get("similiar") or doc.get("similarity"),
                    "category": doc.get("category"),
                    "page_url": details.get("page_url"),
                    "site_name": (details.get("site") or {}).get("Name")
                    if isinstance(details.get("site"), dict)
                    else details.get("site") or "flyb",
                    "text_snippets": details.get("text_snippets") or [],
                    "width": None,
                    "height": None,
                    "details": details,
                }
            )
        if not mapped:
            return "empty", {
                "results": [],
                "status": "upstream_empty_response",
                "message": "no local matches; flyb also empty",
                "upstream_error": None,
            }
        return "flyb", mapped
    return "empty", {
        "results": [],
        "status": "upstream_empty_response",
        "message": "no local matches; flyb also empty",
        "upstream_error": None,
    }


class SearchImageUseCase:
    def __init__(
        self,
        embedding: EmbeddingPort,
        vectors: VectorStorePort,
        metadata: MetadataStorePort,
        flyb_fallback: FlyBFallbackPort,
    ) -> None:
        self._embedding = embedding
        self._vectors = vectors
        self._metadata = metadata
        self._flyb = flyb_fallback

    def execute(
        self,
        *,
        image_bytes: bytes | None = None,
        image_url: str | None = None,
        top_k: int = 10,
        score_threshold: float | None = None,
        public_file_base: str | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        if not image_bytes and not (image_url and str(image_url).strip()):
            raise err.validation_error("file or img_url is required")
        if not (1 <= int(top_k) <= 50):
            raise err.validation_error("top_k must be in 1..50")

        # Keep bytes for potential FlyB (download once if URL)
        bytes_for_flyb = image_bytes
        try:
            if image_bytes:
                vector = self._embedding.embed_bytes(image_bytes)
            else:
                # Prefer downloading once so FlyB can reuse bytes
                url = str(image_url).strip()
                try:
                    import requests

                    resp = requests.get(url, timeout=15.0)
                    resp.raise_for_status()
                    bytes_for_flyb = resp.content
                except Exception as exc:
                    raise err.search_bad_image(f"cannot fetch img_url: {exc}") from exc
                vector = self._embedding.embed_bytes(bytes_for_flyb)
        except err.RetrievalError:
            raise
        except Exception as exc:
            raise err.search_bad_image(f"embed failed: {exc}") from exc

        vector = validate_embedding(vector, for_search=True)

        # Qdrant search — infra errors must NOT fall back to FlyB
        raw_hits = self._vectors.search(
            vector,
            top_k=int(top_k),
            score_threshold=score_threshold,
        )

        # Extra threshold filter (domain may already apply; keep strict >=)
        if score_threshold is not None:
            thr = float(score_threshold)
            raw_hits = [h for h in raw_hits if float(h.get("score") or 0) >= thr]

        ids: list[int] = []
        score_by_id: dict[int, float] = {}
        for h in raw_hits:
            try:
                pid = int(h["id"])
            except (KeyError, TypeError, ValueError):
                continue
            ids.append(pid)
            score_by_id[pid] = float(h.get("score") or 0.0)

        rows = self._metadata.get_active_by_ids(ids) if ids else {}
        # Preserve Qdrant score order
        result: list[dict[str, Any]] = []
        for pid in ids:
            row = rows.get(pid)
            if not row:
                continue
            result.append(
                _hit_from_row(
                    row,
                    score_by_id.get(pid),
                    public_file_base=public_file_base,
                )
            )

        if result:
            return {
                "ok": True,
                "trace_id": trace_id,
                "source": "self",
                "result": result,
            }

        # Final list empty → ONLY place allowed to call FlyB
        logger.info("self-built top-k empty after join; invoking FlyB fallback")
        flyb_raw = self._flyb.search_fallback(
            image_bytes=bytes_for_flyb,
            image_url=image_url,
            top_k=int(top_k),
        )
        source, payload = _normalize_flyb(flyb_raw)
        return {
            "ok": True,
            "trace_id": trace_id,
            "source": source,
            "result": payload,
        }
