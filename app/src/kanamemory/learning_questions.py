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

from .content_pack import (load_namespace, QUESTION_DEPENDENCIES, DEPENDENCY_ID_FIELDS,
                           validate_question_dependency_requirements)
from .learning_content import get_learning_item
from . import pitch_practice
from . import multi_cloze

_CONTENT = load_namespace("learning_questions", {})
_PRACTICE = load_namespace("varied_practice", {})
_SOUND_MIX = load_namespace("word_sound_mix", {})
# Reviewed Chinese-free context choice; its own namespace keeps the size of
# varied_practice and every existing template position unchanged.
_CONTEXT_CHOICE = load_namespace("context_choice", {})
# Optional per-template dependency sets keyed by raw template sha256 (design
# 11 section 5). Absent in demo builds and in packs built before WP-C1.
_DEPENDENCIES = load_namespace(QUESTION_DEPENDENCIES, {})
# An out-of-vocabulary surface counts as one unknown item. The token holds a
# NUL so that no content id (and therefore no known set) can ever contain it.
OOV_TOKEN_PREFIX = "\x00oov:"
from . import word_sound_questions as sound_questions
from .question_kinds import REGISTRY as KIND_REGISTRY
from .question_kinds import vocab_sound as _vocab_sound

# Kind tables are derived from the question-kind registry (question_kinds/builtin);
# names, values and order are unchanged and pinned by test_question_kind_registry.
AUDIO_WORD_KIND = sound_questions.AUDIO_WORD
READING_CHOICE_KIND = sound_questions.READING_CHOICE
SOUND_KINDS = KIND_REGISTRY.by_template_source("sound")
# Kinds whose templates a provider derives from lesson fields alone, so they
# also exist for lessons without reviewed templates (today: SOUND_KINDS).
_GENERATED_KINDS = KIND_REGISTRY.generated()
CONTEXT_CHOICE_KIND = KIND_REGISTRY.by_template_source("context_choice")[0]
# Practice and Tutor only: a retention check prefers choices, so these would
# silently replace what a check measures.
PRACTICE_ONLY_KINDS = KIND_REGISTRY.practice_only()
AUTHORED_WORD_KINDS = KIND_REGISTRY.by_family("word", template_source="authored")
WORD_KINDS = KIND_REGISTRY.by_family("word")
GRAMMAR_KINDS = KIND_REGISTRY.by_family("grammar")
PRONUNCIATION_KINDS = KIND_REGISTRY.by_family("pronunciation")
KIND_LABELS = KIND_REGISTRY.labels()
EXPERIMENTAL_KINDS = KIND_REGISTRY.experimental()
# Heard-meaning tasks stay opt-in. The heard-word task is on by default but still
# needs a working voice, which the UI checks through audio_ready_kinds.
OPT_IN_LISTENING_KINDS = KIND_REGISTRY.opt_in_listening()
LISTENING_KINDS = KIND_REGISTRY.listening()
TEXT_KINDS = KIND_REGISTRY.text()
DEFAULT_KINDS = KIND_REGISTRY.default_kinds()
SOUND_PREFERENCE_VERSION = "sound_kinds_version"
# Kinds a saved list may not have known about, by the version that added them.
KINDS_ADDED_AT = KIND_REGISTRY.added_at()
PREFERENCE_VERSION = max(KINDS_ADDED_AT)
KIND_ABILITIES = KIND_REGISTRY.abilities()
# Cross-family snapshot rules restated as registry spec flags (question_kinds/
# builtin); their former literal values are pinned by test_question_kind_registry.
SAFE_SUPPORT_KINDS = KIND_REGISTRY.safe_support()
BACKGROUND_SUPPORT_KINDS = KIND_REGISTRY.background_support()
ORDER_TRANSLATION_SOURCES = KIND_REGISTRY.order_translation_sources()
DICTIONARY_LESSON_KINDS = KIND_REGISTRY.dictionary_lesson()
READS_WRITTEN_FORM_KINDS = KIND_REGISTRY.reads_written_form()


