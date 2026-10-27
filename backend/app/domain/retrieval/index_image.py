"""Index-image use case (SSOT §3.1).

Flow: decode → embed(+dim check) → MySQL insert → Qdrant upsert(point_id=id).
On Qdrant failure: mark MySQL row deleted (best-effort rollback).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from app.domain.retrieval import errors as err
from app.domain.retrieval.embedding_validate import validate_embedding
from app.domain.retrieval.ports import EmbeddingPort, MetadataStorePort, VectorStorePort

logger = logging.getLogger("retrieval.index")

# Align with SSOT D4 / UNIQUE(path(768))
PATH_MAX_FOR_UNIQUE = 768


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
    """Unicode-safe file read for Windows 中文路径.

    Do NOT use cv2.imread(path) — it breaks on non-ASCII paths.
    Prefer Path.read_bytes(); fallback np.fromfile (算法烟测同款).
    """
    p = Path(path)
    try:
        return p.read_bytes()
    except OSError:
        import numpy as np

        return np.fromfile(path, dtype=np.uint8).tobytes()


def _normalize_path(path: str) -> str:
    """Normalize path for storage + D4 length check (full path uniqueness)."""
    return str(Path(path).expanduser())


def _reject_if_path_too_long(path: str) -> str:
    normalized = _normalize_path(path)
    if len(normalized) > PATH_MAX_FOR_UNIQUE:
        raise err.index_path_too_long(
            f"path length {len(normalized)} > {PATH_MAX_FOR_UNIQUE} after normalize"
        )
    return normalized

def _title_from_path(path: str) -> str:
    return Path(path).name or path


class IndexImageUseCase:
    def __init__(
        self,
        embedding: EmbeddingPort,
        vectors: VectorStorePort,
        metadata: MetadataStorePort,
        *,
        indexed_dir: str = "save_images/indexed",
    ) -> None:
        self._embedding = embedding
        self._vectors = vectors
        self._metadata = metadata
        self._indexed_dir = indexed_dir

    def execute_one(
        self,
        *,
        image_bytes: bytes | None = None,
        source_path: str | None = None,
        filename: str | None = None,
        category: str | None = None,
        tags: list[Any] | None = None,
        public_file_base: str | None = None,
    ) -> dict[str, Any]:
        """Index a single image. Raises RetrievalError on hard failure."""
        path_mode = bool(source_path and not image_bytes)
        if image_bytes is None and not source_path:
            raise err.index_empty_request()

        if path_mode:
            p = Path(source_path)
            if not p.is_file():
                raise err.index_bad_image(f"path not found: {source_path}")
            try:
                image_bytes = _read_image_file(str(p))
            except OSError as exc:
                raise err.index_bad_image(f"cannot read path: {exc}") from exc
            store_path = _reject_if_path_too_long(str(p.resolve() if p.exists() else p))
            title = _title_from_path(store_path)
        else:
            assert image_bytes is not None
            title = filename or "upload.bin"
            # Persist under indexed_dir using a temp name; finalize after MySQL id.
            os.makedirs(self._indexed_dir, exist_ok=True)
            store_path = ""  # filled after id

        width, height = _decode_size(image_bytes)
        if width is None or height is None:
            raise err.index_bad_image("cannot decode image")

        try:
            vector = self._embedding.embed_bytes(image_bytes)
        except err.RetrievalError:
            raise
        except Exception as exc:
            raise err.index_bad_image(f"embed failed: {exc}") from exc
        vector = validate_embedding(vector, for_search=False)

        # For multipart: provisional unique path; rewritten after id if needed
        if not path_mode:
            safe_name = Path(title).name.replace("\x00", "")
            # unique-ish pre-id path to satisfy UNIQUE(path) until we know id
            import uuid as _uuid

            provisional = os.path.join(
                self._indexed_dir, f"pending_{_uuid.uuid4().hex}_{safe_name}"
            )
            Path(provisional).write_bytes(image_bytes)
            store_path = _reject_if_path_too_long(provisional)

        row = {
            "path": store_path,
            "url": None,
            "title": title,
            "category": category,
            "tags": tags,
            "width": width,
            "height": height,
            "status": "active",
        }
        image_id = self._metadata.insert_image(row)

        # Finalize path for uploads: indexed/{id}_{filename}
        if not path_mode:
            ext = Path(title).suffix or ".bin"
            final_path = os.path.join(self._indexed_dir, f"{image_id}{ext}")
            try:
                os.replace(store_path, final_path)
                store_path = _reject_if_path_too_long(final_path)
                self._metadata.update_image(image_id, {"path": store_path})
            except OSError as exc:
                logger.warning("rename indexed file failed id=%s: %s", image_id, exc)

        url = None
        if public_file_base:
            url = f"{public_file_base.rstrip('/')}/api/images/file?id={image_id}"
            try:
                self._metadata.update_image(image_id, {"url": url})
            except Exception:
                logger.warning("failed to persist url for id=%s", image_id)

        try:
            self._vectors.upsert(
                image_id,
                vector,
                payload={"image_id": image_id},  # optional redundancy only
            )
        except err.RetrievalError:
            # Best-effort rollback: soft-delete MySQL row
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
            "id": image_id,
            "qdrant_point_id": image_id,
            "path": created.get("path") or store_path,
            "url": created.get("url") or url,
            "title": created.get("title") or title,
            "category": created.get("category") or category,
            "tags": created.get("tags") if created.get("tags") is not None else tags,
            "width": created.get("width") or width,
            "height": created.get("height") or height,
            "status": created.get("status") or "active",
            "created_at": created.get("created_at"),
        }

    def execute_batch(
        self,
        items: list[dict[str, Any]],
        *,
        public_file_base: str | None = None,
    ) -> dict[str, Any]:
        """Process many items; partial success → 200 + failed[]."""
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
                        tags=item.get("tags"),
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
            # Entire batch failed → all_failed so HTTP can promote codes (D4-3 PATH_TOO_LONG etc.)
            return {"ok": False, "indexed": [], "failed": failed, "all_failed": True}
        return {"ok": True, "indexed": indexed, "failed": failed, "all_failed": False}

