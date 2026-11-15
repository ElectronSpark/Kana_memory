"""How the kana practice saves a rating, pinned as it behaves today.

A rating on the result page writes one review record. These tests bring the practice to its result page as the
learner would (or build a practice that stands there), call the entry the rating buttons call, and pin what the
record is given: the question, the place in the session, the content version, the order of new items, the
question's fingerprint, and what a word answer adds. They also pin what is left when the record cannot be put
together. They read and set the practice's state through ``support.kana_support`` only and build every text and
record they need, so they run on the demo content.
"""
import sqlite3
from datetime import datetime, timezone
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest

from kanamemory import diagnostic_events, session_content
from kanamemory import domain as domain_module
from kanamemory.app import KanaMemory
from kanamemory.domain import Question
from support import kana_support
from support.answer_option_ui_support import RECORDS, WORD, preview_app  # noqa: F401  (fixture)
from support.app_fixtures import question_app, setup_app  # noqa: F401  (fixtures)


SUBMITTED = datetime(2030, 5, 6, 7, 8, 9, tzinfo=timezone.utc)
RATED = datetime(2030, 5, 6, 7, 9, 0, tzinfo=timezone.utc)
RECORD_NAMES = {"session_id", "question_index", "answered_text", "outcome", "audio_ok", "content_version",
                "assisted", "duration_ms", "now", "finalized_at", "fair_new", "question_fingerprint"}
WORD_NAMES = RECORD_NAMES | {"kana_rows", "spelling_corrections"}
NOT_SAVED = "复习记录尚未保存："
BUSY = "数据库正忙，复习记录尚未保存。请稍后再点一次评级。"


# --- questions and practices --------------------------------------------------------------------------------------

def kana_question(**metadata):
    record, other = RECORDS[0], RECORDS[1]
    return Question(record["id"], "kana_choice", "hiragana", "", record["romaji"],
                    options=[record["romaji"], other["romaji"]],
                    metadata={"kana_symbol": record["hiragana"], "romaji": record["romaji"],
                              "content_version": "kana-version", **metadata})


def word_question(**metadata):
    return Question(WORD["id"], "word_meaning_kana", "hiragana", "", WORD["reading"], options=["か", "な"],
                    metadata={"romaji": "kana", "meaning_hint": "假名", "content_version": "word-version",
                              "kana_answer": WORD["reading"], **metadata})


def correction(kinds):
    """A question a misspelt word appends to the queue; it carries the kinds the queue goes on with."""
    return kana_question(spelling_correction_of=WORD["id"], practice_config={"kinds": kinds})


@pytest.fixture
def rating_app(preview_app, monkeypatch):
    app = preview_app
    app.store.record_review.return_value = {"scheduled": True}
    app.dispatched = Mock()
    monkeypatch.setattr(KanaMemory, "_dispatch_after_answer", app.dispatched)
    return app


def answered(app, question, answer):
    """The learner answers ``question`` (its sound already played) and stands on its result page."""
    kana_support.set_questions(app, [question, kana_question()])
    app.show_question()
    kana_support.set_question_audio_ok(app, True)
    app.check_answer(answer)
    assert app.page == "feedback"
    app.alert.reset_mock()
    app.store.set_setting.reset_mock()
    return kana_support.question(app)


def record_of(app):
    app.store.record_review.assert_called_once()
    return app.store.record_review.call_args


def word_parts(app, monkeypatch, *, corrections=(), submission=None, rows=None):
    """Replace what a word answer adds to its record, on the class, as the rating reaches it.

    Returns the doubles (plan, policy, submission) and ``order``, which sees their calls, the clock's and the
    record's in the order they happen.
    """
    rows = [{"id": "ka"}] if rows is None else rows
    parts = SimpleNamespace(
        rows=rows, corrections=list(corrections),
        plan=Mock(return_value=(rows, {"correct": False}, None)), policy=Mock(),
        submission=Mock(return_value={} if submission is None else submission), clock=Mock(return_value=RATED),
        order=Mock())
    parts.plan.return_value = (rows, {"correct": False}, parts.corrections)
    monkeypatch.setattr(KanaMemory, "_word_spelling_plan", parts.plan)
    monkeypatch.setattr(KanaMemory, "_set_queue_kind_policy", parts.policy)
    monkeypatch.setattr(KanaMemory, "_word_romaji_submission", parts.submission)
    monkeypatch.setattr(domain_module, "utc_now", parts.clock)
    for name, double in (("clock", parts.clock), ("plan", parts.plan), ("policy", parts.policy),
                         ("submission", parts.submission), ("record", app.store.record_review)):
        parts.order.attach_mock(double, name)
    return parts


