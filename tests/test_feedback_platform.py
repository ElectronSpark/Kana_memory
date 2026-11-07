"""Feedback ZIPs stay binary; reporting a dialog never grants its confirmation."""
import asyncio
import base64
from io import BytesIO
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock
import zipfile

import pytest

from kanamemory.platform_android import AndroidPlatform, PlatformError
from kanamemory.platform_desktop import DesktopPlatform, PlatformError as DesktopError


@pytest.fixture
def platform():
    service = AndroidPlatform.__new__(AndroidPlatform)
    service._closed = False
    service._backup_native = service._backup_events = None
    service._dynamic_proxy = lambda interface: interface
    service.documents = []

    class Document:
        def __init__(self, activity, events):
            self.events = events
            self.cancelled = False
            self.exportJson = Mock()
            self.exportZipBase64 = Mock()
            self.exportStream = Mock()
            self.writeExportBlock = Mock()
            self.finishExport = Mock()
            self.importJson = Mock()
            service.documents.append(self)

        def cancel(self):
            self.cancelled = True
            self.events.onComplete(None, True, None)

        def isCancelled(self): return self.cancelled

    classes = {
        "org.example.kanamemory.KanaBackupDocuments": Document,
        "org.example.kanamemory.KanaBackupDocuments$Events": object,
        "org.beeware.android.MainActivity": SimpleNamespace(singletonThis=object()),
        "android.content.DialogInterface$OnClickListener": object,
    }
    service._jclass = classes.__getitem__
    return service


def zip_payload():
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("feedback.json", '{"note":"假名の字迹"}'.encode())
        archive.writestr("screenshots/evidence.png", bytes(range(256)))
    return output.getvalue()


def test_feedback_export_routes_exact_binary_zip_and_waits_for_write(platform):
    async def run():
        payload = zip_payload()
        task = asyncio.create_task(platform.export_feedback(payload, "页面反馈.zip"))
        await asyncio.sleep(0)
        request = platform.documents[-1]
        filename, mime = request.exportStream.call_args.args
        assert filename == "页面反馈.zip"
        assert mime == 'application/zip'
        request.exportZipBase64.assert_not_called()
        request.exportJson.assert_not_called()
        request.importJson.assert_not_called()
        assert not task.done()
        request.events.onComplete('kana-backup-export-stream-v1', False, None)
        assert await task is True
        assert b''.join(base64.b64decode(call.args[0], validate=True) for call in request.writeExportBlock.call_args_list) == payload
        request.finishExport.assert_called_once_with()
        assert request.cancelled
        assert platform._backup_native is platform._backup_events is None
    asyncio.run(run())


@pytest.mark.parametrize("result", ["cancel", "error"])
def test_feedback_export_picker_cancellation_or_write_error_is_not_success(platform, result):
    async def run():
        task = asyncio.create_task(platform.export_feedback(zip_payload()))
        await asyncio.sleep(0)
        request = platform.documents[-1]
        request.events.onComplete(None, result == "cancel", "目标文件写入失败" if result == "error" else None)
        if result == "cancel":
            assert await task is False
        else:
            with pytest.raises(PlatformError, match="写入失败"):
                await task
        assert request.cancelled
        assert platform._backup_native is platform._backup_events is None
    asyncio.run(run())


def test_cancelled_export_cannot_complete_a_later_export_from_a_late_callback(platform):
    async def run():
        abandoned = asyncio.create_task(platform.export_feedback(zip_payload()))
        await asyncio.sleep(0)
        old = platform.documents[-1]
        abandoned.cancel()
        with pytest.raises(asyncio.CancelledError):
            await abandoned
        assert old.cancelled
        assert platform._backup_native is None
        current = asyncio.create_task(platform.export_feedback(zip_payload()))
        await asyncio.sleep(0)
        new = platform.documents[-1]
        old.events.onComplete(None, False, None)
        await asyncio.sleep(0)
        assert not current.done()
        assert platform._backup_native is new
        new.events.onComplete('kana-backup-export-stream-v1', False, None)
        assert await current is True
    asyncio.run(run())


def test_feedback_export_respects_existing_native_picker(platform):
    async def run():
        import_task = asyncio.create_task(platform.import_backup())
        await asyncio.sleep(0)
        with pytest.raises(PlatformError, match="当前的文件选择"):
            await platform.export_feedback(zip_payload())
        assert len(platform.documents) == 1
        request = platform.documents[0]
        request.importJson.assert_called_once_with()
        request.events.onComplete(None, True, None)
        assert await import_task is None
    asyncio.run(run())


