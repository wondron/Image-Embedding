"""categories / category_names / heating_groups as the category admin page reads and edits them.

Reads return the whole tree: it is small (hundreds to a few thousand nodes) and the page needs
every node to draw it and to offer move targets, so there is no paging.

Each write is one transaction and first takes a transaction-level advisory lock, so two writes
through this store cannot interleave their checks (A moves under B while B moves under A; a
child is added while its parent is deleted). The database stays the last guard for writers
that bypass this store: unique code, RESTRICT on children and images, and the path trigger
(migration 0001), which rebuilds the paths of the subtree whenever parent_id changes.

Deleting refuses a category that is still used. scene_rules would go silently (ON DELETE
CASCADE) and leave image_scenes stale, so a rule counts as a use too.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core import errors as err
from app.core.category_rules import CATEGORY_DISPLAY_LANG
from app.core.enums import NameKind
from app.db.common import _row_to_dict
from app.services.retrieval.category_fields import (
    check_root_code,
    display_name,
    normalize_category_fields,
    normalize_category_id,
)

# pg_advisory_xact_lock key shared by every category tree write ("CATE")
_TREE_LOCK_KEY = 0x43415445

# One node as the API returns it. names: the display language first, then by language; in a
# language the primary name first. Binds :display_lang and :primary_kind.
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
# Columns a patch sets with a plain bind (cuisines needs a cast; names is another table)
_PLAIN_COLUMNS = ("code", "parent_id", "heating_group_id", "sort_order", "is_active")


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


def _lock_tree(conn: Any) -> None:
    conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _TREE_LOCK_KEY})


def _check_code_free(conn: Any, code: str) -> None:
    taken = conn.execute(text("SELECT 1 FROM categories WHERE code = :code"), {"code": code}).fetchone()
    if taken is not None:
        raise err.category_code_exists(f"another category already uses the code {code!r}")


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
    """A constraint that fired although the checks passed: a writer outside this store got in between."""
    name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None) or "unknown"
    if name == "uq_categories_code":
        return err.category_code_exists("another category already uses this code")
    if op == "delete_category":
        return err.category_in_use(f"the category is still referenced ({name})")
    return err.validation_error(f"{op} breaks the database constraint {name}")


class PgCategoriesMixin:
    """The category tree: list, create, patch (rename / move / relabel), delete."""

    def list_categories(self) -> list[dict[str, Any]]:
        """Every node: parents before their children, siblings in display order (sort_order, id)."""
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
        """The heating groups a category can point at (a short reference list)."""
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
        """Insert one node with its names and return it; the trigger fills path."""
        new = normalize_category_fields(fields, partial=False)
        self.ensure_schema()
        engine = self._get_engine()
        try:
            with engine.begin() as conn:
                _lock_tree(conn)
                parent_id = new["parent_id"]
                if parent_id is None:
                    check_root_code(new["code"])
                elif conn.execute(
                    text("SELECT 1 FROM categories WHERE id = :id"), {"id": parent_id}
                ).fetchone() is None:
                    raise err.validation_error(f"parent_id {parent_id} does not exist")
                _check_code_free(conn, new["code"])
                _check_heating_group(conn, new["heating_group_id"])
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
        """Patch the given fields and return the node. names, when given, replaces every name.

        Changing parent_id moves the node with its whole subtree. This does not recompute
        image_scenes of the images under it (nor does a change of cuisines).
        """
        cid = normalize_category_id(category_id)
        changes = normalize_category_fields(fields, partial=True)
        self.ensure_schema()
        engine = self._get_engine()
        try:
            with engine.begin() as conn:
                _lock_tree(conn)
                current = conn.execute(
                    text(
                        "SELECT code, parent_id, path::text AS path FROM categories "
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
                if moved and parent_id is not None:
                    parent = conn.execute(
                        text(
                            "SELECT path <@ CAST(:own AS ltree) AS inside FROM categories WHERE id = :id"
                        ),
                        {"id": parent_id, "own": current["path"]},
                    ).mappings().fetchone()
                    if parent is None:
                        raise err.validation_error(f"parent_id {parent_id} does not exist")
                    if parent["inside"]:
                        raise err.validation_error(
                            "a category cannot move under itself or one of its descendants"
                        )
                # Only when the code or the position changes: an older root keeps whatever code it has
                if parent_id is None and (renamed or moved):
                    check_root_code(code)
                if renamed:
                    _check_code_free(conn, code)
                _check_heating_group(conn, changes.get("heating_group_id"))

                assignments = [f"{column} = :{column}" for column in _PLAIN_COLUMNS if column in changes]
                if "cuisines" in changes:
                    assignments.append("cuisines = CAST(:cuisines AS text[])")
                assignments.append("updated_at = now()")  # raw SQL: onupdate does not fire
                conn.execute(
                    text(f"UPDATE categories SET {', '.join(assignments)} WHERE id = :id"),
                    {"id": cid, **{key: value for key, value in changes.items() if key != "names"}},
                )
                if "names" in changes:
                    conn.execute(
                        text("DELETE FROM category_names WHERE category_id = :id"), {"id": cid}
                    )
                    _write_names(conn, cid, changes["names"])
                return _read_node(conn, cid)
        except err.AppError:
            raise
        except IntegrityError as exc:
            raise _constraint_error("update_category", exc) from exc
        except Exception as exc:
            raise err.category_postgres_failed(f"update_category: {type(exc).__name__}: {exc}") from exc

    def delete_category(self, category_id: int) -> dict[str, Any]:
        """Delete a node nothing uses (its names go with it); returns {id, code}."""
        cid = normalize_category_id(category_id)
        self.ensure_schema()
        engine = self._get_engine()
        try:
            with engine.begin() as conn:
                _lock_tree(conn)
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
