"""Exercise the native recording protocol without assuming prepare == success."""
import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
from kanamemory.platform_android import AndroidPlatform, PlatformError


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


class AudioManager:
    def __init__(self):
        self.granted = True
        self.requested = []
        self.abandoned = []

    def requestAudioFocus(self, request):
        self.requested.append(request)
        return 1 if self.granted else 0

    def abandonAudioFocusRequest(self, request):
        self.abandoned.append(request)


class Player:
    def __init__(self):
        self.prepared = self.completed = self.failed = None
        self.released = self.started = 0
        self.preparing = False
        self.start_error = self.source_error = self.clear_error = False

    def setAudioAttributes(self, attributes): self.attributes = attributes
    def setOnPreparedListener(self, listener):
        if listener is None and self.clear_error:
            raise RuntimeError("native listener detach failed")
        self.prepared = listener
    def setOnCompletionListener(self, listener): self.completed = listener
    def setOnErrorListener(self, listener): self.failed = listener
    def setDataSource(self, path):
        if self.source_error: raise OSError("decoder source unavailable")
        self.path = path
    def prepareAsync(self): self.preparing = True
    def start(self):
        if self.start_error: raise RuntimeError("decode failed")
        self.started += 1
    def release(self): self.released += 1


@pytest.fixture
def native(tmp_path):
    manager = AudioManager()
    players = []
    messages = []
    def make_player():
        player = Player()
        players.append(player)
        return player
    classes = {
        "android.media.MediaPlayer": make_player,
        "android.media.AudioAttributes$Builder": Builder,
        "android.media.AudioFocusRequest$Builder": Builder,
        "android.util.Log": SimpleNamespace(i=lambda tag, text: messages.append((tag, text))),
    }
    platform = AndroidPlatform.__new__(AndroidPlatform)
    platform._jclass = lambda name: object if name.endswith("Listener") else classes[name]
    platform._dynamic_proxy = lambda interface: object
    platform._context = SimpleNamespace(getSystemService=lambda name: manager)
    platform._closed = False
    platform._recording = None
    platform._tts = None
    platform._utterances = {}
    platform._index_preview = None
    platform._recognizer = None
    platform._listeners = {}
    platform.last_audio_status = "idle"
    platform.remove_back_handler = lambda: None
    path = tmp_path / "recording.ogg"
    path.write_bytes(b"OggS")
    return SimpleNamespace(platform=platform, manager=manager, players=players, messages=messages, path=path)


async def begin(native):
    task = asyncio.create_task(native.platform.play_recording(native.path))
    await asyncio.sleep(0)
    assert native.players[-1].preparing
    return task, native.players[-1]


def test_completes_only_after_real_completion_and_releases(native):
    async def scenario():
        task, player = await begin(native)
        assert not task.done()
        player.prepared.onPrepared(player)
        await asyncio.sleep(0)
        assert player.started == 1 and not task.done()
        player.completed.onCompletion(player)
        await task
        assert player.released == 1
        assert native.platform._recording is None
        assert len(native.manager.abandoned) == 1
        assert native.platform.last_audio_status == "completed"
    asyncio.run(scenario())


def test_optional_native_log_failure_does_not_turn_started_audio_into_failure(native, monkeypatch):
    original = native.platform._jclass
    def jclass(name):
        if name == "android.util.Log":
            def fail(*args):
                raise RuntimeError("private native log message")
            return SimpleNamespace(i=fail)
        return original(name)
    monkeypatch.setattr(native.platform, "_jclass", jclass)
    async def scenario():
        task, player = await begin(native)
        player.prepared.onPrepared(player)
        await asyncio.sleep(0)
        assert player.started == 1 and not task.done()
        assert native.platform.last_audio_status == "playing"
        player.completed.onCompletion(player)
        await task
        assert native.platform.last_audio_status == "completed"
    asyncio.run(scenario())


@pytest.mark.parametrize("event", ["decoder", "start"])
def test_decoder_and_start_failure_release_and_report_platform_error(native, event):
    async def scenario():
        task, player = await begin(native)
        if event == "decoder":
            assert player.failed.onError(player, 1, -1010) is True
        else:
            player.start_error = True
            player.prepared.onPrepared(player)
        with pytest.raises(PlatformError, match="录音"):
            await task
        assert player.released == 1 and len(native.manager.abandoned) == 1
        assert native.platform.last_audio_status == "error"
    asyncio.run(scenario())


