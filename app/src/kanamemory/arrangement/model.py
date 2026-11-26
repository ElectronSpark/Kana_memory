"""Immutable data objects of the study arrangement (design 12).

Pure data: no Toga, database, clock or other kanamemory imports. Instants are
aware datetimes handed in by the caller; nothing here reads the time.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Orientation(str, Enum):
    """The learner's standing choice. THOROUGH is the rule the app had before."""

    BREADTH = "breadth"
    THOROUGH = "thorough"


class Tilt(str, Enum):
    """How the time between new content and review leans (design 12 H1)."""

    ONLY_NEW = "only_new"
    NEW = "new"
    EVEN = "even"
    REVIEW = "review"
    ONLY_REVIEW = "only_review"


class KanaTilt(str, Enum):
    """How much of the review time kana practice takes (design 12 H1)."""

    LESS = "less"
    NORMAL = "normal"
    MORE = "more"


class Unit(str, Enum):
    """The three places study time can go."""

    NEW = "new"        # first teaching of any scheduling state, and its same-day step
    COURSE = "course"  # due reviews of the course lane
    KANA = "kana"      # due reviews of the kana lane


class Side(str, Enum):
    """The first level of the share choice."""

    NEW = "new"
    REVIEW = "review"


SCOPE_KINDS = ("route", "node", "library", "family")


# Between the route id and the node id of a node scope's id; route ids never contain it.
NODE_SEPARATOR = "|"


@dataclass(frozen=True)
class FocusScope:
    """What the learner concentrates on: one route, route node, library or family.

    Ids: a route is its route id; a node (a stage, unit or section of a
    route) is "<route id>|<node id>" (node / split_node); a library is its
    library id; a family is word, grammar or pronunciation.
    """

    kind: str
    id: str

    def __post_init__(self):
        if self.kind not in SCOPE_KINDS:
            raise ValueError("Unknown focus scope kind")
        if not isinstance(self.id, str) or not 1 <= len(self.id) <= 1024:
            raise ValueError("Invalid focus scope identity")

    @classmethod
    def node(cls, route_id: str, node_id: str) -> FocusScope:
        """The scope of a stage, unit or section ``node_id`` of the route ``route_id``."""
        return cls("node", f"{route_id}{NODE_SEPARATOR}{node_id}")

    @staticmethod
    def split_node(scope_id: str) -> tuple[str, str] | None:
        """(route id, node id) of a node scope's id; None when it is not one."""
        route_id, separator, node_id = scope_id.partition(NODE_SEPARATOR)
        return (route_id, node_id) if separator and route_id and node_id else None


@dataclass(frozen=True)
class Focus:
    """The learner's current focus: a tilt, a kana tilt and an optional scope."""

    tilt: Tilt = Tilt.EVEN
    kana: KanaTilt = KanaTilt.NORMAL
    scope: FocusScope | None = None

    @property
    def is_default(self) -> bool:
        return self.tilt is Tilt.EVEN and self.kana is KanaTilt.NORMAL and self.scope is None


@dataclass(frozen=True)
class Weights:
    """The two settings' parts: new against review (the tilt), course against kana inside review (the kana
    tilt). rules/shares.unit_weights makes three units side by side of them (new, review x course,
    review x kana)."""

    new: int
    review: int
    course: int
    kana: int

    def __post_init__(self):
        if min(self.new, self.review, self.course, self.kana) < 0:
            raise ValueError("Weights cannot be negative")
        if self.new + self.review <= 0 or self.course + self.kana <= 0:
            raise ValueError("Each level needs a positive weight")


@dataclass(frozen=True)
class RawEvent:
    """One timed study action before it is attributed to a unit.

    ``state`` identifies the scheduling state (a course unit, or a kana state);
    ``day`` is the learner's study-day key supplied by the source, so that the
    rules never localise an instant themselves. ``formal`` marks a formal
    (primary) rating; ``teaching_mark`` marks the completion of teaching (a
    finished guide, a kana introduction).
    """

    at: datetime
    day: str
    lane: str
    state: str
    formal: bool = False
    teaching_mark: bool = False
    consolidation: bool = False   # a consolidation practice answer (design 12 N5): new content (N3)

    def __post_init__(self):
        if self.lane not in ("course", "kana"):
            raise ValueError("Unknown lane")
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ValueError("Clock events need an aware instant")


@dataclass(frozen=True)
class FirstRatings:
    """When a scheduling state was first formally rated, and its next rating that same day.

    ``follow_up`` is the second formal rating on the study day of the first one
    (the same-day step), or None. Both belong to learning the state; later
    ratings are review.
    """

    first: datetime
    day: str
    follow_up: datetime | None = None


