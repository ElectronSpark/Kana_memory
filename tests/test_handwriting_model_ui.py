"""The real-model preparation UI stays usable without changing learner records."""
import asyncio
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
from kanamemory import app as app_module
from kanamemory.app import KanaMemory
from kanamemory.domain import Question
from kanamemory.navigation import BackStack, PageState


from support.handwriting_model_ui_support import View, descendants, text_of, buttons, model_app


@pytest.mark.parametrize("compact", [False, True])
def test_missing_model_has_a_direct_download_and_recheck(model_app, compact):
    panel = model_app.model_controls(compact=compact)
    actions = {control.text: control for control in buttons(panel)}
    assert "下载日语手写模型" in actions
    assert "重新检查模型" in actions
    assert actions["下载日语手写模型"].enabled
    assert "未下载" in text_of(panel)
    assert model_app.store.mock_calls == []


def test_failed_model_shows_reason_and_retriable_download(model_app):
    model_app.model_state = "error"
    model_app.model_message = "网络暂时不可用，请重试。"
    panel = model_app.model_controls(compact=True)
    assert "网络暂时不可用" in text_of(panel)
    retry = next(control for control in buttons(panel) if control.text == "重试下载日语模型")
    assert retry.enabled


def test_ready_compact_panel_does_not_prompt_another_download(model_app):
    model_app.model_state = "ready"
    compact = model_app.model_controls(compact=True)
    assert "已下载" in text_of(compact)
    assert buttons(compact) == []
    expanded = model_app.model_controls()
    assert [control.text for control in buttons(expanded)] == ["重新检查模型"]


def test_checking_or_downloading_cannot_enable_ink_recognition(model_app):
    for state in ("checking", "not_downloaded", "downloading", "error", "unavailable"):
        model_app.model_state = state
        model_app.recognize_button.enabled = True
        model_app.refresh_model_ui()
        assert not model_app.recognize_button.enabled, state
    model_app.model_state = "ready"
    model_app.refresh_model_ui()
    assert model_app.recognize_button.enabled
    model_app._ink_busy = True
    model_app.refresh_model_ui()
    assert not model_app.recognize_button.enabled


@pytest.mark.parametrize("state,text", [
    ("not_downloaded", "未下载模型"), ("checking", "检查中…"), ("downloading", "下载中…"),
    ("error", "模型未就绪"), ("unavailable", "安卓可用"),
])
def test_unavailable_recognition_has_an_explicit_disabled_label_and_neutral_colors(model_app, state, text):
    app = model_app
    app.model_state = state
    app.recognize_button.style = SimpleNamespace(color=app_module.WHITE, background_color=app_module.GREEN)
    app.refresh_model_ui()
    assert app.recognize_button.text == text and not app.recognize_button.enabled
    assert app.recognize_button.style.color == app_module.MUTED
    assert app.recognize_button.style.background_color != app_module.GREEN
    if state in {"not_downloaded", "error"}:
        assert any(control.enabled and "下载" in control.text for control in buttons(app.ink_model_panel))
    app.model_state = "ready"
    app.refresh_model_ui()
    assert app.recognize_button.text == "识别笔迹" and app.recognize_button.enabled
    assert app.recognize_button.style.color == app_module.WHITE
    assert app.recognize_button.style.background_color == app_module.GREEN


def test_busy_recognition_is_labeled_until_the_actual_model_finishes(model_app):
    app = model_app
    app.model_state = "ready"

    async def run():
        pending = asyncio.get_running_loop().create_future()

        async def recognize(*args):
            return await pending

        app.platform.recognize.side_effect = recognize
        task = asyncio.create_task(app.recognize_ink())
        await asyncio.sleep(0)
        assert app.recognize_button.text == "识别中…" and not app.recognize_button.enabled
        pending.set_result(["ニ"])
        await task

    asyncio.run(run())
    assert app.recognize_button.text == "识别笔迹" and app.recognize_button.enabled


def test_successful_download_refreshes_the_same_ink_page_and_preserves_drawing(model_app):
    app = model_app
    canvas, strokes, token = app.canvas, app.strokes, app._page_token
    asyncio.run(app.download_model())
    app.platform.download_model.assert_awaited_once()
    app.platform.model_status.assert_awaited_once()
    assert app.model_state == "ready" and not app._model_downloading
    assert app.recognize_button.enabled
    assert app.canvas is canvas and app.strokes is strokes and app._page_token == token
    assert app.service_message == "日语声音已就绪"
    assert app.store.mock_calls == []


def test_download_failure_is_retriable_without_losing_ink_or_grading(model_app):
    app = model_app
    strokes = app.strokes
    app.platform.download_model.side_effect = RuntimeError("暂时无法联网")
    asyncio.run(app.download_model())
    assert app.model_state == "error" and not app._model_downloading
    assert "暂时无法联网" in app.model_message
    assert not app.recognize_button.enabled and app.strokes is strokes
    app.platform.download_model.side_effect = None
    asyncio.run(app.download_model())
    assert app.model_state == "ready" and app.recognize_button.enabled
    assert app.store.mock_calls == []


