"""WP-K2: sound kinds draw options from known words and need no gate; kana join the known set.

Synthetic data only (design 11 sections 4-5): a private word pool replaces the
bundled catalogue, and the dependency namespace is installed per test.
Also the heard-word task's kana-spelling variants, built from the lesson alone.
"""
from copy import deepcopy
import re
from types import SimpleNamespace

import pytest

from kanamemory import learning_questions as lq
from kanamemory import word_sound_questions as sound
from kanamemory.course_planning import iter_question_plans, known_ids
from kanamemory.intro_batch_ui import _quiz_variants
from kanamemory.learning_practice_policy import practice_candidates
from support.question_dependency_support import GRAMMAR_ID, grammar, install, namespace, requirement


def lesson(identity, title, reading, **extra):
    return {"id": identity, "family": "word", "title": title, "reading": reading, "meaning": "意味",
            "revision": "1", "abilities": ["understanding", "reading", "use", "listening"],
            "question_templates": {}, "instruction": {"summary": "意味", "rule": "名词。", "steps": [], "example": title,
                            "translation": "意味", "prerequisite": "", "contrast": ""}, **extra}


ITEMS = [lesson("t", "切符", "きっぷ"), lesson("a", "切手", "きって"), lesson("b", "雑誌", "ざっし"),
         lesson("c", "火", "ひ"), lesson("d", "目", "め"), lesson("e", "手", "て"), lesson("f", "木", "き"),
         lesson("g", "書く", "かく"), lesson("h", "昼", "ひる"), lesson("i", "生", "なま"),
         lesson("j", "キップ", "きっぷく")]
POOL = sound.word_pool(ITEMS)
TARGET = ITEMS[0]
TITLES = {item["id"]: item["title"] for item in ITEMS}
READINGS = {item["id"]: item["reading"] for item in ITEMS}


@pytest.fixture
def pool(monkeypatch):
    """The generators read the bundled catalogue through _word_pool; use the synthetic one."""
    monkeypatch.setattr(lq, "_word_pool", lambda: POOL)
    install(monkeypatch, {})


def distractors(raw):
    return set(raw["choices"]) - {raw["answer"]}


# ------------------------------------------------------------ pure generators

def test_fewer_than_three_known_candidates_keep_the_bank():
    assert sound.known_audio_word_variants(TARGET, POOL, {"a", "b"}) is None
    assert sound.known_audio_word_variants(TARGET, POOL, set()) is None


def test_heard_word_options_come_only_from_known_words():
    known = {"c", "e", "g", "h"}
    variants = sound.known_audio_word_variants(TARGET, POOL, known)
    assert len(variants) == sound.AUDIO_VARIANTS
    assert len({frozenset(distractors(raw)) for _, raw in variants}) == len(variants)
    for number, raw in variants:
        assert sound.is_known_variant(number)
        assert distractors(raw) <= {TITLES[i] for i in known}
        assert (raw["answer"], raw["audio_text"], raw["prompt"]) == ("切符", "きっぷ", sound.AUDIO_PROMPT)
        assert len(raw["choices"]) == sound.OPTION_COUNT
        # The number alone rebuilds the template: no known set is needed.
        assert sound.known_variant_template(sound.AUDIO_WORD, TARGET, POOL, number) == raw


def test_a_group_equal_to_a_bank_variant_keeps_the_bank_number():
    everyone = {item["id"] for item in ITEMS}
    variants = sound.known_audio_word_variants(TARGET, POOL, everyone)
    assert variants == list(enumerate(sound.audio_word_templates(TARGET, POOL)))
    reading = sound.known_reading_choice_variants(TARGET, POOL, everyone)
    assert reading == list(enumerate(sound.reading_choice_templates(TARGET, POOL)))


def test_reading_options_never_include_a_reading_of_an_unknown_word():
    options, owners = sound._reading_options(TARGET, POOL)
    assert owners, "the synthetic pool must offer attested readings"
    variants = sound.known_reading_choice_variants(TARGET, POOL, set())
    assert variants
    for number, raw in variants:
        assert not distractors(raw) & set(owners)
        assert raw["answer"] == "きっぷ" and raw["prompt"] == "切符"
        template = (sound.known_variant_template(sound.READING_CHOICE, TARGET, POOL, number)
                    if sound.is_known_variant(number) else sound.reading_choice_templates(TARGET, POOL)[number])
        assert template == raw
    known = next(iter(next(iter(owners.values()))))
    widened = sound.known_reading_choice_variants(TARGET, POOL, {known})
    allowed = {value for value, ids in owners.items() if known in ids}
    assert all(not distractors(raw) & (set(owners) - allowed) for _, raw in widened)