def happened(parts):
    return [entry[0] for entry in parts.order.mock_calls]


FULL = dict(session_id="held", question_index=2, session_right=1, answer_revealed=False, answered_text="ka",
            answer_correct=True, question_audio_ok=True, answer_submitted_at=SUBMITTED.isoformat(),
            answer_duration_ms=900)


def standing_app(monkeypatch, question, *, without=(), **state):
    """A hand-made practice that stands on the result page of ``question`` and holds only the state named: FULL
    without the names in ``without``, with ``state`` over it."""
    store = Mock()
    store.record_review.return_value = {"scheduled": True}
    store.practice_lane = "word_romaji"
    held = {name: value for name, value in {**FULL, **state}.items() if name not in without}
    app = kana_support.with_state(SimpleNamespace(page="feedback", store=store, alert=Mock()),
                                  assisted_attempts=set(), answered_attempts=set(), question=question, **held)
    app._attempt_key = MethodType(KanaMemory._attempt_key, app)
    app.dispatched = Mock()
    monkeypatch.setattr(KanaMemory, "_dispatch_after_answer", app.dispatched)
    return app


# --- what the record names ----------------------------------------------------------------------------------------

def test_a_kana_rating_names_the_question_the_place_and_the_version_and_adds_nothing_else(rating_app):
    app = rating_app
    answered(app, kana_question(), "ka")
    KanaMemory.save_rating(app, 3)
    record = record_of(app)
    assert record.args == ("ka", "kana_choice", "hiragana", 3)
    assert set(record.kwargs) == RECORD_NAMES
    assert (record.kwargs["session_id"], record.kwargs["question_index"], record.kwargs["content_version"]) == (
        "session", 0, "kana-version")


def test_the_record_takes_the_script_of_the_question_not_the_one_the_practice_holds(rating_app):
    app = rating_app
    answered(app, kana_question(), "ka")
    kana_support.set_script(app, "katakana")
    KanaMemory.save_rating(app, 3)
    assert record_of(app).args[2] == "hiragana"


def test_a_rating_names_the_session_and_the_place_the_practice_holds_at_that_moment(rating_app):
    app = rating_app
    answered(app, kana_question(), "ka")
    kana_support.set_session_id(app, "queue-7")
    kana_support.set_question_index(app, 4)
    KanaMemory.save_rating(app, 3)
    record = record_of(app)
    assert (record.kwargs["session_id"], record.kwargs["question_index"]) == ("queue-7", 4)
    assert kana_support.question_index(app) == 5


@pytest.mark.parametrize("lane,fair", [("main", True), ("word_romaji", False)])
def test_new_items_take_their_fair_order_only_in_the_main_lane(rating_app, lane, fair):
    app = rating_app
    answered(app, kana_question(), "ka")
    app.store.practice_lane = lane
    KanaMemory.save_rating(app, 3)
    assert record_of(app).kwargs["fair_new"] is fair


def test_the_fair_order_is_asked_on_the_class_when_the_record_is_put_together(rating_app, monkeypatch):
    app = rating_app
    answered(app, kana_question(), "ka")
    asked = Mock(return_value="as asked")
    monkeypatch.setattr(KanaMemory, "_lazy_practice_questions", asked)
    KanaMemory.save_rating(app, 3)
    asked.assert_called_once_with(app)
    assert record_of(app).kwargs["fair_new"] == "as asked"


def test_a_rating_records_the_fingerprint_of_the_question(rating_app):
    app = rating_app
    question = answered(app, kana_question(), "ka")
    KanaMemory.save_rating(app, 3)
    assert record_of(app).kwargs["question_fingerprint"] == session_content.fingerprint(question)


def test_the_fingerprint_is_taken_when_the_record_is_put_together(rating_app, monkeypatch):
    app = rating_app
    question = answered(app, kana_question(), "ka")
    monkeypatch.setattr(session_content, "fingerprint", lambda value: ("print of", value))
    KanaMemory.save_rating(app, 3)
    assert record_of(app).kwargs["question_fingerprint"] == ("print of", question)


def test_a_kana_answer_asks_for_no_spelling_plan(rating_app, monkeypatch):
    app = rating_app
    answered(app, kana_question(), "ka")
    parts = word_parts(app, monkeypatch, corrections=[correction(["kana_choice"])])
    KanaMemory.save_rating(app, 3)
    assert happened(parts) == ["clock", "record"]
    assert set(record_of(app).kwargs) == RECORD_NAMES


