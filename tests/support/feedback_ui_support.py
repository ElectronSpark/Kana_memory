"""Shared feedback ui fixtures and assertions; no collected scenarios."""
from unittest.mock import AsyncMock
from kanamemory.feedback_store import FeedbackStore
from kanamemory.feedback_ui import FeedbackUIMixin
from types import MethodType
from unittest.mock import Mock
from types import SimpleNamespace
from support.fake_ui import View
from support.app_fixtures import app_module
import asyncio
from support.fake_ui import descendants
import pytest
from support.app_fixtures import setup_app
import struct
import zlib


def capture():
    return {"format": "kanamemory-feedback-context", "version": 1,
        "page": {"name": "learning_question", "title": "语法练习", "scroll_position": [0, 145]},
        "environment": {"app_version": "0.15.0", "platform": "android"},
        "widget_tree": {"type": "Box", "children": [
            {"type": "Label", "text": "这里应该用哪个助词？", "fragment_index": 0},
            {"type": "TextInput", "value": "我的未提交草稿"},
            {"type": "Label", "text": "隐藏标准答案", "visible": False}]},
        "fragments": [{"id": "example:1", "answer": "片段隐藏答案"}],
        "practice": {"question_id": "q1", "maintainer_only": {"answer": "维护答案"}},
        "settings": {"enabled_kinds": ["grammar_meaning"]},
        "recent_events": [{"kind": "navigate"}],
        "completeness": {"complete": True, "warnings": []}}


def png():
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00\xff")) + chunk(b"IEND", b""))


def click(app, caption):
    control = next(item for item in descendants(app.body) if item.text == caption and item.on_press)
    control.on_press(control)
    return control


@pytest.fixture
def feedback_app(setup_app, tmp_path, monkeypatch):
    app = setup_app
    for name, member in vars(FeedbackUIMixin).items():
        if not name.startswith("__") and callable(member):
            setattr(app, name, MethodType(getattr(member, "__wrapped__", member), app))

    def multiline(**kwargs):
        widget = View(**kwargs)
        widget.readonly = kwargs.get("readonly", False)
        return widget

    monkeypatch.setattr(app_module.toga, "MultilineTextInput", multiline)
    service = FeedbackStore(tmp_path / "feedback.sqlite3")
    app.feedback_store = lambda: service
    app.main_window = SimpleNamespace(dialog=AsyncMock(return_value=True))
    app.loop = SimpleNamespace(create_task=lambda coroutine: asyncio.run(coroutine))
    app.platform.export_feedback = AsyncMock(return_value=True)
    app.platform.copy_text = AsyncMock(return_value=True)
    # Feedback-only controller tests have no learning DB for receipt sync.
    # Real background preparation must still open its own feedback connection.
    app.store.data_dir = None
    app.store.zone = None

    def restore():
        app.page = "learning_question"

    app.return_from_feedback = Mock(side_effect=restore)
    app.return_from_feedback_detail = Mock(side_effect=lambda: app._render_feedback_manager(**app._feedback_manager_state))
    app.store.reset_mock()
    yield app
    app.store.assert_not_called()
    assert app.store.mock_calls == [], "feedback UI must not alter learning state"
    service.close()


def seed(app, count=1):
    return [app.feedback_store().create(f"说明 {i}", capture()) for i in range(count)]
