"""Index-image use case (PG v0.1).

Flow: bytes → sha256/uuid → validate labels → embed (no side effects yet) →
S3 original+thumb → one PostgreSQL write transaction: insert images, recompute
image_scenes, read a consistent payload snapshot, stage its embedding ledger,
upsert Qdrant, then commit. Failed writes roll back without deleting another
request's image.

Path-based local disk store is legacy MVP; when storage is present we prefer
put_original_and_thumb and DB https URLs (no path column in PG).

Task center: execute_batch / execute_file write one index_tasks row per file right after it
(succeeded with the image id, or failed with the code and the client-safe detail). Best effort:
a record that cannot be written is logged and the index result stands. execute_one records
nothing (it is the indexing itself).
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
from app.utils.storage_keys import guess_ext_from_bytes_header
from app.services.retrieval.storage_upload import put_original_and_thumb
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
        # Check optional client scene hints against the scenes table (membership still recomputed from rules)
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
        # optional food annotation, stored in image_foods (app.core.rules.food_fields)
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
        tag_list = normalize_tags(tags)  # PG only, like description: not in the Qdrant payload

        # Resolve category_id: prefer explicit; optional code lookup on metadata
        resolved_category_id = category_id
        if resolved_category_id is None and category:
            resolved_category_id = self._metadata.get_category_id_by_code(str(category).strip())
            if resolved_category_id is None:
                logger.warning("index: unknown category code %r; storing unlabeled", category)

        # images invariant: label_status = unlabeled  <=>  category_id IS NULL
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
                # category code given but unknown — keep the image, drop the label
                label = LabelStatus.UNLABELED

        # Embed before any write: a failure here leaves no S3 objects or rows behind.
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
            # TODO(M5+): require ImageStoragePort in production; stub https for dry-run
            base = "https://cdn.example.local/image-embedding"
            original_url = f"{base}/original/{image_id}.{ext}"
            thumb_url = f"{base}/thumb/{image_id}.webp"
            logger.warning(
                "index without ImageStoragePort; stub URLs id=%s (wire storage in stack)",
                image_id,
            )
            # Best-effort local mirror for debug
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
        # The row lock stays held through the vector write and ledger commit.
        # Concurrent uploads use the INSERT outcome, never a separate existence check.
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

        return {
            "id": str(image_id),
            "filename": title,
            "original_url": stored["original_url"],
            "thumb_url": stored["thumb_url"],
            "category_id": stored.get("category_id"),
            "scenes": scene_codes,
            # the stored row wins: on re-upload the first upload's values are kept
            **{key: stored.get(key) for key in FOOD_FIELDS},
            "label_status": str(stored["label_status"]),
            "width": stored["width"],
            "height": stored["height"],
            "description": stored.get("description", ""),
            "tags": stored.get("tags", []),
            "payload_version": payload_version,
            "created_at": stored.get("created_at"),
            # false: the same bytes were stored before; their row (labels included) was kept
            "created": indexed.created,
        }

    def execute_batch(
        self,
        items: list[dict[str, Any]],
        *,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """Index each item on its own: {indexed, failed}. Each file's outcome is recorded in
        index_tasks right after it (task center), with trace_id."""
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
        """One file (POST /api/index/file), recorded like a batch item; its failure is raised."""
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
        """Best effort: the index result stands when its record cannot be written."""
        try:
            self._metadata.record_index_tasks([row])
        except Exception:
            logger.exception("index task record not written: %s %s", row.get("filename"), row.get("status"))


def _item_label(item: dict[str, Any]) -> str:
    return str(item.get("filename") or item.get("source_path") or "unknown")


def _failure(label: str, exc: Exception) -> dict[str, Any]:
    """{filename, error, detail} as a client may see it; an internal cause stays in the log."""
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
