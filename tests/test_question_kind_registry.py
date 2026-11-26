"""Question-kind registry invariants and the frozen pre-registry kind tables.

The snapshot below is copied literally from learning_questions.py before the
kind tables were derived from question_kinds (WP2). It must not be edited to
follow a code change: a difference means the refactor changed behaviour.
The one exception is a new kind, added by its own decision: grammar_audio_meaning
(0.36.26, preference version 3) is the only kind added since the snapshot.
"""
import pytest

from kanamemory import learning_questions as lq
from kanamemory.question_kind_registry import (KNOWN_ABILITIES, QuestionKindSpec, Registry,
                                               is_ignorable)
from kanamemory.question_kinds import REGISTRY

FROZEN_AUTHORED_WORD_KINDS = ("vocab_meaning", "vocab_audio_meaning", "vocab_reading", "vocab_recall",
                              "vocab_orthography", "vocab_usage", "vocab_zh_ja_contrast",
                              "reading_comprehension", "listening_comprehension")
FROZEN_SOUND_KINDS = ("vocab_audio_word", "vocab_reading_choice")
FROZEN_CONTEXT_CHOICE_KIND = "vocab_context_choice"
FROZEN_PRACTICE_ONLY_KINDS = ("vocab_audio_word", "vocab_reading_choice", "vocab_context_choice")
FROZEN_WORD_KINDS = (*FROZEN_AUTHORED_WORD_KINDS, "vocab_audio_word", "vocab_reading_choice",
                     "vocab_context_choice")
FROZEN_LEGACY_GRAMMAR_KINDS = ("grammar_meaning", "grammar_cloze", "grammar_transform",
                               "grammar_reorder", "targeted_correction", "controlled_sentence_production")
FROZEN_GRAMMAR_KINDS = (*FROZEN_LEGACY_GRAMMAR_KINDS, "grammar_audio_meaning")
FROZEN_PRONUNCIATION_KINDS = ("pronunciation_rule", "pronunciation_application")
FROZEN_KIND_LABELS = {
    "vocab_meaning": "看词辨义", "vocab_audio_meaning": "听词辨义",
    "vocab_reading": "写出读法", "vocab_recall": "根据含义想词",
    "vocab_orthography": "选择写法", "vocab_usage": "语境选词",
    "vocab_zh_ja_contrast": "中日词义辨析", "reading_comprehension": "短文理解",
    "listening_comprehension": "短句听力", "grammar_meaning": "语法含义",
    "grammar_cloze": "语法填空", "grammar_transform": "变换词形",
    "grammar_reorder": "排列句子", "targeted_correction": "修正一处错误",
    "controlled_sentence_production": "按要求造句",
    "pronunciation_rule": "读音规则辨析", "pronunciation_application": "读音规则应用",
    "vocab_audio_word": "听词选词", "vocab_reading_choice": "看词选读音",
    "vocab_context_choice": "读句选词", "grammar_audio_meaning": "听句辨义",
}
FROZEN_EXPERIMENTAL_KINDS = ("free_writing_translation", "situational_dialogue",
                             "pitch_discrimination", "shadowing_read_aloud", "spoken_recall")
FROZEN_OPT_IN_LISTENING_KINDS = {"vocab_audio_meaning", "listening_comprehension", "grammar_audio_meaning"}
FROZEN_LISTENING_KINDS = {"vocab_audio_meaning", "listening_comprehension", "vocab_audio_word",
                          "grammar_audio_meaning"}
FROZEN_TEXT_KINDS = {"vocab_reading", "vocab_recall", "grammar_transform",
                     "targeted_correction", "controlled_sentence_production"}
FROZEN_DEFAULT_KINDS = (
    "vocab_meaning", "vocab_reading", "vocab_recall", "vocab_orthography", "vocab_usage",
    "vocab_zh_ja_contrast", "reading_comprehension", "vocab_audio_word", "vocab_reading_choice",
    "vocab_context_choice", "grammar_meaning", "grammar_cloze", "grammar_transform",
    "grammar_reorder", "targeted_correction", "controlled_sentence_production",
    "pronunciation_rule", "pronunciation_application")
FROZEN_SOUND_PREFERENCE_VERSION = "sound_kinds_version"
FROZEN_KINDS_ADDED_AT = {1: ("vocab_audio_word", "vocab_reading_choice"), 2: ("vocab_context_choice",),
                         3: ("grammar_audio_meaning",)}
