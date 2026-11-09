"""Index-image use case (PG v0.1).

Flow: bytes → sha256/uuid → validate labels → embed (no side effects yet) →
S3 original+thumb → insert images → recompute image_scenes → Qdrant upsert with
payload {category_id, scenes, food_state, label_status} into the active model's
collection → image_embeddings ledger records the payload_version just pushed.

Path-based local disk store is legacy MVP; when storage is present we prefer
put_original_and_thumb and DB https URLs (no path column in PG).
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Optional, Sequence
from uuid import UUID

from app.core.enums import (
    DEFAULT_LICENSE,
    DEFAULT_SOURCE_TYPE,
    FOOD_STATE,
    LICENSE,
    SOURCE_TYPE,
    LabelStatus,
)
from app.services.retrieval.active_model import ActiveModel
from app.services.retrieval.enum_fields import coerce_enum, coerce_optional_enum
from app.services.retrieval.image_ids import uuid_from_sha256
from app.core import errors as err
from app.services.retrieval.embedding_validate import validate_embedding
from app.repositories.ports import (
    EmbeddingPort,
    MetadataStorePort,
    ImageStoragePort,
    VectorStorePort,
)
from app.services.retrieval.scene_membership import build_qdrant_payload
from app.services.retrieval.scenes import resolve_index_scenes
from app.services.retrieval.storage_keys import guess_ext_from_bytes_header
from app.services.retrieval.storage_upload import put_original_and_thumb
from app.services.retrieval.text_fields import normalize_description

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
        label_status: str | None = None,
        source_type: str | None = None,
        license: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        del tags
        # Validate optional client scene hints (membership still recomputed from rules)
        _ = resolve_index_scenes(scene=scene, scenes=scenes)

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
        food = coerce_optional_enum(FOOD_STATE, food_state, field="food_state")
        desc = normalize_description(description)

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

        # Same bytes -> same id. If the image is already stored, the first
        # upload's columns win and a failed upsert must not delete it.
        already_stored = self._metadata.get_by_id(image_id) is not None

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
            "food_state": food,
            "label_status": label,
            "description": desc,
        }
        inserted_id = self._metadata.insert_image(row)
        image_id = inserted_id if isinstance(inserted_id, UUID) else UUID(str(inserted_id))

        # Write-time materialize effective scenes → image_scenes + payload_version
        membership = self._metadata.recompute_image_scenes(image_id, bump_version=False)
        scene_codes = list(membership.get("scene_codes") or [])

        # Qdrant payload mirrors the stored row (differs from the request on re-upload)
        stored = self._metadata.get_by_id(image_id) or {}
        if stored:
            resolved_category_id = stored.get("category_id")
            food = stored.get("food_state")
            label = stored.get("label_status") or label
        payload_version = stored.get("payload_version") or membership.get("payload_version")

        payload = build_qdrant_payload(
            category_id=resolved_category_id,
            scene_codes=scene_codes,
            food_state=food,
            label_status=label,
        )
        try:
            self._vectors.upsert(image_id, vector, payload)
        except Exception as exc:
            if not already_stored:
                try:
                    self._metadata.mark_deleted(image_id)
                except Exception:
                    logger.exception("rollback mark_deleted failed id=%s", image_id)
            if isinstance(exc, err.AppError):
                raise
            raise err.index_qdrant_failed(f"{type(exc).__name__}: {exc}") from exc

        # Ledger: which payload_version is now in this model's collection. A missing
        # row only means "not synced" (re-push later), so no rollback on failure.
        self._metadata.upsert_image_embedding(
            model_id=self._model.id,
            image_id=image_id,
            synced_version=int(payload_version) if payload_version is not None else None,
        )

        created = stored or {}
        return {
            "id": str(image_id),
            "filename": title,
            "original_url": created.get("original_url") or original_url,
            "thumb_url": created.get("thumb_url") or thumb_url,
            "category_id": created.get("category_id")
            if created.get("category_id") is not None
            else resolved_category_id,
            "scenes": created.get("scenes") or scene_codes,
            "food_state": created.get("food_state")
            if "food_state" in created
            else (str(food) if food else None),
            "label_status": str(created.get("label_status") or label),
            "width": created.get("width") or width,
            "height": created.get("height") or height,
            "description": created.get("description", desc),
            "payload_version": payload_version,
            "created_at": created.get("created_at"),
        }

    def execute_batch(
        self,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not items:
            raise err.index_empty_request()
        indexed: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for item in items:
            label = item.get("filename") or item.get("source_path") or "unknown"
            try:
                indexed.append(
                    self.execute_one(
                        image_bytes=item.get("image_bytes"),
                        source_path=item.get("source_path"),
                        filename=item.get("filename"),
                        category=item.get("category"),
                        category_id=item.get("category_id"),
                        tags=item.get("tags"),
                        scene=item.get("scene"),
                        scenes=item.get("scenes"),
                        food_state=item.get("food_state"),
                        label_status=item.get("label_status"),
                        source_type=item.get("source_type"),
                        license=item.get("license"),
                        description=item.get("description"),
                    )
                )
            except err.AppError as exc:
                if err.is_internal(exc):  # the client sees the public text; the cause stays in the log
                    logger.error("index item failed: %s | %s: %s", label, exc.code, exc.detail, exc_info=exc)
                failed.append({"filename": label, "error": exc.code, "detail": err.client_detail(exc)})
            except Exception:
                # Details go to the log; the item reports a generic failure.
                logger.exception("index item failed: %s", label)
                failed.append({"filename": label, "error": "INTERNAL_ERROR", "detail": "internal error"})
        return {"indexed": indexed, "failed": failed}
