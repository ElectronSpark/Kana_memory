"""Listening handwriting is opt-in without losing selectable forms or queues."""
from copy import deepcopy

import pytest

from support.app_fixtures import app_module, setup_app
from kanamemory.app import KanaMemory
from kanamemory.domain import Question


LISTENING = "kana_handwriting"
VISUAL = "romaji_handwriting"
ALL_KINDS = app_module.MIXED_PRACTICE_KINDS["adaptive_mixed"]
OPT_IN_VERSION = "listening_handwriting_opt_in_version"


def open_form(app, preferences):
    app._clean_preferences = KanaMemory._clean_preferences
    app._practice_preferences = KanaMemory._clean_preferences(preferences)
    app.show_practice_setup = lambda **kwargs: KanaMemory.show_practice_setup.__wrapped__(app, **kwargs)
    app.show_practice_setup()


def test_new_default_keeps_visual_handwriting_and_listening_remains_selectable():
    preferences = KanaMemory._clean_preferences({})
    assert LISTENING not in preferences["mixed_kinds"]
    assert VISUAL in preferences["mixed_kinds"]
    assert "word_kana" in preferences["mixed_kinds"]
    assert LISTENING in ALL_KINDS
    assert app_module.PRACTICE_KINDS["听音手写假名"] == LISTENING
    assert preferences[OPT_IN_VERSION] == 1


@pytest.mark.parametrize("provided_kinds", [None, list(ALL_KINDS)])
def test_legacy_default_migrates_only_listening_handwriting_and_preserves_scope(provided_kinds):
    old = {"question_plan_version": 2, "kind": "综合自适应练习", "script": "片假名",
           "scopes": ["拗音"], "count": "自定义", "custom_count": "37",
           "word_collection": "英语借词", "research": False, "rounds": "3"}
    if provided_kinds is not None:
        old["mixed_kinds"] = provided_kinds
    before = deepcopy(old)
    changed = KanaMemory._clean_preferences(old)
    assert old == before
    assert changed["mixed_kinds"] == [kind for kind in ALL_KINDS if kind != LISTENING]
    assert changed["script"] == "片假名" and changed["scopes"] == ["拗音"]
    assert changed["custom_count"] == "37" and changed["word_collection"] == "英语借词"
    assert changed["rounds"] == "3" and not changed["research"]
    assert changed[OPT_IN_VERSION] == 1
    assert KanaMemory._clean_preferences(changed) == changed


def test_legacy_custom_subset_preserves_explicit_listening_choice():
    old = {"question_plan_version": 2, "kind": "假名综合练习",
           "mixed_kinds": ["kana_choice", LISTENING]}
    cleaned = KanaMemory._clean_preferences(old)
    assert cleaned["mixed_kinds"] == ["kana_choice", LISTENING]
    assert cleaned[OPT_IN_VERSION] == 1


def test_legacy_explicit_single_listening_mode_is_preserved():
    cleaned = KanaMemory._clean_preferences({"question_plan_version": 2, "kind": "听音手写假名"})
    assert cleaned["kind"] == "听音手写假名"


def test_explicit_opt_in_survives_form_save_cleanup_and_reopen(setup_app):
    app = setup_app
    open_form(app, {})
    assert not app.mixed_kind_switches[LISTENING].value
    assert app.mixed_kind_switches[VISUAL].value
    app.mixed_kind_switches[LISTENING].value = True
    app.refresh_practice_setup(app.mixed_kind_switches[LISTENING])
    saved = deepcopy(app._practice_preferences)
    assert set(saved["mixed_kinds"]) == set(ALL_KINDS)
    assert saved[OPT_IN_VERSION] == 1
    app.store.set_setting.assert_called_with("practice_preferences", saved)
    open_form(app, saved)
    assert app.mixed_kind_switches[LISTENING].value
    assert LISTENING in app._practice_kinds()


def test_explicit_single_listening_mode_can_be_selected_and_reopened(setup_app):
    app = setup_app
    open_form(app, {})
    app.kind_picker.value = "听音手写假名"
    app.refresh_practice_setup(app.kind_picker)
    saved = deepcopy(app._practice_preferences)
    open_form(app, saved)
    assert app._practice_kinds() == (LISTENING,)


def test_reset_to_mixed_uses_new_default_without_removing_listening_control(setup_app):
    app = setup_app
    open_form(app, {})
    app.mixed_kind_switches[LISTENING].value = True
    app.refresh_practice_setup(app.mixed_kind_switches[LISTENING])
    KanaMemory.enable_mixed_practice(app)
    assert not app.mixed_kind_switches[LISTENING].value
    assert app.mixed_kind_switches[VISUAL].value
    assert app._practice_kinds() == app_module.DEFAULT_MIXED_KINDS


