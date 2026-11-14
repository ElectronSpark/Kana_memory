"""Automatic diagnostics carry finite metadata, never user or model text."""
import json
from itertools import product
from types import SimpleNamespace
from unittest.mock import Mock

from kanamemory import diagnostic_events as events
from support.app_fixtures import setup_app  # noqa: F401
from support.feedback_ui_support import feedback_app, seed  # noqa: F401


def test_error_messages_unknown_fields_and_dynamic_type_names_are_not_recorded(monkeypatch):
    buffer = events.DiagnosticBuffer()
    monkeypatch.setattr(events, "_buffer", buffer)
    secret = "sk-private-user-answer-corpus"
    events.record_error("learning_audio", "save", RuntimeError(secret))
    events.record_error(secret, "save", ValueError(secret))
    events.record_error("learning_audio", secret, ValueError(secret))
    events.record_error("learning_audio", "save", type(secret, (Exception,), {})(secret))
    result = events.error_events()
    assert secret not in json.dumps(result)
    assert [row["error_type"] for row in result] == ["RuntimeError", "OtherError"]
    assert all(set(row) == {"operation", "stage", "error_type", "first_at", "last_at", "count"} for row in result)


def test_buffer_capacity_counts_and_returned_copies_are_bounded():
    buffer = events.DiagnosticBuffer()
    for operation, stage in product(sorted(events.OPERATIONS), sorted(events.STAGES)):
        buffer.record(operation, stage, RuntimeError("unrecorded"))
    assert len(buffer.snapshot()) == events.MAX_EVENTS
    for _ in range(events.MAX_COUNT+2):
        buffer.record("learning_audio", "save", RuntimeError())
    result = buffer.snapshot()
    assert len(result) == events.MAX_EVENTS and result[-1]["count"] == events.MAX_COUNT
    result[-1]["count"] = 0
    assert buffer.snapshot()[-1]["count"] == events.MAX_COUNT


def test_diagnostic_failure_cannot_replace_original_error(monkeypatch):
    monkeypatch.setattr(events, "_buffer", SimpleNamespace(
        record=Mock(side_effect=RuntimeError("broken recorder")),
        snapshot=Mock(side_effect=RuntimeError("broken recorder"))))
    events.record_error("learning_audio", "save", ValueError("original"))
    assert events.error_events() == []


def test_feedback_includes_metadata_without_changing_user_report_contract(monkeypatch):
    from kanamemory.feedback_context import capture_feedback_context, context_sha256
    from support.feedback_context_support import fake_app
    monkeypatch.setattr(events, "_buffer", events.DiagnosticBuffer())
    events.record_error("learning_audio", "save", RuntimeError("secret answer"))
    context = capture_feedback_context(fake_app())
    assert context["version"] == 1
    assert context["diagnostic_events"] == events.error_events()
    assert "secret answer" not in json.dumps(context["diagnostic_events"])
    assert context["sha256"] == context_sha256(context)


def test_malformed_fields_are_dropped_without_raising(monkeypatch):
    monkeypatch.setattr(events, "_buffer", events.DiagnosticBuffer())
    events.record_error(["unhashable"], "save", ValueError())
    events.record_error("learning_audio", {"unhashable": 1}, ValueError())
    assert events.error_events() == []


def recorded_names(path):
    """Literal (operation, stage) pairs passed to record_error in one module."""
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "record_error"
                and all(isinstance(arg, ast.Constant) for arg in node.args[:2])):
            yield node.args[0].value, node.args[1].value


def test_swallowed_ui_and_platform_failures_use_registered_names():
    # B7 sites: unregistered names would be silently dropped by record().
    from support.paths import ROOT
    package = ROOT / "app/src/kanamemory"
    names = {name: set(recorded_names(package / name)) for name in (
        "retention_session_ui.py", "tutor_ui.py", "multi_cloze_ui.py", "platform_android.py", "feedback_ui.py")}
    assert {("diagnostic_time", "save"), ("diagnostic_audio", "play"), ("diagnostic_audio", "save"),
            ("diagnostic_scope", "save")} <= names["retention_session_ui.py"]
    assert ("tutor_suggestions", "request") in names["tutor_ui.py"]
    assert names["multi_cloze_ui.py"] == {("multi_cloze", "save"), ("multi_cloze", "commit")}
    assert ("feedback_export", "refresh") in names["feedback_ui.py"]
    for pairs in names.values():
        for operation, stage in pairs:
            assert operation in events.OPERATIONS and stage in events.STAGES, (operation, stage)


def test_feedback_export_progress_maps_every_stage_to_a_registered_name():
    # The export failure stage is dynamic: each progress value the page sets must map
    # to a registered stage (or be the unrecorded "idle"), or record() drops the event.
    import ast
    from support.paths import ROOT
    from kanamemory import feedback_ui
    tree = ast.parse((ROOT / "app/src/kanamemory/feedback_ui.py").read_text(encoding="utf-8"))
    progress = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Subscript)
                and getattr(target.slice, "value", None) == "stage" for target in node.targets):
            progress.add(node.value.value)
        elif isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "update":
            progress.update(k.value.value for k in node.keywords if k.arg == "stage")
        elif isinstance(node, ast.Dict):
            progress.update(v.value for k, v in zip(node.keys, node.values)
                            if getattr(k, "value", None) == "stage" and isinstance(v, ast.Constant))
    assert progress == {"preparing", "delivery", "archiving", "idle"}
    mapping = feedback_ui.EXPORT_DIAGNOSTIC_STAGES
    assert set(mapping) == progress - {"idle"}
    assert set(mapping.values()) <= events.STAGES and "feedback_export" in events.OPERATIONS


def test_feedback_export_delivery_failure_is_recorded(feedback_app, monkeypatch):
    monkeypatch.setattr(events, "_buffer", events.DiagnosticBuffer())
    app = feedback_app
    seed(app)
    app.show_feedback_manager()
    app.platform.export_feedback.side_effect = OSError("写入失败")
    app.export_feedback_records()
    assert "未能导出" in app.alert.call_args.args[0]
    assert [(row["operation"], row["stage"], row["error_type"]) for row in events.error_events()] == [
        ("feedback_export", "commit", "OSError")]
