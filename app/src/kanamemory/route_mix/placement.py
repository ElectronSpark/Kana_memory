"""Per-stage order of the main route: words and grammar alternate (design 11 section 16; pure).

M3 sections that are not movable keep their relative order (the skeleton: every
section with a grammar item, and anything else that is neither a word section
nor a reading-rule section). M4 word sections and reading-rule sections
(movable) only move earlier, never past a later skeleton section; reading-rule
sections are tried both fixed and movable, and the better score wins (fixed on
a tie). M5 a movable section stands only where each of its entries, in section
order, has its hard prerequisites before it and, per ability, at least
min(derivation count, floor) gated questions, and per ability, among the kinds
the learner has switched on (readiness choice keys), at least min(derivation
count, choice floor = 1): so where the learner had such a question, they still
meet one that uses only what is learned; every placement is checked on what is
actually before it. M6 windows, the lower bound L0 and the balanced placements L0 ...
L0 + width, scored by max(longest word run / word target, longest non-word run /
non-word target), then the shorter word run, then the smaller L. The balanced
filling places words; a movable section without words (a reading rule, when
movable) stands in its own gap, or earlier only where it keeps the words due in
a gap within the limit: so rules move earlier to break a run of words (M4), not
to the start of the stage.

M8(b) a library grammar section that is nobody's hard prerequisite moves to the
library's last word stage when it keeps all of its gated questions at that
stage's start and every entry it passes (the movable words of that stage aside,
which the placement checks) keeps all of its own; a passed entry whose
questions cannot be checked blocks it: an extension-library entry without
readiness data, and a core id an accepted override gives to a library (core for
M8, but with the library's questions). In its new stage the deferred sections
are flexible sections, placed in two steps. Step 1 places the stage's own
sections as above, the deferred items known as they were in the derivation, so
the words are spread over the stage's grammar as without them. Step 2 inserts
the deferred sections, in their order, into that order: each between two words,
at an even pace over the stage (the k-th of n once k/(n+1) of its entries stand
before), but before the first section that needs it (a deadline it shares with
every one before it); a group whose deadline comes before its pace is spread
evenly over the places between two words up to that deadline (more than one at
a place only when they outnumber those places). The score is taken of the final
order. The floors hold because every deferred section stands before each
section that needs it (its deadline) and because the stage's final order is
checked again (M9): each item of a movable section has its M5 floors (per
ability and per choice key), each skeleton and each deferred item its full
derivation count per ability and per choice key, and every hard prerequisite stands
before. When that check fails, the stage takes none of its deferred sections:
they stay in their source stages and the route is placed again without them.
They keep no unit of their own (M7). Deterministic: ties follow the derivation
order.

Full counts are per template, so per choice key too: a skeleton or
deferred item keeps every template it had in the derivation. What stands before
it only grows (the skeleton keeps its order, movable sections only move earlier,
deferred sections are inserted, a deferred item stands in a later stage), except
for sections deferred past it; and each such deferral was allowed only if the
item, a passed entry, kept its full count with that section gone, i.e. with a
subset of what stood before it, which is the same templates. The checks also
hold per choice key (counts0 has them), so they are checked, not assumed.
"""
from __future__ import annotations

from bisect import bisect_right, insort
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from .params import DEFAULT_PARAMS, GIVEN_ORDER, MixParams  # noqa: F401 - GIVEN_ORDER for sources
from .readiness import Readiness, ReadinessCounter, is_choice_key

WORD, RULE, GRAMMAR, OTHER = "word", "rule", "grammar", "other"
RULES_FIXED, RULES_MOVED = "rules_fixed", "rules_moved"


class MixCancelled(Exception):
    """The computation was asked to stop (app exit); nothing of it is kept."""


@dataclass(frozen=True)
class Section:
    """What the placement reads of one section."""
    id: str
    unit_id: str
    entries: tuple
    words: tuple             # per entry: a word
    kind: str                # WORD / RULE / GRAMMAR / OTHER (every entry of that sort)
    library: str | None      # the one extension library of every entry, else None
    resolved: bool = True    # no hard prerequisite outside the route

    @property
    def word_count(self) -> int:
        return sum(self.words)


