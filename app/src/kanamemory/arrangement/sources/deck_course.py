"""CourseDeckLane: course questions of the practice deck, from the existing planner, builder and judge (design 12 E5).

- build: course_planning.iter_question_plans for the one ability (the same
  kind choice, variant selection by the unit's history, dependency gate and
  batch gate as a formal question), then learning_questions.
  build_learning_question for the full question. The planner plans only
  abilities without a pending question; here every other ability of the
  lesson is reported pending, so it plans exactly this one, whether or not the
  unit has a pending question of its own (that question is never touched).
- judge: learning_questions.grade_learning_answer, the course pages' judge; an
  answer it cannot score (empty, unresolved) is reported as None.
- mode: a choice question without a special stimulus (pitch, multi-gap) is
  answered by tapping an option; the others are self-checked in the deck
  (their input parts need the course page's own controls).
- texts: the answer with readings (answer_result_ui.shown_answer) and the
  question's own explanation.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime

from ..model import DeckAnswerMode, DeckTexts, StateRef


class _OneAbility:
    """CourseQueries over the store's queries that leaves only ``ability`` of the lesson to plan."""

    def __init__(self, queries, admitted: Iterable[str], ability: str):
        self._queries = queries
        self._others = {value for value in admitted if value != ability}

    def pending_abilities(self, content_id: str) -> set[str]:
        return set(self._others)

    def instruction_ready_ids(self):
        return self._queries.instruction_ready_ids()

    def introduced_kana_ids(self):
        reader = getattr(self._queries, "introduced_kana_ids", None)
        return reader() if callable(reader) else ()


class CourseDeckLane:
    """The course lane of ports.DeckQuestions over one LearningStore; ``enabled_kinds``: the learner's course kinds."""

    def __init__(self, store, learning, enabled_kinds: Iterable[str] = (), *, system_japanese_input: bool = False):
        self._store, self._learning = store, learning
        self._enabled = frozenset(enabled_kinds)
        self._system_japanese = bool(system_japanese_input)

    @staticmethod
    def scheduled(ref: StateRef) -> bool:
        return True

    def build(self, ref: StateRef, now: datetime) -> Mapping[str, object] | None:
        from ...course_planning import iter_question_plans
        from ...learning_questions import build_learning_question
        if not self._enabled:
            return None
        item = self._learning.get_item(ref.content_id)
        if item is None:
            return None
        admitted = item.get("enrolled_abilities", item.get("abilities", []))
        plan = next(iter(iter_question_plans(item, enabled_kinds=self._enabled,
                                             queries=_OneAbility(self._store.queries, admitted, ref.ability),
                                             learning_store=self._learning,
                                             system_japanese_input=self._system_japanese)), None)
        if plan is None or plan.question.get("ability") != ref.ability:
            return None
        question = plan.question
        if "answer" not in question:
            question = build_learning_question(item, question["kind"], variant=question["variant"])
        return question

    @staticmethod
    def mode(ref: StateRef, question: Mapping[str, object]) -> DeckAnswerMode:
        special = question.get("tests_pitch") or question.get("multi_cloze") or question.get("pitch_practice")
        if question.get("input_mode", "choice") == "choice" and question.get("choices") and not special:
            return DeckAnswerMode.CHOICE
        return DeckAnswerMode.SELF_CHECK

    def texts(self, ref: StateRef, question: Mapping[str, object]) -> DeckTexts:
        from ...answer_result_ui import distinct_explanation, shown_answer
        answer = shown_answer(dict(question))
        options = (tuple(question.get("choices") or ())
                   if self.mode(ref, question) is DeckAnswerMode.CHOICE else ())
        return DeckTexts(prompt=str(question.get("prompt", "")), task=str(question.get("task") or ""),
                         options=options, answer=str(answer),
                         explanation=distinct_explanation(question.get("feedback", ""), answer),
                         audio_text=str(question.get("audio_text") or "") if question.get("requires_audio") else "")

    @staticmethod
    def judge(ref: StateRef, question: Mapping[str, object], answer: str) -> tuple[bool | None, str]:
        from ...learning_questions import grade_learning_answer
        result = grade_learning_answer(dict(question), answer)
        scored = result.get("scorable") and type(result.get("correct")) is bool
        return (result["correct"] if scored else None), str(result.get("message") or "")
