"""Where the kana practice stands in its session, pinned as it behaves today.

The practice holds the session whose queue it works through, the queue itself, the place of the question on the
page in that queue (the queue's length once it is finished), how many answers were right so far, the question on
the page and its script. These tests press the page's buttons, or call the entry the page calls, and read and set
that position through ``support.kana_support`` only. Every question and record they need is built here, and every
builder that would draw at random is replaced, so they run on the demo content.
"""
import asyncio
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from kanamemory import domain as domain_module
from kanamemory import (extra_practice_runtime, feedback_context, feedback_exposure, new_dispatch, practice_round,
                        romaji_input_ui, study_path_runtime)
from kanamemory.app import KanaMemory
from kanamemory.domain import Question
from kanamemory.navigation import BackStack, PageState
from kanamemory.session_content import RETIRE
from support import kana_support
from support.answer_option_ui_support import RECORDS, preview_app  # noqa: F401  (fixture)
from support.app_fixtures import CHOICE, app_module, question_app, set_mode, setup_app  # noqa: F401  (fixtures)
from support.fake_ui import View, text_of


# --- questions and queues -----------------------------------------------------------------------------------------

def kana(content_id="ka", script="hiragana", **metadata):
    record = next(r for r in RECORDS if r["id"] == content_id)
    other = next(r for r in RECORDS if r["id"] != content_id)
    return Question(content_id, "kana_choice", script, "", record["romaji"], options=[record["romaji"], other["romaji"]],
                    metadata={"kana_symbol": record[script], "romaji": record["romaji"], "content_version": "test",
                              **metadata})


def queue():
    """Two questions: a hiragana か, then a katakana ナ."""
    return [kana("ka"), kana("na", "katakana")]


def set_position(app, **fields):
    return kana_support.with_state(app, **fields)


def unchanged(app, before):
    """The position equals ``before`` and still holds the same queue list."""
    return kana_support.position(app) == before and kana_support.questions(app) is before["questions"]


def stub_pages(app):
    for name in ("show_word_drill_setup", "show_practice_hub", "show_home", "show_progress", "resume_practice",
                 "continue_spaced_practice", "continue_study_path"):
        setattr(app, name, Mock())


def saved_queue(app, monkeypatch, prepared, *, lane="word_romaji", session="session"):
    """The store holds a saved queue of ``session`` and prepares ``prepared`` as its next head (None: nothing)."""
    app.store.practice_lane = lane
    stub_pages(app)
    monkeypatch.setattr(app_module, "summarize_queue_availability",
                        lambda *a, **k: SimpleNamespace(waiting_count=1, ready_count=1))
    pending = {"session_id": session, "questions": kana_support.questions(app), "question_index": 0,
               "session_right": 0, "state": {}, "assisted_indices": []}
    app.store.load_session_progress.return_value = pending
    app.store.reconcile_session_kinds.return_value = None
    app.store.prepare_next_question.return_value = prepared if prepared is None else {**pending, **prepared}
    return pending


# --- the app's start ------------------------------------------------------------------------------------------------

def test_a_fresh_app_holds_no_session_an_empty_queue_and_hiragana(monkeypatch):
    from kanamemory import content_pack, material_qualification, practice_archive, retention_schedule
    for module, name in ((content_pack, "validate_installed_pack"), (material_qualification, "bind_resources"),
                         (practice_archive, "archive_previous_rounds"), (practice_round, "enable"),
                         (retention_schedule, "visit")):
        monkeypatch.setattr(module, name, Mock())
    for name in ("ContentRepository", "StudyStore", "LearningStore", "create_platform", "KanaLinkIndex",
                 "OriginImageCatalog"):
        monkeypatch.setattr(app_module, name, MagicMock())
    monkeypatch.setattr(app_module, "ui", MagicMock(**{"font_scale.return_value": 1}))
    monkeypatch.setattr(app_module.toga, "MainWindow", MagicMock())
    monkeypatch.setattr(app_module, "_HEAP_FROZEN", True)
    monkeypatch.setattr("kanamemory.thread_switching.shorten_switch_interval", Mock())
    app = MagicMock()
    KanaMemory.startup(app)
    assert kana_support.position(app) == {"session_id": None, "questions": [], "question_index": 0,
                                          "session_right": 0, "question": None, "script": "hiragana"}
    assert type(kana_support.questions(app)) is list


# --- a question is shown ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("lane,lazy", [("word_romaji", False), ("main", True)])
def test_a_just_started_queue_takes_the_frozen_questions_of_its_session(preview_app, lane, lazy):
    app = preview_app
    app.store.practice_lane = lane
    app.store.load_session_progress.return_value = None
    frozen = queue()
    app.store.get_session_questions.return_value = frozen
    set_position(app, questions=queue())
    app.show_question()
    app.store.get_session_questions.assert_called_once_with("session", lazy=lazy)
    assert kana_support.questions(app) is frozen and kana_support.question(app) is frozen[0]
    assert (kana_support.question_index(app), kana_support.session_right(app), kana_support.script(app)) == (
        0, 0, "hiragana")


def test_a_just_started_queue_keeps_its_own_questions_when_the_frozen_number_differs(preview_app):
    app = preview_app
    app.store.practice_lane = "word_romaji"
    app.store.load_session_progress.return_value = None
    app.store.get_session_questions.return_value = queue()[:1]
    own = queue()
    set_position(app, questions=own)
    app.show_question()
    assert kana_support.questions(app) is own and kana_support.question(app) is own[0]


