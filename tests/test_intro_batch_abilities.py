"""四之二 items 4–6: ability gating, later-ability first questions, card lookup, +1 context.

Synthetic lessons only.
"""
from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace

import pytest

from support.learning_fixtures import learning_app, click
from support.course_support import batch_quiz_question
from support.app_fixtures import question_app, setup_app  # noqa: F401 (fixtures used by learning_app)
from support.fake_ui import text_of, descendants
from support.learning_practice_policy_support import raw
from kanamemory import intro_batch
from kanamemory.course_planning import iter_question_plans
from kanamemory.course_line.batch_display import CARD_BUTTON
from kanamemory.intro_batch_quiz import intro_quiz_question
from kanamemory.learning_questions import PREFERENCE_VERSION, SOUND_PREFERENCE_VERSION
from kanamemory.learning_ui import LEARNING_PREFS

CID = "word:synthetic:r1"
KINDS = ["vocab_meaning", "vocab_reading_choice", "vocab_reading", "vocab_usage", "grammar_meaning"]


def prefs(kinds):
    return {"enabled": list(kinds), SOUND_PREFERENCE_VERSION: PREFERENCE_VERSION}


def choice(prompt, answer, other, **extra):
    return {"prompt": prompt, "answer": answer, "choices": [answer, other], "feedback": "说明",
            "input_mode": "choice", "safe": True, **extra}


def word(cid=CID, abilities=("understanding", "reading", "use"), example="語一です。"):
    return {"id": cid, "content_id": cid, "family": "word", "title": "語一", "reading": "ごいち",
            "meaning": "词一", "revision": "1", "abilities": list(abilities), "source_ids": ["test"],
            "instruction": {"summary": "词一", "rule": "名词。", "steps": [], "example": example,
                            "translation": "是词一。", "prerequisite": "", "contrast": ""},
            "question_templates": {
                "vocab_meaning": [choice("語一", "词一", "别的词")],
                "vocab_reading_choice": [choice("語一", "ごいち", "ごに", task="読み方")],
                "vocab_reading": [{"prompt": "語一", "answer": "ごいち", "input_mode": "text",
                                   "feedback": "说明", "safe": True}],
                "vocab_usage": [choice("＿＿です。", "語一", "語二")]}}


def later(app, seconds=61):
    app._test_clock[0] += timedelta(seconds=seconds)


def through_batch(app, item=None, kinds=KINDS):
    app.store.set_setting(LEARNING_PREFS, prefs(kinds))
    app.learning_store.enroll([item or word()])
    app.continue_learning()
    assert app.page == "learning_card"
    click(app, "下一项")
    click(app, batch_quiz_question(app)["answer"])
    click(app, "继续")
    assert app.learning_store.intro_batch() is None


def answer_formally(app, rating="记住了"):
    q = app._learning_question
    click(app, q["answer"])
    assert app.page == "learning_feedback"
    click(app, rating)
    return q


def gate(app, kinds=KINDS):
    return app.learning_store.batch_ability_gate(CID, kinds)


