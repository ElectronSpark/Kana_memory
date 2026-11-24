"""The answer overlay namespace and the one effective answer policy of a question (design 03).

Pure: no Toga, learner database, platform, network, clock or content loading.

``answer_overlay`` (version 1) is {"schema_version": 1, "items": {content id: {kind: {template sha256:
row}}}}, keyed like question_readings by the raw template's sha256 (learning_questions.
template_fingerprint). A row corrects the answer policy of an INSTALLED template without replacing it
(a template's sha covers strict, accepted_answers, known_wrong and feedback, so editing them would make
a new template). A row holds at least one of:
    strict       true or false: replaces the template's strict
    accepted     [text, ...]: answers accepted besides the template's own
    known_wrong  [{"answer": text, "feedback": text}, ...]: answers judged wrong, each with its feedback
    fold         true: the Chinese hint of the question waits behind the hint button
A row whose sha names no installed template is never read (the lookup starts from the template).

The overlay is never copied into a question snapshot: ``effective`` computes the policy when a reader
needs it, and ``view`` gives a detached copy of the question with the effective strict, accepted and
known-wrong answers for the readers that take a question (the judge, the input gate, the probe check,
the hint guard). A verdict, once stored, is never derived again from the snapshot and the policy.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import re
import unicodedata

NAMESPACE = "answer_overlay"
SCHEMA_VERSION = 1
ROW_FIELDS = ("strict", "accepted", "known_wrong", "fold")
# The typed grammar kinds whose typed answer may use either kana script (rule T7).
SCRIPT_FOLD_KINDS = frozenset({"grammar_cloze", "grammar_transform", "targeted_correction",
                               "controlled_sentence_production"})
RECALL_KIND = "vocab_recall"
MAX_ANSWERS = 30   # the bound of a template's accepted_answers / known_wrong lists
MAX_TEXT = 400
MAX_FEEDBACK = 2000
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_KIND = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_MAX_ID = 1024


class OverlayError(ValueError):
    """An answer_overlay row, item or namespace breaks the format."""


def _text(value, limit):
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= limit
            and not any(unicodedata.category(c) in {"Cc", "Cs"} for c in value))


def validate_row(row):
    """One template's row (see the module text); returns it unchanged."""
    if not isinstance(row, Mapping) or not row or set(row) - set(ROW_FIELDS):
        raise OverlayError("Invalid answer overlay row")
    if "strict" in row and type(row["strict"]) is not bool:
        raise OverlayError("Invalid answer overlay strict")
    if "fold" in row and row["fold"] is not True:
        raise OverlayError("Invalid answer overlay fold")
    if "accepted" in row:
        values = row["accepted"]
        if (not isinstance(values, list) or not 0 < len(values) <= MAX_ANSWERS
                or any(not _text(v, MAX_TEXT) for v in values) or len(set(values)) != len(values)):
            raise OverlayError("Invalid answer overlay accepted answers")
    if "known_wrong" in row:
        values = row["known_wrong"]
        if not isinstance(values, list) or not 0 < len(values) <= MAX_ANSWERS:
            raise OverlayError("Invalid answer overlay known-wrong answers")
        for entry in values:
            if (not isinstance(entry, Mapping) or set(entry) != {"answer", "feedback"}
                    or not _text(entry["answer"], MAX_TEXT) or not _text(entry["feedback"], MAX_FEEDBACK)):
                raise OverlayError("Invalid answer overlay known-wrong entry")
        if len({entry["answer"] for entry in values}) != len(values):
            raise OverlayError("Invalid answer overlay known-wrong answers")
        if set(row.get("accepted", ())) & {entry["answer"] for entry in values}:
            raise OverlayError("An answer overlay row accepts one of its known-wrong answers")
    return row


def validate_item(content_id, entry):
    """One content id's rows: {kind: {template sha256: row}}."""
    if not isinstance(content_id, str) or not content_id or len(content_id) > _MAX_ID:
        raise OverlayError("Invalid answer overlay item id")
    if not isinstance(entry, Mapping):
        raise OverlayError("Invalid answer overlay item")
    for kind, templates in entry.items():
        if not isinstance(kind, str) or not _KIND.fullmatch(kind) or not isinstance(templates, Mapping):
            raise OverlayError("Invalid answer overlay kind")
        for sha, row in templates.items():
            if not isinstance(sha, str) or not _SHA256.fullmatch(sha):
                raise OverlayError("Invalid answer overlay template fingerprint")
            validate_row(row)
    return entry


def validate_namespace(value):
    """The whole namespace (v1): {"schema_version": 1, "items": {id: item}}; returns it unchanged."""
    if (not isinstance(value, Mapping) or set(value) != {"schema_version", "items"}
            or type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION
            or not isinstance(value["items"], Mapping)):
        raise OverlayError("Unsupported answer overlay namespace")
    for content_id, entry in value["items"].items():
        validate_item(content_id, entry)
    return value


