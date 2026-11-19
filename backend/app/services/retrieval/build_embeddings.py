"""用存下来的原图补齐一个模型版本的 collection（POST /api/embedding-models/{id}/build）。

一批（run_batch）：

    按 id 顺序取下一批在该版本下没有台账行（在它的 collection 里没有向量）的图片
    -> 从对象存储读取每张原图 -> 该版本的编码器 -> 用 PostgreSQL 现在给出的 payload
    upsert 向量点 -> 每个向量点一条台账行（record_embeddings）

单张图的失败（原图读不了或解码不了、向量无效）只算这张图失败：这一批继续，调用方在
本次运行的剩余部分跳过这个 id。所有图都会失败的问题（Qdrant、PostgreSQL）会抛出，
调用方随之停止。计算 embedding 期间被删除的图片，它的向量点会再删掉，所以 collection
里不会留下孤儿点。

push_payloads 让该版本的 payload 跟上：向量写入之后又修改过的图片（编辑只推送到当前
collection）的台账行落后于它的 payload_version。

用例本身不保存进度：台账就是进度，所以停下来的构建会从停下的地方继续，同时跑的
第二个构建在同一轮里也不会把一张图写两次。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.core import errors as err
from app.core.rules.scene_membership import build_qdrant_payload
from app.repositories.ports import EmbeddingModelStorePort, ImageStoragePort, MetadataStorePort, VectorStorePort
from app.services.retrieval.active_model import ActiveModel
from app.services.retrieval.embedding_validate import validate_embedding
from app.services.retrieval.sync_scenes import SyncSceneMembershipsUseCase

logger = logging.getLogger("retrieval.build")

BUILD_BATCH = 16
VERIFY_BATCH = 256
# 属于单张图的错误码：构建记录下来并继续
IMAGE_FAILURES = frozenset({"IMAGE_FILE_UNAVAILABLE", "INDEX_BAD_IMAGE", "INDEX_BAD_EMBEDDING"})


@dataclass
class LedgerCheck:
    """checked：查看过的台账行数；forgotten：因为向量点不在 collection 里而删除的行数；
    last_id：下一段从哪里开始（None：已经到末尾）。
    """

    checked: int = 0
    forgotten: int = 0
    last_id: str | None = None
    done: bool = False


def verify_ledger(
    model_id: int,
    vectors: Any,
    models: EmbeddingModelStorePort,
    *,
    after: str | None = None,
    limit: int | None = None,
    batch: int = VERIFY_BATCH,
    should_stop: Any = None,
) -> LedgerCheck:
    """台账记录了哪些图片在某个版本的 collection 里有向量；检查它们的向量点是不是真的在，
    忘掉向量点已经不在的行（collection 丢了又重建、向量点被手工删除），这样这些图片
    重新算作没有向量，由构建或对账任务重新计算 embedding。limit：从 `after` 开始最多
    这么多行（一段）；None：全部。

    向量点先于它的台账行写入，所以不会出现台账行比向量点先被检查的情况。
    """
    result = LedgerCheck(last_id=after)
    while not (should_stop and should_stop()):
        size = batch if limit is None else min(batch, limit - result.checked)
        if size <= 0:
            break
        ids = models.list_embedded_ids(model_id, after=result.last_id, limit=size)
        present = vectors.existing_ids(ids) if ids else set()
        gone = [image_id for image_id in ids if image_id not in present]
        if gone:
            result.forgotten += models.forget_embeddings(model_id, gone)
            logger.warning("version %s: %d image(s) in the ledger have no point in the collection; listed for embedding again", model_id, len(gone))
        result.checked += len(ids)
        if len(ids) < size:
            result.last_id, result.done = None, True
            break
        result.last_id = ids[-1]
    return result


@dataclass
class BuildBatch:
    """embedded：现在已经在 collection 里的 id；failures：[{image_id, code, detail}]；gone：
    这期间被删除的；last_id：下一批从哪里开始（None：已经没有剩下的）；done：last_id 之后
    没有缺向量的图片了。
    """

    embedded: list[str] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)
    gone: list[str] = field(default_factory=list)
    last_id: str | None = None
    done: bool = True


class BuildEmbeddingsUseCase:
    def __init__(
        self,
        *,
        model: ActiveModel,
        encoder: Any,
        vectors: VectorStorePort,
        metadata: MetadataStorePort,
        models: EmbeddingModelStorePort,
        storage: ImageStoragePort,
    ) -> None:
        self.model = model
        self._encoder = encoder
        self._vectors = vectors
        self._metadata = metadata
        self._models = models
        self._storage = storage

    def _embed(self, row: dict[str, Any]) -> list[float]:
        url = str(row.get("original_url") or "")
        if not url:
            raise err.image_file_unavailable(f"image {row['id']} has no original_url")
        data = self._storage.read_by_url_or_key(url)
        try:
            vector = self._encoder.embed_bytes(data)
        except err.AppError:
            raise
        except Exception as exc:
            raise err.index_bad_image(f"embed failed: {exc}") from exc
        return validate_embedding(vector, for_search=False, expected_dim=self.model.dim)

    def run_batch(self, *, after: str | None = None, limit: int = BUILD_BATCH, exclude: list[str] | None = None) -> BuildBatch:
        rows = self._models.list_embedding_backlog(self.model.id, after=after, limit=limit, exclude=exclude)
        if not rows:
            return BuildBatch(done=True)
        result = BuildBatch(last_id=str(rows[-1]["id"]), done=len(rows) < limit)
        payload_rows = {row["id"]: row for row in self._metadata.get_payload_rows([str(r["id"]) for r in rows])}
        versions: dict[str, int] = {}
        for row in rows:
            image_id = str(row["id"])
            current = payload_rows.get(image_id)
            if current is None:  # 读取待处理列表之后被删除了
                result.gone.append(image_id)
                continue
            try:
                vector = self._embed(row)
            except err.AppError as exc:
                if exc.code not in IMAGE_FAILURES:
                    raise
                result.failures.append({"image_id": image_id, "code": exc.code, "detail": err.client_detail(exc)})
                logger.warning("build %s: image %s skipped | %s: %s", self.model.space_id, image_id, exc.code, exc.detail)
                continue
            payload = build_qdrant_payload(
                category_id=current.get("category_id"),
                scene_codes=current.get("scenes") or [],
                food_state=current.get("food_state"),
                label_status=current.get("label_status"),
            )
            self._vectors.upsert(image_id, vector, payload)  # INDEX_QDRANT_FAILED 会让构建停止
            versions[image_id] = int(current["payload_version"])
        recorded = set(self._models.record_embeddings(model_id=self.model.id, versions=versions))
        orphans = sorted(set(versions) - recorded)
        for image_id in orphans:  # 向量点写入之后被删除了：把向量点再删掉
            self._vectors.delete(image_id)
        result.gone.extend(orphans)
        result.embedded = sorted(recorded)
        return result

    def push_payloads(self, *, limit: int = 200, should_stop: Any = None) -> int:
        """推送向量写入之后变化过的 payload；返回推送了多少。"""
        sync = SyncSceneMembershipsUseCase(self._metadata, self._vectors, model=self.model)
        pushed, after = 0, None
        while not (should_stop and should_stop()):
            ids = self._metadata.list_unsynced_payload_ids(after=after, limit=limit, model_id=self.model.id)
            if not ids:
                break
            pushed += len(sync.push_images(ids).pushed)
            after = ids[-1]
            if len(ids) < limit:
                break
        return pushed