@pytest.mark.parametrize("variant", [sound.KNOWN_VARIANT_BASE + 5,  # positions 0, 0, 5: repeated
                                     sound.KNOWN_VARIANT_BASE + (60000 << 32) + (1 << 16) + 2,  # out of range
                                     sound.KNOWN_VARIANT_BASE << 2,  # too wide for three positions
                                     sound.KANA_VARIANT_BASE - 1,
                                     sound.KANA_VARIANT_BASE + sound.KANA_VARIANTS])  # no such kana variant
def test_an_invalid_encoded_variant_is_refused(variant):
    with pytest.raises(ValueError):
        sound.known_variant_template(sound.AUDIO_WORD, TARGET, POOL, variant)


def test_bank_variants_are_unchanged():
    # Pinned by test_word_sound_questions for the catalogue; here the refactored
    # helpers give the same slices as the former inline loops.
    near = list(dict.fromkeys(title for title, _ in sound._sound_near(TARGET, POOL)))
    bank = sound.audio_word_templates(TARGET, POOL)
    assert [sorted(distractors(raw)) for raw in bank] == [sorted(near[0:3]), sorted(near[3:6])]


# --------------------------------------------------------- learning_questions

def test_sound_variants_need_a_known_set_with_three_candidates(pool, monkeypatch):
    bank = list(enumerate(lq._sound_templates(TARGET, lq.AUDIO_WORD_KIND)))
    kana = sound.kana_audio_word_variants(TARGET)
    assert lq.sound_variants(TARGET, lq.AUDIO_WORD_KIND) == bank
    # The start: a learner who knows fewer than three words (the target does not count) has nothing to choose
    # known options from and gets the bank — never the kana spellings, withdrawn on 2026-11-12.
    assert len(kana) == 2 and lq.sound_variants(TARGET, lq.AUDIO_WORD_KIND, {"t", "a", "b"}) == bank
    assert lq.sound_variants(TARGET, lq.AUDIO_WORD_KIND, set()) == bank
    # Three known words of which fewer than three are candidates: the heard-word task waits (no bank of
    # unlearned written words any more); the reading kind, whose options are kana, keeps its bank.
    with monkeypatch.context() as patch:
        patch.setattr(lq._vocab_sound, "known_variants", lambda *args: None)
        assert lq.sound_variants(TARGET, lq.AUDIO_WORD_KIND, {"c", "e", "g", "h"}) == []
        assert lq.sound_variants(TARGET, lq.READING_CHOICE_KIND, {"c", "e", "g", "h"}) == list(enumerate(
            lq._sound_templates(TARGET, lq.READING_CHOICE_KIND)))
        assert list(practice_candidates(TARGET, [lq.AUDIO_WORD_KIND],
                                        known_background_ids={"c", "e", "g", "h"})) == []
    known = lq.sound_variants(TARGET, lq.AUDIO_WORD_KIND, {"c", "e", "g", "h"})
    assert known != bank and len(known) == 2 and all(sound.is_known_variant(number) for number, _ in known)
    assert not any(sound.is_kana_variant(number) for number, _ in known)
    assert lq.sound_variants(TARGET, "vocab_meaning", {"c", "e", "g"}) == []
    # The reading kind (written word shown, kana options) keeps its bank when the known words are few.
    assert lq.sound_variants(TARGET, lq.READING_CHOICE_KIND, {"t", "a", "b"}) == list(enumerate(
        lq._sound_templates(TARGET, lq.READING_CHOICE_KIND)))


