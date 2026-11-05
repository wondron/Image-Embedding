"""Index-image use case (PG v0.1).

Flow: bytes → sha256/uuid → S3 original+thumb (if storage wired) → insert images
→ recompute image_scenes (write-time materialize) → embed → Qdrant upsert with
payload {category_id, scenes, food_state, label_status}.
Ledger image_embeddings.synced_version via EmbeddingLedgerVectorStore (M2).

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

from app.domain.retrieval.image_ids import uuid_from_sha256
from app.domain.retrieval import errors as err
from app.domain.retrieval.embedding_validate import validate_embedding
from app.domain.retrieval.ports import (
    EmbeddingPort,
    MetadataStorePort,
    ObjectStoragePort,
    VectorStorePort,
)
from app.domain.retrieval.scene_membership import build_qdrant_payload
from app.domain.retrieval.scenes import resolve_index_scenes
from app.domain.retrieval.storage_keys import guess_ext_from_bytes_header
from app.domain.retrieval.storage_upload import put_original_and_thumb

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
        storage: ObjectStoragePort | None = None,
        indexed_dir: str = "save_images/indexed",
    ) -> None:
        self._embedding = embedding
        self._vectors = vectors
        self._metadata = metadata
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
        public_file_base: str | None = None,
    ) -> dict[str, Any]:
        del tags, public_file_base
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

        # Resolve category_id: prefer explicit; optional code lookup on metadata
        resolved_category_id = category_id
        if resolved_category_id is None and category:
            lookup = getattr(self._metadata, "get_category_id_by_code", None)
            if callable(lookup):
                resolved_category_id = lookup(str(category).strip())

        label = (label_status or ("auto" if resolved_category_id else "unlabeled")).strip()
        if label == "unlabeled" and resolved_category_id is not None:
            raise err.validation_error(
                "label_status=unlabeled requires category_id IS NULL"
            )
        if label != "unlabeled" and resolved_category_id is None and category:
            # code unknown — leave unlabeled rather than invent
            label = "unlabeled"

        original_url: str
        thumb_url: str
        if self._storage is not None:
            urls = put_original_and_thumb(
                self._storage, image_id, image_bytes, ext=ext
            )
            original_url = urls["original_url"]
            thumb_url = urls["thumb_url"]
        else:
            # TODO(M5+): require ObjectStoragePort in production; stub https for dry-run
            base = "https://cdn.example.local/image-embedding"
            original_url = f"{base}/original/{image_id}.{ext}"
            thumb_url = f"{base}/thumb/{image_id}.webp"
            logger.warning(
                "index without ObjectStoragePort; stub URLs id=%s (wire storage in stack)",
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
            "source_type": source_type or "user_upload",
            "license": license or "unknown",
            "category_id": resolved_category_id,
            "food_state": food_state,
            "label_status": label,
        }
        inserted_id = self._metadata.insert_image(row)
        image_id = inserted_id if isinstance(inserted_id, UUID) else UUID(str(inserted_id))

        # Write-time materialize effective scenes → image_scenes + payload_version
        recompute = getattr(self._metadata, "recompute_image_scenes", None)
        if not callable(recompute):
            raise err.index_mysql_failed("metadata lacks recompute_image_scenes (M5)")
        membership = recompute(image_id, bump_version=False)
        scene_codes = list(membership.get("scene_codes") or [])

        try:
            vector = self._embedding.embed_bytes(image_bytes)
        except err.RetrievalError:
            raise
        except Exception as exc:
            raise err.index_bad_image(f"embed failed: {exc}") from exc
        vector = validate_embedding(vector, for_search=False)

        payload = build_qdrant_payload(
            category_id=resolved_category_id,
            scene_codes=scene_codes,
            food_state=food_state,
            label_status=label,
        )
        try:
            self._vectors.upsert(image_id, vector, payload)
        except err.RetrievalError:
            try:
                self._metadata.mark_deleted(image_id)
            except Exception:
                logger.exception("rollback mark_deleted failed id=%s", image_id)
            raise
        except Exception as exc:
            try:
                self._metadata.mark_deleted(image_id)
            except Exception:
                logger.exception("rollback mark_deleted failed id=%s", image_id)
            raise err.index_qdrant_failed(f"{type(exc).__name__}: {exc}") from exc

        created = self._metadata.get_by_id(image_id) or {}
        return {
            "id": str(image_id),
            "qdrant_point_id": str(image_id),
            "original_url": created.get("original_url") or original_url,
            "thumb_url": created.get("thumb_url") or thumb_url,
            "title": title,
            "category_id": created.get("category_id")
            if created.get("category_id") is not None
            else resolved_category_id,
            "scenes": created.get("scenes") or scene_codes,
            "food_state": created.get("food_state")
            if "food_state" in created
            else food_state,
            "label_status": created.get("label_status") or label,
            "width": created.get("width") or width,
            "height": created.get("height") or height,
            "payload_version": created.get("payload_version")
            or membership.get("payload_version"),
            "created_at": created.get("created_at"),
        }

    def execute_batch(
        self,
        items: list[dict[str, Any]],
        *,
        public_file_base: str | None = None,
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
                        public_file_base=public_file_base,
                    )
                )
            except err.RetrievalError as exc:
                failed.append(
                    {
                        "filename_or_path": label,
                        "error": exc.code,
                        "message": exc.detail,
                    }
                )
            except Exception as exc:
                failed.append(
                    {
                        "filename_or_path": label,
                        "error": "INTERNAL_ERROR",
                        "message": str(exc),
                    }
                )
        if not indexed and failed:
            return {"ok": False, "indexed": [], "failed": failed, "all_failed": True}
        return {"ok": True, "indexed": indexed, "failed": failed, "all_failed": False}