@pytest.mark.parametrize("reported", ["not_downloaded", "error", "unavailable"])
def test_download_must_verify_ready_before_reporting_success(model_app, reported):
    app = model_app
    app.platform.model_status.return_value = reported
    asyncio.run(app.download_model())
    assert app.model_state != "ready" and not app.recognize_button.enabled
    assert "已准备" not in app.model_message and "已就绪" not in app.model_message
    assert not app._model_downloading
    assert app.store.mock_calls == []


def test_duplicate_downloads_share_one_active_attempt(model_app):
    app = model_app

    async def run():
        pending = asyncio.get_running_loop().create_future()
        async def download():
            await pending

        app.platform.download_model.side_effect = download
        first = asyncio.create_task(app.download_model())
        await asyncio.sleep(0)
        assert app._model_downloading and app.model_state == "downloading"
        await app.download_model()
        app.platform.download_model.assert_awaited_once()
        pending.set_result(None)
        await first

    asyncio.run(run())
    assert app.model_state == "ready" and not app._model_downloading


def test_late_pre_download_check_cannot_overwrite_verified_download(model_app):
    app = model_app

    async def run():
        pending = asyncio.get_running_loop().create_future()
        calls = 0

        async def status():
            nonlocal calls
            calls += 1
            return await pending if calls == 1 else "ready"

        app.platform.model_status.side_effect = status
        old_check = asyncio.create_task(app.check_model())
        await asyncio.sleep(0)
        await app.download_model()
        assert app.model_state == "ready"
        pending.set_result("not_downloaded")
        await old_check

    asyncio.run(run())
    assert app.model_state == "ready" and app.recognize_button.enabled
    assert app.store.mock_calls == []


def test_download_cancellation_unlocks_retry_and_preserves_learner_state(model_app):
    app = model_app

    async def run():
        pending = asyncio.get_running_loop().create_future()

        async def download():
            await pending

        app.platform.download_model.side_effect = download
        task = asyncio.create_task(app.download_model())
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    assert not app._model_downloading and app.model_state not in {"ready", "downloading"}
    assert not app.recognize_button.enabled
    assert any(control.enabled and "下载" in control.text for control in buttons(app.model_controls()))
    assert app.store.mock_calls == []


@pytest.mark.parametrize("free", [False, True])
def test_entering_ink_page_exposes_download_without_having_to_write_first(model_app, free):
    app = model_app
    if not free:
        app.question = Question("kana-3042", "kana_handwriting", "hiragana", "听音手写", "あ",
                                audio_text="あ", metadata={"romaji": "a"})
    app.show_ink_page(free=free, title="手写")
    assert "下载日语手写模型" in text_of(app.body)
    assert "重新检查模型" in text_of(app.body)
    assert not app.recognize_button.enabled
    assert not app.strokes and app.store.mock_calls == []


@pytest.mark.parametrize("destination", ["home", "settings", "practice_setup"])
def test_download_completion_updates_current_page_without_returning_to_old_ink(model_app, destination):
    app = model_app
    strokes = app.strokes

    async def run():
        pending = asyncio.get_running_loop().create_future()

        async def download():
            await pending

        app.platform.download_model.side_effect = download
        task = asyncio.create_task(app.download_model())
        await asyncio.sleep(0)
        app.page, app._page_token = destination, app._page_token + 1
        pending.set_result(None)
        await task

    asyncio.run(run())
    assert app.page == destination and app.model_state == "ready" and app.strokes is strokes
    if destination == "settings":
        assert "已下载" in text_of(app.model_panel)
        assert "下载日语手写模型" not in text_of(app.model_panel)
    elif destination == "practice_setup":
        app.refresh_practice_setup.assert_called()
    else:
        assert not hasattr(app, "home_services_label")
        app.refresh_home_actions.assert_not_called()
    app.show_home.assert_not_called()
    app.show_settings.assert_not_called()
    assert app.store.mock_calls == []


def test_voice_check_finishing_on_home_needs_no_removed_service_panel(model_app):
    app = model_app
    app.page = "home"
    assert not hasattr(app, "home_services_label")
    asyncio.run(KanaMemory.refresh_services(app))
    assert app.service_message == "日语声音已就绪"
    assert app.page == "home"
    app.platform.prepare_tts.assert_awaited_once()
    app.show_home.assert_not_called()
    app.refresh_home_actions.assert_not_called()
    assert app.store.mock_calls == []


