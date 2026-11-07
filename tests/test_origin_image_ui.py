"""Origin cards are pure graphics, resize proportionally, and keep provenance."""
from kanamemory import ui_components as component_ui
from pathlib import Path
from dataclasses import replace
import sys
from types import SimpleNamespace
from unittest.mock import Mock
import weakref

import pytest

ROOT = Path(__file__).resolve().parents[1]
from kanamemory import app as app_module
from kanamemory.app import KanaMemory
from kanamemory.origin_images import OriginImage


class Node:
    def __init__(self, *children, text="", **kw):
        self.children, self.text = children, text
        self.style = kw.pop("style", SimpleNamespace())
        self.__dict__.update(kw)


def image(script="hiragana", name="a.png"):
    return OriginImage("kana-3042", script, "安", Path(name), ("glyph-image-reference", "letter-reference"), 960, 280)


def harness(monkeypatch, images):
    widgets = []
    def image_view(**kw):
        widget = Node(**kw)
        widget.refresh = Mock()
        widgets.append(widget)
        return widget
    monkeypatch.setattr(app_module.toga, "ImageView", image_view)
    monkeypatch.setattr(component_ui, "label", lambda text, **kw: Node(text=text))
    monkeypatch.setattr(component_ui, "row", lambda *children, **kw: Node(*children, **kw))
    return SimpleNamespace(
        main_window=SimpleNamespace(size=SimpleNamespace(width=440)),
        origin_images=SimpleNamespace(for_kana=Mock(return_value=images)),
        _origin_image_views=weakref.WeakSet(),
        entry_card=lambda *children, **kw: Node(*children),
        content_label=Mock(side_effect=AssertionError("origin cards must not contain links")),
        mixed_label=Mock(side_effect=AssertionError("origin cards must not contain links")),
    ), widgets


def test_cards_contain_only_script_label_and_image_with_sources_returned_separately(monkeypatch):
    app, widgets = harness(monkeypatch, [image(), image("katakana", "ka.png")])
    cards, sources = KanaMemory.origin_image_cards(app, {"id": "kana-3042"})
    assert [[node.text for node in card.children] for card in cards] == [["平假名", ""], ["片假名", ""]]
    assert [card.children[1] for card in cards] == widgets
    assert [widget.image for widget in widgets] == [Path("a.png"), Path("ka.png")]
    assert sources == ["glyph-image-reference", "letter-reference"]
    assert len(app._origin_image_views) == 2
    assert all(widget.style.width == 384 for widget in widgets)
    # Width only: Toga's native image backends calculate height from image ratio.
    assert all(widget.style.height == app_module.Pack().height for widget in widgets)
    app.content_label.assert_not_called()
    app.mixed_label.assert_not_called()


def test_resize_changes_width_only_and_skips_redundant_refresh(monkeypatch):
    app, widgets = harness(monkeypatch, [image()])
    cards, _ = KanaMemory.origin_image_cards(app, {})
    KanaMemory.resize_origin_images(app)
    widgets[0].refresh.assert_not_called()
    app.main_window.size.width = 320
    KanaMemory.resize_origin_images(app)
    assert cards[0].children[1].style.width == 264
    assert widgets[0].style.height == app_module.Pack().height
    widgets[0].refresh.assert_called_once()


def test_removed_images_are_not_retained_by_resize_registry(monkeypatch):
    app, widgets = harness(monkeypatch, [image()])
    cards, _ = KanaMemory.origin_image_cards(app, {})
    reference = weakref.ref(widgets[0])
    cards.clear()
    widgets.clear()
    assert reference() is None
    assert not app._origin_image_views


def test_missing_or_native_decode_failure_produces_no_placeholder_or_source_claim(monkeypatch):
    app, widgets = harness(monkeypatch, [])
    assert KanaMemory.origin_image_cards(app, {}) == ([], [])
    app.origin_images.for_kana.return_value = [image()]
    monkeypatch.setattr(app_module.toga, "ImageView", Mock(side_effect=ValueError("bad PNG")))
    assert KanaMemory.origin_image_cards(app, {}) == ([], [])
    assert not widgets and not app._origin_image_views


