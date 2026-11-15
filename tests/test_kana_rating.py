"""The rating of a kana practice answer (design 13): the arguments of the review record, the service that
saves, and the app side that goes on.

The model is used directly, the service over a recording store with doubles for what the app side knows, and
the app-side module on a bare object with a class of doubles. No store, no control and no app is built here.
"""
from __future__ import annotations

import ast
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kanamemory import diagnostic_events, session_content
from kanamemory import domain as domain_module
from kanamemory import kana_attempt_runtime, kana_session_runtime
from kanamemory import kana_rating_runtime as line
from kanamemory.domain import Question
from kanamemory.kana_line.attempt import KanaAttempt
from kanamemory.kana_line.rating import KanaRating
from kanamemory.kana_line.rating_model import (BUSY, NOT_SAVED, ONLY_AGAIN, RETRY, UNSCHEDULED, Rated, Refused,
                                               advanced, is_word, reread_queue, review_call)
from kanamemory.kana_line.session import KanaSession
from kanamemory.practice_preferences import WORD_PRACTICE_KINDS

SOURCE = Path(line.__file__).parent
SUBMITTED = datetime(2030, 5, 6, 7, 8, 9, tzinfo=timezone.utc)
SAVED = datetime(2030, 5, 6, 7, 9, 0, tzinfo=timezone.utc)
LOCKED = sqlite3.OperationalError("database is locked")
RECORD_ORDER = ["session_id", "question_index", "answered_text", "outcome", "audio_ok", "content_version",
                "assisted", "duration_ms", "now", "finalized_at", "fair_new", "question_fingerprint"]


def question(kind="kana_choice", **metadata):
    return SimpleNamespace(content_id="ka", kind=kind, script="hiragana",
                           metadata={"content_version": "v1", **metadata})


def position(**fields):
    return KanaSession(**{"session_id": "s", "question_index": 2, "session_right": 1, **fields})


def evidence(**fields):
    return KanaAttempt(**{"revealed": False, "answered_text": "ka", "correct": True, "audio_ok": True,
                          "submitted_at": SUBMITTED.isoformat(), "duration_ms": 900, **fields})


def without(state, *names):
    """``state`` with the named fields never set."""
    held = {name: value for name, value in state.set_fields().items() if name not in names}
    return type(state)(**held)


def call_for(asked=None, session=None, attempt=None, rating=3, **parts):
    parts = {"finalized_at": SAVED, "fair_new": lambda: True, "fingerprint": lambda value: ("print", value), **parts}
    return review_call(asked or question(), session or position(), attempt or evidence(), rating, **parts)


# --- a field that must have been set --------------------------------------------------------------------------------

def test_a_needed_field_is_given_or_named_in_an_attribute_error():
    session = KanaSession(session_id=None, question_index=3)
    assert session.need("session_id") is None and session.need("question_index") == 3
    with pytest.raises(AttributeError, match="session_right"):
        session.need("session_right")
    with pytest.raises(AttributeError):
        session.need("no_such_field")


# --- the arguments of the review record -----------------------------------------------------------------------------

def test_the_record_of_a_kana_answer():
    asked = question()
    call = call_for(asked)
    assert call.args == ("ka", "kana_choice", "hiragana", 3)
    assert list(call.kwargs) == RECORD_ORDER
    assert call.kwargs == {
        "session_id": "s", "question_index": 2, "answered_text": "ka", "outcome": "correct", "audio_ok": True,
        "content_version": "v1", "assisted": False, "duration_ms": 900, "now": SUBMITTED, "finalized_at": SAVED,
        "fair_new": True, "question_fingerprint": ("print", asked)}


def test_the_record_says_what_the_attempt_holds():
    call = call_for(attempt=evidence(revealed=True, correct=False, audio_ok=False, answered_text=""), rating=1)
    assert call.args[3] == 1
    assert (call.kwargs["assisted"], call.kwargs["outcome"], call.kwargs["audio_ok"], call.kwargs["answered_text"]) == (
        True, "incorrect", False, "")


@pytest.mark.parametrize("kind", sorted(WORD_PRACTICE_KINDS))
def test_a_word_answer_is_recorded_at_the_time_of_saving(kind):
    assert is_word(question(kind))
    assert call_for(question(kind), rating=None).kwargs["now"] is SAVED
    assert call_for(question(kind), attempt=without(evidence(), "submitted_at"), rating=None).kwargs["now"] is SAVED


