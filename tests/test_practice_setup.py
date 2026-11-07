"""Practice configuration remains coherent across modes, Back and async checks."""
import asyncio
from unittest.mock import Mock

import pytest

from kanamemory import app as app_module
from kanamemory.app import KanaMemory
from kanamemory.navigation import PageState
from support.fake_ui import View, descendants, text_of
from support.app_fixtures import CHOICE, DEFAULT_CATEGORIES, FULL, HANDWRITING, PARTIAL, WORD, pause_typing, question_app as question_app, render_question, set_mode, setup_app as setup_app


def test_default_visual_practice_has_no_unrelated_word_or_service_requirements(setup_app):
    app = setup_app
    assert app._practice_config() == ("kana_choice", "hiragana", False, "partial", DEFAULT_CATEGORIES, 10)
    assert app.practice_scope_card in app.practice_scope_slot.children
    assert app.practice_word_card not in app.practice_word_slot.children
    assert app.practice_start_button.enabled
    assert "平假名" in app.script_picker.items and "片假名" in app.script_picker.items
    assert "词" not in app.practice_script_label.text
    assert all(call[0] == "set_setting" for call in app.store.mock_calls)
    app.store.record_review.assert_not_called()


@pytest.mark.parametrize("kind,internal", [(CHOICE, "kana_choice"), (HANDWRITING, "kana_handwriting")])
def test_hidden_word_options_never_change_kana_question_arguments(setup_app, kind, internal):
    app = setup_app
    set_mode(app, kind, script="片假名", research=True, table=FULL)
    assert app._practice_config() == (internal, "katakana", False, "partial", DEFAULT_CATEGORIES, 10)
    assert app.practice_word_card not in app.practice_word_slot.children


@pytest.mark.parametrize("title,categories", [
    ("清音与拨音", ("basic",)),
    ("浊音", ("dakuten",)),
    ("半浊音", ("handakuten",)),
    ("拗音", ("yoon",)),
    ("外来语组合（含ヴ）", ("foreign_combination", "foreign_voiced")),
])
@pytest.mark.parametrize("kind", [CHOICE, HANDWRITING])
def test_single_scope_is_passed_to_question_generation(setup_app, monkeypatch, title, categories, kind):
    app = setup_app
    app.platform.tts_ready, app.model_state = True, "ready"
    set_mode(app, kind, scopes=(title,))
    build = Mock(return_value=[object()])
    monkeypatch.setattr(app_module, "build_questions", build)
    assert app.practice_start_button.enabled
    asyncio.run(app.start_practice())
    assert build.call_args.kwargs["kana_categories"] == categories
    app.store.start_session.assert_called_once()
    app.show_question.assert_called_once()


def test_mixed_scopes_survive_script_switch_and_reopening(setup_app, monkeypatch):
    app = setup_app
    set_mode(app, CHOICE, scopes=("清音与拨音", "半浊音", "外来语组合（含ヴ）"), count=20)
    set_mode(app, CHOICE, script="片假名")
    KanaMemory.show_practice_setup.__wrapped__(app)
    assert app._practice_config() == (
        "kana_choice", "katakana", False, "partial",
        ("basic", "handakuten", "foreign_combination", "foreign_voiced"), 20,
    )
    build = Mock(return_value=[object()])
    monkeypatch.setattr(app_module, "build_questions", build)
    asyncio.run(app.start_practice())
    assert build.call_args.kwargs["kana_categories"] == ("basic", "handakuten", "foreign_combination", "foreign_voiced")
    assert build.call_args.kwargs["script"] == "katakana"
    assert build.call_args.kwargs["limit"] == 20


@pytest.mark.parametrize("kind", [CHOICE, HANDWRITING])
def test_empty_scope_blocks_button_and_direct_start_without_resetting_selection(setup_app, monkeypatch, kind):
    app = setup_app
    app.platform.tts_ready, app.model_state = True, "ready"
    set_mode(app, kind, scopes=())
    KanaMemory.show_practice_setup.__wrapped__(app)
    assert app._practice_config()[4] == ()
    assert not app.practice_start_button.enabled
    assert "至少选择一种假名范围" in text_of(app.practice_services)
    build = Mock()
    monkeypatch.setattr(app_module, "build_questions", build)
    asyncio.run(app.start_practice())
    build.assert_not_called()
    app.store.start_session.assert_not_called()
    app.platform.model_status.assert_not_awaited()
    app.alert.assert_called_once()
    assert not app._starting_practice