FROZEN_PREFERENCE_VERSION = 3
FROZEN_KIND_ABILITIES = {
    "vocab_meaning": "understanding", "vocab_audio_meaning": "listening",
    "vocab_reading": "reading", "vocab_recall": "use", "vocab_orthography": "use",
    "vocab_usage": "use", "vocab_zh_ja_contrast": "understanding",
    "reading_comprehension": "understanding", "listening_comprehension": "listening",
    "grammar_meaning": "understanding", "grammar_cloze": "application",
    "grammar_transform": "application", "grammar_reorder": "application",
    "targeted_correction": "application", "controlled_sentence_production": "application",
    "pronunciation_rule": "understanding", "pronunciation_application": "application",
    "vocab_audio_word": "listening", "vocab_reading_choice": "reading",
    "vocab_context_choice": "use", "grammar_audio_meaning": "understanding",
}


@pytest.mark.parametrize("name, expected", [
    ("AUTHORED_WORD_KINDS", FROZEN_AUTHORED_WORD_KINDS),
    ("SOUND_KINDS", FROZEN_SOUND_KINDS),
    ("AUDIO_WORD_KIND", "vocab_audio_word"),
    ("READING_CHOICE_KIND", "vocab_reading_choice"),
    ("CONTEXT_CHOICE_KIND", FROZEN_CONTEXT_CHOICE_KIND),
    ("PRACTICE_ONLY_KINDS", FROZEN_PRACTICE_ONLY_KINDS),
    ("WORD_KINDS", FROZEN_WORD_KINDS),
    ("GRAMMAR_KINDS", FROZEN_GRAMMAR_KINDS),
    ("PRONUNCIATION_KINDS", FROZEN_PRONUNCIATION_KINDS),
    ("EXPERIMENTAL_KINDS", FROZEN_EXPERIMENTAL_KINDS),
    ("OPT_IN_LISTENING_KINDS", FROZEN_OPT_IN_LISTENING_KINDS),
    ("LISTENING_KINDS", FROZEN_LISTENING_KINDS),
    ("TEXT_KINDS", FROZEN_TEXT_KINDS),
    ("DEFAULT_KINDS", FROZEN_DEFAULT_KINDS),
    ("SOUND_PREFERENCE_VERSION", FROZEN_SOUND_PREFERENCE_VERSION),
    ("KINDS_ADDED_AT", FROZEN_KINDS_ADDED_AT),
    ("PREFERENCE_VERSION", FROZEN_PREFERENCE_VERSION),
])
def test_derived_tables_equal_the_frozen_snapshot(name, expected):
    value = getattr(lq, name)
    assert value == expected and type(value) is type(expected)
    if isinstance(expected, dict):
        assert list(value.items()) == list(expected.items())


@pytest.mark.parametrize("name, expected", [("KIND_LABELS", FROZEN_KIND_LABELS),
                                            ("KIND_ABILITIES", FROZEN_KIND_ABILITIES)])
def test_kind_dicts_keep_values_and_insertion_order(name, expected):
    value = getattr(lq, name)
    assert type(value) is dict and list(value.items()) == list(expected.items())


def test_registry_invariants():
    every = REGISTRY.kinds(include_experimental=True)
    assert len(every) == len(set(every)) == len(FROZEN_KIND_LABELS) + len(FROZEN_EXPERIMENTAL_KINDS)
    for kind in REGISTRY.kinds():
        spec = REGISTRY.get(kind)
        assert spec.ability in KNOWN_ABILITIES and spec.label.strip() and not spec.experimental
    assert set(REGISTRY.practice_only()) <= set(every)
    assert REGISTRY.experimental() == FROZEN_EXPERIMENTAL_KINDS
    assert not set(REGISTRY.experimental()) & set(lq.DEFAULT_KINDS)
    assert not set(REGISTRY.experimental()) & set(lq.KIND_ABILITIES)
    assert REGISTRY.opt_in_listening() <= REGISTRY.listening()
    assert REGISTRY.by_family("word", template_source="authored") == FROZEN_AUTHORED_WORD_KINDS
    assert REGISTRY.by_template_source("sound") == FROZEN_SOUND_KINDS


def test_derived_views_are_fresh_copies():
    first = REGISTRY.abilities()
    first["vocab_meaning"] = "use"
    assert REGISTRY.abilities()["vocab_meaning"] == "understanding"


