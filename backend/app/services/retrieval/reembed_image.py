"""用存下来的原图重新写入一张已存图片的向量点（POST /api/images/{image_id}/reembed）。

修复 vector.status=missing——向量点被删了、collection 重建了，或者图片从来没有进入
当前模型的 collection——不需要用户重新上传文件：

    存下来的原图（对象存储）-> 当前编码器 -> 锁住图片行 -> 用 PostgreSQL 现在给出的
    payload（和状态检查期望的相同）upsert 向量点 -> 当前模型的台账行 -> 解锁

PostgreSQL 里除了台账什么都不变，所以图片保留它的标注。upsert 之前失败时一切保持原样。

慢的部分（读取原图、计算 embedding）不加锁执行；向量点和它的台账行在持有图片行时写入
（lock_image_for_vector），和入库时一样。先提交的删除会让这里没东西可写：IMAGE_NOT_FOUND，
不写向量点。后来的删除会等待，然后它的清理会删除向量点。如果不加锁，一次删除可能在计算
embedding 和 upsert 之间提交并清理完，向量点就会为一张已经不存在的图片回来，而且再也没有
队列行或台账行能删掉它。
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.core import errors as err
from app.repositories.ports import ImageStoragePort, MetadataStorePort, VectorStorePort
from app.services.retrieval.active_model import ActiveModel
from app.services.retrieval.embedding_validate import validate_embedding
from app.services.retrieval.vector_status import expected_payload

logger = logging.getLogger("retrieval.reembed")


class ReembedImageUseCase:
    def __init__(
        self,
        embedding: Any,
        vectors: VectorStorePort,
        metadata: MetadataStorePort,
        *,
        model: ActiveModel,
        storage: ImageStoragePort | None,
    ) -> None:
        self._embedding = embedding
        self._vectors = vectors
        self._metadata = metadata
        self._model = model
        self._storage = storage

    def execute(self, image_id: UUID | str) -> dict[str, Any]:
        """向量点写入后返回 {image_id, payload_version}。

        抛出 IMAGE_NOT_FOUND（计算 embedding 期间图片被删除时也是）、IMAGE_FILE_UNAVAILABLE
        （原图读不了）、INDEX_BAD_IMAGE / INDEX_BAD_EMBEDDING（无法计算 embedding）、
        INDEX_QDRANT_FAILED、INDEX_POSTGRES_FAILED。
        """
        image = self._metadata.get_by_id(image_id)
        if image is None:
            raise err.image_not_found(f"image {image_id} does not exist")
        if self._storage is None:
            raise err.image_file_unavailable("no object storage is configured")
        original_url = str(image.get("original_url") or "")
        if not original_url:
            raise err.image_file_unavailable(f"image {image_id} has no original_url")
        data = self._storage.read_by_url_or_key(original_url)

        try:
            vector = self._embedding.embed_bytes(data)
        except err.AppError:
            raise
        except Exception as exc:
            raise err.index_bad_image(f"embed failed: {exc}") from exc
        vector = validate_embedding(vector, for_search=False, expected_dim=self._model.dim)

        # 从这里一直持有到台账行提交：见模块文档字符串
        with self._metadata.lock_image_for_vector(image_id, model_id=self._model.id) as current:
            if current is None:
                raise err.image_not_found(f"image {image_id} was deleted while it was being embedded")
            version = current.get("payload_version")
            try:
                self._vectors.upsert(image_id, vector, expected_payload(current))
            except err.AppError:
                raise
            except Exception as exc:
                raise err.index_qdrant_failed(f"{type(exc).__name__}: {exc}") from exc
        logger.info("re-embedded image=%s model=%s payload_version=%s", image_id, self._model.space_id, version)
        return {"image_id": str(image_id), "payload_version": version}
