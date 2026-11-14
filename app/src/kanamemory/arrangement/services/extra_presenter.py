"""ExtraPresenter: what the pages of extra practice show (design 12 E1, E5, UI7; design 13 C2).

Pure. The button 继续加练 (only when something can be practised), the page 加练
once per launch before the first start (services.launch_notes remembers it),
and the deck's presenter while the mode is on (header 加练, leave button
结束加练). Whether something can be practised is ExtraSession.available's
answer, handed in.
"""
from __future__ import annotations

from ..display import EXTRA_DECK_NOTE, EXTRA_END, EXTRA_HEADER, ExtraButtonModel, ExtraIntroModel
from .deck_presenter import DeckPresenter
from .launch_notes import LaunchNotes


class ExtraPresenter:
    """Display models of extra practice over this launch's notes."""

    def __init__(self, launch: LaunchNotes):
        self._launch = launch

    @staticmethod
    def button(available: bool) -> ExtraButtonModel | None:
        """继续加练 when some learned state can be practised, else None (no button)."""
        return ExtraButtonModel() if available else None

    def intro(self) -> ExtraIntroModel | None:
        """The page 加练 before the first start in this launch; None once it has been shown."""
        return ExtraIntroModel() if self._launch.may_explain_extra() else None

    def showed_intro(self) -> None:
        self._launch.explained_extra()

    @staticmethod
    def deck() -> DeckPresenter:
        """The presenter of a practice deck inside extra practice."""
        return DeckPresenter(EXTRA_DECK_NOTE, header=EXTRA_HEADER, leave_caption=EXTRA_END)
