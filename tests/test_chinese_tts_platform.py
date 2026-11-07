"""Strict Chinese offline voice selection and real completion/focus callback paths."""
import asyncio
from types import SimpleNamespace

import pytest

from support.tts_setup_support import Voice
from support.tts_setup_support import tts_environment
from kanamemory.platform_android import AndroidPlatform, PlatformError
from kanamemory.platform_desktop import DesktopPlatform, PlatformError as DesktopError
from kanamemory.voice_policy import describe_voice


@pytest.mark.parametrize("requested, voice, expected", [
    ("cmn", Voice("mandarin", language="cmn", country="CN"), True),
    ("cmn", Voice("mandarin", language="zh", country="CN"), True),
    ("cmn", Voice("mandarin", language="zh", country="TW"), False),
    ("cmn", Voice("mandarin", language="zh", country="HK"), False),
    ("cmn", Voice("mandarin", language="zh", country=""), False),
    ("cmn", Voice("mandarin", language="cmn", country="CN", network=True), False),
    ("cmn", Voice("mandarin", language="cmn", country="CN", features={"notInstalled"}), False),
    ("yue", Voice("cantonese", language="yue", country="HK"), True),
    ("yue", Voice("yue-hk-local", language="zh", country="HK"), False),
    ("yue", Voice("cantonese", language="yue", country="CN"), False),
    ("yue", Voice("cantonese", language="yue", country=""), False),
    ("yue", Voice("cantonese", language="yue", country="HK", network=True), False),
    ("yue", Voice("cantonese", language="yue", country="HK", features={"notInstalled"}), False),
    ("ja", Voice("ja"), True),
    ("ja", Voice("Japanese-name", language="cmn", country="CN"), False),
])
def test_locale_installation_and_network_metadata_define_supported_voice(requested, voice, expected):
    assert describe_voice(voice).supports(requested) is expected


class Builder:
    def __init__(self, *args):
        self.listener = None

    def setUsage(self, value): return self
    def setContentType(self, value): return self
    def setAudioAttributes(self, value): return self
    def setAcceptsDelayedFocusGain(self, value): return self
    def setOnAudioFocusChangeListener(self, value):
        self.listener = value
        return self
    def build(self): return self


@pytest.fixture
def speech_environment(tts_environment):
    platform, host = tts_environment
    host.focus_requests, host.abandoned, host.queued, host.logs = [], [], [], []
    host.focus_result, host.queue_result, host.native_stops = 1, 0, 0
    host.voices = [Voice("ja-local"), Voice("cmn-local", language="cmn", country="CN"),
                   Voice("yue-local", language="yue", country="HK")]

    def request_focus(request):
        host.focus_requests.append(request)
        return host.focus_result

    manager = SimpleNamespace(requestAudioFocus=request_focus, abandonAudioFocusRequest=host.abandoned.append)
    platform._context.getSystemService = lambda name: manager
    classes = {
        "org.example.kanamemory.KanaTtsListener$Events": object,
        "org.example.kanamemory.KanaTtsListener": lambda events: SimpleNamespace(events=events),
        "android.media.AudioManager$OnAudioFocusChangeListener": object,
        "android.media.AudioAttributes$Builder": Builder,
        "android.media.AudioFocusRequest$Builder": Builder,
        "android.os.Bundle": dict,
        "android.util.Log": SimpleNamespace(i=lambda *args: host.logs.append(args)),
    }
    original_class = platform._jclass
    platform._jclass = lambda name: classes[name] if name in classes else original_class(name)
    platform._configure_audio_callbacks = AndroidPlatform._configure_audio_callbacks.__get__(platform)
    platform.stop = AndroidPlatform.stop.__get__(platform)
    host.TTS.QUEUE_FLUSH = 0
    host.TTS.getMaxSpeechInputLength = staticmethod(lambda: 4000)
    host.TTS.setOnUtteranceProgressListener = lambda self, listener: 0
    host.TTS.setAudioAttributes = lambda self, attributes: None

    def stop(self):
        host.native_stops += 1

    def speak(self, text, mode, bundle, utterance):
        host.queued.append((text, self.getVoice(), utterance))
        return host.queue_result

    host.TTS.stop, host.TTS.speak = stop, speak
    return platform, host


