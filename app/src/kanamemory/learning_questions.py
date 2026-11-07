"""Deterministic, inspectable local vocabulary and grammar questions.

Question snapshots contain separate stimulus and answer fields. This module does
not schedule cards and does not write learner data. Unsupported free responses
are unresolved, not confidently marked wrong by a string mismatch.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import random
import re
import unicodedata

from .content_pack import load_namespace
from .learning_content import get_learning_item
from . import pitch_practice
from . import multi_cloze

_CONTENT = load_namespace("learning_questions", {})
_PRACTICE = load_namespace("varied_practice", {})
_SOUND_MIX = load_namespace("word_sound_mix", {})
from .kana_orthography import allowed_word_spellings, same_kana_sound
from . import word_sound_questions as sound_questions

AUDIO_WORD_KIND = sound_questions.AUDIO_WORD
READING_CHOICE_KIND = sound_questions.READING_CHOICE
SOUND_KINDS = (AUDIO_WORD_KIND, READING_CHOICE_KIND)
AUTHORED_WORD_KINDS = ("vocab_meaning", "vocab_audio_meaning", "vocab_reading", "vocab_recall",
                       "vocab_orthography", "vocab_usage", "vocab_zh_ja_contrast",
                       "reading_comprehension", "listening_comprehension")
WORD_KINDS = (*AUTHORED_WORD_KINDS, *SOUND_KINDS)
GRAMMAR_KINDS = ("grammar_meaning", "grammar_cloze", "grammar_transform",
                 "grammar_reorder", "targeted_correction", "controlled_sentence_production")
PRONUNCIATION_KINDS = ("pronunciation_rule", "pronunciation_application")
KIND_LABELS = {
    "vocab_meaning": "看词辨义", "vocab_audio_meaning": "听词辨义",
    "vocab_reading": "写出读法", "vocab_recall": "根据含义想词",
    "vocab_orthography": "选择写法", "vocab_usage": "语境选词",
    "vocab_zh_ja_contrast": "中日词义辨析", "reading_comprehension": "短文理解",
    "listening_comprehension": "短句听力", "grammar_meaning": "语法含义",
    "grammar_cloze": "语法填空", "grammar_transform": "变换词形",
    "grammar_reorder": "排列句子", "targeted_correction": "修正一处错误",
    "controlled_sentence_production": "按要求造句",
    "pronunciation_rule": "读音规则辨析", "pronunciation_application": "读音规则应用",
    AUDIO_WORD_KIND: "听词选词", READING_CHOICE_KIND: "看词选读音",
}
EXPERIMENTAL_KINDS = ("free_writing_translation", "situational_dialogue",
                      "pitch_discrimination", "shadowing_read_aloud", "spoken_recall")
# Heard-meaning tasks stay opt-in. The heard-word task is on by default but still
# needs a working voice, which the UI checks through audio_ready_kinds.
OPT_IN_LISTENING_KINDS = {"vocab_audio_meaning", "listening_comprehension"}
LISTENING_KINDS = {*OPT_IN_LISTENING_KINDS, AUDIO_WORD_KIND}
TEXT_KINDS = {"vocab_reading", "vocab_recall", "grammar_transform",
              "targeted_correction", "controlled_sentence_production"}
DEFAULT_KINDS = tuple(k for k in (*WORD_KINDS, *GRAMMAR_KINDS, *PRONUNCIATION_KINDS)
                      if k not in OPT_IN_LISTENING_KINDS)
SOUND_PREFERENCE_VERSION = "sound_kinds_version"
KIND_ABILITIES = {
    "vocab_meaning": "understanding", "vocab_audio_meaning": "listening",
    "vocab_reading": "reading", "vocab_recall": "use", "vocab_orthography": "use",
    "vocab_usage": "use", "vocab_zh_ja_contrast": "understanding",
    "reading_comprehension": "understanding", "listening_comprehension": "listening",
    "grammar_meaning": "understanding", "grammar_cloze": "application",
    "grammar_transform": "application", "grammar_reorder": "application",
    "targeted_correction": "application", "controlled_sentence_production": "application",
    "pronunciation_rule": "understanding", "pronunciation_application": "application",
    AUDIO_WORD_KIND: "listening", READING_CHOICE_KIND: "reading",
}


def stored_enabled_kinds(raw):
    """Enabled kinds from the saved setting, before any device capability check.

    A list saved before the sound kinds existed gains them once: leaving them
    out then was not a choice. Saving the settings page records the version, so
    switching them off afterwards stays off. An empty list was a choice to
    practise nothing, and a malformed value stays malformed for its caller to
    refuse. Reading never writes the setting.
    """
    if not isinstance(raw, dict):
        return []
    if "enabled" not in raw:
        return list(DEFAULT_KINDS)
    enabled = raw["enabled"]
    if not isinstance(enabled, list) or not enabled or raw.get(SOUND_PREFERENCE_VERSION) == 1:
        return enabled
    return list(dict.fromkeys([*enabled, *SOUND_KINDS]))


def audio_ready_kinds(kinds, audio_ready):
    """Without a Japanese voice the heard-word task cannot be presented."""
    return [kind for kind in kinds if audio_ready or kind != AUDIO_WORD_KIND]


def learning_order_translation_source(question):
    """Resolve an answer-only reference from a locally defined sentence field.

    This does not establish that model-authored Japanese is semantically sound.
    It only binds the reference to the actual stimulus or completed answer,
    without trusting another model-provided source or display-safety flag.
    Fragments, ambiguous blanks, mixed instructions and hidden audio stay out.
    """
    if not isinstance(question, dict):
        return None
    kind = question.get("kind")
    if (kind in LISTENING_KINDS or question.get("requires_audio")
            or question.get("audio_only") or question.get("has_hidden_transcript")):
        return None
    field = "prompt"
    if multi_cloze.is_cloze_question(question):
        text = multi_cloze.answer_label(question)
        field = "answer"
    elif kind in {"grammar_cloze", "vocab_usage"}:
        prompt, answer = question.get("prompt"), question.get("answer")
        if not isinstance(prompt, str) or not isinstance(answer, str) or prompt.count("（　）") != 1:
            return None
        text = prompt.replace("（　）", answer)
    elif kind in {"grammar_transform", "grammar_reorder", "targeted_correction",
                  "controlled_sentence_production"}:
        # The stimulus may deliberately be wrong or contain only shuffled
        # pieces. A complete canonical answer is the only useful source here.
        field, text = "answer", question.get("answer")
    elif kind in {"vocab_meaning", "vocab_reading", "vocab_zh_ja_contrast",
                  "reading_comprehension", "grammar_meaning", "pronunciation_application"}:
        text = question.get("prompt")
    else:
        return None
    roles = question.get("text_roles")
    if isinstance(roles, dict) and roles.get(field) == "ui":
        return None
    # This deliberately conservative shape check does not guess a sentence
    # from a bare word or split a composite instruction/quotation. Such cases
    # retain their ordinary answer and may simply leave this extension null.
    if (not isinstance(text, str) or not re.search(r"[ぁ-ゖァ-ヶ]", text)
            or not text.rstrip().rstrip("」』\"'").endswith(("。", "！", "？", "!", "?"))
            or re.search(r"[A-Za-z_＿□■◯○{}<>→⇒：:\n\r]|[（(\[【]\s*[）)\]】]|\.\.\.|…", text)
            or any(part in text for part in ("选择", "写出", "请用", "改成", "的意思", "怎么读"))):
        return None
    return text


def _item(value, item=None):
    result = deepcopy(item or (value if isinstance(value, dict) else get_learning_item(value)))
    if not result:
        raise ValueError(f"Unknown learning item: {value}")
    return result


def validate_practice(raw, *, target_id=None):
    """Validate presentation/planning facts; this is not an authorship grant.

    Spans always address the public prompt (a completion marks its visible
    gap). Source anchors and authoring decisions remain in the content pack.
    A reviewed flag alone cannot attach an extension to a changed lesson.
    """
    value = raw.get("practice")
    if value is None:
        return None
    required = {"schema_version", "task_family", "context_id", "scaffold",
                "target_spans", "background_ids", "reviewed"}
    if (not isinstance(value, dict) or not required <= set(value)
            or set(value) - required - {"example_id", "coverage_scope", "assessed_ids"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["reviewed"] is not True):
        raise ValueError("Unsupported reviewed practice metadata")
    for name in ("task_family", "context_id", "example_id"):
        if name in value and (not isinstance(value[name], str) or not value[name]
                              or len(value[name]) > 256):
            raise ValueError("Invalid practice identity")
    if value["scaffold"] not in {"recognition", "cued", "retrieval"}:
        raise ValueError("Invalid practice scaffold")
    mode = raw.get("input_mode", "choice")
    if ((mode == "choice" and value["scaffold"] != "recognition")
            or (mode in {"reorder", "multi_cloze"} and value["scaffold"] != "cued")
            or (mode == "text" and value["scaffold"] == "recognition")):
        raise ValueError("Practice scaffold disagrees with response control")
    backgrounds = value["background_ids"]
    if (not isinstance(backgrounds, list) or len(backgrounds) > 40
            or any(not isinstance(cid, str) or not cid or len(cid) > 1024
                   or cid == target_id for cid in backgrounds)
            or len(set(backgrounds)) != len(backgrounds)):
        raise ValueError("Invalid practice background targets")
    if "coverage_scope" in value or "assessed_ids" in value:
        assessed = value.get("assessed_ids")
        if (value.get("coverage_scope") not in {"focused", "cumulative"}
                or not isinstance(assessed, list) or not 1 <= len(assessed) <= 8
                or any(not isinstance(cid, str) or not cid or len(cid) > 1024 for cid in assessed)
                or len(set(assessed)) != len(assessed)
                or target_id is not None and (target_id not in assessed
                    or not set(assessed) <= {target_id, *backgrounds})
                or value["coverage_scope"] == "focused" and len(assessed) != 1
                or value["coverage_scope"] == "cumulative" and len(assessed) < 2):
            raise ValueError("Invalid reviewed coverage scope")
    spans, prompt = value["target_spans"], raw.get("prompt", "")
    if not isinstance(spans, list) or not 1 <= len(spans) <= 20 or not isinstance(prompt, str):
        raise ValueError("Practice needs explicit public target spans")
    for span in spans:
        if (not isinstance(span, dict) or set(span) != {"start", "end", "quote"}
                or type(span["start"]) is not int or type(span["end"]) is not int
                or not 0 <= span["start"] < span["end"] <= len(prompt)
                or prompt[span["start"]:span["end"]] != span["quote"]):
            raise ValueError("Practice target span does not match its prompt")
    return deepcopy(value)


def _practice_binding(item):
    from .material_review import item_fingerprint
    if item.get("ai_generated") or _PRACTICE.get("schema_version") != 1:
        return None
    extension = _PRACTICE.get("items", {}).get(item.get("id"), {})
    if not isinstance(extension, dict) or not extension:
        return None
    identity = item_fingerprint(item)
    if extension.get("item_fingerprint") == identity:
        return extension
    old = extension.get("history", {}).get(identity)
    return old if isinstance(old, dict) and old.get("item_fingerprint") == identity else None


def _template_fingerprint(raw):
    return hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def retired_practice_variants(item, templates):
    """Future selection exclusions, bound to exact lesson and raw template.

    Retired templates remain reconstructible for saved questions and history.
    Custom lessons and model-authored flags cannot retire local material.
    """
    from .material_review import item_fingerprint
    if item.get('ai_generated') or not isinstance(templates, dict):
        return frozenset()
    result = set(_cognate_mapping_variants(item, templates))
    entries = _PRACTICE.get('template_retirements', {}).get(item.get('id'), {})
    if not entries:
        return frozenset(result)
    retired = entries.get(item_fingerprint(item), {})
    if not isinstance(retired, dict):
        raise ValueError('Invalid exact template retirement')
    for kind, hashes in retired.items():
        if (not isinstance(hashes, list) or any(not isinstance(value, str)
                or re.fullmatch(r'[0-9a-f]{64}', value) is None for value in hashes)):
            raise ValueError('Invalid retired template fingerprints')
        fingerprints = set(hashes)
        for variant, raw in enumerate(templates.get(kind, [])):
            if _template_fingerprint(raw) in fingerprints:
                result.add((kind, variant))
    return frozenset(result)


def _cognate_mapping_variants(item, templates):
    """Word-to-Chinese tasks that a shared written form makes nearly automatic.

    The reviewed list binds id, title and reading, so an edited word falls back
    to its full bank. Two tasks keep the understanding ability alive; they need no
    other known word, or a learner with no background would have nothing to answer.
    Base-form recall from a sentence stays: it tests the form, not a translation.
    Selection only: saved questions, drafts and history still rebuild as before.
    """
    entry = _SOUND_MIX.get("cognate_words", {}).get(item.get("id"))
    if not entry or entry.get("title") != item.get("title") or entry.get("reading") != item.get("reading"):
        return ()
    meaning = templates.get("vocab_meaning", [])
    def keep_order(pair):
        practice = pair[1].get("practice") if isinstance(pair[1], dict) else None
        return (bool(practice and practice.get("background_ids")), not practice, pair[0])
    # A bare base task always exists and needs nothing, so the second place goes to it.
    ranked = [index for index, _ in sorted(enumerate(meaning), key=keep_order)]
    bare = [index for index in ranked if not (isinstance(meaning[index], dict) and meaning[index].get("practice"))]
    kept = {*ranked[:1], *bare[:1]} if bare else set(ranked[:2])
    retired = [("vocab_meaning", i) for i in range(len(meaning)) if i not in kept]
    retired.extend(("vocab_recall", i) for i, raw in enumerate(templates.get("vocab_recall", []))
                   if not (isinstance(raw, dict) and "基本形是" in str(raw.get("task", ""))))
    return retired


def _practice_source(item, kind, raw):
    binding = _practice_binding(item)
    if binding is None:
        return None
    fingerprint = _template_fingerprint(raw)
    if any(_template_fingerprint(candidate) == fingerprint
           for candidate in binding.get("templates", {}).get(kind, [])):
        return {"schema_version": 1, "item_fingerprint": binding["item_fingerprint"],
                "template_sha256": fingerprint}
    return None


def rebuild_practice_question(question, item):
    """Reconstruct a frozen task from retained reviewed raw material.

    The old ordinal is retained for identity/shuffle, but never reduced modulo
    a newly extended pool. A self-supplied hash is insufficient: its exact raw
    template and lesson binding must still exist in the trusted content pack.
    """
    source = question.get("practice_source")
    if (not isinstance(source, dict)
            or set(source) != {"schema_version", "item_fingerprint", "template_sha256"}
            or type(source.get("schema_version")) is not int or source["schema_version"] != 1):
        raise ValueError("Invalid frozen practice source")
    binding = _practice_binding(item)
    if binding is None or binding["item_fingerprint"] != source["item_fingerprint"]:
        raise ValueError("Frozen practice lesson binding is unavailable")
    kind = question.get("kind")
    raw = next((candidate for candidate in binding.get("templates", {}).get(kind, [])
                if _template_fingerprint(candidate) == source["template_sha256"]), None)
    variant = question.get("variant")
    if raw is None or type(variant) is not int or variant < 0:
        raise ValueError("Frozen practice template is unavailable")
    return build_learning_question(item, kind, variant=variant,
        guided=question.get("guided") is True, apply_review=False, _practice_template=raw)


def reviewed_practice_context(question):
    """Read verified background glosses; never trust imported display flags."""
    source = question.get("practice_source")
    if (not isinstance(source, dict)
            or set(source) != {"schema_version", "item_fingerprint", "template_sha256"}
            or type(source.get("schema_version")) is not int or source["schema_version"] != 1):
        return None
    cid, kind = question.get("target_id"), question.get("kind")
    record = _PRACTICE.get("items", {}).get(cid, {})
    if record.get("item_fingerprint") != source["item_fingerprint"]:
        record = record.get("history", {}).get(source["item_fingerprint"], {})
    if (not record or record.get("item_fingerprint") != source["item_fingerprint"]
            or question.get("revision") != record.get("item_revision")
            or question.get("content_id") != cid):
        return None
    raw = next((r for r in record.get("templates", {}).get(kind, [])
                if _template_fingerprint(r) == source["template_sha256"]), None)
    if raw is None:
        return None
    mode = raw.get("input_mode", "choice")
    family = "word" if kind in WORD_KINDS else "grammar"
    expected = {"prompt": raw["prompt"], "task": raw.get("task", ""),
                "answer": raw["answer"], "input_mode": mode, "family": family,
                "ability": KIND_ABILITIES[kind], "practice": raw["practice"],
                "strict": raw.get("strict", mode in {"choice", "reorder"})}
    if 'multi_cloze' in raw or 'multi_cloze' in question:
        expected['multi_cloze'] = raw.get('multi_cloze')
    if any(question.get(key) != value for key, value in expected.items()):
        return None
    choices = raw.get("choices", [])
    for key in ("choices", "options"):
        actual = question.get(key)
        if (not isinstance(actual, list) or any(not isinstance(v, str) for v in actual)
                or sorted(actual) != sorted(choices)):
            return None
    accepted = list(dict.fromkeys([raw["answer"], *raw.get("accepted_answers", [])]))
    # Reviewed practice freezes every allowed form in its signed raw template.
    # A later global reading-map update must not change a pending question.
    if (question.get("accepted_answers") != accepted
            or question.get("alternative_answers", []) != raw.get("alternative_answers", [])):
        return None
    context = record.get("contexts", {}).get(source["template_sha256"])
    return deepcopy(context) if isinstance(context, dict) else None


@lru_cache(maxsize=1)
def _word_pool():
    """The bundled catalogue is fixed for a process; options must not rescan it."""
    from .learning_content import learning_items
    return sound_questions.word_pool(learning_items("word"))


def _sound_templates(item, kind):
    """Sound tasks only derive from a lesson's own fields; never AI or custom text."""
    if (item.get("family") != "word" or item.get("ai_generated")
            or str(item.get("id", "")).startswith("local:")
            or not item.get("title") or not item.get("reading")
            or KIND_ABILITIES[kind] not in item.get("abilities", ())):
        return []
    build = (sound_questions.audio_word_templates if kind == AUDIO_WORD_KIND
             else sound_questions.reading_choice_templates)
    return build(item, _word_pool())


