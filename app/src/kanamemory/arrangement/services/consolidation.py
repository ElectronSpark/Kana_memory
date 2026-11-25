"""Consolidation: the consolidation deck of what was taught today, for "继续学习" (design 12 N5).

Application layer without storage of its own: it asks its facts source
(sources/consolidation.StoreConsolidation) for the course states taught
today, rules/consolidation for which are ready and whether a deck is due,
and builds the deck with the existing PracticeDeck (its exposure rows carry
practice:consolidation: event ids, so the count and the last exposure are
read back from the store after a restart). The only memory is the leave
pause: after the learner leaves a deck no new one is offered for
LEAVE_PAUSE (memory of this launch; a restart forgets it). The deck never
rates and never touches a pending question (PracticeDeck).
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime

from ..model import ConsolidationFacts, DeckItem
from ..rules.consolidation import deck_due, deck_order, is_ready, paused, ready_at
from .deck_presenter import DeckPresenter


class Consolidation:
    """When "继续学习" offers a consolidation deck, and the deck itself."""

    def __init__(self, facts: Callable[[datetime], Sequence[ConsolidationFacts]],
                 new_deck: Callable[[Sequence[DeckItem]], object]):
        self._facts = facts
        self._new_deck = new_deck
        self._left_at: datetime | None = None

    def ready(self, now: datetime) -> list[ConsolidationFacts]:
        """The states whose next repeat is ready at ``now``."""
        return [item for item in self._facts(now) if is_ready(item, now)]

    def due_deck(self, now: datetime, *, nothing_else: bool = False) -> tuple[DeckItem, ...]:
        """The items of the deck to offer now, or () (none due, or the leave pause)."""
        if paused(self._left_at, now):
            return ()
        facts = list(self._facts(now))
        ready = [ready_at(item, now) for item in facts if is_ready(item, now)]
        if not deck_due(ready, now, nothing_else=nothing_else):
            return ()
        return tuple(DeckItem(item.ref) for item in deck_order(facts, now))

    def deck(self, items: Sequence[DeckItem]):
        """A PracticeDeck of ``items`` (practice only; its answers write consolidation exposure rows)."""
        return self._new_deck(items)

    def left(self, now: datetime) -> None:
        """The learner left a deck (先到这里): no new deck for LEAVE_PAUSE; unanswered items stay unpractised."""
        self._left_at = now

    @staticmethod
    def presenter() -> DeckPresenter:
        from ..display import CONSOLIDATION_HEADER, CONSOLIDATION_LEAVE, CONSOLIDATION_NOTE
        return DeckPresenter(CONSOLIDATION_NOTE, header=CONSOLIDATION_HEADER, leave_caption=CONSOLIDATION_LEAVE)
