"""类别树管理用例：读取整棵树，创建 / 修改 / 删除一个节点。

字段规则在 app.core.rules.category_fields；对照已存树的检查和写入，在 catalog 存储里
各自是一个事务（app.repositories.categories）。只需要 PostgreSQL（app.bootstrap.catalog）。

移动类别或修改它的菜系会改变它下面的图片属于哪些场景；存储在同一个事务里重算它们，
并说明哪些变了，由 HTTP 层在提交后推送它们的 payload（app.api.services.categories）。
"""

from __future__ import annotations

from typing import Any

from app.repositories.ports import CatalogStorePort
from app.core.rules.category_fields import ROOT_CODES


class ManageCategoriesUseCase:
    def __init__(self, catalog: CatalogStorePort) -> None:
        self._catalog = catalog

    def tree(self) -> dict[str, Any]:
        """管理页加载的内容：所有节点、节点可以指向的加热组，以及根节点可以用的 code。"""
        return {
            "categories": self._catalog.list_categories(),
            "heating_groups": self._catalog.list_heating_groups(),
            "root_codes": list(ROOT_CODES),
        }

    def create(self, fields: dict[str, Any]) -> dict[str, Any]:
        return self._catalog.create_category(fields)

    def update(self, category_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        """{category, scene_membership: None | {images, changed_ids}}。"""
        return self._catalog.update_category(category_id, fields)

    def delete(self, category_id: int) -> dict[str, Any]:
        """被删除节点的 {id, code}。"""
        return self._catalog.delete_category(category_id)