@pytest.mark.parametrize("index,right", [(1, 0), (0, 1)])
def test_a_queue_already_under_way_is_not_replaced_by_the_frozen_questions(preview_app, index, right):
    app = preview_app
    app.store.practice_lane = "word_romaji"
    app.store.load_session_progress.return_value = None
    own = queue()
    set_position(app, questions=own, question_index=index, session_right=right)
    app.show_question()
    app.store.get_session_questions.assert_not_called()
    assert kana_support.questions(app) is own and kana_support.question(app) is own[index]
    assert kana_support.script(app) == own[index].script


def test_without_a_session_no_frozen_questions_are_read(preview_app):
    app = preview_app
    app.store.practice_lane = "word_romaji"
    set_position(app, session_id=None, questions=queue())
    app.show_question()
    app.store.load_session_progress.assert_not_called()
    app.store.get_session_questions.assert_not_called()
    assert kana_support.question(app) is kana_support.questions(app)[0]


@pytest.mark.parametrize("blocked", [None, "content_blocked", "settings_blocked", "dependency_blocked", "waiting"])
def test_a_prepared_head_sets_the_queue_the_place_and_the_right_answers(preview_app, monkeypatch, blocked):
    # Every page drawn from a prepared head, the stopping ones included, holds the head's place and question.
    app = preview_app
    set_position(app, questions=queue(), question_index=0, session_right=0, question="before", script="before")
    prepared_queue = queue()
    if blocked == "content_blocked":
        q = prepared_queue[1]
        prepared_queue[1] = Question(q.content_id, q.kind, q.script, "", q.answer, options=q.options,
                                     metadata={**q.metadata, RETIRE: "retired-na"})
    extra = ({} if blocked is None else {"waiting_until_utc": "2030-01-01T09:00:00+00:00"} if blocked == "waiting"
             else {blocked: True})
    saved_queue(app, monkeypatch, {"questions": prepared_queue, "question_index": 1, "session_right": 1, **extra})
    app.show_question()
    assert app.page == {None: "question", "dependency_blocked": "dependency_wait"}.get(blocked, "session_wait")
    assert kana_support.questions(app) is prepared_queue
    assert kana_support.position(app) == {"session_id": "session", "questions": prepared_queue, "question_index": 1,
                                          "session_right": 1, "question": prepared_queue[1], "script": "katakana"}


def test_nothing_prepared_puts_the_place_at_the_end_of_the_queue_and_keeps_the_rest(preview_app, monkeypatch):
    app = preview_app
    own = queue()
    set_position(app, questions=own, question_index=0, session_right=1, question="on the page", script="katakana")
    pending = saved_queue(app, monkeypatch, None)
    pending["questions"] = queue()
    app.show_question()
    assert app.page == "session_done" and "完成 2 词 · 答对 1 词" in text_of(app.body)
    assert kana_support.position(app) == {"session_id": "session", "questions": own, "question_index": 2,
                                          "session_right": 1, "question": "on the page", "script": "katakana"}
    assert kana_support.questions(app) is own


def test_a_saved_queue_of_another_session_leaves_the_place_and_draws_the_question_there(preview_app, monkeypatch):
    app = preview_app
    own = queue()
    set_position(app, questions=own, question_index=1, session_right=0)
    saved_queue(app, monkeypatch, {"question_index": 0}, session="other")
    app.show_question()
    app.store.prepare_next_question.assert_not_called()
    assert kana_support.question_index(app) == 1 and kana_support.question(app) is own[1]
    assert kana_support.script(app) == "katakana" and kana_support.questions(app) is own


def test_a_refused_reconcile_leaves_the_whole_position(preview_app, monkeypatch):
    app = preview_app
    set_position(app, questions=queue(), question_index=1, session_right=1, question="on the page", script="x")
    saved_queue(app, monkeypatch, {})
    monkeypatch.setattr(KanaMemory, "_reconcile_practice_kinds", lambda self, pending=None: False)
    before = kana_support.position(app)
    app.show_question()
    assert unchanged(app, before)


@pytest.mark.parametrize("ending", ["route", "deck", "word-drill", "taken-over", "batch-done"])
def test_the_end_of_the_queue_leaves_the_position_as_it_is_whatever_page_follows(preview_app, monkeypatch, ending):
    app = preview_app
    stub_pages(app)
    app.store.practice_lane = "word_romaji" if ending == "word-drill" else "main"
    app.store.load_session_progress.return_value = None
    set_position(app, questions=queue(), question_index=2, session_right=1, question="last shown", script="katakana")
    monkeypatch.setattr(practice_round, "active", lambda store: ending == "route")
    monkeypatch.setattr(new_dispatch, "study_path_available", lambda value: ending == "route")
    monkeypatch.setattr(KanaMemory, "_open_practice_deck", lambda self: ending == "deck")
    monkeypatch.setattr(extra_practice_runtime, "take_over", lambda value: ending == "taken-over")
    monkeypatch.setattr(extra_practice_runtime, "extra_button_rows", lambda value: [])
    app.page = "question"
    before = kana_support.position(app)
    app.show_question()
    assert unchanged(app, before)
    if ending == "route":
        app.continue_study_path.assert_called_once_with(_checkpoint_saved=True)
    if ending in {"word-drill", "batch-done"}:
        assert app.page == "session_done"
        assert ("完成 2 词 · 答对 1 词" if ending == "word-drill" else "完成 2 题\n答对 1 题") in text_of(app.body)
    else:
        assert app.page == "question"


