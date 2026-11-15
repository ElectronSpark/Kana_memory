"""The batch introduction as a service: real stores, synthetic lessons, no app object and no page
(design 13, C1 and C3).

Whether a head opens or resumes a batch, what the batch does next, the exposure of a card and the card that
writes nothing, a card's choice, the quiz frozen once, a closed kind, the answers, the batch's end, a later
ability, the help of a formal question and the review hint's count — each asked of BatchIntroduction built from
a StudyStore, a LearningStore and three callables.
"""
from types import SimpleNamespace

import pytest

from kanamemory import intro_batch
from kanamemory.course_line.batch import BatchIntroduction
from kanamemory.course_line.batch_display import ability_hint
from kanamemory.course_line.batch_model import (BatchClosed, BatchHelp, BatchOver, CardOpened, CardStep,
                                                CardUnavailable, QuizAnswered, QuizAsked, QuizPaused, QuizSettled,
                                                QuizStale, QuizStep, QuizUnavailable)
from kanamemory.domain import StudyStore
from kanamemory.learning_questions import build_learning_question
from kanamemory.learning_store import LearningStore
from kanamemory.question_support import learning_text_role

KINDS = ["vocab_meaning", "grammar_meaning", "pronunciation_rule"]
W1, W2 = "word:synthetic:w1", "word:synthetic:w2"


def choice(prompt, answer, other):
    return [{"prompt": prompt, "answer": answer, "choices": [answer, other], "feedback": "说明",
             "input_mode": "choice", "safe": True}]


def word(n):
    cid = f"word:synthetic:w{n}"
    return {"id": cid, "content_id": cid, "family": "word", "title": f"語{n}", "reading": "ご",
            "meaning": f"词{n}", "revision": "1", "abilities": ["understanding"], "source_ids": ["test"],
            "instruction": {"summary": f"词{n}", "rule": "名词。", "steps": [], "example": f"語{n}です。",
                            "translation": f"是词{n}。", "prerequisite": "", "contrast": ""},
            "question_templates": {"vocab_meaning": choice(f"語{n}", f"词{n}", "别的词")}}


class Kinds:
    """The kinds this device can present, counting how often they are asked."""

    def __init__(self, kinds):
        self.kinds, self.asked = list(kinds), 0

    def __call__(self):
        self.asked += 1
        return list(self.kinds)


@pytest.fixture
def stores(tmp_path):
    store = StudyStore(tmp_path / "batch-service", time_zone="UTC")
    yield store, LearningStore(store)
    store.close()


def batch_over(stores, kinds=KINDS, *, fair=False, route=False):
    store, learning_store = stores
    return BatchIntroduction(learning_store, store, enabled_kinds=Kinds(kinds), fair_new=lambda: fair,
                             route_available=lambda: route)


def enroll(stores, *items):
    """These lessons joined the course and, as the course line does before it asks for its head, each got its
    first question queued."""
    stores[1].enroll(list(items))
    for item in stores[1].items_needing_questions():
        stores[1].append_questions([build_learning_question(item, "vocab_meaning", variant=0)])


def open_batch(stores, *items):
    """A batch opened at the course head for these new lessons; returns the service and the batch's entries."""
    enroll(stores, *items)
    batch = batch_over(stores)
    assert batch.route(stores[1].current(KINDS), KINDS) is True
    return batch, stores[1].intro_batch()["entries"]


def record(target, *names):
    """Record the calls of these methods of ``target`` (they still run): [(name, args, kwargs), ...]."""
    log = []
    for name in names:
        original = getattr(target, name)

        def call(*args, _name=name, _original=original, **kwargs):
            log.append((_name, args, kwargs))
            return _original(*args, **kwargs)
        setattr(target, name, call)
    return log


def to_quiz(batch, entries):
    for entry in entries:
        batch.choose_card(entry, "next")
    return batch.open_quiz()


# --- whether a head opens or resumes a batch -------------------------------------------------------------

