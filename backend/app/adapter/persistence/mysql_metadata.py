"""MySQL `images` table adapter (SSOT §2)."""

from __future__ import annotations

import json
import logging
import random
from datetime import datetime
from typing import Any

from app.domain.retrieval import errors as err
from app.domain.retrieval.ports import MetadataStorePort
from app.domain.retrieval.scenes import DEFAULT_SCENES

logger = logging.getLogger("mysql_metadata")

# path stays VARCHAR(1024) per SSOT; utf8mb4 full UNIQUE hits MySQL 1071.
# Use prefix unique path(768) (same as QA prebuild; 768*4=3072).
PATH_UNIQUE_PREFIX = 768

IMAGES_DDL = """
CREATE TABLE IF NOT EXISTS images (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  path VARCHAR(1024) NOT NULL,
  url VARCHAR(2048) NULL,
  title VARCHAR(255) NULL,
  category VARCHAR(64) NULL,
  tags JSON NULL,
  scenes JSON NOT NULL,
  width INT UNSIGNED NULL,
  height INT UNSIGNED NULL,
  qdrant_point_id BIGINT UNSIGNED NOT NULL,
  status ENUM('active','deleted') NOT NULL DEFAULT 'active',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_images_path (path(768)),
  UNIQUE KEY uq_images_qdrant_point_id (qdrant_point_id),
  KEY idx_images_category (category),
  KEY idx_images_status (status),
  KEY idx_images_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def _json_tags(tags: Any) -> str | None:
    if tags is None:
        return None
    if isinstance(tags, str):
        return tags
    return json.dumps(tags, ensure_ascii=False)


def _json_scenes(scenes: Any) -> str:
    """Serialize scenes list to JSON text; default ["normal"]."""
    if scenes is None:
        return json.dumps(list(DEFAULT_SCENES), ensure_ascii=False)
    if isinstance(scenes, str):
        return scenes
    return json.dumps(list(scenes), ensure_ascii=False)


def _parse_json_field(val: Any) -> Any:
    if isinstance(val, (bytes, bytearray)):
        val = val.decode("utf-8")
    if isinstance(val, str):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return val
    return val


def _row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["tags"] = _parse_json_field(out.get("tags"))
    scenes = _parse_json_field(out.get("scenes"))
    if scenes is None:
        scenes = list(DEFAULT_SCENES)
    out["scenes"] = scenes
    for key in ("created_at", "updated_at"):
        val = out.get(key)
        if isinstance(val, datetime):
            out[key] = val.isoformat(timespec="milliseconds")
    return out


class MySQLMetadataStore(MetadataStorePort):
    """Real MySQL via pymysql; ensures `images` schema on demand."""

    def __init__(
        self,
        *,
        host: str = "mysql",
        port: int = 3306,
        user: str = "image_emb",
        password: str = "image_emb",
        database: str = "image_embedding",
        connect_timeout: float = 5.0,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.user = user
        self.password = password
        self.database = database
        self.connect_timeout = float(connect_timeout)
        self._schema_ready = False

    def _connect(self):
        try:
            import pymysql
            from pymysql.cursors import DictCursor
        except ImportError as exc:
            raise err.index_mysql_failed(
                "pymysql is not installed; add it to requirements.txt"
            ) from exc
        try:
            return pymysql.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database,
                charset="utf8mb4",
                cursorclass=DictCursor,
                connect_timeout=self.connect_timeout,
                autocommit=False,
            )
        except Exception as exc:
            raise err.index_mysql_failed(f"{type(exc).__name__}: {exc}") from exc

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(IMAGES_DDL)
                self._ensure_path_unique_prefix(cur)
                self._ensure_scenes_column(cur)
            conn.commit()
            self._schema_ready = True
            logger.info("mysql images schema ready db=%s", self.database)
        except Exception as exc:
            conn.rollback()
            raise err.index_mysql_failed(f"ensure_schema: {exc}") from exc
        finally:
            conn.close()

    def _ensure_path_unique_prefix(self, cur) -> None:
        """D4: ensure UNIQUE KEY uk_images_path (path(768)); migrate legacy names/shapes."""
        cur.execute(
            "SHOW INDEX FROM images WHERE Key_name=%s",
            ("uq_images_path",),
        )
        if cur.fetchall():
            cur.execute("ALTER TABLE images DROP INDEX uq_images_path")

        cur.execute(
            "SHOW INDEX FROM images WHERE Key_name=%s",
            ("uk_images_path",),
        )
        rows = cur.fetchall() or []
        if not rows:
            cur.execute(
                f"ALTER TABLE images ADD UNIQUE KEY uk_images_path (path({PATH_UNIQUE_PREFIX}))"
            )
            return
        row0 = rows[0]
        if isinstance(row0, dict):
            sub = row0.get("Sub_part")
        else:
            try:
                sub = row0[7]
            except Exception:
                sub = None
        if sub == PATH_UNIQUE_PREFIX:
            return
        cur.execute("ALTER TABLE images DROP INDEX uk_images_path")
        cur.execute(
            f"ALTER TABLE images ADD UNIQUE KEY uk_images_path (path({PATH_UNIQUE_PREFIX}))"
        )

    def _ensure_scenes_column(self, cur) -> None:
        """Add scenes JSON NOT NULL; backfill existing rows to [\"normal\"]."""
        cur.execute(
            "SELECT COUNT(*) AS c FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='images' AND COLUMN_NAME='scenes'",
            (self.database,),
        )
        row = cur.fetchone() or {}
        count = int(row.get("c") if isinstance(row, dict) else row[0])
        default_json = json.dumps(list(DEFAULT_SCENES), ensure_ascii=False)
        if count == 0:
            # MySQL 8: ADD nullable first, backfill, then NOT NULL
            cur.execute("ALTER TABLE images ADD COLUMN scenes JSON NULL")
            cur.execute(
                "UPDATE images SET scenes=CAST(%s AS JSON) "
                "WHERE scenes IS NULL OR JSON_TYPE(scenes)='NULL' "
                "OR JSON_LENGTH(scenes)=0",
                (default_json,),
            )
            cur.execute("ALTER TABLE images MODIFY COLUMN scenes JSON NOT NULL")
            logger.info("mysql added scenes column + backfill default=%s", default_json)
            return
        # Column exists: still backfill null/empty
        cur.execute(
            "UPDATE images SET scenes=CAST(%s AS JSON) "
            "WHERE scenes IS NULL OR JSON_TYPE(scenes)='NULL' "
            "OR JSON_LENGTH(scenes)=0",
            (default_json,),
        )

    def ping(self) -> dict[str, Any]:
        try:
            conn = self._connect()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 AS ok")
                    cur.fetchone()
                return {"ok": True, "host": self.host, "database": self.database}
            finally:
                conn.close()
        except Exception as exc:
            return {
                "ok": False,
                "detail": f"{type(exc).__name__}: {exc}",
                "host": self.host,
                "database": self.database,
            }

    def insert_image(self, row: dict[str, Any]) -> int:
        self.ensure_schema()
        path = row.get("path")
        if not path:
            raise err.validation_error("path is required")
        # Temporary unique placeholder for NOT NULL + UNIQUE(qdrant_point_id),
        # then set qdrant_point_id = id in the same transaction (SSOT §1.3).
        placeholder = random.randint(1 << 48, (1 << 63) - 1)
        sql = (
            "INSERT INTO images "
            "(path, url, title, category, tags, scenes, width, height, qdrant_point_id, status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        )
        args = (
            path,
            row.get("url"),
            row.get("title"),
            row.get("category"),
            _json_tags(row.get("tags")),
            _json_scenes(row.get("scenes")),
            row.get("width"),
            row.get("height"),
            placeholder,
            row.get("status") or "active",
        )
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                try:
                    cur.execute(sql, args)
                except Exception as exc:
                    name = type(exc).__name__
                    msg = str(exc)
                    if "Duplicate" in name or "1062" in msg or "duplicate" in msg.lower():
                        raise err.index_duplicate_path(f"path already indexed: {path}") from exc
                    raise err.index_mysql_failed(f"{name}: {msg}") from exc
                image_id = int(cur.lastrowid)
                cur.execute(
                    "UPDATE images SET qdrant_point_id=%s WHERE id=%s",
                    (image_id, image_id),
                )
            conn.commit()
            return image_id
        except err.RetrievalError:
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            raise err.index_mysql_failed(f"{type(exc).__name__}: {exc}") from exc
        finally:
            conn.close()

    def update_image(self, image_id: int, fields: dict[str, Any]) -> None:
        if not fields:
            return
        self.ensure_schema()
        allowed = {
            "path", "url", "title", "category", "tags", "scenes",
            "width", "height", "qdrant_point_id", "status",
        }
        cols = []
        args: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            cols.append(f"`{key}`=%s")
            if key == "tags":
                args.append(_json_tags(value))
            elif key == "scenes":
                args.append(_json_scenes(value))
            else:
                args.append(value)
        if not cols:
            return
        args.append(int(image_id))
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE images SET {', '.join(cols)} WHERE id=%s",
                    args,
                )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise err.index_mysql_failed(f"{type(exc).__name__}: {exc}") from exc
        finally:
            conn.close()

    def mark_deleted(self, image_id: int) -> None:
        self.update_image(int(image_id), {"status": "deleted"})

    def get_active_by_ids(
        self,
        image_ids: list[int],
        *,
        scene: str | None = None,
    ) -> dict[int, dict[str, Any]]:
        if not image_ids:
            return {}
        self.ensure_schema()
        ids = [int(i) for i in image_ids]
        placeholders = ",".join(["%s"] * len(ids))
        sql = (
            f"SELECT * FROM images WHERE id IN ({placeholders}) AND status='active'"
        )
        args: list[Any] = list(ids)
        if scene is not None and str(scene).strip() != "":
            sql += " AND JSON_CONTAINS(scenes, JSON_QUOTE(%s), '$')"
            args.append(str(scene).strip())
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, args)
                rows = cur.fetchall() or []
            return {int(r["id"]): _row_to_dict(r) for r in rows}
        except Exception as exc:
            raise err.search_mysql_failed(f"{type(exc).__name__}: {exc}") from exc
        finally:
            conn.close()

    def get_by_id(self, image_id: int) -> dict[str, Any] | None:
        self.ensure_schema()
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM images WHERE id=%s", (int(image_id),))
                row = cur.fetchone()
            return _row_to_dict(row) if row else None
        except Exception as exc:
            raise err.search_mysql_failed(f"{type(exc).__name__}: {exc}") from exc
        finally:
            conn.close()