def stored_enabled_kinds(raw):
    """Enabled kinds from the saved setting, before any device capability check.

    A list saved before a kind existed gains it once: leaving it out then was
    not a choice. Saving the settings page records the current version, so
    switching a kind off afterwards stays off. An empty list was a choice to
    practise nothing, and a malformed value stays malformed for its caller to
    refuse. Reading never writes the setting.
    """
    if not isinstance(raw, dict):
        return []
    if "enabled" not in raw:
        return list(DEFAULT_KINDS)
    enabled = raw["enabled"]
    saved = raw.get(SOUND_PREFERENCE_VERSION)
    saved = saved if type(saved) is int else 0
    if not isinstance(enabled, list) or not enabled or saved >= PREFERENCE_VERSION:
        return enabled
    added = [kind for version, kinds in sorted(KINDS_ADDED_AT.items()) if version > saved for kind in kinds]
    return list(dict.fromkeys([*enabled, *added]))


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
    # Each kind's sentence source is its spec's order_translation_source.
    source = ORDER_TRANSLATION_SOURCES.get(kind)
    if multi_cloze.is_cloze_question(question):
        text = multi_cloze.answer_label(question)
        field = "answer"
    elif source == "gap":
        prompt, answer = question.get("prompt"), question.get("answer")
        if not isinstance(prompt, str) or not isinstance(answer, str) or prompt.count("（　）") != 1:
            return None
        text = prompt.replace("（　）", answer)
    elif source == "answer":
        # The stimulus may deliberately be wrong or contain only shuffled
        # pieces. A complete canonical answer is the only useful source here.
        field, text = "answer", question.get("answer")
    elif source == "prompt":
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


def _practice_binding(item, source=None):
    from .material_review import item_fingerprint
    source = _PRACTICE if source is None else source
    if item.get("ai_generated") or source.get("schema_version") != 1:
        return None
    extension = source.get("items", {}).get(item.get("id"), {})
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


def template_fingerprint(raw):
    """The raw template's sha256: the key of retirements and question_dependencies."""
    return _template_fingerprint(raw)


def _namespace_covers(content_id):
    """The v1 question_dependencies namespace has an entry for this item."""
    return (isinstance(content_id, str) and _DEPENDENCIES.get("schema_version") == 1
            and isinstance(_DEPENDENCIES.get("items", {}).get(content_id), dict))


def _declared_requirements(content_id, kind, raw):
    """The question_dependencies entry of one raw template, or None when absent.

    A malformed entry is an invalid pack (ValueError), never a silent pass.
    """
    if not isinstance(content_id, str) or _DEPENDENCIES.get("schema_version") != 1:
        return None
    entry = _DEPENDENCIES.get("items", {}).get(content_id)
    templates = entry.get(kind) if isinstance(entry, dict) else None
    if not isinstance(templates, dict):
        return None
    try:
        requirements = templates.get(_template_fingerprint(raw))
    except (TypeError, ValueError):
        return None  # A built legacy question, not a stored raw template.
    if requirements is None:
        return None
    validate_question_dependency_requirements(requirements, target_id=content_id)
    ids = {cid for field in DEPENDENCY_ID_FIELDS for cid in requirements.get(field, ())}
    ids.update(OOV_TOKEN_PREFIX + surface for surface in requirements.get("oov", ()))
    return ids


def template_requires(item, kind, raw):
    """Content a raw template needs besides its target, or None when undeclared.

    The union of the template's reviewed practice.background_ids and its
    question_dependencies entry (design 11 section 5, D1-D3); "oov" surfaces
    become opaque tokens that no known set contains, so they stay unknown. The
    target item is never required. None means nothing is declared: such a
    template is a fallback question (eligible only when no declared template
    is), not a gated one. Selection only: snapshots and history are unchanged.
    """
    content_id = item.get("id") if isinstance(item, dict) else None
    if kind in SOUND_KINDS:
        # Generated sound tasks need only their target: the options are other
        # written forms or kana, and with a known set they are drawn from known
        # words (sound_variants). They are declared (and always met) wherever
        # the pack's namespace covers the item, else undeclared as before.
        return frozenset() if _namespace_covers(content_id) else None
    declared = _declared_requirements(content_id, kind, raw) if isinstance(raw, dict) else None
    practice = raw.get("practice") if isinstance(raw, dict) else None
    background = practice.get("background_ids") if isinstance(practice, dict) else None
    if declared is None and not isinstance(background, list):
        return None
    result = set(declared or ())
    if isinstance(background, list):
        result.update(cid for cid in background if isinstance(cid, str))
    result.discard(content_id)
    return frozenset(result)