def test_registry_rejects_duplicates_invalid_abilities_and_late_registration():
    registry = Registry()
    registry.register(QuestionKindSpec("k", "标签", "word", "use"))
    with pytest.raises(ValueError, match="Duplicate"):
        registry.register(QuestionKindSpec("k", "标签", "word", "use"))
    with pytest.raises(ValueError, match="ability"):
        registry.register(QuestionKindSpec("x", "标签", "word", "speaking"))
    with pytest.raises(ValueError, match="ability"):
        registry.register(QuestionKindSpec("y", "标签", "word", None))
    with pytest.raises(ValueError, match="label"):
        registry.register(QuestionKindSpec("z", " ", "word", "use"))
    with pytest.raises(ValueError, match="family"):
        registry.register(QuestionKindSpec("w", "标签", "kanji", "use"))
    registry.seal()
    with pytest.raises(RuntimeError):
        registry.register(QuestionKindSpec("v", "标签", "word", "use"))
    with pytest.raises(RuntimeError):
        REGISTRY.register(QuestionKindSpec("v", "标签", "word", "use"))


def test_validate_templates_ignores_unknown_and_checks_known_kinds():
    ok, reason = REGISTRY.validate_templates("future_kind_from_a_newer_library", [{"prompt": "x"}])
    assert not ok and is_ignorable(reason)
    ok, reason = REGISTRY.validate_templates("spoken_recall", [])
    assert not ok and is_ignorable(reason)
    ok, reason = REGISTRY.validate_templates("vocab_audio_word", [])
    assert not ok and is_ignorable(reason)
    good = [{"prompt": "p", "answer": "a", "feedback": "f", "choices": ["a", "b"]}]
    assert REGISTRY.validate_templates("vocab_usage", good) == (True, "ok")
    for payload in ({}, [], [1], [{"prompt": "p", "answer": "a"}]):
        ok, reason = REGISTRY.validate_templates("vocab_usage", payload)
        assert not ok and not is_ignorable(reason)


def test_ai_course_catalogue_covers_exactly_the_registered_kinds():
    # COURSE keeps its own order and model-facing task text (catalogue order is
    # validated as a list), so it is not derived; it must still name every kind.
    from kanamemory.ai_question_capabilities import COURSE
    assert set(COURSE) == set(REGISTRY.kinds())


# ---- WP4 round 1: sound and context-choice kinds dispatch through providers

def _provider_item():
    from kanamemory.material_review import item_fingerprint
    item = {"id": "wp4:sample", "family": "word", "title": "切符", "reading": "きっぷ",
            "meaning": "车票", "revision": "r1",
            "abilities": ["understanding", "reading", "use", "listening"],
            "question_templates": {"vocab_meaning": [
                {"prompt": "切符", "answer": "车票", "choices": ["车票", "邮票"], "feedback": "f"}]}}
    context = {"prompt": "（　）を買います。", "task": "文脈で選ぶ", "answer": "切符",
               "choices": ["切符", "切手", "雑誌", "日"], "feedback": "f"}
    namespace = {"schema_version": 1, "items": {item["id"]: {
        "item_fingerprint": item_fingerprint(item),
        "templates": {FROZEN_CONTEXT_CHOICE_KIND: [context]}}}}
    return item, namespace


def test_generated_and_context_kinds_have_their_family_providers():
    from kanamemory.question_kinds import vocab_context, vocab_sound
    providers = {spec.kind: spec.template_provider for spec in REGISTRY.with_template_provider()}
    from kanamemory.question_kinds import grammar_audio_meaning
    assert providers == {"vocab_audio_word": vocab_sound.templates,
                         "vocab_reading_choice": vocab_sound.templates,
                         "vocab_context_choice": vocab_context.templates,
                         "grammar_audio_meaning": grammar_audio_meaning.templates}
    assert REGISTRY.derived() == {"grammar_audio_meaning": "grammar_meaning"}
    assert REGISTRY.generated() == FROZEN_SOUND_KINDS
    assert REGISTRY.get(FROZEN_CONTEXT_CHOICE_KIND).template_source == vocab_context.NAMESPACE
    for kind in REGISTRY.kinds(include_experimental=True):
        spec = REGISTRY.get(kind)
        # Each kind carries exactly the hooks its family module defines.
        hooks = {name for name in HOOK_NAMES if getattr(spec, name) is not None}
        assert hooks == FROZEN_HOOKS.get(kind, set()), kind
        if kind in providers or kind not in REGISTRY.kinds():
            # Provider and experimental kinds build, grade and take input generically.
            assert (spec.builder, spec.grader, spec.input_mode_policy) == (None,) * 3
        if kind not in providers:
            assert spec.template_provider is None


