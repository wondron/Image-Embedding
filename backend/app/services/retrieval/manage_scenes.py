"""场景配置管理用例：场景及其标签和规则。

字段规则在 app.core.rules.scene_fields；对照已存配置的检查和写入，在 catalog 存储里
各自是一个事务（app.repositories.scene_config）。只需要 PostgreSQL（app.bootstrap.catalog）。

配置写入返回 {scene, sync_required}（删除时为 {deleted, sync_required}）：
修改需要通过重算归属（services.retrieval.sync_scenes）才能作用到已存图片时，
sync_required 为 true——规则、场景的 is_active / include_all；标签、名称、描述和
outputs_heating 的修改在下一次请求时就生效。

手工归属的写入返回图片的归属快照（只用 PostgreSQL）；随后检索服务就绪时，
HTTP 层推送图片的向量 payload（app.api.services.scene_memberships）。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core import errors as err
from app.core.scene_rules import DEFAULT_SCENE
from app.repositories.ports import CatalogStorePort
from app.core.rules.scene_fields import normalize_row_id


def _scene_write(result: dict[str, Any]) -> dict[str, Any]:
    """根据存储写入返回的内容（带 sync_required 的场景）得到 {scene, sync_required}。"""
    scene = dict(result)
    return {"scene": scene, "sync_required": bool(scene.pop("sync_required", True))}


class ManageScenesUseCase:
    def __init__(self, catalog: CatalogStorePort) -> None:
        self._catalog = catalog

    def list(self) -> dict[str, Any]:
        """所有场景及其标签和规则，以及请求的默认场景。"""
        return {"scenes": self._catalog.list_scene_configs(), "default_scene": DEFAULT_SCENE}

    def create(self, fields: dict[str, Any]) -> dict[str, Any]:
        return _scene_write(self._catalog.create_scene(fields))

    def update(self, scene_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        return _scene_write(self._catalog.update_scene(scene_id, fields))

    def delete(self, scene_id: int) -> dict[str, Any]:
        """{deleted: {id, code}, sync_required}。"""
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
        """原地修改为新的标签和 / 或新的对象（category_id 或 cuisine）。"""
        return _scene_write(self._catalog.update_scene_rule(scene_id, rule_id, fields))

    def delete_rule(self, scene_id: int, rule_id: int) -> dict[str, Any]:
        return _scene_write(self._catalog.delete_scene_rule(scene_id, rule_id))

    def memberships(self, image_id: UUID) -> dict[str, Any]:
        """PG 里存的归属，不是未同步规则或 Qdrant 状态的预览。"""
        return self._catalog.get_scene_memberships(image_id)

    def set_membership(self, image_id: UUID, scene_id: int, *, is_member: bool) -> dict[str, Any]:
        """手工包含 / 排除，在 PostgreSQL 里重算并提交；返回快照。"""
        sid = normalize_row_id(scene_id, "scene_id")
        if not isinstance(is_member, bool):
            raise err.validation_error("is_member must be true or false")
        return self._catalog.put_manual_scene_membership(image_id, sid, is_member=is_member)

    def clear_membership(self, image_id: UUID, scene_id: int) -> dict[str, Any]:
        """去掉手工覆盖（重新由规则决定）；返回快照。"""
        sid = normalize_row_id(scene_id, "scene_id")
        return self._catalog.clear_manual_scene_membership(image_id, sid)