def section_of(section: Mapping, library_of: Mapping[str, str]) -> Section:
    """A Section from a tree section (library_of: entry id -> extension library)."""
    entries = section["entries"]
    learning = bool(entries) and all(entry.get("kind") == "learning" for entry in entries)
    if learning and all(entry.get("family") == "word" for entry in entries):
        kind = WORD
    elif learning and all(entry.get("domain") == "pronunciation" for entry in entries):
        kind = RULE
    elif learning and all(entry.get("family") == "grammar" and entry.get("domain") != "pronunciation"
                          for entry in entries):
        kind = GRAMMAR
    else:
        kind = OTHER
    owners = {library_of.get(entry["id"]) for entry in entries}
    library = next(iter(owners)) if learning and len(owners) == 1 and None not in owners else None
    return Section(section["id"], section["unit_id"], tuple(entry["id"] for entry in entries),
                   tuple(entry.get("kind") == "learning" and entry.get("family") == "word" for entry in entries),
                   kind, library, not section.get("unresolved_dependencies"))


@dataclass(frozen=True)
class StageResult:
    stage_id: str
    sections: tuple          # Section objects in the new order (deferred sections included)
    changed: bool
    variant: str | None = None
    limit: int | None = None     # the chosen L
    lower: int | None = None     # L0
    fixed: frozenset = frozenset()   # ids of the skeleton sections of the chosen variant (deferred ones never)
    keep_units: bool = False         # the order changed only by sections leaving (M8(b)): units as derived


@dataclass(frozen=True)
class Arrangement:
    stages: tuple                # StageResult per mixed stage, route order
    deferred: Mapping            # section id -> (from stage id, to stage id, unit id in the new stage)


def runs(sections: Sequence[Section]) -> tuple[int, int]:
    """(longest run of word entries, longest run of non-word entries)."""
    best_word = best_other = word = other = 0
    for section in sections:
        for flag in section.words:
            if flag:
                word, other = word + 1, 0
                best_word = max(best_word, word)
            else:
                word, other = 0, other + 1
                best_other = max(best_other, other)
    return best_word, best_other


class _Inconsistent(Exception):
    """A section is not placeable at its own derivation position (inconsistent inputs)."""


class _DeferralFailed(Exception):
    """M8(b): the final order of a stage with deferred sections fails the M9 check (arrange then places
    the route again without deferring into that stage)."""

    def __init__(self, stage_id):
        super().__init__(stage_id)
        self.stage_id = stage_id


