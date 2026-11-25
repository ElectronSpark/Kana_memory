"""DeckFlow: the page flow of one practice deck (design 12 E5, UI8; design 13 C4, C7).

question -> result -> next question -> end, over a PracticeDeck, its
DeckPresenter and the page functions of practice_deck_page. The app hands in
the deck and the presenter (a page module imports no service) and
callbacks: show(header, widgets) puts a page on screen and returns a
liveness check of that page (a button of a page that is no longer shown does
nothing), failed(error) reports an answer that could not be recorded (the
card stays open for the same answer again), unreadable(error) reports that
the next card's state could not be read (a busy database: the deck ends
there), speak(text), now() and leave(). Screen and token handling stay with
the app (design 13 C7).

The deck is memory only: leaving, or any other page, ends it, and nothing
resumes it.
"""
from __future__ import annotations

from collections.abc import Callable

from ..model import DeckOutcome
from . import practice_deck_page as pages

Show = Callable[[str, list], Callable[[], bool]]


class DeckFlow:
    """One deck on screen; start() shows its first question (or its end)."""

    def __init__(self, deck, presenter, *, show: Show, failed: Callable[[BaseException], object],
                 speak: Callable[[str], object], now: Callable[[], object], leave: Callable[[], object],
                 unreadable: Callable[[BaseException], object] | None = None,
                 finish: Callable[[], object] | None = None, before_next: Callable[[], bool] | None = None,
                 more: Callable[[], bool] | None = None):
        """``deck``: a services.PracticeDeck; ``presenter``: its services.DeckPresenter.

        ``finish`` (extra practice, design 12 E1): called instead of the end
        page when the deck is used up, and when ``before_next`` (asked before
        each card after the first) says the deck is to end early; without it
        the end page is shown and its button leaves. ``more`` (design 12 N13):
        asked on the result page of the deck's last question, whether the
        mode can serve more after it; False leaves that page without a way on
        but its leave button (none: it always can).
        """
        self._deck, self._presenter = deck, presenter
        self._show, self._failed, self._speak, self._now, self._leave = show, failed, speak, now, leave
        self._unreadable = unreadable
        self._finish, self._before_next, self._more = finish, before_next, more

    def start(self) -> bool:
        """Show the first question (or the end). False: the deck could not be read and nothing was shown."""
        return self._question(first=True)

    def _page(self, header: str, build: Callable[[Callable], list]) -> None:
        """Build a page whose callbacks act only while it is the page on screen, then show it."""
        shown: dict[str, Callable[[], bool]] = {}

        def live(action: Callable) -> Callable:
            def run(*args):
                check = shown.get("live")
                if check is not None and check():
                    action(*args)
            return run

        widgets = build(live)
        shown["live"] = self._show(header, widgets)

    def _question(self, first: bool = False) -> bool:
        deck, presenter = self._deck, self._presenter
        if self._finish is not None and not first and self._before_next is not None and self._before_next():
            self._finish()
            return True
        try:
            card = deck.next(self._now())
        except Exception as exc:
            # The next card's state could not be read (a busy database). The deck is memory only and
            # ends here: at its start nothing was shown and the caller goes on; later it leaves.
            if self._unreadable is not None:
                self._unreadable(exc)
            if not first:
                self._leave()
            return False
        if card is None and self._finish is not None:
            self._finish()
            return True
        if card is None:
            model = presenter.end(deck.served, deck.correct)
            self._page(model.header, lambda live: pages.deck_end_page(model, on_finish=live(self._leave)))
            return True
        model = presenter.question(card, deck.pending)
        self._page(model.header, lambda live: pages.deck_question_page(
            model,
            on_answer=live(lambda text: self._settle(lambda now: deck.answer(text, now))),
            on_reveal=live(lambda: self._settle(lambda now: deck.answer("", now, revealed=True))),
            on_self_check=live(lambda knew: self._settle(lambda now: deck.self_check(knew, now))),
            on_play=live(self._speak), on_leave=live(self._leave)))
        return True

    def _settle(self, answer: Callable[[object], DeckOutcome]) -> None:
        try:
            outcome = answer(self._now())
        except Exception as exc:   # a busy database: nothing recorded, the same answer may be given again
            self._failed(exc)
            return
        self._result(outcome)

    def _result(self, outcome: DeckOutcome) -> None:
        last = self._deck.pending == 0
        more = self._more() if last and outcome.correct is not None and self._more is not None else True
        model = self._presenter.result(outcome, last=last, more=more)
        self._page(model.header, lambda live: pages.deck_result_page(
            model, on_next=live(self._question), on_leave=live(self._leave)))