def test_a_new_head_opens_a_batch_of_its_items(stores):
    enroll(stores, word(1), word(2))
    batch = batch_over(stores)
    writes = record(stores[1], "start_intro_batch")
    head = stores[1].current(KINDS)
    assert head["type"] == "instruction" and head["content"]["id"] == W1
    assert batch.route(head, KINDS) is True
    assert writes == [("start_intro_batch", ([W1, W2],), {"family": "word"})]
    state = stores[1].intro_batch()
    assert [entry["content_id"] for entry in state["entries"]] == [W1, W2]
    assert (state["phase"], state["card_index"]) == ("cards", 0)


def test_an_open_batch_takes_the_heads_of_its_own_items_and_writes_nothing_more(stores):
    batch, entries = open_batch(stores, word(1), word(2))
    writes = record(stores[1], "start_intro_batch")
    before = stores[1].intro_batch()
    for cid in (W1, W2):
        assert batch.route({"type": "instruction", "content": {"id": cid}}, KINDS) is True
    assert batch.route({"type": "instruction", "content": {"id": "word:other"}}, KINDS) is False
    assert batch.route({"type": "instruction", "content": {"id": W1, "instruction_scope": "reading"}}, KINDS) is False
    assert batch.route({"type": "question", "content": {"id": W1}}, KINDS) is False
    assert batch.route(None, KINDS) is False
    assert writes == [] and stores[1].intro_batch() == before


@pytest.mark.parametrize("head", [
    None,
    {"type": "question"},
    {"type": "instruction", "content": {"id": W1, "family": "word", "stage": "intro", "instruction_scope": "reading"}},
    {"type": "instruction", "content": {"id": "p:1", "family": "pronunciation", "stage": "intro"}},
    {"type": "instruction", "content": {"id": W1, "family": "word", "stage": "bridge"}},
    {"type": "instruction", "content": {"id": W1, "family": "word", "stage": "intro",
                                        "checkpoint": {"initial_familiarity": {"route": "compact"}}}},
])
def test_a_head_that_is_not_a_fresh_word_or_grammar_lesson_opens_no_batch(stores, head):
    enroll(stores, word(1))
    asked = record(stores[1], "intro_batch_candidates", "start_intro_batch")
    assert batch_over(stores).route(head, KINDS) is False
    assert asked == [] and stores[1].intro_batch() is None


def test_a_lesson_without_a_presentable_quiz_question_opens_no_batch(stores):
    enroll(stores, word(1))
    head = stores[1].current(KINDS)
    writes = record(stores[1], "start_intro_batch")
    assert batch_over(stores, ["grammar_meaning"]).route(head, ["grammar_meaning"]) is False
    assert writes == [] and stores[1].intro_batch() is None


# --- the cards -------------------------------------------------------------------------------------------

def test_the_batch_goes_through_its_cards_and_then_to_its_quiz(stores):
    batch, entries = open_batch(stores, word(1), word(2))
    assert batch.advance() == CardStep(entries[0])
    batch.choose_card(entries[0], "next")
    assert batch.advance() == CardStep(entries[1])
    batch.choose_card(entries[1], "next")
    assert batch.advance() == QuizStep()
    assert stores[1].intro_batch()["phase"] == "cards"   # the quiz is frozen when it is first shown, not here


def test_opening_a_card_records_its_exposure(stores):
    batch, entries = open_batch(stores, word(1), word(2))
    assert "instruction_exposed_at_utc" not in stores[1].instruction_item(W2)["checkpoint"]
    writes = record(stores[1], "mark_instruction_exposed")
    opened = batch.open_card(entries[1])
    assert isinstance(opened, CardOpened)
    assert writes == [("mark_instruction_exposed", (W2,), {"expected_introduction_id": entries[1]["introduction_id"]})]
    assert opened.item == stores[1].instruction_item(W2) and opened.item["checkpoint"]["instruction_exposed_at_utc"]
    assert opened.entry == entries[1] and opened.entry is not entries[1]
    assert (opened.index, opened.total) == (1, 2)