def practice_templates(item):
    """Reviewed tasks for an exact lesson, plus sound tasks from its fields."""
    result = _reviewed_templates(item)
    if result is None:
        return None
    for kind in SOUND_KINDS:
        variants = _sound_templates(item, kind)
        if variants:
            result[kind] = variants
    return result


def _reviewed_templates(item):
    """Reviewed append-only tasks for an exact lesson, without changing it.

    Existing template positions, revision, examples and saved queue snapshots
    remain owners. Missing packs and stale/custom lessons keep legacy behavior.
    """
    base = item.get("question_templates")
    extension = _practice_binding(item)
    if extension is None:
        return deepcopy(base)
    if base is None:
        base = _CONTENT.get("GRAMMAR_QUESTIONS", {}).get(item.get("id"))
    if (base is None and item.get("family") == "word"
            and item.get("id") in _CONTENT.get("LEGACY_WORD_IDS", ())
            and not item.get("dictionary_only")):
        # The original four authored lessons used a function rather than a
        # template array. Reserve all four positions used by the selector;
        # exact-bound additions follow them. Already-issued questions remain
        # frozen in their queues, including historical larger variant values.
        base = {}
        for kind in AUTHORED_WORD_KINDS:
            if KIND_ABILITIES[kind] in item.get("abilities", []):
                raw = _word_question(item, kind, 0)  # Legacy raw ignores variant.
                base[kind] = [deepcopy(raw) for _ in range(4)]
    if base is None:
        return None  # No guessed template replacement for dictionary fallback.
    result = deepcopy(base)
    allowed = (PRONUNCIATION_KINDS if item.get("domain") == "pronunciation" else
               WORD_KINDS if item["family"] == "word" else GRAMMAR_KINDS)
    for kind, additions in extension.get("templates", {}).items():
        if kind not in allowed or KIND_ABILITIES[kind] not in item.get("abilities", []):
            raise ValueError("Practice extension cannot create a memory ability")
        if not isinstance(additions, list):
            raise ValueError("Invalid practice extension templates")
        for raw in additions:
            if validate_practice(raw, target_id=item["id"]) is None:
                raise ValueError("Practice extension lacks reviewed metadata")
        result.setdefault(kind, []).extend(deepcopy(additions))
    return result


