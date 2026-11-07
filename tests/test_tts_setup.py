"""Offline Japanese TTS setup and installer routing, without a JVM or downloads."""
import asyncio
from dataclasses import dataclass
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
from kanamemory.app import KanaMemory
from kanamemory.platform_android import AndroidPlatform, PlatformError


from support.tts_setup_support import INSTALL, Voice, Voices, Intent, tts_environment
SETTINGS = "com.android.settings.TTS_SETTINGS"
ACCESSIBILITY = "android.settings.ACCESSIBILITY_SETTINGS"


def test_installed_offline_japanese_voice_is_selected_and_engine_is_recorded(tts_environment):
    platform, host = tts_environment
    good = Voice("ja-high-quality", quality=400)
    host.voices = [Voice("en-local", language="en", quality=900), Voice("ja-network", network=True, quality=1000),
                   Voice("ja-missing", features={"notInstalled"}, quality=1000), Voice("ja-low", quality=100), good]
    assert asyncio.run(platform.prepare_tts()) == "ready"
    assert platform.tts_ready and platform.tts_voice == good.name
    assert platform.tts_engine == host.engine
    assert host.instances[-1].set_voice_calls == [good]
    assert host.instances[-1].language_queries == [host.japan]
    platform._configure_audio_callbacks.assert_called_once()


@pytest.mark.parametrize("voices", [
    [Voice("ja-network", network=True)], [Voice("ja-missing", features={"notInstalled"})],
    [Voice("en-local", language="en"), Voice("zh-local", language="zh")], [],
])
def test_network_uninstalled_or_non_japanese_voices_never_enable_speech(tts_environment, voices):
    platform, host = tts_environment
    host.voices = voices
    assert asyncio.run(platform.prepare_tts()) == "not_installed"
    assert not platform.tts_ready and platform.tts_voice is None
    assert platform.tts_engine == host.engine  # Needed to route the installer.
    assert host.instances[-1].set_voice_calls == []
    platform._configure_audio_callbacks.assert_not_called()


def test_unreadable_voice_list_is_an_error_not_a_missing_download(tts_environment):
    platform, host = tts_environment
    host.voices = None
    assert asyncio.run(platform.prepare_tts()) == "error"
    assert "无法读取系统语音列表" in platform.tts_detail
    assert not platform.tts_ready and platform.tts_voice is None
    assert host.instances[-1].set_voice_calls == []
    platform._configure_audio_callbacks.assert_not_called()


@pytest.mark.parametrize("availability, expected", [(-1, "not_installed"), (-2, "unavailable")])
def test_missing_or_unsupported_japanese_language_does_not_fall_back(tts_environment, availability, expected):
    platform, host = tts_environment
    host.availability, host.voices = availability, [Voice("en-local", language="en")]
    assert asyncio.run(platform.prepare_tts()) == expected
    assert host.instances[-1].language_queries == [host.japan]
    assert host.instances[-1].set_voice_calls == []
    assert not platform.tts_ready and platform.tts_voice is None


def test_recheck_discards_stale_voice_and_engine_before_initializing(tts_environment):
    platform, host = tts_environment

    async def run():
        host.voices = [Voice("ja-before")]
        assert await platform.prepare_tts() == "ready"
        old = host.instances[-1]
        host.voices, host.engine = [], "example.replacement.tts"
        assert await platform.prepare_tts() == "not_installed"
        assert old.shutdown_count == 1
        assert platform.tts_voice is None and platform.tts_engine == host.engine
        assert not platform.tts_ready

    asyncio.run(run())
    assert host.initialization_snapshots == [(None, None), (None, None)]


def test_failed_initialization_does_not_retain_previous_engine_or_voice(tts_environment):
    platform, host = tts_environment
    platform.tts_status, platform.tts_voice, platform.tts_engine = "ready", "stale-ja", "stale.engine"
    host.init_status = host.TTS.ERROR
    assert asyncio.run(platform.prepare_tts()) == "unavailable"
    assert platform.tts_voice is None and platform.tts_engine is None and not platform.tts_ready
    assert host.instances[-1].set_voice_calls == []


