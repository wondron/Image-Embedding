"""根据结构上的父子关系规划类别 code，不访问数据库。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from app.core import errors as err
from app.core.rules.category_fields import normalize_category_code


def plan_subtree_codes(
    nodes: Sequence[Mapping[str, Any]], root_id: int, root_code: str,
) -> dict[int, str]:
    """让每个后代的最后一段 code 跟在它实际父节点的新 code 后面。

    移动子树时这也会修复旧的扁平 code。调用方在写入之前，
    用子树之外的节点检查得到的 code。
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
