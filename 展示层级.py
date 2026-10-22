import os
from typing import Set, List

def _contains_any(haystack: str, needles: Set[str]) -> bool:
    if not needles:
        return False
    hs = haystack.lower()
    return any(n.lower() in hs for n in needles)

def tree(
    root_dir: str,
    exclude_files: Set[str] = None,
    exclude_dirs: Set[str] = None,
) -> str:
    """
    生成目录树（字符串），带两类过滤：
      - exclude_files: 相对路径字符串中包含任意元素的 “文件” 将被过滤
      - exclude_dirs : 相对路径字符串中包含任意元素的 “目录(整棵子树)” 将被过滤

    过滤均为大小写不敏感、基于“相对路径子串”的匹配。
    """
    exclude_files = exclude_files or set()
    exclude_dirs = exclude_dirs or set()

    lines: List[str] = []
    root_dir = os.path.abspath(root_dir)
    root_name = os.path.basename(root_dir.rstrip(os.sep)) or root_dir

    def walk(cur_dir: str, prefix: str) -> None:
        rel_dir = os.path.relpath(cur_dir, root_dir)
        # 根目录时 rel_dir 可能是 "."
        rel_dir_for_match = "" if rel_dir == "." else rel_dir

        # 目录过滤（对子树生效）：如果相对路径包含任一目录排除关键字，则整棵跳过
        if _contains_any(rel_dir_for_match, exclude_dirs):
            return

        try:
            entries = sorted(os.listdir(cur_dir))
        except PermissionError:
            lines.append(f"{prefix}[Permission Denied] {os.path.basename(cur_dir)}")
            return

        for i, name in enumerate(entries):
            path = os.path.join(cur_dir, name)
            rel_path = os.path.relpath(path, root_dir)  # 用于匹配与显示
            is_last = (i == len(entries) - 1)
            connector = "└── " if is_last else "├── "

            if os.path.isdir(path):
                # 子目录整棵过滤
                if _contains_any(rel_path, exclude_dirs):
                    continue
                lines.append(prefix + connector + name)
                walk(path, prefix + ("    " if is_last else "│   "))
            else:
                # 文件过滤
                if _contains_any(rel_path, exclude_files):
                    continue
                lines.append(prefix + connector + name)

    lines.append(root_name)
    walk(root_dir, "")
    return "\n".join(lines)


# ============ 示例 ============
if __name__ == "__main__":
    root = r"frontend"
    # 文件名或路径里包含这些子串的文件会被隐藏（举例）
    exclude_files = {".pyc", ".log", 'img', 'hpp'}
    # 目录路径里包含这些子串的目录会整棵隐藏（举例）
    exclude_dirs = {"__pycache__", ".git", ".vscode", 
    r"flann", r"gapi", r"Scripts", r".venv", r"dnn", 'core', '.env', 
    'img', 'hpp', 'data', 'dist', 'node_modules'}

    print(tree(root, exclude_files, exclude_dirs))