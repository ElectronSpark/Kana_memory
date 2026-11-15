"""What the kana practice shows next, pinned as it behaves today.

Showing a question first brings the practice up to date with the store: the saved progress of its session, the
frozen questions of a queue that has just started, the kinds the queue may ask, the prerequisites of its items
and the next head the store prepares. Then it shows the end of the queue, a page that stops instead of asking,
or a question. These tests pin that conversation with the store call by call, and every page the practice
draws itself on the way: its name, title, texts, buttons and what each button does. Where the practice stands
and what it keeps of an attempt on each of these pages is pinned in the two files beside this one. The tests
set and read the practice's state through ``support.kana_support`` only, press the buttons the page shows, and
build every question they need, so they run on the demo content.
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kanamemory import extra_practice_runtime, kana_dependency_repair, learning_content, new_dispatch, practice_round
from kanamemory import ui_components as component_ui
from kanamemory.app import KanaMemory
from kanamemory.app_clock import FrozenClock, using_clock
from kanamemory.arrangement.services import deck_presenter
from kanamemory.domain import Question
from kanamemory.session_content import RETIRE
from support import kana_support
from support.answer_option_ui_support import RECORDS, WORD, preview_app  # noqa: F401  (fixture)
from support.app_fixtures import question_app, setup_app  # noqa: F401  (fixtures)
from support.fake_ui import View


STORE_CALLS = {"load_session_progress", "get_session_questions", "save_session_progress", "reconcile_session_kinds",
               "prepare_next_question", "get_session_summary"}
ENTRIES = ("show_word_drill_setup", "show_practice_setup", "show_practice_hub", "show_home", "show_progress",
           "resume_practice", "continue_spaced_practice", "continue_study_path", "replace_content_question",
           "enroll_learning", "show_kana_introduction", "show_kana_intro_wait")
WAIT = "2030-01-01T09:00:00+00:00"


class Seen(View):
    """A fake control that also remembers how it was styled."""

    def __init__(self, *children, **kwargs):
        super().__init__(*children, **kwargs)
        self.look = {name: value for name, value in kwargs.items() if name not in {"text", "on_press", "style"}}


# --- questions, the practice and the store ------------------------------------------------------------------------

def kana(content_id="ka", script="hiragana", **metadata):
    record = next(r for r in RECORDS if r["id"] == content_id)
    other = next(r for r in RECORDS if r["id"] != content_id)
    return Question(content_id, "kana_choice", script, "", record["romaji"], options=[record["romaji"], other["romaji"]],
                    metadata={"kana_symbol": record[script], "romaji": record["romaji"], "content_version": "test",
                              **metadata})


def word(**metadata):
    return Question(WORD["id"], "word_meaning_kana", "hiragana", "", WORD["reading"], options=["か", "な"],
                    metadata={"romaji": "kana", "meaning_hint": "假名", "content_version": "test",
                              "kana_answer": WORD["reading"], **metadata})


def queue():
    """Two questions: a hiragana か, then a katakana ナ."""
    return [kana("ka"), kana("na", "katakana")]


@pytest.fixture
def next_app(preview_app, monkeypatch):
    """A practice in the word lane that holds the session "session" and its two questions at their start. Every
    page and entry the practice can go on to is a double; the round's seal and end are recorded."""
    app = preview_app
    monkeypatch.setattr(component_ui, "label", lambda text, **kw: Seen(text=text, style=SimpleNamespace(), **kw))
    monkeypatch.setattr(component_ui, "button", lambda text, handler, **kw: Seen(text=text, on_press=handler, **kw))
    app.note = lambda text: Seen(text=text, note=True)
    app.content_label = lambda text, **kw: Seen(text=text, content=True, **kw)
    for name in ENTRIES:
        setattr(app, name, Mock(name=name))
    app.store.practice_lane = "word_romaji"
    app.round = SimpleNamespace(sealed=Mock(), finished=Mock())
    monkeypatch.setattr(practice_round, "seal", app.round.sealed)
    monkeypatch.setattr(practice_round, "mark_finished", app.round.finished)
    monkeypatch.setattr(practice_round, "active", lambda store: False)
    monkeypatch.setattr(practice_round, "collecting", lambda store: False)
    monkeypatch.setattr(new_dispatch, "study_path_available", lambda value: False)
    monkeypatch.setattr(extra_practice_runtime, "take_over", lambda value: False)
    monkeypatch.setattr(extra_practice_runtime, "extra_button_rows", lambda value: [])
    app.repair = Mock(return_value={"added": 0})
    monkeypatch.setattr(kana_dependency_repair, "ensure_kana_prerequisites", app.repair)
    kana_support.with_state(app, session_id="session", questions=queue(), question_index=0, session_right=0)
    app.store.reset_mock()
    return app


def kinds_of(app):
    return list(KanaMemory._enabled_preference_kinds(app._practice_preferences))