@pytest.mark.parametrize("kind", ["kana_choice", "romaji_kana", "kana_romaji", "kana_handwriting"])
def test_a_kana_answer_is_recorded_at_the_time_of_its_first_submit(kind):
    assert not is_word(question(kind))
    assert call_for(question(kind)).kwargs["now"] == SUBMITTED


@pytest.mark.parametrize("attempt", [evidence(submitted_at=None), evidence(submitted_at=""),
                                     without(evidence(), "submitted_at")])
def test_a_kana_answer_whose_time_was_never_frozen_has_none(attempt):
    assert call_for(attempt=attempt).kwargs["now"] is None


def test_a_duration_that_was_never_set_is_zero_and_one_set_to_none_stays_none():
    assert call_for(attempt=without(evidence(), "duration_ms")).kwargs["duration_ms"] == 0
    assert call_for(attempt=evidence(duration_ms=None)).kwargs["duration_ms"] is None


@pytest.mark.parametrize("state,name", [("session", "session_id"), ("session", "question_index"),
                                        ("attempt", "answered_text"), ("attempt", "correct"),
                                        ("attempt", "audio_ok"), ("attempt", "revealed")])
def test_a_value_of_the_record_that_was_never_set_raises_with_its_name(state, name):
    fair_new, fingerprint = Mock(), Mock()
    states = {"session": position(), "attempt": evidence()}
    states[state] = without(states[state], name)
    with pytest.raises(AttributeError, match=name):
        call_for(**states, fair_new=fair_new, fingerprint=fingerprint)
    fair_new.assert_not_called()
    fingerprint.assert_not_called()


def test_a_question_without_a_content_version_raises_before_anything_is_asked():
    asked = question()
    del asked.metadata["content_version"]
    fair_new, fingerprint = Mock(), Mock()
    with pytest.raises(KeyError, match="content_version"):
        call_for(asked, fair_new=fair_new, fingerprint=fingerprint)
    fair_new.assert_not_called()


def test_the_fair_order_is_asked_once_and_before_the_fingerprint():
    order = Mock()
    order.fair_new.return_value, order.fingerprint.return_value = "fair", "print"
    call = call_for(fair_new=order.fair_new, fingerprint=order.fingerprint)
    assert [entry[0] for entry in order.mock_calls] == ["fair_new", "fingerprint"]
    assert (call.kwargs["fair_new"], call.kwargs["question_fingerprint"]) == ("fair", "print")


# --- the position after a saved rating ------------------------------------------------------------------------------

def test_a_right_answer_moves_on_and_counts():
    queue, asked = [object()], question()
    before = position(questions=queue, question=asked, script="katakana")
    after = advanced(before, evidence(), asked)
    assert after is not before
    assert after.set_fields() == {"session_id": "s", "questions": queue, "question_index": 3, "session_right": 2,
                                  "question": asked, "script": "katakana"}
    assert after.questions is queue
    assert (before.question_index, before.session_right) == (2, 1)


@pytest.mark.parametrize("attempt,metadata", [
    (evidence(correct=False), {}), (evidence(), {"spelling_correction_of": "word"})])
def test_a_wrong_answer_and_a_corrected_spelling_move_on_without_counting(attempt, metadata):
    after = advanced(position(), attempt, question(**metadata))
    assert (after.question_index, after.session_right) == (3, 1)


def test_the_count_is_read_only_when_it_grows():
    never_counted = without(position(), "session_right")
    after = advanced(never_counted, evidence(correct=False), question())
    assert after.question_index == 3 and after.get("session_right", "never") == "never"
    with pytest.raises(AttributeError, match="session_right"):
        advanced(never_counted, evidence(), question())


def test_a_position_without_a_place_cannot_move_on():
    with pytest.raises(AttributeError, match="question_index"):
        advanced(without(position(), "question_index"), evidence(), question())


@pytest.mark.parametrize("metadata,result,again", [
    ({"practice_config": {"schedule_mode": "spaced"}}, {}, True),
    ({"practice_config": {"schedule_mode": "rounds"}}, {}, False),
    ({}, {"queue_extended": True}, True),
    ({}, {"queue_extended": "yes"}, False),
    ({}, {"queue_extended": 1}, False),
    ({}, {"queue_extended": False}, False),
    ({}, {}, False),
])
def test_the_queue_is_read_again_for_a_spaced_queue_or_an_extended_one(metadata, result, again):
    assert reread_queue(question(**metadata), result) is again


