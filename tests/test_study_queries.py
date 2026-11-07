"""Read projections preserve queue ownership, eligibility and saved records."""
from unittest.mock import Mock

import pytest

from kanamemory.domain import Question
from support.learning_store_fixtures import START, item, question, stores


def test_visible_headers_and_eligible_pending_have_different_reservation_filters(stores):
    store, learning = stores
    for suffix in "dcab":
        learning.enroll(item("grammar:" + suffix, abilities=["understanding"]), now=START)
    for suffix in "abc":
        learning.append_questions(question("grammar:" + suffix, qid=suffix,
            kind="grammar_fill" if suffix == "c" else "grammar_meaning"), now=START)
    learning.set_suspended("grammar:b", "understanding", True)
    unit = learning.get_unit("grammar:a", "understanding")["unit_id"]
    # Eligibility checks the reservation's existence, not its payload.
    store.set_setting("retention_reservation_v1:" + unit, {"test_reservation": True})
    before, changes = tuple(store._db.iterdump()), store._db.total_changes
    reader = store.queries
    assert [tuple(row) for row in reader.pending_course_headers(["grammar_meaning"])] == [
        ("grammar:a", "understanding", "intro")]
    assert list(learning.iter_pending_questions(["grammar_meaning"])) == []
    assert [row["question_id"] for row in learning.iter_pending_questions()] == ["c"]
    assert [row[0] for row in reader.unqueued_course_ids(["understanding"])] == ["grammar:d"]
    assert reader.pending_course_headers([]) == reader.unqueued_course_ids([]) == []
    assert reader.course_ids() == ("grammar:a", "grammar:b", "grammar:c", "grammar:d")
    assert reader.course_ids_page(2, 1) == ("grammar:c", "grammar:a")
    assert reader.course_count() == 4 and reader.reviewed_course_count() == 0
    assert reader.pending_abilities("grammar:b") == {"understanding"}
    assert reader.instruction_ready_ids() == set()
    assert reader.first_pending_question_id(unit) == "a"
    assert reader.latest_primary_rating(unit) is None
    assert reader.course_row("grammar:a")["stage"] == "intro"
    # The original draft lookup has no ordering contract; all saved IDs remain.
    assert sorted(row[0] for row in reader.unanswered_course_rows()) == ["a", "b", "c"]
    assert [row["content_id"] for row in reader.diagnostic_scope_rows()] == ["grammar:a", "grammar:c", "grammar:d"]
    assert reader.recent_evidence_rows() == []
    assert set(reader.unfinished_course_ids()) == set(reader.course_ids())
    assert len(reader.course_progress_rows()) == 4
    assert reader.active_review_units() == []
    assert reader.reviewed_kana_rows() == []
    assert tuple(store._db.iterdump()) == before
    assert store._db.total_changes == changes


def test_session_headers_keep_completed_prefix_and_disabled_kinds_without_hydration(stores, monkeypatch):
    store, _ = stores
    questions = [Question("kana-a", "kana_choice", "hiragana", "あ", "a", ["a", "i"]),
                 Question("kana-ki", "romaji_handwriting", "katakana", "ki", "キ", []),
                 Question("word-cat", "word_kana", "hiragana", "", "ねこ", ["ね", "こ"])]
    sid = store.start_session(questions, now=START)
    store.save_session_progress(sid, 1, 1, state={"word_answer": "き"})
    store.set_setting("practice_preferences", {"enabled": ["kana_choice"]})
    store._session_snapshot(sid)  # Already read by the containing route action.
    before, changes = tuple(store._db.iterdump()), store._db.total_changes
    monkeypatch.setattr(store, "get_session_questions", Mock(side_effect=AssertionError("No hydration")))
    monkeypatch.setattr(store._snapshot_reader, "_parse", Mock(side_effect=AssertionError("No redecoding")))
    headers = store.session_question_headers(sid)
    assert headers == (("kana-a", "kana_choice", "hiragana"),
                       ("kana-ki", "romaji_handwriting", "katakana"),
                       ("word-cat", "word_kana", "hiragana"))
    assert tuple(store._db.iterdump()) == before
    assert store._db.total_changes == changes


def test_readers_see_transactional_enrollment_and_do_not_commit_it(stores):
    store, learning = stores
    learning.enroll(item("grammar:existing", abilities=["understanding"]), now=START)
    before = tuple(store._db.iterdump())
    reader, teaching = store.queries, store.teaching_queries
    with pytest.raises(RuntimeError, match="rollback boundary"):
        with store._db:
            store._db.execute("BEGIN IMMEDIATE")
            store._db.execute("UPDATE learning_content SET stage='guide' WHERE content_id='grammar:existing'")
            changes = store._db.total_changes
            trace = []
            store._db.set_trace_callback(trace.append)
            try:
                header = reader.course_introduction_header("grammar:existing")
                assert header["stage"] == "guide" and store.in_transaction
                entry = {"kind": "learning", "content_id": "grammar:existing",
                         "introduction_id": header["introduction_id"]}
                assert teaching.explicit_entry_alive(entry)
                assert reader.course_suspensions("grammar:existing") == {"understanding": 0}
                assert reader.pending_course_modes("grammar:existing") == set()
                assert store._db.total_changes == changes
                assert all(sql.lstrip().upper().startswith("SELECT") for sql in trace)
            finally:
                store._db.set_trace_callback(None)
            raise RuntimeError("rollback boundary")
    assert reader.course_introduction_header("grammar:existing")["stage"] == "intro"
    assert not store.in_transaction
    assert tuple(store._db.iterdump()) == before