def question_kinds(content_id, dimension=None, *, ability=None, item=None):
    target = _item(content_id, item)
    templates = practice_templates(target)
    if templates is not None:
        allowed = (PRONUNCIATION_KINDS if target.get("domain") == "pronunciation" else
                   WORD_KINDS if target["family"] == "word" else GRAMMAR_KINDS)
        kinds = [kind for kind in allowed if templates.get(kind)]
    elif target.get("domain") == "pronunciation":
        # Pronunciation has no inferred exercises or guessed distractors.
        kinds = []
    else:
        kinds = ("vocab_meaning", "vocab_reading") if target.get("dictionary_only") else (
            AUTHORED_WORD_KINDS if target["family"] == "word" else GRAMMAR_KINDS)
        kinds = (*kinds, *(kind for kind in SOUND_KINDS if _sound_templates(target, kind)))
    wanted = ability or dimension
    copy_reading = (target["family"] == "word" and target.get("reading")
                    and _reading_normal(target["title"]) == _reading_normal(target["reading"]))
    return [k for k in kinds if (wanted is None or KIND_ABILITIES[k] == wanted)
            and not (copy_reading and k in {"vocab_reading", READING_CHOICE_KIND})]


def _normal(text):
    text = unicodedata.normalize("NFKC", str(text)).strip()
    return re.sub(r"[\s。．.!！?？、,，]+", "", text)


