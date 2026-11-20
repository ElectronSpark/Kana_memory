"""Opening a content library file from outside the app (design 08 section 16.8): the decisions.

A library pack has the extension ``.kmlib`` (older packs named ``.zip`` stay loadable: a pack is
accepted by its content, whatever its name). Android offers the app for such a file (intent filters
in app/pyproject.toml); tapping one starts or fronts the app, which shows the content library page
with that file's precheck, exactly as after picking it with 添加内容库. Opening never installs.

This module holds only decisions and state, no Android or Toga import:

- classify(): whether an ACTION_VIEW intent's fields are a request to open a library file (own MIME
  type, application/octet-stream, or a path ending in .kmlib; content or file scheme), and never one
  relaunched from the recents list (FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY) or already handled;
- named(): the display name (the provider's, else the path's last part);
- Requests: per App, requests arriving before the app is fully started wait (the newest one), and
  each request goes to the handler exactly once;
- busy(): when the app cannot leave what it is doing, the request is dropped with BUSY_NOTICE.

The few lines that read an Android Intent are platform_android.library_file_request; the page side
is ContentLibraryUIMixin.open_content_library_file.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from urllib.parse import unquote

EXTENSION = ".kmlib"
LEGACY_EXTENSION = ".zip"
MIME_TYPE = "application/vnd.kanamemory.library"
OCTET_STREAM = "application/octet-stream"
# The in-app picker (添加内容库): a .kmlib file is application/octet-stream to Android's providers
# (unknown extension); .zip packs keep their zip types. No text or JSON documents.
PICKER_TYPES = (OCTET_STREAM, "application/zip", "application/x-zip-compressed", MIME_TYPE)
ACTION_VIEW = "android.intent.action.VIEW"
SCHEMES = ("content", "file")
FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY = 0x00100000
# Put on an Intent once it was read as a request: a second delivery of it, or the window Android
# re-creates around it in the same process, reads it as handled.
HANDLED_EXTRA = "kanamemory.library_file_handled"

FILE_CHOICE = "内容库文件（.kmlib，旧的 .zip 也可以）"
BUSY_NOTICE = "请先完成当前操作，再重新打开这个内容库文件。"
FILE_URI_UNREADABLE = "Android 不允许本应用按文件路径读取这个文件。请在 设置 → 内容库 → 添加内容库 中选择它。"
NO_NAME = "（无文件名）"
MAX_NAME = 120

# Which filter of the manifest an intent matches (classify's OpenRequest.match).
OWN_TYPE, OCTET, PATH_SUFFIX = "own_type", "octet_stream", "path_suffix"


@dataclass(frozen=True)
class IntentFields:
    """What the decisions need of an Intent, as plain values."""
    action: str | None
    uri: str | None
    scheme: str | None
    mime_type: str | None
    path: str | None
    flags: int = 0
    handled: bool = False


@dataclass(frozen=True)
class OpenRequest:
    uri: str
    scheme: str
    match: str
    name: str = NO_NAME


def classify(fields):
    """The OpenRequest an intent carries, or None (not a VIEW of a library file, relaunched from the
    recents list, or already handled)."""
    if fields.action != ACTION_VIEW or not fields.uri or fields.handled:
        return None
    if int(fields.flags or 0) & FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY:
        return None
    scheme = (fields.scheme or "").lower()
    if scheme not in SCHEMES:
        return None
    mime_type = (fields.mime_type or "").split(";")[0].strip().lower()
    if mime_type == MIME_TYPE:
        match = OWN_TYPE
    elif (fields.path or "").lower().endswith(EXTENSION):
        match = PATH_SUFFIX
    elif mime_type == OCTET_STREAM:
        match = OCTET
    else:
        return None
    return OpenRequest(uri=fields.uri, scheme=scheme, match=match, name=display_name(None, fields.path))


def display_name(provided, path):
    """The provider's name when it has one, else the path's last part; control characters dropped, bounded."""
    for candidate in (provided, unquote(path or "").rstrip("/").rsplit("/", 1)[-1]):
        text = "".join(ch for ch in str(candidate or "") if ch.isprintable()).strip()
        if text:
            return text[:MAX_NAME]
    return NO_NAME


def named(request, provided):
    """The request with the provider's display name (``provided``) when it gives one."""
    if request is None or display_name(provided, None) == NO_NAME:
        return request
    return replace(request, name=display_name(provided, None))


def with_name(name, message):
    """A message about one opened file, preceded by its name."""
    return f"{name}：{message}" if name else message


def busy(*, blocked_reason=None, choosing=False, working=False, confirming=False, navigation_hidden=False,
         unsaved=False):
    """Whether a request to open a file must be dropped (with BUSY_NOTICE): the guards that stop moving to
    another page today. ``blocked_reason``: the libraries page's own refusal (backup or restore, a practice
    round, a focused page); ``choosing``: a library file is being chosen or checked; ``working``: the
    libraries page shows its status block (a library file is read, checked or installed); ``confirming``: a
    leave confirmation is open; ``navigation_hidden``: the page hides the navigation bar (an answer, a lesson, a diagnostic, a report being written);
    ``unsaved``: a draft could not be saved."""
    return bool(blocked_reason or choosing or working or confirming or navigation_hidden or unsaved)


class Requests:
    """One App's requests: those before the app runs wait (the newest), then each goes to the handler once."""

    def __init__(self):
        self.handler = None
        self.pending = None

    def deliver(self, request, warm=False):
        """A request read from an intent (cold: the launching intent; warm: delivered to the running
        activity). Returns "queued" (the app is not fully started yet) or "handled"."""
        if request is None:
            return None
        if self.handler is None:
            self.pending = request
            return "queued"
        self.handler(request)
        return "handled"

    def start(self, handler):
        """The app is fully started: from now on requests go to ``handler(request)``; a waiting one goes now."""
        self.handler = handler
        request, self.pending = self.pending, None
        if request is not None:
            handler(request)

    def stop(self):
        self.handler = None
        self.pending = None


def of(app):
    """The App's Requests (created on first use)."""
    state = getattr(app, "_library_file_open", None)
    if state is None:
        state = Requests()
        app._library_file_open = state
    return state
