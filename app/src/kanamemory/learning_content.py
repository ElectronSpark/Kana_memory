"""Local teaching catalogue, separate from the kana spelling toolbox.

Identifiers describe a reading/sense or a construction/function, never a quiz.
Teaching text and source attribution are supplied by an independent content
pack. Citation-form pitch metadata is never treated as sentence intonation.

Importable libraries (design 08 phase 5 follow-ups): get_learning_item resolves
an old id through an enabled extension library's alias (core ids and the
core-only catalogue are unchanged), and a learning identity that two
catalogue namespaces define because of extension content rejects the extension
items (diagnostic recorded) while a duplicate inside core content still raises.
Every library whose items were left out that way is reported damaged, with the
reason, by content_pack.library_status() (design 08 section 16.5).
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import unicodedata
import json

from . import content_pack
from .content_library_index import SharedMapping, isolated_copy
from .content_library_merge import catalogue_items
from .content_pack import load_namespace

_CONTENT = load_namespace('learning_content', {})

CONTENT_REVISION = _CONTENT.get('CONTENT_REVISION', '')
_WORDS = _CONTENT.get('_WORDS', [])
_GRAMMAR = _CONTENT.get('_GRAMMAR', [])

_ITEMS = {item["id"]: item for item in [*_WORDS, *_GRAMMAR]}
_DIAGNOSTICS = []  # (kind, content ids, library ids) rows of the catalogue build


def learning_items(family: str | None = None) -> list[dict]:
    """Return independent snapshots; callers cannot edit the bundled catalogue."""
    from .material_review import reviewed_item
    return [reviewed_item(x) for x in _ITEMS.values() if family is None or x["family"] == family]


_SHARED_FIELDS = ("question_templates",)


def _view(item, shared=False):
    from .material_review import has_item_review, reviewed_item
    if has_item_review(item.get("id")):
        return reviewed_item(item)  # a reviewed correction may replace fields: the full rule decides
    if shared:
        from .shared_tables import share
        return {key: (_shared(value) if key in _SHARED_FIELDS else share(value)) for key, value in item.items()}
    # isolated_copy: the same independent copy as deepcopy for these plain fields, without a call per string
    # (listing the catalogue copies every small field of every lesson, several times per added lesson).
    return {key: (_shared(value) if key in _SHARED_FIELDS else isolated_copy(value)) for key, value in item.items()}


def _shared(value):
    if isinstance(value, SharedMapping):
        return value  # an extension library's templates, read on demand
    return SharedMapping(value) if isinstance(value, dict) else deepcopy(value)


def learning_item_views(family: str | None = None, *, shared: bool = False) -> list[dict]:
    """learning_items for callers that only read (design 08 section 6.5).

    Each item is an independent dict equal to its learning_items snapshot,
    except that question_templates is the catalogue's own mapping, read-only
    (an extension library's is decoded when first read) instead of a copy:
    listing the catalogue then costs neither the copy nor the decoding.
    deepcopy(view) is the plain independent item. Never serialize a view or
    change its templates; copy it first.
    shared=True (a caller that reads every lesson once and keeps none of them, the reviewed-example
    index): the other fields too are the catalogue's own values, handed out through shared_tables.share
    (read-only under the test suite's guard) instead of copied; a lesson with a reviewed correction is
    still its reviewed copy.
    """
    return [_view(x, shared) for x in _ITEMS.values() if family is None or x["family"] == family]


OWN_ITEM_VIEWS = learning_item_views   # the function itself (a test may replace the module's name)


def learning_item_view(content_id: str) -> dict | None:
    """get_learning_item as a read-only view (see learning_item_views)."""
    value = _ITEMS.get(content_id)
    if value is None and isinstance(content_id, str):
        value = _ITEMS.get(_alias_target(content_id))
    return _view(value) if value else None


def catalogue_abilities(content_id: str) -> tuple | None:
    """The abilities the effective catalogue lists for a lesson now (raw names), or None: not a catalogue lesson.

    A content update may list fewer abilities than a saved lesson was enrolled with (brief J6 R4); an empty
    list is a lesson that is only read. No alias is followed and nothing is copied.
    """
    value = _ITEMS.get(content_id) if isinstance(content_id, str) else None
    if value is None:
        return None
    return tuple(value.get("abilities", value.get("dimensions", ("understanding",))))


def catalogue_ids() -> frozenset:
    """Content ids the effective catalogue defines (no item decoding or copies)."""
    return frozenset(_ITEMS)


def catalogue_fingerprints() -> dict:
    """content id -> item_fingerprint of the effective catalogue, as the App sees it."""
    from .material_review import item_fingerprint
    return {content_id: item_fingerprint(item) for content_id, item in _ITEMS.items()}


def get_learning_item(content_id: str) -> dict | None:
    """The reviewed item; an extension library's alias old -> new returns the new item."""
    from .material_review import reviewed_item
    value = _ITEMS.get(content_id)
    if value is None and isinstance(content_id, str):
        value = _ITEMS.get(_alias_target(content_id))
    return reviewed_item(value) if value else None


def _alias_target(content_id):
    """The effective alias target of an id the catalogue lacks (extension libraries only)."""
    if not content_pack.library_layers():
        return None
    catalogue = content_pack.effective_catalogue()
    if not catalogue.extension_ids:
        return None
    target = catalogue.alias_target(content_id)
    return target if target != content_id else None


def catalogue_diagnostics() -> tuple:
    """(kind, content ids, library ids) rows recorded while building the catalogue."""
    return tuple(_DIAGNOSTICS)


def _kana_identity(value):
    return "".join(chr(ord(c) - 0x60) if 0x30A1 <= ord(c) <= 0x30F6 else c
                   for c in unicodedata.normalize("NFKC", value))


def learning_item_from_word(word: dict, sense_index: int = 0, *, enrich: bool = True) -> dict:
    """Select an attested reading/sense, without promoting the whole word entry.

    Dictionary-only entries deliberately have no productive/use units; listening
    only asks which written word was heard, from the fields selected here.
    We never invent an example or translate a missing sense. A missing/restricted
    sense is returned as instruction_missing so enrollment can retain a pending
    item while question generation refuses it.
    """
    word = {**(word.get("payload") or {}), **word}
    form = str(word.get("form") or word.get("display_kanji") or "")
    reading = str(word.get("reading") or word.get("display_reading") or "")
    senses = word.get("senses") or []
    if not isinstance(sense_index, int) or sense_index < 0 or sense_index >= len(senses):
        raise ValueError("Choose an existing dictionary sense")
    sense = senses[sense_index]
    sense_id = sense.get("sense_id") or f"{word['id']}:sense:{sense_index + 1}"
    glosses = [g.get("text", "") for g in sense.get("japanese_meaning_english_glosses", [])
               if g.get("language", "eng") in ("eng", "en") and g.get("text")]
    chinese = next((s.get("glosses") or [] for s in (word.get("chinese_senses") or [])
                    if s.get("sense_id") == sense_id), [])
    meaning = "；".join(chinese) if chinese else "; ".join(glosses)
    allowed = bool(form and reading and glosses
                   and (not sense.get("reading_restrictions") or reading in sense["reading_restrictions"])
                   and (not sense.get("kanji_restrictions") or form in sense["kanji_restrictions"]))
    reading_records = word.get("readings") or []
    exact = next((r for r in reading_records if r.get("text") == reading), None)
    if reading_records and (not exact or (exact.get("applies_to_kanji") and form not in exact["applies_to_kanji"])):
        allowed = False
    other_readings = []
    for r in reading_records:
        text = r.get("text")
        if (text and (not r.get("applies_to_kanji") or form in r["applies_to_kanji"])
                and (not sense.get("reading_restrictions") or text in sense["reading_restrictions"])
                and "search-only kana form" not in r.get("info", [])):
            other_readings.append(text)
    pitch = next((p for p in (word.get("pitch_accent") or [])
                  if p.get("source_form") == form and p.get("reading") == reading
                  and len(p.get("positions", [])) == 1), None)
    accent = pitch["positions"][0] if pitch else None
    snapshot = json.dumps({"form": form, "reading": reading, "sense": sense,
                           "meaning": meaning}, ensure_ascii=False, sort_keys=True)
    revision = "dictionary-v1-" + hashlib.sha256(snapshot.encode()).hexdigest()[:16]
    identity = f"word:{word['id']}:{reading}:{sense_id}"
    result = {
        "id": identity, "content_id": identity, "family": "word", "title": form,
        "reading": reading, "meaning": meaning, "revision": revision,
        "abilities": ["understanding"] + ([] if _kana_identity(form) == _kana_identity(reading) else ["reading"]) + ["listening"],
        "dictionary_only": True,
        "instruction_status": "ready" if allowed else "instruction_missing",
        "material_status": "dictionary_sense; usage_examples_not_pedagogically_reviewed",
        "word_id": word["id"], "sense_id": sense_id,
        "accepted_readings": [reading],
        "alternative_readings": list(dict.fromkeys(r for r in other_readings if r != reading)),
        "accepted_meanings": list(dict.fromkeys([meaning, *chinese, *glosses])),
        "instruction": {"summary": meaning,
                        "rule": "本次只学习所列词典义项与读法；其它义项和用法另学。",
                        "steps": [f"{form}（{reading}）", meaning],
                        "example": form, "translation": meaning,
                        "prerequisite": "", "contrast": ""},
        "accent": accent, "accent_scope": "citation_form_not_sentence_sandhi",
        "reading_source_id": word.get("entry_id") or word["id"],
        "accent_source_id": "kanjium-pitch-accent" if pitch else None,
        "source_ids": list(word.get("source_ids") or [word["id"]]),
        "source_url": word.get("source_url"),
    }
    if enrich:
        from .vocabulary_lessons import enrich_word_lesson
        return enrich_word_lesson(word, sense_index, result)
    return result


def learning_sources() -> list[dict]:
    from .grammar_curriculum import grammar_sources
    from .exam_grammar_curriculum import grammar_sources as exam_sources
    from .usage_grammar_curriculum import grammar_sources as usage_sources
    from .vocabulary_lessons import vocabulary_sources
    from .pronunciation_curriculum import pronunciation_sources
    return deepcopy(_CONTENT.get("SOURCES", [])) + grammar_sources() + exam_sources() + usage_sources() + vocabulary_sources() + pronunciation_sources()


def _duplicate_ids(groups):
    seen, duplicates = set(_ITEMS), []
    for items in groups:
        for item in items:
            if item["id"] in seen:
                duplicates.append(item["id"])
            seen.add(item["id"])
    return list(dict.fromkeys(duplicates))


def _without_extension_duplicates(sources):
    """Items to add after the learning_content namespace (design 08 §6.3).

    A learning identity defined twice across catalogue namespaces is a core
    packaging error when only core content is involved: everything is
    returned and the caller raises, as before. When every duplicate involves
    an id an enabled extension library contributed, the extension is rejected
    from this catalogue: its extension-only items are left out (also from the
    learning_content namespace), one core copy of each duplicated core id is
    kept, and a content_library diagnostic is recorded.
    """
    groups = [items for _, items in sources]
    everything = [item for items in groups for item in items]
    duplicates = _duplicate_ids(groups)
    if not duplicates or not content_pack.library_layers():
        return everything
    catalogue = content_pack.effective_catalogue()
    contributed, by_library = {}, {}
    for library_id in catalogue.extension_ids:
        for name, payload in catalogue.payloads.get(library_id, {}).items():
            ids = {cid for cid, _ in catalogue_items(payload)}
            contributed.setdefault(name, set()).update(ids)
            by_library.setdefault(library_id, set()).update(ids)
    extension_ids = set().union(*contributed.values()) if contributed else set()
    placed = {cid: {"learning_content"} for cid in _ITEMS}
    for name, items in sources:
        for item in items:
            placed.setdefault(item["id"], set()).add(name)
    # Extension-involved: some copy sits in a namespace an extension contributed it to.
    if not extension_ids or any(not any(cid in contributed.get(name, ()) for name in placed[cid])
                                for cid in duplicates):
        return everything
    core_ids = catalogue.core_ids()
    rejected = extension_ids - core_ids
    for cid in [cid for cid in _ITEMS if cid in rejected]:
        del _ITEMS[cid]
    involved = set(duplicates)
    preferred = {}
    for name, items in sources:
        for item in items:
            if item["id"] in involved and item["id"] not in contributed.get(name, ()):
                preferred.setdefault(item["id"], name)
    keep, kept = [], set(_ITEMS)
    for name, items in sources:
        for item in items:
            cid = item["id"]
            if cid in rejected:
                continue
            if cid in involved:
                if cid in kept or preferred.get(cid, name) != name:
                    continue
                kept.add(cid)
            keep.append(item)
    libraries = tuple(lib for lib in catalogue.extension_ids if by_library.get(lib, set()) & involved)
    _DIAGNOSTICS.append(("duplicate_identity_rejected_extension", tuple(duplicates), libraries))
    shown = "、".join(duplicates[:3]) + ("等" if len(duplicates) > 3 else "")
    reasons = {}
    for library_id in catalogue.extension_ids:
        if library_id in libraries:
            reasons[library_id] = f"学习目录拒绝了本库内容：与其他命名空间重复定义学习 ID（{shown}）"
        elif by_library.get(library_id, set()) & rejected:
            reasons[library_id] = "学习目录拒绝了本库内容：其他扩展库的重复学习 ID 使扩展内容一并未加入"
    content_pack.record_catalogue_rejection(getattr(catalogue, "token", None), reasons,
                                            bystanders=tuple(lib for lib in reasons if lib not in libraries))
    from . import diagnostic_events
    diagnostic_events.record_error("content_library", "read",
                                   ValueError("Duplicate learning identity across catalogue namespaces"))
    return keep


def _rows(module, name="_ITEMS"):
    """Independent catalogue rows of a curriculum module.

    The modules' own accessors deepcopy, which would decode every question
    template an extension library reads on demand (design 08 section 6.5);
    isolated_copy keeps those deferred and is otherwise the same copy.
    """
    return isolated_copy(getattr(module, name))


def _extend_catalogue():
    from . import (_vocabulary_catalog, exam_grammar_curriculum, grammar_curriculum, pronunciation_curriculum,
                   usage_grammar_curriculum)
    from . import vocabulary_lessons  # noqa: F401 - loaded together with the catalogue, as it always was
    from .learning_dependencies import validate_catalog
    grammar_sources = [("grammar_curriculum", _rows(grammar_curriculum)),
                       ("exam_grammar_curriculum", _rows(exam_grammar_curriculum)),
                       ("usage_grammar_curriculum", _rows(usage_grammar_curriculum))]
    for identity, metadata in getattr(grammar_curriculum, "LEGACY_GRAMMAR_METADATA", {}).items():
        _ITEMS[identity].update(deepcopy(metadata))
    sources = [*grammar_sources, ("vocabulary_catalog", _rows(_vocabulary_catalog, "WORD_LESSONS")),
               ("pronunciation_curriculum", _rows(pronunciation_curriculum))]
    added = _without_extension_duplicates(sources)
    added_objects = {id(item) for item in added}
    new_grammar = [item for _, items in grammar_sources for item in items if id(item) in added_objects]
    for item in added:
        if item["id"] in _ITEMS:
            raise ValueError("Duplicate bundled learning identity: " + item["id"])
        _ITEMS[item["id"]] = item
    # Begin the library with the authored foundation sequence, then insert any
    # prerequisites before their dependents. Saved queues have their own order.
    catalogue = {item["id"]: item for item in [*new_grammar, *_ITEMS.values()]}
    from .course_prerequisites import apply_course_prerequisites
    apply_course_prerequisites(catalogue)
    ordered = validate_catalog(catalogue)
    _ITEMS.clear()
    _ITEMS.update((identity, catalogue[identity]) for identity in ordered)
    # Every word can be heard: the task derives from the lesson's own fields.
    # Reviewed teaching and its fingerprint do not include the ability list.
    # A lesson that is only read (abilities: [], brief J6 R1) gets none.
    for item in _ITEMS.values():
        if (item.get("family") == "word" and item.get("abilities", ("understanding",))
                and "listening" not in item.get("abilities", ())):
            item["abilities"] = [*item.get("abilities", ()), "listening"]


_extend_catalogue()