def _reading_normal(text):
    return "".join(chr(ord(c) - 0x60) if 0x30A1 <= ord(c) <= 0x30F6 else c for c in _normal(text))


def _grammar_kana(text):
    """Curated readings for these exact examples, not a general kanji guesser."""
    forms = deepcopy(_CONTENT.get('GRAMMAR_READINGS', {}))
    return re.sub("|".join(re.escape(x) for x in sorted(forms, key=len, reverse=True)) or r"(?!)",
                  lambda match: forms[match.group()], text)


def _support(prompt, target, enabled=False, readings=None):
    """Only reviewed exact spans from the public question, never its answer."""
    known = deepcopy(_CONTENT.get('SUPPORT_READINGS', {}))
    if readings is not None:
        # New lessons provide explicit readings for their actual sentence.
        # Do not guess readings of arbitrary compounds from the old tiny map.
        known = {surface: (reading, None, "kanamemory-local-curriculum-v1")
                 for surface, reading in readings.items() if surface and reading}
    if target["family"] == "word":
        personal = target.get('personal_reading')
        known[target["title"]] = (target["reading"], None if personal else target.get("accent"),
                                   'personal-reading:' + personal['version_id'] if personal else
                                   target.get("reading_source_id", "kanamemory-learning-foundation-v1"))
    segments = []
    position = 0
    for match in re.finditer("|".join(re.escape(x) for x in sorted(known, key=len, reverse=True)) or r"(?!)", prompt):
        if match.start() > position:
            segments.append({"start": position, "end": match.start(), "surface": prompt[position:match.start()]})
        surface = match.group(); reading, accent, source = known[surface]
        segment = {"start": match.start(), "end": match.end(), "surface": surface,
                   "reading": reading, "reading_source_id": source, "role": "background",
                   "reading_safe": enabled, "accent_safe": enabled, "scope": "word"}
        if target.get('personal_reading') and surface == target.get('title'):
            segment['accent_safe'] = False
        if accent is not None:
            segment.update(accent_number=accent, accent_source_id="kanjium-pitch-accent",
                           accent_surface=surface, accent_reading=reading)
        segments.append(segment); position = match.end()
    if position < len(prompt):
        segments.append({"start": position, "end": len(prompt), "surface": prompt[position:]})
    return {"reviewed": True, "content_revision": target["revision"], "prompt": prompt,
            "reading_safe": enabled, "accent_safe": enabled, "audio_safe": enabled,
            "has_gap": "（　）" in prompt or "___" in prompt,
            "reading_ambiguity": False, "segments": segments if enabled else []}


