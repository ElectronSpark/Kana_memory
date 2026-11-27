"""Heard expression meaning (grammar_audio_meaning): heard twins of an expression lesson's meaning questions.

Made-up lessons only (no course content): the twin's spec, its derived variants, its facts and its
selection follow its source grammar_meaning template; twin and source are one stimulus; the twin's page
prints nothing of its sentence before the answer; it speaks no speaker labels and no Latin letters.
"""
import re

from copy import deepcopy

import pytest

from kanamemory import learning_practice_policy as policy
from kanamemory import learning_questions as lq
from kanamemory.question_kind_registry import QuestionKindSpec, Registry
from kanamemory.question_kinds import REGISTRY
from kanamemory.question_kinds import grammar_audio_meaning as gam
from kanamemory.question_sentences import question_sentences
from support.app_fixtures import question_app as question_app, setup_app as setup_app  # noqa: F401
from support.learning_fixtures import learning_app as learning_app  # noqa: F401

KIND, SOURCE = "grammar_audio_meaning", "grammar_meaning"
MEANINGS = ["晚安", "早上好", "谢谢", "再见"]


def _practice(prompt, quote, number):
    start = prompt.index(quote)
    return {"schema_version": 1, "task_family": "expression_meaning", "context_id": f"demo:ctx:{number}",
            "scaffold": "recognition", "target_spans": [{"start": start, "end": start + len(quote), "quote": quote}],
            "background_ids": [], "reviewed": True}


def _meaning(prompt, number, *, quote="おやすみ", practice=True):
    raw = {"prompt": prompt, "task": "这句话里的「おやすみ」是什么意思？", "answer": MEANINGS[0],
           "choices": list(MEANINGS), "feedback": "睡前说的话。", "safe": True}
    if practice:
        raw.update(input_mode="choice", strict=True, practice=_practice(prompt, quote, number))
    return raw


PROMPTS = ("今日はありがとう。おやすみ。", "A：もう寝るよ。\nB：うん、おやすみ。", "おやすみ…。",
           "选择意思：おやすみ。", "じゃあ、おやすみ。また明日ね。", "「good night」は、おやすみです。")
# What each twin speaks: line after line, no speaker label; 2 (a gap), 3 (Chinese), 5 (Latin): nothing.
SPOKEN = [PROMPTS[0], "もう寝るよ。\nうん、おやすみ。", "", "", PROMPTS[4], ""]
HEARD = (0, 1, 4)
UNHEARD = (2, 3, 5)


def lesson(*, domain="expression", prompts=PROMPTS, abilities=("understanding", "application")):
    return {"id": "grammar:expression:demo_oyasumi", "family": "grammar", "domain": domain,
            "title": "おやすみ", "revision": "r1", "abilities": list(abilities),
            "instruction": {"rule": "睡前道别。", "example": "おやすみ。"},
            "question_templates": {SOURCE: [_meaning(p, n) for n, p in enumerate(prompts)],
                                   "grammar_cloze": [{"prompt": "じゃあ、（　）。", "answer": "おやすみ",
                                                      "choices": ["おやすみ", "おはよう"], "feedback": "f"}]}}


def test_spec_is_an_opt_in_heard_understanding_kind_of_the_grammar_family():
    spec = REGISTRY.get(KIND)
    assert (spec.label, spec.family, spec.ability) == ("听句辨义", "grammar", "understanding")
    assert spec.opt_in_listening and spec.needs_audio and not spec.default_enabled and not spec.text_input
    assert (spec.added_at_version, spec.derived_from, spec.template_source) == (3, SOURCE, "derived")
    assert not spec.practice_only and not spec.safe_support and not spec.background_support
    assert KIND in lq.OPT_IN_LISTENING_KINDS and KIND in lq.LISTENING_KINDS and KIND in lq.GRAMMAR_KINDS
    assert KIND not in lq.DEFAULT_KINDS and lq.KIND_LABELS[KIND] == "听句辨义"
    # A library never supplies its templates.
    ok, reason = REGISTRY.validate_templates(KIND, [{"prompt": "p", "answer": "a", "feedback": "f"}])
    assert not ok and reason == "ignored:derived_kind"


