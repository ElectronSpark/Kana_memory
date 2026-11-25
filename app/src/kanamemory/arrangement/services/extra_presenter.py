"""ExtraPresenter: what the pages of extra practice show (design 12 E1, E5, UI7; design 13 C2).

Pure. The button 继续加练 (only when something can be practised) and the deck's
presenter while the mode is on (header 加练, leave button 结束加练). Whether
something can be practised is ExtraSession.available's answer, handed in.
The page 加练 with its parts (design 12 E9, shown at every entry) is
ArrangementPresenter.extra_parts.
"""
from __future__ import annotations

from ..display import DECK_AGAIN, EXTRA_DECK_NOTE, EXTRA_END, EXTRA_HEADER, ExtraButtonModel
from .deck_presenter import DeckPresenter
from .launch_notes import LaunchNotes


class ExtraPresenter:
    """Display models of extra practice (``launch``: this launch's notes, kept for the callers' shape)."""

    def __init__(self, launch: LaunchNotes | None = None):
        self._launch = launch

    @staticmethod
    def button(available: bool) -> ExtraButtonModel | None:
        """继续加练 when some learned state can be practised, else None (no button)."""
        return ExtraButtonModel() if available else None

    @staticmethod
    def deck() -> DeckPresenter:
        """The presenter of a practice deck inside extra practice (after its last question 再来一组, N13)."""
        return DeckPresenter(EXTRA_DECK_NOTE, header=EXTRA_HEADER, leave_caption=EXTRA_END, last_caption=DECK_AGAIN)
