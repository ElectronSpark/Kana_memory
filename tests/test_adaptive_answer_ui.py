"""Production tasks keep one script, survive interruption and require no TTS."""
import asyncio
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from support.fake_ui import View, descendants, text_of
from support.app_fixtures import app_module, question_app, setup_app
from support.answer_option_ui_support import preview_app
from support.answer_option_ui_support import control
from support.optimization_ui_support import learner
from support.optimization_ui_support import session_app
from kanamemory.app import KanaMemory
from kanamemory.domain import Question, StudyStore


def q(kind, script="hiragana", compound=False):
    symbol = ("きゃ" if compound else "か") if script == "hiragana" else ("キャ" if compound else "カ")
    options = ["か", "き", "ゃ", "な"] if script == "hiragana" else ["カ", "キ", "ャ", "ナ"]
    return Question("kya" if compound else "ka", kind, script, "", symbol, options=options,
        metadata={"kana_symbol": symbol, "romaji": "kya" if compound else "ka", "audio_only": False,
                  "word_table": "full", "content_version": "test"})


def button(app, text):
    return next(node for node in descendants(app.body) if node.text == text and node.on_press)


@pytest.mark.parametrize("script", ["hiragana", "katakana"])
def test_full_table_has_visual_cue_single_script_and_composes_groups(question_app, script):
    app = question_app
    app.questions = [q("romaji_kana_full", script, compound=True)]
    app.script = "katakana" if script == "hiragana" else "hiragana"
    app.show_question()
    assert app.script == script and app.page == "word_question" and app.question_audio_ok
    text = text_of(app.body)
    # The romaji stimulus and the table keys are the cue; the instruction sentence
    # "从完整假名表选出..." was dropped as redundant (0.28.9, c7ac715).
    assert "kya" in text and all(option in text for option in app.question.options)
    assert "词义" not in text and "播放" not in text and "基础词" not in text
    assert ("平假名" if script == "hiragana" else "片假名") in text
    for char in app.question.answer:
        button(app, char).on_press(None)
    assert app.word_answer == app.question.answer
    stale_key = button(app, app.question.answer[0])
    button(app, "提交答案").on_press(None)
    assert app.page == "feedback" and app.answer_correct and "很轻松" in text_of(app.body)
    stale_key.on_press(None)
    assert app.word_answer == app.question.answer
    app.play_question_audio.assert_not_called()
    app.store.record_review.assert_not_called()


def test_full_table_reveal_allows_only_again_and_feedback_links_to_kana(preview_app):
    app = preview_app
    app.questions = [q("romaji_kana_full", "katakana")]
    app.show_question()
    app.reveal_answer()
    assert app.page == "feedback" and not app.answer_correct
    assert "再学一次" in text_of(app.body) and "记住了" not in text_of(app.body)
    preview = text_of(app.practice_preview_slot)
    # Kana-answer feedback already displays its single-script answer above the
    # context slot. The slot must not repeat it (see preview_practice_answer).
    body = text_of(app.body)
    assert body.count("カ") == 1 and "か" not in body
    assert "ka" in preview and "カ" not in preview
    control(app, "详细").on_press(None)
    assert app.opened_kana == "ka"


@pytest.mark.parametrize("script", ["hiragana", "katakana"])
def test_visual_handwriting_recognition_and_confirmation_work_without_audio(question_app, monkeypatch, script):
    app = question_app
    monkeypatch.setattr(app_module.toga, "Canvas", lambda **kwargs: View(**kwargs))
    app._ink_revision, app._ink_busy, app.model_state = 0, False, "ready"
    app._redraw_ink = Mock()
    for method in ("ink_press", "ink_drag", "ink_release", "ink_resize", "undo_ink", "clear_ink"):
        setattr(app, method, Mock())
    app.platform.recognize = AsyncMock(return_value=["か" if script == "hiragana" else "カ"])
    for method in ("show_ink_page", "recognize_ink", "confirm_ink"):
        setattr(app, method, MethodType(getattr(KanaMemory, method), app))
    app.questions = [q("romaji_handwriting", script)]
    app.show_question()
    assert app.page == "ink_question" and app.question_audio_ok
    assert "ka" in text_of(app.body) and "播放声音" not in text_of(app.body)
    assert ("请写平假名" if script == "hiragana" else "请写片假名") in text_of(app.body)
    app.strokes = [[(10, 20, 0), (40, 60, 100)]]
    asyncio.run(app.recognize_ink())
    candidate = next(v for v in descendants(app.ink_result_box) if v.text == app.question.answer and v.on_press)
    candidate.on_press(None)
    assert app.page == "feedback" and app.answer_correct
    assert "记住了" in text_of(app.body)
    app.play_question_audio.assert_not_called()
    app.alert.assert_not_called()
    app.store.record_review.assert_not_called()


@pytest.mark.parametrize("kind", ["romaji_kana_full", "romaji_handwriting"])
@pytest.mark.parametrize("feedback", [False, True])
def test_new_production_modes_checkpoint_backup_resume_keep_input_without_tts(learner, tmp_path, kind, feedback):
    original = session_app(learner, kind, assisted=feedback, feedback=feedback)
    if not feedback:
        original.page = "word_question" if kind == "romaji_kana_full" else "ink_question"
    original._checkpoint()
    saved = learner.load_session_progress()
    with StudyStore(tmp_path / "imported") as restored:
        restored.import_backup(learner.export_backup())
        assert restored.load_session_progress()["state"] == saved["state"]
        app = session_app(restored, kind)
        app.check_answer = Mock()
        app._redraw_ink = Mock()
        def show_question():
            app.question = app.questions[app.question_index]
            app.page = "word_question" if kind == "romaji_kana_full" else "ink_question"
            app.question_audio_ok = True
            app.answer_revealed = app._attempt_key() in app._assisted_attempts
            app.word_answer_label = SimpleNamespace(text="")
            app.question_notice = SimpleNamespace(text="")
        app.show_question = show_question
        app.resume_practice()
        assert app.session_id == original.session_id and app.question_audio_ok
        if kind == "romaji_kana_full":
            assert app.word_answer == app.word_answer_label.text == "あ"
        else:
            assert [[p[:2] for p in stroke] for stroke in app.strokes] == [[(10, 20), (20, 30)]]
            assert app.strokes[0][1][2] - app.strokes[0][0][2] == 20
        if feedback:
            app.check_answer.assert_called_once_with("あ")
            assert app.answer_revealed
        else:
            app.check_answer.assert_not_called()
        assert restored.stats()["total_reviews"] == 0