def test_a_known_variant_builds_and_rebuilds_the_same_snapshot(pool):
    (number, raw), *_ = lq.sound_variants(TARGET, lq.AUDIO_WORD_KIND, {"c", "e", "g", "h"})
    first = lq.build_learning_question(TARGET, lq.AUDIO_WORD_KIND, variant=number)
    again = lq.build_learning_question(deepcopy(TARGET), lq.AUDIO_WORD_KIND, variant=number)
    assert first == again and first["variant"] == number
    assert sorted(first["choices"]) == sorted(raw["choices"]) and first["answer"] == "切符"
    assert first["requires_audio"] is True and first["audio_text"] == "きっぷ"
    assert lq.grade_learning_answer(first, "切符")["status"] == "correct"
    assert lq.variant_template(TARGET, lq.AUDIO_WORD_KIND, number) == raw
    # Bank variants keep their numbers and questions.
    assert lq.build_learning_question(TARGET, lq.AUDIO_WORD_KIND, variant=1)["variant"] == 1


def test_candidates_offer_known_word_options_for_both_sound_kinds(pool):
    known = {"c", "e", "g", "h"}
    titles = {TITLES[i] for i in known}
    _, owners = sound._reading_options(TARGET, POOL)
    unknown_readings = {value for value, ids in owners.items() if not ids & known}
    for candidate in practice_candidates(TARGET, [lq.AUDIO_WORD_KIND, lq.READING_CHOICE_KIND],
                                         known_background_ids=known):
        question = lq.build_learning_question(TARGET, candidate["kind"], variant=candidate["variant"])
        assert not sound.is_kana_variant(candidate["variant"])  # withdrawn: never offered
        if candidate["kind"] == lq.AUDIO_WORD_KIND:
            assert distractors(question) <= titles
        else:
            assert not distractors(question) & unknown_readings
    # A learner who knows no other word is at the start: the bank's written words, as before 0.34.0, and
    # no kana spelling for any word.
    for item in (TARGET, ITEMS[4]):
        plain = [(c["kind"], c["variant"]) for c in practice_candidates(item, [lq.AUDIO_WORD_KIND])]
        assert plain == [(lq.AUDIO_WORD_KIND, 0), (lq.AUDIO_WORD_KIND, 1)]


def test_sound_kinds_require_nothing_but_their_target(pool, monkeypatch):
    bank = lq._sound_templates(TARGET, lq.AUDIO_WORD_KIND)
    # A pack written before this rule declared the bank's distractors as words.
    install(monkeypatch, namespace(("t", lq.AUDIO_WORD_KIND, bank[0], requirement(words=["a", "b", "c"])),
                                   ("t", lq.AUDIO_WORD_KIND, bank[1], requirement(words=["d", "e", "f"]))))
    assert lq.template_requires(TARGET, lq.AUDIO_WORD_KIND, bank[0]) == frozenset()
    known = {"c", "e", "g", "h"}
    offered = {(c["variant"], c["fallback"])
               for c in practice_candidates(TARGET, [lq.AUDIO_WORD_KIND], known_background_ids=known)}
    assert offered == {(number, False) for number, _ in sound.known_audio_word_variants(TARGET, POOL, known)}
    for variant in (0, sound.KANA_VARIANT_BASE):  # a stored kana-spelling question still rebuilds and declares nothing
        question = lq.build_learning_question(TARGET, lq.AUDIO_WORD_KIND, variant=variant)
        assert lq.question_requires(TARGET, question) == frozenset()
    # Without the namespace they stay undeclared (the fallback pass), as before.
    install(monkeypatch, {})
    assert lq.template_requires(TARGET, lq.AUDIO_WORD_KIND, bank[0]) is None
    assert {c["fallback"] for c in practice_candidates(TARGET, [lq.AUDIO_WORD_KIND],
                                                       known_background_ids=known)} == {True}


