"""Pitch is a wrapping annotation, never an answer or a clipped text row."""
from kanamemory import ui_components as component_ui
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
from kanamemory import app as app_module
from kanamemory import text_layout
from kanamemory.pitch_accent import morae, word_pitch_variants
from kanamemory.pitch_display import (
    PITCH_DISPLAY_SETTING, _mora_lines, build_pitch_block,
    clean_pitch_preferences, native_pitch_preview, pitch_display_variants,
)


from support.pitch_display_support import Node, walk


def texts(node):
    return [item.text for item in walk(node) if item.text]


def record(reading="あめ", raw="0, 2"):
    return {"reading": reading, "pitch_accent": [
        {"reading": reading, "raw": raw, "source_form": "雨", "source_ids": ["dictionary"]}]}


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(component_ui, "label", lambda text, **style: Node("label", text=text, **style))
    monkeypatch.setattr(component_ui, "row", lambda *children, **style: Node("row", *children, **style))
    monkeypatch.setattr(component_ui, "col", lambda *children, **style: Node("col", *children, **style))
    monkeypatch.setattr(text_layout, "font_scale", lambda: 1)
    return SimpleNamespace(main_window=SimpleNamespace(size=SimpleNamespace(width=360)),
        content_label=lambda text, **style: Node("linked", text=text, **style))


@pytest.mark.parametrize("invalid", [None, False, [], "off", {"contour": "false", "number": 0}])
def test_settings_are_boolean_and_default_both_on(invalid):
    assert PITCH_DISPLAY_SETTING == "pitch_display_v1"
    assert clean_pitch_preferences(invalid) == {"contour": True, "number": True}


@pytest.mark.parametrize("contour,number", [(True, True), (True, False), (False, True), (False, False)])
def test_independent_switches_persist_explicit_false(contour, number):
    value = {"contour": contour, "number": number, "future": 7}
    assert clean_pitch_preferences(value) == {"contour": contour, "number": number}
    assert value["future"] == 7


def test_disabled_or_missing_annotation(app):
    assert build_pitch_block(app, record(), contour=False, number=False, show_missing=True) is None
    assert build_pitch_block(app, {"reading": "あめ"}) is None
    missing = build_pitch_block(app, {"reading": "あめ"}, show_missing=True)
    assert texts(missing) == ["音高资料未收录"]
    assert not missing._pitch_has_contour


def test_flat_and_final_fall_have_same_marks_but_distinct_numbers(app):
    result = build_pitch_block(app, record())
    assert result._pitch_has_contour
    assert [text for text in texts(result) if text.startswith("调号")] == ["调号 [0]", "调号 [2]"]
    assert [text for text in texts(result) if text in ("-", "_")] == ["_", "-", "_", "-"]


def test_marks_are_above_complete_morae(app):
    result = build_pitch_block(app, record("ティッシュ", "1"))
    rows = [item for item in walk(result) if item.kind == "row"]
    assert [[texts(cell) for cell in row.children] for row in rows] == [
        [["-", "ティ"], ["_", "ッ"], ["_", "シュ"]]]
    assert all("height" not in item.style for item in walk(result))
    # Learning glyphs keep their own typeface while pitch strokes retain their
    # fixed-width alignment and the Chinese notation caption stays UI text.
    assert all(cell.children[0].style["font_family"] == "monospace"
               and cell.children[1].style["text_role"] == "study"
               for row in rows for cell in row.children)
    assert all(item.style.get("text_role", "ui") == "ui"
               for item in walk(result) if item.text.startswith("调号"))


def test_only_numbers_are_one_wrapping_caption_without_reading(app):
    result = build_pitch_block(app, record("あいにく", "(名)4,0,(副)0"), contour=False)
    assert texts(result) == ["调号 [4]（名） · [0]（名） · [0]（副）"]
    assert not result._pitch_has_contour
    assert "width" not in result.style and "height" not in result.style


def test_contour_without_numbers_preserves_variant_qualifiers(app):
    result = build_pitch_block(app, record("あいにく", "(名)4,0,(副)0"), number=False, compact=True)
    assert "名" in texts(result) and "副" in texts(result)
    assert not any("[" in text for text in texts(result))
    assert len([text for text in texts(result) if text in ("-", "_")]) == 12


def test_masked_question_shows_only_pitch_without_kana_or_answer_placeholders(app):
    original = record("ティッシュ", "1")
    before = deepcopy(original)
    result = build_pitch_block(app, original, masked=True, link_kana=True)
    rows = [item for item in walk(result) if item.kind == "row"]
    assert [texts(cell) for cell in rows[0].children] == [["-"], ["_"], ["_"]]
    assert len({cell.style["width"] for cell in rows[0].children}) == 1
    assert not any(char in text for text in texts(result) for char in "□ティッシュ")
    assert not any(item.kind == "linked" for item in walk(result))
    assert original == before


