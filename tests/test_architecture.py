"""Positive and negative examples for the engineering dependency contract."""

from collections import Counter
import json
from pathlib import Path

import pytest

from check_architecture import Edge, check_edges, check_pages, check_tree, scan_page_coupling, scan_source, tree_report


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


# --- design 13 G1-G3: one shrinking number per file that creates controls ----------------------

PAGE = "app/src/kanamemory/page_ui.py"


def page(body, header="from . import ui_components as ui\n", file=PAGE, owners=()):
    return scan_page_coupling(header + body, file, owners)


@pytest.mark.parametrize("body", [
    "def show(self):\n    return self.store.get_setting('x')",                  # a call through the store
    "def show(self):\n    return self.learning_store.queries.course_count()",   # a member of the store, called
    "def helper(app):\n    return app.store.summary()",                         # through a passed app object
    "def show(self):\n    return fetch(self.store)",                            # the store as an argument
    "def show(self):\n    return fetch(1, store=self.learning_store)",          # ... or as a keyword
    "def show(self):\n    return fetch([self.store])",                          # ... inside a list
    "def show(self):\n    return fetch((1, self.store))",                       # ... inside a tuple
    "def show(self):\n    return fetch(*[self.store])",                         # ... starred
    "def show(self):\n    return getattr(self.store, 'practice_lane', 'main')",  # constant getattr
    "def show(self):\n    return hasattr(self.store, 'load_session_progress')",
    "def show(self):\n    store = self.store\n    return store.get_setting('x')",   # a local alias
    "def show(self):\n    queries = self.store.queries\n    return queries.x()",       # a bare read kept in a local
    "def show(self):\n    return self.store is None",                           # a comparison
    "def show(self):\n    self.store.practice_lane = 'main'",                   # a member set on the store
    "def show(self, learning):\n    return learning.store.get_setting('x')",   # any base, not only self and app
    "def show(self):\n    return self.window.app.store.zone",
    "def show(self):\n    return fetch(self.learning_store.store)",             # a chain is one reference
    "def show(self):\n    return self.learning_store.store.queries.x()",
])
def test_every_store_reference_counts_once(body):
    assert page(body).store_access == 1
    assert page(body, header="import toga\n").store_access == 1


@pytest.mark.parametrize("body", [
    "def show(self):\n    return id(self.store), isinstance(self.store, object), type(self.learning_store)",
    "def show(self):\n    return self.repo.lookup('x'), self.stores, self.store_name",
    "def setup(self):\n    self.store = Store()\n    self.learning_store = LearningStore(None)",   # assigned, not read
])
def test_identity_checks_assignments_and_other_names_are_not_counted(body):
    assert page(body).store_access == 0


def test_only_a_direct_argument_of_a_listed_owner_call_is_exempt():
    owners = {"practice_round.restart": "round owner"}
    named = "from .practice_round import restart, sealed\ndef go(self):\n    restart(self.store)\n    return sealed(self.store)"
    assert page(named, owners=owners).store_access == 1          # sealed reads: the page fetches data
    assert page(named).store_access == 2
    module = "from . import practice_round as pr\ndef go(self):\n    pr.restart(self.store, store=self.learning_store)"
    assert page(module, owners=owners).store_access == 0
    wrapped = "from . import practice_round as pr\ndef go(self):\n    pr.restart([self.store], *[app.store])"
    assert page(wrapped, owners=owners).store_access == 2        # not directly an argument
    used = "from . import practice_round as pr\ndef go(self):\n    pr.restart(self.store.queries)"
    assert page(used, owners=owners).store_access == 1           # the argument is a store member, not the store
    nested = "from ...practice_round import restart\ndef go(app):\n    restart(app.store)"
    assert page(nested, file="app/src/kanamemory/x/y.py", owners=owners).store_access == 0
    # The page packages (arrangement/ui, admission/ui): no exemption, every reference counts.
    assert page(nested, file="app/src/kanamemory/arrangement/ui/x.py", owners=owners).store_access == 1
    assert page(nested, file="app/src/kanamemory/admission/ui/x.py", owners=owners).store_access == 1
    sibling = nested.replace("...", "..")   # arrangement.practice_round, a different module
    assert page(sibling, file="app/src/kanamemory/arrangement/services/x.py", owners=owners).store_access == 1


def test_files_that_create_no_controls_are_not_scanned_and_arrangement_pages_always_are():
    body = "def go(self):\n    return self.store.get_setting('x')"
    assert page(body, header="") is None
    assert page(body, header="", file="app/src/kanamemory/arrangement/ui/page.py").store_access == 1
    assert page(body, header="", file="app/src/kanamemory/admission/ui/page.py").store_access == 1
    assert page(body, header="", file="app/src/kanamemory/admission/page.py") is None   # only its ui package
    assert page(body, header="from toga.style import Pack\n").store_access == 1
    assert page(body, header="from .ui_components import label\n").store_access == 1