def test_a_card_whose_lesson_was_introduced_anew_or_is_gone_writes_nothing(stores):
    batch, entries = open_batch(stores, word(1))
    before = stores[1].instruction_item(W1)
    writes = record(stores[1], "mark_instruction_exposed")
    assert batch.open_card(dict(entries[0], introduction_id="another")) == CardUnavailable()
    assert batch.open_card({"content_id": "word:gone", "introduction_id": "x"}) == CardUnavailable()
    assert writes == [] and stores[1].instruction_item(W1) == before


def test_a_repeated_card_choice_is_refused_by_the_store_and_changes_nothing(stores):
    batch, entries = open_batch(stores, word(1), word(2))
    batch.choose_card(entries[0], "next")
    saved = stores[1].intro_batch()
    batch.choose_card(entries[0], "next")
    batch.choose_card(entries[0], "known")
    assert stores[1].intro_batch() == saved and saved["card_index"] == 1


def test_fair_sharing_is_asked_only_for_a_known_card(stores):
    enroll(stores, word(1), word(2))
    fair = []
    batch = BatchIntroduction(stores[1], stores[0], enabled_kinds=Kinds(KINDS),
                              fair_new=lambda: fair.append("asked") or True, route_available=lambda: False)
    assert batch.route(stores[1].current(KINDS), KINDS) is True
    entries = stores[1].intro_batch()["entries"]
    fair.clear()
    writes = record(stores[1], "choose_intro_card")
    batch.choose_card(entries[0], "next")
    assert fair == [] and writes[-1][2]["fair_new"] is False
    batch.choose_card(entries[1], "known")
    assert fair == ["asked"] and writes[-1][2]["fair_new"] is True
    assert [call[1] for call in writes] == [(W1, "next"), (W2, "known")]


def test_a_batch_whose_every_card_was_known_is_closed_with_its_two_writes_in_order(stores):
    batch, entries = open_batch(stores, word(1))
    batch.choose_card(entries[0], "known")
    writes = record(stores[1], "start_intro_quiz", "complete_intro_batch")
    assert batch.advance() == BatchClosed()
    assert writes == [("start_intro_quiz", ({},), {}), ("complete_intro_batch", (), {"fair_new": False})]
    assert stores[1].intro_batch() is None
    assert batch.advance() == BatchOver() and len(writes) == 2


# --- the quiz --------------------------------------------------------------------------------------------

def test_the_quiz_is_frozen_once_when_it_is_first_shown(stores):
    batch, entries = open_batch(stores, word(1), word(2))
    for entry in entries:
        batch.choose_card(entry, "next")
    writes = record(stores[1], "start_intro_quiz")
    asked = batch.open_quiz()
    assert isinstance(asked, QuizAsked) and len(writes) == 1
    assert set(writes[0][1][0]) == {W1, W2}
    state = stores[1].intro_batch()
    assert state["phase"] == "quiz"
    head = intro_batch.quiz_head(state)
    assert (asked.item["id"], asked.question["id"]) == (head["content_id"], head["question_id"])
    assert asked.question in asked.item["checkpoint"][intro_batch.ITEM_KEY]["questions"]
    assert asked.question["kind"] == "vocab_meaning" and {W1, W2} <= set(asked.known)
    assert asked.role == learning_text_role(asked.question, "choice")
    assert set(asked.labels) <= set(asked.question["choices"])
    again = batch.open_quiz()
    assert len(writes) == 1 and again.question == asked.question and stores[1].intro_batch() == state


def test_the_quiz_cannot_start_while_an_item_has_no_question_of_an_open_kind(stores):
    batch, entries = open_batch(stores, word(1))
    batch.choose_card(entries[0], "next")
    saved = stores[1].intro_batch()
    closed = batch_over(stores, ["grammar_meaning"])
    assert closed.open_quiz() == QuizUnavailable("这一批里有内容暂时没有可用的小测题型，请检查题型设置。")
    assert stores[1].intro_batch() == saved and saved["phase"] == "cards"


