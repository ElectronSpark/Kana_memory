"""The decision of 继续学习 as a value (design 13 S4, acceptance A3): continue_runtime.decide.

On the path_app fixture, each kana-only outcome of the dispatch is reached by
a real continue action; the decision is observed where the action makes it:
the step it returns, and that nothing was drawn, no line entered and no
take-over asked while it was made (perform makes the call afterwards). The
import guard keeps the module free of Toga, the control components and the
app module, anywhere in the file.

Public: the kana あ/い/う of the bundled table and the core route's first
all-kana section, read from the content at run time.
"""
import ast
from datetime import timedelta
from pathlib import Path

import pytest

from kanamemory import continue_runtime, practice_round
from kanamemory.app import KanaMemory
from kanamemory.next_step.model import ExtraOr, KanaItem, NoKanaKind, Reference, RoundEnd, RouteEnd
from kanamemory.practice_availability import QueueAvailability
from support import continue_support as cs
from support.app_fixtures import question_app, setup_app  # noqa: F401
from support.learning_fixtures import learning_app  # noqa: F401
from support.study_home_support import orientation_seen, stub_can_continue
from support.study_path_runtime_support import kana_id, path_app, seed_main  # noqa: F401


@pytest.fixture
def app(path_app):
    """path_app with the orientation seen and "can 继续学习 reach anything" answering False."""
    orientation_seen(path_app)
    stub_can_continue(path_app, False)
    return path_app


def decided(app, monkeypatch, *, take_over=False):
    """Run one continue action; the step decide returned, after checking that nothing was called while it
    was made. The line entries do not draw (real=False) and the take-over answers ``take_over``."""
    log = cs.watch(app, monkeypatch, real=False, take_over=take_over)
    real = continue_runtime.decide
    seen = []

    def spy(*args, **kwargs):
        before = len(log)
        step = real(*args, **kwargs)
        seen.append((step, log[before:]))
        return step
    monkeypatch.setattr(continue_runtime, "decide", spy)
    app.continue_study_path()
    ((step, during),) = seen
    assert during == []
    return step


def reviewed(app, symbol, days=12):
    """A kana card of ``symbol`` rated ``days`` days ago: due now, not queued."""
    app.store.record_review(kana_id(app, symbol), "kana_choice", "hiragana", 3,
                            now=app._test_clock[0] - timedelta(days=days))


def availability(**counts):
    value = QueueAvailability(**counts)
    return lambda *args, **kwargs: value


def test_d3_a_due_kana_review_is_the_kana_line_with_its_prepared_token(app, monkeypatch):
    reviewed(app, "あ")
    sid, _ = seed_main(app)
    step = decided(app, monkeypatch)
    assert isinstance(step, KanaItem) and step.preserve_audio is True and step.first_teaching_targets is None
    assert type(step.prepared).__name__ == "PreparedKana" and step.prepared.session_id == sid


def test_d5_d6_a_round_with_nothing_left_is_the_round_end_after_extra_practice(app, monkeypatch):
    practice_round.enable(app.store)
    assert decided(app, monkeypatch) == ExtraOr(RoundEnd())
    assert practice_round.sealed(app.store) and practice_round.finished(app.store)


def test_d7_a_reference_entry_is_the_reference_page(app, monkeypatch):
    monkeypatch.setattr(app.study_path_service(), "next_reference_entry", lambda: {"id": "ref:synthetic"})
    assert decided(app, monkeypatch) == Reference("ref:synthetic")


def test_d8_a_waiting_kana_is_extra_practice_before_the_kana_line_without_preserve_audio(app, monkeypatch):
    sid, _ = seed_main(app, waiting=True)
    step = decided(app, monkeypatch)
    assert isinstance(step, ExtraOr) and isinstance(step.fallback, KanaItem)
    kana = step.fallback
    assert kana.preserve_audio is False and kana.first_teaching_targets is None
    assert kana.prepared.session_id == sid


def test_d10_waiting_kana_without_a_due_time_is_extra_practice_before_the_kana_line(app, monkeypatch):
    cs.patch_dispatch_availability(monkeypatch, availability(waiting_count=1))
    assert decided(app, monkeypatch) == ExtraOr(KanaItem())


def test_d11_gated_kana_alone_are_the_kana_line_without_extra_practice(app, monkeypatch):
    cs.patch_dispatch_availability(monkeypatch, availability(blocked_count=1))
    assert decided(app, monkeypatch) == KanaItem()


def test_d12_an_added_kana_section_without_a_usable_kind_is_its_page(app, monkeypatch):
    section = next(s for stage in app.study_path_service().overview()["stages"]
                   for unit in stage["units"] for s in unit["sections"]
                   if s["new_count"] and all(e["kind"] == "kana" for e in s["entries"]))
    app.study_path_service().add_sections([section["id"]])
    app._practice_preferences = KanaMemory._clean_preferences({
        "kind": "看罗马音手写假名", "mixed_kinds": ["romaji_handwriting"],
        "question_plan_version": 2, "listening_handwriting_opt_in_version": 1})
    app.model_state = "missing"
    assert decided(app, monkeypatch) == NoKanaKind()


@pytest.mark.parametrize("takes_over", [True, False], ids=["D13", "D14"])
def test_d13_d14_nothing_at_all_is_extra_practice_before_the_route_end(app, monkeypatch, takes_over):
    step = decided(app, monkeypatch, take_over=takes_over)
    assert isinstance(step, ExtraOr) and isinstance(step.fallback, RouteEnd)
    service = app.study_path_service()
    assert step.fallback.service is service and step.fallback.path == service.overview()


# --- the import guard ----------------------------------------------------------------------------------

FORBIDDEN = ("toga", "travertino", "kanamemory.app", "kanamemory.ui_components")


def _imports(path, package):
    """Absolute module names a source file imports anywhere (inside functions too), with imported names."""
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package.split(".")[:len(package.split(".")) - node.level + 1] if node.level else []
            module = ".".join([*base, node.module] if node.module else base)
            names.add(module)
            names.update(module + "." + alias.name for alias in node.names)
    return names


def test_continue_runtime_imports_no_toga_no_controls_and_no_app_module():
    path = Path(continue_runtime.__file__)
    imported = _imports(path, "kanamemory")
    assert "kanamemory.next_step.model" in imported      # the resolver sees the relative imports
    assert not [name for name in imported
                if any(name == forbidden or name.startswith(forbidden + ".") for forbidden in FORBIDDEN)]
