"""Sense-bound local vocabulary teaching, built from the bundled JMdict extract.

Examples, notes and dictionary-derived fields live in an independent content
pack. The generated catalogue retains attribution and sense fingerprints.
Dictionary-only introductions are enriched conservatively: an unreviewed example
is never attached merely because the same written word occurs in it.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from .content_pack import load_namespace

_CONTENT = load_namespace('vocabulary_lessons', {})

VOCABULARY_REVISION = _CONTENT.get('VOCABULARY_REVISION', '')
SOURCE_ID = _CONTENT.get('SOURCE_ID', '')

# Conservative exclusions for multiple-choice meanings. Related concepts such
# as 学校/大学, 質問/問題, 声/音 must not be presented as mutually exclusive
# wrong translations. Distinct groups are used only for lexical meaning tasks.
SEMANTIC_GROUPS = _CONTENT.get('SEMANTIC_GROUPS', {})


def semantic_group(form):
    matches = [group for group, forms in SEMANTIC_GROUPS.items() if form in forms]
    if len(matches) != 1:
        raise ValueError("Word needs exactly one distractor exclusion group: " + form)
    return matches[0]

# form | narrowly taught meaning | usage note | two original, aligned examples
# {w} identifies the exact target occurrence, not arbitrary string replacement.
# The first JMdict sense is the compatibility default. Authored overrides name
# an exact sense index; generation still validates its pinned lexical evidence.
LESSON_ROWS = _CONTENT.get('LESSON_ROWS', '')


def lesson_specs():
    """Authoring rows; dictionary identity is resolved only by the build tool."""
    specs = []
    for line in LESSON_ROWS.splitlines():
        columns = line.split("|")
        if len(columns) != 7:
            raise ValueError("Malformed vocabulary lesson: " + columns[0])
        form, meaning, rule, ex1, zh1, ex2, zh2 = columns
        if ex1.count("{w}") != 1 or ex2.count("{w}") != 1:
            raise ValueError("Each example must identify one target")
        sense_index = _CONTENT.get('SENSE_INDEXES', {}).get(form, 0)
        if type(sense_index) is not int or sense_index < 0:
            raise ValueError("Invalid authored sense index: " + form)
        binding = _CONTENT.get('WORD_BINDINGS', {}).get(form, {})
        if (not isinstance(binding, dict) or set(binding) - {'word_id', 'reading'}
                or binding and any(not isinstance(binding.get(k), str) or not binding[k]
                                   for k in ('word_id', 'reading'))):
            raise ValueError("Invalid authored dictionary binding: " + form)
        specs.append(dict(form=form, meaning=meaning, rule=rule,
                          examples=[(ex1, zh1), (ex2, zh2)], sense_index=sense_index, **binding))
    return specs


def bundled_word_lessons():
    from ._vocabulary_catalog import WORD_LESSONS
    return deepcopy(WORD_LESSONS)


def attested_word_forms(word):
    """Exclude dictionary lookup aliases from teachable written forms."""
    records = [*word.get('kanji', []), *word.get('readings', [])]
    if not records:
        return {word['form']} if word.get('form') else set()
    return {record['text'] for record in records if record.get('text')
            and not any('search-only' in tag for tag in record.get('info', []))}


def attested_word_pair(word, form, reading):
    if form not in attested_word_forms(word):
        return False
    records = word.get('readings', [])
    if not records:
        return True  # Compatibility with old non-JMdict word snapshots.
    exact = next((row for row in records if row.get('text') == reading), None)
    return bool(exact and not any('search-only' in tag for tag in exact.get('info', []))
                and (not exact.get('no_kanji') or form == reading)
                and (not exact.get('applies_to_kanji') or form in exact['applies_to_kanji']))


def _part_of_speech(sense):
    tags = sense.get("pos_effective") or sense.get("pos_explicit") or []
    result = []
    for tag in tags:
        low = tag.lower()
        if "ichidan verb" in low:
            result.append("一段动词")
        elif "godan verb" in low:
            result.append("五段动词")
        elif "suru verb" in low or "aux. verb suru" in low:
            result.append("可接する的词")
        elif "kuru verb" in low:
            result.append("来る类不规则动词")
        elif low == "transitive verb":
            result.append("他动词")
        elif low == "intransitive verb":
            result.append("自动词")
        elif "adjectival nouns" in low or "na-adjective" in low:
            result.append("な形容词")
        elif "i-adjective" in low or low == "adjective (keiyoushi)":
            result.append("い形容词")
        elif "pronoun" in low:
            result.append("代词")
        elif "noun" in low:
            result.append("名词")
        elif "adverb" in low:
            result.append("副词")
        elif "interjection" in low:
            result.append("感叹词")
        elif "conjunction" in low:
            result.append("接续词")
        elif "particle" in low:
            result.append("助词")
        elif "counter" in low:
            result.append("量词")
        elif "numeric" in low:
            result.append("数词")
        elif "prefix" in low:
            result.append("前缀")
        elif "suffix" in low:
            result.append("后缀")
    return list(dict.fromkeys(result))


def _legacy_identity_lesson(word, sense_index, item):
    """Reuse an explicitly pinned old teaching identity, without enrolling it."""
    mappings = _CONTENT.get('WORD_IDENTITY_ALIASES', {})
    binding = mappings.get(item.get('id')) if isinstance(mappings, dict) else None
    fields = {'target_id', 'dictionary_sense_sha256', 'form', 'reading'}
    if (item.get('instruction_status') != 'ready' or not isinstance(binding, dict)
            or set(binding) != fields
            or any(not isinstance(binding.get(key), str) or not binding[key] for key in fields)
            or (item.get('title'), item.get('reading')) != (binding['form'], binding['reading'])
            or not attested_word_pair(word, binding['form'], binding['reading'])):
        return None
    from .learning_content import get_learning_item, learning_item_from_word
    current = learning_item_from_word(word, sense_index, enrich=False)
    if (current['instruction_status'] != 'ready' or current['id'] != item['id']
            or (current['title'], current['reading']) != (binding['form'], binding['reading'])):
        return None
    fingerprint = hashlib.sha256(json.dumps(word['senses'][sense_index],
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if fingerprint != binding['dictionary_sense_sha256']:
        return None
    target = get_learning_item(binding['target_id'])
    if (target and target.get('family') == 'word'
            and target.get('instruction_status', 'ready') == 'ready'
            and (target.get('title'), target.get('reading')) == (binding['form'], binding['reading'])):
        return deepcopy(target)
    return None


def enrich_word_lesson(word, sense_index, item):
    """Keep the reading/sense identity; never enroll or mutate learner records."""
    result = deepcopy(item)
    legacy = _legacy_identity_lesson(word, sense_index, result)
    if legacy is not None:
        return legacy
    from ._vocabulary_catalog import WORD_LESSONS
    curated = next((x for x in WORD_LESSONS if x["id"] == result["id"]), None)
    if result.get('instruction_status') == 'instruction_missing' and (
            curated is None or curated['title'] == result['title']):
        return result
    if curated is not None:
        # Only use notes pinned to the exact lexical evidence used at build time.
        sense = word["senses"][sense_index]
        fingerprint = hashlib.sha256(json.dumps(sense, ensure_ascii=False,
            sort_keys=True).encode()).hexdigest()
        if fingerprint == curated["dictionary_sense_sha256"]:
            # One dictionary entry may license different spellings for different
            # senses. Validate the lesson's spelling instead of assuming that
            # the dictionary's primary display form fits every sense.
            from .learning_content import learning_item_from_word
            candidate = learning_item_from_word({**word, 'form': curated['title'],
                'reading': curated['reading']}, sense_index, enrich=False)
            if (attested_word_pair(word, curated['title'], curated['reading'])
                    and candidate['instruction_status'] == 'ready'):
                return deepcopy(curated)
    if result.get("instruction_status") == "instruction_missing":
        return result
    sense = word["senses"][sense_index]
    pos = _part_of_speech(sense)
    form, reading = result["title"], result["reading"]
    parts = ["／".join(pos)] if pos else []
    parts.append(_CONTENT.get("INSTRUCTION_NOTES", {}).get('sense_binding', ""))
    if any("一段动词" == x for x in pos):
        parts.append(_CONTENT.get("INSTRUCTION_NOTES", {}).get('ichidan', ""))
    elif any("五段动词" == x for x in pos):
        parts.append(_CONTENT.get("INSTRUCTION_NOTES", {}).get('godan', ""))
    if "な形容词" in pos:
        parts.append(_CONTENT.get("INSTRUCTION_NOTES", {}).get('na_adjective', ""))
    if "い形容词" in pos:
        parts.append(_CONTENT.get("INSTRUCTION_NOTES", {}).get('i_adjective', ""))
    if "他动词" in pos:
        parts.append(_CONTENT.get("INSTRUCTION_NOTES", {}).get('transitive', ""))
    if "自动词" in pos:
        parts.append(_CONTENT.get("INSTRUCTION_NOTES", {}).get('intransitive', ""))
    if sense.get("reading_restrictions"):
        parts.append(_CONTENT.get("INSTRUCTION_NOTES", {}).get('reading_restrictions', ""))
    if sense.get("kanji_restrictions"):
        parts.append(_CONTENT.get("INSTRUCTION_NOTES", {}).get('kanji_restrictions', ""))
    parts = [part for part in parts if part]
    instruction = result["instruction"]
    instruction.update(rule="\n".join(parts),
        steps=[f"{form}（{reading}）", result["meaning"], *parts],
        example=form, translation=result["meaning"], prerequisite="", contrast="")
    result["part_of_speech_zh"] = pos
    result["material_status"] = "dictionary_sense_introduction; no_unverified_usage_example"
    result["revision"] = "dictionary-teaching-v2-" + hashlib.sha256(
        json.dumps({"base": item["revision"], "instruction": instruction},
                   ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    return result


def vocabulary_sources():
    return deepcopy(_CONTENT.get("SOURCE_RECORDS", []))
