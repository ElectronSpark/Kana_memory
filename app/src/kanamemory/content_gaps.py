"""Bounded process-local record of item x ability slots the dependency gate left empty.

Design 11 section 5 (D4): when neither a gated template (declared needs all
known) nor a fallback template (no declared needs) is available, the ability
is skipped for this round and one "content_gap" event is recorded for later
content work. Only allowed fields are kept: the content id, the ability, the
enabled kind names and the number of missing ids. Prompts, answers, sentence
text and the missing ids themselves are never accepted. Recording is best
effort and never changes selection, snapshots, drafts, queues or due dates.
"""
from collections import OrderedDict
import re
from threading import Lock
from time import time

EVENT = "content_gap"
MAX_EVENTS = 256
MAX_COUNT = 9999
MAX_KINDS = 16
# Only id-shaped fields are kept: a family-prefixed content id without
# whitespace and an ability/kind name. Free text never matches.
_CONTENT_ID = re.compile(r"[a-z][a-z0-9_]{0,31}:\S{1,224}\Z")
_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


class ContentGapBuffer:
    def __init__(self):
        self._events = OrderedDict()
        self._lock = Lock()

    def record(self, content_id: str, ability: str, missing: int, kinds=()) -> None:
        if (not isinstance(content_id, str) or not _CONTENT_ID.fullmatch(content_id)
                or not isinstance(ability, str) or not _NAME.fullmatch(ability)
                or type(missing) is not int or missing < 0):
            return
        names = tuple(sorted({k for k in kinds if isinstance(k, str) and _NAME.fullmatch(k)}))[:MAX_KINDS]
        key, now = (content_id, ability), int(time())
        with self._lock:
            event = self._events.get(key)
            if event is None:
                event = dict(event=EVENT, content_id=content_id, ability=ability, kinds=names,
                             missing=missing, first_at=now, last_at=now, count=0)
                self._events[key] = event
            event.update(last_at=now, kinds=names, missing=min(MAX_COUNT, missing),
                         count=min(MAX_COUNT, event["count"] + 1))
            self._events.move_to_end(key)
            while len(self._events) > MAX_EVENTS:
                self._events.popitem(last=False)

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [dict(event) for event in self._events.values()]


_buffer = ContentGapBuffer()


def record_gap(content_id: str, ability: str, missing: int, kinds=()) -> None:
    try:
        _buffer.record(content_id, ability, missing, kinds)
    except (TypeError, RuntimeError):
        # Diagnostics never mask or replace the selection result.
        pass


def gap_events() -> list[dict]:
    try:
        return _buffer.snapshot()
    except Exception:
        return []