def test_engine_returning_another_language_is_not_marked_ready(tts_environment):
    platform, host = tts_environment
    japanese = Voice("ja-local")
    host.voices, host.selected_override = [japanese], Voice("en-returned", language="en")
    assert asyncio.run(platform.prepare_tts()) == "error"
    assert host.instances[-1].set_voice_calls == [japanese]
    assert not platform.tts_ready and platform.tts_voice is None
    platform._configure_audio_callbacks.assert_not_called()


@pytest.mark.parametrize("engine", ["example.current.tts", None])
def test_installation_routes_to_current_engine_without_claiming_install_success(tts_environment, engine):
    platform, host = tts_environment
    platform.tts_status, platform.tts_engine = "not_installed", engine
    platform.open_tts_installation()
    assert len(host.intents) == 1
    intent = host.intents[0]
    assert intent.action == INSTALL and intent.package == engine
    assert intent.flags & Intent.FLAG_ACTIVITY_NEW_TASK
    # Returning/cancelling from the engine UI must not make the app ready.
    assert platform.tts_status == "not_installed" and not platform.tts_ready
    assert platform.tts_voice is None and not host.instances


@pytest.mark.parametrize("current_engine, expected_package", [
    ("example.newly-selected.tts", "example.newly-selected.tts"),
    (None, None),
    (RuntimeError("Default engine query failed"), "example.current.tts"),
])
def test_installation_rechecks_default_engine_and_only_uses_cache_on_query_error(tts_environment, current_engine, expected_package):
    platform, host = tts_environment
    assert asyncio.run(platform.prepare_tts()) == "not_installed"
    assert platform.tts_engine == "example.current.tts"
    host.engine = current_engine
    platform.open_tts_installation()
    assert len(host.intents) == 1 and host.intents[0].action == INSTALL
    assert host.intents[0].package == expected_package
    assert len(host.instances) == 1  # Opening the installer is not a readiness recheck.
    assert not platform.tts_ready and platform.tts_voice is None


@pytest.mark.parametrize("fail_actions, expected", [
    ({INSTALL}, [INSTALL, SETTINGS]),
    ({INSTALL, SETTINGS}, [INSTALL, SETTINGS, ACCESSIBILITY]),
])
def test_missing_installer_falls_back_to_settings(tts_environment, fail_actions, expected):
    platform, host = tts_environment
    platform.tts_status, platform.tts_engine = "not_installed", host.engine
    host.fail_actions = fail_actions
    platform.open_tts_installation()
    assert [intent.action for intent in host.intents] == expected
    assert host.intents[0].package == host.engine
    assert all(intent.package is None for intent in host.intents[1:])
    assert not platform.tts_ready


def test_installation_and_settings_failures_are_reported_without_readiness(tts_environment):
    platform, host = tts_environment
    platform.tts_status = "not_installed"
    host.fail_actions = {INSTALL, SETTINGS, ACCESSIBILITY}
    with pytest.raises(PlatformError):
        platform.open_tts_installation()
    assert not platform.tts_ready and platform.tts_voice is None


def test_refresh_services_checks_tts_without_rechecking_handwriting():
    platform = SimpleNamespace(
        model_status=AsyncMock(side_effect=PlatformError("手写服务不可用")),
        prepare_tts=AsyncMock(return_value="ready"), tts_detail="系统合成音 · 日语 · ja-installed",
    )
    page = SimpleNamespace(platform=platform, page="settings", model_state="error", service_message="",
                           settings_message=SimpleNamespace(text=""), show_settings=Mock(),
                           _model_downloading=False, _model_checking=False, _model_request=0,
                           model_message="手写服务不可用", refresh_model_ui=Mock())
    page.check_model = MethodType(KanaMemory.check_model, page)
    asyncio.run(KanaMemory.refresh_services(page))
    platform.model_status.assert_not_awaited()
    platform.prepare_tts.assert_awaited_once()
    assert platform.tts_detail in page.service_message
    assert page.model_state == "error"
    assert page.model_message == "手写服务不可用"
    assert "手写服务不可用" not in page.service_message
    page.show_settings.assert_called_once()


