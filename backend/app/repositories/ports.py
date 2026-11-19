"""检索端口：services/retrieval 对外部世界的需要。

实现：PgMetadataStore（repositories.metadata；同时是给检索用例的 MetadataStorePort 和
给类别 / 场景管理的 CatalogStorePort）、QdrantVectorStore（repositories.vectors，基于
clients.qdrant.QdrantStore）、Mock/S3ImageStorage（clients.image_storage）、Embedder
（clients.inference；和预测共用一个实例）。
组装：app.bootstrap.catalog（管理，只用 PostgreSQL）和 app.bootstrap.retrieval_stack。

PG v0.1：向量点 id == images.id（uuid）。collection/dim/distance 取自 status='current' 的
embedding_models 行（services.retrieval.active_model）。
VECTOR_DIM 只是兜底的默认值（食物 ONNX = 1024）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from uuid import UUID


VECTOR_DIM = 1024


@dataclass(frozen=True)
class ImageIndexResult:
    """一个入库事务持有的已存图片快照。"""

    image: dict[str, Any]
    created: bool


@runtime_checkable
class EmbeddingPort(Protocol):
    def embed_bytes(self, image_bytes: bytes) -> list[float]:
        ...


class VectorStorePort(ABC):
    """Qdrant 读写。向量点 id 是 uuid（== images.id）。"""

    @abstractmethod
    def upsert(
        self,
        point_id: UUID | str,
        vector: list[float],
        payload: dict[str, Any],
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def search(
        self,
        vector: list[float],
        top_k: int = 10,
        *,
        score_threshold: float | None = None,
        scene: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """返回 [{id, score, payload}, ...]。空列表 = 没有近邻。"""
        raise NotImplementedError

    @abstractmethod
    def delete(self, point_id: UUID | str) -> None:
        raise NotImplementedError

    @abstractmethod
    def set_payloads(self, payloads: dict[str, dict[str, Any]]) -> list[str]:
        """写入每个向量点的 payload 字段（不动向量）；返回不在 collection 里的 id，
        这些会被跳过。
        """
        raise NotImplementedError

    @abstractmethod
    def get_payloads(self, point_ids: list[UUID | str]) -> dict[str, dict[str, Any]]:
        """给定 id 中是 collection 里向量点的那些的 {向量点 id: payload}（不含向量）；
        没有向量点的 id 不返回。
        """
        raise NotImplementedError

    @abstractmethod
    def ping(self) -> dict[str, Any]:
        """{ok, collection, points_count, ...}；从不抛出。"""
        raise NotImplementedError

    def close(self) -> None:
        """释放客户端（可重复调用）。"""


class MetadataStorePort(ABC):
    """PostgreSQL 元数据（PG v0.1）。由后端负责实现。"""

    @abstractmethod
    def insert_image(self, row: dict[str, Any]) -> UUID | str:
        raise NotImplementedError

    @abstractmethod
    def index_image(
        self, row: dict[str, Any], *, model_id: int
    ) -> AbstractContextManager[ImageIndexResult]:
        """插入或锁住一张图片，重算它的场景，并 yield 一个已存快照。

        调用方写入向量期间，图片行和目录一直锁着。正常退出时记录该快照的版本并提交；
        出现异常时回滚事务，包括新插入的图片。
        """
        raise NotImplementedError

    @abstractmethod
    def lock_image_for_vector(
        self, image_id: UUID | str, *, model_id: int
    ) -> AbstractContextManager[dict[str, Any] | None]:
        """在调用方重新写入已存图片的向量点期间锁住它；yield 图片现在的样子（带场景），
        已被删除时为 None。删除会等待这次写入。正常退出时把 yield 出去的 payload_version
        记录到 model_id 的台账并提交。
        """
        raise NotImplementedError

    @abstractmethod
    def mark_deleted(self, image_id: UUID | str) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_active_by_ids(
        self,
        image_ids: list[UUID | str],
        *,
        scene: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def list_images(
        self,
        *,
        scene: str,
        page: int = 1,
        page_size: int = 24,
        q: str | None = None,
        category: str | None = None,
        tag: str | None = None,
        category_id: int | None = None,
        label_status: str | None = None,
        out_of_sync: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """category_id 按类别的子树过滤；label_status 按精确值过滤；
        out_of_sync 保留当前模型台账行缺失或落后的图片。
        """
        raise NotImplementedError

    @abstractmethod
    def get_by_id(self, image_id: UUID | str) -> dict[str, Any] | None:
        raise NotImplementedError

    # -- 类别 / 场景 ----------------------------------------------------------

    @abstractmethod
    def get_category_id_by_code(self, code: str) -> int | None:
        raise NotImplementedError

    @abstractmethod
    def get_scene_by_code(self, code: str) -> dict[str, Any] | None:
        """scenes 行（任何状态）或 None。"""
        raise NotImplementedError

    @abstractmethod
    def list_scene_codes(self, *, active_only: bool = True) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def recompute_image_scenes(
        self, image_id: UUID | str, *, bump_version: bool = True
    ) -> dict[str, Any]:
        """物化 image_scenes；返回 {scene_codes, payload_version, ...}。"""
        raise NotImplementedError

    @abstractmethod
    def recompute_scene_memberships(
        self, *, after: UUID | str | None = None, limit: int = 200
    ) -> dict[str, Any]:
        """按 id 顺序重算下一批图片：{image_ids, changed_ids, last_id}。"""
        raise NotImplementedError

    @abstractmethod
    def map_categories_to_scene_labels(
        self, scene_id: int, category_ids: list[int]
    ) -> dict[int, dict[str, Any]]:
        """映射到启用标签的那些类别的 {category_id: {id, code, name}}。"""
        raise NotImplementedError

    # -- embedding 模型 / 台账 -------------------------------------------------

    @abstractmethod
    def get_current_embedding_model(self) -> dict[str, Any]:
        """唯一一行 status='current' 的 embedding_models（没有时 NOT_READY）。"""
        raise NotImplementedError

    @abstractmethod
    def upsert_image_embedding(
        self,
        *,
        model_id: int,
        image_id: UUID | str,
        synced_version: int | None = None,
    ) -> None:
        """记录 image_id 在 synced_version 的 payload 已经在 model_id 的 collection 里。"""
        raise NotImplementedError

    @abstractmethod
    def get_payload_rows(self, image_ids: list[str]) -> list[dict[str, Any]]:
        """这些图片的 Qdrant payload 的来源字段：每张已有图片一个 {id, payload_version,
        category_id, label_status, food_state, scenes}。
        """
        raise NotImplementedError

    @abstractmethod
    def mark_payloads_pushed(self, *, model_id: int, versions: dict[str, int]) -> list[str]:
        """台账：{image_id: payload_version} 现在已经在 model_id 的 collection 里。只记录仍是图片
        当前版本的那些；返回记录了的图片 id。
        """
        raise NotImplementedError

    @abstractmethod
    def list_unsynced_payload_ids(
        self,
        *,
        after: UUID | str | None = None,
        until: UUID | str | None = None,
        limit: int = 200,
        model_id: int | None = None,
    ) -> list[str]:
        """当前模型（给了 model_id 时为它）的台账行落后于 payload_version 的图片，按 id 顺序，
        在 `after` 之后、到 `until` 为止（含）。没有台账行的图片不返回。
        """
        raise NotImplementedError

    @abstractmethod
    def try_maintenance_lock(self, key: int) -> AbstractContextManager[bool]:
        """维护任务用的会话级 advisory lock：持有时 yield True，另一个进程持有时 yield False
        （repositories.maintenance）。
        """
        raise NotImplementedError

    # -- 删除队列 -------------------------------------------------------------

    @abstractmethod
    def enqueue_image_deletion(self, image_id: UUID | str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def list_pending_deletions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def complete_deletion(self, image_id: UUID | str) -> None:
        raise NotImplementedError

    @abstractmethod
    def count_pending_deletions(self) -> int:
        raise NotImplementedError

    # -- 任务中心 ---------------------------------------------------------------

    @abstractmethod
    def record_index_tasks(self, rows: list[dict[str, Any]]) -> None:
        """index_tasks：每个入库的文件一行 {filename, status, image_id?, new_image?,
        error_code?, error_detail?, duration_ms?, trace_id?}。
        """
        raise NotImplementedError

    # -- 生命周期 ---------------------------------------------------------------

    @abstractmethod
    def ping(self) -> dict[str, Any]:
        raise NotImplementedError

    def close(self) -> None:
        """释放连接池（可重复调用）。"""


class EmbeddingModelStorePort(ABC):
    """模型版本（embedding_models）及其台账，给 /api/embedding-models 和向量构建用
    （services.retrieval.model_versions / build_embeddings）。
    """

    @abstractmethod
    def list_embedding_models(self) -> dict[str, Any]:
        """{images, models: [行 + embedded + behind]}，最早的版本在前。"""
        raise NotImplementedError

    @abstractmethod
    def list_embedding_model_rows(self, *, statuses: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        """各版本的行，不带计数（给了 statuses 时只返回这些状态）。"""
        raise NotImplementedError

    @abstractmethod
    def get_embedding_model(self, model_id: int) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    def register_embedding_model(self, fields: dict[str, Any]) -> dict[str, Any]:
        """新版本，状态为 building（名称 + 版本或 collection 已被占用时为 EMBEDDING_MODEL_EXISTS）。"""
        raise NotImplementedError

    @abstractmethod
    def record_model_fingerprint(self, model_id: int, fingerprint: dict[str, Any]) -> bool:
        """填上缺失的指纹；从不覆盖已有的。"""
        raise NotImplementedError

    @abstractmethod
    def set_embedding_model_status(self, model_id: int, status: str, *, expected: tuple[str, ...]) -> bool:
        raise NotImplementedError

    @abstractmethod
    def activate_embedding_model(self, model_id: int) -> dict[str, Any]:
        """{model, previous_id}：这个版本成为当前版本，之前的当前版本变为 ready。"""
        raise NotImplementedError

    @abstractmethod
    def retire_embedding_model(self, model_id: int) -> dict[str, Any]:
        """{model, ledger_rows}：状态变为 retired，台账行已删除（不能是当前版本）。"""
        raise NotImplementedError

    @abstractmethod
    def count_embedding_backlog(self, model_id: int) -> int:
        """在该版本 collection 里没有向量的已存图片数。"""
        raise NotImplementedError

    @abstractmethod
    def list_embedding_backlog(
        self, model_id: int, *, after: UUID | str | None = None, limit: int = 16, exclude: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """在该版本 collection 里没有向量的图片的 [{id, original_url}]，按 id 顺序。"""
        raise NotImplementedError

    @abstractmethod
    def record_embeddings(self, *, model_id: int, versions: dict[str, int]) -> list[str]:
        """刚写入的向量点的台账行（{图片 id: payload_version}）；返回记录了的 id
        （这期间被删除的图片不记录）。
        """
        raise NotImplementedError

    @abstractmethod
    def sample_embedded_images(self, model_id: int, *, limit: int = 3) -> list[dict[str, Any]]:
        """最近记录在该版本 collection 里的图片的 [{id, original_url}]。"""
        raise NotImplementedError

    @abstractmethod
    def list_embedded_ids(self, model_id: int, *, after: UUID | str | None = None, limit: int = 256) -> list[str]:
        """在该版本下有台账行的图片 id，按 id 顺序（用来检查它们的向量点是否存在）。"""
        raise NotImplementedError

    @abstractmethod
    def forget_embeddings(self, model_id: int, image_ids: list[str]) -> int:
        """删除这些台账行（它们的向量点不在了）；返回删除了多少。"""
        raise NotImplementedError

    @abstractmethod
    def clear_embeddings(self, model_id: int) -> int:
        """删除该版本的所有台账行（它的 collection 重新建了）。"""
        raise NotImplementedError

    @abstractmethod
    def refresh_model_fingerprint(self, model_id: int, fingerprint: dict[str, Any]) -> bool:
        """记录同一个编码器在新路径下的指纹（以及 weights_uri）。"""
        raise NotImplementedError


class CatalogStorePort(ABC):
    """类别树和场景配置，即管理页编辑的内容。

    只用 PostgreSQL：编码器、Qdrant 或对象存储挂了时，管理用例
    （services.retrieval.manage_categories / manage_scenes）照样能用。每次写入是目录锁
    （repositories.catalog_lock）下的一个事务。由 PgMetadataStore 实现。
    """

    # -- 类别树 ---------------------------------------------------------------

    @abstractmethod
    def list_categories(self) -> list[dict[str, Any]]:
        """所有节点，父节点在子节点之前，同级按显示顺序。"""
        raise NotImplementedError

    @abstractmethod
    def list_heating_groups(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def create_category(self, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def update_category(self, category_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        """修改传来的字段；parent_id 会把节点连同子树一起移动。移动或修改菜系时，在同一个事务里
        重算节点下图片的场景；不推送向量 payload（由 HTTP 层在提交后推送）。

        {category, scene_membership: None | {images, changed_ids}}。
        """
        raise NotImplementedError

    @abstractmethod
    def delete_category(self, category_id: int) -> dict[str, Any]:
        """删除一个没有被使用的节点；否则 CATEGORY_IN_USE。"""
        raise NotImplementedError

    # -- 图片的场景归属（只用 PG；写入返回一致的快照）--

    @abstractmethod
    def get_scene_memberships(self, image_id: UUID | str) -> dict[str, Any]:
        """完整的场景目录、生效的 code 和图片的 payload_version；不重算。"""
        raise NotImplementedError

    @abstractmethod
    def put_manual_scene_membership(
        self, image_id: UUID | str, scene_id: int, *, is_member: bool
    ) -> dict[str, Any]:
        """原子地新增或更新手工覆盖并重算；不推送向量 payload（检索服务就绪时，由 HTTP 层
        在提交后推送）。
        """
        raise NotImplementedError

    @abstractmethod
    def clear_manual_scene_membership(self, image_id: UUID | str, scene_id: int) -> dict[str, Any]:
        """只清空手工覆盖并原子地重算（停用的场景也可以清空）。"""
        raise NotImplementedError

    # -- 单张已存图片（只用 PG；向量库由 HTTP 层负责跟上）--

    @abstractmethod
    def get_image_detail(self, image_id: UUID | str) -> dict[str, Any] | None:
        """带类别、加热组、食物、生效场景及其台账行（embeddings）的图片，在同一个快照里读取；
        没有这张图片时为 None。
        """
        raise NotImplementedError

    @abstractmethod
    def patch_image(self, image_id: UUID | str, fields: dict[str, Any]) -> dict[str, Any]:
        """用 app.core.rules.image_fields 校验修改并在一个事务里应用；返回提交后的详情；
        没有这张图片时为 IMAGE_NOT_FOUND。
        """
        raise NotImplementedError

    @abstractmethod
    def enqueue_image_deletion(self, image_id: UUID | str) -> dict[str, Any]:
        """删除图片，并把它的向量 / 文件清理加入队列（image_deletions）；返回队列行。
        既没有图片也没有队列行时为 IMAGE_NOT_FOUND。
        """
        raise NotImplementedError

    @abstractmethod
    def get_image_file_urls(self, image_id: UUID | str) -> dict[str, Any] | None:
        """{original_url, thumb_url, deleted}：已存图片的，或者文件等待清理的已删除图片的；
        两边都不认识这个 id 时为 None。
        """
        raise NotImplementedError

    # -- 任务中心（只用 PG：检索服务挂了时也能读）------

    @abstractmethod
    def list_index_tasks(
        self,
        *,
        status: str | None = None,
        image_state: str | None = None,
        new_image: bool | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """最新的在前：{items, total, counts: {succeeded, failed, image_deleted, repeated}}；图片仍存在
        时，每一项带上它的 thumb_url / 类别（image_exists）。
        image_state 过滤成功的记录：present（图片仍存在）/ deleted（之后被删除了）；new_image
        过滤存了新图片的（True）/ 重复上传已存图片的（False）。counts.repeated：仍存在、
        而且之前就存过的图片的成功记录（new_image 为 false）；它和 image_deleted 都是 succeeded
        的一部分。
        """
        raise NotImplementedError

    @abstractmethod
    def clear_index_tasks(self, *, status: str | None = None, image_state: str | None = None) -> int:
        """删除列表在这个过滤条件下显示的记录（status 和 / 或 image_state，至少一个）；
        返回删除了多少。不碰图片。
        """
        raise NotImplementedError

    @abstractmethod
    def delete_index_task(self, task_id: int) -> int:
        """删除一条记录：存在时返回 1，不存在时返回 0。"""
        raise NotImplementedError

    @abstractmethod
    def list_pending_deletions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """排队的删除，最早的在前：{image_id, original_url, thumb_url, deleted_at}。"""
        raise NotImplementedError

    @abstractmethod
    def count_pending_deletions(self) -> int:
        raise NotImplementedError

    # -- 场景（每次写入返回整个场景：字段、标签、规则，加上 sync_required：
    #    已存图片是否需要 POST /api/scenes/sync；删除返回 {id, code, sync_required}）

    @abstractmethod
    def list_scene_configs(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def create_scene(self, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def update_scene(self, scene_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def delete_scene(self, scene_id: int) -> dict[str, Any]:
        """删除场景及其标签、规则和归属；返回 {id, code}。"""
        raise NotImplementedError

    @abstractmethod
    def create_scene_label(self, scene_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def update_scene_label(self, scene_id: int, label_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def delete_scene_label(self, scene_id: int, label_id: int) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def create_scene_rule(self, scene_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def update_scene_rule(self, scene_id: int, rule_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def delete_scene_rule(self, scene_id: int, rule_id: int) -> dict[str, Any]:
        raise NotImplementedError

    # -- 生命周期 ---------------------------------------------------------------

    @abstractmethod
    def ping(self) -> dict[str, Any]:
        raise NotImplementedError

    def close(self) -> None:
        """释放连接池（可重复调用）。"""


class FlyBFallbackPort(ABC):
    """PG v0.1：FlyB 不在范围内（M0）。只为兼容旧的导入而保留。"""

    @abstractmethod
    def search_fallback(
        self,
        image_bytes: bytes | None = None,
        image_url: str | None = None,
        top_k: int = 10,
    ) -> Any:
        raise NotImplementedError


class ImageStoragePort(ABC):
    """S3/OSS 对象存储（契约 §5）。为数据库列返回完整的 https URL。"""

    @abstractmethod
    def put_original(
        self,
        image_id: UUID | str,
        data: bytes,
        *,
        ext: str,
        content_type: str | None = None,
    ) -> str:
        """上传 original/{shard}/{image_id}.{ext}（shard：SHA-256[0:2] 的十六进制；可选 key_prefix）；
        返回完整的 https original_url。
        """
        raise NotImplementedError

    @abstractmethod
    def put_thumb(
        self,
        image_id: UUID | str,
        data: bytes,
        *,
        content_type: str | None = "image/webp",
    ) -> str:
        """上传 thumb/{shard}/{image_id}.webp（和原图同一个分片；可选 key_prefix）；
        返回完整的 https thumb_url。
        """
        raise NotImplementedError

    @abstractmethod
    def put_staging(
        self,
        candidate_id: UUID | str,
        data: bytes,
        *,
        ext: str,
        content_type: str | None = None,
    ) -> str:
        """上传 staging/{candidate_id}.{ext}（可选 key_prefix）；返回完整的 https staged_url。"""
        raise NotImplementedError

    @abstractmethod
    def read_by_url_or_key(self, url_or_key: str) -> bytes:
        """按完整的 https URL 或对象 key 读取已存对象的字节（重新计算 embedding 时读回原图）。
        对象不存在或读不了时抛出 IMAGE_FILE_UNAVAILABLE。
        """
        raise NotImplementedError

    @abstractmethod
    def delete_by_url_or_key(self, url_or_key: str) -> None:
        """按完整的 https URL 或对象 key 尽力删除。对象不存在 = 成功。"""
        raise NotImplementedError

    @abstractmethod
    def ping(self) -> dict[str, Any]:
        """{ok, driver, bucket, ...}；从不抛出。"""
        raise NotImplementedError

