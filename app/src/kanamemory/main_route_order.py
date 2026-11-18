"""The given beginning of the core main route (study_path_catalog MAIN_ROUTE_ORDER; pure).

MAIN_ROUTE_ORDER gives the lessons that come first on the main route, in
order; the lessons not listed follow, placed as before. It has two forms:

- structured (what the content writes): a list of units
  ``{"title": text, "sections": [{"title": text, "entries": [content id, ...]}, ...]}``
  (the field names of the explicit routes, study_routes.validate_route_spec);
  units and sections are taken as given, with their titles;
- flat (fallback, test input): a list of content ids, cut by flat_layout.

The beginning stands at the start of the first stage after the fixed sounds
stage (the stage route_mix never mixes either): stages are one route order,
so a beginning that runs through the lessons of several stages can only be
read as the opening of one stage. A listed lesson the sounds stage holds stays
there (it stands before the beginning already), and so does an optional one.
Section and unit ids are digests of what they hold; sections are marked
``given_order`` so that the main-route mix keeps them where they are.

Prerequisites stay hard: a lesson listed before one of its depends_on lessons
is a content error (order_problems names every such pair, with the other
errors). The route is still built from such data, never dropping a lesson: a
missing prerequisite is placed first (a structured order: in a section of its
own just before the section that needs it, in the same unit), an oversized or
mixed-family section is split, an empty title is replaced; the caller records
one diagnostic. Learning ids and entries never change, only positions.

No Toga, database, clock or network.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import hashlib

ORDER_KEY = "MAIN_ROUTE_ORDER"
GIVEN_ORDER = "given_order"  # section flag: a section of the given beginning (route_mix keeps it)
# Largest sections the route builds today: word topic sections up to 8 words, combined grammar
# (and reading-rule) sections up to 4 lessons (_combine_micro_lessons).
WORD_LIMIT = 8
OTHER_LIMIT = 4
UNIT_SECTIONS = 8  # a flat order starts a new unit after this many sections
_MAX_ORDER = 20000
_ERRORS = ("malformed", "empty_title", "empty", "oversize", "mixed_family", "unknown", "repeated",
           "prerequisite_after")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _limit(family) -> int:
    return WORD_LIMIT if family == "word" else OTHER_LIMIT


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _list(value) -> bool:
    return isinstance(value, (list, tuple))


def is_structured(value) -> bool:
    """A non-empty list of units (mappings); a flat order is a list of ids."""
    return _list(value) and bool(value) and all(isinstance(unit, Mapping) for unit in value)


def order_ids(value) -> tuple:
    """Every listed id in order (both forms), as the app reads them: anything that is not one of the
    two forms is no given order at all; entries that are not text are skipped."""
    if not _list(value):
        return ()
    if is_structured(value):
        ids = [cid for _, sections in _units(value) for _, entries in sections for cid in entries]
    elif any(isinstance(unit, Mapping) for unit in value):
        return ()
    else:
        ids = value
    return tuple(cid for cid in ids[:_MAX_ORDER] if isinstance(cid, str) and cid)


def _units(value):
    """The structured form as [(unit title or None, [(section title or None, [ids])])], what can be read
    of it (order_problems says what cannot)."""
    units = []
    for unit in value:
        sections = []
        for section in unit.get("sections") if _list(unit.get("sections")) else ():
            if not isinstance(section, Mapping):
                continue
            entries = section.get("entries") if _list(section.get("entries")) else ()
            sections.append((section.get("title") if _text(section.get("title")) else None,
                             [cid for cid in entries if isinstance(cid, str) and cid]))
        units.append((unit.get("title") if _text(unit.get("title")) else None, sections))
    return units


def _structure_trouble(value) -> bool:
    """Whether a structured order has a missing title, an empty or unreadable unit or section."""
    for unit in value:
        sections = unit.get("sections")
        if not _text(unit.get("title")) or not _list(sections) or not sections:
            return True
        for section in sections:
            if not isinstance(section, Mapping) or not _text(section.get("title")):
                return True
            entries = section.get("entries")
            if not _list(entries) or not entries or any(not isinstance(cid, str) or not cid for cid in entries):
                return True
    return False


def _learning(records) -> dict:
    return {record[5]["id"]: record for record in records if record[5].get("kind") == "learning"}


def _orderable(record, first_stage, optional_stage) -> bool:
    return record is not None and first_stage <= record[0] < optional_stage


def order_problems(value, records, *, first_stage: int = 1, optional_stage: int = 6) -> dict:
    """What is wrong with a MAIN_ROUTE_ORDER value over study_path_records ``records``.

    Places: "entry n" in a flat order (from 0); "unit u", "unit u section s" and "unit u section s
    entry n" in a structured one (from 1). Errors: ``malformed`` (place, or None: neither form;
    a unit or section that is not a mapping, a unit without a sections list, a section without an
    entries list, an entry that is not a content id), ``empty_title`` (place), ``empty`` (place: a
    unit without sections, a section without entries), ``oversize`` ((place, family, size, limit)),
    ``mixed_family`` ((place, families)), ``unknown`` ((place, id)), ``repeated`` ((place, id,
    first place)), ``prerequisite_after`` ((place, id, prerequisite, its place or None when not
    listed)). Notes: ``kept_in_place`` ((place, id): a lesson of the sounds stage or of an
    optional stage keeps its place)."""
    result = {name: [] for name in (*_ERRORS, "kept_in_place")}
    if value is None:
        return result
    lessons = _learning(records)
    listed = []  # (place, id)
    if is_structured(value):
        for u, unit in enumerate(value, 1):
            where = f"unit {u}"
            if not _text(unit.get("title")):
                result["empty_title"].append(where)
            sections = unit.get("sections")
            if not _list(sections):
                result["malformed"].append(where)
                continue
            if not sections:
                result["empty"].append(where)
            for s, section in enumerate(sections, 1):
                place = f"{where} section {s}"
                if not isinstance(section, Mapping):
                    result["malformed"].append(place)
                    continue
                if not _text(section.get("title")):
                    result["empty_title"].append(place)
                entries = section.get("entries")
                if not _list(entries):
                    result["malformed"].append(place)
                    continue
                if not entries:
                    result["empty"].append(place)
                families = []
                for n, cid in enumerate(entries, 1):
                    if not isinstance(cid, str) or not cid:
                        result["malformed"].append(f"{place} entry {n}")
                        continue
                    listed.append((f"{place} entry {n}", cid))
                    if cid in lessons:
                        families.append(lessons[cid][5].get("family"))
                kinds = sorted(set(families), key=str)
                if len(kinds) > 1:
                    result["mixed_family"].append((place, tuple(kinds)))
                elif kinds and len(families) > _limit(kinds[0]):
                    result["oversize"].append((place, kinds[0], len(families), _limit(kinds[0])))
    elif _list(value) and not any(isinstance(unit, Mapping) for unit in value):
        for position, cid in enumerate(value):
            if not isinstance(cid, str) or not cid:
                result["malformed"].append(f"entry {position}")
            else:
                listed.append((f"entry {position}", cid))
    else:
        result["malformed"].append(None)
        return result
    first, index = {}, {}
    for number, (place, cid) in enumerate(listed):
        if cid in first:
            result["repeated"].append((place, cid, first[cid]))
            continue
        first[cid], index[cid] = place, number
        if cid not in lessons:
            result["unknown"].append((place, cid))
        elif not _orderable(lessons[cid], first_stage, optional_stage):
            result["kept_in_place"].append((place, cid))
    for cid, place in first.items():
        record = lessons.get(cid)
        if not _orderable(record, first_stage, optional_stage):
            continue
        for dependency in record[5].get("depends_on", ()):
            if not _orderable(lessons.get(dependency), first_stage, optional_stage):
                continue  # in the sounds stage (before) or not a lesson of the route
            if dependency not in index or index[dependency] > index[cid]:
                result["prerequisite_after"].append((place, cid, dependency, first.get(dependency)))
    return result


def problem_count(problems: Mapping) -> int:
    """The number of errors (notes left out)."""
    return sum(len(problems[name]) for name in _ERRORS)


def describe_problems(problems: Mapping) -> list:
    """One line per problem, errors first, for the checking tool and the content tests."""
    lines = [("MAIN_ROUTE_ORDER is neither a list of content ids nor a list of units" if place is None
              else f"{place}: not readable (a unit needs a sections list, a section an entries list of content ids)")
             for place in problems["malformed"]]
    lines.extend(f"{place}: empty or missing title" for place in problems["empty_title"])
    lines.extend(f"{place}: empty" for place in problems["empty"])
    lines.extend(f"{place}: {size} {family} entries, more than {limit}"
                 for place, family, size, limit in problems["oversize"])
    lines.extend(f"{place}: entries of different families ({', '.join(map(str, families))})"
                 for place, families in problems["mixed_family"])
    lines.extend(f"{place}: unknown id {cid}" for place, cid in problems["unknown"])
    lines.extend(f"{place}: {cid} repeated (first at {first})" for place, cid, first in problems["repeated"])
    for place, cid, dependency, at in problems["prerequisite_after"]:
        where = "is not listed" if at is None else f"is listed later, at {at}"
        lines.append(f"{place}: {cid} comes before its prerequisite {dependency}, which {where}")
    lines.extend(f"note: {place}: {cid} belongs to the sounds stage or an optional stage and keeps its place"
                 for place, cid in problems["kept_in_place"])
    return lines


class _Placer:
    """Places listed lessons once each, a missing prerequisite first (recursively)."""

    def __init__(self, records, first_stage, optional_stage):
        self.lessons = _learning(records)
        self.first_stage, self.optional_stage = first_stage, optional_stage
        self.placed, self.lifted, self.repeated = set(), [], 0

    def orderable(self, cid) -> bool:
        return _orderable(self.lessons.get(cid), self.first_stage, self.optional_stage)

    def _place(self, cid, lifted_out, own_out, chain):
        for dependency in self.lessons[cid][5].get("depends_on", ()):
            if dependency in self.placed or dependency in chain or not self.orderable(dependency):
                continue
            self.lifted.append((cid, dependency))
            self._place(dependency, lifted_out, lifted_out, chain | {cid})
        if cid not in self.placed:
            self.placed.add(cid)
            own_out.append(cid)

    def take(self, cid, lifted_out, own_out):
        """Place a listed lesson: its unplaced prerequisites go to lifted_out, it to own_out."""
        if cid in self.placed:
            self.repeated += 1
        elif self.orderable(cid):
            self._place(cid, lifted_out, own_out, frozenset())


def beginning_sequence(order: Sequence[str], records, *, first_stage: int = 1, optional_stage: int = 6):
    """(lesson ids of a flat beginning in route order, lifted (lesson, prerequisite) pairs)."""
    placer, sequence = _Placer(records, first_stage, optional_stage), []
    for cid in order:
        placer.take(cid, sequence, sequence)
    return sequence, placer.lifted


def _topic(lessons, cid):
    return lessons[cid][3]


def _is_grammar(lessons, cid) -> bool:
    entry = lessons[cid][5]
    return entry.get("family") == "grammar" and entry.get("domain") != "pronunciation"


def _fits(section, cid, lessons) -> bool:
    """Whether lesson ``cid`` continues ``section`` in a flat order."""
    head, entry = lessons[section[0]][5], lessons[cid][5]
    if head.get("family") != entry.get("family"):
        return False
    if entry.get("family") == "word":
        return len(section) < WORD_LIMIT
    return len(section) < OTHER_LIMIT and _topic(lessons, section[0]) == _topic(lessons, cid)


def _section_title(ids, lessons, words_title):
    topics = {_topic(lessons, cid) for cid in ids}
    if len(topics) == 1:
        return next(iter(topics))
    return words_title if lessons[ids[0]][5].get("family") == "word" else _topic(lessons, ids[0])


def _unit_title(ids, lessons, opening_title, number):
    """(title, number): the unit title of the first grammar lesson, else the numbered opening text."""
    grammar = next((cid for cid in ids if _is_grammar(lessons, cid)), None)
    if grammar is not None:
        return lessons[grammar][2], number
    return opening_title.format(number + 1), number + 1


def flat_layout(order: Sequence[str], records, *, words_title: str, opening_title: str,
                first_stage: int = 1, optional_stage: int = 6):
    """(layout, trouble) of a flat order: layout [(unit title, [(section title, [ids])])].

    Consecutive words form one section whatever their topics, up to WORD_LIMIT; consecutive
    lessons of another family form a section while they share a topic, up to OTHER_LIMIT. A unit
    starts at every grammar lesson (a grammar point and what follows it) and after UNIT_SECTIONS
    sections. A section is titled with its entries' topic when they share one, else
    ``words_title``; a unit with the unit title of its first grammar lesson, else ``opening_title``
    with its number among such units ("{}"). ``trouble``: a prerequisite was lifted."""
    sequence, lifted = beginning_sequence(order, records, first_stage=first_stage, optional_stage=optional_stage)
    lessons = _learning(records)
    units = []  # [[ids], ...] per unit
    for cid in sequence:
        if not units or _is_grammar(lessons, cid):
            units.append([])
        sections = units[-1]
        if sections and _fits(sections[-1], cid, lessons):
            sections[-1].append(cid)
            continue
        if len(sections) >= UNIT_SECTIONS:
            units.append([])
            sections = units[-1]
        sections.append([cid])
    layout, number = [], 0
    for sections in units:
        title, number = _unit_title([cid for ids in sections for cid in ids], lessons, opening_title, number)
        layout.append((title, [(_section_title(ids, lessons, words_title), ids) for ids in sections]))
    return layout, bool(lifted)


def _split(ids, lessons):
    """Runs of one family, each cut into parts of at most its family's limit."""
    runs = []
    for cid in ids:
        family = lessons[cid][5].get("family")
        if runs and runs[-1][0] == family:
            runs[-1][1].append(cid)
        else:
            runs.append((family, [cid]))
    return [run[start:start + _limit(family)] for family, run in runs for start in range(0, len(run), _limit(family))]