def test_a_derived_kind_needs_a_provider_and_a_registered_source():
    registry = Registry()
    registry.register(QuestionKindSpec("src", "标签", "grammar", "understanding"))
    with pytest.raises(ValueError, match="Derived kind"):
        registry.register(QuestionKindSpec("a", "标签", "grammar", "understanding", derived_from="src"))
    with pytest.raises(ValueError, match="Derived kind"):
        registry.register(QuestionKindSpec("b", "标签", "grammar", "understanding", derived_from="missing",
                                           template_provider=gam.templates))
    registry.register(QuestionKindSpec("c", "标签", "grammar", "understanding", derived_from="src",
                                       template_provider=gam.templates))
    with pytest.raises(ValueError, match="Derived kind"):
        registry.register(QuestionKindSpec("d", "标签", "grammar", "understanding", derived_from="c",
                                           template_provider=gam.templates))
    assert registry.derived() == {"c": "src"}


def test_a_speakable_prompt_gives_a_twin_with_the_same_number_answer_options_and_explanation():
    item = lesson()
    templates = lq.practice_templates(item)
    sources, twins = templates[SOURCE], templates[KIND]
    assert len(twins) == len(sources)
    for source, twin in zip(sources, twins):
        rest = {key: value for key, value in twin.items() if key not in {"audio_text", "task", "source_task"}}
        assert rest == {key: value for key, value in source.items() if key != "task"}
        assert twin["task"] == gam.HEARD_TASK and twin["source_task"] == source["task"]
        assert lq._derived_source(twin) == source
    assert [twin["audio_text"] for twin in twins] == SPOKEN
    retired = lq.retired_practice_variants(item, templates)
    # 2: an ellipsis is a gap, nothing is spoken; 3: Chinese instruction; 5: Latin letters.
    assert {(kind, v) for kind, v in retired} == {(KIND, v) for v in UNHEARD}
    from kanamemory.question_support import prompt_audio_text
    for variant in HEARD:
        heard = lq.build_learning_question(item, KIND, variant=variant, apply_review=False)
        built = lq.build_learning_question(item, SOURCE, variant=variant, apply_review=False)
        # The source's own optional audio is unchanged (with its labels); the twin speaks without them.
        assert prompt_audio_text(built) == PROMPTS[variant]
        assert heard["audio_text"] == SPOKEN[variant] and "：" not in heard["audio_text"]
        assert heard["task"] == gam.HEARD_TASK and "source_task" not in heard
        assert heard["requires_audio"] is True and heard["optional_audio_text"] == ""
        assert not heard["support"]["segments"] and heard["support"]["audio_safe"] is False
        assert heard["variant"] == built["variant"] == variant and heard["id"] != built["id"]
        assert (heard["prompt"], heard["answer"], sorted(heard["choices"]), heard["feedback"], heard["ability"]) == (
            built["prompt"], built["answer"], sorted(built["choices"]), built["feedback"], built["ability"])
        assert heard["practice"] == built["practice"] and "practice_source" not in heard
        # No text kana on the hidden prompt, no translation bound to it.
        assert lq.learning_order_translation_source(heard) is None
    for variant in UNHEARD:
        with pytest.raises(ValueError, match="cannot be heard"):
            lq.build_learning_question(item, KIND, variant=variant, apply_review=False)


def test_no_twin_for_a_retired_source_an_unspeakable_lesson_or_another_grammar_lesson(monkeypatch):
    from kanamemory.material_review import item_fingerprint
    item = lesson()
    retirement = {item["id"]: {item_fingerprint(item): {SOURCE: [lq.template_fingerprint(
        item["question_templates"][SOURCE][1])]}}}
    monkeypatch.setattr(lq, "_PRACTICE", {"template_retirements": retirement})
    retired = lq.retired_practice_variants(item, lq.practice_templates(item))
    assert {(SOURCE, 1), (KIND, 1)} <= retired and (KIND, 0) not in retired
    monkeypatch.setattr(lq, "_PRACTICE", {})
    for other in (lesson(domain=None), lesson(domain="pronunciation"), lesson(prompts=(PROMPTS[2], PROMPTS[3])),
                  lesson(abilities=("application",)), dict(lesson(), ai_generated=True)):
        templates = lq.practice_templates(other) or {}
        assert KIND not in templates
        assert KIND not in lq.question_kinds(other, templates=templates)