async def queued_task(platform, host, text, language):
    count = len(host.queued)
    task = asyncio.create_task(platform.play(text) if language == "ja" else platform.play_chinese(text, language))
    for _ in range(10):
        await asyncio.sleep(0)
        if len(host.queued) > count or task.done():
            break
    if task.done():
        await task
    assert len(host.queued) == count + 1
    return task, host.queued[-1][2]


def test_chinese_is_ready_when_japanese_language_is_not_installed(speech_environment):
    platform, host = speech_environment
    host.availability = -1
    host.voices = host.voices[1:]

    async def run():
        assert await platform.prepare_tts() == "not_installed"
        assert not platform.tts_ready
        for language in ("cmn", "yue"):
            state = platform.chinese_voice_status(language)
            assert state["status"] == "ready" and state["engine"] == host.engine
            assert "自动选音" in state["detail"]
            assert state["voice"] not in state["detail"]
            task, utterance = await queued_task(platform, host, "中国", language)
            assert not task.done()
            platform._progress_events.onDone(utterance)
            await asyncio.wait_for(task, 1)
        assert platform.tts_status == "not_installed"
        assert len(platform.tts_voice_inventory) == 2
    asyncio.run(run())


def test_japanese_availability_exception_does_not_disable_chinese(speech_environment):
    platform, host = speech_environment

    def fail_japanese_query(self, locale):
        raise RuntimeError("Japanese locale query failed")

    host.TTS.isLanguageAvailable = fail_japanese_query

    async def run():
        assert await platform.prepare_tts() == "error"
        task, utterance = await queued_task(platform, host, "中", "cmn")
        platform._progress_events.onDone(utterance)
        await asyncio.wait_for(task, 1)
        assert platform.chinese_voice_status()["status"] == "ready"
    asyncio.run(run())


def test_native_stop_and_focus_cleanup_errors_still_resolve_waiter(speech_environment):
    platform, host = speech_environment

    def native_failure(*args):
        raise RuntimeError("Native cleanup failed")

    async def run():
        await platform.prepare_tts()
        task, _ = await queued_task(platform, host, "中", "cmn")
        platform._tts.stop = native_failure
        platform._audio_manager.abandonAudioFocusRequest = native_failure
        platform.stop()
        with pytest.raises(PlatformError, match="停止"):
            await asyncio.wait_for(task, 1)
        assert not platform._utterances and platform._focus_request is None
    asyncio.run(run())


def test_japanese_chinese_japanese_reselect_exact_voices_and_wait_for_real_done(speech_environment):
    platform, host = speech_environment

    async def run():
        assert await platform.prepare_tts() == "ready"
        for language, text in (("ja", "かな"), ("cmn", "中国"), ("yue", "香港"), ("ja", "かな")):
            task, utterance = await queued_task(platform, host, text, language)
            assert describe_voice(host.queued[-1][1]).supports(language)
            platform._progress_events.onStart(utterance)
            await asyncio.sleep(0)
            assert not task.done() and platform.last_audio_status == "playing"
            platform._progress_events.onDone(utterance)
            await asyncio.wait_for(task, 1)
            assert platform.last_audio_status == "completed"
        assert [voice.name for voice in platform._tts.set_voice_calls] == [
            "ja-local", "ja-local", "cmn-local", "yue-local", "ja-local"]
        assert len(host.abandoned) == 4
        selected_logs = [message for tag, message in host.logs if message.startswith("voice_selected ")]
        assert len(selected_logs) == 5
        assert "language=yue voice=yue-local locale=yue-HK network_required=false not_installed=false" in selected_logs[3]
        assert all("かな" not in message and "中国" not in message and "香港" not in message for message in selected_logs)
    asyncio.run(run())


