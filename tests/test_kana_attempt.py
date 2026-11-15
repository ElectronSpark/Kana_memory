"""The evidence of one kana attempt and the marks of a session's attempts, without a page (design 13).

The rules of kana_line.attempt are called directly with hand-made values; the app-side module is driven on a
bare object. No store, no control and no app is built here.
"""
from __future__ import annotations

import ast
from copy import copy, deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from kanamemory import kana_attempt_runtime as line
from kanamemory.kana_line import attempt as rules
from kanamemory.kana_line.attempt import UNSET, AttemptMarks, KanaAttempt

KEY = ("session-1", 3)
SOURCE = Path(line.__file__).parent


def clocks(utc="2026-11-15T00:00:05+00:00", monotonic=12.5):
    calls = []

    def utc_iso():
        calls.append("utc")
        return utc

    def now():
        calls.append("monotonic")
        return monotonic

    return calls, {"utc_iso": utc_iso, "monotonic": now}


# --- the attempt object ---------------------------------------------------------------------------------------------

def test_a_new_attempt_has_nothing_set():
    attempt = KanaAttempt()
    assert attempt.set_fields() == {}
    assert [attempt.get(name, "default") for name in ("revealed", "answered_text", "correct", "audio_ok", "started",
                                                      "submitted_at", "duration_ms")] == ["default"] * 7
    assert not UNSET and repr(UNSET) == "UNSET" and copy(UNSET) is UNSET and deepcopy(UNSET) is UNSET


def test_a_field_set_to_none_is_set():
    attempt = KanaAttempt(answered_text=None, submitted_at=None)
    assert attempt.set_fields() == {"answered_text": None, "submitted_at": None}
    assert attempt.get("answered_text", "default") is None


def test_two_attempts_with_the_same_fields_are_still_two():
    assert KanaAttempt(revealed=True) != KanaAttempt(revealed=True)


# --- the rules --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("assisted, heard", [(False, False), (True, False), (False, True), (True, True)])
def test_a_question_about_to_be_drawn_starts_fresh_but_keeps_the_verdict(assisted, heard):
    before = KanaAttempt(revealed=not assisted, answered_text="old", correct=True, audio_ok=heard, started=1.0,
                         submitted_at="2026-11-14T10:00:00+00:00", duration_ms=900)
    marks = AttemptMarks(assisted={KEY} if assisted else {("session-1", 2)})
    after = rules.fresh(before, KEY, marks, heard=heard, started=40.0)
    assert after is not before and before.answered_text == "old"
    assert after.set_fields() == {"revealed": assisted, "answered_text": None, "correct": True, "audio_ok": not heard,
                                  "started": 40.0, "submitted_at": None, "duration_ms": 0}


def test_a_fresh_attempt_leaves_a_verdict_that_was_never_set_unset():
    after = rules.fresh(KanaAttempt(), KEY, AttemptMarks(), heard=False, started=1.0)
    assert "correct" not in after.set_fields()


def test_a_page_that_stops_clears_what_was_seen_submitted_and_judged_only():
    before = KanaAttempt(revealed=True, answered_text="old", correct=True, audio_ok=True, started=3.0,
                         submitted_at="2026-11-14T10:00:00+00:00", duration_ms=900)
    after = rules.cleared(before)
    assert after.set_fields() == {"revealed": False, "answered_text": None, "correct": False, "audio_ok": True,
                                  "started": 3.0, "submitted_at": "2026-11-14T10:00:00+00:00", "duration_ms": 900}
    assert set(rules.cleared(KanaAttempt()).set_fields()) == {"revealed", "answered_text", "correct"}


def test_the_first_submit_freezes_the_time_and_marks_the_attempt_answered():
    calls, clock = clocks()
    marks = AttemptMarks()
    after = rules.submitted(KanaAttempt(revealed=False, started=10.0, submitted_at=None, duration_ms=0), "ka", KEY,
                            marks, **clock)
    assert (after.answered_text, after.submitted_at, after.duration_ms) == ("ka", "2026-11-15T00:00:05+00:00", 2500)
    assert calls == ["utc", "monotonic"]
    assert marks.answered == {KEY} and marks.assisted == set()


