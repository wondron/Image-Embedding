"""数据核对：PostgreSQL、Qdrant、对象存储三边是否一致，模型版本之间是否一致（/api/consistency）。

只读：只报告，不修复。修复走已有的路：后台对账（RECONCILE_CONFIG）补推 payload、为当前版本补向量、
清删除队列；「模型版本」的生成向量；「场景管理」的全量同步；原图丢了只能重新上传。

PostgreSQL 是权威来源。每个在用的模型版本（building / ready / current）一个 collection：

    PG → Qdrant（按 id 顺序一段一段读图片，每段对每个版本的 collection 读一次 payload）
      missing         没有点，也没有写入记录：还没生成过
      lost            有写入记录、collection 里没有点：向量丢了
      unrecorded      collection 里有点、没有写入记录（另一台服务器写的，或入库时记录没写成）
      ledger_behind   写入记录落后于 payload_version：对账（当前版本）/ 生成向量（其他版本）会补推
      stale_payload   写入记录说是最新的，点的 payload 却和 PostgreSQL 算出来的不一样：不会自动补推
      vs_current      和当前版本比，只有一边有点的图片（only_current / only_this）

    Qdrant → PG（orphans：翻遍 collection 的点 id）
      orphan_points   点对应的图片不在数据库里，也不在删除队列里：搜索时会占掉结果名额
      pending_points  图片在删除队列里：对账会删掉

对象存储（files）：每张图的原图和缩略图在不在（HEAD，不下载）；content 时下载原图核对 SHA-256。
反过来（orphans）：列出 original/、thumb/ 下的对象，找数据库里（图片和删除队列）都没有引用的。

encoders：用这个版本的编码器（已经在内存里时）重算几张已存图片，和 collection 里的向量比较，
确认这个 collection 是它写的。

和入库、删除同时进行时：入库先传文件、再在一个事务里写 PostgreSQL 和 Qdrant 后提交，所以核对
可能看到还没提交的点或文件。看起来是多余的点和文件在最后再查一次数据库，期间提交了的不算。
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from app.core import errors as err
from app.core.enums import ModelStatus
from app.services.retrieval.model_versions import check_collection_encoder
from app.services.retrieval.vector_status import expected_payload, payload_matches
from app.utils.storage_keys import relative_key

logger = logging.getLogger("retrieval.consistency")

SAMPLE_LIMIT = 20  # 每类问题列出的例子
IMAGE_BATCH = 500  # 每段读多少张图片
POINT_BATCH = 1000  # 翻 collection、列对象存储时每段多少个
FILE_WORKERS = 16  # 同时对对象存储发多少个 HEAD
RECHECK_MAX = 50_000  # 最后再查一次的候选最多保留这么多（更多的只计数）
LIST_MAX = 1_000_000  # 每个目录最多列这么多个对象
ENCODER_SAMPLES = 5
DELETION_STALE_SECONDS = 3600  # 删除队列里最早的一条超过这么久：清理多半没在跑
DETAIL_MAX = 300

VERSION_FINDINGS = ("missing", "lost", "unrecorded", "ledger_behind", "stale_payload", "orphan_points", "pending_points")
FILE_FINDINGS = ("missing_original", "missing_thumb", "unreadable", "content_mismatch", "orphan_files", "pending_files")
LIVE_STATUSES = (ModelStatus.BUILDING.value, ModelStatus.READY.value, ModelStatus.CURRENT.value)


@dataclass(frozen=True)
class CheckOptions:
    vectors: bool = True  # PG ↔ Qdrant，每个在用的版本
    files: bool = True  # PG → 对象存储：原图和缩略图还在不在
    orphans: bool = True  # 反过来：向量库、对象存储里有，数据库里没有的
    content: bool = False  # 下载原图核对 SHA-256（慢：每张图下载一次）
    encoders: bool = True  # 用编码器重算几张图，确认向量是这个版本写的

    def as_dict(self) -> dict[str, bool]:
        return asdict(self)


class Finding:
    """一类问题：总数和前几个例子。keep：另外保留全部（最多 RECHECK_MAX），最后再查一次用。"""

    def __init__(self, *, keep: bool = False) -> None:
        self.count = 0
        self.samples: list[str] = []
        self.kept: list[str] | None = [] if keep else None

    def add(self, item: str) -> None:
        self.count += 1
        if len(self.samples) < SAMPLE_LIMIT:
            self.samples.append(item)
        if self.kept is not None and len(self.kept) < RECHECK_MAX:
            self.kept.append(item)

    def drop(self, items: set[str]) -> int:
        """再查一次后不算问题的那些（只能是保留下来的）；返回去掉了几个。"""
        if not items or self.kept is None:
            return 0
        before = len(self.kept)
        self.kept = [item for item in self.kept if item not in items]
        dropped = before - len(self.kept)
        self.count -= dropped
        self.samples = self.kept[:SAMPLE_LIMIT]
        return dropped

    def as_dict(self) -> dict[str, Any]:
        return {"count": self.count, "samples": list(self.samples)}


@dataclass
class VersionTarget:
    """一个在用的版本，以及核对它要用的东西。"""

    row: Mapping[str, Any]  # embedding_models 的一行
    vectors: Any  # 它的 collection（QdrantVectorStore）；None：Qdrant 不可用
    encoder: Any = None  # 已经在内存里的它的编码器；None：没加载，跳过抽查
    encoder_source: str | None = None  # configured / serving
    serving: bool = False  # 本进程检索在用的版本
    weights: str | None = None  # 权重文件在本镜像里的情况（clients.inference.weights_status）


class _Version:
    def __init__(self, target: VersionTarget) -> None:
        row = target.row
        self.target = target
        self.id = int(row["id"])
        self.status = str(row["status"])
        self.label = f"{row['name']}:{row['version']}"
        self.collection = str(row["collection"])
        self.dim = int(row["dim"])
        self.distance = str(row["distance"])
        self.qdrant: dict[str, Any] = {"exists": None, "dim": None, "distance": None, "points": None, "matches": None}
        self.error: str | None = None if target.vectors is not None else "Qdrant is not available"
        self.scanned = 0
        self.ledger = 0
        self.with_vector = 0
        self.findings = {name: Finding(keep=name == "orphan_points") for name in VERSION_FINDINGS}
        self.vs_current: dict[str, Finding] | None = None
        self.encoder: dict[str, Any] = {"status": "off", "source": target.encoder_source, "checked": 0,
                                        "min_similarity": None, "detail": None}

    @property
    def usable(self) -> bool:
        return self.error is None and self.qdrant["exists"] is True

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "status": self.status,
            "collection": self.collection,
            "dim": self.dim,
            "distance": self.distance,
            "serving": self.target.serving,
            "weights": self.target.weights,
            "qdrant": dict(self.qdrant),
            "error": self.error,
            "scanned": self.scanned,
            "ledger": self.ledger,
            "with_vector": self.with_vector,
            "findings": {name: finding.as_dict() for name, finding in self.findings.items()},
            "vs_current": None if self.vs_current is None else {k: f.as_dict() for k, f in self.vs_current.items()},
            "encoder": dict(self.encoder),
        }


class _Files:
    def __init__(self) -> None:
        self.skipped: str | None = None
        self.checked = 0
        self.findings = {name: Finding(keep=name == "orphan_files") for name in FILE_FINDINGS}
        self.first_error: str | None = None
        self.listed = 0
        self.referenced = 0
        self.truncated = False
        self.list_error: str | None = None
        self.listing = False  # 列过对象存储（orphans）

    def as_dict(self) -> dict[str, Any]:
        return {
            "skipped": self.skipped,
            "checked": self.checked,
            "first_error": self.first_error,
            "listed": self.listed if self.listing else None,
            "referenced": self.referenced if self.listing else None,
            "truncated": self.truncated,
            "list_error": self.list_error,
            "findings": {name: finding.as_dict() for name, finding in self.findings.items()},
        }


def _canonical_uuid(value: str) -> str | None:
    try:
        return str(UUID(str(value).strip()))
    except (ValueError, AttributeError, TypeError):
        return None


def _image_id_of_key(key: str) -> str | None:
    """对象 key 的文件名（去掉扩展名）就是图片 id：original/a3/{id}.jpg、thumb/{id}.webp。"""
    stem = key.rsplit("/", 1)[-1].split(".", 1)[0]
    return _canonical_uuid(stem)


def _never() -> bool:
    return False


class ConsistencyCheck:
    def __init__(
        self,
        *,
        metadata: Any,
        versions: list[VersionTarget],
        storage: Any,
        options: CheckOptions,
        models: list[Mapping[str, Any]] | None = None,
        list_collections: Callable[[], list[str]] | None = None,
        progress: Callable[[str, int, int | None, str | None], None] | None = None,
        should_stop: Callable[[], bool] = _never,
        redact: Callable[[str], str] | None = None,
        storage_unavailable: str | None = None,
    ) -> None:
        """metadata：PgMetadataStore（PgConsistencyMixin + sample_embedded_images）；versions：在用的版本；
        storage：对象存储（None：用不了，原因在 storage_unavailable）；models：全部 embedding_models 行
        （认出已下线版本留下的 collection）；list_collections：这个 Qdrant 上全部 collection 的名字；
        progress(phase, done, total, label)；redact：遮盖错误信息里的密钥。
        """
        self._metadata = metadata
        self._targets = versions
        self._storage = storage
        self._options = options
        self._models = list(models or [])
        self._list_collections = list_collections
        self._progress = progress or (lambda *_: None)
        self._should_stop = should_stop
        self._redact = redact or (lambda text: text)
        self._storage_unavailable = storage_unavailable
        self._stopped = False

    # -- 一些小工具 -------------------------------------------------------------------

    def _stop(self) -> bool:
        if not self._stopped and self._should_stop():
            self._stopped = True
        return self._stopped

    def _detail(self, exc: BaseException) -> str:
        """给页面看的一句话：错误码（或异常类名）和原因，遮盖密钥，截短。"""
        code = exc.code if isinstance(exc, err.AppError) else type(exc).__name__
        text = exc.detail if isinstance(exc, err.AppError) else str(exc)
        return self._redact(f"{code}: {text}")[:DETAIL_MAX]

    # -- 一轮 -------------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        started = time.monotonic()
        options = self._options
        database = self._metadata.consistency_counts()
        versions = [_Version(target) for target in self._targets]
        current = next((v for v in versions if v.status == ModelStatus.CURRENT.value), None)
        for version in versions:
            if version is not current and current is not None:
                version.vs_current = {"only_current": Finding(), "only_this": Finding()}

        files: _Files | None = None
        if options.files:
            files = _Files()
            if self._storage is None or self._storage_unavailable:
                files.skipped = self._storage_unavailable or "object storage is not available"

        if options.vectors or options.encoders:
            self._progress("collections", 0, len(versions), None)
            for index, version in enumerate(versions, 1):
                self._describe_collection(version)
                self._progress("collections", index, len(versions), version.label)

        check_files = files is not None and files.skipped is None
        if options.vectors or check_files:
            self._scan_images(database["images"], versions, current, files if check_files else None)
        if options.orphans and options.vectors and not self._stop():
            for version in versions:
                if self._stop():
                    break
                self._scan_points(version)
        if options.orphans and check_files and not self._stop():
            self._scan_files(files)
        if not self._stop():
            self._recheck(versions, files if check_files else None)
        if options.encoders and not self._stop():
            for version in versions:
                if self._stop():
                    break
                self._check_encoder(version)

        stray, stray_error = self._stray_collections(versions) if (options.vectors or options.encoders) else (None, None)
        return {
            "complete": not self._stopped,
            "options": options.as_dict(),
            "duration_ms": int((time.monotonic() - started) * 1000),
            "database": database,
            "versions": [version.as_dict() for version in versions],
            "stray_collections": stray,
            "stray_error": stray_error,
            "files": files.as_dict() if files is not None else None,
        }

    # -- collection 本身 ----------------------------------------------------------------

    def _describe_collection(self, version: _Version) -> None:
        if version.target.vectors is None:
            return
        try:
            info = version.target.vectors.describe()
            if info is None:
                version.qdrant["exists"] = False
                return
            points = version.target.vectors.point_count()
        except Exception as exc:
            version.error = self._detail(exc)
            return
        dim, distance = info.get("size"), info.get("distance")
        version.qdrant.update(
            exists=True, dim=dim, distance=distance, points=points,
            matches=dim == version.dim and distance == version.distance,
        )

    # -- PG → Qdrant 和 PG → 对象存储 -----------------------------------------------------

    def _scan_images(self, total: int, versions: list[_Version], current: _Version | None, files: _Files | None) -> None:
        done, after = 0, None
        pool = ThreadPoolExecutor(max_workers=FILE_WORKERS, thread_name_prefix="consistency-files") if files else None
        try:
            self._progress("images", 0, total, None)
            while not self._stop():
                rows = self._metadata.scan_images(after=after, limit=IMAGE_BATCH)
                if not rows:
                    break
                if self._options.vectors:
                    self._check_vectors(rows, versions, current)
                if files is not None and pool is not None:
                    self._check_files(rows, files, pool)
                done += len(rows)
                after = rows[-1]["id"]
                self._progress("images", done, max(total, done), None)
                if len(rows) < IMAGE_BATCH:
                    break
        finally:
            if pool is not None:
                pool.shutdown(wait=True)

    def _check_vectors(self, rows: list[dict[str, Any]], versions: list[_Version], current: _Version | None) -> None:
        expected = {str(row["id"]): expected_payload(row) for row in rows}
        ids = list(expected)
        present: dict[int, set[str]] = {}
        for version in versions:
            if not version.usable:
                continue
            try:
                payloads = version.target.vectors.get_payloads(ids)
            except Exception as exc:
                version.error = self._detail(exc)
                continue
            present[version.id] = set(payloads)
            version.scanned += len(rows)
            found = version.findings
            for row in rows:
                image_id = str(row["id"])
                synced = (row.get("ledger") or {}).get(version.id)
                payload = payloads.get(image_id)
                if synced is not None:
                    version.ledger += 1
                if payload is None:
                    found["lost" if synced is not None else "missing"].add(image_id)
                    continue
                version.with_vector += 1
                if synced is None:
                    found["unrecorded"].add(image_id)  # 补向量时会重写这个点（payload 一起）并记上
                elif int(synced) < int(row["payload_version"]):
                    found["ledger_behind"].add(image_id)
                elif not payload_matches(payload, expected[image_id]):
                    found["stale_payload"].add(image_id)
        if current is None or current.id not in present:
            return
        mine = present[current.id]
        for version in versions:
            if version is current or version.vs_current is None or version.id not in present:
                continue
            theirs = present[version.id]
            for image_id in ids:
                if image_id in mine and image_id not in theirs:
                    version.vs_current["only_current"].add(image_id)
                elif image_id in theirs and image_id not in mine:
                    version.vs_current["only_this"].add(image_id)

    def _check_files(self, rows: list[dict[str, Any]], files: _Files, pool: ThreadPoolExecutor) -> None:
        tasks = [(row, kind) for row in rows for kind in ("original", "thumb")]
        for image_id, kind, outcome, detail in pool.map(self._check_file, tasks):
            if outcome == "missing":
                files.findings[f"missing_{kind}"].add(image_id)
            elif outcome == "unreadable":
                files.findings["unreadable"].add(image_id)
                files.first_error = files.first_error or detail
            elif outcome == "mismatch":
                files.findings["content_mismatch"].add(image_id)
        files.checked += len(rows)

    def _check_file(self, task: tuple[dict[str, Any], str]) -> tuple[str, str, str, str | None]:
        """(图片 id, original / thumb, ok / missing / unreadable / mismatch, 原因)。"""
        row, kind = task
        image_id = str(row["id"])
        url = str(row.get(f"{kind}_url") or "")
        if not url:
            return image_id, kind, "missing", None
        try:
            size = self._storage.head(url)
        except Exception as exc:
            return image_id, kind, "unreadable", self._detail(exc)
        if not size:  # 不在，或者是个空文件：都用不了
            return image_id, kind, "missing", None
        if kind == "original" and self._options.content:
            try:
                data = self._storage.read_by_url_or_key(url)
            except Exception as exc:
                return image_id, kind, "unreadable", self._detail(exc)
            if hashlib.sha256(data).hexdigest() != str(row.get("sha256") or ""):
                return image_id, kind, "mismatch", None
        return image_id, kind, "ok", None

    # -- Qdrant → PG -------------------------------------------------------------------

    def _scan_points(self, version: _Version) -> None:
        if not version.usable:
            return
        done, offset, total = 0, None, version.qdrant.get("points")
        self._progress("points", 0, total, version.label)
        orphans, pending = version.findings["orphan_points"], version.findings["pending_points"]
        while not self._stop():
            try:
                ids, offset = version.target.vectors.scroll_ids(offset=offset, limit=POINT_BATCH)
            except Exception as exc:
                version.error = self._detail(exc)
                return
            canonical = {point_id: _canonical_uuid(point_id) for point_id in ids}
            images = [image_id for image_id in canonical.values() if image_id is not None]
            known = self._metadata.existing_image_ids(images)
            unknown = [image_id for image_id in images if image_id not in known]
            queued = self._metadata.queued_deletion_ids(unknown)
            for point_id, image_id in canonical.items():
                if image_id is None:
                    orphans.add(point_id)  # 不是图片 id 的点：不是本服务写的
                elif image_id not in known:
                    (pending if image_id in queued else orphans).add(image_id)
            done += len(ids)
            self._progress("points", done, max(total or 0, done), version.label)
            if offset is None or not ids:
                return

    # -- 对象存储 → PG -----------------------------------------------------------------

    def _scan_files(self, files: _Files) -> None:
        files.listing = True
        self._progress("files", 0, None, None)
        for directory in ("original", "thumb"):
            batch: list[str] = []
            iterator = iter(self._storage.list_keys(directory))
            while not self._stop():
                try:
                    key, _size = next(iterator)
                except StopIteration:
                    break
                except Exception as exc:
                    files.list_error = files.list_error or self._detail(exc)
                    break
                if files.listed >= LIST_MAX:
                    files.truncated = True
                    break
                files.listed += 1
                batch.append(key)
                if len(batch) >= POINT_BATCH:
                    self._classify_files(batch, files)
                    batch = []
                    self._progress("files", files.listed, None, directory)
            self._classify_files(batch, files)
            self._progress("files", files.listed, None, directory)
            if self._stopped or files.truncated:
                return

    def _references(self, keys: Iterable[str]) -> dict[str, str]:
        """{key: referenced / pending / orphan}：图片行引用它 / 只有删除队列引用它 / 都没有。"""
        parsed = {key: _image_id_of_key(key) for key in keys}
        refs = self._metadata.file_refs(sorted({image_id for image_id in parsed.values() if image_id}))
        out: dict[str, str] = {}
        for key, image_id in parsed.items():
            rel = relative_key(key)
            matches = [
                ref for ref in refs.get(image_id or "", [])
                if rel is not None and rel in (relative_key(ref.get("original_url")), relative_key(ref.get("thumb_url")))
            ]
            out[key] = (
                "referenced" if any(not ref["deleted"] for ref in matches) else "pending" if matches else "orphan"
            )
        return out

    def _classify_files(self, keys: list[str], files: _Files) -> None:
        if not keys:
            return
        for key, state in self._references(keys).items():
            if state == "referenced":
                files.referenced += 1
            elif state == "pending":
                files.findings["pending_files"].add(key)
            else:
                files.findings["orphan_files"].add(key)

    # -- 最后再查一次 ----------------------------------------------------------------------

    def _recheck(self, versions: list[_Version], files: _Files | None) -> None:
        """核对期间提交的入库：当时看起来多余的点和文件，现在数据库里有了就不算。"""
        candidates = sorted({
            image_id for version in versions for image_id in (version.findings["orphan_points"].kept or [])
            if _canonical_uuid(image_id) == image_id
        })
        if candidates:
            self._progress("recheck", 0, None, None)
            known: set[str] = set()
            for start in range(0, len(candidates), POINT_BATCH):
                known |= self._metadata.existing_image_ids(candidates[start : start + POINT_BATCH])
            for version in versions:
                version.findings["orphan_points"].drop(known)
        if files is None:
            return
        kept = list(files.findings["orphan_files"].kept or [])
        settled: set[str] = set()
        for start in range(0, len(kept), POINT_BATCH):
            for key, state in self._references(kept[start : start + POINT_BATCH]).items():
                if state == "referenced":
                    settled.add(key)
                    files.referenced += 1
                elif state == "pending":
                    settled.add(key)
                    files.findings["pending_files"].add(key)
        files.findings["orphan_files"].drop(settled)

    # -- 编码器抽查 -----------------------------------------------------------------------

    def _check_encoder(self, version: _Version) -> None:
        state = version.encoder
        encoder = version.target.encoder
        if encoder is None:
            state["status"] = "not_loaded"
            return
        if not version.usable:
            state.update(status="unchecked", detail="collection is not readable")
            return
        if self._storage is None:
            state.update(status="unchecked", detail=self._storage_unavailable or "object storage is not available")
            return
        self._progress("encoders", 0, ENCODER_SAMPLES, version.label)
        try:
            samples = list(self._metadata.sample_embedded_images(version.id, limit=ENCODER_SAMPLES))
            stored = version.target.vectors.get_vectors([str(s["id"]) for s in samples]) if samples else {}
        except Exception as exc:
            state.update(status="unchecked", detail=self._detail(exc))
            return
        report = check_collection_encoder(
            samples=samples, stored=stored, read_original=self._storage.read_by_url_or_key, embed=encoder.embed_bytes,
        )
        state.update(checked=report["checked"], min_similarity=report["min_similarity"])
        if report["matches"] is None:
            state.update(status="unchecked", detail="no stored image could be embedded again" if samples else "no vectors yet")
        else:
            state["status"] = "ok" if report["matches"] else "mismatch"
        self._progress("encoders", report["checked"], ENCODER_SAMPLES, version.label)

    # -- 多出来的 collection --------------------------------------------------------------

    def _stray_collections(self, versions: list[_Version]) -> tuple[list[dict[str, Any]] | None, str | None]:
        """这个 Qdrant 上不属于任何在用版本的 collection；已下线版本留下的标出是哪个版本。"""
        if self._list_collections is None:
            return None, None
        try:
            names = self._list_collections()
        except Exception as exc:
            return None, self._detail(exc)
        live = {version.collection for version in versions}
        by_collection = {str(row["collection"]): row for row in self._models}
        out = []
        for name in names:
            if name in live:
                continue
            row = by_collection.get(name)
            out.append({
                "name": name,
                "model_id": int(row["id"]) if row else None,
                "label": f"{row['name']}:{row['version']}" if row else None,
                "status": str(row["status"]) if row else None,
            })
        return out, None


# -- 结论 ----------------------------------------------------------------------------------

LEVELS = ("error", "warn", "info")


def _age_seconds(iso: str | None, now: datetime) -> float | None:
    if not iso:
        return None
    try:
        at = datetime.fromisoformat(str(iso))
    except ValueError:
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return (now - at).total_seconds()


def build_issues(report: Mapping[str, Any], *, now: datetime | None = None) -> list[dict[str, Any]]:
    """报告里要处理的事，最严重的在前：[{level, code, count, version_id, label, detail}]。
    level：error 数据不一致或查不了 / warn 需要处理 / info 会自动处理或只是说明。页面按 code 给出
    说明和去哪里修。
    """
    now = now or datetime.now(timezone.utc)
    issues: list[dict[str, Any]] = []

    def add(level: str, code: str, count: int | None = None, *, version: Mapping[str, Any] | None = None,
            detail: str | None = None) -> None:
        issues.append({
            "level": level, "code": code, "count": count,
            "version_id": version["id"] if version else None, "label": version["label"] if version else None,
            "detail": detail,
        })

    connections = report.get("connections") or {}
    for name in ("postgres", "qdrant", "storage"):
        part = connections.get(name)
        if part is not None and not part.get("ok"):
            add("error", f"{name}_unreachable", detail=part.get("detail"))
    qdrant = connections.get("qdrant") or {}
    if qdrant.get("restart_required"):
        add("error", "qdrant_restart_required", detail=qdrant.get("configured_url"))
    serving = connections.get("serving") or {}
    database = report.get("database") or {}
    current = next((v for v in report.get("versions") or [] if v["status"] == ModelStatus.CURRENT.value), None)
    if database.get("current_models") == 0:
        add("error", "no_current_model")
    if serving.get("model_id") is None:
        add("warn", "retrieval_not_ready", detail=serving.get("detail"))
    elif current is not None and serving.get("model_id") != current["id"]:
        add("warn", "serving_not_current", detail=serving.get("label"))
    pending = int(database.get("pending_deletions") or 0)
    if pending:
        age = _age_seconds(database.get("oldest_deletion_at"), now)
        add("warn" if age is not None and age > DELETION_STALE_SECONDS else "info", "deletion_queue", pending)

    for version in report.get("versions") or []:
        building = version["status"] == ModelStatus.BUILDING.value
        qd = version.get("qdrant") or {}
        if version.get("error"):
            add("error", "version_unreadable", version=version, detail=version["error"])
        if qd.get("exists") is False:
            add("error", "collection_missing", version=version)
        elif qd.get("matches") is False:
            add("error", "collection_mismatch", version=version,
                detail=f"{qd.get('dim')} / {qd.get('distance')}, expected {version['dim']} / {version['distance']}")
        found = version.get("findings") or {}

        def count(name: str) -> int:
            return int((found.get(name) or {}).get("count") or 0)

        for code, name, level in (
            ("missing_vectors", "missing", "info" if building else "warn"),
            ("lost_vectors", "lost", "warn"),
            ("unrecorded_vectors", "unrecorded", "warn"),
            ("stale_payloads", "stale_payload", "warn"),
            ("ledger_behind", "ledger_behind", "info"),
            ("orphan_points", "orphan_points", "warn"),
            ("pending_points", "pending_points", "info"),
        ):
            if count(name):
                add(level, code, count(name), version=version)
        differ = version.get("vs_current")
        if differ:
            total = int(differ["only_current"]["count"]) + int(differ["only_this"]["count"])
            if total:
                add("info" if building else "warn", "versions_differ", total, version=version)
        encoder = version.get("encoder") or {}
        if encoder.get("status") == "mismatch":
            add("error", "encoder_mismatch", encoder.get("checked"), version=version,
                detail=None if encoder.get("min_similarity") is None else f"{encoder['min_similarity']:.4f}")
        if version.get("weights") in ("missing", "changed"):
            add("warn", "weights_unavailable", version=version, detail=version["weights"])

    stray = report.get("stray_collections") or []
    left = [item for item in stray if item.get("model_id") is not None]
    if left:
        add("warn", "retired_collection_left", len(left), detail=", ".join(item["name"] for item in left))
    if len(stray) > len(left):
        add("info", "stray_collections", len(stray) - len(left),
            detail=", ".join(item["name"] for item in stray if item.get("model_id") is None))
    if report.get("stray_error"):
        add("warn", "collections_unlisted", detail=report["stray_error"])

    files = report.get("files")
    if files:
        if files.get("skipped"):
            add("info", "files_unchecked", detail=files["skipped"])
        found = files.get("findings") or {}
        for code, level in (
            ("missing_original", "error"), ("missing_thumb", "warn"), ("unreadable", "error"),
            ("content_mismatch", "error"), ("orphan_files", "info"), ("pending_files", "info"),
        ):
            n = int((found.get(code) or {}).get("count") or 0)
            if n:
                add(level, "files_unreadable" if code == "unreadable" else code, n,
                    detail=files.get("first_error") if code == "unreadable" else None)
        if files.get("list_error"):
            add("warn", "file_list_failed", detail=files["list_error"])
        if files.get("truncated"):
            add("info", "file_list_truncated", files.get("listed"))
    if report.get("complete") is False:
        add("warn", "check_incomplete")
    issues.sort(key=lambda item: LEVELS.index(item["level"]))
    return issues


def overall_status(issues: list[Mapping[str, Any]]) -> str:
    """ok：没有 error 和 warn / warn / error。"""
    levels = {item["level"] for item in issues}
    return "error" if "error" in levels else "warn" if "warn" in levels else "ok"