# --- the service ----------------------------------------------------------------------------------------------------

def rating_service(answer=None, **parts):
    """A KanaRating over a recording store; returns (service, store, doubles, order of the calls)."""
    store = Mock()
    store.record_review.return_value = {"scheduled": True} if answer is None else answer
    doubles = dict(now=Mock(return_value=SAVED), fair_new=Mock(return_value=False),
                   fingerprint=Mock(return_value="print"), word_plan=Mock(return_value=(["row"], {}, [])),
                   set_kind_policy=Mock(), word_submission=Mock(return_value={}), record_error=Mock())
    doubles.update(parts)
    order = Mock()
    for name, double in doubles.items():
        if isinstance(double, Mock):
            order.attach_mock(double, name)
    order.attach_mock(store.record_review, "record")
    return KanaRating(store, **doubles), store, SimpleNamespace(**doubles), order


def happened(order):
    return [entry[0] for entry in order.mock_calls]


def test_a_kana_rating_is_saved_with_the_arguments_of_the_model():
    answer = {"scheduled": True, "queue_extended": True}
    service, store, doubles, order = rating_service(answer)
    asked = question()
    rated = service.rate(asked, position(), evidence(), 3)
    assert isinstance(rated, Rated) and rated.result is answer
    expected = call_for(asked, fair_new=lambda: False, fingerprint=lambda value: "print")
    store.record_review.assert_called_once_with(*expected.args, **expected.kwargs)
    assert happened(order) == ["now", "fair_new", "fingerprint", "record"]
    doubles.fingerprint.assert_called_once_with(asked)


def test_an_attempt_that_saw_help_takes_only_again():
    service, store, _, order = rating_service()
    refused = service.rate(question(), position(), evidence(revealed=True, correct=False), 3)
    assert refused == Refused(ONLY_AGAIN) and happened(order) == []
    rated = service.rate(question(), position(), evidence(revealed=True, correct=False), 1)
    assert isinstance(rated, Rated)
    assert store.record_review.call_args.args[3] == 1 and store.record_review.call_args.kwargs["assisted"] is True


def test_an_attempt_whose_help_was_never_set_cannot_be_rated():
    service, store, _, order = rating_service()
    with pytest.raises(AttributeError, match="revealed"):
        service.rate(question(), position(), without(evidence(), "revealed"), 3)
    assert happened(order) == []


@pytest.mark.parametrize("kind", sorted(WORD_PRACTICE_KINDS))
@pytest.mark.parametrize("revealed", [False, True])
def test_a_word_answer_is_saved_without_a_rating_and_with_its_rows_even_after_help(kind, revealed):
    service, store, doubles, order = rating_service()
    rated = service.rate(question(kind), position(), evidence(revealed=revealed, answered_text="かな"), 3)
    assert isinstance(rated, Rated)
    assert happened(order) == ["now", "word_plan", "word_submission", "fair_new", "fingerprint", "record"]
    doubles.word_plan.assert_called_once_with("かな")
    doubles.word_submission.assert_called_once_with("かな")
    record = store.record_review.call_args
    assert record.args == ("ka", kind, "hiragana", None)
    assert list(record.kwargs) == RECORD_ORDER + ["kana_rows", "spelling_corrections"]
    assert record.kwargs["kana_rows"] == ["row"] and record.kwargs["spelling_corrections"] == []
    assert record.kwargs["now"] is SAVED and record.kwargs["assisted"] is revealed


def test_corrections_set_the_kinds_of_the_first_one_before_the_record():
    first = question(practice_config={"kinds": ["kana_choice"]})
    second = question(practice_config={"kinds": ["romaji_kana"]})
    corrections = [first, second]
    service, store, doubles, order = rating_service(word_plan=Mock(return_value=(["row"], {}, corrections)))
    service.rate(question("word_kana"), position(session_id="queue"), evidence(), None)
    assert happened(order) == ["now", "word_plan", "set_kind_policy", "word_submission", "fair_new", "fingerprint",
                               "record"]
    doubles.set_kind_policy.assert_called_once_with("queue", ["kana_choice"])
    assert store.record_review.call_args.kwargs["spelling_corrections"] is corrections


