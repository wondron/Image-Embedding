"""Category tree admin use case: read the tree, create / patch / delete one node.

Field rules live in app.services.retrieval.category_fields; the checks against the stored tree
and the writes are one transaction each in the catalog store (app.repositories.categories).
Needs PostgreSQL only (app.bootstrap.catalog).

Moving a category or changing its cuisines changes which scenes its images belong to; stored
images follow when the memberships are recomputed (services.retrieval.sync_scenes).
"""

from __future__ import annotations

from typing import Any

from app.repositories.ports import CatalogStorePort
from app.services.retrieval.category_fields import ROOT_CODES


class ManageCategoriesUseCase:
    def __init__(self, catalog: CatalogStorePort) -> None:
        self._catalog = catalog

    def tree(self) -> dict[str, Any]:
        """What the admin page loads: every node, the heating groups a node can point at, and
        the codes a root may use."""
        return {
            "categories": self._catalog.list_categories(),
            "heating_groups": self._catalog.list_heating_groups(),
            "root_codes": list(ROOT_CODES),
        }

    def create(self, fields: dict[str, Any]) -> dict[str, Any]:
        return self._catalog.create_category(fields)

    def update(self, category_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        return self._catalog.update_category(category_id, fields)

    def delete(self, category_id: int) -> dict[str, Any]:
        """{id, code} of the deleted node."""
        return self._catalog.delete_category(category_id)