def _bundled_teaching_target(target):
    """Only exact local teaching material may use automatic background spans."""
    if target.get("ai_generated") or target.get("dictionary_only"):
        return False
    original = get_learning_item(target.get("id"))
    if not original:
        return False
    fields = ("id", "family", "domain", "revision", "title", "reading", "instruction", "question_templates")
    candidate, authored = ({key: value.get(key) for key in fields} for value in (target, original))
    if candidate == authored:
        return True
    from .material_review import without_order_translations
    return without_order_translations(candidate) == without_order_translations(authored)


def _background_support(prompt, target, raw, kind, *, guided=False):
    from .question_support import background_support
    if guided or kind not in {"vocab_usage", "grammar_cloze", "grammar_transform",
                              "grammar_reorder", "targeted_correction", "controlled_sentence_production"}:
        return None
    authored = raw.get("readings")
    explicit = _support(prompt, target, True, authored)["segments"] if isinstance(authored, dict) else []
    return background_support(prompt, target, raw, kind, explicit=explicit,
        bundled=_bundled_teaching_target(target), guided=guided)


def _word_question(item, kind, variant):
    if kind in SOUND_KINDS:
        variants = _sound_templates(item, kind)
        if not variants:
            raise ValueError("No sound task is available for this word")
        return variants[variant % len(variants)]
    form, reading, meaning = item["title"], item["reading"], item["meaning"]
    instruction = item["instruction"]
    # This branch serves the original four lessons and conservative dictionary
    # recall. Expanded lessons own reviewed templates; cloning their full
    # catalogue just to obtain three old distractors is unnecessary work.
    legacy_words = [] if item.get("dictionary_only") else [value for key in _CONTENT.get("LEGACY_WORD_IDS", ())
        if (value := get_learning_item(key)) is not None]
    other_meanings = [x["meaning"] for x in legacy_words
                      if x["id"] != item["id"] and x["meaning"] != meaning]
    # Dictionary entries can have overlapping senses; an unknown alternative is
    # never auto-marked wrong. These questions use recall rather than guessed
    # dictionary distractors.
    if kind == "vocab_meaning" and item.get("dictionary_only"):
        return dict(prompt=form, task="写出本课义项的中文或英文意思。也可显示答案后核对。",
                    answer=meaning, accepted_answers=item.get("accepted_meanings", [meaning]),
                    input_mode="text", choices=[], feedback=meaning, strict=False,
                    safe=False)
    base = dict(prompt=form, task="这个词在本课中的意思是？", answer=meaning,
                choices=[meaning, *other_meanings[:3]], feedback=meaning, safe=True)
    if kind == "vocab_meaning":
        base["task"] = "这个词在本课中的意思是？"
    elif kind == "vocab_audio_meaning":
        base.update(prompt="听单词，选择本课所学的意思。", task="先播放，再选择。",
                    audio_text=reading, safe=False)
    elif kind == "vocab_reading":
        base.update(prompt=form, task=f"本课义项：{meaning}。写出日语假名读法。",
                    answer=reading, accepted_answers=[reading],
                    alternative_answers=list(dict.fromkeys([
                        *item.get("alternative_readings", []),
                        *[r for r in item.get("accepted_readings", [])
                          if _reading_normal(r) != _reading_normal(reading)]])),
                    choices=[], input_mode="text", strict=True, safe=False)
    elif kind == "vocab_recall":
        base.update(prompt=item["recall"], task="写出本课学的单词。",
                    answer=form, accepted_answers=[form, reading], choices=[],
                    input_mode="text", strict=False, known_wrong=item["wrong_forms"], safe=False)
    elif kind == "vocab_orthography":
        choices = [form, *[x["title"] for x in legacy_words if x["id"] != item["id"] and x["title"] != form][:3]]
        base.update(prompt=f"{reading} · {meaning}", task="选择对应的常用写法。",
                    answer=form, choices=choices, safe=False)
    elif kind == "vocab_usage":
        prompt, context = item["cloze"]
        answer = _CONTENT.get("USAGE_ANSWER_OVERRIDES", {}).get(form, form)
        choices = [answer, *[x for x in _CONTENT.get("USAGE_CHOICES", ()) if x != answer][:3]]
        base.update(prompt=prompt, task=context, answer=answer, choices=choices,
                    feedback=instruction["rule"], safe=False)
    elif kind == "vocab_zh_ja_contrast":
        contrasts = deepcopy(_CONTENT.get('CONTRAST_CHOICES', {}))
        choices = contrasts[form]
        base.update(prompt=form, task="本课这个日语词应该用于哪种情况？", answer=choices[0],
                    choices=choices, feedback=instruction["contrast"], safe=True)
    elif kind in ("reading_comprehension", "listening_comprehension"):
        sentence = instruction["example"]
        base.update(prompt=sentence, task=f"句中「{form}」表达的本课义项是？",
                    feedback=instruction["translation"], safe=True)
        if kind == "listening_comprehension":
            base.update(prompt="听短句，选择所学单词在句中的意思。", task="先播放，再选择。",
                        audio_text=sentence, safe=False)
    return base