def test_course_plan_and_quiz_carry_known_word_variants(pool):
    known = {"c", "e", "g", "h"}
    numbers = [number for number, _ in lq.sound_variants(TARGET, lq.READING_CHOICE_KIND, known)]
    assert _quiz_variants(TARGET, lq.READING_CHOICE_KIND, known) == (*numbers[1:2], *numbers[:1], *numbers[2:])
    queries = SimpleNamespace(pending_abilities=lambda cid: set(), instruction_ready_ids=lambda: known)
    learning = SimpleNamespace(get_unit=lambda cid, ability: {"attempt_count": 0},
                               practice_history=lambda cid, ability: [])
    plans = [plan.question for plan in iter_question_plans(
        TARGET, enabled_kinds=[lq.AUDIO_WORD_KIND], queries=queries,
        learning_store=learning, system_japanese_input=False)]
    assert [(q["ability"], q["kind"]) for q in plans] == [("listening", lq.AUDIO_WORD_KIND)]
    assert sound.is_known_variant(plans[0]["variant"]) and not sound.is_kana_variant(plans[0]["variant"])
    # A learner without known words is planned a bank variant through the same path (the start), never a
    # kana spelling.
    queries.instruction_ready_ids = set
    plans = [plan.question for plan in iter_question_plans(
        TARGET, enabled_kinds=[lq.AUDIO_WORD_KIND], queries=queries,
        learning_store=learning, system_japanese_input=False)]
    assert len(plans) == 1 and not sound.is_known_variant(plans[0]["variant"])  # a bank number


# ------------------------------------- kana-spelling variants of the heard word

KANJI = "[㐀-鿿々]"
# (lesson, expected usable near-miss count): two give one three-option variant, fewer none.
KANA_CASES = [(TARGET, 4), (ITEMS[3], 3), (ITEMS[4], 1), (ITEMS[5], 2), (ITEMS[7], 5), (ITEMS[10], 7),
              (lesson("k", "テレビ", "テレビ"), 5), (lesson("s", "する", "する"), 3)]


@pytest.mark.parametrize("item, usable", KANA_CASES, ids=[item["id"] for item, _ in KANA_CASES])
def test_kana_variants_use_only_near_miss_spellings_of_the_lesson_itself(item, usable):
    invented = sound._invented_readings(item)
    templates = sound.kana_word_templates(item)
    assert len(invented) == usable
    assert len(templates) == (0 if usable < 2 else 1 if usable <= 3 else sound.KANA_VARIANTS)
    # The reading-choice options start with the same near-misses in the same order.
    assert sound._reading_options(item, POOL)[0][:usable] == invented
    groups = [invented[:3], invented[3:6] if usable >= 6 else invented[-3:]][:len(templates)]
    katakana = item["title"] == "テレビ"
    for raw, group in zip(templates, groups, strict=True):
        if katakana:
            group = ["".join(chr(ord(char) + 0x60) if char != "ー" else char for char in value) for value in group]
        assert raw["choices"] == [raw["answer"], *group]
        assert len(set(raw["choices"])) == min(usable, sound.OPTION_COUNT - 1) + 1
        assert raw["answer"] == item["reading"] and raw["audio_text"] == item["reading"]
        assert (raw["prompt"], raw["task"]) == (sound.AUDIO_KANA_PROMPT, sound.AUDIO_KANA_TASK)
        assert not re.search(KANJI, "".join(raw["choices"])) and item["title"] not in raw["choices"][1:]
        assert not {sound.normal_reading(choice) for choice in raw["choices"][1:]} & sound.word_readings(item)
        assert all(sound._kana_script(choice) == sound._kana_script(raw["answer"]) for choice in raw["choices"])
        assert raw["feedback"] == sound._feedback(item) and raw["strict"] is True
    if len(templates) == 2:
        assert set(templates[0]["choices"]) != set(templates[1]["choices"])
    # From the item alone: no pool, and the same result for a copy.
    assert sound.kana_word_templates(deepcopy(item)) == templates
    assert sound.kana_audio_word_variants(item) == [(sound.KANA_VARIANT_BASE + index, raw)
                                                    for index, raw in enumerate(templates)]