def test_back_refreshes_retained_ink_controls_after_background_download(model_app):
    app = model_app
    strokes, canvas, model_panel = app.strokes, app.canvas, app.ink_model_panel
    app.refresh_model_ui()
    assert "下载日语手写模型" in text_of(model_panel)
    body = View(canvas, model_panel, app.recognize_button)
    saved = {name: getattr(app, name) for name in app_module.INK_BINDINGS}
    app.back_stack.pages.append(PageState(("show_free_ink", None), "free_ink", "试写", body, saved))
    app.page = "settings"
    app.canvas, app.strokes, app.ink_model_panel = View(), [], View()
    app.recognize_button = View(enabled=False)
    asyncio.run(app.download_model())
    app.go_back()
    assert app.page == "free_ink" and app.content_slot.children == [body]
    assert app.canvas is canvas and app.strokes is strokes and app.ink_model_panel is model_panel
    assert app.recognize_button.enabled and "已下载" in text_of(model_panel)
    assert "下载日语手写模型" not in text_of(model_panel)
    app._schedule_ink_draw.assert_called_once()


def test_model_recheck_failure_keeps_an_independent_retriable_message(model_app):
    app = model_app
    app.platform.model_status.side_effect = RuntimeError("手写下载服务暂不可用")
    asyncio.run(app.check_model())
    assert app.model_state == "error" and "手写下载服务暂不可用" in app.model_message
    assert app.service_message == "日语声音已就绪"
    assert not app._model_checking and not app.recognize_button.enabled
    assert app.store.mock_calls == []


def test_duplicate_checks_and_recheck_during_download_do_not_start_extra_requests(model_app):
    app = model_app

    async def run():
        pending = asyncio.get_running_loop().create_future()

        async def status():
            return await pending

        app.platform.model_status.side_effect = status
        first = asyncio.create_task(app.check_model())
        await asyncio.sleep(0)
        await app.check_model()
        app.platform.model_status.assert_awaited_once()
        pending.set_result("not_downloaded")
        await first
        app._model_downloading, app.model_state = True, "downloading"
        await app.check_model()
        app.platform.model_status.assert_awaited_once()
        assert app.model_state == "downloading"

    asyncio.run(run())


def test_cancelled_model_check_releases_the_check_guard(model_app):
    app = model_app

    async def run():
        pending = asyncio.get_running_loop().create_future()

        async def status():
            return await pending

        app.platform.model_status.side_effect = status
        task = asyncio.create_task(app.check_model())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert not app._model_checking and app.model_state == "error"
    assert any(control.enabled and "下载" in control.text for control in buttons(app.model_controls()))


def test_back_refreshes_retained_settings_download_controls(model_app):
    app = model_app
    app.page = "settings"
    app.refresh_model_ui()
    model_panel, message = app.model_panel, app.settings_message
    app.pitch_contour_switch, app.pitch_number_switch = View(), View()
    body = View(model_panel, message, app.pitch_contour_switch, app.pitch_number_switch)
    saved = {name: getattr(app, name) for name in app_module.PAGE_BINDINGS["settings"]}
    app.back_stack.pages.append(PageState(("show_settings", None), "settings", "设置", body, saved))
    # A focused writing flow now exits to Home after confirmation. This test
    # covers restoring retained settings from an ordinary content detail page.
    app.page = "kana_detail"
    app._view_key = ("show_kana_detail", "kana-3042")
    app.model_panel, app.settings_message = View(), View()
    asyncio.run(app.download_model())
    app.go_back()
    assert app.model_panel is model_panel and app.settings_message is message
    assert "已下载" in text_of(model_panel)
    assert [control.text for control in buttons(model_panel)] == ["重新检查模型"]
    assert message.text == "日语声音已就绪"


def test_new_check_clears_old_error_only_after_running_its_own_status_request(model_app):
    app = model_app
    app.model_state, app.model_message = "error", "旧下载错误"
    asyncio.run(app.check_model())
    assert app.model_state == "ready" and app.model_message == ""
    assert not app._model_checking and app.recognize_button.enabled


def test_recognition_after_preparation_uses_only_actual_strokes_and_model_candidates(model_app):
    app = model_app
    asyncio.run(app.download_model())
    app.platform.recognize.return_value = ["い", "り", "い"]
    strokes = [list(stroke) for stroke in app.strokes]
    asyncio.run(app.recognize_ink())
    app.platform.recognize.assert_awaited_once_with(strokes, 360, 210)
    assert [control.text for control in buttons(app.ink_result_box)] == ["い", "り"]
    app.check_answer.assert_not_called()
    assert app.store.mock_calls == []


def test_recognition_failure_does_not_become_a_review_or_invented_answer(model_app):
    app = model_app
    app.model_state = "ready"
    app.platform.recognize.side_effect = RuntimeError("模型识别服务暂不可用")
    asyncio.run(app.recognize_ink())
    assert "模型识别服务暂不可用" in app.ink_status_label.text
    assert buttons(app.ink_result_box) == []
    assert not app._ink_busy and app.recognize_button.enabled
    app.check_answer.assert_not_called()
    assert app.store.mock_calls == []


def test_missing_model_direct_recognize_never_calls_engine_or_grades(model_app):
    app = model_app
    asyncio.run(app.recognize_ink())
    app.platform.recognize.assert_not_awaited()
    app.check_answer.assert_not_called()
    assert app.store.mock_calls == []
    assert not app.recognize_button.enabled
