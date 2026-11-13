"""PostgreSQL metadata adapter facade (PG v0.1 contract §2).

M2: replace MySQL with 12-table PG schema.
Schema ownership: Alembic (`alembic upgrade head`); this adapter pings /
verifies tables and supplies CRUD + model/embedding helpers for stack wiring.

Split (DDD D2): connection / images / embeddings / deletions / scenes (membership) /
categories / scene_config / maintenance (advisory locks) mixins in app.repositories; this module composes them as
PgMetadataStore.

ASSUMPTION A7 / id policy: images.id = uuid_from_bytes(sha256(original)[0:16]);
retain content-hash bytes; do NOT rewrite RFC 4122 version/variant nibbles.

categories.path: DB trigger `categories_set_path` (migration); app never writes path;
forbid attaching under own descendant.

VectorStorePort is uuid (algo M3). Adapter persistence/qdrant_store thin-wraps
adapter.persistence.qdrant_store.QdrantStore — no second client.
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
    """Real PostgreSQL via SQLAlchemy 2 + psycopg3; schema via Alembic.

    One store, three ports: MetadataStorePort for the retrieval use cases, CatalogStorePort for
    the category / scene admin, EmbeddingModelStorePort for the model versions. SQL lives in
    the mixins.
    """

    pass
