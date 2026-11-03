"""Desktop development adapter: browsing works, Android services are explicit gaps."""

from urllib.parse import urlsplit
import asyncio
import webbrowser
import weakref
from pathlib import Path

from .voice_policy import VOICE_LABELS, chinese_language


_CLIPBOARD_MAX_UTF16_BYTES = 256 * 1024


def _pitch_preview_text(pitch):
    """Plain text fallback for desktop's native confirmation dialog.

    A pair of lines retains the above-kana notation even on platforms whose
    message dialog cannot host custom widgets. Wrap whole morae explicitly so
    long words cannot separate the marks from their corresponding kana row.
    """
    if not isinstance(pitch, dict):
        return ""
    from .pitch_display import clean_pitch_preferences, pitch_display_variants

    settings = clean_pitch_preferences(pitch.get("preferences"))
    if not any(settings.values()):
        return ""
    variants = pitch_display_variants(pitch.get("record"))
    if not variants:
        return "音高资料未收录"

    def caption(variant):
        qualifier = variant.get("qualifier") or ""
        source_form = variant.get("display_source_form") or ""
        return (f"[{variant['position']}]" + (f"（{qualifier}）" if qualifier else "")
                + (f"「{source_form}」" if source_form else ""))

    if not settings["contour"]:
        return "调号 " + " · ".join(caption(variant) for variant in variants)
    output = []
    for variant in variants:
        lines = []
        if settings["number"]:
            lines.append("调号 " + caption(variant))
        else:
            source_form = variant.get("display_source_form") or ""
            qualifier = " · ".join(value for value in (
                f"「{source_form}」" if source_form else "", variant.get("qualifier") or "") if value)
            if qualifier:
                lines.append(qualifier)
        marks, kana, occupied = [], [], 0
        for mora, mark in zip(variant["morae"], variant["levels"]):
            # Kana occupy two monospace columns; each complete mora gets a
            # centered ASCII mark and two columns of separation from its peers.
            width = 2 * len(mora)
            if marks and occupied + width + 2 > 32:
                lines.extend(("  ".join(marks).rstrip(), "  ".join(kana)))
                marks, kana, occupied = [], [], 0
            marks.append(mark.center(width))
            kana.append(mora)
            occupied += width + (2 if len(marks) > 1 else 0)
        if marks:
            lines.extend(("  ".join(marks).rstrip(), "  ".join(kana)))
        output.append("\n".join(lines))
    return "\n\n".join(output)


class PlatformError(RuntimeError):
    pass


class ModelNotReady(PlatformError):
    pass


