"""embedding_models（模型版本，每个对应一个 Qdrant collection）和 image_embeddings 台账
（哪些图片在哪个版本的 collection 里有向量）。
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.core.enums import Distance, ModelStatus
from app.db.common import _as_uuid, _row_to_dict
from app.core import errors as err

_MODEL_COLUMNS = (
    "m.id, m.name, m.version, m.dim, m.distance, m.input_size, m.preprocess, m.weights_uri, "
    "m.collection, m.status, m.fingerprint, m.created_at, m.activated_at"
)


def _model_row(row: Any) -> dict[str, Any]:
    out = _row_to_dict(row)
    out["distance"] = Distance(str(out.get("distance") or Distance.COSINE).lower()).value
    return out


def _registry_error(op: str, exc: IntegrityError) -> err.AppError:
    name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None) or "unknown"
    if name == "uq_embedding_models_name_version":
        return err.embedding_model_exists("a model version with this name and version already exists")
    if name == "uq_embedding_models_collection":
        return err.embedding_model_exists("another model version already uses this collection")
    if name == "uq_embedding_models_one_current":
        return err.embedding_model_state("another model version became current at the same time; try again")
    return err.index_postgres_failed(f"{op}: constraint {name}: {exc}")


def _upsert_image_embedding(
    conn: Any,
    *,
    model_id: int,
    image_id: UUID | str,
    synced_version: int | None = None,
) -> None:
    """在调用方的连接和事务上记录一条 embedding。"""
    from sqlalchemy import text

    uid = _as_uuid(image_id)
    version = synced_version
    if version is None:
        prow = conn.execute(
            text("SELECT payload_version FROM images WHERE id = CAST(:id AS uuid)"),
            {"id": str(uid)},
        ).fetchone()
        if prow is None:
            raise err.index_postgres_failed(f"upsert_image_embedding: image {uid} not found")
        version = int(prow[0])
    conn.execute(
        text(
            "INSERT INTO image_embeddings "
            "(model_id, image_id, synced_version, embedded_at) "
            "VALUES (:mid, CAST(:iid AS uuid), :ver, now()) "
            "ON CONFLICT (model_id, image_id) DO UPDATE SET "
            "synced_version = EXCLUDED.synced_version, "
            "embedded_at = EXCLUDED.embedded_at"
        ),
        {"mid": int(model_id), "iid": str(uid), "ver": int(version)},
    )


class PgEmbeddingsMixin:
    """embedding_models 的当前行 + image_embeddings 台账。"""

    def get_current_embedding_model(self) -> dict[str, Any]:
        """返回唯一一行 status='current' 的 embedding_models。

        没有时明确报错——数据初始化的预期（M2 空库重建）：
          INSERT INTO embedding_models
            (name, version, dim, distance, input_size, preprocess, weights_uri,
             collection, status, activated_at)
          VALUES (..., 'cosine', ..., '{}', '...', '<collection>', 'current', now());
        部分唯一索引保证最多一个 current。
        """
        self.ensure_schema()
        from sqlalchemy import text

        engine = self._get_engine()
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT id, name, version, dim, distance, input_size, "
                    "preprocess, weights_uri, collection, status, fingerprint, "
                    "created_at, activated_at "
                    "FROM embedding_models WHERE status = :current LIMIT 1"
                ),
                {"current": ModelStatus.CURRENT.value},
            ).mappings().fetchone()
        if row is None:
            raise err.not_ready(
                "no embedding_models row with status='current'; "
                "seed a current model (collection/dim/distance) before starting retrieval"
            )
        out = _row_to_dict(row)
        out["vector_dim"] = int(out["dim"])
        # 给 QdrantStore 的普通 str（cosine/dot/euclid）；ck_embedding_models_distance
        # 保证取值合法
        out["distance"] = Distance(str(out.get("distance") or Distance.COSINE).lower()).value
        return out

    def upsert_image_embedding(
        self,
        *,
        model_id: int,
        image_id: UUID | str,
        synced_version: int | None = None,
    ) -> None:
        """Qdrant upsert 成功之后：同步 image_embeddings 台账。

        synced_version 为 None 时，读取 image_id 的 images.payload_version。
        """
        self.ensure_schema()
        engine = self._get_engine()
        try:
            with engine.begin() as conn:
                _upsert_image_embedding(
                    conn, model_id=model_id, image_id=image_id, synced_version=synced_version
                )
        except err.AppError:
            raise
        except Exception as exc:
            raise err.index_postgres_failed(
                f"upsert_image_embedding: {type(exc).__name__}: {exc}"
            ) from exc

    def get_payload_rows(self, image_ids: list[str]) -> list[dict[str, Any]]:
        """这些图片的 Qdrant payload 的来源字段（契约 §4.2），按 id 顺序：
        {id, payload_version, category_id, label_status, food_state, scenes}。不存在的 id 不返回。
        """
        self.ensure_schema()
        from sqlalchemy import text

        ids = [_as_uuid(i) for i in image_ids]
        if not ids:
            return []
        engine = self._get_engine()
        try:
            with engine.connect() as conn:
                conn.execution_options(isolation_level="REPEATABLE READ")
                with conn.begin():
                    rows = conn.execute(
                        text(
                            "SELECT i.id::text AS id, i.payload_version, i.category_id, i.label_status, f.food_state "
                            "FROM images i LEFT JOIN image_foods f ON f.image_id = i.id "
                            "WHERE i.id = ANY(:ids) ORDER BY i.id"
                        ),
                        {"ids": ids},
                    ).mappings().fetchall()
                    codes = self._scene_codes_by_image(conn, [r["id"] for r in rows])
            return [{**dict(r), "scenes": codes.get(r["id"], [])} for r in rows]
        except err.AppError:
            raise
        except Exception as exc:
            raise err.index_postgres_failed(f"get_payload_rows: {type(exc).__name__}: {exc}") from exc

    def mark_payloads_pushed(self, *, model_id: int, versions: dict[str, int]) -> list[str]:
        """台账：这些 payload 版本现在已经在 model_id 的 collection 里了。按 id 顺序返回记录了的
        图片 id。

        只有版本仍是图片的 payload_version 时才记录：图片在读取 payload 之后又变了（或被删了）时，
        刚写入的 payload 可能比另一个写入方推送的更旧，所以调用方重新读取并再推送一次
        （services.retrieval.sync_scenes），而不是让台账声称它是最新的。

        已经在台账里的图片保留原来的 embedded_at（只是 payload 变了）；台账里缺失的图片
        （入库时台账写入失败）现在补上一行。
        """
        if not versions:
            return []
        self.ensure_schema()
        from sqlalchemy import text

        items = sorted(versions.items())
        engine = self._get_engine()
        try:
            with engine.begin() as conn:
                rows = conn.execute(
                    text(
                        "INSERT INTO image_embeddings (model_id, image_id, synced_version, embedded_at) "
                        "SELECT :mid, d.image_id, d.version, now() "
                        "FROM unnest(CAST(:ids AS uuid[]), CAST(:versions AS bigint[])) AS d(image_id, version) "
                        "JOIN images i ON i.id = d.image_id AND i.payload_version = d.version "
                        "ON CONFLICT (model_id, image_id) DO UPDATE SET synced_version = EXCLUDED.synced_version "
                        "RETURNING image_id::text"
                    ),
                    {
                        "mid": int(model_id),
                        "ids": [_as_uuid(image_id) for image_id, _ in items],
                        "versions": [int(version) for _, version in items],
                    },
                ).fetchall()
            return sorted(str(row[0]) for row in rows)
        except Exception as exc:
            raise err.index_postgres_failed(f"mark_payloads_pushed: {type(exc).__name__}: {exc}") from exc

    def list_unsynced_payload_ids(
        self,
        *,
        after: UUID | str | None = None,
        until: UUID | str | None = None,
        limit: int = 200,
        model_id: int | None = None,
    ) -> list[str]:
        """当前模型（给了 model_id 时为它）的台账行落后于 payload_version 的图片，按 id 顺序：
        在 `after` 之后（不含），给了 `until` 时到它为止（含）。

        只包含有台账行的图片：没有台账行的需要的是向量，推送 payload 给不了
        （list_images(out_of_sync=True) 两种都显示）。
        """
        self.ensure_schema()
        from sqlalchemy import text

        model_join = (
            "JOIN embedding_models m ON m.id = e.model_id AND m.status = :current "
            if model_id is None
            else "JOIN embedding_models m ON m.id = e.model_id AND m.id = :mid "
        )
        try:
            with self._get_engine().connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT i.id::text FROM images i "
                        "JOIN image_embeddings e ON e.image_id = i.id "
                        + model_join
                        + "WHERE e.synced_version < i.payload_version "
                        "AND (CAST(:after AS uuid) IS NULL OR i.id > CAST(:after AS uuid)) "
                        "AND (CAST(:until AS uuid) IS NULL OR i.id <= CAST(:until AS uuid)) "
                        "ORDER BY i.id LIMIT :lim"
                    ),
                    {
                        "current": ModelStatus.CURRENT.value,
                        "mid": int(model_id) if model_id is not None else None,
                        "after": _as_uuid(after) if after is not None else None,
                        "until": _as_uuid(until) if until is not None else None,
                        "lim": max(1, int(limit)),
                    },
                ).fetchall()
            return [str(row[0]) for row in rows]
        except Exception as exc:
            raise err.index_postgres_failed(f"list_unsynced_payload_ids: {type(exc).__name__}: {exc}") from exc

    # -- 模型版本（/api/embedding-models）---------------------------------------------

    def list_embedding_models(self) -> dict[str, Any]:
        """{images, models}：存了多少张图片，以及每个模型版本（最早的在前）及其 embedded
        （台账行数：在它的 collection 里有向量的图片）和 behind（比图片 payload_version 旧的
        台账行数）。同一个快照，所以 embedded <= images。
        """
        self.ensure_schema()
        from sqlalchemy import text

        try:
            with self._get_engine().connect() as conn:
                conn.execution_options(isolation_level="REPEATABLE READ")
                with conn.begin():
                    images = int(conn.execute(text("SELECT count(*) FROM images")).scalar_one())
                    rows = conn.execute(
                        text(
                            f"SELECT {_MODEL_COLUMNS}, "
                            "COALESCE(c.embedded, 0) AS embedded, COALESCE(c.behind, 0) AS behind "
                            "FROM embedding_models m LEFT JOIN ("
                            "  SELECT e.model_id, count(*) AS embedded, "
                            "         count(*) FILTER (WHERE e.synced_version < i.payload_version) AS behind "
                            "  FROM image_embeddings e JOIN images i ON i.id = e.image_id GROUP BY e.model_id"
                            ") c ON c.model_id = m.id ORDER BY m.id"
                        )
                    ).mappings().fetchall()
            return {"images": images, "models": [_model_row(row) for row in rows]}
        except Exception as exc:
            raise err.index_postgres_failed(f"list_embedding_models: {type(exc).__name__}: {exc}") from exc

    def list_embedding_model_rows(self, *, statuses: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        """各版本的行（不带计数），最早的在前；给了 statuses 时只返回这些状态。"""
        self.ensure_schema()
        from sqlalchemy import text

        try:
            with self._get_engine().connect() as conn:
                rows = conn.execute(
                    text(
                        f"SELECT {_MODEL_COLUMNS} FROM embedding_models m "
                        "WHERE CAST(:statuses AS text[]) IS NULL OR m.status = ANY(CAST(:statuses AS text[])) "
                        "ORDER BY m.id"
                    ),
                    {"statuses": [ModelStatus(s).value for s in statuses] if statuses is not None else None},
                ).mappings().fetchall()
            return [_model_row(row) for row in rows]
        except Exception as exc:
            raise err.index_postgres_failed(f"list_embedding_model_rows: {type(exc).__name__}: {exc}") from exc

    def get_embedding_model(self, model_id: int) -> dict[str, Any] | None:
        self.ensure_schema()
        from sqlalchemy import text

        try:
            with self._get_engine().connect() as conn:
                row = conn.execute(
                    text(f"SELECT {_MODEL_COLUMNS} FROM embedding_models m WHERE m.id = :id"), {"id": int(model_id)}
                ).mappings().fetchone()
            return _model_row(row) if row is not None else None
        except Exception as exc:
            raise err.index_postgres_failed(f"get_embedding_model: {type(exc).__name__}: {exc}") from exc

    def register_embedding_model(self, fields: dict[str, Any]) -> dict[str, Any]:
        """新的模型版本，状态为 building：{name, version, dim, distance, input_size,
        preprocess, weights_uri, collection, fingerprint}。名称 + 版本或 collection 已被占用时
        为 EMBEDDING_MODEL_EXISTS。
        """
        self.ensure_schema()
        from sqlalchemy import text

        try:
            with self._get_engine().begin() as conn:
                row = conn.execute(
                    text(
                        "INSERT INTO embedding_models AS m (name, version, dim, distance, input_size, preprocess, "
                        "weights_uri, collection, status, fingerprint) VALUES (:name, :version, :dim, :distance, "
                        ":input_size, CAST(:preprocess AS jsonb), :weights_uri, :collection, :status, "
                        f"CAST(:fingerprint AS jsonb)) RETURNING {_MODEL_COLUMNS}"
                    ),
                    {
                        "name": str(fields["name"]),
                        "version": str(fields["version"]),
                        "dim": int(fields["dim"]),
                        "distance": str(fields["distance"]),
                        "input_size": int(fields["input_size"]),
                        "preprocess": json.dumps(fields.get("preprocess") or {}),
                        "weights_uri": str(fields["weights_uri"]),
                        "collection": str(fields["collection"]),
                        "status": ModelStatus.BUILDING.value,
                        "fingerprint": json.dumps(fields["fingerprint"]),
                    },
                ).mappings().fetchone()
            return _model_row(row)
        except IntegrityError as exc:
            raise _registry_error("register_embedding_model", exc) from exc
        except Exception as exc:
            raise err.index_postgres_failed(f"register_embedding_model: {type(exc).__name__}: {exc}") from exc

    def record_model_fingerprint(self, model_id: int, fingerprint: dict[str, Any]) -> bool:
        """为有指纹之前登记的版本记录指纹；从不覆盖已有的指纹。这一行已经有指纹（或已经不在）
        时返回 False。
        """
        self.ensure_schema()
        from sqlalchemy import text

        try:
            with self._get_engine().begin() as conn:
                row = conn.execute(
                    text(
                        "UPDATE embedding_models SET fingerprint = CAST(:fp AS jsonb) "
                        "WHERE id = :id AND fingerprint IS NULL RETURNING id"
                    ),
                    {"id": int(model_id), "fp": json.dumps(fingerprint)},
                ).fetchone()
            return row is not None
        except Exception as exc:
            raise err.index_postgres_failed(f"record_model_fingerprint: {type(exc).__name__}: {exc}") from exc

    def set_embedding_model_status(self, model_id: int, status: str, *, expected: tuple[str, ...]) -> bool:
        """版本处于 expected 之一时把它改成 status；不是时返回 False。"""
        self.ensure_schema()
        from sqlalchemy import text

        try:
            with self._get_engine().begin() as conn:
                row = conn.execute(
                    text(
                        "UPDATE embedding_models SET status = :to WHERE id = :id "
                        "AND status = ANY(CAST(:expected AS text[])) RETURNING id"
                    ),
                    {
                        "id": int(model_id),
                        "to": ModelStatus(status).value,
                        "expected": [ModelStatus(s).value for s in expected],
                    },
                ).fetchone()
            return row is not None
        except IntegrityError as exc:
            raise _registry_error("set_embedding_model_status", exc) from exc
        except Exception as exc:
            raise err.index_postgres_failed(f"set_embedding_model_status: {type(exc).__name__}: {exc}") from exc

    def activate_embedding_model(self, model_id: int) -> dict[str, Any]:
        """在一个事务里让一个 building / ready 版本成为当前版本：之前的当前版本回到 ready
        （为回滚保留）。返回 {model, previous_id}（这个版本本来就是当前版本，或者之前没有
        当前版本时，previous_id 为 None）。

        EMBEDDING_MODEL_NOT_FOUND、EMBEDDING_MODEL_STATE（已下线）。
        """
        self.ensure_schema()
        from sqlalchemy import text

        try:
            with self._get_engine().begin() as conn:
                rows = conn.execute(
                    text("SELECT id, status FROM embedding_models WHERE id = :id OR status = :current FOR UPDATE"),
                    {"id": int(model_id), "current": ModelStatus.CURRENT.value},
                ).mappings().fetchall()
                target = next((r for r in rows if int(r["id"]) == int(model_id)), None)
                if target is None:
                    raise err.embedding_model_not_found(f"embedding model {model_id} does not exist")
                if target["status"] == ModelStatus.RETIRED.value:
                    raise err.embedding_model_state(f"embedding model {model_id} is retired")
                previous = None
                if target["status"] != ModelStatus.CURRENT.value:
                    previous = next((int(r["id"]) for r in rows if r["status"] == ModelStatus.CURRENT.value), None)
                    conn.execute(
                        text("UPDATE embedding_models SET status = :ready WHERE status = :current"),
                        {"ready": ModelStatus.READY.value, "current": ModelStatus.CURRENT.value},
                    )
                    conn.execute(
                        text("UPDATE embedding_models SET status = :current, activated_at = now() WHERE id = :id"),
                        {"id": int(model_id), "current": ModelStatus.CURRENT.value},
                    )
                row = conn.execute(
                    text(f"SELECT {_MODEL_COLUMNS} FROM embedding_models m WHERE m.id = :id"), {"id": int(model_id)}
                ).mappings().fetchone()
            return {"model": _model_row(row), "previous_id": previous}
        except err.AppError:
            raise
        except IntegrityError as exc:
            raise _registry_error("activate_embedding_model", exc) from exc
        except Exception as exc:
            raise err.index_postgres_failed(f"activate_embedding_model: {type(exc).__name__}: {exc}") from exc

    def retire_embedding_model(self, model_id: int) -> dict[str, Any]:
        """停用一个非当前版本：删除它的台账行（collection 由调用方删除），状态变为 retired。
        返回 {model, ledger_rows}。

        EMBEDDING_MODEL_NOT_FOUND、EMBEDDING_MODEL_STATE（当前版本）。
        """
        self.ensure_schema()
        from sqlalchemy import text

        try:
            with self._get_engine().begin() as conn:
                found = conn.execute(
                    text("SELECT status FROM embedding_models WHERE id = :id FOR UPDATE"), {"id": int(model_id)}
                ).fetchone()
                if found is None:
                    raise err.embedding_model_not_found(f"embedding model {model_id} does not exist")
                if found[0] == ModelStatus.CURRENT.value:
                    raise err.embedding_model_state("the current model version cannot be retired; switch to another first")
                deleted = conn.execute(
                    text("DELETE FROM image_embeddings WHERE model_id = :id"), {"id": int(model_id)}
                ).rowcount
                conn.execute(
                    text("UPDATE embedding_models SET status = :retired WHERE id = :id"),
                    {"id": int(model_id), "retired": ModelStatus.RETIRED.value},
                )
                row = conn.execute(
                    text(f"SELECT {_MODEL_COLUMNS} FROM embedding_models m WHERE m.id = :id"), {"id": int(model_id)}
                ).mappings().fetchone()
            return {"model": _model_row(row), "ledger_rows": int(deleted or 0)}
        except err.AppError:
            raise
        except Exception as exc:
            raise err.index_postgres_failed(f"retire_embedding_model: {type(exc).__name__}: {exc}") from exc

    def count_embedding_backlog(self, model_id: int) -> int:
        """有多少已存图片在 model_id 的 collection 里没有向量（没有台账行）。"""
        self.ensure_schema()
        from sqlalchemy import text

        try:
            with self._get_engine().connect() as conn:
                return int(conn.execute(
                    text(
                        "SELECT count(*) FROM images i WHERE NOT EXISTS "
                        "(SELECT 1 FROM image_embeddings e WHERE e.model_id = :mid AND e.image_id = i.id)"
                    ),
                    {"mid": int(model_id)},
                ).scalar_one())
        except Exception as exc:
            raise err.index_postgres_failed(f"count_embedding_backlog: {type(exc).__name__}: {exc}") from exc

    def list_embedding_backlog(
        self,
        model_id: int,
        *,
        after: UUID | str | None = None,
        limit: int = 16,
        exclude: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """在 model_id 下没有台账行的图片，按 id 顺序在 `after` 之后，跳过 exclude 里的 id
        （本次运行已经试过的）：[{id, original_url}]。
        """
        self.ensure_schema()
        from sqlalchemy import text

        try:
            with self._get_engine().connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT i.id::text AS id, i.original_url FROM images i "
                        "WHERE NOT EXISTS (SELECT 1 FROM image_embeddings e WHERE e.model_id = :mid AND e.image_id = i.id) "
                        "AND (CAST(:after AS uuid) IS NULL OR i.id > CAST(:after AS uuid)) "
                        "AND NOT (i.id = ANY(CAST(:exclude AS uuid[]))) "
                        "ORDER BY i.id LIMIT :lim"
                    ),
                    {
                        "mid": int(model_id),
                        "after": _as_uuid(after) if after is not None else None,
                        "exclude": [_as_uuid(i) for i in exclude or []],
                        "lim": max(1, int(limit)),
                    },
                ).mappings().fetchall()
            return [dict(row) for row in rows]
        except Exception as exc:
            raise err.index_postgres_failed(f"list_embedding_backlog: {type(exc).__name__}: {exc}") from exc

    def record_embeddings(self, *, model_id: int, versions: dict[str, int]) -> list[str]:
        """台账：这些图片现在在 model_id 的 collection 里有向量了，写入时用的是给定版本的
        payload（embedded_at = now）。按 id 顺序返回记录了的 id；这期间被删除的图片不记录
        （由调用方删除它的向量点）。

        和 mark_payloads_pushed 不同，即使图片之后又变了也会记录这个版本：向量已经在了，
        payload 由一次推送补上。
        """
        if not versions:
            return []
        self.ensure_schema()
        from sqlalchemy import text

        statement = text(
            "INSERT INTO image_embeddings (model_id, image_id, synced_version, embedded_at) "
            "SELECT :mid, d.image_id, d.version, now() "
            "FROM unnest(CAST(:ids AS uuid[]), CAST(:versions AS bigint[])) AS d(image_id, version) "
            "JOIN images i ON i.id = d.image_id "
            "ON CONFLICT (model_id, image_id) DO UPDATE SET synced_version = EXCLUDED.synced_version, "
            "embedded_at = EXCLUDED.embedded_at RETURNING image_id::text"
        )

        def run(items: list[tuple[str, int]]) -> list[str]:
            with self._get_engine().begin() as conn:
                rows = conn.execute(statement, {
                    "mid": int(model_id),
                    "ids": [_as_uuid(image_id) for image_id, _ in items],
                    "versions": [int(version) for _, version in items],
                }).fetchall()
            return [str(row[0]) for row in rows]

        items = sorted(versions.items())
        try:
            try:
                return sorted(run(items))
            except IntegrityError:
                # 在 join 和插入之间有图片没了：逐个记录其他图片
                recorded: list[str] = []
                for item in items:
                    try:
                        recorded.extend(run([item]))
                    except IntegrityError:
                        continue
                return sorted(recorded)
        except Exception as exc:
            raise err.index_postgres_failed(f"record_embeddings: {type(exc).__name__}: {exc}") from exc

    def list_embedded_ids(self, model_id: int, *, after: UUID | str | None = None, limit: int = 256) -> list[str]:
        """在 model_id 下有台账行的图片 id，按 id 顺序在 `after` 之后（用来检查它们的向量点
        是不是真的在 collection 里）。
        """
        self.ensure_schema()
        from sqlalchemy import text

        try:
            with self._get_engine().connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT e.image_id::text FROM image_embeddings e WHERE e.model_id = :mid "
                        "AND (CAST(:after AS uuid) IS NULL OR e.image_id > CAST(:after AS uuid)) "
                        "ORDER BY e.image_id LIMIT :lim"
                    ),
                    {"mid": int(model_id), "after": _as_uuid(after) if after is not None else None,
                     "lim": max(1, int(limit))},
                ).fetchall()
            return [str(row[0]) for row in rows]
        except Exception as exc:
            raise err.index_postgres_failed(f"list_embedded_ids: {type(exc).__name__}: {exc}") from exc

    def forget_embeddings(self, model_id: int, image_ids: list[str]) -> int:
        """删除 model_id 的这些台账行（它们的向量点不在 collection 里）：这些图片重新被列为
        没有向量。返回删除了多少行。
        """
        if not image_ids:
            return 0
        self.ensure_schema()
        from sqlalchemy import text

        try:
            with self._get_engine().begin() as conn:
                return int(conn.execute(
                    text("DELETE FROM image_embeddings WHERE model_id = :mid AND image_id = ANY(CAST(:ids AS uuid[]))"),
                    {"mid": int(model_id), "ids": [_as_uuid(i) for i in image_ids]},
                ).rowcount or 0)
        except Exception as exc:
            raise err.index_postgres_failed(f"forget_embeddings: {type(exc).__name__}: {exc}") from exc

    def clear_embeddings(self, model_id: int) -> int:
        """删除 model_id 的所有台账行（发现它的 collection 不见了，重新建了一个）。"""
        self.ensure_schema()
        from sqlalchemy import text

        try:
            with self._get_engine().begin() as conn:
                return int(conn.execute(
                    text("DELETE FROM image_embeddings WHERE model_id = :mid"), {"mid": int(model_id)}
                ).rowcount or 0)
        except Exception as exc:
            raise err.index_postgres_failed(f"clear_embeddings: {type(exc).__name__}: {exc}") from exc

    def refresh_model_fingerprint(self, model_id: int, fingerprint: dict[str, Any]) -> bool:
        """在另一个路径下（或者现在带着文件哈希）重新找到了这个版本的编码器：记录新的指纹和
        它的 weights_uri。调用方已经确认是同一个编码器。
        """
        self.ensure_schema()
        from sqlalchemy import text

        try:
            with self._get_engine().begin() as conn:
                row = conn.execute(
                    text(
                        "UPDATE embedding_models SET fingerprint = CAST(:fp AS jsonb), weights_uri = :uri "
                        "WHERE id = :id RETURNING id"
                    ),
                    {"id": int(model_id), "fp": json.dumps(fingerprint),
                     "uri": str((fingerprint.get("spec") or {}).get("model_path") or "")},
                ).fetchone()
            return row is not None
        except Exception as exc:
            raise err.index_postgres_failed(f"refresh_model_fingerprint: {type(exc).__name__}: {exc}") from exc

    def sample_embedded_images(self, model_id: int, *, limit: int = 3) -> list[dict[str, Any]]:
        """最近记录在 model_id 的 collection 里的图片：[{id, original_url}]（用来检查 collection
        里的向量是否来自某个编码器）。
        """
        self.ensure_schema()
        from sqlalchemy import text

        try:
            with self._get_engine().connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT i.id::text AS id, i.original_url FROM image_embeddings e "
                        "JOIN images i ON i.id = e.image_id WHERE e.model_id = :mid "
                        "ORDER BY e.embedded_at DESC, i.id LIMIT :lim"
                    ),
                    {"mid": int(model_id), "lim": max(1, int(limit))},
                ).mappings().fetchall()
            return [dict(row) for row in rows]
        except Exception as exc:
            raise err.index_postgres_failed(f"sample_embedded_images: {type(exc).__name__}: {exc}") from exc
