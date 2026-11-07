"""Desktop modal fallback retains optional pitch without altering navigation."""
import asyncio
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
from kanamemory.platform_desktop import DesktopPlatform, _pitch_preview_text
from kanamemory.pitch_accent import morae


def pitch(contour=True, number=True, reading="ティッシュ", raw="1"):
    return {"record": {"reading": reading, "pitch_accent": [{"reading": reading, "raw": raw}]},
            "preferences": {"contour": contour, "number": number}}


@pytest.mark.parametrize("contour,number", [(True, True), (True, False), (False, True), (False, False)])
def test_desktop_notations_are_independent_and_contour_is_above_kana(contour, number):
    rendered = _pitch_preview_text(pitch(contour, number))
    assert ("[1]" in rendered) is number
    assert ("ティ" in rendered) is contour
    if contour:
        lines = rendered.splitlines()
        kana_line = next(index for index, line in enumerate(lines) if "ティ" in line)
        assert lines[kana_line - 1].split() == ["-", "_", "_"]
        assert lines[kana_line].split() == ["ティ", "ッ", "シュ"]
    elif number:
        assert rendered == "调号 [1]"
    else:
        assert rendered == ""


def test_desktop_preserves_alternatives_qualifiers_and_source_data():
    data = pitch(reading="あいにく", raw="(名)4,0,(副)0")
    original = deepcopy(data)
    rendered = _pitch_preview_text(data)
    assert all(text in rendered for text in ("[4]（名）", "[0]（名）", "[0]（副）"))
    assert rendered.count("あ  い  に  く") == 3
    assert data == original


def test_desktop_long_words_wrap_complete_mora_pairs():
    reading = "ティッシュペーパーきゃきゅきょ" * 3
    rendered = _pitch_preview_text(pitch(number=False, reading=reading, raw="0"))
    lines = rendered.splitlines()
    assert len(lines) > 2 and len(lines) % 2 == 0
    assert [unit for line in lines[1::2] for unit in line.split()] == list(morae(reading))
    for marks, kana in zip(lines[::2], lines[1::2]):
        assert len(marks.split()) == len(kana.split())
        assert len(marks) <= 32


def test_desktop_missing_data_never_invents_flat_accent():
    assert _pitch_preview_text(None) == ""
    assert _pitch_preview_text({}) == "音高资料未收录"
    assert _pitch_preview_text({"preferences": {"contour": False, "number": False}}) == ""


def test_desktop_duplicate_dictionary_spellings_render_once():
    data = pitch(reading="がっこう", raw="0")
    source = data["record"]["pitch_accent"][0]
    source["source_form"] = "学校"
    data["record"]["pitch_accent"].append({**source, "source_form": "學校"})
    assert _pitch_preview_text(data).count("调号 [0]") == 1
    assert len(data["record"]["pitch_accent"]) == 2


@pytest.mark.parametrize("contour,number", [(True, True), (True, False), (False, True)])
def test_desktop_keeps_distinct_spelling_accent_restrictions(contour, number):
    data = pitch(contour=contour, number=number, reading="もっとも", raw="3")
    data["record"]["pitch_accent"][0]["source_form"] = "最も"
    data["record"]["pitch_accent"].append({"reading": "もっとも", "raw": "1,3", "source_form": "尤も"})
    rendered = _pitch_preview_text(data)
    assert "「最も」" in rendered and rendered.count("「尤も」") == 2
    if number:
        assert "[3]「最も」" in rendered and "[1]「尤も」" in rendered


@pytest.mark.parametrize("accepted", [True, False])
def test_desktop_dialog_includes_annotation_and_keeps_explicit_navigation(monkeypatch, accepted):
    shown = []

    async def show_dialog(dialog):
        shown.append(dialog)
        return accepted

    fake_toga = SimpleNamespace(
        App=SimpleNamespace(app=SimpleNamespace(current_window=SimpleNamespace(dialog=show_dialog))),
        ConfirmDialog=lambda title, message: SimpleNamespace(title=title, message=message),
    )
    monkeypatch.setitem(sys.modules, "toga", fake_toga)
    platform = DesktopPlatform()
    assert asyncio.run(platform.preview_index("词条", "ティッシュ / tissue", pitch=pitch())) is accepted
    assert len(shown) == 1
    assert shown[0].message.startswith("ティッシュ / tissue\n\n调号 [1]")
    assert "OK" in shown[0].message and "Cancel" in shown[0].message
    assert platform._preview_task is None