def saved(app, monkeypatch, prepared, *, session="session", **fields):
    """The store holds saved progress of ``session`` and prepares ``prepared`` as the next head (None: nothing
    is left). Returns the saved progress."""
    pending = {"session_id": session, "questions": kana_support.questions(app), "question_index": 0,
               "session_right": 0, "state": {}, "assisted_indices": [], **fields}
    app.store.load_session_progress.return_value = pending
    app.store.reconcile_session_kinds.return_value = None
    app.store.prepare_next_question.return_value = prepared if prepared is None else {**pending, **prepared}
    app.availability = Mock(return_value=SimpleNamespace(waiting_count=3, ready_count=2))
    kana_support.replace_asked(monkeypatch, "summarize_queue_availability", app.availability)
    return pending


def conversation(app):
    """What the practice asked of the store about its queue, in order."""
    return [(name, args, kwargs) for name, args, kwargs in app.store.mock_calls if name in STORE_CALLS]


def drawn(app):
    """The page on the screen: its name, its title, and the texts of its own children in order."""
    return app.page, app.title_label.text, [child.text for child in app.body.children[1:]]


def control(app, text):
    return next(child for child in app.body.children[1:] if child.text == text)


def main_lane(app):
    app.store.practice_lane = "main"


# --- the conversation with the store -------------------------------------------------------------------------------

def test_a_practice_without_a_session_asks_the_store_nothing_and_seals_the_round(next_app):
    app = next_app
    kana_support.set_session_id(app, None)
    app.show_question()
    assert conversation(app) == []
    app.round.sealed.assert_called_once_with(app.store)
    assert app.page == "question" and kana_support.question(app) is kana_support.questions(app)[0]


def test_in_the_word_lane_a_queue_reads_its_progress_reconciles_its_kinds_and_prepares_its_head(next_app,
                                                                                               monkeypatch):
    app = next_app
    saved(app, monkeypatch, {})
    app.show_question()
    assert conversation(app) == [
        ("load_session_progress", (), {}),
        ("reconcile_session_kinds", ("session", kinds_of(app)), {}),
        ("prepare_next_question", ("session",), {})]
    app.repair.assert_not_called()
    assert app.page == "question"


@pytest.mark.parametrize("sound,model", [(False, "not_downloaded"), (True, "ready")])
def test_in_the_main_lane_the_queue_is_read_lazily_and_its_prerequisites_are_checked_before_the_head(
        next_app, monkeypatch, sound, model):
    app = next_app
    main_lane(app)
    app.platform.tts_ready, app.model_state = sound, model
    saved(app, monkeypatch, {})
    asked_after = []
    app.repair.side_effect = lambda *a, **k: asked_after.append(len(conversation(app))) or {"added": 0}
    app.show_question()
    assert conversation(app) == [
        ("load_session_progress", (), {"lazy_questions": True}),
        ("reconcile_session_kinds", ("session", kinds_of(app)), {"lazy_questions": True}),
        ("prepare_next_question", ("session",), {"lazy_questions": True, "fair_new": True})]
    app.repair.assert_called_once_with(app.repo, app.store, kinds_of(app), lazy_questions=True,
                                       audio_available=sound, handwriting_available=sound)
    assert asked_after == [2]


def test_added_prerequisites_make_the_practice_read_its_progress_again(next_app, monkeypatch):
    app = next_app
    main_lane(app)
    saved(app, monkeypatch, {})
    app.repair.return_value = {"added": 2}
    app.show_question()
    assert [entry[0] for entry in conversation(app)] == [
        "load_session_progress", "reconcile_session_kinds", "load_session_progress", "prepare_next_question"]
    assert conversation(app)[2] == ("load_session_progress", (), {"lazy_questions": True})


@pytest.mark.parametrize("lane,lazy", [("word_romaji", False), ("main", True)])
def test_a_queue_that_has_just_started_reads_its_frozen_questions_and_nothing_more(next_app, lane, lazy):
    app = next_app
    app.store.practice_lane = lane
    app.store.load_session_progress.return_value = None
    app.store.get_session_questions.return_value = queue()
    app.show_question()
    assert conversation(app) == [
        ("load_session_progress", (), {"lazy_questions": True} if lazy else {}),
        ("get_session_questions", ("session",), {"lazy": lazy})]
    app.round.sealed.assert_called_once_with(app.store)
    assert app.page == "question"


def test_a_queue_that_has_just_started_in_a_collecting_round_saves_its_first_checkpoint_and_goes_on(
        next_app, monkeypatch):
    app = next_app
    monkeypatch.setattr(practice_round, "collecting", lambda store: True)
    pending = saved(app, monkeypatch, {})
    app.store.load_session_progress.side_effect = [None, pending]
    app.store.get_session_questions.return_value = queue()
    app.show_question()
    assert conversation(app) == [
        ("load_session_progress", (), {}),
        ("get_session_questions", ("session",), {"lazy": False}),
        ("save_session_progress", ("session", 0), {}),
        ("load_session_progress", (), {}),
        ("reconcile_session_kinds", ("session", kinds_of(app)), {}),
        ("prepare_next_question", ("session",), {})]