def test_a_later_submit_keeps_the_frozen_time_and_reads_no_clock():
    calls, clock = clocks()
    marks = AttemptMarks(answered={KEY})
    before = KanaAttempt(revealed=False, started=10.0, submitted_at="2026-11-14T09:00:00+00:00", duration_ms=1200)
    after = rules.submitted(before, "ki", KEY, marks, **clock)
    assert (after.answered_text, after.submitted_at, after.duration_ms) == ("ki", "2026-11-14T09:00:00+00:00", 1200)
    assert calls == [] and marks.answered == {KEY}


def test_a_duration_is_never_negative_and_is_whole_milliseconds():
    _, clock = clocks(monotonic=9.0)
    early = rules.submitted(KanaAttempt(revealed=False, started=10.0), "ka", KEY, AttemptMarks(), **clock)
    assert early.duration_ms == 0
    _, clock = clocks(monotonic=10.0019)
    assert rules.submitted(KanaAttempt(revealed=False, started=10.0), "ka", KEY, AttemptMarks(), **clock).duration_ms == 1


def test_a_submit_after_help_marks_the_attempt_assisted_too():
    _, clock = clocks()
    marks = AttemptMarks()
    rules.submitted(KanaAttempt(revealed=True, started=10.0), "", KEY, marks, **clock)
    assert marks.assisted == {KEY} and marks.answered == {KEY}


def test_a_submit_needs_the_start_time_and_whether_help_was_seen():
    _, clock = clocks()
    with pytest.raises(AttributeError):
        rules.submitted(KanaAttempt(revealed=False), "ka", KEY, AttemptMarks(), **clock)
    marks = AttemptMarks()
    with pytest.raises(AttributeError):
        rules.submitted(KanaAttempt(started=1.0), "ka", KEY, marks, **clock)
    assert marks.answered == {KEY}


@pytest.mark.parametrize("matches, revealed, verdict", [(True, False, True), (True, True, False),
                                                        (False, False, False), (False, True, False)])
def test_an_answer_is_right_only_when_it_matches_and_no_help_was_seen(matches, revealed, verdict):
    after = rules.judged(KanaAttempt(revealed=revealed, answered_text="ka"), matches)
    assert after.correct is verdict and after.answered_text == "ka"


def test_a_falsy_match_is_kept_as_it_is_and_help_is_not_asked_about():
    assert rules.judged(KanaAttempt(), None).correct is None


@pytest.mark.parametrize("heard, played, audio_ok", [(False, False, True), (False, True, True), (True, True, True),
                                                     (True, False, False)])
def test_a_restored_result_brings_back_its_sound_flag_time_and_duration(heard, played, audio_ok):
    state = {"question_audio_ok": played, "submitted_at_utc": "2026-11-14T09:00:00+00:00", "answer_duration_ms": 1500}
    after = rules.restored_result(KanaAttempt(revealed=True, answered_text=None, started=5.0), state, heard=heard)
    assert after.set_fields() == {"revealed": True, "answered_text": None, "started": 5.0, "audio_ok": audio_ok,
                                  "submitted_at": "2026-11-14T09:00:00+00:00", "duration_ms": 1500}


def test_a_restored_result_without_saved_times_has_none_and_zero():
    after = rules.restored_result(KanaAttempt(), {}, heard=True)
    assert (after.audio_ok, after.submitted_at, after.duration_ms) == (False, None, 0)


def test_back_after_a_seen_result_marks_help_and_opens_the_attempt_again():
    marks = AttemptMarks(assisted={("session-1", 1)}, answered={KEY, ("session-1", 1)})
    rules.returned_from_result(marks, KEY)
    assert marks.assisted == {("session-1", 1), KEY} and marks.answered == {("session-1", 1)}
    rules.returned_from_result(marks, KEY)
    assert marks.assisted == {("session-1", 1), KEY} and marks.answered == {("session-1", 1)}


