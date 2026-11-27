"""API 进程里的数据核对（/api/consistency）：在后台线程里跑一次 services.retrieval.consistency，
页面轮询进度和结果。

一个进程同一时间只跑一次；结果留在内存里（最近一次），重启就没了。只读，所以不和对账、
生成向量、切换版本抢锁。

连的是本进程实际在用的那几个：PostgreSQL 用目录的存储；Qdrant 用启动时的 QDRANT_CONFIG（和检索、
入库、模型版本一样只在启动时读取——Nacos 里改了地址、没重启时，核对的仍是本进程连着的那个，
并在报告里标出 restart_required）；对象存储用检索服务的（mock 驱动的内存内容在它那里）。
两台服务器共用一个 PostgreSQL、各连一个 Qdrant 时，在两边各核对一次，对比 connections.qdrant.url。
"""

from __future__ import annotations

import copy
import logging
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.bootstrap.dependency_logging import redact_text
from app.clients.inference import weights_status
from app.clients.qdrant import QdrantStore
from app.core import errors as err
from app.repositories.vectors import QdrantVectorStore
from app.services.retrieval.consistency import (
    LIVE_STATUSES,
    CheckOptions,
    ConsistencyCheck,
    VersionTarget,
    build_issues,
    overall_status,
)
from app.services.retrieval.model_versions import same_encoder

if TYPE_CHECKING:
    from app.bootstrap.state import ApplicationState

logger = logging.getLogger("consistency")

# 列 collection 用的占位名：只调 get_collections，不碰这个 collection
_PROBE_COLLECTION = "_consistency_probe"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _fingerprint(encoder: Any) -> Any:
    if encoder is None:
        return None
    try:
        return encoder.fingerprint()
    except Exception:
        return None


