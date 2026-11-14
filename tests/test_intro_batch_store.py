"""Batch introduction persistence: self-report and guided evidence only."""
from datetime import datetime, timedelta, timezone
import json
import random

import pytest
from record_test_support import decode_current, encode_current

from kanamemory import ability_teaching, explicit_learning_start, intro_batch
from kanamemory.domain import StudyStore
from kanamemory.learning_store import LearningStore, validate_backup_rows

NOW = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)
WORDS = ["word:synthetic:a", "word:synthetic:b", "word:synthetic:c"]


def lesson(cid, family="word"):
    abilities = ["understanding", "reading"] if family == "word" else ["understanding", "application"]
    return {"id": cid, "family": family, "revision": "1", "abilities": abilities,
            "instruction": {"meaning": "合成含义", "rule": "合成规则"}}


def formal(cid, ability):
    return {"id": cid + ":" + ability, "target_id": cid, "ability": ability,
            "kind": "vocab_meaning" if ability == "understanding" else "vocab_reading",
            "prompt": "提示", "answer": "答案", "input_mode": "choice", "revision": "1"}


def guided(cid, n=1):
    return [{"id": f"{cid}:guide:{i}", "target_id": cid, "ability": "understanding", "kind": "vocab_meaning",
             "prompt": "提示", "answer": "答案", "guided": True, "revision": "1"} for i in range(n)]


@pytest.fixture
def stores(tmp_path):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        learning = LearningStore(store)
        learning.enroll([lesson(cid) for cid in WORDS], now=NOW)
        learning.append_questions([formal(cid, a) for cid in WORDS for a in ("understanding", "reading")], now=NOW)
        yield store, learning


def scheduler_rows(learning):
    return [tuple(row) for row in learning._db.execute("""SELECT unit_id,scheduler_state_json,
        due_at_utc,review_count,probe_due_at_utc,last_unassisted_success_at_utc FROM learning_unit ORDER BY rowid""")]


def frozen_questions(learning):
    return [tuple(row) for row in learning._db.execute("SELECT * FROM learning_question ORDER BY rowid")]


def events(learning):
    return learning._db.execute("SELECT COUNT(*) FROM learning_event").fetchone()[0]


def intro_id(learning, cid):
    return learning.instruction_row(cid)["introduction_id"]


def run_cards(learning, choices):
    state = learning.intro_batch()
    for entry, choice in zip(state["entries"], choices):
        state = learning.choose_intro_card(entry["content_id"], choice,
                                           expected_introduction_id=entry["introduction_id"], now=NOW)
    return state


def test_start_batch_claims_separates_and_refuses_overlap(stores):
    store, learning = stores
    db = learning._db
    with db:
        db.execute("BEGIN IMMEDIATE")
        explicit_learning_start.arm(db, [{"entry_id": "e0", "content_id": WORDS[0], "kind": "learning",
            "script": None, "introduction_id": intro_id(learning, WORDS[0])}], now=NOW)
    before, questions = scheduler_rows(learning), frozen_questions(learning)
    state = learning.start_intro_batch(WORDS[:2], family="word", now=NOW)
    assert state["phase"] == "cards" and state["card_index"] == 0 and state["size"] == 4
    assert [e["ability"] for e in state["entries"]] == ["understanding", "understanding"]
    assert explicit_learning_start.read(db)["selected_entry_id"] == "e0"
    teaching = ability_teaching.read(db, WORDS[0])
    assert teaching["original_abilities"] == ["understanding"] and set(teaching["additions"]) == {"reading"}
    assert learning.get_item(WORDS[0])["checkpoint"]["intro_route"] == "batch_v1"
    with pytest.raises(ValueError):
        learning.start_intro_batch([WORDS[2]], family="word", now=NOW)
    with pytest.raises(ValueError):
        learning.start_intro_batch([WORDS[2]], family="grammar", now=NOW)
    assert scheduler_rows(learning) == before and frozen_questions(learning) == questions and events(learning) == 0
    validate_backup_rows(db)


def test_sizes_setting_limits_batch(stores):
    _, learning = stores
    assert learning.intro_batch_sizes() == {"version": 1, "word": 4, "grammar": 1}
    learning.set_intro_batch_sizes({"version": 1, "word": 2, "grammar": 3})
    with pytest.raises(ValueError):
        learning.set_intro_batch_sizes({"version": 1, "word": 9, "grammar": 1})
    with pytest.raises(ValueError):
        learning.start_intro_batch(WORDS, family="word", now=NOW)
    assert learning.intro_batch() is None
    assert learning.start_intro_batch(WORDS[:2], family="word", now=NOW)["size"] == 2


def test_started_item_is_not_eligible(stores):
    _, learning = stores
    learning.mark_instruction_exposed(WORDS[0], now=NOW)
    learning.advance_instruction(WORDS[0], "intro_done", now=NOW)
    with pytest.raises(ValueError):
        learning.start_intro_batch([WORDS[0]], family="word", now=NOW)
    assert learning.intro_batch() is None


