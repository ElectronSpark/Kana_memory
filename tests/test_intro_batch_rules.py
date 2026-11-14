"""Pure batch-introduction rules; synthetic identifiers only."""
from copy import deepcopy
from datetime import datetime, timezone
import random

import pytest

from kanamemory import intro_batch as rules
from kanamemory.learning_questions import READING_CHOICE_KIND

NOW = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)
BATCH = "a" * 32


def state(n=3, **changes):
    value = {"version": 1, "batch_id": BATCH, "family": "word", "created_at_utc": NOW.isoformat(),
             "size": 4, "entries": [{"content_id": f"w{i}", "introduction_id": f"i{i}", "ability": "understanding"}
                                   for i in range(n)],
             "phase": "cards", "card_index": 0, "checks": [], "quiz_queue": []}
    value.update(changes)
    return value


def item(cid="w0", qids=("q0",)):
    return {"version": 1, "batch_id": BATCH, "answers": {}, "correct": False, "draft": {},
            "questions": [{"id": q, "kind": "vocab_meaning", "target_id": cid, "guided": True} for q in qids]}


def view(**changes):
    value = {"content_id": "word:x", "family": "word", "domain": None, "instruction_scope": None,
             "stage": "intro", "checkpoint": {"supports_initial_familiarity": True},
             "has_events": False, "has_started_draft": False,
             "units": [{"instruction_ready": 0, "review_count": 0, "scheduler_state": None, "due_at_utc": None}]}
    value.update(changes)
    return value


def test_sizes_defaults_and_ranges():
    assert rules.read_sizes(None) == {"version": 1, "word": 4, "grammar": 1}
    assert rules.validate_sizes({"version": 1, "word": 8, "grammar": 3})["word"] == 8
    for bad in ({"version": 1, "word": 9, "grammar": 1}, {"version": 1, "word": 0, "grammar": 1},
                {"version": 1, "word": 4, "grammar": 4}, {"version": 1, "word": True, "grammar": 1},
                {"version": 2, "word": 4, "grammar": 1}, {"word": 4, "grammar": 1}, None):
        with pytest.raises(ValueError):
            rules.validate_sizes(bad)
    with pytest.raises(ValueError):
        rules.read_sizes({"version": 1, "word": 4})


def test_ability_order_and_reading_kinds():
    assert rules.ability_order("word") == ["understanding", "reading", "listening", "use"]
    assert rules.ability_order("grammar") == ["understanding", "application"]
    assert rules.READING_KIND_ORDER == (READING_CHOICE_KIND, "vocab_reading")
    assert rules.first_ability("word", ["use", "reading"]) == "reading"


@pytest.mark.parametrize("change", [
    {"version": 2}, {"batch_id": "A" * 32}, {"batch_id": "a" * 31}, {"family": "kana"},
    {"created_at_utc": "2026-11-07T12:00:00"}, {"size": 9}, {"size": 2}, {"entries": []},
    {"phase": "review"}, {"card_index": 4}, {"checks": ["w0"]}, {"checks": ["zz"]},
    {"quiz_queue": [{"content_id": "w0", "question_id": "q"}]},
    {"phase": "quiz", "card_index": 1},
    {"phase": "quiz", "card_index": 3, "quiz_queue": [{"content_id": "w0", "question_id": "q"},
                                                      {"content_id": "w0", "question_id": "q2"}]},
    {"phase": "quiz", "card_index": 3, "checks": ["w1"], "quiz_queue": [{"content_id": "w1", "question_id": "q"}]},
])
def test_validate_state_rejects(change):
    with pytest.raises(ValueError):
        rules.validate_state(state(**change))


def test_validate_state_rejects_duplicate_or_bad_entries():
    value = state()
    value["entries"][1]["content_id"] = "w0"
    with pytest.raises(ValueError):
        rules.validate_state(value)
    value = state()
    value["entries"][0]["ability"] = "application"
    with pytest.raises(ValueError):
        rules.validate_state(value)
    rules.validate_state(state(phase="quiz", card_index=3, checks=["w1"],
                               quiz_queue=[{"content_id": "w0", "question_id": "q"}]))


def test_item_state_and_route_marker():
    rules.validate_item_state(item(qids=("a", "b")), content_id="w0")
    for bad in (item(qids=()), item(qids=("a", "b", "c")), item(qids=("a", "a")), {**item(), "correct": True},
                {**item(), "draft": {"question_id": "zz", "state": {}}}, {**item(), "extra": 1}):
        with pytest.raises(ValueError):
            rules.validate_item_state(bad)
    second_only = item(qids=("a", "b"))
    second_only["answers"]["b"] = {"answer": "x", "correct": False, "at_utc": NOW.isoformat()}
    with pytest.raises(ValueError):  # the second question follows a missed first one
        rules.validate_item_state(second_only)
    unguided = item()
    unguided["questions"][0]["guided"] = False
    with pytest.raises(ValueError):
        rules.validate_item_state(unguided)
    with pytest.raises(ValueError):
        rules.validate_item_state(item(), content_id="other")
    assert rules.validate_route("batch_v1") == "batch_v1"
    with pytest.raises(ValueError):
        rules.validate_route("batch_v2")