def test_the_twin_resolves_to_its_source_dependency_entry(monkeypatch):
    item = lesson()
    templates = lq.practice_templates(item)
    source, twin = templates[SOURCE][0], templates[KIND][0]
    entry = {"words": ["word:demo:kyou", "word:demo:arigatou"], "stem_words": ["word:demo:kyou"]}
    monkeypatch.setattr(lq, "_DEPENDENCIES", {"schema_version": 1, "items": {item["id"]: {
        SOURCE: {lq.template_fingerprint(source): entry}}}})
    facts = lq.template_facts(item, KIND, twin)
    assert facts == lq.template_facts(item, SOURCE, source)
    assert facts.requires == {"word:demo:kyou", "word:demo:arigatou"}
    assert facts.stem_only == {"word:demo:kyou"}
    heard = lq.build_learning_question(item, KIND, variant=0, apply_review=False)
    assert lq.question_requires(item, heard, apply_review=False) == facts.requires


def _state(**extra):
    return {"known_background_ids": [], **extra}


def test_with_listening_off_no_twin_is_a_candidate_and_with_it_on_one_can_be_chosen():
    item = lesson()
    off = list(policy._candidate_questions(item, "understanding", [SOURCE], _state()))
    assert off and {c["kind"] for c in off} == {SOURCE}
    on = list(policy._candidate_questions(item, "understanding", [SOURCE, KIND], _state()))
    assert {(c["kind"], c["variant"]) for c in on if c["kind"] == KIND} == {(KIND, v) for v in HEARD}
    plan = policy.select_practice_plan(item, "understanding", [KIND], _state(), [])
    assert plan == {"kind": KIND, "variant": 0, "input_mode": "choice"}
    # The default and stored kind lists never switch it on.
    assert KIND not in lq.stored_enabled_kinds({})


def test_a_twin_shares_its_sources_sentence_units():
    item = lesson()
    templates = lq.practice_templates(item)
    for variant in HEARD:
        units = question_sentences(KIND, templates[KIND][variant])
        assert units and units == question_sentences(SOURCE, templates[SOURCE][variant])


@pytest.fixture
def catalogued(monkeypatch):
    """The made-up lesson as the catalogue's: a stored twin finds its source's task by its lesson."""
    item = lesson()
    monkeypatch.setattr(lq, "get_learning_item", lambda cid: deepcopy(item) if cid == item["id"] else None)
    lq._source_task.cache_clear()
    yield item
    lq._source_task.cache_clear()


def _row(question):
    return {"question": question, "role": "primary", "scheduled": True, "outcome": "correct", "correct": True,
            "assisted": False}


def test_a_twin_and_its_source_are_one_stimulus(catalogued):
    item = catalogued
    templates = lq.practice_templates(item)
    for variant in HEARD:
        heard = lq.build_learning_question(item, KIND, variant=variant, apply_review=False)
        built = lq.build_learning_question(item, SOURCE, variant=variant, apply_review=False)
        source_print = policy._fingerprint(built)
        assert policy._fingerprint(heard) == source_print
        assert policy._fingerprint(dict(templates[KIND][variant], kind=KIND)) == source_print
        assert policy._fingerprint(dict(templates[SOURCE][variant], kind=SOURCE)) == source_print
        assert policy.question_context(heard) == policy.question_context(built)
    # The template fingerprint of retirements and dependency entries is another function: unchanged.
    assert lq.template_fingerprint(templates[KIND][0]) != lq.template_fingerprint(templates[SOURCE][0])
    # A stored twin whose source is gone keeps its own stimulus.
    other = lq.build_learning_question(item, KIND, variant=0, apply_review=False)
    assert policy._fingerprint(dict(other, revision="r0")) != policy._fingerprint(
        lq.build_learning_question(item, SOURCE, variant=0, apply_review=False))
    # Both stay candidates: a twin is never dropped as a copy of its source.
    on = list(policy._candidate_questions(item, "understanding", [SOURCE, KIND], _state()))
    assert sorted((c["kind"], c["variant"]) for c in on) == sorted(
        [(SOURCE, v) for v in range(len(PROMPTS))] + [(KIND, v) for v in HEARD])


