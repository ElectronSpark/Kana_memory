"""Audio races must not expose a future answer or mutate study progress."""
import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from kanamemory.answer_autoplay import autoplay_answer, autoplay_prompt, MAX_RECENT_ANSWERS


def host(page="feedback"):
    return SimpleNamespace(page=page, _page_token=1, _audio_serial=0,
        platform=SimpleNamespace(play=AsyncMock()), alert=Mock(),
        store=Mock(), question_audio_ok=False,
        loop=SimpleNamespace(create_task=lambda coroutine: asyncio.run(coroutine)))


@pytest.mark.parametrize("page", ["feedback", "learning_feedback", "learning_guide", "ai_feedback", "diagnostic_feedback"])
def test_once_per_answer_not_per_render_and_no_evidence(page):
    app = host(page)
    assert autoplay_answer(app, "ア", ("answer", 1))
    app._page_token += 1
    assert not autoplay_answer(app, "ア", ("answer", 1))
    app.platform.play.assert_awaited_once_with("ア")
    assert not app.question_audio_ok
    assert not app.store.mock_calls
    app.alert.assert_not_called()
    assert autoplay_answer(app, "イ", ("answer", 2))
    assert app.platform.play.await_count == 2


@pytest.mark.parametrize("page", ["home", "question", "word_question", "ink_question", "learning_question", "ai_question", "learning_detail", "diagnostic_question"])
def test_answers_are_never_queued_on_question_or_detail(page):
    app = host(page)
    assert not autoplay_answer(app, "こたえ", ("answer", 1))
    app.platform.play.assert_not_awaited()


@pytest.mark.parametrize("invalidated", ["page", "token", "audio"])
def test_deferred_task_is_stale_safe_and_can_retry_after_return(invalidated):
    app = host()
    pending = []
    app.loop = SimpleNamespace(create_task=pending.append)
    assert autoplay_answer(app, "ア", 1)
    assert not autoplay_answer(app, "ア", 1)
    if invalidated == "page":
        app.page = "question"
    elif invalidated == "token":
        app._page_token += 1
    else:
        app._audio_serial += 1
    asyncio.run(pending.pop())
    app.platform.play.assert_not_awaited()
    app.page = "feedback"
    assert autoplay_answer(app, "ア", 1)
    asyncio.run(pending.pop())
    app.platform.play.assert_awaited_once_with("ア")


def test_repaint_before_first_play_keeps_new_task_and_discards_old():
    app = host()
    callbacks, tasks = [], []
    app.loop = SimpleNamespace(call_soon=callbacks.append, create_task=tasks.append)
    autoplay_answer(app, "ア", 1)
    app._page_token += 1
    autoplay_answer(app, "ア", 1)
    callbacks.pop(0)()
    assert not tasks
    callbacks.pop(0)()
    asyncio.run(tasks.pop())
    app.platform.play.assert_awaited_once_with("ア")


def test_manual_replay_supersedes_queued_automatic_audio():
    app = host()
    callbacks = []
    app.loop.call_soon = callbacks.append
    autoplay_answer(app, "ア", 1)
    app._audio_serial += 1  # Existing speak_text owns the later audio serial.
    callbacks.pop()()
    app.platform.play.assert_not_awaited()


def test_failure_does_not_show_dialog_change_audio_ready_or_keep_retrying():
    app = host()
    app.platform.play.side_effect = RuntimeError("No voice installed")
    assert autoplay_answer(app, "ア", 1)
    assert not autoplay_answer(app, "ア", 1)
    app.platform.play.assert_awaited_once()
    app.alert.assert_not_called()
    assert not app.question_audio_ok and not app.store.mock_calls
    del app._answer_autoplay_entries  # New process, restored feedback.
    app.platform.play.side_effect = None
    assert autoplay_answer(app, "ア", 1)
    assert app.platform.play.await_count == 2


def test_empty_or_unavailable_audio_is_not_marked_played():
    app = host()
    assert not autoplay_answer(app, "", 1)
    assert not autoplay_answer(app, None, 1)
    app.platform.play = None
    assert not autoplay_answer(app, "ア", 1)
    assert not hasattr(app, "_answer_autoplay_entries")