class _StagePlacer:
    """One stage, one variant: movable/fixed sections, a counter and the M5 checks."""

    def __init__(self, sections, movable_mask, readiness, prereqs, floors, known_before):
        self.sections = sections
        self.fixed = [i for i, movable in enumerate(movable_mask) if not movable]
        self.movable = [i for i, movable in enumerate(movable_mask) if movable]
        local_ids = [entry for section in sections for entry in section.entries]
        self.local = {entry: position for position, entry in enumerate(local_ids)}
        self.span = {}
        start = 0
        for i, section in enumerate(sections):
            self.span[i] = tuple(range(start, start + len(section.entries)))
            start += len(section.entries)
        items = [entry for i in self.movable for entry in sections[i].entries if entry in readiness]
        self.counter0 = ReadinessCounter(readiness, items, local_ids, known_before)
        gap, self.gap_of = 0, {}
        for i, movable in enumerate(movable_mask):
            if movable:
                self.gap_of[i] = gap
            else:
                gap += 1
        self.checks, affects = {}, {}
        for i in self.movable:
            rows = []
            for entry in sections[i].entries:
                position = self.local[entry]
                before = tuple(self.local[p] for p in prereqs.get(entry, ()) if p in self.local)
                bounds = tuple(floors.get(entry, ()))
                rows.append((position, entry, before, bounds))
                touched = set(before)
                for ability, _ in bounds:
                    touched |= self.counter0.requirements(entry, ability)
                for p in touched:
                    affects.setdefault(p, set()).add(i)
            self.checks[i] = tuple(rows)
        self.affects = {p: tuple(sorted(value)) for p, value in affects.items()}
        self.words = [section.word_count for section in sections]
        self.zero_movable = any(not self.words[i] for i in self.movable)

    def eligible(self, i, counter) -> bool:
        extra, known = set(), counter.known
        for position, entry, before, bounds in self.checks[i]:
            for p in before:
                if not known[p] and p not in extra:
                    return False
            for ability, floor in bounds:
                count = counter.count_of(entry, ability)
                if count < floor and (not extra or counter.count_with(entry, ability, extra) < floor):
                    return False
            extra.add(position)
        return True

    def learn(self, i, counter) -> set:
        dirty = set()
        for position in self.span[i]:
            if not counter.known[position]:
                counter.learn(position)
                dirty.update(self.affects.get(position, ()))
        return dirty

    def windows(self) -> dict:
        """M6(1): the earliest gap of each movable section when all the others go as early as they can."""
        counter, placed, early = self.counter0.copy(), set(), {}
        work = list(self.movable)
        for gap in range(len(self.fixed) + 1):
            while work:
                follow = set()
                for i in sorted(set(work)):
                    if i not in placed and self.eligible(i, counter):
                        placed.add(i)
                        early[i] = gap
                        follow |= self.learn(i, counter)
                work = sorted(follow - placed)
            if gap < len(self.fixed):
                work = sorted(self.learn(self.fixed[gap], counter) - placed)
        return {i: min(early.get(i, self.gap_of[i]), self.gap_of[i]) for i in self.movable}

    def sweep(self, limit, balanced, early, nonword_before, total):
        """M6(2)/(3): gap -> section indices, or None when a gap's forced words run past ``limit``."""
        counter = self.counter0.copy()
        fixed, words, gap_of, n = self.fixed, self.words, self.gap_of, len(self.fixed)
        rank = {i: (gap_of[i], i) for i in self.movable}
        forced = [[] for _ in range(n + 1)]
        opening = [[] for _ in range(n + 1)]
        for i in self.movable:
            forced[gap_of[i]].append(i)
            opening[early[i]].append(i)
        gaps = [[] for _ in range(n + 1)]
        placed, cache, candidates = set(), {}, []
        done, grammar_total = 0, max(1, nonword_before[n])

        def ok(i):
            value = cache.get(i)
            if value is None:
                value = cache[i] = self.eligible(i, counter)
            return value

        def place(i, gap):
            placed.add(i)
            gaps[gap].append(i)
            for d in self.learn(i, counter):
                cache.pop(d, None)

        for gap in range(n + 1):
            for i in opening[gap]:
                insort(candidates, (rank[i], i))
            run = load = 0
            # The sections due here (deadline at this gap), words first in derivation order. When the
            # next due words would run past the limit, a movable section without words (a reading
            # rule, M4) that may stand here comes between them: a due one, else one from a later gap.
            # Movable sections change places only among themselves, never past a skeleton section.
            due = [i for i in forced[gap] if i not in placed]
            while due:
                pick = next((i for i in due if words[i] and run + words[i] <= limit and ok(i)), None)
                if pick is None:
                    if any(words[i] for i in due):
                        pick = next((i for i in due if not words[i] and ok(i)), None)
                        if pick is None and run:
                            pick = next((i for _, i in candidates
                                         if i not in placed and not words[i] and ok(i)), None)
                    else:
                        pick = next((i for i in due if ok(i)), None)
                    if pick is None:
                        return None
                if pick in due:
                    due.remove(pick)
                place(pick, gap)
                load += words[pick]
                run = run + words[pick] if words[pick] else 0
            need = total - limit * (n - gap)
            ideal = 2 * total * (nonword_before[gap] if gap < n else grammar_total)  # x 2 * grammar_total
            candidates = [row for row in candidates if row[1] not in placed]
            # Then the words of later gaps, nearest deadline first (sections without words wait for
            # their own gap or for a run they break).
            while True:
                pick = None
                if run >= limit or balanced and done + load >= need and (
                        2 * (done + load) * grammar_total + grammar_total > ideal):
                    break
                for _, i in candidates:
                    if i in placed:
                        continue
                    w = words[i]
                    if not w or run + w > limit:
                        continue
                    if balanced and not (done + load < need
                                         or (2 * (done + load) + w) * grammar_total <= ideal):
                        continue
                    if ok(i):
                        pick = i
                        break
                if pick is None:
                    break
                place(pick, gap)
                load += words[pick]
                run = run + words[pick] if words[pick] else 0
            done += load
            if gap < n:
                for d in self.learn(fixed[gap], counter):
                    cache.pop(d, None)
        return gaps

    def order(self, gaps):
        result = []
        for gap, rows in enumerate(gaps):
            result.extend(rows)
            if gap < len(self.fixed):
                result.append(self.fixed[gap])
        return result


