"""Model versions in the API process: what /api/embedding-models calls.

Changing the embedding model is a sequence, and this is where it runs:

1. register: the configured encoder (EMBEDDING_CONFIG, the one predict uses) becomes a new version
   (status building) with its own, empty collection. Search and index stay on the current one.
2. build: a background thread first checks the ledger against the collection's real points
   (verify_ledger: rows whose point is gone are forgotten), then embeds every stored image's
   original that has no vector yet with the version's encoder (services.retrieval
   .build_embeddings), then catches up payloads that changed meanwhile. The ledger is the
   progress: stopping and starting again goes on where it stopped. A build that leaves no image
   behind marks the version ready. The encoder is the configured one, the one search holds, or
   the version's own loaded again from the weights its fingerprint names (still in the image).
3. activate: checks the target's ledger against its points, builds its retrieval stack (its
   encoder, its collection), then closes the vector write gate (app.bootstrap.write_gate: new
   index requests wait, the ones in flight finish), embeds what the target still lacks (images
   indexed since its build, at most FINAL_CATCHUP_MAX), makes it current in PostgreSQL (the
   previous one goes back to ready), swaps the stack in and opens the gate again: no image
   written meanwhile can miss the new collection. The old stack is closed a little later, after
   requests that still hold it. A roll back is the same call on the previous version.
4. retire: a version that is not current loses its collection and its ledger rows.

It works while the retrieval stack is down: a changed EMBEDDING_CONFIG whose current version
cannot be paired (bootstrap.retrieval_stack.pair_encoder) is fixed by registering, building and
switching to the configured encoder's version. Needs the catalog's PostgreSQL store, QDRANT_CONFIG
and the object storage; one build at a time per process, and a PostgreSQL advisory lock per
version keeps two processes from building the same one.

Other API processes do not follow a switch made here: they keep their stack until restarted
(the API image runs one worker process).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.clients.inference import weights_status
from app.clients.qdrant import QdrantStore
from app.core import errors as err
from app.core.enums import ModelStatus
from app.repositories.vectors import QdrantVectorStore
from app.services.retrieval.active_model import ActiveModel
from app.services.retrieval.build_embeddings import BUILD_BATCH, BuildEmbeddingsUseCase, verify_ledger
from app.services.retrieval.model_versions import (
    artifact_of,
    collection_name,
    default_name,
    default_version,
    fingerprint_dim,
    label,
    registration_fields,
    require_activatable,
    require_buildable,
    require_retirable,
    same_encoder,
)

if TYPE_CHECKING:
    from app.bootstrap.state import ApplicationState

logger = logging.getLogger("model_registry")

# pg_try_advisory_lock key of a build: this base + the version id ("EMB" 0)
BUILD_LOCK_KEY_BASE = 0x454D4200
# A new pass picks up images stored while the previous one ran
MAX_PASSES = 3
# This many images in a row failing with the same code stops the build (storage down, ...)
FAILURE_STREAK_LIMIT = 25
# Failures kept for the page (the count covers all)
MAX_FAILURES_SHOWN = 50
# Requests that took the old stack just before a switch finish on it for this long
RETIRED_STACK_GRACE_SECONDS = 30.0
# A switch embeds at most this many missing images itself, with index requests held back; a
# version further behind needs a build first (or allow_missing)
FINAL_CATCHUP_MAX = 200


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class _Halt(Exception):
    """The build stops on its own: the message is what the page shows."""


@dataclass
class BuildState:
    """One build of this process. state: running / stopping / done / stopped / failed."""

    model_id: int
    state: str
    started_at: str
    finished_at: str | None = None
    verified: int = 0  # ledger rows checked against the collection's points
    repaired: int = 0  # of them, rows whose point was gone (embedded again by this build)
    embedded: int = 0
    failed: int = 0
    gone: int = 0
    payloads: int = 0
    passes: int = 0
    remaining: int | None = None
    error: str | None = None
    failures: list[dict[str, Any]] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.state in ("running", "stopping")


class ModelRegistry:
    def __init__(self, runtime: "ApplicationState") -> None:
        self._runtime = runtime
        self._lock = threading.RLock()  # build start / stop / status
        self._switch_lock = threading.Lock()  # one activation or retirement at a time
        self._client: Any = None
        self._build: BuildState | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._timers: list[threading.Timer] = []

    # -- what it works with -------------------------------------------------------

    def _store(self) -> Any:
        try:
            return self._runtime.require_catalog().store
        except RuntimeError as exc:
            raise err.not_ready(f"catalog (PostgreSQL) is not ready: {exc}") from exc

    def _configured(self) -> Any:
        return self._runtime.embedder

    def _connection(self) -> Any:
        from app.bootstrap.retrieval_stack import _qdrant_connection

        return _qdrant_connection(self._runtime.config)

    def _vectors(self, row: dict[str, Any]) -> QdrantVectorStore:
        """The version's collection over this registry's own client (it outlives stack swaps)."""
        with self._lock:
            if self._client is None:
                try:
                    self._client = self._connection().create_client()
                except ValueError as exc:
                    raise err.not_ready(str(exc)) from exc
            client = self._client
        return QdrantVectorStore(QdrantStore(
            self._connection(), collection=str(row["collection"]), vector_dim=int(row["dim"]),
            distance=str(row["distance"]), client=client,
        ))

    def _storage(self) -> Any:
        stack = self._runtime.retrieval
        if stack is not None:
            return stack.infra.storage
        try:
            return self._runtime.require_catalog().file_storage()
        except RuntimeError as exc:
            raise err.not_ready(f"object storage is not available: {exc}") from exc
        except ValueError as exc:
            raise err.not_ready(f"IMAGE_STORAGE_CONFIG: {exc}") from exc

    @staticmethod
    def _fingerprint(encoder: Any) -> dict[str, Any] | None:
        if encoder is None:
            return None
        try:
            return encoder.fingerprint()
        except Exception:
            logger.exception("编码器指纹计算失败")
            return None

    def _encoder_in_memory(self, row: dict[str, Any]) -> tuple[str, Any] | None:
        """("configured" | "serving", encoder) already loaded for this version, else None."""
        fingerprint = row.get("fingerprint")
        if fingerprint is None:
            return None
        configured = self._configured()
        if same_encoder(fingerprint, self._fingerprint(configured)):
            return "configured", configured
        stack = self._runtime.retrieval
        if stack is not None and same_encoder(fingerprint, self._fingerprint(stack.embedding)):
            return "serving", stack.embedding
        return None

    def _open_collection(self, row: dict[str, Any], store: Any) -> QdrantVectorStore:
        """The version's collection, made anew when it is gone; then its ledger rows are void."""
        vectors = self._vectors(row)
        existed = vectors.collection_exists()
        vectors.ensure_collection(recreate=False)
        if not existed:
            forgotten = store.clear_embeddings(int(row["id"]))
            if forgotten:
                logger.warning("collection=%s 不存在，已新建；%d 条写入记录作废", row["collection"], forgotten)
        return vectors

    def _encoder_for_build(self, row: dict[str, Any]) -> tuple[str, Any]:
        """The version's encoder: in memory, else loaded again from the weights its fingerprint
        names (EMBEDDING_ENCODER_UNAVAILABLE when they are gone or no longer the same)."""
        found = self._encoder_in_memory(row)
        if found is not None:
            return found
        fingerprint = row.get("fingerprint")
        spec = dict((fingerprint or {}).get("spec") or {})
        status = weights_status(fingerprint)
        if fingerprint is None or not spec.get("model_path") or status in ("missing", "changed"):
            reason = {"missing": "are not in this image", "changed": "were replaced by other files"}.get(
                str(status), "were never recorded"
            )
            raise err.embedding_encoder_unavailable(
                f"the weights of {row['name']}:{row['version']} ({row['weights_uri']}) {reason}; keep each version's "
                "weights in their own folder of the image until it is retired"
            )
        from app.bootstrap.retrieval_stack import load_encoder_from_spec

        try:
            encoder = load_encoder_from_spec(self._runtime.config)(spec)
        except Exception as exc:
            raise err.embedding_encoder_unavailable(
                f"the weights of {row['name']}:{row['version']} could not be loaded from {spec.get('model_path')}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if not same_encoder(fingerprint, self._fingerprint(encoder)):
            raise err.embedding_encoder_unavailable(
                f"the weights at {spec.get('model_path')} are no longer the encoder of {row['name']}:{row['version']}"
            )
        return "loaded", encoder

    # -- reading --------------------------------------------------------------------

    def build_status(self) -> dict[str, Any] | None:
        with self._lock:
            return asdict(self._build) if self._build is not None else None

    def overview(self) -> dict[str, Any]:
        """Every version with its coverage, the configured encoder and what search is paired with."""
        store = self._store()
        data = store.list_embedding_models()
        images = int(data["images"])
        configured = self._configured()
        configured_fp = self._fingerprint(configured)
        stack = self._runtime.retrieval
        serving_fp = self._fingerprint(stack.embedding) if stack is not None else None
        models = []
        configured_id = None
        for row in data["models"]:
            fingerprint = row.get("fingerprint")
            is_configured = same_encoder(fingerprint, configured_fp)
            is_serving_encoder = same_encoder(fingerprint, serving_fp)
            if is_configured and row["status"] != ModelStatus.RETIRED.value and configured_id is None:
                configured_id = int(row["id"])
            embedded = int(row.get("embedded") or 0)
            live = row["status"] != ModelStatus.RETIRED.value
            points = None
            if live:
                try:
                    points = self._vectors(row).point_count()
                except Exception:
                    points = None  # unreachable, or no collection (a build makes it again)
            models.append({
                "id": int(row["id"]),
                "name": row["name"],
                "version": row["version"],
                "status": row["status"],
                "collection": row["collection"],
                "dim": int(row["dim"]),
                "distance": row["distance"],
                "weights_uri": row["weights_uri"],
                "created_at": row.get("created_at"),
                "activated_at": row.get("activated_at"),
                "embedded": embedded,
                "missing": max(0, images - embedded) if row["status"] != ModelStatus.RETIRED.value else 0,
                "behind": int(row.get("behind") or 0),
                "points": points,
                "weights": weights_status(fingerprint) if live else None,
                "artifact": artifact_of(fingerprint),
                "fingerprint": fingerprint is not None,
                "configured": is_configured,
                "serving": stack is not None and stack.model.id == int(row["id"]),
                "encoder": (
                    "configured" if is_configured
                    else "serving" if is_serving_encoder
                    else "recorded" if fingerprint is not None
                    else None
                ),
            })
        own = stack is not None and configured is not None and stack.embedding is not configured
        return {
            "images": images,
            "configured": {
                "loaded": configured is not None,
                "model_path": getattr(configured, "model_path", None) if configured is not None else None,
                "dim": fingerprint_dim(configured_fp),
                "artifact": artifact_of(configured_fp),
                "model_id": configured_id,
                "detail": None if configured is not None else self._runtime.initialization_error,
            },
            "retrieval": {
                "ready": stack is not None,
                "model_id": stack.model.id if stack is not None else None,
                "encoder": None if stack is None else "own" if own else "configured",
                "code": None if stack is not None else self._runtime.retrieval_error_code,
                "detail": None if stack is not None else self._runtime.retrieval_error,
            },
            "build": self.build_status(),
            "switch_catch_up_max": FINAL_CATCHUP_MAX,
            "models": models,
        }

    def item(self, model_id: int) -> dict[str, Any]:
        found = next((m for m in self.overview()["models"] if m["id"] == int(model_id)), None)
        if found is None:
            raise err.embedding_model_not_found(f"embedding model {model_id} does not exist")
        return found

    # -- register ---------------------------------------------------------------------

    def register(self, *, name: str | None = None, version: str | None = None) -> dict[str, Any]:
        """The configured encoder as a new version (building) with a new, empty collection."""
        configured = self._configured()
        fingerprint = self._fingerprint(configured)
        if fingerprint is None:
            raise err.not_ready(
                f"the configured encoder (EMBEDDING_CONFIG) is not loaded: {self._runtime.initialization_error or 'unknown'}"
            )
        store = self._store()
        rows = store.list_embedding_model_rows()
        for row in rows:
            if row["status"] != ModelStatus.RETIRED.value and same_encoder(row.get("fingerprint"), fingerprint):
                raise err.embedding_model_exists(
                    f"the configured encoder already is version {row['name']}:{row['version']} (id {row['id']}, "
                    f"{row['status']})"
                )
        name = label(name if name not in (None, "") else default_name(fingerprint["spec"].get("model_path")), "name")
        version = label(version if version not in (None, "") else default_version(datetime.now()), "version")
        taken = {str(row["collection"]) for row in rows}
        while True:
            collection = collection_name(name, version, taken)
            fields = registration_fields(name=name, version=version, collection=collection, fingerprint=fingerprint)
            vectors = self._vectors(fields)
            if not vectors.collection_exists():  # never reuse points someone else left in Qdrant
                break
            taken.add(collection)
        vectors.ensure_collection(recreate=False)
        try:
            row = store.register_embedding_model(fields)
        except BaseException:
            try:
                vectors.drop_collection()
            except Exception:
                logger.exception("登记失败后删除空 collection=%s 也失败", collection)
            raise
        logger.info("已登记模型版本 %s:%s | id=%s | collection=%s | dim=%s", name, version, row["id"], collection, fields["dim"])
        return row

    # -- build ------------------------------------------------------------------------

    def start_build(self, model_id: int) -> dict[str, Any]:
        store = self._store()
        with self._lock:
            if self._build is not None and self._build.active:
                raise err.embedding_build_running(
                    f"embedding model {self._build.model_id} is being built; stop it or wait for it to finish"
                )
            row = store.get_embedding_model(model_id)
            if row is None:
                raise err.embedding_model_not_found(f"embedding model {model_id} does not exist")
            require_buildable(row)
            found = self._encoder_for_build(row)
            vectors = self._open_collection(row, store)  # made again if it was dropped by hand
            use_case = BuildEmbeddingsUseCase(
                model=ActiveModel.from_row(row), encoder=found[1], vectors=vectors,
                metadata=store, models=store, storage=self._storage(),
            )
            self._stop.clear()
            self._build = BuildState(model_id=int(row["id"]), state="running", started_at=_now())
            self._thread = threading.Thread(
                target=self._run_build, args=(use_case, vectors, store, self._build),
                name=f"embedding-build-{row['id']}", daemon=True,
            )
            self._thread.start()
            logger.info("开始生成向量 | model=%s:%s | collection=%s | encoder=%s", row["name"], row["version"], row["collection"], found[0])
            return asdict(self._build)

    def stop_build(self, model_id: int) -> dict[str, Any] | None:
        with self._lock:
            if self._build is not None and self._build.model_id == int(model_id) and self._build.active:
                self._stop.set()
                self._build.state = "stopping"
            return self.build_status()

    def _stopping(self) -> bool:
        return self._stop.is_set() or self._runtime.stopping

    def _record(self, build: BuildState, **changes: Any) -> None:
        with self._lock:
            for key, value in changes.items():
                setattr(build, key, value)

    def _run_build(self, use_case: BuildEmbeddingsUseCase, vectors: Any, store: Any, state: BuildState) -> None:
        model = use_case.model
        try:
            with store.try_maintenance_lock(BUILD_LOCK_KEY_BASE + model.id) as held:
                if not held:
                    raise _Halt(f"EMBEDDING_BUILD_RUNNING: another process is building {model.space_id}")
                check = verify_ledger(model.id, vectors, store, should_stop=self._stopping)
                self._record(state, verified=check.checked, repaired=check.forgotten)
                self._passes(use_case, state)
                if self._stopping():
                    self._record(state, state="stopped")
                    return
                row = store.get_embedding_model(model.id) or {}
                if row.get("status") != ModelStatus.CURRENT.value:  # the reconciler catches the current one up
                    self._record(state, payloads=use_case.push_payloads(should_stop=self._stopping))
                remaining = store.count_embedding_backlog(model.id)
                if remaining == 0 and row.get("status") == ModelStatus.BUILDING.value:
                    store.set_embedding_model_status(model.id, ModelStatus.READY.value, expected=(ModelStatus.BUILDING.value,))
                self._record(state, state="stopped" if self._stopping() else "done", remaining=remaining)
        except _Halt as exc:
            logger.warning("生成向量中止 | model=%s | %s", model.space_id, exc)
            self._record(state, state="failed", error=str(exc))
        except err.AppError as exc:
            logger.warning("生成向量中止 | model=%s | %s: %s", model.space_id, exc.code, exc.detail)
            self._record(state, state="failed", error=f"{exc.code}: {err.client_detail(exc)}")
        except Exception as exc:
            logger.exception("生成向量失败 | model=%s", model.space_id)
            self._record(state, state="failed", error=f"INTERNAL_ERROR: {type(exc).__name__}")
        finally:
            self._record(state, finished_at=_now())
            logger.info(
                "生成向量结束 | model=%s | state=%s | verified=%d | repaired=%d | embedded=%d | failed=%d | gone=%d | payloads=%d | remaining=%s",
                model.space_id, state.state, state.verified, state.repaired, state.embedded, state.failed, state.gone,
                state.payloads, state.remaining,
            )

    def _passes(self, use_case: BuildEmbeddingsUseCase, state: BuildState) -> None:
        """Pass after pass over the images still without a vector, each tried once per build."""
        tried: list[str] = []
        streak_code, streak = None, 0
        for number in range(1, MAX_PASSES + 1):
            self._record(state, passes=number)
            after, progressed = None, False
            while not self._stopping():
                batch = use_case.run_batch(after=after, limit=BUILD_BATCH, exclude=tried)
                failed_ids = [f["image_id"] for f in batch.failures]
                tried.extend(failed_ids)
                with self._lock:
                    state.embedded += len(batch.embedded)
                    state.gone += len(batch.gone)
                    state.failed += len(batch.failures)
                    room = MAX_FAILURES_SHOWN - len(state.failures)
                    if room > 0:
                        state.failures.extend(batch.failures[:room])
                if batch.embedded:
                    progressed, streak_code, streak = True, None, 0
                for failure in batch.failures:
                    streak = streak + 1 if failure["code"] == streak_code else 1
                    streak_code = failure["code"]
                if streak >= FAILURE_STREAK_LIMIT:
                    raise _Halt(
                        f"{streak_code}: {streak} images in a row failed the same way; stopped (is the object storage reachable?)"
                    )
                if batch.done:
                    break
                after = batch.last_id
            if self._stopping() or not progressed:
                return

    # -- activate / retire ------------------------------------------------------------

    def _swap_in(self, stack: Any) -> None:
        old = self._runtime.replace_retrieval(stack)
        if old is not None and old is not stack:
            if self._runtime.stopping:
                old.close()
            else:
                timer = threading.Timer(RETIRED_STACK_GRACE_SECONDS, old.close)
                timer.daemon = True
                timer.start()
                with self._lock:
                    self._timers = [t for t in self._timers if t.is_alive()] + [timer]
        if self._runtime.reconciler is None:
            from app.bootstrap.reconciler import start_reconciler

            self._runtime.reconciler = start_reconciler(self._runtime)

    def activate(self, model_id: int, *, allow_missing: bool = False) -> dict[str, Any]:
        """Switch search and index to this version (a roll back when it was current before)."""
        from app.bootstrap.retrieval_stack import build_retrieval_stack

        with self._switch_lock:
            store = self._store()
            row = store.get_embedding_model(model_id)
            if row is None:
                raise err.embedding_model_not_found(f"embedding model {model_id} does not exist")
            build = self.build_status()
            if build is not None and build["model_id"] == int(model_id) and build["state"] in ("running", "stopping"):
                raise err.embedding_build_running("its vectors are being built; stop the build or wait for it to finish")
            stack = self._runtime.retrieval
            already = row["status"] == ModelStatus.CURRENT.value
            if already and stack is not None and stack.model.id == int(model_id):
                return {"model": row, "previous_id": None, "switched": False}
            require_activatable(row, backlog=0, allow_missing=True)  # retired: EMBEDDING_MODEL_STATE
            configured = self._configured()
            if configured is None:
                raise err.not_ready(f"the configured encoder (EMBEDDING_CONFIG) is not loaded: {self._runtime.initialization_error}")
            # Count what the collection really holds, not what the ledger remembers
            target = self._vectors(row)
            if target.collection_exists():
                verify_ledger(int(model_id), target, store)
            backlog = store.count_embedding_backlog(int(model_id))
            if not already and backlog > FINAL_CATCHUP_MAX:
                require_activatable(row, backlog=backlog, allow_missing=allow_missing)
            candidates = [stack.embedding] if stack is not None else []
            try:
                new_stack = build_retrieval_stack(
                    self._runtime.config, self._runtime.settings, embedder=configured, metadata=store,
                    model=ActiveModel.from_row(row), candidates=candidates,
                )
            except err.AppError as exc:
                if exc.code == "EMBEDDING_MODEL_MISMATCH":
                    raise err.embedding_encoder_unavailable(err.client_detail(exc)) from exc
                raise
            swapped = False
            try:
                with self._runtime.vector_writes.exclusive():
                    # No index request writes from here on until the new stack is in place
                    caught = self._catch_up(new_stack, store) if backlog <= FINAL_CATCHUP_MAX else None
                    remaining = store.count_embedding_backlog(int(model_id))
                    if remaining and not already:
                        reasons = sorted({f["code"] for f in (caught or {}).get("failures", [])})
                        require_activatable(
                            row, backlog=remaining, allow_missing=allow_missing,
                            reasons=", ".join(reasons) if reasons else None,
                        )
                    result = store.activate_embedding_model(int(model_id))
                    self._swap_in(new_stack)
                    swapped = True
            finally:
                if not swapped:
                    new_stack.close()
            logger.info(
                "已切换检索模型 | model=%s:%s | collection=%s | previous_id=%s | 切换时补齐 %s 张 | 仍缺 %d 张",
                row["name"], row["version"], row["collection"], result["previous_id"],
                (caught or {}).get("embedded", "—"), remaining,
            )
            return {**result, "switched": True, "caught_up": (caught or {}).get("embedded", 0), "remaining": remaining}

    def _catch_up(self, stack: Any, store: Any) -> dict[str, Any]:
        """Embed what the switch target still lacks (images indexed since its build), with its
        own stack, while the write gate is closed; each image is tried once."""
        use_case = BuildEmbeddingsUseCase(
            model=stack.model, encoder=stack.embedding, vectors=stack.infra.vectors,
            metadata=store, models=store, storage=stack.infra.storage,
        )
        embedded, failures, tried, after = 0, [], [], None
        while True:
            batch = use_case.run_batch(after=after, limit=BUILD_BATCH, exclude=tried)
            embedded += len(batch.embedded)
            failures.extend(batch.failures)
            tried.extend(f["image_id"] for f in batch.failures)
            if batch.done:
                return {"embedded": embedded, "failures": failures}
            after = batch.last_id

    def retire(self, model_id: int) -> dict[str, Any]:
        """Drop a version that is not current: its collection, then its ledger rows."""
        with self._switch_lock:
            store = self._store()
            row = store.get_embedding_model(model_id)
            if row is None:
                raise err.embedding_model_not_found(f"embedding model {model_id} does not exist")
            require_retirable(row)
            build = self.build_status()
            if build is not None and build["model_id"] == int(model_id) and build["state"] in ("running", "stopping"):
                raise err.embedding_build_running("its vectors are being built; stop the build first")
            if row["status"] == ModelStatus.RETIRED.value:
                return {"model": row, "dropped": False, "ledger_rows": 0}
            dropped = self._vectors(row).drop_collection()
            result = store.retire_embedding_model(int(model_id))
            logger.info(
                "已下线模型版本 %s:%s | collection=%s（%s）| 删除写入记录 %d 条",
                row["name"], row["version"], row["collection"], "已删除" if dropped else "本来就不在", result["ledger_rows"],
            )
            return {**result, "dropped": dropped}

    # -- lifecycle --------------------------------------------------------------------

    def close(self, timeout: float = 30.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout)
            if thread.is_alive():
                logger.warning("生成向量的线程在 %s 秒后仍在运行（daemon thread）", timeout)
        with self._lock:
            timers, self._timers = self._timers, []
        for timer in timers:
            if timer.is_alive():
                timer.cancel()
                try:
                    timer.function()  # close the old stack now
                except Exception:
                    logger.exception("关闭旧检索栈失败")
        client, self._client = self._client, None
        if client is not None:
            try:
                client.close()
            except Exception:
                logger.debug("qdrant client close failed", exc_info=True)
