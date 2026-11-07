"""Derived append cache must equal the retained SQL replay after every mutation."""
from datetime import timedelta
import json
import random
import sqlite3
from uuid import uuid4

import pytest

from support.word_romaji_lanes_support import NOW
from support.word_romaji_lanes_support import KA
from support.word_romaji_lanes_support import KI
from support.word_romaji_lanes_support import question
from support.word_romaji_lanes_support import submit
from kanamemory import domain
from kanamemory.domain import StudyStore


@pytest.fixture
def store(tmp_path):
    with StudyStore(tmp_path / "learner", "UTC") as value:
        yield value


def primary(store, when=NOW, cid=KA, script="hiragana", rating=3, assisted=False):
    return store.record_review(cid, "kana_choice", script, rating, now=when,
        outcome="incorrect" if rating == 1 else "correct", assisted=assisted)


def compare(store, when):
    actual = {}
    for script in ("hiragana", "katakana"):
        actual[script] = domain._adaptive_stages(store, script, when)
        assert actual[script] == domain._adaptive_stages_uncached(store, script, when)
    return actual


def clone(db, table, *, rowid=None, **changes):
    """Fixture-only historical import, including deliberate out-of-order rows."""
    row = dict(db.execute("SELECT * FROM " + table + " ORDER BY rowid LIMIT 1").fetchone())
    row["event_id" if table == "review_event" else "attempt_id"] = str(uuid4())
    if table == "word_spelling_attempt":
        row["question_index"] = db.execute("SELECT MAX(question_index)+1 FROM word_spelling_attempt").fetchone()[0]
    row.update(changes)
    if rowid is not None:
        row["rowid"] = rowid
    db.execute("INSERT INTO " + table + " (" + ",".join(row) + ") VALUES (" + ",".join("?" for _ in row) + ")", tuple(row.values()))


def main_rows(store):
    return {r[0]: [tuple(x) for x in store._db.execute('SELECT * FROM "' + r[0] + '" ORDER BY rowid')]
        for r in store._db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}


def test_append_only_reads_and_folds_only_delta_and_returns_independent_results(store, monkeypatch):
    for i in range(12):
        primary(store, NOW + timedelta(days=i))
    before = main_rows(store)
    assert compare(store, NOW + timedelta(days=11))["hiragana"] == {KA: 2}
    queries, folds = [], []
    original_rows, original_fold = domain._adaptive_event_rows, domain._advance_adaptive_evidence

    def read(db, script, cursors=None):
        rows = original_rows(db, script, cursors)
        queries.append((script, cursors, len(rows)))
        return rows

    def fold(evidence, event):
        folds.append(event["content_id"])
        original_fold(evidence, event)

    monkeypatch.setattr(domain, "_adaptive_event_rows", read)
    monkeypatch.setattr(domain, "_advance_adaptive_evidence", fold)
    result = domain._adaptive_stages(store, "hiragana", NOW + timedelta(days=12))
    result.clear()
    assert compare(store, NOW + timedelta(days=12))["hiragana"] == {KA: 2}
    assert queries == folds == []
    assert main_rows(store) == before
    primary(store, NOW + timedelta(days=12), cid=KI)
    compare(store, NOW + timedelta(days=12))
    assert all(cursors is not None for _, cursors, _ in queries)
    assert sum(size for _, _, size in queries) == len(folds) == 1


def test_future_events_and_time_rewind_are_never_double_counted(store):
    primary(store)
    primary(store, NOW + timedelta(days=3))
    assert compare(store, NOW)["hiragana"] == {KA: 0}
    with store._db:
        clone(store._db, "review_event", occurred_at_utc=(NOW + timedelta(days=2)).isoformat())
    assert compare(store, NOW + timedelta(days=1))["hiragana"] == {KA: 0}
    assert compare(store, NOW + timedelta(days=2))["hiragana"] == {KA: 2}
    compare(store, NOW + timedelta(days=3))
    assert compare(store, NOW)["hiragana"] == {KA: 0}
    compare(store, NOW + timedelta(days=4))
    with store._db:
        clone(store._db, "review_event", occurred_at_utc=(NOW + timedelta(days=1)).isoformat(), rating=1, outcome="incorrect")
    assert compare(store, NOW + timedelta(days=4))["hiragana"] == {KA: 2}