def test_providers_return_exactly_what_practice_templates_appends(monkeypatch):
    from kanamemory import word_sound_questions as sound
    pool_items = [{"id": key, "family": "word", "title": title, "reading": reading}
                  for key, title, reading in (("a", "切手", "きって"), ("b", "雑誌", "ざっし"),
                                              ("c", "日", "ひ"), ("d", "書く", "かく"),
                                              ("e", "昼", "ひる"), ("f", "木", "き"))]
    monkeypatch.setattr(lq, "_word_pool", lambda: sound.word_pool(pool_items))
    item, namespace = _provider_item()
    monkeypatch.setattr(lq, "_CONTEXT_CHOICE", namespace)
    templates = lq.practice_templates(item)
    for spec in REGISTRY.with_template_provider():
        variants = spec.template_provider(item, spec, lq._TEMPLATE_ENV)
        if spec.derived_from is not None:
            # A heard twin exists only for an expression lesson (test_grammar_audio_meaning).
            assert variants == [] and spec.kind not in templates
            continue
        assert variants, spec.kind
        assert templates[spec.kind] == variants
    assert list(templates)[-3:] == list(FROZEN_PRACTICE_ONLY_KINDS)
    # The namespace is read at call time, so an unbound lesson loses the kind.
    monkeypatch.setattr(lq, "_CONTEXT_CHOICE", {})
    assert FROZEN_CONTEXT_CHOICE_KIND not in lq.practice_templates(item)


# ---- WP4 round 2: authored choice and free-text word kinds

MIGRATED_HOOK_KINDS = ("vocab_meaning", "vocab_audio_meaning", "vocab_reading", "vocab_recall",
                       "vocab_orthography", "vocab_usage", "vocab_zh_ja_contrast",
                       "reading_comprehension", "listening_comprehension")


def test_choice_and_text_kinds_have_their_family_hooks():
    from kanamemory.question_kinds import vocab_choice, vocab_text
    assert set(vocab_choice.KINDS) | set(vocab_text.KINDS) == set(MIGRATED_HOOK_KINDS)
    assert set(vocab_text.KINDS) == set(FROZEN_AUTHORED_WORD_KINDS) & FROZEN_TEXT_KINDS
    assert not set(vocab_choice.KINDS) & FROZEN_TEXT_KINDS
    for kind in vocab_choice.KINDS:
        spec = REGISTRY.get(kind)
        # F5-A: vocab_usage's text form (かなで穴埋め) composes romaji like recall.
        assert spec.builder is vocab_choice.build and spec.input_mode_policy is (
            vocab_text.recall_input if kind == "vocab_usage" else None)
        assert spec.presenter is vocab_choice.PRESENTATIONS.get(kind)
        assert spec.grader is (vocab_text.WRITTEN_FORM_GRADER if kind == "vocab_orthography" else None)
    reading, recall = REGISTRY.get("vocab_reading"), REGISTRY.get("vocab_recall")
    assert (reading.builder, reading.grader, reading.presenter, reading.input_mode_policy) == (
        vocab_text.build, vocab_text.READING_GRADER, vocab_text.READING_PRESENTATION,
        vocab_text.reading_input)
    assert (recall.builder, recall.grader, recall.presenter, recall.input_mode_policy) == (
        vocab_text.build, vocab_text.WRITTEN_FORM_GRADER, vocab_text.RECALL_PRESENTATION,
        vocab_text.recall_input)


def test_family_cues_keep_their_instruction_roles():
    from kanamemory import question_presentation as qp
    ui_cues = {"意味 · 中文 / English"}  # Historical: a Chinese/English answer, not study text.
    assert {cue for kind in REGISTRY.kinds() if REGISTRY.get(kind).presenter
            for cue in REGISTRY.get(kind).presenter.ui_cues} == ui_cues
    for kind in REGISTRY.kinds():
        presenter = REGISTRY.get(kind).presenter
        if presenter is None:
            continue
        cues = {cue for _, cue in (*presenter.focused_tasks, *presenter.plain_tasks)}
        if presenter.quoted_tasks:
            cues.add(presenter.quoted_cue)
        assert cues - ui_cues <= qp._JAPANESE_CUES, kind
        assert set(presenter.study_tasks) <= qp._JAPANESE_CUES, kind
        assert not ui_cues & qp._JAPANESE_CUES


