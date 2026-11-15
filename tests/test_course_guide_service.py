"""The guide of a teaching step without a page (design 13, C1): the teaching service on real stores with a
synthetic lesson.

The start of the guide from the introduction, the checkpoint of a draft, a guide answer (nothing to judge, a
right one, a wrong one, one that cannot be saved, one that carries its draft), the answer saved in the lesson,
"seen, go on", and the end of the guide — a step of a sequence, a finished guide, a guide passed after a wrong
answer.
"""
from types import SimpleNamespace

import pytest

from kanamemory.arrangement.rules import gates
from kanamemory.course_line.teaching import CourseTeaching
from kanamemory.course_line.teaching_model import (GuideAnswered, GuideEmpty, GuideFinished, GuideNext,
                                                   GuideSaveFailed)
from kanamemory.domain import StudyStore
from kanamemory.learning_questions import build_learning_question
from kanamemory.learning_replacement import PREFIX
from kanamemory.learning_store import LearningStore

KINDS = ["vocab_meaning"]
CID = "word:synthetic:w1"
RIGHT, WRONG = "词1", "别的词"


def word():
    return {"id": CID, "content_id": CID, "family": "word", "title": "語1", "reading": "ご",
            "meaning": "词1", "revision": "1", "abilities": ["understanding"], "source_ids": ["test"],
            "instruction": {"summary": "词1", "rule": "名词。", "steps": [], "example": "語1です。",
                            "translation": "是词1。", "prerequisite": "", "contrast": ""},
            "question_templates": {"vocab_meaning": [{"prompt": "語1", "answer": RIGHT, "choices": [RIGHT, WRONG],
                                                      "feedback": "说明", "input_mode": "choice", "safe": True}]}}


@pytest.fixture
def stores(tmp_path):
    store = StudyStore(tmp_path / "guide-service", time_zone="UTC")
    yield store, LearningStore(store)
    store.close()


def teaching(stores, *, fair=False, noted=None):
    store, learning = stores
    return CourseTeaching(learning, store, enabled_kinds=lambda: list(KINDS), fair_new=lambda: fair,
                          record_error=(lambda *args: noted.append(args)) if noted is not None else None)


def introduced(stores):
    """The lesson after its familiarity answer and its introduction: (service, the lesson as stored)."""
    store, learning = stores
    learning.enroll([word()])
    for item in learning.items_needing_questions():
        learning.append_questions([build_learning_question(item, "vocab_meaning", variant=0)])
    head = learning.current(KINDS)
    service = teaching(stores)
    opened = service.open_familiarity(head["content"], head, lambda **fields: None)
    saved = service.set_familiarity(opened, {"understanding": "new"})
    service.step(saved, lambda **fields: None)
    return service, learning.instruction_item(CID)


def guided(stores):
    """The lesson in its guide with its guide question: (service, lesson, question)."""
    service, lesson = introduced(stores)
    lesson = service.start_guide(lesson)
    step = service.step(lesson, lambda **fields: None)
    return service, step.item, step.question


def record(target, *names):
    log = []
    for name in names:
        original = getattr(target, name)

        def call(*args, _name=name, _original=original, **kwargs):
            log.append((_name, args, kwargs))
            return _original(*args, **kwargs)
        setattr(target, name, call)
    return log


def test_the_guide_is_started_from_the_introduction(stores):
    service, lesson = introduced(stores)
    assert lesson["stage"] == "intro"
    calls = record(stores[1], "advance_instruction")
    started = service.start_guide(lesson)
    assert calls == [("advance_instruction", (CID, "intro_done"), {"instruction_scope": None})]
    assert started["stage"] == "guide" and started == stores[1].instruction_item(CID) == service.lesson(lesson)


def test_a_draft_is_checkpointed_with_its_question_and_a_refusal_is_raised(stores):
    service, lesson, question = guided(stores)
    state = {"guide_question": dict(question), "guide_draft": {"answer": RIGHT}, "guide_answer": None,
             "guide_correct": False}
    calls = record(stores[1], "advance_instruction")
    service.checkpoint_guide(lesson, state)
    assert calls == [("advance_instruction", (CID, "guide"), {"state": state, "instruction_scope": None})]
    checkpoint = stores[1].instruction_item(CID)["checkpoint"]
    assert checkpoint["guide_draft"] == {"answer": RIGHT} and checkpoint["guide_question"]["id"] == question["id"]
    with pytest.raises((ValueError, KeyError)):
        service.checkpoint_guide({"id": "word:gone", "instruction_scope": None}, state)


