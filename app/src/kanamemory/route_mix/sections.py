"""M8(a): cut an extension library's large sections (design 11 section 16; pure).

Done while the derivation tree is built, without question data. A library's
word section with more than params.library_word_limit words becomes sections
of params.library_word_chunk words in catalogue order; a library grammar
section with more than one item becomes one section per item. A new section is
section:<digest(old id|n)> titled "old title（n）"; its depends_on is computed
from its entries again, and a section that depended on the old one depends on
the new ones its entries need. Core sections, sections of several libraries
and every section of a fixed stage (stage 0, optional stages) stay as they are.
"""
from __future__ import annotations

from collections.abc import Callable, Collection
import hashlib

from .params import DEFAULT_PARAMS, GIVEN_ORDER, MixParams


def _digest(value: str) -> str:
    """The section identity digest of study_path_catalog (sha256, 12 hex digits)."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _flatten(path):
    return [section for stage in path["stages"] for unit in stage["units"] for section in unit["sections"]]


def library_of_section(section, library_of: Callable[[str], str | None]) -> str | None:
    """The one extension library that provides every learning entry of a section, else None."""
    entries = section.get("entries") or ()
    if not entries or any(entry.get("kind") != "learning" for entry in entries):
        return None
    owners = {library_of(entry.get("content_id", entry.get("id"))) for entry in entries}
    return next(iter(owners)) if len(owners) == 1 and None not in owners else None


def _chunks(section, library_of, params):
    if section.get(GIVEN_ORDER) or library_of_section(section, library_of) is None:
        # A section of the main route's given beginning is cut as the content gave it.
        return None
    entries = section["entries"]
    if all(entry.get("family") == "word" for entry in entries):
        size = params.library_word_chunk if len(entries) > params.library_word_limit else None
    elif all(entry.get("family") == "grammar" and entry.get("domain") != "pronunciation" for entry in entries):
        size = 1 if len(entries) > 1 else None
    else:
        size = None
    return None if size is None else [entries[start:start + size] for start in range(0, len(entries), size)]


def split_library_sections(path, library_of: Callable[[str], str | None] | None, *,
                           fixed_stage_ids: Collection[str] = (), params: MixParams = DEFAULT_PARAMS,
                           digest: Callable[[str], str] = _digest):
    """The tree with M8(a) applied; the same object when nothing is cut (always without library_of)."""
    if library_of is None:
        return path
    cuts = {}
    for stage in path["stages"]:
        if stage.get("optional") or stage["id"] in fixed_stage_ids:
            continue
        for unit in stage["units"]:
            for section in unit["sections"]:
                chunks = _chunks(section, library_of, params)
                if chunks:
                    cuts[section["id"]] = chunks
    if not cuts:
        return path
    new_ids, owner = {}, {}
    for section in _flatten(path):
        chunks = cuts.get(section["id"])
        if chunks is None:
            owner.update((entry["id"], section["id"]) for entry in section["entries"])
            continue
        ids = new_ids[section["id"]] = ["section:" + digest(f"{section['id']}|{number}")
                                        for number in range(1, len(chunks) + 1)]
        for sid, chunk in zip(ids, chunks):
            owner.update((entry["id"], sid) for entry in chunk)
    stages, created = [], []
    for stage in path["stages"]:
        units = []
        for unit in stage["units"]:
            sections = []
            for section in unit["sections"]:
                chunks = cuts.get(section["id"])
                if chunks is None:
                    if any(dependency in new_ids for dependency in section.get("depends_on", ())):
                        section = dict(section, depends_on=_replaced(section, new_ids, owner))
                    sections.append(section)
                    continue
                unresolved = set(section.get("unresolved_dependencies", ()))
                for number, (sid, chunk) in enumerate(zip(new_ids[section["id"]], chunks), 1):
                    title = f"{section['title']}（{number}）"
                    new = dict(section, id=sid, title=title, entries=list(chunk), depends_on=[],
                               component_section_ids=[sid], topics=[title])
                    new.pop("unresolved_dependencies", None)
                    missing = sorted({dependency for entry in chunk for dependency in entry.get("depends_on", ())
                                      if dependency in unresolved})
                    if missing:
                        new["unresolved_dependencies"] = missing
                    sections.append(new)
                    created.append(new)
            units.append(dict(unit, sections=sections))
        stages.append(dict(stage, units=units))
    result = dict(path, stages=stages)
    positions = {section["id"]: index for index, section in enumerate(_flatten(result))}
    for section in created:
        section["depends_on"] = sorted({owner[dependency] for entry in section["entries"]
                                        for dependency in entry.get("depends_on", ())
                                        if dependency in owner and owner[dependency] != section["id"]},
                                       key=positions.__getitem__)
    return result


def _replaced(section, new_ids, owner):
    """depends_on with each cut section replaced by the new sections this section's entries need."""
    needed = {owner[dependency] for entry in section["entries"] for dependency in entry.get("depends_on", ())
              if dependency in owner}
    result = []
    for dependency in section["depends_on"]:
        if dependency in new_ids:
            result.extend(sid for sid in new_ids[dependency] if sid in needed and sid not in result)
        elif dependency not in result:
            result.append(dependency)
    return result
