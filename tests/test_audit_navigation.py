"""Retained setup pages must recover their own pending-batch prompt container."""
from types import MethodType
from unittest.mock import Mock

from support.fake_ui import View, descendants
from support.app_fixtures import setup_app
from kanamemory.app import KanaMemory
from kanamemory.navigation import page_view


def test_back_to_earlier_setup_restores_its_slot_and_clears_old_confirmation(setup_app):
    app = setup_app
    app._view_key = ("show_practice_setup", None)
    app._render_key, app._page_scroll, app._paint_handle = None, None, None
    app._navigating = False
    app._ink_revision = app._audio_serial = 0
    app.question = None
    app.title_label = View(text="选择练习")
    app.content_slot = View(app.body)
    app._sync_back_handler = Mock()
    for name in ("_capture_page", "go_back", "show_practice_setup"):
        setattr(app, name, MethodType(getattr(KanaMemory, name), app))
    original_screen = app.screen

    def screen(name, title, *children, **kwargs):
        body = original_screen(name, title, *children, **kwargs)
        app.title_label.text = title
        app.content_slot.clear()
        app.content_slot.add(body)
        return body

    app.screen = screen

    @page_view()
    def show_other(self):
        self.screen("audit_other", "另一页面", View(text="中间页面"))

    first_body = app.content_slot.children[0]
    first_slot = app.practice_replace_slot
    old_confirmation = View(text="结束原批次并开始")
    first_slot.add(old_confirmation)
    assert first_slot in list(descendants(first_body))

    # Real page_view captures setup A before setup B replaces widget bindings.
    show_other(app)
    captured = app.back_stack.pages[-1]
    assert captured.bindings["practice_replace_slot"] is first_slot
    assert captured.content is first_body
    app.show_practice_setup()
    second_slot = app.practice_replace_slot
    second_confirmation = View(text="另一设置页的确认")
    second_slot.add(second_confirmation)
    assert second_slot is not first_slot

    # Special setup pages now confirm an exit to home. A content page can
    # still return through retained content views to setup A; use that route
    # to verify the original slot binding rather than bypass the new policy.
    @page_view(replace=True)
    def replace_with_content(self):
        self.screen("audit_content", "资料页", View(text="只读资料"))

    replace_with_content(app)
    assert app.go_back() and app.page == "audit_other"
    assert app.go_back() and app.page == "practice_setup"
    assert app.content_slot.children[0] is first_body
    assert app.practice_replace_slot is first_slot
    assert first_slot.children == []
    assert old_confirmation not in list(descendants(first_body))
    assert second_slot.children == [second_confirmation]

    fresh_confirmation = View(text="重新检查后的确认")
    app.practice_replace_slot.add(fresh_confirmation)
    assert fresh_confirmation in list(descendants(app.content_slot.children[0]))
