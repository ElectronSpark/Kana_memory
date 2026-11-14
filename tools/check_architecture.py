"""Check explicit dependency boundaries without importing the application.

Exceptions are a shrinking migration ledger, not blanket file exclusions. Keys
include scope and import shape (not source line); repeated edges are counted.
The independent public tree checks only the source files shipped in that tree.
Files that create controls also carry one shrinking number each (design 13
G1-G3): store references and attributes set on the app, ceilings in page_coupling.
Design 12 and 13, named in comments here and in the policy, are design documents
that are not published; docs/design_current/07_engineering_structure.txt restates the rule.
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


# --- Page coupling (design 13 G1-G3): one shrinking number per file that creates controls ---------

PACKAGE = "app/src/kanamemory/"
# Design 13 C4 page packages: always scanned, always zero, no owner exemption, never a ceiling entry.
PAGE_PACKAGES = (PACKAGE + "arrangement/ui/", PACKAGE + "admission/ui/", PACKAGE + "home/ui/")
STORES = frozenset({"store", "learning_store"})
MEMBER_PROBES = frozenset({"getattr", "hasattr"})
IGNORED_BUILTINS = frozenset({"id", "isinstance", "type"})   # identity and type checks read nothing


@dataclass
class PageCoupling:
    """G1 (store access, G3 private members included) and G2 (attributes set on the app) of one file."""
    store_access: int = 0
    app_state: set = None
    private: list = None

    def __post_init__(self):
        self.app_state = set() if self.app_state is None else self.app_state
        self.private = [] if self.private is None else self.private


def _absolute(target: str, file: str) -> str:
    """'.practice_round.restart' in app/src/kanamemory/x.py -> 'practice_round.restart'."""
    level = len(target) - len(target.lstrip("."))
    if not level:
        return target.removeprefix("kanamemory.")
    package = file.removeprefix(PACKAGE).split("/")[:-1]
    package = package[:len(package) - (level - 1)] if level > 1 else package
    return ".".join([*package, target[level:]]).strip(".")


def creates_controls(tree: ast.AST, file: str) -> bool:
    """Design 13: a file importing toga or ui_components, and every file of a page package."""
    if file.startswith(PAGE_PACKAGES):
        return True
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = [module, *(f"{module}.{alias.name}" for alias in node.names)]
        else:
            continue
        if any(name == "toga" or name.startswith("toga.") or name.split(".")[-1] == "ui_components" for name in names):
            return True
    return False


def store_references(tree: ast.AST, parents: dict) -> list[ast.Attribute]:
    """Every read of '<any expression>.store' / '.learning_store'; a dotted chain of them is one reference.

    The reference returned is the outermost store attribute of the chain
    (x.learning_store.store -> the whole expression), so a chain counts once.
    """
    references = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and node.attr in STORES and isinstance(node.ctx, ast.Load)):
            continue
        outer = parents.get(node)
        while isinstance(outer, ast.Attribute) and not (outer.attr in STORES and isinstance(outer.ctx, ast.Load)):
            outer = parents.get(outer)
        if not isinstance(outer, ast.Attribute):
            references.append(node)
    return references


def _chain_private_member(reference: ast.Attribute, parents: dict) -> ast.Attribute | None:
    """The underscore member that directly follows a store attribute of this reference's chain, if any."""
    node = reference
    while isinstance(node, ast.Attribute):
        parent = parents.get(node)
        if node.attr in STORES and isinstance(parent, ast.Attribute) and parent.attr.startswith("_"):
            return parent
        node = node.value
    return None


