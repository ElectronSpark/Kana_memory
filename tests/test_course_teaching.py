"""The teaching steps without a page (design 13, C1 and C3): the service on real stores with a synthetic
lesson, and the session the app side keeps.

What a teaching head becomes (the familiarity question, a lesson that may not be taught, a guide waiting for a
check, the introduction with its exposure, the guide with its frozen question, the paused guide), the
familiarity question's preparation and its answers, and the way on without a guide — each asked of
CourseTeaching built from a StudyStore, a LearningStore and two callables. Then the app side: the session is
replaced when a field is assigned, the decision keeps the lesson and the guide question at the moments it
reaches them, and each value is performed by one call.
"""
from types import SimpleNamespace

import pytest

from kanamemory import assessment_policy, course_teaching_runtime as runtime, personal_content_versions
from kanamemory.course_line.teaching import CourseTeaching
from kanamemory.course_line.teaching_model import (AskFamiliarity, BatchOwns, FamiliarityOpened, Guide, GuideBlocked,
                                                   GuidePaused, GuideUnavailable, Introduce, MaterialNotReady,
                                                   OpenAbility, TeachingAction, TeachingActionKind, TeachingSession)
from kanamemory.domain import StudyStore
from kanamemory.learning_questions import build_learning_question
from kanamemory.learning_store import LearningStore

KINDS = ["vocab_meaning", "grammar_meaning", "pronunciation_rule"]
CID = "word:synthetic:w1"
WRITES = ("mark_instruction_exposed", "advance_instruction", "prepare_initial_familiarity",
          "save_familiarity_draft", "set_initial_familiarity")


def word(n=1):
    cid = f"word:synthetic:w{n}"
    return {"id": cid, "content_id": cid, "family": "word", "title": f"語{n}", "reading": "ご",
            "meaning": f"词{n}", "revision": "1", "abilities": ["understanding"], "source_ids": ["test"],
            "instruction": {"summary": f"词{n}", "rule": "名词。", "steps": [], "example": f"語{n}です。",
                            "translation": f"是词{n}。", "prerequisite": "", "contrast": ""},
            "question_templates": {"vocab_meaning": [{"prompt": f"語{n}", "answer": f"词{n}",
                                                      "choices": [f"词{n}", "别的词"], "feedback": "说明",
                                                      "input_mode": "choice", "safe": True}]}}


@pytest.fixture
def stores(tmp_path):
    store = StudyStore(tmp_path / "teaching-service", time_zone="UTC")
    yield store, LearningStore(store)
    store.close()


def teaching(stores, kinds=KINDS, *, fair=False):
    store, learning = stores
    return CourseTeaching(learning, store, enabled_kinds=lambda: list(kinds), fair_new=lambda: fair)


def fresh_head(stores):
    """A synthetic word joined the course and got its first question queued: the course head (its teaching)."""
    store, learning = stores
    learning.enroll([word()])
    for item in learning.items_needing_questions():
        learning.append_questions([build_learning_question(item, "vocab_meaning", variant=0)])
    head = learning.current(KINDS)
    assert head["type"] == "instruction" and head["content"]["id"] == CID
    return head


def record(target, *names):
    log = []
    for name in names:
        original = getattr(target, name)

        def call(*args, _name=name, _original=original, **kwargs):
            log.append((_name, args, kwargs))
            return _original(*args, **kwargs)
        setattr(target, name, call)
    return log


class Kept:
    """What the decision told its caller to hold, in order: [("item", object), ("guide", object), ...]."""

    def __init__(self):
        self.log = []

    def __call__(self, **fields):
        self.log.extend(fields.items())

    def names(self):
        return [name for name, _ in self.log]


def familiar(stores, level="new"):
    """The lesson after the learner answered the familiarity question; returns (service, the lesson as stored)."""
    head = fresh_head(stores)
    service = teaching(stores)
    opened = service.open_familiarity(head["content"], head, Kept())
    return service, service.set_familiarity(opened, {"understanding": level})


# --- what a teaching head becomes ---------------------------------------------------------------------------

