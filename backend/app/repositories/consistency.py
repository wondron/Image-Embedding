"""数据核对（app.services.retrieval.consistency）用的只读查询：按 id 顺序一段一段地读图片，
以及给向量库、对象存储里找到的 id 查数据库认不认识。

全部只读，不加锁：核对和入库、删除同时进行时，看到的是各自那一刻的状态。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core import errors as err
from app.core.enums import ModelStatus
from app.db.common import _as_uuid, _iso


def _uuids(image_ids: list[str]) -> list[UUID]:
    return [_as_uuid(image_id) for image_id in image_ids]


class PgConsistencyMixin:
    def scan_images(self, *, after: UUID | str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        """`after` 之后（不含）按 id 顺序的一段图片，每张带核对要用的全部字段：

        {id, sha256（十六进制）, original_url, thumb_url, payload_version, category_id, label_status,
        food_state, scenes, ledger: {model_id: synced_version}}

        同一个快照里读（REPEATABLE READ），所以 payload 字段、场景和写入记录彼此一致。
        """
        self.ensure_schema()
        from sqlalchemy import text

        try:
            with self._get_engine().connect() as conn:
                conn.execution_options(isolation_level="REPEATABLE READ")
                with conn.begin():
                    rows = conn.execute(
                        text(
                            "SELECT i.id::text AS id, encode(i.sha256, 'hex') AS sha256, i.original_url, i.thumb_url, "
                            "i.payload_version, i.category_id, i.label_status, f.food_state "
                            "FROM images i LEFT JOIN image_foods f ON f.image_id = i.id "
                            "WHERE CAST(:after AS uuid) IS NULL OR i.id > CAST(:after AS uuid) "
                            "ORDER BY i.id LIMIT :lim"
                        ),
                        {"after": _as_uuid(after) if after is not None else None, "lim": max(1, int(limit))},
                    ).mappings().fetchall()
                    ids = [str(row["id"]) for row in rows]
                    codes = self._scene_codes_by_image(conn, ids)
                    ledger: dict[str, dict[int, int]] = {}
                    if ids:
                        for image_id, model_id, synced in conn.execute(
                            text(
                                "SELECT image_id::text, model_id, synced_version FROM image_embeddings "
                                "WHERE image_id = ANY(CAST(:ids AS uuid[]))"
                            ),
                            {"ids": _uuids(ids)},
                        ).fetchall():
                            ledger.setdefault(str(image_id), {})[int(model_id)] = int(synced)
            return [
                {**dict(row), "scenes": codes.get(str(row["id"]), []), "ledger": ledger.get(str(row["id"]), {})}
                for row in rows
            ]
        except err.AppError:
            raise
        except Exception as exc:
            raise err.index_postgres_failed(f"scan_images: {type(exc).__name__}: {exc}") from exc

    def existing_image_ids(self, image_ids: list[str]) -> set[str]:
        """image_ids 中在 images 表里的那些（规范的 uuid 文本）。"""
        if not image_ids:
            return set()
        self.ensure_schema()
        from sqlalchemy import text

        try:
            with self._get_engine().connect() as conn:
                rows = conn.execute(
                    text("SELECT id::text FROM images WHERE id = ANY(CAST(:ids AS uuid[]))"), {"ids": _uuids(image_ids)}
                ).fetchall()
            return {str(row[0]) for row in rows}
        except Exception as exc:
            raise err.index_postgres_failed(f"existing_image_ids: {type(exc).__name__}: {exc}") from exc

    def queued_deletion_ids(self, image_ids: list[str]) -> set[str]:
        """image_ids 中在删除队列（image_deletions）里、等着清理向量和文件的那些。"""
        if not image_ids:
            return set()
        self.ensure_schema()
        from sqlalchemy import text

        try:
            with self._get_engine().connect() as conn:
                rows = conn.execute(
                    text("SELECT image_id::text FROM image_deletions WHERE image_id = ANY(CAST(:ids AS uuid[]))"),
                    {"ids": _uuids(image_ids)},
                ).fetchall()
            return {str(row[0]) for row in rows}
        except Exception as exc:
            raise err.index_postgres_failed(f"queued_deletion_ids: {type(exc).__name__}: {exc}") from exc

    def file_refs(self, image_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        """这些 id 在数据库里记着的文件：{id: [{original_url, thumb_url, deleted}]}。图片行和删除队列
        都算（deleted=True 的是已删除、文件等着清理的）；两边都没有的 id 不返回。
        """
        if not image_ids:
            return {}
        self.ensure_schema()
        from sqlalchemy import text

        try:
            with self._get_engine().connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT id::text AS id, original_url, thumb_url, false AS deleted FROM images "
                        "WHERE id = ANY(CAST(:ids AS uuid[])) "
                        "UNION ALL "
                        "SELECT image_id::text, original_url, thumb_url, true FROM image_deletions "
                        "WHERE image_id = ANY(CAST(:ids AS uuid[]))"
                    ),
                    {"ids": _uuids(image_ids)},
                ).mappings().fetchall()
            out: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                out.setdefault(str(row["id"]), []).append(
                    {"original_url": row["original_url"], "thumb_url": row["thumb_url"], "deleted": bool(row["deleted"])}
                )
            return out
        except Exception as exc:
            raise err.index_postgres_failed(f"file_refs: {type(exc).__name__}: {exc}") from exc

    def consistency_counts(self) -> dict[str, Any]:
        """{images, pending_deletions, oldest_deletion_at, current_models}，同一个快照。"""
        self.ensure_schema()
        from sqlalchemy import text

        try:
            with self._get_engine().connect() as conn:
                conn.execution_options(isolation_level="REPEATABLE READ")
                with conn.begin():
                    images = int(conn.execute(text("SELECT count(*) FROM images")).scalar_one())
                    pending, oldest = conn.execute(text("SELECT count(*), min(deleted_at) FROM image_deletions")).one()
                    current = int(conn.execute(
                        text("SELECT count(*) FROM embedding_models WHERE status = :current"),
                        {"current": ModelStatus.CURRENT.value},
                    ).scalar_one())
            return {
                "images": images,
                "pending_deletions": int(pending),
                "oldest_deletion_at": _iso(oldest),
                "current_models": current,
            }
        except Exception as exc:
            raise err.index_postgres_failed(f"consistency_counts: {type(exc).__name__}: {exc}") from exc