# --- a rating -----------------------------------------------------------------------------------------------------

@pytest.fixture
def rating_app(preview_app, monkeypatch):
    app = preview_app
    app.store.record_review.return_value = {"scheduled": True}
    seen = []
    dispatched = Mock(side_effect=lambda value: seen.append(kana_support.position(value)))
    monkeypatch.setattr(KanaMemory, "_dispatch_after_answer", dispatched)
    app.dispatched, app.seen = dispatched, seen
    return app


def answered(app, question, answer):
    kana_support.set_questions(app, [question, kana("na", "katakana")])
    app.show_question()
    kana_support.set_question_audio_ok(app, True)
    app.check_answer(answer)
    assert app.page == "feedback"


@pytest.mark.parametrize("answer,metadata,right", [
    ("ka", {}, 1), ("na", {}, 0), ("ka", {"spelling_correction_of": "earlier"}, 0)])
def test_a_rating_moves_on_one_place_and_counts_only_a_right_answer_that_is_no_correction(rating_app, answer,
                                                                                          metadata, right):
    app = rating_app
    question = kana(**metadata)
    answered(app, question, answer)
    queue_before = kana_support.questions(app)
    KanaMemory.save_rating(app, 3 if answer == "ka" else 1)
    assert kana_support.position(app) == {"session_id": "session", "questions": queue_before, "question_index": 1,
                                          "session_right": right, "question": question, "script": "hiragana"}
    assert kana_support.questions(app) is queue_before
    app.store.get_session_questions.assert_not_called()


@pytest.mark.parametrize("lane,spaced,extended,call", [
    ("word_romaji", True, None, (("session",), {})),
    ("main", True, None, (("session",), {"lazy": True})),
    ("word_romaji", False, True, (("session",), {})),
    ("main", False, True, (("session",), {"lazy": True})),
    ("main", False, "yes", None),
    ("main", False, False, None),
])
def test_a_rating_reads_the_queue_again_for_a_spaced_or_extended_queue(rating_app, lane, spaced, extended, call):
    app = rating_app
    app.store.practice_lane = lane
    metadata = {"practice_config": {"schedule_mode": "spaced"}} if spaced else {}
    answered(app, kana(**metadata), "ka")
    queue_before = kana_support.questions(app)
    reread = queue()
    app.store.get_session_questions.return_value = reread
    app.store.record_review.return_value = {"scheduled": True, **({} if extended is None else
                                                                  {"queue_extended": extended})}
    KanaMemory.save_rating(app, 3)
    if call is None:
        app.store.get_session_questions.assert_not_called()
        assert kana_support.questions(app) is queue_before
    else:
        assert app.store.get_session_questions.call_args_list == [call]
        assert kana_support.questions(app) is reread


def test_the_next_question_is_dispatched_after_the_position_moved(rating_app):
    app = rating_app
    answered(app, kana(practice_config={"schedule_mode": "spaced"}), "ka")
    reread = queue()
    app.store.get_session_questions.return_value = reread
    KanaMemory.save_rating(app, 3)
    assert len(app.seen) == 1
    assert app.seen[0]["question_index"] == 1 and app.seen[0]["session_right"] == 1
    assert app.seen[0]["questions"] is reread


@pytest.mark.parametrize("failure", [RuntimeError("disk full"), {"scheduled": False, "reason": "已过期"}])
def test_a_failed_rating_moves_nothing_and_reads_no_queue(rating_app, failure):
    app = rating_app
    answered(app, kana(practice_config={"schedule_mode": "spaced"}), "ka")
    if isinstance(failure, dict):
        app.store.record_review.return_value = failure
    else:
        app.store.record_review.side_effect = failure
    before = kana_support.position(app)
    KanaMemory.save_rating(app, 3)
    assert unchanged(app, before)
    app.store.get_session_questions.assert_not_called()
    app.dispatched.assert_not_called()


# --- a practice-only answer moved past -------------------------------------------------------------------------------

def practice_only_app(monkeypatch, *, settled="effect", failure=None, session="s"):
    practice = SimpleNamespace(settled=Mock(return_value=settled), go_on=Mock(side_effect=failure))
    store = Mock()
    app = set_position(SimpleNamespace(page="feedback", store=store, alert=Mock(),
        _study_arrangement_value=SimpleNamespace(serves=lambda value: value is store, practice=practice)),
        assisted_attempts={("s", 0), ("s", 2), ("t", 5)}, answered_attempts=set(), answer_revealed=False,
        session_id=session, questions=queue(), question_index=1, session_right=4, script="hiragana",
        question=kana())
    app._attempt_key = MethodType(KanaMemory._attempt_key, app)
    seen = []
    monkeypatch.setattr(KanaMemory, "_dispatch_after_answer", lambda value: seen.append(kana_support.position(value)))
    monkeypatch.setattr(domain_module, "utc_now", lambda: "now")
    return app, practice, seen


def test_moving_past_a_practice_only_answer_moves_on_one_place_and_counts_nothing(monkeypatch):
    app, practice, seen = practice_only_app(monkeypatch)
    queue_before = kana_support.questions(app)
    KanaMemory.save_rating(app, None)
    practice.settled.assert_called_once_with("s", 1)
    args = practice.go_on.call_args.args
    assert args[:3] == ("s", 1, 4) and sorted(args[3]) == [0, 2] and args[4] == "now"
    assert (kana_support.question_index(app), kana_support.session_right(app)) == (2, 4)
    assert kana_support.questions(app) is queue_before
    assert [(p["question_index"], p["session_right"]) for p in seen] == [(2, 4)]
    app.store.record_review.assert_not_called()


