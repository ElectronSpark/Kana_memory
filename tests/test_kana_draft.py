"""The typed draft of the kana practice (design 13): the state object, what a kept page holds of it, and the
app-side module on a bare object.

No store, no control and no app is built here.
"""
from __future__ import annotations

import ast
import dataclasses
from pathlib import Path
from types import SimpleNamespace

import pytest

from kanamemory import kana_draft_runtime as line
from kanamemory import page_restore
from kanamemory.kana_line.draft import CONVERTER_FIELDS, PAGE_FIELDS, KanaDraft, kept_fields
from kanamemory.kana_line.fields import PartlySet

SOURCE = Path(line.__file__).parent
NAMES = ["romaji_answer", "word_answer", "word_romaji_parts", "word_romaji_plan", "word_romaji_unit_mode",
         "word_conversion_source", "word_conversion_convention", "word_used_conversion"]
CONVERTER = {"word_conversion_source": "gakk", "word_conversion_convention": "keyboard", "word_used_conversion": True}


def full():
    return KanaDraft(romaji_answer="k", word_answer="か", word_romaji_parts=["ka"], word_romaji_plan={"units": []},
                     word_romaji_unit_mode=True, **CONVERTER)


# --- the state object -------------------------------------------------------------------------------------------------

def test_a_new_draft_has_its_eight_fields_and_nothing_set():
    assert [field.name for field in dataclasses.fields(KanaDraft)] == NAMES
    assert isinstance(KanaDraft(), PartlySet) and KanaDraft().set_fields() == {}
    assert KanaDraft(word_answer="") != KanaDraft(word_answer="")


def test_only_keeps_the_named_fields_that_were_set_in_a_new_object():
    draft = KanaDraft(romaji_answer="k", word_answer=None)
    kept = draft.only(("romaji_answer", "word_answer", "word_romaji_parts"))
    assert kept is not draft and isinstance(kept, KanaDraft)
    assert kept.set_fields() == {"romaji_answer": "k", "word_answer": None}
    assert draft.only(()).set_fields() == {} and draft.only(("word_romaji_plan",)).set_fields() == {}
    parts = ["ka"]
    assert KanaDraft(word_romaji_parts=parts).only(("word_romaji_parts",)).word_romaji_parts is parts


@pytest.mark.parametrize("page,practice,fields", [
    ("question", True, ("romaji_answer", "word_romaji_parts", "word_romaji_plan", "word_romaji_unit_mode",
                        *CONVERTER_FIELDS)),
    ("word_question", True, ("word_answer", *CONVERTER_FIELDS)),
    ("ink_question", True, CONVERTER_FIELDS), ("feedback", True, CONVERTER_FIELDS),
    ("session_wait", True, CONVERTER_FIELDS), ("kana_detail", False, ()), ("home", False, ()),
])
def test_a_kept_page_holds_what_it_shows_and_a_practice_page_the_converter_values(page, practice, fields):
    assert kept_fields(page, practice_page=practice) == fields
    assert set(PAGE_FIELDS) == {"question", "word_question"}
    assert CONVERTER_FIELDS == ("word_conversion_source", "word_conversion_convention", "word_used_conversion")


# --- the app side -----------------------------------------------------------------------------------------------------

def test_an_app_object_without_a_draft_reads_as_nothing_set():
    app = SimpleNamespace()
    assert line.draft(app).set_fields() == {} and not hasattr(app, "_kana_typed")
    for name in NAMES:
        with pytest.raises(AttributeError, match=name):
            getattr(line, name)(app)
    assert line.draft(app).get("word_answer", "") == "" and line.draft(app).get("word_used_conversion", False) is False


def test_assigning_a_field_gives_the_app_a_new_draft_and_leaves_the_old_one():
    app = SimpleNamespace()
    line.set_romaji_answer(app, "k")
    first = app._kana_typed
    line.set_word_answer(app, "か")
    assert app._kana_typed is not first and first.set_fields() == {"romaji_answer": "k"}
    assert app._kana_typed.set_fields() == {"romaji_answer": "k", "word_answer": "か"}
    line.assign(app, word_romaji_parts=[], word_romaji_unit_mode=True)
    assert (line.word_romaji_parts(app), line.word_romaji_unit_mode(app), line.romaji_answer(app)) == ([], True, "k")


def test_every_setter_sets_its_own_field_and_every_reader_reads_it():
    app = SimpleNamespace()
    for index, name in enumerate(NAMES):
        getattr(line, "set_" + name)(app, index)
        assert getattr(line, name)(app) == index
    assert app._kana_typed.set_fields() == {name: index for index, name in enumerate(NAMES)}


def test_the_list_of_parts_is_changed_in_place_through_its_reader():
    app = SimpleNamespace()
    parts = []
    line.set_word_romaji_parts(app, parts)
    line.word_romaji_parts(app).append("ka")
    assert parts == ["ka"] and line.word_romaji_parts(app) is parts


def test_something_that_is_no_draft_on_the_app_reads_as_nothing_set():
    app = SimpleNamespace(_kana_typed="not a draft")
    assert line.draft(app).set_fields() == {}
    line.set_word_answer(app, "か")
    assert app._kana_typed.set_fields() == {"word_answer": "か"}


# --- a page kept for Back -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("page,practice,held", [
    ("question", True, {"romaji_answer", "word_romaji_parts", "word_romaji_plan", "word_romaji_unit_mode",
                        *CONVERTER}),
    ("word_question", True, {"word_answer", *CONVERTER}),
    ("feedback", True, set(CONVERTER)),
])
def test_a_page_keeps_the_fields_it_shows_in_an_object_of_its_own(page, practice, held):
    app = SimpleNamespace(_kana_typed=full())
    bindings = {"other": 1}
    line.keep(app, bindings, page, practice_page=practice)
    kept = bindings["_kana_typed"]
    assert kept is not app._kana_typed and set(kept.set_fields()) == held and bindings["other"] == 1
    assert all(kept.set_fields()[name] is app._kana_typed.set_fields()[name] for name in held)


@pytest.mark.parametrize("app", [SimpleNamespace(), SimpleNamespace(_kana_typed=KanaDraft(word_answer="か"))])
def test_a_page_that_holds_nothing_of_the_draft_keeps_no_draft(app):
    bindings = {}
    line.keep(app, bindings, "question", practice_page=True)
    line.keep(app, bindings, "kana_detail", practice_page=False)
    assert bindings == {}


def test_back_gives_the_kept_fields_back_and_leaves_the_others():
    app = SimpleNamespace(_kana_typed=full())
    kept_parts = ["then"]
    page_restore.restore_bindings(app, {"_kana_typed": KanaDraft(romaji_answer="then", word_romaji_parts=kept_parts)})
    assert app._kana_typed.romaji_answer == "then" and app._kana_typed.word_romaji_parts is kept_parts
    assert app._kana_typed.word_answer == "か" and app._kana_typed.word_conversion_source == "gakk"
    bare = SimpleNamespace()
    page_restore.restore_bindings(bare, {"_kana_typed": KanaDraft(word_answer="then")})
    assert bare._kana_typed.set_fields() == {"word_answer": "then"}


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
    assert imported(SOURCE / "kana_line" / "draft.py") == {"__future__", "dataclasses", ".fields"}


def test_the_app_side_module_imports_no_controls_and_not_the_app():
    assert imported(SOURCE / "kana_draft_runtime.py") == {"__future__", "dataclasses", ".kana_line.draft"}
