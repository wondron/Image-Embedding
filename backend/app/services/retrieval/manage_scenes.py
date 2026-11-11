"""Scene configuration admin use case: scenes, their labels and their rules.

Field rules live in app.services.retrieval.scene_fields; the checks against the stored
configuration and the writes are one transaction each in the catalog store
(app.repositories.scene_config). Needs PostgreSQL only (app.bootstrap.catalog).

Configuration writes answer {scene, sync_required} ({deleted, sync_required} for a delete):
sync_required is true when the change has to reach stored images through the memberships
recompute (services.retrieval.sync_scenes) - rules, a scene's is_active / include_all; label,
name, description and outputs_heating changes show at the next request.

Manual membership writes return the image's membership snapshot (PostgreSQL only); the HTTP
layer then pushes the image's vector payload when the retrieval stack is up
(app.api.services.scene_memberships).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core import errors as err
from app.core.scene_rules import DEFAULT_SCENE
from app.repositories.ports import CatalogStorePort
from app.services.retrieval.scene_fields import normalize_row_id


def _scene_write(result: dict[str, Any]) -> dict[str, Any]:
    """{scene, sync_required} from what a store write answers (the scene with its sync_required)."""
    scene = dict(result)
    return {"scene": scene, "sync_required": bool(scene.pop("sync_required", True))}


class ManageScenesUseCase:
    def __init__(self, catalog: CatalogStorePort) -> None:
        self._catalog = catalog

    def list(self) -> dict[str, Any]:
        """Every scene with its labels and rules, and the scene requests default to."""
        return {"scenes": self._catalog.list_scene_configs(), "default_scene": DEFAULT_SCENE}

    def create(self, fields: dict[str, Any]) -> dict[str, Any]:
        return _scene_write(self._catalog.create_scene(fields))

    def update(self, scene_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        return _scene_write(self._catalog.update_scene(scene_id, fields))

    def delete(self, scene_id: int) -> dict[str, Any]:
        """{deleted: {id, code}, sync_required}."""
        deleted = dict(self._catalog.delete_scene(scene_id))
        return {"deleted": deleted, "sync_required": bool(deleted.pop("sync_required", True))}

    def create_label(self, scene_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        return _scene_write(self._catalog.create_scene_label(scene_id, fields))

    def update_label(self, scene_id: int, label_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        return _scene_write(self._catalog.update_scene_label(scene_id, label_id, fields))

    def delete_label(self, scene_id: int, label_id: int) -> dict[str, Any]:
        return _scene_write(self._catalog.delete_scene_label(scene_id, label_id))

    def create_rule(self, scene_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        return _scene_write(self._catalog.create_scene_rule(scene_id, fields))

    def update_rule(self, scene_id: int, rule_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        """A new label and / or a new target (category_id or cuisine), in place."""
        return _scene_write(self._catalog.update_scene_rule(scene_id, rule_id, fields))

    def delete_rule(self, scene_id: int, rule_id: int) -> dict[str, Any]:
        return _scene_write(self._catalog.delete_scene_rule(scene_id, rule_id))

    def memberships(self, image_id: UUID) -> dict[str, Any]:
        """Stored PG membership, not a preview of unsynced rules or Qdrant state."""
        return self._catalog.get_scene_memberships(image_id)

    def set_membership(self, image_id: UUID, scene_id: int, *, is_member: bool) -> dict[str, Any]:
        """Manual include / exclude, recomputed and committed in PostgreSQL; returns the snapshot."""
        sid = normalize_row_id(scene_id, "scene_id")
        if not isinstance(is_member, bool):
            raise err.validation_error("is_member must be true or false")
        return self._catalog.put_manual_scene_membership(image_id, sid, is_member=is_member)

    def clear_membership(self, image_id: UUID, scene_id: int) -> dict[str, Any]:
        """Drop the manual override (the rules decide again); returns the snapshot."""
        sid = normalize_row_id(scene_id, "scene_id")
        return self._catalog.clear_manual_scene_membership(image_id, sid)
