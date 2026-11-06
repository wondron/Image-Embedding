"""Search-image use case (PG v0.1 §4.3 / A4 / A5 / A6 + §6.1 Wire).

Self-built only: embed → Qdrant top-k filtered by scene code → hydrate from PG.
Empty self-built → source=empty. FlyB OUT OF SCOPE (never called).
score_threshold: omit→0.55; 0 disables; else {0}∪[0.30,1].

§6.1 (R1): items envelope; original_url/thumb_url only; no FlyB shell / title /
image_url aliases; scene_label string|null; outputs_heating on envelope.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.services.retrieval import errors as err
from app.services.retrieval.active_model import ActiveModel
from app.services.retrieval.embedding_validate import validate_embedding
from app.repositories.ports import (
    EmbeddingPort,
    MetadataStorePort,
    VectorStorePort,
)
from app.services.retrieval.scenes import normalize_search_scene
from app.services.retrieval.score_threshold import (
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


def _scene_label_name(scene_label: Any) -> str | None:
    """P8: wire scene_label is string | null (squeeze repo dict → name)."""
    if scene_label is None:
        return None
    if isinstance(scene_label, str):
        text = scene_label.strip()
        return text or None
    if isinstance(scene_label, dict):
        name = scene_label.get("name")
        if name is None:
            return None
        text = str(name).strip()
        return text or None
    text = str(scene_label).strip()
    return text or None


def _outputs_heating(scene_meta: dict[str, Any] | None) -> bool:
    return bool(scene_meta and scene_meta.get("outputs_heating"))


def _hit_from_row(
    row: dict[str, Any],
    score: float | None,
    *,
    scene_meta: dict[str, Any] | None,
    scene_label: Any,
) -> dict[str, Any]:
    """Build one Search hit per §6.1.1 (authoritative fields only)."""
    image_id = _as_id_str(row.get("id"))
    original = row.get("original_url") or row.get("url")
    thumb = row.get("thumb_url")
    hit: dict[str, Any] = {
        "id": image_id,
        "original_url": original,
        "thumb_url": thumb,
        "similarity": score,
        "category_id": row.get("category_id"),
        "category": row.get("category_name") or row.get("category"),
        "scenes": row.get("scenes"),
        "label_status": row.get("label_status"),
        "description": row.get("description") or "",
        "width": row.get("width"),
        "height": row.get("height"),
    }
    # Optional: include scene_label when mapping was attempted (string|null).
    if scene_label is not None:
        hit["scene_label"] = _scene_label_name(scene_label)
    if _outputs_heating(scene_meta):
        hit["food_state"] = row.get("food_state")
        hit["heating_group"] = row.get("heating_group")
    return hit


class SearchImageUseCase:
    def __init__(
        self,
        embedding: EmbeddingPort,
        vectors: VectorStorePort,
        metadata: MetadataStorePort,
        *,
        model: ActiveModel,
    ) -> None:
        self._embedding = embedding
        self._vectors = vectors
        self._metadata = metadata
        self._model = model

    def execute(
        self,
        *,
        image_bytes: bytes | None = None,
        scene: str | None = None,
        top_k: int = 10,
        score_threshold: float | None = None,
    ) -> dict[str, Any]:
        """Search by query image bytes (the HTTP layer downloads img_url inputs)."""
        if not image_bytes:
            raise err.validation_error("file or img_url is required")
        if not (1 <= int(top_k) <= 50):
            raise err.validation_error("top_k must be in 1..50")

        active_scene = normalize_search_scene(scene)
        effective_t = resolve_score_threshold(score_threshold)
        native_thr = qdrant_score_threshold(effective_t)

        try:
            vector = self._embedding.embed_bytes(image_bytes)
        except err.RetrievalError:
            raise
        except Exception as exc:
            raise err.search_bad_image(f"embed failed: {exc}") from exc

        vector = validate_embedding(vector, for_search=True, expected_dim=self._model.dim)

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
        try:
            scene_meta = self._metadata.get_scene_by_code(active_scene)
        except Exception:
            logger.exception("get_scene_by_code failed scene=%s", active_scene)

        heating = _outputs_heating(scene_meta)

        items: list[dict[str, Any]] = []
        for pid in ids:
            row = rows.get(pid) or rows.get(pid.lower())
            if not row:
                continue
            scene_label = None
            mapped = False
            if row.get("category_id") is not None:
                try:
                    scene_label = self._metadata.map_category_to_scene_label(
                        active_scene, int(row["category_id"])
                    )
                    mapped = True
                except Exception:
                    logger.exception("scene label map failed id=%s", pid)
            hit = _hit_from_row(
                row,
                score_by_id.get(pid),
                scene_meta=scene_meta,
                scene_label=scene_label if mapped else None,
            )
            # When mapping ran and returned None → emit scene_label: null (optional but typed).
            if mapped and "scene_label" not in hit:
                hit["scene_label"] = None
            items.append(hit)

        if items:
            return {
                "source": "self",
                "scene": active_scene,
                "outputs_heating": heating,
                "items": items,
            }

        logger.info(
            "self-built empty after join; source=empty scene=%s (FlyB skipped A4)",
            active_scene,
        )
        return {
            "source": "empty",
            "scene": active_scene,
            "outputs_heating": heating,
            "items": [],
            "message": f"no local matches for scene={active_scene}",
        }