def test_a_practice_only_answer_that_could_not_be_saved_keeps_the_place(monkeypatch):
    app, practice, seen = practice_only_app(monkeypatch, failure=RuntimeError("disk full"))
    KanaMemory.save_rating(app, None)
    app.alert.assert_called_once_with("练习进度尚未保存：disk full")
    assert (kana_support.question_index(app), kana_support.session_right(app)) == (1, 4) and seen == []


@pytest.mark.parametrize("case", ["no-session", "not-settled"])
def test_an_answer_that_is_not_practice_only_is_rated_instead(monkeypatch, case):
    app, practice, seen = practice_only_app(monkeypatch, settled=None if case == "not-settled" else "effect",
                                            session=None if case == "no-session" else "s")
    assert KanaMemory._go_on_after_practice(app) is False
    practice.go_on.assert_not_called()
    assert kana_support.question_index(app) == 1 and seen == []
    if case == "no-session":
        practice.settled.assert_not_called()


# --- a restore ----------------------------------------------------------------------------------------------------

def restore_app(app, pending, *, active=None, drawn=None):
    """The store holds ``pending``; the drawing of the restored question is replaced by ``drawn`` (which may move
    the place, as a dispatch can) and the position at the drawing is recorded."""
    app.store.practice_lane = "word_romaji"
    app.store.reconcile_session_kinds.return_value = None
    app.store.load_session_progress.side_effect = [pending, active]
    stub_pages(app)
    seen = []

    def show_question(**kwargs):
        seen.append(kana_support.position(app))
        if drawn:
            drawn(app)
        else:
            app.page = "session_wait"
    app.show_question = Mock(side_effect=show_question)
    return seen


def test_a_restore_takes_the_session_the_queue_the_place_and_the_right_answers_from_the_saved_progress(preview_app):
    app = preview_app
    set_position(app, session_id="old", questions=[kana()], question_index=0, session_right=0, question="old",
                 script="hiragana")
    saved = queue()
    seen = restore_app(app, {"session_id": "s", "questions": saved, "question_index": 1, "session_right": 1,
                             "assisted_indices": [], "state": {"page": "question"}})
    KanaMemory.resume_practice(app)
    assert seen == [{"session_id": "s", "questions": saved, "question_index": 1, "session_right": 1,
                     "question": "old", "script": "katakana"}]
    assert kana_support.questions(app) is saved


def test_a_restore_of_a_finished_queue_draws_its_end_and_keeps_the_script(preview_app):
    app = preview_app
    set_position(app, questions=[kana()], question="old", script="hiragana")
    app.research_enabled = "kept"
    saved = queue()
    seen = restore_app(app, {"session_id": "s", "questions": saved, "question_index": 2, "session_right": 2,
                             "assisted_indices": [], "state": {}})
    app.back_stack.pages.append(PageState(("show_question", None), "question", "练习", object(), {}))
    KanaMemory.resume_practice(app)
    assert seen == [{"session_id": "s", "questions": saved, "question_index": 2, "session_right": 2,
                     "question": "old", "script": "hiragana"}]
    assert app.back_stack.pages == [] and app.research_enabled == "kept"


def test_a_restore_with_nothing_saved_goes_home_and_keeps_the_position(preview_app):
    app = preview_app
    set_position(app, questions=queue(), question_index=1, session_right=1, question="on the page", script="x")
    restore_app(app, None)
    before = kana_support.position(app)
    KanaMemory.resume_practice(app)
    app.show_home.assert_called_once_with()
    app.show_question.assert_not_called()
    assert unchanged(app, before)


@pytest.mark.parametrize("change,restored", [
    ("none", "ki"), ("session", ""), ("place", ""), ("question", "")])
def test_a_head_that_changed_during_the_restore_gets_only_its_own_saved_draft(preview_app, change, restored):
    app = preview_app
    kana_support.set_answer_revealed(app, False)
    set_position(app, question="old", script="hiragana")
    saved = [kana(), Question("na", "kana_romaji", "hiragana", "", "na", metadata={"kana_symbol": "な",
                                                                                 "romaji": "na", "content_version": "test"})]

    def drawn(value):
        # The dispatch moved past the saved question to the next one.
        kana_support.set_question_index(value, 1)
        kana_support.set_question(value, saved[1])
        value.page, value.romaji_input = "question", View()
    active_questions = list(saved)
    if change == "question":
        active_questions[1] = kana("na")
    active = {"session_id": "other" if change == "session" else "s", "questions": active_questions,
              "question_index": 0 if change == "place" else 1, "state": {"romaji_answer": "ki"}}
    restore_app(app, {"session_id": "s", "questions": saved, "question_index": 0, "session_right": 0,
                      "assisted_indices": [], "state": {"page": "question", "romaji_answer": "ka"}},
                active=active, drawn=drawn)
    KanaMemory.resume_practice(app)
    assert app.romaji_answer == restored and app.romaji_input.value == restored
    assert kana_support.question_index(app) == 1 and kana_support.question(app) is saved[1]


# --- queue changes ------------------------------------------------------------------------------------------------

def reconcile_page(revised):
    value = kana_support.with_state(SimpleNamespace(back_stack=BackStack(), platform=Mock(), alert=Mock(), store=Mock(),
        _practice_preferences=KanaMemory._clean_preferences({}), _ink_revision=0, _audio_serial=0),
        assisted_attempts=set(), answered_attempts=set(),
        session_id="s", questions=[kana()], question_index=0, session_right=0, question="old", script="kept")
    value.store.practice_lane = "word_romaji"
    value.store.reconcile_session_kinds.return_value = revised
    return value