def question_template(item, question, *, apply_review=True):
    """(lesson, raw template) of a built question, located by kind and variant; None without one.

    A frozen reviewed task (practice_source) is matched by its template
    sha256; legacy builder questions have no template.
    """
    target = _item(item)
    if apply_review:
        from .material_review import reviewed_item
        target = reviewed_item(target)
    kind, variant = question.get("kind"), question.get("variant")
    templates = practice_templates(target)
    variants = templates.get(kind) if isinstance(templates, dict) else None
    if not isinstance(variants, list) or not variants or type(variant) is not int or variant < 0:
        return None
    try:
        raw = variant_template(target, kind, variant, templates)
    except (ValueError, KeyError, TypeError):
        return None
    source = question.get("practice_source")
    if isinstance(source, dict) and isinstance(source.get("template_sha256"), str):
        raw = next((candidate for candidate in variants
                    if _template_fingerprint(candidate) == source["template_sha256"]), raw)
    return target, raw


def question_requires(item, question, *, apply_review=True):
    """template_requires for a built question; legacy builder questions without templates declare nothing."""
    located = question_template(item, question, apply_review=apply_review)
    return None if located is None else template_requires(located[0], question.get("kind"), located[1])


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


COGNATE_CLASSES = ("S", "P", "D", "E")


def cognate_class(item):
    """The reviewed cognate class (S/P/D/E) of a bundled word, else None.

    word_sound_mix.cognate_words binds id, title and reading, so an edited
    word has no class. A listed entry without a class is reported as "P",
    the rule every listed word followed before classes existed.
    """
    entry = _SOUND_MIX.get("cognate_words", {}).get(item.get("id")) if isinstance(item, dict) else None
    if (not isinstance(entry, dict) or entry.get("title") != item.get("title")
            or entry.get("reading") != item.get("reading")):
        return None
    value = entry.get("class", "P")
    if value not in COGNATE_CLASSES:
        raise ValueError("Invalid cognate class")
    return value


def _cognate_mapping_variants(item, templates):
    """Word-to-Chinese tasks that a shared written form makes nearly automatic.

    Design 11 section 4 (S2-S4), by the word's cognate class:
    S (transparent same form): every vocab_meaning task but one bare one (the
    word alone, no practice metadata; without one, the first-ranked task, which
    prefers needing no other word, so understanding never starves on this
    rule) and every vocab_recall task except base-form recall from a sentence;
    P (and a listed word without a class): two meaning tasks that need no other
    known word, and the same recall rule; D and E: nothing. Base-form recall stays: it tests the form, not a
    translation. For S and P, meaning tasks whose practice family is
    KANA_FORM_MEANING always stay: they write the word in kana (alone or
    inside a sentence), so the meaning must come from the reading, not the
    shared characters (design 11 section 4, S2); when an S word has one, its
    kanji-prompt bare task is retired as a copy task, and a P word keeps one
    sentence task beside them. Selection only: saved questions, drafts and
    history still rebuild as before.
    """
    group = cognate_class(item)
    if group in (None, "D", "E"):
        return ()
    meaning = templates.get("vocab_meaning", [])
    kana_form = {index for index, raw in enumerate(meaning) if _kana_form_meaning(raw)}
    recall = [("vocab_recall", i) for i, raw in enumerate(templates.get("vocab_recall", []))
              if not (isinstance(raw, dict) and "基本形是" in str(raw.get("task", "")))]
    def keep_order(pair):
        practice = pair[1].get("practice") if isinstance(pair[1], dict) else None
        return (bool(practice and practice.get("background_ids")), not practice, pair[0])
    # A bare base task always exists and needs nothing, so the second place goes to it.
    ranked = [index for index, _ in sorted(enumerate(meaning), key=keep_order)]
    if group == "S":
        # With a kana-form task the kanji-prompt bare task (学生 -> 学生) is a
        # copy task and goes too; without one it stays so understanding never starves.
        bare = [index for index, raw in enumerate(meaning) if isinstance(raw, dict)
                and not raw.get("practice") and raw.get("prompt") == item.get("title")]
        kept = kana_form or set(bare[:1] or ranked[:1])
        return [*(("vocab_meaning", i) for i in range(len(meaning)) if i not in kept), *recall]
    bare = [index for index in ranked if not (isinstance(meaning[index], dict) and meaning[index].get("practice"))]
    if kana_form:
        # P: the kana-form tasks plus one sentence task; no kanji-prompt bare task.
        sentence = [index for index in ranked if index not in bare and index not in kana_form]
        kept = kana_form | set(sentence[:1])
    else:
        kept = {*ranked[:1], *bare[:1]} if bare else set(ranked[:2])
    return [*(("vocab_meaning", i) for i in range(len(meaning)) if i not in kept), *recall]