def test_links_are_enabled_only_for_requested_visible_content(app):
    result = build_pitch_block(app, record("きょう", "1"), link_kana=True)
    assert [node.text for node in walk(result) if node.kind == "linked"] == ["きょ", "う"]
    assert all(node.style["text_role"] == "study" for node in walk(result) if node.kind == "linked")
    plain = build_pitch_block(app, record("きょう", "1"))
    assert not any(node.kind == "linked" for node in walk(plain))


@pytest.mark.parametrize("width,scale", [(320, 1), (320, 2), (360, 2), (200, 3)])
def test_large_type_and_long_words_wrap_whole_morae_without_horizontal_growth(app, monkeypatch, width, scale):
    app.main_window.size.width = width
    monkeypatch.setattr(text_layout, "font_scale", lambda: scale)
    reading = "ティッシュペーパーきゃきゅきょきゃきゅきょ"
    result = build_pitch_block(app, record(reading, "0"), size=26)
    rows = [item for item in walk(result) if item.kind == "row"]
    assert len(rows) > 1
    assert [cell.children[1].text for row in rows for cell in row.children] == list(morae(reading))
    assert all(sum(cell.style["width"] for cell in row.children) <= width - 112 + 1e-6 for row in rows)
    assert all("height" not in item.style for item in walk(result))


@pytest.mark.parametrize("scale,reading", [
    (1.6, "あいうえおかき"),
    (2.0, "きゃきゅきょティシュファ"),
])
def test_nested_word_example_reserves_card_and_detail_button_width(app, monkeypatch, scale, reading):
    app.main_window.size.width = 411
    monkeypatch.setattr(text_layout, "font_scale", lambda: scale)
    result = build_pitch_block(app, record(reading, "0"), size=18, compact=True)
    rows = [item for item in walk(result) if item.kind == "row"]
    # The nested caller has screen/card padding and a 68 dp word-detail button.
    actual_width = 411 - 32 - 24 - 68 - 6
    assert len(rows) > 1
    assert all(sum(cell.style["width"] for cell in row.children) <= actual_width for row in rows)
    assert [cell.children[1].text for row in rows for cell in row.children] == list(morae(reading))
    assert all(cell.children[1].style["size"] == 18 for row in rows for cell in row.children)


def test_absent_window_has_conservative_fallback(app):
    del app.main_window
    result = build_pitch_block(app, record("ティッシュペーパー", "0"))
    assert result._pitch_has_contour


def test_reading_override_retains_requested_script_and_rejects_other_word(app):
    result = build_pitch_block(app, record("あめ", "1"), reading="アメ")
    assert "ア" in texts(result) and "メ" in texts(result)
    assert "あ" not in texts(result)
    assert build_pitch_block(app, record("あめ", "1"), reading="あま") is None


class NativeNode:
    def __init__(self, kind):
        self.kind, self.children, self.text, self.calls = kind, [], "", {}

    def addView(self, child, params=None):
        child.params = params
        self.children.append(child)

    def setText(self, text):
        self.text = text

    def __getattr__(self, name):
        if name.startswith("set"):
            return lambda *args: self.calls.__setitem__(name, args)
        raise AttributeError(name)


@pytest.fixture
def native(monkeypatch):
    from kanamemory import typography
    monkeypatch.setattr(typography, "configure_native_text",
                        lambda widget, text_role="ui", font_family=None:
                        widget.calls.update(text_role=text_role, font_family=font_family))
    metrics = SimpleNamespace(density=3, widthPixels=1080)
    config = SimpleNamespace(fontScale=2)
    resources = SimpleNamespace(getDisplayMetrics=lambda: metrics, getConfiguration=lambda: config)
    activity = SimpleNamespace(getResources=lambda: resources)
    classes = {
        "org.beeware.android.MainActivity": SimpleNamespace(singletonThis=activity),
        "android.widget.LinearLayout": lambda activity: NativeNode("linear"),
        "android.widget.ScrollView": lambda activity: NativeNode("scroll"),
        "android.widget.TextView": lambda activity: NativeNode("text"),
        "android.widget.LinearLayout$LayoutParams": lambda width, height: (width, height),
    }
    return SimpleNamespace(_jclass=classes.__getitem__)


def test_native_popup_uses_scroll_natural_heights_no_links_and_complete_long_word(native):
    reading = "ティッシュペーパーきゃきゅきょ"
    result = native_pitch_preview(native, "词条简介", record(reading, "0"), {"contour": True, "number": True})
    assert result.kind == "scroll"
    assert texts(result)[0] == "词条简介" and "调号 [0]" in texts(result)
    rows = [node for node in walk(result) if node.calls.get("setOrientation") == (0,)]
    assert len(rows) >= 3
    assert [cell.children[1].text for row in rows for cell in row.children] == list(morae(reading))
    assert all(cell.children[0].calls["font_family"] == "monospace"
               and cell.children[1].calls["text_role"] == "study"
               for row in rows for cell in row.children)
    assert next(node for node in walk(result) if node.text == "词条简介").calls["text_role"] == "ui"
    for item in walk(result):
        if item.kind == "text":
            assert item.calls["setLinksClickable"] == (False,)
            assert item.calls["setAutoLinkMask"] == (0,)
            assert item.calls["setMovementMethod"] == (None,)
            assert item.params[1] == -2
    assert all(sum(cell.params[0] for cell in row.children) <= 660 for row in rows)