@dataclass(frozen=True)
class ClockEvent:
    """A timed study action attributed to a unit; ``teaching`` selects the long gap cap."""

    at: datetime
    day: str
    unit: Unit
    teaching: bool = False


@dataclass(frozen=True)
class Usage:
    """Charged seconds per unit, over a window or over a study day."""

    new: float = 0.0
    course: float = 0.0
    kana: float = 0.0

    @property
    def review(self) -> float:
        return self.course + self.kana

    @property
    def total(self) -> float:
        return self.new + self.course + self.kana

    def of(self, unit: Unit) -> float:
        return {Unit.NEW: self.new, Unit.COURSE: self.course, Unit.KANA: self.kana}[unit]


@dataclass(frozen=True)
class Candidate:
    """The head a unit could serve now; ``ref`` is the caller's own handle."""

    unit: Unit
    in_scope: bool = True
    ref: object = None


@dataclass(frozen=True)
class Decision:
    """Which candidate goes next and why (``reason`` is for tests and diagnostics)."""

    candidate: Candidate | None
    reason: str

    @property
    def unit(self) -> Unit | None:
        return self.candidate.unit if self.candidate else None


class Credit(str, Enum):
    """What one answer counts as (design 12 E3)."""

    FORMAL_DUE = "formal_due"      # scheduled work: always a formal rating
    FORMAL_EARLY = "formal_early"  # not due, 24 hours or more since the last formal rating
    PRACTICE = "practice"          # not due, rated less than 24 hours ago: practice only


class Lane(str, Enum):
    """The two lanes a scheduling state belongs to."""

    COURSE = "course"
    KANA = "kana"


@dataclass(frozen=True)
class StateRef:
    """Which scheduling state an answer is about (design 12 E3).

    A course unit is (content id, ability); a kana state is (content id,
    skill, script), the skill being the question kind. ``ability`` holds the
    course ability or the kana skill; ``script`` is empty for a course unit.
    """

    lane: Lane
    content_id: str
    ability: str
    script: str = ""

    def __post_init__(self):
        if not isinstance(self.lane, Lane):
            raise ValueError("Unknown lane")
        if not self.content_id or not self.ability or (self.lane is Lane.KANA) != bool(self.script):
            raise ValueError("Invalid scheduling state reference")

    @classmethod
    def course(cls, content_id: str, ability: str) -> StateRef:
        return cls(Lane.COURSE, content_id, ability)

    @classmethod
    def kana(cls, content_id: str, skill: str, script: str) -> StateRef:
        return cls(Lane.KANA, content_id, skill, script)

    @property
    def key(self) -> str:
        """The StateView key: "course:<content>:<ability>" or "kana:<content>:<script>:<skill>"."""
        if self.lane is Lane.COURSE:
            return f"course:{self.content_id}:{self.ability}"
        return f"kana:{self.content_id}:{self.script}:{self.ability}"


@dataclass(frozen=True)
class ConsolidationFacts:
    """What rules/consolidation needs to know about one course state taught today (design 12 N5).

    ``taught_at``: its lesson's teaching mark in the probe-first flow (None:
    none); ``due``: the queue's serviceable due; ``repeats``: consolidation
    answers since the teaching mark; ``last_exposure``: its latest formal or
    practice answer; ``last_kind``: the question kind it was last shown with.
    """

    ref: StateRef
    taught_at: datetime | None
    last_rated: datetime | None
    due: datetime | None
    in_step: bool = False
    repeats: int = 0
    last_exposure: datetime | None = None
    last_kind: str = ""

    @property
    def key(self) -> str:
        return self.ref.key

    @property
    def content_id(self) -> str:
        return self.ref.content_id


@dataclass(frozen=True)
class StateView:
    """What the credit and extra-practice rules need to know about one scheduling state.

    ``due`` is the effective due the queue uses; ``in_step`` is true for a
    state inside its learning or relearning steps; ``last_rated`` is the last
    formal rating. ``retrievability`` is the scheduler's own estimate at ``now``.
    """

    key: str
    due: datetime | None
    last_rated: datetime | None
    in_step: bool = False
    retrievability: float | None = None
    in_scope: bool = True
    early: bool = False   # due only by the short-interval reading of a recent state (rules/recent.serviceable_due)


class PracticeEffect(str, Enum):
    """What a practice-only answer leads to (design 12 E3, debounced)."""

    NONE = "none"        # correct: nothing changes
    RETRY = "retry"      # a miss that may be a slip: practise again later in this sitting
    ADVANCE = "advance"  # a confirmed miss: bring the next formal review forward once