class DesktopPlatform:
    name = "desktop"
    tts_status = "unavailable"
    tts_ready = False
    tts_voice = None
    tts_detail = "桌面预览未接入日语语音；请在 Android 模拟器或手机中练习听力。"

    def __init__(self):
        self._back_handler = None
        self._back_enabled = False
        self._closed = False
        self._preview_task = None
        self.tts_voice_inventory = []
        self.tts_engine = None

    def register_back_handler(self, callback):
        """Keep the shared lifecycle API; desktop has no Android system Back key."""
        if not callable(callback):
            raise TypeError("Back handler must be callable and return a bool.")
        self._back_handler = callback
        self._back_enabled = False

    def set_back_enabled(self, enabled):
        """Mirror navigation state without intercepting desktop system keys."""
        if not isinstance(enabled, bool):
            raise TypeError("Back enabled state must be a bool.")
        self._back_enabled = enabled and self._back_handler is not None

    def remove_back_handler(self):
        self._back_handler = None
        self._back_enabled = False

    def link_kana_label(self, widget, spans, on_open):
        """No inline native links here; the caller can show content-only buttons."""
        return False

    async def preview_index(self, title, message, action="查看详情", *, pitch=None):
        """Desktop fallback uses Toga's native OK/Cancel window-modal dialog."""
        if self._closed or self._preview_task is not None:
            return False
        import toga

        window = toga.App.app.current_window
        if window is None:
            return False
        annotation = _pitch_preview_text(pitch)
        if annotation:
            message = str(message) + "\n\n" + annotation
        hint = f"\n\n选择“确定 / OK”{action}；选择“取消 / Cancel”关闭。"
        task = asyncio.create_task(window.dialog(toga.ConfirmDialog(str(title), str(message) + hint)))
        self._preview_task = task
        owner_ref = weakref.ref(self)

        def finished(completed):
            owner = owner_ref()
            if owner is not None and owner._preview_task is completed:
                owner._preview_task = None
            if not completed.cancelled():
                completed.exception()  # Retrieve errors if the caller was cancelled.

        task.add_done_callback(finished)
        try:
            # Shield Toga's own Future: WinForms unconditionally completes it when
            # its native MessageBox closes, including after a caller is cancelled.
            accepted = await asyncio.shield(task)
            return bool(accepted) and not self._closed
        except asyncio.CancelledError:
            return False

    async def initialize(self):
        return None

    async def export_backup(self, data: bytes, filename="KanaMemory-learning.json"):
        import toga
        window = toga.App.app.current_window
        if self._closed or window is None:
            return False
        path = await window.dialog(toga.SaveFileDialog("导出学习记录", suggested_filename=filename, file_types=["json"]))
        if path is None or self._closed:
            return False
        await asyncio.to_thread(Path(path).write_bytes, data)
        return True

    async def export_feedback(self, data: bytes, filename="KanaMemory-feedback.zip"):
        import toga
        window = toga.App.app.current_window
        if self._closed or window is None:
            return False
        path = await window.dialog(toga.SaveFileDialog("导出反馈", suggested_filename=filename, file_types=["zip"]))
        if path is None or self._closed:
            return False
        await asyncio.to_thread(Path(path).write_bytes, data)
        return True

    async def copy_text(self, text: str) -> bool:
        """Use WinForms on Toga's STA thread, without reading the clipboard."""
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
            from System.Windows.Forms import Clipboard, TextDataFormat

            # SetText requires STA. Do not dispatch to asyncio.to_thread, whose
            # workers are MTA; native rejection must not count as an export.
            Clipboard.SetText(text, TextDataFormat.UnicodeText)
        except Exception as exc:
            raise PlatformError("未能复制到剪贴板，请重试或导出 ZIP。") from exc
        return True

    async def import_backup(self):
        import toga
        from .progress_backup import MAX_BYTES
        window = toga.App.app.current_window
        if self._closed or window is None:
            return None
        path = await window.dialog(toga.OpenFileDialog("选择学习记录备份", file_types=["json"]))
        if path is None or self._closed:
            return None
        def read():
            with Path(path).open("rb") as stream:
                data = stream.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise PlatformError("备份超过 64 MiB 限制。")
            return data
        return await asyncio.to_thread(read)

    def pending_material_shares(self):
        return []

    def acknowledge_material_share(self, identity):
        return None

    async def import_material_text(self):
        import toga
        from .material_input import MAX_BYTES
        window = toga.App.app.current_window
        if self._closed or window is None:
            return None
        path = await window.dialog(toga.OpenFileDialog("选择 UTF-8 日文文本", file_types=["txt"]))
        if path is None or self._closed:
            return None
        def read():
            with Path(path).open("rb") as stream:
                data = stream.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise PlatformError("文本文件最多 16,000 字节，请分段导入。")
            return data
        return await asyncio.to_thread(read)

    async def model_status(self):
        return "unavailable"

    async def download_model(self):
        raise PlatformError("真实日语手写识别在 Android 模拟器或手机中运行。")

    async def recognize(self, strokes, width, height):
        raise ModelNotReady("桌面预览不伪造手写识别结果，请使用 Android 版本。")

    async def prepare_tts(self):
        return self.tts_status

    def chinese_voice_status(self, language="cmn"):
        chinese_language(language)
        detail = ("语音服务已关闭。" if self._closed else
                  f"桌面预览未接入{VOICE_LABELS[language]}语音；请在 Android 模拟器或手机中检查离线声音。")
        return {"status": "unavailable", "detail": detail, "voice": None, "locale": None, "engine": None}

    async def prepare_chinese_tts(self):
        return {language: self.chinese_voice_status(language) for language in ("cmn", "yue")}

    async def play_chinese(self, text, language="cmn"):
        raise PlatformError(self.chinese_voice_status(language)["detail"])

    def speak(self, text):
        raise PlatformError(self.tts_detail)

    async def play(self, text):
        raise PlatformError(self.tts_detail)

    async def play_recording(self, path):
        raise PlatformError("录音已随包保存；请在 Android 版本播放。")

    def stop(self):
        pass

    def close(self):
        self._closed = True
        self.remove_back_handler()

    def open_url(self, url):
        if urlsplit(url).scheme not in {"http", "https"}:
            raise PlatformError("仅支持打开网页来源链接。")
        if not webbrowser.open(url):
            raise PlatformError("无法打开系统浏览器。")

    def open_tts_settings(self):
        raise PlatformError("请在 Android 模拟器或手机的系统设置中下载相应语言声音。")

    def open_tts_installation(self):
        raise PlatformError("请在 Android 模拟器或手机中安装相应语言语音数据。")


def create_platform(context=None):
    return DesktopPlatform()
