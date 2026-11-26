"""The route's lessons as two lines, words and grammar: the first part of the two-line design (pure).

The route stays one mixed order with its units, sections and ids; this module
only reads it as two projections. line_of puts a lesson on a line: words,
reading rules and common expressions on the word line, every other grammar
lesson on the grammar line, kana on neither. A section is a grammar section when
its lessons are all of the grammar line, else a word section (a section without
lessons, kana or references only, is on neither). Each line's next section is the
first section in route order on that line that is not optional and still has
new content (next_sections). suggest marks the line to add first, and verdict
says whether a section's lessons have enough gated questions with what is known
(learned, added and ticked): a hint, nothing is blocked. route_lines puts them
together for one overview (StudyPathService.overview()); while the route's next
section is on neither line (the kana stage) the lines wait behind it and the
suggestion is that section, as today. No share of time, no
setting and nothing stored: every fact is derived from the overview and the
readiness table it is given (route_mix.readiness.Readiness).
"""
from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass

from .route_mix.readiness import is_choice_key

WORD, GRAMMAR = "word", "grammar"
LINES = (WORD, GRAMMAR)
LINE_TITLES = {WORD: "词汇", GRAMMAR: "语法"}
# Grammar-family domains practised like words (whole-chunk recall): they stay on the word line.
WORD_LINE_DOMAINS = frozenset({"pronunciation", "expression"})
# The floor of the main-route mix (design 11 section 16, M5): a lesson is short of questions for an ability
# while fewer than min(that ability's templates, READY_FLOOR) can be asked.
READY_FLOOR = 3
ABILITY_ORDER = ("understanding", "application", "reading", "use", "listening")
ABILITY_SHORT = {"understanding": "理解", "application": "应用", "reading": "读法", "use": "使用", "listening": "听辨"}
# The mark and the reason of the suggestion (decision: one sentence, only when it changes the line).
SUGGEST_MARK = "建议先加"
NOT_READY_REASON = "「{grammar}」的题还要用到 {count} 项没加入的内容，先加「{words}」。"
_UNLEARNED = frozenset({"added", "started"})


def line_of(family, domain) -> str | None:
    """WORD, GRAMMAR, or None (kana and anything that is not a lesson)."""
    if family == "word":
        return WORD
    if family == "grammar":
        return WORD if domain in WORD_LINE_DOMAINS else GRAMMAR
    return None


def entry_line(entry: Mapping) -> str | None:
    """The line of one overview entry: only a lesson (kind "learning") has one."""
    if entry.get("kind") != "learning":
        return None
    return line_of(entry.get("family"), entry.get("domain"))


def section_line(section: Mapping) -> str | None:
    """GRAMMAR when every lesson of the section is of the grammar line, WORD when some lesson is not,
    None without lessons."""
    lines = {entry_line(entry) for entry in section.get("entries", ()) if entry.get("kind") == "learning"}
    if not lines:
        return None
    return GRAMMAR if lines == {GRAMMAR} else WORD


def route_sections(overview: Mapping) -> Iterator[Mapping]:
    """Every section of an overview (or a route tree), in route order."""
    for stage in overview.get("stages", ()):
        for unit in stage.get("units", ()):
            yield from unit.get("sections", ())


def open_section(section: Mapping) -> bool:
    """A section the next-section rule may name: not optional and with new content."""
    return bool(section.get("new_count")) and not section.get("optional")


def next_sections(overview: Mapping, count: int, line: str | None = None) -> list:
    """The next ``count`` sections in route order (``line`` None: today's rule over the whole route), else
    the next ``count`` sections of that line."""
    result = []
    for section in route_sections(overview):
        if open_section(section) and (line is None or section_line(section) == line):
            result.append(section)
            if len(result) >= count:
                break
    return result


def unlearned_lines(overview: Mapping) -> frozenset:
    """The lines with added content not yet learned (an entry in state "added" or "started")."""
    found = set()
    for section in route_sections(overview):
        for entry in section.get("entries", ()):
            if entry.get("state") in _UNLEARNED:
                line = entry_line(entry)
                if line is not None:
                    found.add(line)
    return frozenset(found)


# ------------------------------------------------------------------ readiness

@dataclass(frozen=True)
class Missing:
    """A new lesson of the route that some short template needs, and its section."""

    entry_id: str
    title: str
    section_id: str
    section_title: str