def test_saved_progress_of_another_session_is_neither_reconciled_nor_prepared(next_app, monkeypatch):
    app = next_app
    saved(app, monkeypatch, {}, session="other")
    kana_support.set_question_index(app, 1)
    app.show_question()
    assert conversation(app) == [("load_session_progress", (), {})]
    app.round.sealed.assert_called_once_with(app.store)
    assert app.page == "question"


def test_a_reconcile_that_refuses_stops_before_anything_is_prepared_sealed_or_drawn(next_app, monkeypatch):
    app = next_app
    main_lane(app)
    pending = saved(app, monkeypatch, {})
    refuse = Mock(return_value=False)
    monkeypatch.setattr(KanaMemory, "_reconcile_practice_kinds", refuse)
    page, title = app.page, app.title_label.text
    app.show_question()
    refuse.assert_called_once_with(app, pending)
    assert conversation(app) == [("load_session_progress", (), {"lazy_questions": True})]
    app.repair.assert_not_called()
    app.round.sealed.assert_not_called()
    assert (app.page, app.title_label.text) == (page, title)


def test_a_queue_the_reconcile_calls_completed_is_not_prepared_and_ends(next_app, monkeypatch):
    app = next_app
    main_lane(app)
    pending = saved(app, monkeypatch, {})
    app.store.reconcile_session_kinds.return_value = {**pending, "completed": True}
    app.show_question()
    assert [entry[0] for entry in conversation(app)][:2] == ["load_session_progress", "reconcile_session_kinds"]
    app.store.prepare_next_question.assert_not_called()
    app.repair.assert_not_called()
    assert app.page == "session_done" and kana_support.question_index(app) == 2


@pytest.mark.parametrize("lane,lazy", [("word_romaji", False), ("main", True)])
def test_the_first_teaching_asks_for_a_teaching_head_of_the_queue_kinds_and_targets(next_app, monkeypatch, lane,
                                                                                    lazy):
    app = next_app
    app.store.practice_lane = lane
    saved(app, monkeypatch, {})
    app.show_question(_first_teaching=True, _first_teaching_targets=("ka", "na"))
    assert conversation(app)[-1] == (
        "prepare_next_question", ("session",),
        {"lazy_questions": lazy, "fair_new": lazy, "teaching_only": True, "teaching_kinds": kinds_of(app),
         "teaching_targets": ("ka", "na")})
    assert app.store.prepare_next_question.call_count == 1
    assert app.page == "question"


def test_a_first_teaching_without_a_teaching_head_takes_the_ordinary_head(next_app, monkeypatch):
    app = next_app
    pending = saved(app, monkeypatch, {})
    app.store.prepare_next_question.side_effect = [None, {**pending, "question_index": 1}]
    app.show_question(_first_teaching=True)
    calls = app.store.prepare_next_question.call_args_list
    assert len(calls) == 2 and calls[0].kwargs["teaching_only"] is True and calls[0].kwargs["teaching_targets"] is None
    assert (calls[1].args, calls[1].kwargs) == (("session",), {})
    assert kana_support.question_index(app) == 1


def proof(head, *current):
    """What the caller prepared for this same action: a head and the check that nothing was written since."""
    return SimpleNamespace(prepared=head, current=Mock(side_effect=list(current)))


def test_a_head_the_caller_prepared_is_used_while_its_proof_is_current(next_app, monkeypatch):
    app = next_app
    main_lane(app)
    pending = saved(app, monkeypatch, {})
    head = {**pending, "questions": queue(), "question_index": 1, "session_right": 1}
    given = proof(head, True, True)
    app.show_question(_prepared=given)
    assert conversation(app) == [("load_session_progress", (), {"lazy_questions": True})] * 2
    assert given.current.call_args_list == [((app.store, "session", kinds_of(app)), {})] * 2
    app.repair.assert_called_once()
    assert kana_support.questions(app) is head["questions"]
    assert (kana_support.question_index(app), kana_support.session_right(app)) == (1, 1)


def test_a_stale_proof_is_dropped_and_the_queue_is_reconciled_and_prepared_as_usual(next_app, monkeypatch):
    app = next_app
    main_lane(app)
    saved(app, monkeypatch, {})
    given = proof({"unused": True}, False)
    app.show_question(_prepared=given)
    assert [entry[0] for entry in conversation(app)] == [
        "load_session_progress", "load_session_progress", "reconcile_session_kinds", "prepare_next_question"]
    assert given.current.call_count == 1


