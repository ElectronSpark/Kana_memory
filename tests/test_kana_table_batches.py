"""Render all kana immediately; keep catalog batching and navigation state."""
from kanamemory import ui_components as component_ui
from collections import Counter
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
from kanamemory import app as app_module
from kanamemory.app import KanaMemory, CATEGORY_LABELS
from kanamemory.navigation import BackStack, PageState


from support.kana_table_batches_support import View, walk, builder


def kana_items(groups):
    result = []
    for category, count in groups:
        for index in range(count):
            key = f"{category}-{index}"
            result.append(dict(id=key, category=category, hiragana=f"平{key}", katakana=f"片{key}", romaji=key))
    return result


def forms(grid):
    return [node for node in walk(grid) if node.kind == "button" and node.style.get("compact")]


def more_button(grid, prefix="显示更多假名"):
    return next((node for node in walk(grid) if node.text.startswith(prefix)), None)


@pytest.mark.parametrize("count", [0, 1, 24, 25, 46, 165])
def test_tables_render_every_item_without_extra_controls(builder, count):
    app, trace, buttons = builder
    items = kana_items([("basic", count)])
    app.repo = SimpleNamespace(kana_list=lambda **kw: items)
    KanaMemory.refresh_kana(app)
    assert len(forms(app.kana_grid)) == count
    assert more_button(app.kana_grid) is None
    assert all(len(node.children) == 6 for node in walk(app.kana_grid) if node.kind == "row")
    assert len([event for event in trace if event[0] == "attach"]) == 1


def test_all_groups_keep_their_direct_targets_without_expansion(builder):
    app, trace, native_buttons = builder
    items = kana_items([("basic", 46), ("voiced", 25), ("yoon", 33), ("historical", 7)])
    app.repo = SimpleNamespace(kana_list=lambda **kw: items)
    KanaMemory.refresh_kana(app)
    assert len(forms(app.kana_grid)) == len(items)
    assert len(native_buttons) == len(items)
    assert more_button(app.kana_grid) is None
    assert all(len(node.children) == 6 for node in walk(app.kana_grid) if node.kind == "row")
    actual = Counter()
    for node in forms(app.kana_grid):
        node.on_press(node)
        for form in node.text.splitlines():
            actual[form, app.open_table_kana.call_args.args[0]] += 1
    assert actual == Counter((item[form], item["id"]) for item in items for form in ("hiragana", "katakana"))


@pytest.mark.parametrize("scale, minimum", [(1.3, 93), (2, 144)])
def test_large_font_table_reserves_width_for_three_character_forms(builder, monkeypatch, scale, minimum):
    app, _, buttons = builder
    monkeypatch.setattr(component_ui, "font_scale", lambda: scale)
    item = dict(id="combo", category="foreign_combination", hiragana="てぃゃ", katakana="ティャ", romaji="tya")
    list(KanaMemory._kana_table_rows(app, [item]))
    assert buttons and all(node.style["width"] >= minimum for node in buttons)
    assert buttons[0].text == "てぃゃ\nティャ"


def test_filter_change_replaces_complete_table_without_duplicate_widgets(builder):
    app, trace, native_buttons = builder
    items = kana_items([("basic", 46), ("yoon", 33)])
    query = Mock(side_effect=lambda category=None: [item for item in items if category is None or item["category"] == category])
    app.repo = SimpleNamespace(kana_list=query)
    KanaMemory.refresh_kana(app)
    old_forms = forms(app.kana_grid)
    app.kana_filter.value = CATEGORY_LABELS["yoon"]
    KanaMemory.refresh_kana(app)
    assert query.call_args.kwargs == {"category": "yoon"}
    current = list(forms(app.kana_grid))
    assert len(current) == 33 and all("yoon-" in node.text for node in current)
    assert not any(node in current for node in old_forms)
    assert more_button(app.kana_grid) is None


def restore_from_detail(app, content, page_name, bindings):
    previous = PageState(("show_" + page_name, None), page_name, "列表", content, bindings)
    stack = BackStack()
    stack.pages.append(previous)
    app.__dict__.update(page="kana_detail", _view_key=("show_kana_detail", "detail"),
        _page_token=5, _ink_revision=0, _audio_serial=0, _paint_handle=None, question=None,
        back_stack=stack, content_slot=View(View(text="详情")), title_label=SimpleNamespace(text="详情"),
        platform=Mock(), _sync_back_handler=Mock())
    assert KanaMemory.go_back(app) is True
    assert app.content_slot.children == [content]


def test_back_retains_filter_and_complete_kana_widgets(builder):
    app, trace, buttons = builder
    app.repo = SimpleNamespace(kana_list=lambda **kw: kana_items([("yoon", 63)]))
    app.kana_filter.value = CATEGORY_LABELS["yoon"]
    KanaMemory.refresh_kana(app)
    old_forms, old_grid, old_filter = list(forms(app.kana_grid)), app.kana_grid, app.kana_filter
    assert len(old_forms) == 63
    app.kana_grid, app.kana_filter = View(), SimpleNamespace(value="全部")
    restore_from_detail(app, old_grid, "kana", {"kana_grid": old_grid, "kana_filter": old_filter})
    assert app.kana_filter.value == CATEGORY_LABELS["yoon"]
    assert forms(app.kana_grid) == old_forms
    assert more_button(app.kana_grid) is None


@pytest.mark.parametrize("count", [0, 20, 21, 74])
def test_catalog_constructs_at_most_twenty_per_batch_and_all_preview_targets_remain(builder, count):
    app, trace, buttons = builder
    characters = [chr(0x4E00 + index) for index in range(count)]
    app.repo = SimpleNamespace(historical_characters=lambda: characters)
    KanaMemory.show_character_catalog.__wrapped__(app)
    root = View(*app.screen.call_args.args[2:])
    # The shared compact header owns Back; the catalog body contains only
    # character targets and (when needed) its expansion control.
    assert len(buttons) == min(count, 20) + int(count > 20)
    assert not any("返回" in node.text for node in buttons)
    while (more := more_button(root, "显示更多汉字")) is not None:
        previous = len(buttons)
        trace.clear()
        more.on_press(more)
        added = len(buttons) - previous
        assert 1 <= added <= 20
        assert [event[0] for event in trace] == ["build"] * added + ["attach"]
    targets = [node for node in walk(root) if node.kind == "button" and node.text in characters]
    assert [node.text for node in targets] == characters
    for node in targets:
        node.on_press(node)
        assert app.open_character_readings.call_args.args == (node.text,)


def test_catalog_back_keeps_expanded_buttons_and_can_continue(builder):
    app, trace, buttons = builder
    app.repo = SimpleNamespace(historical_characters=lambda: [chr(0x4E00+i) for i in range(74)])
    KanaMemory.show_character_catalog.__wrapped__(app)
    root = View(*app.screen.call_args.args[2:])
    more = more_button(root, "显示更多汉字")
    more.on_press(more)
    original = tuple(buttons)
    restore_from_detail(app, root, "character_catalog", {})
    assert tuple(buttons) == original
    more.on_press(more)
    assert len(buttons) == len(original) + 20
    more.on_press(more)
    assert more_button(root, "显示更多汉字") is None