@dataclass(frozen=True)
class PracticeMark:
    """One state's trace inside one sitting of extra practice (memory only)."""

    wrong_streak: int = 0
    needs_retry: bool = False
    last_position: int = -1


class DeckAnswerMode(str, Enum):
    """How a practice-deck question is answered (design 12 E5)."""

    CHOICE = "choice"          # tap one option; the lane's existing judge decides
    TEXT = "text"              # type the answer; the lane's existing judge decides
    SELF_CHECK = "self_check"  # recall (or write on paper), see the answer, say whether it was right


@dataclass(frozen=True)
class DeckItem:
    """One question of the practice deck: the state it practises and, when already built, the question.

    ``question`` None: the deck builds it on the spot from the state's kind and
    variant (extra practice). A question given here (a reinforce batch's miss,
    already built by the batch builder) is served as it is.
    """

    ref: StateRef
    question: Mapping[str, object] | None = field(default=None, compare=False)


@dataclass(frozen=True)
class ExtraEntry:
    """One learned scheduling state in the extra-practice pool (design 12 E2).

    ``view`` is the state as the rules see it (its ``key`` is ``ref.key``).
    ``card_id`` is the kana scheduling card behind the state: the ability
    carrier when an ability route is active, so every question kind of one
    ability shares it; empty for a course unit. ``carrier`` is the identity
    that makes "one scheduling state": the card id for kana, the key for a
    course unit.
    """

    ref: StateRef
    view: StateView
    card_id: str = ""

    def __post_init__(self):
        if self.view.key != self.ref.key:
            raise ValueError("An extra entry's view must be about its own state")
        if (self.ref.lane is Lane.KANA) != bool(self.card_id):
            raise ValueError("A kana entry needs its scheduling card, a course entry none")

    @property
    def carrier(self) -> str:
        return f"card:{self.card_id}" if self.card_id else self.ref.key


@dataclass(frozen=True)
class Arranged:
    """Extra-practice step: arranged work has come due; serve it with the main lane's own dispatch.

    ``refs``: the states whose due or step came due (for diagnostics; the
    dispatch decides what it serves).
    """

    refs: tuple[StateRef, ...]


@dataclass(frozen=True)
class EarlyKana:
    """Extra-practice step: up to 20 kana states rated early, as one new main-lane batch."""

    states: tuple[ExtraEntry, ...]

    @property
    def refs(self) -> tuple[StateRef, ...]:
        return tuple(entry.ref for entry in self.states)


@dataclass(frozen=True)
class EarlyCourse:
    """Extra-practice step: one course unit rated early through its own pending question."""

    ref: StateRef


@dataclass(frozen=True)
class Practice:
    """Extra-practice step: one practice deck of these items (each built on the spot)."""

    items: tuple[DeckItem, ...]


@dataclass(frozen=True)
class Nothing:
    """Extra-practice step: no learned state can be practised."""


ExtraStep = Arranged | EarlyKana | EarlyCourse | Practice | Nothing


@dataclass(frozen=True)
class DeckTexts:
    """What the deck shows of one question, as the lane presents it (design 12 E5, UI8)."""

    prompt: str
    task: str = ""
    options: tuple[str, ...] = ()
    answer: str = ""
    explanation: str = ""
    audio_text: str = ""
    # A callable giving the answer's (surface, kana or "") cells, called only when the answer is shown
    # (course questions, owner 2026-11-18: kana above the standard spelling); None: the answer as text.
    answer_cells: object = field(default=None, compare=False)
    # A callable giving the task as the ordinary question page shows it (question_instruction_ui.instruction_view:
    # the short cue, its role, the Chinese hint, whether it is folded; design 12 N12); None: ``task`` as text.
    cue: object = field(default=None, compare=False)
    # A callable giving the prompt's widgets as the ordinary question page draws them (its readings above the
    # written forms and its emphasised target, by that page's own presentation function and reading rule;
    # design 12 N12), called when the question is shown; None, or a view giving None: ``prompt`` as a plain label.
    prompt_view: object = field(default=None, compare=False)


@dataclass(frozen=True)
class DeckCard:
    """A deck question ready to show. ``number`` counts the questions served in this deck, from 1."""

    item: DeckItem
    question: Mapping[str, object] = field(compare=False)
    mode: DeckAnswerMode
    texts: DeckTexts
    number: int