def test_a_proof_that_goes_stale_after_the_prerequisites_were_checked_is_replaced_by_a_fresh_head(next_app,
                                                                                                 monkeypatch):
    app = next_app
    main_lane(app)
    saved(app, monkeypatch, {"question_index": 1})
    given = proof({"unused": True}, True, False)
    app.show_question(_prepared=given)
    assert [entry[0] for entry in conversation(app)] == [
        "load_session_progress", "load_session_progress", "prepare_next_question"]
    assert kana_support.question_index(app) == 1


def test_outside_the_main_lane_a_proof_is_not_looked_at_before_the_reconcile(next_app, monkeypatch):
    app = next_app
    pending = saved(app, monkeypatch, {})
    given = proof({**pending, "question_index": 1}, True)
    app.show_question(_prepared=given)
    assert [entry[0] for entry in conversation(app)] == [
        "load_session_progress", "reconcile_session_kinds", "prepare_next_question"]
    assert given.current.call_count == 0
    app.store.prepare_next_question.assert_called_once_with("session")


def test_a_first_teaching_ignores_a_head_the_caller_prepared(next_app, monkeypatch):
    app = next_app
    main_lane(app)
    saved(app, monkeypatch, {})
    given = proof({"unused": True}, True, True)
    app.show_question(_first_teaching=True, _prepared=given)
    assert given.current.call_count == 0
    assert [entry[0] for entry in conversation(app)] == [
        "load_session_progress", "reconcile_session_kinds", "prepare_next_question"]


def test_the_round_is_sealed_after_the_head_was_prepared_and_before_its_end_is_marked(next_app, monkeypatch):
    app = next_app
    saved(app, monkeypatch, None)
    order = Mock()
    order.sealed.side_effect = lambda store: order.store_calls(len(conversation(app)))
    monkeypatch.setattr(practice_round, "seal", order.sealed)
    monkeypatch.setattr(practice_round, "mark_finished", order.finished)
    app.show_question()
    assert [entry[0] for entry in order.mock_calls] == ["sealed", "store_calls", "finished"]
    order.store_calls.assert_called_once_with(3)


# --- the end of the queue ------------------------------------------------------------------------------------------

def at_the_end(app, **fields):
    kana_support.with_state(app, question_index=2, session_right=1, **fields)
    app.store.load_session_progress.return_value = None


@pytest.mark.parametrize("fixed,path,lane,route", [
    (True, True, "main", True), (False, True, "main", False), (True, False, "main", False),
    (True, True, "word_romaji", False)])
def test_only_a_fixed_round_of_the_main_lane_goes_on_with_the_route_and_then_its_end_is_not_marked(
        next_app, monkeypatch, fixed, path, lane, route):
    app = next_app
    at_the_end(app)
    app.store.practice_lane = lane
    app.store.get_session_summary.return_value = None
    monkeypatch.setattr(practice_round, "active", lambda store: fixed)
    monkeypatch.setattr(new_dispatch, "study_path_available", lambda value: path)
    kana_support.replace_asked(monkeypatch, "current_route_next", lambda service: ())
    page = app.page
    app.show_question()
    if route:
        app.continue_study_path.assert_called_once_with(_checkpoint_saved=True)
        app.round.finished.assert_not_called()
        app.store.get_session_summary.assert_not_called()
        assert app.page == page
    else:
        app.continue_study_path.assert_not_called()
        app.round.finished.assert_called_once_with(app.store)
        assert app.page == "session_done"


def test_a_practice_deck_that_opens_at_the_end_is_all_that_is_shown(next_app, monkeypatch):
    app = next_app
    at_the_end(app)
    opened = Mock(return_value=True)
    monkeypatch.setattr(KanaMemory, "_open_practice_deck", opened)
    taken = Mock(return_value=False)
    monkeypatch.setattr(extra_practice_runtime, "take_over", taken)
    page = app.page
    app.show_question()
    opened.assert_called_once_with(app)
    app.round.finished.assert_called_once_with(app.store)
    app.store.get_session_summary.assert_not_called()
    taken.assert_not_called()
    assert app.page == page


@pytest.mark.parametrize("summary,line", [
    ({"answered": 5, "content_count": 3, "skipped": 1}, "完成 5 词 · 答对 1 词"),
    (None, "完成 2 词 · 答对 1 词")])
def test_the_word_lane_ends_on_its_own_page(next_app, monkeypatch, summary, line):
    app = next_app
    at_the_end(app)
    app.store.get_session_summary.return_value = summary
    taken = Mock(return_value=True)
    monkeypatch.setattr(extra_practice_runtime, "take_over", taken)
    app.show_question()
    assert drawn(app) == ("session_done", "本组单词完成",
                          [line, "拼写记录已保存；符合条件的假名复习时间已调整。", "再练一组单词", "回到今日"])
    assert control(app, line).look == {"size": 24, "bold": True}
    assert control(app, "再练一组单词").look == {"primary": True} and control(app, "回到今日").look == {}
    app.store.get_session_summary.assert_called_once_with("session")
    taken.assert_not_called()
    control(app, "再练一组单词").on_press("pressed")
    app.show_word_drill_setup.assert_called_once_with("pressed")
    control(app, "回到今日").on_press("pressed")
    app.show_home.assert_called_once_with("pressed")


