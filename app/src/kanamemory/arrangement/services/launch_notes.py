"""LaunchNotes: what this launch has already shown once (design 12 B10, UI1).

Since design 12 E9 the page 加练 is shown at every entry (with its parts), so it is no longer a launch note.

Memory only, one per store object (StoreArrangement), so it is forgotten at
the next launch and when the store object is replaced (restore, reset). No
storage, no clock, no rule: the presenter decides whether a prompt is due;
this only remembers that it was shown.
"""
from __future__ import annotations


class LaunchNotes:
    """Once-per-launch facts: the B10 first-continue prompt and the fresh-install orientation screen."""

    def __init__(self):
        self._continue_prompted = False
        self._orientation_shown = False

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
