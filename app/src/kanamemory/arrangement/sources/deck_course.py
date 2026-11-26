"""CourseDeckLane: course questions of the practice deck, from the existing planner, builder and judge (design 12 E5).

- build: course_planning.iter_question_plans for the one ability (the same
  kind choice, variant selection by the unit's history, dependency gate and
  batch gate as a formal question), then learning_questions.
  build_learning_question for the full question. The planner plans only
  abilities without a pending question; here every other ability of the
  lesson is reported pending, so it plans exactly this one, whether or not the
  unit has a pending question of its own (that question is never touched).
- avoid_last (the consolidation deck, design 12 N5): the planner is told the
  (kind, variant) of the state's most recent exposure (its latest formal
  question, LearningStore.practice_history, or its latest practice answer,
  TeachingQueries.latest_practice_exposure, whichever is later) and chooses
  another template whenever the ability has one; with one template it repeats it.
- judge: learning_questions.grade_learning_answer, the course pages' judge; an
  answer it cannot score (empty, unresolved) is reported as None.
- mode: a choice question without a special stimulus (pitch, multi-gap) is
  answered by tapping an option; the others are self-checked in the deck
  (their input parts need the course page's own controls).
- texts: the answer, its cells with kana above the standard spelling
  (answer_furigana.answer_cells, made only when the answer is shown) and the
  question's own explanation as the course result page shows it (result_explanations).
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

    def __init__(self, store, learning, enabled_kinds: Iterable[str] = (), *, system_japanese_input: bool = False,
                 avoid_last: bool = False):
        self._store, self._learning = store, learning
        self._enabled = frozenset(enabled_kinds)
        self._system_japanese = bool(system_japanese_input)
        self._avoid_last = bool(avoid_last)   # the consolidation deck (design 12 N5)

    def last_template(self, ref: StateRef) -> tuple[str, object] | None:
        """(kind, variant) of the state's most recent exposure: its latest formal question or practice answer."""
        formal = self._learning.practice_history(ref.content_id, ref.ability, limit=1)
        practice = self._store.teaching_queries.latest_practice_exposure(ref.content_id, ref.ability)
        candidates = []
        if formal:
            question = formal[-1]["question"]
            candidates.append((datetime.fromisoformat(formal[-1]["occurred_at_utc"]),
                               (question.get("kind"), question.get("variant"))))
        if practice is not None and practice["kind"]:
            candidates.append((datetime.fromisoformat(practice["at"]), (practice["kind"], practice["variant"])))
        return max(candidates, key=lambda value: value[0])[1] if candidates else None

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
        avoid = {ref.ability: self.last_template(ref)} if self._avoid_last else None
        plan = next(iter(iter_question_plans(item, enabled_kinds=self._enabled,
                                             queries=_OneAbility(self._store.queries, admitted, ref.ability),
                                             learning_store=self._learning,
                                             system_japanese_input=self._system_japanese,
                                             avoid_templates=avoid)), None)
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
        from ...answer_furigana import answer_cells, answer_pitch
        from ...answer_result_ui import answer_text
        from ...result_explanations import shown_course_explanation
        answer = answer_text(dict(question))
        options = (tuple(question.get("choices") or ())
                   if self.mode(ref, question) is DeckAnswerMode.CHOICE else ())
        # The answer's cells (standard spelling, kana above) are made only when the page shows the answer.
        return DeckTexts(prompt=str(question.get("prompt", "")), task=str(question.get("task") or ""),
                         options=options, answer=str(answer),
                         # The pitch marks follow the result pages' (answer_pitch), read when it is shown.
                         answer_cells=lambda shown=dict(question): tuple(answer_cells(shown, pitch=answer_pitch(
                             getattr(self, "_store", None), getattr(self, "_learning", None), shown))),
                         # The same visible text as the course result page of this question
                         # (result_explanations.shown_course_explanation): no line that is only the answer
                         # (owner 2026-11-18: the sentence is shown once, in the answer line), every reading
                         # (rule 9) and no bracket that only repeats the kana before it.
                         explanation=shown_course_explanation(dict(question)),
                         # As on the course question page: a question with audio is heard (播放题目), its
                         # prompt text is not shown (prompt_rows draws none for it).
                         audio_text=str(question.get("audio_text") or ""),
                         # Design 12 N12: the task as the course question page shows it, read when it is shown.
                         cue=lambda shown=dict(question): self.cue(shown),
                         prompt_view=lambda shown=dict(question): self.prompt_rows(shown))

    def prompt_rows(self, question: Mapping[str, object]) -> list | None:
        """The prompt of ``question`` as the course question page draws it (learning_ui.show_learning_question).

        The same presentation function (question_support.build_prompt_block; multi_cloze_ui.prompt_view for a
        multi-gap sentence, with no saved draft) and the same reading rule: the learner's prompt display
        setting and the readings of words not yet learned (course_planning.known_ids), so the deck shows no
        reading the question page would hide. Nothing for a question with audio (the page plays it instead);
        None for a pitch question: the deck keeps its prompt as a plain label (the question page draws its
        pitch editor there).
        No window here: the cells wrap at a phone's width, as the deck's answer cells do.
        """
        from ... import ui_components as u
        from ...course_planning import known_ids
        from ...pitch_practice import is_pitch_question
        from ...question_support import PROMPT_DISPLAY_SETTING, build_prompt_block, clean_prompt_preferences
        question = dict(question)
        if question.get("audio_text"):
            return []
        if is_pitch_question(question):
            return None
        store = getattr(self, "_store", None)
        queries = getattr(store, "queries", None)
        known = known_ids(queries) if queries is not None else None
        preferences = clean_prompt_preferences(store.get_setting(PROMPT_DISPLAY_SETTING, {}) if store else {})
        if question.get("input_mode") == "multi_cloze":
            from ...multi_cloze_ui import prompt_view
            return [prompt_view(None, u, question, {}, preferences, known_ids=known)]
        return [build_prompt_block(None, question, preferences, size=20, known_ids=known)]

    def cue(self, question: Mapping[str, object]) -> tuple:
        """The task of ``question`` as the course question page shows it (instruction_view with the known set)."""
        from ...course_planning import known_ids
        from ...question_instruction_ui import instruction_view
        queries = getattr(getattr(self, "_store", None), "queries", None)
        return instruction_view(dict(question), known_ids(queries) if queries is not None else None)

    @staticmethod
    def judge(ref: StateRef, question: Mapping[str, object], answer: str) -> tuple[bool | None, str]:
        from ...learning_questions import grade_learning_answer
        result = grade_learning_answer(dict(question), answer)
        scored = result.get("scorable") and type(result.get("correct")) is bool
        return (result["correct"] if scored else None), str(result.get("message") or "")
