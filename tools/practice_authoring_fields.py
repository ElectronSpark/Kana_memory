"""The author's optional fields of a new reviewed template, as the content pack stores them.

Design 11 section 18 (Q1-Q3). An authoring row of a NEW template may carry
  "opens_after_reviews": int 0..40  -> practice.opens_after_reviews (0 is not written),
  "scenario_id": str                -> practice.scenario_id (written as given),
  "glosses": [{"surface", "reading", "meaning", "lesson_id"?, "start"?}]
                                    -> contexts[template sha256]["background_glosses"] rows,
  "needs": [content id, ...] (1..8) -> first in practice.background_ids, before the derived ids,
  "answer_display": str, "answer_reading": str
                                    -> contexts[template sha256]["answer_display" / "answer_reading"]: the answer's
                                       standard spelling and (for an answer that is not all kana) its reading,
                                       shown on the result page (answer_form_fields checks them).
The two pacing keys are part of the practice and therefore of the template's fingerprint: they are set before
validate_practice and before the fingerprint, never added to an existing template. Glosses sit beside the
template, keyed by its fingerprint, so they may also be added to an existing template without changing it; so
does the answer's display form.
needs are real dependencies the dependency tool cannot detect (a construction it does not recognise); they are not
for timing (that is opens_after_reviews). They go into background_ids, which the App's gate unions with the derived
question_dependencies entry (learning_questions.template_requires), so a later re-derivation keeps them; being
part of the practice they are part of the fingerprint as well.

A gloss row is refused unless the App would show it to a learner who does not know the word: every condition of
question_support._practice_background_context is checked here, with the App's own normalisers, and a refusal names
the template key. The App's validator (learning_questions.validate_practice) stays the final judge of the practice;
the tools call it after apply_pacing. Used by tools/compile_reviewed_question_batch.py and the merge tool.
Pure functions: no file, database, clock or course text. The App modules are imported when a function needs them
(the calling tool puts app/src on sys.path).
"""
from __future__ import annotations

from copy import deepcopy
import re

AUTHORING_FIELDS = frozenset({"glosses", "opens_after_reviews", "scenario_id", "needs", "answer_display",
                              "answer_reading"})
# The answer's standard spelling and its reading, beside the template like the glosses (the App reads them with
# learning_questions.reviewed_answer_form, on result pages only); learning_questions.ANSWER_FORM_KEYS.
ANSWER_FORM_FIELDS = ("answer_display", "answer_reading")
MAX_NEEDS = 8
MAX_BACKGROUND = 40  # learning_questions.validate_practice: at most 40 background_ids
GLOSS_REQUIRED = ("surface", "reading", "meaning")
GLOSS_OPTIONAL = ("lesson_id", "start")
SCENARIO_NAMESPACE = "question_scenarios"
# A gloss reading is the word's kana, as the page's reading rule (design 11 section 7, rule 3) shows it.
_KANA_READING = re.compile(r"[ぁ-ゖゝゞァ-ヺーヽヾ]+\Z")
_KANA_CHAR = re.compile(r"[ぁ-ゖゝゞァ-ヺーヽヾ]")
# Flags of a question whose background-gloss channel the App closes (question_support._practice_background_context).
_CLOSED_FLAGS = ("ai_generated", "tests_reading", "tests_pitch", "requires_audio", "audio_only",
                 "has_hidden_transcript")


class AuthoringFieldError(ValueError):
    """An authoring field the content pack cannot store as given; the message names the template key."""


def _fail(key, message):
    raise AuthoringFieldError(f"{key}: {message}")


def split_fields(fields, *, key):
    """The optional authoring fields of one template: (pacing fields, gloss entries).

    fields: the mapping of the optional keys (absent ones are simply missing). Any other key is refused, so a
    misspelt field is never silently dropped. needs is read by needs_ids and the answer's display form by
    answer_form_fields (a caller that takes authoring fields must call them too, or refuse the fields).
    """
    if fields is None:
        return {}, []
    if not isinstance(fields, dict):
        _fail(key, "authoring fields must be an object")
    unknown = sorted(set(fields) - AUTHORING_FIELDS)
    if unknown:
        _fail(key, "unknown authoring field(s) " + ", ".join(unknown))
    glosses = fields.get("glosses", [])
    if not isinstance(glosses, list):
        _fail(key, "glosses must be a list")
    return {name: fields[name] for name in ("opens_after_reviews", "scenario_id") if name in fields}, glosses