# --- what a word answer adds --------------------------------------------------------------------------------------

def test_a_word_answer_is_saved_with_the_kana_table_and_no_corrections_on_a_store_that_plans_none(rating_app):
    app = rating_app
    answered(app, word_question(), WORD["reading"])
    KanaMemory.save_rating(app, 3)
    record = record_of(app)
    assert record.args == (WORD["id"], "word_meaning_kana", "hiragana", None)
    assert set(record.kwargs) == WORD_NAMES
    assert record.kwargs["kana_rows"] is RECORDS and record.kwargs["spelling_corrections"] == []
    assert record.kwargs["content_version"] == "word-version"
    app.store.set_setting.assert_not_called()


def test_a_misspelt_word_sets_the_kinds_of_its_first_correction_before_the_record(rating_app, monkeypatch):
    app = rating_app
    answered(app, word_question(), "かか")
    parts = word_parts(app, monkeypatch, corrections=[correction(["kana_choice"]), correction(["romaji_kana"])])
    KanaMemory.save_rating(app, 3)
    assert happened(parts) == ["clock", "plan", "policy", "submission", "record"]
    parts.plan.assert_called_once_with(app, "かか")
    parts.policy.assert_called_once_with(app, "session", ["kana_choice"])
    parts.submission.assert_called_once_with(app, "かか")
    record = record_of(app)
    assert set(record.kwargs) == WORD_NAMES
    assert record.kwargs["kana_rows"] is parts.rows and record.kwargs["spelling_corrections"] is parts.corrections
    assert (record.kwargs["answered_text"], record.kwargs["outcome"]) == ("かか", "incorrect")
    assert record.kwargs["now"] is RATED and record.kwargs["finalized_at"] is RATED


def test_the_kinds_are_set_for_the_session_the_practice_holds(rating_app, monkeypatch):
    app = rating_app
    answered(app, word_question(), "かか")
    kana_support.set_session_id(app, "queue-7")
    parts = word_parts(app, monkeypatch, corrections=[correction(["kana_choice"])])
    KanaMemory.save_rating(app, 3)
    parts.policy.assert_called_once_with(app, "queue-7", ["kana_choice"])


def test_the_kinds_are_written_to_the_store_with_the_regular_kinds_they_replace(rating_app, monkeypatch):
    app = rating_app
    answered(app, word_question(), "かか")
    plan = Mock(return_value=(RECORDS, {"correct": False}, [correction(["kana_choice"])]))
    monkeypatch.setattr(KanaMemory, "_word_spelling_plan", plan)
    KanaMemory.save_rating(app, 3)
    key, policy = app.store.set_setting.call_args.args
    assert key == "practice_queue_kinds"
    assert (policy["session_id"], policy["kinds"]) == ("session", ["kana_choice"])
    assert policy["regular_kinds"] == list(KanaMemory._enabled_preference_kinds(app._practice_preferences))
    record_of(app)


def test_a_word_without_corrections_sets_no_kinds(rating_app, monkeypatch):
    app = rating_app
    answered(app, word_question(), WORD["reading"])
    parts = word_parts(app, monkeypatch)
    KanaMemory.save_rating(app, 3)
    assert happened(parts) == ["clock", "plan", "submission", "record"]
    assert record_of(app).kwargs["spelling_corrections"] == []


def test_a_word_answer_adds_the_romaji_parts_the_learner_chose(rating_app, monkeypatch):
    app = rating_app
    answered(app, word_question(), WORD["reading"])
    chosen = {"word_romaji_parts": ["ka", "na"], "word_romaji_plan": "the plan"}
    parts = word_parts(app, monkeypatch, submission=chosen)
    KanaMemory.save_rating(app, 3)
    record = record_of(app)
    assert set(record.kwargs) == WORD_NAMES | set(chosen)
    assert {name: record.kwargs[name] for name in chosen} == chosen
    parts.submission.assert_called_once_with(app, WORD["reading"])


def test_a_part_that_names_a_value_of_the_record_again_fails_the_save(rating_app, monkeypatch):
    app = rating_app
    answered(app, word_question(), WORD["reading"])
    word_parts(app, monkeypatch, submission={"answered_text": "another"})
    KanaMemory.save_rating(app, 3)
    assert app.alert.call_args.args[0].startswith(NOT_SAVED)
    app.store.record_review.assert_not_called()
    assert kana_support.question_index(app) == 0
    app.dispatched.assert_not_called()