def test_exactly_two_usable_near_misses_give_one_three_option_variant(pool):
    item = ITEMS[5]  # 手 て: で, てい
    (number, raw), = sound.kana_audio_word_variants(item)
    assert number == sound.KANA_VARIANT_BASE and len(raw["choices"]) == 3
    assert set(raw["choices"][1:]) == set(sound._invented_readings(item)) == {"で", "てい"}
    # Withdrawn: it is no longer offered to the learner who knows no other word (nothing is), but a stored
    # one still rebuilds and grades.
    assert lq.sound_variants(item, lq.AUDIO_WORD_KIND, {item["id"]}) == list(enumerate(
        lq._sound_templates(item, lq.AUDIO_WORD_KIND)))
    question = lq.build_learning_question(item, lq.AUDIO_WORD_KIND, variant=number)
    assert lq.withdrawn_task(question) and not lq.withdrawn_task(lq.build_learning_question(item, lq.AUDIO_WORD_KIND))
    assert question == lq.build_learning_question(deepcopy(item), lq.AUDIO_WORD_KIND, variant=number)
    assert sorted(question["choices"]) == sorted(raw["choices"]) and question["answer"] == "て"
    assert lq.grade_learning_answer(question, "て")["status"] == "correct"
    assert [lq.grade_learning_answer(question, wrong)["status"] for wrong in ("で", "てい")] == ["incorrect"] * 2
    assert lq.variant_template(item, lq.AUDIO_WORD_KIND, number) == raw
    with pytest.raises(ValueError):
        lq.build_learning_question(item, lq.AUDIO_WORD_KIND, variant=number + 1)
    # The reading-choice options of the same word are untouched by the rule.
    assert sound._reading_options(item, POOL)[0][:2] == sound._invented_readings(item)


def test_a_vowel_kana_stretched_before_a_long_mark_is_not_a_kana_option():
    taxi = lesson("x", "タクシー", "タクシー")
    stretched = [value for value in sound._invented_readings(taxi) if re.search("[あいうえお]ー", value)]
    assert stretched == ["たくしいー"]
    # The reading-choice path keeps today's filter exactly: the spelling is still one of its options.
    assert "たくしいー" in sound._reading_options(taxi, POOL)[0]
    kept = [value for value in sound._invented_readings(taxi) if value not in stretched]
    templates = sound.kana_word_templates(taxi)
    to_katakana = str.maketrans({chr(code): chr(code + 0x60) for code in range(0x3041, 0x3097)})
    assert [raw["choices"][1:] for raw in templates] == [[value.translate(to_katakana) for value in group]
                                                         for group in (kept[:3], kept[3:6])]
    assert not any(re.search("[アイウエオ]ー", choice) for raw in templates for choice in raw["choices"])
    # A taught spelling that itself has a vowel kana before the long mark keeps its near-misses:
    # only a stretch the answer does not have is dropped.
    opened = lesson("o", "オープン", "オープン")
    (raw,) = sound.kana_word_templates(opened)
    assert raw["choices"] == ["オープン", *(value.translate(to_katakana) for value in sound._invented_readings(opened))]
    assert len(raw["choices"]) == 3 and all(choice.startswith("オー") for choice in raw["choices"])


def test_a_kana_title_is_its_own_answer_and_mixed_or_missing_fields_give_no_variant():
    taught = sound.kana_word_templates(lesson("k", "テレビ", "てれび"))
    assert taught and all(raw["answer"] == "テレビ" and raw["audio_text"] == "てれび" for raw in taught)
    assert all(sound._kana_script(choice) == "katakana" for raw in taught for choice in raw["choices"])
    assert sound.kana_word_templates(lesson("m", "消しゴム", "けしゴム")) == []  # two scripts: no single spelling
    assert sound.kana_word_templates({**TARGET, "meaning": ""}) == []
    assert sound.kana_word_templates({**TARGET, "reading": ""}) == []


def test_variant_number_ranges_never_meet():
    numbers = [number for number, _ in sound.kana_audio_word_variants(TARGET)]
    assert numbers == [1 << 52, (1 << 52) + 1] and all(number < 1 << 53 for number in numbers)
    assert all(sound.is_kana_variant(number) and sound.is_known_variant(number) for number in numbers)
    highest_known = sound._encode([sound._POSITION_RADIX - 1] * 3)
    assert sound.is_known_variant(highest_known) and highest_known < sound.KANA_VARIANT_BASE
    for value in (0, 1, sound.KNOWN_VARIANT_BASE, highest_known, True, "4503599627370496", None, 4503599627370496.0):
        assert not sound.is_kana_variant(value)
    with pytest.raises(ValueError):
        sound.known_variant_template(sound.READING_CHOICE, TARGET, POOL, sound.KANA_VARIANT_BASE)