def needs_ids(fields, *, key, target_id, known_items=None):
    """The author's needs of one template's authoring fields ([] when absent), checked.

    1..8 distinct non-empty content ids (an empty list is refused: leave the field out), never the template's
    target and, with a catalogue (known_items), each a known learning item of any family.
    """
    if not isinstance(fields, dict) or "needs" not in fields:
        return []
    needs = fields["needs"]
    if not isinstance(needs, list) or not 1 <= len(needs) <= MAX_NEEDS:
        _fail(key, f"needs must be a list of 1..{MAX_NEEDS} content ids (leave the field out when there are none)")
    if any(not isinstance(cid, str) or not cid.strip() for cid in needs):
        _fail(key, "every need must be a non-empty content id")
    if len(set(needs)) != len(needs):
        _fail(key, "needs repeats an id")
    for cid in needs:
        if cid == target_id:
            _fail(key, f"need {cid!r} is the template's own target")
        if known_items is not None and cid not in known_items:
            _fail(key, f"need {cid!r} is not a known learning item")
    return list(needs)


def background_ids(needs, derived, target_id):
    """(final practice.background_ids, derived ids dropped by the cap).

    The author's needs first, in their order, then the derived ids (in their order) that are not needs, never the
    target, at most 40 in all. Only derived ids are dropped at the cap; needs (at most 8) always stay.
    """
    needs = [cid for cid in needs if cid != target_id]
    rest = [cid for cid in dict.fromkeys(derived) if cid != target_id and cid not in set(needs)]
    room = MAX_BACKGROUND - len(needs)
    return needs + rest[:room], max(0, len(rest) - room)


def pacing_keys(fields, *, key):
    """The practice keys of the pacing fields, in canonical form.

    A release level of 0 is not written, so a level-0 template keeps exactly today's practice shape; a scenario id is
    written as given. Types and ranges follow learning_questions.validate_practice.
    """
    from kanamemory.learning_questions import _SCENARIO_ID, MAX_OPENS_AFTER_REVIEWS
    result = {}
    if "opens_after_reviews" in fields:
        level = fields["opens_after_reviews"]
        if type(level) is not int or not 0 <= level <= MAX_OPENS_AFTER_REVIEWS:
            _fail(key, f"opens_after_reviews must be an integer 0..{MAX_OPENS_AFTER_REVIEWS}, not {level!r}")
        if level:
            result["opens_after_reviews"] = level
    if "scenario_id" in fields:
        scenario = fields["scenario_id"]
        if not isinstance(scenario, str) or not _SCENARIO_ID.fullmatch(scenario):
            _fail(key, f"scenario_id must match ^[a-z][a-z0-9_]{{0,63}}$, not {scenario!r}")
        result["scenario_id"] = scenario
    return result


def apply_pacing(raw, fields, *, key):
    """A copy of the raw template with the pacing keys in its practice (before validation and fingerprint).

    A template whose practice already holds a pacing key is refused: the authoring field is the one channel, and two
    sources could disagree.
    """
    from kanamemory.learning_questions import PRACTICE_PACING_KEYS
    keys = pacing_keys(fields, key=key)
    result = deepcopy(raw)
    practice = result.get("practice")
    if not isinstance(practice, dict):
        _fail(key, "the template has no practice metadata")
    present = sorted(set(PRACTICE_PACING_KEYS) & set(practice))
    if present:
        _fail(key, "the template's practice already carries " + ", ".join(present) + "; give it as an authoring field")
    practice.update(keys)
    return result


