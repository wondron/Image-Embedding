"""按 standard_tree.py 的 create_standard_tree 分类，将类别树添加到后端。

在项目根目录运行：
  python scripts/import_standard_tree.py --source F:/standard_tree.py
  python scripts/import_standard_tree.py --source F:/standard_tree.py --show-tree
  python scripts/import_standard_tree.py --source F:/standard_tree.py --apply

默认只预览，不访问服务器；--apply 通过 /api/categories 添加类别。
使用后端当前生效的数据库，无需另填数据库密码。可用 --base-url 指定后端。
Food 根映射到 ingredient（食材），其余节点保留原层级、名称、别称和同级顺序。
除根节点外，identifier 必须恰好是 6 个英文字母（A-Z / a-z）。
编码包含全部祖先，保留 identifier 大小写，例如 ingredient.YeTiLe.BaiSYe.DJiang；转小写后必须唯一。
编码冲突或不合法时，ERROR 日志会列出源文件行号、名称和 identifier，写入前停止。
请手动修改 create_node 的 identifier（第二个参数）及对应 parent 引用后重试。
源文件只静态解析，不执行，也不需要安装 numpy/treelib。
原识别逻辑字段（type/goto/compare/review 等）不作为类别属性导入。
已存在的编码按小写匹配，名称和父级相同时跳过；冲突会停止，不覆盖已有类别。
若后端仍有旧版 standard.n_... 编码，先日志提示并停止，避免重复导入另一套目录。
若后端已有旧版两段编码（如 ingredient.DJiang），会列出对应的完整路径并停止，供手动修改。
中断后可重新运行以继续添加；每个节点独立提交，不会整体回滚。
"""

from __future__ import annotations

import argparse
import ast
import json
import logging
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)
CODE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)*$")
IDENTIFIER_PATTERN = re.compile(r"[A-Za-z]{6}")


@dataclass(frozen=True)
class Category:
    source_id: str
    parent: str | None
    name: str
    aliases: tuple[str, ...]
    code: str
    order: int
    depth: int

    def body(self, parent_id: int | None) -> dict[str, Any]:
        return {
            "code": self.code,
            "parent_id": parent_id,
            "names": [
                {"lang": "zh", "name": self.name, "kind": "primary"},
                *({"lang": "zh", "name": alias, "kind": "alias"} for alias in self.aliases),
            ],
            "sort_order": self.order,
            "is_active": True,
        }


def category_code(identifier: str, parent_code: str = "ingredient") -> str:
    # Preserve the identifier, including its casing. Validate the whole tree before writing;
    # do not silently replace characters or add suffixes to resolve collisions.
    return f"{parent_code}.{identifier}"


