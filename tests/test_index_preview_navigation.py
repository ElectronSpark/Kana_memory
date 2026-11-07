"""Exercise the app's actual preview coordinator across asynchronous boundaries."""
from kanamemory import ui_components as component_ui
import asyncio
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest

ROOT = Path(__file__).resolve().parents[1]
from kanamemory import app as app_module
from kanamemory.app import KanaMemory
from kanamemory.kana_links import ContentText, KanaLinkIndex
from kanamemory.navigation import BackStack, PageState
from kanamemory.pitch_display import PITCH_DISPLAY_SETTING
from support.fake_ui import View


KANA = {"id": "ka", "hiragana": "か", "katakana": "カ", "romaji": "ka", "category": "seion"}
WORD = {"id": "jmdict:1", "form": "書く", "reading": "かく", "gloss": "to write", "practice_approved": False}
SOURCE = {"id": "jmdict:1", "title": "JMdict entry 1", "license": "CC-BY-SA-4.0", "url": "https://example.org/entry/1"}


def harness(*, page="words", key=("show_words", None)):
    app = SimpleNamespace(
        page=page, _view_key=key, _render_key=None, _page_token=5,
        _preview_pending=None, session_id=None, question_index=0,
        _assisted_attempts=set(), _answered_attempts=set(), answer_revealed=False,
        question_notice=SimpleNamespace(text=""), back_stack=BackStack(),
        platform=Mock(), repo=Mock(), store=Mock(), alert=Mock(),
        show_kana_detail=Mock(), show_word_detail=Mock(), show_source=Mock(),
        source_map={SOURCE["id"]: SOURCE}, tasks=[], _checkpoint=Mock(), refresh_home_actions=Mock(),
        _show_practice_preview=Mock(),
    )
    app.back_stack.pages.append(PageState(("show_home", None), "home", "Home", object()))
    app.platform.preview_index = AsyncMock(return_value=False)
    app.repo.get_kana.return_value = KANA
    app.repo.get_word.return_value = WORD
    app.repo.source.return_value = SOURCE
    for name in ("_attempt_key", "_content_page_key", "_mark_lookup_assisted", "_preview_link",
                 "open_content_kana", "open_content_word", "open_content_source", "open_url", "_open_external_url"):
        setattr(app, name, MethodType(getattr(KanaMemory, name), app))
    def schedule(coroutine):
        task = asyncio.create_task(coroutine)
        app.tasks.append(task)
        return task
    app.loop = SimpleNamespace(create_task=schedule)
    return app


def open_index(app, kind):
    if kind == "kana":
        app.open_content_kana(KANA["id"])
        return app.show_kana_detail, KANA["id"]
    if kind == "word":
        app.open_content_word(WORD["id"])
        return app.show_word_detail, WORD["id"]
    app.open_content_source(SOURCE)
    return app.show_source, SOURCE


@pytest.mark.parametrize("kind", ["kana", "word", "source"])
def test_closing_plain_preview_keeps_page_and_back_stack(kind):
    async def run():
        app = harness()
        original = (app.page, app._view_key, app._page_token, list(app.back_stack.pages))
        navigate, _ = open_index(app, kind)
        navigate.assert_not_called()
        await asyncio.gather(*app.tasks)
        app.platform.preview_index.assert_awaited_once()
        title, message = app.platform.preview_index.await_args.args
        assert isinstance(title, str) and type(message) is str
        assert "https://" not in message
        navigate.assert_not_called()
        assert (app.page, app._view_key, app._page_token, app.back_stack.pages) == original
        assert app._preview_pending is None
        app.store.assert_not_called()
        assert app.store.mock_calls == ([call.get_setting(PITCH_DISPLAY_SETTING, {})] if kind == "word" else [])
    asyncio.run(run())


@pytest.mark.parametrize("kind", ["kana", "word", "source"])
def test_explicit_confirmation_calls_only_selected_detail_once(kind):
    async def run():
        app = harness()
        app.platform.preview_index.return_value = True
        navigate, expected = open_index(app, kind)
        navigate.assert_not_called()
        await asyncio.gather(*app.tasks)
        navigate.assert_called_once_with(expected)
        app.platform.preview_index.assert_awaited_once()
        assert sum(mock.call_count for mock in (app.show_kana_detail, app.show_word_detail, app.show_source)) == 1
        assert app._preview_pending is None
    asyncio.run(run())