# Practice family of a meaning task that writes the target in kana inside a
# Japanese sentence (design 11 section 4, S2); kept for cognate words.
KANA_FORM_MEANING = "kana_form_meaning"


def _kana_form_meaning(raw):
    practice = raw.get("practice") if isinstance(raw, dict) else None
    return isinstance(practice, dict) and practice.get("task_family") == KANA_FORM_MEANING


def _template_namespace(name):
    """Separately loaded template namespaces, by a spec's template_source.

    Content loading stays here (looked up at call time); kinds whose source is
    not listed bind through varied_practice.
    """
    return {"context_choice": _CONTEXT_CHOICE}.get(name) if isinstance(name, str) else None


def _kind_source(kind):
    spec = KIND_REGISTRY.get(kind) if isinstance(kind, str) else None
    return None if spec is None else _template_namespace(spec.template_source)


class _TemplateEnv:
    """question_kind_registry.BuildEnv for template providers and builders."""

    @staticmethod
    def word_pool():
        return _word_pool()

    @staticmethod
    def practice_binding(item, namespace):
        source = _template_namespace(namespace)
        return None if source is None else _practice_binding(item, source)

    @staticmethod
    def legacy_word_base(item):
        return _legacy_word_base(item)

    @staticmethod
    def legacy_word_lessons(item):
        return _legacy_word_lessons(item)

    @staticmethod
    def content(name, default):
        return deepcopy(_CONTENT.get(name, default))

    @staticmethod
    def reading_normal(text):
        return _reading_normal(text)

    @staticmethod
    def legacy_grammar_variants(item, kind):
        return deepcopy(_CONTENT.get("GRAMMAR_QUESTIONS", {}).get(item["id"], {}).get(kind, []))


_TEMPLATE_ENV = _TemplateEnv()


def _practice_source(item, kind, raw):
    binding = _practice_binding(item, _kind_source(kind))
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
    kind = question.get("kind")
    binding = _practice_binding(item, _kind_source(kind))
    if binding is None or binding["item_fingerprint"] != source["item_fingerprint"]:
        raise ValueError("Frozen practice lesson binding is unavailable")
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
    # The kind's own template namespace (context choice), else varied_practice.
    source_pack = _kind_source(kind)
    record = (_PRACTICE if source_pack is None else source_pack).get("items", {}).get(cid, {})
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
    from .learning_content import learning_item_views
    return sound_questions.word_pool(learning_item_views("word"))


def _provided_templates(item, spec):
    return spec.template_provider(item, spec, _TEMPLATE_ENV)


def _sound_templates(item, kind):
    """Generated tasks (question_kinds/vocab_sound); never AI or custom text."""
    return _provided_templates(item, KIND_REGISTRY.get(kind))


