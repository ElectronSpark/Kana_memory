"""Preview raw legacy queues accurately without editing their saved drafts."""
from datetime import datetime, timezone

import pytest

from support.app_fixtures import app_module, setup_app
from kanamemory.domain import Question, StudyStore


NOW = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)


def learning_rows(store):
    return {table: [tuple(row) for row in store._db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in ("study_session", "session_checkpoint", "study_card", "review_event",
                          "session_answer", "practice_attempt", "word_spelling_attempt")}


@pytest.mark.parametrize("kind,ordinary_id,ordinary,foreign_id,foreign,katakana", [
    ("romaji_kana_full", "kana-304d-3083", "きゃ", "kana-3066-3043", "てぃ", "ティ"),
    ("word_meaning_kana", "word-guest", "きゃく", "word-file", "ふぁいる", "ファイル"),
])
def test_append_preview_excludes_retired_answers_before_queue_reconciliation(
        setup_app, tmp_path, kind, ordinary_id, ordinary, foreign_id, foreign, katakana):
    app = setup_app
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        app.store = store
        app.repo.approved_word_count.return_value = 3
        app.kind_picker.value = next(title for title, value in app_module.PRACTICE_KINDS.items()
                                     if value == kind)
        app.script_picker.value = "平假名与片假名（逐题分开）"
        app.batch_picker.value = "只学新内容"
        app.practice_plan_summary.return_value = {"total": 3, "content_count": 3, "due": 0, "new": 3}
        prior = Question("kana-3042", kind, "hiragana", "prior", "あ", ["あ"])
        active = Question(ordinary_id, kind, "hiragana", "current", ordinary, list(ordinary))
        retired = Question(foreign_id, kind, "hiragana", "legacy", foreign, list(foreign))
        kept = Question(foreign_id, kind, "katakana", "kept", katakana, list(katakana))
        disabled = Question("kana-3046", "kana_choice", "hiragana", "う", "u", ["u", "a"])
        sid = store.start_session([prior, active, retired, kept, disabled], now=NOW)
        draft = {"page": "word_question", "word_answer": "き"}
        store.save_session_progress(sid, 1, state=draft)
        before = learning_rows(store)

        app.refresh_practice_setup()

        summary = " ".join(app.practice_count_summary.text.split())
        assert "原队列未完成 2 题" in summary
        assert "追加后待完成 5 题" in summary
        assert app.practice_start_button.text == "追加到当前练习"
        assert app.practice_start_button.enabled
        # A read-only preview must not silently migrate/reorder the old queue,
        # advance its cursor, clear the current draft, or invent review events.
        assert learning_rows(store) == before
        pending = store.load_session_progress()
        assert pending["question_index"] == 1
        assert pending["state"] == draft