def test_pure_order_skips_disabled_and_unadmitted_abilities():
    order = dict(admitted={"understanding", "reading", "use"},
                 enabled_abilities={"understanding", "reading", "use"})
    assert intro_batch.ability_open("word", "understanding", formally_correct=set(), **order)
    assert not intro_batch.ability_open("word", "reading", formally_correct=set(), **order)
    assert intro_batch.ability_open("word", "reading", formally_correct={"understanding"}, **order)
    assert not intro_batch.ability_open("word", "use", formally_correct={"understanding"}, **order)
    # A disabled earlier ability is skipped, as is one that was never admitted.
    assert intro_batch.ability_open("word", "use", admitted={"understanding", "reading", "use"},
                                    enabled_abilities={"understanding", "use"}, formally_correct={"understanding"})
    assert intro_batch.ability_open("word", "listening", admitted={"understanding", "listening"},
                                    enabled_abilities={"understanding", "reading", "listening"},
                                    formally_correct={"understanding"})
    assert not intro_batch.ability_open("grammar", "application", admitted={"understanding", "application"},
                                        enabled_abilities={"understanding", "application"}, formally_correct=())
    choice_kind, text_kind = intro_batch.READING_KIND_ORDER
    both = [choice_kind, text_kind]
    assert intro_batch.reading_kinds_open(both, enabled_kinds=both, formally_correct_kinds=()) == [choice_kind]
    assert intro_batch.reading_kinds_open(both, enabled_kinds=both, formally_correct_kinds={choice_kind}) == both
    assert intro_batch.reading_kinds_open(both, enabled_kinds=[text_kind], formally_correct_kinds=()) == both
    assert intro_batch.reading_kinds_open([text_kind], enabled_kinds=both, formally_correct_kinds=()) == [text_kind]


def test_gate_order_counts_hint_and_reading_choice_before_text(learning_app):
    app = learning_app
    through_batch(app)
    store = app.learning_store
    assert gate(app)["closed"] == {"reading", "use", "listening"}
    assert gate(app)["closed_kinds"] == {"vocab_reading"}
    # The closed later abilities are "not yet open": not instruction, not disabled.
    counts = store.counts(KINDS)
    assert (counts["ready"], counts["waiting"], counts["instruction"], counts["disabled"]) == (0, 1, 0, 0)
    assert store.current(KINDS)["type"] == "waiting"
    later(app)
    counts = store.counts(KINDS)
    assert (counts["ready"], counts["instruction"], counts["disabled"]) == (1, 0, 0)
    assert store.current(KINDS)["question"]["ability"] == "understanding"
    app.continue_learning()
    assert app.page == "learning_question" and app._learning_question["kind"] == "vocab_meaning"
    assert CARD_BUTTON in text_of(app.body) and "新能力" not in text_of(app.body)
    answer_formally(app)
    assert gate(app)["closed"] == {"use", "listening"}
    later(app)
    app.continue_learning()
    # Reading opens without an intro page or familiarity picker.
    assert app.page == "learning_question", app.page
    q = app._learning_question
    assert q["ability"] == "reading" and q["kind"] == "vocab_reading_choice"
    body = text_of(app.body)
    assert "新能力：读法 — 先选读音" in body and CARD_BUTTON in body and "初学" not in body
    reading = store.instruction_item(CID, "reading")
    profile = reading["checkpoint"]["initial_familiarity"]
    assert profile["levels"] == {"reading": "new"} and profile["route"] == "full"
    assert profile["source"] == intro_batch.CARD_SOURCE and reading["stage"] == "bridge"
    assert "instruction_exposed_at_utc" not in reading["checkpoint"]
    assert store.counts(KINDS)["ready"] == 1
    answer_formally(app)
    # Listening is not admitted, so use opens straight after reading.
    assert gate(app)["closed_kinds"] == set() and gate(app)["closed"] == set()
    assert store.get_unit(CID, "reading")["review_count"] == 1


def test_disabled_earlier_kind_does_not_block_a_later_ability(learning_app):
    app = learning_app
    through_batch(app)
    without_meaning = [kind for kind in KINDS if kind != "vocab_meaning"]
    assert "reading" not in gate(app, without_meaning)["closed"]
    assert "use" in gate(app, without_meaning)["closed"]
    # Without the reading choice, the written reading is not held back.
    without_choice = [kind for kind in KINDS if kind != "vocab_reading_choice"]
    assert gate(app, without_choice)["closed_kinds"] == set()