@pytest.mark.parametrize("scopes", [(), ("外来语组合（含ヴ）",)])
def test_word_questions_ignore_hidden_kana_scope_but_restore_it_on_return(setup_app, monkeypatch, scopes):
    app = setup_app
    app.platform.tts_ready = True
    set_mode(app, CHOICE, scopes=scopes)
    set_mode(app, WORD, research=True, count=15)
    assert not app.practice_scope_slot.children
    assert app.practice_start_button.enabled
    build = Mock(return_value=[object()])
    monkeypatch.setattr(app_module, "build_questions", build)
    asyncio.run(app.start_practice())
    assert build.call_args.kwargs["kana_categories"] is None
    assert build.call_args.kwargs["limit"] == 15
    set_mode(app, CHOICE)
    assert app.practice_scope_card in app.practice_scope_slot.children
    assert tuple(title for title, control in app.scope_switches.items() if control.value) == scopes
    assert app.practice_start_button.enabled is bool(scopes)


@pytest.mark.parametrize("count", [5, 10, 15, 20, 30])
@pytest.mark.parametrize("kind", [CHOICE, HANDWRITING, WORD])
def test_each_question_count_reaches_generator_for_each_mode(setup_app, monkeypatch, kind, count):
    app = setup_app
    app.platform.tts_ready, app.model_state = True, "ready"
    set_mode(app, kind, research=True, count=count)
    build = Mock(return_value=[object()])
    monkeypatch.setattr(app_module, "build_questions", build)
    asyncio.run(app.start_practice())
    assert build.call_args.kwargs["limit"] == count
    assert app._practice_preferences["count"] == f"{count} 项"
    app.store.start_session.assert_called_once()


@pytest.mark.parametrize("script,internal", [("平假名", "hiragana"), ("片假名", "katakana")])
@pytest.mark.parametrize("table,internal_table", [(PARTIAL, "partial"), (FULL, "full")])
def test_word_mode_explains_answer_script_and_uses_explicit_keyboard_choice(setup_app, script, internal, table, internal_table):
    app = setup_app
    set_mode(app, WORD, script=script, research=True, table=table)
    assert app._practice_config() == ("word_kana", internal, True, internal_table, None, 10)
    assert app.practice_scope_card not in app.practice_scope_slot.children
    assert app.practice_word_card in app.practice_word_slot.children
    assert "作答" in app.practice_script_label.text
    assert "统一" in app.practice_script_hint.text and "字形" in app.practice_script_hint.text


def test_mode_switches_retain_controls_and_save_word_preferences(setup_app):
    app = setup_app
    controls = (app.kind_picker, app.script_picker, app.word_table_picker, app.research_switch, app.scope_switches, app.count_picker)
    set_mode(app, WORD, script="片假名", research=True, table=FULL, scopes=("浊音", "外来语组合（含ヴ）"), count=20)
    for mode in (CHOICE, HANDWRITING, WORD):
        set_mode(app, mode)
        assert (app.kind_picker, app.script_picker, app.word_table_picker, app.research_switch, app.scope_switches, app.count_picker) == controls
        assert app.script_picker.value == "片假名"
        assert app.word_table_picker.value == FULL and app.research_switch.value is True
        assert tuple(title for title, control in app.scope_switches.items() if control.value) == ("浊音", "外来语组合（含ヴ）")
        assert app.count_picker.value == "20 项"
    assert {key: value for key, value in app._practice_preferences.items() if key != "mixed_kinds"} == {"kind": WORD, "script": "片假名", "word_table": FULL, "research": True,
                                         "scopes": ("浊音", "外来语组合（含ヴ）"), "count": "20 项",
                                         "custom_count": "10", "word_collection": "全部可用词汇", "batch": "到期优先，补充新内容",
                                         "rounds": "1", "schedule": "多轮强化", "question_plan_version": 2,
                                         "listening_handwriting_opt_in_version": 1}
    assert set(app._practice_preferences["mixed_kinds"]) == set(app_module.DEFAULT_MIXED_KINDS)
    assert len(app._practice_preferences["mixed_kinds"]) == len(app_module.DEFAULT_MIXED_KINDS)
    assert all(call[0] == "set_setting" for call in app.store.mock_calls)
    app.store.set_setting.assert_called_with("practice_preferences", app._practice_preferences)
    app.store.record_review.assert_not_called()
    KanaMemory.show_practice_setup.__wrapped__(app)
    assert app._practice_config() == ("word_kana", "katakana", True, "full", None, 20)