def scan_page_coupling(source: str, file: str, owner_calls=()) -> PageCoupling | None:
    """None for files that create no controls. Never executes source code.

    G1 (the lead's rule for design 13 S0b): every store reference counts once
    (see store_references), whatever its base and use: a call through it, the
    store as an argument (also inside a list, tuple or starred argument),
    getattr/hasattr on it, a local alias, a bare read, a comparison. Not
    counted: a reference that is directly a positional or keyword argument of
    a listed owner call (design 13 O3) -- never in a page package -- and the
    direct argument of id(), isinstance() or type(). G3: a reference followed
    by an underscore member, or getattr/hasattr on it with a constant
    underscore name, is also listed (still one reference). G2: distinct
    attribute names assigned on the app object (self inside the App or a
    *Mixin class, or app), as assignment, loop, with-as, tuple, list or
    starred targets, or by setattr with a constant name.
    """
    tree = ast.parse(source, filename=file)
    if not creates_controls(tree, file):
        return None
    result = PageCoupling()
    owner_exempt = not file.startswith(PAGE_PACKAGES)
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            for alias in node.names:
                aliases[alias.asname or alias.name] = module + ("" if module.endswith(".") else ".") + alias.name
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def callee(call):
        parts = ast.unparse(call.func).split(".")
        return _absolute(".".join([aliases[parts[0]], *parts[1:]]), file) if parts[0] in aliases else None

    def private(node, shape):
        result.private.append(f"{file}:{node.lineno} {shape}")

    for node in store_references(tree, parents):
        parent = parents.get(node)
        keyword = isinstance(parent, ast.keyword)
        call = parents.get(parent) if keyword else parent
        positional = isinstance(call, ast.Call) and any(arg is node for arg in call.args)
        if positional and ast.unparse(call.func) in IGNORED_BUILTINS:
            continue   # identity and type checks read nothing
        if (positional or (keyword and isinstance(call, ast.Call))) and owner_exempt and callee(call) in owner_calls:
            continue   # design 13 O3: the store handed to the owner of the write it performs
        result.store_access += 1
        member = _chain_private_member(node, parents)
        if member is not None:
            private(node, ast.unparse(member))
        elif positional and ast.unparse(call.func) in MEMBER_PROBES and call.args[0] is node and len(call.args) > 1:
            name = call.args[1]
            if isinstance(name, ast.Constant) and isinstance(name.value, str) and name.value.startswith("_"):
                private(node, ast.unparse(call))

    class AppState(ast.NodeVisitor):
        def __init__(self):
            self.app_class = [False]

        def visit_ClassDef(self, node):
            bases = [ast.unparse(base) for base in node.bases]   # toga.App, or App imported from toga
            self.app_class.append(node.name.endswith("Mixin")
                                  or any(base == "toga.App" or aliases.get(base) == "toga.App" for base in bases))
            self.generic_visit(node)
            self.app_class.pop()

        def on_app(self, target):
            for element in ast.walk(target):
                if (isinstance(element, ast.Attribute) and isinstance(element.ctx, ast.Store)
                        and isinstance(element.value, ast.Name)
                        and (element.value.id == "app" or (element.value.id == "self" and self.app_class[-1]))):
                    result.app_state.add(element.attr)

        def visit_Assign(self, node):
            for target in node.targets:
                self.on_app(target)
            self.generic_visit(node)

        def visit_AugAssign(self, node):
            self.on_app(node.target)
            self.generic_visit(node)

        visit_AnnAssign = visit_AugAssign
        visit_For = visit_AugAssign       # for / async for targets
        visit_AsyncFor = visit_AugAssign

        def visit_With(self, node):
            for item in node.items:
                if item.optional_vars is not None:
                    self.on_app(item.optional_vars)
            self.generic_visit(node)

        visit_AsyncWith = visit_With

        def visit_Call(self, node):
            if (ast.unparse(node.func) == "setattr" and len(node.args) > 1 and isinstance(node.args[0], ast.Name)
                    and (node.args[0].id == "app" or (node.args[0].id == "self" and self.app_class[-1]))
                    and isinstance(node.args[1], ast.Constant)):
                result.app_state.add(str(node.args[1].value))
            self.generic_visit(node)

    AppState().visit(tree)
    result.private.sort(key=lambda entry: int(entry.split(":")[1].split()[0]))
    return result


def scan_pages(root: Path, owner_calls=()) -> dict[str, PageCoupling]:
    pages = {}
    for path in sorted((root / PACKAGE).rglob("*.py")):
        if {"resources", "__pycache__"} & set(path.relative_to(root).parts):
            continue
        file = path.relative_to(root).as_posix()
        coupling = scan_page_coupling(path.read_text("utf-8-sig"), file, owner_calls)
        if coupling is not None:
            pages[file] = coupling
    return pages


def check_pages(pages: dict[str, PageCoupling], policy: dict, files: set[str], *, public_subset=False) -> list[str]:
    """Each number may only fall; a file without an entry (a new UI module) has ceiling zero."""
    section = policy.get("page_coupling", {})
    errors = []
    for measure, label in (("store_access", "page store access"), ("app_state", "app attributes set by pages")):
        ceilings = section.get(measure, {})
        for file, ceiling in ceilings.items():
            if type(ceiling) is not int or ceiling < 1:
                raise ValueError(f"Invalid {label} ceiling (remove a zero entry): {file}")
            if file.startswith(PAGE_PACKAGES):
                raise ValueError(f"page packages have no {label} ceiling: {file}")
        for file in sorted(pages.keys() | ceilings.keys()):
            if public_subset and file not in files:
                continue
            coupling = pages.get(file, PageCoupling())
            value = coupling.store_access if measure == "store_access" else len(coupling.app_state)
            ceiling = ceilings.get(file, 0)
            if value > ceiling:
                errors.append(f"New {label} ({value} > {ceiling}): {file}")
            elif value < ceiling:
                errors.append(f"Lower {label} ceiling ({value} < {ceiling}): {file}")
    return errors


def tree_report(root: Path, policy: dict) -> tuple[list[str], list[str]]:
    """Errors, and the G3 listing of private store members in files that create controls."""
    actual, files = scan_tree(root, policy["pure_imports"])
    public_subset = (root / "clean-source-manifest.json").is_file()
    pages = scan_pages(root, policy.get("page_coupling", {}).get("owner_calls", {}))
    errors = check_edges(actual, policy, files, public_subset=public_subset)
    errors += check_pages(pages, policy, files, public_subset=public_subset)
    private = sorted((entry for coupling in pages.values() for entry in coupling.private),
                     key=lambda entry: (entry.split(":")[0], int(entry.split(":")[1].split()[0])))
    summary = [f"Page store access {sum(c.store_access for c in pages.values())} and app attributes "
               f"{sum(len(c.app_state) for c in pages.values())} in {len(pages)} files that create controls; "
               f"private store members: {len(private)}", *(f"  private store member: {entry}" for entry in private)]
    return errors, summary


def check_tree(root: Path, policy: dict) -> list[str]:
    return tree_report(root, policy)[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--policy", type=Path, default=POLICY)
    args = parser.parse_args()
    policy = json.loads(args.policy.read_text("utf-8"))
    errors, summary = tree_report(args.root, policy)
    for error in errors:
        print(error)
    for line in summary:
        print(line)
    if not errors:
        print("Architecture boundaries passed (explicit, shrinking exceptions).")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
