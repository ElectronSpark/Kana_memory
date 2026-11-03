"""Android services. Java imports stay lazy so desktop tooling can import this module.

ML Kit consumes ordered pen coordinates, never the expected answer. All Google Task
callbacks resolve asyncio futures on the Toga event loop, not on Java callback threads.
"""

from __future__ import annotations

import asyncio
import base64
import codecs
import math
import struct
import tempfile
import weakref
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from .diagnostic_events import record_error
from .voice_policy import VOICE_LABELS, chinese_language, describe_voice, rank_voices


class PlatformError(RuntimeError):
    """A device service is unavailable or a real platform operation failed."""


class ModelNotReady(PlatformError):
    pass


_BACKUP_MAX_BYTES = 64 * 1024 * 1024
_BACKUP_BLOCK_BYTES = 64 * 1024
_BACKUP_IMPORT_READY = 'kana-backup-import-stream-v1'
_BACKUP_EXPORT_READY = 'kana-backup-export-stream-v1'
_CLIPBOARD_MAX_UTF16_BYTES = 256 * 1024


def _validate_backup_export(data, archive):
    if not isinstance(data, bytes) or len(data) > _BACKUP_MAX_BYTES:
        raise PlatformError('文件必须为不超过 64 MiB 的字节数据。')
    if not archive:
        decoder = codecs.getincrementaldecoder('utf-8')('strict')
        for start in range(0, len(data), _BACKUP_BLOCK_BYTES):
            decoder.decode(data[start:start + _BACKUP_BLOCK_BYTES], final=False)
        decoder.decode(b'', final=True)