def scenario_ids(payload):
    """The ids of a question_scenarios payload {"schema_version": 1, "scenarios": {id: {"title", "summary"}}}."""
    from kanamemory.learning_questions import _SCENARIO_ID
    if (not isinstance(payload, dict) or set(payload) != {"schema_version", "scenarios"}
            or type(payload["schema_version"]) is not int or payload["schema_version"] != 1
            or not isinstance(payload["scenarios"], dict) or not payload["scenarios"]):
        raise AuthoringFieldError("question_scenarios: expected {schema_version: 1, scenarios: {id: entry}}")
    for scenario, entry in payload["scenarios"].items():
        if not _SCENARIO_ID.fullmatch(scenario):
            raise AuthoringFieldError(f"question_scenarios: invalid scenario id {scenario!r}")
        if (not isinstance(entry, dict) or set(entry) != {"title", "summary"}
                or any(not isinstance(entry[name], str) or not entry[name].strip() for name in entry)):
            raise AuthoringFieldError(f"question_scenarios: {scenario} needs exactly a non-empty title and summary")
    return frozenset(payload["scenarios"])


def scenario_payload(document):
    """A question_scenarios payload, given plainly (the authors' working copy) or as a teaching namespace document."""
    from kanamemory.content_pack import NAMESPACE_FORMAT, decode_value
    if isinstance(document, dict) and document.get("format") == NAMESPACE_FORMAT:
        if document.get("name") != SCENARIO_NAMESPACE:
            raise AuthoringFieldError("the scenarios input is not the question_scenarios namespace")
        return decode_value(document.get("payload"))
    return document


def check_scenario(fields, catalogue, *, key):
    """A scenario id that is used must be in the catalogue (None: no catalogue given, nothing to check)."""
    scenario = fields.get("scenario_id")
    if catalogue is not None and scenario is not None and scenario not in catalogue:
        _fail(key, f"scenario_id {scenario!r} is not in question_scenarios")


def _occurrences(text, part):
    found, start = [], text.find(part)
    while start >= 0:
        found.append(start)
        start = text.find(part, start + 1)
    return found


def _overlaps(start, end, rows):
    return next((row for row in rows if row["start"] < end and start < row["end"]), None)


def _closed_reason(raw, kind, target_id):
    """Why the App never shows a background gloss on this template, or None."""
    from kanamemory.learning_questions import KIND_ABILITIES
    from kanamemory.multi_cloze import is_cloze_question
    from kanamemory.question_support import _BACKGROUND_KINDS, _MEANING_KINDS, _UNSAFE_ABILITIES
    if is_cloze_question(raw) or "multi_cloze" in raw:
        return "a multi-cloze template takes no glosses (its slot values and the stem cannot be told apart)"
    if kind not in (_MEANING_KINDS | _BACKGROUND_KINDS) or KIND_ABILITIES.get(kind) in _UNSAFE_ABILITIES:
        return f"the App shows no background glosses on {kind} questions"
    if isinstance(target_id, str) and target_id.startswith("pronunciation:"):
        return "the App shows no background glosses on pronunciation questions"
    flags = [name for name in _CLOSED_FLAGS if raw.get(name)]
    if flags:
        return "the App shows no background glosses on a template with " + ", ".join(flags)
    return None


def _protected(raw):
    """What a gloss may not reveal, as the App protects it: the answers and every target quote without a gap."""
    from kanamemory.question_support import _GAP
    practice = raw.get("practice") or {}
    forms = [raw.get("answer", ""), *raw.get("accepted_answers", ())]
    forms.extend(span.get("quote", "") for span in practice.get("target_spans", ())
                 if isinstance(span, dict) and isinstance(span.get("quote"), str) and not _GAP.search(span["quote"]))
    return [value for value in forms if isinstance(value, str)]


def _reveals(surface, meaning, reading, value):
    """question_support._practice_background_context's answer-leak test for one protected value."""
    from kanamemory.question_support import _KANJI, _hint_normal, _hint_sound
    normal, gloss, sound = _hint_normal(surface), _hint_normal(meaning), _hint_sound(reading or surface)
    other, other_sound = _hint_normal(value), _hint_sound(value)
    if not other:
        return False
    return bool(normal in other or other in normal or gloss in other or other in gloss
                or set(_KANJI.findall(normal)) & set(_KANJI.findall(other))
                or (sound and other_sound and (sound in other_sound or other_sound in sound)))