def test_the_romaji_parts_the_learner_chose_go_into_the_record():
    chosen = {"word_romaji_parts": ["ka", "na"], "word_romaji_plan": "plan"}
    service, store, _, _ = rating_service(word_submission=Mock(return_value=chosen))
    service.rate(question("word_romaji"), position(), evidence(), None)
    kwargs = store.record_review.call_args.kwargs
    assert list(kwargs) == RECORD_ORDER + ["kana_rows", "spelling_corrections", "word_romaji_parts", "word_romaji_plan"]
    assert {name: kwargs[name] for name in chosen} == chosen


def test_a_part_that_names_a_value_of_the_record_again_fails_the_save():
    service, store, doubles, _ = rating_service(word_submission=Mock(return_value={"outcome": "correct"}))
    refused = service.rate(question("word_romaji"), position(), evidence(correct=False), None)
    assert isinstance(refused, Refused) and refused.message.startswith(NOT_SAVED) and "outcome" in refused.message
    store.record_review.assert_not_called()
    doubles.record_error.assert_not_called()


def test_a_store_that_cannot_record_is_refused_before_the_fair_order_is_asked():
    service, store, doubles, order = rating_service()
    del store.record_review
    refused = service.rate(question(), position(), evidence(), 3)
    assert isinstance(refused, Refused) and refused.message.startswith(NOT_SAVED)
    assert happened(order) == ["now"]
    doubles.record_error.assert_not_called()


@pytest.mark.parametrize("answer,message", [
    ({"scheduled": False}, UNSCHEDULED + RETRY),
    ({"scheduled": False, "reason": "已过期"}, UNSCHEDULED + "已过期"),
    ({"scheduled": 0, "reason": "x"}, UNSCHEDULED + "x"),
    ({}, NOT_SAVED + "'scheduled'"),
])
def test_an_answer_of_the_store_that_schedules_nothing_is_refused(answer, message):
    service, _, doubles, _ = rating_service(answer)
    assert service.rate(question(), position(), evidence(), 3) == Refused(message)
    doubles.record_error.assert_not_called()


def test_a_busy_store_is_noted_and_refused_so_that_the_rating_can_be_pressed_again():
    service, store, doubles, _ = rating_service()
    store.record_review.side_effect = [LOCKED, {"scheduled": True}]
    assert service.rate(question(), position(), evidence(), 3) == Refused(BUSY)
    doubles.record_error.assert_called_once_with("practice_rating", "commit", LOCKED)
    assert isinstance(service.rate(question(), position(), evidence(), 3), Rated)
    assert service.record_error is doubles.record_error


def test_another_failure_of_the_store_is_refused_with_its_text_and_not_noted():
    service, store, doubles, _ = rating_service()
    store.record_review.side_effect = RuntimeError("disk full")
    assert service.rate(question(), position(), evidence(), 3) == Refused(NOT_SAVED + "disk full")
    doubles.record_error.assert_not_called()


@pytest.mark.parametrize("part", ["now", "word_plan", "set_kind_policy", "word_submission", "fair_new",
                                  "fingerprint"])
@pytest.mark.parametrize("failure,message,noted", [
    (RuntimeError("it failed"), NOT_SAVED + "it failed", False), (LOCKED, BUSY, True)])
def test_a_failure_of_what_the_app_side_gives_is_refused_like_one_of_the_store(part, failure, message, noted):
    plan = Mock(return_value=(["row"], {}, [question(practice_config={"kinds": ["kana_choice"]})]))
    service, store, doubles, _ = rating_service(word_plan=plan)
    getattr(doubles, part).side_effect = failure
    assert service.rate(question("word_kana"), position(), evidence(), None) == Refused(message)
    store.record_review.assert_not_called()
    assert doubles.record_error.called is noted


@pytest.mark.parametrize("state,name", [("attempt", "answered_text"), ("attempt", "correct"),
                                        ("session", "question_index")])
def test_a_value_that_was_never_set_is_refused_with_its_name(state, name):
    service, store, _, _ = rating_service()
    states = {"session": position(), "attempt": evidence()}
    states[state] = without(states[state], name)
    assert service.rate(question(), states["session"], states["attempt"], 3) == Refused(NOT_SAVED + name)
    store.record_review.assert_not_called()


