"""图片搜索用例（PG v0.1 §4.3 / A4 / A5 / A6 + §6.1 Wire）。

只查自建库：确定场景（scenes 表）→ 计算 embedding → 按场景 code 过滤的 Qdrant top-k →
从 PG 补全信息 → 把类别映射到场景的标签（所有结果一次查询）。
自建库为空 → source=empty。FlyB 不在范围内（从不调用）。
score_threshold：不传→0.55；0 关闭；其余 {0}∪[0.30,1]。

§6.1（2026-11-12）：items 结构；每条结果是和 FlyB 结果共用的结果卡
（tags / description / original_url / similarity），其余字段都在 details 里；
scene_label 为 string|null；outputs_heating 在外层结构上。

带多张查询图的请求（POST /api/search/batch/*）先检查它们共享的内容（check：top_k、
场景、阈值），这样有一个不合法时，在读任何图片之前请求就失败了；然后对每张图调用
execute，从线程池里同时调用多个：用例在两次调用之间不保存状态。
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.core import errors as err
from app.services.retrieval.active_model import ActiveModel
from app.services.retrieval.embedding_validate import validate_embedding
from app.core.rules.food_fields import FOOD_FIELDS
from app.core.rules.text_fields import tags_text
from app.repositories.ports import (
    EmbeddingPort,
    MetadataStorePort,
    VectorStorePort,
)
from app.services.retrieval.scenes import lookup_scene, requested_scene_code
from app.services.retrieval.score_threshold import (
    qdrant_score_threshold,
    resolve_score_threshold,
)
from app.utils.timer import timer_tool

logger = logging.getLogger("embedsearch")


def _as_id_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return str(value)
    text = str(value).strip()
    return text or None


def _scene_label_name(scene_label: Any) -> str | None:
    """P8：wire 的 scene_label 是 string | null（把仓储返回的 dict 压缩成名称）。"""
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
    """一条搜索结果（§6.1.1）：和 FlyB 结果共用的卡片字段，其余放在 details 里。"""
    original = row.get("original_url") or row.get("url")
    details: dict[str, Any] = {
        "id": _as_id_str(row.get("id")),
        "category_id": row.get("category_id"),
        "category": row.get("category_name") or row.get("category"),
        "scenes": row.get("scenes"),
        "label_status": row.get("label_status"),
        # 和 FlyB 结果 details.images[] 的键相同
        "images": [{
            "image_url": original,
            "thumbnail_url": row.get("thumb_url"),
            "width": row.get("width"),
            "height": row.get("height"),
        }],
    }
    # 可选：尝试过映射时带上 scene_label（string|null）。
    if scene_label is not None:
        details["scene_label"] = _scene_label_name(scene_label)
    if _outputs_heating(scene_meta):
        details.update({key: row.get(key) for key in FOOD_FIELDS})
        details["heating_group"] = row.get("heating_group")
    return {
        "tags": tags_text(row.get("tags")),
        "description": row.get("description") or "",
        "original_url": original,
        "similarity": score,
        "details": details,
    }


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
        trace_id: str | None = None,
        timer: timer_tool | None = None,
    ) -> dict[str, Any]:
        """按查询图的字节搜索（img_url 输入由 HTTP 层下载）。

        timer：调用接口的计时器。embedding推理 / vector搜索 / 后处理 这几步记在上面；
        由接口写出请求的耗时行（它带着 trace_id 写日志，这里不写）。
        """
        timer = timer or timer_tool()
        if not image_bytes:
            raise err.validation_error("file or img_url is required")
        scene_meta, effective_t = self._resolve(scene, top_k, score_threshold)
        active_scene = str(scene_meta["code"])
        native_thr = qdrant_score_threshold(effective_t)

        with timer.step("embedding推理"):
            try:
                vector = self._embedding.embed_bytes(image_bytes)
            except err.AppError:
                raise
            except Exception as exc:
                raise err.search_bad_image(f"embed failed: {exc}") from exc
            vector = validate_embedding(vector, for_search=True, expected_dim=self._model.dim)

        with timer.step("vector搜索"):
            raw_hits = self._vectors.search(
                vector,
                top_k=int(top_k),
                score_threshold=native_thr,
                scene=active_scene,
            )

        with timer.step("后处理"):
            if effective_t > 0.0:
                raw_hits = [h for h in raw_hits if float(h.get("score") or 0) >= effective_t]

            ids: list[str] = []
            score_by_id: dict[str, float] = {}
            for h in raw_hits:
                pid = _as_id_str(h.get("id"))
                if not pid:
                    continue
                ids.append(pid)
                score_by_id[pid] = float(h.get("score") or 0.0)

            rows = (self._metadata.get_active_by_ids(ids, scene=active_scene) if ids else {})

            heating = _outputs_heating(scene_meta)
            labels = self._scene_labels(scene_meta, rows.values())

            items: list[dict[str, Any]] = []
            for pid in ids:
                row = rows.get(pid) or rows.get(pid.lower())
                if not row:
                    continue
                category_id = row.get("category_id")
                mapped = labels is not None and category_id is not None
                hit = _hit_from_row(
                    row,
                    score_by_id.get(pid),
                    scene_meta=scene_meta,
                    scene_label=labels.get(int(category_id)) if mapped else None,
                )
                if mapped:
                    hit["details"].setdefault("scene_label", None)
                items.append(hit)

            if items:
                body = {"source": "self", "scene": active_scene, "outputs_heating": heating, "items": items}
            else:
                logger.info("self-built empty after join; source=empty scene=%s (FlyB skipped A4)",active_scene,)
                body = {
                    "source": "empty",
                    "scene": active_scene,
                    "outputs_heating": heating,
                    "items": [],
                    "message": f"no local matches for scene={active_scene}",
                }
        return body

    def check(
        self,
        *,
        scene: str | None = None,
        top_k: int = 10,
        score_threshold: float | None = None,
    ) -> dict[str, Any]:
        """一个请求的各张查询图共享的内容，不碰任何图片就能检查：
        {scene: 要搜的 code, outputs_heating, score_threshold: 生效的阈值，0 = 关闭}。

        抛出 execute 对它们会抛出的错误：VALIDATION_ERROR（top_k）、SEARCH_UNKNOWN_SCENE、
        SEARCH_POSTGRES_FAILED、SEARCH_BAD_SCORE_THRESHOLD。
        """
        scene_meta, effective_t = self._resolve(scene, top_k, score_threshold)
        return {
            "scene": str(scene_meta["code"]),
            "outputs_heating": _outputs_heating(scene_meta),
            "score_threshold": effective_t,
        }

    def _resolve(
        self, scene: str | None, top_k: int, score_threshold: float | None
    ) -> tuple[dict[str, Any], float]:
        """（启用的场景行, 生效的阈值）；先检查 top_k。"""
        if not (1 <= int(top_k) <= 50):
            raise err.validation_error("top_k must be in 1..50")

        # 先查场景：不存在或停用的场景在任何 embedding 计算之前就被拒绝。
        scene_meta = lookup_scene(
            self._metadata,
            requested_scene_code(scene),
            unknown=err.search_unknown_scene,
            failed=err.search_postgres_failed,
        )
        return scene_meta, resolve_score_threshold(score_threshold)

    def _scene_labels(
        self, scene_meta: dict[str, Any], rows: Any
    ) -> dict[int, dict[str, Any]] | None:
        """结果里各类别的 {category_id: 场景标签}，一次查询。

        查询失败时为 None：这时结果完全不带 scene_label（写日志），这和客户端在没有标签的
        场景里看到的一样，而不是让搜索失败。
        """
        category_ids = sorted({int(row["category_id"]) for row in rows if row.get("category_id") is not None})
        if not category_ids:
            return {}
        try:
            return self._metadata.map_categories_to_scene_labels(int(scene_meta["id"]), category_ids)
        except Exception:
            logger.exception("scene label mapping failed scene=%s", scene_meta.get("code"))
            return None