def _entry(entry, index, *, key):
    label = f"gloss {index + 1}"
    if not isinstance(entry, dict):
        _fail(key, f"{label} must be an object")
    unknown = sorted(set(entry) - set(GLOSS_REQUIRED) - set(GLOSS_OPTIONAL))
    missing = [name for name in GLOSS_REQUIRED
               if not isinstance(entry.get(name), str) or not entry[name].strip()]
    if unknown or missing:
        _fail(key, f"{label} is incomplete or has unknown keys (missing {missing}, unknown {unknown})")
    if "start" in entry and type(entry["start"]) is not int:
        _fail(key, f"{label}: start must be an integer character offset")
    if "lesson_id" in entry and (not isinstance(entry["lesson_id"], str) or not entry["lesson_id"].startswith("word:")):
        _fail(key, f"{label}: lesson_id must be a word lesson id")
    return label


def gloss_rows(raw, kind, target_id, entries, *, key, known_items=None, existing=()):
    """The stored background_glosses rows of one built raw template (its final practice included).

    entries: the author's gloss entries. known_items: the catalogue's content ids, when given every lesson_id must be
    one. existing: rows the template's context already holds (adding glosses to an existing template); a new row
    may not overlap them. Returns the new rows {start, end, surface, reading, meaning, target_id?} in prompt order.
    Every refusal raises AuthoringFieldError naming key; nothing is dropped silently.
    """
    from kanamemory.question_support import _GAP, _hint_normal
    if not entries:
        return []
    reason = _closed_reason(raw, kind, target_id)
    if reason:
        _fail(key, reason)
    prompt = raw.get("prompt")
    practice = raw.get("practice")
    if not isinstance(prompt, str) or not isinstance(practice, dict):
        _fail(key, "glosses need a template with a prompt and practice metadata")
    targets = practice.get("target_spans", [])
    options = [value for value in raw.get("choices", ()) if isinstance(value, str)]
    protected = _protected(raw)
    rows = []
    for index, entry in enumerate(entries):
        label = _entry(entry, index, key=key)
        surface, reading, meaning = entry["surface"], entry["reading"], entry["meaning"]
        places = _occurrences(prompt, surface)
        if "start" in entry:
            if entry["start"] not in places:
                _fail(key, f"{label}: {surface!r} does not stand in the prompt at {entry['start']}")
            start = entry["start"]
        elif len(places) != 1:
            _fail(key, f"{label}: {surface!r} occurs {len(places)} times in the prompt; give its start")
        else:
            start = places[0]
        end = start + len(surface)
        if _GAP.search(surface):
            _fail(key, f"{label}: {surface!r} contains a gap marker")
        if not _KANA_READING.fullmatch(reading):
            _fail(key, f"{label}: reading {reading!r} must be kana")
        normal = _hint_normal(surface)
        if not normal or len(meaning) > 80 or _hint_normal(meaning) in {"", normal}:
            _fail(key, f"{label}: the meaning must be 1..80 characters and differ from the written form")
        lesson = entry.get("lesson_id")
        if lesson is not None and lesson == target_id:
            _fail(key, f"{label}: lesson_id is the template's own target")
        if lesson is not None and known_items is not None and lesson not in known_items:
            _fail(key, f"{label}: lesson_id {lesson!r} is not a known learning item")
        for span in targets:
            if span["start"] < end and start < span["end"]:
                _fail(key, f"{label}: {surface!r} overlaps the target span {span['quote']!r}")
        clash = _overlaps(start, end, [*existing, *rows])
        if clash is not None:
            _fail(key, f"{label}: {surface!r} overlaps the gloss of {clash.get('surface')!r}")
        for value in options:
            if normal in _hint_normal(value):
                _fail(key, f"{label}: {surface!r} also stands in the option {value!r}")
        for value in protected:
            if _reveals(surface, meaning, reading, value):
                _fail(key, f"{label}: {surface!r} or its meaning would reveal the answer or target {value!r}")
        row = {"start": start, "end": end, "surface": surface, "reading": reading, "meaning": meaning}
        if lesson is not None:
            row["target_id"] = lesson
        rows.append(row)
    return sorted(rows, key=lambda row: row["start"])


def context_row(rows, existing=(), form=None):
    """The stored context of one template: existing and new gloss rows in prompt order, then the answer's display
    form (answer_form_fields) when there is one; without it the row is exactly the glosses row of before."""
    row = {"background_glosses": sorted([*deepcopy(list(existing)), *deepcopy(rows)], key=lambda row: row["start"])}
    row.update((name, form[name]) for name in ANSWER_FORM_FIELDS if form and name in form)
    return row