def test_registry_rejects_foreign_grader_and_presenter_objects():
    from kanamemory.question_kind_registry import KindGrader
    with pytest.raises(ValueError, match="grader"):
        Registry().register(QuestionKindSpec("g", "标签", "word", "use", grader=lambda q, a: {}))
    with pytest.raises(ValueError, match="grader"):
        Registry().register(QuestionKindSpec("g", "标签", "word", "use",
                                             grader=KindGrader(normalizer="grammar")))
    with pytest.raises(ValueError, match="presenter"):
        Registry().register(QuestionKindSpec("g", "标签", "word", "use", presenter={"cue": "x"}))


def _context_question(monkeypatch):
    """A reviewed context-choice task with a recorded background context."""
    from kanamemory.material_review import item_fingerprint
    item, _ = _provider_item()
    context = {"prompt": "（　）を買います。", "task": "文脈で選ぶ", "answer": "切符",
               "choices": ["切符", "切手", "雑誌", "日"], "feedback": "f",
               "practice": {"schema_version": 1, "task_family": "context_choice",
                            "context_id": "wp4:sample:context", "scaffold": "recognition",
                            "target_spans": [{"start": 0, "end": 3, "quote": "（　）"}],
                            "background_ids": [], "reviewed": True}}
    recorded = {"background_glosses": [{"start": 4, "end": 6, "surface": "買い",
                                        "meaning": "买", "target_id": "wp4:other"}]}
    namespace = {"schema_version": 1, "items": {item["id"]: {
        "item_fingerprint": item_fingerprint(item), "item_revision": item["revision"],
        "templates": {FROZEN_CONTEXT_CHOICE_KIND: [context]},
        "contexts": {lq._template_fingerprint(context): recorded}}}}
    monkeypatch.setattr(lq, "_CONTEXT_CHOICE", namespace)
    # varied_practice knows the lesson but not this kind: the former lookup.
    monkeypatch.setattr(lq, "_PRACTICE", {"schema_version": 1, "items": {item["id"]: {
        "item_fingerprint": item_fingerprint(item), "item_revision": item["revision"],
        "templates": {}, "contexts": {}}}})
    question = lq.build_learning_question(item["id"], FROZEN_CONTEXT_CHOICE_KIND, item=item,
                                          apply_review=False)
    return question, recorded


def test_reviewed_practice_context_reads_the_kinds_own_template_source(monkeypatch):
    # The one intended behaviour change of WP4 round 2: context choice used to
    # look only in varied_practice and so always returned None.
    question, recorded = _context_question(monkeypatch)
    assert question["practice_source"]["template_sha256"]
    assert lq.reviewed_practice_context(question) == recorded
    assert lq.reviewed_practice_context(question) is not recorded
    forged = dict(question, practice_source=dict(question["practice_source"], template_sha256="0" * 64))
    assert lq.reviewed_practice_context(forged) is None
    assert lq.reviewed_practice_context(dict(question, answer="切手")) is None
    monkeypatch.setattr(lq, "_CONTEXT_CHOICE", {})
    assert lq.reviewed_practice_context(question) is None


def test_a_generated_kind_needs_a_template_provider():
    with pytest.raises(ValueError, match="template provider"):
        Registry().register(QuestionKindSpec("g", "标签", "word", "use", template_source="sound"))


# ---- WP4 round 3: grammar and pronunciation families, cross-family flags

HOOK_NAMES = ("template_provider", "builder", "grader", "presenter", "input_mode_policy",
              "snapshot_finisher")