def test_an_empty_guide_answer_gets_the_judges_words_and_writes_nothing(stores, monkeypatch):
    from kanamemory.course_line import teaching as teaching_module
    service, lesson, question = guided(stores)
    calls = record(stores[1], "advance_instruction")
    assert service.answer_guide(lesson, question, "", None) == GuideEmpty("请先作答。") and calls == []
    monkeypatch.setattr(teaching_module, "grade_learning_answer", lambda question, answer: {"status": "invalid"})
    assert service.answer_guide(lesson, question, "x", None) == GuideEmpty("请先完成这一题。") and calls == []


@pytest.mark.parametrize("answer, correct, stage", [(RIGHT, True, "guide"), (WRONG, False, "guide_failed")])
def test_a_guide_answer_is_judged_and_saved_in_the_lessons_guide(stores, answer, correct, stage):
    service, lesson, question = guided(stores)
    calls = record(stores[1], "advance_instruction")
    assert service.answer_guide(lesson, question, answer, None) == GuideAnswered(correct, None)
    assert [(name, args) for name, args, _ in calls] == [("advance_instruction", (CID, stage))]
    assert calls[0][2] == {"state": {"guide_answer": answer, "guide_correct": correct, "guide_question": question},
                           "instruction_scope": None}
    assert calls[0][2]["state"]["guide_question"] is not question
    saved = stores[1].instruction_item(CID)["checkpoint"]
    assert (saved["guide_answer"], saved["guide_correct"]) == (answer, correct)
    assert service.saved_guide_answer(lesson) == answer


def test_a_practice_guide_answer_carries_its_draft_and_a_typed_one_the_answer_inside_it(stores):
    calls = []
    learning = SimpleNamespace(advance_instruction=lambda *args, **kwargs: calls.append((args, kwargs)))
    service = CourseTeaching(learning, None, enabled_kinds=lambda: [], fair_new=lambda: False)
    lesson = {"id": CID, "instruction_scope": "use"}
    chosen = {"id": "g1", "kind": "vocab_meaning", "answer": RIGHT, "choices": [RIGHT, WRONG], "input_mode": "choice",
              "practice": {"stage": 1}}
    draft = {"answer": RIGHT, "demonstration_open": True}
    answered = service.answer_guide(lesson, chosen, RIGHT, draft)
    assert answered == GuideAnswered(True, draft) and answered.draft is not draft
    assert calls[0][1]["state"]["guide_draft"] == draft and calls[0][1]["instruction_scope"] == "use"
    plain = {key: value for key, value in chosen.items() if key != "practice"}
    assert service.answer_guide(lesson, plain, RIGHT, draft) == GuideAnswered(True, None)   # a simple guide keeps no draft
    typed = dict(chosen, input_mode="text", kind="vocab_recall", choices=[])
    assert service.answer_guide(lesson, typed, RIGHT, {"answer": "typed before"}).draft == {"answer": RIGHT}


def test_a_guide_answer_that_cannot_be_saved_is_noted_and_reported(stores):
    noted = []
    service, lesson, question = guided(stores)
    service = teaching(stores, noted=noted)
    failure = RuntimeError("the save failed")

    def broken(*args, **kwargs):
        raise failure
    stores[1].advance_instruction = broken
    assert service.answer_guide(lesson, question, RIGHT, None) == GuideSaveFailed()
    assert noted == [("learning_guide", "submit", failure)]