def test_planning_offers_written_reading_only_after_a_formal_choice(learning_app):
    app = learning_app
    through_batch(app)
    queries = SimpleNamespace(pending_abilities=lambda cid: set(), instruction_ready_ids=lambda: set())

    def reading_kinds(kinds):
        return {plan["kind"] for _, plan in iter_question_plans(
            app.learning_store.get_item(CID), enabled_kinds=kinds, queries=queries,
            learning_store=app.learning_store, system_japanese_input=True) if plan["ability"] == "reading"}
    assert reading_kinds(KINDS) == {"vocab_reading_choice"}
    assert reading_kinds(["vocab_reading"]) == {"vocab_reading"}


def test_unmarked_item_with_the_same_abilities_is_not_gated(learning_app):
    app = learning_app
    app.store.set_setting(LEARNING_PREFS, prefs(KINDS))
    app.learning_store.enroll([word()])
    saved = app.learning_store.get_item(CID)
    app.learning_store.set_initial_familiarity(CID, {a: "new" for a in ("understanding", "reading", "use")},
                                               expected_introduction_id=saved["introduction_id"])
    app.continue_learning()
    assert app.page == "learning_intro" and app.learning_store.intro_batch() is None
    assert app.learning_store.batch_ability_gate(CID, KINDS) is None
    queries = SimpleNamespace(pending_abilities=lambda cid: set(), instruction_ready_ids=lambda: set())
    kinds = {plan["kind"] for _, plan in iter_question_plans(
        app.learning_store.get_item(CID), enabled_kinds=["vocab_reading"], queries=queries,
        learning_store=app.learning_store, system_japanese_input=True)}
    assert kinds == {"vocab_reading"}
    # All three abilities share the one legacy introduction, as before.
    assert app.learning_store.counts(KINDS)["instruction"] == 1


def test_card_lookup_marks_the_answer_assisted_and_does_not_open_the_next_ability(learning_app):
    app = learning_app
    through_batch(app)
    later(app)
    app.continue_learning()
    q = app._learning_question
    click(app, CARD_BUTTON)
    assert app.page == "learning_prerequisite" and "語一" in text_of(app.body)
    assert app.learning_store.get_question(q["id"])["draft"]["assisted"] is True
    click(app, "返回题目")
    assert app.page == "learning_question" and app._learning_question["id"] == q["id"]
    click(app, q["answer"])
    # Assisted correct: the ordinary rule offers no success rating.
    assert "记住了" not in text_of(app.body)
    click(app, "保存并继续")
    event = app.learning_store._db.execute("SELECT assisted,rating FROM learning_event WHERE question_id=?",
                                           (q["id"],)).fetchone()
    assert event["assisted"] == 1 and event["rating"] == 1
    assert "reading" in gate(app)["closed"]


def test_listening_admission_respects_the_gate_and_keeps_due_times(learning_app):
    app = learning_app
    through_batch(app, word(abilities=("understanding",)))
    store = app.learning_store
    assert store.admit_word_listening(enabled_kinds=KINDS) == []
    later(app)
    app.continue_learning()
    answer_formally(app)
    due = store.get_unit(CID, "understanding")["due_at_utc"]
    assert due
    assert store.admit_word_listening(enabled_kinds=KINDS) == [CID]
    assert store.get_unit(CID, "understanding")["due_at_utc"] == due
    assert store.get_unit(CID, "listening")["due_at_utc"] is None


def test_listening_waits_for_an_unpassed_earlier_ability(learning_app):
    app = learning_app
    through_batch(app, word(abilities=("understanding", "reading")))
    later(app)
    app.continue_learning()
    answer_formally(app)
    # Understanding passed, reading admitted and enabled but not yet passed.
    assert app.learning_store.admit_word_listening(enabled_kinds=KINDS) == []
    without_reading = [kind for kind in KINDS if kind not in ("vocab_reading", "vocab_reading_choice")]
    assert app.learning_store.admit_word_listening(enabled_kinds=without_reading) == [CID]


