"""图片入库用例（PG v0.1）。

流程：字节 → sha256/uuid → 校验标注 → 计算 embedding（此时还没有副作用）→
S3 原图 + 缩略图 → 一个 PostgreSQL 写事务：插入 images、重算 image_scenes、读取一致的
payload 快照、暂存它的 embedding 台账、upsert 到 Qdrant，然后提交。写入失败会回滚，
不会删掉另一个请求的图片。重复上传保留已存的行；当这一行的文件在其他 key 下时
（在分片目录或 key_prefix 变更之前存的），刚写入的副本会被删除。

按路径的本地磁盘存储是旧的 MVP 做法；有存储时优先使用 put_original_and_thumb 和
数据库里的 https URL（PG 里没有 path 列）。

任务中心：execute_batch / execute_file 处理完每个文件后立刻写一行 index_tasks
（成功时带图片 id，失败时带错误码和可以给客户端看的 detail）。尽力而为：
写不了的记录只写日志，入库结果照常有效。execute_one 不记录任何东西（它就是入库本身）。
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Any, Sequence
from uuid import UUID

from app.core.enums import (
    DEFAULT_LICENSE,
    DEFAULT_SOURCE_TYPE,
    LICENSE,
    SOURCE_TYPE,
    IndexTaskStatus,
    LabelStatus,
)
from app.services.retrieval.active_model import ActiveModel
from app.core.rules.enum_fields import coerce_enum
from app.core.rules.food_fields import FOOD_FIELDS, coerce_food_field
from app.core.rules.image_ids import uuid_from_sha256
from app.core import errors as err
from app.services.retrieval.embedding_validate import validate_embedding
from app.repositories.ports import (
    EmbeddingPort,
    MetadataStorePort,
    ImageStoragePort,
    VectorStorePort,
)
from app.core.rules.scene_membership import build_qdrant_payload
from app.services.retrieval.scenes import resolve_index_scenes
from app.utils.storage_keys import guess_ext_from_bytes_header, original_key, public_url, thumb_key
from app.services.retrieval.storage_upload import drop_unused_upload, put_original_and_thumb
from app.core.rules.text_fields import normalize_description, normalize_tags

logger = logging.getLogger("retrieval.index")


def _decode_size(image_bytes: bytes) -> tuple[int | None, int | None]:
    try:
        from PIL import Image
        import io

        with Image.open(io.BytesIO(image_bytes)) as img:
            img.verify()
        with Image.open(io.BytesIO(image_bytes)) as img:
            return int(img.width), int(img.height)
    except Exception:
        try:
            import cv2
            import numpy as np

            arr = np.frombuffer(image_bytes, dtype=np.uint8)
            decoded = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if decoded is None:
                return None, None
            h, w = decoded.shape[:2]
            return int(w), int(h)
        except Exception:
            return None, None


def _read_image_file(path: str) -> bytes:
    p = Path(path)
    try:
        return p.read_bytes()
    except OSError:
        import numpy as np

        return np.fromfile(path, dtype=np.uint8).tobytes()


class IndexImageUseCase:
    def __init__(
        self,
        embedding: EmbeddingPort,
        vectors: VectorStorePort,
        metadata: MetadataStorePort,
        *,
        model: ActiveModel,
        storage: ImageStoragePort | None = None,
        indexed_dir: str = "save_images/indexed",
    ) -> None:
        self._embedding = embedding
        self._vectors = vectors
        self._metadata = metadata
        self._model = model
        self._storage = storage
        self._indexed_dir = indexed_dir

    def execute_one(
        self,
        *,
        image_bytes: bytes | None = None,
        source_path: str | None = None,
        filename: str | None = None,
        category: str | None = None,
        category_id: int | None = None,
        tags: list[Any] | None = None,
        scene: str | None = None,
        scenes: Sequence[Any] | None = None,
        food_state: str | None = None,
        food_processing: str | None = None,
        food_count: int | str | None = None,
        total_mass_g: int | str | None = None,
        water_mass_g: int | str | None = None,
        main_ingredient_mass_g: int | str | None = None,
        label_status: str | None = None,
        source_type: str | None = None,
        license: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        # 用 scenes 表检查客户端传来的可选场景提示（归属仍然按规则重算）
        resolve_index_scenes(self._metadata, scene=scene, scenes=scenes)

        if image_bytes is None and not source_path:
            raise err.index_empty_request()

        if image_bytes is None:
            p = Path(source_path)
            if not p.is_file():
                raise err.index_bad_image(f"path not found: {source_path}")
            try:
                image_bytes = _read_image_file(str(p))
            except OSError as exc:
                raise err.index_bad_image(f"cannot read path: {exc}") from exc
            title = p.name
        else:
            title = filename or "upload.bin"

        width, height = _decode_size(image_bytes)
        if width is None or height is None:
            raise err.index_bad_image("cannot decode image")

        sha = hashlib.sha256(image_bytes).digest()
        image_id: UUID = uuid_from_sha256(sha)
        ext = guess_ext_from_bytes_header(image_bytes)

        src = coerce_enum(SOURCE_TYPE, source_type, field="source_type", default=DEFAULT_SOURCE_TYPE)
        lic = coerce_enum(LICENSE, license, field="license", default=DEFAULT_LICENSE)
        # 可选的食物标注，存在 image_foods 里（app.core.rules.food_fields）
        food_fields = {
            key: coerce_food_field(key, value)
            for key, value in (
                ("food_state", food_state),
                ("food_processing", food_processing),
                ("food_count", food_count),
                ("total_mass_g", total_mass_g),
                ("water_mass_g", water_mass_g),
                ("main_ingredient_mass_g", main_ingredient_mass_g),
            )
        }
        desc = normalize_description(description)
        tag_list = normalize_tags(tags)  # 只存 PG，和 description 一样：不在 Qdrant payload 里

        # 确定 category_id：优先用显式传入的；可选地在元数据里按 code 查找
        resolved_category_id = category_id
        if resolved_category_id is None and category:
            resolved_category_id = self._metadata.get_category_id_by_code(str(category).strip())
            if resolved_category_id is None:
                logger.warning("index: unknown category code %r; storing unlabeled", category)

        # images 的不变式：label_status = unlabeled  <=>  category_id IS NULL
        if label_status is None or not str(label_status).strip():
            label = LabelStatus.AUTO if resolved_category_id is not None else LabelStatus.UNLABELED
        else:
            label = coerce_enum(LabelStatus, label_status, field="label_status")
            if label is LabelStatus.UNLABELED and resolved_category_id is not None:
                raise err.validation_error(
                    "label_status=unlabeled requires category_id IS NULL"
                )
            if label is not LabelStatus.UNLABELED and resolved_category_id is None:
                if not category:
                    raise err.validation_error(
                        f"label_status={label.value} requires a category"
                    )
                # 给了类别 code 但不认识——保留图片，去掉标注
                label = LabelStatus.UNLABELED

        # 在任何写入之前计算 embedding：这里失败不会留下 S3 对象或数据库行。
        try:
            vector = self._embedding.embed_bytes(image_bytes)
        except err.AppError:
            raise
        except Exception as exc:
            raise err.index_bad_image(f"embed failed: {exc}") from exc
        vector = validate_embedding(vector, for_search=False, expected_dim=self._model.dim)

        original_url: str
        thumb_url: str
        if self._storage is not None:
            urls = put_original_and_thumb(
                self._storage, image_id, image_bytes, ext=ext
            )
            original_url = urls["original_url"]
            thumb_url = urls["thumb_url"]
        else:
            # TODO(M5+)：生产环境要求有 ImageStoragePort；dry-run 时用 https 占位
            base = "https://cdn.example.local/image-embedding"
            original_url = public_url(base, original_key(image_id, ext))
            thumb_url = public_url(base, thumb_key(image_id))
            logger.warning(
                "index without ImageStoragePort; stub URLs id=%s (wire storage in stack)",
                image_id,
            )
            # 尽力而为的本地镜像，用于调试
            try:
                os.makedirs(self._indexed_dir, exist_ok=True)
                Path(self._indexed_dir, f"{image_id}.{ext}").write_bytes(image_bytes)
            except OSError:
                pass

        row = {
            "id": image_id,
            "sha256": sha,
            "original_url": original_url,
            "thumb_url": thumb_url,
            "width": width,
            "height": height,
            "source_type": src,
            "license": lic,
            "category_id": resolved_category_id,
            "label_status": label,
            "description": desc,
            "tags": tag_list,
            **food_fields,
        }
        # 行锁一直持有到向量写入和台账提交之后。
        # 并发上传按 INSERT 的结果判断，从不另外做存在性检查。
        with self._metadata.index_image(row, model_id=self._model.id) as indexed:
            stored = indexed.image
            image_id = UUID(str(stored["id"]))
            scene_codes = list(stored["scenes"])
            payload_version = int(stored["payload_version"])
            payload = build_qdrant_payload(
                category_id=stored.get("category_id"),
                scene_codes=scene_codes,
                food_state=stored.get("food_state"),
                label_status=stored["label_status"],
            )
            try:
                self._vectors.upsert(image_id, vector, payload)
            except err.AppError:
                raise
            except Exception as exc:
                raise err.index_qdrant_failed(f"{type(exc).__name__}: {exc}") from exc

        if not indexed.created and self._storage is not None:
            # 已提交：已存的行胜出，从现在起它的 URL 就是这张图的文件。
            drop_unused_upload(self._storage, image_id, written=row, stored=stored)

        return {
            "id": str(image_id),
            "filename": title,
            "original_url": stored["original_url"],
            "thumb_url": stored["thumb_url"],
            "category_id": stored.get("category_id"),
            "scenes": scene_codes,
            # 已存的行胜出：重复上传时保留第一次上传的值
            **{key: stored.get(key) for key in FOOD_FIELDS},
            "label_status": str(stored["label_status"]),
            "width": stored["width"],
            "height": stored["height"],
            "description": stored.get("description", ""),
            "tags": stored.get("tags", []),
            "payload_version": payload_version,
            "created_at": stored.get("created_at"),
            # false：同样的字节之前已经存过；保留了它们的行（包括标注）
            "created": indexed.created,
        }

    def execute_batch(
        self,
        items: list[dict[str, Any]],
        *,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """逐项入库：{indexed, failed}。每个文件的结果在处理完后立刻记录到 index_tasks
        （任务中心），带 trace_id。
        """
        if not items:
            raise err.index_empty_request()
        indexed: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for item in items:
            started = time.perf_counter()
            try:
                out = self._index_item(item)
            except Exception as exc:
                failure = _failure(_item_label(item), exc)
                failed.append(failure)
                self._record(_failure_record(failure, started, trace_id))
            else:
                indexed.append(out)
                self._record(_success_record(item, out, started, trace_id))
        return {"indexed": indexed, "failed": failed}

    def execute_file(self, item: dict[str, Any], *, trace_id: str | None = None) -> dict[str, Any]:
        """单个文件（POST /api/index/file），像批量中的一项一样记录；失败会抛出。"""
        started = time.perf_counter()
        try:
            out = self._index_item(item)
        except Exception as exc:
            self._record(_failure_record(_failure(_item_label(item), exc), started, trace_id))
            raise
        self._record(_success_record(item, out, started, trace_id))
        return out

    def _index_item(self, item: dict[str, Any]) -> dict[str, Any]:
        return self.execute_one(
            image_bytes=item.get("image_bytes"),
            source_path=item.get("source_path"),
            filename=item.get("filename"),
            category=item.get("category"),
            category_id=item.get("category_id"),
            tags=item.get("tags"),
            scene=item.get("scene"),
            scenes=item.get("scenes"),
            **{key: item.get(key) for key in FOOD_FIELDS},
            label_status=item.get("label_status"),
            source_type=item.get("source_type"),
            license=item.get("license"),
            description=item.get("description"),
        )

    def _record(self, row: dict[str, Any]) -> None:
        """尽力而为：记录写不了时入库结果照常有效。"""
        try:
            self._metadata.record_index_tasks([row])
        except Exception:
            logger.exception("index task record not written: %s %s", row.get("filename"), row.get("status"))


def _item_label(item: dict[str, Any]) -> str:
    return str(item.get("filename") or item.get("source_path") or "unknown")


def _failure(label: str, exc: Exception) -> dict[str, Any]:
    """客户端可以看到的 {filename, error, detail}；内部原因留在日志里。"""
    if isinstance(exc, err.AppError):
        if err.is_internal(exc):
            logger.error("index item failed: %s | %s: %s", label, exc.code, exc.detail, exc_info=exc)
        return {"filename": label, "error": exc.code, "detail": err.client_detail(exc)}
    logger.error("index item failed: %s", label, exc_info=exc)
    return {"filename": label, "error": "INTERNAL_ERROR", "detail": "internal error"}


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _success_record(item: dict[str, Any], out: dict[str, Any], started: float, trace_id: str | None) -> dict[str, Any]:
    return {
        "filename": _item_label(item),
        "status": IndexTaskStatus.SUCCEEDED,
        "image_id": out["id"],
        "new_image": bool(out.get("created", True)),
        "duration_ms": _elapsed_ms(started),
        "trace_id": trace_id,
    }


def _failure_record(failure: dict[str, Any], started: float, trace_id: str | None) -> dict[str, Any]:
    return {
        "filename": failure["filename"],
        "status": IndexTaskStatus.FAILED,
        "error_code": failure["error"],
        "error_detail": failure["detail"],
        "duration_ms": _elapsed_ms(started),
        "trace_id": trace_id,
    }