def test_private_store_members_count_once_and_are_listed():
    body = ("def show(self):\n    self.store._session_lane()\n    rows = self.learning_store._dispatch_pending\n"
            "    return getattr(self.store, '_db', None), getattr(self.store, 'zone', None)")
    coupling = page(body)
    assert coupling.store_access == 4
    assert coupling.private == [f"{PAGE}:3 self.store._session_lane", f"{PAGE}:4 self.learning_store._dispatch_pending",
                                f"{PAGE}:5 getattr(self.store, '_db', None)"]


def test_private_members_of_any_base_and_of_a_chain_are_listed_once():
    body = "def show(self):\n    return self.learning_store.store._db, app.store._pending\n"
    coupling = page(body)
    assert coupling.store_access == 2
    assert coupling.private == [f"{PAGE}:3 self.learning_store.store._db", f"{PAGE}:3 app.store._pending"]


def test_attributes_set_on_the_app_are_distinct_names():
    body = ("class PageMixin:\n    def show(self):\n        self.a = 1\n        self.a += 1\n        self.b: int = 2\n"
            "        self.c, other = 3, 4\n        setattr(self, 'd', 5)\n"
            "        [self.f, *self.g] = 6, 7\n        *self.h, other = 8, 9\n"
            "        for self.i in range(2):\n            pass\n"
            "        with open('x') as self.j, open('y') as (self.k, other):\n            pass\n"
            "    async def run(self):\n        async for self.l in source():\n            pass\n"
            "        async with lock() as self.m:\n            pass\n"
            "class Model:\n    def __init__(self):\n        self.not_app = 1\n        for self.not_app_either in ():\n"
            "            pass\n"
            "def helper(app):\n    app.e = 1\n    for app.n in ():\n        pass\n")
    assert page(body).app_state == set("abcdefghijklmn")


def coupling_policy(store=None, state=None):
    return {"page_coupling": {"owner_calls": {}, "store_access": store or {}, "app_state": state or {}}}


def test_ceilings_reject_growth_require_lowering_and_new_files_start_at_zero():
    two = page("class PageMixin:\n    def show(self):\n        self.a = self.store.x()\n        return self.store.y()")
    assert check_pages({PAGE: two}, coupling_policy({PAGE: 2}, {PAGE: 1}), {PAGE}) == []
    above = check_pages({PAGE: two}, coupling_policy({PAGE: 1}, {PAGE: 1}), {PAGE})
    assert above == [f"New page store access (2 > 1): {PAGE}"]
    below = check_pages({PAGE: two}, coupling_policy({PAGE: 3}, {PAGE: 2}), {PAGE})
    assert below == [f"Lower page store access ceiling (2 < 3): {PAGE}",
                     f"Lower app attributes set by pages ceiling (1 < 2): {PAGE}"]
    new = check_pages({PAGE: two}, coupling_policy(), {PAGE})
    assert new == [f"New page store access (2 > 0): {PAGE}", f"New app attributes set by pages (1 > 0): {PAGE}"]
    # A removed or migrated file must drop its entry; the public export skips files it does not ship.
    assert check_pages({}, coupling_policy({PAGE: 2}), set()) == [f"Lower page store access ceiling (0 < 2): {PAGE}"]
    assert check_pages({}, coupling_policy({PAGE: 2}), set(), public_subset=True) == []


@pytest.mark.parametrize("ceilings", [{PAGE: 0}, {PAGE: "3"}, {"app/src/kanamemory/arrangement/ui/page.py": 1},
                                      {"app/src/kanamemory/admission/ui/add_page.py": 1}])
def test_ceiling_entries_are_positive_and_never_for_arrangement_pages(ceilings):
    with pytest.raises(ValueError):
        check_pages({}, coupling_policy(ceilings), set())


def test_tree_report_lists_private_members_and_checks_ceilings(tmp_path):
    engine = tmp_path / "app/src/kanamemory"
    engine.mkdir(parents=True)
    (engine / "page_ui.py").write_text("import toga\ndef show(self):\n    return getattr(self.store, '_db', None)\n", "utf-8")
    (engine / "rules.py").write_text("def plain(self):\n    return self.store._db\n", "utf-8")
    policy = {**policy_for(Counter()), **coupling_policy({PAGE: 1})}
    errors, summary = tree_report(tmp_path, policy)
    assert all("page_ui" not in error for error in errors)   # rules.py's _db is the private_storage rule's
    assert summary[1] == f"  private store member: {PAGE}:3 getattr(self.store, '_db', None)"
    assert any("New page store access (1 > 0)" in error for error in tree_report(tmp_path, policy_for(Counter()))[0])


def test_an_app_base_imported_by_name_is_the_app_class_too():
    source = "from toga import App\n\nclass Main(App):\n    def start(self):\n        self.window_state = 1\n"
    assert scan_page_coupling(source, "app/src/kanamemory/main_ui.py").app_state == {"window_state"}
    other = "import toga\nfrom somewhere import App\n\nclass Main(App):\n    def start(self):\n        self.x = 1\n"
    assert scan_page_coupling(other, "app/src/kanamemory/main_ui.py").app_state == set()
