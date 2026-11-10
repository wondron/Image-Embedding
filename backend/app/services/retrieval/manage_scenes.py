"""Scene configuration admin use case: scenes, their labels and their rules.

Field rules live in app.services.retrieval.scene_fields; the checks against the stored
configuration and the writes are one transaction each in the catalog store
(app.repositories.scene_config). Needs PostgreSQL only (app.bootstrap.catalog).

Every write answers with the whole scene. Changes to rules or to a scene's is_active /
include_all reach stored images when the memberships are recomputed
(services.retrieval.sync_scenes); label changes show at the next search.
"""

from __future__ import annotations

from typing import Any

from app.core.scene_rules import DEFAULT_SCENE
from app.repositories.ports import CatalogStorePort


class ManageScenesUseCase:
    def __init__(self, catalog: CatalogStorePort) -> None:
        self._catalog = catalog

    def list(self) -> dict[str, Any]:
        """Every scene with its labels and rules, and the scene requests default to."""
        return {"scenes": self._catalog.list_scene_configs(), "default_scene": DEFAULT_SCENE}

    def create(self, fields: dict[str, Any]) -> dict[str, Any]:
        return self._catalog.create_scene(fields)

    def update(self, scene_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        return self._catalog.update_scene(scene_id, fields)

    def delete(self, scene_id: int) -> dict[str, Any]:
        """{id, code} of the deleted scene."""
        return self._catalog.delete_scene(scene_id)

    def create_label(self, scene_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        return self._catalog.create_scene_label(scene_id, fields)

    def update_label(self, scene_id: int, label_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        return self._catalog.update_scene_label(scene_id, label_id, fields)

    def delete_label(self, scene_id: int, label_id: int) -> dict[str, Any]:
        return self._catalog.delete_scene_label(scene_id, label_id)

    def create_rule(self, scene_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        return self._catalog.create_scene_rule(scene_id, fields)

    def update_rule(self, scene_id: int, rule_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        return self._catalog.update_scene_rule(scene_id, rule_id, fields)

    def delete_rule(self, scene_id: int, rule_id: int) -> dict[str, Any]:
        return self._catalog.delete_scene_rule(scene_id, rule_id)
