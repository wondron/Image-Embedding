"""List `from app.` / `import app.` occurrences under backend/app (R0 freeze helper).

Read-only. Usage (from backend/ or repo root):
  python scripts/list_app_imports.py
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "app"
PAT = re.compile(r"^\s*(?:from\s+(app(?:\.\w+)*)\s+import|import\s+(app(?:\.\w+)*))", re.M)


def main() -> None:
    counts: Counter[str] = Counter()
    for path in sorted(ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in PAT.finditer(text):
            mod = m.group(1) or m.group(2)
            if mod:
                root = ".".join(mod.split(".")[:2])  # app.xxx
                counts[root] += 1
    for root, n in counts.most_common():
        print(f"{n:4d}  {root}")
    print(f"# files scanned under {ROOT}")


if __name__ == "__main__":
    main()
