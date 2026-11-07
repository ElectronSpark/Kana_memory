"""Sound-only cues cannot determine a homophone spelling; words still can."""
from runtime_record_test_support import snapshot_json, write_snapshot
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import sys

import pytest

from kanamemory.domain import Question, StudyStore, build_questions, check_answer, isolated_kana_question, to_script

NOW = datetime(2026, 10, 28, 18, tzinfo=timezone.utc)
KINDS = ("romaji_kana", "romaji_kana_full", "romaji_handwriting", "kana_handwriting")
PAIRS = (("じ", "ぢ"), ("ず", "づ"), ("じゃ", "ぢゃ"), ("じゅ", "ぢゅ"),
         ("じょ", "ぢょ"), ("お", "を"), ("い", "ゐ"), ("え", "ゑ"))


@pytest.mark.parametrize("script", ["hiragana", "katakana"])
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("first,second", PAIRS)
def test_sound_only_accepts_both_spellings_in_the_requested_script(script, kind, first, second):
    first, second = to_script(first, script), to_script(second, script)
    q = Question("legacy", kind, script, "sound only", first, options=[first, second])
    before = deepcopy(q.to_dict())
    assert check_answer(q, first) and check_answer(q, second)
    other_script = "katakana" if script == "hiragana" else "hiragana"
    assert not check_answer(q, to_script(second, other_script))
    assert not check_answer(q, second + "う")
    assert not check_answer(q, "きや")
    assert q.to_dict() == before


@pytest.mark.parametrize("kind", KINDS)
def test_explicit_context_keeps_spelling_distinction(kind):
    q = Question("target", kind, "hiragana", "请写出ち加浊点后的字形", "ぢ",
                 metadata={"context_given": True})
    assert not isolated_kana_question(q)
    assert check_answer(q, "ぢ") and not check_answer(q, "じ")
    marked = Question("target", kind, "hiragana", "明确字形", "ぢ",
                      metadata={"answer_scope": "orthography"})
    assert not check_answer(marked, "じ")


@pytest.mark.parametrize("kind,prompt", [
    ("romaji_kana", "选出罗马音「jo」对应的假名。"),
    ("romaji_kana_full", "从完整假名表拼出罗马音「jo」对应的假名。"),
    ("romaji_handwriting", "写出罗马音「jo」对应的假名，并确认识别结果。"),
])
def test_exact_legacy_reverse_prompt_is_sound_only_despite_old_context_flag(kind, prompt):
    q = Question("kana-3062-3087", kind, "hiragana", prompt, "ぢょ",
                 metadata={"context_given": True, "audio_only": False,
                           "romaji": "jo", "kana_symbol": "ぢょ"})
    assert isolated_kana_question(q) and check_answer(q, "じょ")
    explicit = Question(q.content_id, kind, q.script, prompt + "请使用ぢ。", q.answer,
                        metadata=q.metadata)
    assert not isolated_kana_question(explicit) and not check_answer(explicit, "じょ")


def test_explicit_new_scope_overrides_old_context_marker_and_forward_labels_remain_distinct():
    q = Question("old", "romaji_kana", "hiragana", "o", "を",
                 metadata={"context_given": True, "answer_scope": "isolated_sound"})
    assert check_answer(q, "お")
    for kind in ("kana_choice", "kana_romaji"):
        q = Question("old", kind, "hiragana", "を", "wo")
        assert check_answer(q, "WO") and not check_answer(q, "o")


def test_old_listening_choice_with_kana_answers_is_not_a_roman_letter_question():
    q = Question("old-audio", "kana_choice", "katakana", "听声音选假名", "ヂュ",
                 ["ヂュ", "ジュ", "キュ"], metadata={"audio_only": True})
    assert isolated_kana_question(q)
    assert check_answer(q, "ジュ") and check_answer(q, "ヂュ")
    assert not check_answer(q, "じゅ") and not check_answer(q, "ju")


