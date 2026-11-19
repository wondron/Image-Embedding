from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

from app.db.common import _as_uuid, _row_to_dict
from app.core import errors as err


class PgDeletionsMixin:
    """image_deletions 入队 / 清理的辅助函数。"""

    def enqueue_image_deletion(self, image_id: UUID | str) -> dict[str, Any]:
        """事务：记下 URL → DELETE images（CASCADE 依赖）→ INSERT image_deletions。

        契约规定的顺序：先从 images 删除，再插入 image_deletions。
        先查出 URL，这样插入时能存下 original_url/thumb_url。
        可重复调用：已经删除且仍在排队的图片返回它的队列行。旧行还在排队时又被重新存入的图片
        （同样的字节，同样的 id）会再次删除，并刷新队列行。既没有图片也没有队列行时为
        IMAGE_NOT_FOUND。
        """
        self.ensure_schema()
        from sqlalchemy import text

        uid = str(_as_uuid(image_id))
        engine = self._get_engine()
        try:
            with engine.begin() as conn:
                row = conn.execute(
                    text(
                        "SELECT id, original_url, thumb_url FROM images "
                        "WHERE id = CAST(:id AS uuid) FOR UPDATE"
                    ),
                    {"id": uid},
                ).mappings().fetchone()
                if row is None:
                    existing = conn.execute(
                        text(
                            "SELECT image_id, original_url, thumb_url, deleted_at "
                            "FROM image_deletions WHERE image_id = CAST(:id AS uuid)"
                        ),
                        {"id": uid},
                    ).mappings().fetchone()
                    if existing is not None:
                        return _row_to_dict(existing)
                    raise err.image_not_found(f"image {uid} does not exist")
                original_url = str(row["original_url"] or "")
                thumb_url = str(row["thumb_url"] or "")
                if not original_url or not thumb_url:
                    raise err.validation_error(
                        f"image {uid} missing original_url/thumb_url"
                    )

                # 1) 删除 images（+ CASCADE 删除 image_scenes / image_embeddings / image_foods）
                conn.execute(
                    text("DELETE FROM images WHERE id = CAST(:id AS uuid)"),
                    {"id": uid},
                )
                # 2) 把 Qdrant + S3 的清理加入队列（刷新之前删除留下的行）
                conn.execute(
                    text(
                        "INSERT INTO image_deletions "
                        "(image_id, original_url, thumb_url, deleted_at) "
                        "VALUES (CAST(:id AS uuid), :ou, :tu, now()) "
                        "ON CONFLICT (image_id) DO UPDATE SET original_url = EXCLUDED.original_url, "
                        "thumb_url = EXCLUDED.thumb_url, deleted_at = EXCLUDED.deleted_at"
                    ),
                    {"id": uid, "ou": original_url, "tu": thumb_url},
                )
                out = conn.execute(
                    text(
                        "SELECT image_id, original_url, thumb_url, deleted_at "
                        "FROM image_deletions WHERE image_id = CAST(:id AS uuid)"
                    ),
                    {"id": uid},
                ).mappings().fetchone()
            return _row_to_dict(out)
        except err.AppError:
            raise
        except Exception as exc:
            raise err.index_postgres_failed(
                f"enqueue_image_deletion: {type(exc).__name__}: {exc}"
            ) from exc

    def list_pending_deletions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """给后台任务的待处理 image_deletions 行，最早的在前。"""
        self.ensure_schema()
        from sqlalchemy import text

        lim = max(1, min(int(limit), 1000))
        engine = self._get_engine()
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT image_id, original_url, thumb_url, deleted_at "
                        "FROM image_deletions "
                        "ORDER BY deleted_at ASC "
                        "LIMIT :lim"
                    ),
                    {"lim": lim},
                ).mappings().fetchall()
            return [_row_to_dict(r) for r in rows]
        except Exception as exc:
            raise err.index_postgres_failed(
                f"list_pending_deletions: {type(exc).__name__}: {exc}"
            ) from exc

    def count_pending_deletions(self) -> int:
        """有多少已删除的图片还在等向量 / 文件的清理。"""
        self.ensure_schema()
        from sqlalchemy import text

        try:
            with self._get_engine().connect() as conn:
                return int(conn.execute(text("SELECT count(*) FROM image_deletions")).scalar_one())
        except Exception as exc:
            raise err.list_postgres_failed(
                f"count_pending_deletions: {type(exc).__name__}: {exc}"
            ) from exc

    def complete_deletion(self, image_id: UUID | str) -> None:
        """Qdrant + S3 清理成功后删除 image_deletions 行。"""
        self.ensure_schema()
        from sqlalchemy import text

        uid = str(_as_uuid(image_id))
        engine = self._get_engine()
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "DELETE FROM image_deletions "
                        "WHERE image_id = CAST(:id AS uuid)"
                    ),
                    {"id": uid},
                )
        except Exception as exc:
            raise err.index_postgres_failed(
                f"complete_deletion: {type(exc).__name__}: {exc}"
            ) from exc
