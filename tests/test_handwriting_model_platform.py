"""Model preparation uses real native readiness, including retries and late Tasks."""

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
from kanamemory.platform_android import AndroidPlatform, PlatformError


class NativeTask:
    def __init__(self, result=None, *, pending=False, error=None, cancelled=False):
        self.result = result
        self.pending = pending
        self.error = error
        self.cancelled = cancelled
        self.listeners = []

    def addOnCompleteListener(self, listener):
        self.listeners.append(listener)
        if not self.pending:
            listener.onComplete(self)

    def isCanceled(self):
        return self.cancelled

    def isSuccessful(self):
        return not self.error and not self.cancelled and not self.pending

    def getResult(self):
        return self.result

    def getException(self):
        return self.error

    def complete(self):
        self.pending = False
        for listener in self.listeners:
            listener.onComplete(self)


class NativeError:
    def __init__(self, code, message="native diagnostic"):
        self.code = code
        self.message = message

    def getErrorCode(self):
        return self.code

    def getMessage(self):
        return self.message


class JavaBoolean:
    """A Java wrapper is truthy in Python even when its stored value is false."""
    def __init__(self, value):
        self.value = value

    def booleanValue(self):
        return self.value


class ModelManager:
    def __init__(self, ready=False):
        self.ready = ready
        self.checks = 0
        self.downloads = 0
        self.task = NativeTask(pending=True)

    def isModelDownloaded(self, model):
        self.checks += 1
        return NativeTask(JavaBoolean(self.ready))

    def download(self, model, conditions):
        self.downloads += 1
        return self.task

    def complete_download(self, stored=True):
        self.ready = stored
        self.task.complete()


@pytest.fixture
def native():
    platform = AndroidPlatform.__new__(AndroidPlatform)
    manager = ModelManager()
    platform._closed = False
    platform._manager = manager
    platform._model = object()
    platform._model_state = "not_downloaded"
    platform._download_lock = asyncio.Lock()
    platform._listeners = {}
    platform._dynamic_proxy = lambda interface: object
    platform._jclass = lambda name: (
        (lambda: SimpleNamespace(build=lambda: object()))
        if name.endswith("DownloadConditions$Builder") else object
    )
    return platform, manager


async def wait_for_download(manager):
    for _ in range(30):
        if manager.downloads:
            return
        await asyncio.sleep(0)
    raise AssertionError("The native download did not start")


def test_download_reuses_installed_model_without_network_update(native):
    platform, manager = native
    manager.ready = True
    asyncio.run(platform.download_model())
    assert platform._model_state == "ready"
    assert manager.checks == 1
    assert manager.downloads == 0
    assert not platform._download_lock.locked()


def test_download_requires_post_download_readiness_check(native):
    platform, manager = native

    async def scenario():
        download = asyncio.create_task(platform.download_model())
        await wait_for_download(manager)
        assert platform._model_state == "downloading"
        assert await platform.model_status() == "downloading"
        manager.complete_download()
        await download

    asyncio.run(scenario())
    assert manager.checks == 2
    assert platform._model_state == "ready"
    assert not platform._listeners


def test_successful_native_task_without_stored_model_is_not_ready(native):
    platform, manager = native

    async def scenario():
        download = asyncio.create_task(platform.download_model())
        await wait_for_download(manager)
        manager.complete_download(stored=False)
        with pytest.raises(PlatformError, match="尚未可用"):
            await download

    asyncio.run(scenario())
    assert manager.checks == 2
    assert platform._model_state == "error"
    assert not platform._download_lock.locked()


def test_concurrent_preparation_does_not_download_twice(native):
    platform, manager = native

    async def scenario():
        first = asyncio.create_task(platform.download_model())
        second = asyncio.create_task(platform.download_model())
        await wait_for_download(manager)
        manager.complete_download()
        await asyncio.gather(first, second)

    asyncio.run(scenario())
    assert manager.downloads == 1
    assert manager.checks == 3
    assert platform._model_state == "ready"


