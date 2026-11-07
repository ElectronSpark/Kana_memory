"""Shared focused navigation fixtures and assertions; no collected scenarios."""
from unittest.mock import AsyncMock
from kanamemory.app import KanaMemory
from kanamemory.learning_ui import LearningUIMixin
from types import MethodType
from types import SimpleNamespace
from kanamemory.tutor_ui import TutorUIMixin
from support.fake_ui import View
from kanamemory import app as app_module
import asyncio
from kanamemory.navigation import page_view
import pytest


@pytest.fixture
def focused_app(preview_app, monkeypatch):
    app = preview_app
    app.show_question = MethodType(KanaMemory.show_question, app)
    app.navigation = View(text="底部导航")
    app.root = View(app.content_slot, app.navigation)
    app.header_back_slot = View()
    app.header_back_button = View(text="‹", on_press=app.go_back)
    app._practice_context = False
    app._leave_confirmation_active = False
    app.request_learning_exit = MethodType(LearningUIMixin.request_learning_exit, app)
    app.leave_learning = MethodType(LearningUIMixin.leave_learning, app)
    app.leave_tutor_home = MethodType(TutorUIMixin.leave_tutor_home, app)
    app.main_window.dialog = AsyncMock(return_value=False)
    monkeypatch.setattr(app_module.toga, "ConfirmDialog", lambda title, message: SimpleNamespace(title=title, message=message))
    pending = []
    app.loop = SimpleNamespace(create_task=lambda coroutine: pending.append(coroutine), call_soon=lambda callback: callback())
    app.pending_confirmation = pending

    # Keep the real page transition and chrome, without constructing unrelated
    # homepage statistics/model widgets in this navigation-focused fixture.
    @page_view()
    def show_home(self):
        assert self._returning_home
        self.screen("home", "首页", View(text="继续未完成练习"))

    app.show_home = MethodType(show_home, app)
    yield app
    for coroutine in pending:
        coroutine.close()


def resolve_confirmation(app, confirmed):
    app.main_window.dialog.return_value = confirmed
    assert len(app.pending_confirmation) == 1
    asyncio.run(app.pending_confirmation.pop())