def test_a_fresh_lesson_is_asked_how_familiar_it_is_and_nothing_is_written(stores):
    head = fresh_head(stores)
    writes, kept = record(stores[1], *WRITES), Kept()
    step = teaching(stores).step(head["content"], kept)
    assert step == AskFamiliarity(head["content"]) and step.item is head["content"]
    assert kept.log == [("item", head["content"])] and writes == []


@pytest.mark.parametrize("status, missing", [("instruction_missing", True), ("unverified", False)])
def test_a_lesson_that_may_not_be_taught_stops_before_anything_is_held(stores, monkeypatch, status, missing):
    head = fresh_head(stores)
    item = dict(head["content"], instruction_status=status)
    asked = []
    monkeypatch.setattr(assessment_policy, "teaching_allowed", lambda lesson, *, store=None: asked.append(
        (lesson, store)) and False)
    writes, kept = record(stores[1], *WRITES), Kept()
    assert teaching(stores).step(item, kept) == MaterialNotReady(item, missing)
    assert asked == [(item, stores[0])] and kept.log == [] and writes == []


def test_a_guide_whose_material_changed_waits_for_a_check_before_anything_else_is_asked(stores, monkeypatch):
    frozen = {"id": "guide-1", "kind": "vocab_meaning"}
    item = {"id": CID, "stage": "guide", "checkpoint": {"guide_question": frozen}}
    asked = []
    monkeypatch.setattr(personal_content_versions, "question_blocked", lambda store, lesson, question: asked.append(
        (store, lesson, question)) or True)
    monkeypatch.setattr(assessment_policy, "teaching_allowed", lambda *args, **kwargs: pytest.fail("not asked"))
    kept = Kept()
    assert teaching(stores).step(item, kept) == GuideBlocked(item)
    assert asked == [(stores[0], item, frozen)] and kept.log == []
    # Only a lesson in its guide stage has a frozen question to check.
    monkeypatch.setattr(assessment_policy, "teaching_allowed", lambda *args, **kwargs: False)
    assert isinstance(teaching(stores).step(dict(item, stage="intro"), Kept()), MaterialNotReady)
    assert len(asked) == 1


def test_the_introduction_records_its_exposure_and_the_line_holds_the_lesson_as_stored_afterwards(stores):
    service, saved = familiar(stores)
    assert saved["stage"] == "intro" and "instruction_exposed_at_utc" not in saved["checkpoint"]
    calls, kept = record(stores[1], *WRITES, "instruction_item"), Kept()
    step = service.step(saved, kept)
    assert step == Introduce(saved, saved["checkpoint"]["initial_familiarity"], True) and step.item is saved
    # The exposure is written first; the lesson is read again afterwards (the store reads it once itself, too).
    assert calls[0] == ("mark_instruction_exposed", (CID,), {"expected_introduction_id": saved["introduction_id"],
                                                             "instruction_scope": None})
    assert calls[-1] == ("instruction_item", (CID, None), {})
    assert [name for name, *_ in calls].count("mark_instruction_exposed") == 1
    assert kept.names() == ["item", "item"] and kept.log[0][1] is saved
    refreshed = kept.log[1][1]
    assert refreshed == stores[1].instruction_item(CID) and refreshed["checkpoint"]["instruction_exposed_at_utc"]


def test_the_guide_asks_the_lessons_guide_question_and_holds_it_before_its_kind_is_tested(stores):
    service, _ = familiar(stores)
    stores[1].advance_instruction(CID, "intro_done")
    guided = stores[1].instruction_item(CID)
    assert guided["stage"] == "guide"
    writes, kept = record(stores[1], *WRITES), Kept()
    step = service.step(guided, kept)
    assert isinstance(step, Guide) and step.item is guided and step.shared_intro is False
    assert (step.question["kind"], step.question["target_id"]) == ("vocab_meaning", CID)
    assert kept.log == [("item", guided), ("guide", step.question)] and kept.log[1][1] is step.question
    assert CID in step.known and step.checkpoint == guided["checkpoint"]
    assert writes == []                                   # a simple guide question is not frozen in the lesson
    again = service.step(stores[1].instruction_item(CID), Kept())
    assert again.question["id"] == step.question["id"] and writes == []