def test_seen_go_on_is_offered_only_after_a_recorded_wrong_answer_and_only_when_the_orientation_allows(
        stores, monkeypatch):
    service, lesson, question = guided(stores)
    asked = []
    allow = [True]
    monkeypatch.setattr(gates, "guide_may_continue", lambda orientation, *, answered_wrong: asked.append(
        (orientation, answered_wrong)) or allow[0])
    assert service.pass_caption(lesson, question) is None                      # no answer yet
    service.answer_guide(lesson, question, WRONG, None)
    assert service.pass_caption(lesson, question) == "看过了，继续"
    assert service.pass_caption(lesson, dict(question, id="another")) is None  # the saved answer is another question's
    assert asked[0] == (stores[1].arrangement_orientation(), True)
    allow[0] = False
    reads = record(stores[1], "instruction_item")
    assert service.pass_caption(lesson, question) is None and reads == []      # the orientation decides first
    allow[0] = True
    service.answer_guide(lesson, question, RIGHT, None)
    assert service.pass_caption(lesson, question) is None                      # a right answer needs no pass


def sequence_store(index, length, calls):
    lesson = {"id": CID, "stage": "guide", "checkpoint": {
        "guide_answer": WRONG, "guide_correct": False, "guide_question": {"id": "g1"},
        "guide_sequence": [{"id": f"g{n}"} for n in range(length)], "guide_index": index}}

    def advance_guided_sequence(content_id, **kwargs):
        calls.append(("advance_guided_sequence", (content_id,), kwargs))
        return {"finished": index >= length - 1, "item": {"id": CID, "stage": "guide", "next": True}}
    return SimpleNamespace(
        instruction_item=lambda content_id, scope=None: lesson, arrangement_orientation=lambda: "breadth",
        advance_guided_sequence=advance_guided_sequence,
        advance_instruction=lambda *args, **kwargs: calls.append(("advance_instruction", args, kwargs)))


def test_a_step_of_a_guided_sequence_goes_on_to_the_next_question(monkeypatch):
    monkeypatch.setattr(gates, "guide_may_continue", lambda orientation, *, answered_wrong: True)
    calls = []
    service = CourseTeaching(sequence_store(0, 2, calls), None, enabled_kinds=lambda: [], fair_new=lambda: True)
    lesson, question = {"id": CID, "instruction_scope": None}, {"id": "g1"}
    assert service.pass_caption(lesson, question) == "这一步看过了，下一步"
    assert service.finish_guide(lesson, question, True) == GuideNext({"id": CID, "stage": "guide", "next": True})
    assert calls == [("advance_guided_sequence", (CID,), {"question_id": "g1", "instruction_scope": None})]


@pytest.mark.parametrize("passed, stage", [(False, "guide_correct"), (True, "bridge")])
def test_the_last_step_of_a_sequence_ends_the_guide(monkeypatch, passed, stage):
    monkeypatch.setattr(gates, "guide_may_continue", lambda orientation, *, answered_wrong: True)
    calls = []
    service = CourseTeaching(sequence_store(1, 2, calls), None, enabled_kinds=lambda: [], fair_new=lambda: True)
    lesson, question = {"id": CID, "instruction_scope": "use"}, {"id": "g1"}
    assert service.pass_caption(lesson, question) == "看过了，继续"
    assert service.finish_guide(lesson, question, passed) == GuideFinished()
    assert calls == [("advance_guided_sequence", (CID,), {"question_id": "g1", "instruction_scope": "use"}),
                     ("advance_instruction", (CID, stage), {"fair_new": True, "instruction_scope": "use"})]


def test_a_single_guide_ends_with_the_real_store(stores):
    service, lesson, question = guided(stores)
    service.answer_guide(lesson, question, RIGHT, None)
    calls = record(stores[1], "advance_instruction", "advance_guided_sequence")
    assert service.finish_guide(lesson, question, False) == GuideFinished()
    assert calls == [("advance_instruction", (CID, "guide_correct"), {"fair_new": False, "instruction_scope": None})]
    assert stores[1].instruction_item(CID)["stage"] != "guide"


def test_the_record_of_a_replaced_guide_question(stores):
    service = teaching(stores)
    assert service.guide_archive({}) is None
    stores[0].set_setting(PREFIX + "guide:" + "replacement-1", {"before": {"guide_question": {"id": "old"}}})
    assert service.guide_archive({"guide_replacement_id": "replacement-1"}) == {
        "before": {"guide_question": {"id": "old"}}}
    assert service.guide_archive({"guide_replacement_id": "unknown"}) is None