def test_delayed_events_from_replaced_controls_cannot_change_current_preferences(setup_app):
    app = setup_app
    old_picker = app.kind_picker
    KanaMemory.show_practice_setup.__wrapped__(app)
    assert old_picker is not app.kind_picker
    before = app._practice_preferences.copy()
    app.kind_picker.value = WORD
    old_picker.on_change(old_picker)
    assert app._practice_preferences == before
    assert app.practice_word_card not in app.practice_word_slot.children
    app.kind_picker.on_change(app.kind_picker)
    assert app._practice_preferences["kind"] == WORD
    assert app.practice_word_card in app.practice_word_slot.children


@pytest.mark.parametrize("control_name", ["scope", "count", "batch"])
def test_delayed_scope_or_count_event_cannot_apply_new_page_edits(setup_app, control_name):
    app = setup_app
    def control():
        return {"scope": app.scope_switches["清音与拨音"], "count": app.count_picker, "batch": app.batch_picker}[control_name]
    old_control = control()
    KanaMemory.show_practice_setup.__wrapped__(app)
    before = app._practice_preferences.copy()
    app.scope_switches["清音与拨音"].value = False
    app.count_picker.value = "30 项"
    app.batch_picker.value = "只学新内容"
    old_control.on_change(old_control)
    assert app._practice_preferences == before
    current = control()
    current.on_change(current)
    assert app._practice_preferences["scopes"] == ("浊音", "半浊音", "拗音")
    assert app._practice_preferences["count"] == "30 项"
    assert app._practice_preferences["batch"] == "只学新内容"


@pytest.mark.parametrize("kind,tts,model,research,starting,enabled", [
    (CHOICE, False, "not_downloaded", False, False, True),
    (CHOICE, True, "ready", True, True, False),
    (HANDWRITING, False, "ready", False, False, False),
    (HANDWRITING, True, "not_downloaded", False, False, False),
    (HANDWRITING, True, "downloading", False, False, False),
    (HANDWRITING, True, "ready", False, False, True),
    (WORD, False, "ready", True, False, False),
    (WORD, True, "not_downloaded", False, False, False),
    (WORD, True, "not_downloaded", True, False, True),
    (WORD, True, "ready", True, True, False),
])
def test_start_availability_depends_only_on_current_mode(setup_app, kind, tts, model, research, starting, enabled):
    app = setup_app
    app.platform.tts_ready, app.model_state, app._starting_practice = tts, model, starting
    set_mode(app, kind, research=research)
    assert app.practice_start_button.enabled is enabled


def test_handwriting_setup_exposes_model_download_and_keeps_voice_setup_separate(setup_app):
    app = setup_app
    set_mode(app, HANDWRITING)
    captions = text_of(app.practice_services)
    assert "下载日语手写模型" in captions and "重新检查模型" in captions
    assert "准备日语声音" in captions
    assert not app.practice_start_button.enabled
    app.platform.tts_ready, app.model_state = True, "ready"
    app.refresh_model_ui()
    assert app.practice_start_button.enabled
    assert "下载日语手写模型" not in text_of(app.practice_services)
    assert "准备日语声音" not in text_of(app.practice_services)
    set_mode(app, CHOICE)
    assert "模型" not in text_of(app.practice_services)


