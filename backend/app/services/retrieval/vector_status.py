"""一张已存图片在当前模型 collection 里的情况（GET /api/images/{image_id}）。

读取图片的向量点（只读 payload，不读向量），和 PostgreSQL 现在给出的 payload 比较——
即 scene_membership.build_qdrant_payload，入库、同步和推送写入的就是它——并报告当前模型的
台账行（image_embeddings）：

    status    含义
    synced    向量点存在且 payload 和 PostgreSQL 一致：搜索过滤看到的图片和存储的一样
    stale     向量点存在但 payload 落后：需要推送（POST /api/images/{image_id}/vector-sync，
              或者用 POST /api/scenes/sync 推送全部）
    missing   当前 collection 里没有向量点：搜索找不到这张图（需要重新入库）

检索服务没有构建或读取失败时，HTTP 层（app.api.services.images）返回 status=unavailable。
场景按集合比较：搜索只问"是否包含"。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.repositories.ports import VectorStorePort
from app.services.retrieval.active_model import ActiveModel
from app.core.rules.scene_membership import build_qdrant_payload


def expected_payload(image: Mapping[str, Any]) -> dict[str, Any]:
    """图片的向量点应该带的 payload，由它存储的字段和生效的场景得出。"""
    return build_qdrant_payload(
        category_id=image.get("category_id"),
        scene_codes=list(image.get("scenes") or []),
        food_state=image.get("food_state"),
        label_status=image.get("label_status"),
    )


def payload_matches(actual: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    """expected 的每个字段在 actual 里都有相同的值时为 True（场景按集合比较）；只有 actual
    有的键不算（没有过滤会读它们）。
    """
    for key, want in expected.items():
        have = actual.get(key)
        if key == "scenes":
            if sorted(str(code) for code in have or []) != sorted(str(code) for code in want or []):
                return False
        elif have != want:
            return False
    return True


class ImageVectorStatusUseCase:
    def __init__(self, vectors: VectorStorePort, *, model: ActiveModel) -> None:
        self._vectors = vectors
        self._model = model

    def statuses(self, expected: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
        """一次读取 collection 得到多张图的 {图片 id: synced / stale / missing}（图库的一页）。
        expected：{图片 id: expected_payload(image)}。

        向量库读不了时抛出 AppError（SEARCH_QDRANT_FAILED）。
        """
        if not expected:
            return {}
        payloads = self._vectors.get_payloads(list(expected))
        out: dict[str, str] = {}
        for image_id, want in expected.items():
            payload = payloads.get(image_id)
            out[image_id] = "missing" if payload is None else "synced" if payload_matches(payload, want) else "stale"
        return out

    def inspect(self, image: Mapping[str, Any]) -> dict[str, Any]:
        """图片详情里的向量部分；image 是 ManageImagesUseCase.get 返回的内容。

        向量库读不了时抛出 AppError（SEARCH_QDRANT_FAILED）。
        """
        image_id = str(image["id"])
        expected = expected_payload(image)
        payload = self._vectors.get_payloads([image_id]).get(image_id)
        ledger = next(
            (row for row in image.get("embeddings") or [] if row.get("model_id") == self._model.id), None
        )
        exists = payload is not None
        matches = payload_matches(payload, expected) if exists else None
        return {
            "status": "missing" if not exists else "synced" if matches else "stale",
            "collection": self._model.collection,
            "embedding_space_id": self._model.space_id,
            "exists": exists,
            "payload": payload,
            "expected_payload": expected,
            "payload_matches": matches,
            "payload_version": image.get("payload_version"),
            "synced_version": ledger.get("synced_version") if ledger else None,
            "embedded_at": ledger.get("embedded_at") if ledger else None,
        }