def test_compact_origins_share_one_row_with_independent_proportional_images(monkeypatch):
    app, widgets = harness(monkeypatch, [image(), image("katakana", "ka.png")])
    rows, sources = KanaMemory.origin_image_cards(app, {"id": "kana-3042"}, compact=True)
    assert len(rows) == 1
    assert len(rows[0].children) == 2
    assert [[node.text for node in card.children] for card in rows[0].children] == [["平假名", ""], ["片假名", ""]]
    assert [card.children[1] for card in rows[0].children] == widgets
    assert sources == ["glyph-image-reference", "letter-reference"]
    assert all(widget.style.width == 176 for widget in widgets)
    assert all(widget.style.height == app_module.Pack().height for widget in widgets)
    assert all(widget._origin_columns == 2 for widget in widgets)
    assert all(card.style.flex == 1 for card in rows[0].children)
    app.content_label.assert_not_called()
    app.mixed_label.assert_not_called()


@pytest.mark.parametrize("count", [1, 2])
def test_compact_resize_never_expands_an_image_to_full_width(monkeypatch, count):
    app, widgets = harness(monkeypatch, [image(), image("katakana", "ka.png")][:count])
    rows, _ = KanaMemory.origin_image_cards(app, {}, compact=True)
    assert len(rows) == 1 and len(rows[0].children) == count
    KanaMemory.resize_origin_images(app)
    assert all(widget.refresh.call_count == 0 for widget in widgets)

    app.main_window.size.width = 320
    KanaMemory.resize_origin_images(app)
    assert all(widget.style.width == 116 for widget in widgets)
    assert all(widget.style.height == app_module.Pack().height for widget in widgets)
    assert all(widget.refresh.call_count == 1 for widget in widgets)
    KanaMemory.resize_origin_images(app)
    assert all(widget.refresh.call_count == 1 for widget in widgets)

    supplied_window = SimpleNamespace(size=SimpleNamespace(width=640))
    KanaMemory.resize_origin_images(app, supplied_window)
    assert all(widget.style.width == 276 for widget in widgets)
    assert all(widget.style.height == app_module.Pack().height for widget in widgets)
    assert all(widget.refresh.call_count == 2 for widget in widgets)


def test_mixed_saved_pages_keep_each_images_original_column_count_on_resize(monkeypatch):
    app, widgets = harness(monkeypatch, [image()])
    compact, _ = KanaMemory.origin_image_cards(app, {}, compact=True)
    full_width, _ = KanaMemory.origin_image_cards(app, {})
    assert compact[0].children[0].children[1] is widgets[0]
    assert full_width[0].children[1] is widgets[1]
    app.main_window.size.width = 320
    KanaMemory.resize_origin_images(app)
    assert [widget.style.width for widget in widgets] == [116, 264]
    assert all(widget.style.height == app_module.Pack().height for widget in widgets)


def test_compact_decode_failure_keeps_surviving_image_small_and_no_phantom_source(monkeypatch):
    broken = image()
    survivor = OriginImage("kana-3042", "katakana", "阿", Path("ka.png"), ("surviving-source",), 960, 280)
    app, widgets = harness(monkeypatch, [broken, survivor])
    normal_image_view = app_module.toga.ImageView

    def decode(**kwargs):
        if kwargs["image"] == Path("a.png"):
            raise ValueError("bad PNG")
        return normal_image_view(**kwargs)

    monkeypatch.setattr(app_module.toga, "ImageView", decode)
    rows, sources = KanaMemory.origin_image_cards(app, {}, compact=True)
    assert len(rows) == 1 and len(rows[0].children) == 1
    assert len(widgets) == 1
    assert widgets[0].image == Path("ka.png")
    assert widgets[0].style.width == 176
    assert widgets[0].style.height == app_module.Pack().height
    assert sources == ["surviving-source"]


def test_teaching_reference_requires_explicit_review_view_and_remains_noninteractive(monkeypatch):
    primary = replace(image(), path=Path("comparison.png"), width=640,
                      reference_path=Path("teaching.png"), reference_width=960,
                      reference_height=280, review_note="字源有文献依据；具体中间笔形未核实。")
    app, widgets = harness(monkeypatch, [primary, image("katakana", "ka.png")])
    cards, _ = KanaMemory.origin_image_cards(app, {}, compact=True)
    assert [widget.image for widget in widgets] == [Path("comparison.png"), Path("ka.png")]
    assert cards[0].children[0].children[0].text == "平假名 · 字源对照"
    app.origin_image_cards = lambda item, **kw: KanaMemory.origin_image_cards(app, item, **kw)
    review = KanaMemory.origin_review_cards(app, {})
    assert review[0].text == primary.review_note
    assert len(review) == 2  # One factual note, without a duplicate warning paragraph.
    assert review[1].children[0].text == "现代教学示意 · 中间笔形待核"
    assert [widget.image for widget in widgets[2:]] == [Path("teaching.png")]
    app.content_label.assert_not_called()
    app.mixed_label.assert_not_called()