def sound_variants(item, kind, known_ids=None):
    """(variant, raw template) pairs of a generated sound kind for one learner.

    Without a known set (None: tools, the pack builder) these are the bank
    variants. With one (design 11 section 5, WP-K2) the options come only from
    known words. A bank-equal variant keeps its number; any other carries an
    encoded variant that build_learning_question rebuilds from the lesson
    alone, so snapshots and canonical rebuilds never need the known set.

    With fewer than three known candidates the heard-word kind has no variant
    yet ([]: it waits until three of its candidates are known; the options
    are written words and would otherwise be words never taught), while the
    reading-choice kind keeps the bank variants, whose options are kana.
    The start is the one exception: a learner who knows fewer than three
    words at all has nothing to choose known options from, and a learner whose
    only word kind is this one could then never begin a first word; until the
    third word is known the heard-word kind therefore uses its bank.

    The heard-word task's kana-spelling variants of 0.34.0 are withdrawn
    (2026-11-12, owner: hearing a word and picking among kana spellings tests
    no mapping between how a word is written and how it sounds). They are
    never selected; a stored one is still rebuilt by variant_template, and
    withdrawn_task names it so a queued one can be replaced.
    """
    bank = _sound_templates(item, kind) if kind in SOUND_KINDS else []
    if not bank or known_ids is None:
        return list(enumerate(bank))
    spec = KIND_REGISTRY.get(kind)
    known = {value for value in known_ids if isinstance(value, str)} - {item.get("id")}
    chosen = (None if len(known) < sound_questions.OPTION_COUNT - 1
              else _vocab_sound.known_variants(item, spec, _TEMPLATE_ENV, known))
    if chosen is None:
        if kind == sound_questions.AUDIO_WORD and _known_words(known) >= sound_questions.OPTION_COUNT - 1:
            return []
        return list(enumerate(bank))
    return chosen


def _known_words(known):
    """How many course words the learner knows (the target is not in ``known``), counted up to three."""
    count = 0
    for row in _TEMPLATE_ENV.word_pool():
        if row[0] in known:
            count += 1
            if count >= sound_questions.OPTION_COUNT - 1:
                break
    return count


def withdrawn_task(question):
    """A task form the app no longer asks: the heard-word kana-spelling variant (see sound_variants)."""
    return bool(isinstance(question, dict) and question.get("kind") == sound_questions.AUDIO_WORD
                and sound_questions.is_kana_variant(question.get("variant")))


def _known_sound_template(item, kind, variant):
    """The template of an encoded sound variant (known-word positions or a kana-spelling number)."""
    return _vocab_sound.variant_template(item, KIND_REGISTRY.get(kind), _TEMPLATE_ENV, variant)


def variant_template(item, kind, variant, templates=None):
    """The raw template a (kind, variant) plan names, as build_learning_question uses it.

    Encoded sound variants (known-word and kana-spelling) are rebuilt from the
    lesson; every other variant indexes the kind's templates (pass
    practice_templates(item) to reuse it).
    """
    if kind in SOUND_KINDS and sound_questions.is_known_variant(variant):
        return _known_sound_template(item, kind, variant)
    templates = practice_templates(item) if templates is None else templates
    variants = templates[kind]
    return variants[variant % len(variants)]


def practice_templates(item):
    """Reviewed tasks for an exact lesson, plus each provider kind's tasks.

    Provider kinds (sound, context choice) follow in registration order.
    """
    result = _reviewed_templates(item)
    if result is None:
        return None
    for spec in KIND_REGISTRY.with_template_provider():
        variants = _provided_templates(item, spec)
        if variants:
            result[spec.kind] = variants
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


NO_TEMPLATES = object()  # "not given": None is a real answer (a lesson without reviewed templates)


def question_kinds(content_id, dimension=None, *, ability=None, item=None, templates=NO_TEMPLATES):
    """The kinds this lesson can be asked in (optionally of one ability).

    ``templates``: practice_templates of this very lesson, when the caller
    already has them. Planning one lesson asks for its kinds once per ability
    and for its templates again each time; each of those copied the whole
    lesson and rebuilt every reviewed and generated task. With the templates
    given the lesson is only read.
    """
    if templates is NO_TEMPLATES:
        target = _item(content_id, item)
        templates = practice_templates(target)
    else:
        target = item or content_id
    if templates is not None:
        allowed = (PRONUNCIATION_KINDS if target.get("domain") == "pronunciation" else
                   WORD_KINDS if target["family"] == "word" else GRAMMAR_KINDS)
        kinds = [kind for kind in allowed if templates.get(kind)]
    elif target.get("domain") == "pronunciation":
        # Pronunciation has no inferred exercises or guessed distractors.
        kinds = []
    else:
        kinds = DICTIONARY_LESSON_KINDS if target.get("dictionary_only") else (
            AUTHORED_WORD_KINDS if target["family"] == "word" else GRAMMAR_KINDS)
        kinds = (*kinds, *(kind for kind in _GENERATED_KINDS if _sound_templates(target, kind)))
    wanted = ability or dimension
    copy_reading = (target["family"] == "word" and target.get("reading")
                    and _reading_normal(target["title"]) == _reading_normal(target["reading"]))
    return [k for k in kinds if (wanted is None or KIND_ABILITIES[k] == wanted)
            and not (copy_reading and k in READS_WRITTEN_FORM_KINDS)]


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