def test_background_initialization_refreshes_an_already_open_settings_page():
    platform = SimpleNamespace(initialize=AsyncMock(), model_status=AsyncMock(return_value="ready"),
                               prepare_tts=AsyncMock(return_value="ready"),
                               tts_detail="系统合成音 · 日语 · newly-installed-ja")
    page = SimpleNamespace(platform=platform, page="settings", model_state="not_downloaded",
                           service_message="未找到已安装的离线日语语音", show_settings=Mock(), show_home=Mock(),
                           _model_downloading=False, _model_checking=False, _model_request=0,
                           model_message="", refresh_model_ui=Mock())
    page.check_model = MethodType(KanaMemory.check_model, page)
    page.refresh_services = MethodType(KanaMemory.refresh_services, page)
    asyncio.run(KanaMemory.on_running(page))
    platform.prepare_tts.assert_awaited_once()
    assert page.model_state == "ready" and page.service_message == platform.tts_detail
    page.show_settings.assert_called_once()
    page.show_home.assert_not_called()


@pytest.mark.parametrize("method", ["on_running", "refresh_services"])
def test_sound_refresh_does_not_overwrite_independent_model_failure(method):
    platform = SimpleNamespace(model_status=AsyncMock(side_effect=PlatformError("日语模型下载连接失败")),
                               prepare_tts=AsyncMock(return_value="ready"), tts_detail="系统日语声音已就绪")
    page = SimpleNamespace(platform=platform, page="free_ink", model_state="error",
                           model_message="日语模型下载连接失败", service_message="", _model_request=0,
                           _model_downloading=False, _model_checking=False, refresh_model_ui=Mock(),
                           strokes=[[(1, 2, 3)]], store=Mock())
    page.check_model = MethodType(KanaMemory.check_model, page)
    page.refresh_services = MethodType(KanaMemory.refresh_services, page)
    strokes = page.strokes
    asyncio.run(getattr(KanaMemory, method)(page))
    assert page.model_state == "error" and page.model_message == "日语模型下载连接失败"
    assert page.service_message == "系统日语声音已就绪"
    assert page.strokes is strokes and page.store.mock_calls == []
    platform.prepare_tts.assert_awaited_once()
    page.refresh_model_ui.assert_called()


def test_stalled_model_check_does_not_delay_voice_or_listening_setup():
    async def run():
        release_model, model_started, voice_ready = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def model_status():
            model_started.set()
            await release_model.wait()
            return "ready"
        async def prepare_tts():
            platform.tts_ready = True
            voice_ready.set()
            return "ready"
        platform = SimpleNamespace(model_status=AsyncMock(side_effect=model_status),
                                   prepare_tts=AsyncMock(side_effect=prepare_tts),
                                   tts_detail="离线日语声音已就绪", tts_ready=False)
        page = SimpleNamespace(platform=platform, page="practice_setup", model_state="not_downloaded",
                               model_message="", service_message="", _model_request=0,
                               _model_downloading=False, _model_checking=False,
                               refresh_model_ui=Mock(), refresh_practice_setup=Mock())
        page.check_model = MethodType(KanaMemory.check_model, page)
        page.refresh_services = MethodType(KanaMemory.refresh_services, page)
        startup = asyncio.create_task(KanaMemory.on_running(page))
        try:
            await asyncio.wait_for(model_started.wait(), timeout=1)
            await asyncio.wait_for(voice_ready.wait(), timeout=1)
            assert not startup.done() and page.model_state == "checking"
            assert platform.tts_ready and page.service_message == platform.tts_detail
            page.refresh_practice_setup.assert_called_once()
            platform.prepare_tts.assert_awaited_once()
        finally:
            release_model.set()
            await asyncio.wait_for(startup, timeout=1)
        assert page.model_state == "ready"
    asyncio.run(run())