def test_card_choices_write_profiles_and_replay(stores):
    _, learning = stores
    before = scheduler_rows(learning)
    learning.start_intro_batch(WORDS[:2], family="word", now=NOW)
    with pytest.raises(ValueError):  # not the current card
        learning.choose_intro_card(WORDS[1], "next", expected_introduction_id=intro_id(learning, WORDS[1]), now=NOW)
    with pytest.raises(ValueError):
        learning.choose_intro_card(WORDS[0], "next", expected_introduction_id="stale", now=NOW)
    state = run_cards(learning, ["next", "known"])
    assert state["card_index"] == 2 and state["checks"] == [WORDS[1]]
    first, second = learning.get_item(WORDS[0]), learning.get_item(WORDS[1])
    assert first["checkpoint"]["initial_familiarity"] == {"version": 1, "evidence": "self_report",
        "levels": {"understanding": "new"}, "route": "full", "selected_at_utc": NOW.isoformat(),
        "diagnostic_state": "not_requested", "source": "intro_card_v1"}
    assert first["stage"] == "intro"
    profile = second["checkpoint"]["initial_familiarity"]
    assert profile["levels"] == {"understanding": "familiar"} and profile["route"] == "diagnostic"
    assert profile["diagnostic_state"] == "active" and profile["title_answer_exposed"] is True
    row = learning.instruction_row(WORDS[1])
    assert row["stage"] == "bridge" and row["guided_at_utc"] == NOW.isoformat()
    assert row["retrieval_at_utc"] == (NOW + timedelta(seconds=60)).isoformat()
    assert learning.get_unit(WORDS[1], "understanding")["instruction_ready"] == 1
    assert learning.get_unit(WORDS[1], "reading")["instruction_ready"] == 0
    assert learning.get_unit(WORDS[0], "understanding")["instruction_ready"] == 0
    assert learning.choose_intro_card(WORDS[1], "known", expected_introduction_id=row["introduction_id"],
                                      now=NOW + timedelta(hours=1)) == state
    with pytest.raises(ValueError):
        learning.choose_intro_card(WORDS[1], "next", expected_introduction_id=row["introduction_id"], now=NOW)
    assert learning.intro_batch() == state
    assert scheduler_rows(learning) == before and events(learning) == 0
    validate_backup_rows(learning._db)


def test_quiz_requeues_wrong_answer_and_completes_atomically(stores):
    _, learning = stores
    before, questions = scheduler_rows(learning), frozen_questions(learning)
    learning.start_intro_batch(WORDS, family="word", now=NOW)
    with pytest.raises(ValueError):
        learning.start_intro_quiz({cid: guided(cid) for cid in WORDS}, rng=random.Random(1))
    run_cards(learning, ["next", "known", "next"])
    quiz_ids = [WORDS[0], WORDS[2]]
    with pytest.raises(ValueError):  # checks are not quizzed
        learning.start_intro_quiz({cid: guided(cid) for cid in WORDS}, rng=random.Random(1))
    unguided = {cid: [{**q, "guided": False} for q in guided(cid)] for cid in quiz_ids}
    with pytest.raises(ValueError):
        learning.start_intro_quiz(unguided, rng=random.Random(1))
    state = learning.start_intro_quiz({WORDS[0]: guided(WORDS[0], 2), WORDS[2]: guided(WORDS[2])},
                                      rng=random.Random(3))
    assert state["phase"] == "quiz" and {h["content_id"] for h in state["quiz_queue"]} == set(quiz_ids)
    for cid in quiz_ids:
        item = learning.get_item(cid)
        assert item["stage"] == "guide" and item["checkpoint"]["intro_batch"]["batch_id"] == state["batch_id"]
    assert learning.get_item(WORDS[1])["stage"] == "bridge"
    validate_backup_rows(learning._db)
    head = state["quiz_queue"][0]
    learning.save_intro_quiz_draft(head["content_id"], head["question_id"], {"text": "草稿"})
    assert learning.get_item(head["content_id"])["checkpoint"]["intro_batch"]["draft"] == {
        "question_id": head["question_id"], "state": {"text": "草稿"}}
    with pytest.raises(ValueError):
        learning.complete_intro_batch(now=NOW)
    other = state["quiz_queue"][1]
    with pytest.raises(ValueError):
        learning.answer_intro_quiz(other["content_id"], other["question_id"], answered_text="x", correct=True, now=NOW)
    state, item = learning.answer_intro_quiz(head["content_id"], head["question_id"], answered_text="错",
                                             correct=False, now=NOW)
    requeued = {"content_id": head["content_id"], "question_id": (
        f"{WORDS[0]}:guide:1" if head["content_id"] == WORDS[0] else head["question_id"])}
    assert state["quiz_queue"] == [other, requeued] and item["correct"] is False and item["draft"] == {}
    validate_backup_rows(learning._db)
    while state["quiz_queue"]:
        h = state["quiz_queue"][0]
        state, item = learning.answer_intro_quiz(h["content_id"], h["question_id"], answered_text="对",
                                                 correct=True, now=NOW)
        assert item["correct"] is True
    assert scheduler_rows(learning) == before and frozen_questions(learning) == questions and events(learning) == 0
    done = NOW + timedelta(minutes=5)
    assert sorted(learning.complete_intro_batch(now=done, fair_new=True)) == sorted(quiz_ids)
    assert learning.intro_batch() is None
    for cid in quiz_ids:
        row = learning.instruction_row(cid)
        assert row["stage"] == "bridge" and row["guided_at_utc"] == done.isoformat()
        assert row["retrieval_at_utc"] == (done + timedelta(seconds=60)).isoformat()
        assert learning.get_unit(cid, "understanding")["instruction_ready"] == 1
        assert json.loads(row["checkpoint_json"])["intro_batch"]["correct"] is True
    assert learning.instruction_row(WORDS[1])["guided_at_utc"] == NOW.isoformat()
    assert scheduler_rows(learning) == before and frozen_questions(learning) == questions and events(learning) == 0
    validate_backup_rows(learning._db)