@pytest.mark.parametrize("served, follow", [(SOURCE, KIND), (KIND, SOURCE)])
def test_after_one_of_the_pair_the_other_waits_while_another_candidate_is_not_recent(catalogued, served, follow):
    item = catalogued
    for variant in HEARD:
        history = [_row(lq.build_learning_question(item, served, variant=variant, apply_review=False))]
        plan = policy.select_practice_plan(item, "understanding", [SOURCE, KIND],
                                           _state(attempt_count=1), history)
        assert plan is not None and plan["variant"] != variant, (served, variant, plan)
    # With nothing else left, the other of the pair is still served.
    only = lesson(prompts=(PROMPTS[0],))
    history = [_row(lq.build_learning_question(only, served, variant=0, apply_review=False))]
    plan = policy.select_practice_plan(only, "understanding", [follow], _state(attempt_count=1), history)
    assert plan == {"kind": follow, "variant": 0, "input_mode": "choice"}


def test_a_guide_never_uses_the_heard_twin():
    item = lesson()
    guided = policy.guided_practice_sequence(item, [SOURCE, KIND, "grammar_cloze"])
    assert guided and all(q["kind"] != KIND for q in guided)
    assert len(policy.guided_practice_sequence(item, [KIND])) == 0


def test_saved_kind_lists_gain_the_twin_only_with_the_listening_group_on():
    old = {"enabled": ["grammar_meaning", "vocab_meaning"], lq.SOUND_PREFERENCE_VERSION: 2}
    assert lq.stored_enabled_kinds(old) == old["enabled"]
    listening = {"enabled": ["grammar_meaning", "vocab_audio_meaning"], lq.SOUND_PREFERENCE_VERSION: 2}
    assert lq.stored_enabled_kinds(listening) == [*listening["enabled"], KIND]
    current = {"enabled": ["vocab_audio_meaning"], lq.SOUND_PREFERENCE_VERSION: lq.PREFERENCE_VERSION}
    assert lq.stored_enabled_kinds(current) == ["vocab_audio_meaning"]
    # A list from before the first sound kinds still gains only the default-on kinds.
    oldest = {"enabled": ["vocab_meaning"]}
    assert lq.stored_enabled_kinds(oldest) == ["vocab_meaning", "vocab_audio_word", "vocab_reading_choice",
                                               "vocab_context_choice"]


_RUN = re.compile(r"[\u3041-\u3096\u30a1-\u30fa\u30fc\u3400-\u4dbf\u4e00-\u9fff々]{3,}")


def _quotes(sentence, texts):
    """Japanese runs of three or more characters of the sentence that a text shows."""
    runs = {run[i:j] for run in _RUN.findall(sentence) for i in range(len(run)) for j in range(i + 3, len(run) + 1)}
    return [text for text in texts if any(run in text for run in runs)]


def _texts(widgets):
    from support.fake_ui import descendants
    return [str(node.text) for widget in widgets for node in descendants(widget) if getattr(node, "text", None)]


