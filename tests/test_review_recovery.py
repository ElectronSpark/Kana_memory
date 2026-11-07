"""Recovery only spends queue priority; real ratings, due dates and rows survive."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest

from kanamemory.domain import Question, StudyStore
from kanamemory.learning_store import LearningStore
from kanamemory.review_dispatch import review_priority, reviews_today, stored_review_priority
from support.learning_store_fixtures import item, question, ready
from support.word_context_memory_support import ContextContent
from support.word_context_memory_support import answer_word
from support.word_context_memory_support import begin

UTC = timezone.utc
NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)


def card(last, due, *, state=1, count=3, serialized=False):
    result = {"card_id": "direction", "review_count": count, "due_at_utc": due.isoformat()}
    scheduler = {"state": state, "last_review": last.isoformat() if last else None}
    result["scheduler_state_json" if serialized else "scheduler_state"] = (
        json.dumps(scheduler) if serialized else scheduler)
    return result


def database_rows(store):
    """Include every persisted table, not just the subset with FSRS state."""
    names = [row[0] for row in store._db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    return {name: tuple(tuple(row) for row in store._db.execute(
        'SELECT * FROM "' + name.replace('"', '""') + '" ORDER BY rowid')) for name in names}


@pytest.mark.parametrize("serialized", [False, True])
@pytest.mark.parametrize("state", [1, 3])
@pytest.mark.parametrize("today,rank", [(0, 2), (1, 2), (2, 3), (3, 3)])
def test_only_first_two_actual_ratings_preserve_intraday_short_priority(serialized, state, today, rank):
    due = NOW - timedelta(minutes=1)
    saved = card(NOW - timedelta(minutes=11), due, state=state, serialized=serialized)
    before = deepcopy(saved)
    assert review_priority(saved, now=NOW, day_start=NOW.replace(hour=0), reviewed_today=today) == (
        rank, due.timestamp())
    assert saved == before


@pytest.mark.parametrize("last,due,state,count,rank", [
    (NOW - timedelta(days=21, minutes=10), NOW - timedelta(days=21), 1, 3, 3),
    (NOW - timedelta(hours=13), NOW - timedelta(hours=12, minutes=50), 3, 3, 3),
    (NOW - timedelta(days=2), NOW, 1, 3, 3),
    (NOW - timedelta(hours=25), NOW - timedelta(hours=24, minutes=50), 3, 3, 3),
    (NOW - timedelta(minutes=10), NOW, 2, 3, 3),
    (NOW + timedelta(seconds=1), NOW, 1, 3, 3),
    (None, NOW, 1, 3, 3),
    (NOW, NOW + timedelta(seconds=1), 1, 3, 4),
    (NOW - timedelta(minutes=10), NOW, 1, 0, 4),
])
def test_old_invalid_or_future_steps_never_gain_urgent_priority(last, due, state, count, rank):
    saved = card(last, due, state=state, count=count)
    before = deepcopy(saved)
    assert review_priority(saved, now=NOW, day_start=NOW.replace(hour=0))[0] == rank
    assert saved == before


def test_regular_due_order_remains_original_due_not_service_count():
    old = card(NOW - timedelta(days=30), NOW - timedelta(days=20), state=2)
    retry = card(NOW - timedelta(minutes=12), NOW - timedelta(minutes=2))
    old_priority = review_priority(old, now=NOW, reviewed_today=9)
    retry_priority = review_priority(retry, now=NOW, reviewed_today=2)
    assert old_priority == (3, datetime.fromisoformat(old["due_at_utc"]).timestamp())
    assert old_priority < retry_priority < review_priority(None, now=NOW)
    assert review_priority(retry, due=(NOW + timedelta(seconds=1)).isoformat(), now=NOW) == (4, 0)


def test_priority_budget_survives_short_visits_restart_backup_and_duplicate_callback(tmp_path):
    folder = tmp_path / "learner"
    q = Question("kana-a", "kana_choice", "hiragana", "あ", "a", ["a", "i"])
    with StudyStore(folder, "UTC") as store:
        sid = store.start_session([q], now=NOW)
        first = store.record_review(q.content_id, q.kind, q.script, 1, now=NOW,
                                   session_id=sid, question_index=0)
        repeat = store.record_review(q.content_id, q.kind, q.script, 1, now=NOW,
                                    session_id=sid, question_index=0)
        assert repeat["already_recorded"]
        at = datetime.fromisoformat(first["due_at_utc"]) + timedelta(seconds=1)
        current = store.get_card(q.content_id, q.kind, q.script)
        assert reviews_today(store, at)[1] == {current["card_id"]: 1}
        assert stored_review_priority(store, current, now=at)[0] == 2
    with StudyStore(folder, "UTC") as store:
        assert reviews_today(store, at)[1][current["card_id"]] == 1
        second = store.record_review(q.content_id, q.kind, q.script, 1, now=at)
        at = datetime.fromisoformat(second["due_at_utc"]) + timedelta(seconds=1)
        current = store.get_card(q.content_id, q.kind, q.script)
        assert reviews_today(store, at)[1][current["card_id"]] == 2
        assert stored_review_priority(store, current, now=at) == (
            3, datetime.fromisoformat(current["due_at_utc"]).timestamp())
        before = database_rows(store)
        backup = store.export_backup()
        for _ in range(3):
            reviews_today(store, at)
            stored_review_priority(store, current, now=at)
        assert database_rows(store) == before
    with StudyStore(folder, "UTC") as store:
        assert reviews_today(store, at)[1][current["card_id"]] == 2
        assert stored_review_priority(store, current, now=at)[0] == 3
    with StudyStore(tmp_path / "restored", "UTC") as restored:
        restored.import_backup(backup)
        assert reviews_today(restored, at)[1][current["card_id"]] == 2
        assert stored_review_priority(restored, current, now=at)[0] == 3
        tomorrow = NOW + timedelta(days=1)
        # An active ability always lists its carrier, here with no ratings today (0d70dcb, 2c44345).
        assert reviews_today(restored, tomorrow)[1] == {current["card_id"]: 0}
        assert stored_review_priority(restored, current, now=tomorrow)[0] == 3


@pytest.mark.parametrize("offset", [-5, 0, 9])
def test_equivalent_now_offsets_and_future_events_give_same_actual_count(tmp_path, offset):
    with StudyStore(tmp_path, "America/Toronto") as store:
        for name, at in [("before", NOW - timedelta(seconds=1)), ("now", NOW),
                         ("future", NOW + timedelta(seconds=1))]:
            store.record_review(name, "kana_choice", "hiragana", 1, now=at)
        before = database_rows(store)
        represented = NOW.astimezone(timezone(timedelta(hours=offset)))
        start, counts = reviews_today(store, represented)
        assert start == datetime(2026, 10, 27, 4, tzinfo=UTC)
        assert counts == {store.get_card(name, "kana_choice", "hiragana")["card_id"]: 1
                          for name in ("before", "now")}
        assert database_rows(store) == before


def test_daily_count_rejects_ambiguous_naive_now_without_writing(tmp_path):
    with StudyStore(tmp_path, "America/Toronto") as store:
        before = database_rows(store)
        with pytest.raises((TypeError, ValueError)):
            reviews_today(store, NOW.replace(tzinfo=None))
        assert database_rows(store) == before


@pytest.mark.parametrize("when,hours", [
    (datetime(2026, 3, 8, 12, tzinfo=UTC), 23),
    (datetime(2026, 11, 1, 12, tzinfo=UTC), 25),
])
def test_local_day_counts_include_midnight_exclude_previous_and_future_dst(tmp_path, when, hours):
    with StudyStore(tmp_path, "America/Toronto") as store:
        start, end = map(datetime.fromisoformat, store._day_bounds(when))
        assert end - start == timedelta(hours=hours)
        entries = [("previous", start - timedelta(microseconds=1)), ("midnight", start),
                   ("current", when), ("future", when + timedelta(seconds=1)), ("nextday", end)]
        for name, at in entries:
            store.record_review(name, "kana_choice", "hiragana", 1, now=at)
        before = database_rows(store)
        actual_start, counts = reviews_today(store, when)
        assert actual_start == start
        assert counts == {store.get_card(name, "kana_choice", "hiragana")["card_id"]: 1
                          for name in ("midnight", "current")}
        assert database_rows(store) == before


def test_fall_back_same_local_day_does_not_make_24_hour_old_step_urgent(tmp_path):
    # Toronto's 2026-11-01 is 25 hours; these are both that local date.
    last = datetime(2026, 11, 1, 4, 1, tzinfo=UTC)
    now = datetime(2026, 11, 2, 4, 1, tzinfo=UTC)
    saved = card(last, last + timedelta(minutes=10))
    with StudyStore(tmp_path, "America/Toronto") as store:
        start, _ = reviews_today(store, now)
        assert start == datetime(2026, 11, 1, 4, tzinfo=UTC)
        assert review_priority(saved, now=now, day_start=start, reviewed_today=1)[0] == 3
        assert review_priority(saved, now=now-timedelta(seconds=1), day_start=start,
                               reviewed_today=1)[0] == 2
        assert review_priority(saved, now=now+timedelta(minutes=30), day_start=start,
                               reviewed_today=1)[0] == 3


def test_course_counts_share_ability_across_types_and_exclude_ungraded_unresolved(tmp_path):
    with StudyStore(tmp_path, "UTC") as store:
        learning = LearningStore(store)
        cid = "grammar:recovery"
        learning.enroll(item(cid), now=NOW)
        at = ready(learning, cid, NOW)
        unit_id = learning.get_unit(cid, "understanding")["unit_id"]
        assert reviews_today(store, at, course=True)[1] == {}
        for i, kind in enumerate(("grammar_meaning", "grammar_text")):
            q = question(cid, f"review-{i}", kind=kind, input_mode="text" if i else "choice")
            learning.append_questions(q, now=at)
            learning.submit(q["id"], "wrong", False, now=at)
            # A submitted answer is frozen feedback, not yet a review event.
            assert reviews_today(store, at, course=True)[1].get(unit_id, 0) == i
            result = learning.finalize(q["id"], 1, now=at)
            assert learning.finalize(q["id"], 4, now=at) == result
            assert reviews_today(store, at, course=True)[1] == {unit_id: i + 1}
            at = datetime.fromisoformat(result["due_at_utc"]) + timedelta(seconds=61)
        learning.append_questions(question(cid, "unresolved"), now=at)
        learning.submit("unresolved", "", None, outcome="device_failure", now=at)
        assert not learning.finalize("unresolved", now=at)["scheduled"]
        assert len(learning.history()) == 3
        before = database_rows(store)
        for _ in range(3):
            start, counts = reviews_today(store, at, course=True)
            assert counts == {unit_id: 2}
            unit = learning.get_unit(cid, "understanding")
            assert review_priority(unit, now=at, day_start=start,
                                   reviewed_today=counts[unit_id])[0] == 3
        assert database_rows(store) == before
        future = at + timedelta(minutes=2)
        learning.append_questions(question(cid, "future-primary"), now=future)
        learning.submit("future-primary", "wrong", False, now=future)
        learning.finalize("future-primary", 1, now=future)
        before = database_rows(store)
        assert reviews_today(store, at, course=True)[1] == {unit_id: 2}
        assert reviews_today(store, future, course=True)[1] == {unit_id: 3}
        assert database_rows(store) == before


def test_auxiliary_word_and_failed_device_do_not_spend_primary_priority(tmp_path):
    with StudyStore(tmp_path, "UTC") as store:
        LearningStore(store)
        store.record_review("kana-304b", "kana_choice", "hiragana", 1, now=NOW)
        sid, q = begin(store)
        answer_word(store, ContextContent(), sid, q, now=NOW)
        failed = store.record_review("kana-304b", "kana_handwriting", "hiragana", None,
                                     recognition_ok=False, now=NOW)
        assert not failed["scheduled"]
        assert store._db.execute("SELECT COUNT(*) FROM word_spelling_attempt").fetchone()[0] == 1
        assert store._db.execute("SELECT COUNT(*) FROM practice_attempt").fetchone()[0] == 1
        before = database_rows(store)
        current = store.get_card("kana-304b", "kana_choice", "hiragana")
        at = datetime.fromisoformat(current["due_at_utc"]) + timedelta(seconds=1)
        assert reviews_today(store, at)[1] == {current["card_id"]: 1}
        assert reviews_today(store, at, course=True)[1] == {}
        assert stored_review_priority(store, current, now=at)[0] == 2
        assert database_rows(store) == before
