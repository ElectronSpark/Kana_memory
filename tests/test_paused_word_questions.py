"""Disabling an auxiliary question kind parks its work without a false review."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import sys

import pytest

from kanamemory.domain import Question, StudyStore, WORD_KIND_PAUSE
from kanamemory.practice_availability import summarize_queue_availability
from kanamemory.progress_backup import _questions
from support.word_context_memory_support import ContextContent
from support.word_context_memory_support import NOW
from support.word_context_memory_support import KA
from support.word_context_memory_support import KI
from support.word_context_memory_support import KU
from support.word_context_memory_support import word_question


from support.paused_word_questions_support import KINDS, DRAFT, FEEDBACK, start, evidence, reconcile


def kana(identity=KU):
    return Question(identity, "kana_choice", "hiragana", "く", "ku", ["ku", "ka"])


@pytest.mark.parametrize("lazy", [False, True])
@pytest.mark.parametrize("state", [DRAFT, FEEDBACK])
def test_single_word_pause_restart_and_reenable_preserves_exact_question_and_state(tmp_path, lazy, state):
    location = tmp_path / "learner"
    with StudyStore(location, "UTC") as store:
        sid, q = start(store, state=state)
        original_snapshot = deepcopy(store._session_snapshot(sid))
        before = evidence(store)
        disabled = reconcile(store, sid, ("kana_choice",), lazy=lazy)
        assert not disabled["completed"]
        parked = store.prepare_next_question(sid, now=NOW, lazy_questions=lazy)
        assert parked["settings_blocked"] and not parked["dependency_blocked"]
        assert summarize_queue_availability(store, parked, now=NOW).ready_count == 0
        assert summarize_queue_availability(store, parked, now=NOW).total_count == 0
        assert summarize_queue_availability(store, parked, allowed_kinds=KINDS, now=NOW).ready_count == 1
        assert evidence(store) == before
    with StudyStore(location, "UTC") as store:
        unchanged = list(store._db.iterdump())
        again = reconcile(store, sid, ("kana_choice",), lazy=lazy)
        assert not again["changed"]
        assert list(store._db.iterdump()) == unchanged
        enabled = reconcile(store, sid, KINDS, lazy=lazy)
        assert enabled["questions"][enabled["question_index"]].to_dict() == q.to_dict()
        assert store._session_snapshot(sid) == original_snapshot
        assert enabled["state"] == state
        assert enabled["assisted_indices"] == [0]
        assert store.prepare_next_question(sid, now=NOW, lazy_questions=lazy)["state"] == state
        assert evidence(store) == before


@pytest.mark.parametrize("lazy", [False, True])
def test_other_work_can_finish_while_word_feedback_waits_without_reattribution(tmp_path, lazy):
    with StudyStore(tmp_path / "learner", "UTC") as store:
        sid, word = start(store, state=FEEDBACK, tail=[kana()])
        before = evidence(store)
        reconcile(store, sid, ("kana_choice",), lazy=lazy)
        next_item = store.prepare_next_question(sid, now=NOW, lazy_questions=lazy)
        assert next_item["questions"][next_item["question_index"]].content_id == KU
        assert next_item["state"] == {}  # the word's frozen result never becomes the kana's result
        assert summarize_queue_availability(store, next_item, now=NOW).ready_count == 1
        assert evidence(store) == before
        store.record_review(KU, "kana_choice", "hiragana", 4, session_id=sid,
                            question_index=next_item["question_index"], outcome="correct", now=NOW)
        waiting = store.prepare_next_question(sid, now=NOW, lazy_questions=lazy)
        assert waiting["settings_blocked"]
        after_kana = evidence(store)
        active = reconcile(store, sid, KINDS, lazy=lazy)
        assert active["questions"][active["question_index"]].to_dict() == word.to_dict()
        assert active["state"] == FEEDBACK
        assert active["assisted_indices"] == [1]
        assert evidence(store) == after_kana


def test_reenable_does_not_replace_another_visible_draft(tmp_path):
    with StudyStore(tmp_path / "learner", "UTC") as store:
        sid, word = start(store, tail=[kana()])
        reconcile(store, sid, ("kana_choice",))
        current = store.prepare_next_question(sid, now=NOW)
        other_draft = {"page": "question", "romaji_answer": "k"}
        store.save_session_progress(sid, current["question_index"], state=other_draft)
        active = reconcile(store, sid, KINDS)
        assert active["questions"][active["question_index"]].content_id == KU
        assert active["state"] == other_draft
        store.record_review(KU, "kana_choice", "hiragana", 4, session_id=sid,
                            question_index=active["question_index"], outcome="correct", now=NOW)
        restored = store.prepare_next_question(sid, now=NOW)
        assert restored["questions"][restored["question_index"]].to_dict() == word.to_dict()
        assert restored["state"] == DRAFT


def test_paused_word_reserves_identity_when_appending_and_survives_backup(tmp_path):
    with StudyStore(tmp_path / "source", "UTC") as store:
        sid, word = start(store)
        reconcile(store, sid, ("kana_choice",))
        token = store.session_append_token(sid)
        unchanged = list(store._db.iterdump())
        with pytest.raises(ValueError, match="已经在练习队列"):
            store.append_session(sid, [word], expected_token=token, now=NOW)
        assert list(store._db.iterdump()) == unchanged
        store.append_session(sid, [kana()], expected_token=token, now=NOW)
        reconcile(store, sid, ("kana_choice",))
        raw = store.export_backup()
        before = evidence(store)
    with StudyStore(tmp_path / "restored", "UTC") as restored:
        restored.import_backup(raw)
        assert evidence(restored) == before
        active = reconcile(restored, sid, KINDS)
        next_item = restored.prepare_next_question(sid, now=NOW)
        if next_item["questions"][next_item["question_index"]].content_id == KU:
            restored.record_review(KU, "kana_choice", "hiragana", 4, session_id=sid,
                question_index=next_item["question_index"], outcome="correct", now=NOW)
            next_item = restored.prepare_next_question(sid, now=NOW)
        assert next_item["questions"][next_item["question_index"]].to_dict() == word.to_dict()
        assert next_item["state"] == DRAFT


def test_paused_submission_is_rejected_then_single_answer_cannot_replay(tmp_path):
    repo = ContextContent()
    with StudyStore(tmp_path / "learner", "UTC") as store:
        for identity in (KA, KI):
            store.record_review(identity, "kana_choice", "hiragana", 4,
                                outcome="correct", now=NOW - timedelta(days=1))
        sid, word = start(store, tail=[kana()])
        reconcile(store, sid, ("kana_choice",))
        before = evidence(store)
        parked_index = next(i for i, q in enumerate(store.get_session_questions(sid)) if q.content_id == word.content_id)
        with pytest.raises(ValueError, match="Resume"):
            store.record_review(word.content_id, word.kind, word.script, None, session_id=sid,
                question_index=parked_index, answered_text=word.answer, outcome="correct", kana_rows=repo.kana_list(), now=NOW)
        assert evidence(store) == before
        reconcile(store, sid, KINDS)
        current = store.prepare_next_question(sid, now=NOW)
        if current["questions"][current["question_index"]].content_id == KU:
            store.record_review(KU, "kana_choice", "hiragana", 4, session_id=sid,
                question_index=current["question_index"], outcome="correct", now=NOW)
            current = store.prepare_next_question(sid, now=NOW)
        index = current["question_index"]
        args = dict(session_id=sid, question_index=index, answered_text=word.answer,
                    outcome="correct", kana_rows=repo.kana_list(), now=NOW)
        store.record_review(word.content_id, word.kind, word.script, None, **args)
        saved = evidence(store)
        assert store.record_review(word.content_id, word.kind, word.script, None, **args)["already_recorded"]
        assert evidence(store) == saved
        assert reconcile(store, sid, ("kana_choice",)) is None
        assert reconcile(store, sid, KINDS) is None
        assert evidence(store) == saved


def test_obsolete_word_timer_stays_retired(tmp_path):
    with StudyStore(tmp_path / "learner", "UTC") as store:
        q = word_question()
        q.metadata["not_before_utc"] = (NOW + timedelta(minutes=1)).isoformat()
        sid = store.start_session([q, kana()], now=NOW)
        store.save_session_progress(sid, 0, state=DRAFT)
        reconcile(store, sid, ("kana_choice",))
        resumed = reconcile(store, sid, KINDS)
        assert resumed["restored_count"] == 0
        assert all(q.kind not in {"word_kana", "word_meaning_kana", "word_romaji"}
                   for q in resumed["questions"][resumed["question_index"]:])


@pytest.mark.parametrize("lazy", [False, True])
@pytest.mark.parametrize("disabled", [False, True])
def test_newly_retired_paused_word_is_not_replayed_by_direct_dispatch(tmp_path, lazy, disabled):
    with StudyStore(tmp_path / "learner", "UTC") as store:
        retired = Question("word:file", "word_meaning_kana", "hiragana", "fairu", "ふぁいる",
            metadata={"word_romaji_drill": True, WORD_KIND_PAUSE: {
                "version": 1, "disabled": disabled, "state": deepcopy(FEEDBACK)}})
        sid = store.start_session([retired], now=NOW)
        store.practice_lane = "word_romaji"
        store.save_session_progress(sid, 0)
        assert store.prepare_next_question(sid, now=NOW, lazy_questions=lazy) is None
        assert store.load_session_progress() is None
        assert WORD_KIND_PAUSE not in store.get_session_questions(sid)[0].metadata
        # An upgrade's retirement must not leave an orphaned pause that makes
        # the user's otherwise valid backup impossible to restore.
        store.inspect_backup(store.export_backup())


@pytest.mark.parametrize("change", [
    lambda q: q["metadata"][WORD_KIND_PAUSE].update(disabled="yes"),
    lambda q: q["metadata"][WORD_KIND_PAUSE].update(version=True),
    lambda q: q["metadata"][WORD_KIND_PAUSE].update(state={"feedback_pending": "yes"}),
    lambda q: q["metadata"].update(not_before_utc=NOW.isoformat()),
    lambda q: q.update(kind="kana_choice"),
])
def test_backup_rejects_malformed_pause_markers(change):
    q = word_question().to_dict()
    q["metadata"][WORD_KIND_PAUSE] = {"version": 1, "disabled": True, "state": deepcopy(DRAFT)}
    change(q)
    with pytest.raises(ValueError):
        _questions(json.dumps([q], ensure_ascii=False))


def test_cancelled_restore_cannot_replace_existing_learning(tmp_path):
    from kanamemory.progress_backup import import_backup
    with StudyStore(tmp_path / "source", "UTC") as source, StudyStore(tmp_path / "destination", "UTC") as destination:
        sid, _ = start(source)
        reconcile(source, sid, ("kana_choice",))
        raw = source.export_backup()
        destination.record_review(KA, "kana_choice", "hiragana", 4, now=NOW)
        original = list(destination._db.iterdump())
        def cancel():
            raise InterruptedError("cancelled by user")
        with pytest.raises(InterruptedError):
            import_backup(destination, raw, cancel_check=cancel)
        assert list(destination._db.iterdump()) == original


@pytest.mark.parametrize("replacement", [False, True])
def test_explicitly_abandoned_paused_queue_remains_valid_history_without_resuming(tmp_path, replacement):
    with StudyStore(tmp_path / "source", "UTC") as source:
        sid, _ = start(source)
        reconcile(source, sid, ("kana_choice",))
        before = evidence(source)
        if replacement:
            new_sid = source.start_session([kana()], now=NOW)
            source.save_session_progress(new_sid, 0)
        else:
            source.clear_session_progress()
        raw = source.export_backup()
        source.inspect_backup(raw)
    with StudyStore(tmp_path / "restored", "UTC") as restored:
        restored.import_backup(raw)
        assert evidence(restored) == before
        pending = restored.load_session_progress()
        assert (pending["session_id"] == new_sid) if replacement else pending is None
        assert reconcile(restored, sid, KINDS) is None
        # The original ungraded draft is retained as history; importing it
        # neither activates an abandoned queue nor invents an assessment.
        marker = restored.get_session_questions(sid)[0].metadata[WORD_KIND_PAUSE]
        assert marker["state"] == DRAFT


def test_current_records_restore_live_pause_without_retaining_history_in_memory(tmp_path):
    from kanamemory import record_transport
    with StudyStore(tmp_path / "source", "UTC") as source:
        sid, question = start(source, state=FEEDBACK)
        reconcile(source, sid, ("kana_choice",))
        raw = source.export_backup()
        assert raw.startswith(record_transport.PREFIX)
        before = evidence(source)
    with StudyStore(tmp_path / "restored", "UTC") as restored:
        restored.inspect_backup(raw)
        restored.import_backup(raw)
        pending = reconcile(restored, sid, KINDS)
        assert pending["questions"][0].to_dict() == question.to_dict()
        assert pending["state"] == FEEDBACK
        assert evidence(restored) == before