@pytest.mark.parametrize("replacement", [
    Voice("cmn-local", language="zh", country="HK"),
    Voice("cmn-local", language="cmn", country="CN", network=True),
    Voice("cmn-local", language="cmn", country="CN", features={"notInstalled"}),
    Voice("fallback-other-name", language="cmn", country="CN"),
])
def test_engine_silent_fallback_is_rejected_before_any_audio_is_queued(speech_environment, replacement):
    platform, host = speech_environment

    async def run():
        await platform.prepare_tts()
        host.selected_override = replacement
        with pytest.raises(PlatformError, match="没有保持指定"):
            await platform.play_chinese("中")
        assert not host.queued and not host.focus_requests
        assert platform.chinese_voice_status()["status"] == "error"
        assert platform.tts_ready
    asyncio.run(run())


def test_removed_voice_is_rechecked_and_never_replaced_by_zh_hk(speech_environment):
    platform, host = speech_environment

    async def run():
        await platform.prepare_tts()
        host.voices = [Voice("yue-local", language="zh", country="HK")]
        with pytest.raises(PlatformError, match="未找到已安装的离线香港粤语"):
            await platform.play_chinese("香港", "yue")
        assert not host.queued
        assert platform.chinese_voice_status("yue")["status"] == "not_installed"
    asyncio.run(run())


def test_not_installed_error_updates_only_the_utterance_language(speech_environment):
    platform, host = speech_environment

    async def run():
        await platform.prepare_tts()
        task, utterance = await queued_task(platform, host, "香港", "yue")
        platform._progress_events.onError(utterance, -9)
        with pytest.raises(PlatformError, match="香港粤语"):
            await asyncio.wait_for(task, 1)
        assert platform.chinese_voice_status("yue")["status"] == "not_installed"
        assert platform.chinese_voice_status("cmn")["status"] == "ready" and platform.tts_ready
    asyncio.run(run())


def test_stale_completion_and_focus_loss_cannot_finish_or_stop_new_language(speech_environment):
    platform, host = speech_environment

    async def run():
        await platform.prepare_tts()
        first, first_id = await queued_task(platform, host, "香港", "yue")
        old_focus = host.focus_requests[-1].listener
        current, current_id = await queued_task(platform, host, "かな", "ja")
        with pytest.raises(PlatformError, match="停止"):
            await first
        stop_count = host.native_stops
        old_focus.onAudioFocusChange(-1)
        platform._progress_events.onError(first_id, -9)
        platform._progress_events.onDone(first_id)
        await asyncio.sleep(0)
        assert not current.done() and platform._current_utterance == current_id
        assert host.native_stops == stop_count
        assert platform.chinese_voice_status("yue")["status"] == "ready"
        host.focus_requests[-1].listener.onAudioFocusChange(-2)
        with pytest.raises(PlatformError, match="停止"):
            await asyncio.wait_for(current, 1)
        assert not platform._utterances and not platform._utterance_languages
    asyncio.run(run())


@pytest.mark.parametrize("old_language,missing_language", [("ja", "yue"), ("cmn", "ja"), ("yue", "cmn")])
def test_requesting_missing_language_stops_previous_sound_once(speech_environment, old_language, missing_language):
    platform, host = speech_environment
    host.voices = [voice for voice in host.voices if not describe_voice(voice).supports(missing_language)]
    if missing_language == "ja":
        host.availability = -1

    async def run():
        await platform.prepare_tts()
        previous, previous_id = await queued_task(platform, host, "安", old_language)
        before = host.native_stops
        with pytest.raises(PlatformError):
            if missing_language == "ja":
                await platform.play("あ")
            else:
                await platform.play_chinese("安", missing_language)
        assert host.native_stops == before + 1
        with pytest.raises(PlatformError, match="停止"):
            await asyncio.wait_for(previous, 1)
        assert len(host.queued) == 1 and not platform._utterances
        assert platform._current_utterance is None and platform.last_audio_status == "stopped"
        platform._progress_events.onDone(previous_id)
        await asyncio.sleep(0)
        assert platform.last_audio_status == "stopped"
    asyncio.run(run())