def _read_backup_stream(native, cache_dir, max_bytes=_BACKUP_MAX_BYTES):
    """Bounded JNI strings; no Java full-file byte array or UTF-16 string.

    Only the framework-owned cache directory is used, never a selected path.
    Android TemporaryFile is unlinked while open, including process death.
    Existing callers still receive the original immutable, UTF-8 bytes.
    """
    decoder = codecs.getincrementaldecoder('utf-8')('strict')
    total = 0
    with tempfile.TemporaryFile(mode='w+b', dir=cache_dir) as output:
        while True:
            encoded = str(native.readImportBlock())
            if not encoded:
                break
            if len(encoded) > ((_BACKUP_BLOCK_BYTES + 2) // 3) * 4:
                raise PlatformError('文件传输块超过限制。')
            block = base64.b64decode(encoded, validate=True)
            if not block or len(block) > _BACKUP_BLOCK_BYTES:
                raise PlatformError('文件传输块无效。')
            total += len(block)
            if total > max_bytes:
                raise PlatformError('文件超过 64 MiB 限制。' if max_bytes == _BACKUP_MAX_BYTES else '文本文件超过 16,000 字节限制。')
            decoder.decode(block, final=False)
            output.write(block)
        decoder.decode(b'', final=True)
        output.seek(0)
        result = output.read(total + 1)
        if len(result) != total:
            raise PlatformError('临时文件读取不完整。')
        return result


def _write_backup_stream(native, data):
    for start in range(0, len(data), _BACKUP_BLOCK_BYTES):
        # Bounded base64 avoids per-byte Python/Java boxing on the JNI bridge.
        native.writeExportBlock(base64.b64encode(data[start:start + _BACKUP_BLOCK_BYTES]).decode('ascii'))
    native.finishExport()
    return True


async def _backup_stream_io(native, function, *args):
    task = asyncio.create_task(asyncio.to_thread(function, native, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        native.cancel()  # Close the provider handle to interrupt a pending read/write.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                pass
            except Exception:
                break
        if not task.cancelled():
            task.exception()  # Consume a close-induced worker failure before cleanup.
        raise


def _model_operation_error(exception):
    """Translate documented ML Kit codes without guessing from English messages."""
    # https://developers.google.com/android/reference/com/google/mlkit/common/MlKitException
    try:
        code = int(exception.getErrorCode())
    except (AttributeError, TypeError, ValueError):
        code = None
    messages = {
        4: "设备操作等待超时，请稍后重新检查模型状态；下载可能仍在继续。",
        14: "手写服务暂不可用，请稍后重新检查模型状态或重试。",
        17: "无法连接模型服务，请检查网络是否能访问 Google 下载服务，再重试。",
        100: "模型与当前识别组件不兼容，请更新应用后重试。",
        101: "设备存储空间不足，请释放一些空间后重试下载。",
        102: "下载的模型校验失败，请重新下载日语手写模型。",
    }
    if code in messages:
        return messages[code]
    try:
        detail = str(exception.getMessage() or "").split("\n", 1)[0][:100]
    except (AttributeError, TypeError):
        detail = ""
    message = "设备服务未能完成，请稍后重试或重新检查模型。"
    return message + (f"（代码 {code}）" if code is not None else "") + (" " + detail if detail else "")


def _kana_span_offsets(text, spans):
    """Validate explicit [start, end) code-point spans and return UTF-16 offsets."""
    units = [0]
    for char in text:
        units.append(units[-1] + len(char.encode("utf-16-le")) // 2)
    normalized = []
    for span in spans:
        if not isinstance(span, Mapping):
            raise TypeError("Kana spans must be dictionaries with start, end, kana_id.")
        start, end, kana_id = span["start"], span["end"], span["kana_id"]
        if type(start) is not int or type(end) is not int:
            raise TypeError("Kana span offsets must be integer Python character indexes.")
        if not 0 <= start < end <= len(text):
            raise ValueError("Kana span is outside the label text.")
        if not isinstance(kana_id, str) or not kana_id:
            raise ValueError("Kana span requires a nonempty kana_id.")
        normalized.append((start, end, kana_id))
    normalized.sort(key=lambda item: item[0])
    previous_end = 0
    for start, end, _ in normalized:
        if start < previous_end:
            raise ValueError("Kana link spans cannot overlap.")
        previous_end = end
    return [(units[start], units[end], kana_id) for start, end, kana_id in normalized]


class _IndexPreviewRequest:
    """One native modal lifetime; native listeners keep only a weak reference here."""
    def __init__(self, loop):
        self.loop = loop
        self.future = loop.create_future()
        self.decided = False
        self.dialog = self.lifecycle = self.lifecycle_listener = None
        self.listeners = ()

    def resolve(self, accepted):
        if self.decided:
            return
        self.decided = True
        future = self.future

        def complete():
            if not future.done():
                future.set_result(bool(accepted))

        if not self.loop.is_closed():
            self.loop.call_soon_threadsafe(complete)

    def cancel(self):
        self.resolve(False)
        self.dispose()

    def dispose(self):
        dialog, lifecycle, observer = self.dialog, self.lifecycle, self.lifecycle_listener
        self.dialog = self.lifecycle = self.lifecycle_listener = None
        self.listeners = ()
        if lifecycle is not None and observer is not None:
            try:
                lifecycle.removeObserver(observer)
            except Exception as exc:
                record_error("platform_index", "cleanup", exc)
        if dialog is not None:
            try:
                dialog.setOnCancelListener(None)
                dialog.setOnDismissListener(None)
                dialog.dismiss()
            except Exception as exc:
                record_error("platform_index", "cleanup", exc)


class AndroidPlatform:
    name = "android"

    def __init__(self, context=None):
        from java import dynamic_proxy, jclass

        self._jclass = jclass
        self._dynamic_proxy = dynamic_proxy
        if context is None:
            context = jclass("org.beeware.android.MainActivity").singletonThis
        if context is None:
            raise PlatformError("Android 窗口尚未准备完成。")
        self._context = context.getApplicationContext()
        self._manager = self._model = self._recognizer = None
        self._tts = None
        self._tts_listener = None
        self._progress_events = self._progress_listener = None
        self._audio_manager = self._focus_request = self._focus_listener = None
        self._tts_audio_attributes = None
        self._utterances = {}
        self._utterance_languages = {}
        self._current_utterance = None
        self.last_audio_status = "idle"
        self._listeners = {}
        self._back_native = self._back_events = None
        self._index_preview = None
        self._backup_native = self._backup_events = None
        self._recording = None
        self._closed = False
        self._model_state = "not_downloaded"
        self.tts_status = "unavailable"
        self.tts_detail = "日语语音尚未初始化。"
        self.tts_voice = None
        self.tts_engine = None
        self.tts_voice_inventory = []
        self._voice_cache = None
        self._voice_candidates = {}
        self._chinese_tts = {language: {"status": "unavailable", "detail": f"{VOICE_LABELS[language]}语音尚未初始化。", "voice": None, "locale": None}
                             for language in ("cmn", "yue")}
        self._tts_preparing = False
        self._tts_audio_ready = False
        self._tts_lock = asyncio.Lock()
        self._download_lock = asyncio.Lock()

    @property
    def tts_ready(self):
        return self.tts_status == "ready" and not self._closed

    def _check_open(self):
        if self._closed:
            raise PlatformError("Android 服务已关闭。")

    def bind_ink_touch(self, widget, on_samples):
        """Deliver complete MotionEvent batches in logical canvas coordinates.

        Android timestamps already share the uptime clock; do not replace them
        with Python callback times when a busy UI receives historical samples.
        The native listener consumes one pointer and protects the parent scroll.
        """
        self._check_open()
        if not callable(on_samples):
            raise TypeError("Ink callback must be callable.")
        density = float(widget._impl.dpi_scale)
        if not math.isfinite(density) or density <= 0:
            raise ValueError("Invalid canvas density.")
        widget_ref, platform_ref, token = weakref.ref(widget), weakref.ref(self), object()
        c = self._jclass

        class InkEvents(self._dynamic_proxy(c("org.example.kanamemory.KanaInkTouchListener$Events"))):
            def onSamples(self, phase, packed):
                owner, platform = widget_ref(), platform_ref()
                if owner is None or platform is None or platform._closed:
                    return
                state = getattr(owner, "_ink_touch_state", None)
                if state is None or state["token"] is not token:
                    return
                phases = ("down", "move", "up", "cancel")
                index = int(phase)
                if not 0 <= index < len(phases):
                    raise ValueError("Unknown ink phase.")
                # One bulk copy across JNI, instead of one call per coordinate.
                samples = list(struct.iter_unpack("<ffq", bytes(packed)))
                state["callback"](phases[index], samples)

        events = InkEvents()
        listener = c("org.example.kanamemory.KanaInkTouchListener")(density, events)
        native = widget._impl.native
        previous = getattr(widget, "_ink_touch_state", None)
        if previous is not None:
            previous["listener"].detach(native)
        widget._ink_touch_state = {"token": token, "callback": on_samples,
                                   "events": events, "listener": listener}
        native.setOnTouchListener(listener)
        return True

    async def _backup_document(self, data=None, filename=None, *, archive=False, max_bytes=_BACKUP_MAX_BYTES):
        """Await one native SAF picker. Cancellation never changes learning data."""
        self._check_open()
        if self._backup_native is not None:
            raise PlatformError("请先完成当前的文件选择。")
        if data is not None:
            _validate_backup_export(data, archive)
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        c = self._jclass

        def complete(content, cancelled, error):
            if not future.done():
                if error:
                    future.set_exception(PlatformError(error))
                else:
                    future.set_result(None if cancelled else content)

        class Events(self._dynamic_proxy(c("org.example.kanamemory.KanaBackupDocuments$Events"))):
            def onComplete(self, content, cancelled, error):
                # Convert Java values before leaving the callback thread.
                value = str(content) if content is not None else ""
                message = str(error) if error is not None else None
                if not loop.is_closed():
                    loop.call_soon_threadsafe(complete, value, bool(cancelled), message)

        activity = c("org.beeware.android.MainActivity").singletonThis
        if activity is None:
            raise PlatformError("Android 窗口尚未准备完成。")
        self._backup_events = Events()
        native = self._backup_native = c("org.example.kanamemory.KanaBackupDocuments")(activity, self._backup_events)
        try:
            if data is None:
                native.importJson()
            else:
                native.exportStream(filename, "application/zip" if archive else "application/json")
            result = await future
            if result is None:
                return None if data is None else False
            if data is None:
                if result != _BACKUP_IMPORT_READY:
                    raise PlatformError("文件读取接口版本不匹配。")
                cache = str(self._context.getCacheDir().getCanonicalPath())
                value = await _backup_stream_io(native, _read_backup_stream, cache, max_bytes)
                return None if native.isCancelled() else value
            if result != _BACKUP_EXPORT_READY:
                raise PlatformError("文件保存接口版本不匹配。")
            value = await _backup_stream_io(native, _write_backup_stream, data)
            return False if native.isCancelled() else value
        finally:
            native.cancel()
            self._backup_native = self._backup_events = None

    async def export_backup(self, data: bytes, filename="KanaMemory-learning.json"):
        return await self._backup_document(data, filename)

    async def export_feedback(self, data: bytes, filename="KanaMemory-feedback.zip"):
        return await self._backup_document(data, filename, archive=True)

    async def copy_text(self, text: str) -> bool:
        """Copy a complete text export; only inspect our write's label, never its text."""
        if self._closed:
            return False
        if not isinstance(text, str) or not text.strip() or "\x00" in text:
            raise PlatformError("复制内容必须是非空且不含空字符的文字。")
        if len(text) > _CLIPBOARD_MAX_UTF16_BYTES // 2:
            raise PlatformError("内容较多，请导出 ZIP 或减少所选反馈。")
        try:
            size = len(text.encode("utf-16-le"))
        except UnicodeEncodeError as exc:
            raise PlatformError("复制内容含有无法编码的字符，请导出 ZIP。") from exc
        if size > _CLIPBOARD_MAX_UTF16_BYTES:
            raise PlatformError("内容较多，请导出 ZIP 或减少所选反馈。")
        try:
            c = self._jclass
            manager = self._context.getSystemService(c("android.content.Context").CLIPBOARD_SERVICE)
            if manager is None:
                raise PlatformError("剪贴板服务不可用，请导出 ZIP。")
            label = "KanaMemory-feedback-" + uuid4().hex
            clip = c("android.content.ClipData").newPlainText(label, text)
            # Stay on the foreground UI thread. The conservative size bound
            # leaves Binder overhead; an actual transaction can still fail.
            manager.setPrimaryClip(clip)
            description = manager.getPrimaryClipDescription()
            if description is None or str(description.getLabel()) != label:
                raise PlatformError("未能确认剪贴板写入，请重试或导出 ZIP。")
        except PlatformError:
            raise
        except Exception as exc:
            raise PlatformError("未能复制到剪贴板，请重试或导出 ZIP。") from exc
        return True

    def feedback_environment(self):
        c = self._jclass
        resources = self._context.getResources()
        metrics = resources.getDisplayMetrics()
        return {"platform": "android", "api": int(c("android.os.Build$VERSION").SDK_INT),
                "model": str(c("android.os.Build").MODEL), "density": float(metrics.density),
                "font_scale": float(resources.getConfiguration().fontScale),
                "width_px": int(metrics.widthPixels), "height_px": int(metrics.heightPixels),
                "tts_status": self.tts_status, "tts_voice": self.tts_voice,
                "last_audio_status": self.last_audio_status, "model_state": self._model_state}

    def capture_feedback_screenshot(self, view=None):
        """Render only our own window, without screen-capture/storage permissions."""
        c = self._jclass
        if view is None:
            activity = c("org.beeware.android.MainActivity").singletonThis
            view = activity.getWindow().getDecorView()
        width, height = int(view.getWidth()), int(view.getHeight())
        if width <= 0 or height <= 0:
            return None
        scale = min(1.0, 2560 / max(width, height))
        bitmap = c("android.graphics.Bitmap").createBitmap(max(1, int(width * scale)),
            max(1, int(height * scale)), c("android.graphics.Bitmap$Config").ARGB_8888)
        output = c("java.io.ByteArrayOutputStream")()
        try:
            canvas = c("android.graphics.Canvas")(bitmap)
            canvas.scale(scale, scale)
            view.draw(canvas)
            if not bitmap.compress(c("android.graphics.Bitmap$CompressFormat").PNG, 100, output):
                raise PlatformError("页面截图压缩失败。")
            return bytes((int(value) & 255 for value in output.toByteArray()))
        finally:
            output.close()
            bitmap.recycle()

    def attach_feedback_dialog(self, dialog):
        """A feedback click cancels confirmations; it can never authorize them."""
        callback = getattr(self, "feedback_callback", None)
        implementation = getattr(dialog, "_impl", None)
        native = getattr(implementation, "native", None)
        if not callback or native is None or not hasattr(native, "setButton"):
            return
        c, owner = self._jclass, self
        loop = asyncio.get_running_loop()

        class Report(self._dynamic_proxy(c("android.content.DialogInterface$OnClickListener"))):
            def onClick(self, current, which):
                future = implementation.future
                if future.done():
                    return
                future.set_result(False)
                overlay = {"kind": type(dialog).__name__, "visible_text": []}
                def collect(view, depth=0):
                    if depth > 20:
                        return
                    if hasattr(view, "getText"):
                        overlay["visible_text"].append(str(view.getText()))
                    if hasattr(view, "getChildCount"):
                        for index in range(int(view.getChildCount())):
                            collect(view.getChildAt(index), depth + 1)
                try:
                    view = native.getWindow().getDecorView()
                    collect(view)
                    screenshot = owner.capture_feedback_screenshot(view)
                except Exception as exc:
                    overlay["capture_error"] = type(exc).__name__
                    screenshot = None
                loop.call_soon(lambda: callback(overlay=overlay, screenshot=screenshot))

        listener = Report()
        dialog._feedback_listener = listener
        native.setButton(-3, "反馈", listener)

    async def import_backup(self):
        return await self._backup_document()

    async def import_material_text(self):
        from .material_input import MAX_BYTES
        return await self._backup_document(max_bytes=MAX_BYTES)

    def pending_material_shares(self):
        import json
        if self._closed:
            return []
        native = self._jclass("org.example.kanamemory.KanaTextShareActivity")
        raw = str(native.peekPending(self._context))
        if len(raw) > 300000:
            raise PlatformError("分享文本过大，未读取。")
        return json.loads(raw)

    def acknowledge_material_share(self, identity):
        if not self._closed:
            self._jclass("org.example.kanamemory.KanaTextShareActivity").acknowledge(self._context, identity)

    def register_back_handler(self, callback):
        """Register one synchronous UI-thread callback returning bool handled.

        Android still owns IME/dialog dismissal. Replacing
        a callback removes the previous one; activity destruction also unregisters.
        Starts disabled; the app enables it even at the root to consume Back.
        """
        self._check_open()
        if not callable(callback):
            raise TypeError("Back handler must be callable and return a bool.")
        self.remove_back_handler()
        c = self._jclass

        class BackEvents(self._dynamic_proxy(c("org.example.kanamemory.KanaBackHandler$Handler"))):
            def onBack(self):
                try:
                    result = callback()
                    if not isinstance(result, bool):
                        raise TypeError("Back callback must return bool synchronously.")
                    return result
                except Exception as exc:
                    record_error("platform_back", "callback", exc)
                    return True

        activity = c("org.beeware.android.MainActivity").singletonThis
        if activity is None:
            raise PlatformError("Android 窗口尚未准备完成，无法注册返回处理。")
        self._back_events = BackEvents()
        self._back_native = c("org.example.kanamemory.KanaBackHandler")(activity, self._back_events)

    def set_back_enabled(self, enabled):
        """Enable on the UI thread only while an app page can handle Back."""
        self._check_open()
        if not isinstance(enabled, bool):
            raise TypeError("Back enabled state must be a bool.")
        if self._back_native is not None:
            self._back_native.setPageBackEnabled(enabled)

    def remove_back_handler(self):
        """Call on the UI thread; safe when no callback is registered."""
        if self._back_native is not None:
            self._back_native.unregister()
        self._back_native = self._back_events = None

    def link_kana_label(self, widget, spans, on_open):
        """Link only supplied content ranges on an existing Toga Label, on the UI thread.

        spans: dictionaries with Python character indexes start/end (end exclusive)
        and kana_id. on_open(kana_id) is synchronous. Returns True on Android.
        Reapply after assigning widget.text, which replaces Android text spans.
        Empty spans remove links previously installed by this method.
        """
        self._check_open()
        if not callable(on_open):
            raise TypeError("Kana link callback must be callable.")
        text = str(widget.text)
        offsets = _kana_span_offsets(text, spans)
        native = widget._impl.native
        original = getattr(widget, "_kana_link_original", None)
        if not offsets:
            if original is not None:
                widget._kana_link_state = None
                native.setText(text)
                movement, links, important, focusable, touch_focusable, clickable, long_clickable = original
                native.setMovementMethod(movement)
                native.setLinksClickable(links)
                native.setImportantForAccessibility(important)
                native.setFocusable(focusable)
                native.setFocusableInTouchMode(touch_focusable)
                native.setClickable(clickable)
                native.setLongClickable(long_clickable)
                del widget._kana_link_original
            return True

        if original is None:
            original = (
                native.getMovementMethod(), native.getLinksClickable(),
                native.getImportantForAccessibility(), native.isFocusable(),
                native.isFocusableInTouchMode(), native.isClickable(), native.isLongClickable(),
            )
        # The widget owns the callback, including temporary lambdas. The Java proxy
        # only holds weak references, so native spans cannot retain widget/app cycles.
        widget_ref, platform_ref = weakref.ref(widget), weakref.ref(self)
        token = object()
        c = self._jclass

        class LinkEvents(self._dynamic_proxy(c("org.example.kanamemory.KanaLinkSpan$Handler"))):
            def openKana(self, kana_id):
                owner, platform = widget_ref(), platform_ref()
                if owner is None or platform is None or platform._closed:
                    return
                state = getattr(owner, "_kana_link_state", None)
                if state is None or state["token"] is not token:
                    return
                try:
                    state["callback"](str(kana_id))
                except Exception as exc:
                    record_error("platform_link", "callback", exc)

        events = LinkEvents()
        linked_text = c("android.text.SpannableString")(text)
        span_type = c("org.example.kanamemory.KanaLinkSpan")
        flags = c("android.text.Spanned").SPAN_EXCLUSIVE_EXCLUSIVE
        for start, end, kana_id in offsets:
            linked_text.setSpan(span_type(kana_id, events), start, end, flags)
        widget._kana_link_original = original
        widget._kana_link_state = {"callback": on_open, "events": events, "token": token}
        native.setText(linked_text)
        native.setMovementMethod(c("android.text.method.LinkMovementMethod").getInstance())
        native.setLinksClickable(True)
        native.setImportantForAccessibility(c("android.view.View").IMPORTANT_FOR_ACCESSIBILITY_YES)
        return True

    async def preview_index(self, title, message, action="查看详情", *, pitch=None):
        """Show plain text with explicit action/Close buttons; cancellation is False.

        Runs on the Android UI event loop. This dialog never changes app navigation
        and never creates inline links, even if message contains kana or URLs.
        """
        if self._closed or self._index_preview is not None:
            return False
        c = self._jclass
        activity = c("org.beeware.android.MainActivity").singletonThis
        if activity is None or activity.isFinishing() or activity.isDestroyed():
            return False
        request = _IndexPreviewRequest(asyncio.get_running_loop())
        self._index_preview = request
        request_ref = weakref.ref(request)
        try:
            destroy_event = c("androidx.lifecycle.Lifecycle$Event").ON_DESTROY

            class Click(self._dynamic_proxy(c("android.content.DialogInterface$OnClickListener"))):
                def onClick(self, dialog, which):
                    pending = request_ref()
                    if pending is not None:
                        if int(which) == -3:
                            owner._report_index_preview(pending, title, message, action, pitch)
                        pending.resolve(int(which) == -1)

            class Cancel(self._dynamic_proxy(c("android.content.DialogInterface$OnCancelListener"))):
                def onCancel(self, dialog):
                    pending = request_ref()
                    if pending is not None:
                        pending.resolve(False)

            class Dismiss(self._dynamic_proxy(c("android.content.DialogInterface$OnDismissListener"))):
                def onDismiss(self, dialog):
                    pending = request_ref()
                    if pending is not None:
                        pending.resolve(False)

            class Destroy(self._dynamic_proxy(c("androidx.lifecycle.LifecycleEventObserver"))):
                def onStateChanged(self, source, event):
                    pending = request_ref()
                    if pending is not None and event == destroy_event:
                        pending.cancel()

            owner = self
            click, cancel, dismiss, destroy = Click(), Cancel(), Dismiss(), Destroy()
            request.listeners = (click, cancel, dismiss, destroy)
            builder = c("android.app.AlertDialog$Builder")(activity)
            builder.setTitle(str(title))
            if pitch:
                from .pitch_display import native_pitch_preview
                builder.setView(native_pitch_preview(self, str(message), pitch["record"], pitch["preferences"]))
            else:
                builder.setMessage(str(message))
            builder.setCancelable(True)
            builder.setPositiveButton(str(action), click)
            builder.setNegativeButton("关闭", click)
            if getattr(self, "feedback_callback", None):
                builder.setNeutralButton("反馈", click)
            dialog = builder.create()
            request.dialog = dialog
            dialog.setOnCancelListener(cancel)
            dialog.setOnDismissListener(dismiss)
            dialog.setCanceledOnTouchOutside(True)
            request.lifecycle = activity.getLifecycle()
            request.lifecycle_listener = destroy
            request.lifecycle.addObserver(destroy)
            if request.decided:
                return False
            dialog.show()
            message_view = dialog.findViewById(c("android.R$id").message)
            if message_view is not None:
                message_view.setAutoLinkMask(0)
                message_view.setLinksClickable(False)
                message_view.setMovementMethod(None)
            return bool(await request.future) and not self._closed
        except asyncio.CancelledError:
            request.cancel()
            return False
        except Exception as exc:
            request.cancel()
            record_error("platform_index", "callback", exc)
            raise PlatformError("无法打开索引预览，请稍后重试。") from exc
        finally:
            request.dispose()
            if self._index_preview is request:
                self._index_preview = None

    def _report_index_preview(self, request, title, message, action, pitch):
        overlay = {"kind": "index_preview", "title": str(title), "message": str(message),
                   "actions": [str(action), "关闭", "反馈"], "pitch": pitch}
        try:
            screenshot = self.capture_feedback_screenshot(request.dialog.getWindow().getDecorView())
        except Exception:
            screenshot = None
        request.loop.call_soon(lambda: self.feedback_callback(overlay=overlay, screenshot=screenshot))

    async def _await_task(self, task, *, timeout=60):
        self._check_open()
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        token = object()
        owner = self

        def settle(completed):
            owner._listeners.pop(token, None)
            if future.done():
                return
            if completed.isCanceled():
                future.set_exception(PlatformError("设备操作已取消，请重试。"))
            elif completed.isSuccessful():
                future.set_result(completed.getResult())
            else:
                exception = completed.getException()
                if exception is not None:
                    record_error("platform_model", "callback", exception)
                future.set_exception(PlatformError(_model_operation_error(exception)))

        class Completion(self._dynamic_proxy(self._jclass("com.google.android.gms.tasks.OnCompleteListener"))):
            def onComplete(self, completed):
                if not loop.is_closed():
                    loop.call_soon_threadsafe(settle, completed)

        listener = Completion()
        self._listeners[token] = (listener, future)
        task.addOnCompleteListener(listener)
        try:
            return await asyncio.wait_for(future, timeout)
        except TimeoutError as exc:
            # Google Task has no general cancellation API; a download may finish later.
            raise PlatformError("设备操作暂未完成，请稍后重新检查状态；若正在下载，下载可能仍在继续。") from exc

    async def initialize(self):
        self._check_open()
        if self._manager is None:
            try:
                c = self._jclass
                identifier = c("com.google.mlkit.vision.digitalink.recognition.DigitalInkRecognitionModelIdentifier").fromLanguageTag("ja")
                if identifier is None:
                    raise PlatformError("当前识别器没有日语手写模型。")
                self._model = c("com.google.mlkit.vision.digitalink.recognition.DigitalInkRecognitionModel").builder(identifier).build()
                options = c("com.google.mlkit.vision.digitalink.recognition.DigitalInkRecognizerOptions").builder(self._model).build()
                self._recognizer = c("com.google.mlkit.vision.digitalink.recognition.DigitalInkRecognition").getClient(options)
                self._manager = c("com.google.mlkit.common.model.RemoteModelManager").getInstance()
            except Exception as exc:
                self._model_state = "error"
                raise PlatformError(f"无法初始化日语手写识别：{exc}") from exc
        await self.model_status()

    async def model_status(self):
        self._check_open()
        if self._manager is None:
            await self.initialize()
            return self._model_state
        if self._download_lock.locked():
            return "downloading"
        try:
            self._model_state = "ready" if await self._is_model_downloaded() else "not_downloaded"
            return self._model_state
        except Exception:
            self._model_state = "error"
            raise

    async def _is_model_downloaded(self):
        available = await self._await_task(self._manager.isModelDownloaded(self._model))
        self._check_open()
        if hasattr(available, "booleanValue"):
            available = available.booleanValue()
        return bool(available)

    async def download_model(self):
        self._check_open()
        if self._manager is None:
            await self.initialize()
        async with self._download_lock:
            self._check_open()
            try:
                # A queued second tap or an offline, already prepared device must
                # not start an unnecessary server-side update check.
                if await self._is_model_downloaded():
                    self._model_state = "ready"
                    return
                self._model_state = "downloading"
                conditions = self._jclass("com.google.mlkit.common.model.DownloadConditions$Builder")().build()
                await self._await_task(self._manager.download(self._model, conditions), timeout=300)
                if not await self._is_model_downloaded():
                    raise PlatformError("下载已结束，但日语手写模型尚未可用。请重新检查模型状态或重试下载。")
                self._model_state = "ready"
            except asyncio.CancelledError:
                # Cancelling the Python waiter cannot cancel Google's download.
                # Clear our busy state; the next explicit check reads the device.
                self._model_state = "error"
                raise
            except Exception:
                self._model_state = "error"
                raise

    async def recognize(self, strokes, width, height):
        """Return ranked raw candidate strings. No confidence or correctness is invented."""
        from java import jfloat, jlong

        if await self.model_status() != "ready":
            raise ModelNotReady("请先下载日语手写模型，再提交笔迹。")
        width, height = float(width), float(height)
        if not (math.isfinite(width) and math.isfinite(height) and width > 0 and height > 0):
            raise PlatformError("手写区域尺寸无效。")
        points = [point for stroke in strokes for point in stroke]
        if not points:
            raise PlatformError("请先在手写区域写字。")
        if len(points) > 20000:
            raise PlatformError("笔迹过长，请清空后只写当前题目的假名。")
        c = self._jclass
        Ink = c("com.google.mlkit.vision.digitalink.recognition.Ink")
        Stroke = c("com.google.mlkit.vision.digitalink.recognition.Ink$Stroke")
        Point = c("com.google.mlkit.vision.digitalink.recognition.Ink$Point")
        builder = Ink.builder()
        first_time = float(points[0][2])
        previous_time = first_time
        for stroke in strokes:
            if not stroke:
                continue
            stroke_builder = Stroke.builder()
            for x, y, timestamp in stroke:
                x, y, timestamp = float(x), float(y), float(timestamp)
                if not all(math.isfinite(v) for v in (x, y, timestamp)) or timestamp < previous_time:
                    raise PlatformError("笔迹采样时间或坐标无效，请重新书写。")
                previous_time = timestamp
                stroke_builder.addPoint(Point.create(jfloat(x), jfloat(y), jlong(round(timestamp - first_time))))
            builder.addStroke(stroke_builder.build())
        area = c("com.google.mlkit.vision.digitalink.recognition.WritingArea")(jfloat(width), jfloat(height))
        context = (c("com.google.mlkit.vision.digitalink.recognition.RecognitionContext").builder()
                   .setWritingArea(area).setPreContext("").build())
        # 19.0 requires this property to be set. Always empty: no target answer.
        result = await self._await_task(self._recognizer.recognize(builder.build(), context))
        candidates = result.getCandidates()
        c("android.util.Log").i("KanaMemoryInk", f"recognition_completed candidates={candidates.size()}")
        return [str(candidates.get(i).getText()) for i in range(candidates.size())]

    async def prepare_tts(self):
        """Refresh installed offline Japanese, Mandarin and Hong Kong Cantonese."""
        self._check_open()
        async with self._tts_lock:
            self._check_open()
            # The settings page can install/remove voices or change the engine.
            # A user-triggered recheck must query it again, not return cached ready.
            self.stop()
            self.tts_voice = None
            self.tts_engine = None
            self.tts_status = "unavailable"
            self.tts_detail = "正在检查系统日语语音。"
            self._tts_preparing = True
            self._tts_audio_ready = False
            self.tts_voice_inventory = []
            self._voice_cache = None
            self._voice_candidates = {}
            for language in ("cmn", "yue"):
                self._set_voice_state(language, "unavailable", f"正在检查{VOICE_LABELS[language]}离线语音。")
            c = self._jclass
            TTS = c("android.speech.tts.TextToSpeech")
            loop = asyncio.get_running_loop()
            initialized = loop.create_future()

            def complete(status):
                if not initialized.done():
                    initialized.set_result(int(status))

            class InitListener(self._dynamic_proxy(c("android.speech.tts.TextToSpeech$OnInitListener"))):
                def onInit(self, status):
                    if not loop.is_closed():
                        loop.call_soon_threadsafe(complete, status)

            try:
                if self._tts is not None:
                    self._tts.shutdown()
                self._tts_listener = InitListener()
                self._tts = TTS(self._context, self._tts_listener)
                status = await asyncio.wait_for(initialized, 15)
                if self._closed:
                    self._tts.shutdown()
                    for language in ("cmn", "yue"):
                        self._set_voice_state(language, "unavailable", "语音服务已关闭。")
                    return "unavailable"
                if status != TTS.SUCCESS:
                    self.tts_status = "unavailable"
                    self.tts_detail = "系统没有可用的语音引擎，请安装支持日语的语音服务。"
                    for language in ("cmn", "yue"):
                        self._set_voice_state(language, "unavailable", "系统没有可用的语音引擎，请安装支持相应语言的语音服务。")
                    return self.tts_status
                engine = self._tts.getDefaultEngine()
                self.tts_engine = str(engine) if engine else None
                voices = self._read_voices()
                for language in ("cmn", "yue"):
                    candidates = self._voice_candidates[language]
                    if candidates:
                        profile = candidates[0][0]
                        self._set_voice_state(language, "ready", self._voice_demo_detail(language, profile.name), profile)
                    else:
                        self._set_voice_state(language, "not_installed", self._missing_voice_detail(language))
                # Chinese can remain usable even when the engine has no Japanese.
                if any(state["status"] == "ready" for state in self._chinese_tts.values()):
                    self._configure_audio_callbacks(loop)
                    self._tts_audio_ready = True
                locale = c("java.util.Locale").JAPAN
                availability = self._tts.isLanguageAvailable(locale)
                if availability < TTS.LANG_AVAILABLE:
                    self.tts_status = "not_installed" if availability == TTS.LANG_MISSING_DATA else "unavailable"
                    self.tts_detail = "系统尚未提供日语声音。请在应用设置点“安装日语语音数据”，选择日语／日本語；完成后点“重新检查声音”。"
                    return self.tts_status
                usable = self._voice_candidates["ja"]
                if not usable:
                    self.tts_status = "not_installed"
                    self.tts_detail = "未找到已安装的离线日语语音。请在应用设置点“安装日语语音数据”，选择日语／日本語；下载完成后返回并点“重新检查声音”。"
                    return self.tts_status
                self._select_tts_voice("ja", verify_inventory=False)
                if not self._tts_audio_ready:
                    self._configure_audio_callbacks(loop)
                    self._tts_audio_ready = True
            except asyncio.CancelledError:
                if self._tts is not None:
                    self._tts.shutdown()
                self.tts_status = "unavailable"
                self._tts_audio_ready = False
                for language in ("cmn", "yue"):
                    self._set_voice_state(language, "unavailable", "语音检查已取消，请重新检查声音。")
                raise
            except Exception as exc:
                self.tts_status = "error"
                self.tts_detail = f"日语语音初始化失败：{exc}"
                if not self._tts_audio_ready:
                    for language in ("cmn", "yue"):
                        self._set_voice_state(language, "error", f"系统语音初始化失败：{exc}")
            finally:
                self._tts_preparing = False
            return self.tts_status

    def chinese_voice_status(self, language="cmn"):
        """Return installed-voice capability, not a dictionary pronunciation guarantee."""
        chinese_language(language)
        result = dict(self._chinese_tts[language])
        result["engine"] = self.tts_engine
        if self._closed:
            result.update(status="unavailable", detail="语音服务已关闭。", voice=None, locale=None)
        return result

    async def prepare_chinese_tts(self):
        """Refresh the shared engine and all language inventories after installation."""
        await self.prepare_tts()
        return {language: self.chinese_voice_status(language) for language in ("cmn", "yue")}

    def _read_voices(self):
        if self._voice_cache is not None:
            return self._voice_cache
        voices = self._tts.getVoices()
        if voices is None:
            raise PlatformError("无法读取系统语音列表，请稍后重新检查声音。")
        iterator, result = voices.iterator(), []
        while iterator.hasNext():
            result.append(iterator.next())
        profiles = [(describe_voice(voice), voice) for voice in result]
        self.tts_voice_inventory = [profile.to_dict() for profile, _ in profiles]
        self._voice_candidates = {language: sorted(
            ((profile, voice) for profile, voice in profiles if profile.supports(language)),
            key=lambda item: (-item[0].quality, item[0].name)) for language in VOICE_LABELS}
        self._voice_cache = result
        return result

    @staticmethod
    def _voice_demo_detail(language, voice):
        if language == "ja":
            return f"系统合成音 · 日语 · {voice}"
        return "离线音包已就绪，可按字自动选音。"

    @staticmethod
    def _missing_voice_detail(language):
        return f"未找到已安装的离线{VOICE_LABELS[language]}声音。请打开系统语音数据安装入口下载对应声音，然后重新检查；不会使用其他语种替代。"

    def _set_voice_state(self, language, status, detail, profile=None):
        if language == "ja":
            self.tts_status, self.tts_detail = status, detail
            self.tts_voice = profile.name if profile else None
        else:
            self._chinese_tts[language] = {"status": status, "detail": detail,
                                           "voice": profile.name if profile else None,
                                           "locale": profile.locale if profile else None}

    def _select_tts_voice(self, language, voices=None, *, verify_inventory=True):
        if voices is None:
            self._read_voices()
            candidates = self._voice_candidates[language]
        else:
            candidates = rank_voices(voices, language)
        if not candidates:
            detail = self._missing_voice_detail(language)
            self._set_voice_state(language, "not_installed", detail)
            raise PlatformError(detail)
        profile, native_voice = candidates[0]
        if verify_inventory:
            # Installation can change outside our process. Recheck only this
            # candidate in the live inventory, keeping all-language metadata and
            # ranking cached. Never trust an old native Voice after removal.
            installed = self._tts.getVoices()
            current_voice = None
            if installed is not None:
                iterator = installed.iterator()
                while iterator.hasNext():
                    current = iterator.next()
                    if str(current.getName()) == profile.name:
                        current_voice = current
                        break
            if current_voice is None or not describe_voice(current_voice).supports(language):
                detail = self._missing_voice_detail(language)
                self._set_voice_state(language, "not_installed", detail)
                self._voice_candidates[language] = []
                raise PlatformError(detail)
            native_voice = current_voice
        if self._tts.setVoice(native_voice) != 0:
            detail = f"系统未能切换到{VOICE_LABELS[language]}语音，已停止朗读。"
            self._set_voice_state(language, "error", detail)
            raise PlatformError(detail)
        selected = self._tts.getVoice()
        actual = describe_voice(selected) if selected is not None else None
        if actual is None or actual.name != profile.name or not actual.supports(language):
            detail = f"系统没有保持指定的已安装离线{VOICE_LABELS[language]}声音，已停止朗读。"
            self._set_voice_state(language, "error", detail)
            raise PlatformError(detail)
        self._set_voice_state(language, "ready", self._voice_demo_detail(language, actual.name), actual)
        self._jclass("android.util.Log").i("KanaMemoryAudio",
            f"voice_selected language={language} voice={actual.name} locale={actual.locale} "
            f"network_required={str(actual.network_required).lower()} not_installed={str(actual.not_installed).lower()}")

    def _configure_audio_callbacks(self, loop):
        owner = self
        c = self._jclass

        def post(event, utterance_id, code=None):
            if not loop.is_closed():
                loop.call_soon_threadsafe(owner._on_tts_event, event, str(utterance_id), code)

        class SpeechEvents(self._dynamic_proxy(c("org.example.kanamemory.KanaTtsListener$Events"))):
            def onStart(self, utterance_id):
                post("start", utterance_id)

            def onDone(self, utterance_id):
                post("done", utterance_id)

            def onError(self, utterance_id, code):
                post("error", utterance_id, int(code))

            def onStop(self, utterance_id, interrupted):
                post("stop", utterance_id)

        self._progress_events = SpeechEvents()
        self._progress_listener = c("org.example.kanamemory.KanaTtsListener")(self._progress_events)
        if self._tts.setOnUtteranceProgressListener(self._progress_listener) != 0:
            raise PlatformError("无法监听语音播放完成状态。")
        attributes = c("android.media.AudioAttributes$Builder")().setUsage(1).setContentType(1).build()
        self._tts_audio_attributes = attributes
        self._tts.setAudioAttributes(attributes)
        self._audio_manager = self._context.getSystemService("audio")

    def _request_speech_focus(self, utterance_id):
        """A late focus-loss callback may stop only its own utterance."""
        c, loop, owner_ref = self._jclass, asyncio.get_running_loop(), weakref.ref(self)

        def stop_current():
            owner = owner_ref()
            if owner is not None and owner._current_utterance == utterance_id:
                owner.stop()

        class FocusListener(self._dynamic_proxy(c("android.media.AudioManager$OnAudioFocusChangeListener"))):
            def onAudioFocusChange(self, change):
                if int(change) < 0 and not loop.is_closed():
                    loop.call_soon_threadsafe(stop_current)

        self._focus_listener = FocusListener()
        self._focus_request = (c("android.media.AudioFocusRequest$Builder")(2)
                               .setAudioAttributes(self._tts_audio_attributes)
                               .setAcceptsDelayedFocusGain(False)
                               .setOnAudioFocusChangeListener(self._focus_listener)
                               .build())
        if self._audio_manager.requestAudioFocus(self._focus_request) != 1:
            self._focus_request = self._focus_listener = None
            raise PlatformError("暂时无法获得音频播放权限，请稍后重播。")

    def _on_tts_event(self, event, utterance_id, code):
        future = self._utterances.get(utterance_id)
        if future is None:
            return
        # Diagnostics deliberately exclude spoken text and pen coordinates.
        self._jclass("android.util.Log").i("KanaMemoryAudio", f"tts_{event} code={code}")
        if event == "start":
            self.last_audio_status = "playing"
            return
        self._utterances.pop(utterance_id, None)
        language = self._utterance_languages.pop(utterance_id)
        if utterance_id == self._current_utterance:
            self._current_utterance = None
            if self._audio_manager is not None and self._focus_request is not None:
                try:
                    self._audio_manager.abandonAudioFocusRequest(self._focus_request)
                except Exception as exc:
                    record_error("platform_tts", "cleanup", exc)
            self._focus_request = self._focus_listener = None
        if future.done():
            return
        if event == "done":
            self.last_audio_status = "completed"
            future.set_result(None)
        else:
            self.last_audio_status = "stopped" if event == "stop" else "error"
            if code == -9:
                self._set_voice_state(language, "not_installed", f"{VOICE_LABELS[language]}语音数据尚未完成下载，请安装后重新检查声音。")
            message = "朗读已停止，请重播。" if event == "stop" else f"系统语音播放失败（代码 {code}），请检查{VOICE_LABELS[language]}语音设置。"
            future.set_exception(PlatformError(message))

    def _begin_speech(self, text, language="ja"):
        self._check_open()
        if language != "ja":
            chinese_language(language)
        # A requested language supersedes the previous sound even if its voice
        # pack is missing. Otherwise a failed Cantonese request can leave Japanese
        # audibly playing behind its error, which sounds like a language fallback.
        self.stop()
        if self._tts_preparing:
            raise PlatformError("正在重新检查声音，请检查完成后重播。")
        if language == "ja":
            status, detail = self.tts_status, self.tts_detail
        else:
            state = self.chinese_voice_status(language)
            status, detail = state["status"], state["detail"]
        if status != "ready" or not self._tts_audio_ready:
            raise PlatformError(detail)
        if not isinstance(text, str) or not text.strip():
            raise PlatformError(f"没有可朗读的{VOICE_LABELS[language]}文本。")
        TTS = self._jclass("android.speech.tts.TextToSpeech")
        if len(text.encode("utf-16-le")) // 2 > TTS.getMaxSpeechInputLength():
            raise PlatformError("朗读文本超过系统长度限制。")
        # The engine is shared. Always reselect and verify before queuing, including
        # Japanese after Chinese; never rely on a previous language availability.
        self._select_tts_voice(language)
        utterance_id = str(uuid4())
        self._request_speech_focus(utterance_id)
        future = asyncio.get_running_loop().create_future()
        # A caller may use the fire-and-forget speak() interface. Consume its
        # exception while leaving the same result available to await play().
        future.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        self._utterances[utterance_id] = future
        self._utterance_languages[utterance_id] = language
        self._current_utterance = utterance_id
        self.last_audio_status = "queued"
        try:
            result = self._tts.speak(text, TTS.QUEUE_FLUSH, self._jclass("android.os.Bundle")(), utterance_id)
        except Exception as exc:
            self._on_tts_event("error", utterance_id, None)
            raise PlatformError("系统未能开始朗读，请重新检查声音。") from exc
        if result != TTS.SUCCESS:
            self._on_tts_event("error", utterance_id, int(result))
        return utterance_id, future

    async def play(self, text):
        """Complete only after onDone; an accepted queue item is not playback success."""
        await self._play_speech(text, "ja")

    async def play_chinese(self, text, language="cmn"):
        """System character-reading demonstration, not dictionary-syllable audio."""
        chinese_language(language)
        await self._play_speech(text, language)

    async def _play_speech(self, text, language):
        utterance_id, future = self._begin_speech(text, language)
        try:
            await asyncio.wait_for(asyncio.shield(future), min(90, max(15, len(text) * 0.8 + 8)))
        except TimeoutError as exc:
            if self._current_utterance == utterance_id:
                self.stop()
            raise PlatformError(f"朗读未能按时完成，请检查{VOICE_LABELS[language]}语音数据后重试。") from exc
        except asyncio.CancelledError:
            if self._current_utterance == utterance_id:
                self.stop()
            raise

    async def play_recording(self, path):
        """Play an attributed local file, completing only on MediaPlayer completion."""
        self._check_open()
        path = Path(path)
        if not path.is_file():
            raise PlatformError("录音文件不在安装包中。")
        self.stop()
        c, loop = self._jclass, asyncio.get_running_loop()
        future = loop.create_future()
        future.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        try:
            player = c("android.media.MediaPlayer")()
        except Exception as exc:
            self.last_audio_status = "error"
            raise PlatformError("无法创建录音播放器。") from exc
        state = {"player":player, "future":future, "loop":loop, "manager":None,
                 "focus":None, "focus_granted":False, "listeners":(), "settled":False}
        self._recording = state
        self.last_audio_status = "queued"

        def complete(error=None):
            if self._recording is not state or state["settled"]:
                return
            state["settled"] = True
            def finish():
                if future.done():
                    return
                if error:
                    self.last_audio_status = "error"
                    future.set_exception(PlatformError(error))
                else:
                    self.last_audio_status = "completed"
                    future.set_result(None)
                    try:
                        c("android.util.Log").i("KanaMemoryAudio", "recording_done asset=" + path.name)
                    except Exception as exc:
                        record_error("platform_recording", "callback", exc)
            if not loop.is_closed(): loop.call_soon_threadsafe(finish)

        class Prepared(self._dynamic_proxy(c("android.media.MediaPlayer$OnPreparedListener"))):
            def onPrepared(self, native):
                def start():
                    if (owner._recording is not state or state["settled"] or future.done()
                            or (state["player"] is not native and state["player"] != native)):
                        return
                    try:
                        native.start()
                    except Exception as exc:
                        record_error("platform_recording", "play", exc)
                        complete("录音无法开始播放。")
                        return
                    owner.last_audio_status = "playing"
                    try:
                        c("android.util.Log").i("KanaMemoryAudio", "recording_start asset=" + path.name)
                    except Exception as exc:
                        record_error("platform_recording", "callback", exc)
                if not loop.is_closed(): loop.call_soon_threadsafe(start)

        class Completed(self._dynamic_proxy(c("android.media.MediaPlayer$OnCompletionListener"))):
            def onCompletion(self, native):
                complete()

        class Failed(self._dynamic_proxy(c("android.media.MediaPlayer$OnErrorListener"))):
            def onError(self, native, what, extra):
                complete(f"录音播放失败（{int(what)} / {int(extra)}）。")
                return True

        class Focus(self._dynamic_proxy(c("android.media.AudioManager$OnAudioFocusChangeListener"))):
            def onAudioFocusChange(self, change):
                if int(change) < 0:
                    complete("录音被其他声音打断，请重播。")
        owner = self
        try:
            prepared, completed, failed, focus = Prepared(), Completed(), Failed(), Focus()
            state["listeners"] = (prepared, completed, failed, focus)
            attributes = c("android.media.AudioAttributes$Builder")().setUsage(1).setContentType(1).build()
            request = (c("android.media.AudioFocusRequest$Builder")(2).setAudioAttributes(attributes)
                       .setAcceptsDelayedFocusGain(False).setOnAudioFocusChangeListener(focus).build())
            manager = self._context.getSystemService("audio")
            state["manager"], state["focus"] = manager, request
            if manager.requestAudioFocus(request) != 1:
                raise PlatformError("暂时无法获得录音播放权限。")
            state["focus_granted"] = True
            player.setAudioAttributes(attributes)
            player.setOnPreparedListener(prepared)
            player.setOnCompletionListener(completed)
            player.setOnErrorListener(failed)
            player.setDataSource(str(path))
            player.prepareAsync()
            await asyncio.wait_for(asyncio.shield(future), 90)
        except TimeoutError as exc:
            if self._recording is state:
                self.last_audio_status = "error"
            raise PlatformError("录音未能按时播放完成，请重播。") from exc
        except asyncio.CancelledError:
            raise
        except PlatformError:
            if self._recording is state and self.last_audio_status != "stopped":
                self.last_audio_status = "error"
            raise
        except Exception as exc:
            if self._recording is state:
                self.last_audio_status = "error"
            raise PlatformError("无法读取或播放这段录音。") from exc
        finally:
            if self._recording is state:
                self._stop_recording()

    def _stop_recording(self):
        state, self._recording = self._recording, None
        if state is None:
            return
        state["settled"] = True
        future = state["future"]
        if not future.done():
            if self.last_audio_status != "error":
                self.last_audio_status = "stopped"
            future.set_exception(PlatformError("录音已停止，请重播。"))
        player, state["player"] = state["player"], None
        if player is not None:
            for method in ("setOnPreparedListener", "setOnCompletionListener", "setOnErrorListener", "release"):
                try:
                    getattr(player, method)(*(() if method == "release" else (None,)))
                except Exception as exc:
                    record_error("platform_recording", "cleanup", exc)
        if state["manager"] is not None and state["focus"] is not None and state["focus_granted"]:
            try:
                state["manager"].abandonAudioFocusRequest(state["focus"])
            except Exception as exc:
                record_error("platform_recording", "cleanup", exc)
        state["listeners"] = ()

    def speak(self, text):
        """Queue playback. Prefer await play() wherever success affects an exercise."""
        self._begin_speech(text)

    def stop(self):
        if getattr(self, "_recording", None) is not None:
            self._stop_recording()
        if self._tts is not None:
            try:
                self._tts.stop()
            except Exception as exc:
                record_error("platform_tts", "cleanup", exc)
        for utterance_id in list(self._utterances):
            self._on_tts_event("stop", utterance_id, None)

    def open_url(self, url):
        if urlsplit(url).scheme not in {"http", "https"}:
            raise PlatformError("仅支持打开网页来源链接。")
        Intent = self._jclass("android.content.Intent")
        intent = Intent(Intent.ACTION_VIEW, self._jclass("android.net.Uri").parse(url))
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        try:
            self._context.startActivity(intent)
        except Exception as exc:
            raise PlatformError("无法打开链接，请确认系统中有浏览器。") from exc

    def open_tts_installation(self):
        """Open the default engine's installer; returning does not imply success."""
        self._check_open()
        engine = self.tts_engine
        if self._tts is not None:
            try:
                # The user may have changed the default engine in system settings.
                default_engine = self._tts.getDefaultEngine()
                engine = str(default_engine) if default_engine else None
            except Exception:
                pass
        try:
            Intent = self._jclass("android.content.Intent")
            intent = Intent("android.speech.tts.engine.INSTALL_TTS_DATA")
            if engine:
                intent.setPackage(engine)
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            self._context.startActivity(intent)
        except Exception:
            self.open_tts_settings()

    def open_tts_settings(self):
        """Try the Android TTS settings activity, then the public accessibility page."""
        Intent = self._jclass("android.content.Intent")
        for action in ("com.android.settings.TTS_SETTINGS", "android.settings.ACCESSIBILITY_SETTINGS"):
            try:
                intent = Intent(action)
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                self._context.startActivity(intent)
                return
            except Exception:
                continue
        raise PlatformError("无法打开语音设置，请从系统设置进入文字转语音并下载相应语言声音。")

    def register_activity_visibility(self, callback):
        """Report real Android foreground transitions with capture-time clocks."""
        from time import monotonic
        previous = getattr(self, '_activity_lifecycle', None)
        if previous is not None:
            previous.removeObserver(self._activity_observer)
        c = self._jclass
        activity = c('org.beeware.android.MainActivity').singletonThis
        events = c('androidx.lifecycle.Lifecycle$Event')
        owner = self
        class Observer(self._dynamic_proxy(c('androidx.lifecycle.LifecycleEventObserver'))):
            def onStateChanged(self, source, event):
                if owner._closed: return
                if event == events.ON_STOP: callback(False, monotonic())
                elif event == events.ON_START: callback(True, monotonic())
        self._activity_observer = Observer()
        self._activity_lifecycle = activity.getLifecycle()
        self._activity_lifecycle.addObserver(self._activity_observer)

    def close(self):
        if self._closed:
            return
        self._closed = True
        lifecycle = getattr(self, '_activity_lifecycle', None)
        if lifecycle is not None:
            lifecycle.removeObserver(self._activity_observer)
            self._activity_lifecycle = self._activity_observer = None
        preview, self._index_preview = self._index_preview, None
        if preview is not None:
            preview.cancel()
        backup, self._backup_native = getattr(self, "_backup_native", None), None
        if backup is not None:
            backup.cancel()
        self.remove_back_handler()
        self.stop()
        if self._tts is not None:
            self._tts.shutdown()
        if self._recognizer is not None:
            self._recognizer.close()
        self.tts_status = "unavailable"
        self._tts_audio_ready = False
        self._voice_cache = None
        self._voice_candidates = {}
        for _listener, future in self._listeners.values():
            if not future.done():
                future.cancel()
        self._listeners.clear()


def create_platform(context=None):
    return AndroidPlatform(context)