def _grammar_question(item, kind, variant):
    variants = _CONTENT.get("GRAMMAR_QUESTIONS", {}).get(item["id"], {}).get(kind, [])
    if not variants:
        raise ValueError("No reviewed question templates are available")
    return deepcopy(variants[variant % len(variants)])


def build_learning_question(content_id, kind=None, *, variant=0, guided=False, item=None, apply_review=True,
                            _practice_template=None):
    target = _item(content_id, item)
    if apply_review:
        from .material_review import reviewed_item
        target = reviewed_item(target)
    if target.get("instruction_status") == "instruction_missing":
        raise ValueError("Learning material is not ready for this reading/sense")
    kinds = question_kinds(target)
    if not kinds:
        raise ValueError("No reviewed question templates are available")
    kind = kind or kinds[variant % len(kinds)]
    if kind not in kinds:
        raise ValueError(f"Question kind {kind!r} is not available for {target['id']}")
    templates = practice_templates(target)
    if templates is not None:
        variants = templates[kind]
        raw = deepcopy(variants[variant % len(variants)])
        if _practice_template is not None:
            if _practice_source(target, kind, _practice_template) is None:
                raise ValueError("Unregistered frozen practice template")
            raw = deepcopy(_practice_template)
        if pitch_practice.is_pitch_question(raw):
            if target.get("domain") != "pronunciation" or kind not in PRONUNCIATION_KINDS:
                raise ValueError("Pitch exercises require a pronunciation learning item")
            expected = pitch_practice.canonical_answer(raw)
            if raw.get("answer") != expected:
                raise ValueError("Pitch template answer disagrees with its reviewed model")
        if multi_cloze.is_cloze_question(raw):
            expected = multi_cloze.canonical_answer(dict(raw, target_id=target['id'],
                                                         ability=KIND_ABILITIES[kind]))
            if raw.get('answer') != expected:
                raise ValueError('Multi-gap answer disagrees with its reviewed combinations')
        if not raw.get("prompt") or not raw.get("answer") or not raw.get("feedback"):
            raise ValueError("A local question requires a prompt, answer and explanation")
        if raw.get("input_mode", "choice") == "choice":
            choices = raw.get("choices", [])
            if (len(choices) < 2 or len(set(choices)) != len(choices)
                    or raw["answer"] not in choices):
                raise ValueError("A choice template requires distinct options including the answer")
    else:
        raw = (_word_question if target["family"] == "word" else _grammar_question)(target, kind, variant)
    legacy_word_fields = (target['family'] == 'word' and (templates is None or
        target.get('question_templates') is None and 'practice' not in raw
        and raw == _word_question(target, kind, variant)))
    if (guided and target.get("question_templates") is None
            and target["family"] == "word" and kind == "vocab_meaning"
            and "practice" not in raw):
        example = target["instruction"]["example"]
        raw["prompt"] = (example if example != target["title"] else
                         f"{target['title']}（{target['reading']}）\n{target['meaning']}")
        raw["task"] = "对照讲解，选择例词在这里的意思。"
    # Legacy guides retain a small choice control, including dictionary recall.
    # Reviewed application tasks keep their actual input operation. Both remain
    # explicitly assisted exposure; their demonstration is never SRS evidence.
    practice = validate_practice(raw, target_id=target["id"])
    if (guided and practice is None and raw.get("input_mode", "choice") != "choice"
            and not pitch_practice.is_pitch_question(raw)):
        raw.update(input_mode="choice", choices=[raw["answer"], "还不清楚，回看讲解"],
                   task="对照上方示范，选择本题的表达。", safe=False)
    identity = f"{target['id']}|{target['revision']}|{kind}|{variant}|{'guided' if guided else 'retrieval'}"
    question_id = "learning:" + hashlib.sha256(identity.encode()).hexdigest()[:24]
    choices = list(raw.get("choices", []))
    random.Random(identity).shuffle(choices)
    prompt = raw["prompt"]
    mode = raw.get("input_mode", "choice")
    safe = bool(raw.get("safe") and mode == "choice" and not guided and kind in {
        "vocab_meaning", "vocab_zh_ja_contrast", "grammar_meaning", "reading_comprehension"})
    # Appending private variants must not remove the old exact-span readings
    # from foundation lessons whose base templates are synthesized in code.
    explicit_readings = raw.get("readings", {}) if (target.get("question_templates") is not None or practice is not None) else None
    support = _support(prompt, target, safe, explicit_readings)
    if not safe:
        background = _background_support(prompt, target, raw, kind, guided=guided)
        if background:
            support = background
    result = {
        "id": question_id, "question_id": question_id,
        "target_id": target["id"], "content_id": target["id"],
        "family": target["family"], "ability": KIND_ABILITIES[kind],
        "dimension": KIND_ABILITIES[kind], "kind": kind, "label": KIND_LABELS[kind],
        "revision": target["revision"], "content_revision": target["revision"],
        "prompt": prompt, "task": raw.get("task", ""), "answer": raw["answer"],
        "accepted_answers": list(dict.fromkeys([raw["answer"], *raw.get("accepted_answers", [])])),
        "alternative_answers": raw.get("alternative_answers", []),
        "choices": choices, "options": choices, "input_mode": mode, "response": mode,
        "audio_text": raw.get("audio_text", ""),
        "optional_audio_text": prompt if safe else "",
        "support": support, "spans": support["segments"],
        "guide": (target["instruction"]["rule"] + "\n" + target["instruction"]["example"]
                  + "\n本题示范：" + (pitch_practice.answer_label(raw)
                      if pitch_practice.is_pitch_question(raw) else multi_cloze.answer_label(raw)
                      if multi_cloze.is_cloze_question(raw) else raw["answer"])) if guided else "",
        "feedback": raw["feedback"], "evidence_mode": "exposure_only" if guided else "primary",
        "strict": raw.get("strict", mode in ("choice", "reorder")),
        "known_wrong": raw.get("known_wrong", []), "guided": guided,
        "requires_audio": kind in LISTENING_KINDS,
        "exposure_group": target["id"] + ":" + target["revision"],
        "variant": variant,
    }
    if practice is not None:
        result["practice"] = practice
        source = _practice_source(target, kind, raw)
        if source is not None:
            result["practice_source"] = source
    if multi_cloze.is_cloze_question(raw):
        result['multi_cloze'] = multi_cloze.specification(raw)
        result.update(optional_audio_text='', audio_text='', requires_audio=False, has_gap=True)
        multi_cloze.validate_snapshot(result)
    if support.get("background_support_version"):
        result["background_support_version"] = support["background_support_version"]
    if target.get("domain") == "pronunciation":
        # All these tests concern reading phenomena. TTS and optional ruby can
        # reveal their answer; generic voice output cannot certify pitch either.
        result.update(domain="pronunciation", tests_reading=True,
                      audio_text="", optional_audio_text="", requires_audio=False)
        roles = raw.get("text_roles", {})
        if not isinstance(roles, dict) or any(roles.get(field) not in {"ui", "study"}
                                               for field in ("prompt", "answer")):
            raise ValueError("Pronunciation templates require explicit prompt and answer text roles")
        result["text_roles"] = {field: roles[field] for field in ("prompt", "answer")}
        if target.get("question_template_revision"):
            result["question_template_revision"] = target["question_template_revision"]
    if pitch_practice.is_pitch_question(raw):
        result.update(pitch_practice=pitch_practice.specification(raw), tests_pitch=True,
                      strict=True, audio_text="", optional_audio_text="", requires_audio=False)
        if result["pitch_practice"]["version"] in (2, 3):
            result["task"] = pitch_practice.public_task(result)
        pitch_practice.validate_snapshot(result)
    if 'prosody_cue' in raw:
        from .prosody_cue import validate_cue
        if target.get('domain') != 'pronunciation' or mode != 'choice' or practice is None or practice['task_family'] != 'prosody_observation':
            raise ValueError('A contour stimulus requires a reviewed pronunciation observation')
        result['prosody_cue'] = validate_cue(raw['prosody_cue'], prompt)
        result['tests_pitch'] = True
    if (practice is None and target["family"] == "grammar"
            and target.get("domain") != "pronunciation" and mode in {"text", "reorder"}):
        # Grammar application does not test kanji orthography. A correct kana
        # rendering must not be penalized merely for using an unconverted IME.
        result["accepted_answers"] = list(dict.fromkeys([
            *result["accepted_answers"], _grammar_kana(raw["answer"])]))
    if "order_translation" in raw:
        reference = raw["order_translation"]
        if reference is not None:
            from .order_translations import validate_order_translation
            source = learning_order_translation_source({**raw, **result})
            if source is None:
                raise ValueError("An experimental translation requires a complete local sentence source")
            reference = validate_order_translation(reference, source_text=source, source_translation="")
        # Separate metadata, never a support channel, answer or identity input.
        result["order_translation"] = reference
    from .learning_conditions import for_question
    result.update(for_question(result))
    from .question_dependencies import attach as attach_field_dependencies
    attach_field_dependencies(result, target, templates=not legacy_word_fields)
    if apply_review:
        # New and guided questions use the same reviewed presentation as a
        # pending stored question. This never changes an existing snapshot.
        from .material_review import reviewed_question
        result = reviewed_question(result)
    from .personal_question import apply as apply_personal_rubric
    result = apply_personal_rubric(result, target)
    if not guided:
        from .assessment_contract import for_question as assessment_contract
        result["assessment_contract"] = assessment_contract(result)
    return result