def row_count(value) -> int:
    """The number of rows of a namespace value (0 for anything that is not one)."""
    items = value.get("items") if isinstance(value, Mapping) else None
    if not isinstance(items, Mapping):
        return 0
    return sum(len(templates) for entry in items.values() if isinstance(entry, Mapping)
               for templates in entry.values() if isinstance(templates, Mapping))


def fold_katakana(text: str) -> str:
    """Katakana letters as hiragana (ヴ ゔ, ヵ ゕ, ヶ ゖ); the long-vowel mark and all else unchanged."""
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text)


@dataclass(frozen=True)
class LessonForms:
    """What rule T8 reads of the question's lesson: its written forms and its reading."""
    written: tuple[str, ...] = ()
    reading: str = ""


@dataclass(frozen=True)
class AnswerPolicy:
    """The effective answer policy of one question (never stored).

    accepted / known_wrong: the template's answers first, then the row's (and the lesson reading of
    rule T8), without repeats. row_feedback: the row's known-wrong answers with their feedback.
    changed: whether anything differs from the question's own fields (then ``view`` copies).
    """
    strict: bool
    accepted: tuple[str, ...]
    known_wrong: tuple[str, ...]
    row_feedback: tuple[tuple[str, str], ...] = ()
    fold: bool = False
    changed: bool = False
    row: Mapping | None = field(default=None, compare=False)


def _strings(values):
    return [v for v in values if isinstance(v, str)] if isinstance(values, (list, tuple)) else []


def recall_reading(question, lesson: LessonForms | None, normal) -> str:
    """Rule T8: the lesson reading a vocab_recall question accepts, or "".

    Only when the question's answer is the lesson's own written form (its title or a listed spelling).
    """
    if lesson is None or question.get("kind") != RECALL_KIND or not lesson.reading:
        return ""
    answer = question.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        return ""
    written = {normal(form) for form in lesson.written if isinstance(form, str) and form.strip()}
    return lesson.reading if normal(answer) in written else ""


def effective(question: Mapping, row: Mapping | None = None, *, lesson: LessonForms | None = None,
              normal=None) -> AnswerPolicy:
    """The policy of ``question`` with its overlay ``row`` (None: no row) and its lesson (rule T8).

    strict: the row's when it has one, else the question's. accepted: the question's accepted answers
    (its answer when it lists none) plus the row's plus the T8 reading. known_wrong: the question's plus
    the row's answers. fold: the row's mark. ``normal`` compares written forms for T8 (default NFKC).
    """
    normal = normal or (lambda text: unicodedata.normalize("NFKC", str(text)).strip())
    base_accepted = question.get("accepted_answers", [question.get("answer")])
    base_accepted = base_accepted if isinstance(base_accepted, list) else [question.get("answer")]
    accepted = list(base_accepted)
    known = list(question.get("known_wrong") or [])
    strict = bool(question.get("strict"))
    feedback, fold = [], False
    if isinstance(row, Mapping):
        if type(row.get("strict")) is bool:
            strict = row["strict"]
        accepted.extend(v for v in _strings(row.get("accepted")) if v not in accepted)
        for entry in row.get("known_wrong") or ():
            if isinstance(entry, Mapping) and isinstance(entry.get("answer"), str):
                if entry["answer"] not in known:
                    known.append(entry["answer"])
                feedback.append((entry["answer"], str(entry.get("feedback") or "")))
        fold = row.get("fold") is True
    reading = recall_reading(question, lesson, normal)
    if reading and reading not in accepted:
        accepted.append(reading)
    changed = (strict != bool(question.get("strict")) or accepted != list(base_accepted)
               or known != list(question.get("known_wrong") or []))
    return AnswerPolicy(strict=strict, accepted=tuple(accepted), known_wrong=tuple(known),
                        row_feedback=tuple(feedback), fold=fold, changed=changed,
                        row=row if isinstance(row, Mapping) else None)


def view(question: Mapping, policy: AnswerPolicy):
    """The question with the policy's strict / accepted / known-wrong answers: a detached copy (never
    stored), or the question itself when the policy changes nothing."""
    if not policy.changed:
        return question
    result = dict(question)
    result.update(strict=policy.strict, accepted_answers=list(policy.accepted),
                  known_wrong=list(policy.known_wrong))
    return result


def row_feedback(policy: AnswerPolicy, value: str, normal) -> str:
    """The feedback of the row's known-wrong answer that ``value`` (already normalised) equals, or ""."""
    for answer, text in policy.row_feedback:
        if normal(answer) == value:
            return text
    return ""
