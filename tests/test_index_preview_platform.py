"""Native-modal protocol tests without a JVM, display, or Android emulator."""
import asyncio
import gc
from pathlib import Path
import sys
from types import SimpleNamespace
import weakref

import pytest

ROOT = Path(__file__).resolve().parents[1]
from kanamemory.platform_android import AndroidPlatform, PlatformError
from kanamemory.platform_desktop import DesktopPlatform


class Lifecycle:
    def __init__(self):
        self.observers = []

    def addObserver(self, observer):
        self.observers.append(observer)

    def removeObserver(self, observer):
        self.observers.remove(observer)

    def destroy(self):
        for observer in list(self.observers):
            observer.onStateChanged(self, "destroy")


class Activity:
    def __init__(self):
        self.lifecycle = Lifecycle()

    def isFinishing(self):
        return False

    def isDestroyed(self):
        return False

    def getLifecycle(self):
        return self.lifecycle


class MessageView:
    def setAutoLinkMask(self, value):
        self.auto_link_mask = value

    def setLinksClickable(self, value):
        self.links_clickable = value

    def setMovementMethod(self, value):
        self.movement = value


class NativeDialog:
    def __init__(self, builder):
        self.builder = builder
        self.shown = self.dismissed = False
        self.on_cancel = self.on_dismiss = None
        self.message_view = MessageView()

    def setOnCancelListener(self, listener):
        self.on_cancel = listener

    def setOnDismissListener(self, listener):
        self.on_dismiss = listener

    def setCanceledOnTouchOutside(self, value):
        self.cancel_outside = value

    def show(self):
        if self.builder.host.fail_show:
            raise RuntimeError("Native window unavailable")
        self.shown = True

    def dismiss(self):
        self.dismissed = True
        if self.on_dismiss:
            self.on_dismiss.onDismiss(self)

    def findViewById(self, view_id):
        assert view_id == 16908299
        return self.message_view

    def click(self, which):
        self.builder.buttons[which][1].onClick(self, which)
        self.dismiss()

    def cancel(self):
        assert self.builder.cancelable
        self.on_cancel.onCancel(self)
        self.dismiss()


class Builder:
    def __init__(self, host, activity):
        self.host = host
        self.activity = activity
        self.buttons = {}

    def setTitle(self, title):
        self.title = title

    def setMessage(self, message):
        self.message = message

    def setCancelable(self, value):
        self.cancelable = value

    def setPositiveButton(self, label, listener):
        self.buttons[-1] = (label, listener)

    def setNegativeButton(self, label, listener):
        self.buttons[-2] = (label, listener)

    def create(self):
        dialog = NativeDialog(self)
        self.host.dialogs.append(dialog)
        return dialog


@pytest.fixture
def native_environment():
    host = SimpleNamespace(dialogs=[], fail_show=False)
    activity = Activity()
    classes = {
        "org.beeware.android.MainActivity": SimpleNamespace(singletonThis=activity),
        "android.app.AlertDialog$Builder": lambda owner: Builder(host, owner),
        "android.content.DialogInterface$OnClickListener": object,
        "android.content.DialogInterface$OnCancelListener": object,
        "android.content.DialogInterface$OnDismissListener": object,
        "androidx.lifecycle.LifecycleEventObserver": object,
        "androidx.lifecycle.Lifecycle$Event": SimpleNamespace(ON_DESTROY="destroy"),
        "android.R$id": SimpleNamespace(message=16908299),
    }
    platform = AndroidPlatform.__new__(AndroidPlatform)
    platform._closed = False
    platform._index_preview = None
    platform._jclass = classes.__getitem__
    platform._dynamic_proxy = lambda interface: object
    platform._back_native = platform._back_events = None
    platform._tts = platform._recognizer = None
    platform._utterances = {}
    platform._listeners = {}
    return platform, host, activity, classes


async def start_preview(platform, host):
    task = asyncio.create_task(platform.preview_index("假名：あ", "あ / ア · a\nhttps://example.com/あ"))
    await asyncio.sleep(0)
    assert len(host.dialogs) == 1
    return task, host.dialogs[0]