def test_extra_practice_may_take_over_the_end_of_a_batch(next_app, monkeypatch):
    app = next_app
    main_lane(app)
    at_the_end(app)
    app.store.get_session_summary.return_value = None
    taken = Mock(return_value=True)
    monkeypatch.setattr(extra_practice_runtime, "take_over", taken)
    page = app.page
    app.show_question()
    taken.assert_called_once_with(app)
    app.store.get_session_summary.assert_called_once_with("session")
    assert app.page == page


def test_the_end_of_a_batch_shows_its_summary_and_the_ways_on(next_app, monkeypatch):
    app = next_app
    main_lane(app)
    at_the_end(app)
    app.store.get_session_summary.return_value = {"answered": 7, "content_count": 4, "skipped": 2,
                                                  "practised": {"kana": 3}}
    monkeypatch.setattr(deck_presenter, "practised_line", lambda value: f"另练 {value['kana']} 个")
    monkeypatch.setattr(new_dispatch, "study_path_available", lambda value: True)
    service = object()
    app.study_path_service = lambda: service
    route_next = Mock(return_value=("route", "item"))
    kana_support.replace_asked(monkeypatch, "current_route_next", route_next)
    app._path_next_controls = Mock(return_value=[View(text="下一步")])
    monkeypatch.setattr(extra_practice_runtime, "extra_button_rows", lambda value: [View(text="加练")])
    app.show_question()
    summary = "练习 4 个假名记忆项 · 完成 7 题\n答对 1 题\n跳过 2 题（保留原有进度）\n另练 3 个"
    assert drawn(app) == ("session_done", "本批完成", [summary, "下一步", "加练", "查看复习记录", "回到今日"])
    assert control(app, summary).look == {"size": 24, "bold": True}
    assert control(app, "查看复习记录").look == {} and control(app, "回到今日").look == {}
    route_next.assert_called_once_with(service)
    app._path_next_controls.assert_called_once_with("route", "item")
    control(app, "查看复习记录").on_press("pressed")
    app.show_progress.assert_called_once_with("pressed")
    control(app, "回到今日").on_press("pressed")
    app.show_home.assert_called_once_with("pressed")


@pytest.mark.parametrize("questions,summary,text", [
    (None, None, "练习 2 个假名记忆项 · 完成 2 题\n答对 1 题"),
    ([], None, "完成 0 题\n答对 1 题"),
    (None, {"answered": 3, "content_count": 0, "skipped": 0}, "完成 3 题\n答对 1 题"),
    ([kana("ka"), kana("ka")], None, "练习 1 个假名记忆项 · 完成 2 题\n答对 1 题"),
])
def test_the_summary_of_a_batch_counts_its_items_and_questions(next_app, monkeypatch, questions, summary, text):
    app = next_app
    main_lane(app)
    at_the_end(app)
    if questions is not None:
        kana_support.with_state(app, questions=questions, question_index=len(questions))
    app.store.get_session_summary.return_value = summary
    app.show_question()
    assert drawn(app) == ("session_done", "本批完成", [text, "查看复习记录", "回到今日"])
    app._path_next_controls.assert_not_called()


def test_a_practice_without_a_session_reads_no_summary_at_its_end(next_app):
    app = next_app
    main_lane(app)
    at_the_end(app, session_id=None)
    app.show_question()
    app.store.get_session_summary.assert_not_called()
    assert drawn(app)[2][0] == "练习 2 个假名记忆项 · 完成 2 题\n答对 1 题"


# --- the pages that stop instead of asking -------------------------------------------------------------------------

def retired(question, name):
    return Question(question.content_id, question.kind, question.script, "", question.answer,
                    options=question.options, metadata={**question.metadata, RETIRE: name})


@pytest.mark.parametrize("lane,setup", [("word_romaji", "show_word_drill_setup"), ("main", "show_practice_setup")])
def test_updated_content_stops_on_a_page_that_offers_the_new_question(next_app, monkeypatch, lane, setup):
    app = next_app
    app.store.practice_lane = lane
    questions = [retired(kana("ka"), "before the place"), kana("na", "katakana"), retired(kana("ka"), "the one")]
    saved(app, monkeypatch, {"questions": questions, "question_index": 1, "content_blocked": True})
    app.show_question()
    assert drawn(app) == ("session_wait", "题目内容已更新",
                          ["原题和草稿已保存。更新后继续使用原有学习记录。", "使用更新后的题目", "练习设置", "回到今日"])
    assert control(app, "使用更新后的题目").look == {"primary": True}
    assert control(app, "练习设置").look == {} and control(app, "回到今日").look == {}
    control(app, "使用更新后的题目").on_press("pressed")
    app.replace_content_question.assert_called_once_with("session", "the one")
    control(app, "练习设置").on_press("pressed")
    getattr(app, setup).assert_called_once_with("pressed")
    control(app, "回到今日").on_press("pressed")
    app.show_home.assert_called_once_with("pressed")


