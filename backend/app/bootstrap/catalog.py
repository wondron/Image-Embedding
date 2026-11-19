"""目录管理的组合根（类别树、场景配置、已存图片、任务中心）。

只需要 PostgreSQL，所以单独构建：编码器、Qdrant 或对象存储挂了时，或者检索被关闭时
（TOTAL_CONFIG.retrieval_enabled: false），管理页照样能用。检索服务借用这个存储
（app.bootstrap.retrieval_stack，metadata=...），所以 API 进程只有一个连接池。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.bootstrap.dependency_logging import dependency_initialization
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
    """目录 HTTP 层调用的内容。持有 PostgreSQL 存储。"""

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
        """检索服务没起来时用来读回已存文件的对象存储（第一次使用时用 IMAGE_STORAGE_CONFIG 构建；
        mock 驱动读取它的镜像目录）。
        """
        with self._file_storage_lock:
            if self._file_storage is None:
                self._file_storage = build_image_storage(ImageStorageSettings.from_config(self.config))
            return self._file_storage

    def close(self) -> None:
        self.store.close()


def build_catalog(config: Mapping[str, Any]) -> CatalogServices:
    """POSTGRES_CONFIG -> 存储（检查表结构）-> 管理用例。"""
    with dependency_initialization(logger, config, "POSTGRES_CONFIG", stage="Catalog 配置及连接检查"):
        pg = PostgresSettings.from_config(config)
        store = PgMetadataStore(url=pg.url(), connect_timeout=pg.connect_timeout)
        try:
            store.ensure_schema()
        except BaseException:
            store.close()
            raise
    logger.info("Catalog 已就绪 | postgres=%s/%s", store.host, store.database)
    return CatalogServices(
        store=store,
        categories_uc=ManageCategoriesUseCase(store),
        scenes_uc=ManageScenesUseCase(store),
        images_uc=ManageImagesUseCase(store),
        tasks_uc=TaskCenterUseCase(store),
        list_uc=ListImagesUseCase(store),
        config=config,
    )