@pytest.mark.parametrize("index,expected", [(1, 1), (2, None)])
def test_a_reconcile_that_changed_the_queue_replaces_five_values_and_keeps_the_script(index, expected):
    revised_queue = queue()
    value = reconcile_page({"changed": True, "session_id": "t", "questions": revised_queue, "question_index": index,
                            "session_right": 1, "assisted_indices": []})
    KanaMemory._reconcile_practice_kinds(value, {"session_id": "s", "questions": revised_queue})
    assert kana_support.position(value) == {
        "session_id": "t", "questions": revised_queue, "question_index": index, "session_right": 1,
        "question": None if expected is None else revised_queue[expected], "script": "kept"}
    assert kana_support.questions(value) is revised_queue


def test_a_reconcile_that_changed_nothing_keeps_the_position():
    value = reconcile_page({"changed": False, "session_id": "t", "questions": queue(), "question_index": 1,
                            "session_right": 1})
    before = kana_support.position(value)
    KanaMemory._reconcile_practice_kinds(value, {"session_id": "s", "questions": [kana()]})
    assert unchanged(value, before)


def in_a_round(store, lane):
    """The store takes part in the app's practice rounds, with no round begun yet."""
    store._practice_run_enabled, store._practice_run_plans, store.practice_lane = True, {}, lane


@pytest.mark.parametrize("picked,script", [("平假名", "hiragana"), ("片假名", "katakana")])
@pytest.mark.parametrize("round_on", [False, True])
def test_a_new_practice_from_the_setup_page_starts_at_the_first_place_of_its_new_queue(setup_app, monkeypatch, picked,
                                                                                      script, round_on):
    app = setup_app
    set_mode(app, CHOICE, script=picked)
    if round_on:
        in_a_round(app.store, "main")
    built = [kana(script=script), kana("na", script)]
    for name in ("build_questions", "build_practice_batch"):
        monkeypatch.setattr(app_module, name, Mock(return_value=built))
    set_position(app, session_id="old", questions=[kana()], question_index=3, session_right=2, question="on the page")
    asyncio.run(app.start_practice())
    app.show_question.assert_called_once_with()
    assert kana_support.position(app) == {"session_id": "new-session", "questions": built, "question_index": 0,
                                          "session_right": 0, "question": "on the page", "script": script}
    assert kana_support.questions(app) is built
    assert practice_round.active(app.store) is round_on


def word_drill_app(monkeypatch, pending=None):
    app = kana_support.with_state(SimpleNamespace(page="word_drill_setup", _page_token=1, store=Mock(), repo=Mock(),
        alert=Mock(), back_stack=BackStack(), show_question=Mock(), resume_practice=Mock(),
        refresh_word_drill_setup=Mock(), word_drill_start_button=SimpleNamespace(enabled=True, text="")),
        assisted_attempts=set(), answered_attempts=set(),
        session_id="old", questions=[kana()], question_index=3, session_right=2, question="on the page",
        script="hiragana")
    app.store._practice_run_enabled = False
    app.store.load_session_progress.return_value = pending
    app.store.start_session.return_value = "drill"
    monkeypatch.setattr(KanaMemory, "_word_drill_config", lambda self: {"word_table": "partial"})
    monkeypatch.setattr(KanaMemory, "_activate_practice_lane", lambda self, lane: True)
    monkeypatch.setattr(KanaMemory, "_reconcile_practice_kinds", lambda self, *a: None)
    built = [Question("w", "word_romaji", "katakana", "", "カナ", metadata={"content_version": "test"}),
             Question("v", "word_romaji", "hiragana", "", "かな", metadata={"content_version": "test"})]
    monkeypatch.setattr(app_module, "build_word_romaji_drill", lambda *a, **k: built)
    return app, built


@pytest.mark.parametrize("round_on", [False, True])
def test_a_new_word_drill_starts_at_the_first_place_with_the_script_of_its_first_word(monkeypatch, round_on):
    app, built = word_drill_app(monkeypatch)
    if round_on:
        in_a_round(app.store, "word_romaji")
    asyncio.run(KanaMemory.start_word_drill(app))
    app.alert.assert_not_called()
    app.show_question.assert_called_once_with()
    assert kana_support.position(app) == {"session_id": "drill", "questions": built, "question_index": 0,
                                          "session_right": 0, "question": "on the page", "script": "katakana"}
    assert kana_support.questions(app) is built and app.use_full_keyboard is False
    assert practice_round.active(app.store) is round_on


def test_a_word_drill_appended_to_a_saved_queue_is_restored_and_sets_no_position_itself(monkeypatch):
    app, built = word_drill_app(monkeypatch, pending={"session_id": "saved"})
    before = kana_support.position(app)
    asyncio.run(KanaMemory.start_word_drill(app))
    app.store.append_session.assert_called_once()
    assert app.store.append_session.call_args.args[:2] == ("saved", built)
    app.resume_practice.assert_called_once_with(preserve_audio=True)
    app.show_question.assert_not_called()
    assert unchanged(app, before)