def test_equal_timestamp_source_order_and_low_weight_are_preserved(store):
    primary(store, NOW - timedelta(days=2))
    primary(store, NOW - timedelta(days=1))
    submit(store, question(answer="か"), answer="き", now=NOW)
    assert compare(store, NOW)["hiragana"][KA] == 0
    # New primary must precede an already consumed auxiliary at the same time.
    primary(store, NOW)
    assert compare(store, NOW)["hiragana"][KA] == 2
    submit(store, question(answer="か"), answer="か", now=NOW)
    compare(store, NOW)
    primary(store, NOW, rating=1)
    assert compare(store, NOW)["hiragana"][KA] == 0


def test_backfilled_primary_lapse_sorts_before_eight_same_time_auxiliary_successes(store):
    primary(store, NOW - timedelta(days=2))
    for _ in range(8):
        submit(store, question(answer="か"), now=NOW)
    assert compare(store, NOW)["hiragana"][KA] == 2
    primary(store, NOW, rating=1)
    # All auxiliary events sort after this lapse, despite having lower rowids.
    # Applying the lapse to the cached suffix would incorrectly return zero.
    assert compare(store, NOW)["hiragana"][KA] == 1


@pytest.mark.parametrize("table", ["review_event", "word_spelling_attempt"])
def test_update_delete_reuse_lower_rowid_and_replace_invalidate(store, table):
    primary(store)
    primary(store, NOW + timedelta(days=1))
    submit(store, question(answer="か"), now=NOW + timedelta(days=2))
    compare(store, NOW + timedelta(days=3))
    with store._db:
        if table == "review_event":
            store._db.execute("UPDATE review_event SET rating=1,outcome='incorrect' WHERE rowid=2")
        else:
            store._db.execute("UPDATE word_spelling_attempt SET assisted=1 WHERE rowid=1")
    compare(store, NOW + timedelta(days=3))
    with store._db:
        clone(store._db, table, rowid=-10)
    compare(store, NOW + timedelta(days=3))
    # REPLACE's implicit deletion need not fire delete triggers (SQLite default).
    row = dict(store._db.execute("SELECT * FROM " + table + " WHERE rowid=-10").fetchone())
    row["occurred_at_utc"] = (NOW - timedelta(days=2)).isoformat()
    with store._db:
        store._db.execute("INSERT OR REPLACE INTO " + table + " VALUES (" + ",".join("?" for _ in row) + ")", tuple(row.values()))
    compare(store, NOW + timedelta(days=3))
    with store._db:
        store._db.execute("DELETE FROM " + table + " WHERE rowid=(SELECT MAX(rowid) FROM " + table + ")")
    compare(store, NOW + timedelta(days=3))
    with store._db:
        clone(store._db, table)
    compare(store, NOW + timedelta(days=3))


def test_card_identity_changes_are_reflected_without_touching_scheduler_state(store):
    primary(store)
    primary(store, NOW + timedelta(days=1))
    compare(store, NOW + timedelta(days=1))
    original = store.get_card(KA, "kana_choice", "hiragana")
    with store._db:
        store._db.execute("UPDATE study_card SET content_id=?,script='katakana'", (KI,))
    assert compare(store, NOW + timedelta(days=1)) == {"hiragana": {}, "katakana": {KI: 2}}
    assert store.get_card(KI, "kana_choice", "katakana")["scheduler_state_json"] == original["scheduler_state_json"]


def test_card_replace_and_orphan_reattachment_rebuild_script_history(store):
    primary(store)
    primary(store, NOW + timedelta(days=1))
    compare(store, NOW + timedelta(days=2))
    row = dict(store._db.execute("SELECT * FROM study_card").fetchone())
    row.update(content_id=KI, script="katakana")
    with store._db:
        store._db.execute("INSERT OR REPLACE INTO study_card VALUES (" + ",".join("?" for _ in row) + ")", tuple(row.values()))
    assert compare(store, NOW + timedelta(days=2)) == {"hiragana": {}, "katakana": {KI: 2}}
    # An imported orphan is ignored by the original JOIN until its card exists.
    store._db.execute("PRAGMA foreign_keys=OFF")
    with store._db:
        store._db.execute("DELETE FROM study_card")
    assert compare(store, NOW + timedelta(days=2)) == {"hiragana": {}, "katakana": {}}
    with store._db:
        store._db.execute("INSERT INTO study_card VALUES (" + ",".join("?" for _ in row) + ")", tuple(row.values()))
    store._db.execute("PRAGMA foreign_keys=ON")
    assert compare(store, NOW + timedelta(days=2))["katakana"] == {KI: 2}


