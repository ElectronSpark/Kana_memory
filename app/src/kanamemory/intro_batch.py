"""Batch introduction rules for new vocabulary/grammar items (dormant).

Pure state shapes and transitions only. Nothing here owns a transaction,
question snapshot, scheduler field or learning event: LearningStore applies
these rules inside its own transaction through intro_batch_state.
A batch quiz answer is guided evidence and never an FSRS review.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import random
import re
from typing import Any, Mapping

from .arrangement.model import Orientation
from .arrangement.rules.gates import quiz_item_settled, quiz_requeue_after_miss
from .word_sound_questions import READING_CHOICE

SIZE_SETTING = "intro_batch_size_v1"
STATE_SETTING = "intro_batch_v1"
ITEM_KEY = "intro_batch"
ROUTE_KEY = "intro_route"
ROUTE = "batch_v1"
CARD_SOURCE = "intro_card_v1"
DEFAULT_SIZES = {"word": 4, "grammar": 1}
SIZE_RANGES = {"word": (1, 8), "grammar": (1, 3)}
MAX_ENTRIES = 8
MAX_ITEM_QUESTIONS = 2
CHOICES = ("next", "known")
READING_KIND_ORDER = (READING_CHOICE, "vocab_reading")
_ABILITY_ORDER = {"word": ("understanding", "reading", "listening", "use"),
                  "grammar": ("understanding", "application")}
# Checkpoint keys a still-untouched introduction may already carry.
_UNTOUCHED_KEYS = frozenset({"supports_initial_familiarity", "instruction_exposed_at_utc",
    "prerequisite_acknowledged", "prerequisite_acknowledged_at_utc", "prerequisite_evidence",
    "prerequisite_version"})
_HEX32 = re.compile(r"[0-9a-f]{32}")


def ability_order(family: str) -> list[str]:
    if family not in _ABILITY_ORDER:
        raise ValueError("Batch introduction supports only words and grammar")
    return list(_ABILITY_ORDER[family])


def first_ability(family: str, admitted) -> str:
    """Easiest admitted ability; the rest keep their own deferred introductions."""
    admitted = set(admitted)
    for ability in ability_order(family):
        if ability in admitted:
            return ability
    raise ValueError("No batch ability is admitted for this item")


def ability_open(family: str, ability: str, *, admitted, enabled_abilities, formally_correct) -> bool:
    """四之二 item 4: an ability opens after each earlier one is formally passed.

    Earlier abilities without an admitted (unsuspended) unit or without any
    enabled kind are skipped, so a closed kind never blocks later abilities.
    formally_correct names abilities with a primary, correct, unassisted
    event rated above Again; guided and self-report evidence never count.
    """
    order = ability_order(family)
    if ability not in order:
        return True
    admitted, enabled, passed = set(admitted), set(enabled_abilities), set(formally_correct)
    return all(earlier not in admitted or earlier not in enabled or earlier in passed
               for earlier in order[:order.index(ability)])


def reading_kinds_open(kinds, *, enabled_kinds, formally_correct_kinds) -> list[str]:
    """Within reading: written reading only after one formal reading choice.

    A disabled or unavailable choice kind never holds back the written task.
    """
    kinds = list(kinds)
    choice, text = READING_KIND_ORDER
    if (text in kinds and choice in kinds and choice in set(enabled_kinds)
            and choice not in set(formally_correct_kinds)):
        return [kind for kind in kinds if kind != text]
    return kinds


def _int(value: Any) -> bool:
    return type(value) is int


def _text(value: Any, limit: int = 1024) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= limit


def _aware(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Batch introduction time must be text")
    at = datetime.fromisoformat(value)
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("Batch introduction requires an aware timestamp")
    return at


def validate_sizes(value: Any) -> dict:
    if (not isinstance(value, dict) or set(value) != {"version", "word", "grammar"}
            or not _int(value["version"]) or value["version"] != 1):
        raise ValueError("Invalid batch introduction sizes")
    for family, (low, high) in SIZE_RANGES.items():
        if not _int(value[family]) or not low <= value[family] <= high:
            raise ValueError("Batch introduction size is outside its range")
    return dict(value)


def read_sizes(raw: Any) -> dict:
    return {"version": 1, **DEFAULT_SIZES} if raw is None else validate_sizes(raw)


def validate_state(value: Any) -> dict:
    fields = {"version", "batch_id", "family", "created_at_utc", "size", "entries",
              "phase", "card_index", "checks", "quiz_queue"}
    if (not isinstance(value, dict) or set(value) != fields
            or not _int(value["version"]) or value["version"] != 1
            or not isinstance(value["batch_id"], str) or not _HEX32.fullmatch(value["batch_id"])
            or value["family"] not in SIZE_RANGES):
        raise ValueError("Invalid batch introduction")
    _aware(value["created_at_utc"])
    low, high = SIZE_RANGES[value["family"]]
    entries = value["entries"]
    if (not _int(value["size"]) or not low <= value["size"] <= high
            or not isinstance(entries, list) or not 1 <= len(entries) <= min(MAX_ENTRIES, value["size"])):
        raise ValueError("Invalid batch introduction size")
    abilities = set(ability_order(value["family"]))
    ids = []
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) != {"content_id", "introduction_id", "ability"}
                or not _text(entry["content_id"]) or not _text(entry["introduction_id"], 256)
                or entry["ability"] not in abilities):
            raise ValueError("Invalid batch introduction entry")
        ids.append(entry["content_id"])
    if len(set(ids)) != len(ids):
        raise ValueError("Batch introduction repeats an item")
    index, checks, queue = value["card_index"], value["checks"], value["quiz_queue"]
    if (value["phase"] not in ("cards", "quiz") or not _int(index) or not 0 <= index <= len(entries)
            or (value["phase"] == "quiz" and index != len(entries))):
        raise ValueError("Invalid batch introduction cursor")
    if (not isinstance(checks, list) or any(not isinstance(cid, str) for cid in checks)
            or len(set(checks)) != len(checks) or not set(checks) <= set(ids[:index])):
        raise ValueError("Invalid batch introduction checks")
    if not isinstance(queue, list) or (value["phase"] == "cards" and queue):
        raise ValueError("Invalid batch quiz queue")
    for head in queue:
        if (not isinstance(head, dict) or set(head) != {"content_id", "question_id"}
                or head["content_id"] not in ids or not _text(head["question_id"])):
            raise ValueError("Invalid batch quiz question")
    # Each unfinished item waits exactly once; a checked item is never quizzed.
    queued = [head["content_id"] for head in queue]
    if len(set(queued)) != len(queued) or set(queued) & set(checks):
        raise ValueError("Invalid batch quiz queue")
    return deepcopy(value)


def validate_route(value: Any) -> str:
    if value != ROUTE:
        raise ValueError("Invalid introduction route marker")
    return value


def validate_item_state(value: Any, *, content_id: str | None = None) -> dict:
    if (not isinstance(value, dict)
            or set(value) != {"version", "batch_id", "questions", "answers", "correct", "draft"}
            or not _int(value["version"]) or value["version"] != 1
            or not isinstance(value["batch_id"], str) or not _HEX32.fullmatch(value["batch_id"])
            or type(value["correct"]) is not bool or not isinstance(value["draft"], dict)):
        raise ValueError("Invalid batch quiz checkpoint")
    questions = value["questions"]
    if not isinstance(questions, list) or not 1 <= len(questions) <= MAX_ITEM_QUESTIONS:
        raise ValueError("Invalid batch quiz questions")
    ids = []
    for question in questions:
        if (not isinstance(question, dict) or not _text(question.get("id"))
                or not _text(question.get("kind")) or not _text(question.get("target_id"))
                or question.get("guided") is not True
                or (content_id is not None and question["target_id"] != content_id)):
            raise ValueError("Invalid frozen batch quiz question")
        ids.append(question["id"])
    if len(set(ids)) != len(ids):
        raise ValueError("Batch quiz repeats a question")
    answers = value["answers"]
    if not isinstance(answers, dict) or not set(answers) <= set(ids):
        raise ValueError("Invalid batch quiz answers")
    # The second question is only asked after the first one was missed.
    if answers and ids[0] not in answers:
        raise ValueError("Invalid batch quiz answers")
    for answer in answers.values():
        if (not isinstance(answer, dict) or set(answer) != {"answer", "correct", "at_utc"}
                or not isinstance(answer["answer"], str) or type(answer["correct"]) is not bool):
            raise ValueError("Invalid batch quiz answer")
        _aware(answer["at_utc"])
    if value["correct"] != any(answer["correct"] for answer in answers.values()):
        raise ValueError("Batch quiz result disagrees with its answers")
    draft = value["draft"]
    if draft and (set(draft) != {"question_id", "state"} or draft["question_id"] not in ids
                  or not isinstance(draft["state"], dict)):
        raise ValueError("Invalid batch quiz draft")
    return deepcopy(value)


def eligible(view: Mapping[str, Any]) -> bool:
    """Only a wholly untouched original introduction may join a batch.

    Started or legacy items keep their saved route and snapshots.
    """
    checkpoint = view.get("checkpoint")
    units = view.get("units")
    return bool(
        view.get("family") in SIZE_RANGES
        and view.get("domain") not in ("pronunciation", "kana")
        # AI-generated personal items keep the old route for now.
        and not str(view.get("content_id", "")).startswith("local:ai:")
        and view.get("instruction_scope") is None
        and view.get("stage") == "intro"
        and isinstance(checkpoint, dict)
        and checkpoint.get("supports_initial_familiarity") is True
        and not set(checkpoint) - _UNTOUCHED_KEYS
        and view.get("has_events") is False
        and view.get("has_started_draft") is False
        and isinstance(units, list) and units
        and all(not unit.get("instruction_ready") and not unit.get("review_count")
                and unit.get("scheduler_state") is None and unit.get("due_at_utc") is None
                for unit in units))


def card_done(state: Mapping[str, Any], content_id: str, choice: str) -> dict:
    state = validate_state(state)
    if choice not in CHOICES:
        raise ValueError("Unknown introduction card choice")
    index = state["card_index"]
    if state["phase"] != "cards" or index >= len(state["entries"]):
        raise ValueError("No introduction card is waiting")
    if state["entries"][index]["content_id"] != content_id:
        raise ValueError("This is not the current introduction card")
    state["card_index"] = index + 1
    if choice == "known":
        state["checks"].append(content_id)
    return validate_state(state)


def quiz_items(state: Mapping[str, Any]) -> list[str]:
    checks = set(state["checks"])
    return [entry["content_id"] for entry in state["entries"] if entry["content_id"] not in checks]


def start_quiz(state: Mapping[str, Any], question_ids_by_content: Mapping[str, Any], rng=None) -> dict:
    state = validate_state(state)
    if state["phase"] != "cards" or state["card_index"] != len(state["entries"]):
        raise ValueError("Finish every introduction card before the batch quiz")
    items = quiz_items(state)
    if not isinstance(question_ids_by_content, Mapping) or set(question_ids_by_content) != set(items):
        raise ValueError("Batch quiz questions do not match its items")
    queue = []
    for cid in items:
        ids = question_ids_by_content[cid]
        if (not isinstance(ids, (list, tuple)) or not 1 <= len(ids) <= MAX_ITEM_QUESTIONS
                or any(not _text(qid) for qid in ids) or len(set(ids)) != len(ids)):
            raise ValueError("Each batch item needs one or two quiz questions")
        queue.append({"content_id": cid, "question_id": ids[0]})
    (rng if rng is not None else random.Random()).shuffle(queue)
    state.update(phase="quiz", quiz_queue=queue)
    return validate_state(state)


def quiz_head(state: Mapping[str, Any]) -> dict | None:
    queue = state["quiz_queue"]
    return dict(queue[0]) if state["phase"] == "quiz" and queue else None


def requeue_question(item_state: Mapping[str, Any]) -> str:
    """Question for the item's next turn after a miss.

    The unused second question (another kind/context) comes first; once it
    has been answered too, the first question is asked again.
    """
    questions, answers = item_state["questions"], item_state["answers"]
    if len(questions) > 1 and questions[1]["id"] not in answers:
        return questions[1]["id"]
    return questions[0]["id"]


def queued_question(item_state: Mapping[str, Any]) -> str:
    """Question an unfinished item waits with in the queue."""
    return item_state["questions"][0]["id"] if not item_state["answers"] else requeue_question(item_state)


def _question_ids(item_state: Mapping[str, Any]) -> list[str]:
    return [question["id"] for question in item_state["questions"]]


def _wrong_ids(item_state: Mapping[str, Any]) -> list[str]:
    """Frozen questions whose recorded (latest) answer is wrong."""
    return [qid for qid, value in item_state["answers"].items() if value["correct"] is False]


def released(item_state: Mapping[str, Any]) -> bool:
    """An item the quiz let go without a correct answer (design 12 Q2).

    gates.quiz_item_settled decides: every frozen question has a recorded wrong
    answer. Only the breadth-first rule takes an unfinished item out of the
    queue, so an item out of the queue in this shape was released, and it stays
    released whatever the orientation is later; under thorough alone no
    unfinished item ever leaves the queue.
    """
    return item_state["correct"] is not True and quiz_item_settled(
        Orientation.BREADTH, correct=False, question_ids=_question_ids(item_state),
        wrong_ids=_wrong_ids(item_state))


def record_answer(state: Mapping[str, Any], item_state: Mapping[str, Any], content_id: str,
                  question_id: str, correct: bool, now: datetime, *, answer: str = "",
                  orientation: Orientation = Orientation.THOROUGH) -> tuple[dict, dict]:
    """orientation: the learner's study orientation; the caller reads it (pure module)."""
    state = validate_state(state)
    item_state = validate_item_state(item_state, content_id=content_id)
    if type(correct) is not bool or not isinstance(answer, str):
        raise ValueError("Invalid batch quiz answer")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Batch introduction requires an aware timestamp")
    head = quiz_head(state)
    if head != {"content_id": content_id, "question_id": question_id}:
        raise ValueError("This is not the current batch quiz question")
    if item_state["batch_id"] != state["batch_id"] or item_state["correct"]:
        raise ValueError("Batch quiz checkpoint is not waiting for an answer")
    if question_id not in {question["id"] for question in item_state["questions"]}:
        raise ValueError("Batch quiz question is not frozen for this item")
    state["quiz_queue"].pop(0)
    item_state["answers"][question_id] = {"answer": answer, "correct": correct, "at_utc": now.isoformat()}
    item_state["draft"] = {}
    if correct:
        item_state["correct"] = True
    elif quiz_requeue_after_miss(orientation, question_ids=_question_ids(item_state),
                                 wrong_ids=_wrong_ids(item_state)):
        # Show the answer and card, then ask once more after the others.
        state["quiz_queue"].append({"content_id": content_id, "question_id": requeue_question(item_state)})
    # Otherwise (breadth first, every frozen question missed once) the answer
    # and card are shown and the item leaves the quiz (design 12 Q2).
    return validate_state(state), validate_item_state(item_state, content_id=content_id)


def finished(state: Mapping[str, Any], item_states: Mapping[str, Any]) -> bool:
    """Nothing waits in the queue and every quiz item is correct or was released.

    The orientation is not asked here: record_answer decides who leaves the
    queue, and an item released under breadth first stays settled if the
    orientation is thorough now, so an open batch can always complete.
    """
    state = validate_state(state)

    def settled(cid):
        value = item_states.get(cid)
        if not isinstance(value, Mapping):
            return False
        if value.get("correct") is True:
            return True
        try:
            value = validate_item_state(value, content_id=cid)
        except ValueError:
            return False
        return released(value)

    return state["phase"] == "quiz" and not state["quiz_queue"] and all(settled(cid) for cid in quiz_items(state))
