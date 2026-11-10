"""Category tree admin use case: read the tree, create / patch / delete one node.

Field rules live in app.services.retrieval.category_fields; the checks against the stored tree
and the writes are one transaction each in the metadata store (app.repositories.categories).

Not done here: moving a category or changing its cuisines does not recompute image_scenes
of images already stored, and nothing re-pushes their Qdrant payload.
"""

from __future__ import annotations

from typing import Any

from app.repositories.ports import MetadataStorePort
from app.services.retrieval.category_fields import ROOT_CODES


class ManageCategoriesUseCase:
    def __init__(self, metadata: MetadataStorePort) -> None:
        self._metadata = metadata

    def tree(self) -> dict[str, Any]:
        """What the admin page loads: every node, the heating groups a node can point at, and
        the codes a root may use."""
        return {
            "categories": self._metadata.list_categories(),
            "heating_groups": self._metadata.list_heating_groups(),
            "root_codes": list(ROOT_CODES),
        }

    def create(self, fields: dict[str, Any]) -> dict[str, Any]:
        return self._metadata.create_category(fields)

    def update(self, category_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        return self._metadata.update_category(category_id, fields)

    def delete(self, category_id: int) -> dict[str, Any]:
        """{id, code} of the deleted node."""
        return self._metadata.delete_category(category_id)