def read_tree(path: Path) -> list[Category]:
    module = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    functions = [n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == "create_standard_tree"]
    if len(functions) != 1:
        raise ValueError("源文件需要恰好一个 create_standard_tree 函数")
    nodes: list[Category] = []
    seen: dict[str, Category] = {}
    locations: dict[str, int] = {}
    siblings: Counter[str | None] = Counter()
    for stmt in functions[0].body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            if (isinstance(stmt.targets[0], ast.Name) and stmt.targets[0].id == "tree"
                    and isinstance(stmt.value, ast.Call) and isinstance(stmt.value.func, ast.Name)
                    and stmt.value.func.id == "Tree" and not stmt.value.args and not stmt.value.keywords):
                continue
        if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Name) and stmt.value.id == "tree":
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
            continue
        call = stmt.value if isinstance(stmt, ast.Expr) else None
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name) and call.func.value.id == "tree"
                and call.func.attr == "create_node"):
            raise ValueError(f"第 {stmt.lineno} 行不受支持；仅解析静态 tree.create_node 调用")
        if len(call.args) > 4:
            raise ValueError(f"第 {stmt.lineno} 行 create_node 参数过多")
        fields = dict(zip(("tag", "identifier", "parent", "data"), map(ast.literal_eval, call.args)))
        for kw in call.keywords:
            if kw.arg not in ("tag", "identifier", "parent", "data") or kw.arg in fields:
                raise ValueError(f"第 {stmt.lineno} 行有不支持或重复的参数")
            fields[kw.arg] = ast.literal_eval(kw.value)
        identifier, name = fields.get("identifier"), fields.get("tag")
        parent, data = fields.get("parent"), fields.get("data")
        if not isinstance(identifier, str) or not identifier.strip() or identifier in seen:
            raise ValueError(f"第 {stmt.lineno} 行节点标识为空或重复：{identifier!r}")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"节点 {identifier!r} 缺少名称")
        if parent is None:
            if nodes:
                raise ValueError("源树必须只有一个根节点，且根节点在最前")
        elif not isinstance(parent, str) or parent not in seen:
            raise ValueError(f"节点 {identifier!r} 的父节点 {parent!r} 必须先定义")
        if data is not None and not isinstance(data, dict):
            raise ValueError(f"节点 {identifier!r} 的 data 必须是字典")
        aliases = (data or {}).get("别称", [])
        if isinstance(aliases, str):
            aliases = [aliases]
        elif isinstance(aliases, set):
            aliases = sorted(aliases)
        if not isinstance(aliases, (list, tuple)) or not all(isinstance(a, str) for a in aliases):
            raise ValueError(f"节点 {identifier!r} 的别称必须为字符串或字符串列表/集合")
        name = name.strip() if parent is not None else "食材"
        unique = tuple(dict.fromkeys(a.strip() for a in aliases if a.strip() and a.strip() != name))
        if len(unique) + 1 > 50 or any(len(n) > 128 for n in (name, *unique)):
            raise ValueError(f"节点 {identifier!r} 的名称长度或别称数量超出类别接口限制")
        node = Category(identifier, parent, name, unique,
                        category_code(identifier, seen[parent].code) if parent is not None else "ingredient",
                        siblings[parent], seen[parent].depth + 1 if parent is not None else 0)
        siblings[parent] += 1
        nodes.append(node)
        seen[identifier] = node
        locations[identifier] = stmt.lineno
    if not nodes:
        raise ValueError("源分类树为空")
    grouped: dict[str, list[Category]] = {}
    problems = 0
    for node in nodes:
        grouped.setdefault(node.code.lower(), []).append(node)
        if node.parent is not None and not IDENTIFIER_PATTERN.fullmatch(node.source_id):
            logger.error(
                "%s:%d 名称=%r identifier=%r 编码=%r 不合法："
                "identifier 必须恰好是 6 个英文字母（A-Z / a-z），当前长度=%d",
                path, locations[node.source_id], node.name, node.source_id, node.code, len(node.source_id),
            )
            problems += 1
        elif len(node.code) > 128 or not CODE_PATTERN.fullmatch(node.code):
            logger.error(
                "%s:%d 名称=%r identifier=%r 编码=%r 不合法："
                "编码只能含大小写字母、数字、下划线和点，总长度不超过 128",
                path, locations[node.source_id], node.name, node.source_id, node.code,
            )
            problems += 1
    for code, group in grouped.items():
        if len(group) > 1:
            logger.error("小写编码冲突 %r，共 %d 个节点：", code, len(group))
            for node in group:
                logger.error("  %s:%d 名称=%r identifier=%r parent=%r",
                             path, locations[node.source_id], node.name, node.source_id, node.parent)
            problems += 1
    if problems:
        raise ValueError(
            f"发现 {problems} 组编码问题，详见 ERROR 日志；"
            "请修改 identifier（create_node 第二个参数）及对应 parent 引用后重试。未访问后端、未导入。"
        )
    return nodes