# --- the app side ---------------------------------------------------------------------------------------------------

def kana(**metadata):
    return Question("ka", "kana_choice", "hiragana", "", "ka", options=["ka", "na"],
                    metadata={"kana_symbol": "か", "romaji": "ka", "content_version": "v1", **metadata})


def app_and_class(asked, *, answer=None, lazy=False, **attempt):
    """A bare app object on the result page of ``asked`` and a class of doubles for the app's helpers."""
    store = Mock()
    store.record_review.return_value = {"scheduled": True} if answer is None else answer
    app = SimpleNamespace(store=store, alert=Mock())
    kana_session_runtime.assign(app, session_id="s", questions=[asked], question_index=0, session_right=0,
                                question=asked, script="hiragana")
    kana_attempt_runtime.assign(app, **{"revealed": False, "answered_text": "ka", "correct": True, "audio_ok": True,
                                        "submitted_at": None, "duration_ms": 5, **attempt})
    doubles = SimpleNamespace(
        _lazy_practice_questions=Mock(return_value=lazy), _word_spelling_plan=Mock(return_value=([], {}, [])),
        _set_queue_kind_policy=Mock(), _word_romaji_submission=Mock(return_value={}), _dispatch_after_answer=Mock())
    return app, doubles


def test_a_saved_rating_moves_the_position_on_in_a_new_object_and_dispatches():
    asked = kana()
    app, K = app_and_class(asked)
    before, attempt_before, queue = app._kana_session, app._kana_attempt, app._kana_session.questions
    line.rate(app, K, 3)
    record = app.store.record_review.call_args
    assert record.args == ("ka", "kana_choice", "hiragana", 3)
    assert record.kwargs["question_fingerprint"] == session_content.fingerprint(asked)
    assert record.kwargs["fair_new"] is False
    assert app._kana_session is not before and before.question_index == 0 and before.session_right == 0
    assert (app._kana_session.question_index, app._kana_session.session_right) == (1, 1)
    assert app._kana_session.questions is queue and app._kana_attempt is attempt_before
    K._dispatch_after_answer.assert_called_once_with(app)
    app.alert.assert_not_called()
    app.store.get_session_questions.assert_not_called()


def test_the_position_has_moved_when_the_next_question_is_dispatched():
    app, K = app_and_class(kana())
    seen = []
    K._dispatch_after_answer.side_effect = lambda value: seen.append(
        (value._kana_session.question_index, value._kana_session.session_right))
    line.rate(app, K, 3)
    assert seen == [(1, 1)]


def test_a_refused_rating_alerts_and_leaves_everything():
    app, K = app_and_class(kana(), revealed=True, correct=False)
    before = app._kana_session
    line.rate(app, K, 3)
    app.alert.assert_called_once_with(ONLY_AGAIN)
    assert app._kana_session is before
    app.store.record_review.assert_not_called()
    K._dispatch_after_answer.assert_not_called()


@pytest.mark.parametrize("lazy,call", [(True, (("s",), {"lazy": True})), (False, (("s",), {}))])
@pytest.mark.parametrize("why", ["spaced", "extended"])
def test_the_queue_is_read_again_in_the_way_of_the_lane(lazy, call, why):
    asked = kana(practice_config={"schedule_mode": "spaced"}) if why == "spaced" else kana()
    app, K = app_and_class(asked, answer={"scheduled": True, "queue_extended": why == "extended"}, lazy=lazy)
    reread = [asked, kana()]
    app.store.get_session_questions.return_value = reread
    seen = []
    K._dispatch_after_answer.side_effect = lambda value: seen.append(value._kana_session.questions)
    line.rate(app, K, 3)
    assert app.store.get_session_questions.call_args_list == [call]
    assert app._kana_session.questions is reread and seen == [reread]
    assert app._kana_session.question_index == 1
    assert K._lazy_practice_questions.call_count == 2


def test_the_fair_order_is_asked_again_for_the_way_the_queue_is_read():
    asked = kana(practice_config={"schedule_mode": "spaced"})
    app, K = app_and_class(asked)
    K._lazy_practice_questions.side_effect = [False, True]
    line.rate(app, K, 3)
    assert app.store.record_review.call_args.kwargs["fair_new"] is False
    app.store.get_session_questions.assert_called_once_with("s", lazy=True)