def test_completion_rolls_back_as_one_transaction(stores):
    _, learning = stores
    learning.start_intro_batch(WORDS[:2], family="word", now=NOW)
    run_cards(learning, ["next", "next"])
    state = learning.start_intro_quiz({cid: guided(cid) for cid in WORDS[:2]}, rng=random.Random(0))
    for h in list(state["quiz_queue"]):
        learning.answer_intro_quiz(h["content_id"], h["question_id"], answered_text="对", correct=True, now=NOW)
    learning._db.execute("""CREATE TRIGGER fail_clear BEFORE DELETE ON app_setting
        WHEN OLD.key='intro_batch_v1' BEGIN SELECT RAISE(ABORT, 'clear failure'); END""")
    with pytest.raises(Exception, match="clear failure"):
        learning.complete_intro_batch(now=NOW)
    assert [learning.instruction_row(cid)["stage"] for cid in WORDS[:2]] == ["guide", "guide"]
    assert learning.intro_batch() is not None
    learning._db.execute("DROP TRIGGER fail_clear")
    learning.complete_intro_batch(now=NOW)
    assert [learning.instruction_row(cid)["stage"] for cid in WORDS[:2]] == ["bridge", "bridge"]


def test_backup_round_trip_accepts_state_and_rejects_corruption(stores):
    store, learning = stores
    learning.set_intro_batch_sizes({"version": 1, "word": 3, "grammar": 2})
    learning.start_intro_batch(WORDS[:2], family="word", now=NOW)
    run_cards(learning, ["next", "known"])
    learning.start_intro_quiz({WORDS[0]: guided(WORDS[0], 2)}, rng=random.Random(0))
    before = store.export_backup()
    store.import_backup(before)
    assert learning.intro_batch()["phase"] == "quiz"
    assert learning.intro_batch_sizes()["grammar"] == 2
    for corrupt in ("queue", "batch", "size"):
        payload = decode_current(before)
        settings = payload["tables"]["app_setting"]
        row = next(r for r in settings if r["key"] == ("intro_batch_size_v1" if corrupt == "size" else "intro_batch_v1"))
        value = json.loads(row["value_json"])
        if corrupt == "queue":
            value["quiz_queue"][0]["question_id"] = "not-frozen"
        elif corrupt == "batch":
            value["batch_id"] = "b" * 32
        else:
            value["word"] = 99
        row["value_json"] = json.dumps(value)
        with pytest.raises(ValueError):
            store.import_backup(encode_current(payload))
    assert learning.intro_batch()["phase"] == "quiz"


def test_two_question_item_reasks_with_second_and_backup_checks_queue(stores):
    store, learning = stores
    learning.start_intro_batch(WORDS[:1], family="word", now=NOW)
    run_cards(learning, ["next"])
    state = learning.start_intro_quiz({WORDS[0]: guided(WORDS[0], 2)}, rng=random.Random(0))
    first, second = (q["id"] for q in guided(WORDS[0], 2))
    assert state["quiz_queue"] == [{"content_id": WORDS[0], "question_id": first}]
    state, _ = learning.answer_intro_quiz(WORDS[0], first, answered_text="错", correct=False, now=NOW)
    assert state["quiz_queue"] == [{"content_id": WORDS[0], "question_id": second}]
    validate_backup_rows(learning._db)
    before = store.export_backup()
    payload = decode_current(before)
    row = next(r for r in payload["tables"]["app_setting"] if r["key"] == "intro_batch_v1")
    value = json.loads(row["value_json"])
    value["quiz_queue"][0]["question_id"] = first  # frozen, but not the question the rules chose
    row["value_json"] = json.dumps(value)
    with pytest.raises(ValueError):
        store.import_backup(encode_current(payload))
    state, _ = learning.answer_intro_quiz(WORDS[0], second, answered_text="错", correct=False, now=NOW)
    assert state["quiz_queue"] == [{"content_id": WORDS[0], "question_id": first}]
    state, item = learning.answer_intro_quiz(WORDS[0], first, answered_text="对", correct=True, now=NOW)
    assert state["quiz_queue"] == [] and item["correct"] is True and set(item["answers"]) == {first, second}
    assert events(learning) == 0