class ConsistencyChecker:
    def __init__(self, runtime: "ApplicationState") -> None:
        self._runtime = runtime
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._run: dict[str, Any] | None = None
        self._qdrant_config = copy.deepcopy(runtime.config.get("QDRANT_CONFIG"))

    # -- 页面调用的 -----------------------------------------------------------------------

    def status(self) -> dict[str, Any] | None:
        with self._lock:
            return copy.deepcopy(self._run)

    def start(self, options: CheckOptions) -> dict[str, Any]:
        with self._lock:
            if self._run is not None and self._run["state"] in ("running", "stopping"):
                raise err.consistency_check_running("a consistency check is running; stop it or wait for it to finish")
            self._runtime.require_catalog()  # 没有 PostgreSQL 什么都核对不了：NOT_READY
            self._stop.clear()
            self._run = {
                "state": "running",
                "started_at": _now(),
                "finished_at": None,
                "options": options.as_dict(),
                "progress": {"phase": "starting", "done": 0, "total": None, "label": None},
                "error": None,
                "report": None,
            }
            self._thread = threading.Thread(target=self._execute, args=(options,), name="consistency-check", daemon=True)
            self._thread.start()
            logger.info("开始数据核对 | options=%s", options.as_dict())
            return copy.deepcopy(self._run)

    def stop(self) -> dict[str, Any] | None:
        with self._lock:
            if self._run is not None and self._run["state"] == "running":
                self._stop.set()
                self._run["state"] = "stopping"
            return copy.deepcopy(self._run)

    def close(self, timeout: float = 30.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout)
            if thread.is_alive():
                logger.warning("数据核对线程在 %s 秒后仍在运行（daemon thread）", timeout)

    # -- 后台线程 -------------------------------------------------------------------------

    def _stopping(self) -> bool:
        return self._stop.is_set() or self._runtime.stopping

    def _progress(self, phase: str, done: int, total: int | None, label: str | None) -> None:
        with self._lock:
            if self._run is not None:
                self._run["progress"] = {"phase": phase, "done": int(done), "total": total, "label": label}

    def _finish(self, **changes: Any) -> None:
        with self._lock:
            if self._run is not None:
                self._run.update(finished_at=_now(), **changes)

    def _redact(self, text: str) -> str:
        return redact_text(str(text), self._runtime.config)

    def _execute(self, options: CheckOptions) -> None:
        client = None
        try:
            store = self._runtime.require_catalog().store
            connection, connection_error = self._connection()
            if connection is not None:
                try:
                    client = connection.create_client()
                except Exception as exc:
                    connection, connection_error = None, f"{type(exc).__name__}: {exc}"
            storage, storage_error = self._storage()
            rows = store.list_embedding_model_rows()
            stack = self._runtime.retrieval
            targets = [self._target(row, connection, client, stack) for row in rows if row["status"] in LIVE_STATUSES]
            probe = (
                QdrantVectorStore(QdrantStore(connection, collection=_PROBE_COLLECTION, vector_dim=1, client=client))
                if connection is not None else None
            )
            connections = self._connections(store, connection, connection_error, probe, storage, storage_error, stack)
            unavailable = storage_error
            if storage is not None and unavailable is None:
                unavailable = storage.inventory_unavailable()
            check = ConsistencyCheck(
                metadata=store, versions=targets, storage=storage, options=options, models=rows,
                list_collections=probe.list_collections if probe is not None else None,
                progress=self._progress, should_stop=self._stopping, redact=self._redact,
                storage_unavailable=unavailable,
            )
            report = check.run()
            report["connections"] = connections
            issues = build_issues(report)
            report["issues"] = issues
            report["status"] = overall_status(issues)
            state = "done" if report["complete"] else "stopped"
            self._finish(state=state, report=report)
            logger.info(
                "数据核对结束 | state=%s | status=%s | %s | %dms", state, report["status"],
                ", ".join(f"{i['code']}={i['count']}" if i["count"] is not None else i["code"] for i in issues) or "没有问题",
                report["duration_ms"],
            )
        except err.AppError as exc:
            logger.warning("数据核对中止 | %s: %s", exc.code, exc.detail)
            self._finish(state="failed", error=self._redact(f"{exc.code}: {exc.detail}")[:500])
        except Exception as exc:
            logger.exception("数据核对失败")
            self._finish(state="failed", error=self._redact(f"{type(exc).__name__}: {exc}")[:500])
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    logger.debug("qdrant client close failed", exc_info=True)

    # -- 它要连的东西 ----------------------------------------------------------------------

    def _connection(self) -> tuple[Any, str | None]:
        from app.bootstrap.retrieval_stack import _qdrant_connection

        try:
            return _qdrant_connection({"QDRANT_CONFIG": self._qdrant_config}), None
        except (TypeError, ValueError) as exc:
            return None, str(exc)

    def _storage(self) -> tuple[Any, str | None]:
        """检索服务的对象存储（mock 驱动的内存内容在它那里），没有检索服务时用目录按配置建的。"""
        stack = self._runtime.retrieval
        if stack is not None:
            return stack.infra.storage, None
        try:
            return self._runtime.require_catalog().file_storage(), None
        except Exception as exc:
            return None, f"IMAGE_STORAGE_CONFIG: {type(exc).__name__}: {exc}"

    def _target(self, row: dict[str, Any], connection: Any, client: Any, stack: Any) -> VersionTarget:
        vectors = None
        if connection is not None and client is not None:
            vectors = QdrantVectorStore(QdrantStore(
                connection, collection=str(row["collection"]), vector_dim=int(row["dim"]),
                distance=str(row["distance"]), client=client,
            ))
        encoder, source = None, None
        fingerprint = row.get("fingerprint")
        if fingerprint is not None:
            configured = self._runtime.embedder
            if configured is not None and same_encoder(fingerprint, _fingerprint(configured)):
                encoder, source = configured, "configured"
            elif stack is not None and same_encoder(fingerprint, _fingerprint(stack.embedding)):
                encoder, source = stack.embedding, "serving"
        serving = stack is not None and stack.model.id == int(row["id"])
        return VersionTarget(
            row=row, vectors=vectors, encoder=encoder, encoder_source=source, serving=serving,
            weights=weights_status(fingerprint),
        )

    def _connections(
        self, store: Any, connection: Any, connection_error: str | None, probe: Any,
        storage: Any, storage_error: str | None, stack: Any,
    ) -> dict[str, Any]:
        """本进程实际连着的 PostgreSQL、Qdrant 和对象存储，以及检索在用的版本。"""
        from app.bootstrap.retrieval_stack import _qdrant_connection

        pg = store.ping()
        postgres = {"ok": bool(pg.get("ok")), "host": pg.get("host"), "database": pg.get("database"),
                    "detail": self._redact(pg["detail"]) if pg.get("detail") else None}

        qdrant: dict[str, Any] = {"ok": False, "url": None, "configured_url": None, "restart_required": False,
                                  "collections": None, "detail": connection_error}
        if connection is not None:
            qdrant["url"] = self._redact(connection.url)
            try:
                qdrant["collections"] = len(probe.list_collections())
                qdrant["ok"] = True
            except Exception as exc:
                qdrant["detail"] = self._redact(f"{type(exc).__name__}: {getattr(exc, 'detail', exc)}")[:300]
            try:
                configured = _qdrant_connection(self._runtime.config).url
            except (TypeError, ValueError):
                configured = None
            if configured and configured != connection.url:
                # QDRANT_CONFIG 只在启动时读取：Nacos 里改了地址，这个进程要重启才会改连过去
                qdrant["configured_url"] = self._redact(configured)
                qdrant["restart_required"] = True

        files: dict[str, Any] = {"ok": False, "driver": None, "bucket": None, "endpoint_url": None,
                                 "public_base_url": None, "key_prefix": None, "detail": storage_error}
        if storage is not None:
            ping = storage.ping()
            settings = getattr(storage, "settings", None)
            files.update(
                ok=bool(ping.get("ok")), driver=ping.get("driver"), bucket=ping.get("bucket"),
                endpoint_url=self._redact(settings.endpoint_url) if settings is not None and settings.endpoint_url else None,
                public_base_url=ping.get("public_base_url"), key_prefix=ping.get("key_prefix"),
                detail=self._redact(ping["detail"])[:300] if ping.get("detail") else None,
            )

        serving = (
            {"model_id": stack.model.id, "label": stack.model.space_id, "detail": None}
            if stack is not None else
            {"model_id": None, "label": None, "detail": self._runtime.retrieval_error or "retrieval is not running"}
        )
        return {"postgres": postgres, "qdrant": qdrant, "storage": files, "serving": serving}