@pytest.mark.parametrize("lane,setup", [("word_romaji", "show_word_drill_setup"), ("main", "show_practice_setup")])
def test_paused_kinds_stop_on_a_page_that_leads_to_the_settings(next_app, monkeypatch, lane, setup):
    app = next_app
    app.store.practice_lane = lane
    saved(app, monkeypatch, {"settings_blocked": True})
    app.show_question()
    assert drawn(app) == ("session_wait", "题型已暂停",
                          ["未完成的题目和草稿已保留。开启题型后可继续。", "练习设置", "回到今日"])
    assert control(app, "练习设置").look == {"primary": True} and control(app, "回到今日").look == {}
    control(app, "练习设置").on_press("pressed")
    getattr(app, setup).assert_called_once_with("pressed")
    control(app, "回到今日").on_press("pressed")
    app.show_home.assert_called_once_with("pressed")


def test_missing_prerequisite_kana_stop_on_a_page_that_names_them(next_app, monkeypatch):
    app = next_app
    blocked = ["kana:ka:hiragana", "kana:na:katakana", "kana:ka:hiragana", "kana:unknown:hiragana", "kana:kya:romaji"]
    saved(app, monkeypatch, {"dependency_blocked": True, "blocked_prerequisites": blocked})
    app.show_question()
    kept = "原题与进度已保留。请检查这些假名是否已暂停，或相应题型是否可用。"
    assert drawn(app) == ("dependency_wait", "先完成前置假名", ["か · ナ · kya", kept, "检查并继续", "练习设置", "回到今日"])
    assert control(app, "か · ナ · kya").look == {"content": True, "size": 26, "color": component_ui.GREEN,
                                                 "text_role": "study"}
    assert control(app, kept).look == {"size": 15, "color": component_ui.MUTED}
    assert control(app, "检查并继续").look == {"primary": True} and control(app, "练习设置").look == {}
    control(app, "检查并继续").on_press("pressed")
    app.resume_practice.assert_called_once_with("pressed")
    control(app, "练习设置").on_press("pressed")
    app.show_practice_hub.assert_called_once_with("pressed")
    control(app, "回到今日").on_press("pressed")
    app.show_home.assert_called_once_with("pressed")


def test_a_missing_rule_stops_on_a_page_that_offers_its_lesson(next_app, monkeypatch):
    app = next_app
    rule = {"id": "pronunciation:long", "title": "长音怎么读"}
    looked_up = Mock(side_effect=lambda identity: rule if identity == "pronunciation:long" else None)
    monkeypatch.setattr(learning_content, "get_learning_item", looked_up)
    blocked = ["pronunciation:long", "kana:ka:hiragana", "pronunciation:gone"]
    saved(app, monkeypatch, {"dependency_blocked": True, "blocked_prerequisites": blocked})
    app.show_question()
    kept = "原题与进度已保留。先看规则讲解，再继续练习。"
    assert drawn(app) == ("dependency_wait", "先学所需规则", ["か", "长音怎么读", kept, "检查并继续", "练习设置", "回到今日"])
    assert control(app, "长音怎么读").look == {"primary": True, "multiline": True}
    assert [entry.args for entry in looked_up.call_args_list] == [("pronunciation:long",), ("pronunciation:gone",)]
    control(app, "长音怎么读").on_press("pressed")
    app.enroll_learning.assert_called_once_with(rule)


def test_a_blocked_head_without_named_prerequisites_shows_an_empty_line(next_app, monkeypatch):
    app = next_app
    saved(app, monkeypatch, {"dependency_blocked": True})
    app.show_question()
    assert drawn(app)[:2] == ("dependency_wait", "先完成前置假名") and drawn(app)[2][0] == ""


def test_the_first_stop_that_applies_is_the_one_shown(next_app, monkeypatch):
    app = next_app
    questions = [retired(kana("ka"), "the one"), kana("na", "katakana")]
    saved(app, monkeypatch, {"questions": questions, "content_blocked": True, "settings_blocked": True,
                             "dependency_blocked": True, "waiting_until_utc": WAIT})
    app.show_question()
    assert app.title_label.text == "题目内容已更新"
    saved(app, monkeypatch, {"settings_blocked": True, "dependency_blocked": True, "waiting_until_utc": WAIT})
    app.show_question()
    assert app.title_label.text == "题型已暂停"
    saved(app, monkeypatch, {"dependency_blocked": True, "waiting_until_utc": WAIT})
    app.show_question()
    assert app.title_label.text == "先完成前置假名"


# --- the first introduction of a kana ------------------------------------------------------------------------------

