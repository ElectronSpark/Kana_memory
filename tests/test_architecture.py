"""Positive and negative examples for the engineering dependency contract."""

from collections import Counter
import json
from pathlib import Path

import pytest

from check_architecture import Edge, check_edges, check_tree, scan_source


def policy_for(edges):
    return {
        "version": 1,
        "pure_imports": {},
        "exceptions": [
            dict(rule=e.rule, file=e.file, scope=e.scope, shape=e.shape, count=count,
                 status="migration_debt", owner="E1", reason="Frozen test fixture dependency",
                 evidence="synthetic baseline", exit_condition="Move fixture to support")
            for e, count in edges.items()
        ],
    }


def test_source_lines_aliases_and_format_do_not_create_false_debt():
    original = "def render():\n    from .app import button, label\n"
    updated = "# explanation\n\ndef render():\n    from .app import (\n        label as text, button,\n    )\n"
    file = "app/src/kanamemory/page.py"
    baseline = scan_source(original, file)
    assert len(baseline) == 2
    assert check_edges(scan_source(updated, file), policy_for(baseline), {file}) == []


@pytest.mark.parametrize("source", [
    "def render(app):\n    return app.store._db.execute('SELECT 1')",
    "def render(learning):\n    return learning._pending()",
    "def render(store):\n    connection = store._db\n    return connection",
])
def test_pages_cannot_acquire_private_storage_or_pending_readers(source):
    file = "app/src/kanamemory/page.py"
    edges = scan_source(source, file)
    assert len(edges) == 1 and next(iter(edges)).rule == "private_storage"
    assert check_edges(edges, policy_for(Counter()), {file})


def test_storage_ownership_is_exact_scope_and_count_not_a_file_allowance():
    file = "app/src/kanamemory/store.py"
    original = "def close(self):\n    self._db.close()"
    baseline = scan_source(original, file)
    policy = policy_for(baseline)
    assert not check_edges(scan_source("# changed lines\n" + original, file), policy, {file})
    added = original + "\ndef page(store):\n    return store._db\n"
    assert check_edges(scan_source(added, file), policy, {file})
    assert check_edges(Counter(), policy, {file})
    assert not scan_source("store.queries.course_count()", file)
    assert not scan_source("store._db.iterdump()", "tests/test_store.py")


@pytest.mark.parametrize("change", [
    "from .app import button\nfrom .app import button\n",
    "from .app import button, label\n",
    "def other():\n    from .app import button\n",
])
def test_new_duplicate_symbol_or_scope_is_rejected(change):
    file = "app/src/kanamemory/page.py"
    baseline = scan_source("from .app import button\n", file)
    assert any("New dependency" in e for e in check_edges(scan_source(change, file), policy_for(baseline), {file}))


@pytest.mark.parametrize("source", [
    "from test_other import helper",
    "import tests.test_other as other",
    "from tests.test_other import *",
    "from tests import test_other as other",
    "from importlib import import_module as load\nload('test_other')",
    "__import__('test_other')",
])
def test_support_cannot_hide_test_imports(source):
    edges = scan_source(source, "tests/support/fixtures.py")
    assert sum(edges.values()) == 1
    assert next(iter(edges)).rule == "test_import"


def test_string_monkeypatch_target_cannot_reintroduce_test_module_import():
    edges = scan_source("monkeypatch.setattr('test_other.clock', fake)", "tests/test_a.py")
    assert next(iter(edges)).rule == "test_import"
    assert not scan_source("monkeypatch.setattr('support.fixture.clock', fake)", "tests/test_a.py")


@pytest.mark.parametrize("source", [
    "from kanamemory import app as a",
    "import kanamemory.app as a",
    "from . import app",
    "import importlib as lib\nlib.import_module('kanamemory.app')",
])
def test_alternate_app_import_forms_are_rejected(source):
    edges = scan_source(source, "app/src/kanamemory/page.py")
    assert sum(edges.values()) == 1
    assert next(iter(edges)).rule == "app_reverse"


def test_removed_dependency_must_remove_its_allowance():
    file = "tests/test_example.py"
    policy = policy_for(scan_source("from test_other import helper", file))
    assert "stale" in check_edges(Counter(), policy, {file})[0]
    # Explicit public source selection excludes many developer-only tests.
    assert "stale" in check_edges(Counter(), policy, set())[0]
    assert check_edges(Counter(), policy, set(), public_subset=True) == []


