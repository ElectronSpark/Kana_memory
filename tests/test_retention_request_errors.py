"""Cancellation and local failures never trigger an automatic paid retry."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from kanamemory import diagnostic_events, request_outcome
from kanamemory.retention_ai_ui import DiagnosticAIUIMixin
from kanamemory.retention_scope_ui import ScopeUIMixin
from kanamemory.retention_dialogue_ui import DiagnosticDialogueUIMixin


@pytest.mark.parametrize("failure", ["request", "save", "cancel"])
def test_failed_request_and_failed_error_persistence_are_distinct_no_retry(monkeypatch, failure):
    monkeypatch.setattr(diagnostic_events, "_buffer", diagnostic_events.DiagnosticBuffer())
    background = AsyncMock(return_value={"answer": "private"})
    if failure in {"request", "cancel"}:
        background.side_effect = asyncio.CancelledError() if failure == "cancel" else RuntimeError("private prompt")
    monkeypatch.setattr(request_outcome.asyncio, "to_thread", background)
    finish = Mock(side_effect=OSError("private path"))
    coroutine = request_outcome.finish_request("diagnostic_plan", Mock(), object(), {"id": "job", "request": {}}, "token", finish)
    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(coroutine)
    else:
        assert asyncio.run(coroutine) is None
    assert background.await_count == 1
    assert finish.call_count == (2 if failure == "save" else 1)
    assert finish.call_args.kwargs["error"] == {
        "save": "response_save_failed", "request": "request_failed", "cancel": "request_cancelled"}[failure]
    assert [row["stage"] for row in diagnostic_events.error_events()] == [failure, "record_failure"]


@pytest.mark.parametrize("kind", ["plan", "scope", "mid", "dialogue"])
@pytest.mark.parametrize("late", [False, True])
def test_cancelled_diagnostic_releases_own_ui_without_touching_new_page(monkeypatch, kind, late):
    monkeypatch.setattr(request_outcome.asyncio, "to_thread", AsyncMock(side_effect=asyncio.CancelledError()))
    service = SimpleNamespace(finish=Mock(return_value={"status": "failed"}))
    context = {"ai_busy": True, "scope_busy": True, "busy": True,
        "ai_button": SimpleNamespace(enabled=False), "scope_button": SimpleNamespace(enabled=False),
        "send": SimpleNamespace(enabled=False), "status": SimpleNamespace(text="original"), "form": {"id": "form"}}
    context["context"] = {"status": context["status"]}
    page = {"plan": "diagnostic_scope", "scope": "diagnostic_scope", "mid": "diagnostic_feedback", "dialogue": "diagnostic_dialogue"}[kind]
    app = SimpleNamespace(page="home" if late else page, _diagnostic_scope=context,
        _diagnostic_context=context, _diagnostic_dialogue=context,
        diagnostic_ai=lambda: service, scope_ai=lambda: service, diagnostic_dialogue=lambda: service,
        _show_diagnostic_mid=Mock())
    job = {"id": "job", "request": {"mode": "ask"}}
    if kind == "plan":
        coroutine = DiagnosticAIUIMixin._run_diagnostic_ai(app, context, job, {}, object(), "token")
    elif kind == "scope":
        coroutine = ScopeUIMixin._run_scope_suggestion(app, context, job, object(), "token")
    elif kind == "mid":
        coroutine = DiagnosticAIUIMixin._run_diagnostic_mid(app, context, job, object(), "token")
    else:
        coroutine = DiagnosticDialogueUIMixin._run_diagnostic_dialogue(app, context, job, object(), "token")
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(coroutine)
    if kind != "mid":
        busy, button = {"plan": ("ai_busy", "ai_button"), "scope": ("scope_busy", "scope_button"), "dialogue": ("busy", "send")}[kind]
        assert context[busy] is False
        assert context[button].enabled is not late
    else:
        assert app._show_diagnostic_mid.call_count == int(not late)
    assert app.page == ("home" if late else page)
    service.finish.assert_called_once_with("job", "token", error="request_cancelled")
    if late:
        assert context["status"].text == "original"
