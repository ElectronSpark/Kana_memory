"""Kana and word practice: a prompt that must be heard starts once when it is shown.

Public test: synthetic kana records and one synthetic word, no course content.
"""
import asyncio
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from support.app_fixtures import setup_app, question_app
from support.answer_option_ui_support import preview_app, render, RECORDS, WORD
from support.fake_ui import descendants
from kanamemory.app import KanaMemory
from kanamemory.domain import Question


def word(index=0, reading=WORD["reading"]):
    return Question(WORD["id"], "word_kana", "hiragana", "", reading, options=list(reading), audio_text=reading,
                    metadata={"romaji": "kana", "meaning_hint": "假名", "content_version": "test"})


def run_loop(app):
    """Callbacks queued with call_soon run when the test says so; tasks run at once."""
    app.callbacks = []
    app.loop = SimpleNamespace(call_soon=app.callbacks.append, create_task=lambda task: asyncio.run(task))
    app.play_question_audio = MethodType(KanaMemory.play_question_audio, app)
    app.platform.play = AsyncMock()
    return app


def drain(app):
    while app.callbacks:
        app.callbacks.pop(0)()


def show(app, *questions, index=0):
    app.questions, app.question_index = list(questions), index
    app.show_question()


def button(app, caption):
    return next(v for v in descendants(app.body) if v.text == caption and v.on_press)


def test_listening_word_plays_once_with_the_buttons_text_and_records_audio_ok(preview_app):
    app = run_loop(preview_app)
    show(app, word())
    assert app.page == "word_question" and not app.question_audio_ok
    app.platform.play.assert_not_awaited()  # Not before the page is drawn.
    drain(app)
    app.platform.play.assert_awaited_once_with("かな")
    assert app.question_audio_ok
    app._checkpoint.assert_called()
    app.alert.assert_not_called()
    app.store.record_review.assert_not_called()
    # A repaint of the same question does not replay.
    KanaMemory.show_word_question(app, "repaint")
    drain(app)
    app.platform.play.assert_awaited_once()
    # The manual button stays and replays the same text.
    asyncio.run(button(app, "播放词语").on_press(None))
    assert [call.args for call in app.platform.play.await_args_list] == [("かな",), ("かな",)]


def test_repaint_after_a_fresh_render_resets_audio_ok_still_does_not_replay(preview_app):
    app = run_loop(preview_app)
    show(app, word())
    drain(app)
    show(app, word())  # show_question again (e.g. a settings change) resets audio_ok.
    drain(app)
    app.platform.play.assert_awaited_once()


def test_next_question_plays(preview_app):
    app = run_loop(preview_app)
    first, second = word(), word(reading="なか")
    show(app, first, second)
    drain(app)
    show(app, first, second, index=1)
    drain(app)
    assert [call.args for call in app.platform.play.await_args_list] == [("かな",), ("なか",)]


@pytest.mark.parametrize("kind", ["word_meaning_kana", "kana_choice", "kana_romaji", "romaji_kana", "romaji_kana_full"])
def test_visible_prompts_stay_silent(preview_app, kind):
    app = run_loop(preview_app)
    render(app, kind)
    drain(app)
    app.platform.play.assert_not_awaited()


def test_failed_start_is_quiet_and_leaves_the_question_as_a_failed_manual_play(preview_app):
    app = run_loop(preview_app)
    app.platform.play.side_effect = RuntimeError("No Japanese voice")
    show(app, word())
    drain(app)
    app.platform.play.assert_awaited_once()
    app.alert.assert_not_called()
    assert app.page == "word_question" and not app.question_audio_ok
    KanaMemory.show_word_question(app, "repaint")
    drain(app)
    app.platform.play.assert_awaited_once()  # Not retried on a repaint.
    # The visible button still reports the usual error, and leaves the same state.
    asyncio.run(button(app, "播放词语").on_press(None))
    app.alert.assert_called_once_with("No Japanese voice")
    assert not app.question_audio_ok


def test_leaving_before_the_start_drops_it_and_showing_again_plays(preview_app):
    app = run_loop(preview_app)
    show(app, word())
    app.screen("home", "今日")
    drain(app)
    app.platform.play.assert_not_awaited()
    show(app, word())
    drain(app)
    app.platform.play.assert_awaited_once_with("かな")


def test_answer_autoplay_still_plays_after_an_autoplayed_prompt(preview_app):
    app = run_loop(preview_app)
    show(app, word())
    drain(app)
    for kana in "かな":
        app.append_word_key(kana)
    button(app, "提交答案").on_press(None)
    drain(app)
    assert app.page == "feedback" and app.answer_correct
    assert [call.args for call in app.platform.play.await_args_list] == [("かな",), ("かな",)]
    app.store.record_review.assert_not_called()


@pytest.mark.parametrize("kind,plays", [("kana_handwriting", True), ("romaji_handwriting", False)])
def test_ink_page_plays_only_the_heard_handwriting_kind(preview_app, kind, plays):
    app = run_loop(preview_app)
    record = RECORDS[0]
    app.question = Question(record["id"], kind, "hiragana", "", record["hiragana"], audio_text=record["hiragana"],
                            metadata={"kana_symbol": record["hiragana"], "romaji": record["romaji"], "content_version": "test"})
    app.page, app.question_audio_ok = "ink_question", kind != "kana_handwriting"
    KanaMemory._autoplay_question_prompt(app)
    drain(app)
    if not plays:
        app.platform.play.assert_not_awaited()
        return
    automatic = app.platform.play.await_args
    assert app.question_audio_ok
    app._page_token += 1
    KanaMemory._autoplay_question_prompt(app)
    drain(app)
    asyncio.run(app.play_question_audio(None))  # The 播放声音 button.
    assert app.platform.play.await_count == 2 and app.platform.play.await_args == automatic