def test_due_shortcut_obeys_default_and_does_not_overwrite_ordinary_preferences(setup_app):
    app = setup_app
    open_form(app, {"question_plan_version": 2, "kind": "综合自适应练习",
                    "mixed_kinds": list(ALL_KINDS), "count": "20 项"})
    saved = deepcopy(app._practice_preferences)
    KanaMemory.show_due_practice(app, {"research": False, "due": 2})
    assert LISTENING not in app._practice_kinds()
    assert VISUAL in app._practice_kinds()
    assert app._practice_preferences == saved
    assert app._practice_setup_temporary


@pytest.mark.parametrize("opted_in", [False, True])
def test_completed_legacy_batch_cannot_reenable_listening_against_current_choice(setup_app, opted_in):
    app = setup_app
    preferences = KanaMemory._clean_preferences({})
    if opted_in:
        preferences["mixed_kinds"] = list(ALL_KINDS)
    open_form(app, preferences)
    questions = [Question("ka", "kana_choice", "hiragana", "か", "ka", metadata={
        "practice_config": {"kinds": list(ALL_KINDS), "requested_script": "both",
                            "requested_count": 5, "kana_categories": ["basic"],
                            "schedule_mode": "spaced"}})]
    app.questions, app.page = questions, "session_done"
    before = deepcopy(questions)
    KanaMemory.show_new_batch(app)
    assert (LISTENING in app._practice_kinds()) is opted_in
    assert VISUAL in app._practice_kinds()
    assert app.questions == before
    assert app._practice_new_only()


def test_current_opt_in_is_not_rejected_by_preference_validation():
    preferences = KanaMemory._clean_preferences({})
    preferences["mixed_kinds"] = [LISTENING, "unknown", LISTENING, VISUAL, None]
    cleaned = KanaMemory._clean_preferences(preferences)
    assert cleaned["mixed_kinds"] == [LISTENING, VISUAL]


@pytest.mark.parametrize("current_kind,current_kinds,expected", [
    ("综合自适应练习", ["kana_choice", "word_kana"], ("kana_choice", "word_kana")),
    ("词汇综合练习", ["word_kana"], ("word_kana",)),
    ("看假名拼罗马音", [], ("kana_romaji",)),
])
def test_new_batch_does_not_reenable_any_old_kind_or_inherit_old_family(
        setup_app, current_kind, current_kinds, expected):
    app = setup_app
    open_form(app, {"question_plan_version": 2, "kind": current_kind,
                    "mixed_kinds": current_kinds})
    app.questions = [Question("ka", LISTENING, "hiragana", "", "か", metadata={
        "practice_config": {"kinds": [LISTENING, "word_meaning_kana"],
                            "requested_script": "hiragana", "requested_count": 5,
                            "schedule_mode": "spaced"}})]
    app.page = "session_done"
    KanaMemory.show_new_batch(app)
    assert app.kind_picker.value == current_kind
    assert app._practice_kinds() == expected
    assert app._practice_preferences["mixed_kinds"] == current_kinds
    assert app.count_picker.value == "5 项"
    assert app._practice_new_only()


@pytest.mark.parametrize("current_kind", ["综合自适应练习", "看假名拼罗马音"])
def test_completed_old_single_listening_batch_does_not_undo_explicit_opt_out(setup_app, current_kind):
    app = setup_app
    open_form(app, {})
    app.mixed_kind_switches[LISTENING].value = True
    app.refresh_practice_setup(app.mixed_kind_switches[LISTENING])
    app.mixed_kind_switches[LISTENING].value = False
    app.kind_picker.value = current_kind
    app.refresh_practice_setup(app.mixed_kind_switches[LISTENING])
    app.questions = [Question("ka", LISTENING, "hiragana", "", "か", metadata={
        "practice_config": {"kinds": [LISTENING], "kana_categories": ["basic"],
                            "requested_script": "hiragana", "requested_count": 5,
                            "schedule_mode": "spaced"}})]
    original = deepcopy(app.questions)
    app.page = "session_done"
    KanaMemory.show_new_batch(app)
    assert app.kind_picker.value != "听音手写假名"
    assert LISTENING not in app._practice_kinds()
    assert not app.mixed_kind_switches[LISTENING].value
    assert app.questions == original
    assert app._practice_new_only()