def test_back_restores_configuration_and_refreshes_new_service_readiness(setup_app):
    app = setup_app
    set_mode(app, HANDWRITING, script="片假名", scopes=("拗音", "外来语组合（含ヴ）"), count=15)
    assert not app.practice_start_button.enabled
    old_controls = (app.kind_picker, app.script_picker, app.practice_start_button, app.scope_switches, app.count_picker)
    bindings = {name: getattr(app, name) for name in app_module.PAGE_BINDINGS["practice_setup"]}
    body = app.body
    previous = PageState(("show_practice_setup", None), "practice_setup", "选择练习", body, bindings)
    app.back_stack.pages.append(previous)
    # Other navigation can replace all instance references before this old page
    # comes back; dynamic labels and containers must belong to the saved page.
    KanaMemory.show_practice_setup.__wrapped__(app)
    assert app.kind_picker is not old_controls[0]
    app.__dict__.update(page="settings", _view_key=("show_settings", None),
        _ink_revision=0, _audio_serial=0, _paint_handle=None, question=None,
        content_slot=View(View(text="设置")), title_label=View(text="设置"), _sync_back_handler=Mock())
    app.platform.tts_ready, app.model_state = True, "ready"
    assert KanaMemory.go_back(app) is True
    assert app.content_slot.children == [body]
    assert (app.kind_picker, app.script_picker, app.practice_start_button, app.scope_switches, app.count_picker) == old_controls
    assert app._practice_config() == ("kana_handwriting", "katakana", False, "partial", ("yoon", "foreign_combination", "foreign_voiced"), 15)
    assert app.practice_start_button.enabled
    set_mode(app, WORD, research=True)
    assert app.practice_word_card in app.practice_word_slot.children
    assert app.practice_word_slot in list(descendants(body))
    assert app.practice_scope_slot in list(descendants(body))
    assert app.count_picker in list(descendants(body))
    assert app.practice_script_label in list(descendants(body))
    assert "作答" in app.practice_script_label.text


@pytest.mark.parametrize("kind,internal,research,table,expected_research,expected_table", [
    (CHOICE, "kana_choice", True, FULL, False, "partial"),
    (HANDWRITING, "kana_handwriting", True, FULL, False, "partial"),
    (WORD, "word_kana", True, PARTIAL, True, "partial"),
    (WORD, "word_kana", True, FULL, True, "full"),
])
def test_start_passes_only_effective_configuration_and_starts_one_session(setup_app, monkeypatch, kind, internal, research, table, expected_research, expected_table):
    app = setup_app
    app.platform.tts_ready, app.model_state = True, "ready"
    set_mode(app, kind, script="片假名", research=research, table=table)
    questions = [object()]
    build = Mock(return_value=questions)
    monkeypatch.setattr(app_module, "build_questions", build)
    asyncio.run(app.start_practice())
    build.assert_called_once()
    assert build.call_args.args == (app.repo, app.store)
    args = build.call_args.kwargs
    assert (args["kind"], args["script"], args["include_research"], args["word_table"]) == (internal, "katakana", expected_research, expected_table)
    assert args["limit"] == 10
    assert args["kana_categories"] == (None if kind == WORD else DEFAULT_CATEGORIES)
    app.store.start_session.assert_called_once_with(questions, seed=args["seed"])
    app.show_question.assert_called_once()
    assert app.script == "katakana" and not app._starting_practice
    app.alert.assert_not_called()


@pytest.mark.parametrize("reason", ["no_voice", "no_model", "word_consent"])
def test_direct_start_cannot_bypass_required_preparation_or_word_consent(setup_app, monkeypatch, reason):
    app = setup_app
    app.platform.tts_ready, app.model_state = True, "ready"
    set_mode(app, WORD if reason == "word_consent" else HANDWRITING)
    if reason == "no_voice":
        app.platform.tts_ready = False
    elif reason == "no_model":
        app.platform.model_status.return_value = "not_downloaded"
    build = Mock()
    monkeypatch.setattr(app_module, "build_questions", build)
    asyncio.run(app.start_practice())
    build.assert_not_called()
    app.store.start_session.assert_not_called()
    app.show_question.assert_not_called()
    assert not app._starting_practice


