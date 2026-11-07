"""Shared learning dependencies fixtures and assertions; no collected scenarios."""
from support.learning_store_fixtures import START
from support.learning_store_fixtures import item
from support.learning_store_fixtures import question


def lesson(cid, dependencies=(), **kwargs):
    return item(cid, title=cid, depends_on=list(dependencies), **kwargs)


def queued(learning, target, qid=None, kind="grammar_meaning", ability="understanding"):
    qid = qid or target["id"] + ":q"
    learning.append_questions(question(target["id"], qid, kind=kind, ability=ability), now=START)
    return qid
