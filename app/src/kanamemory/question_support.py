"""Reviewed, presentation-only support for public Japanese question prompts.

The public prompt is the sole text source. Answers, options, feedback and the
mandatory stimulus of a listening question never enter this module's output.
``reviewed`` is a local authoring/validation decision, not an LLM confidence flag.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256
from functools import lru_cache
from math import isfinite
import re
import unicodedata

from .pitch_accent import morae


PROMPT_DISPLAY_SETTING = "prompt_display_v1"
_MEANING_KINDS = frozenset({
    "vocab_meaning", "vocab_zh_ja_contrast", "grammar_meaning",
    "reading_comprehension",
})
_BACKGROUND_KINDS = frozenset({
    "vocab_usage", "grammar_cloze", "grammar_transform", "grammar_reorder",
    "targeted_correction", "controlled_sentence_production",
})
_UNSAFE_ABILITIES = frozenset({
    "reading", "listening", "spelling", "handwriting", "pitch", "pronunciation",
    "K识读", "K拼写", "K听辨", "K手写", "V读法", "V听辨理解", "P发音训练",
})
_GAP = re.compile(r"[_＿□■◯○]|[（(\[【]\s*[）)\]】]|（[①-⑥1-6]）|［[1-6]］|\{[^}]*\}|<[^>]*>|\.\.\.|…")
_PLAIN_PROMPT_UNIT = re.compile(r"（(?:[^\S\r\n]*|[①-⑥1-6])）|［[1-6]］|\([^\S\r\n]*\)|\[[^\S\r\n]*\]|【[^\S\r\n]*】|[\s\S]")
_KANA = re.compile(r"[ぁ-ゖァ-ヶー]+\Z")
_POINT_TO_DP = 96 / 72
_BACKGROUND_SUPPORT_VERSION = 2
_KANJI = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff々〆]")


def _protected_answer_forms(question):
    forms = [question.get('answer', ''), *question.get('accepted_answers', ())]
    if question.get('input_mode') == 'multi_cloze':
        from .multi_cloze import specification
        forms.extend(value for row in specification(question)['accepted_combinations'] for value in row)
    return forms


def clean_prompt_preferences(value):
    """Keep both independent switches enabled unless an actual bool overrides."""
    settings = value if isinstance(value, Mapping) else {}
    return {key: settings[key] if type(settings.get(key)) is bool else True
            for key in ("furigana", "number")}


def _hint_normal(value):
    # Normalization is used only to reject an answer-bearing hint, never to
    # change the visible question or its assessment/accepted-answer policy.
    value = unicodedata.normalize("NFKC", value) if isinstance(value, str) else ""
    return "".join(c for c in value if not c.isspace()
                   and unicodedata.category(c) not in {"Cf", "Cc", "Zs"})


def _hint_sound(value):
    value = _hint_normal(value)
    if not _KANA.fullmatch(value):
        return ""
    value = "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in value)
    return value.translate(str.maketrans({"ぢ": "じ", "づ": "ず", "を": "お", "ゐ": "い", "ゑ": "え"}))


def _background_span_safe(span, protected):
    """Exclude target roots/readings, including normalized and homophone forms."""
    surface, reading = span.get("surface", ""), span.get("reading", "")
    if (not isinstance(surface, str) or not isinstance(reading, str)
            or not _KANJI.search(surface) or _GAP.search(surface) or not _KANA.fullmatch(reading)):
        return False
    normal, sound = _hint_normal(surface), _hint_sound(reading)
    characters = set(_KANJI.findall(normal))
    for value in protected:
        other = _hint_normal(value)
        if not other:
            continue
        # Shared kanji are deliberately conservative: a transformed verb can
        # retain its lexical root without retaining its complete answer form.
        if normal in other or other in normal or characters.intersection(_KANJI.findall(other)):
            return False
        other_sound = _hint_sound(other)
        if other_sound and (sound in other_sound or other_sound in sound):
            return False
    return True


def background_support(prompt, target, raw, kind, *, explicit=(), bundled=False, guided=False):
    """Authored non-target readings for application tasks, without speech.

    Full sentence enrichment is limited to exact bundled cloze/usage prompts.
    Other kinds require explicitly authored public readings. No answer or
    option is sent to the reading database; they only veto candidate spans.
    """
    if (kind not in _BACKGROUND_KINDS or guided or target.get("domain") == "pronunciation"
            or target.get("ai_generated") or raw.get("tests_reading") or raw.get("tests_pitch")
            or raw.get("audio_only") or raw.get("has_hidden_transcript")):
        return None
    candidates = list(explicit)
    if not candidates and bundled:
        from .sentence_annotations import sentence_annotation_plan
        plan = sentence_annotation_plan(prompt)
        if (plan.get('text') == prompt and plan.get('status') == 'precomputed'
                and (kind in {'grammar_cloze', 'vocab_usage'} or plan.get('context_projection_sha256'))):
            candidates = plan.get("segments", [])
    protected = _protected_answer_forms(raw)
    if target.get("family") == "word":
        protected.extend((target.get("title", ""), target.get("reading", "")))
    valid = _aligned_segments({"prompt": prompt}, {"segments": candidates})
    approved = [dict(span, role="background", reading_safe=True, accent_safe=True)
                for span in valid if _source(span.get("reading_source_id"))
                and _background_span_safe(span, protected)]
    if not approved:
        return None
    segments, offset = [], 0
    for span in approved:
        if span["start"] > offset:
            segments.append({"start": offset, "end": span["start"], "surface": prompt[offset:span["start"]]})
        segments.append(span)
        offset = span["end"]
    if offset < len(prompt):
        segments.append({"start": offset, "end": len(prompt), "surface": prompt[offset:]})
    return {"reviewed": True, "content_revision": target["revision"], "prompt": prompt,
            "reading_safe": True, "accent_safe": True, "audio_safe": False,
            "has_gap": bool(_GAP.search(prompt)), "reading_ambiguity": False,
            "segments": segments, "background_support_version": _BACKGROUND_SUPPORT_VERSION}


def _local_question_identity(question, current):
    """Accept a template ID or the controller's per-ability review identity.

    LearningUIMixin gives each attempt a new ID; LearningStore then keeps both
    ID fields equal. This shape is not proof that imported material is safe:
    callers still compare its body against the reviewed local template, and
    derive display hints from local content rather than imported hint fields.
    """
    identity = question.get("id")
    if (not isinstance(identity, str) or question.get("question_id") != identity
            or question.get("ability") != current.get("ability")):
        return False
    if identity == current["id"] == current["question_id"]:
        return True
    prefix = f"{current['target_id']}:{current['ability']}:review:"
    return identity.startswith(prefix) and bool(re.fullmatch(r"0|[1-9][0-9]*", identity[len(prefix):]))


def _legacy_learning_question_display(question):
    """Read-only compatibility for exact old bundled snapshots, not imports.

    The original answer, ID, queue and draft are never rewritten. Only local
    public annotations and an exact legacy non-target instruction may be added.
    """
    if (not isinstance(question, Mapping) or question.get("kind") not in _BACKGROUND_KINDS
            or question.get("background_support_version") == _BACKGROUND_SUPPORT_VERSION
            or question.get("ai_generated") or question.get("guided")
            or question.get("audio_only") or question.get("has_hidden_transcript")
            or question.get("tests_reading") or question.get("tests_pitch")
            or question.get("domain") == "pronunciation"):
        return question
    target_id = question.get("target_id")
    if not isinstance(target_id, str) or question.get("content_id") != target_id:
        return question
    support = question.get("support")
    if (not isinstance(support, Mapping) or support.get("reviewed") is not True
            or support.get("prompt") != question.get("prompt")
            or support.get("content_revision") != _revision(question)
            or support.get('audio_safe') is not False
            or support.get("reading_ambiguity") is True or support.get("may_reveal_answer") is True):
        return question
    empty_legacy = not support.get('segments') and support.get('reading_safe') is False and support.get('accent_safe') is False
    previous_background = (question.get('background_support_version') == 1
                           and support.get('reading_safe') is True and support.get('accent_safe') is True
                           and all(s.get('role') == 'background' and s.get('reading_safe') is True
                                   and s.get('accent_safe') is True for s in support.get('segments', []) if s.get('reading')))
    if not (empty_legacy or previous_background):
        return question
    from .learning_content import get_learning_item
    from .learning_questions import build_learning_question
    item = get_learning_item(target_id)
    variant = question.get("variant")
    if (item is None or type(variant) is not int or variant < 0
            or _revision(question) != item["revision"] or question.get("family") != item["family"]):
        return question
    try:
        current = build_learning_question(item, question["kind"], variant=variant)
    except (TypeError, ValueError, KeyError):
        return question
    if not _local_question_identity(question, current):
        return question
    from .question_contexts import legacy_display_rules
    for rule in legacy_display_rules():
        variants = rule.get("variants", ())
        if not variants:
            continue
        expected = variants[variant % len(variants)]
        if (target_id == rule["target_id"] and question["kind"] == rule["kind"]
                and (question.get("prompt"), question.get("answer")) in rule["prompt_answer_pairs"]
                and question.get("accepted_answers") == expected["accepted_answers"]
                and question.get("task") == expected["task"]
                and question.get("input_mode") == "text" and question.get("response") == "text"
                and not question.get("choices")):
            return {**question, "context_hint": rule["context_hint"]}
    # Changes to an answer or distractor can change what information is being
    # tested. Only the exact unchanged local question receives new support.
    fields = ('prompt', 'answer', 'accepted_answers', 'choices', 'input_mode', 'task', 'practice', 'multi_cloze')
    if (not all(question.get(key) == current.get(key) for key in fields)
            or not current.get("background_support_version")):
        return question
    return {**question, "support": deepcopy(current["support"]), "spans": deepcopy(current["spans"]),
            "background_support_version": _BACKGROUND_SUPPORT_VERSION}


@lru_cache(maxsize=1)
def _background_word_senses():
    """Exact curated lesson senses, not arbitrary first dictionary meanings."""
    from .learning_content import learning_items
    indexed = {}
    for item in learning_items("word"):
        key = (item.get("title"), item.get("reading"))
        if all(isinstance(value, str) and value for value in key) and item.get("meaning"):
            indexed.setdefault(key, []).append((item["id"], item["meaning"]))
    result = {key: values[0] for key, values in indexed.items() if len(values) == 1}
    from .question_contexts import supplemental_background_senses
    for key, sense in supplemental_background_senses():
        result.setdefault(key, sense)
    return result


def _background_context(question):
    if (question.get("kind") not in {"grammar_meaning", "grammar_cloze"}
            or question.get("family") != "grammar" or question.get("domain") == "pronunciation"
            or question.get("ai_generated") or question.get("guided")
            or question.get("tests_reading") or question.get("tests_pitch")
            or question.get("audio_only") or question.get("has_hidden_transcript")):
        return {}
    from .learning_content import get_learning_item
    from .learning_questions import build_learning_question
    from .sentence_annotations import sentence_annotation_plan
    identity, variant = question.get("target_id"), question.get("variant")
    if (not isinstance(identity, str) or question.get("content_id") != identity
            or type(variant) is not int or variant < 0 or _reviewed(question) is None):
        return {}
    item = get_learning_item(identity)
    if not item or _revision(question) != item["revision"]:
        return {}
    try:
        current = build_learning_question(item, question["kind"], variant=variant)
    except (TypeError, ValueError, KeyError):
        return {}
    if (not _local_question_identity(question, current)
            or not all(question.get(key) == current.get(key) for key in (
                "prompt", "answer", "accepted_answers", "alternative_answers", "choices", "task", "input_mode"))):
        return {}
    from .question_contexts import reviewed_question_context
    context = reviewed_question_context(identity, question["prompt"])
    plan = sentence_annotation_plan(question["prompt"])
    protected = [*_protected_answer_forms(question), item['title']]
    senses, results, seen = _background_word_senses(), [], set()
    spans = (_aligned_segments(question, {"segments": plan.get("segments")})
             if plan.get("status") == "precomputed" and plan.get("text") == question["prompt"] else ())
    for span in spans:
        if (span.get("unknown_token") or not _source(span.get("reading_source_id"))
                or not _background_span_safe(span, protected)):
            continue
        key = (span["surface"], span.get("reading"))
        sense = senses.get(key)
        if sense is None or key in seen:
            continue
        target_id, meaning = sense
        # A cognate repeated verbatim adds clutter, and an answer-equivalent
        # gloss would turn a grammar meaning question into a disclosed answer.
        normalized = _hint_normal(meaning)
        if (not normalized or normalized == _hint_normal(span["surface"])
                or len(meaning) > 80
                or any(normalized in _hint_normal(value) or _hint_normal(value) in normalized
                       for value in protected if _hint_normal(value))):
            continue
        results.append({"start": span["start"], "end": span["end"], "surface": span["surface"],
                        "reading": span["reading"], "meaning": meaning, "target_id": target_id})
        seen.add(key)
    for part in context["background_glosses"]:
        # Authored kana words (e.g. アレルギー) may supply lexical context too.
        # This path is exact-prompt-only; it never guesses a dictionary sense.
        normal, meaning = _hint_normal(part["surface"]), _hint_normal(part["meaning"])
        reading = next((s.get("reading") for s in spans if s["start"] == part["start"]
                        and s["end"] == part["end"]), "")
        sound = _hint_sound(reading or part["surface"])
        if (not normal or not meaning or normal == meaning or _GAP.search(part["surface"])
                or any(old["start"] < part["end"] and part["start"] < old["end"] for old in results)):
            continue
        if any(normal in _hint_normal(value) or _hint_normal(value) in normal
               or meaning in _hint_normal(value) or _hint_normal(value) in meaning
               or set(_KANJI.findall(normal)).intersection(_KANJI.findall(_hint_normal(value)))
               or (sound and _hint_sound(value) and
                   (sound in _hint_sound(value) or _hint_sound(value) in sound))
               for value in protected if _hint_normal(value)):
            continue
        results.append({**part, "reading": reading})
    return {"background_glosses": sorted(results, key=lambda part: part["start"]),
            "context_hint": context["context_hint"]}


def _practice_background_context(question):
    """Only exact archived practice may offer reviewed, non-target glosses.

    The registry verifies the task body. This final display gate independently
    excludes target spans, answers, homophone readings and hidden transcripts.
    An imported question cannot authorize its own hint by copying a flag.
    """
    if (not isinstance(question, Mapping) or not question.get("practice_source")
            or question.get("kind") not in (_MEANING_KINDS | _BACKGROUND_KINDS)
            or question.get("ability") in _UNSAFE_ABILITIES
            or question.get("ai_generated") or question.get("tests_reading")
            or question.get("tests_pitch") or question.get("requires_audio")
            or question.get("audio_only") or question.get("has_hidden_transcript")
            or question.get("domain") == "pronunciation"):
        return {}
    from .learning_questions import reviewed_practice_context
    context = reviewed_practice_context(question)
    if not isinstance(context, Mapping) or not context.get("background_glosses"):
        return {}
    prompt = question.get("prompt", "")
    meta = question.get("practice") or {}
    if not isinstance(prompt, str) or not isinstance(meta, Mapping):
        return {}
    targets = meta.get("target_spans", [])
    if (not isinstance(targets, list) or any(not isinstance(span, Mapping)
            or type(span.get("start")) is not int or type(span.get("end")) is not int
            or not isinstance(span.get("quote"), str) for span in targets)):
        return {}
    protected = _protected_answer_forms(question)
    protected.extend(span.get("quote", "") for span in targets
                     if isinstance(span, Mapping) and not _GAP.search(span.get("quote", "")))
    results = []
    for part in context.get("background_glosses", ()):
        if not isinstance(part, Mapping):
            continue
        start, end = part.get("start"), part.get("end")
        surface, meaning = part.get("surface"), part.get("meaning")
        if (type(start) is not int or type(end) is not int
                or not 0 <= start < end <= len(prompt)
                or not isinstance(surface, str) or prompt[start:end] != surface
                or not isinstance(meaning, str) or not meaning or len(meaning) > 80
                or part.get("target_id") == question.get("target_id")
                or _GAP.search(surface)
                or any(s.get("start", 0) < end and start < s.get("end", 0) for s in targets)
                or any(s["start"] < end and start < s["end"] for s in results)):
            continue
        normal, gloss = _hint_normal(surface), _hint_normal(meaning)
        if not normal or gloss == normal:
            continue
        sound = _hint_sound(part.get("reading") or surface)
        unsafe = False
        for value in protected:
            other, other_sound = _hint_normal(value), _hint_sound(value)
            if not other:
                continue
            if (normal in other or other in normal or gloss in other or other in gloss
                    or set(_KANJI.findall(normal)).intersection(_KANJI.findall(other))
                    or (sound and other_sound and (sound in other_sound or other_sound in sound))):
                unsafe = True
                break
        if not unsafe:
            results.append(dict(part))
    return {"background_glosses": sorted(results, key=lambda part: part["start"])} if results else {}


def learning_question_display(question):
    """Add safe display support to a copy while retaining the original test."""
    # These are derived display fields, never trusted import/template inputs.
    # Rebuild both from exact local rules (including the legacy adapter below).
    if isinstance(question, Mapping) and ("background_glosses" in question or "context_hint" in question):
        question = {key: value for key, value in question.items()
                    if key not in {"background_glosses", "context_hint"}}
    display = _legacy_learning_question_display(question)
    if not isinstance(display, Mapping):
        return display
    context = (_practice_background_context(display) if display.get("practice_source")
               else _background_context(display))
    if context.get("background_glosses") or context.get("context_hint"):
        return {**display, **{key: value for key, value in context.items() if value}}
    if "background_glosses" in display:
        # Imported display metadata cannot promote itself into trusted hints.
        return {key: value for key, value in display.items() if key != "background_glosses"}
    return display


def _revision(question):
    return question.get("content_revision", question.get("revision"))


def _reviewed(question):
    if not isinstance(question, Mapping):
        return None
    text, support = question.get("prompt"), question.get("support")
    identity = question.get("question_id", question.get("id"))
    revision = _revision(question)
    if (not isinstance(text, str) or not text.strip() or len(text) > 10000
            or not isinstance(identity, str) or not identity
            or type(revision) not in (str, int) or revision == ""
            or not isinstance(support, Mapping) or support.get("reviewed") is not True
            or support.get("content_revision") != revision or support.get("prompt") != text
            or question.get("prompt_visible") is False or question.get("audio_only") is True
            or any(unicodedata.category(c) in {"Cc", "Cf"} and c not in "\n\t" for c in text)):
        return None
    # Reading rules never gain answer-bearing support from forged safe flags.
    if question.get("domain") == "pronunciation" or question.get("kind") in {
            "pronunciation_rule", "pronunciation_application"}:
        return None
    # Use kind as a hard upper bound, even if imported metadata claims safety.
    if question.get("kind") not in _MEANING_KINDS | _BACKGROUND_KINDS:
        return None
    abilities = question.get("target_abilities", ())
    if isinstance(abilities, str):
        abilities = (abilities,)
    if not isinstance(abilities, (list, tuple)):
        return None
    tested = (*abilities, question.get("ability"), question.get("primary_dimension"))
    if any(a is not None and (not isinstance(a, str) or a in _UNSAFE_ABILITIES) for a in tested):
        return None
    if (question.get("tests_reading") is True or question.get("tests_pitch") is True
            or question.get("has_hidden_transcript") is True):
        return None
    if support.get("reading_ambiguity") is True or support.get("may_reveal_answer") is True:
        return None
    return support


def prompt_audio_text(question):
    """Return only eligible public text, or None. Never substitute an answer.

    Call again on the active question when the speaker is pressed. The caller
    owns cancellation and question identity checks across async playback.
    Listening questions keep their separately defined mandatory stimulus.
    """
    return _display_prompt_audio_text(learning_question_display(question))


def _display_prompt_audio_text(question):
    """Use an already reviewed display copy without rebuilding it twice."""
    support = _reviewed(question)
    if support is None or question.get("kind") not in _MEANING_KINDS:
        return None
    response = question.get("response", question.get("input_mode"))
    if (response != "choice" or support.get("audio_safe") is not True
            or support.get("has_gap") is not False or _GAP.search(question["prompt"])
            or question.get("has_gap") is True or question.get("tests_reading") is True
            or question.get("tests_pitch") is True):
        return None
    return question["prompt"]


def _aligned_segments(question, support):
    """Require an ordered, non-overlapping public text alignment.

    Either all ranges are explicit, or the complete list partitions the prompt.
    A single malformed span invalidates the annotations rather than printing
    metadata that may contain a completed answer.
    """
    text, items = question["prompt"], support.get("segments")
    if not isinstance(items, (list, tuple)) or len(items) > 256:
        return ()
    if not items:
        return ()
    explicit = all(isinstance(s, Mapping) and "start" in s and "end" in s for s in items)
    implicit = all(isinstance(s, Mapping) and "start" not in s and "end" not in s for s in items)
    if not explicit and not implicit:
        return ()
    result, offset = [], 0
    for segment in items:
        surface = segment.get("surface")
        if not isinstance(surface, str) or not surface:
            return ()
        start, end = ((segment.get("start"), segment.get("end")) if explicit
                      else (offset, offset + len(surface)))
        if (type(start) is not int or type(end) is not int or start < offset
                or not 0 <= start < end <= len(text) or text[start:end] != surface):
            return ()
        result.append({**segment, "start": start, "end": end, "surface": text[start:end]})
        offset = end
    if implicit and offset != len(text):
        return ()
    return tuple(result)


def _source(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 256


def _sentence_spans(question, support, spans):
    """Only enrich a public meaning prompt explicitly approved for readings.

    Readings marked unsafe never reach the sidecar. The lookup takes only the
    already visible prompt, never an answer, choice or hidden transcript.
    """
    if (support is None or question.get('kind') not in _MEANING_KINDS
            or support.get('reading_safe') is not True
            or question.get('ai_generated') is True or question.get('has_gap') is True
            or support.get('has_gap') is not False or _GAP.search(question['prompt'])
            or support.get('segments') and not spans):
        return spans
    from .sentence_annotations import sentence_annotation_plan
    plan = sentence_annotation_plan(question['prompt'])
    if not isinstance(plan, Mapping) or plan.get('text') != question['prompt']:
        return spans
    additions = _aligned_segments(question, {'segments': plan.get('segments')})
    if not additions:
        return spans
    merged = []
    for part in additions:
        if not part.get('reading'):
            continue
        segment = {**part, 'role': 'background',
                   'reading_safe': support.get('reading_safe') is True,
                   'accent_safe': support.get('accent_safe') is True}
        for old in spans:
            if old['start'] < part['end'] and part['start'] < old['end']:
                for key in ('reading_safe', 'accent_safe'):
                    if key in old:
                        segment[key] = segment[key] and old[key] is True
        exact = next((old for old in spans if old['start'] == part['start'] and old['end'] == part['end']
                      and old.get('reading') and _source(old.get('reading_source_id'))), None)
        if exact:
            # An authored contextual reading may disambiguate a homograph.
            # Do not retain an accent belonging to a different reading.
            if exact['reading'] != segment['reading']:
                segment.update(reading=exact['reading'], reading_source_id=exact['reading_source_id'],
                               accent_number=None, accent_source_id=None, accent_phrases=[],
                               accent_scope='word', accent_surface=None, accent_reading=None)
            for key in ('reading_safe', 'accent_safe'):
                segment[key] = segment[key] and exact.get(key) is True
            if exact.get('accent_number') is not None:
                segment.update(accent_phrases=[], accent_scope=exact.get('accent_scope', 'word'))
                for key in ('accent_number', 'accent_source_id', 'accent_reading', 'accent_surface', 'accent_scope'):
                    if key in exact:
                        segment[key] = exact[key]
            elif exact.get('accent_phrases'):
                for key in ('accent_number', 'accent_source_id', 'accent_reading', 'accent_surface',
                            'accent_scope', 'accent_phrases'):
                    segment[key] = deepcopy(exact.get(key))
        merged.append(segment)
    merged.extend(old for old in spans if old.get('reading') and not any(
        old['start'] < part['end'] and part['start'] < old['end'] for part in merged))
    return tuple(sorted(merged, key=lambda part: part['start']))


def prompt_support(question, preferences=None):
    """Produce plain public spans plus only independently approved annotations.

    No source/technical captions are added to the visible prompt. Provenance
    stays in the frozen question record for the separate details view.
    """
    from .sentence_annotations import validated_accent_phrases
    settings = clean_prompt_preferences(preferences)
    question = question if isinstance(question, Mapping) else {}
    question = learning_question_display(question)
    text = question.get("prompt") if isinstance(question.get("prompt"), str) else ""
    result = {"prompt": text, "segments": [], "audio_text": _display_prompt_audio_text(question),
              "question_id": question.get("question_id", question.get("id")),
              "content_revision": _revision(question), "furigana_visible": False,
              "accent_visible": False}
    result["payload_hash"] = sha256(str((result["question_id"], result["content_revision"], text)).encode("utf-8")).hexdigest()
    support = _reviewed(question)
    spans = _aligned_segments(question, support) if support is not None else ()
    spans = _sentence_spans(question, support, spans)
    approved = []
    for span in spans:
        # For cloze/transform/reorder/correction, only explicit unrelated spans
        # are eligible. Never annotate an empty target or normalize an error.
        background_only = (question.get("kind") in _BACKGROUND_KINDS or question.get("has_gap") is True
                           or support.get("has_gap") is True or bool(_GAP.search(text)))
        if background_only and span.get("role") != "background":
            continue
        if _GAP.search(span["surface"]):
            continue
        reading = span.get("reading")
        valid_reading = (isinstance(reading, str) and bool(_KANA.fullmatch(reading))
                         and _source(span.get("reading_source_id")))
        safe_reading = (support.get("reading_safe") is True and span.get("reading_safe") is True)
        visible_reading = (reading if settings["furigana"] and valid_reading and safe_reading
                           and reading != span["surface"] else None)
        number = span.get("accent_number")
        # Unknown, booleans, negative and out-of-range values are not type 0.
        valid_accent = (valid_reading and type(number) is int
                        and 0 <= number <= len(morae(reading)) and len(morae(reading)) > 0
                        and _source(span.get("accent_source_id"))
                        and span.get("accent_surface") == span["surface"]
                        and span.get("accent_reading") == reading
                        and span.get("accent_scope", "word") in {"word", "inflected_unit"})
        safe_accent = support.get("accent_safe") is True and span.get("accent_safe") is True
        visible_number = number if settings["number"] and valid_accent and safe_accent else None
        phrases = validated_accent_phrases(span) if valid_reading else []
        visible_phrases = ([{"reading": p["reading"] if visible_reading is not None else None,
                             "accent_number": p["accent_number"]} for p in phrases]
                           if settings["number"] and safe_accent else [])
        if visible_reading is not None or visible_number is not None or visible_phrases:
            approved.append({"start": span["start"], "end": span["end"],
                             "surface": span["surface"], "reading": visible_reading,
                             "accent_number": visible_number,
                             **({"accent_phrases": visible_phrases} if visible_phrases else {})})
    offset = 0
    for span in approved:
        if span["start"] > offset:
            result["segments"].append({"surface": text[offset:span["start"]], "reading": None, "accent_number": None})
        result["segments"].append(span)
        offset = span["end"]
    if offset < len(text):
        result["segments"].append({"surface": text[offset:], "reading": None, "accent_number": None})
    result["furigana_visible"] = any(s["reading"] is not None for s in result["segments"])
    result["accent_visible"] = any(s["accent_number"] is not None or s.get("accent_phrases")
                                   for s in result["segments"])
    return result


def accent_caption(number):
    if type(number) is not int or number < 0:
        return ""
    return "⓪" if number == 0 else chr(0x2460 + number - 1) if number <= 20 else f"[{number}]"


def _annotation_text(span):
    """Format an already approved display plan, retaining accent boundaries."""
    phrases = span.get("accent_phrases")
    if isinstance(phrases, (list, tuple)) and 2 <= len(phrases) <= 16:
        if all(isinstance(p, Mapping) and type(p.get("accent_number")) is int
               and p["accent_number"] >= 0
               and (p.get("reading") is None or isinstance(p["reading"], str)
                    and _KANA.fullmatch(p["reading"])) for p in phrases):
            return "｜".join((p.get("reading") or "") + accent_caption(p["accent_number"]) for p in phrases)
    return (span.get("reading") or "") + accent_caption(span.get("accent_number"))


def _text_width(text, size, scale):
    # Circled accent digits are East Asian Ambiguous, but Android's Japanese
    # fallback font renders them at a full cell. Treating them as Latin-width
    # caused only the number to wrap underneath an otherwise fitting reading.
    units = sum(1 if c == "⓪" or unicodedata.east_asian_width(c) in "WFA" else .6 for c in text)
    return max(1, units * size * _POINT_TO_DP * scale)


def _prompt_lines(segments, available, size, scale):
    """Wrap complete words and blank markers; other plain text may wrap by character."""
    available = max(1, available)
    annotation_size = max(11, size * .55)
    cells = []
    for span in segments:
        annotation = _annotation_text(span)
        # Keep a visible answer slot intact at a row boundary. Splitting its
        # brackets from the blank makes enlarged cloze questions ambiguous.
        surfaces = (span["surface"],) if annotation else _PLAIN_PROMPT_UNIT.findall(span["surface"])
        for surface in surfaces:
            # Native font fallback/hinting rounds per glyph, so reserve more
            # than one pixel at enlarged system sizes. This is internal text
            # allowance, not reduced type size or fixed clipping height.
            allowance = max(4, 4 * scale) if annotation else 2
            width = min(available, max(_text_width(surface, size, scale),
                                      _text_width(annotation, annotation_size, scale) if annotation else 0) + allowance)
            cells.append({"surface": surface, "annotation": annotation, "width": width,
                          "annotation_size": annotation_size})
    lines, current, occupied = [], [], 0
    for cell in cells:
        if cell["surface"] == "\n":
            if current:
                lines.append(current)
                current, occupied = [], 0
            continue
        if current and occupied + cell["width"] > available:
            if cell["surface"] in "。、，．！？!?」』）】" and len(current) > 1 and current[-1]["width"] + cell["width"] <= available:
                # Keep closing punctuation with its preceding cell instead of
                # creating a line containing only a full stop.
                previous = current.pop()
                lines.append(current)
                current, occupied = [previous], previous["width"]
            else:
                lines.append(current)
                current, occupied = [], 0
        current.append(cell)
        occupied += cell["width"]
    if current:
        lines.append(current)
    return lines


def learning_text_role(question, field="prompt"):
    """Choose typography from the exercise contract, never from Hanzi shapes.

    A meaning such as 学校 is Chinese in a recall prompt but Japanese in its
    answer. Existing immutable snapshots need no new metadata or migration.
    Unknown question families conservatively keep the instruction typeface.
    """
    kind = question.get("kind") if isinstance(question, Mapping) else None
    if kind in {"pronunciation_rule", "pronunciation_application"}:
        roles = question.get("text_roles", {})
        key = "answer" if field in {"answer", "choice", "input"} else field
        role = roles.get(key) if isinstance(roles, Mapping) else None
        return role if role in {"ui", "study"} else "ui"
    if field == "prompt":
        practice = question.get("practice") if isinstance(question, Mapping) else None
        if isinstance(practice, Mapping) and practice.get('task_family') == 'translation_wordbank':
            return 'ui'
        if (isinstance(practice, Mapping) and isinstance(practice.get("task_family"), str)
                and (kind, practice["task_family"]) in {
                ("vocab_orthography", "contextual_orthography"),
                ("vocab_recall", "word_form_recall"),
                ("grammar_transform", "grammar_transform")}):
            return "study"
        kinds = {"vocab_meaning", "vocab_reading", "vocab_usage", "vocab_zh_ja_contrast",
                 "grammar_meaning", "grammar_cloze", "reading_comprehension", "targeted_correction",
                 "vocab_audio_word", "vocab_reading_choice"}
        return "study" if kind in kinds else "ui"
    if field in {"answer", "choice", "input"}:
        kinds = {"vocab_reading", "vocab_recall", "vocab_orthography", "vocab_usage",
                 "vocab_audio_word", "vocab_reading_choice",
                 "grammar_cloze", "grammar_transform", "grammar_reorder",
                 "targeted_correction", "controlled_sentence_production"}
        return "study" if kind in kinds else "ui"
    return "ui"


def _plain_prompt_parts(question, prompt):
    """Separate known mixed legacy fields without guessing their language."""
    kind = question.get("kind") if isinstance(question, Mapping) else None
    if kind == "vocab_orthography" and " · " in prompt:
        reading, meaning = prompt.split(" · ", 1)
        return [(reading, "study"), (meaning, "ui")]
    if (kind == "vocab_meaning" and question.get("guided")
            and "\n" in prompt and prompt.endswith("\n" + question.get("answer", ""))):
        stimulus, meaning = prompt.rsplit("\n", 1)
        return [(stimulus, "study"), (meaning, "ui")]
    if kind == "grammar_cloze":
        if prompt.startswith("变化已发生，状态仍持续："):
            instruction, stimulus = prompt.split("：", 1)
            return [(instruction + "：", "ui"), (stimulus, "study")]
        if question.get("target_id") == "grammar:te_form:godan_mu":
            # The old stem exercise includes a Chinese verb-class gloss inside
            # the stimulus. Leave that composite in the instruction face.
            return [(prompt, "ui")]
    return [(prompt, learning_text_role(question))]


def build_prompt_block(app, question, preferences=None, *, size=20, available_width=None):
    """Render safe annotations, then emphasize only exact public target text."""
    from .question_presentation import question_presentation

    if 'prosody_cue' in question:
        from .prosody_cue_ui import cue_widget
        return cue_widget(question, width=available_width or 320)

    block = _build_prompt_block(app, question, preferences, size=size, available_width=available_width)
    presentation = question_presentation(question)
    fragment = block._feedback_fragment
    fragment["instruction"] = presentation["instruction"]
    spans = presentation["target_spans"]
    return _emphasize_prompt_block(block, question.get('prompt', ''), spans)


def _emphasize_prompt_block(block, prompt, spans, *, selected=False):
    from .example_highlight import configure_example_text, EMPHASIS_COLOR
    from .typography import configure_study_ranges

    fragment = block._feedback_fragment
    if not spans:
        return block
    if fragment.get("lines"):
        bodies = [cell.children[-1] for row in block.children for cell in row.children]
    else:
        bodies = list(block.children) if getattr(block, "children", ()) else [block]
    offset, aligned = 0, []
    for body in bodies:
        text = str(body.text)
        start = prompt.find(text, offset)
        if start < 0 or prompt[offset:start].strip(" \n\r\t·"):
            return block
        end = start + len(text)
        local = [{"start": max(start, span["start"]) - start,
                  "end": min(end, span["end"]) - start,
                  "quote": prompt[max(start, span["start"]):min(end, span["end"])]}
                 for span in spans if max(start, span["start"]) < min(end, span["end"])]
        aligned.append((body, local))
        offset = end
    if prompt[offset:].strip():
        return block
    for body, local in aligned:
        if local:
            configure_study_ranges(body, [(span["start"], span["end"]) for span in local],
                                   font_family='sans-serif' if selected else 'serif')
            configure_example_text(body, local)
            if len(local) == 1 and local[0]["start"] == 0 and local[0]["end"] == len(str(body.text)):
                if getattr(body, "style", None) is not None:
                    body.style.font_weight = "bold"
                    body.style.color = EMPHASIS_COLOR
    if selected:
        block._cloze_selected_spans = deepcopy(spans)
        fragment['selected_spans'] = deepcopy(spans)
    else:
        block._practice_target_spans = deepcopy(spans)
        fragment["target_spans"] = deepcopy(spans)
    return block


def render_prompt_plan(app, question, plan, *, size=20, available_width=None):
    """Render an already safety-filtered plan, including selected gap text."""
    block = _build_prompt_block(app, question, size=size, available_width=available_width, plan=plan)
    return _emphasize_prompt_block(block, plan['prompt'], plan.get('selected_spans', ()), selected=True)


def _build_prompt_block(app, question, preferences=None, *, size=20, available_width=None, plan=None):
    """Native wrapping word cells: annotation above body, no hidden answer links.

    All sizes can grow with the system font setting, and no fixed height clips
    the two text lines. Very long words retain their own wrapping cell rather
    than shrinking the user's requested type size. Works on both Toga backends.
    """
    from .ui_components import label, col, row, INK, MUTED
    from .text_layout import font_scale

    plan = prompt_support(question, preferences) if plan is None else plan
    if not plan["furigana_visible"] and not plan["accent_visible"]:
        parts = _plain_prompt_parts(question, plan["prompt"])
        labels = [label(text, size=size, text_role=role, wrap=False) for text, role in parts]
        result = labels[0] if len(labels) == 1 else col(*labels)
        result._prompt_support = plan
        result._feedback_fragment = {"kind": "question_prompt", "question_id": question.get("id"),
            "target_id": question.get("target_id"), "plan": deepcopy(plan), "font_size": size}
        return result
    try:
        scale = float(font_scale())
    except (TypeError, ValueError):
        scale = 1
    if not isfinite(scale) or scale <= 0:
        scale = 1
    if available_width is None:
        try:
            available_width = float(app.main_window.size.width) - 32
        except (AttributeError, TypeError, ValueError):
            available_width = 328
    if not isfinite(available_width) or available_width < 1:
        available_width = 328
    lines = _prompt_lines(plan["segments"], available_width, size, scale)
    rows = []
    for line in lines:
        cells = []
        for cell in line:
            children = []
            if cell["annotation"]:
                children.append(label(cell["annotation"], size=cell["annotation_size"], color=MUTED,
                                      text_align="center", width=cell["width"], margin_bottom=0,
                                      text_role="study"))
            children.append(label(cell["surface"], size=size, color=INK, text_align="center",
                                  width=cell["width"], margin_bottom=0,
                                  text_role=learning_text_role(question)))
            cells.append(col(*children, width=cell["width"]))
        rows.append(row(*cells, align_items="end", margin_bottom=6))
    result = col(*rows, margin_bottom=8)
    result._prompt_support = plan
    result._feedback_fragment = {"kind": "question_prompt", "question_id": question.get("id"),
        "target_id": question.get("target_id"), "plan": deepcopy(plan), "font_size": size,
        "available_width": available_width, "font_scale": scale, "lines": deepcopy(lines)}
    return result