_LATIN = re.compile(r"[A-Za-z]")
# Particle kana typed by sound (wa/o/e) instead of the particle spelling.
_PARTICLE_SWAPS = (("わ", "は", "particle_ha_typed_wa"), ("お", "を", "particle_wo_typed_o"),
                   ("え", "へ", "particle_he_typed_e"))
_PARTICLE_SWAP_LIMIT = 8


def _unresolved_reason(question, answer, value, normal, accepted):
    """Diagnose a non-strict mismatch; an added code, never a new status."""
    raw = unicodedata.normalize("NFKC", str(answer))
    expected = [str(x) for x in question.get("accepted_answers", [question.get("answer", "")])]
    if _LATIN.search(raw) and not any(_LATIN.search(unicodedata.normalize("NFKC", x)) for x in expected):
        return "latin_in_kana"
    swaps = {typed: (particle, code) for typed, particle, code in _PARTICLE_SWAPS}
    positions = [index for index, char in enumerate(value) if char in swaps][:_PARTICLE_SWAP_LIMIT]
    for mask in range(1, 1 << len(positions)):
        chars, used = list(value), set()
        for bit, index in enumerate(positions):
            if mask >> bit & 1:
                particle, code = swaps[chars[index]]
                chars[index] = particle
                used.add(code)
        if normal("".join(chars)) in accepted:
            return next(code for _, _, code in _PARTICLE_SWAPS if code in used)
    return "other_form"


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
    if guided or kind not in BACKGROUND_SUPPORT_KINDS:
        return None
    authored = raw.get("readings")
    explicit = _support(prompt, target, True, authored)["segments"] if isinstance(authored, dict) else []
    return background_support(prompt, target, raw, kind, explicit=explicit,
        bundled=_bundled_teaching_target(target), guided=guided)


def _legacy_word_lessons(item):
    # This path serves the original four lessons and conservative dictionary
    # recall. Expanded lessons own reviewed templates; cloning their full
    # catalogue just to obtain three old distractors is unnecessary work.
    return [] if item.get("dictionary_only") else [value for key in _CONTENT.get("LEGACY_WORD_IDS", ())
        if (value := get_learning_item(key)) is not None]


def _legacy_word_base(item):
    """The shared legacy word question; kind builders update a fresh copy."""
    # Every legacy word question needs these fields; a missing one fails here.
    form, _reading, meaning, _instruction = (item["title"], item["reading"], item["meaning"],
                                             item["instruction"])
    other_meanings = [x["meaning"] for x in _legacy_word_lessons(item)
                      if x["id"] != item["id"] and x["meaning"] != meaning]
    return dict(prompt=form, task="这个词在本课中的意思是？", answer=meaning,
                choices=[meaning, *other_meanings[:3]], feedback=meaning, safe=True)


def _word_question(item, kind, variant):
    if kind in _GENERATED_KINDS:
        if kind in SOUND_KINDS and sound_questions.is_known_variant(variant):
            return _known_sound_template(item, kind, variant)
        variants = _sound_templates(item, kind)
        if not variants:
            raise ValueError("No sound task is available for this word")
        return variants[variant % len(variants)]
    # Authored kinds build from the lesson's own fields in their family module
    # (question_kinds/vocab_choice, vocab_text).
    spec = KIND_REGISTRY.get(kind)
    if spec is None or spec.builder is None:
        return _legacy_word_base(item)
    return spec.builder(item, spec, _TEMPLATE_ENV, variant)


def _grammar_question(item, kind, variant):
    # Grammar lessons without reviewed templates use their original authored
    # variants through the family builder (question_kinds/grammar_meaning).
    spec = KIND_REGISTRY.get(kind)
    if spec is None or spec.builder is None:
        raise ValueError("No reviewed question templates are available")
    return spec.builder(item, spec, _TEMPLATE_ENV, variant)