def test_an_early_batch_taken_into_memory_starts_at_its_first_place_and_keeps_the_question_and_script():
    app = set_position(SimpleNamespace(store=Mock()), session_id="old", questions=[kana()], question_index=3,
                       session_right=2, question="on the page", script="katakana")
    held = queue()
    app.store.get_session_questions.return_value = held
    extra_practice_runtime._hold_batch(app, "batch")
    app.store.get_session_questions.assert_called_once_with("batch")
    assert kana_support.position(app) == {"session_id": "batch", "questions": held, "question_index": 0,
                                          "session_right": 0, "question": "on the page", "script": "katakana"}


def test_an_ended_early_batch_held_in_memory_is_forgotten_but_its_question_and_script_stay():
    app = set_position(SimpleNamespace(back_stack=BackStack()), session_id="batch", questions=queue(),
                       question_index=1, session_right=1, question="on the page", script="katakana")
    app.back_stack.pages.append(PageState(("show_question", None), "question", "练习", object(), {}))
    app.back_stack.pages.append(PageState(("show_home", None), "home", "今日", object(), {}))
    extra_practice_runtime._forget_batch(app, "batch")
    assert kana_support.position(app) == {"session_id": None, "questions": [], "question_index": 0,
                                          "session_right": 0, "question": "on the page", "script": "katakana"}
    assert [page.name for page in app.back_stack.pages] == ["home"]


@pytest.mark.parametrize("ended", [None, "other"])
def test_an_ended_early_batch_that_is_not_the_one_in_memory_changes_nothing(ended):
    app = set_position(SimpleNamespace(back_stack=BackStack()), session_id="batch", questions=queue(),
                       question_index=1, session_right=1, question="on the page", script="katakana")
    app.back_stack.pages.append(PageState(("show_question", None), "question", "练习", object(), {}))
    before = kana_support.position(app)
    extra_practice_runtime._forget_batch(app, ended)
    assert unchanged(app, before) and len(app.back_stack.pages) == 1


def test_an_ended_early_batch_on_an_app_without_a_session_changes_nothing():
    app = SimpleNamespace()
    extra_practice_runtime._forget_batch(app, "batch")
    assert vars(app) == {}


def test_deleting_the_learning_data_empties_the_position_but_keeps_the_script():
    old_queue = queue()
    app = set_position(SimpleNamespace(page="settings", _page_token=1, store=Mock(), alert=Mock(), platform=Mock(),
        main_window=SimpleNamespace(dialog=AsyncMock(return_value=True)), back_stack=BackStack(),
        show_settings=Mock(), _ink_revision=0, _audio_serial=0),
        assisted_attempts=set(), answered_attempts=set(),
        session_id="s", questions=old_queue, question_index=1, session_right=1, question=old_queue[1],
        script="katakana")
    asyncio.run(KanaMemory.delete_learning_data(app))
    app.store.clear_learning_data.assert_called_once_with()
    assert kana_support.position(app) == {"session_id": None, "questions": [], "question_index": 0,
                                          "session_right": 0, "question": None, "script": "katakana"}
    assert kana_support.questions(app) is not old_queue and old_queue == queue()


def test_a_restored_learning_backup_empties_the_position_but_keeps_the_script(monkeypatch):
    from kanamemory import backup_ui

    async def stage(app, action, title, data=None):
        return SimpleNamespace(value={"action": action}, committed=action == "restore")
    monkeypatch.setattr(backup_ui, "_stage", stage)
    monkeypatch.setattr(backup_ui, "_ready", lambda app: None)
    monkeypatch.setattr(backup_ui, "_restore_summary", lambda summary: "")
    monkeypatch.setattr(backup_ui.progress_backup, "is_legacy_backup", lambda data: False)
    old_queue = queue()
    app = set_position(SimpleNamespace(store=Mock(), back_stack=BackStack(), show_home=Mock(), alert=Mock(),
        platform=SimpleNamespace(import_backup=AsyncMock(return_value=b"{}")),
        main_window=SimpleNamespace(dialog=AsyncMock(return_value=True)),
        _clean_preferences=KanaMemory._clean_preferences),
        assisted_attempts=set(), answered_attempts=set(),
        session_id="s", questions=old_queue, question_index=1, session_right=1, question=old_queue[1],
        script="katakana")
    asyncio.run(backup_ui.restore(app))
    app.show_home.assert_called_once_with()
    assert kana_support.position(app) == {"session_id": None, "questions": [], "question_index": 0,
                                          "session_right": 0, "question": None, "script": "katakana"}
    assert kana_support.questions(app) is not old_queue


# --- the checkpoint -----------------------------------------------------------------------------------------------

def checkpoint_page(page="question", **fields):
    q = kana()
    return set_position(SimpleNamespace(page=page, store=Mock(), use_full_keyboard=False),
                        assisted_attempts=set(), answered_attempts=set(), answer_revealed=False,
                        **{"session_id": "s", "questions": [q, kana("na")], "question_index": 1, "session_right": 1,
                           "question": q, **fields})


def test_a_question_page_saves_the_session_its_place_and_its_right_answers():
    value = checkpoint_page()
    KanaMemory._checkpoint(value)
    assert value.store.save_session_progress.call_args.args[:3] == ("s", 1, 1)
    value.store.clear_session_progress.assert_not_called()


@pytest.mark.parametrize("index", [2, 3])
def test_a_finished_queue_clears_the_saved_progress_instead(index):
    value = checkpoint_page("feedback", question_index=index)
    KanaMemory._checkpoint(value)
    value.store.clear_session_progress.assert_called_once_with()
    value.store.save_session_progress.assert_not_called()


