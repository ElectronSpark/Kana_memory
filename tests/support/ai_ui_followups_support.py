"""Shared ai ui followups fixtures and assertions; no collected scenarios."""
from kanamemory.app import KanaMemory
from types import MethodType
from unittest.mock import Mock
from support.fake_ui import View
from support.ai_ui_support import ai_app
from support.example_ui_support import examples_app
from support.learning_fixtures import learning_app
import pytest
from support.app_fixtures import question_app
from support.app_fixtures import setup_app


@pytest.fixture
def ai_navigation_app(ai_app):
    app = ai_app
    app._view_key = None
    app._render_key = None
    app._navigating = False
    app._paint_handle = app._page_scroll = None
    app._ink_revision = app._audio_serial = 0
    app.question = None
    app.content_slot, app.title_label, app.subtitle_label = View(View()), View(), View()
    app.platform.set_back_enabled = Mock()
    app.loop.call_soon = lambda callback: callback()
    def screen(name, title, *children, **kwargs):
        app.body = KanaMemory.screen(app, name, title, *children, **kwargs)
        if app._page_scroll:
            app._page_scroll.position = (0, 0)
        return app.body
    app.screen = screen
    for name in ('_capture_page', '_sync_back_handler', 'go_back', 'show_ai_proposal', 'show_ai_form', 'show_material_task'):
        setattr(app, name, MethodType(getattr(KanaMemory, name), app))
    return app
