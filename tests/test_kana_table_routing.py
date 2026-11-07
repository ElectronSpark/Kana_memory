"""The table opens details directly; embedded content retains its preview gate."""
from kanamemory import ui_components as component_ui
from collections import Counter
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
from kanamemory import app as app_module
from kanamemory.app import KanaMemory
from kanamemory.content import ContentRepository
from kanamemory.kana_links import ContentText, KanaLinkIndex
from kanamemory.navigation import BackStack, PageState


KANA = {"id": "kana-304b", "hiragana": "か", "katakana": "カ", "romaji": "ka", "category": "basic"}


def state():
    app = SimpleNamespace(
        page="kana", _view_key=("show_kana", None), _render_key=None,
        session_id=None, question_index=0, answer_revealed=False,
        _answered_attempts=set(), _assisted_attempts=set(), back_stack=BackStack(),
        question_notice=SimpleNamespace(text=""), platform=Mock(), repo=Mock(),
        store=Mock(), show_kana_detail=Mock(), _preview_link=Mock(), _checkpoint=Mock(),
    )
    app.repo.get_kana.return_value = KANA
    for method in ("_attempt_key", "_content_page_key", "_mark_lookup_assisted", "open_table_kana", "open_content_kana"):
        setattr(app, method, MethodType(getattr(KanaMemory, method), app))
    return app


def test_table_opens_detail_immediately_without_preview_or_review():
    app = state()
    app.open_table_kana(KANA["id"])
    app.show_kana_detail.assert_called_once_with(KANA["id"])
    app._preview_link.assert_not_called()
    app.platform.preview_index.assert_not_called()
    assert app.store.mock_calls == [] and not app._assisted_attempts


@pytest.mark.parametrize("self_location", ["_view_key", "_render_key"])
def test_table_does_not_reopen_the_current_or_being_built_detail(self_location):
    app = state()
    setattr(app, self_location, ("show_kana_detail", KANA["id"]))
    app.open_table_kana(KANA["id"])
    app.repo.get_kana.assert_not_called()
    app.show_kana_detail.assert_not_called()
    app._preview_link.assert_not_called()


def test_unknown_table_id_cannot_reveal_or_navigate_a_pending_question():
    app = state()
    app.page, app.session_id = "question", "session-1"
    app.repo.get_kana.return_value = None
    app.open_table_kana("missing")
    assert not app._assisted_attempts and not app.answer_revealed
    app.show_kana_detail.assert_not_called()
    assert app.store.mock_calls == []


def test_direct_table_lookup_marks_suspended_attempt_before_capturing_detail():
    app = state()
    app.session_id = "session-1"
    app.back_stack.pages.append(PageState(
        ("show_question", None), "question", "练习", object(),
        {"session_id": "session-1", "question_index": 0, "answer_revealed": False},
    ))
    def navigate(kana_id):
        assert app.answer_revealed and app._assisted_attempts == {("session-1", 0)}
    app.show_kana_detail.side_effect = navigate
    app.open_table_kana(KANA["id"])
    app.show_kana_detail.assert_called_once_with(KANA["id"])
    app._preview_link.assert_not_called()
    assert app.store.mock_calls == []


def test_completed_attempt_is_not_reclassified_by_later_table_lookup():
    app = state()
    app.session_id = "session-1"
    app._answered_attempts.add(("session-1", 0))
    app.back_stack.pages.append(PageState(
        ("show_question", None), "question", "练习", object(),
        {"session_id": "session-1", "question_index": 0},
    ))
    app.open_table_kana(KANA["id"])
    assert not app._assisted_attempts and not app.answer_revealed
    app.show_kana_detail.assert_called_once_with(KANA["id"])


class Widget:
    def __init__(self, *children, text=None, handler=None, **style):
        self.children = list(children)
        self.text, self.handler = text, handler

    def add(self, *children):
        self.children.extend(children)

    def clear(self):
        self.children.clear()

    def remove(self, child):
        self.children.remove(child)


def descendants(widget):
    yield widget
    for child in widget.children:
        yield from descendants(child)


def test_every_shipped_hiragana_and_katakana_cell_binds_its_own_direct_target(monkeypatch):
    monkeypatch.setattr(component_ui, "col", Widget)
    monkeypatch.setattr(component_ui, "row", Widget)
    monkeypatch.setattr(component_ui, "label", lambda text, **kw: Widget(text=text, **kw))
    monkeypatch.setattr(component_ui, "button", lambda text, handler, **kw: Widget(text=text, handler=handler, **kw))
    with ContentRepository(ROOT / "app/src/kanamemory/resources/content.sqlite3") as repo:
        items = repo.kana_list()
        app = SimpleNamespace(
            repo=repo, kana_filter=SimpleNamespace(value="全部"), kana_grid=Widget(),
            main_window=SimpleNamespace(size=SimpleNamespace(width=420)),
            heading=lambda text: Widget(text=text), open_table_kana=Mock(),
            open_content_kana=Mock(), content_label=Mock(side_effect=AssertionError("Table forms must not enter the content preview route")),
        )
        KanaMemory.refresh_kana(app)
        assert not any("显示更多" in (w.text or "") for w in descendants(app.kana_grid))
        expected = Counter((form, item["id"]) for item in items
                           for form in set(filter(None, (item.get("hiragana"), item.get("katakana")))))
        actual = Counter()
        for widget in descendants(app.kana_grid):
            if widget.handler is not None:
                widget.handler(None)
                for form in widget.text.splitlines():
                    actual[(form, app.open_table_kana.call_args.args[0])] += 1
        assert actual == expected
        assert app.open_table_kana.call_count == len(items)
        app.open_content_kana.assert_not_called()
        app.content_label.assert_not_called()
        # Refresh discards the old widgets rather than duplicating either script.
        KanaMemory.refresh_kana(app)
        assert sum(w.handler is not None for w in descendants(app.kana_grid)) == len(items)


def test_inline_content_still_enters_preview_even_after_table_has_direct_route(monkeypatch):
    app = state()
    app.page, app._view_key = "word_detail", ("show_word_detail", "jmdict:1")
    app.kana_links = KanaLinkIndex([KANA])
    app.platform.link_kana_label.return_value = True
    monkeypatch.setattr(component_ui, "label", lambda text, **kw: Widget(text=text))
    KanaMemory.mixed_label(app, "读法：", ContentText("かカ"))
    _, spans, callback = app.platform.link_kana_label.call_args.args
    assert [s["kana_id"] for s in spans] == [KANA["id"], KANA["id"]]
    assert callback is app.open_content_kana
    callback(KANA["id"])
    app.show_kana_detail.assert_not_called()
    app._preview_link.assert_called_once()
    assert app._preview_link.call_args.args[0] == ("show_kana_detail", KANA["id"])
    assert app._preview_link.call_args.kwargs["assisted"] is True

