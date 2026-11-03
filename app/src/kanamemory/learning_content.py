"""Local teaching catalogue, separate from the kana spelling toolbox.

Identifiers describe a reading/sense or a construction/function, never a quiz.
Teaching text and source attribution are supplied by an independent content
pack. Citation-form pitch metadata is never treated as sentence intonation.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import unicodedata
import json

from .content_pack import load_namespace

_CONTENT = load_namespace('learning_content', {})

CONTENT_REVISION = _CONTENT.get('CONTENT_REVISION', '')
_WORDS = _CONTENT.get('_WORDS', [])
_GRAMMAR = _CONTENT.get('_GRAMMAR', [])

_ITEMS = {item["id"]: item for item in [*_WORDS, *_GRAMMAR]}


def learning_items(family: str | None = None) -> list[dict]:
    """Return independent snapshots; callers cannot edit the bundled catalogue."""
    from .material_review import reviewed_item
    return [reviewed_item(x) for x in _ITEMS.values() if family is None or x["family"] == family]


def get_learning_item(content_id: str) -> dict | None:
    from .material_review import reviewed_item
    value = _ITEMS.get(content_id)
    return reviewed_item(value) if value else None


def _kana_identity(value):
    return "".join(chr(ord(c) - 0x60) if 0x30A1 <= ord(c) <= 0x30F6 else c
                   for c in unicodedata.normalize("NFKC", value))


def learning_item_from_word(word: dict, sense_index: int = 0, *, enrich: bool = True) -> dict:
    """Select an attested reading/sense, without promoting the whole word entry.

    Dictionary-only entries deliberately have no productive/use/listening units.
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
        "abilities": ["understanding"] + ([] if _kana_identity(form) == _kana_identity(reading) else ["reading"]),
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


def _extend_catalogue():
    from . import grammar_curriculum
    from .exam_grammar_curriculum import grammar_items as exam_items
    from .usage_grammar_curriculum import grammar_items as usage_items
    from .vocabulary_lessons import bundled_word_lessons
    from .pronunciation_curriculum import pronunciation_items
    from .learning_dependencies import validate_catalog
    new_grammar = [*grammar_curriculum.grammar_items(), *exam_items(), *usage_items()]
    for identity, metadata in getattr(grammar_curriculum, "LEGACY_GRAMMAR_METADATA", {}).items():
        _ITEMS[identity].update(deepcopy(metadata))
    for item in [*new_grammar, *bundled_word_lessons(), *pronunciation_items()]:
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


_extend_catalogue()