@pytest.mark.parametrize("contour,number", [(True, True), (True, False), (False, True), (False, False)])
def test_native_popup_respects_independent_notation_preferences(native, contour, number):
    result = native_pitch_preview(native, "说明", record(), {"contour": contour, "number": number})
    rendered = texts(result)
    assert ("_" in rendered) is contour
    assert any("[0]" in text for text in rendered) is number
    assert ("あ" in rendered) is contour
    assert rendered[0] == "说明"


def test_native_popup_unknown_is_not_flat_accent(native):
    rendered = texts(native_pitch_preview(native, "说明", {"reading": "あめ"}, {}))
    assert rendered == ["说明", "音高资料未收录"]


def test_native_dialog_rows_remain_bounded_on_wide_landscape_screen(native):
    activity = native._jclass("org.beeware.android.MainActivity").singletonThis
    activity.getResources().getDisplayMetrics().widthPixels = 2880
    result = native_pitch_preview(native, "说明", record("きゃきゅきょ" * 10, "0"), {})
    rows = [node for node in walk(result) if node.calls.get("setOrientation") == (0,)]
    assert len(rows) > 1
    assert all(sum(cell.params[0] for cell in row.children) <= 1260 for row in rows)


def source_duplicates():
    word = record("がっこう", "0")
    word["pitch_accent"] = [
        {"reading": "がっこう", "raw": "0", "source_form": "学校", "source_ids": ["one"]},
        {"reading": "ガッコウ", "raw": "0", "source_form": "學校", "source_ids": ["two"]},
        {"reading": "がっこう", "raw": "(名)0", "source_form": "学校", "source_ids": ["three"]},
        {"reading": "がっこう", "raw": "(名)0", "source_form": "學校", "source_ids": ["four"]},
    ]
    return word


def test_visible_duplicate_sources_coalesce_without_changing_evidence_or_qualifiers(app, native):
    word = source_duplicates()
    before = deepcopy(word)
    assert len(word_pitch_variants(word)) == 4
    assert [(variant["position"], variant["qualifier"]) for variant in pitch_display_variants(word)] == [(0, ""), (0, "名")]
    assert texts(build_pitch_block(app, word)).count("调号 [0]") == 1
    assert "调号 [0]（名）" in texts(build_pitch_block(app, word))
    assert texts(native_pitch_preview(native, "说明", word, {})).count("调号 [0]") == 1
    assert texts(build_pitch_block(app, word, contour=False)) == ["调号 [0] · [0]（名）"]
    assert word == before and len(word_pitch_variants(word)) == 4


def test_different_accent_sets_keep_source_spelling_restrictions_in_every_notation(app, native):
    word = {"reading": "もっとも", "pitch_accent": [
        {"reading": "もっとも", "raw": "3", "source_form": "最も"},
        {"reading": "もっとも", "raw": "1,3", "source_form": "尤も"},
    ]}
    before = deepcopy(word)
    variants = pitch_display_variants(word)
    assert [(item["position"], item["display_source_form"]) for item in variants] == [
        (3, "最も"), (1, "尤も"), (3, "尤も")]
    assert texts(build_pitch_block(app, word, contour=False)) == [
        "调号 [3]「最も」 · [1]「尤も」 · [3]「尤も」"]
    rendered = texts(build_pitch_block(app, word, number=False))
    assert rendered.count("「最も」") == 1 and rendered.count("「尤も」") == 2
    native_text = texts(native_pitch_preview(native, "说明", word, {}))
    assert "调号 [3]「最も」" in native_text and "调号 [1]「尤も」" in native_text
    assert word == before


@pytest.mark.parametrize("number", [True, False])
def test_masked_ambiguous_kana_source_uses_exact_form_without_leaking_answer(app, number):
    word = {"form": "インチ", "reading": "インチ", "pitch_accent": [
        {"reading": "インチ", "raw": "(名)1", "source_form": "インチ"},
        {"reading": "インチ", "raw": "0,1", "source_form": "吋"},
    ]}
    before = deepcopy(word)
    rendered = texts(build_pitch_block(app, word, masked=True, number=number))
    assert [text for text in rendered if text in ("-", "_")] == ["-", "_", "_"]
    assert "□" not in rendered
    assert not any(char in text for text in rendered for char in "インチ吋")
    assert any("名" in text for text in rendered)
    if number:
        assert "调号 [1]（名）" in rendered and not any("[0]" in text for text in rendered)
    assert word == before


def test_masked_ambiguous_unmatched_word_form_has_no_pitch_hint(app):
    word = {"form": "未指定字形", "reading": "もっとも", "pitch_accent": [
        {"reading": "もっとも", "raw": "3", "source_form": "最も"},
        {"reading": "もっとも", "raw": "1,3", "source_form": "尤も"},
    ]}
    assert build_pitch_block(app, word, masked=True, show_missing=True) is None
    assert build_pitch_block(app, word, masked=True, contour=False) is None
    assert build_pitch_block(app, word)._pitch_has_contour