_CHOICE_HOOKS = {"builder", "presenter"}
FROZEN_HOOKS = {
    "vocab_meaning": _CHOICE_HOOKS, "vocab_audio_meaning": _CHOICE_HOOKS,
    "vocab_reading": {"builder", "grader", "presenter", "input_mode_policy"},
    "vocab_recall": {"builder", "grader", "presenter", "input_mode_policy"},
    "vocab_orthography": {"builder", "grader", "presenter"},
    "vocab_usage": _CHOICE_HOOKS | {"input_mode_policy"}, "vocab_zh_ja_contrast": {"builder"},
    "reading_comprehension": _CHOICE_HOOKS, "listening_comprehension": _CHOICE_HOOKS,
    "grammar_meaning": {"builder", "presenter"},
    "grammar_cloze": {"builder", "presenter", "input_mode_policy"},
    "grammar_transform": {"builder", "input_mode_policy"},
    "grammar_reorder": {"builder", "presenter", "input_mode_policy"},
    "targeted_correction": {"builder", "input_mode_policy"},
    "controlled_sentence_production": {"builder", "presenter", "input_mode_policy"},
    "pronunciation_rule": {"snapshot_finisher"}, "pronunciation_application": {"snapshot_finisher"},
    "vocab_audio_word": {"template_provider", "presenter"},
    "vocab_reading_choice": {"template_provider", "presenter"},
    "vocab_context_choice": {"template_provider", "presenter"},
    "grammar_audio_meaning": {"template_provider", "presenter"},
}
# Literal values of the former learning_questions / question_presentation /
# answer_input tables (before WP4 round 3). Do not edit to follow a change.
FROZEN_SAFE_SUPPORT_KINDS = {"vocab_meaning", "vocab_zh_ja_contrast", "grammar_meaning",
                             "reading_comprehension"}
FROZEN_BACKGROUND_SUPPORT_KINDS = {"vocab_usage", "vocab_context_choice", "grammar_cloze",
                                   "grammar_transform", "grammar_reorder", "targeted_correction",
                                   "controlled_sentence_production"}
FROZEN_ORDER_TRANSLATION_SOURCES = {
    **dict.fromkeys(("grammar_cloze", "vocab_usage", "vocab_context_choice"), "gap"),
    **dict.fromkeys(("grammar_transform", "grammar_reorder", "targeted_correction",
                     "controlled_sentence_production"), "answer"),
    **dict.fromkeys(("vocab_meaning", "vocab_reading", "vocab_zh_ja_contrast",
                     "reading_comprehension", "grammar_meaning", "pronunciation_application"),
                    "prompt"),
}
FROZEN_DICTIONARY_LESSON_KINDS = ("vocab_meaning", "vocab_reading")
FROZEN_COPY_READING_KINDS = {"vocab_reading", "vocab_reading_choice"}
FROZEN_JAPANESE_CUES = {"読み", "意味", "書き方", "穴埋め", "基本形", "単語", "文の意味", "語順",
                        "単語 · かな", "基本形 · 漢字 / かな", "かなで穴埋め", "聞いて、単語を選ぶ",
                        "文脈で選ぶ", "文脈で選ぶ · 語群"}
FROZEN_ROMAJI_STYLES = {
    "vocab_reading": "reading", "vocab_recall": "runs",
    **dict.fromkeys(("grammar_cloze", "grammar_transform", "grammar_reorder",
                     "targeted_correction", "controlled_sentence_production"), "runs"),
}
FROZEN_PROMPT_INSTRUCTION_KINDS = {"grammar_reorder", "controlled_sentence_production"}


@pytest.mark.parametrize("name, expected", [
    ("SAFE_SUPPORT_KINDS", FROZEN_SAFE_SUPPORT_KINDS),
    ("BACKGROUND_SUPPORT_KINDS", FROZEN_BACKGROUND_SUPPORT_KINDS),
    ("ORDER_TRANSLATION_SOURCES", FROZEN_ORDER_TRANSLATION_SOURCES),
    ("DICTIONARY_LESSON_KINDS", FROZEN_DICTIONARY_LESSON_KINDS),
    ("READS_WRITTEN_FORM_KINDS", FROZEN_COPY_READING_KINDS),
])
def test_cross_family_sets_equal_their_former_literals(name, expected):
    assert getattr(lq, name) == expected


def test_presentation_and_input_tables_equal_their_former_literals():
    from kanamemory import answer_input, question_presentation as qp
    # The frozen literal stays as it was; the heard word's kana-spelling variants add their task.
    assert qp._JAPANESE_CUES == FROZEN_JAPANESE_CUES | {"聞いて、かなを選ぶ"}
    assert (qp.CONTEXT_CHOICE, qp.CONTEXT_WORD_BANK) == ("文脈で選ぶ", "文脈で選ぶ · 語群")
    styles = {kind: answer_input._romaji_style({"kind": kind})
              for kind in REGISTRY.kinds(include_experimental=True)}
    # The frozen literal stays as it was; F5-A adds only vocab_usage's text form.
    assert {kind: style for kind, style in styles.items() if style is not None} == {
        **FROZEN_ROMAJI_STYLES, "vocab_usage": "runs"}
    assert answer_input._romaji_style({"kind": "unknown_kind"}) is None
    assert {kind for kind in REGISTRY.kinds(include_experimental=True)
            if qp._kind_presentation(kind).prompt_is_instruction} == FROZEN_PROMPT_INSTRUCTION_KINDS
    # No per-kind table of a not-yet-migrated family remains in either module.
    assert not [name for name in (*vars(qp), *vars(answer_input)) if name.startswith("_RESIDUAL")]