def test_bank_and_known_numbers_are_unchanged(pool):
    # Literal values from before the kana-spelling variants existed.
    known = {"c", "e", "g", "h"}
    assert [n for n, _ in sound.known_audio_word_variants(TARGET, POOL, known)] == [281492156973064, 281500747038729]
    assert [n for n, _ in sound.known_reading_choice_variants(TARGET, POOL, known)] == [
        0, 281487862136842, 281526517170176, 281479271809027]
    assert [sorted(distractors(raw)) for raw in sound.audio_word_templates(TARGET, POOL)] == [
        ["キップ", "切手", "木"], ["書く", "生", "雑誌"]]
    assert [n for n, _ in lq.sound_variants(TARGET, lq.AUDIO_WORD_KIND, known)] == [
        281492156973064, 281500747038729]  # the kana-spelling numbers that followed them are withdrawn
    # The reading kind never had a kana-spelling variant: its lists are as before.
    assert [n for n, _ in lq.sound_variants(TARGET, lq.READING_CHOICE_KIND, known)] == [
        0, 281487862136842, 281526517170176, 281479271809027]
    assert lq.sound_variants(TARGET, lq.READING_CHOICE_KIND, {"t"}) == list(enumerate(
        sound.reading_choice_templates(TARGET, POOL)))


@pytest.mark.parametrize("kind", [lq.AUDIO_WORD_KIND, lq.READING_CHOICE_KIND])
def test_sound_variants_without_a_known_set_is_the_bank_enumeration(pool, kind):
    generator = sound.audio_word_templates if kind == lq.AUDIO_WORD_KIND else sound.reading_choice_templates
    for item in ITEMS:
        bank = list(enumerate(generator(item, POOL)))
        assert lq.sound_variants(item, kind) == lq.sound_variants(item, kind, None) == bank
        assert lq.practice_templates(item).get(kind, []) == [raw for _, raw in bank]


def test_exactly_three_known_candidates_give_one_listening_task_with_written_options(pool):
    known = {"c", "e", "g"}
    assert [n for n, _ in sound.known_audio_word_variants(TARGET, POOL, known)] == [281492157104137]
    candidates = list(practice_candidates(TARGET, [lq.AUDIO_WORD_KIND], known_background_ids=known))
    assert [c["variant"] for c in candidates] == [281492157104137]  # no kana-spelling variants beside it
    for candidate in candidates:
        question = lq.build_learning_question(TARGET, lq.AUDIO_WORD_KIND, variant=candidate["variant"])
        assert distractors(question).isdisjoint(set(TITLES.values()) - {TITLES[i] for i in known})


def test_a_kana_variant_rebuilds_from_its_number_and_is_graded_like_any_choice(pool, monkeypatch):
    for number, raw in sound.kana_audio_word_variants(TARGET):
        assert lq.variant_template(TARGET, lq.AUDIO_WORD_KIND, number) == raw
        assert sound.known_variant_template(sound.AUDIO_WORD, TARGET, (), number) == raw  # no pool needed
        first = lq.build_learning_question(TARGET, lq.AUDIO_WORD_KIND, variant=number)
        assert first == lq.build_learning_question(deepcopy(TARGET), lq.AUDIO_WORD_KIND, variant=number)
        assert first["variant"] == number and sorted(first["choices"]) == sorted(raw["choices"])
        assert (first["answer"], first["audio_text"], first["requires_audio"]) == ("きっぷ", "きっぷ", True)
        assert (first["ability"], first["kind"], first["strict"]) == ("listening", lq.AUDIO_WORD_KIND, True)
        # The written form stays hidden until the feedback; nothing optional reads the answer aloud.
        shown = first["prompt"] + first["task"] + "".join(first["choices"]) + first["optional_audio_text"]
        assert "切符" not in shown and not re.search(KANJI, "".join(first["choices"]))
        assert first["support"]["segments"] == [] and first["spans"] == []
        assert lq.grade_learning_answer(first, "きっぷ")["status"] == "correct"
        for wrong in distractors(first):
            assert lq.grade_learning_answer(first, wrong)["status"] == "incorrect"
        guided = lq.build_learning_question(TARGET, lq.AUDIO_WORD_KIND, variant=number, guided=True)
        assert guided["evidence_mode"] == "exposure_only" and sorted(guided["choices"]) == sorted(raw["choices"])
        # A later expansion changes the pool, never a stored kana-spelling question.
        monkeypatch.setattr(lq, "_word_pool", lambda: EXPANDED)
        assert lq.build_learning_question(TARGET, lq.AUDIO_WORD_KIND, variant=number) == first
        monkeypatch.setattr(lq, "_word_pool", lambda: POOL)
    with pytest.raises(ValueError):
        lq.build_learning_question(TARGET, lq.AUDIO_WORD_KIND, variant=sound.KANA_VARIANT_BASE + 2)
    with pytest.raises(ValueError):  # 目 has one usable near-miss: no kana-spelling task to rebuild
        lq.build_learning_question(ITEMS[4], lq.AUDIO_WORD_KIND, variant=sound.KANA_VARIANT_BASE)
    with pytest.raises(ValueError):
        lq.build_learning_question(TARGET, lq.READING_CHOICE_KIND, variant=sound.KANA_VARIANT_BASE)


