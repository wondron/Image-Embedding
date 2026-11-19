"""类别管理页读取和编辑的 categories / category_names / heating_groups。

读取返回整棵树：它很小（几百到几千个节点），页面需要所有节点来画树、提供可以移动到的
位置，所以不分页。

每次写入是一个事务，并且先获取目录锁（app.repositories.catalog_lock），这样两次写入的
检查不会交错（A 移到 B 下的同时 B 移到 A 下；删除父节点的同时添加子节点；为正在删除的
类别创建场景规则）。对绕过本存储的写入方，数据库仍是最后一道防线：code 唯一约束、
子节点和图片上的 RESTRICT，以及路径触发器（迁移 0001），parent_id 变化时它会重建
子树的路径。

移动类别或修改它的菜系会改变它下面的图片属于哪些场景。同一个事务重算它们的
image_scenes，并在生效集合变化时递增 payload_version，所以 PostgreSQL 永远不会和自己的
规则不一致，向量台账也能显示还有哪些需要推送（调用方在提交后立刻推送；对账任务负责补齐）。

删除会拒绝仍在使用的类别。scene_rules 会被悄悄删掉（ON DELETE CASCADE）并让 image_scenes
过时，所以规则也算一种使用。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core import errors as err
from app.core.category_rules import CATEGORY_DISPLAY_LANG, CATEGORY_MAX_ROOTS
from app.core.enums import NameKind
from app.db.common import _row_to_dict
from app.repositories.catalog_lock import lock_catalog_for_write
from app.repositories.scenes import _recompute_subtree
from app.core.rules.category_fields import (
    check_root_code,
    display_name,
    normalize_category_code,
    normalize_category_fields,
    normalize_category_id,
)
from app.core.rules.category_paths import plan_subtree_codes

# API 返回的一个节点。names：先是显示语言的，再按语言排序；同一种语言里主名称在前。
# 绑定 :display_lang 和 :primary_kind。
_NODE_SELECT = (
    "SELECT c.id, c.code, c.parent_id, c.path::text AS path, c.cuisines, c.sort_order, "
    "c.is_active, c.created_at, c.updated_at, "
    "hg.id AS heating_group_id, hg.code AS heating_group_code, hg.name AS heating_group_name, "
    "(SELECT count(*) FROM images i WHERE i.category_id = c.id) AS image_count, "
    "(SELECT count(*) FROM scene_rules r WHERE r.category_id = c.id) AS scene_rule_count, "
    "COALESCE(("
    "  SELECT json_agg(json_build_object('lang', n.lang, 'name', n.name, 'kind', n.kind) "
    "    ORDER BY (n.lang <> :display_lang), n.lang, (n.kind <> :primary_kind), n.name) "
    "  FROM category_names n WHERE n.category_id = c.id"
    "), '[]'::json) AS names "
    "FROM categories c LEFT JOIN heating_groups hg ON hg.id = c.heating_group_id "
)
_NODE_PARAMS = {"display_lang": CATEGORY_DISPLAY_LANG, "primary_kind": NameKind.PRIMARY.value}
# 修改请求用普通绑定设置的列（cuisines 需要类型转换；names 在另一张表里）
_PLAIN_COLUMNS = ("code", "parent_id", "heating_group_id", "sort_order", "is_active")


def _check_root_capacity(conn: Any) -> None:
    """在独占的目录锁下、添加根节点（包括移动）之前执行。

    停用的和旧的根节点也要计入；已经存了其他 code 的旧根节点时，只靠领域白名单
    无法限制总数。
    """
    count = conn.execute(text("SELECT count(*) FROM categories WHERE parent_id IS NULL")).scalar_one()
    if count >= CATEGORY_MAX_ROOTS:
        raise err.validation_error(f"at most {CATEGORY_MAX_ROOTS} root categories are allowed")


def _rewrite_subtree_codes(conn: Any, category_id: int, path: str, root_code: str) -> None:
    """在调用方已加锁的目录事务里预检并写入所有 code。"""
    nodes = conn.execute(
        text("SELECT id, parent_id, code FROM categories WHERE path <@ CAST(:path AS ltree) "
             "ORDER BY nlevel(path), id FOR UPDATE"),
        {"path": path},
    ).mappings().fetchall()
    planned = plan_subtree_codes(nodes, category_id, root_code)
    conflict = conn.execute(
        text("SELECT id, code FROM categories "
             "WHERE lower(code) = ANY(CAST(:codes AS text[])) "
             "AND NOT (id = ANY(CAST(:ids AS bigint[]))) LIMIT 1"),
        {"codes": [code.lower() for code in planned.values()], "ids": list(planned)},
    ).mappings().fetchone()
    if conflict is not None:
        raise err.category_code_exists(
            f"subtree code conflicts with category {conflict['id']}: {conflict['code']!r} (ignoring case)"
        )
    changed = [{"node_id": int(node["id"]), "code": planned[int(node["id"])]}
               for node in nodes if str(node["code"]) != planned[int(node["id"])]]
    if not changed:
        return
    # 如果某个目标值暂时属于这个子树里的另一个节点，不可延迟的唯一索引可能会拒绝
    # 一个合法的最终方案。所以只暂存 code 值；ltree 父节点触发器要到下面真正更新
    # 父节点时才会触发。
    token = uuid.uuid4().hex
    conn.execute(
        text("UPDATE categories SET code = :code WHERE id = :node_id"),
        [{"node_id": row["node_id"], "code": f"__category_recode_{token}_{row['node_id']}"} for row in changed],
    )
    conn.execute(text("UPDATE categories SET code = :code, updated_at = now() WHERE id = :node_id"), changed)


def _node(row: Any) -> dict[str, Any]:
    node = _row_to_dict(row)
    group_id = node.pop("heating_group_id")
    group_code = node.pop("heating_group_code")
    group_name = node.pop("heating_group_name")
    node["heating_group"] = (
        None if group_id is None else {"id": group_id, "code": group_code, "name": group_name}
    )
    node["cuisines"] = list(node["cuisines"] or [])
    node["names"] = list(node["names"] or [])
    node["name"] = display_name(node["names"])
    node["image_count"] = int(node["image_count"])
    node["scene_rule_count"] = int(node["scene_rule_count"])
    return node


def _read_node(conn: Any, category_id: int) -> dict[str, Any]:
    row = conn.execute(
        text(_NODE_SELECT + "WHERE c.id = :id"), {**_NODE_PARAMS, "id": category_id}
    ).mappings().one()
    return _node(row)


def _check_code_free(conn: Any, code: str, *, exclude_id: int | None = None) -> None:
    sql = "SELECT 1 FROM categories WHERE lower(code) = lower(:code)"
    params: dict[str, Any] = {"code": code}
    if exclude_id is not None:
        sql += " AND id <> :exclude_id"
        params["exclude_id"] = exclude_id
    taken = conn.execute(text(sql), params).fetchone()
    if taken is not None:
        raise err.category_code_exists(f"another category already uses the code {code!r} (ignoring case)")


def _check_heating_group(conn: Any, heating_group_id: int | None) -> None:
    if heating_group_id is None:
        return
    found = conn.execute(
        text("SELECT 1 FROM heating_groups WHERE id = :id"), {"id": heating_group_id}
    ).fetchone()
    if found is None:
        raise err.validation_error(f"heating_group_id {heating_group_id} does not exist")


def _write_names(conn: Any, category_id: int, names: list[dict[str, str]]) -> None:
    conn.execute(
        text(
            "INSERT INTO category_names (category_id, lang, name, kind) "
            "VALUES (:category_id, :lang, :name, :kind)"
        ),
        [{"category_id": category_id, **entry} for entry in names],
    )


def _constraint_error(op: str, exc: IntegrityError) -> err.AppError:
    """检查都通过了，约束却触发了：本存储之外的写入方插了进来。"""
    name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None) or "unknown"
    if name in {"uq_categories_code", "uq_categories_code_lower"}:
        return err.category_code_exists("another category already uses this code")
    if op == "delete_category":
        return err.category_in_use(f"the category is still referenced ({name})")
    return err.validation_error(f"{op} breaks the database constraint {name}")


class PgCategoriesMixin:
    """类别树：列出、创建、修改（改名 / 移动 / 重新标注）、删除。"""

    def list_categories(self) -> list[dict[str, Any]]:
        """所有节点：父节点在子节点之前，同级按显示顺序（sort_order、id）。"""
        self.ensure_schema()
        engine = self._get_engine()
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text(_NODE_SELECT + "ORDER BY nlevel(c.path), c.sort_order, c.id"), _NODE_PARAMS
                ).mappings().fetchall()
            return [_node(row) for row in rows]
        except Exception as exc:
            raise err.category_postgres_failed(f"list_categories: {type(exc).__name__}: {exc}") from exc

    def list_heating_groups(self) -> list[dict[str, Any]]:
        """类别可以指向的加热组（一个很短的参照列表）。"""
        self.ensure_schema()
        engine = self._get_engine()
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text("SELECT id, code, name FROM heating_groups ORDER BY id")
                ).mappings().fetchall()
            return [dict(row) for row in rows]
        except Exception as exc:
            raise err.category_postgres_failed(f"list_heating_groups: {type(exc).__name__}: {exc}") from exc

    def create_category(self, fields: dict[str, Any]) -> dict[str, Any]:
        """插入一个节点及其名称并返回它；path 由触发器填写。"""
        new = normalize_category_fields(fields, partial=False)
        self.ensure_schema()
        engine = self._get_engine()
        try:
            with engine.begin() as conn:
                lock_catalog_for_write(conn)
                parent_id = new["parent_id"]
                if parent_id is None:
                    check_root_code(new["code"])
                elif conn.execute(
                    text("SELECT 1 FROM categories WHERE id = :id"), {"id": parent_id}
                ).fetchone() is None:
                    raise err.validation_error(f"parent_id {parent_id} does not exist")
                _check_code_free(conn, new["code"])
                _check_heating_group(conn, new["heating_group_id"])
                if parent_id is None:
                    _check_root_capacity(conn)
                category_id = conn.execute(
                    text(
                        "INSERT INTO categories "
                        "(code, parent_id, cuisines, heating_group_id, sort_order, is_active) "
                        "VALUES (:code, :parent_id, CAST(:cuisines AS text[]), :heating_group_id, "
                        ":sort_order, :is_active) RETURNING id"
                    ),
                    {key: value for key, value in new.items() if key != "names"},
                ).scalar_one()
                _write_names(conn, category_id, new["names"])
                return _read_node(conn, category_id)
        except err.AppError:
            raise
        except IntegrityError as exc:
            raise _constraint_error("create_category", exc) from exc
        except Exception as exc:
            raise err.category_postgres_failed(f"create_category: {type(exc).__name__}: {exc}") from exc

    def update_category(self, category_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        """修改传来的字段。传了 names 时，替换所有名称。

        修改 parent_id 会把节点连同整个子树一起移动，并重建新父节点下的所有 code。
        只改 code 时也会原子地更新后代的 code。
        移动或修改菜系时，在同一个事务里重算标注为该节点或其下节点的所有图片的场景归属
        （repositories.scenes._recompute_subtree）。

        返回 {category: 节点, scene_membership: 没有归属可能变化时为 None，
        否则为 {images: 重算了多少张, changed_ids: 生效场景变化的图片}}。
        """
        cid = normalize_category_id(category_id)
        changes = normalize_category_fields(fields, partial=True)
        self.ensure_schema()
        engine = self._get_engine()
        try:
            with engine.begin() as conn:
                lock_catalog_for_write(conn)
                current = conn.execute(
                    text(
                        "SELECT code, parent_id, path::text AS path, cuisines FROM categories "
                        "WHERE id = :id FOR UPDATE"
                    ),
                    {"id": cid},
                ).mappings().fetchone()
                if current is None:
                    raise err.category_not_found(f"category {cid} does not exist")
                code = changes.get("code", current["code"])
                parent_id = changes["parent_id"] if "parent_id" in changes else current["parent_id"]
                renamed = code != current["code"]
                moved = parent_id != current["parent_id"]
                # 规则按包含关系匹配菜系，所以只有集合才重要
                recuisined = "cuisines" in changes and set(changes["cuisines"]) != set(current["cuisines"] or [])
                if moved and parent_id is not None:
                    parent = conn.execute(
                        text(
                            "SELECT code, path <@ CAST(:own AS ltree) AS inside FROM categories WHERE id = :id"
                        ),
                        {"id": parent_id, "own": current["path"]},
                    ).mappings().fetchone()
                    if parent is None:
                        raise err.validation_error(f"parent_id {parent_id} does not exist")
                    if parent["inside"]:
                        raise err.validation_error(
                            "a category cannot move under itself or one of its descendants"
                        )
                if moved:
                    segment = str(code).rsplit(".", 1)[-1]
                    code = normalize_category_code(f"{parent['code']}.{segment}" if parent_id is not None else segment)
                    changes["code"] = code
                    renamed = code != current["code"]
                # 只在 code 或位置变化时：旧的根节点保留它原有的 code
                if parent_id is None and (renamed or moved):
                    check_root_code(code)
                _check_heating_group(conn, changes.get("heating_group_id"))
                if moved and parent_id is None:
                    _check_root_capacity(conn)

                if moved or renamed:
                    _rewrite_subtree_codes(conn, cid, current["path"], code)

                assignments = [f"{column} = :{column}" for column in _PLAIN_COLUMNS if column in changes]
                if "cuisines" in changes:
                    assignments.append("cuisines = CAST(:cuisines AS text[])")
                assignments.append("updated_at = now()")  # 原生 SQL：onupdate 不会触发
                conn.execute(
                    text(f"UPDATE categories SET {', '.join(assignments)} WHERE id = :id"),
                    {"id": cid, **{key: value for key, value in changes.items() if key != "names"}},
                )
                if "names" in changes:
                    conn.execute(
                        text("DELETE FROM category_names WHERE category_id = :id"), {"id": cid}
                    )
                    _write_names(conn, cid, changes["names"])
                membership = None
                if moved or recuisined:
                    images, changed_ids = _recompute_subtree(conn, cid)
                    membership = {"images": images, "changed_ids": changed_ids}
                return {"category": _read_node(conn, cid), "scene_membership": membership}
        except err.AppError:
            raise
        except IntegrityError as exc:
            raise _constraint_error("update_category", exc) from exc
        except Exception as exc:
            raise err.category_postgres_failed(f"update_category: {type(exc).__name__}: {exc}") from exc

    def delete_category(self, category_id: int) -> dict[str, Any]:
        """删除一个没有被使用的节点（它的名称一起删除）；返回 {id, code}。"""
        cid = normalize_category_id(category_id)
        self.ensure_schema()
        engine = self._get_engine()
        try:
            with engine.begin() as conn:
                lock_catalog_for_write(conn)
                row = conn.execute(
                    text(
                        "SELECT c.code, "
                        "(SELECT count(*) FROM categories k WHERE k.parent_id = c.id) AS children, "
                        "(SELECT count(*) FROM images i WHERE i.category_id = c.id) AS images, "
                        "(SELECT count(*) FROM scene_rules r WHERE r.category_id = c.id) AS rules "
                        "FROM categories c WHERE c.id = :id FOR UPDATE OF c"
                    ),
                    {"id": cid},
                ).mappings().fetchone()
                if row is None:
                    raise err.category_not_found(f"category {cid} does not exist")
                uses = [
                    f"{label}: {count}"
                    for label, count in (
                        ("child categories", row["children"]),
                        ("images", row["images"]),
                        ("scene rules", row["rules"]),
                    )
                    if count
                ]
                if uses:
                    raise err.category_in_use(
                        f"category {row['code']!r} is still in use ({', '.join(uses)})"
                    )
                conn.execute(text("DELETE FROM categories WHERE id = :id"), {"id": cid})
            return {"id": cid, "code": row["code"]}
        except err.AppError:
            raise
        except IntegrityError as exc:
            raise _constraint_error("delete_category", exc) from exc
        except Exception as exc:
            raise err.category_postgres_failed(f"delete_category: {type(exc).__name__}: {exc}") from exc