def test_cancelled_python_waiter_releases_busy_state_and_allows_late_success(native):
    platform, manager = native

    async def scenario():
        download = asyncio.create_task(platform.download_model())
        await wait_for_download(manager)
        download.cancel()
        with pytest.raises(asyncio.CancelledError):
            await download
        assert platform._model_state == "error"
        assert not platform._download_lock.locked()
        # The SDK task remains alive. Its later completion must not complete the
        # cancelled Python Future or prevent an accurate readiness recheck.
        manager.complete_download()
        assert await platform.model_status() == "ready"
        assert not platform._listeners

    asyncio.run(scenario())


def test_cancelling_queued_request_does_not_change_active_download(native):
    platform, manager = native

    async def scenario():
        first = asyncio.create_task(platform.download_model())
        await wait_for_download(manager)
        queued = asyncio.create_task(platform.download_model())
        await asyncio.sleep(0)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        assert platform._model_state == "downloading"
        assert platform._download_lock.locked()
        manager.complete_download()
        await first

    asyncio.run(scenario())
    assert manager.downloads == 1
    assert platform._model_state == "ready"


def test_timeout_is_uncertain_and_later_recheck_can_find_model(native):
    platform, manager = native
    await_native = platform._await_task

    async def short_download_wait(task, *, timeout=60):
        return await await_native(task, timeout=0.001 if task is manager.task else timeout)

    platform._await_task = short_download_wait

    async def scenario():
        with pytest.raises(PlatformError, match="下载可能仍在继续"):
            await platform.download_model()
        assert manager.task.pending
        assert platform._model_state == "error"
        assert not platform._download_lock.locked()
        manager.complete_download()
        assert await platform.model_status() == "ready"
        assert not platform._listeners

    asyncio.run(scenario())


def test_failed_download_can_be_retried_without_reinitializing(native):
    platform, manager = native
    manager.task = NativeTask(error=NativeError(17))

    async def scenario():
        with pytest.raises(PlatformError, match="检查网络"):
            await platform.download_model()
        assert platform._model_state == "error"
        assert not platform._download_lock.locked()
        manager.task = NativeTask(pending=True)
        retry = asyncio.create_task(platform.download_model())
        for _ in range(30):
            if manager.downloads == 2:
                break
            await asyncio.sleep(0)
        assert manager.downloads == 2
        manager.complete_download()
        await retry

    asyncio.run(scenario())
    assert platform._model_state == "ready"


@pytest.mark.parametrize("code, message", [
    (4, "下载可能仍在继续"),
    (14, "稍后重新检查"),
    (17, "检查网络"),
    (100, "更新应用"),
    (101, "释放一些空间"),
    (102, "重新下载"),
])
def test_documented_native_error_codes_have_actionable_messages(native, code, message):
    platform, _ = native
    with pytest.raises(PlatformError, match=message):
        asyncio.run(platform._await_task(NativeTask(error=NativeError(code))))
    assert not platform._listeners


def test_unknown_native_error_keeps_short_diagnostic_and_code(native):
    platform, _ = native
    error = NativeError(987, "unknown failure\nlong native stack")
    with pytest.raises(PlatformError) as raised:
        asyncio.run(platform._await_task(NativeTask(error=error)))
    assert "代码 987" in str(raised.value)
    assert "unknown failure" in str(raised.value)
    assert "long native stack" not in str(raised.value)


def test_non_mlkit_java_error_does_not_require_error_code(native):
    platform, _ = native
    error = SimpleNamespace(getMessage=lambda: "native operation failed")
    with pytest.raises(PlatformError, match="native operation failed"):
        asyncio.run(platform._await_task(NativeTask(error=error)))


def test_cancelled_native_task_is_not_reported_as_installed(native):
    platform, manager = native
    manager.task = NativeTask(cancelled=True)
    with pytest.raises(PlatformError, match="取消"):
        asyncio.run(platform.download_model())
    assert platform._model_state == "error"
    assert not platform._download_lock.locked()


def test_closed_service_does_not_start_download(native):
    platform, manager = native
    platform._closed = True
    with pytest.raises(PlatformError, match="已关闭"):
        asyncio.run(platform.download_model())
    assert manager.downloads == manager.checks == 0

