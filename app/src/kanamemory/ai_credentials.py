"""Platform protected storage; credentials are never app_setting values."""
from pathlib import Path
import os
import re

from .ai_service import AIServiceError, validate_api_key


def normalize_pasted_api_key(value):
    """Accept one copied key or env assignment; never evaluate pasted content.

    Whitespace from line wrapping is harmless inside a single key. Multiple
    keys, other assignments and annotations remain invalid instead of choosing
    an arbitrary credential from the pasted text.
    """
    if not isinstance(value, str) or len(value) > 4096:
        return validate_api_key(None if value is None else False)
    text = value.strip().lstrip("\ufeff").strip()
    assignment = re.fullmatch(r"(?:export\s+)?OPENAI_API_KEY\s*=\s*(.*)", text, re.DOTALL)
    if assignment:
        text = assignment[1].strip()
    pairs = {'"': '"', "'": "'", '“': '”', '‘': '’'}
    if text and text[0] in pairs and text[-1:] == pairs[text[0]]:
        text = text[1:-1].strip()
    # Do not concatenate two separately copied credentials. An ordinary key
    # can legitimately contain the sequence "sk-" within its random body.
    if re.search(r"[\s\u200b\ufeff]+sk-", text):
        return validate_api_key(False)
    text = re.sub(r"[\s\u200b\ufeff]+", "", text)
    return validate_api_key(text)


def _dpapi(data, decrypt=False):
    import ctypes
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = Blob(len(data), buffer)
    output = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise AIServiceError("credentials", "无法使用系统安全存储，请重新连接 AI 服务。")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


class DeviceSecrets:
    def __init__(self, app, slot="openai_api_key"):
        if slot != "openai_api_key" and not re.fullmatch(r"endpoint_[a-f0-9]{64}", slot):
            raise AIServiceError("credentials", "无效的服务凭据位置。")
        self._slot = slot
        self._context = getattr(app.platform, "_context", None)
        self._path = Path(app.paths.data) / ("ai-openai-credential.bin" if slot == "openai_api_key" else "ai-" + slot + ".bin")

    def _android(self):
        from java import jclass
        return jclass("org.example.kanamemory.KanaAISecrets")

    def get(self):
        try:
            if self._context is not None:
                return str(self._android().get(self._context, self._slot))
            if os.name == "nt":
                return _dpapi(self._path.read_bytes(), True).decode("utf-8") if self._path.exists() else ""
            return ""
        except Exception:
            raise AIServiceError("credentials", "本机 AI 连接凭据不可用，请重新连接。") from None

    def put(self, token):
        try:
            if self._context is not None:
                self._android().put(self._context, self._slot, token)
            elif os.name == "nt":
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._path.write_bytes(_dpapi(token.encode("utf-8")))
            else:
                raise AIServiceError("credentials", "此平台暂不支持安全保存 AI 连接。")
        except Exception:
            raise AIServiceError("credentials", "无法安全保存 AI 连接，请重试。") from None

    def remove(self):
        try:
            if self._context is not None:
                self._android().remove(self._context, self._slot)
            else:
                self._path.unlink(missing_ok=True)
        except Exception:
            raise AIServiceError("credentials", "无法删除本机 AI 连接，请重试。") from None

    def prepare_import(self):
        if self._slot != "openai_api_key":
            raise AIServiceError("credentials", "安全导入助手仅用于 OpenAI 官方服务；其他服务请在本机填写密钥。")
        if self._context is None:
            raise AIServiceError("device", "安全导入用于 Android 手机。")
        try:
            self._android().prepareImport(self._context)
        except Exception:
            raise AIServiceError("credentials", "无法准备安全导入，可改用本机密钥输入。") from None

    def complete_import(self):
        if self._slot != "openai_api_key":
            raise AIServiceError("credentials", "安全导入助手仅用于 OpenAI 官方服务；其他服务请在本机填写密钥。")
        if self._context is None:
            raise AIServiceError("device", "安全导入用于 Android 手机。")
        try:
            self._android().completeImport(self._context)
        except Exception:
            raise AIServiceError("credentials", "导入未完成或已过期，请重新准备并传入加密文件。") from None
