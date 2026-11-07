"""Long-horizon contracts using actual stores and independently replayed FSRS."""
from datetime import timedelta
import json
import math

from fsrs import Card, Rating, Scheduler
import pytest

from support.learning_store_fixtures import START, item, question, ready
from kanamemory.domain import StudyStore
from kanamemory.learning_store import LearningStore, TABLES, validate_backup_rows
from kanamemory.learning_dependencies import independent_ready_ids, prerequisite_status


def table_rows(store):
    return {table: [tuple(row) for row in store._db.execute("SELECT * FROM " + table + " ORDER BY rowid")]
            for table in TABLES}


@pytest.mark.slow
def test_four_year_irregular_reviews_match_actual_observation_oracle(tmp_path):
    """This is an implementation contract, not a behavioral-learning model."""
    with StudyStore(tmp_path / "four-year", time_zone="UTC") as store:
        learning = LearningStore(store)
        ids = ["synthetic:stable", "synthetic:variable", "synthetic:fragile"]
        for cid in ids:
            learning.enroll(item(cid, abilities=["understanding"]), now=START)
            ready(learning, cid)
        oracle = Scheduler.from_dict(store.scheduler.to_dict())
        cards, counts = {}, dict.fromkeys(ids, 0)
        next_questions = {}
        for cid in ids:
            qid = cid + ":0"
            learning.append_questions(question(cid, qid), now=START)
            next_questions[cid] = qid
        long_gaps = [(150, 180), (390, 435), (650, 740), (1030, 1150)]
        active_dates = []
        for day in range(1461):
            if any(a <= day < b for a, b in long_gaps) or (day >= 20 and day % 29 in (0, 1, 2)):
                continue
            active_dates.append(day)
            for hour in (1, 6, 11):
                at = START + timedelta(days=day, hours=hour)
                for cid in ids:
                    unit = learning.get_unit(cid, "understanding")
                    if unit["due_at_utc"] and Card.from_dict(unit["scheduler_state"]).due > at:
                        continue
                    n = counts[cid]
                    rating = ((3, 4, 3)[n % 3] if cid.endswith("stable") else
                              (3, 1, 2, 3, 4)[n % 5] if cid.endswith("variable") else
                              (1, 1, 1, 3)[n % 4])
                    qid = next_questions[cid]
                    card = cards.get(cid) or Card(card_id=int(unit["unit_id"][:15], 16), due=at)
                    expected, review = oracle.review_card(card, Rating(rating), review_datetime=at,
                                                         review_duration=18000)
                    learning.submit(qid, "correct" if rating > 1 else "wrong", rating > 1,
                                    now=at, duration_ms=18000)
                    saved = learning.finalize(qid, rating, now=at+timedelta(seconds=2))
                    cards[cid], counts[cid] = expected, n + 1
                    current = learning.get_unit(cid, "understanding")
                    assert current["scheduler_state"] == expected.to_dict()
                    assert current["review_count"] == counts[cid]
                    assert expected.due > at
                    assert math.isfinite(expected.stability) and expected.stability > 0
                    assert math.isfinite(expected.difficulty) and 1 <= expected.difficulty <= 10
                    assert saved["due_at_utc"] == expected.due.isoformat()
                    if n % 31 == 0:
                        assert learning.finalize(qid, 4, now=at+timedelta(days=100)) == saved
                        assert learning.get_unit(cid, "understanding") == current
                    qid = cid + ":" + str(counts[cid])
                    learning.append_questions(question(cid, qid), now=at+timedelta(seconds=2))
                    next_questions[cid] = qid
            if day % 137 == 0:
                before = table_rows(store)
                learning.current(now=START+timedelta(days=day, hours=12))
                learning.counts(now=START+timedelta(days=day, hours=12))
                assert table_rows(store) == before
                store.import_backup(store.export_backup())
                learning = LearningStore(store)
                assert table_rows(store) == before
        assert active_dates[-1] == 1460
        assert sum(counts.values()) > 2000
        assert counts[ids[2]] > counts[ids[0]] * 5
        assert store._db.execute("SELECT COUNT(*) FROM learning_event").fetchone()[0] == sum(counts.values())
        assert len({row[0] for row in store._db.execute("SELECT question_id FROM learning_event")}) == sum(counts.values())
        validate_backup_rows(store._db)