def test_nothing_is_saved_without_a_session():
    value = checkpoint_page(session_id=None)
    assert KanaMemory._checkpoint(value) is None
    value.store.save_session_progress.assert_not_called()
    value.store.clear_session_progress.assert_not_called()


# --- Back ---------------------------------------------------------------------------------------------------------

def back_page():
    question = SimpleNamespace(content_id="ka", kind="kana_choice")
    value = kana_support.with_state(SimpleNamespace(
        page="kana_detail", _view_key=("show_kana_detail", "ka"),
        back_stack=BackStack(), question_notice=SimpleNamespace(text=""), platform=Mock(),
        store=Mock(is_content_available=lambda content_id: True), alert=Mock(),
        _page_token=5, _ink_revision=2, _audio_serial=0, _paint_handle=None, content_slot=Mock(),
        title_label=SimpleNamespace(text=""), _checkpoint=Mock(), _sync_back_handler=Mock(), loop=Mock()),
        assisted_attempts=set(), answered_attempts=set(), answer_revealed=False,
        session_id="now", questions=[question], question_index=0, session_right=0, question=question,
        script="hiragana")
    value._attempt_key = MethodType(KanaMemory._attempt_key, value)
    return value


def kept(name="feedback", **position):
    return PageState(("show_question", None), name, "练习", object(),
                     {**kana_support.kept_position(**position), "question_notice": SimpleNamespace(text="")})


def test_back_to_a_kept_practice_page_gives_back_the_whole_position_of_its_moment():
    value = back_page()
    kept_queue = [SimpleNamespace(content_id="na", kind="kana_choice"), SimpleNamespace(content_id="ka",
                                                                                         kind="kana_choice")]
    moment = {"session_id": "then", "questions": kept_queue, "question_index": 1, "session_right": 1,
              "question": kept_queue[1], "script": "katakana"}
    value.back_stack.pages.append(kept(**moment))
    KanaMemory.go_back(value)
    assert value.page == "feedback"
    assert kana_support.position(value) == moment and kana_support.questions(value) is kept_queue


@pytest.mark.parametrize("name", ["question", "feedback"])
def test_back_gives_back_only_the_values_the_kept_page_holds(name):
    value = back_page()
    before = kana_support.position(value)
    value.back_stack.pages.append(kept(name, session_id="then", question_index=4))
    KanaMemory.go_back(value)
    assert value.page == name
    assert kana_support.position(value) == {**before, "session_id": "then", "question_index": 4}
    assert kana_support.questions(value) is before["questions"]


def test_back_to_a_kept_practice_page_after_the_queue_changed_restores_the_practice_instead(monkeypatch):
    value = back_page()
    value.resume_practice, value.show_question = Mock(), Mock()
    monkeypatch.setattr(KanaMemory, "_reconcile_practice_kinds", lambda self, pending=None: {"changed": True})
    before = kana_support.position(value)
    value.back_stack.pages.append(kept(session_id="then", question_index=4))
    KanaMemory.go_back(value)
    value.resume_practice.assert_called_once_with()
    assert unchanged(value, before) and value.page == "kana_detail" and len(value.back_stack.pages) == 1


def test_a_page_kept_for_back_shares_the_queue_list_with_the_practice(preview_app):
    app = preview_app
    kana_support.set_questions(app, queue())
    app.show_question()
    shown = kana_support.position(app)
    app.show_kana_detail("ka")
    assert app.page == "kana_detail"
    held = kana_support.kept_values(app.back_stack.pages[-1])
    assert held == shown and held["questions"] is kana_support.questions(app)
    app.go_back()
    assert app.page == "question"
    assert kana_support.position(app) == shown and kana_support.questions(app) is shown["questions"]


# --- by name --------------------------------------------------------------------------------------------------------

def test_an_attempt_is_named_by_its_session_and_place():
    app = set_position(SimpleNamespace(), session_id="s", question_index=3)
    assert KanaMemory._attempt_key(app) == ("s", 3)


def test_the_draft_timer_is_keyed_by_session_place_page_and_question():
    q = kana()
    app = set_position(SimpleNamespace(page="question"), session_id="s", question_index=2, question=q)
    assert KanaMemory._kana_draft_key(app) == ("s", 2, "question", q)
    assert KanaMemory._kana_draft_key(SimpleNamespace()) == (None, None, None, None)


class Draft:
    """A keystroke draft waiting for its timer."""

    def __init__(self):
        self.pending, self.requeued = None, []

    def accept(self, key, value=None):
        self.pending = (key, value)

    def flush_now(self):
        pending, self.pending = self.pending, None
        return pending

    def requeue(self, key, value=None):
        self.requeued.append((key, value))


@pytest.mark.parametrize("change", ["nothing", "place", "session", "page", "equal-question"])
def test_a_draft_timer_saves_only_while_the_session_place_page_and_question_are_the_same(change):
    q = kana()
    app = set_position(SimpleNamespace(page="question", _kana_draft_state=Draft(), _checkpoint=Mock(return_value=True)),
                       session_id="s", question_index=2, question=q)
    KanaMemory._keystroke_checkpoint(app)
    if change == "place":
        kana_support.set_question_index(app, 3)
    elif change == "session":
        kana_support.set_session_id(app, "t")
    elif change == "page":
        app.page = "word_question"
    elif change == "equal-question":
        kana_support.set_question(app, kana())
    result = KanaMemory.flush_kana_draft(app)
    if change == "nothing":
        assert result is True
        app._checkpoint.assert_called_once_with()
    else:
        assert result is None
        app._checkpoint.assert_not_called()


