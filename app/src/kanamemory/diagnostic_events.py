"""Bounded process-local error metadata, separate from explicit user feedback.

No exception messages, tracebacks, paths, answers, prompts or credentials are
accepted. Recording is best effort and must never replace an original error.
"""
from collections import OrderedDict
from threading import Lock
from time import time

MAX_EVENTS = 64
MAX_COUNT = 9999
OPERATIONS = frozenset({
    "learning_audio", "tutor_audio", "content_adoption", "diagnostic_plan",
    "material_revision", "material_review", "ai_generation",
    "material_input", "example_import", "learning_pitch", "diagnostic_permission",
    "feedback_analysis", "feedback_revision", "feedback_batch", "feedback_impact",
    "feedback_plan", "feedback_support", "tutor_request",
    "diagnostic_mid", "diagnostic_scope", "diagnostic_dialogue", "answer_autoplay",
    "platform_back", "platform_link", "platform_index", "platform_model",
    "platform_tts", "platform_recording", "feedback_review", "feedback_field",
    "feedback_sources", "feedback_delete",
    "diagnostic_time", "diagnostic_audio", "tutor_suggestions", "multi_cloze",
    "feedback_export",
})
STAGES = frozenset({"play", "save", "commit", "sync", "refresh", "request", "read", "decode",
                    "record_failure", "cancel", "callback", "cleanup", "schedule", "reveal"})
ERROR_TYPES = frozenset({"Exception", "RuntimeError", "ValueError", "TypeError",
    "KeyError", "AttributeError", "OSError", "IOError", "PermissionError",
    "FileNotFoundError", "TimeoutError", "CancelledError", "OperationalError",
    "IntegrityError", "DatabaseError", "AIServiceError", "PlatformError", "JavaException"})


class DiagnosticBuffer:
    def __init__(self):
        self._events = OrderedDict()
        self._lock = Lock()

    def record(self, operation: str, stage: str, error: BaseException) -> None:
        if operation not in OPERATIONS or stage not in STAGES:
            return
        name = type(error).__name__
        category = name if name in ERROR_TYPES else "OtherError"
        key, now = (operation, stage, category), int(time())
        with self._lock:
            event = self._events.get(key)
            if event is None:
                event = dict(operation=operation, stage=stage, error_type=category,
                             first_at=now, last_at=now, count=0)
                self._events[key] = event
            event.update(last_at=now, count=min(MAX_COUNT, event["count"] + 1))
            self._events.move_to_end(key)
            while len(self._events) > MAX_EVENTS:
                self._events.popitem(last=False)

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [dict(event) for event in self._events.values()]


_buffer = DiagnosticBuffer()


def record_error(operation: str, stage: str, error: BaseException) -> None:
    try:
        _buffer.record(operation, stage, error)
    except (TypeError, RuntimeError):
        # Unhashable/non-text fields or a broken lock: diagnostics cannot
        # mask the operation's own failure or recovery.
        pass


def error_events() -> list[dict]:
    try:
        return _buffer.snapshot()
    except Exception:
        return []
