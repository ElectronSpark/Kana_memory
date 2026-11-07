"""Shared learning probe fixtures and assertions; no collected scenarios."""
from support.learning_store_fixtures import START
from datetime import datetime
from support.learning_store_fixtures import item
from support.learning_store_fixtures import question
from support.learning_store_fixtures import ready
from datetime import timedelta


def overdue_probe(learning):
    learning.enroll(item(), now=START)
    learning.append_questions(question(), now=START)
    first = ready(learning)
    learning.submit("q1", "correct", True, now=first)
    initial = learning.finalize("q1", 3, now=first)
    deadline = datetime.fromisoformat(initial["due_at_utc"])
    learning.append_questions(question(qid="legacy-choice"), now=first)
    learning.submit("legacy-choice", "correct", True, now=deadline)
    second = learning.finalize("legacy-choice", 3, now=deadline)
    assert datetime.fromisoformat(second["due_at_utc"]) > deadline + timedelta(seconds=60)
    return deadline, learning.get_unit(item()["id"], "understanding")