@pytest.mark.parametrize("failure", [None, "window", "text", "screenshot"])
def test_reporting_dialog_cancels_before_capture_and_never_authorizes_action(platform, failure):
    async def run():
        future = asyncio.get_running_loop().create_future()
        text = SimpleNamespace(getText=Mock(return_value="删除全部学习数据？"))
        view = SimpleNamespace(getChildCount=lambda: 1, getChildAt=lambda index: text)
        native = SimpleNamespace(setButton=Mock(), getWindow=Mock(
            return_value=SimpleNamespace(getDecorView=lambda: view)))
        if failure == "window":
            native.getWindow.side_effect = RuntimeError("window destroyed")
        elif failure == "text":
            text.getText.side_effect = RuntimeError("view detached")
        class ConfirmDialog:
            _impl = SimpleNamespace(native=native, future=future)
        dialog = ConfirmDialog()
        platform.feedback_callback = Mock()

        def capture(actual_view):
            # The destructive action is already cancelled even if capture fails.
            assert future.done() and future.result() is False
            assert actual_view is view
            if failure == "screenshot":
                raise RuntimeError("bitmap unavailable")
            return b"image evidence"
        platform.capture_feedback_screenshot = Mock(side_effect=capture)
        destructive_action = Mock()

        async def confirm():
            if await future:
                destructive_action()
        pending = asyncio.create_task(confirm())
        platform.attach_feedback_dialog(dialog)
        which, caption, listener = native.setButton.call_args.args
        assert which == -3 and caption == "反馈"
        listener.onClick(native, which)
        assert future.done() and future.result() is False
        platform.feedback_callback.assert_not_called()  # Deferred until modal handler returns.
        await asyncio.sleep(0)
        await pending
        destructive_action.assert_not_called()
        platform.feedback_callback.assert_called_once()
        evidence = platform.feedback_callback.call_args.kwargs
        assert evidence["overlay"]["kind"] == "ConfirmDialog"
        if failure:
            assert evidence["screenshot"] is None
            assert evidence["overlay"]["capture_error"] == "RuntimeError"
        else:
            assert evidence["screenshot"] == b"image evidence"
            assert evidence["overlay"]["visible_text"] == ["删除全部学习数据？"]
        listener.onClick(native, which)
        await asyncio.sleep(0)
        platform.feedback_callback.assert_called_once()  # Duplicate native events are ignored.
    asyncio.run(run())


@pytest.fixture(params=["android", "desktop"])
def clipboard_platform(request, monkeypatch):
    """Native clipboard doubles only: these tests never access the real clipboard."""
    owner_thread = threading.get_ident()
    writes, events = [], []

    def forbidden_read(*args):
        raise AssertionError("Clipboard text must never be read.")

    monkeypatch.setattr(asyncio, "to_thread", Mock(side_effect=AssertionError("Must stay on UI thread.")))
    if request.param == "android":
        service = AndroidPlatform.__new__(AndroidPlatform)
        service._closed = False
        description = None

        def set_clip(clip):
            nonlocal description
            assert threading.get_ident() == owner_thread
            events.append("write")
            writes.append(clip.text)
            description = SimpleNamespace(getLabel=lambda: clip.label)

        def get_description():
            assert writes  # No metadata is read before our own write succeeds.
            events.append("description")
            return description

        manager = SimpleNamespace(setPrimaryClip=Mock(side_effect=set_clip),
            getPrimaryClipDescription=Mock(side_effect=get_description),
            getPrimaryClip=Mock(side_effect=forbidden_read))
        service._context = SimpleNamespace(getSystemService=Mock(return_value=manager))
        create_clip = Mock(side_effect=lambda label, text: SimpleNamespace(label=label, text=text))
        service._jclass = {
            "android.content.Context": SimpleNamespace(CLIPBOARD_SERVICE="clipboard"),
            "android.content.ClipData": SimpleNamespace(newPlainText=create_clip),
        }.__getitem__
        return SimpleNamespace(service=service, error=PlatformError, writes=writes,
            native=manager, events=events, create_clip=create_clip, name=request.param)

    def set_text(text, text_format):
        assert threading.get_ident() == owner_thread
        assert text_format is unicode_format
        events.append("write")
        writes.append(text)

    unicode_format = object()
    clipboard = SimpleNamespace(SetText=Mock(side_effect=set_text), GetText=Mock(side_effect=forbidden_read))
    forms = SimpleNamespace(Clipboard=clipboard, TextDataFormat=SimpleNamespace(UnicodeText=unicode_format))
    monkeypatch.setitem(sys.modules, "System", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "System.Windows", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "System.Windows.Forms", forms)
    return SimpleNamespace(service=DesktopPlatform(), error=DesktopError, writes=writes,
        native=clipboard, events=events, name=request.param)


@pytest.mark.parametrize("text", ["反馈：假名の字迹 😀\n第二行\r\n  保留空格  ",
                                    "字" * (128 * 1024), "😀" * (64 * 1024)],
                         ids=["unicode-newlines", "bmp-limit", "astral-limit"])
def test_copy_text_is_complete_unicode_on_calling_thread(clipboard_platform, text):
    p = clipboard_platform
    assert asyncio.run(p.service.copy_text(text)) is True
    assert p.writes == [text]
    assert p.events == (["write", "description"] if p.name == "android" else ["write"])


