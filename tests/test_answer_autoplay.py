"""Audio races must not expose a future answer or mutate study progress."""
import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from kanamemory.answer_autoplay import autoplay_answer, MAX_RECENT_ANSWERS


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
