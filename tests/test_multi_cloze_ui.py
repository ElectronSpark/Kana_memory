from copy import deepcopy
from types import SimpleNamespace

import pytest

from kanamemory import multi_cloze as cloze
from kanamemory.multi_cloze_ui import controls
from support.multi_cloze_support import question, ui, descendants


def setup(*, save=None, submit=None, width=360, scale=1):
    q = question()
    saved, submitted, active = [], [], [True]
    view = controls(SimpleNamespace(main_window=SimpleNamespace(size=SimpleNamespace(width=width))),
                    ui(scale), q, cloze.initial_draft(q),
                    save or (lambda draft: saved.append(deepcopy(draft))),
                    submit or (lambda answer: (submitted.append(answer), True)[1]), lambda: active[0])
    return q, view, view._multi_cloze_context, saved, submitted, active


def test_slot_choices_save_then_advance_and_submit_once():
    q, view, ctx, saved, submitted, active = setup()
    assert not ctx["submit"].enabled and not ctx["clear"].enabled
    assert ctx["slots"]["slot1"].style["font_weight"] == "bold"
    ctx["choices"][0].press()
    assert len(saved) == 1 and ctx["draft"]["cloze_state"]["active_slot"] == "slot2"
    assert ctx["slots"]["slot1"].text == "① ✓"
    assert ctx['clear'].enabled  # next gap is empty, but the previous choice can be undone
    ctx["choices"][0].press()
    assert ctx["submit"].enabled
    ctx["submit"].press()
    ctx["submit"].press()
    assert submitted == [q["answer"]]
    assert ctx["locked"]
    assert not any("cloze1:" in node.text for node in descendants(view))


@pytest.mark.parametrize("raises", [False, True])
def test_failed_save_preserves_draft_and_visible_selection(raises):
    def fail(draft):
        if raises:
            raise ValueError("synthetic failure")
        return False
    _, _, ctx, _, submitted, _ = setup(save=fail)
    before = deepcopy(ctx["draft"])
    ctx["choices"][0].press()
    assert ctx["draft"] == before
    assert ctx["slots"]["slot1"].text == "①" and not submitted
    assert "未保存" in ctx["status"].text


def test_raised_save_and_submit_failures_record_only_error_metadata(monkeypatch):
    from kanamemory import diagnostic_events as events
    monkeypatch.setattr(events, "_buffer", events.DiagnosticBuffer())
    def fail(value):
        raise ValueError("private draft answer")
    _, _, ctx, _, _, _ = setup(save=fail)
    ctx["choices"][0].press()
    _, _, ctx, _, _, _ = setup(submit=fail)
    ctx["choices"][0].press(); ctx["choices"][0].press(); ctx["submit"].press()
    assert not ctx["locked"] and "未提交" in ctx["status"].text
    rows = events.error_events()
    assert [(row["operation"], row["stage"], row["error_type"]) for row in rows] == [
        ("multi_cloze", "save", "ValueError"), ("multi_cloze", "commit", "ValueError")]
    assert "private" not in repr(rows)


def test_selecting_old_slot_does_not_change_which_choice_was_last():
    _, _, ctx, saved, _, _ = setup()
    ctx["choices"][0].press()
    ctx["choices"][0].press()
    ctx["slots"]["slot1"].press()
    ctx["clear"].press()
    assert ctx["draft"]["cloze_state"]["values"] == {"slot1": "は"}
    assert ctx["draft"]["answer"] == "" and not ctx["submit"].enabled
    assert len(saved) == 4


def test_stale_choice_and_offscreen_handlers_cannot_edit_or_submit():
    _, _, ctx, saved, submitted, active = setup()
    stale_choice = ctx["choices"][1]
    ctx["choices"][0].press()
    stale_choice.press()
    assert len(saved) == 1
    active[0] = False
    ctx["choices"][0].press()
    ctx["slots"]["slot1"].press()
    ctx["submit"].handler()
    assert len(saved) == 1 and not submitted


def test_restored_complete_draft_keeps_choices_and_allows_local_revision():
    q = question()
    draft = cloze.edit_slot(q, cloze.edit_slot(q, cloze.initial_draft(q), value="は"), value="に")
    prior = deepcopy(draft)
    view = controls(SimpleNamespace(), ui(), q, draft, lambda state: None, lambda answer: None, lambda: True)
    ctx = view._multi_cloze_context
    assert draft == prior and ctx["draft"] == prior and ctx["submit"].enabled
    assert ctx["slots"]["slot2"].text == "② ✓"


def test_submit_failure_unlocks_same_draft_for_retry():
    calls = []
    def submit(answer):
        calls.append(answer)
        return len(calls) > 1
    q, _, ctx, _, _, _ = setup(submit=submit)
    ctx["choices"][0].press()
    ctx["choices"][0].press()
    ctx["submit"].press()
    assert not ctx["locked"] and ctx["draft"]["answer"] == q["answer"]
    ctx["submit"].press()
    assert ctx["locked"] and calls == [q["answer"], q["answer"]]


def test_nonterminal_guide_submission_remains_editable_without_failure_message():
    calls = []
    q, _, ctx, _, _, _ = setup(submit=lambda answer: calls.append(answer))
    ctx["choices"][0].press()
    ctx["choices"][0].press()
    ctx["submit"].press()
    assert calls == [q["answer"]] and not ctx["locked"]
    assert ctx["status"].text == "" and ctx["submit"].enabled
    ctx["slots"]["slot1"].press()
    ctx["choices"][1].press()
    assert ctx["draft"]["cloze_state"]["values"]["slot1"] == "を"


@pytest.mark.parametrize("width,scale", [(208, 1.3), (360, 2), (360, 1)])
def test_long_choices_use_one_column_without_fixed_text_height(width, scale):
    q = question()
    q["multi_cloze"]["slots"][0]["choices"].append("にほかならないということは")
    view = controls(SimpleNamespace(main_window=SimpleNamespace(size=SimpleNamespace(width=width))),
                    ui(scale), q, cloze.initial_draft(q), lambda state: None, lambda answer: None, lambda: True)
    option_rows = view.children[1].children
    assert all(len(row.children) == 1 for row in option_rows)
    assert all(len(row.children) <= 3 for row in view.children[0].children)
    assert all("height" not in button.style and button.style["multiline"]
               for button in view._multi_cloze_context["choices"])