def test_the_saved_indices_become_marks_of_the_session():
    assert rules.saved_assisted("session-1", [0, 3, 3]) == {("session-1", 0), ("session-1", 3)}
    assert rules.saved_assisted("session-1", ()) == set()


def test_the_time_on_a_report_page_does_not_count_as_answering_time():
    assert rules.moved_start(100.0, 130.0, 120.0) == 110.0


def test_marks_are_emptied_in_place():
    assisted, answered = {KEY}, {KEY}
    marks = AttemptMarks(assisted=assisted, answered=answered)
    marks.clear()
    assert marks.assisted is assisted and marks.answered is answered and not assisted and not answered


# --- the app side -----------------------------------------------------------------------------------------------------

def test_an_app_object_without_an_attempt_reads_as_nothing_set():
    app = SimpleNamespace()
    assert line.attempt(app).set_fields() == {} and not hasattr(app, "_kana_attempt")
    for reader in (line.revealed, line.answered_text, line.correct, line.audio_ok, line.started, line.submitted_at,
                   line.duration_ms):
        with pytest.raises(AttributeError):
            reader(app)


def test_assigning_a_field_gives_the_app_a_new_attempt_and_leaves_the_old_one():
    app = SimpleNamespace()
    line.assign(app, revealed=False, answered_text=None)
    first = app._kana_attempt
    line.set_revealed(app, True)
    assert app._kana_attempt is not first and first.revealed is False
    assert (line.revealed(app), line.answered_text(app)) == (True, None)
    for setter, reader, value in ((line.set_answered_text, line.answered_text, "ka"), (line.set_correct, line.correct, True),
                                  (line.set_audio_ok, line.audio_ok, True), (line.set_started, line.started, 4.5),
                                  (line.set_submitted_at, line.submitted_at, "2026-11-15T00:00:00+00:00"),
                                  (line.set_duration_ms, line.duration_ms, 700)):
        kept = app._kana_attempt
        setter(app, value)
        assert reader(app) == value and app._kana_attempt is not kept


def test_a_kept_attempt_is_restored_whole():
    app = SimpleNamespace()
    line.assign(app, revealed=False, answered_text=None, audio_ok=True)
    kept = app._kana_attempt
    line.assign(app, revealed=True, answered_text="ka", correct=False)
    line.set_attempt(app, kept)
    assert line.attempt(app).set_fields() == {"revealed": False, "answered_text": None, "audio_ok": True}


def test_something_else_in_the_attempts_place_reads_as_nothing_set():
    app = SimpleNamespace(_kana_attempt=object())
    assert line.attempt(app).set_fields() == {}
    line.set_revealed(app, True)
    assert isinstance(app._kana_attempt, KanaAttempt) and line.revealed(app) is True


def test_the_marks_are_the_apps_own_sets():
    app = SimpleNamespace()
    with pytest.raises(AttributeError):
        line.marks(app)
    marks = line.new_marks(app)
    assert line.marks(app) is marks and line.assisted(app) is marks.assisted and line.answered(app) is marks.answered
    line.assisted(app).add(KEY)
    replacement = {("session-2", 0)}
    line.set_assisted(app, replacement)
    assert line.assisted(app) is replacement and line.marks(app) is marks
    line.set_answered(app, {KEY})
    assert line.answered(app) == {KEY}


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


def test_the_rules_import_nothing_of_the_app():
    assert imported(SOURCE / "kana_line" / "attempt.py") == {"__future__", "collections.abc", "dataclasses",
                                                             ".fields"}
    assert imported(SOURCE / "kana_line" / "__init__.py") == set()


def test_the_app_side_module_imports_no_controls_and_not_the_app():
    assert imported(SOURCE / "kana_attempt_runtime.py") == {"__future__", "dataclasses", ".kana_line.attempt"}