def structured_layout(value, records, *, words_title: str, opening_title: str,
                      first_stage: int = 1, optional_stage: int = 6):
    """(layout, trouble) of a structured order, taken as given where it can be.

    Lessons that are not on the route, repeated or kept in place are left out; a lifted
    prerequisite gets a section of its own (titled with its topic) just before the section that
    needs it, in the same unit; a section of several families is split into runs of one family and
    a run over its family's limit into parts of the limit ("title（n）" from the second part on); a
    missing title is replaced as in a flat order; sections and units left empty are left out.
    ``trouble``: any of these repairs or structural errors (the caller's one diagnostic)."""
    placer = _Placer(records, first_stage, optional_stage)
    lessons = placer.lessons
    trouble = _structure_trouble(value)
    layout, number = [], 0
    for unit_title, sections in _units(value):
        built = []
        for section_title, ids in sections:
            lifted, own = [], []
            for cid in ids:
                placer.take(cid, lifted, own)
            built.extend((_topic(lessons, cid), [cid]) for cid in lifted)
            parts = _split(own, lessons)
            trouble = trouble or len(parts) > 1
            for n, part in enumerate(parts, 1):
                title = section_title if section_title is not None else _section_title(part, lessons, words_title)
                built.append((title if n == 1 else f"{title}（{n}）", part))
        if not built:
            continue
        if unit_title is None:
            unit_title, number = _unit_title([cid for _, part in built for cid in part], lessons,
                                             opening_title, number)
        layout.append((unit_title, built))
    return layout, trouble or bool(placer.lifted) or placer.repeated > 0


