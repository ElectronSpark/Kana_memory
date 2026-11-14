"""StoreEventSource: the study clock's events read from a learner store (design 12 B6, WP-B2).

Implements ports.EventSource. Gathers the timed study actions of both lanes,
gives each its study-day key and attributes it with rules/clock.attribute.
All reads go through the store's public query owner (TeachingQueries) and
public methods; no SQL here. Nothing is stored.

Events (B6):
- kana formal answers (review_event) and auxiliary word-spelling answers
  (word_spelling_attempt); course answers (learning_event, role primary or
  unresolved);
- teaching marks: a completed course or added-ability guide (the shared
  first_teaching_dispatch.guide_evidence predicate, so a diagnostic title
  exposure is not one), a real kana introduction (kana_introductions_v1,
  prior-knowledge / legacy routes left out).
AI practice and tutor answers are not events. Only teaching marks are
teaching actions (rules/clock.attribute); a first rating is an ordinary answer.

The moment of an answer is COALESCE(finalized_at_utc, occurred_at_utc) of its
log, the instant the first-teaching and review dispatch probes use; a
word-spelling attempt has only occurred_at_utc.

State identity: a course unit is its unit_id; a kana state is (content, script,
ability), the ability being that of the skill the answer was routed through
(kana_ability_bridge.SKILL_TO_ABILITY), so one state's answers may sit on
different cards. Word-skill answers are not a scheduling state: they are kana
practice outright (B6: "含带单词的假名题").

First formal rating: a state's earliest logged formal answer, unless the state
was learned before the log: some card of it shows, in the scheduling state
before one of its earliest logged answers, a review earlier than the state's
earliest logged answer, or some card of it has reviews and no logged answer at
all (TeachingQueries.legacy_kana_states_without_events). Such a state has no
first rating left, and every logged answer of it is review. A kana declared
known without a card keeps its first rating: its first answer is new content.

Reading (design 12 section 10 (2)): the first read of a source object reads
each source newest first from now - LOOKBACK, at most ROW_LIMIT rows; when the
store's read revision changes later, only rows appended since are read (the
answer logs are append-only while the app runs) and added to the events kept
in memory, so the read after one more answer does not grow with the history.
After every read each source's cut is recomputed as a fresh read would make
it, so the kept events always equal a fresh read's. Teaching marks are small
and read in full each time. A full re-read happens when now moves back before
the last read, when another connection committed (PRAGMA data_version), or
when a log's last-read row is gone or replaced (an import or reset on this
connection). A read inside an open transaction is a full read that is kept
nowhere: a rollback could take its writes back unseen.

Four reads beside the window (design 12 UI1, UI2, UI4): learned() names the
lessons and kana first learned on given study days: its candidates come from
the window's own first ratings and teaching marks (a cached lookup of each
new course unit's lesson), and a candidate counts only when the lesson or
kana itself was first learned on those days (_ItemOrigins: the earliest of
its teaching marks and of the first formal answers of all its abilities or
skills, by indexed lookups for the candidates only; an item found old stays
old). window_cut() says whether the window is complete: None, or the instant
at or before which a source's row limit cut it. period() reads a period in
full, without the row limit, cut or caching, for the records page; and
has_history() tells whether the store has any answer or teaching mark at all.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json

from ...domain import WORD_SKILLS
from ..model import ClockEvent, FirstRatings, NewLearning, PeriodActivity, RawEvent, Unit
from ..rules.clock import LOOKBACK, attribute

# Rows read per source, newest first. The window needs at most 30 minutes of
# charged study time (clock.WINDOW = 1800 s); an ordinary answer is charged its
# gap to the previous action, which for a real answer is a few seconds at
# least, and an action opening a sitting is charged 20 s or more. 2,000 rows of
# one source therefore reach past the window unless that source averaged under
# 0.9 s per row; only then is the oldest part cut (see _AnswerLogs._take).
ROW_LIMIT = 2000

# A state learned before the log gets a first rating just before its earliest
# logged answer, so that rules/clock.attribute treats every logged answer as review.
_BEFORE_THE_LOG = timedelta(microseconds=1)

# A state with a formal answer this long before the lookback starts is settled:
# its first rating, and the same-day step on that rating's study day (a study
# day is at most 25 hours), both lie before the lookback, so every answer in
# the window is review. Its old history then needs no ranking (design 12 O7).
SETTLED_MARGIN = timedelta(days=2)

# has_history() looks at every teaching mark, whenever it was made.
_EARLIEST = datetime.min.replace(tzinfo=timezone.utc)


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _sort_key(event: ClockEvent):
    # Ties are ordered by content, never by row order, so an imported or
    # reopened store gives the same sequence.
    return event.at, event.unit.value, event.teaching


class _Reread(Exception):
    """An incremental read cannot be trusted; the caller reads in full."""


@dataclass
class _Batch:
    """Events of one read of the answer logs, each with the source (log and role) it came from."""

    raw: list = field(default_factory=list)     # (source, RawEvent), still to be attributed
    direct: list = field(default_factory=list)  # (source, ClockEvent), kana practice outright
    keys: dict = field(default_factory=dict)    # state -> ("course", unit_id) | ("kana", content_id)
    cuts: dict = field(default_factory=dict)    # cut source -> the instant at or before which it is incomplete
    tails: dict = field(default_factory=dict)   # log -> (rowid, id) of its newest row, or None
    slots: dict = field(default_factory=dict)   # kana state -> (content_id, script)


class _AnswerLogs:
    """Rows of the three answer logs as clock events, and first ratings of their states."""

    def __init__(self, store, ability_of: Callable[[str], str]):
        self._store = store
        self._ability_of = ability_of

    def day(self, at: datetime) -> str:
        return self._store.study_day(at).isoformat()

    def kana_state(self, content_id: str, script: str, skill: str) -> str:
        return f"kana:{content_id}:{script}:{self._ability_of(skill)}"

    def read(self, since: datetime, course: bool, tails: Mapping | None = None) -> _Batch:
        """Rows at or after ``since``: bounded and cut (``tails`` None) or only those appended after ``tails``.

        Raises _Reread when a log's tail row is gone or replaced, or when an
        incremental read reaches the row limit.
        """
        queries = self._store.teaching_queries
        logs = ("review_event", "word_spelling_attempt") + (("learning_event",) if course else ())
        if tails is not None and any(tails.get(log) and not queries.clock_log_holds(log, tails[log]) for log in logs):
            raise _Reread

        def after(log):
            return None if tails is None else (tails.get(log) or (0, None))[0]

        batch = _Batch()
        for role in ("primary", "unresolved") if course else ():
            source = "learning_event:" + role
            for row in self._take(queries.clock_course_events(role, since, ROW_LIMIT + 1, after("learning_event")),
                                  batch, source, tails is not None):
                self._course_row(batch, source, role, row)
        for row in self._take(queries.clock_kana_events(since, ROW_LIMIT + 1, after("review_event")),
                              batch, "review_event", tails is not None):
            self._kana_row(batch, row)
        for row in self._take(queries.clock_word_attempts(since, ROW_LIMIT + 1, after("word_spelling_attempt")),
                              batch, "word_spelling_attempt", tails is not None):
            self._word_row(batch, row)
        batch.tails = {log: queries.clock_log_tail(log) for log in logs}
        return batch

    def read_all(self, since: datetime, course: bool) -> _Batch:
        """Every row at or after ``since``: no row limit, no cut, no tails (the uncached period read).

        The same queries as read() with SQLite's "no limit" (-1), newest first.
        """
        queries = self._store.teaching_queries
        batch = _Batch()
        for role in ("primary", "unresolved") if course else ():
            source = "learning_event:" + role
            for row in queries.clock_course_events(role, since, -1):
                self._course_row(batch, source, role, row)
        for row in queries.clock_kana_events(since, -1):
            self._kana_row(batch, row)
        for row in queries.clock_word_attempts(since, -1):
            self._word_row(batch, row)
        return batch

    def _course_row(self, batch: _Batch, source: str, role: str, row) -> None:
        at = _instant(row["at"])
        state = "course:" + row["unit_id"]
        batch.keys[state] = ("course", row["unit_id"])
        batch.raw.append((source, RawEvent(at, self.day(at), "course", state, formal=role == "primary")))

    def _kana_row(self, batch: _Batch, row) -> None:
        at = _instant(row["at"])
        if row["card_skill"] in WORD_SKILLS:
            batch.direct.append(("review_event", ClockEvent(at, self.day(at), Unit.KANA)))
        else:
            state = self.kana_state(row["content_id"], row["script"], row["skill"])
            batch.keys[state] = ("kana", row["content_id"])
            batch.slots[state] = (row["content_id"], row["script"])
            batch.raw.append(("review_event", RawEvent(at, self.day(at), "kana", state, formal=True)))

    def _word_row(self, batch: _Batch, row) -> None:
        at = _instant(row["at"])
        batch.direct.append(("word_spelling_attempt", ClockEvent(at, self.day(at), Unit.KANA)))

    @staticmethod
    def _take(rows: Iterable, batch: _Batch, source: str, incremental: bool) -> list:
        """Rows of a source read with limit + 1: cut only when the extra row exists.

        Then only rows newer than the extra row's instant are kept, so a source
        read completely is never cut and an instant group is never split.
        """
        rows = list(rows)
        if len(rows) <= ROW_LIMIT:
            return rows
        if incremental:
            raise _Reread
        cut = _instant(rows[ROW_LIMIT]["at"])
        batch.cuts[source] = cut
        return [row for row in rows[:ROW_LIMIT] if _instant(row["at"]) > cut]

    def first_ratings(self, states: Mapping[str, tuple[str, str]],
                      settled_before: datetime | None = None) -> dict[str, tuple[FirstRatings, bool]]:
        """{state: (FirstRatings, final)} of these states ({state: (lane, unit or content id)}).

        A state with a formal answer before ``settled_before`` is settled: its
        first rating and same-day step lie before that instant, so every
        later answer is review. Its earliest answer stands in for the first
        rating (final: True) and only the other states are ranked, so the
        read grows with the answers near the window, not with the history.
        """
        settled = self._settled(states, settled_before) if settled_before is not None else {}
        result = {state: (FirstRatings(at, self.day(at)), True) for state, at in settled.items()}
        rest = [key for state, key in states.items() if state not in result]
        result.update(self._ranked({key for lane, key in rest if lane == "course"},
                                   {key for lane, key in rest if lane == "kana"}))
        return result

    def _settled(self, states: Mapping[str, tuple[str, str]], before: datetime) -> dict[str, datetime]:
        """{state: its earliest formal answer} of the states answered before ``before``; one index seek per unit or card.

        Only a card's earliest answer is looked at: a state whose old answers
        all sit behind another ability's on a shared card is not settled here
        and is ranked instead (exact either way).
        """
        queries = self._store.teaching_queries
        units = sorted({key for lane, key in states.values() if lane == "course"})
        contents = sorted({key for lane, key in states.values() if lane == "kana"})
        found: dict[str, datetime] = {}

        def add(state, value):
            at = _instant(value) if value else None
            if at is not None and at < before and state in states:
                found[state] = min(at, found.get(state, at))

        for row in queries.course_earliest_answers(units) if units else ():
            add("course:" + row["unit_id"], row["at"])
        for row in queries.kana_card_earliest_answers(contents, WORD_SKILLS) if contents else ():
            add(self.kana_state(row["content_id"], row["script"], row["skill"]), row["at"])
        return found

    def _ranked(self, units: Iterable[str], contents: Iterable[str]) -> dict[str, tuple[FirstRatings, bool]]:
        """{state: (FirstRatings, learned before the log)} of the states of these units and contents."""
        queries = self._store.teaching_queries
        units, contents = sorted(units), sorted(contents)
        earliest: dict[str, list] = {}

        def add(state, row):
            last = row["last_review"]
            earliest.setdefault(state, []).append(
                (_instant(row["at"]), _instant(row["occurred"]), _instant(last) if last else None))

        for row in queries.course_first_ratings(units) if units else ():
            add("course:" + row["unit_id"], row)
        for row in queries.kana_first_ratings(contents, WORD_SKILLS) if contents else ():
            add(self.kana_state(row["content_id"], row["script"], row["skill"]), row)
        legacy = self._legacy_states(contents) if contents else set()
        return {state: self._first(rows, state in legacy) for state, rows in earliest.items()}

    def _first(self, rows: list, legacy: bool) -> tuple[FirstRatings, bool]:
        """FirstRatings of one state from its earliest rows (at, occurred, last_review)."""
        rows.sort(key=lambda value: value[0])
        first = rows[0][0]
        opened = min(occurred for _, occurred, _ in rows)
        earlier = [last for _, _, last in rows if last is not None and last < opened]
        if earlier or legacy:
            before = min([first - _BEFORE_THE_LOG, *earlier])
            return FirstRatings(before, self.day(before)), True
        day = self.day(first)
        follow = rows[1][0] if len(rows) > 1 and self.day(rows[1][0]) == day else None
        return FirstRatings(first, day, follow), False

    def _legacy_states(self, contents: Iterable[str]) -> set[str]:
        """Kana states of these contents with a card that has reviews and no logged answer."""
        wanted = set(contents)
        return {self.kana_state(row["content_id"], row["script"], row["skill"])
                for row in self._store.teaching_queries.legacy_kana_states_without_events()
                if row["content_id"] in wanted and row["skill"] not in WORD_SKILLS}


class _TeachingMarks:
    """Completed teaching: course and added-ability guides, real kana introductions (small: read in full)."""

    def __init__(self, store, introductions: Callable, guide_evidence: Callable):
        self._store = store
        self._introductions = introductions
        self._guide_evidence = guide_evidence

    def read(self, since: datetime, course: bool, day: Callable[[datetime], str]) -> list[ClockEvent]:
        return self.events(self.entries(since, course), day)

    def entries(self, since: datetime, course: bool) -> list[tuple]:
        """(instant, lane, state, key) of the marks at or after ``since``.

        ``key`` names what was taught: ("course", content id) or ("kana", (content id, script)).
        """
        marks = []
        if course:
            queries = self._store.teaching_queries
            for row in queries.instruction_rows(queries.ability_states()):
                value = row["guided_at_utc"]
                if (value and _instant(value) >= since
                        and self._guide_evidence(row, json.loads(row["checkpoint_json"])).guided):
                    scope = dict(row).get("instruction_scope")
                    marks.append((_instant(value), "course", f"course-guide:{row['content_id']}"
                                  + (f":{scope}" if scope else ""), ("course", row["content_id"])))
        for (content_id, script), at in self._introductions(self._store).items():
            if at >= since:
                marks.append((at, "kana", f"kana-intro:{content_id}:{script}", ("kana", (content_id, script))))
        return marks

    @staticmethod
    def events(entries, day: Callable[[datetime], str]) -> list[ClockEvent]:
        # A mark is new content whatever state it names; the state only says what was taught.
        return attribute([RawEvent(at, day(at), lane, state, teaching_mark=True) for at, lane, state, _ in entries], {})

    def earliest(self, items: Iterable[tuple]) -> dict[tuple, datetime]:
        """{item: its earliest teaching mark, whenever made} for these items (("course", id) / ("kana", (id, script))).

        The guide evidence is decided only for rows of the asked lessons.
        """
        lessons = {identity for lane, identity in items if lane == "course"}
        slots = {identity for lane, identity in items if lane == "kana"}
        found: dict[tuple, datetime] = {}

        def add(item, at):
            found[item] = min(at, found.get(item, at))

        if lessons:
            queries = self._store.teaching_queries
            for row in queries.instruction_rows(queries.ability_states()):
                value = row["guided_at_utc"]
                if (value and row["content_id"] in lessons
                        and self._guide_evidence(row, json.loads(row["checkpoint_json"])).guided):
                    add(("course", row["content_id"]), _instant(value))
        if slots:
            for slot, at in self._introductions(self._store).items():
                if slot in slots:
                    add(("kana", slot), at)
        return found


class _ItemOrigins:
    """When each lesson and each kana was first learned (design 12 UI2, UI4: 新学 counts an item once).

    A lesson (course content id) began at the earliest of its teaching marks
    and of the earliest formal answers of all its units (every ability and
    scope); a kana (content id, script) at the earliest of its introduction
    and of the earliest formal answers of all its cards (word cards left
    out). An earliest answer whose scheduling state already had a review
    (state_before has a last_review), or a card with reviews and no logged
    answer, means the item was learned before the log: it is older than any
    period (None). Indexed lookups for the asked items only (one seek per unit
    or card, through learning_teaching_primary_final and
    reviews_card_first_finalized), plus the teaching marks of the asked
    lessons. An item found older than the period asked stays old: that answer
    is kept (rows appended later are later); the others are looked up again.
    """

    def __init__(self, store, marks: _TeachingMarks, day: Callable[[datetime], str]):
        self._store = store
        self._marks = marks
        self._day = day
        self._old: dict[tuple, datetime | None] = {}

    def clear(self) -> None:
        self._old.clear()

    def first_days(self, items: Iterable[tuple], first_day: str, *, keep: bool = True) -> dict[tuple, str | None]:
        """{item: the study day it was first learned, None when before the log} of the items found.

        ``first_day``: the earliest study day of the period asked; an item
        learned before it is kept as old when ``keep``.
        """
        items = set(items)
        result: dict[tuple, str | None] = {}
        for item in items & self._old.keys():
            at = self._old[item]
            result[item] = None if at is None else self._day(at)
        wanted = items - self._old.keys()
        for item, at in self._lookup(wanted).items() if wanted else ():
            day = None if at is None else self._day(at)
            result[item] = day
            if keep and (day is None or day < first_day):
                self._old[item] = at
        return result

    def _lookup(self, items: set[tuple]) -> dict[tuple, datetime | None]:
        queries = self._store.teaching_queries
        found: dict[tuple, datetime | None] = dict(self._marks.earliest(items))
        before_log: set[tuple] = set()

        def add(item, value, last_review):
            if last_review or item in before_log:
                before_log.add(item)
                return
            if value:
                at = _instant(value)
                current = found.get(item)
                found[item] = at if current is None else min(at, current)

        lessons = sorted(identity for lane, identity in items if lane == "course")
        for row in queries.course_content_first_answers(lessons) if lessons else ():
            add(("course", row["content_id"]), row["at"], row["last_review"])
        slots = {identity for lane, identity in items if lane == "kana"}
        contents = sorted({content for content, _ in slots})
        for row in queries.kana_card_first_answers(contents, WORD_SKILLS) if contents else ():
            slot = (row["content_id"], row["script"])
            if slot in slots:
                add(("kana", slot), row["at"], row["last_review"] or (row["reviews"] > 0 and row["at"] is None))
        for item in before_log:
            found[item] = None
        return found


class _FirstRatingsCache:
    """FirstRatings per scheduling state, kept across reads once final.

    An entry is final once it has a follow-up, once the study day of its first
    rating is before the study day of now, or when the state was learned before
    the log. An appended answer on or before that day, or before the first
    rating, reopens it (reopen), so finality holds whatever order answers are
    appended in. Only states not known or not final are queried.
    """

    def __init__(self, logs: _AnswerLogs):
        self._logs = logs
        self.known: dict[str, FirstRatings] = {}
        # States whose known entry is not their real first rating (learned
        # before the log, or settled long before the lookback): never "first learned".
        self.not_first: set[str] = set()
        self._final: set[str] = set()

    def reopen(self, appended: Iterable[RawEvent]) -> None:
        """Reopen the final states that an appended formal answer may change."""
        for raw in appended:
            info = self.known.get(raw.state)
            if raw.formal and info is not None and (raw.day <= info.day or raw.at < info.first):
                self._final.discard(raw.state)

    def refresh(self, keys: Mapping[str, tuple[str, str]], today: str, since: datetime | None = None) -> set[str]:
        """Query the open states among ``keys``; return the states whose FirstRatings changed.

        ``since`` is the start of the lookback: a state answered more than
        SETTLED_MARGIN before it is review for every event of the window.
        """
        open_states = {state: key for state, key in keys.items() if state not in self._final}
        if not open_states:
            return set()
        fresh = self._logs.first_ratings(open_states, None if since is None else since - SETTLED_MARGIN)
        changed = set()
        for state in open_states:
            info, before_log = fresh.get(state, (None, False))
            if info != self.known.get(state):
                changed.add(state)
                if info is None:
                    self.known.pop(state, None)
                else:
                    self.known[state] = info
            if info is not None and before_log:
                self.not_first.add(state)
            else:
                self.not_first.discard(state)
            if info is not None and (before_log or info.follow_up is not None or info.day < today):
                self._final.add(state)
        return changed


class _Cuts:
    """Per-source cuts, kept equal to the cuts a fresh read would make.

    A fresh read cuts a source when more than ROW_LIMIT of its rows lie in the
    lookback, at the instant of the (ROW_LIMIT + 1)-th newest, and drops every
    event at or before the latest cut. Kept rows of a cut source are all newer
    than its cut, so only rows at or before it were left unread: the cut moves
    to the (ROW_LIMIT + 1)-th newest kept row once there are more than
    ROW_LIMIT, stays while the rows at the cut instant are in the lookback, and
    goes when they leave it.
    """

    def __init__(self, cuts: Mapping[str, datetime]):
        self._at = dict(cuts)

    def keeps(self, source: str, at: datetime) -> bool:
        cut = self._at.get(source)
        return cut is None or at > cut

    def update(self, since: datetime, rows: Iterable[tuple[str, datetime]]) -> None:
        """Recompute the cuts from the kept rows (source, instant) of the lookback."""
        self._at = {source: cut for source, cut in self._at.items() if cut >= since}
        per_source: dict[str, list[datetime]] = {}
        for source, at in rows:
            if at >= since and self.keeps(source, at):
                per_source.setdefault(source, []).append(at)
        for source, instants in per_source.items():
            if len(instants) > ROW_LIMIT:
                self._at[source] = sorted(instants, reverse=True)[ROW_LIMIT]

    @property
    def latest(self) -> datetime | None:
        return max(self._at.values(), default=None)


class _Window:
    """The attributed events of one store kept in memory and brought up to date by appended rows."""

    def __init__(self, store, logs: _AnswerLogs, marks: _TeachingMarks):
        self._store = store
        self._logs = logs
        self._marks = marks
        self._firsts = _FirstRatingsCache(logs)
        self._revision = None
        self._read_at: datetime | None = None
        self._tails: dict | None = None
        self._cuts = _Cuts({})
        self._pairs: list[tuple[str, RawEvent, ClockEvent]] = []
        self._direct: list[tuple[str, ClockEvent]] = []
        self._keys: dict[str, tuple[str, str]] = {}
        self._slots: dict[str, tuple[str, str]] = {}
        self.marks: tuple = ()   # the teaching-mark entries of the last read
        self.events: tuple[ClockEvent, ...] = ()

    def current(self, now: datetime, revision) -> bool:
        return self._revision == revision and now >= self._read_at

    def can_follow(self, now: datetime, revision) -> bool:
        """Whether appended rows can bring this window up to date (else read in full)."""
        return self._revision is not None and now >= self._read_at and revision[1] == self._revision[1]

    def read(self, now: datetime, revision) -> _Window:
        since = now - LOOKBACK
        course = self._store.queries.has_course_history()
        batch = self._logs.read(since, course, self._tails)
        if self._tails is None:
            self._cuts = _Cuts(batch.cuts)
        self._firsts.reopen(raw for _, raw in batch.raw)
        self._keys.update(batch.keys)
        self._slots.update(batch.slots)
        changed = self._firsts.refresh(self._keys, self._logs.day(now), since)
        known = self._firsts.known
        pairs = [(source, raw, attribute([raw], known)[0] if raw.state in changed else event)
                 for source, raw, event in self._pairs]
        pairs += [(source, raw, event) for (source, raw), event
                  in zip(batch.raw, attribute([raw for _, raw in batch.raw], known))]
        direct = self._direct + batch.direct
        self._cuts.update(since, [(source, raw.at) for source, raw, _ in pairs] + [(s, e.at) for s, e in direct])

        def kept(source, at):
            return at >= since and self._cuts.keeps(source, at)

        kept_pairs = [pair for pair in pairs if kept(pair[0], pair[1].at)]
        if len(kept_pairs) < len(pairs):
            self._keys = {raw.state: self._keys[raw.state] for _, raw, _ in kept_pairs}
        self._pairs = kept_pairs
        self._direct = [(source, event) for source, event in direct if kept(source, event.at)]
        self.marks = tuple(self._marks.entries(since, course))
        events = sorted([event for _, _, event in self._pairs] + [event for _, event in self._direct]
                        + self._marks.events(self.marks, self._logs.day), key=_sort_key)
        # Drop what may be incomplete: everything at or before the cut of a source that hit its limit.
        cutoff = self._cuts.latest
        self.events = tuple(events if cutoff is None else (event for event in events if event.at > cutoff))
        self._revision, self._read_at, self._tails = revision, now, batch.tails
        return self

    @property
    def cut(self) -> datetime | None:
        """The instant at or before which this window may miss events (a source hit ROW_LIMIT), or None."""
        return self._cuts.latest

    def learned(self, now: datetime, days: frozenset[str], contents: Callable, first_days: Callable) -> NewLearning:
        """Lessons and kana first learned on ``days`` among the states and marks this window holds."""
        firsts = self._firsts
        return _new_learning(firsts.known, firsts.not_first, self._keys, self._slots, self.marks,
                             now, days, contents, self._logs.day, first_days)


def _new_learning(known: Mapping[str, FirstRatings], not_first, keys: Mapping, slots: Mapping, marks,
                  now: datetime, days: frozenset[str], contents: Callable, day: Callable,
                  first_days: Callable) -> NewLearning:
    """NewLearning from first ratings ({state: FirstRatings}) and teaching-mark entries.

    Candidates: a state whose real first formal rating (not in
    ``not_first``) is on one of ``days`` and not after ``now``, as its lesson
    (``contents``: unit ids -> {unit id: content id}) or its (content,
    script); a mark on those days, as what it taught. A candidate counts only
    when the item itself was first learned on those days (``first_days``:
    _ItemOrigins.first_days), so a newly opened ability of an older lesson, or
    a new skill of an older kana, is new content for the clock but not a new
    lesson or a new kana.
    """
    units, kana = set(), set()
    for state, info in known.items():
        if state in not_first or state not in keys or info.day not in days or info.first > now:
            continue
        lane, identity = keys[state]
        if lane == "course":
            units.add(identity)
        elif state in slots:
            kana.add(slots[state])
    lessons = set(contents(sorted(units)).values()) if units else set()
    for at, _, _, (lane, identity) in marks:
        if at <= now and day(at) in days:
            (lessons if lane == "course" else kana).add(identity)
    candidates = {("course", lesson) for lesson in lessons} | {("kana", slot) for slot in kana}
    if not candidates:
        return NewLearning()
    first = first_days(candidates, min(days))
    # A candidate has a mark or an answer on ``days``, so its first day is on or before them; one not found keeps it.
    counted = {item for item in candidates if item not in first or first[item] in days}
    return NewLearning(frozenset(identity for lane, identity in counted if lane == "course"),
                       frozenset(identity for lane, identity in counted if lane == "kana"))


class StoreEventSource:
    """ports.EventSource over a StudyStore (one connection, its owning thread)."""

    def __init__(self, store):
        from ...first_teaching_dispatch import guide_evidence
        from ...first_teaching_kana import taught_introductions
        from ...kana_ability_bridge import SKILL_TO_ABILITY
        self._store = store
        self._logs = _AnswerLogs(store, lambda skill: SKILL_TO_ABILITY.get(skill, skill))
        self._marks = _TeachingMarks(store, taught_introductions, guide_evidence)
        self._window: _Window | None = None
        self._unit_contents: dict[str, str] = {}   # a unit id is a hash of its content and ability: never changes
        self._origins = _ItemOrigins(store, self._marks, self._logs.day)

    def _new_window(self) -> _Window:
        return _Window(self._store, self._logs, self._marks)

    def _fresh_window(self) -> _Window:
        """A window for a full re-read; what is kept of items' origins goes with the old one (an import, a reset)."""
        self._origins.clear()
        return self._new_window()

    def clock_events(self, now: datetime) -> list[ClockEvent]:
        """Attributed events in [now - LOOKBACK, now], oldest first."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("The study clock needs an aware instant")
        revision = self._store.read_revision()
        if self._store.in_transaction:
            events = self._new_window().read(now, revision).events
        else:
            window = self._window
            if window is None or not window.current(now, revision):
                if window is None or not window.can_follow(now, revision):
                    window = self._fresh_window()
                try:
                    window.read(now, revision)
                except _Reread:
                    window = self._fresh_window().read(now, revision)
                except Exception:
                    # A read that failed part-way leaves the window half updated: start afresh next time.
                    self._window = None
                    self._origins.clear()
                    raise
            self._window = window
            events = window.events
        lower = now - LOOKBACK
        return [event for event in events if lower <= event.at <= now]

    def _current_window(self, now: datetime) -> _Window:
        """The window clock_events(now) maintains, brought up to date (a throwaway one inside a transaction)."""
        self.clock_events(now)
        window = self._window
        if self._store.in_transaction or window is None:
            window = self._new_window().read(now, self._store.read_revision())
        return window

    def window_cut(self, now: datetime) -> datetime | None:
        """Where the window clock_events(now) keeps is complete: None, or the instant of its cut.

        None: it holds every event of [now - LOOKBACK, now]. Otherwise a source
        hit ROW_LIMIT inside the lookback and every event at or before the
        returned instant was dropped, so the window is complete since any
        instant after it (and only then). Costs what clock_events costs.
        """
        return self._current_window(now).cut

    def learned(self, now: datetime, days) -> NewLearning:
        """Lessons and kana first learned on the study days ``days`` (keys as ClockEvent.day), up to ``now``.

        From the window clock_events(now) maintains (brought up to date first,
        so it costs what clock_events costs, plus a cached lookup of each newly
        seen course unit's lesson, and _ItemOrigins' indexed lookups for the
        few candidates not yet known to be old). An item counts once, on the
        study day it was first learned (_ItemOrigins). The window reaches back
        LOOKBACK; when a source hit ROW_LIMIT inside it, what lies before its
        cut is not seen (window_cut says so; period() is the full read).
        """
        window = self._current_window(now)
        return window.learned(now, frozenset(days), self._contents, self._first_days)

    def _first_days(self, items, first_day: str) -> dict:
        # Nothing read inside an open transaction is kept: a rollback could take its rows back.
        return self._origins.first_days(items, first_day, keep=not self._store.in_transaction)

    def period(self, now: datetime, since: datetime, days) -> PeriodActivity:
        """Every attributed event in [since, now] and what was first learned on ``days``: a full, uncached read.

        No row limit and no cut, so it stays exact however many answers the
        period holds; first ratings and teaching marks are found as for the
        window (the same attribution). Nothing is kept: the window that
        dispatch uses is not touched (only which items are known to be old,
        _ItemOrigins, is shared with learned()). For the records page; its
        cost grows with the rows in the period.
        """
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("The study clock needs an aware instant")
        course = self._store.queries.has_course_history()
        batch = self._logs.read_all(since, course)
        firsts = self._logs.first_ratings(batch.keys, since - SETTLED_MARGIN)
        known = {state: info for state, (info, _) in firsts.items()}
        not_first = {state for state, (_, flag) in firsts.items() if flag}
        marks = self._marks.entries(since, course)
        events = sorted(attribute([raw for _, raw in batch.raw], known) + [event for _, event in batch.direct]
                        + self._marks.events(marks, self._logs.day), key=_sort_key)
        learned = _new_learning(known, not_first, batch.keys, batch.slots, marks, now, frozenset(days),
                                self._contents, self._logs.day, self._first_days)
        return PeriodActivity(tuple(event for event in events if since <= event.at <= now), learned)

    def has_history(self) -> bool:
        """Whether the store holds any row of the three answer logs or any teaching mark.

        Tail reads (one row per log, by rowid); the marks are read only when
        every log is empty.
        """
        queries = self._store.teaching_queries
        course = self._store.queries.has_course_history()
        logs = ("review_event", "word_spelling_attempt") + (("learning_event",) if course else ())
        if any(queries.clock_log_tail(log) is not None for log in logs):
            return True
        return bool(self._marks.entries(_EARLIEST, course))

    def _contents(self, unit_ids) -> dict[str, str]:
        """{unit id: content id} of these course units: a per-source cache and one lookup for the rest."""
        missing = [unit for unit in unit_ids if unit not in self._unit_contents]
        if missing:
            for row in self._store.teaching_queries.course_unit_contents(missing):
                self._unit_contents[row["unit_id"]] = row["content_id"]
        return {unit: self._unit_contents[unit] for unit in unit_ids if unit in self._unit_contents}