def test_what_follows_is_decided_by_what_the_app_holds_after_the_record():
    app, K = app_and_class(kana())
    later = kana(spelling_correction_of="word", practice_config={"schedule_mode": "spaced"})

    def record(*args, **kwargs):
        kana_session_runtime.assign(app, question=later)
        return {"scheduled": True}
    app.store.record_review.side_effect = record
    line.rate(app, K, 3)
    assert app.store.record_review.call_args.args[0] == "ka"
    assert (app._kana_session.question_index, app._kana_session.session_right) == (1, 0)
    app.store.get_session_questions.assert_called_once_with("s")


def test_a_word_answer_reaches_the_helpers_of_the_class():
    asked = Question("word", "word_meaning_kana", "hiragana", "", "かな", options=["か", "な"],
                     metadata={"content_version": "v1", "kana_answer": "かな"})
    app, K = app_and_class(asked, answered_text="かか", correct=False)
    correction = kana(practice_config={"kinds": ["kana_choice"]}, spelling_correction_of="word")
    K._word_spelling_plan.return_value = (["row"], {}, [correction])
    K._word_romaji_submission.return_value = {"word_romaji_parts": ["ka"], "word_romaji_plan": "plan"}
    line.rate(app, K, 3)
    K._word_spelling_plan.assert_called_once_with(app, "かか")
    K._set_queue_kind_policy.assert_called_once_with(app, "s", ["kana_choice"])
    K._word_romaji_submission.assert_called_once_with(app, "かか")
    record = app.store.record_review.call_args
    assert record.args[3] is None and record.kwargs["spelling_corrections"] == [correction]
    assert record.kwargs["word_romaji_parts"] == ["ka"]
    assert (app._kana_session.question_index, app._kana_session.session_right) == (1, 0)


def test_the_helpers_are_looked_up_on_the_class_at_the_moment_of_the_call(monkeypatch):
    asked = kana()
    app, K = app_and_class(asked)
    service = line.service(app, K)
    K._lazy_practice_questions = Mock(return_value="replaced later")
    monkeypatch.setattr(domain_module, "utc_now", lambda: SAVED)
    monkeypatch.setattr(session_content, "fingerprint", lambda value: ("print", value))
    rated = service.rate(asked, app._kana_session, app._kana_attempt, 3)
    assert isinstance(rated, Rated)
    kwargs = app.store.record_review.call_args.kwargs
    assert (kwargs["fair_new"], kwargs["finalized_at"], kwargs["question_fingerprint"]) == (
        "replaced later", SAVED, ("print", asked))


def test_a_busy_store_is_noted_by_the_diagnostics_of_that_moment(monkeypatch):
    app, K = app_and_class(kana())
    app.store.record_review.side_effect = LOCKED
    noted = Mock()
    monkeypatch.setattr(diagnostic_events, "record_error", noted)
    line.rate(app, K, 3)
    noted.assert_called_once_with("practice_rating", "commit", LOCKED)
    app.alert.assert_called_once_with(BUSY)
    assert app._kana_session.question_index == 0


def test_an_app_object_without_a_question_cannot_be_rated():
    app, K = app_and_class(kana())
    app._kana_session = KanaSession(session_id="s", question_index=0)
    with pytest.raises(AttributeError, match="question"):
        line.rate(app, K, 3)
    app.store.record_review.assert_not_called()


# --- what the modules may import ------------------------------------------------------------------------------------

def imported(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add("." * node.level + (node.module or ""))
    return names


def test_the_model_and_the_service_import_nothing_of_the_app():
    assert imported(SOURCE / "kana_line" / "rating_model.py") == {
        "__future__", "collections.abc", "dataclasses", "datetime", "..practice_preferences", ".attempt", ".session"}
    assert imported(SOURCE / "kana_line" / "rating.py") == {
        "__future__", "collections.abc", "..draft_debounce", ".attempt", ".rating_model", ".session"}


def test_the_app_side_module_imports_no_controls_and_not_the_app():
    assert imported(SOURCE / "kana_rating_runtime.py") == {
        "__future__", ".", ".kana_line", ".kana_line.rating", ".kana_line.rating_model",
        ".domain", ".session_content", ".diagnostic_events"}