# --- the record cannot be put together ----------------------------------------------------------------------------

@pytest.mark.parametrize("part", ["clock", "plan", "policy", "submission", "fair", "fingerprint"])
def test_a_failure_while_the_record_is_put_together_is_reported_and_keeps_the_position(rating_app, monkeypatch,
                                                                                        part):
    app = rating_app
    answered(app, word_question(), "かか")
    parts = word_parts(app, monkeypatch, corrections=[correction(["kana_choice"])])
    failure = RuntimeError(f"the {part} failed")
    if part == "fair":
        monkeypatch.setattr(KanaMemory, "_lazy_practice_questions", Mock(side_effect=failure))
    elif part == "fingerprint":
        monkeypatch.setattr(session_content, "fingerprint", Mock(side_effect=failure))
    else:
        getattr(parts, part).side_effect = failure
    before = kana_support.position(app)
    KanaMemory.save_rating(app, 3)
    app.alert.assert_called_once_with(f"{NOT_SAVED}the {part} failed")
    app.store.record_review.assert_not_called()
    assert kana_support.position(app) == before and app.page == "feedback"
    app.dispatched.assert_not_called()


def test_a_busy_database_while_the_record_is_put_together_is_noted_and_can_be_pressed_again(rating_app,
                                                                                            monkeypatch):
    app = rating_app
    answered(app, word_question(), "かか")
    parts = word_parts(app, monkeypatch)
    locked = sqlite3.OperationalError("database is locked")
    parts.plan.side_effect = [locked, parts.plan.return_value]
    noted = Mock()
    monkeypatch.setattr(diagnostic_events, "record_error", noted)
    KanaMemory.save_rating(app, 3)
    noted.assert_called_once_with("practice_rating", "commit", locked)
    app.alert.assert_called_once_with(BUSY)
    app.store.record_review.assert_not_called()
    assert kana_support.question_index(app) == 0
    KanaMemory.save_rating(app, 3)
    record_of(app)
    assert kana_support.question_index(app) == 1
    app.dispatched.assert_called_once_with(app)


def test_a_busy_store_is_noted_for_diagnostics_before_the_learner_is_told(rating_app, monkeypatch):
    app = rating_app
    answered(app, kana_question(), "ka")
    locked = sqlite3.OperationalError("database is locked")
    app.store.record_review.side_effect = locked
    order = Mock()
    monkeypatch.setattr(diagnostic_events, "record_error", order.noted)
    order.attach_mock(app.alert, "alert")
    KanaMemory.save_rating(app, 3)
    assert [entry[0] for entry in order.mock_calls] == ["noted", "alert"]
    order.noted.assert_called_once_with("practice_rating", "commit", locked)
    app.alert.assert_called_once_with(BUSY)


def test_a_store_that_cannot_record_is_reported_before_the_fair_order_is_asked(rating_app, monkeypatch):
    app = rating_app
    answered(app, kana_question(), "ka")
    del app.store.record_review
    asked = Mock()
    monkeypatch.setattr(KanaMemory, "_lazy_practice_questions", asked)
    KanaMemory.save_rating(app, 3)
    app.alert.assert_called_once()
    assert app.alert.call_args.args[0].startswith(NOT_SAVED)
    asked.assert_not_called()
    assert kana_support.question_index(app) == 0
    app.dispatched.assert_not_called()


def test_another_failure_of_the_store_is_not_noted_for_diagnostics(rating_app, monkeypatch):
    app = rating_app
    answered(app, kana_question(), "ka")
    app.store.record_review.side_effect = RuntimeError("disk full")
    noted = Mock()
    monkeypatch.setattr(diagnostic_events, "record_error", noted)
    KanaMemory.save_rating(app, 3)
    noted.assert_not_called()
    app.alert.assert_called_once_with(f"{NOT_SAVED}disk full")


@pytest.mark.parametrize("answer,message", [
    ({"scheduled": False}, "这次作答未安排复习：请重试"),
    ({"scheduled": False, "reason": "已过期"}, "这次作答未安排复习：已过期"),
    ({}, f"{NOT_SAVED}'scheduled'"),
])
def test_an_answer_of_the_store_that_schedules_nothing_keeps_the_position(rating_app, answer, message):
    app = rating_app
    answered(app, kana_question(practice_config={"schedule_mode": "spaced"}), "ka")
    app.store.record_review.return_value = answer
    before = kana_support.position(app)
    KanaMemory.save_rating(app, 3)
    app.alert.assert_called_once_with(message)
    assert kana_support.position(app) == before
    app.store.get_session_questions.assert_not_called()
    app.dispatched.assert_not_called()