def test_a_kana_variant_is_shown_as_study_text_with_a_hidden_stimulus(pool):
    from kanamemory.question_presentation import question_presentation
    from kanamemory.question_support import learning_text_role, prompt_audio_text, prompt_support
    question = lq.build_learning_question(TARGET, lq.AUDIO_WORD_KIND, variant=sound.KANA_VARIANT_BASE)
    bank = lq.build_learning_question(TARGET, lq.AUDIO_WORD_KIND, variant=0)
    for built, task in ((question, sound.AUDIO_KANA_TASK), (bank, sound.AUDIO_TASK)):
        view = question_presentation(built)
        assert (view["instruction"], view["instruction_role"], view["target_spans"]) == (task, "study", [])
        assert prompt_audio_text(built) is None and prompt_support(built)["furigana_visible"] is False
        assert learning_text_role(built, "choice") == "study"


def test_legacy_field_uses_name_the_reading_for_a_kana_variant(pool):
    def uses(item, variant):
        question = lq.build_learning_question(item, lq.AUDIO_WORD_KIND, variant=variant)
        return {entry["field"]: {use["slot"] for use in entry["uses"]}
                for entry in question["content_dependencies"]["entries"]}
    legacy = {key: value for key, value in TARGET.items() if key != "question_templates"}
    answer_slots = {"answer", "accepted_answers", "choices", "options"}
    assert uses(legacy, 0) == {"meaning": {"feedback"}, "reading": {"audio_text"}, "title": answer_slots}
    assert uses(legacy, sound.KANA_VARIANT_BASE) == {"meaning": {"feedback"},
                                                     "reading": {"audio_text", *answer_slots}}
    kana = {key: value for key, value in lesson("j2", "きっぷく", "きっぷく").items() if key != "question_templates"}
    assert uses(kana, sound.KANA_VARIANT_BASE) == {"meaning": {"feedback"}, "title": answer_slots,
                                                   "reading": {"audio_text", *answer_slots}}


# ------------------------------------------- stable bank pool (design 11 R1)

EXPANSION = sound.EXPANSION_SOURCE_PREFIX + "20261109"
# Sound-alikes of 切符 that an expansion adds: they outrank the original distractors.
ADDED = [lesson("x", "喫茶", "きっさ", source_ids=["test", EXPANSION]),
         lesson("y", "菊花", "きっか", source_ids=[EXPANSION]),
         lesson("z", "急度", "きっと", source_ids=[sound.EXPANSION_SOURCE_PREFIX + "20270101", EXPANSION])]
EXPANDED = sound.word_pool(ITEMS + ADDED)


def test_the_cohort_is_the_earliest_expansion_source_of_the_lesson_itself():
    assert [sound.word_cohort(item) for item in (TARGET, *ADDED)] == ["", "20261109", "20261109", "20261109"]
    assert sound.word_cohort({**TARGET, "source_ids": "kanamemory-course-expansion-20261109"}) == ""
    assert {row[0]: row[4] for row in EXPANDED}["x"] == "20261109"


def test_an_expansion_never_changes_the_bank_variants_of_earlier_words():
    # The expansion does reorder the whole-pool ranking of the original target ...
    assert sound._audio_candidates(TARGET, EXPANDED)[:4] != sound._audio_candidates(TARGET, POOL)[:4]
    # ... but every original word keeps its bank options exactly (both kinds).
    for item in ITEMS:
        assert sound.audio_word_templates(item, EXPANDED) == sound.audio_word_templates(item, POOL), item["id"]
        assert sound.reading_choice_templates(item, EXPANDED) == sound.reading_choice_templates(item, POOL), item["id"]
    assert {row[0] for row in sound.bank_pool(TARGET, EXPANDED)} == {item["id"] for item in ITEMS}


