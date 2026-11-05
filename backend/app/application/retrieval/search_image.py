"""Search-image use case (PG v0.1 §4.3 / A4 / A5 / A6).

Self-built only: embed → Qdrant top-k filtered by scene code → hydrate from PG.
Empty self-built → source=empty. FlyB OUT OF SCOPE (never called).
score_threshold: omit→0.55; 0 disables; else {0}∪[0.30,1].
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.domain.retrieval import errors as err
from app.domain.retrieval.embedding_validate import validate_embedding
from app.domain.retrieval.ports import (
    EmbeddingPort,
    FlyBFallbackPort,
    MetadataStorePort,
    VectorStorePort,
)
from app.domain.retrieval.scenes import normalize_search_scene
from app.domain.retrieval.score_threshold import (
    qdrant_score_threshold,
    resolve_score_threshold,
)

logger = logging.getLogger("retrieval.search")


def _as_id_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return str(value)
    text = str(value).strip()
    return text or None


def _hit_from_row(
    row: dict[str, Any],
    score: float | None,
    *,
    scene_meta: dict[str, Any] | None,
    scene_label: dict[str, Any] | None,
) -> dict[str, Any]:
    image_id = _as_id_str(row.get("id"))
    original = row.get("original_url") or row.get("url")
    thumb = row.get("thumb_url")
    title = row.get("title") or row.get("category_name") or image_id or ""
    hit: dict[str, Any] = {
        "id": image_id,
        "title": title,
        "image_url": original,
        "thumbnail_url": thumb,
        "similarity": score,
        "category_id": row.get("category_id"),
        "category": row.get("category_name") or row.get("category"),
        "scenes": row.get("scenes"),
        "food_state": row.get("food_state"),
        "label_status": row.get("label_status"),
        "page_url": None,
        "site_name": "local",
        "text_snippets": [],
        "width": row.get("width"),
        "height": row.get("height"),
        "details": {
            "page_url": None,
            "site": "local",
            "text_snippets": [],
            "images": [
                {
                    "image_url": original,
                    "thumbnail_url": thumb,
                    "alt": title,
                    "width": row.get("width"),
                    "height": row.get("height"),
                }
            ],
        },
    }
    if scene_label:
        hit["scene_label"] = scene_label
    if scene_meta and scene_meta.get("outputs_heating"):
        hit["food_state"] = row.get("food_state")
        hit["heating_group"] = row.get("heating_group")
    return hit


def _empty_envelope(*, message: str = "no local matches") -> dict[str, Any]:
    return {
        "results": [],
        "status": "upstream_empty_response",
        "message": message,
        "upstream_error": None,
    }


class SearchImageUseCase:
    def __init__(
        self,
        embedding: EmbeddingPort,
        vectors: VectorStorePort,
        metadata: MetadataStorePort,
        flyb_fallback: FlyBFallbackPort | None = None,
    ) -> None:
        self._embedding = embedding
        self._vectors = vectors
        self._metadata = metadata
        # Kept for bootstrap ctor compatibility; never invoked (A4).
        self._flyb = flyb_fallback

    def execute(
        self,
        *,
        image_bytes: bytes | None = None,
        image_url: str | None = None,
        scene: str | None = None,
        top_k: int = 10,
        score_threshold: float | None = None,
        public_file_base: str | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        del public_file_base  # PG stores full https; no local file URL fallback
        if not image_bytes and not (image_url and str(image_url).strip()):
            raise err.validation_error("file or img_url is required")
        if not (1 <= int(top_k) <= 50):
            raise err.validation_error("top_k must be in 1..50")

        active_scene = normalize_search_scene(scene)
        effective_t = resolve_score_threshold(score_threshold)
        native_thr = qdrant_score_threshold(effective_t)

        try:
            if image_bytes:
                vector = self._embedding.embed_bytes(image_bytes)
            else:
                url = str(image_url).strip()
                try:
                    import requests

                    resp = requests.get(url, timeout=15.0)
                    resp.raise_for_status()
                    vector = self._embedding.embed_bytes(resp.content)
                except err.RetrievalError:
                    raise
                except Exception as exc:
                    raise err.search_bad_image(f"cannot fetch img_url: {exc}") from exc
        except err.RetrievalError:
            raise
        except Exception as exc:
            raise err.search_bad_image(f"embed failed: {exc}") from exc

        vector = validate_embedding(vector, for_search=True)

        raw_hits = self._vectors.search(
            vector,
            top_k=int(top_k),
            score_threshold=native_thr,
            scene=active_scene,
        )

        if effective_t > 0.0:
            raw_hits = [
                h for h in raw_hits if float(h.get("score") or 0) >= effective_t
            ]

        ids: list[str] = []
        score_by_id: dict[str, float] = {}
        for h in raw_hits:
            pid = _as_id_str(h.get("id"))
            if not pid:
                continue
            ids.append(pid)
            score_by_id[pid] = float(h.get("score") or 0.0)

        rows = (
            self._metadata.get_active_by_ids(ids, scene=active_scene) if ids else {}
        )

        scene_meta = None
        get_scene = getattr(self._metadata, "get_scene_by_code", None)
        if callable(get_scene):
            try:
                scene_meta = get_scene(active_scene)
            except Exception:
                logger.exception("get_scene_by_code failed scene=%s", active_scene)

        map_label = getattr(self._metadata, "map_category_to_scene_label", None)

        result: list[dict[str, Any]] = []
        for pid in ids:
            row = rows.get(pid) or rows.get(pid.lower())
            if not row:
                continue
            scene_label = None
            if callable(map_label) and row.get("category_id") is not None:
                try:
                    scene_label = map_label(active_scene, int(row["category_id"]))
                except Exception:
                    logger.exception("scene label map failed id=%s", pid)
            result.append(
                _hit_from_row(
                    row,
                    score_by_id.get(pid),
                    scene_meta=scene_meta,
                    scene_label=scene_label,
                )
            )

        if result:
            return {
                "ok": True,
                "trace_id": trace_id,
                "source": "self",
                "scene": active_scene,
                "result": result,
            }

        logger.info(
            "self-built empty after join; source=empty scene=%s (FlyB skipped A4)",
            active_scene,
        )
        return {
            "ok": True,
            "trace_id": trace_id,
            "source": "empty",
            "scene": active_scene,
            "result": _empty_envelope(
                message=f"no local matches for scene={active_scene}"
            ),
        }
