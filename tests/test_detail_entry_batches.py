"""Long reference lists create bounded native batches and survive real Back."""
from kanamemory import ui_components as component_ui
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
from kanamemory import app as app_module
from kanamemory.app import KanaMemory
from kanamemory.navigation import BackStack, PageState


class View:
    """A retained widget tree that records each live attachment separately."""

    def __init__(self, *children, trace, text="", on_press=None):
        self.children = list(children)
        self.trace = trace
        self.text = text
        self.on_press = on_press

    def add(self, *children):
        self.trace.append(("attach", children))
        self.children.extend(children)

    def remove(self, child):
        self.children.remove(child)

    def clear(self):
        self.children.clear()


@pytest.fixture
def builder(monkeypatch):
    trace, built = [], []

    def detail_entry(entry, index):
        card = SimpleNamespace(entry=entry, index=index)
        built.append(card)
        trace.append(("build", card))
        return card

    monkeypatch.setattr(component_ui, "col", lambda *children, **kw: View(*children, trace=trace))
    monkeypatch.setattr(component_ui, "button", lambda text, handler, **kw: View(trace=trace, text=text, on_press=handler))
    return SimpleNamespace(detail_entry=detail_entry), built, trace


@pytest.mark.parametrize("count", [0, 1, 6])
def test_short_lists_still_render_all_entries_without_an_expansion_control(builder, count):
    app, built, trace = builder
    entries = [object() for _ in range(count)]
    rendered = KanaMemory.detail_entries(app, iter(entries))
    assert rendered == built
    assert [(card.entry, card.index) for card in rendered] == list(zip(entries, range(count)))
    assert all(kind == "build" for kind, _ in trace)


@pytest.mark.parametrize("count", [7, 10, 11, 22])
def test_long_lists_bound_construction_and_mount_each_complete_batch_once(builder, count):
    app, built, trace = builder
    entries = [object() for _ in range(count)]
    rendered = KanaMemory.detail_entries(app, iter(entries))
    assert len(rendered) == 1
    section = rendered[0]
    cards, more = section.children
    # This is the performance contract: hidden native cards do not exist yet.
    assert len(built) == 4
    assert cards.children == built
    assert str(count - 4) in more.text

    while more in section.children:
        previous = len(built)
        trace.clear()
        more.on_press(more)
        added = len(built) - previous
        assert 1 <= added <= 6
        assert added == min(6, count - previous)
        assert [event[0] for event in trace] == ["build"] * added + ["attach"]
        assert list(trace[-1][1]) == built[previous:]
        assert cards.children == built
        if len(built) < count:
            assert more in section.children
            assert str(count - len(built)) in more.text

    assert section.children == [cards]
    assert [card.entry for card in cards.children] == entries
    # Global indices preserve alternating backgrounds across expansion boundaries.
    assert [card.index for card in cards.children] == list(range(count))
    assert len({id(card.entry) for card in cards.children}) == count


def test_real_back_reattaches_the_same_section_and_expansion_continues_after_token_change(builder):
    app, built, trace = builder
    entries = [object() for _ in range(17)]
    section = KanaMemory.detail_entries(app, entries)[0]
    cards, more = section.children
    more.on_press(more)
    assert len(built) == 10

    # Model opening a source and returning through the actual navigation method.
    # The saved page owns the widget closure, rather than an external cursor cache.
    slot = View(section, trace=trace)
    key = ("show_kana_detail", "kana-3042")
    previous = PageState(key, "kana_detail", "假名详情", section)
    stack = BackStack()
    stack.pages.append(previous)
    slot.clear()
    slot.add(View(trace=trace, text="来源详情"))
    app.__dict__.update(
        page="source", _view_key=("show_source", "source-1"),
        _page_token=8, _ink_revision=1, _audio_serial=0,
        _paint_handle=None, question=None, back_stack=stack,
        content_slot=slot, title_label=SimpleNamespace(text="来源详情"),
        platform=Mock(), _sync_back_handler=Mock(), _checkpoint=Mock(),
    )

    assert KanaMemory.go_back(app) is True
    assert app._page_token == 9
    assert app._view_key == key
    assert slot.children == [section]
    assert section.children[0] is cards
    assert len(built) == 10  # Returning must not recreate already expanded cards.
    more.on_press(more)
    assert len(built) == 16
    more.on_press(more)
    assert section.children == [cards]
    assert [card.entry for card in cards.children] == entries
    assert [card.index for card in cards.children] == list(range(17))