def test_a_guide_without_an_open_kind_is_paused_before_any_question_is_chosen(stores):
    familiar(stores)
    stores[1].advance_instruction(CID, "intro_done")
    guided = stores[1].instruction_item(CID)
    asked = record(stores[1], "guided_question")
    kept = Kept()
    assert teaching(stores, []).step(guided, kept) == GuidePaused()
    assert kept.names() == ["item"] and asked == []


def test_a_lesson_whose_guide_question_cannot_be_built_says_so(stores):
    familiar(stores)
    stores[1].advance_instruction(CID, "intro_done")
    guided = stores[1].instruction_item(CID)
    stores[1].guided_question = lambda item, kind, variant=1: None
    kept = Kept()
    assert teaching(stores).step(guided, kept) == GuideUnavailable(guided)
    assert kept.names() == ["item"]


def test_the_units_and_the_way_on_without_a_guide(stores):
    service, saved = familiar(stores)
    assert service.understanding_unit(saved) == stores[1].get_unit(CID, "understanding")
    calls = record(stores[1], "advance_instruction")
    stores[1].advance_instruction(CID, "intro_done")
    calls.clear()
    teaching(stores, fair=True).bridge_without_guide(CID, None)
    assert calls == [("advance_instruction", (CID, "bridge"), {"fair_new": True, "instruction_scope": None})]


# --- the familiarity question -----------------------------------------------------------------------------------

def test_the_familiarity_question_is_prepared_when_it_is_asked(stores):
    head = fresh_head(stores)
    calls, kept = record(stores[1], *WRITES), Kept()
    opened = teaching(stores).open_familiarity(head["content"], head, kept)
    assert isinstance(opened, FamiliarityOpened)
    assert calls == [("prepare_initial_familiarity", (CID,), {
        "expected_introduction_id": head["content"]["introduction_id"], "instruction_scope": None,
        "ability": "understanding"})]
    assert kept.log == [("item", opened.item)] and opened.item == stores[1].instruction_item(CID)
    assert (opened.abilities, opened.levels, opened.ability) == (("understanding",), {}, "understanding")
    assert opened.state == {"content_id": CID, "introduction_id": opened.item["introduction_id"],
                            "instruction_scope": None, "ability": "understanding", "levels": {}, "busy": False}
    assert opened.state["levels"] is opened.levels


def test_a_lesson_that_may_not_be_taught_is_not_asked(stores, monkeypatch):
    head = fresh_head(stores)
    monkeypatch.setattr(assessment_policy, "teaching_allowed", lambda lesson, *, store=None: False)
    calls, kept = record(stores[1], *WRITES), Kept()
    assert teaching(stores).open_familiarity(head["content"], head, kept) is None
    assert calls == [] and kept.log == []


def test_the_answers_are_saved_as_a_draft_until_every_ability_is_answered(stores):
    calls = []
    learning = SimpleNamespace(
        save_familiarity_draft=lambda *args, **kwargs: calls.append(("draft", args, kwargs)) or {"stage": "intro"},
        set_initial_familiarity=lambda *args, **kwargs: calls.append(("set", args, kwargs)) or {"stage": "intro"})
    service = CourseTeaching(learning, None, enabled_kinds=lambda: [], fair_new=lambda: True)
    state = {"content_id": CID, "introduction_id": "intro-1", "instruction_scope": "reading", "ability": "reading",
             "levels": {}, "busy": False}
    opened = FamiliarityOpened({"id": CID, "domain": "pronunciation"}, state, ("understanding", "reading"), {}, "reading")
    service.save_familiarity_draft(opened, {"reading": "some"})
    service.set_familiarity(opened, {"reading": "some", "understanding": "new"})
    assert calls == [
        ("draft", (CID, {"reading": "some"}), {"expected_introduction_id": "intro-1", "instruction_scope": "reading"}),
        ("set", (CID, {"reading": "some", "understanding": "new"}), {
            "expected_introduction_id": "intro-1", "title_answer_exposed": True, "fair_new": True,
            "instruction_scope": "reading"})]


def test_the_learners_answer_is_recorded_with_the_real_store(stores):
    _, saved = familiar(stores, "familiar")
    assert saved["checkpoint"]["initial_familiarity"]["levels"] == {"understanding": "familiar"}
    assert stores[1].instruction_item(CID) == saved