@pytest.mark.parametrize("event,expected", [
    ("positive", True), ("negative", False), ("cancel", False), ("dismiss", False),
])
def test_explicit_action_only_and_all_close_routes_settle_once(native_environment, event, expected):
    platform, host, activity, _ = native_environment

    async def scenario():
        task, dialog = await start_preview(platform, host)
        assert dialog.builder.title == "假名：あ"
        assert type(dialog.builder.message) is str
        assert dialog.builder.message == "あ / ア · a\nhttps://example.com/あ"
        assert {key: value[0] for key, value in dialog.builder.buttons.items()} == {-1: "查看详情", -2: "关闭"}
        assert dialog.builder.cancelable and dialog.cancel_outside
        assert dialog.message_view.auto_link_mask == 0
        assert dialog.message_view.links_clickable is False
        assert dialog.message_view.movement is None
        request_ref = weakref.ref(platform._index_preview)
        future = platform._index_preview.future
        # Keep native listeners alive after cleanup to check that they retain no request.
        old_cancel, old_dismiss = dialog.on_cancel, dialog.on_dismiss
        if event == "positive":
            dialog.click(-1)
        elif event == "negative":
            dialog.click(-2)
        else:
            getattr(dialog, event)()
        old_cancel.onCancel(dialog)
        old_dismiss.onDismiss(dialog)
        assert await asyncio.wait_for(task, 1) is expected
        assert future.done() and future.result() is expected
        assert platform._index_preview is None
        assert activity.lifecycle.observers == []
        assert dialog.on_cancel is None and dialog.on_dismiss is None
        assert dialog.dismissed
        gc.collect()
        assert request_ref() is None
        old_cancel.onCancel(dialog)
        old_dismiss.onDismiss(dialog)

    asyncio.run(scenario())


@pytest.mark.parametrize("ending", ["platform_close", "activity_destroy", "caller_cancel"])
def test_lifecycle_and_task_cancellation_dismiss_and_finish_false(native_environment, ending):
    platform, host, activity, _ = native_environment

    async def scenario():
        task, dialog = await start_preview(platform, host)
        future = platform._index_preview.future
        if ending == "platform_close":
            platform.close()
        elif ending == "activity_destroy":
            activity.lifecycle.destroy()
        else:
            task.cancel()
        assert await asyncio.wait_for(task, 1) is False
        assert future.done()
        assert platform._index_preview is None
        assert dialog.dismissed
        assert activity.lifecycle.observers == []

    asyncio.run(scenario())


def test_second_preview_does_not_open_over_the_first(native_environment):
    platform, host, _, _ = native_environment

    async def scenario():
        task, dialog = await start_preview(platform, host)
        assert await platform.preview_index("Another", "No second modal") is False
        assert len(host.dialogs) == 1
        assert not task.done()
        dialog.cancel()
        assert await task is False
        next_task = asyncio.create_task(platform.preview_index("来源", "纯文本", action="打开网页"))
        await asyncio.sleep(0)
        assert host.dialogs[-1].builder.buttons[-1][0] == "打开网页"
        host.dialogs[-1].click(-1)
        assert await next_task is True

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["missing_interface", "native_show"])
def test_native_setup_error_releases_request_and_lifecycle(native_environment, failure):
    platform, host, activity, classes = native_environment
    if failure == "missing_interface":
        del classes["android.content.DialogInterface$OnClickListener"]
    else:
        host.fail_show = True

    async def scenario():
        with pytest.raises(PlatformError, match="无法打开索引预览"):
            await platform.preview_index("索引", "简明说明")
        assert platform._index_preview is None
        assert activity.lifecycle.observers == []
        if host.dialogs:
            assert host.dialogs[-1].dismissed

    asyncio.run(scenario())


def test_closed_or_absent_activity_cannot_open_dialog(native_environment):
    platform, host, _, classes = native_environment

    async def scenario():
        classes["org.beeware.android.MainActivity"].singletonThis = None
        assert await platform.preview_index("索引", "说明") is False
        platform._closed = True
        assert await platform.preview_index("索引", "说明") is False
        assert host.dialogs == []

    asyncio.run(scenario())


def test_desktop_fallback_uses_modal_confirmation_and_keeps_pending_guard(monkeypatch):
    platform = DesktopPlatform()
    shown = []

    async def scenario():
        response = asyncio.get_running_loop().create_future()

        async def show_dialog(dialog):
            shown.append(dialog)
            return await response

        fake_toga = SimpleNamespace(
            App=SimpleNamespace(app=SimpleNamespace(current_window=SimpleNamespace(dialog=show_dialog))),
            ConfirmDialog=lambda title, message: SimpleNamespace(title=title, message=message),
        )
        monkeypatch.setitem(sys.modules, "toga", fake_toga)
        task = asyncio.create_task(platform.preview_index("索引", "あ / a"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(shown) == 1 and shown[0].title == "索引"
        assert "あ / a" in shown[0].message and "OK" in shown[0].message and "Cancel" in shown[0].message
        task.cancel()
        assert await task is False
        assert not response.cancelled()
        assert await platform.preview_index("Repeated", "No second dialog") is False
        response.set_result(False)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert platform._preview_task is None

    asyncio.run(scenario())