def test_grammar_and_pronunciation_kinds_have_their_family_hooks():
    from kanamemory.question_kinds import (grammar_cloze, grammar_meaning, grammar_production,
                                           pronunciation)
    from kanamemory.question_kinds import grammar_audio_meaning
    assert (*grammar_meaning.KINDS, *grammar_cloze.KINDS, *grammar_production.KINDS,
            *grammar_audio_meaning.KINDS) == FROZEN_GRAMMAR_KINDS
    assert pronunciation.KINDS == FROZEN_PRONUNCIATION_KINDS
    heard = REGISTRY.get("grammar_audio_meaning")
    assert (heard.builder, heard.grader, heard.input_mode_policy, heard.snapshot_finisher) == (None,) * 4
    assert heard.presenter is grammar_meaning.PRESENTATION
    for kind in FROZEN_LEGACY_GRAMMAR_KINDS:
        spec = REGISTRY.get(kind)
        assert spec.builder is grammar_meaning.build and spec.grader is None
        assert spec.snapshot_finisher is None
    assert REGISTRY.get("grammar_meaning").presenter is grammar_meaning.PRESENTATION
    assert REGISTRY.get("grammar_cloze").presenter is grammar_cloze.PRESENTATION
    for kind in grammar_production.KINDS:
        assert REGISTRY.get(kind).presenter is grammar_production.PRESENTATIONS.get(kind)
        assert REGISTRY.get(kind).input_mode_policy is grammar_production.romaji_input
    for kind in FROZEN_PRONUNCIATION_KINDS:
        spec = REGISTRY.get(kind)
        assert spec.snapshot_finisher is pronunciation.finish
        assert (spec.builder, spec.grader, spec.presenter, spec.input_mode_policy) == (None,) * 4


class _GrammarEnv:
    def __init__(self, table):
        self.table = table

    def legacy_grammar_variants(self, item, kind):
        from copy import deepcopy
        return deepcopy(self.table.get(item["id"], {}).get(kind, []))


def test_grammar_builder_selects_a_fresh_variant_or_refuses():
    from kanamemory.question_kinds import grammar_meaning
    spec = REGISTRY.get("grammar_cloze")
    first, second = {"prompt": "a（　）", "answer": "が"}, {"prompt": "b（　）", "answer": "を"}
    env = _GrammarEnv({"g:1": {"grammar_cloze": [first, second]}})
    built = grammar_meaning.build({"id": "g:1"}, spec, env, 3)
    assert built == second and built is not second
    with pytest.raises(ValueError, match="No reviewed question templates"):
        grammar_meaning.build({"id": "g:1"}, REGISTRY.get("grammar_reorder"), env, 0)


def test_pronunciation_finisher_drops_audio_and_requires_text_roles():
    from kanamemory.question_kinds import pronunciation
    result = {"audio_text": "x", "optional_audio_text": "y", "requires_audio": True}
    pronunciation.finish(result, {"text_roles": {"prompt": "ui", "answer": "study", "task": "ui"}},
                         {"question_template_revision": "t1"})
    assert result == {"audio_text": "", "optional_audio_text": "", "requires_audio": False,
                      "domain": "pronunciation", "tests_reading": True,
                      "text_roles": {"prompt": "ui", "answer": "study"},
                      "question_template_revision": "t1"}
    for raw in ({}, {"text_roles": {"prompt": "ui"}}, {"text_roles": ["ui", "study"]}):
        with pytest.raises(ValueError, match="text roles"):
            pronunciation.finish({}, raw, {})


def test_registry_rejects_an_unknown_order_translation_source():
    with pytest.raises(ValueError, match="order translation"):
        Registry().register(QuestionKindSpec("g", "标签", "word", "use",
                                             order_translation_source="stimulus"))
