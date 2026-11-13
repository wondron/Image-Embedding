"""Plan category codes from structural parent links, without touching the database."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from app.core import errors as err
from app.core.rules.category_fields import normalize_category_code


def plan_subtree_codes(
    nodes: Sequence[Mapping[str, Any]], root_id: int, root_code: str,
) -> dict[int, str]:
    """Keep each descendant's last code segment under its actual parent's new code.

    This also repairs older flat codes while moving a subtree. The caller checks
    the resulting codes against nodes outside the subtree before writing anything.
    """
    by_id = {int(node["id"]): node for node in nodes}
    if root_id not in by_id or len(by_id) != len(nodes):
        raise err.validation_error("invalid category subtree")
    root_parent = by_id[root_id]["parent_id"]
    if root_parent is not None and int(root_parent) in by_id:
        raise err.validation_error("category subtree contains a cycle")
    children: dict[int, list[int]] = defaultdict(list)
    for node in nodes:
        nid = int(node["id"])
        if nid != root_id and node["parent_id"] is not None:
            children[int(node["parent_id"])].append(nid)
    planned = {root_id: normalize_category_code(root_code)}
    owners = {planned[root_id].lower(): root_id}
    pending = [root_id]
    while pending:
        parent_id = pending.pop()
        for nid in children[parent_id]:
            if nid in planned:
                raise err.validation_error("category subtree contains a cycle")
            segment = str(by_id[nid]["code"]).rsplit(".", 1)[-1]
            code = normalize_category_code(f"{planned[parent_id]}.{segment}")
            if code.lower() in owners:
                raise err.category_code_exists(f"subtree codes would conflict ignoring case: {code!r}")
            planned[nid] = code
            owners[code.lower()] = nid
            pending.append(nid)
    if len(planned) != len(nodes):
        raise err.validation_error("category subtree contains an invalid parent or cycle")
    return planned
