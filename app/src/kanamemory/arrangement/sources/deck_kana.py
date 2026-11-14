"""KanaDeckLane: kana questions of the practice deck, from the existing kana builder and judge (design 12 E5).

- build: domain.build_questions for one (content, skill, script), the content
  chosen through domain._BatchQuestionStore (the batch builder's selector: the
  builder's own eligibility, prompts and options, previous options avoided);
  every kana category is allowed, so a learned extended kana is built too.
  Word skills are no scheduling state and are not built here (their
  answers in a queue are never practice only).
- judge: domain.check_answer, the judge of the kana pages.
- mode: kana_choice and romaji_kana are answered by tapping an option,
  kana_romaji by typing Roman letters; the kinds that need the kana keypads,
  the word keyboards or the ink canvas are self-checked in the deck.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime

from ...domain import PRACTICE_KANA_CATEGORIES, WORD_SKILLS, _BatchQuestionStore, _question_from_snapshot
from ...domain import build_questions, check_answer
from ..model import DeckAnswerMode, DeckTexts, StateRef

CHOICE_KINDS = frozenset({"kana_choice", "romaji_kana"})
TEXT_KINDS = frozenset({"kana_romaji"})


class KanaDeckLane:
    """The kana lane of ports.DeckQuestions; ``repository``: the app's content repository, asked only to build."""

    def __init__(self, store, repository: Callable[[], object] | None = None):
        self._store = store
        self._repository = repository

    @staticmethod
    def scheduled(ref: StateRef) -> bool:
        return ref.ability not in WORD_SKILLS

    def build(self, ref: StateRef, now: datetime) -> Mapping[str, object] | None:
        if ref.ability in WORD_SKILLS or self._repository is None:
            return None
        built = build_questions(self._repository(), _BatchQuestionStore(self._store, [ref.content_id]),
                                kind=ref.ability, script=ref.script, limit=1, now=now,
                                kana_categories=tuple(sorted(PRACTICE_KANA_CATEGORIES)))
        return built[0].to_dict() if built and built[0].content_id == ref.content_id else None

    @staticmethod
    def mode(ref: StateRef, question: Mapping[str, object]) -> DeckAnswerMode:
        if ref.ability in CHOICE_KINDS and question.get("options"):
            return DeckAnswerMode.CHOICE
        if ref.ability in TEXT_KINDS:
            return DeckAnswerMode.TEXT
        return DeckAnswerMode.SELF_CHECK

    def texts(self, ref: StateRef, question: Mapping[str, object]) -> DeckTexts:
        options = tuple(question.get("options") or ()) if self.mode(ref, question) is DeckAnswerMode.CHOICE else ()
        return DeckTexts(prompt=str(question.get("prompt", "")), options=options,
                         answer=str(question.get("answer", "")), audio_text=str(question.get("audio_text") or ""))

    @staticmethod
    def judge(ref: StateRef, question: Mapping[str, object], answer: str) -> tuple[bool | None, str]:
        return check_answer(_question_from_snapshot(dict(question)), answer), ""