def test_months_away_do_not_reset_draft_due_or_foundation(tmp_path):
    with StudyStore(tmp_path / "away", time_zone="UTC") as store:
        learning = LearningStore(store)
        base, dependent = "synthetic:base", "synthetic:advanced"
        learning.enroll([item(base, abilities=["understanding"]),
                         item(dependent, abilities=["understanding"], depends_on=[base])], now=START)
        learning.append_questions([question(base, "base-first"), question(dependent, "advanced-first")], now=START)
        at = ready(learning, base)
        assert learning.prerequisite_status(dependent)["blocked"]
        learning.submit("base-first", "yes", True, now=at)
        learning.finalize("base-first", 4, now=at)
        assert not learning.prerequisite_status(dependent)["blocked"]
        ready(learning, dependent, at)
        learning.save_draft("advanced-first", {"text": "unfinished"})
        learning.append_questions(question(base, "base-return"), now=at)
        prior = table_rows(store)
        original = learning.get_question("advanced-first")
        return_at = START + timedelta(days=400)
        assert learning.current(now=return_at)["question"]["id"] == "advanced-first"
        assert table_rows(store) == prior
        learning.set_suspended(base, "understanding", True)
        assert not learning.prerequisite_status(dependent)["blocked"]
        learning.set_suspended(base, "understanding", False)
        learning.submit("base-return", "forgot", False, now=return_at)
        learning.finalize("base-return", 1, now=return_at)
        assert not learning.prerequisite_status(dependent)["blocked"]
        assert learning.get_question("advanced-first") == original


def test_history_index_and_batched_reads_upgrade_without_rewriting_rows(tmp_path):
    with StudyStore(tmp_path / "upgrade", time_zone="UTC") as store:
        learning = LearningStore(store)
        for n in range(6):
            cid = f"synthetic:course:{n}"
            learning.enroll(item(cid), now=START)
            at = ready(learning, cid)
            learning.append_questions(question(cid, f"first:{n}"), now=START)
            learning.submit(f"first:{n}", "yes", True, now=at)
            learning.finalize(f"first:{n}", 3, now=at)
        store._db.execute("DROP INDEX learning_events_unit")
        store._db.execute("DROP INDEX learning_ready_evidence")
        store._db.commit()
        before = table_rows(store)
        expected = [learning.get_item(f"synthetic:course:{n}") for n in range(6)]
        learning = LearningStore(store)  # Also upgrades an existing v1 database.
        assert table_rows(store) == before
        statements = []
        store._db.set_trace_callback(statements.append)
        actual = learning.list_items()
        store._db.set_trace_callback(None)
        assert actual == expected
        # The contract is one batched read per learning table. Personal content
        # overlays are point reads of app_setting and never touch learner history.
        assert len([sql for sql in statements if sql.startswith("SELECT") and "FROM app_setting" not in sql]) == 3
        plan = " ".join(row[3] for row in store._db.execute(
            "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM learning_event WHERE unit_id=?", (expected[0]["units"][0]["unit_id"],)))
        assert "USING COVERING INDEX learning_events_unit" in plan
        # This tiny database has several equally selective primary-event
        # indexes. Verify the eligible index was actually restored and can
        # serve the exact readiness predicate, without freezing SQLite's tie.
        index_sql = store._db.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
                                     ('learning_ready_evidence',)).fetchone()[0]
        assert ''.join(index_sql.split()).endswith(
            "ONlearning_event(unit_id)WHERErole='primary'ANDoutcome='correct'ANDassisted=0ANDrating>1")
        evidence_plan = " ".join(row[3] for row in store._db.execute("""EXPLAIN QUERY PLAN
            SELECT DISTINCT u.content_id FROM learning_event e INDEXED BY learning_ready_evidence
            JOIN learning_unit u ON u.unit_id=e.unit_id
            WHERE e.role='primary' AND e.outcome='correct' AND e.assisted=0 AND e.rating>1
            AND u.ability IN ('understanding','application','reading','use')"""))
        assert "learning_ready_evidence" in evidence_plan
        assert independent_ready_ids(learning) == {row["id"] for row in expected}
        actual[0]["units"][0]["attempt_count"] = -99
        assert learning.get_item(actual[0]["id"])["units"][0]["attempt_count"] == 1
        store.import_backup(store.export_backup())
        assert LearningStore(store).list_items() == expected


def test_no_prerequisites_do_not_read_history_to_prove_empty_dependency_set(tmp_path):
    with StudyStore(tmp_path / "no-dependencies", time_zone="UTC") as store:
        learning = LearningStore(store)
        learning.enroll(item("synthetic:no-deps"), now=START)
        content = learning.get_item("synthetic:no-deps")
        statements = []
        store._db.set_trace_callback(statements.append)
        result = prerequisite_status(learning, content)
        store._db.set_trace_callback(None)
        assert result == {"ready": True, "blocked": False, "waived": False,
                          "grandfathered": False, "dependencies": [], "missing": []}
        # Personal revisions may override dependencies, so app_setting is checked;
        # no learning table is read to prove the dependency set is empty.
        assert [sql for sql in statements if "FROM app_setting" not in sql] == []