def test_recent_cache_is_bounded_for_long_sessions():
    app = host()
    for index in range(MAX_RECENT_ANSWERS + 100):
        autoplay_answer(app, "ア", index)
    assert len(app._answer_autoplay_entries) == MAX_RECENT_ANSWERS


def test_closed_loop_does_not_poison_unplayed_answer():
    app = host()
    app.loop.call_soon = Mock(side_effect=RuntimeError("closed"))
    assert not autoplay_answer(app, "ア", 1)
    app.platform.play.assert_not_awaited()
    assert not app._answer_autoplay_entries


# Prompt autoplay (P1): a question whose prompt must be heard starts once when shown.

def prompt_host(page="word_question"):
    app = host(page)
    app.heard = False
    app.started = []

    async def play():
        # Stands for the page's own quiet play path: success records audio_ok.
        app.started.append(app.page)
        await app.platform.play("かな")
        app.heard = True
    app.play = play
    return app


def show_prompt(app, key=("practice", 1)):
    return autoplay_prompt(app, key, app.play, heard=lambda: app.heard)


def test_prompt_starts_once_and_a_repaint_or_heard_prompt_does_not_replay():
    app = prompt_host()
    assert show_prompt(app)
    app.platform.play.assert_awaited_once_with("かな")
    assert app.heard
    app._page_token += 1  # Repaint of the same question.
    assert not show_prompt(app)
    app.heard = False  # Even unheard, a repaint of this showing stays silent.
    assert not show_prompt(app)
    assert app.platform.play.await_count == 1
    assert show_prompt(app, ("practice", 2))  # The next question plays.
    assert app.platform.play.await_count == 2
    app.alert.assert_not_called()
    assert not app.store.mock_calls


@pytest.mark.parametrize("page", ["home", "question", "feedback", "learning_feedback", "learning_guide", "ai_question"])
def test_prompt_never_starts_outside_must_hear_question_pages(page):
    app = prompt_host(page)
    assert not show_prompt(app)
    app.platform.play.assert_not_awaited()


def test_heard_prompt_does_not_start():
    app = prompt_host()
    app.heard = True
    assert not show_prompt(app)
    app.platform.play.assert_not_awaited()


def test_leaving_before_the_start_drops_it_and_showing_again_plays():
    app = prompt_host()
    callbacks = []
    app.loop.call_soon = callbacks.append
    assert show_prompt(app)
    app.page, app._page_token = "detail", app._page_token + 1
    callbacks.pop()()
    app.platform.play.assert_not_awaited()
    app.page, app._page_token = "word_question", app._page_token + 1
    assert show_prompt(app)
    callbacks.pop()()
    app.platform.play.assert_awaited_once_with("かな")


def test_leaving_while_it_plays_is_forgotten_so_the_next_showing_plays():
    app = prompt_host()

    async def interrupted(text):
        app.page = "detail"  # Navigation stops the speech: not heard.
        raise RuntimeError("stopped")
    app.platform.play.side_effect = interrupted
    assert show_prompt(app)
    assert not app.heard
    app.page, app._page_token = "word_question", app._page_token + 1
    app.platform.play.side_effect = None
    assert show_prompt(app)
    assert app.platform.play.await_count == 2 and app.heard
    app.alert.assert_not_called()


def test_failed_prompt_start_is_quiet_and_not_retried_on_repaint():
    app = prompt_host()
    app.platform.play.side_effect = RuntimeError("No voice installed")
    assert show_prompt(app)
    app._page_token += 1
    assert not show_prompt(app)
    app.platform.play.assert_awaited_once()
    app.alert.assert_not_called()
    assert not app.heard and not app.store.mock_calls


def test_manual_play_before_the_start_supersedes_it():
    app = prompt_host()
    callbacks = []
    app.loop.call_soon = callbacks.append
    show_prompt(app)
    app._audio_serial += 1  # The button's own play path owns the later serial.
    callbacks.pop()()
    app.platform.play.assert_not_awaited()


def test_prompt_and_answer_memories_do_not_collide():
    app = prompt_host()
    assert show_prompt(app, ("practice", 1))
    app.page = "feedback"
    assert autoplay_answer(app, "かな", ("practice", 1))
    assert [call.args for call in app.platform.play.await_args_list] == [("かな",), ("かな",)]
    assert len(app._prompt_autoplay_entries) == len(app._answer_autoplay_entries) == 1
