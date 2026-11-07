"""Shared word interval fixtures and assertions; no collected scenarios."""
from kanamemory.domain import Question
from kanamemory.domain import StudyStore
from kanamemory.domain import WORD_ROMAJI_POLICY
from kanamemory.word_interval import apply_word_interval_adjustments
from datetime import datetime
import json
import pytest
from datetime import timedelta
from datetime import timezone
from uuid import uuid4


BASE = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


NOW = BASE + timedelta(days=1)


KA, KI = "kana-304b", "kana-304d"


@pytest.fixture
def store(tmp_path):
    with StudyStore(tmp_path / "learner", "UTC") as value:
        yield value


def mature(store, cid=KA, skill="romaji_kana", script="hiragana", now=BASE):
    store.record_review(cid, skill, script, 4, now=now, outcome="correct")
    card = store.get_card(cid, skill, script)
    assert card["scheduler_state"]["state"] == 2
    return card


def evidence(expected="か", *, result="correct", cid=KA, script="hiragana"):
    return {"version": 1, "practice_policy": WORD_ROMAJI_POLICY,
        "expected": expected, "actual": expected if result == "correct" else "き",
        "script": script, "assisted": False, "correct": result == "correct",
        "units": [{"content_id": cid, "symbol": expected, "result": result,
                   "error_types": [] if result == "correct" else ["substitution"]}],
        "errors": [], "ambiguous": False, "evidence_limited": False}


def apply(store, spelling=None, *, word_id="word:one", now=NOW, rollback=False):
    """Persist exactly the helper's transaction contract independently of UI."""
    spelling = evidence() if spelling is None else spelling
    q = Question(word_id, "word_meaning_kana", spelling["script"], "ka",
        spelling["expected"], metadata={"word_romaji_drill": True})
    sid = store.start_session([q], now=now)
    spelling.update(source_content_id=word_id, source_kind=q.kind, source_question_index=0)
    store._db.execute("BEGIN IMMEDIATE")
    try:
        adjustments = apply_word_interval_adjustments(store, spelling, q.script, now)
        spelling["schedule_adjustments"] = adjustments
        store._db.execute("INSERT INTO word_spelling_attempt VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(uuid4()), sid, 0, word_id, q.kind, q.script, now.isoformat(),
             spelling["actual"], int(spelling["assisted"]), int(spelling["correct"]), 0,
             json.dumps(spelling, ensure_ascii=False)))
        if rollback:
            raise ValueError("deliberate failed save")
        store._db.commit()
    except Exception:
        store._db.rollback()
        raise
    return adjustments


def due(card):
    return datetime.fromisoformat(card["due_at_utc"])


def unchanged_except_due(before, after):
    left, right = dict(before), dict(after)
    for item in (left, right):
        item.pop("due_at_utc")
        item.pop("scheduler_state_json")
        item["scheduler_state"] = {k: v for k, v in item["scheduler_state"].items() if k != "due"}
    assert left == right
    assert after["scheduler_state"]["due"] == after["due_at_utc"]
