"""The ink pad (design 13): the state object, what a kept ink page holds of it, and the app-side module on a bare
object.

No store, no control and no app is built here.
"""
from __future__ import annotations

import ast
import dataclasses
from pathlib import Path
from types import SimpleNamespace

import pytest

from kanamemory import kana_ink_runtime as line
from kanamemory import page_restore
from kanamemory.kana_line.fields import PartlySet
from kanamemory.kana_line.ink import FIELDS, InkPad

SOURCE = Path(line.__file__).parent
NAMES = ["strokes", "ink_width", "ink_height"]


# --- the state object -------------------------------------------------------------------------------------------------

def test_a_new_pad_has_its_three_fields_and_nothing_set():
    assert [field.name for field in dataclasses.fields(InkPad)] == NAMES and FIELDS == tuple(NAMES)
    assert isinstance(InkPad(), PartlySet) and InkPad().set_fields() == {}
    assert InkPad(strokes=[]) != InkPad(strokes=[])


def test_a_field_was_set_even_when_it_was_set_to_something_empty():
    pad = InkPad(strokes=[], ink_width=None)
    assert pad.has("strokes") and pad.has("ink_width") and not pad.has("ink_height")
    with pytest.raises(AttributeError):
        pad.has("no_such_field")


# --- the app side -----------------------------------------------------------------------------------------------------

def test_an_app_object_without_a_pad_reads_as_nothing_set():
    app = SimpleNamespace()
    assert line.pad(app).set_fields() == {} and not hasattr(app, "_ink_pad")
    for name in NAMES:
        with pytest.raises(AttributeError, match=name):
            getattr(line, name)(app)
    assert line.pad(app).get("strokes", []) == [] and line.pad(app).get("ink_width", 360) == 360
    assert not line.pad(app).has("strokes")


def test_assigning_a_field_gives_the_app_a_new_pad_and_leaves_the_old_one():
    app = SimpleNamespace()
    strokes = []
    line.set_strokes(app, strokes)
    first = app._ink_pad
    line.set_ink_width(app, 360)
    line.set_ink_height(app, 210)
    assert app._ink_pad is not first and first.set_fields() == {"strokes": strokes}
    assert app._ink_pad.set_fields() == {"strokes": strokes, "ink_width": 360, "ink_height": 210}
    assert (line.strokes(app), line.ink_width(app), line.ink_height(app)) == (strokes, 360, 210)
    assert line.strokes(app) is strokes


def test_the_list_of_strokes_is_changed_in_place_through_its_reader():
    app = SimpleNamespace()
    strokes = []
    line.set_strokes(app, strokes)
    line.strokes(app).append([(1.0, 2.0, 3)])
    assert strokes == [[(1.0, 2.0, 3)]] and line.strokes(app) is strokes
    line.strokes(app).pop()
    assert strokes == []


def test_something_that_is_no_pad_on_the_app_reads_as_nothing_set():
    app = SimpleNamespace(_ink_pad="not a pad")
    assert line.pad(app).set_fields() == {}
    line.set_strokes(app, [])
    assert app._ink_pad.set_fields() == {"strokes": []}


# --- a page kept for Back -------------------------------------------------------------------------------------------

def test_an_ink_page_keeps_the_pad_in_an_object_of_its_own_with_the_same_list():
    strokes = [[(1.0, 2.0, 3)]]
    app = SimpleNamespace(_ink_pad=InkPad(strokes=strokes, ink_width=360, ink_height=210))
    bindings = {"other": 1}
    line.keep(app, bindings, ink_page=True)
    kept = bindings["_ink_pad"]
    assert kept is not app._ink_pad and kept.set_fields() == {"strokes": strokes, "ink_width": 360, "ink_height": 210}
    assert kept.strokes is strokes and bindings["other"] == 1


@pytest.mark.parametrize("app,ink_page", [
    (SimpleNamespace(_ink_pad=InkPad(strokes=[], ink_width=360, ink_height=210)), False),
    (SimpleNamespace(), True), (SimpleNamespace(_ink_pad=InkPad()), True)])
def test_another_page_and_an_app_without_a_pad_keep_no_pad(app, ink_page):
    bindings = {}
    line.keep(app, bindings, ink_page=ink_page)
    assert bindings == {}


def test_an_ink_page_keeps_only_the_fields_that_were_set():
    app = SimpleNamespace(_ink_pad=InkPad(strokes=[]))
    bindings = {}
    line.keep(app, bindings, ink_page=True)
    assert bindings["_ink_pad"].set_fields() == {"strokes": []}


def test_back_gives_the_kept_fields_back_and_leaves_the_others():
    app = SimpleNamespace(_ink_pad=InkPad(strokes=[["now"]], ink_width=1, ink_height=2))
    kept_strokes = [["then"]]
    page_restore.restore_bindings(app, {"_ink_pad": InkPad(strokes=kept_strokes, ink_width=360)})
    assert app._ink_pad.strokes is kept_strokes
    assert (app._ink_pad.ink_width, app._ink_pad.ink_height) == (360, 2)
    bare = SimpleNamespace()
    page_restore.restore_bindings(bare, {"_ink_pad": InkPad(strokes=[])})
    assert bare._ink_pad.set_fields() == {"strokes": []}


# --- what the modules may import --------------------------------------------------------------------------------------

def imported(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add("." * node.level + (node.module or ""))
    return names


def test_the_state_module_imports_nothing_of_the_app():
    assert imported(SOURCE / "kana_line" / "ink.py") == {"__future__", "dataclasses", ".fields"}


def test_the_app_side_module_imports_no_controls_and_not_the_app():
    assert imported(SOURCE / "kana_ink_runtime.py") == {"__future__", "dataclasses", ".kana_line.ink"}
