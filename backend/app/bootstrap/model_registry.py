"""API 进程里的模型版本：/api/embedding-models 调用的内容。

更换 embedding 模型是一系列步骤，在这里执行：

1. 登记（register）：配置的编码器（EMBEDDING_CONFIG，预测用的那个）成为一个新版本
   （状态 building），有自己的空 collection。搜索和入库仍然用当前版本。
2. 构建（build）：后台线程先用 collection 里真实的向量点核对台账（verify_ledger：向量点已经
   不在的行被忘掉），然后用该版本的编码器为每张还没有向量的已存图片的原图计算 embedding
   （services.retrieval.build_embeddings），再补上这期间变化的 payload。台账就是进度：
   停下再启动会从停下的地方继续。没有遗漏任何图片的构建会把版本标记为 ready。编码器是
   配置的那个、搜索正在用的那个，或者根据指纹记录的权重（仍在镜像里）重新加载的该版本
   自己的编码器。
3. 切换（activate）：用目标版本的向量点核对它的台账，构建它的检索服务（它的编码器、它的
   collection），然后关闭向量写入闸门（app.bootstrap.write_gate：新的入库请求等待，进行中的
   完成），为目标版本补算它还缺的（构建之后入库的图片，最多 FINAL_CATCHUP_MAX 张），在
   PostgreSQL 里把它设为当前版本（之前的回到 ready），换上新的检索服务，再打开闸门：这期间
   写入的图片不会漏掉新的 collection。旧的检索服务稍后关闭，等仍持有它的请求结束。回滚就是
   对上一个版本做同样的调用。
4. 下线（retire）：非当前版本失去它的 collection 和台账行。

检索服务挂了时也能用：EMBEDDING_CONFIG 改了、当前版本配对不上时
（bootstrap.retrieval_stack.pair_encoder），通过登记、构建并切换到配置的编码器的版本来修复。
需要目录的 PostgreSQL 存储、QDRANT_CONFIG 和对象存储；每个进程同时只能有一个构建，每个
版本一把 PostgreSQL advisory lock，防止两个进程构建同一个版本。

其他 API 进程不会跟随这里做的切换：它们保留自己的检索服务直到重启
（API 镜像只运行一个 worker 进程）。
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

# 一次构建的 pg_try_advisory_lock 键：这个基数 + 版本 id（"EMB" 0）
BUILD_LOCK_KEY_BASE = 0x454D4200
# 新的一遍会处理上一遍运行期间存入的图片
MAX_PASSES = 3
# 连续这么多张图片以同一个错误码失败时停止构建（存储挂了等）
FAILURE_STREAK_LIMIT = 25
# 保留给页面显示的失败数（计数覆盖全部）
MAX_FAILURES_SHOWN = 50
# 切换前刚拿到旧检索服务的请求，在旧服务上继续运行这么长时间
RETIRED_STACK_GRACE_SECONDS = 30.0
# 切换时自己最多补算这么多张缺失的图片，期间入库请求被挡住；落后更多的版本需要先构建
# （或者 allow_missing）
FINAL_CATCHUP_MAX = 200


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class _Halt(Exception):
    """构建自己停下了：信息就是页面上显示的内容。"""


@dataclass
class BuildState:
    """本进程的一次构建。state：running / stopping / done / stopped / failed。"""

    model_id: int
    state: str
    started_at: str
    finished_at: str | None = None
    verified: int = 0  # 用 collection 的向量点核对过的台账行数
    repaired: int = 0  # 其中向量点已经不在的行数（由这次构建重新计算 embedding）
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
        self._lock = threading.RLock()  # 构建的启动 / 停止 / 状态
        self._switch_lock = threading.Lock()  # 同一时间只能有一次切换或下线
        self._client: Any = None
        self._build: BuildState | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._timers: list[threading.Timer] = []

    # -- 它用到的东西 ---------------------------------------------------------------

    def _store(self) -> Any:
        return self._runtime.require_catalog().store

    def _configured(self) -> Any:
        return self._runtime.embedder

    def _connection(self) -> Any:
        from app.bootstrap.retrieval_stack import _qdrant_connection

        return _qdrant_connection(self._runtime.config)

    def _vectors(self, row: dict[str, Any]) -> QdrantVectorStore:
        """基于本版本登记自己的客户端的版本 collection（它比检索服务的替换活得更久）。"""
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
            catalog = self._runtime.require_catalog()
        except err.NotReadyError as exc:
            raise err.not_ready(f"object storage is not available: {exc.detail}") from exc
        try:
            return catalog.file_storage()
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
        """已经为这个版本加载好的（"configured" | "serving", 编码器），否则为 None。"""
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
        """版本的 collection，不在了就重新建一个；这时它的台账行就作废了。"""
        vectors = self._vectors(row)
        existed = vectors.collection_exists()
        vectors.ensure_collection(recreate=False)
        if not existed:
            forgotten = store.clear_embeddings(int(row["id"]))
            if forgotten:
                logger.warning("collection=%s 不存在，已新建；%d 条写入记录作废", row["collection"], forgotten)
        return vectors

    def _encoder_for_build(self, row: dict[str, Any]) -> tuple[str, Any]:
        """版本的编码器：在内存里就用内存里的，否则根据指纹记录的权重重新加载（权重不在了或者
        已经不一样了时为 EMBEDDING_ENCODER_UNAVAILABLE）。
        """
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

    # -- 读取 -------------------------------------------------------------------------

    def build_status(self) -> dict[str, Any] | None:
        with self._lock:
            return asdict(self._build) if self._build is not None else None

    def overview(self) -> dict[str, Any]:
        """每个版本及其覆盖情况、配置的编码器，以及搜索当前配对的版本。"""
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
                    points = None  # 连不上，或者没有 collection（构建会重新创建）
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

    # -- 登记 -------------------------------------------------------------------------

    def register(self, *, name: str | None = None, version: str | None = None) -> dict[str, Any]:
        """把配置的编码器登记为一个新版本（building），带一个新的空 collection。"""
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
            if not vectors.collection_exists():  # 从不复用别人留在 Qdrant 里的向量点
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

    # -- 构建 -------------------------------------------------------------------------

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
            vectors = self._open_collection(row, store)  # 如果被手工删了就重新创建
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
                if row.get("status") != ModelStatus.CURRENT.value:  # 当前版本由对账任务补齐
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
        """一遍又一遍地处理还没有向量的图片，每张图片在一次构建里只试一次。"""
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

    # -- 切换 / 下线 ------------------------------------------------------------------

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
        """把搜索和入库切换到这个版本（如果它之前是当前版本，就是回滚）。"""
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
            require_activatable(row, backlog=0, allow_missing=True)  # 已下线：EMBEDDING_MODEL_STATE
            configured = self._configured()
            if configured is None:
                raise err.not_ready(f"the configured encoder (EMBEDDING_CONFIG) is not loaded: {self._runtime.initialization_error}")
            # 统计 collection 里真正有的，而不是台账记得的
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
                    # 从这里开始，在新的检索服务就位之前，没有入库请求会写入
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
        """在写入闸门关闭期间，用目标版本自己的检索服务补算它还缺的（构建之后入库的图片）；
        每张图片只试一次。
        """
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
        """删除一个非当前版本：先删它的 collection，再删它的台账行。"""
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

    # -- 生命周期 ---------------------------------------------------------------------

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
                    timer.function()  # 现在就关闭旧的检索服务
                except Exception:
                    logger.exception("关闭旧检索栈失败")
        client, self._client = self._client, None
        if client is not None:
            try:
                client.close()
            except Exception:
                logger.debug("qdrant client close failed", exc_info=True)
