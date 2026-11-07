"""Shared kana rule evidence fixtures and assertions; no collected scenarios."""
from kanamemory.kana_rule_evidence import SMALL_KANA_RULE
from kanamemory.kana_rule_evidence import SOKUON_RULE
from support.learning_store_fixtures import START
from support.learning_store_fixtures import item
from support.learning_store_fixtures import question
from support.learning_store_fixtures import ready
from kanamemory.kana_rule_evidence import record_rule_attempt


def record(store, event="event1", rule=SMALL_KANA_RULE, context="きゃ", outcome="unresolved", now=START, **kw):
    with store._db:
        store._db.execute("BEGIN IMMEDIATE")
        return record_rule_attempt(store, event, rule_id=rule, context_id=context,
            outcome=outcome, source=kw.pop("source", "kana"), script=kw.pop("script", "hiragana"), now=now, **kw)


def mature(learning, cid=SOKUON_RULE):
    learning.enroll(item(cid, domain="pronunciation"), now=START)
    when = ready(learning, cid)
    for n in range(5):
        q = question(cid, "mature:"+str(n), ability="application", kind="grammar_cloze")
        learning.append_questions(q, now=when)
        learning.submit(q["id"], q["answer"], True, now=when)
        learning.finalize(q["id"], 3, now=when)
        unit = learning.get_unit(cid, "application")
        if unit["scheduler_state"]["state"] == 2:
            return unit, when
        when = unit["scheduler_state"]["due"]
        from datetime import datetime
        when = datetime.fromisoformat(when)
    raise AssertionError("Failed to mature synthetic rule")
