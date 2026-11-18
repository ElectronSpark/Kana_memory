"""A real restart for content library changes (process_restart, KanaRestart.java).

Android keeps the process when the user leaves the app and comes back; the App starts again in it
and still serves the content it started with. A pending library change then restarts the process
at start-up, once; the libraries page offers 立即重启 where the platform can restart.
"""
from pathlib import Path
import re

import pytest

from kanamemory import process_restart
from kanamemory.content_library_ui import ContentLibraryUIMixin

JAVA = (Path(__file__).resolve().parents[1] / "app" / "native" / "java" / "org" / "example" / "kanamemory"
        / "KanaRestart.java")


@pytest.mark.parametrize("started_before", [False, True])
@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize("can_restart", [False, True])
@pytest.mark.parametrize("attempted", [False, True])
def test_only_a_start_again_with_a_pending_change_restarts_and_only_once(started_before, pending, can_restart,
                                                                         attempted):
    action = process_restart.decide(started_before=started_before, change_pending=pending,
                                    can_restart=can_restart, already_attempted=attempted)
    expected = process_restart.RESTART if (started_before and pending and can_restart and not attempted) \
        else process_restart.CONTINUE
    assert action == expected


@pytest.fixture
def fresh_process(monkeypatch):
    monkeypatch.setitem(process_restart._STATE, "starts", 0)
    monkeypatch.setitem(process_restart._STATE, "attempted", False)


class _Platform:
    can_restart = True

    def __init__(self, result=False, error=None):
        self.calls, self.result, self.error = 0, result, error

    def restart_app(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result  # a real restart never returns; returning means it did not happen


class _Service:
    def __init__(self, pending):
        self.pending = pending

    def pending_restart(self):
        return self.pending


class _App(ContentLibraryUIMixin):
    def __init__(self, platform, pending):
        self.platform, self._service = platform, _Service(pending)

    def content_library_service(self):
        return self._service


def test_the_first_start_of_a_process_never_restarts(fresh_process):
    platform = _Platform()
    assert _App(platform, pending=True).restart_for_pending_library_change() is False
    assert platform.calls == 0


def test_a_start_again_with_a_pending_change_restarts_once_and_never_loops(fresh_process, monkeypatch):
    errors = []
    monkeypatch.setattr("kanamemory.diagnostic_events.record_error", lambda *args: errors.append(args))
    platform = _Platform(error=RuntimeError("refused"))
    _App(platform, pending=True).restart_for_pending_library_change()  # the first start: nothing
    assert _App(platform, pending=True).restart_for_pending_library_change() is False  # refused: go on
    assert platform.calls == 1 and errors and process_restart.attempted()
    for _ in range(3):  # Android re-creates the window again: no second attempt in this process
        _App(platform, pending=True).restart_for_pending_library_change()
    assert platform.calls == 1


def test_a_plain_return_to_the_app_restarts_nothing(fresh_process):
    platform = _Platform()
    for _ in range(3):  # rotation or a return without a pending change
        assert _App(platform, pending=False).restart_for_pending_library_change() is False
    assert platform.calls == 0 and not process_restart.attempted()


def test_a_platform_that_cannot_restart_is_never_asked(fresh_process):
    platform = _Platform()
    platform.can_restart = False
    for _ in range(2):
        _App(platform, pending=True).restart_for_pending_library_change()
    assert platform.calls == 0


def test_the_desktop_cannot_restart():
    from kanamemory.platform_desktop import DesktopPlatform
    assert DesktopPlatform.can_restart is False and DesktopPlatform.restart_app(object()) is False


def test_the_android_helper_starts_the_launcher_as_a_new_task_then_ends_the_process():
    source = JAVA.read_text("utf-8")
    body = source[source.index("public static boolean restart(Activity activity)"):]
    calls = ["getLaunchIntentForPackage(activity.getPackageName())", "Intent.makeRestartActivityTask(component)",
             "activity.startActivity(restart)", "Runtime.getRuntime().exit(0)"]
    positions = [body.index(call) for call in calls]
    assert positions == sorted(positions)
    # No permission or manifest entry: a plain foreground activity start.
    assert not re.search(r"PendingIntent|AlarmManager|killProcess|<uses-permission", source)
    android = (JAVA.parents[5] / "src" / "kanamemory" / "platform_android.py").read_text("utf-8")
    assert 'c("org.example.kanamemory.KanaRestart").restart(activity)' in android
