"""Composition root for the catalog admin (category tree, scene configuration, stored images,
task center).

Needs PostgreSQL only, so it is built on its own: the admin pages keep working while the
encoder, Qdrant or object storage are down, or when retrieval is switched off
(TOTAL_CONFIG.retrieval_enabled: false). The retrieval stack borrows this store
(app.bootstrap.retrieval_stack, metadata=...) so the API process keeps one connection pool.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.clients.image_storage import build_image_storage
from app.core.config.settings import ImageStorageSettings, PostgresSettings
from app.repositories.metadata import PgMetadataStore
from app.repositories.ports import ImageStoragePort
from app.services.retrieval.list_images import ListImagesUseCase
from app.services.retrieval.manage_categories import ManageCategoriesUseCase
from app.services.retrieval.manage_images import ManageImagesUseCase
from app.services.retrieval.manage_scenes import ManageScenesUseCase
from app.services.retrieval.task_center import TaskCenterUseCase

logger = logging.getLogger("catalog")


@dataclass
class CatalogServices:
    """What the catalog HTTP layer calls. Owns the PostgreSQL store."""

    store: PgMetadataStore
    categories_uc: ManageCategoriesUseCase
    scenes_uc: ManageScenesUseCase
    images_uc: ManageImagesUseCase
    tasks_uc: TaskCenterUseCase
    list_uc: ListImagesUseCase
    config: Mapping[str, Any] = field(default_factory=dict, repr=False)
    _file_storage: ImageStoragePort | None = field(default=None, init=False, repr=False)
    _file_storage_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def file_storage(self) -> ImageStoragePort:
        """Object storage to read stored files back while the retrieval stack is not up (built
        on first use from IMAGE_STORAGE_CONFIG; the mock driver reads its mirror dir)."""
        with self._file_storage_lock:
            if self._file_storage is None:
                self._file_storage = build_image_storage(ImageStorageSettings.from_config(self.config))
            return self._file_storage

    def close(self) -> None:
        self.store.close()


def build_catalog(config: Mapping[str, Any]) -> CatalogServices:
    """POSTGRES_CONFIG -> store (schema checked) -> admin use cases."""
    pg = PostgresSettings.from_config(config)
    store = PgMetadataStore(url=pg.url(), connect_timeout=pg.connect_timeout)
    try:
        store.ensure_schema()
    except BaseException:
        store.close()
        raise
    logger.info("catalog ready | postgres=%s/%s", store.host, store.database)
    return CatalogServices(
        store=store,
        categories_uc=ManageCategoriesUseCase(store),
        scenes_uc=ManageScenesUseCase(store),
        images_uc=ManageImagesUseCase(store),
        tasks_uc=TaskCenterUseCase(store),
        list_uc=ListImagesUseCase(store),
        config=config,
    )
