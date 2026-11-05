"""Effective scene membership (contract §2.8–2.9 / §4.2).

STRATEGY (M5 choice — write-time materialize):
  1. Derive rule membership per active scene:
       - include_all=true → member
       - else any scene_rules hit: category subtree (ltree path <@ rule path)
         OR cuisine in effective category.cuisines (own else nearest ancestor)
  2. Apply image_scenes origin=manual overrides (include/exclude) on top.
  3. Persist: replace origin=rule rows for the image; leave manual rows intact.
  4. Effective codes → Qdrant payload.scenes; bump images.payload_version when
     membership/category/food_state/label_status changes.

Recompute entrypoint: MetadataStore / PgMetadataStore.recompute_image_scenes(image_id).
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence


def effective_cuisines(
    category_row: Mapping[str, Any] | None,
    ancestors_root_first: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Own cuisines if non-empty; else first non-empty ancestor (root→leaf order)."""
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
    """True if one scene_rule matches this image's category/cuisine."""
    if image_category_id is None:
        return False
    rule_cat = rule.get("category_id")
    rule_cuisine = rule.get("cuisine")
    if rule_cat is not None:
        # Subtree: image path is equal to or under rule category path (ltree <@)
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
    members: set[int] = set()
    for sc in scenes:
        sid = int(sc["id"])
        if sc.get("include_all"):
            members.add(sid)
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
    """Manual include/exclude wins over rule membership."""
    effective = set(rule_member_ids)
    for row in manual_rows:
        sid = int(row["scene_id"])
        if bool(row.get("is_member")):
            effective.add(sid)
        else:
            effective.discard(sid)
    return effective


def most_specific_label_id(
    matching_rules: Sequence[Mapping[str, Any]],
    rule_cat_paths: Mapping[int, str],
) -> int | None:
    """Among rules with label_id, pick deepest category path (contract §2.8)."""
    best: tuple[int, int] | None = None  # (depth, label_id)
    for rule in matching_rules:
        lid = rule.get("label_id")
        if lid is None:
            continue
        rcat = rule.get("category_id")
        path = rule_cat_paths.get(int(rcat)) if rcat is not None else ""
        depth = str(path or "").count(".") + (1 if path else 0)
        if best is None or depth > best[0]:
            best = (depth, int(lid))
    return best[1] if best else None


def build_qdrant_payload(
    *,
    category_id: int | None,
    scene_codes: Sequence[str],
    food_state: str | None,
    label_status: str,
) -> dict[str, Any]:
    """Contract §4.2 filter payload."""
    return {
        "category_id": category_id,
        "scenes": list(scene_codes),
        "food_state": food_state,
        "label_status": str(label_status or "unlabeled"),
    }
