"""Data of the add-content use case: what it reads from the outside, and what an action did (design 13 S2, C1, C2).

Pure: no Toga, no store, no app object.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .display import COUNT_INVALID, AddPageModel


@dataclass(frozen=True)
class AddInputs:
    """Device abilities and settings, read again at every operation that counts the workload (C1: data, not the app)."""

    course_kinds: tuple[str, ...]   # the learner's enabled course question kinds this device can serve now
    kana_kinds: tuple[str, ...]     # the main lane's kana kinds: the pending queue's policy, else the practice preferences
    speech_ready: bool              # Japanese speech is ready (listening kinds can be served)
    handwriting_ready: bool         # the handwriting model is ready


Outcome = Literal["ok", "ignored", "rejected", "failed"]


@dataclass(frozen=True)
class AddResult:
    """What 加入并继续 or 创建路线 did.

    ok: written (``route_id``: the new route of 创建路线); ignored: nothing to
    do and nothing to say (nothing left to add, a second press while saving);
    rejected: the request was refused, ``message`` says why; failed: storage
    failed, ``message`` asks to retry. ``model``: the page after the rebase of
    加入并继续 (None for 创建路线, which redraws nothing).
    """

    outcome: Outcome
    message: str = ""
    route_id: str | None = None
    model: AddPageModel | None = None

    @property
    def ok(self) -> bool:
        return self.outcome == "ok"


def section_count(text: str) -> int:
    """The number of sections 按顺序选多节 asks for; ValueError(COUNT_INVALID) unless a whole number above zero."""
    value = str(text or "").strip()
    if not value.isdecimal() or int(value) < 1:
        raise ValueError(COUNT_INVALID)
    return int(value)