def test_the_twin_page_prints_nothing_of_its_sentence_before_the_answer(monkeypatch, learning_app):
    from kanamemory import question_support
    from kanamemory.question_presentation import question_instruction
    item = lesson()
    heard = lq.build_learning_question(item, KIND, variant=0, apply_review=False)
    built = lq.build_learning_question(item, SOURCE, variant=0, apply_review=False)
    # A word gloss and a rule hint that name words of the sentence, and one that does not.
    monkeypatch.setattr(question_support, "unmet_requirement_glosses", lambda q, known, requires=None: [
        {"target_id": "word:demo:kyou", "surface": "今日", "meaning": "今天", "start": 0, "end": 2},
        {"target_id": "word:demo:arigatou", "surface": "ありがとう", "meaning": "谢谢", "start": 3, "end": 8}])
    monkeypatch.setattr(question_support, "unmet_rule_hints", lambda q, known, requires=None: [
        {"id": "grammar:demo:wa", "title": "は：主题", "lines": ["今日は：话题"]},
        {"id": "grammar:demo:ne", "title": "ね：确认", "lines": ["名词＋ね。"]}])
    source_rows = _texts(learning_app._learning_context_support(built, set()))
    assert _quotes(heard["prompt"], source_rows)  # the source's page shows them
    rows = _texts(learning_app._learning_context_support(heard, set()))
    assert rows == ["还没学的规则：ね：确认 ▸"]
    shown = [heard["task"], question_instruction(heard), *rows, *heard["choices"]]
    assert not _quotes(heard["prompt"], shown) and not _quotes(heard["audio_text"], shown)
    assert heard["task"] == "听句子，选出它的意思。"
    assert gam.shown_before_answer(built, "今日") and not gam.shown_before_answer(heard, "今日：今天")


def test_provider_returns_fresh_objects_and_keeps_the_lesson():
    item = lesson()
    before = deepcopy(item)
    first = gam.templates(item, REGISTRY.get(KIND), lq._TEMPLATE_ENV)
    first[0]["prompt"] = "changed"
    assert gam.templates(item, REGISTRY.get(KIND), lq._TEMPLATE_ENV)[0]["prompt"] == PROMPTS[0]
    assert item == before
    assert gam.chinese_instruction("选择意思") and gam.chinese_instruction("这是什么")
    assert not gam.chinese_instruction("練習、お疲れ様でした！乾杯！")


def test_the_route_mix_reads_the_twins_and_gates_them_by_their_source_entries():
    """route_mix.sources reads the templates the app selects from, the heard twins included (the app's own
    provider), and a twin's readiness row is its source's: same fingerprint, same requirements."""
    from kanamemory import content_pack
    from kanamemory.route_mix import sources
    item = lesson()
    templates = lq.practice_templates(item)
    providers = sources._providers(lq)
    assert KIND not in {spec.kind for spec in providers}
    read = sources._item_templates(item, {}, {spec.template_source: {} for spec in providers}, providers,
                                   lambda: None)
    assert read == {kind: rows for kind, rows in templates.items() if kind not in lq.SOUND_KINDS}
    assert lq.derived_templates(item) == {KIND: templates[KIND]}
    sources_sha = [lq.template_fingerprint(raw) for raw in templates[SOURCE]]
    assert [lq.dependency_fingerprint(KIND, raw) for raw in templates[KIND]] == [(SOURCE, sha) for sha in sources_sha]
    assert lq.dependency_fingerprint(SOURCE, templates[SOURCE][0]) == (SOURCE, sources_sha[0])
    entry = {SOURCE: {sha: {"words": [f"word:demo:w{n}"]} for n, sha in enumerate(sources_sha)}}
    namespace = {"schema_version": 1, "items": {item["id"]: entry}}

    def load(name, default):
        return namespace if name == content_pack.QUESTION_DEPENDENCIES else default
    for kinds, heard in (((KIND,), HEARD), ((SOURCE,), range(len(PROMPTS))), ((), ())):
        rows = sources.read_readiness([item["id"]], {item["id"]: item}, load=load, kinds=kinds).abilities(item["id"])
        assert {sha for sha, _ in rows["understanding"]} == set(sources_sha)  # a twin is its source's question
        assert set(rows.get("on:understanding", ())) == {(sources_sha[n], (f"word:demo:w{n}",)) for n in heard}