def test_invalid_language_does_not_interrupt_current_playback(speech_environment):
    platform, host = speech_environment

    async def run():
        await platform.prepare_tts()
        task, utterance = await queued_task(platform, host, "安", "cmn")
        before = host.native_stops
        with pytest.raises(ValueError):
            await platform.play_chinese("安", "zh-HK")
        assert host.native_stops == before and not task.done()
        platform._progress_events.onDone(utterance)
        await asyncio.wait_for(task, 1)
    asyncio.run(run())


@pytest.mark.parametrize("action", ["cancel", "close", "stop"])
def test_cancel_close_and_navigation_stop_resolve_pending_chinese_playback(speech_environment, action):
    platform, host = speech_environment

    async def run():
        await platform.prepare_tts()
        task, utterance = await queued_task(platform, host, "中", "cmn")
        if action == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            getattr(platform, action)()
            with pytest.raises(PlatformError, match="停止"):
                await task
        assert not platform._utterances and platform._current_utterance is None
        platform._progress_events.onDone(utterance)
        await asyncio.sleep(0)
        assert platform.last_audio_status == "stopped"
        if action == "close":
            assert platform.chinese_voice_status()["status"] == "unavailable"
            with pytest.raises(PlatformError, match="关闭"):
                await platform.play_chinese("中")
    asyncio.run(run())


def test_voice_refresh_blocks_speech_and_resets_missing_chinese_cache(speech_environment):
    platform, host = speech_environment

    async def run():
        await platform.prepare_tts()
        platform._tts_preparing = True
        with pytest.raises(PlatformError, match="正在重新检查"):
            await platform.play_chinese("中")
        platform._tts_preparing = False
        host.voices = [Voice("ja-local")]
        states = await platform.prepare_chinese_tts()
        assert all(state["status"] == "not_installed" for state in states.values())
        assert all(state["voice"] is None for state in states.values())
        assert not host.queued and platform.tts_ready
    asyncio.run(run())


def test_refresh_waiter_cannot_recreate_engine_after_close(speech_environment):
    platform, host = speech_environment

    async def run():
        await platform._tts_lock.acquire()
        waiting = asyncio.create_task(platform.prepare_chinese_tts())
        await asyncio.sleep(0)
        platform.close()
        platform._tts_lock.release()
        with pytest.raises(PlatformError, match="关闭"):
            await waiting
        assert not host.instances
    asyncio.run(run())


def test_focus_denied_and_queue_failure_never_report_completion(speech_environment):
    platform, host = speech_environment

    async def run():
        await platform.prepare_tts()
        host.focus_result = 0
        with pytest.raises(PlatformError, match="音频播放权限"):
            await platform.play_chinese("中")
        assert not host.queued and not platform._utterances
        host.focus_result, host.queue_result = 1, -1
        with pytest.raises(PlatformError, match="播放失败"):
            await platform.play_chinese("中")
        assert platform.last_audio_status == "error" and not platform._utterances
    asyncio.run(run())


def test_desktop_api_does_not_fake_chinese_speech():
    platform = DesktopPlatform()

    async def run():
        states = await platform.prepare_chinese_tts()
        assert all(state["status"] == "unavailable" for state in states.values())
        with pytest.raises(DesktopError, match="香港粤语"):
            await platform.play_chinese("香港", "yue")
        with pytest.raises(ValueError):
            platform.chinese_voice_status("zh-HK")
        platform.close()
        assert "关闭" in platform.chinese_voice_status()["detail"]
    asyncio.run(run())