def test_stop_while_preparing_blocks_late_prepared_event(native):
    async def scenario():
        task, player = await begin(native)
        late = player.prepared
        native.platform.stop()
        late.onPrepared(player)
        with pytest.raises(PlatformError, match="停止"):
            await task
        assert player.started == 0 and player.released == 1
        assert native.platform.last_audio_status == "stopped"
        native.platform.stop()
        assert player.released == 1
    asyncio.run(scenario())


def test_focus_denial_never_prepares_and_releases_player(native):
    async def scenario():
        native.manager.granted = False
        with pytest.raises(PlatformError, match="权限"):
            await native.platform.play_recording(native.path)
        player = native.players[-1]
        assert not player.preparing and player.started == 0 and player.released == 1
        assert native.platform._recording is None
    asyncio.run(scenario())


def test_focus_loss_before_prepared_cannot_start_audio(native):
    async def scenario():
        task, player = await begin(native)
        native.manager.requested[0].listener.onAudioFocusChange(-1)
        player.prepared.onPrepared(player)
        with pytest.raises(PlatformError, match="打断"):
            await task
        assert player.started == 0 and player.released == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["close", "cancel"])
def test_lifecycle_close_and_cancel_clean_native_resources(native, action):
    async def scenario():
        task, player = await begin(native)
        if action == "close":
            native.platform.close()
            error = PlatformError
        else:
            task.cancel()
            error = asyncio.CancelledError
        with pytest.raises(error): await task
        assert player.released == 1 and len(native.manager.abandoned) == 1
        assert native.platform._recording is None
    asyncio.run(scenario())


def test_new_recording_stops_previous_and_stale_callbacks_cannot_finish_it(native):
    async def scenario():
        first, player1 = await begin(native)
        stale = player1.completed
        second, player2 = await begin(native)
        with pytest.raises(PlatformError, match="停止"): await first
        assert native.platform.last_audio_status == "queued"
        stale.onCompletion(player1)
        await asyncio.sleep(0)
        assert not second.done()
        player2.completed.onCompletion(player2)
        await second
        assert player1.released == player2.released == 1
    asyncio.run(scenario())


def test_cleanup_still_releases_and_abandons_focus_if_listener_detach_throws(native):
    async def scenario():
        task, player = await begin(native)
        player.clear_error = True
        native.platform.stop()
        with pytest.raises(PlatformError): await task
        assert player.released == 1 and len(native.manager.abandoned) == 1
    asyncio.run(scenario())


def test_missing_file_and_closed_service_do_not_allocate_native_player(native):
    async def scenario():
        with pytest.raises(PlatformError, match="安装包"):
            await native.platform.play_recording(native.path.parent / "missing.ogg")
        native.platform._closed = True
        with pytest.raises(PlatformError, match="关闭"):
            await native.platform.play_recording(native.path)
        assert not native.players
    asyncio.run(scenario())


def test_prepare_failure_is_actionable_and_still_releases(native, monkeypatch):
    def fail_source(self, path): raise OSError("native data-source failure")
    monkeypatch.setattr(Player, "setDataSource", fail_source)
    async def scenario():
        with pytest.raises(PlatformError, match="无法读取或播放"):
            await native.platform.play_recording(native.path)
        assert native.players[0].released == 1
        assert len(native.manager.abandoned) == 1
        assert native.platform.last_audio_status == "error"
    asyncio.run(scenario())


def test_timeout_reports_error_and_releases(native, monkeypatch):
    async def time_out(awaitable, timeout): raise TimeoutError()
    monkeypatch.setattr(asyncio, "wait_for", time_out)
    async def scenario():
        with pytest.raises(PlatformError, match="按时"):
            await native.platform.play_recording(native.path)
        assert native.players[0].released == 1
        assert len(native.manager.abandoned) == 1
        assert native.platform.last_audio_status == "error"
    asyncio.run(scenario())
