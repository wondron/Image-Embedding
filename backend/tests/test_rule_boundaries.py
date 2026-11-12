"""Keep shared rules and infrastructure independent of service orchestration."""

import ast
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest


BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"


def imports(path):
    package = ".".join(("app", *path.relative_to(APP).parent.parts))
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = importlib.util.resolve_name("." * node.level + module, package)
            yield node.lineno, module
            for alias in node.names:
                yield node.lineno, f"{module}.{alias.name}"


class RuleBoundaryTests(unittest.TestCase):
    def test_shared_rules_depend_only_on_core_and_the_standard_library(self):
        violations = []
        for path in (APP / "core/rules").rglob("*.py"):
            for line, module in imports(path):
                if module == "app":
                    continue  # from app import core is checked as app.core below
                allowed = (
                    module == "app.core" or module.startswith("app.core.")
                    if module.startswith("app.")
                    else module.split(".", 1)[0] in sys.stdlib_module_names
                )
                if not allowed:
                    violations.append(f"{path.relative_to(APP)}:{line}: {module}")
        self.assertEqual(violations, [], "Shared rules import an outer layer")

    def test_repositories_and_clients_do_not_import_services(self):
        violations = []
        for layer in ("repositories", "clients"):
            for path in (APP / layer).rglob("*.py"):
                for line, module in imports(path):
                    if module == "app.services" or module.startswith("app.services."):
                        violations.append(f"{path.relative_to(APP)}:{line}: {module}")
        self.assertEqual(violations, [], "Infrastructure imports service orchestration")

    def test_cold_infrastructure_imports_do_not_load_services(self):
        # A fresh interpreter catches imports triggered by package __init__ files,
        # even when the ordinary test runner has already loaded every use case.
        code = """
import socket
import sys
from unittest.mock import patch

with patch.object(socket.socket, 'connect', side_effect=AssertionError('unexpected network')):
    import app.repositories.metadata
    import app.clients.image_storage

loaded = [name for name in sys.modules if name == 'app.services' or name.startswith('app.services.')]
assert not loaded, loaded
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=BACKEND,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