def build_learning_question(content_id, kind=None, *, variant=0, guided=False, item=None, apply_review=True,
                            _practice_template=None):
    target = _item(content_id, item)
    if apply_review:
        from .material_review import reviewed_item
        target = reviewed_item(target)
    if target.get("instruction_status") == "instruction_missing":
        raise ValueError("Learning material is not ready for this reading/sense")
    # One table for this build: its kinds are read from it (asking for the kinds alone copied the lesson
    # and built the same table a second time for every question).
    templates = practice_templates(target)
    kinds = question_kinds(target, templates=templates)
    if not kinds:
        raise ValueError("No reviewed question templates are available")
    kind = kind or kinds[variant % len(kinds)]
    if kind not in kinds:
        raise ValueError(f"Question kind {kind!r} is not available for {target['id']}")
    if templates is not None:
        raw = deepcopy(variant_template(target, kind, variant, templates))
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
    safe = bool(raw.get("safe") and mode == "choice" and not guided and kind in SAFE_SUPPORT_KINDS)
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
    # Kind-specific snapshot fields (question_kinds/pronunciation: every task of
    # a pronunciation lesson tests a reading, so it drops audio and names roles).
    finisher = KIND_REGISTRY.get(kind).snapshot_finisher
    if finisher is not None:
        finisher(result, raw, target)
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


_REORDER_BLANK = re.compile(r"＿{2,}|_{2,}")  # one blank is one run of underscores, however long


def reorder_cue(question) -> str:
    """The first block a cued reorder prompt already shows (李さんは ＿＿ ＿＿), else "".

    Only when the shown text is one of the blocks and the prompt has exactly one
    blank per remaining block: then the blanks ask for the other blocks alone.
    A blank is a run of two or more underscores (＿＿＿＿ is one blank, not two).
    """
    prompt, choices = question.get("prompt"), question.get("choices")
    if question.get("input_mode") != "reorder" or not isinstance(prompt, str) or not isinstance(choices, list):
        return ""
    blanks = _REORDER_BLANK.findall(prompt)
    head = _REORDER_BLANK.split(prompt, maxsplit=1)[0].strip() if blanks else ""
    return head if head and head in choices and len(blanks) == len(choices) - 1 else ""


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
    # Kind-specific grading steps (question_kinds/vocab_text); None is generic.
    kind = question["kind"]
    spec = KIND_REGISTRY.get(kind) if isinstance(kind, str) else None
    grader = None if spec is None else spec.grader
    if grader is not None and grader.normalizer == "reading":
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
    if grader is not None and grader.visible_answer is not None:
        visible = grader.visible_answer(question, normal)
        if visible is not None:
            return visible
    accepted = {normal(x) for x in question.get("accepted_answers", [question["answer"]])}
    cue = reorder_cue(question)
    if cue:
        # The prompt already shows the first block: the remaining blocks in order are the same answer
        # as tapping the shown block first (0.34.0 native retest, D3).
        accepted |= {normal(x[len(cue):]) for x in question.get("accepted_answers", [question["answer"]])
                     if isinstance(x, str) and x.startswith(cue) and x[len(cue):].strip()}
    if question.get('personal_rubric', {}).get('method') == 'reviewed-exact-rubric-v1':
        # These are explicitly reviewed alternatives for this exact target and
        # context, separate from dictionary readings for another usage. The
        # store still rederives eligibility before granting scheduling credit.
        from .personal_question import grade
        return grade(question, value, normal)
    if grader is not None and grader.match is not None:
        matched, accepted = grader.match(question, value, normal, accepted)
        if matched is not None:
            return matched
    if value in accepted:
        return {"status": "correct", "correct": True, "message": question.get("feedback", "正确。"), "scorable": True}
    known_wrong = {normal(x) for x in question.get("known_wrong", [])}
    if question.get("strict") or value in known_wrong:
        result = {"status": "incorrect", "correct": False, "message": question.get("feedback", "请核对答案。"), "scorable": True}
        if grader is not None:
            result.update(grader.incorrect_fields)
        return result
    return {"status": "unresolved", "correct": None,
            "message": "你的表达可能有其它合理写法。本题暂不计入复习，请与示例核对。", "scorable": False,
            "unresolved_reason": _unresolved_reason(question, answer, value, normal, accepted)}