@pytest.mark.parametrize("changed", ["page", "kind", "script", "scope", "count", "batch"])
def test_async_model_result_cannot_start_an_abandoned_or_changed_configuration(setup_app, monkeypatch, changed):
    app = setup_app
    app.platform.tts_ready, app.model_state = True, "ready"
    set_mode(app, HANDWRITING, script="片假名")
    original = (app.script, app.research_enabled, app.use_full_keyboard)
    build = Mock()
    monkeypatch.setattr(app_module, "build_questions", build)

    async def run():
        ready = asyncio.get_running_loop().create_future()
        async def status():
            return await ready
        app.platform.model_status.side_effect = status
        task = asyncio.create_task(app.start_practice())
        await asyncio.sleep(0)
        assert app._starting_practice
        if changed == "page":
            app.page, app._page_token = "settings", app._page_token + 1
        elif changed == "kind":
            set_mode(app, CHOICE)
        elif changed == "script":
            set_mode(app, HANDWRITING, script="平假名")
        elif changed == "scope":
            set_mode(app, HANDWRITING, scopes=("浊音",))
        elif changed == "batch":
            set_mode(app, HANDWRITING, batch="只学新内容")
        else:
            set_mode(app, HANDWRITING, count=30)
        ready.set_result("ready")
        await task

    asyncio.run(run())
    build.assert_not_called()
    app.store.start_session.assert_not_called()
    app.show_question.assert_not_called()
    assert (app.script, app.research_enabled, app.use_full_keyboard) == original
    assert not app._starting_practice


def test_duplicate_start_while_checking_model_creates_only_one_session(setup_app, monkeypatch):
    app = setup_app
    app.platform.tts_ready, app.model_state = True, "ready"
    set_mode(app, HANDWRITING)
    build = Mock(return_value=[object()])
    monkeypatch.setattr(app_module, "build_questions", build)

    async def run():
        ready = asyncio.get_running_loop().create_future()
        async def status():
            return await ready
        app.platform.model_status.side_effect = status
        task = asyncio.create_task(app.start_practice())
        await asyncio.sleep(0)
        await app.start_practice()
        assert not app.practice_start_button.enabled
        app.platform.model_status.assert_awaited_once()
        ready.set_result("ready")
        await task

    asyncio.run(run())
    build.assert_called_once()
    app.store.start_session.assert_called_once()
    assert not app._starting_practice


@pytest.mark.parametrize("result", ["empty", "generation_error", "storage_error", "model_error"])
def test_failed_start_keeps_configuration_retriable_without_a_review(setup_app, monkeypatch, result):
    app = setup_app
    app.platform.tts_ready, app.model_state = True, "ready"
    set_mode(app, HANDWRITING, script="片假名")
    build = Mock(return_value=[] if result == "empty" else [object()])
    monkeypatch.setattr(app_module, "build_questions", build)
    if result == "generation_error":
        build.side_effect = RuntimeError("fixture generation failure")
    elif result == "storage_error":
        app.store.start_session.side_effect = RuntimeError("fixture storage failure")
    elif result == "model_error":
        app.platform.model_status.side_effect = RuntimeError("fixture model unavailable")
    asyncio.run(app.start_practice())
    assert not app._starting_practice
    assert app.practice_start_button.enabled
    assert app._practice_config() == ("kana_handwriting", "katakana", False, "partial", DEFAULT_CATEGORIES, 10)
    app.show_question.assert_not_called()
    app.store.record_review.assert_not_called()
    app.alert.assert_called_once()


@pytest.mark.parametrize("title,kind", [
    ("看假名拼罗马音", "kana_romaji"), ("看罗马音选假名", "romaji_kana"),
    ("看词义和罗马音选假名", "word_meaning_kana"),
])
def test_new_visual_modes_start_without_voice_or_handwriting(setup_app, monkeypatch, title, kind):
    app = setup_app
    app.repo.approved_word_count.return_value = 121
    set_mode(app, title, count=20, scopes=("浊音",))
    assert app.practice_start_button.enabled
    assert "声音" not in text_of(app.practice_services)
    assert "模型" not in text_of(app.practice_services)
    build = Mock(return_value=[object()])
    monkeypatch.setattr(app_module, "build_questions", build)
    asyncio.run(app.start_practice())
    assert build.call_args.kwargs["kind"] == kind
    assert build.call_args.kwargs["limit"] == 20
    assert build.call_args.kwargs["kana_categories"] == (None if kind == "word_meaning_kana" else ("dakuten",))
    app.platform.model_status.assert_not_awaited()
    app.show_question.assert_called_once()