class CategoryAPI:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.url = base_url.rstrip("/") + "/api/categories"
        self.timeout = timeout

    def request(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = Request(self.url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.load(response)
            if result.get("ok") is not True:
                raise ValueError(f"类别接口未返回成功：{result}")
            return result
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1500]
            raise RuntimeError(f"类别接口 HTTP {exc.code}：{detail}") from exc
        except (URLError, TimeoutError) as exc:
            raise RuntimeError(f"无法访问后端 {self.url}：{exc}") from exc

    def list_categories(self) -> list[dict[str, Any]]:
        return self.request()["categories"]

    def create_category(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.request(body)["category"]


def import_tree(nodes: list[Category], api: CategoryAPI) -> tuple[int, int]:
    existing = api.list_categories()
    normalized: dict[str, list[dict[str, Any]]] = {}
    for row in existing:
        normalized.setdefault(row["code"].lower(), []).append(row)
    duplicates = {code: rows for code, rows in normalized.items() if len(rows) > 1}
    if duplicates:
        for code, rows in duplicates.items():
            logger.error("后端已有小写编码冲突 %r，共 %d 个节点：", code, len(rows))
            for row in rows:
                logger.error("  已有节点 id=%s 名称=%r 编码=%r", row["id"], row.get("name"), row["code"])
        raise ValueError("后端已有转小写后重复的类别编码，请先处理日志中的冲突。本次未新增或修改节点。")
    legacy = [row for row in existing if row["code"].lower().startswith("ingredient.standard.n_")]
    if legacy:
        logger.error("后端已有 %d 个旧格式类别，新旧编码不同，继续导入会产生重复目录。", len(legacy))
        for row in legacy[:10]:
            logger.error("  已有节点 id=%s 名称=%r 编码=%r", row["id"], row.get("name"), row["code"])
        if len(legacy) > 10:
            logger.error("  另有 %d 个旧格式类别，请在类别管理中查看。", len(legacy) - 10)
        raise ValueError("请先核对并修改已导入节点的旧编码，再使用新编码导入。本次未新增或修改节点。")
    by_code = {code: rows[0] for code, rows in normalized.items()}
    by_id = {row["id"]: row for row in existing}
    source = {node.source_id: node for node in nodes}
    planned_codes = {node.code.lower() for node in nodes}
    flat_rows = []
    for node in nodes:
        if node.depth < 2:
            continue
        flat_code = category_code(node.source_id).lower()
        row = by_code.get(flat_code)
        # A planned top-level category legitimately keeps its two-segment code.
        # Other matching flat codes came from the earlier importer: do not create
        # a second node for the same source item under its new full-path code.
        if row is not None and flat_code not in planned_codes:
            flat_rows.append((row, node))
    if flat_rows:
        logger.error("检测到旧版两段编码；请先核对并修改现有节点编码，避免重复导入：")
        for row, node in flat_rows:
            logger.error("  已有节点 id=%s 名称=%r：%s → %s", row["id"], row.get("name"), row["code"], node.code)
        raise ValueError("检测到旧版两段编码，完整父级路径已在日志中列出。本次未新增或修改节点。")
    # Preflight the entire plan before the first write: a late conflict cannot leave
    # half an import behind. Network failures during creation can still be resumed.
    for node in nodes:
        row = by_code.get(node.code.lower())
        if row is None:
            continue
        actual_parent = by_id.get(row["parent_id"], {}).get("code") if row["parent_id"] is not None else None
        actual_parent = actual_parent.lower() if actual_parent is not None else None
        expected_parent = source[node.parent].code.lower() if node.parent is not None else None
        if actual_parent != expected_parent:
            raise ValueError(f"类别 {node.code} 的现有父级不同，已停止；请先检查现有分类")
        if node.parent is not None and row.get("name") != node.name:
            raise ValueError(f"类别 {node.code} 的现有名称 {row.get('name')!r} 与源名称 {node.name!r} 不同")
    added, skipped = 0, 0
    ids: dict[str, int] = {}
    for node in nodes:
        normalized_code = node.code.lower()
        if normalized_code in by_code:
            row = by_code[normalized_code]
            skipped += 1
        else:
            try:
                row = api.create_category(node.body(ids[node.parent] if node.parent is not None else None))
            except Exception as exc:
                raise RuntimeError(
                    f"添加 {node.name}（{node.source_id}）失败；本次已添加 {added}，跳过 {skipped}。"
                    f"修复问题后重跑同一命令可继续。原因：{exc}"
                ) from exc
            by_code[normalized_code] = row
            added += 1
            if added % 25 == 0:
                print(f"进度：已添加 {added}，跳过 {skipped}", flush=True)
        ids[node.source_id] = row["id"]
    return added, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", type=Path, default=Path("F:/standard_tree.py"), help="原分类 Python 文件路径")
    parser.add_argument("--base-url", default="http://127.0.0.1:8320", help="后端地址，默认 http://127.0.0.1:8320")
    parser.add_argument("--timeout", type=float, default=30, help="单次 HTTP 请求超时秒数，默认 30")
    parser.add_argument("--show-tree", action="store_true", help="完整打印将导入的树和别称")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="向后端实际添加类别")
    mode.add_argument("--dry-run", action="store_true", help="仅预览（默认），不访问后端")
    args = parser.parse_args(argv)
    if not 0 < args.timeout < float("inf"):
        parser.error("--timeout 必须是大于 0 的有限数值")
    try:
        nodes = read_tree(args.source)
        print(f"分类文件：{args.source.resolve()}")
        print(f"节点数：{len(nodes)}（含食材根节点）；别称数：{sum(len(n.aliases) for n in nodes)}")
        print(f"最大层级：{max(node.depth for node in nodes) + 1}（含食材根节点）")
        print("一级分类：" + "、".join(n.name for n in nodes if n.depth == 1))
        if args.show_tree:
            for node in nodes:
                aliases = f"；别称：{'、'.join(node.aliases)}" if node.aliases else ""
                print(f"{'    ' * node.depth}{node.name} [{node.code}]{aliases}")
        if not args.apply:
            print("PREVIEW 仅预览，未访问后端。添加 --apply 执行导入。")
            return 0
        print(f"导入后端：{args.base_url}（使用该后端的生效数据库配置）", flush=True)
        added, skipped = import_tree(nodes, CategoryAPI(args.base_url, args.timeout))
        print(f"PASS 导入完成：新增 {added}，已存在跳过 {skipped}。刷新前端「食材管理」查看类别树。")
        return 0
    except (OSError, SyntaxError, ValueError, RuntimeError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已中断；重新运行同一命令可跳过已添加的节点并继续。")
        raise SystemExit(130)