# --- what follows a saved rating ----------------------------------------------------------------------------------

def test_the_fair_order_is_asked_again_when_the_queue_is_read_again(rating_app, monkeypatch):
    app = rating_app
    answered(app, kana_question(practice_config={"schedule_mode": "spaced"}), "ka")
    asked = Mock(side_effect=[False, True])
    monkeypatch.setattr(KanaMemory, "_lazy_practice_questions", asked)
    reread = [kana_question()]
    app.store.get_session_questions.return_value = reread
    KanaMemory.save_rating(app, 3)
    assert record_of(app).kwargs["fair_new"] is False
    assert asked.call_count == 2
    app.store.get_session_questions.assert_called_once_with("session", lazy=True)
    assert kana_support.questions(app) is reread


def test_what_follows_is_decided_by_the_question_the_practice_holds_after_the_record(rating_app):
    app = rating_app
    answered(app, kana_question(), "ka")
    later = kana_question(spelling_correction_of=WORD["id"], practice_config={"schedule_mode": "spaced"})

    def record(*args, **kwargs):
        kana_support.set_question(app, later)
        return {"scheduled": True}
    app.store.record_review.side_effect = record
    app.store.practice_lane = "word_romaji"
    reread = [kana_question()]
    app.store.get_session_questions.return_value = reread
    KanaMemory.save_rating(app, 3)
    # The question held now is a correction in a spaced queue: the right answer is not counted, the queue is read.
    assert (kana_support.question_index(app), kana_support.session_right(app)) == (1, 0)
    app.store.get_session_questions.assert_called_once_with("session")
    assert kana_support.questions(app) is reread
    app.dispatched.assert_called_once_with(app)


# --- a practice that holds only part of its state -----------------------------------------------------------------

def test_a_standing_practice_saves_what_it_holds(monkeypatch):
    question = kana_question()
    app = standing_app(monkeypatch, question)
    KanaMemory.save_rating(app, 3)
    record = record_of(app)
    assert record.args == ("ka", "kana_choice", "hiragana", 3)
    assert record.kwargs == {
        "session_id": "held", "question_index": 2, "answered_text": "ka", "outcome": "correct", "audio_ok": True,
        "content_version": "kana-version", "assisted": False, "duration_ms": 900, "now": SUBMITTED,
        "finalized_at": record.kwargs["finalized_at"], "fair_new": False,
        "question_fingerprint": session_content.fingerprint(question)}
    assert (kana_support.question_index(app), kana_support.session_right(app)) == (3, 2)
    app.dispatched.assert_called_once_with(app)
    app.alert.assert_not_called()


def test_a_time_and_a_duration_that_were_never_set_are_recorded_as_none_and_zero(monkeypatch):
    app = standing_app(monkeypatch, kana_question(), without=("answer_submitted_at", "answer_duration_ms"))
    KanaMemory.save_rating(app, 3)
    record = record_of(app)
    assert record.kwargs["now"] is None and record.kwargs["duration_ms"] == 0


@pytest.mark.parametrize("missing", ["answered_text", "answer_correct", "question_audio_ok"])
def test_evidence_that_was_never_set_is_reported_as_a_failed_save(monkeypatch, missing):
    app = standing_app(monkeypatch, kana_question(), without=(missing,))
    KanaMemory.save_rating(app, 3)
    app.alert.assert_called_once()
    assert app.alert.call_args.args[0].startswith(NOT_SAVED)
    app.store.record_review.assert_not_called()
    assert kana_support.question_index(app) == 2
    app.dispatched.assert_not_called()


def test_a_wrong_answer_is_saved_and_moves_on_without_a_count_of_right_answers(monkeypatch):
    app = standing_app(monkeypatch, kana_question(), without=("session_right",), answer_correct=False)
    KanaMemory.save_rating(app, 1)
    record_of(app)
    assert kana_support.question_index(app) == 3
    with pytest.raises(AttributeError):
        kana_support.session_right(app)
    app.dispatched.assert_called_once_with(app)


def test_a_content_version_the_question_lacks_is_reported_as_a_failed_save(monkeypatch):
    question = kana_question()
    del question.metadata["content_version"]
    app = standing_app(monkeypatch, question)
    KanaMemory.save_rating(app, 3)
    app.alert.assert_called_once_with(f"{NOT_SAVED}'content_version'")
    app.store.record_review.assert_not_called()
    assert kana_support.question_index(app) == 2
