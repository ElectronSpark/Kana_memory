"""FocusService: the learner's focus as the scheduling and the pages use it (design 12 H1-H4).

Pure application layer: the preferences service reads and writes the
setting, a ports.ScopeCatalog turns the scope into content, and ``restart``
(injected by the assembly: practice_round.restart(store, lane="main")) asks
for one new scheduling pass. Pages never call restart themselves: changing
or clearing the focus is ``change``.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from ..model import Focus, FocusScope
from ..ports import ScopeCatalog
from .preferences import ArrangementPreferences


@dataclass(frozen=True)
class ScopeState:
    """The stored scope as shown: ``title`` None when it cannot be named; ``resolves`` False when it is
    gone or disabled (then the scheduling behaves as without a scope; the setting stays as stored)."""

    scope: FocusScope
    title: str | None
    resolves: bool


class FocusService:
    """Reads the focus for the scheduling and changes it as one explicit rearrangement."""

    def __init__(self, preferences: ArrangementPreferences, catalog: ScopeCatalog,
                 restart: Callable[[], object] | None = None):
        self._preferences = preferences
        self._catalog = catalog
        self._restart = restart

    @property
    def catalog(self) -> ScopeCatalog:
        return self._catalog

    def focus(self) -> Focus:
        """The stored focus (one settings read); Focus() when none is stored."""
        return self._preferences.focus()

    def scope_contents(self, focus: Focus | None = None) -> frozenset[str] | None:
        """The content of the focus scope for the scheduling; None without a scope or when it does not resolve.

        Without ``focus`` the stored one is read (one settings read); with
        no scope nothing else is read.
        """
        scope = (self.focus() if focus is None else focus).scope
        return None if scope is None else self._catalog.contents(scope)

    def scope_state(self, focus: Focus | None = None) -> ScopeState | None:
        """The stored (or given) focus scope with its title and whether it resolves; None without a scope."""
        scope = (self.focus() if focus is None else focus).scope
        if scope is None:
            return None
        return ScopeState(scope, self._catalog.title(scope), self._catalog.contents(scope) is not None)

    def change(self, focus: Focus) -> Focus:
        """Store ``focus`` and rearrange the current main round once (H4); returns the stored focus.

        practice_round.restart ends this round's frozen order: the next
        "继续学习" collects a new round in the new focus order. A half-done
        answer, its draft and an opened teaching block still come first there
        (they are ahead of every scoped tier). Nothing happens when ``focus``
        equals the stored one. A Focus of the wrong type raises ValueError
        and changes nothing.
        """
        if not isinstance(focus, Focus):
            raise ValueError("A Focus is required")
        if focus == self.focus():
            return focus
        stored = self._preferences.set_focus(focus)
        if self._restart is not None:
            self._restart()
        return stored

    def with_scope(self, scope: FocusScope | None) -> Focus:
        """change() to the stored focus with another scope (the tilts kept); None removes the scope."""
        return self.change(replace(self.focus(), scope=scope))

    def clear(self) -> Focus:
        """change() to no focus at all (取消重点): even tilt, normal kana, no scope."""
        return self.change(Focus())