def test_double_tap_and_other_index_while_waiting_open_one_dialog():
    async def run():
        app = harness()
        response = asyncio.get_running_loop().create_future()
        async def answer(*args, **kwargs):
            return await response
        app.platform.preview_index.side_effect = answer
        app.open_content_kana("ka")
        app.open_content_kana("ka")
        assert len(app.tasks) == 1
        await asyncio.sleep(0)
        app.open_content_word(WORD["id"])
        assert len(app.tasks) == 1
        app.platform.preview_index.assert_awaited_once()
        app.show_kana_detail.assert_not_called()
        response.set_result(True)
        await asyncio.gather(*app.tasks)
        app.show_kana_detail.assert_called_once_with("ka")
        app.show_word_detail.assert_not_called()
        assert app._preview_pending is None
    asyncio.run(run())


@pytest.mark.parametrize("changed", ["token", "view_key"])
def test_old_dialog_confirmation_cannot_navigate_after_page_changed(changed):
    async def run():
        app = harness()
        response = asyncio.get_running_loop().create_future()
        async def answer(*args, **kwargs):
            return await response
        app.platform.preview_index.side_effect = answer
        app.open_content_kana("ka")
        await asyncio.sleep(0)
        if changed == "token":
            app._page_token += 1
        else:
            app._view_key = ("show_settings", None)
        response.set_result(True)
        await asyncio.gather(*app.tasks)
        app.show_kana_detail.assert_not_called()
        assert app._preview_pending is None
    asyncio.run(run())


def test_abandoned_nonpractice_preview_task_never_shows_or_marks_assistance():
    async def run():
        app = harness(page="words", key=("show_words", None))
        app.session_id = "session-1"
        app.back_stack.pages.append(PageState(("show_question", None), "question", "Question", object(),
                                              {"session_id": app.session_id, "question_index": 0}))
        app.open_content_kana("ka")
        app._page_token += 1
        await asyncio.gather(*app.tasks)
        app.platform.preview_index.assert_not_awaited()
        assert not app._assisted_attempts and not app.answer_revealed
        assert app._preview_pending is None
    asyncio.run(run())


@pytest.mark.parametrize("kind,key", [
    ("kana", ("show_kana_detail", "ka")),
    ("word", ("show_word_detail", "jmdict:1")),
    ("source", ("show_source", "jmdict:1")),
])
def test_self_index_does_not_even_schedule_a_preview(kind, key):
    app = harness(key=key)
    navigate, _ = open_index(app, kind)
    assert not app.tasks and app._preview_pending is None
    app.platform.preview_index.assert_not_called()
    navigate.assert_not_called()


def test_current_and_pending_detail_self_spans_are_plain_text(monkeypatch):
    app = harness(key=("show_kana_detail", "ka"))
    app.kana_links = KanaLinkIndex([KANA, {"id": "na", "hiragana": "な", "katakana": "ナ"}])
    app.platform.link_kana_label.return_value = True
    monkeypatch.setattr(component_ui, "label", lambda text, **kwargs: SimpleNamespace(text=text))
    widget = KanaMemory.mixed_label(app, ContentText("かカ"))
    assert widget.text == "かカ"
    app.platform.link_kana_label.assert_not_called()
    # During construction the committed page still names the previous detail.
    app._render_key = ("show_kana_detail", "na")
    KanaMemory.mixed_label(app, "かな", ContentText("かカなナ"))
    widget, spans, callback = app.platform.link_kana_label.call_args.args
    assert widget.text == "かなかカなナ"
    assert [(span["start"], span["end"], span["kana_id"]) for span in spans] == [(2, 3, "ka"), (3, 4, "ka")]
    assert callback is app.open_content_kana


@pytest.mark.parametrize("kind", ["kana", "word", "source"])
@pytest.mark.parametrize("page", ["question", "word_question", "ink_question"])
def test_inline_learning_summary_marks_assistance_before_it_is_shown(kind, page):
    app = harness(page=page, key=("show_question", None))
    app.session_id = "session-1"
    app.question = SimpleNamespace(kind={"question": "kana_choice",
        "word_question": "word_meaning_kana", "ink_question": "kana_handwriting"}[page])

    def show_inline(*args, **kwargs):
        assert app._assisted_attempts == {("session-1", 0)}
        assert app.answer_revealed
        assert type(args[2]) is str

    app._show_practice_preview.side_effect = show_inline
    navigate, _ = open_index(app, kind)
    navigate.assert_not_called()
    app._show_practice_preview.assert_called_once()
    app.platform.preview_index.assert_not_called()
    assert not app.tasks and app._preview_pending is None
    expected_notice = "不计入无提示回忆" if page == "word_question" else "仅安排再学一次"
    assert expected_notice in app.question_notice.text
    assert app.store.mock_calls == []


