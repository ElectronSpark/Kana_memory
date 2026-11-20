"""The content library page's own state (design 08 sections 5.2 and 16.8): what it is doing now, and why
the app did not restart by itself.

- A library file is read, inspected or installed: the page shows a status block at its top (a moving
  indicator, the phase in words, the file's name, WAIT_NOTE) and offers no action that would start another
  library operation. ``phase`` is the sentence now shown (None: no work); ``label`` the control showing it,
  so a later phase changes its text in place without building the page again.
- After a saved change that needs a restart the app restarts at once through 立即重启's path; ``note`` is
  the sentence saying why it did not, shown after today's note until the page is left. ``slot`` is the
  notice box of the libraries page now shown (None when nothing is pending).

Only state and texts here; content_library_ui draws them and does the work.
"""
from __future__ import annotations

READING = "正在读取内容库文件…"
CHECKING = "正在检查内容库文件（校验并与已安装的内容比对）…"
INSTALLING = "正在安装内容库，完成后自动重启…"
WAIT_NOTE = "文件较大时需要几十秒，请稍候。"
RESTARTING = "变更已保存，正在重启应用…"
# How long the page waits after showing a status (or the restart notice) before the work starts, so the
# window draws it first. Long enough for one frame.
PAINT_PAUSE = 0.05


class LibraryActivity:
    def __init__(self):
        self.phase = None
        self.file_name = None
        self.label = None
        self.note = None
        self.slot = None

    @property
    def working(self):
        return self.phase is not None

    def begin(self, phase, file_name=None):
        self.phase, self.file_name, self.label = phase, file_name or None, None

    def advance(self, phase):
        """The next phase of the same work: the shown sentence changes in place."""
        self.phase = phase
        if self.label is not None:
            self.label.text = phase

    def end(self):
        """The work is over; True when a status was shown (the page then draws without it)."""
        shown = self.phase is not None
        self.phase = self.file_name = self.label = None
        return shown


def of(app):
    """The App's LibraryActivity (created on first use)."""
    state = getattr(app, "_library_activity", None)
    if state is None:
        state = LibraryActivity()
        app._library_activity = state
    return state
