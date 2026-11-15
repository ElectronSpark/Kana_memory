"""The position of the kana practice session, and state whose fields may never have been set (design 13).

The state objects are used directly, the app-side module on a bare object, and the restore of a kept page's
bindings on a bare object too. No store, no control and no app is built here.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from kanamemory import kana_session_runtime as line
from kanamemory import page_restore
from kanamemory.kana_line.attempt import KanaAttempt
from kanamemory.kana_line.fields import UNSET, PartlySet
from kanamemory.kana_line.session import KanaSession

SOURCE = Path(line.__file__).parent
QUEUE = [object(), object(), object()]


def position(**fields):
    return KanaSession(**fields)


# --- fields that may never have been set ----------------------------------------------------------------------------

def test_a_new_position_has_nothing_set():
    session = KanaSession()
    assert session.set_fields() == {}
    assert [session.get(name, "default") for name in ("session_id", "questions", "question_index", "session_right",
                                                      "question", "script")] == ["default"] * 6
    assert isinstance(session, PartlySet) and isinstance(KanaAttempt(), PartlySet)


def test_a_field_set_to_none_is_set():
    assert position(session_id=None, question=None).set_fields() == {"session_id": None, "question": None}


def test_a_kept_object_gives_back_the_fields_it_held_and_leaves_the_others():
    kept = position(session_id="old", question_index=2)
    now = position(session_id="new", question_index=5, session_right=4, script="katakana")
    restored = kept.restored_over(now)
    assert restored is not kept and restored is not now
    assert restored.set_fields() == {"session_id": "old", "question_index": 2, "session_right": 4, "script": "katakana"}
    assert now.session_id == "new" and kept.get("session_right", "never") == "never"


def test_a_kept_object_over_nothing_or_over_something_else_is_itself_in_a_new_object():
    kept = position(session_id="old", questions=QUEUE)
    for current in (None, object(), KanaAttempt(revealed=True)):
        restored = kept.restored_over(current)
        assert isinstance(restored, KanaSession) and restored is not kept
        assert restored.set_fields() == {"session_id": "old", "questions": QUEUE}


def test_a_field_kept_as_none_is_given_back_as_none():
    assert position(question=None).restored_over(position(question=QUEUE[0])).question is None


def test_two_positions_with_the_same_fields_are_still_two():
    assert position(session_id="s") != position(session_id="s")


# --- the app side -----------------------------------------------------------------------------------------------------

def test_an_app_object_without_a_position_reads_as_nothing_set():
    app = SimpleNamespace()
    assert line.session(app).set_fields() == {} and not hasattr(app, "_kana_session")
    for reader in (line.session_id, line.questions, line.question_index, line.session_right, line.question,
                   line.script):
        with pytest.raises(AttributeError):
            reader(app)
    assert line.session(app).get("session_id") is None and line.session(app).get("questions", []) == []


def test_assigning_a_field_gives_the_app_a_new_position_and_leaves_the_old_one():
    app = SimpleNamespace()
    line.assign(app, session_id="s", questions=QUEUE, question_index=0, session_right=0)
    first = app._kana_session
    line.set_question_index(app, 1)
    assert app._kana_session is not first and first.question_index == 0
    assert (line.session_id(app), line.questions(app), line.question_index(app), line.session_right(app)) == (
        "s", QUEUE, 1, 0)
    for setter, reader, value in ((line.set_session_id, line.session_id, "t"), (line.set_questions, line.questions, []),
                                  (line.set_session_right, line.session_right, 3),
                                  (line.set_question, line.question, QUEUE[1]), (line.set_script, line.script, "katakana")):
        kept = app._kana_session
        setter(app, value)
        assert reader(app) is value and app._kana_session is not kept


def test_the_queue_is_the_same_list_in_every_position_that_holds_it():
    app = SimpleNamespace()
    queue = list(QUEUE)
    line.assign(app, questions=queue)
    first = app._kana_session
    line.set_question_index(app, 1)
    line.questions(app).append("added")
    assert first.questions is queue and queue[-1] == "added"


def test_something_else_in_the_positions_place_reads_as_nothing_set():
    app = SimpleNamespace(_kana_session=object())
    assert line.session(app).set_fields() == {}
    line.set_session_id(app, "s")
    assert isinstance(app._kana_session, KanaSession) and line.session_id(app) == "s"


@pytest.mark.parametrize("bindings, key", [
    ({}, (None, None)),
    ({"_kana_session": object()}, (None, None)),
    ({"_kana_session": KanaSession()}, (None, None)),
    ({"_kana_session": KanaSession(session_id="s")}, ("s", None)),
    ({"_kana_session": KanaSession(session_id="s", question_index=4)}, ("s", 4)),
])
def test_a_kept_pages_attempt_is_named_by_the_position_it_was_kept_with(bindings, key):
    assert line.kept_attempt_key(bindings) == key


# --- Back -----------------------------------------------------------------------------------------------------------

def test_back_gives_a_kept_state_object_field_by_field_and_everything_else_as_it_is():
    widget = object()
    app = SimpleNamespace(question_notice="now")
    line.assign(app, session_id="new", question_index=7, session_right=2)
    kept = KanaSession(session_id="old", question_index=3)
    page_restore.restore_bindings(app, {"_kana_session": kept, "question_notice": widget,
                                        "_kana_attempt": KanaAttempt(revealed=False)})
    assert app.question_notice is widget
    assert app._kana_session is not kept
    assert app._kana_session.set_fields() == {"session_id": "old", "question_index": 3, "session_right": 2}
    assert app._kana_attempt.set_fields() == {"revealed": False}


def test_back_keeps_what_the_kept_page_never_held_of_the_attempt():
    app = SimpleNamespace(_kana_attempt=KanaAttempt(revealed=True, answered_text="ka", audio_ok=True, started=4.0))
    page_restore.restore_bindings(app, {"_kana_attempt": KanaAttempt(revealed=False, answered_text=None, correct=False)})
    assert app._kana_attempt.set_fields() == {"revealed": False, "answered_text": None, "correct": False,
                                              "audio_ok": True, "started": 4.0}


def test_unset_is_one_falsy_value():
    assert not UNSET and repr(UNSET) == "UNSET"


# --- what the modules may import ------------------------------------------------------------------------------------

def imported(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add("." * node.level + (node.module or ""))
    return names


def test_the_state_modules_import_nothing_of_the_app():
    assert imported(SOURCE / "kana_line" / "fields.py") == {"__future__", "dataclasses"}
    assert imported(SOURCE / "kana_line" / "session.py") == {"__future__", "dataclasses", ".fields"}


def test_the_app_side_module_imports_no_controls_and_not_the_app():
    assert imported(SOURCE / "kana_session_runtime.py") == {"__future__", "dataclasses", ".kana_line.session"}
