"""Shared learning store fixtures; no test scenarios or autouse registration."""
from datetime import datetime, timedelta, timezone
import pytest
from kanamemory.domain import StudyStore
from kanamemory.learning_store import LearningStore


START = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


def item(cid="grammar:te_iru:progressive", **kw):
    return {"id": cid, "family": "grammar", "revision": "v1", "abilities": ["understanding", "application"],
            "instruction": {"title": "〜ている", "meaning": "正在做", "rule": "て形＋います"}, **kw}


def question(cid="grammar:te_iru:progressive", qid="q1", ability="understanding", kind="grammar_meaning", **kw):
    return {"id": qid, "target_id": cid, "ability": ability, "kind": kind,
            "prompt": "今、本を読んでいます。", "answer": "正在读书", "input_mode": "choice", "revision": "v1", **kw}


def ready(learning, cid="grammar:te_iru:progressive", when=START):
    learning.advance_instruction(cid, "intro_done", now=when)
    learning.advance_instruction(cid, "guide_correct", now=when)
    return when + timedelta(seconds=61)


@pytest.fixture
def stores(tmp_path):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        yield store, LearningStore(store)


def legacy_dump(store):
    return {name: [tuple(row) for row in store._db.execute("SELECT * FROM " + name + " ORDER BY rowid")]
            for name in ("study_card", "study_session", "review_event", "session_checkpoint", "session_answer", "word_spelling_attempt")}