def test_practice_external_reference_uses_inline_explicit_action():
    app = harness(page="feedback", key=("check_answer", None))
    app.session_id = "session-1"
    app._answered_attempts.add(app._attempt_key())
    url = "https://example.org/entry/1"
    app.open_url(url, title="例句出处", summary="原始例句与署名")
    app._show_practice_preview.assert_called_once()
    args, kwargs = app._show_practice_preview.call_args
    assert args[0] == ("external_url", url) and kwargs["action"] == "查看原文"
    assert url not in args[2]
    app.platform.open_url.assert_not_called()
    app.platform.preview_index.assert_not_called()
    assert not app.tasks and not app._assisted_attempts
    args[3]()
    app.platform.open_url.assert_called_once_with(url)


@pytest.mark.parametrize("origin_kind,target_kind", [("show_word_detail", "source"), ("show_source", "word")])
def test_word_and_citation_with_same_id_remain_different_destinations(origin_kind, target_kind):
    async def run():
        app = harness(key=(origin_kind, "jmdict:1"))
        app.platform.preview_index.return_value = True
        navigate, expected = open_index(app, target_kind)
        await asyncio.gather(*app.tasks)
        app.platform.preview_index.assert_awaited_once()
        navigate.assert_called_once_with(expected)
    asyncio.run(run())


def test_self_word_and_source_actions_hidden_but_other_type_is_retained(monkeypatch):
    app = harness(key=("show_word_detail", "jmdict:1"))
    app.content_label = lambda text, **kw: View(text=text)
    monkeypatch.setattr(component_ui, "label", lambda text, **kw: View(text=text))
    monkeypatch.setattr(component_ui, "col", lambda *children, **kw: View(*children))
    monkeypatch.setattr(component_ui, "row", lambda *children, **kw: View(*children))
    monkeypatch.setattr(component_ui, "button", lambda text, handler, **kw: View(text=text, on_press=handler))
    word_view = KanaMemory.word_link(app, WORD)
    assert len(word_view.children[0].children) == 1
    assert len(KanaMemory.source_buttons(app, [SOURCE["id"], SOURCE["id"]])) == 1
    app._view_key = ("show_source", "jmdict:1")
    assert KanaMemory.source_buttons(app, [SOURCE["id"]]) == []
    assert len(KanaMemory.word_link(app, WORD).children[0].children) == 2


@pytest.mark.parametrize("kind", ["kana", "word", "source"])
@pytest.mark.parametrize("preferences", [
    {"contour": True, "number": True}, {"contour": False, "number": True},
    {"contour": True, "number": False}, {"contour": False, "number": False},
])
def test_only_word_preview_receives_enabled_pitch_preferences(kind, preferences):
    async def run():
        app = harness()
        app.store.get_setting.return_value = preferences
        navigate, _ = open_index(app, kind)
        await asyncio.gather(*app.tasks)
        kwargs = app.platform.preview_index.await_args.kwargs
        if kind == "word" and any(preferences.values()):
            assert kwargs["pitch"] == {"record": WORD, "preferences": preferences}
        else:
            assert "pitch" not in kwargs
        assert kwargs["action"] == "查看详情"
        navigate.assert_not_called()
        app.store.set_setting.assert_not_called()
        app.store.record_review.assert_not_called()
    asyncio.run(run())


def test_external_page_only_opens_after_explicit_confirmation():
    async def run():
        app = harness()
        url = "https://example.org/entry/1?revision=2"
        app.open_url(url, title="例句出处", summary="原始例句与署名")
        app.platform.open_url.assert_not_called()
        await asyncio.gather(*app.tasks)
        app.platform.open_url.assert_not_called()
        assert app.platform.preview_index.await_args.kwargs["action"] == "查看原文"
        assert url not in app.platform.preview_index.await_args.args[1]
        app.platform.preview_index.return_value = True
        app.open_url(url)
        app.platform.open_url.assert_not_called()
        await asyncio.gather(*app.tasks)
        app.platform.open_url.assert_called_once_with(url)
    asyncio.run(run())


def test_preview_failure_releases_pending_gate_and_allows_retry():
    async def run():
        app = harness()
        app.platform.preview_index.side_effect = RuntimeError("dialog unavailable")
        app.open_content_kana("ka")
        await asyncio.gather(*app.tasks)
        app.alert.assert_called_once_with("dialog unavailable")
        app.show_kana_detail.assert_not_called()
        assert app._preview_pending is None
        app.platform.preview_index.side_effect = None
        app.platform.preview_index.return_value = True
        app.open_content_kana("ka")
        await asyncio.gather(*app.tasks)
        app.show_kana_detail.assert_called_once_with("ka")
    asyncio.run(run())