def test_a_draft_that_could_not_be_saved_waits_for_the_next_flush():
    app = set_position(SimpleNamespace(page="question", _kana_draft_state=Draft(), _checkpoint=Mock(return_value=False)),
                       session_id="s", question_index=2, question=kana())
    KanaMemory._keystroke_checkpoint(app)
    assert KanaMemory.flush_kana_draft(app) is False
    assert app._kana_draft_state.requeued == [(KanaMemory._kana_draft_key(app), None)]


def test_a_kept_page_that_holds_no_session_or_place_is_not_a_question_of_this_attempt():
    value = set_position(SimpleNamespace(page="kana_detail", back_stack=BackStack(), _checkpoint=Mock(),
        question_notice=SimpleNamespace(text="")), assisted_attempts=set(), answered_attempts=set(),
        answer_revealed=False, session_id="s", question_index=0, question=SimpleNamespace(kind="kana_choice"))
    value._attempt_key = MethodType(KanaMemory._attempt_key, value)
    value.back_stack.pages.append(PageState(("show_question", None), "question", "练习", object(),
                                            kana_support.kept_position(questions=[], session_right=0)))
    KanaMemory._mark_lookup_assisted(value)
    assert kana_support.assisted_attempts(value) == set()
    value._checkpoint.assert_not_called()


def report_of(app, page):
    return feedback_context._capture_practice(feedback_context._Capture(), app, page)


def report_app(**fields):
    return set_position(SimpleNamespace(use_full_keyboard=False, store=SimpleNamespace(practice_lane="main")),
                        **fields)


def test_a_problem_report_carries_the_position_and_the_question_on_the_page():
    app = report_app(session_id="s", questions=queue(), question_index=1, session_right=1, question=kana("na"),
                     script="katakana")
    result = report_of(app, "question")
    assert {name: result[name] for name in ("session_id", "question_index", "session_right", "script")} == {
        "session_id": "s", "question_index": 1, "session_right": 1, "script": "katakana"}
    assert result["queue_length"] == 2 and result["question"]["content_id"] == "na"


def test_a_problem_report_off_the_question_pages_carries_the_position_but_no_question():
    app = report_app(session_id="s", questions=queue(), question_index=2, session_right=1, question=kana(),
                     script="hiragana")
    result = report_of(app, "session_done")
    assert (result["session_id"], result["question_index"], result["queue_length"]) == ("s", 2, 2)
    assert "question" not in result


def test_a_problem_report_of_an_app_without_a_position_reports_none_and_an_empty_queue():
    result = report_of(report_app(), "question")
    assert {name: result[name] for name in ("session_id", "question_index", "session_right", "script")} == {
        "session_id": None, "question_index": None, "session_right": None, "script": None}
    assert result["queue_length"] == 0 and "question" not in result


def test_a_problem_report_counts_no_queue_that_is_not_a_sequence():
    result = report_of(report_app(questions=iter(queue()), question=kana()), "question")
    assert "queue_length" not in result and "question" in result


def romaji_tool_app(affected, **fields):
    u = SimpleNamespace(toga=SimpleNamespace(TextInput=lambda **kw: View(**kw)), Pack=lambda **kw: None,
        text_style=lambda **kw: None, MUTED="muted", selection=lambda items, value, style: View(items=items, value=value),
        label=lambda text, **kw: View(text=text), button=lambda text, handler, **kw: View(text=text, on_press=handler),
        row=lambda *children, **kw: View(*children))
    app = set_position(SimpleNamespace(page="romaji_tool", store=Mock(), screen=Mock(), word_used_conversion=False),
                       **fields)
    app.store.mark_pending_word_conversion.return_value = affected
    romaji_input_ui.show_romaji_tool(app, u)
    return app


@pytest.mark.parametrize("affected,fields,used", [
    ([("s", 2)], {"session_id": "s", "question_index": 2}, True),
    ([("s", 1)], {"session_id": "s", "question_index": 2}, False),
    ([("t", 2)], {"session_id": "s", "question_index": 2}, False),
    ([(None, None)], {}, True),
])
def test_the_romaji_tool_marks_the_word_on_the_page_only_when_its_attempt_was_affected(affected, fields, used):
    app = romaji_tool_app(affected, **fields)
    composer = app._romaji_tool_context
    composer.source_input.value = "ka"
    composer.change()
    app.store.mark_pending_word_conversion.assert_called_once_with()
    assert app.word_used_conversion is used


@pytest.mark.parametrize("index,revealed", [(1, True), (0, False)])
def test_an_exposure_reveals_the_attempt_on_the_page_by_its_session_and_place(monkeypatch, index, revealed):
    monkeypatch.setattr(feedback_exposure, "mark_legacy_heads", lambda store, origins: [("s", 1)])
    app = set_position(SimpleNamespace(store=Mock(), feedback_analysis=lambda: SimpleNamespace(resources={}),
        diagnostics=Mock(), _mark_visible_tutor_help=Mock()), assisted_attempts=set(), answer_revealed=False,
        session_id="s", question_index=index)
    feedback_exposure.expose(app, {"context": {}}, [])
    assert kana_support.answer_revealed(app) is revealed


@pytest.mark.parametrize("question,expected", [(kana("na"), "na"), (None, None), ("absent", None)])
def test_the_route_sees_the_content_of_the_question_on_the_page(question, expected):
    app = SimpleNamespace() if question == "absent" else set_position(SimpleNamespace(), question=question)
    assert study_path_runtime._landed_content_id(app, "question") == expected
