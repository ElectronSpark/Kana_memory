"""New-batch entry points retain the requested scope without daily quotas."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from support.app_fixtures import CHOICE, WORD, FULL, app_module, set_mode, setup_app, question_app
from support.fake_ui import View, descendants, text_of
from kanamemory.app import KanaMemory
from kanamemory.domain import Question


DUE_FIRST = "到期优先，补充新内容"
NEW_ONLY = "只学新内容"


@pytest.mark.parametrize("batch,new_only", [(DUE_FIRST, False), (NEW_ONLY, True)])
@pytest.mark.parametrize("kind", list(app_module.PRACTICE_KINDS))
def test_each_mode_saves_batch_and_passes_it_to_generation(setup_app, monkeypatch, batch, new_only, kind):
    app = setup_app
    app.platform.tts_ready, app.model_state = True, "ready"
    app.repo.approved_word_count.return_value = 121
    set_mode(app, kind, batch=batch)
    assert app._practice_new_only() is new_only
    assert app._practice_preferences["batch"] == batch
    KanaMemory.show_practice_setup.__wrapped__(app)
    assert app.batch_picker.value == batch
    # The action and any required preparation must be reachable without
    # scrolling past the retained configuration controls.
    assert app.body.children[:4] == [app.practice_start_button,
        app.practice_replace_slot, app.practice_count_summary, app.practice_services]
    assert sum(item is app.practice_start_button for item in descendants(app.body)) == 1
    build = Mock(return_value=[object()])
    monkeypatch.setattr(app_module, "build_questions", build)
    monkeypatch.setattr(app_module, "build_practice_batch", build)
    asyncio.run(app.start_practice())
    assert build.call_args.kwargs["new_only"] is new_only
    app.store.set_daily_new_limit.assert_not_called()


def test_new_batch_switch_is_preserved_by_back_navigation_bindings(setup_app):
    app = setup_app
    set_mode(app, CHOICE, batch=NEW_ONLY)
    assert "batch_picker" in app_module.PAGE_BINDINGS["practice_setup"]
    assert "daily_new_limit_input" not in app_module.PAGE_BINDINGS["practice_setup"]
    assert "每日新记忆上限" not in text_of(app.body)
    assert "1–200" not in text_of(app.body)


def test_empty_new_batch_explains_scope_without_starting_a_review_session(setup_app, monkeypatch):
    app = setup_app
    set_mode(app, CHOICE, batch=NEW_ONLY)
    build = Mock(return_value=[])
    monkeypatch.setattr(app_module, "build_questions", build)
    monkeypatch.setattr(app_module, "build_practice_batch", build)
    asyncio.run(app.start_practice())
    assert build.call_args.kwargs["new_only"] is True
    app.store.start_session.assert_not_called()
    app.store.record_review.assert_not_called()
    assert "没有剩余新内容" in app.alert.call_args.args[0]
    assert "每日" not in app.alert.call_args.args[0]
    assert not app._starting_practice


def test_home_new_batch_uses_saved_preferences_even_with_retained_old_questions(setup_app):
    app = setup_app
    set_mode(app, "看假名拼罗马音", script="片假名", scopes=("拗音",), count=20)
    before = deepcopy(app._practice_preferences)
    app.page = "home"
    app.questions = [Question("unused", "word_kana", "hiragana", "", "あ",
        metadata={"practice_config": {"requested_count": 999, "include_research": True}})]
    app._clean_preferences = KanaMemory._clean_preferences
    app.show_practice_setup = Mock()
    KanaMemory.show_new_batch(app)
    assert app._practice_preferences == {**before, "scopes": list(before["scopes"]), "batch": NEW_ONLY}
    app.show_practice_setup.assert_called_once()
    app.store.start_session.assert_not_called()


def test_completed_appended_queue_continues_latest_batch_scope_not_old_timed_repeat(setup_app):
    app = setup_app
    set_mode(app, "看假名拼罗马音", script="平假名", scopes=("清音与拨音",))
    old = Question("old", "kana_romaji", "hiragana", "", "a", metadata={"practice_config": {
        "kana_categories": ["basic"], "requested_count": 5, "word_table": "partial"}})
    latest = Question("new", "kana_romaji", "katakana", "", "kya", metadata={"practice_config": {
        "kana_categories": ["yoon"], "requested_count": 15, "word_table": "partial"}})
    retry = deepcopy(old)
    retry.metadata["not_before_utc"] = "2026-10-15T12:00:00+00:00"
    app.questions, app.page = [old, latest, retry], "session_done"
    app._clean_preferences = KanaMemory._clean_preferences
    app.show_practice_setup = Mock()
    KanaMemory.show_new_batch(app)
    assert app._practice_preferences["script"] == "片假名"
    assert app._practice_preferences["scopes"] == ["拗音"]
    assert app._practice_preferences["count"] == "15 项"


@pytest.mark.parametrize("kind", ["kana_choice", "kana_romaji", "romaji_kana", "kana_handwriting"])
def test_completed_kana_batch_inherits_scope_count_but_keeps_current_kind(setup_app, kind):
    app = setup_app
    set_mode(app, WORD, script="平假名", research=True, table=FULL, count=5)
    before = deepcopy(app._practice_preferences)
    app.page = "session_done"
    app.questions = [Question("kana-test", kind, "katakana", "", "kya",
        metadata={"practice_config": {
            "kana_categories": ["yoon", "foreign_combination", "foreign_voiced"],
            "requested_count": 201, "word_collection": None, "include_research": False,
            "word_table": "partial",
        }})]
    app._clean_preferences = KanaMemory._clean_preferences
    app.show_practice_setup = Mock()
    KanaMemory.show_new_batch(app)
    prefs = app._practice_preferences
    assert prefs["kind"] == WORD
    assert prefs["mixed_kinds"] == before["mixed_kinds"]
    assert prefs["script"] == "片假名"
    assert prefs["scopes"] == ["拗音", "外来语组合（含ヴ）"]
    assert prefs["count"] == "自定义" and prefs["custom_count"] == "201"
    assert prefs["research"] is False
    assert prefs["batch"] == NEW_ONLY
    app.show_practice_setup.assert_called_once()


@pytest.mark.parametrize("kind", ["word_kana", "word_meaning_kana"])
def test_completed_word_batch_keeps_collection_research_choice_and_keyboard(setup_app, kind):
    app = setup_app
    before = deepcopy(app._practice_preferences)
    app.page = "session_done"
    app.questions = [Question("word-test", kind, "hiragana", "", "こーひー",
        metadata={"practice_config": {
            "kana_categories": None, "requested_count": 3003,
            "word_collection": "english_loanwords", "include_research": True,
            "word_table": "full",
        }})]
    app._clean_preferences = KanaMemory._clean_preferences
    app.show_practice_setup = Mock()
    KanaMemory.show_new_batch(app)
    prefs = app._practice_preferences
    assert prefs["kind"] == before["kind"]
    assert prefs["mixed_kinds"] == before["mixed_kinds"]
    assert prefs["script"] == "平假名" and prefs["batch"] == NEW_ONLY
    assert prefs["word_collection"] == "英语借词" and prefs["research"] is True
    assert prefs["word_table"] == FULL
    assert prefs["count"] == "自定义" and prefs["custom_count"] == "3003"


def test_legacy_completed_batch_keeps_current_kind_and_scope_with_completed_script(setup_app):
    app = setup_app
    set_mode(app, "看假名拼罗马音", script="片假名", scopes=("浊音",), count=15)
    before = deepcopy(app._practice_preferences)
    app.page = "session_done"
    app.questions = [Question("legacy", "word_kana", "hiragana", "", "あ")]
    app._clean_preferences = KanaMemory._clean_preferences
    app.show_practice_setup = Mock()
    KanaMemory.show_new_batch(app)
    assert app._practice_preferences == {
        **before, "script": "平假名", "scopes": list(before["scopes"]), "batch": NEW_ONLY,
    }
    app.show_practice_setup.assert_called_once()


def test_home_groups_learning_actions_without_duplicate_navigation(setup_app):
    app = setup_app
    app.page = "home"
    app._stats_summary = Mock(return_value="今日新增 300 个")
    app.content_label = lambda text, **kw: View(text=text, **kw)
    for name in ("show_practice_setup", "show_new_batch", "show_kana", "show_free_ink", "show_progress", "show_cards", "show_tutor_center"):
        setattr(app, name, Mock())
    KanaMemory.show_home.__wrapped__(app)
    assert app.body.children[:2] == [app.home_action_slot, app.home_secondary_slot]
    buttons = [item for item in descendants(app.body) if item.on_press]
    # The retention check entry joined the home with 561fa07.
    assert [item.text for item in buttons] == ["针对性练习 · 问 AI", "检查掌握情况", "学习记录"]
    buttons[0].on_press(None)
    app.show_tutor_center.assert_called_once()
    buttons[1].on_press(None)
    app.show_retention_check_center.assert_called_once()
    entry = buttons[2]
    entry.on_press(None)
    app.show_progress.assert_called_once()
    for removed in ("浏览假名表", "自由试写识别", "准备模型与语音", "管理记忆卡片",
                    "查看复习进度", "开始今天的练习", "增加新批次"):
        assert removed not in text_of(app.body)
    app.show_new_batch.assert_not_called()
    app.store.start_session.assert_not_called()


@pytest.mark.parametrize("caption,handler", [
    ("查看复习记录", "show_progress"), ("回到今日", "show_home"),
])
def test_completed_page_secondary_buttons_do_not_start_another_batch(question_app, caption, handler):
    app = question_app
    app.questions, app.question_index, app.session_right = [object()], 1, 1
    app.show_new_batch, app.show_progress, app.show_home = Mock(), Mock(), Mock()
    app.show_question()
    assert app.page == "session_done"
    entry = next(item for item in descendants(app.body) if item.text == caption)
    entry.on_press(None)
    getattr(app, handler).assert_called_once()
    app.show_new_batch.assert_not_called()
    app.store.start_session.assert_not_called()
    app.store.record_review.assert_not_called()


def test_completed_page_offers_study_path_next_step_instead_of_new_batch(question_app):
    app = question_app
    app.questions, app.question_index, app.session_right = [object()], 1, 1
    app.show_new_batch, app.show_progress, app.show_home = Mock(), Mock(), Mock()
    overview = {"sections": []}
    app.study_path_service = lambda: SimpleNamespace(overview=lambda: overview)
    app.continue_study_path = Mock()  # A route runtime; standalone controllers get no path controls.
    next_step = Mock()
    app._path_next_controls = Mock(return_value=[View(text="学习下一小节", on_press=next_step)])
    app.show_question()
    assert app.page == "session_done"
    app._path_next_controls.assert_called_once_with(overview)
    texts = [item.text for item in descendants(app.body)]
    assert "学习下一小节" in texts and "增加新批次" not in texts
    next(item for item in descendants(app.body) if item.text == "学习下一小节").on_press(None)
    next_step.assert_called_once()
    app.show_new_batch.assert_not_called()
    app.store.start_session.assert_not_called()


@pytest.mark.parametrize("count", [201, 3003, 100000])
def test_preferences_keep_large_user_requested_count(count):
    prefs = KanaMemory._clean_preferences({"count": "自定义", "custom_count": str(count), "batch": NEW_ONLY})
    assert prefs["custom_count"] == str(count) and prefs["batch"] == NEW_ONLY


@pytest.mark.parametrize("value", [None, [], {}, 42, "unrecognized"])
def test_imported_invalid_batch_uses_due_first_default(value):
    assert KanaMemory._clean_preferences({"batch": value})["batch"] == DUE_FIRST
