"""Check explicit dependency boundaries without importing the application.

Exceptions are a shrinking migration ledger, not blanket file exclusions. Keys
include scope and import shape (not source line); repeated edges are counted.
The independent public tree checks only the source files shipped in that tree.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = Path(__file__).with_name("architecture_policy.json")


@dataclass(frozen=True, order=True)
class Edge:
    rule: str
    file: str
    scope: str
    shape: str


def _test_module(module: str) -> bool:
    return any(part.startswith("test_") for part in module.split("."))


def scan_source(source: str, file: str, pure_imports: dict | None = None) -> Counter:
    """Return stable dependency edges; AST parsing never executes source code."""
    counts: Counter[Edge] = Counter()
    is_test = file.startswith("tests/")
    pure_allowed = (pure_imports or {}).get(file)

    class Scan(ast.NodeVisitor):
        def __init__(self):
            self.scope: list[str] = []
            self.aliases: dict[str, str] = {}

        def add(self, rule, shape):
            counts[Edge(rule, file, ".".join(self.scope) or "<module>", shape)] += 1

        def visit_FunctionDef(self, node):
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef

        def visit_Attribute(self, node):
            if not is_test and node.attr in {"_db", "_pending"}:
                self.add("private_storage", ast.unparse(node))
            self.generic_visit(node)

        def import_edge(self, module, shape):
            if is_test and _test_module(module):
                self.add("test_import", shape)
            if not is_test and module in {"kanamemory.app", ".app"}:
                self.add("app_reverse", shape)
            if pure_allowed is not None and module not in pure_allowed:
                self.add("pure_import", shape)

        def visit_Import(self, node):
            for alias in node.names:
                self.aliases[alias.asname or alias.name] = alias.name
                self.import_edge(alias.name, "import " + alias.name)

        def visit_ImportFrom(self, node):
            module = "." * node.level + (node.module or "")
            for alias in node.names:
                target = module + ("" if module.endswith(".") else ".") + alias.name
                self.aliases[alias.asname or alias.name] = target
                effective = target if target in {"kanamemory.app", ".app"} or _test_module(target) else module
                self.import_edge(effective, f"from {module} import {alias.name}")

        def visit_Call(self, node):
            call = ast.unparse(node.func)
            first, *rest = call.split(".")
            resolved = ".".join([self.aliases.get(first, first), *rest])
            if is_test and resolved in {
                "sys.path.insert", "sys.path.append", "sys.path.extend", "sys.path.remove"
            }:
                # Include arguments: unrelated path mutations must not share an allowance.
                args = ", ".join(ast.unparse(arg) for arg in node.args)
                kwargs = ", ".join(ast.unparse(arg) for arg in node.keywords)
                self.add("test_path", f"{resolved}({args}{', ' if args and kwargs else ''}{kwargs})")
            if resolved in {"importlib.import_module", "__import__"} and node.args:
                target = node.args[0]
                if isinstance(target, ast.Constant) and isinstance(target.value, str):
                    self.import_edge(target.value, f"dynamic import {target.value}")
            if is_test and call == "monkeypatch.setattr" and node.args:
                target = node.args[0]
                if isinstance(target, ast.Constant) and isinstance(target.value, str) and _test_module(target.value):
                    self.add("test_import", "patch import " + target.value)
            self.generic_visit(node)

        def path_target(self, target):
            root = target.value if isinstance(target, ast.Subscript) else target
            parts = ast.unparse(root).split(".")
            return ".".join([self.aliases.get(parts[0], parts[0]), *parts[1:]]) == "sys.path"

        def visit_Assign(self, node):
            if is_test and any(self.path_target(target) for target in node.targets):
                self.add("test_path", ast.unparse(node))
            self.generic_visit(node)

        def visit_AugAssign(self, node):
            if is_test and self.path_target(node.target):
                self.add("test_path", ast.unparse(node))
            self.generic_visit(node)

        def visit_Delete(self, node):
            if is_test and any(self.path_target(target) for target in node.targets):
                self.add("test_path", ast.unparse(node))
            self.generic_visit(node)

    Scan().visit(ast.parse(source, filename=file))
    return counts


def scan_tree(root: Path, pure_imports: dict) -> tuple[Counter, set[str]]:
    counts: Counter[Edge] = Counter()
    files: set[str] = set()
    for directory in ("app/src/kanamemory", "tests"):
        for path in sorted((root / directory).rglob("*.py")):
            if {"resources", "__pycache__"} & set(path.relative_to(root).parts):
                continue
            file = path.relative_to(root).as_posix()
            files.add(file)
            counts.update(scan_source(path.read_text("utf-8-sig"), file, pure_imports))
    return counts, files


def check_edges(actual: Counter, policy: dict, files: set[str], *, public_subset=False) -> list[str]:
    """Reject new, expanded and stale allowances; require accountable exceptions."""
    if policy.get("version") != 1:
        raise ValueError("Unsupported architecture policy version")
    allowed: Counter[Edge] = Counter()
    for entry in policy["exceptions"]:
        edge = Edge(*(entry[key] for key in ("rule", "file", "scope", "shape")))
        if edge in allowed:
            raise ValueError(f"Duplicate architecture exception: {edge}")
        if entry.get("status") not in {"migration_debt", "retained"}:
            raise ValueError(f"Missing exception disposition: {edge}")
        for field in ("owner", "reason", "evidence", "exit_condition"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                raise ValueError(f"Missing exception {field}: {edge}")
        if type(entry.get("count")) is not int or entry["count"] < 1:
            raise ValueError(f"Invalid exception count: {edge}")
        allowed[edge] = entry["count"]
    errors = []
    for edge in sorted(actual.keys() | allowed.keys()):
        if public_subset and edge.file not in files:
            continue  # Public export intentionally ships only reviewed tests.
        if actual[edge] > allowed[edge]:
            errors.append(f"New dependency ({actual[edge]} > {allowed[edge]}): {edge}")
        elif actual[edge] < allowed[edge]:
            errors.append(f"Remove stale exception ({actual[edge]} < {allowed[edge]}): {edge}")
    return errors


def check_tree(root: Path, policy: dict) -> list[str]:
    actual, files = scan_tree(root, policy["pure_imports"])
    return check_edges(actual, policy, files, public_subset=(root / "clean-source-manifest.json").is_file())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--policy", type=Path, default=POLICY)
    args = parser.parse_args()
    policy = json.loads(args.policy.read_text("utf-8"))
    errors = check_tree(args.root, policy)
    for error in errors:
        print(error)
    if not errors:
        print("Architecture boundaries passed (explicit, shrinking exceptions).")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