def test_pure_module_has_explicit_import_budget():
    file = "app/src/kanamemory/selection.py"
    rules = {file: ["dataclasses", "typing"]}
    assert not scan_source("from dataclasses import dataclass", file, rules)
    for module in ("toga", "sqlite3", "requests", ".domain", ".app"):
        assert any(e.rule == "pure_import" for e in scan_source(f"from {module} import Something", file, rules))


@pytest.mark.parametrize("name", ["ui_theme", "ui_components"])
@pytest.mark.parametrize("dependency", [".app", ".domain", ".learning_store", "sqlite3", "httpx"])
def test_shared_ui_cannot_acquire_application_storage_or_network(name, dependency):
    policy = json.loads((Path(__file__).resolve().parents[1] / "tools/architecture_policy.json").read_text("utf8"))
    file = f"app/src/kanamemory/{name}.py"
    edges = scan_source(f"from {dependency} import Something", file, policy["pure_imports"])
    assert any(edge.rule == "pure_import" for edge in edges)


@pytest.mark.parametrize("name", ["study_queries", "teaching_queries"])
@pytest.mark.parametrize("dependency", [".app", "toga", "httpx"])
def test_owned_readers_cannot_acquire_ui_or_network(name, dependency):
    policy = json.loads((Path(__file__).resolve().parents[1] / "tools/architecture_policy.json").read_text("utf8"))
    file = f"app/src/kanamemory/{name}.py"
    edges = scan_source(f"from {dependency} import Something", file, policy["pure_imports"])
    assert any(edge.rule == "pure_import" for edge in edges)


@pytest.mark.parametrize("dependency", [".app", ".domain", ".learning_store", "sqlite3", "httpx"])
def test_page_restore_cannot_acquire_policy_storage_or_network(dependency):
    policy = json.loads((Path(__file__).resolve().parents[1] / "tools/architecture_policy.json").read_text("utf8"))
    edges = scan_source(f"from {dependency} import Something", "app/src/kanamemory/page_restore.py", policy["pure_imports"])
    assert any(edge.rule == "pure_import" for edge in edges)


@pytest.mark.parametrize("name", ["diagnostic_events", "request_outcome"])
@pytest.mark.parametrize("dependency", [".app", ".domain", "toga", "sqlite3", "httpx"])
def test_error_helpers_cannot_own_ui_storage_or_provider(name, dependency):
    policy = json.loads((Path(__file__).resolve().parents[1] / "tools/architecture_policy.json").read_text("utf8"))
    edges = scan_source(f"from {dependency} import Something", f"app/src/kanamemory/{name}.py", policy["pure_imports"])
    assert any(edge.rule == "pure_import" for edge in edges)


def test_path_aliases_and_arguments_are_checked():
    edges = scan_source("from sys import path as p\np.insert(0, 'hidden')", "tests/test_a.py")
    assert edges == Counter({Edge("test_path", "tests/test_a.py", "<module>", "sys.path.insert(0, 'hidden')"): 1})


@pytest.mark.parametrize("source", [
    "import sys\nsys.path[:0] = ['hidden']",
    "import sys as runtime\nruntime.path = ['hidden']",
    "from sys import path as p\np[:0] = ['hidden']",
    "import sys\nsys.path += ['hidden']",
    "import sys\ndel sys.path[0]",
])
def test_path_assignment_and_slice_cannot_bypass_call_guard(source):
    edges = scan_source(source, "tests/test_a.py")
    assert len(edges) == 1 and next(iter(edges)).rule == "test_path"


@pytest.mark.parametrize("field", ["owner", "reason", "evidence", "exit_condition", "status", "count"])
def test_exceptions_require_ownership_reason_evidence_and_exit(field):
    edges = scan_source("from test_other import helper", "tests/test_example.py")
    policy = policy_for(edges)
    del policy["exceptions"][0][field]
    with pytest.raises(ValueError):
        check_edges(edges, policy, {"tests/test_example.py"})


def test_tree_scan_never_executes_application_or_resource_code(tmp_path):
    engine = tmp_path / "app/src/kanamemory"
    (engine / "resources").mkdir(parents=True)
    (engine / "page.py").write_text("raise RuntimeError('never execute')\nfrom .app import label", "utf-8")
    (engine / "resources/irrelevant.py").write_text("not python !", "utf-8")
    errors = check_tree(tmp_path, policy_for(Counter()))
    assert len(errors) == 1 and "New dependency" in errors[0]