@dataclass(frozen=True)
class Verdict:
    """Whether a section can be asked about with what is known (learned, added, ticked).

    ``counts``: (ability, askable questions) summed over the section's lessons,
    abilities in ABILITY_ORDER; ``ready`` False when some lesson has an ability
    with fewer than min(its templates, floor) askable questions; ``missing``:
    the new route lessons that would lift those up to the floor, in route order;
    ``unreachable``: how many needed lessons are not new lessons of this route
    (they cannot be ticked here).
    """

    counts: tuple[tuple[str, int], ...]
    ready: bool
    missing: tuple[Missing, ...] = ()
    unreachable: int = 0

    def word_section(self, lines: Mapping[str, str | None]) -> Missing | None:
        """The first missing lesson that lies in a word section (``lines``: section id -> line), else None."""
        return next((item for item in self.missing if lines.get(item.section_id) == WORD), None)


def _ability_rank(ability: str) -> tuple:
    return (ABILITY_ORDER.index(ability) if ability in ABILITY_ORDER else len(ABILITY_ORDER), ability)


def verdict(section: Mapping, readiness, known: Callable[[str], bool], *,
            places: Mapping[str, tuple[int, Missing]], floor: int = READY_FLOOR) -> Verdict | None:
    """The verdict of one section, or None when ``readiness`` (a route_mix.readiness.Readiness) is None or
    lacks one of the section's lessons (the table is not built for them).

    ``known(content id)``: learned, added or ticked; ``places``: content id ->
    (route position, Missing) of the route's new lessons (route_lines builds it).
    Only abilities count (the switched-on choice keys of the mix are not shown).
    """
    lessons = [entry.get("content_id", entry.get("id")) for entry in section.get("entries", ())
               if entry.get("kind") == "learning"]
    if readiness is None or any(lesson not in readiness for lesson in lessons):
        return None
    totals: dict[str, int] = {}
    ready, needed = True, set()
    for lesson in lessons:
        for ability, templates in readiness.abilities(lesson).items():
            if is_choice_key(ability):
                continue
            unmet: dict[str, tuple] = {}
            met = set()
            for sha, requires in templates:
                unknown = tuple(sorted(r for r in requires if not known(r)))
                if not unknown:
                    met.add(sha)
                elif sha not in unmet or len(unknown) < len(unmet[sha]):
                    unmet[sha] = unknown
            unmet = {sha: unknown for sha, unknown in unmet.items() if sha not in met}
            totals[ability] = totals.get(ability, 0) + len(met)
            short = min(len(met) + len(unmet), floor) - len(met)
            if short > 0:
                ready = False
                # The cheapest templates first: the fewest unknown lessons, then by those lessons.
                for unknown in sorted(unmet.values(), key=lambda value: (len(value), value))[:short]:
                    needed.update(unknown)
    missing = sorted((places[lesson] for lesson in needed if lesson in places), key=lambda pair: pair[0])
    return Verdict(tuple(sorted(totals.items(), key=lambda pair: _ability_rank(pair[0]))), ready,
                   tuple(item for _, item in missing), sum(lesson not in places for lesson in needed))


def counts_text(counts: Iterable[tuple[str, int]]) -> str:
    """现在能出：理解 6／应用 5 (abilities without a short name keep their key)."""
    return "现在能出：" + "／".join(f"{ABILITY_SHORT.get(ability, ability)} {count}" for ability, count in counts)


# ------------------------------------------------------------------ the suggestion

@dataclass(frozen=True)
class Suggestion:
    """The section to add first and its line (None: a section on neither line, 深度优先's route order)."""

    line: str | None
    section_id: str
    title: str
    reason: str = ""


def suggest(nexts: Mapping[str, Mapping | None], unlearned: Collection[str], *, breadth: bool,
            route_next: Mapping | None, positions: Mapping[str, int], grammar_verdict: Verdict | None = None,
            lines: Mapping[str, str | None] | None = None) -> Suggestion | None:
    """The line to add first.

    Not ``breadth`` (深度优先, or no orientation chosen): the route's next
    section, as today. Breadth: of the lines with a next section, (a) the only
    line without added-but-unlearned content (``unlearned``) when exactly one
    such line is left, (b) otherwise the line whose next section comes first in
    route order (``positions``: section id -> route position); (c) when that is
    the grammar line and its next section is not ready (``grammar_verdict``), the
    word section (``lines``: section id -> line) of the first lesson it misses,
    with the reason. None when nothing is left.
    """
    if not breadth:
        if route_next is None:
            return None
        return Suggestion(section_line(route_next), str(route_next["id"]), str(route_next.get("title", "")))
    open_lines = [line for line in LINES if nexts.get(line) is not None]
    if not open_lines:
        return None
    free = [line for line in open_lines if line not in unlearned]
    if len(open_lines) > 1 and len(free) == 1:
        chosen = free[0]
    else:
        chosen = min(open_lines, key=lambda line: positions.get(str(nexts[line]["id"]), len(positions)))
    section = nexts[chosen]
    if chosen == GRAMMAR and grammar_verdict is not None and not grammar_verdict.ready:
        first = grammar_verdict.word_section(lines or {})
        if first is not None:
            count = len(grammar_verdict.missing)
            reason = NOT_READY_REASON.format(grammar=section.get("title", ""), count=count, words=first.section_title)
            return Suggestion(WORD, first.section_id, first.section_title, reason)
    return Suggestion(chosen, str(section["id"]), str(section.get("title", "")))