@pytest.mark.parametrize("text", [None, b"bytes", "", " \n\t", "before\x00after", "\ud800",
                                    "字" * (128 * 1024 + 1), "😀" * (64 * 1024 + 1)],
                         ids=["none", "bytes", "empty", "whitespace", "nul", "surrogate",
                              "bmp-too-large", "astral-too-large"])
def test_copy_text_invalid_or_large_content_is_never_truncated_or_written(clipboard_platform, text):
    p = clipboard_platform
    with pytest.raises(p.error):
        asyncio.run(p.service.copy_text(text))
    assert p.writes == []
    assert p.events == []
    if p.name == "android":
        p.create_clip.assert_not_called()
        p.service._context.getSystemService.assert_not_called()
    else:
        p.native.SetText.assert_not_called()


def test_copy_text_closed_service_does_not_report_success(clipboard_platform):
    p = clipboard_platform
    p.service._closed = True
    assert asyncio.run(p.service.copy_text("完整反馈")) is False
    assert p.writes == []
    assert p.events == []


def test_copy_text_native_failure_does_not_report_success_or_read_back(clipboard_platform):
    p = clipboard_platform
    setter = p.native.setPrimaryClip if p.name == "android" else p.native.SetText
    setter.side_effect = RuntimeError("native service busy")
    with pytest.raises(p.error, match="未能复制"):
        asyncio.run(p.service.copy_text("完整反馈"))
    assert p.writes == []
    if p.name == "android":
        p.native.getPrimaryClipDescription.assert_not_called()


@pytest.mark.parametrize("failure", ["missing_service", "missing_description", "different_label", "metadata_error"])
@pytest.mark.parametrize("clipboard_platform", ["android"], indirect=True)
def test_android_copy_requires_confirmation_of_its_own_write(clipboard_platform, failure):
    p = clipboard_platform
    if failure == "missing_service":
        p.service._context.getSystemService.return_value = None
    elif failure == "metadata_error":
        p.native.getPrimaryClipDescription.side_effect = RuntimeError("metadata unavailable")
    else:
        p.native.getPrimaryClipDescription.side_effect = None
        p.native.getPrimaryClipDescription.return_value = (None if failure == "missing_description"
            else SimpleNamespace(getLabel=lambda: "another-write"))
    with pytest.raises(p.error):
        asyncio.run(p.service.copy_text("完整反馈"))
    p.native.getPrimaryClip.assert_not_called()
    if failure == "missing_service":
        p.create_clip.assert_not_called()
        assert p.writes == []


@pytest.mark.parametrize("clipboard_platform", ["android"], indirect=True)
def test_android_copy_uses_a_fresh_marker_for_each_request(clipboard_platform):
    p = clipboard_platform
    assert asyncio.run(p.service.copy_text("第一次")) is True
    assert asyncio.run(p.service.copy_text("第二次")) is True
    labels = [call.args[0] for call in p.create_clip.call_args_list]
    assert len(set(labels)) == 2
    p.native.getPrimaryClip.assert_not_called()


@pytest.mark.parametrize("clipboard_platform", ["desktop"], indirect=True)
def test_desktop_copy_unavailable_backend_is_not_success(clipboard_platform, monkeypatch):
    p = clipboard_platform
    monkeypatch.delattr(sys.modules["System.Windows.Forms"], "Clipboard")
    with pytest.raises(p.error, match="未能复制"):
        asyncio.run(p.service.copy_text("完整反馈"))
    assert p.writes == []


def test_feedback_screenshot_copies_native_png_buffer_exactly(platform):
    png = b"\x89PNG\r\n\x1a\n" + bytes(range(256))
    events = []

    class Bitmap:
        @staticmethod
        def createBitmap(width, height, config):
            events.append(("bitmap", width, height))
            return SimpleNamespace(compress=lambda fmt, quality, out: out.write(png) or True,
                                   recycle=lambda: events.append("recycled"))

    class Output:
        def __init__(self): self.data = bytearray()
        def write(self, raw): self.data.extend(raw)
        def toByteArray(self): return self.data  # buffer-protocol array, like Chaquopy byte[]
        def close(self): events.append("closed")

    classes = {
        "android.graphics.Bitmap": Bitmap,
        "android.graphics.Bitmap$Config": SimpleNamespace(ARGB_8888="argb"),
        "android.graphics.Bitmap$CompressFormat": SimpleNamespace(PNG="png"),
        "android.graphics.Canvas": lambda bitmap: SimpleNamespace(scale=lambda x, y: None),
        "java.io.ByteArrayOutputStream": Output,
    }
    platform._jclass = classes.__getitem__
    view = SimpleNamespace(getWidth=lambda: 400, getHeight=lambda: 800, draw=lambda canvas: None)
    raw = platform.capture_feedback_screenshot(view)
    assert type(raw) is bytes and raw == png
    assert events == [("bitmap", 400, 800), "closed", "recycled"]