def _reading_chars_ok(text):
    """Kana (and the long mark) with the punctuation and spaces of the spelling, at least one kana, nothing else."""
    import unicodedata
    if not _KANA_CHAR.search(text):
        return False
    return all(_KANA_CHAR.fullmatch(char) or unicodedata.category(char)[0] in "PZ" for char in text)


def _aligned(display, reading):
    """Whether the App can put the reading over the display's kanji words (result_readings.word_spans lines it up).

    word_spans falls back to the whole reading over the whole text when the kana between the kanji do not match; a
    display that is one run of kanji always lines up. A display without kanji must sound like the reading.
    """
    from kanamemory.result_readings import _KANJI_RUN, word_spans
    from kanamemory.word_sound_questions import normal_reading
    if not _KANJI_RUN.search(display):
        fold = lambda text: "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c  # noqa: E731
                                    for c in normal_reading(text))
        return fold(display) == fold(reading)
    spans = word_spans(display, reading)
    return bool(_KANJI_RUN.fullmatch(display)) or spans != [(0, len(display), normal_reading(reading))]


def answer_form_fields(raw, kind, fields, *, key, target_id=None):
    """{answer_display[, answer_reading]} of one template's authoring fields ({} when absent), checked.

    The App trusts what it reads (answer_furigana.answer_form), so every condition is checked here: both are
    non-empty strings without surrounding space; a reading needs a display; the reading is kana (with the
    spelling's punctuation); the display differs from the answer; the answer is Japanese (the App's own check,
    result_readings.answers_in_japanese: a meaning keeps no display); an all-kana answer is the reading of the
    display (a reading, when given, must be that answer); any other answer needs the reading, which the display
    must line up with (result_readings.word_spans). A multi-cloze template shows its slot label, never a display.
    """
    if not isinstance(fields, dict) or not any(name in fields for name in ANSWER_FORM_FIELDS):
        return {}
    form = {}
    for name in ANSWER_FORM_FIELDS:
        if name in fields:
            value = fields[name]
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                _fail(key, f"{name} must be a non-empty string without surrounding space")
            form[name] = value
    if "answer_display" not in form:
        _fail(key, "answer_reading is the reading of answer_display; give both")
    from kanamemory.learning_questions import KIND_ABILITIES
    from kanamemory.multi_cloze import is_cloze_question
    from kanamemory.result_readings import _KANJI_RUN, answers_in_japanese
    from kanamemory.word_sound_questions import normal_reading
    display, reading = form["answer_display"], form.get("answer_reading")
    answer = raw.get("answer") if isinstance(raw, dict) else None
    if is_cloze_question(raw) or "multi_cloze" in raw:
        _fail(key, "a multi-cloze template shows its slot answers; it takes no answer_display")
    if not isinstance(answer, str) or not answer.strip():
        _fail(key, "the template has no answer")
    question = {"answer": answer, "kind": kind, "ability": KIND_ABILITIES.get(kind), "target_id": target_id}
    if not answers_in_japanese(question):
        _fail(key, f"the answer {answer!r} is not Japanese for {kind} (a meaning); the App shows no display for it")
    if display == answer.strip():
        _fail(key, "answer_display is the answer as written; leave it out")
    if reading is not None and not _reading_chars_ok(reading):
        _fail(key, f"answer_reading {reading!r} must be kana")
    text = answer.strip()
    all_kana = bool(re.search(r"[ぁ-ゖァ-ヺー]", text)) and not _KANJI_RUN.search(text)
    if all_kana:
        if reading is not None and normal_reading(reading) != normal_reading(text):
            _fail(key, f"the answer {text!r} is all kana and is the reading; answer_reading {reading!r} differs "
                       "(leave it out)")
        if not _aligned(display, text):
            _fail(key, f"answer_display {display!r} does not line up with the answer {text!r}")
    else:
        if reading is None:
            _fail(key, f"the answer {text!r} is not all kana: give answer_reading, the reading of answer_display")
        if not _aligned(display, reading):
            _fail(key, f"answer_display {display!r} does not line up with answer_reading {reading!r}")
    return form
