"""embedding_models (the model versions, one Qdrant collection each) and the image_embeddings
ledger (which images have a vector in which version's collection)."""

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
    """Record one embedding on the caller's connection and transaction."""
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
    """embedding_models current row + image_embeddings ledger."""

    def get_current_embedding_model(self) -> dict[str, Any]:
        """Return the single status='current' embedding_models row.

        Raises clearly if none — seed expectation (M2 empty rebuild):
          INSERT INTO embedding_models
            (name, version, dim, distance, input_size, preprocess, weights_uri,
             collection, status, activated_at)
          VALUES (..., 'cosine', ..., '{}', '...', '<collection>', 'current', now());
        Partial unique index enforces at most one current.
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
        # Plain str for QdrantStore (cosine/dot/euclid); ck_embedding_models_distance
        # guarantees a valid value
        out["distance"] = Distance(str(out.get("distance") or Distance.COSINE).lower()).value
        return out

    def upsert_image_embedding(
        self,
        *,
        model_id: int,
        image_id: UUID | str,
        synced_version: int | None = None,
    ) -> None:
        """After successful Qdrant upsert: sync image_embeddings ledger.

        If synced_version is None, read images.payload_version for image_id.
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
        """What the Qdrant payload of these images is built from (contract §4.2), in id order:
        {id, payload_version, category_id, label_status, food_state, scenes}. Missing ids are left out."""
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
        """Ledger: these payload versions are now in model_id's collection. Returns the image ids
        recorded, in id order.

        A version is recorded only while it is still the image's payload_version: when the image
        changed (or was deleted) after its payload was read, the payload just written may be
        older than what another writer pushed, so the caller reads and pushes it again
        (services.retrieval.sync_scenes) instead of the ledger claiming it is current.

        embedded_at stays as it was for images already in the ledger (only the payload moved);
        an image missing from it (its ledger write failed at index time) gets a row now.
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
        """Images whose current-model ledger row (model_id's when given) is behind their
        payload_version, in id order: after `after` (exclusive) and up to `until` (inclusive)
        when given.

        Only images with a ledger row: one without needs a vector, which a payload push cannot
        give (list_images(out_of_sync=True) shows both kinds).
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

    # -- model versions (/api/embedding-models) -------------------------------------

    def list_embedding_models(self) -> dict[str, Any]:
        """{images, models}: how many images are stored, and every model version (oldest first)
        with embedded (ledger rows: images with a vector in its collection) and behind (ledger
        rows older than the image's payload_version). One snapshot, so embedded <= images."""
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
        """The version rows (without counts), oldest first; only these statuses when given."""
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
        """A new model version, status building: {name, version, dim, distance, input_size,
        preprocess, weights_uri, collection, fingerprint}. EMBEDDING_MODEL_EXISTS when the name +
        version or the collection is taken."""
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
        """Record the fingerprint of a version registered before fingerprints existed; never
        overwrites one. False when the row already had one (or is gone)."""
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
        """Move a version to status while it is in one of expected; False when it was not."""
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
        """Make a building / ready version the current one, in one transaction: the previous
        current one goes back to ready (kept for a roll back). Returns {model, previous_id}
        (previous_id None when the version already was current, or there was none).

        EMBEDDING_MODEL_NOT_FOUND, EMBEDDING_MODEL_STATE (retired).
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
        """Take a version that is not current out of use: its ledger rows go (the caller drops
        its collection) and its status becomes retired. Returns {model, ledger_rows}.

        EMBEDDING_MODEL_NOT_FOUND, EMBEDDING_MODEL_STATE (the current version).
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
        """How many stored images have no vector in model_id's collection (no ledger row)."""
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
        """Images with no ledger row for model_id, in id order after `after`, leaving out the
        ids in exclude (already tried by this run): [{id, original_url}]."""
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
        """Ledger: these images now have a vector in model_id's collection, written with the
        payload of the given version (embedded_at = now). Returns the ids recorded, in id order;
        an image deleted meanwhile is left out (the caller removes its point).

        Unlike mark_payloads_pushed the version is recorded even when the image moved on since:
        the vector is there, and a payload push catches the payload up."""
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
                # an image went away between the join and the insert: record the others one by one
                recorded: list[str] = []
                for item in items:
                    try:
                        recorded.extend(run([item]))
                    except IntegrityError:
                        continue
                return sorted(recorded)
        except Exception as exc:
            raise err.index_postgres_failed(f"record_embeddings: {type(exc).__name__}: {exc}") from exc

    def sample_embedded_images(self, model_id: int, *, limit: int = 3) -> list[dict[str, Any]]:
        """The images last recorded in model_id's collection: [{id, original_url}] (to check that
        the collection's vectors come from a given encoder)."""
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
