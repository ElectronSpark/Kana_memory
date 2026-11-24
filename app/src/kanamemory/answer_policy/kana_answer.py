"""Whether a typed question has a kana answer (rule T9, design 03): else it is never served.

A kana answer is an answer the judge (learning_questions.grade_learning_answer, with the effective
policy and the typed-answer rules T5-T8) accepts as correct that consists only of hiragana, katakana,
the long-vowel mark and digits once normalised as the judge normalises it (spaces and punctuation the
judge drops are dropped). Independent of the system-keyboard setting and of the Han characters the
prompt shows: those decide how an answer can be entered, this decides whether a kana one exists.
Candidates: the effective accepted answers (the template's, the overlay row's and the T8 reading), which
count without a judge call (a listed answer is accepted by definition), then the all-kana renderings of
the reading forms (reading_form_answers), each confirmed by the judge.
Content lookup through the judge only; no Toga, learner database or clock.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import re

from ..content_caches import register as register_content_cache
from . import overlay

_KANA = re.compile(r"[ぁ-ゖァ-ヺー0-9]+")
# Judged questions without a listed kana answer (the reading-form path is the slow one): key -> bool.
_JUDGED = {}
_JUDGED_SIZE = 4096
register_content_cache(lambda: _JUDGED.clear(), "answer_policy.kana_answer._JUDGED")


def kana_text(value, normal) -> bool:
    """``value`` normalised with ``normal`` is non-empty kana / long-vowel mark / digits only."""
    if not isinstance(value, str):
        return False
    text = normal(value)
    return isinstance(text, str) and bool(text) and _KANA.fullmatch(text) is not None


def _correct(question, answer, policy) -> bool:
    from ..learning_questions import grade_learning_answer
    try:
        return grade_learning_answer(question, answer, policy=policy).get("status") == "correct"
    except (ValueError, KeyError, TypeError, AttributeError, IndexError):
        return False


def _reading_form_candidates(question):
    from .. import reading_form_answers
    from ..learning_questions import _reading_forms
    if not reading_form_answers.applies(question):
        return []
    try:
        forms = _reading_forms(question)
    except (ValueError, KeyError, TypeError, AttributeError, IndexError):
        return []
    return list(dict.fromkeys("".join(piece[-1] for piece in pieces) for pieces in forms))


def _key(question):
    try:
        text = json.dumps(question, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def has_kana_answer(question: Mapping, policy: overlay.AnswerPolicy | None = None) -> bool:
    """Rule T9 for one typed question (a built snapshot or a raw template with its kind, family, domain
    and target_id). ``policy``: its effective policy when the caller has it."""
    from ..learning_questions import typed_answer_normal
    from .source import policy_for
    if not isinstance(question, Mapping):
        return False
    if policy is None:
        policy = policy_for(question)
    question_view = overlay.view(question, policy)
    normal = typed_answer_normal(question_view)
    # Fast path, no judge call: a listed (effective accepted) answer is accepted by definition, so one
    # that is kana only once normalised is a kana answer. The judge is asked only for rule-derived ones.
    if any(kana_text(answer, normal) for answer in policy.accepted):
        return True
    key = _key(question_view)
    if key is not None and key in _JUDGED:
        return _JUDGED[key]
    found = any(kana_text(answer, normal) and _correct(question, answer, policy)
                for answer in _reading_form_candidates(question_view))
    if key is not None:
        _JUDGED[key] = found
        while len(_JUDGED) > _JUDGED_SIZE:
            del _JUDGED[next(iter(_JUDGED))]
    return found