@pytest.mark.parametrize("kind", ["word_kana", "word_meaning_kana"])
def test_word_spelling_ignores_legacy_cartesian_aliases_but_keeps_permitted_forms(kind):
    wrong = Question("legacy-word", kind, "hiragana", "単語", "はなぢ",
                     accepted_answers=("はなじ",), metadata={"word_romaji_drill": True})
    before = wrong.to_dict()
    assert check_answer(wrong, "はなぢ") and not check_answer(wrong, "はなじ")
    permitted = Question("legacy-world", kind, "hiragana", "世界中", "せかいじゅう")
    assert check_answer(permitted, "せかいぢゅう")
    assert not check_answer(permitted, "せかいぢゅー")
    assert not check_answer(permitted, "セカイヂュウ")
    assert wrong.to_dict() == before


@pytest.mark.parametrize("kind", ["word_kana", "word_meaning_kana"])
@pytest.mark.parametrize("script", ["hiragana", "katakana"])
def test_new_partial_word_keyboard_can_enter_permitted_variant(tmp_path, kind, script):
    class Repository:
        def metadata(self):
            return {"content_version": "test"}
        def kana_list(self):
            return []
        def all_kana_symbols(self, requested):
            return list(to_script("いなずまかきく", requested))
        def practice_word_candidates(self, **kwargs):
            return [{"id": "word:lightning", "reading": "いなずま", "gloss": "闪电", "practice_approved": True}]
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        q, = build_questions(Repository(), store, kind=kind, script=script, limit=1, seed=5, now=NOW)
        alternative = to_script("いなづま", script)
        assert check_answer(q, alternative)
        assert alternative in q.accepted_answers
        assert set(alternative) <= set(q.options)
        assert all(len(option) == 1 for option in q.options)


def test_reopened_legacy_queue_keeps_draft_history_and_future_time(tmp_path):
    folder = tmp_path / "learner"
    q = Question("kana-3062-3087", "romaji_handwriting", "hiragana",
        "写出罗马音「jo」对应的假名，并确认识别结果。", "ぢょ", ["ぢょ", "じょ"],
        metadata={"context_given": True, "audio_only": False, "romaji": "jo",
                  "kana_symbol": "ぢょ", "practice_config": {"schedule_mode": "reinforce"}})
    due = (NOW + timedelta(days=3)).isoformat()
    tail = Question("kana-304b", "romaji_kana", "hiragana", "ka", "か", ["か", "き"],
                    metadata={"not_before_utc": due})
    draft = {"page": "ink_question", "strokes": [[[1.0, 2.0, 0], [3.0, 4.0, 20]]],
             "ink_width": 300, "ink_height": 180}
    with StudyStore(folder, time_zone="UTC") as store:
        store.record_review("existing", "kana_choice", "hiragana", 4, now=NOW-timedelta(days=10))
        old_card = store.get_card("existing", "kana_choice", "hiragana")
        sid = store.start_session([q, tail], now=NOW)
        store.save_session_progress(sid, 0, state=draft)
        before = snapshot_json(store._db, sid)
        history = [tuple(row) for row in store._db.execute("SELECT * FROM review_event")]
    with StudyStore(folder, time_zone="UTC") as store:
        pending = store.load_session_progress()
        assert pending["state"] == draft
        assert check_answer(pending["questions"][0], "じょ")
        assert store.get_card("existing", "kana_choice", "hiragana") == old_card
        assert [tuple(row) for row in store._db.execute("SELECT * FROM review_event")] == history
        result = store.record_review(q.content_id, q.kind, q.script, 3, session_id=sid,
            question_index=0, answered_text="じょ", outcome="correct", now=NOW)
        assert result["scheduled"]
        pending = store.load_session_progress()
        assert pending["question_index"] == 1 and pending["state"] == {}
        assert pending["questions"][1].metadata["not_before_utc"] == due
        assert snapshot_json(store._db, sid) == before
        assert store.get_card("existing", "kana_choice", "hiragana") == old_card
        again = store.record_review(q.content_id, q.kind, q.script, 3, session_id=sid,
            question_index=0, answered_text="じょ", outcome="correct", now=NOW)
        assert again["already_recorded"]
        assert store._db.execute("SELECT COUNT(*) FROM session_answer WHERE session_id=?", (sid,)).fetchone()[0] == 1


