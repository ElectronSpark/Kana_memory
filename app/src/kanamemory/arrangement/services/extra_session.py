"""ExtraSession: extra practice (加练) of one learner store, a mode held in memory (design 12 E1-E8, AC15).

Pure: no clock, database or Toga; every instant is handed in. One per learner
store (StoreArrangement.extra). start() switches the mode on (the learner
pressed 继续加练), stop() switches it off (结束加练, the home page, a new
store); nothing of it is stored. The early kana batches it opens are marked
and ended by the wiring when the mode ends; batch_unchecked remembers, once
per session object (one per store and launch), that a batch an earlier
launch left has still to be looked for.

Entering counts as an Arranged step over the states due at that instant:
the wiring lets the main-lane dispatch serve what it can first, and each
terminal outcome of that dispatch then asks next(now) for one step, carried
out with the lanes' own machinery:

- Arranged: a learned state's due or step has come due; serve it with the
  main lane's own dispatch (a new scheduling pass), then ask again.
- EarlyKana: up to EARLY_KANA_BATCH kana states rated early, one per
  scheduling card, as one new main-lane batch.
- EarlyCourse: one course unit rated early through its own pending question
  (LearningStore.early_head); permit() names the question that may be
  submitted early.
- Practice: up to DECK_SIZE practice-only states as one deck (deck() builds it
  over the session's PracticeSitting).
- Nothing: no learned state can be practised.

What happened is read, not reported: the next call to next() looks at the
states it handed out last. The pool (ports.ExtraStates) is read in full on
start(), after an Arranged or EarlyKana step and at the latest every
``refresh``; after an EarlyCourse step and after deck answers only the states
concerned are read again. With the mode off, available() reuses its own last
read for ``probe_reuse``. A handed-out state that comes back unchanged (the
dispatch could not serve it, its question could not be built or shown) is
set aside ("tried") for one ``refresh`` period, or until it changes: a due
state the dispatch cannot serve costs one scheduling pass per period and
nothing per answer. A failed read leaves the step handed out, so the next
call settles it again. An early state that comes back
rated counts one position of the sitting (PracticeSitting.note_formal), so
the order and the 8-question gap of rules/extra run across early answers and
deck answers alike.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timedelta

from ..model import (Arranged, DeckItem, EarlyCourse, EarlyKana, ExtraEntry, ExtraStep, Lane, Nothing, Practice,
                     PracticeMark, StateRef, StateView)
from ..ports import ExtraStates
from ..rules.extra import eligible, first_segment, practice_run
from .practice_deck import PracticeDeck
from .practice_sitting import PracticeSitting

EARLY_KANA_BATCH = 20               # kana states rated early in one batch (design 12 E1)
DECK_SIZE = 8                       # practice-only items in one deck
REFRESH = timedelta(minutes=5)      # the pool is read in full at least this often
PROBE_REUSE = timedelta(seconds=30)  # the button's read, reused by the next page while the mode is off

NewDeck = Callable[[Sequence[DeckItem], PracticeSitting], PracticeDeck]


def _signature(view: StateView) -> tuple:
    return view.due, view.last_rated, view.in_step


class ExtraSession:
    """The extra-practice mode of one learner store: start/stop, available(), next() and their memory."""

    def __init__(self, states: ExtraStates, *, new_sitting: Callable[[], PracticeSitting], new_deck: NewDeck,
                 refresh: timedelta = REFRESH, probe_reuse: timedelta = PROBE_REUSE,
                 on_stop: Callable[[], object] = lambda: None):
        self._states = states
        self._on_stop = on_stop   # the assembly: forget the extra-practice part (design 12 E9)
        self._new_sitting, self._new_deck = new_sitting, new_deck
        self._refresh, self._probe_reuse = refresh, probe_reuse
        self._probe: tuple[datetime, list[ExtraEntry]] | None = None
        self._batch_unchecked = True
        self._clear()

    def _clear(self) -> None:
        self._active = False
        self._pool: dict[str, ExtraEntry] = {}
        self._read_at: datetime | None = None
        self._tried: dict[str, tuple[tuple, datetime]] = {}   # key -> (signature, until)
        self._handed: tuple[ExtraStep, tuple[ExtraEntry, ...]] | None = None
        self._sitting: PracticeSitting | None = None
        self._deck: PracticeDeck | None = None
        self._seen_position = 0
        self._permit: str | None = None
        self._reported: frozenset[str] = frozenset()

    # --- the mode ------------------------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._active

    @property
    def states(self) -> ExtraStates:
        """The pool reader (the page 加练 counts its parts from one read of it)."""
        return self._states

    @property
    def sitting(self) -> PracticeSitting | None:
        """The sitting of the mode (marks and positions), None while the mode is off."""
        return self._sitting

    def available(self, now: datetime) -> bool:
        """Whether some learned state can be practised at ``now`` (the 继续加练 button).

        Mode off: a full read of the pool (its last one when younger than
        ``probe_reuse``), kept for a start() within ``refresh``. Mode on: the
        session's own pool.
        """
        if self._active:
            self._keep_fresh(now)
            entries = list(self._pool.values())
        elif self._probe is not None and timedelta(0) <= now - self._probe[0] < self._probe_reuse:
            entries = self._probe[1]
        else:
            entries = list(self._states.read(now))
            self._probe = (now, entries)
        return bool(eligible([entry.view for entry in entries], now))

    def start(self, now: datetime) -> None:
        """Switch the mode on with a new sitting. The pool is the read of available() when it is at most
        ``refresh`` old, else a new one (when that read raises, the mode stays off). The start counts as
        an Arranged step over the states due at ``now``: the wiring lets the main-lane dispatch serve
        them first, and the next call to next() settles what it did."""
        probe, self._probe = self._probe, None
        if probe is not None and timedelta(0) <= now - probe[0] < self._refresh:
            entries, read_at = probe[1], probe[0]
        else:
            entries, read_at = list(self._states.read(now)), now
        self._clear()
        self._active = True
        self._sitting = self._new_sitting()
        self._adopt(entries, read_at)
        due = tuple(self._due(now))
        if due:
            self._handed = (Arranged(tuple(entry.ref for entry in due)), due)
            self._reported = frozenset(entry.ref.key for entry in due)

    def stop(self) -> None:
        """Switch the mode off and forget everything of it (also its part, through ``on_stop``)."""
        self._probe = None
        self._clear()
        self._on_stop()

    def learned(self, now: datetime) -> bool:
        """Whether some learned state is in the pool at ``now`` (the home page's 加练… entry, design 12 E9).

        Uses the button's read (available(); reused for ``probe_reuse``) while the mode is off.
        """
        if self._active:
            return bool(self._pool)
        if not (self._probe is not None and timedelta(0) <= now - self._probe[0] < self._probe_reuse):
            self._probe = (now, list(self._states.read(now)))
        return bool(self._probe[1])

    @property
    def batch_unchecked(self) -> bool:
        """Whether an early batch left behind (by an earlier launch, a backup restore, or a mode end whose
        batch could not be ended) has still to be looked for. True for a new session (one per store and
        launch); the wiring looks once while the mode is off and reports with note_batch_checked."""
        return self._batch_unchecked

    def note_batch_checked(self, done: bool = True) -> None:
        """The wiring looked for a left early batch (``done``), or failed to end one (False: look again)."""
        self._batch_unchecked = not done

    # --- steps ---------------------------------------------------------------------------------------

    def next(self, now: datetime) -> ExtraStep:
        """The next step at ``now`` (see the module notes). Raises RuntimeError while the mode is off."""
        if not self._active:
            raise RuntimeError("Extra practice is not on")
        self._settle(now)
        step = self._choose(now)
        if isinstance(step, Arranged):
            self._handed = (step, tuple(self._pool[ref.key] for ref in step.refs))
            self._reported = frozenset(ref.key for ref in step.refs)
        elif isinstance(step, EarlyKana):
            self._handed = (step, step.states)
        elif isinstance(step, EarlyCourse):
            self._handed = (step, (self._pool[step.ref.key],))
        elif isinstance(step, Practice):
            self._handed = (step, ())
        return step

    def deck(self, step: Practice) -> PracticeDeck:
        """The practice deck of the Practice step next() just gave, over the session's sitting (one per step)."""
        if not self._active or self._handed is None or self._handed[0] is not step:
            raise ValueError("This practice step is not the current one")
        if self._deck is None:
            self._deck = self._new_deck(step.items, self._sitting)
        return self._deck

    def arranged_due(self, now: datetime) -> bool:
        """Whether arranged work has come due at ``now`` (asked between two deck cards); a filter in memory,
        after the point reads of the states practised since the last look (or a timed full read)."""
        return bool(self.arranged_refs(now))

    def arranged_refs(self, now: datetime) -> tuple[StateRef, ...]:
        """The states whose arranged work has come due at ``now`` (arranged_due's answer, named)."""
        if not self._active:
            return ()
        self._keep_fresh(now)
        return tuple(entry.ref for entry in self._due(now))

    def arranged_news(self, now: datetime) -> bool:
        """Whether a state is due at ``now`` that was not reported before (by this call, an Arranged step or
        the start): the wiring starts one scheduling pass per new set of due states inside an early batch,
        so a state the pass could not serve does not start another one at every answer. Memory of the
        mode: start and stop reset it."""
        due = frozenset(ref.key for ref in self.arranged_refs(now))
        news = bool(due - self._reported)
        self._reported = due
        return news

    def permit(self, ref: StateRef, question_id: str) -> None:
        """Allow ``question_id`` (the head LearningStore.early_head gave for the EarlyCourse step about
        ``ref``) to be submitted early, until the next step is asked."""
        if not self._active or self._handed is None or self._handed[0] != EarlyCourse(ref):
            raise ValueError("This course unit is not the current early step")
        if not isinstance(question_id, str) or not question_id:
            raise ValueError("A question id is needed")
        self._permit = question_id

    def early_permitted(self, question_id: str) -> bool:
        """Whether the submit of ``question_id`` passes early=True (only the permitted question)."""
        return self._active and self._permit is not None and question_id == self._permit

    # --- what happened -------------------------------------------------------------------------------

    def _settle(self, now: datetime) -> None:
        # The step stays handed out until its reads have succeeded: after a failed read (a busy database)
        # the next call settles the same step.
        step, entries = self._handed if self._handed is not None else (None, ())
        deck = self._deck
        if (isinstance(step, (Arranged, EarlyKana)) or self._stale(now)
                or isinstance(step, Practice) and deck is not None and deck.left_out):
            self._full_read(now)
        else:
            self._reread_practised(now)
            for entry in entries:
                self._point(entry, now)
        self._handed, self._deck, self._permit = None, None, None
        until = now + self._refresh
        if isinstance(step, Practice) and (deck is None or not deck.served and not deck.left_out):
            for item in step.items:   # nothing of it could be built or shown
                if item.ref.key in self._pool:
                    self._tried[item.ref.key] = (_signature(self._pool[item.ref.key].view), until)
        for entry in entries:
            current = self._pool.get(entry.ref.key)
            if current is None:
                continue
            if _signature(current.view) == _signature(entry.view):
                self._tried[entry.ref.key] = (_signature(current.view), until)
            elif not isinstance(step, Arranged) and current.view.last_rated != entry.view.last_rated:
                self._sitting.note_formal(entry.ref)
        self._seen_position = self._sitting.position

    def _keep_fresh(self, now: datetime) -> None:
        if self._stale(now):
            self._full_read(now)
        else:
            self._reread_practised(now)

    def _stale(self, now: datetime) -> bool:
        return self._read_at is None or now - self._read_at >= self._refresh

    def _full_read(self, now: datetime) -> None:
        self._adopt(self._states.read(now), now)

    def _adopt(self, entries: Iterable[ExtraEntry], read_at: datetime) -> None:
        self._pool = {entry.ref.key: entry for entry in entries}
        self._read_at = read_at
        for key, (signature, until) in list(self._tried.items()):
            entry = self._pool.get(key)
            if entry is None or _signature(entry.view) != signature or read_at >= until:
                del self._tried[key]
        if self._sitting is not None:
            self._seen_position = self._sitting.position

    def _point(self, entry: ExtraEntry, now: datetime) -> None:
        current = self._states.reread(self._pool.get(entry.ref.key, entry), now)
        if current is None:
            self._pool.pop(entry.ref.key, None)
            self._tried.pop(entry.ref.key, None)
            return
        self._pool[entry.ref.key] = current
        tried = self._tried.get(entry.ref.key)
        if tried is not None and tried[0] != _signature(current.view):
            del self._tried[entry.ref.key]

    def _reread_practised(self, now: datetime) -> None:
        """Point reads of the states the sitting recorded since the last look (deck answers)."""
        if self._sitting is None or self._sitting.position <= self._seen_position:
            return
        for key, mark in self._sitting.marks.items():
            if mark.last_position >= self._seen_position and key in self._pool:
                self._point(self._pool[key], now)
        self._seen_position = self._sitting.position

    # --- the choice ----------------------------------------------------------------------------------

    def _usable(self, now: datetime) -> list[ExtraEntry]:
        return [entry for entry in self._pool.values()
                if entry.ref.key not in self._tried or now >= self._tried[entry.ref.key][1]]

    def _due(self, now: datetime) -> list[ExtraEntry]:
        return [entry for entry in self._usable(now) if entry.view.due is not None and entry.view.due <= now]

    def _choose(self, now: datetime) -> ExtraStep:
        due = self._due(now)
        if due:
            return Arranged(tuple(entry.ref for entry in due))
        usable = self._usable(now)
        views = [entry.view for entry in usable]
        early = first_segment(views, now)
        if early:
            head = self._pool[early[0].key]
            if head.ref.lane is Lane.COURSE:
                return EarlyCourse(head.ref)
            batch, cards = [], set()
            for view in early:
                entry = self._pool[view.key]
                if entry.ref.lane is Lane.KANA and entry.carrier not in cards:
                    batch.append(entry)
                    cards.add(entry.carrier)
                    if len(batch) == EARLY_KANA_BATCH:
                        break
            return EarlyKana(tuple(batch))
        groups = {entry.ref.key: entry.carrier for entry in usable}
        run = practice_run(views, now, self._group_marks(groups), self._sitting.position, DECK_SIZE, groups=groups)
        if run:
            return Practice(tuple(DeckItem(self._pool[view.key].ref) for view in run))
        return Nothing()

    def _group_marks(self, groups: dict[str, str]) -> dict[str, PracticeMark]:
        """The sitting's marks, each state given the latest mark of its scheduling state (one card, many kinds)."""
        latest: dict[str, PracticeMark] = {}
        for key, mark in self._sitting.marks.items():
            group = groups.get(key, key)
            if group not in latest or mark.last_position > latest[group].last_position:
                latest[group] = mark
        return {key: latest[group] for key, group in groups.items() if group in latest}