@pytest.mark.parametrize("count", [1, 47, 200, 201, 3003, 100000])
def test_custom_memory_count_has_no_app_limit_and_is_saved_and_used(setup_app, monkeypatch, count):
    app = setup_app
    app.count_picker.value = "自定义"
    app.custom_count_input.value = str(count)
    app.refresh_practice_setup()
    assert app.custom_count_input in app.custom_count_slot.children
    assert app._practice_config()[-1] == count
    assert app._practice_preferences["custom_count"] == str(count)
    assert not hasattr(app, "daily_new_limit_input")
    app.store.set_daily_new_limit.assert_not_called()
    KanaMemory.show_practice_setup.__wrapped__(app)
    assert app.custom_count_input.value == str(count)
    build = Mock(return_value=[object()])
    monkeypatch.setattr(app_module, "build_questions", build)
    asyncio.run(app.start_practice())
    assert build.call_args.kwargs["limit"] == count


@pytest.mark.parametrize("value", ["", "0", "1.5", "-1", "a"])
def test_invalid_memory_counts_block_start_without_generating_questions(setup_app, monkeypatch, value):
    app = setup_app
    app.count_picker.value = "自定义"
    app.custom_count_input.value = value
    app.refresh_practice_setup()
    assert not app.practice_start_button.enabled
    assert "大于 0 的整数" in text_of(app.practice_services)
    build = Mock()
    monkeypatch.setattr(app_module, "build_questions", build)
    asyncio.run(app.start_practice())
    build.assert_not_called()
    app.store.start_session.assert_not_called()
    app.alert.assert_called_once()


@pytest.mark.parametrize("kind", [WORD, "看词义和罗马音选假名"])
@pytest.mark.parametrize("title,collection", list(app_module.PRACTICE_WORD_COLLECTIONS.items()))
def test_word_collection_is_preserved_and_reaches_generator(setup_app, monkeypatch, kind, title, collection):
    app = setup_app
    app.platform.tts_ready = True
    set_mode(app, kind, research=True)
    app.practice_word_collection_picker.value = title
    app.refresh_practice_setup()
    set_mode(app, CHOICE)
    assert app._practice_word_collection() is None
    set_mode(app, kind)
    assert app._practice_word_collection() == collection
    KanaMemory.show_practice_setup.__wrapped__(app)
    assert app.practice_word_collection_picker.value == title
    build = Mock(return_value=[object()])
    monkeypatch.setattr(app_module, "build_questions", build)
    asyncio.run(app.start_practice())
    assert build.call_args.kwargs["word_collection"] == collection


def test_custom_count_changed_during_model_check_does_not_start_old_plan(setup_app, monkeypatch):
    app = setup_app
    app.platform.tts_ready, app.model_state = True, "ready"
    set_mode(app, HANDWRITING)
    app.count_picker.value = "自定义"
    app.custom_count_input.value = "201"
    app.refresh_practice_setup()
    build = Mock(return_value=[object()])
    monkeypatch.setattr(app_module, "build_questions", build)
    async def run():
        ready = asyncio.get_running_loop().create_future()
        app.platform.model_status.side_effect = lambda: ready
        # AsyncMock returns its side_effect result without awaiting Future.
        async def status():
            return await ready
        app.platform.model_status.side_effect = status
        task = asyncio.create_task(app.start_practice())
        await asyncio.sleep(0)
        app.custom_count_input.value = "3003"
        ready.set_result("ready")
        await task
    asyncio.run(run())
    build.assert_not_called()
    app.store.start_session.assert_not_called()


def test_romaji_typing_renders_editable_input_and_submits_without_audio(question_app):
    app = question_app
    render_question(app, "kana_romaji")
    assert app.page == "question" and app.question_audio_ok
    # The kana stimulus and input replace the redundant instruction (0.28.9, c7ac715).
    assert "し" in text_of(app.body) and "拼出对应的罗马音" not in text_of(app.body)
    assert app.romaji_input in list(descendants(app.body))
    app.romaji_input.value = "shi"
    app.romaji_input.on_change(app.romaji_input)
    assert app.romaji_answer == "shi"
    app._checkpoint.assert_called()
    app.romaji_input.on_confirm(app.romaji_input)
    assert app.page == "feedback" and app.answer_correct
    assert "记住了" in text_of(app.body)
    app.play_question_audio.assert_not_called()