def test_a_closed_kind_pauses_the_frozen_head_and_an_answer_to_it_is_not_judged(stores):
    batch, entries = open_batch(stores, word(1))
    asked = to_quiz(batch, entries)
    saved = stores[1].intro_batch()
    closed = batch_over(stores, [])
    writes = record(stores[1], "answer_intro_quiz")
    paused = closed.open_quiz()
    assert isinstance(paused, QuizPaused) and paused.item["id"] == W1
    assert closed.answer(asked.question, asked.question["answer"]) == QuizPaused(None)
    assert writes == [] and stores[1].intro_batch() == saved
    assert batch.open_quiz().question == asked.question


def test_an_empty_answer_does_nothing_and_a_right_one_settles_the_item(stores):
    batch, entries = open_batch(stores, word(1))
    asked = to_quiz(batch, entries)
    writes = record(stores[1], "answer_intro_quiz")
    assert batch.answer(asked.question, "") is None and writes == []
    assert batch.quiz_done() is False
    assert batch.answer(asked.question, asked.question["answer"]) == QuizAnswered(True)
    assert writes == [("answer_intro_quiz", (W1, asked.question["id"]),
                       {"answered_text": asked.question["answer"], "correct": True})]
    assert batch.quiz_done() is True
    assert batch.answer(asked.question, asked.question["answer"]) == QuizStale()
    assert len(writes) == 2 and batch.quiz_done() is True


def test_a_wrong_answer_is_recorded_as_wrong_and_the_quiz_goes_on(stores):
    batch, entries = open_batch(stores, word(1), word(2))
    asked = to_quiz(batch, entries)
    wrong = next(choice for choice in asked.question["choices"] if choice != asked.question["answer"])
    writes = record(stores[1], "answer_intro_quiz")
    assert batch.answer(asked.question, wrong) == QuizAnswered(False)
    assert writes[0][2] == {"answered_text": wrong, "correct": False}
    assert batch.quiz_done() is False


def test_the_batch_is_completed_only_once_its_quiz_is_settled(stores):
    batch, entries = open_batch(stores, word(1))
    writes = record(stores[1], "complete_intro_batch")
    batch.finish()
    asked = to_quiz(batch, entries)
    batch.finish()
    assert writes == [] and stores[1].intro_batch() is not None
    batch.answer(asked.question, asked.question["answer"])
    settled = batch.open_quiz()
    assert isinstance(settled, QuizSettled) and settled.item["id"] == W1
    batch.finish()
    assert writes == [("complete_intro_batch", (), {"fair_new": False})] and stores[1].intro_batch() is None
    batch.finish()
    assert len(writes) == 1 and batch.open_quiz() is None and batch.quiz_done() is True


def test_the_kinds_are_asked_each_time_they_are_needed_and_never_kept(stores):
    enroll(stores, word(1))
    kinds = Kinds(KINDS)
    batch = BatchIntroduction(stores[1], stores[0], enabled_kinds=kinds, fair_new=lambda: False,
                              route_available=lambda: False)
    assert batch.route(stores[1].current(KINDS), KINDS) is True and kinds.asked == 0
    batch.choose_card(stores[1].intro_batch()["entries"][0], "next")
    assert batch.advance() == QuizStep() and kinds.asked == 0
    asked = batch.open_quiz()            # freezing the questions, then the closed-kind test
    assert kinds.asked == 2
    batch.open_quiz()                    # shown again: the closed-kind test only
    assert kinds.asked == 3
    batch.answer(asked.question, asked.question["answer"])
    assert kinds.asked == 4
    batch.review_due()
    assert kinds.asked == 5