@dataclass(frozen=True)
class DeckOutcome:
    """The deck's answer to one practice answer.

    ``correct`` None: the judge could not score it (nothing recorded; the
    learner may answer again). ``effect`` is the sitting's (RETRY also for a
    miss on a question about no scheduling state); None when nothing was
    recorded. ``message`` is the judge's own note, if any.
    """

    card: DeckCard
    correct: bool | None
    effect: PracticeEffect | None
    message: str = ""


class KanaDirection(str, Enum):
    """A kana practice direction as the records page groups it (design 12 UI4)."""

    READING = "reading"          # 识读
    SPELLING = "spelling"        # 拼写
    HANDWRITING = "handwriting"  # 手写


# The question kinds of each direction (UI4): listening handwriting counts as handwriting here.
KANA_DIRECTION_KINDS = {
    KanaDirection.READING: ("kana_choice", "kana_romaji"),
    KanaDirection.SPELLING: ("romaji_kana", "romaji_kana_full"),
    KanaDirection.HANDWRITING: ("romaji_handwriting", "kana_handwriting"),
}


@dataclass(frozen=True)
class NewLearning:
    """What was first learned over some study days (design 12 UI2, UI4).

    ``lessons`` are course content ids, ``kana`` (content id, script) pairs,
    each counted once, on the study day the item itself was first learned:
    the earliest of its teaching marks (a completed guide, a kana
    introduction) and of the first formal ratings of all its abilities or
    skills. A newly opened ability of an older lesson, or a new skill of an
    older kana, is new content for the clock but not a new lesson or kana; an
    item learned before the log never counts.
    """

    lessons: frozenset[str] = frozenset()
    kana: frozenset[tuple[str, str]] = frozenset()


@dataclass(frozen=True)
class PeriodActivity:
    """Every attributed clock event of a period, read without the window's row limit."""

    events: tuple[ClockEvent, ...]
    learned: NewLearning


@dataclass(frozen=True)
class RouteFacts:
    """The current route as the home page already read it (StudyPathService.overview()).

    ``completed`` and ``total`` count the required (not optional) sections:
    overview()["required_completed_sections"] and ["required_total_sections"].
    ``next_section_id`` / ``next_section_title`` are overview()["next_section"]
    ["id"] / ["title"], None when the route has no section left to add.
    The ``suggested_*`` fields (the first part of the two-line design): the
    section to add first, its mark (建议先加 · <line>) and the reason, when the
    caller computed a suggestion (study_lines.suggest); B10's note names it.
    """

    completed: int = 0
    total: int = 0
    next_section_id: str | None = None
    next_section_title: str | None = None
    suggested_section_id: str | None = None
    suggested_section_title: str | None = None
    suggested_mark: str = ""
    suggested_reason: str = ""

    @property
    def position(self) -> int:
        """N of "第 N／M 小节": the required section being learned, one past the completed ones.

        Capped at ``total`` (every required section completed gives M／M); 0 for an empty route.
        """
        return 0 if self.total <= 0 else min(max(self.completed, 0) + 1, self.total)

    @classmethod
    def from_overview(cls, overview, suggestion=None, mark: str = "") -> RouteFacts:
        """From the dict StudyPathService.overview() returns; reads four keys, nothing else. ``suggestion``: a
        study_lines.Suggestion (section_id, title, reason) or None; ``mark``: the words that mark it."""
        section = overview.get("next_section")
        extra = {} if suggestion is None else dict(
            suggested_section_id=str(suggestion.section_id), suggested_section_title=str(suggestion.title),
            suggested_mark=mark, suggested_reason=str(suggestion.reason or ""))
        return cls(int(overview.get("required_completed_sections", 0)), int(overview.get("required_total_sections", 0)),
                   None if not section else str(section["id"]), None if not section else str(section["title"]),
                   **extra)


class Part(str, Enum):
    """What extra practice may be limited to (design 12 E9): one part per start, memory only."""

    TODAY = "today"      # lessons with a course state first formally rated on this study day
    RECENT = "recent"    # lessons with a recent course state (rules/recent.is_recent)
    SCOPE = "scope"      # the focus scope (only while it resolves)
    ALL = "all"          # everything extra practice may serve (as before E9)


@dataclass(frozen=True)
class ReviewFilterSetting:
    """The learner's two switches over what 继续学习 and 加练 serve (design 12 H8, H9; setting study_review_filter_v1).

    ``only``: 只做范围内 (acts only while the focus scope resolves). ``excluded``:
    the scopes paused by 暂不复习, in the order they were added, at most
    settings.MAX_EXCLUDED, no duplicates.
    """

    only: bool = False
    excluded: tuple[FocusScope, ...] = ()