def _place_variant(sections, movable_mask, context, known_before, params, finish=None):
    """(score key, ordered Sections, L, L0) of one variant, None when nothing can move. ``finish``
    (M8(b) step 2) turns a placed order into the final one (None when it cannot); scores are of it."""
    placer = _StagePlacer(sections, movable_mask, context["readiness"], context["prereqs"],
                          context["floors"], known_before)
    if not placer.fixed or not placer.movable:
        return None
    early = placer.windows()
    nonword_before = [0]
    for i in placer.fixed:
        nonword_before.append(nonword_before[-1] + len(sections[i].entries) - sections[i].word_count)
    total = sum(placer.words[i] for i in placer.movable)
    limit = _lower_bound(placer, early)
    while placer.sweep(limit, False, early, nonword_before, total) is None:
        limit += 1
        if limit > total + 1:  # every section fits: a due section is never placeable
            raise _Inconsistent("no placement")

    def scored(gaps, candidate):
        order = [sections[i] for i in placer.order(gaps)]
        if finish is not None:
            order = finish(order)
            if order is None:
                return None
        word_run, other_run = runs(order)
        return (max(word_run * params.nonword_target, other_run * params.word_target), word_run, candidate), order

    best = None
    for candidate in range(limit, limit + params.search_width + 1):
        gaps = placer.sweep(candidate, True, early, nonword_before, total)
        if gaps is None:
            continue
        result = scored(gaps, candidate)
        if result is not None and (best is None or result[0] < best[0]):
            best = (*result, candidate)
    if best is None:  # no balanced placement fits: the L0 placement itself
        result = scored(placer.sweep(limit, False, early, nonword_before, total), limit)
        if result is None:
            raise _Inconsistent("no placement")
        best = (*result, limit)
    key, order, candidate = best
    return key, order, candidate, limit


def _lower_bound(placer, early):
    """A limit below which no placement fits: the largest section and, when no movable section is
    without words (nothing can break a run), per gap the words that can stand only there."""
    bound = max(placer.words[i] for i in placer.movable)
    if placer.zero_movable:
        return bound
    pinned = {}
    for i in placer.movable:
        if early[i] == placer.gap_of[i]:
            pinned[early[i]] = pinned.get(early[i], 0) + placer.words[i]
    return max([bound, *pinned.values()])


_NEVER = 1 << 30


def _waiting_for(entries, chain_index, readiness, floors, known):
    """How many deferred sections (in their order) the entries of one section need before them to pass
    M5 with ``known`` (a predicate) and their own earlier entries; _NEVER when no number suffices."""
    need, earlier = 0, set()
    for entry in entries:
        for ability, floor in floors.get(entry, ()):
            lowest = {}
            for sha, requires in readiness.abilities(entry).get(ability, ()):
                deepest = 0
                for requirement in requires:
                    if known(requirement) or requirement in earlier:
                        continue
                    number = chain_index.get(requirement)
                    if number is None:
                        deepest = _NEVER
                        break
                    deepest = max(deepest, number)
                if deepest < lowest.get(sha, _NEVER + 1):
                    lowest[sha] = deepest
            ranked = sorted(value for value in lowest.values() if value < _NEVER)
            need = max(need, ranked[floor - 1] if len(ranked) >= floor else _NEVER)
        earlier.add(entry)
    return need