def introductions(app, monkeypatch, *, introduce=False, due=None):
    app.learning_store = Mock()
    asked = SimpleNamespace(introduce=Mock(return_value=introduce), due=Mock(return_value=due))
    kana_support.replace_asked(monkeypatch, "should_introduce", asked.introduce)
    kana_support.replace_asked(monkeypatch, "intro_wait_until", asked.due)
    return asked


def test_a_kana_that_should_be_introduced_is_introduced_instead_of_asked(next_app, monkeypatch):
    app = next_app
    saved(app, monkeypatch, {})
    asked = introductions(app, monkeypatch, introduce=True)
    page = app.page
    app.show_question()
    question = kana_support.question(app)
    asked.introduce.assert_called_once_with(app, question)
    app.show_kana_introduction.assert_called_once_with(question)
    asked.due.assert_not_called()
    assert app.page == page


def test_an_introduction_that_still_waits_shows_its_wait_and_one_that_is_over_asks(next_app, monkeypatch):
    app = next_app
    saved(app, monkeypatch, {})
    with using_clock(FrozenClock("2030-01-01T08:00:00+00:00")):
        later = FrozenClock("2030-01-01T08:00:30+00:00").utc_now()
        asked = introductions(app, monkeypatch, due=later)
        app.show_question()
        asked.due.assert_called_once_with(app.store, kana_support.question(app))
        app.show_kana_intro_wait.assert_called_once_with(later)
        assert app.page != "question"
        introductions(app, monkeypatch, due=FrozenClock("2030-01-01T07:59:59+00:00").utc_now())
        app.show_question()
        assert app.page == "question" and app.show_kana_intro_wait.call_count == 1


@pytest.mark.parametrize("case", ["no course store", "waiting", "answer started", "result shown"])
def test_no_introduction_is_considered_without_a_course_store_or_for_a_head_that_waits_or_was_started(
        next_app, monkeypatch, case):
    app = next_app
    extra = {"waiting": {"waiting_until_utc": WAIT}, "answer started": {"state": {"page": "question"}},
             "result shown": {"state": {"page": "feedback"}}}.get(case, {})
    saved(app, monkeypatch, extra)
    asked = introductions(app, monkeypatch, introduce=True)
    if case == "no course store":
        del app.learning_store
    with using_clock(FrozenClock("2030-01-01T08:00:00+00:00")):
        app.show_question()
    asked.introduce.assert_not_called()
    asked.due.assert_not_called()
    app.show_kana_introduction.assert_not_called()


# --- what the question says about the practice ---------------------------------------------------------------------

@pytest.mark.parametrize("metadata,research,keyboard", [
    ({}, False, "kept"),
    ({"research_content": True}, True, "kept"),
    ({"practice_config": {"include_research": True, "word_table": "full"}}, True, True),
    ({"research_content": False, "practice_config": {"include_research": True}}, False, "kept"),
    ({"word_table": "partial", "practice_config": {"word_table": "full"}}, False, False),
    ({"word_table": "other"}, False, "kept"),
])
def test_a_question_that_is_asked_sets_the_research_flag_and_the_keyboard_from_its_own_settings(
        next_app, monkeypatch, metadata, research, keyboard):
    app = next_app
    app.research_enabled, app.use_full_keyboard = "kept", "kept"
    saved(app, monkeypatch, {"questions": [kana("ka", **metadata), kana("na", "katakana")]})
    app.show_question()
    assert app.page == "question"
    assert (app.research_enabled, app.use_full_keyboard) == (research, keyboard)


@pytest.mark.parametrize("stop", ["content_blocked", "settings_blocked", "dependency_blocked", "introduced"])
def test_a_page_that_stops_before_the_question_leaves_the_research_flag_and_the_keyboard(next_app, monkeypatch,
                                                                                         stop):
    app = next_app
    app.research_enabled, app.use_full_keyboard = "kept", "kept"
    settings = {"research_content": True, "word_table": "full"}
    questions = [retired(kana("ka", **settings), "the one"), kana("na", "katakana")]
    saved(app, monkeypatch, {"questions": questions, **({} if stop == "introduced" else {stop: True})})
    if stop == "introduced":
        introductions(app, monkeypatch, introduce=True)
    app.show_question()
    assert (app.research_enabled, app.use_full_keyboard) == ("kept", "kept")


def test_the_review_wait_takes_the_research_flag_and_the_keyboard_of_the_waiting_question(next_app, monkeypatch):
    app = next_app
    app.research_enabled, app.use_full_keyboard = "kept", "kept"
    questions = [kana("ka", research_content=True, word_table="full"), kana("na", "katakana")]
    saved(app, monkeypatch, {"questions": questions, "waiting_until_utc": WAIT})
    with using_clock(FrozenClock("2030-01-01T08:00:00+00:00")):
        app.show_question()
    assert app.page == "session_wait" and (app.research_enabled, app.use_full_keyboard) == (True, True)


