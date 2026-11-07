"""Shared learning practice store fixtures and assertions; no collected scenarios."""
from support.learning_practice_policy_support import CID
from kanamemory.learning_store import TABLES
from datetime import datetime
from datetime import timezone


START = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


def all_rows(store):
    return {name: [tuple(r) for r in store._db.execute('SELECT * FROM ' + name + ' ORDER BY rowid')]
            for name in TABLES}


def answer_guide(learning, question):
    learning.advance_instruction(CID, 'guide', now=START, state={
        'guide_question': question, 'guide_draft': {'answer': question['answer']},
        'guide_answer': question['answer'], 'guide_correct': True})
