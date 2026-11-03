"""Feedback is an observation of a retained screen, never a learning action."""
from __future__ import annotations

import hashlib
from pathlib import Path
import time

from .feedback_context import capture_feedback_context, context_sha256
from .feedback_store import FeedbackStore


REPORT_BINDINGS = {
    "report_editor": ["_report_editor"],
    "report_list": "_feedback_manager_state _feedback_selected _feedback_manager_controls _feedback_selection_label _feedback_bulk_buttons _feedback_page_switches _feedback_export_controls _feedback_export_status _feedback_export_actions".split(),
    "report_detail": "_report_detail _feedback_manager_state _feedback_selected _feedback_export_controls".split(),
    "feedback_analysis": ["_feedback_analysis_view"],
    "feedback_batch": ["_feedback_batch_view"],
}


class FeedbackLifecycleMixin:
    def feedback_store(self):
        store = getattr(self, "_feedback_store", None)
        if store is None:
            store = self._feedback_store = FeedbackStore(Path(self.paths.data) / "feedback.sqlite3")
        return store

    def install_feedback(self):
        self._feedback_return_stack = []
        self._feedback_events = []
        self.platform.feedback_callback = self.begin_page_feedback
        original = self.main_window.dialog

        async def dialog_with_feedback(dialog):
            attach = getattr(self.platform, "attach_feedback_dialog", None)
            if attach:
                attach(dialog)
            return await original(dialog)

        self.main_window.dialog = dialog_with_feedback

    def begin_page_feedback(self, widget=None, *, overlay=None, screenshot=None, **kwargs):
        """Capture before replacing the body, preserving all original widgets."""
        previous = self._capture_page()
        if previous is None:
            return
        try:
            context = capture_feedback_context(self, overlay=overlay)
            image_error = None
            if screenshot is None and overlay is None:
                capture = getattr(self.platform, "capture_feedback_screenshot", None)
                try:
                    screenshot = capture() if capture else None
                except Exception as exc:
                    image_error = type(exc).__name__
            context["screenshot"] = {
                "available": bool(screenshot), "scope": "app_dialog" if overlay else "app_window",
                "sha256": hashlib.sha256(screenshot).hexdigest() if screenshot else None,
            }
            if not screenshot:
                context["completeness"]["warnings"].append({"path": "screenshot", "reason": "unavailable", "error_type": image_error})
                context["completeness"]["warning_count"] = context["completeness"].get("warning_count", 0) + 1
                context["completeness"]["complete"] = False
            context["sha256"] = context_sha256(context)
        except Exception as exc:
            self.alert("无法记录当前页面：" + str(exc))
            return
        # Persist the existing draft, but do not generate, reveal or score anything.
        if self._checkpoint() is False:
            return
        extra = {name: getattr(self, name) for name in REPORT_BINDINGS.get(self.page, ()) if hasattr(self, name)}
        extra["_active_stroke"] = getattr(self, "_active_stroke", None)
        frame = {"page": previous, "stack": list(self.back_stack.pages), "bindings": extra,
                 "entered": time.monotonic(), "question_started": getattr(self, "_question_started", None)}
        self._feedback_return_stack.append(frame)
        self._audio_serial += 1
        self._ink_revision += 1
        self.render_feedback_editor(context, screenshot)
        self._view_key = ("report_editor", len(self._feedback_return_stack))
        self._sync_back_handler()

    def _restore_feedback_page(self, previous):
        from .app import KanaMemory, INK_PAGES
        KanaMemory._cancel_review_wait(self)
        self._page_token += 1
        self.page, self._view_key = previous.name, previous.key
        self.title_label.text = previous.title
        from .page_restore import restore_bindings, mount_retained_content, schedule_scroll_restore
        restore_bindings(self, previous.bindings)
        from .personal_content_refresh import refresh_return
        if refresh_return(self, previous): return
        self._page_scroll = previous.scroll
        mount_retained_content(self, previous)
        KanaMemory._update_chrome(self)
        self._sync_back_handler()
        token = self._page_token
        schedule_scroll_restore(self, previous, only_if_changed=False)
        if self.page in INK_PAGES:
            self.refresh_model_ui()
            self._schedule_ink_draw()
        elif self.page == "settings":
            self.refresh_model_ui()
            self.settings_message.text = self.service_message
        elif self.page == "practice_setup":
            self.refresh_practice_setup()
        elif self.page == "word_drill_setup":
            self.refresh_word_drill_setup()
        if self.page == "session_wait":
            self._review_wait_handle = self.loop.call_soon(KanaMemory._tick_review_wait, self, token)
        elif self.page == "home":
            KanaMemory._arm_home_review_refresh(self)

    def return_from_feedback(self):
        frames = getattr(self, "_feedback_return_stack", [])
        if not frames:
            self.show_home()
            return
        frame = frames.pop()
        self.back_stack.pages[:] = frame["stack"]
        self._restore_feedback_page(frame["page"])
        for name, value in frame["bindings"].items():
            setattr(self, name, value)
        if self.page == "report_list":
            draft = {key: control.value for key, control in self._feedback_manager_controls.items()}
            self._render_feedback_manager(**self._feedback_manager_state)
            for key, value in draft.items():
                self._feedback_manager_controls[key].value = value
        started = frame["question_started"]
        if started is not None:
            self._question_started = started + time.monotonic() - frame["entered"]

    def return_from_feedback_detail(self):
        state = dict(getattr(self, "_feedback_manager_state", {}))
        if self.back_stack.pages and self.back_stack.pages[-1].name == "report_list":
            self._restore_feedback_page(self.back_stack.pop())
        self._render_feedback_manager(**state)
        self._view_key = ("show_feedback_manager", None)
