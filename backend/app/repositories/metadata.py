"""PostgreSQL 元数据适配器门面（PG v0.1 契约 §2）。

M2：用 12 张表的 PG 结构替换 MySQL。
表结构归 Alembic 管（`alembic upgrade head`）；这个适配器负责 ping / 校验表，
并为检索服务的组装提供增删改查以及模型 / embedding 辅助函数。

拆分（DDD D2）：connection / images / embeddings / deletions / scenes（归属）/
categories / scene_config / maintenance（advisory lock）这些 mixin 在 app.repositories 里；本模块把它们组合成
PgMetadataStore。

假设 A7 / id 规则：images.id = uuid_from_bytes(sha256(original)[0:16])；
保留内容哈希的字节；**不要**改写 RFC 4122 的 version/variant 位。

categories.path：由数据库触发器 `categories_set_path`（迁移）维护；应用从不写 path；
禁止挂到自己的后代下面。

VectorStorePort 用 uuid（算法 M3）。适配器 persistence/qdrant_store 是对
adapter.persistence.qdrant_store.QdrantStore 的薄包装——没有第二个客户端。
"""

from __future__ import annotations

from app.db.common import (  # noqa: F401
    _as_uuid,
    _iso,
    _row_to_dict,
    _uuid_in_clause,
    logger,
)
from app.db.session import PgConnectionMixin
from app.repositories.categories import PgCategoriesMixin
from app.repositories.deletions import PgDeletionsMixin
from app.repositories.embeddings import PgEmbeddingsMixin
from app.repositories.images import PgImagesMixin
from app.repositories.maintenance import PgMaintenanceMixin
from app.repositories.ports import CatalogStorePort, EmbeddingModelStorePort, MetadataStorePort
from app.repositories.scene_config import PgSceneConfigMixin
from app.repositories.scenes import PgScenesMixin
from app.repositories.tasks import PgIndexTasksMixin


class PgMetadataStore(
    PgConnectionMixin,
    PgImagesMixin,
    PgEmbeddingsMixin,
    PgDeletionsMixin,
    PgIndexTasksMixin,
    PgScenesMixin,
    PgCategoriesMixin,
    PgSceneConfigMixin,
    PgMaintenanceMixin,
    MetadataStorePort,
    CatalogStorePort,
    EmbeddingModelStorePort,
):
    """通过 SQLAlchemy 2 + psycopg3 访问真实的 PostgreSQL；表结构由 Alembic 管理。

    一个存储，三个端口：给检索用例的 MetadataStorePort，给类别 / 场景管理的 CatalogStorePort，
    给模型版本的 EmbeddingModelStorePort。SQL 在各个 mixin 里。
    """

    pass