# --- the session the app keeps ------------------------------------------------------------------------------------

def test_assigning_a_field_gives_the_app_a_new_session_and_leaves_the_old_one_alone():
    app = SimpleNamespace()
    assert isinstance(runtime.teaching_session(app), TeachingSession) and not hasattr(app, "_course_teaching")
    assert (runtime.item(app), runtime.guide(app), runtime.familiarity(app), runtime.head(app)) == (None,) * 4
    lesson, question, state, head = {"id": CID}, {"id": "g1"}, {"busy": False}, {"type": "instruction"}
    runtime.set_item(app, lesson)
    first = runtime.teaching_session(app)
    runtime.set_guide(app, question)
    runtime.set_familiarity(app, state)
    runtime.set_head(app, head)
    last = runtime.teaching_session(app)
    assert (first.item, first.guide, first.familiarity, first.head) == (lesson, None, None, None)
    assert (last.item, last.guide, last.familiarity, last.head) == (lesson, question, state, head)
    assert last.item is lesson and last.familiarity is state and last is not first
    assert runtime.item(app) is lesson and runtime.guide(app) is question
    assert runtime.familiarity(app) is state and runtime.head(app) is head


def line_app(stores, log):
    store, learning = stores
    app = SimpleNamespace(store=store, learning_store=learning, page="home")
    app.learning_preferences = lambda: {"enabled": list(KINDS)}
    app._new_guide_result = lambda: log.append("new result column") or SimpleNamespace(children=[])
    for name in ("open_batch_ability", "show_learning_familiarity", "_draw_learning_guide", "_draw_learning_intro",
                 "show_learning_detail"):
        setattr(app, name, lambda value, _name=name: log.append((_name, value)))
    for name in ("show_intro_batch", "leave_learning", "show_home", "continue_added_learning",
                 "show_learning_settings"):
        setattr(app, name, lambda *args, _name=name: log.append((_name, args)))
    return app


def test_the_decision_holds_the_lesson_and_the_guide_question_and_gives_the_guide_a_new_result_column(stores):
    familiar(stores)
    stores[1].advance_instruction(CID, "intro_done")
    guided = stores[1].instruction_item(CID)
    log = []
    app = line_app(stores, log)
    step = runtime.decide_teaching(app, guided)
    assert isinstance(step, Guide) and log == ["new result column"]
    assert runtime.item(app) is guided and runtime.guide(app) is step.question
    assert app._learning_guide_result.children == []


def test_each_teaching_step_is_performed_by_one_call(stores):
    log = []
    app = line_app(stores, log)
    lesson = {"id": CID}
    guide = Guide(lesson, {"id": "g1"}, {}, set(), False)
    intro = Introduce(lesson, None, True)
    for step in (OpenAbility(lesson), BatchOwns(), AskFamiliarity(lesson), guide, intro):
        runtime.perform_teaching(app, step)
    assert log == [("open_batch_ability", lesson), ("show_intro_batch", ()), ("show_learning_familiarity", lesson),
                   ("_draw_learning_guide", guide), ("_draw_learning_intro", intro)]
    with pytest.raises(TypeError):
        runtime.perform_teaching(app, object())


def test_a_pressed_button_of_a_stop_page_makes_its_call(stores):
    log = []
    app = line_app(stores, log)
    lesson = {"id": CID}
    for kind in (TeachingActionKind.LEAVE, TeachingActionKind.HOME, TeachingActionKind.CONTINUE_OTHER,
                 TeachingActionKind.LEARNING_SETTINGS):
        runtime.run_teaching_action(app, TeachingAction(kind), "the button")
    runtime.run_teaching_action(app, TeachingAction(TeachingActionKind.DETAIL, item=lesson), "the button")
    assert log == [("leave_learning", ("the button",)), ("show_home", ("the button",)),
                   ("continue_added_learning", ("the button",)), ("show_learning_settings", ("the button",)),
                   ("show_learning_detail", lesson)]
    with pytest.raises(ValueError):
        runtime.run_teaching_action(app, TeachingAction(TeachingActionKind.START_PRACTICE))
