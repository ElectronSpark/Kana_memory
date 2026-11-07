"""Fast planning/checkpoints retain the actual selection and recovery contracts."""
from record_test_support import decode_current, encode_current
from runtime_record_test_support import snapshot_json, write_snapshot
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys

import pytest

from support.session_snapshots import unrouted

from kanamemory import domain
from kanamemory.domain import Question, StudyStore, build_practice_batch, practice_plan_counts
from kanamemory.kana_routing import ROUTE_KEY
from support.domain_support import SmallContent

NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
KANA = ("kana_choice", "romaji_kana", "kana_romaji", "romaji_kana_full", "romaji_handwriting", "kana_handwriting")
WORDS = ("word_kana", "word_meaning_kana")


@pytest.mark.parametrize("kinds,script,adaptive", [
    (KANA, "hiragana", False), (WORDS, "katakana", False),
    (KANA, "both", True), ((*KANA, *WORDS), "both", True),
    (("romaji_handwriting",), "both", True), (("kana_choice",), "hiragana", False),
])
@pytest.mark.parametrize("schedule_mode,new_only", [("spaced", False), ("reinforce", False), ("reinforce", True)])
@pytest.mark.parametrize("reviewed", [False, True])
def test_count_preview_exactly_matches_generation(tmp_path, monkeypatch, kinds, script, adaptive,
                                                  schedule_mode, new_only, reviewed):
    with StudyStore(tmp_path) as store:
        if reviewed:
            for kind, when, rating in [("kana_choice", NOW - timedelta(days=2), 4),
                                       ("romaji_kana", NOW - timedelta(minutes=2), 1)]:
                store.record_review("kana-0", kind, "hiragana", rating,
                                    outcome="correct", now=when)
            store.set_content_control("kana-1", "suspend")
            store.set_content_control("kana-2", "bury", now=NOW)
        config = dict(kinds=kinds, script=script, adaptive=adaptive, schedule_mode=schedule_mode,
                      rounds=1 if schedule_mode == "spaced" else 3, limit=7,
                      seed=19, now=NOW, new_only=new_only, include_research=True,
                      handwriting_available=False, audio_available=True)
        questions = build_practice_batch(SmallContent(), store, **config)
        expected = {**store.batch_counts(questions, now=NOW), "kind_counts": {
            kind: sum(question.kind == kind for question in questions)
            for kind in kinds if any(question.kind == kind for question in questions)}}
        before = decode_current(store.export_backup())["tables"]
        def forbidden(*args, **kwargs):
            raise AssertionError("Preview must not construct or serialize exercises")
        monkeypatch.setattr(domain, "Question", forbidden)
        monkeypatch.setattr(domain, "_kana_choices", forbidden)
        monkeypatch.setattr(domain, "_tokens", forbidden)
        monkeypatch.setattr(domain, "romanize_kana", forbidden)
        monkeypatch.setattr(store, "previous_choice_options", forbidden)
        assert practice_plan_counts(SmallContent(), store, **config) == expected
        assert decode_current(store.export_backup())["tables"] == before


def questions():
    return [Question(str(index % 20), "kana_choice", "hiragana", "あ", "a", ["a", "ka"],
                     metadata={"practice_config": {"kinds": ["kana_choice"]}})
            for index in range(200)]


def test_repeated_drafts_and_ready_queue_do_not_read_or_rewrite_snapshot(tmp_path):
    with StudyStore(tmp_path) as store:
        sid = store.start_session(questions())
        store.save_session_progress(sid, 0)
        trace = []
        store._db.set_trace_callback(trace.append)
        for text in ("a", "ab", "abc"):
            store.save_session_progress(sid, 0, state={"romaji_answer": text})
        store.save_session_progress(sid, 0)
        pending = store.prepare_next_question(sid, now=NOW)
        assert unrouted(pending["questions"]) == questions()
        assert not any("snapshot_json" in sql for sql in trace)
        assert store.stats()["total_reviews"] == 0


def test_snapshot_cache_is_private_and_invalidates_for_same_and_other_connection(tmp_path):
    with StudyStore(tmp_path) as store:
        sid = store.start_session(questions())
        original = store.get_session_questions(sid)
        pristine = deepcopy(original)  # Includes current enrollment bindings.
        original[0].options.append("wrong")
        original[0].metadata["practice_config"]["kinds"].append("wrong")
        assert store.get_session_questions(sid) == pristine
        replacement = questions()[:3]
        with store._db:
            write_snapshot(store._db, sid, json.dumps([q.to_dict() for q in replacement]))
        store.save_session_progress(sid, 2)
        assert len(store.load_session_progress()["questions"]) == 3
        with sqlite3.connect(store.path) as other:
            write_snapshot(other, sid, json.dumps([q.to_dict() for q in questions()]))
        store.save_session_progress(sid, 100)
        assert len(store.load_session_progress()["questions"]) == 200


def test_rolled_back_snapshot_read_cannot_poison_cache(tmp_path):
    with StudyStore(tmp_path) as store:
        sid = store.start_session(questions())
        assert len(store.get_session_questions(sid)) == 200
        for count in (3, 5):
            store._db.execute("BEGIN")
            write_snapshot(store._db, sid, json.dumps([q.to_dict() for q in questions()[:count]]))
            assert len(store.get_session_questions(sid)) == count
            store._db.rollback()
        assert len(store.get_session_questions(sid)) == 200


def test_worker_prepared_session_does_not_write_until_accepted(tmp_path, monkeypatch):
    with StudyStore(tmp_path) as store:
        # kana_handwriting has no route, so its prepared text must be used as is.
        expected = [*questions(), Question("0", "kana_handwriting", "hiragana", "あ", "あ",
                                           metadata={"practice_config": {"kinds": ["kana_choice"]}})]
        prepared = domain.prepare_session_snapshot(expected, seed=19)
        assert store._db.execute("SELECT COUNT(*) FROM study_session").fetchone()[0] == 0
        encode = json.dumps
        def only_route_encoding(value, *args, **kwargs):
            # Routing encodes ability identities, its setting and each newly
            # stamped question copy (2c44345). Nothing else is serialized again.
            if isinstance(value, list) and any(isinstance(item, dict) for item in value):
                pytest.fail("Already serialized")
            if isinstance(value, dict) and "content_id" in value and ROUTE_KEY not in value["metadata"]:
                pytest.fail("Already serialized")
            return encode(value, *args, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(domain.json, "dumps", only_route_encoding)
            sid = store.start_prepared_session(prepared, now=NOW)
        assert unrouted(store.get_session_questions(sid)) == expected
        store.save_session_progress(sid, 1, 1)
        with StudyStore(tmp_path / "restored") as restored:
            restored.import_backup(store.export_backup())
            assert restored.load_session_progress() == store.load_session_progress()
