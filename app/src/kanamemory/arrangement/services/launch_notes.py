"""LaunchNotes: what this launch has already shown once (design 12 B10, UI1).

Memory only, one per store object (StoreArrangement), so it is forgotten at
the next launch and when the store object is replaced (restore, reset). No
storage, no clock, no rule: the presenter decides whether a prompt is due;
this only remembers that it was shown.
"""
from __future__ import annotations


class LaunchNotes:
    """Once-per-launch facts: the B10 first-continue prompt, the fresh-install orientation screen and
    the page 加练 before the first extra practice (UI7)."""

    def __init__(self):
        self._continue_prompted = False
        self._orientation_shown = False
        self._extra_explained = False

    def may_prompt_continue(self) -> bool:
        """True until the B10 prompt before the first "继续学习" has been shown in this launch."""
        return not self._continue_prompted

    def prompted_continue(self) -> None:
        """Record that the B10 first-continue prompt was shown (whatever the learner pressed)."""
        self._continue_prompted = True

    def may_show_orientation(self) -> bool:
        """True until the fresh-install orientation screen has been shown in this launch."""
        return not self._orientation_shown

    def showed_orientation(self) -> None:
        """Record that the orientation screen was shown for a fresh install in this launch."""
        self._orientation_shown = True

    def may_explain_extra(self) -> bool:
        """True until the page 加练 before the first start of extra practice has been shown in this launch (UI7)."""
        return not self._extra_explained

    def explained_extra(self) -> None:
        """Record that the page 加练 was shown (whatever the learner pressed)."""
        self._extra_explained = True