def test_the_known_ids_are_the_learners_the_batchs_and_the_ones_given(stores):
    batch, entries = open_batch(stores, word(1), word(2))
    known = batch.known_ids()
    assert {W1, W2} <= known
    assert batch.known_ids(("word:extra",)) == known | {"word:extra"}
    assert batch.batch_open() is True


# --- a later ability, the help of a formal question, the review hint ------------------------------------------

def later_ability_store(*, refuse=False):
    calls = []

    def open_batch_ability(content_id, **kwargs):
        calls.append((content_id, kwargs))
        if refuse:
            raise ValueError("refused")
    return SimpleNamespace(open_batch_ability=open_batch_ability), calls


@pytest.mark.parametrize("refuse", [False, True])
def test_a_later_ability_is_opened_by_the_store_or_refused(refuse):
    learning_store, calls = later_ability_store(refuse=refuse)
    batch = BatchIntroduction(learning_store, None, enabled_kinds=Kinds(["vocab_reading"]), fair_new=lambda: True,
                              route_available=lambda: False)
    item = {"id": W1, "instruction_scope": "reading", "introduction_id": "intro-1"}
    assert batch.open_ability(item) is (not refuse)
    assert calls == [(W1, {"instruction_scope": "reading", "expected_introduction_id": "intro-1",
                           "enabled_kinds": ["vocab_reading"], "fair_new": True})]


def help_for(unit, content, *, marked=True, question=None):
    learning_store = SimpleNamespace(get_unit=lambda content_id, ability: unit)
    if marked is not None:
        learning_store.is_batch_route = lambda content_id: marked
    batch = BatchIntroduction(learning_store, None, enabled_kinds=Kinds([]), fair_new=lambda: False,
                              route_available=lambda: False)
    question = question or {"target_id": W1, "ability": "reading", "kind": "vocab_reading"}
    return batch.question_help({"question": question, "content": content}), question


def test_the_help_of_a_formal_question_of_a_batch_item():
    card_profile = {"checkpoint": {"initial_familiarity": {"source": intro_batch.CARD_SOURCE}},
                    "instruction_scope": "reading"}
    first, question = help_for({"review_count": 0}, card_profile)
    assert first == BatchHelp(ability_hint(question), True) and first.hint == "新能力：读法 — 写出读法"
    assert help_for({"review_count": 1, "attempt_count": 1}, card_profile)[0] == BatchHelp(None, True)
    assert help_for({"review_count": 2}, card_profile)[0] == BatchHelp(first.hint, False)
    assert help_for({"review_count": 0}, {"checkpoint": {}, "instruction_scope": "reading"})[0] == BatchHelp(None, True)
    assert help_for({"review_count": 0}, dict(card_profile, instruction_scope=None))[0] == BatchHelp(None, True)
    assert help_for(None, None)[0] == BatchHelp(None, True)


def test_a_question_of_an_item_outside_the_batch_route_gets_no_help():
    assert help_for({"review_count": 0}, {}, marked=False)[0] == BatchHelp(None, False)
    assert help_for({"review_count": 0}, {}, marked=None)[0] == BatchHelp(None, False)


def test_the_review_hint_counts_the_course_and_then_the_main_lane_only_for_the_continue_action(stores, monkeypatch):
    from kanamemory import practice_availability
    enroll(stores, word(1))
    loads = record(stores[0], "load_session_progress")
    summaries = []

    def summarize(store, pending):
        summaries.append((store, pending))
        return SimpleNamespace(ready_count=3)
    monkeypatch.setattr(practice_availability, "summarize_queue_availability", summarize)
    counts = record(stores[1], "counts")
    assert batch_over(stores, route=False).review_due() == 0
    assert counts == [("counts", (KINDS,), {})] and loads == [] and summaries == []
    assert batch_over(stores, route=True).review_due() == 3
    assert loads == [("load_session_progress", (), {"lane": "main", "lazy_questions": True})]
    assert summaries[0][0] is stores[0]