def test_replace_same_rowid_and_changed_auxiliary_json_are_not_missed(store):
    primary(store)
    primary(store, NOW + timedelta(days=1))
    submit(store, question(answer="か"), answer="き", now=NOW + timedelta(days=2))
    compare(store, NOW + timedelta(days=3))
    row = dict(store._db.execute("SELECT * FROM review_event WHERE rowid=2").fetchone())
    row.update(event_id=str(uuid4()), rating=1, outcome="incorrect", rowid=2)
    with store._db:
        store._db.execute("INSERT OR REPLACE INTO review_event (" + ",".join(row) + ") VALUES (" + ",".join("?" for _ in row) + ")", tuple(row.values()))
    assert compare(store, NOW + timedelta(days=3))["hiragana"][KA] == 0
    with store._db:
        store._db.execute("UPDATE word_spelling_attempt SET evidence_json='invalid json'")
    compare(store, NOW + timedelta(days=3))


def test_timezone_text_cutoffs_and_same_time_ordinals_match_original_sql(store):
    primary(store)
    with store._db:
        for offset in ("2026-10-23T10:00:00-04:00", "2026-10-23T12:00:00+00:00", "2026-10-24T12:00:00+02:00"):
            clone(store._db, "review_event", occurred_at_utc=offset)
    for hour in (0, 2, 7, 26, 1, 24, 48):
        compare(store, NOW + timedelta(hours=hour))


def test_changed_weight_rebuilds_evidence_instead_of_mixing_policies(store, monkeypatch):
    for _ in range(4):
        submit(store, question(answer="か"), now=NOW)
    assert compare(store, NOW)["hiragana"][KA] == 0
    monkeypatch.setattr(domain, "WORD_ROMAJI_WEIGHT", .5)
    assert compare(store, NOW)["hiragana"][KA] == 1


def test_transactions_bypass_cache_commit_and_rollback_are_safe(store):
    primary(store)
    primary(store, NOW + timedelta(days=1))
    compare(store, NOW + timedelta(days=1))
    cached = store._adaptive_evidence_cache.copy()
    store._db.execute("BEGIN")
    store._db.execute("UPDATE review_event SET rating=1,outcome='incorrect'")
    assert compare(store, NOW + timedelta(days=2))["hiragana"] == {KA: 0}
    assert store._adaptive_evidence_cache == cached
    store._db.rollback()
    assert compare(store, NOW + timedelta(days=2))["hiragana"] == {KA: 2}
    store._db.execute("BEGIN")
    clone(store._db, "review_event", occurred_at_utc=(NOW + timedelta(days=2)).isoformat(), rating=1, outcome="incorrect")
    compare(store, NOW + timedelta(days=2))
    store._db.commit()
    assert compare(store, NOW + timedelta(days=2))["hiragana"] == {KA: 0}


def test_first_read_inside_transaction_does_not_commit_or_install_temp_monitor(store):
    primary(store)
    store._db.execute("BEGIN")
    clone(store._db, "review_event")
    assert compare(store, NOW)["hiragana"] == {KA: 1}
    assert store._db.in_transaction
    assert not getattr(store, "_adaptive_monitor_ready", False)
    store._db.rollback()
    assert compare(store, NOW)["hiragana"] == {KA: 0}


def test_external_connection_and_concurrent_read_invalidation(store, monkeypatch):
    primary(store)
    primary(store, NOW + timedelta(days=1))
    compare(store, NOW + timedelta(days=1))
    with sqlite3.connect(store.path) as other:
        other.execute("UPDATE review_event SET rating=1,outcome='incorrect' WHERE rowid=2")
    assert compare(store, NOW + timedelta(days=2))["hiragana"] == {KA: 0}
    original = domain._adaptive_event_rows
    fired = []

    def racing_read(db, script, cursors=None):
        rows = original(db, script, cursors)
        if not fired:
            fired.append(True)
            with sqlite3.connect(store.path) as other:
                other.execute("UPDATE review_event SET rating=3,outcome='correct' WHERE rowid=2")
        return rows

    with store._db:
        clone(store._db, "review_event", occurred_at_utc=(NOW + timedelta(days=2)).isoformat())
    monkeypatch.setattr(domain, "_adaptive_event_rows", racing_read)
    assert compare(store, NOW + timedelta(days=2))["hiragana"] == {KA: 2}
    assert fired