def _insert_deferred(order, chain, readiness, floors, known_before):
    """M8(b) step 2: ``order`` (step 1) with the deferred sections inserted in their order, or None
    when a section of it cannot be satisfied.

    Slots are the places between sections (slot i: before order[i]; len(order): the end); a good
    slot stands between two words. The k-th of n deferred sections has a pace slot (the first good
    slot with k/(n+1) of the stage's entries before it) and a deadline (the first section that
    needs it, or a later one's deadline, which it must precede too). Those without a deadline take
    their pace slots; a group that shares a deadline takes its pace slots while they come before
    the deadline and spreads the others evenly over the good slots from the last slot taken to the
    deadline (several to a slot only when there are more of them than good slots), the deadline
    slot itself when there is none."""
    count = len(chain)
    chain_index = {entry: number + 1 for number, section in enumerate(chain) for entry in section.entries}
    deadline, seen = [len(order)] * count, set()
    for index, section in enumerate(order):
        if any(entry in floors for entry in section.entries):
            need = _waiting_for(section.entries, chain_index, readiness, floors,
                                lambda r: known_before(r) or r in seen)
            if need >= _NEVER:
                return None
            for k in range(need):
                deadline[k] = min(deadline[k], index)
        seen.update(section.entries)
    for k in range(count - 2, -1, -1):
        deadline[k] = min(deadline[k], deadline[k + 1])
    good, progress, entries, word_before = [], [], 0, False
    for index, section in enumerate(order):
        progress.append(entries)
        if section.kind == WORD and word_before:
            good.append(index)
        entries += len(section.entries)
        word_before = bool(section.words) and section.words[-1]
    good.append(len(order))
    progress.append(entries)
    pace, g = [], 0
    for k in range(count):
        while good[g] < len(order) and progress[good[g]] * (count + 1) < (k + 1) * entries:
            g += 1
        pace.append(good[g])
    def after(slot):
        """The first good slot after ``slot`` (the end, the last one, when there is none)."""
        return good[min(bisect_right(good, slot), len(good) - 1)]
    slots, last, k = [0] * count, -1, 0
    while k < count:
        end = k
        while end + 1 < count and deadline[end + 1] == deadline[k]:
            end += 1
        limit, paced, at = deadline[k], [], last
        for number in range(k, end + 1):
            at = max(after(at), pace[number])
            paced.append(at)
        if paced[-1] <= limit:
            slots[k:end + 1] = paced
        else:  # the deadline binds: the whole group spread evenly up to it
            spots = ([slot for slot in good if last < slot <= limit]
                     or [slot for slot in good if last <= slot <= limit] or [max(last, limit)])
            share = end - k + 1
            for offset in range(share):
                slots[k + offset] = spots[offset * len(spots) // share]
        last, k = slots[end], end + 1
    at = {}
    for section, slot in zip(chain, slots):
        at.setdefault(slot, []).append(section)
    out = []
    for index, section in enumerate(order):
        out.extend(at.get(index, ()))
        out.append(section)
    out.extend(at.get(len(order), ()))
    return tuple(out)


def _deferred_keep(order, chain_ids, readiness, floors, known_before) -> bool:
    """A deferred item keeps every gated question it had where it stands (its floors are full counts)."""
    before = set()
    for section in order:
        if section.id in chain_ids:
            extra = set()
            for entry in section.entries:
                now = readiness.counts(entry, lambda r: known_before(r) or r in before or r in extra)
                if any(now.get(ability, 0) < floor for ability, floor in floors.get(entry, ())):
                    return False
                extra.add(entry)
        before.update(section.entries)
    return True


def _stage_holds(order, movable, *, readiness, counts0, floors, prereqs, positions0, known_before) -> bool:
    """M9 on the final order of a stage, with what stands before the stage (``known_before``): each item
    of a movable section (``movable``: their ids) has its M5 floors per ability and per choice key
    (``floors``), each other item, skeleton or deferred, its derivation count per key (``counts0``),
    and each hard prerequisite that stood before an entry in the derivation (``positions0``) stands
    before it."""
    seen = set()

    def known(requirement):
        return known_before(requirement) or requirement in seen
    for section in order:
        for entry in section.entries:
            at = positions0.get(entry)
            if at is not None and any(positions0.get(p, at) < at and not known(p) for p in prereqs.get(entry, ())):
                return False
            if entry in readiness:
                need = dict(floors.get(entry, ())) if section.id in movable else counts0.get(entry, {})
                now = readiness.counts(entry, known)
                if any(now.get(ability, 0) < count for ability, count in need.items()):
                    return False
            seen.add(entry)
    return True


def place_stage(stage_id, sections: Sequence[Section], *, readiness: Readiness, prereqs, floors,
                known_before: Callable[[str], bool], params: MixParams = DEFAULT_PARAMS,
                deferred: frozenset = frozenset(), counts0: Mapping | None = None,
                positions0: Mapping | None = None) -> StageResult:
    """The best order of one stage (M3-M6); the derivation order when nothing can move.

    ``deferred``: ids of the library grammar sections M8(b) moved here (at the end, in their order);
    they stand among the skeleton sections (see the module docstring). The final order is checked
    (_stage_holds with ``counts0``, the derivation counts, and ``positions0``, the derivation
    positions); _DeferralFailed when it fails.
    """
    sections = tuple(sections)
    chain = tuple(section for section in sections if section.id in deferred)
    core = tuple(section for section in sections if section.id not in deferred) if chain else sections
    known, finish = known_before, None
    if chain:
        chain_ids = frozenset(section.id for section in chain)
        chain_entries = frozenset(entry for section in chain for entry in section.entries)

        def known(requirement):  # step 1: the deferred items count as known, as in the derivation
            return known_before(requirement) or requirement in chain_entries

        def finish(order):
            final = _insert_deferred(order, chain, readiness, floors, known_before)
            if final is None or not _deferred_keep(final, chain_ids, readiness, floors, known_before):
                return None
            return final
    context = {"readiness": readiness, "prereqs": prereqs, "floors": floors}
    has_rules = any(section.kind == RULE for section in core)
    variants = [(RULES_FIXED, [section.kind == WORD and section.resolved for section in core])]
    if has_rules:
        variants.append((RULES_MOVED, [section.kind in (WORD, RULE) and section.resolved for section in core]))
    best = None
    for rank, (name, mask) in enumerate(variants):
        try:
            result = _place_variant(core, mask, context, known, params, finish)
        except _Inconsistent:
            result = None
        if result is None:
            continue
        key, order, limit, lower = result
        if best is None or (key, rank) < best[0]:
            best = ((key, rank), name, order, limit, lower, mask)
    if best is None:
        if not chain:
            return StageResult(stage_id, sections, False)
        ordered = tuple(finish(core) or (*core, *chain))
        movable = frozenset(section.id for section in core if section.kind == WORD and section.resolved)
        result = StageResult(stage_id, ordered, True, None, None, None,
                             frozenset(section.id for section in core if section.kind != WORD))
    else:
        _, name, order, limit, lower, mask = best
        ordered = tuple(order)
        movable = frozenset(section.id for section, flag in zip(core, mask) if flag)
        result = StageResult(stage_id, ordered, ordered != sections, name, limit, lower,
                             frozenset(section.id for section, flag in zip(core, mask) if not flag))
    if chain and not _stage_holds(ordered, movable, readiness=readiness, counts0=counts0 or {}, floors=floors,
                                  prereqs=prereqs, positions0=positions0 or {}, known_before=known_before):
        raise _DeferralFailed(stage_id)
    return result


def _positions(prefix, stages, suffix):
    positions, index = {}, 0
    for entry in prefix:
        positions[entry] = index
        index += 1
    for _, sections in stages:
        for section in sections:
            for entry in section.entries:
                positions[entry] = index
                index += 1
    for entry in suffix:
        positions[entry] = index
        index += 1
    return positions


def derivation_counts(readiness: Readiness, positions: Mapping[str, int]) -> dict:
    """item -> ability -> gated questions when the item is first learned in this order."""
    result = {}
    for item in readiness.item_ids():
        at = positions.get(item)
        if at is None:
            continue
        result[item] = readiness.counts(item, lambda r, at=at: positions.get(r, at) < at)
    return result


def defer_library_grammar(stages, *, prefix, suffix, readiness: Readiness, counts0, prereqs, library_of,
                          excluded: frozenset = frozenset(), overridden: frozenset = frozenset()):
    """M8(b) on the stage lists (route order); returns (stages, {section id: (from, to, unit id)}).

    A deferred section goes to the end of the library's last word stage, after the sections
    deferred there before it (their order is kept); place_stage places it there. The unit id is the
    one it joins when it has no section to follow: the stage's last unit as derived. ``excluded``:
    ids of sections that stay where they are (a stage whose final order failed the M9 check);
    ``overridden``: entry ids that are core although a library provides them (they block a move).
    """
    stages = [(stage_id, list(sections)) for stage_id, sections in stages]
    index = {stage_id: number for number, (stage_id, _) in enumerate(stages)}
    last_word = {}
    for stage_id, sections in stages:
        for section in sections:
            if section.kind == WORD:
                for entry in section.entries:
                    library = library_of.get(entry)
                    if library is not None:
                        last_word[library] = index[stage_id]
    needed = {p for values in prereqs.values() for p in values}
    candidates = [(stage_id, section) for stage_id, sections in stages for section in sections
                  if section.kind == GRAMMAR and section.library in last_word
                  and index[stage_id] < last_word[section.library] and section.resolved
                  and not any(entry in needed for entry in section.entries) and section.id not in excluded]
    last_unit = {stage_id: sections[-1].unit_id for stage_id, sections in stages if sections}
    # The words of the new stage that its placement checks itself (movable: resolved word sections).
    stage_words = {stage_id: frozenset(entry for section in sections if section.kind == WORD and section.resolved
                                       for entry in section.entries) for stage_id, sections in stages}
    users = readiness.requirement_index()
    positions = _positions(prefix, stages, suffix)
    deferred = {}
    for stage_id, section in candidates:
        number = last_word[section.library]
        target_id = stages[number][0]
        trial = [(sid, [s for s in rows if s is not section]) for sid, rows in stages]
        trial[number][1].append(section)
        moved = _positions(prefix, trial, suffix)
        start = moved[trial[number][1][0].entries[0]]
        if _keeps_questions(section, positions, moved, readiness, counts0, users, library_of, start=start,
                            free=stage_words[target_id], overridden=overridden):
            deferred[section.id] = (stage_id, target_id, last_unit[target_id])
            stages, positions = trial, moved
    return [(stage_id, tuple(sections)) for stage_id, sections in stages], deferred


def _keeps_questions(section, before, after, readiness, counts0, users, library_of, *, start, free,
                     overridden=frozenset()):
    """M8(b): the section keeps all of its gated questions even at the start of its new stage
    (``start``: that stage's first position), and so does every entry it passes, with it at the end
    of that stage. The movable words of the new stage (``free``) are left to the placement's own M5
    check. A passed entry whose questions the plan cannot check blocks the move: an extension-library
    entry the readiness table does not know, and an overridden core id (``overridden``: core for M8,
    but an extension library provides it, so its questions are the library's and may need library
    content). Any other core entry has core questions only, which never need extension-library
    content, the only content M8(b) moves."""
    old, new = before[section.entries[0]], after[section.entries[0]]
    for number, entry in enumerate(section.entries):
        if entry not in readiness:
            continue
        own = set(section.entries[:number])
        now = readiness.counts(entry, lambda r: after.get(r, start) < start or r in own)
        if any(now.get(ability, 0) < count for ability, count in counts0.get(entry, {}).items()):
            return False
    passed = [entry for entry, at in after.items()
              if entry not in section.entries and before.get(entry, -1) > old and at < new and entry not in free]
    if any(entry in overridden or (library_of.get(entry) is not None and entry not in readiness) for entry in passed):
        return False
    using = {item for entry in section.entries for item in users.get(entry, ())}
    for item in passed:
        if item not in using:
            continue
        at = after[item]
        now = readiness.counts(item, lambda r, at=at: after.get(r, at) < at)
        if any(now.get(ability, 0) < count for ability, count in counts0.get(item, {}).items()):
            return False
    return True


def arrange(stages: Sequence[tuple[str, Sequence[Section]]], *, prefix: Sequence[str] = (),
            suffix: Sequence[str] = (), readiness: Readiness, prereqs: Mapping[str, Sequence[str]],
            library_of: Mapping[str, str], params: MixParams = DEFAULT_PARAMS,
            cancelled: Callable[[], bool] | None = None, overridden: frozenset = frozenset()) -> Arrangement:
    """M8(b), then M3-M6 per stage. ``stages``: the mixed stages in route order (derivation order);
    ``prefix``/``suffix``: the entries before (stage 0) and after them (optional stages).
    ``cancelled``: asked between stages; True raises MixCancelled. ``overridden``: entry ids that are
    core although a library provides them (no deferral passes one). When a stage's final order fails
    the M9 check, its deferred sections stay in their source stages and the route is placed again."""
    stages = [(stage_id, tuple(sections)) for stage_id, sections in stages]
    positions0 = _positions(prefix, stages, suffix)
    counts0 = derivation_counts(readiness, positions0)
    def floor(key, count):  # M5: min(derivation count, floor) per ability, min(..., choice_floor) per choice
        return min(count, params.choice_floor if is_choice_key(key) else params.floor)
    base_floors = {item: tuple((key, floor(key, count)) for key, count in sorted(abilities.items())
                               if floor(key, count) > 0)
                   for item, abilities in counts0.items()}
    excluded = set()
    while True:
        ordered, deferred = defer_library_grammar(stages, prefix=prefix, suffix=suffix, readiness=readiness,
                                                  counts0=counts0, prereqs=prereqs, library_of=library_of,
                                                  excluded=frozenset(excluded), overridden=overridden)
        try:
            return Arrangement(_place_stages(stages, ordered, deferred, prefix=prefix, readiness=readiness,
                                             prereqs=prereqs, counts0=counts0, positions0=positions0,
                                             floors=dict(base_floors), params=params, cancelled=cancelled),
                               deferred)
        except _DeferralFailed as failure:
            dropped = {sid for sid, row in deferred.items() if row[1] == failure.stage_id}
            if not dropped - excluded:
                raise  # nothing left to keep back: never loops
            excluded |= dropped


def _place_stages(stages, ordered, deferred, *, prefix, readiness, prereqs, counts0, positions0, floors, params,
                  cancelled):
    """place_stage for every mixed stage in route order (``stages``: as derived; ``ordered``: after M8(b))."""
    # M9: a deferred item keeps every gated question it had (not only the floor) wherever it stands.
    for stage_id, sections in ordered:
        for section in sections:
            if section.id in deferred:
                for entry in section.entries:
                    floors[entry] = tuple((ability, count) for ability, count in sorted(counts0.get(entry, {}).items())
                                          if count > 0)
    known, results = set(prefix), []
    for stage_id, sections in ordered:
        if cancelled is not None and cancelled():
            raise MixCancelled()
        before = frozenset(known)
        here = frozenset(sid for sid, row in deferred.items() if row[1] == stage_id)
        result = place_stage(stage_id, sections, readiness=readiness, prereqs=prereqs, floors=floors,
                             known_before=before.__contains__, params=params, deferred=here, counts0=counts0,
                             positions0=positions0)
        original = next(rows for sid, rows in stages if sid == stage_id)
        if here and not result.changed:
            # deferred sections arrived: they need units of this stage (M7)
            result = replace(result, changed=True, fixed=result.fixed or frozenset(
                section.id for section in sections if section.kind != WORD and section.id not in here))
        elif not result.changed and tuple(sections) != original:
            # only sections leaving changed it (M8(b)): the derivation order, the derivation units
            result = replace(result, changed=True, keep_units=True)
        results.append(result)
        known.update(entry for section in sections for entry in section.entries)
    return tuple(results)