@pytest.mark.parametrize("kind", ["kana_romaji", "romaji_kana", "word_meaning_kana"])
def test_new_modes_reveal_without_voice_and_allow_only_again(question_app, kind):
    app = question_app
    render_question(app, kind)
    app.reveal_answer()
    assert app.page == "feedback" and not app.answer_correct
    assert ("保存并继续" if kind == "word_meaning_kana" else "再学一次") in text_of(app.body)
    assert "很轻松" not in text_of(app.body) and "有些困难" not in text_of(app.body)
    app.alert.assert_not_called()


def test_romaji_prompt_offers_kana_without_exposing_answer_as_prompt(question_app):
    app = question_app
    render_question(app, "romaji_kana")
    assert app.question_audio_ok
    assert any(child.text == "shi" for child in app.body.children)
    options = [child for child in app.body.children if child.text in {"し", "き", "い"}]
    assert len(options) == 3 and all(child.on_press for child in options)
    next(child for child in options if child.text == "し").on_press(None)
    assert app.answer_correct


def test_meaning_mode_displays_meaning_and_romaji_with_kana_keyboard_without_audio(question_app):
    app = question_app
    render_question(app, "word_meaning_kana")
    assert app.page == "word_question" and app.question_audio_ok
    assert "shi" in text_of(app.body) and "poem" in text_of(app.body)
    assert "播放词语" not in text_of(app.body)
    app.append_word_key("し")
    assert app.word_answer == "し"
    app.check_answer(app.word_answer)
    assert app.answer_correct


def test_stale_romaji_input_cannot_overwrite_current_answer(question_app):
    app = question_app
    render_question(app, "kana_romaji")
    previous = app.romaji_input
    render_question(app, "kana_romaji")
    previous.value = "shi"
    app.change_romaji_answer(previous)
    assert app.romaji_answer == ""


def typed_count(app, value):
    app.count_picker.value = "自定义"
    app.count_picker.on_change(app.count_picker)
    app.store.set_setting.reset_mock(); app.practice_plan_summary.reset_mock(); app.loop.call_later.reset_mock()
    app.custom_count_input.value = value
    app.custom_count_input.on_change(app.custom_count_input)


def test_typed_numbers_save_and_plan_once_after_a_pause_not_per_keystroke(setup_app):
    app = setup_app
    KanaMemory.show_practice_setup.__wrapped__(app)
    typed_count(app, "1")
    first = app.loop.call_later.return_value
    app.custom_count_input.value = "12"
    app.custom_count_input.on_change(app.custom_count_input)
    first.cancel.assert_called_once_with()
    app.store.set_setting.assert_not_called()
    app.practice_plan_summary.assert_not_called()
    assert app._practice_preferences["custom_count"] != "12"
    pause_typing(app, app.custom_count_input)
    assert app._practice_preferences["custom_count"] == "12"
    app.store.set_setting.assert_called_once_with("practice_preferences", app._practice_preferences)
    app.practice_plan_summary.assert_called_once()


def test_confirm_applies_typed_rounds_immediately(setup_app):
    app = setup_app
    KanaMemory.show_practice_setup.__wrapped__(app)
    app.rounds_input.value = "3"
    app.rounds_input.on_change(app.rounds_input)
    assert app._practice_preferences["rounds"] != "3"
    app.rounds_input.on_confirm(app.rounds_input)
    app.loop.call_later.return_value.cancel.assert_called()
    assert app._practice_preferences["rounds"] == "3"


def test_pending_typed_number_is_dropped_after_leaving_the_page(setup_app):
    app = setup_app
    KanaMemory.show_practice_setup.__wrapped__(app)
    typed_count(app, "12")
    delayed = app.loop.call_later.call_args.args[1]
    app.page = "home"; app._page_token += 1
    delayed()
    app.store.set_setting.assert_not_called()
    app.practice_plan_summary.assert_not_called()


def test_start_applies_a_pending_typed_number_before_building(setup_app, monkeypatch):
    app = setup_app
    KanaMemory.show_practice_setup.__wrapped__(app)
    typed_count(app, "12")
    starts = []
    async def start(token, **kwargs):
        starts.append(app._practice_preferences["custom_count"])
    app._start_practice = start
    asyncio.run(app.start_practice())
    assert starts == ["12"]
    app.loop.call_later.return_value.cancel.assert_called()