def test_query_only_fallback_leaves_no_transaction_or_main_schema_changes(store):
    primary(store)
    before = main_rows(store)
    store._db.execute("PRAGMA query_only=ON")
    assert compare(store, NOW)["hiragana"] == {KA: 0}
    assert not store._db.in_transaction
    assert main_rows(store) == before
    store._db.execute("PRAGMA query_only=OFF")
    assert compare(store, NOW)["hiragana"] == {KA: 0}
    assert store._adaptive_monitor_ready


def test_same_connection_vacuum_replays_before_reusing_rowid_cursors(store, monkeypatch):
    primary(store)
    primary(store, NOW + timedelta(days=1))
    compare(store, NOW + timedelta(days=1))
    original = domain._adaptive_event_rows
    cursors_used = []

    def read(db, script, cursors=None):
        cursors_used.append(cursors)
        return original(db, script, cursors)

    monkeypatch.setattr(domain, "_adaptive_event_rows", read)
    store._db.execute("VACUUM")
    assert compare(store, NOW + timedelta(days=2))["hiragana"] == {KA: 2}
    assert cursors_used == [None, None]
    primary(store, NOW + timedelta(days=3))
    compare(store, NOW + timedelta(days=3))


def test_backup_restart_restore_and_learning_reset_rebuild_derived_cache(store, tmp_path):
    primary(store)
    primary(store, NOW + timedelta(days=1))
    submit(store, question(answer="か"), answer="き", now=NOW + timedelta(days=2))
    expected = compare(store, NOW + timedelta(days=3))
    backup = store.export_backup()
    assert "adaptive_evidence" not in backup.decode()
    with StudyStore(tmp_path / "restored", "UTC") as restored:
        restored.import_backup(backup)
        assert compare(restored, NOW + timedelta(days=3)) == expected
        restored.clear_learning_data()
        assert compare(restored, NOW + timedelta(days=4)) == {"hiragana": {}, "katakana": {}}
        restored.import_backup(backup)
        assert compare(restored, NOW + timedelta(days=4)) == expected
    with StudyStore(tmp_path / "restored", "UTC") as restarted:
        assert compare(restarted, NOW + timedelta(days=4)) == expected


@pytest.mark.parametrize("seed", [7, 40, 129])
def test_random_mixed_history_matches_full_replay_at_every_step(store, seed):
    rng = random.Random(seed)
    primary(store, NOW - timedelta(days=1))
    primary(store, NOW, cid=KI, script="katakana")
    submit(store, question(answer="かき"), now=NOW)
    clock = NOW
    for i in range(100):
        operation = rng.randrange(8)
        table = rng.choice(("review_event", "word_spelling_attempt"))
        clock += timedelta(minutes=rng.randrange(0, 3000))
        if operation < 3:
            when = clock + timedelta(days=rng.choice((-4, 0, 0, 0, 2)))
            with store._db:
                changes = {"occurred_at_utc": when.isoformat()}
                if table == "review_event":
                    changes.update(rating=rng.randint(1, 4), assisted=rng.randrange(2), outcome=rng.choice(("correct", "self_rated", "incorrect")))
                clone(store._db, table, **changes)
        elif operation == 3:
            submit(store, question(answer="かき", drill=rng.choice((True, False))),
                answer=rng.choice(("かき", "かか", "き")), assisted=rng.random() < .1, now=clock)
        elif operation in (4, 5):
            rowids = [r[0] for r in store._db.execute("SELECT rowid FROM " + table)]
            rowid = rng.choice(rowids)
            with store._db:
                if operation == 4:
                    store._db.execute("UPDATE " + table + " SET assisted=1-assisted WHERE rowid=?", (rowid,))
                elif len(rowids) > 1:
                    store._db.execute("DELETE FROM " + table + " WHERE rowid=?", (rowid,))
        elif operation == 6:
            store._db.execute("BEGIN")
            store._db.execute("UPDATE " + table + " SET assisted=1-assisted")
            compare(store, clock)
            store._db.rollback()
        else:
            compare(store, clock - timedelta(days=rng.randrange(10)))
        compare(store, clock)