# ------------------------------------------------------------------ one overview

@dataclass(frozen=True)
class LineNext:
    """One line's next section and its verdict (None: no readiness table for its lessons)."""

    line: str
    section_id: str
    title: str
    verdict: Verdict | None = None

    @property
    def line_title(self) -> str:
        return LINE_TITLES[self.line]


@dataclass(frozen=True)
class RouteLines:
    """Both lines of one overview: the lines that still have a next section (word first) and the suggestion."""

    lines: tuple[LineNext, ...] = ()
    suggestion: Suggestion | None = None

    def get(self, line: str) -> LineNext | None:
        return next((item for item in self.lines if item.line == line), None)


def readiness_ids(overview: Mapping) -> tuple[str, ...]:
    """The lessons whose readiness route_lines reads: those of each line's next section."""
    ids = []
    for line in LINES:
        for section in next_sections(overview, 1, line):
            ids.extend(entry.get("content_id", entry.get("id")) for entry in section.get("entries", ())
                       if entry.get("kind") == "learning")
    return tuple(dict.fromkeys(ids))


def route_lines(overview: Mapping, *, readiness=None, ticked: Iterable[str] = (), breadth: bool = False,
                floor: int = READY_FLOOR) -> RouteLines:
    """The two lines of ``overview``; ``readiness``: a Readiness holding readiness_ids(overview), or None
    (no verdicts); ``ticked``: entry ids counted as known besides the learned and added ones (the add
    page's selection and the prerequisites it brings); ``breadth``: the learner's orientation is 广度优先."""
    sections = list(route_sections(overview))
    positions = {str(section["id"]): index for index, section in enumerate(sections)}
    lines = {str(section["id"]): section_line(section) for section in sections}
    nexts: dict[str, Mapping | None] = {line: None for line in LINES}
    route_next = None
    for section in sections:
        if not open_section(section):
            continue
        if route_next is None:
            route_next = section
        line = lines[str(section["id"])]
        if line is not None and nexts[line] is None:
            nexts[line] = section
        if route_next is not None and all(nexts.values()):
            break
    if route_next is not None and lines[str(route_next["id"])] is None:
        # The route still asks for a section on neither line first (the kana stage): the lines wait behind it
        # and the suggestion is that section, as today.
        return RouteLines((), Suggestion(None, str(route_next["id"]), str(route_next.get("title", ""))))
    ticked = set(ticked)
    known, places = set(), {}
    position = 0
    for section in sections:
        for entry in section.get("entries", ()):
            content = entry.get("content_id", entry.get("id"))
            if entry.get("state", "new") != "new" or entry.get("id") in ticked:
                known.add(content)
            elif entry.get("kind") == "learning":
                places.setdefault(content, (position, Missing(str(entry.get("id")), str(entry.get("title", "")),
                                                              str(section["id"]), str(section.get("title", "")))))
            position += 1
    if readiness is None:
        verdicts = {}
    else:
        verdicts = {line: verdict(section, readiness, known.__contains__, places=places, floor=floor)
                    for line, section in nexts.items() if section is not None}
    shown = tuple(LineNext(line, str(section["id"]), str(section.get("title", "")), verdicts.get(line))
                  for line, section in nexts.items() if section is not None)
    suggestion = suggest(nexts, unlearned_lines(overview), breadth=breadth, route_next=route_next,
                         positions=positions, grammar_verdict=verdicts.get(GRAMMAR), lines=lines)
    return RouteLines(shown, suggestion)


def line_sections(overview: Mapping) -> dict[str, int]:
    """line -> the number of sections on it (None: sections on neither line); for tools and documents."""
    result: dict = {}
    for section in route_sections(overview):
        line = section_line(section)
        result[line] = result.get(line, 0) + 1
    return result

