"""让已存图片和当前的场景配置保持一致（契约 §2.9 / §4.4）。

场景规则、场景开关（is_active、include_all）和类别树决定一张图属于哪些场景。归属在图片
写入时物化，所以其中任何一个变化后，已存图片保留旧的场景，直到被重算。这里的一批：

1. 按 id 顺序为接下来的图片重算 image_scenes（PG；生效的场景集合变化的图片递增
   payload_version）；
2. 把这一批每张图的 payload 写到当前模型的 collection，不管它的版本有没有变：切换一个
   场景会改变生效的集合，却不会递增任何图片的版本，而全量写入也能修复因其他原因
   漂移的 payload；
3. 把推送的版本记录到 image_embeddings。

调用方用 next_after 重复调用直到完成：POST /api/scenes/sync（管理页显示进度）或
`python -m app.workers.sync_scenes`。中途失败的一批可以安全地重跑：PG 的修改按批提交，
没写进去的 payload 由下一次运行写入。

push_images 对少数刚提交了归属的图片做第 2-3 步（手工覆盖，
app.api.services.scene_memberships），这样不用完整跑一遍，搜索就能看到修改。

两者都只在版本仍是图片当前版本时才记录（MetadataStorePort.mark_payloads_pushed）。
在读取 payload 和记录之间又被写入的图片（手工修改、入库、另一次同步）会重新读取并推送：
这样最后推送的写入方写的是最新的 payload，台账永远不会声称 collection 里有它实际
没有的 payload。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.core import errors as err
from app.core.scene_rules import SCENE_SYNC_BATCH_DEFAULT, SCENE_SYNC_BATCH_MAX
from app.repositories.ports import MetadataStorePort, VectorStorePort
from app.services.retrieval.active_model import ActiveModel
from app.core.rules.scene_membership import build_qdrant_payload

logger = logging.getLogger("retrieval.sync_scenes")

# 对中间被别人修改的图片，"读取 -> 写 payload -> 记录"的轮数
PUSH_ROUNDS = 3


def _after_id(value: Any) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return str(value if isinstance(value, UUID) else UUID(str(value).strip()))
    except (ValueError, AttributeError) as exc:
        raise err.validation_error(f"after must be an image id (uuid), got {value!r}") from exc


def _limit(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= SCENE_SYNC_BATCH_MAX:
        raise err.validation_error(f"limit must be an integer in 1..{SCENE_SYNC_BATCH_MAX}, got {value!r}")
    return value


@dataclass
class PushResult:
    """pushed：写入并记录的 {image_id: payload_version}；missing：当前 collection 里没有向量点
    （需要重新计算 embedding）；unconfirmed：PUSH_ROUNDS 轮之后仍在被别人修改（由修改它的
    写入方推送，否则由下一次同步修复）。
    """

    pushed: dict[str, int] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    unconfirmed: list[str] = field(default_factory=list)


class SyncSceneMembershipsUseCase:
    def __init__(self, metadata: MetadataStorePort, vectors: VectorStorePort, *, model: ActiveModel) -> None:
        self._metadata = metadata
        self._vectors = vectors
        self._model = model

    def _push(self, image_ids: list[str]) -> PushResult:
        """把这些图片的 payload（按 PG 现在的内容）写到当前 collection 并记录。
        这期间被删除的图片不出现在任何列表里。
        """
        result = PushResult()
        pending = list(image_ids)
        for _ in range(PUSH_ROUNDS):
            if not pending:
                break
            payloads: dict[str, dict[str, Any]] = {}
            versions: dict[str, int] = {}
            for row in self._metadata.get_payload_rows(pending):
                payloads[row["id"]] = build_qdrant_payload(
                    category_id=row.get("category_id"),
                    scene_codes=row.get("scenes") or [],
                    food_state=row.get("food_state"),
                    label_status=row.get("label_status"),
                )
                versions[row["id"]] = int(row["payload_version"])
            absent = set(self._vectors.set_payloads(payloads)) if payloads else set()
            result.missing.extend(sorted(absent))
            written = {image_id: version for image_id, version in versions.items() if image_id not in absent}
            recorded = set(self._metadata.mark_payloads_pushed(model_id=self._model.id, versions=written))
            result.pushed.update({image_id: written[image_id] for image_id in recorded})
            pending = sorted(set(written) - recorded)
        result.unconfirmed = pending
        if pending:
            logger.warning(
                "payload push not confirmed after %d rounds (images changed meanwhile): %s collection=%s",
                PUSH_ROUNDS, pending, self._model.collection,
            )
        return result

    def push_images(self, image_ids: list[str]) -> PushResult:
        """推送少数刚提交了归属的图片的当前 payload。"""
        result = self._push([str(image_id) for image_id in image_ids])
        logger.info(
            "payload push images=%d pushed=%d missing=%d unconfirmed=%d collection=%s",
            len(image_ids), len(result.pushed), len(result.missing), len(result.unconfirmed),
            self._model.collection,
        )
        return result

    def run_batch(self, *, after: Any = None, limit: Any = SCENE_SYNC_BATCH_DEFAULT) -> dict[str, Any]:
        """图片 id `after` 之后的一批（None：从头开始）。

        返回 {processed, membership_changed, payload_pushed, payload_missing, next_after, done}；
        payload_missing 列出当前 collection 里没有向量点的图片（它们需要重新计算 embedding，
        这里不做）。
        """
        start = _after_id(after)
        size = _limit(limit)
        batch = self._metadata.recompute_scene_memberships(after=start, limit=size)
        image_ids = batch["image_ids"]
        pushed = self._push(image_ids)
        done = len(image_ids) < size
        result = {
            "processed": len(image_ids),
            "membership_changed": len(batch["changed_ids"]),
            "payload_pushed": len(pushed.pushed),
            "payload_missing": pushed.missing,
            "next_after": None if done else batch["last_id"],
            "done": done,
        }
        logger.info(
            "scene sync batch after=%s processed=%d changed=%d pushed=%d missing=%d collection=%s",
            start, result["processed"], result["membership_changed"], result["payload_pushed"],
            len(pushed.missing), self._model.collection,
        )
        return result

    def run_all(self, *, limit: int = SCENE_SYNC_BATCH_DEFAULT) -> dict[str, Any]:
        """一批接一批直到完成（后台任务用）；返回各批的合计。"""
        totals: dict[str, Any] = {"processed": 0, "membership_changed": 0, "payload_pushed": 0, "payload_missing": [], "batches": 0}
        after: str | None = None
        while True:
            result = self.run_batch(after=after, limit=limit)
            totals["batches"] += 1
            for key in ("processed", "membership_changed", "payload_pushed"):
                totals[key] += result[key]
            totals["payload_missing"].extend(result["payload_missing"])
            if result["done"]:
                return totals
            after = result["next_after"]
