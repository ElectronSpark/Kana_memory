"""Reading cards retain inline audio while native cards build on demand."""
from kanamemory import ui_components as component_ui
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
from kanamemory import app as app_module
from kanamemory.app import KanaMemory
from kanamemory.detail_content import dialect_entry
from kanamemory.detail_enrichment import history_entries
from kanamemory.kana_links import ContentText
from kanamemory.navigation import BackStack, PageState
from kanamemory.reading_audio import MODERN, MIDDLE_CHINESE
from support.reading_audio_support import modern
from support.reading_audio_support import historical
from support.reading_audio_ui_support import nodes


class View:
    def __init__(self, *children, trace, text="", on_press=None):
        self.children = list(children)
        self.trace, self.text, self.on_press = trace, text, on_press

    def add(self, *children):
        self.trace.append(("attach", children))
        self.children.extend(children)

    def remove(self, child):
        self.children.remove(child)

    def clear(self):
        self.children.clear()


def records(count, kind):
    result = []
    for index in range(count):
        row, audio = (modern if kind == MODERN else historical)()
        row["id"] = f"reading-{index}"
        audio.update(id=f"audio-{index}", reading_id=row["id"],
                     source_ids=["shared-source", f"source-{index}"],
                     source_url=f"https://example.org/audio-{index}",
                     local_path=f"media/audio-{index}.ogg", recording_scope=f"sample-{index}")
        result.append(row)
    return result


def entries(records, kind):
    return [dialect_entry(record) for record in records] if kind == MODERN else history_entries({"historical_reading_references": records})


@pytest.fixture
def builder(monkeypatch):
    built, labels, trace = [], [], []

    def make_label(text, **kwargs):
        labels.append(text)
        return View(trace=trace, text=text)

    def entry_card(*children, index):
        card = View(*children, trace=trace)
        card.index = index
        built.append(card)
        trace.append(("build", card))
        return card

    monkeypatch.setattr(component_ui, "label", make_label)
    monkeypatch.setattr(component_ui, "button", lambda text, handler, **kw: View(trace=trace, text=text, on_press=handler))
    monkeypatch.setattr(component_ui, "col", lambda *children, **kw: View(*children, trace=trace))
    monkeypatch.setattr(component_ui, "row", lambda *children, **kw: View(*children, trace=trace))
    monkeypatch.setattr(component_ui, "speaker_button", lambda handler, description="播放读音": View(trace=trace, text="🔊", on_press=handler))
    monkeypatch.setattr(app_module, "recording_path", Mock(return_value=SimpleNamespace(is_file=lambda: True)))
    app = SimpleNamespace(entry_card=entry_card, play_recording=Mock(), open_url=Mock())
    app.mixed_label = lambda *parts, **kw: make_label("".join(p.text if isinstance(p, ContentText) else p for p in parts), **kw)
    app.detail_entry = MethodType(KanaMemory.detail_entry, app)
    return app, built, labels, trace


@pytest.mark.parametrize("count", [0, 1, 6])
def test_short_reading_groups_have_one_card_per_reading(builder, count):
    app, built, labels, trace = builder
    cards = KanaMemory.detail_entries(app, entries(records(count, MODERN), MODERN))
    assert cards == built and len(cards) == count
    assert [card.index for card in cards] == list(range(count))
    assert sum(node.text == "🔊" for node in nodes(cards)) == count
    assert [event[0] for event in trace] == ["build"] * count


@pytest.mark.parametrize("kind", [MODERN, MIDDLE_CHINESE])
@pytest.mark.parametrize("count", [7, 9, 14])
def test_long_reading_groups_bound_native_construction_and_keep_audio_in_each_card(builder, kind, count):
    app, built, labels, trace = builder
    inputs = records(count, kind)
    rendered = KanaMemory.detail_entries(app, entries(inputs, kind))
    section, = rendered
    cards, more = section.children
    assert len(built) == 4 and cards.children == built
    assert "音频范围：sample-3" in labels and "音频范围：sample-4" not in labels
    assert str(count - 4) in more.text

    while more in section.children:
        previous = len(built)
        trace.clear()
        more.on_press(more)
        added = len(built) - previous
        assert added == min(6, count - previous)
        assert [event[0] for event in trace] == ["build"] * added + ["attach"]
        assert list(trace[-1][1]) == built[previous:]
        assert cards.children == built
        if len(built) < count:
            assert str(count - len(built)) in more.text
    assert section.children == [cards]
    assert [card.index for card in cards.children] == list(range(count))
    assert len({id(card) for card in cards.children}) == count
    # Expanded buttons retain their own audio and provenance, not the last row.
    first_expanded = cards.children[4]
    play = next(child for child in nodes([first_expanded]) if child.text == "🔊")
    play.on_press(play)
    assert app.play_recording.call_args.args[0]["id"] == "audio-4"
    source = next(child for child in first_expanded.children if child.text == "音频出处")
    source.on_press(source)
    assert app.open_url.call_args.args == ("https://example.org/audio-4",)
    assert len([n for n in nodes(cards.children) if n.text == "🔊"]) == count


def test_missing_audio_keeps_reading_without_a_play_or_audio_source_control(builder, monkeypatch):
    app, built, labels, trace = builder
    inputs = records(7, MODERN)

    def resolve(folder, audio):
        if audio["id"] == "audio-5":
            raise ValueError("unsafe")
        return SimpleNamespace(is_file=lambda: audio["id"] != "audio-6")

    monkeypatch.setattr(app_module, "recording_path", resolve)
    rendered = KanaMemory.detail_entries(app, entries(inputs, MODERN))
    cards, more = rendered[0].children
    assert len(built) == 4 and more.text == "显示更多（还有 3 条）"
    more.on_press(more)
    assert len(cards.children) == 7
    assert len([n for n in nodes(cards.children) if n.text == "🔊"]) == 5
    assert all(not n.on_press for card in cards.children[5:] for n in nodes([card]))
    assert "音频范围：sample-5" not in labels and "音频范围：sample-6" not in labels


def test_back_preserves_expanded_reading_widgets_and_remaining_audio_binding(builder):
    app, built, labels, trace = builder
    rendered = KanaMemory.detail_entries(app, entries(records(17, MODERN), MODERN))
    section, = rendered
    cards, more = section.children
    more.on_press(more)
    assert len(built) == 10
    original_cards = tuple(cards.children)
    slot = View(section, trace=trace)
    key = ("show_character_readings", "安")
    stack = BackStack()
    stack.pages.append(PageState(key, "character_readings", "字音详情", section))
    slot.clear()
    slot.add(View(trace=trace, text="来源详情"))
    app.__dict__.update(
        page="source", _view_key=("show_source", "source-4"),
        _page_token=8, _ink_revision=1, _audio_serial=0,
        _paint_handle=None, question=None, back_stack=stack,
        content_slot=slot, title_label=SimpleNamespace(text="来源详情"),
        platform=Mock(), _sync_back_handler=Mock(), _checkpoint=Mock(),
    )
    assert KanaMemory.go_back(app) is True
    assert app._page_token == 9 and slot.children == [section]
    assert tuple(cards.children) == original_cards and len(built) == 10
    more.on_press(more)
    assert len(built) == 16 and tuple(cards.children[:10]) == original_cards
    more.on_press(more)
    assert len(built) == 17 and section.children == [cards]
    assert [card.index for card in cards.children] == list(range(17))
    speaker = next(n for n in nodes([cards.children[-1]]) if n.text == "🔊")
    speaker.on_press(speaker)
    assert app.play_recording.call_args.args[0]["id"] == "audio-16"