@pytest.mark.parametrize("answer,correct,error_category", [
    ("はなじ", False, "spelling"), ("はなき", False, "reading"), ("はなぢ", True, None),
])
def test_saved_word_outcome_and_error_diagnosis_use_the_same_grading_policy(tmp_path, answer, correct, error_category):
    rows = [{"id": "kana-3062", "hiragana": "ぢ", "katakana": "ヂ"}]
    q = Question("word:nosebleed", "word_meaning_kana", "hiragana", "鼻血 hana ji", "はなぢ",
                 accepted_answers=("はなじ",), metadata={"word_romaji_drill": True})
    with StudyStore(tmp_path / "word", time_zone="UTC") as store:
        sid = store.start_session([q], now=NOW)
        store.save_session_progress(sid, 0, state={"page": "word_question", "word_answer": "は"})
        assert check_answer(q, answer) is correct
        store.record_review(q.content_id, q.kind, q.script, None, session_id=sid,
            question_index=0, answered_text=answer, outcome="correct" if correct else "incorrect",
            kana_rows=rows, now=NOW)
        saved = store._db.execute("SELECT correct,evidence_json FROM word_spelling_attempt").fetchone()
        evidence = json.loads(saved["evidence_json"])
        assert bool(saved["correct"]) is correct and evidence["correct"] is correct
        if error_category is not None:
            assert evidence["error_category"] == error_category
        if error_category == "spelling":
            assert evidence["units"] == []
            assert evidence["orthography_units"]
        assert store.get_card(q.content_id, q.kind, q.script) is None


def test_permitted_word_variant_is_recorded_without_false_kana_evidence(tmp_path):
    q = Question("word:world", "word_meaning_kana", "hiragana", "世界中", "せかいじゅう",
                 metadata={"word_romaji_drill": True})
    with StudyStore(tmp_path / "word", time_zone="UTC") as store:
        sid = store.start_session([q], now=NOW)
        store.save_session_progress(sid, 0)
        store.record_review(q.content_id, q.kind, q.script, None, session_id=sid, question_index=0,
            answered_text="せかいぢゅう", outcome="correct", kana_rows=[], now=NOW)
        saved = store._db.execute("SELECT correct,evidence_json FROM word_spelling_attempt").fetchone()
        evidence = json.loads(saved["evidence_json"])
        assert saved["correct"] == 1 and evidence["accepted_alternative"]
        assert evidence["units"] == evidence["errors"] == evidence["schedule_adjustments"] == []


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("script", ["hiragana", "katakana"])
def test_homophone_review_advances_only_its_sound_direction_and_records_actual_glyph(tmp_path, kind, script):
    canonical, alternative = to_script("ぢ", script), to_script("じ", script)
    q = Question("kana-3062", kind, script, "ji", canonical,
                 metadata={"answer_scope": "isolated_sound"})
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        for forward in ("kana_choice", "kana_romaji"):
            store.record_review(q.content_id, forward, script, 3,
                                answered_text="ji", outcome="correct", now=NOW-timedelta(days=1))
        previous_cards = {forward: store.get_card(q.content_id, forward, script)
                          for forward in ("kana_choice", "kana_romaji")}
        history = [tuple(row) for row in store._db.execute("SELECT * FROM review_event")]
        sid = store.start_session([q], now=NOW)
        store.save_session_progress(sid, 0)
        result = store.record_review(q.content_id, kind, script, 3, session_id=sid,
            question_index=0, answered_text=alternative, outcome="correct", now=NOW)
        assert result["scheduled"] and result["card"]["review_count"] == 1
        assert result["card"]["due_at_utc"] > NOW.isoformat()
        assert {forward: store.get_card(q.content_id, forward, script)
                for forward in previous_cards} == previous_cards
        assert [tuple(row) for row in store._db.execute("SELECT * FROM review_event ORDER BY rowid LIMIT 2")] == history
        log = json.loads(store._db.execute("SELECT review_log_json FROM review_event WHERE event_id=?",
                                         (result["event_id"],)).fetchone()[0])
        assert log["kana_answer_evidence"] == {
            "version": 1, "answer_scope": "isolated_sound", "script": script,
            "canonical_answer": canonical, "actual_answer": alternative, "accepted_homophone": True}
        assert store.get_card("kana-3058", kind, script) is None
        before = [tuple(row) for row in store._db.execute("SELECT * FROM review_event")]
        repeated = store.record_review(q.content_id, kind, script, 3, session_id=sid,
            question_index=0, answered_text=canonical, outcome="correct", now=NOW)
        assert repeated["already_recorded"]
        assert [tuple(row) for row in store._db.execute("SELECT * FROM review_event")] == before