@pytest.mark.parametrize("change,expected", [
    ({}, True), ({"family": "grammar"}, True), ({"family": "kana"}, False),
    ({"domain": "pronunciation"}, False), ({"content_id": "local:ai:x"}, False),
    ({"instruction_scope": "reading"}, False), ({"stage": "guide"}, False),
    ({"checkpoint": {}}, False),
    ({"checkpoint": {"supports_initial_familiarity": True, "instruction_exposed_at_utc": NOW.isoformat()}}, True),
    ({"checkpoint": {"supports_initial_familiarity": True, "initial_familiarity": {}}}, False),
    ({"checkpoint": {"supports_initial_familiarity": True, "familiarity_draft": {}}}, False),
    ({"checkpoint": {"supports_initial_familiarity": True, "guide_answer": "x"}}, False),
    ({"checkpoint": {"supports_initial_familiarity": True, "intro_route": "batch_v1"}}, False),
    ({"has_events": True}, False), ({"has_started_draft": True}, False), ({"units": []}, False),
    ({"units": [{"instruction_ready": 1, "review_count": 0, "scheduler_state": None, "due_at_utc": None}]}, False),
    ({"units": [{"instruction_ready": 0, "review_count": 1, "scheduler_state": None, "due_at_utc": None}]}, False),
    ({"units": [{"instruction_ready": 0, "review_count": 0, "scheduler_state": {}, "due_at_utc": None}]}, False),
    ({"units": [{"instruction_ready": 0, "review_count": 0, "scheduler_state": None, "due_at_utc": "x"}]}, False),
])
def test_eligible_matrix(change, expected):
    assert rules.eligible(view(**change)) is expected


def test_card_transitions():
    value = state()
    with pytest.raises(ValueError):
        rules.card_done(value, "w1", "next")
    with pytest.raises(ValueError):
        rules.card_done(value, "w0", "maybe")
    value = rules.card_done(value, "w0", "next")
    value = rules.card_done(value, "w1", "known")
    value = rules.card_done(value, "w2", "next")
    assert value["card_index"] == 3 and value["checks"] == ["w1"]
    with pytest.raises(ValueError):
        rules.card_done(value, "w2", "next")


def test_quiz_requeues_wrong_answers_until_every_item_is_correct():
    value = state()
    with pytest.raises(ValueError):
        rules.start_quiz(value, {"w0": ["q0"]}, random.Random(1))
    for cid, choice in (("w0", "next"), ("w1", "known"), ("w2", "next")):
        value = rules.card_done(value, cid, choice)
    with pytest.raises(ValueError):
        rules.start_quiz(value, {"w0": ["q0"], "w1": ["q1"], "w2": ["q2"]}, random.Random(1))
    rng = random.Random(7)
    expected = [{"content_id": "w0", "question_id": "q0"}, {"content_id": "w2", "question_id": "q2"}]
    random.Random(7).shuffle(expected)
    value = rules.start_quiz(value, {"w0": ["q0", "q0b"], "w2": ["q2"]}, rng)
    assert value["phase"] == "quiz" and value["quiz_queue"] == expected
    items = {"w0": item("w0", ("q0", "q0b")), "w2": item("w2", ("q2",))}
    assert not rules.finished(value, items)
    first = value["quiz_queue"][0]
    with pytest.raises(ValueError):
        rules.record_answer(value, items[value["quiz_queue"][1]["content_id"]],
                            value["quiz_queue"][1]["content_id"], value["quiz_queue"][1]["question_id"], True, NOW)
    value, items[first["content_id"]] = rules.record_answer(
        value, items[first["content_id"]], first["content_id"], first["question_id"], False, NOW, answer="x")
    requeued = {"content_id": first["content_id"],
                "question_id": "q0b" if first["content_id"] == "w0" else "q2"}
    assert value["quiz_queue"][-1] == requeued and len(value["quiz_queue"]) == 2
    assert items[first["content_id"]]["correct"] is False
    while value["quiz_queue"]:
        head = value["quiz_queue"][0]
        before = deepcopy(items[head["content_id"]])
        value, items[head["content_id"]] = rules.record_answer(
            value, before, head["content_id"], head["question_id"], True, NOW, answer="ok")
        assert items[head["content_id"]]["answers"][head["question_id"]]["correct"] is True
    assert rules.finished(value, items)
    assert not rules.finished(value, {**items, "w2": item("w2", ("q2",))})


def test_all_known_batch_finishes_without_questions():
    value = state(n=1)
    value = rules.card_done(value, "w0", "known")
    value = rules.start_quiz(value, {}, random.Random(0))
    assert value["quiz_queue"] == [] and rules.finished(value, {})


def test_requeue_uses_the_unused_second_question_then_the_first():
    value = rules.card_done(state(n=1), "w0", "next")
    value = rules.start_quiz(value, {"w0": ["q0", "q1"]}, random.Random(0))
    current = item("w0", ("q0", "q1"))
    assert rules.queued_question(current) == "q0" and value["quiz_queue"][0]["question_id"] == "q0"
    asked = []
    for _ in range(3):
        head = rules.quiz_head(value)
        asked.append(head["question_id"])
        value, current = rules.record_answer(value, current, "w0", head["question_id"], False, NOW, answer="x")
        assert value["quiz_queue"] == [{"content_id": "w0", "question_id": rules.queued_question(current)}]
    assert asked == ["q0", "q1", "q0"]
    assert rules.requeue_question(current) == "q0" and set(current["answers"]) == {"q0", "q1"}
    value, current = rules.record_answer(value, current, "w0", "q0", True, NOW, answer="ok")
    assert value["quiz_queue"] == [] and rules.finished(value, {"w0": current})
    # A single frozen question is simply asked again.
    single = item("w0", ("q0",))
    single["answers"]["q0"] = {"answer": "x", "correct": False, "at_utc": NOW.isoformat()}
    assert rules.requeue_question(single) == "q0"