def beginning_units(layout, records, stage: tuple, *, digest: Callable[[str], str] = _digest) -> list:
    """The route units of a layout (stage: (stage id, stage title)); section depends_on left empty."""
    lessons = _learning(records)
    stage_id, stage_title = stage
    units = []
    for unit_title, sections in layout:
        rows = []
        for section_title, ids in sections:
            sid = "section:" + digest("main-order|" + stage_id + "|" + "|".join(ids))
            rows.append({"id": sid, "title": section_title,
                         "entries": [dict(lessons[cid][5], depends_on=list(lessons[cid][5].get("depends_on", ())))
                                     for cid in ids]})
        unit = {"id": "unit:" + digest(f"main-order|{stage_id}|{unit_title}|{rows[0]['id']}"),
                "title": unit_title, "sections": rows}
        for section in rows:
            section.update(depends_on=[], optional=False, stage_id=stage_id, stage_title=stage_title,
                           unit_id=unit["id"], unit_title=unit_title, component_section_ids=[section["id"]],
                           topics=[section["title"]], **{GIVEN_ORDER: True})
        units.append(unit)
    return units


def join_beginning(path, units, stage: tuple, stage_ids: Sequence[str]):
    """The route with the beginning's units at the start of ``stage`` (made when the rest has no
    section there), every section's depends_on naming the sections its entries need. ``path`` is the
    route of the other lessons (changed in place and returned)."""
    if not units:
        return path
    stage_id, stage_title = stage
    target = next((value for value in path["stages"] if value["id"] == stage_id), None)
    if target is None:
        target = {"id": stage_id, "title": stage_title, "optional": False, "units": []}
        rank = {value: index for index, value in enumerate(stage_ids)}
        at = next((index for index, value in enumerate(path["stages"])
                   if rank.get(value["id"], len(rank)) > rank.get(stage_id, len(rank))), len(path["stages"]))
        path["stages"].insert(at, target)
    target["units"][:0] = units
    sections = [section for value in path["stages"] for unit in value["units"] for section in unit["sections"]]
    positions = {section["id"]: index for index, section in enumerate(sections)}
    owner = {entry["id"]: section["id"] for section in sections for entry in section["entries"]}
    given = {section["id"] for unit in units for section in unit["sections"]}
    for section in sections:
        needed = {owner[dependency] for entry in section["entries"] for dependency in entry.get("depends_on", ())
                  if dependency in owner and owner[dependency] != section["id"]}
        if section["id"] in given:
            section["depends_on"] = sorted(needed, key=positions.__getitem__)
        else:
            extra = {sid for sid in needed if sid in given} - set(section["depends_on"])
            if extra:
                section["depends_on"] = sorted({*section["depends_on"], *extra}, key=positions.__getitem__)
    return path
