"""生效的场景归属（契约 §2.8–2.9 / §4.2）。

策略（M5 的选择——写入时物化）：
  1. include_all 场景（general）启用时包含所有图片。它们从不写入 image_scenes，
     也不能手工覆盖（app.models.scene.ImageScene）。
  2. 对其他每个启用的场景按规则推导归属：命中任意一条 scene_rules 即可——
     类别子树（ltree 路径 <@ 规则路径），或者菜系在生效的类别菜系里
     （自己的，否则取最近的祖先的）。
  3. 再叠加 image_scenes 里 origin=manual 的手工覆盖（包含 / 排除）。
  4. 持久化：替换这张图 origin=rule 的行；手工的行保持不变。
  5. 生效的 code（include_all ∪ 规则 / 手工）→ Qdrant payload.scenes；归属 / 类别 /
     food_state / label_status 变化时递增 images.payload_version。
  停用的场景不参与：重新启用之前没有成员。

重算入口（app.repositories.scenes）：recompute_image_scenes 重算一张图，
recompute_scene_memberships 在目录变化后批量重算。类别到场景标签的映射在那里
是一条 SQL 查询（覆盖它的最深的、标签启用的规则）。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from app.core.enums import LabelStatus


def include_all_scene_ids(scenes: Sequence[Mapping[str, Any]]) -> set[int]:
    """隐式包含所有图片的场景（没有 image_scenes 行）。"""
    return {int(sc["id"]) for sc in scenes if sc.get("include_all")}


def effective_cuisines(
    category_row: Mapping[str, Any] | None,
    ancestors_root_first: Sequence[Mapping[str, Any]],
) -> list[str]:
    """自己的菜系不为空就用自己的；否则取第一个不为空的祖先的（从根到叶的顺序）。"""
    if category_row is not None:
        own = list(category_row.get("cuisines") or [])
        if own:
            return [str(c) for c in own]
    for anc in reversed(list(ancestors_root_first)):
        vals = list(anc.get("cuisines") or [])
        if vals:
            return [str(c) for c in vals]
    return []


def rule_matches_category(
    *,
    image_category_id: int | None,
    image_path: str | None,
    image_cuisines: Sequence[str],
    rule: Mapping[str, Any],
    rule_category_path: str | None,
) -> bool:
    """有一条 scene_rule 匹配这张图的类别 / 菜系时为 True。"""
    if image_category_id is None:
        return False
    rule_cat = rule.get("category_id")
    rule_cuisine = rule.get("cuisine")
    if rule_cat is not None:
        # 子树：图片路径等于规则类别路径，或在它下面（ltree <@）
        if not image_path or not rule_category_path:
            return int(rule_cat) == int(image_category_id)
        return _ltree_is_descendant_or_equal(image_path, rule_category_path)
    if rule_cuisine is not None and str(rule_cuisine).strip():
        return str(rule_cuisine).strip() in set(image_cuisines)
    return False


def _ltree_is_descendant_or_equal(child: str, ancestor: str) -> bool:
    c = str(child).strip()
    a = str(ancestor).strip()
    if not c or not a:
        return False
    return c == a or c.startswith(a + ".")


def compute_rule_member_scene_ids(
    *,
    scenes: Sequence[Mapping[str, Any]],
    rules_by_scene: Mapping[int, Sequence[Mapping[str, Any]]],
    rule_cat_paths: Mapping[int, str],
    image_category_id: int | None,
    image_path: str | None,
    image_cuisines: Sequence[str],
) -> set[int]:
    """非 include_all 场景里按规则推导出的成员（要持久化的行）。"""
    members: set[int] = set()
    for sc in scenes:
        sid = int(sc["id"])
        if sc.get("include_all"):
            continue
        for rule in rules_by_scene.get(sid, ()):
            rcat = rule.get("category_id")
            rpath = rule_cat_paths.get(int(rcat)) if rcat is not None else None
            if rule_matches_category(
                image_category_id=image_category_id,
                image_path=image_path,
                image_cuisines=image_cuisines,
                rule=rule,
                rule_category_path=rpath,
            ):
                members.add(sid)
                break
    return members


def apply_manual_overrides(
    rule_member_ids: set[int],
    manual_rows: Iterable[Mapping[str, Any]],
) -> set[int]:
    """手工的包含 / 排除优先于按规则的归属。"""
    effective = set(rule_member_ids)
    for row in manual_rows:
        sid = int(row["scene_id"])
        if bool(row.get("is_member")):
            effective.add(sid)
        else:
            effective.discard(sid)
    return effective


def build_qdrant_payload(
    *,
    category_id: int | None,
    scene_codes: Sequence[str],
    food_state: str | None,
    label_status: LabelStatus | str | None,
) -> dict[str, Any]:
    """契约 §4.2 的过滤用 payload（普通 JSON 字符串，取值和 PG 里相同）。"""
    return {
        "category_id": category_id,
        "scenes": [str(c) for c in scene_codes],
        "food_state": str(food_state) if food_state else None,
        "label_status": str(label_status or LabelStatus.UNLABELED),
    }