def grade_learning_answer(question: dict, answer) -> dict:
    from .material_review import reviewed_question
    question = reviewed_question(question)
    if multi_cloze.is_cloze_question(question):
        reason = question.get('assessment_blocked_reason')
        if isinstance(reason, str) and reason:
            return {'status': 'unresolved', 'correct': None, 'message': reason, 'scorable': False}
        return multi_cloze.grade_answer(question, answer)
    if pitch_practice.is_pitch_question(question):
        reason = question.get("assessment_blocked_reason")
        if isinstance(reason, str) and reason:
            return {"status": "unresolved", "correct": None, "message": reason, "scorable": False}
        return pitch_practice.grade_answer(question, answer)
    if isinstance(answer, (list, tuple)):
        answer = "".join(map(str, answer))
    if question["kind"] == "vocab_reading":
        normal = _reading_normal
    elif (question.get("family") == "grammar" and question.get("domain") != "pronunciation"
          and question.get("input_mode") in {"text", "reorder"}):
        normal = lambda value: _normal(_grammar_kana(str(value)))
    else:
        normal = _normal
    value = normal(answer)
    if not value:
        return {"status": "empty", "correct": None, "message": "请先作答。", "scorable": False}
    reason = question.get("assessment_blocked_reason")
    if isinstance(reason, str) and reason:
        return {"status": "unresolved", "correct": None, "message": reason, "scorable": False}
    if value in {_normal(x) for x in question.get("retired_ambiguous_answers", [])}:
        return {"status": "unresolved", "correct": None,
                "message": "旧题的这个选项也可成立，不能作为错误答案。本次不调整复习间隔。", "scorable": False}
    if (question["kind"] == "vocab_reading"
            and normal(question["prompt"]) == normal(question["answer"])):
        # Keep old pending snapshots intact, but copying a visible kana reading
        # cannot establish recall or change its stored review interval.
        return {"status": "unresolved", "correct": None,
                "message": "题目已显示假名读法，本题不计入复习。", "scorable": False}
    accepted = {normal(x) for x in question.get("accepted_answers", [question["answer"]])}
    if question.get('personal_rubric', {}).get('method') == 'reviewed-exact-rubric-v1':
        # These are explicitly reviewed alternatives for this exact target and
        # context, separate from dictionary readings for another usage. The
        # store still rederives eligibility before granting scheduling credit.
        from .personal_question import grade
        return grade(question, value, normal)
    if question["kind"] == "vocab_reading":
        target = normal(question["answer"])
        permitted = {normal(x) for x in allowed_word_spellings(question["answer"])}
        if value in permitted:
            return {"status": "correct", "correct": True, "message": question.get("feedback", "正确。"),
                    "scorable": True, "accepted_spelling": value != target}
        if same_kana_sound(target, value, fold_script=True):
            # This ability tracks recalling the reading. A same-sound letter
            # error does not establish forgetting that reading; preserve its
            # due date while keeping an explicit spelling diagnosis.
            return {"status": "unresolved", "correct": None, "scorable": False,
                    "error_category": "spelling", "error_type": "orthography",
                    "reading_correct": True, "spelling_correct": False,
                    "message": "读音对应，但这个词的假名写法不同。本次不调整读音复习。"}
        # Existing immutable snapshots may still include other dictionary
        # readings as accepted answers. Recognize them without awarding the
        # selected reading's ability, and never rewrite a saved draft/queue.
        alternatives = {normal(x) for x in question.get("alternative_answers", [])}
        alternatives.update(accepted - {target})
        if value != target and value in alternatives:
            return {"status": "unresolved", "correct": None,
                    "message": "这是词典收录的其他读法，可能限于其他语境；未检验本课目标读法。本次不计入复习。",
                    "scorable": False}
    if question["kind"] in {"vocab_recall", "vocab_orthography"}:
        target = normal(question["answer"])
        kana_targets = [candidate for candidate in accepted if same_kana_sound(candidate, candidate)]
        # Keep reviewed kanji/other expression alternatives, but never treat a
        # legacy same-sound spelling as permission to write a different word.
        if same_kana_sound(target, target):
            kana_targets = [target]
            accepted = {candidate for candidate in accepted
                        if not same_kana_sound(target, candidate, fold_script=True)}
        permitted = {normal(form) for candidate in kana_targets
                     for form in allowed_word_spellings(candidate)}
        accepted.update(permitted)
        if value in permitted:
            return {"status": "correct", "correct": True, "message": question.get("feedback", "正确。"),
                    "scorable": True, "accepted_spelling": value not in kana_targets}
        if any(same_kana_sound(candidate, value, fold_script=True) for candidate in kana_targets):
            return {"status": "incorrect", "correct": False, "scorable": True,
                    "error_category": "spelling", "error_type": "orthography",
                    "reading_correct": True, "spelling_correct": False,
                    "message": "读音对应，但这个词的写法不同。"}
    if value in accepted:
        return {"status": "correct", "correct": True, "message": question.get("feedback", "正确。"), "scorable": True}
    known_wrong = {normal(x) for x in question.get("known_wrong", [])}
    if question.get("strict") or value in known_wrong:
        result = {"status": "incorrect", "correct": False, "message": question.get("feedback", "请核对答案。"), "scorable": True}
        if question["kind"] == "vocab_reading":
            result.update(error_category="reading", reading_correct=False, spelling_correct=False)
        return result
    return {"status": "unresolved", "correct": None,
            "message": "你的表达可能有其它合理写法。本题暂不计入复习，请与示例核对。", "scorable": False}