def test_expansion_words_draw_their_bank_from_the_whole_pool():
    for item in ADDED:
        assert sound.bank_pool(item, EXPANDED) == EXPANDED
    # A hand-built four-field pool has no cohorts: nothing is filtered.
    four = tuple(row[:4] for row in EXPANDED)
    assert sound.bank_pool(TARGET, four) == four


def test_known_variants_still_rank_the_whole_pool_and_rebuild_from_their_number():
    known = {"x", "y", "z", "a"}
    variants = sound.known_audio_word_variants(TARGET, EXPANDED, known)
    bank = sound.audio_word_templates(TARGET, EXPANDED)
    assert variants and any(distractors(raw) & {"喫茶", "菊花", "急度"} for _, raw in variants)
    for number, raw in variants:
        # A bank number is kept only for an identical template; otherwise the encoded
        # positions name the whole-pool ranking, exactly as before the rule.
        if sound.is_known_variant(number):
            assert raw not in bank
            assert sound.known_variant_template(sound.AUDIO_WORD, TARGET, EXPANDED, number) == raw
        else:
            assert bank[number] == raw
    everyone = {row[0] for row in EXPANDED}
    for kind, generator, templates in ((sound.AUDIO_WORD, sound.known_audio_word_variants, sound.audio_word_templates),
                                       (sound.READING_CHOICE, sound.known_reading_choice_variants,
                                        sound.reading_choice_templates)):
        bank = templates(TARGET, EXPANDED)
        for number, raw in generator(TARGET, EXPANDED, everyone):
            assert (bank[number] if not sound.is_known_variant(number)
                    else sound.known_variant_template(kind, TARGET, EXPANDED, number)) == raw


def test_a_stored_bank_question_rebuilds_identically_after_an_expansion(monkeypatch):
    install(monkeypatch, {})
    monkeypatch.setattr(lq, "_word_pool", lambda: POOL)
    before = [lq.build_learning_question(TARGET, kind, variant=variant)
              for kind in (lq.AUDIO_WORD_KIND, lq.READING_CHOICE_KIND) for variant in range(2)]
    monkeypatch.setattr(lq, "_word_pool", lambda: EXPANDED)
    after = [lq.build_learning_question(TARGET, kind, variant=variant)
             for kind in (lq.AUDIO_WORD_KIND, lq.READING_CHOICE_KIND) for variant in range(2)]
    assert after == before


# ------------------------------------------------------------ known kana (D5)

KANA_A = "kana:kana-3042:hiragana"


def test_known_set_adds_introduced_kana_once_per_planning_pass(monkeypatch):
    item = grammar()
    meaning = item["question_templates"]["grammar_meaning"]
    install(monkeypatch, namespace((GRAMMAR_ID, "grammar_meaning", meaning[0], requirement(kana=[KANA_A])),
                                   (GRAMMAR_ID, "grammar_meaning", meaning[1], requirement(kana=[KANA_A])),
                                   (GRAMMAR_ID, "grammar_meaning", meaning[2], requirement(words=["word:z"]))))
    calls = []

    def kana():
        calls.append(1)
        return {KANA_A}
    queries = SimpleNamespace(pending_abilities=lambda cid: set(), instruction_ready_ids=set,
                              introduced_kana_ids=kana)
    learning = SimpleNamespace(get_unit=lambda cid, ability: {"attempt_count": 0},
                               practice_history=lambda cid, ability: [])
    plans = [plan.question for plan in iter_question_plans(
        item, enabled_kinds=["grammar_meaning", "grammar_cloze"], queries=queries, learning_store=learning,
        system_japanese_input=False)]
    assert calls == [1]
    assert (plans[0]["kind"], plans[0]["variant"]) == ("grammar_meaning", 0)
    # A reader without the kana query still plans, with ready items only.
    assert known_ids(SimpleNamespace(instruction_ready_ids=lambda: {"x"})) == frozenset({"x"})
    assert known_ids(queries) == frozenset({KANA_A})
