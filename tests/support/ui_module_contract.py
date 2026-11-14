"""The contract of a design 13 C4 page module (the ui packages of arrangement, admission, home and next_step),
read from its source.

A page module holds no app object or store (no name or attribute app, store,
learning_store, self_app) and imports no service, source or app module. The
source is parsed, never imported.
"""
import ast
from pathlib import Path

FORBIDDEN_NAMES = frozenset({"app", "store", "learning_store", "self_app"})


def assert_page_module_holds_no_app_store_or_service(path: Path) -> None:
    tree = ast.parse(path.read_text("utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    modules = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    modules |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    # A from-dots import names its target in the alias ("from ... import app").
    modules |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}
    assert not (FORBIDDEN_NAMES & (names | attributes))
    # "service" covers arrangement's services package and admission's service module.
    assert not any("service" in module or "sources" in module or module.endswith("app") for module in modules)