# --- the review wait -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("now,status,due", [
    ("2030-01-01T08:59:30+00:00", "距离下一题还有 30 秒。", "09:00:00"),
    ("2029-12-31T23:59:00+00:00", "距离下一题还有 9 小时 1 分 0 秒。", "01月01日 09:00:00"),
    ("2030-01-01T09:00:00+00:00", "已到期，可以继续复习。", "09:00:00"),
])
def test_a_head_that_waits_stops_on_the_review_wait_with_its_time_and_what_is_saved(next_app, monkeypatch, now,
                                                                                    status, due):
    app = next_app
    pending = saved(app, monkeypatch, {"waiting_until_utc": WAIT})
    app.loop.reset_mock()
    with using_clock(FrozenClock(now)):
        app.show_question()
    saved_line = "已保存 3 道后续复习题，到期后自动继续。也可以先回到今日。"
    assert drawn(app) == ("session_wait", "本轮先歇一会儿",
                          [status, f"下一题可在 {due} 开始", saved_line, "检查并继续复习", "回到今日"])
    assert control(app, status).look == {"size": 20, "bold": True, "color": component_ui.GREEN}
    assert control(app, f"下一题可在 {due} 开始").look == {"size": 15, "color": component_ui.MUTED}
    assert control(app, "检查并继续复习").look == {"primary": True} and control(app, "回到今日").look == {}
    app.availability.assert_called_once_with(app.store, {**pending, "waiting_until_utc": WAIT})
    app.loop.call_soon.assert_called_once_with(KanaMemory._tick_review_wait, app, app._page_token)
    control(app, "检查并继续复习").on_press("pressed")
    app.continue_spaced_practice.assert_called_once_with("pressed")
    control(app, "回到今日").on_press("pressed")
    app.show_home.assert_called_once_with("pressed")


def test_the_review_wait_offers_the_route_and_extra_practice_between_its_button_and_the_way_home(next_app,
                                                                                                  monkeypatch):
    app = next_app
    saved(app, monkeypatch, {"waiting_until_utc": WAIT})
    monkeypatch.setattr(new_dispatch, "study_path_available", lambda value: True)
    service = object()
    app.study_path_service = lambda: service
    route_next = Mock(return_value=("route", "item"))
    kana_support.replace_asked(monkeypatch, "current_route_next", route_next)
    app._path_next_controls = Mock(return_value=[View(text="下一步")])
    monkeypatch.setattr(extra_practice_runtime, "extra_button_rows", lambda value: [View(text="加练")])
    with using_clock(FrozenClock("2030-01-01T08:59:30+00:00")):
        app.show_question()
    assert drawn(app)[2][3:] == ["检查并继续复习", "下一步", "加练", "回到今日"]
    route_next.assert_called_once_with(service)
    app._path_next_controls.assert_called_once_with("route", "item", compact=True)


# --- a parked word --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("page", ["question", "word_question", "ink_question", "feedback"])
def test_a_word_whose_answer_was_started_is_restored_instead_of_drawn_empty(next_app, monkeypatch, page):
    app = next_app
    saved(app, monkeypatch, {"questions": [word(), kana("na", "katakana")], "state": {"page": page}})
    before = app.page
    app.show_question()
    app.resume_practice.assert_called_once_with(preserve_audio=True)
    assert app.page == before


@pytest.mark.parametrize("case", ["restoring", "not started", "a kana question"])
def test_a_question_is_drawn_when_it_is_no_started_word_or_is_being_restored(next_app, monkeypatch, case):
    app = next_app
    first = kana("ka") if case == "a kana question" else word()
    state = {} if case == "not started" else {"page": "word_question"}
    saved(app, monkeypatch, {"questions": [first, kana("na", "katakana")], "state": state})
    if case == "restoring":
        app._restoring_practice = True
    app.show_question()
    app.resume_practice.assert_not_called()
    assert app.page == ("question" if case == "a kana question" else "word_question")


# --- the title of a question page -----------------------------------------------------------------------------------

def test_a_prepared_queue_is_titled_with_what_it_can_still_ask_now(next_app, monkeypatch):
    app = next_app
    pending = saved(app, monkeypatch, {"question_index": 1})
    app.show_question()
    assert app.title_label.text == "本轮剩 2 题 · 第 2 / 2 题"
    app.availability.assert_called_once_with(app.store, {**pending, "question_index": 1})


def test_a_queue_without_a_prepared_head_is_titled_with_what_is_left_of_it(next_app):
    app = next_app
    kana_support.with_state(app, session_id=None, question_index=1)
    app.show_question()
    assert app.title_label.text == "本轮剩 1 题 · 第 2 / 2 题"


def test_a_word_drill_is_titled_with_the_words_left(next_app, monkeypatch):
    app = next_app
    saved(app, monkeypatch, {"questions": [kana("ka", word_romaji_drill=True), kana("na", "katakana")]})
    app.show_question()
    assert app.title_label.text == "拼写 · 剩 2 词"