class FakeExamples:
    def __init__(self, examples):
        self.examples = examples

    def read_operation(self):
        return nullcontext()

    def for_item_page(self, target_id, *, offset=0, limit=8):
        rows = [dict(e) for e in self.examples]
        return {"total": len(rows), "examples": rows[offset:None if limit is None else offset + limit]}

    def references(self, example_id, current_target_id=None):
        return [r for e in self.examples if e["id"] == example_id for r in e["refs"]]

    def reference_targets(self, example_id):
        return {r["target_id"] for r in self.references(example_id)}


def example(eid, text, *others):
    spans = [{"start": 0, "end": 2, "quote": "語一"}]
    own = {"id": eid + ":own", "target_id": CID, "spans": spans, "hints": [], "hint": {},
           "target": {"id": CID, "family": "word"}}
    refs = [own, *({"id": f"{eid}:{other}", "target_id": other, "hints": [], "hint": {},
                    "spans": [{"start": len(text) - 3, "end": len(text) - 2, "quote": text[-3]}],
                    "target": {"id": other, "family": "grammar"}} for other in others)]
    return {"id": eid, "text": text, "translation": "", "hints": [], "reference": own, "refs": refs}


def card_examples(app, examples, known=()):
    service = FakeExamples(examples)
    app.example_store = lambda: service
    item = {**word(), "id": CID}
    return app.example_cards(item, limit=1, known_ids=set(known))


def highlights(widget):
    return [h["quote"] for view in [widget, *descendants(widget)] for h in getattr(view, "_example_highlights", ())]


def test_card_example_uses_only_known_background_and_highlights_the_item(learning_app):
    app = learning_app
    unknown = example("e1", "語一と猫だ。", "grammar:unknown")
    batch = example("e2", "語一と本だ。", "grammar:batch")
    result = card_examples(app, [unknown, batch], known={"grammar:batch"})
    assert result._feedback_fragment["ordered_example_ids"] == ["e2"]
    assert "語一" in highlights(result)
    # No qualifying shared sentence: the lesson sentence has no references.
    result = card_examples(app, [unknown])
    assert result._feedback_fragment["kind"] == "example_fallback"
    assert "語一です。" in text_of(result) and "語一" in highlights(result)
    # The lesson sentence itself names an unknown item: show no example.
    withheld = example("e3", "語一です。", "grammar:unknown")
    result = card_examples(app, [withheld])
    assert result._feedback_fragment["kind"] == "example_withheld" and not result.children


def test_quiz_prefers_a_question_whose_background_is_known():
    item = {**word(abilities=("understanding",)), "question_templates": {
        "vocab_meaning": [raw("a"), raw("b", background=["word:synthetic:other"])],
        "vocab_zh_ja_contrast": [raw("c"), raw("d")]}, "enrolled_abilities": ["understanding"]}
    plain = intro_quiz_question(item, ["vocab_meaning", "vocab_zh_ja_contrast"])
    assert (plain["kind"], plain["variant"]) == ("vocab_meaning", 1)
    # WP-K1 (design 11 section 5, D3): the unmet second variant is never the
    # quiz question; the recognition kind's self-contained variant comes before
    # a less preferred kind (previously the contrast kind was taken).
    filtered = intro_quiz_question(item, ["vocab_meaning", "vocab_zh_ja_contrast"], known_background_ids=set())
    assert (filtered["kind"], filtered["variant"]) == ("vocab_meaning", 0)
    only_unmet = {**item, "question_templates": {**item["question_templates"],
                  "vocab_meaning": [raw("b", background=["word:synthetic:other"])] * 2}}
    contrast = intro_quiz_question(only_unmet, ["vocab_meaning", "vocab_zh_ja_contrast"], known_background_ids=set())
    assert contrast["kind"] == "vocab_zh_ja_contrast"
    known = intro_quiz_question(item, ["vocab_meaning", "vocab_zh_ja_contrast"],
                                known_background_ids={"word:synthetic:other"})
    assert (known["kind"], known["variant"]) == ("vocab_meaning", 1)
