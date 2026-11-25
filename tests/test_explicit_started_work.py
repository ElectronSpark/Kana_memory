"""Design 12 section 22 (brief J11 F3): the work a learner started by hand, read from the explicit-start record.

Made-up lessons on a real learner store (support.learning_fixtures). Covers: an unclaimed start names every
entry, a claimed one only the selected lesson, the deferred request too; each ability until its first formal
rating; a lesson without units until it is read; a new introduction (a reset) ends it; an app start (the archive
pass that ends every pending course question) changes nothing.
"""
from datetime import timedelta

from support.learning_fixtures import learning_app  # noqa: F401
from support.app_fixtures import question_app, setup_app  # noqa: F401
from kanamemory.explicit_learning_start import arm, claim, started_work
from kanamemory.learning_questions import build_learning_question
from kanamemory.practice_archive import archive_previous_rounds

A, B, R = "grammar:synthetic:started-a", "grammar:synthetic:started-b", "pronunciation:synthetic:started-read"


def choice(prompt, answer, other):
    return {"prompt": prompt, "answer": answer, "choices": [answer, other], "feedback": "说明",
            "input_mode": "choice", "safe": True}


def lesson(cid, abilities=("understanding", "application")):
    templates = {}
    if "understanding" in abilities:
        templates["grammar_meaning"] = [choice("本です。", "是书", "不是书")]
    if "application" in abilities:
        templates["grammar_cloze"] = [choice("本（　）。", "です", "だ")]
    item = {"id": cid, "content_id": cid, "family": "grammar", "title": cid.rsplit(":", 1)[-1], "revision": "1",
            "abilities": list(abilities), "source_ids": ["test"], "depends_on": [],
            "instruction": {"summary": "要点", "rule": "规则。", "steps": [], "example": "本です。",
                            "translation": "是书。", "prerequisite": "", "contrast": ""},
            "question_templates": templates}
    if not abilities:
        item["domain"] = "pronunciation"
    return item


def entry(app, cid):
    return {"entry_id": "learning:" + cid, "content_id": cid, "kind": "learning", "script": None,
            "introduction_id": app.store.queries.course_introduction_header(cid)["introduction_id"]}


def write(app, action):
    db = app.store._db
    with db:
        db.execute("BEGIN IMMEDIATE")
        return action(db)


def rate(app, cid, kind, now):
    learning = app.learning_store
    q = build_learning_question(learning.get_item(cid), kind)
    q["id"] = f"{cid}:{kind}"
    learning.append_questions(q, now=now)
    learning.submit(q["id"], q["answer"], True, now=now)
    learning.finalize(q["id"], 3, now=now)


def test_started_work_follows_the_record_and_the_first_ratings(learning_app):
    app = learning_app
    now = app._test_clock[0]
    learning = app.learning_store
    learning.enroll([lesson(A), lesson(B), lesson(R, ())], now=now)
    assert started_work(app.store._db) == frozenset()
    assert write(app, lambda db: arm(db, [entry(app, A), entry(app, B), entry(app, R)], now=now))
    both = {(A, "understanding"), (A, "application"), (B, "understanding"), (B, "application"), (R, None)}
    assert started_work(app.store._db) == both
    # An app start ends every pending course question; the started work is read from stored records alone.
    archive_previous_rounds(app.store, now=now)
    assert app.store.teaching_queries.explicit_started_work() == both
    # A real start chooses one lesson: only it remains.
    assert write(app, lambda db: claim(db, A, introduction_id=entry(app, A)["introduction_id"], now=now))
    assert started_work(app.store._db) == {(A, "understanding"), (A, "application")}
    learning.advance_instruction(A, "intro_done", now=now)
    learning.advance_instruction(A, "guide_correct", now=now)
    rate(app, A, "grammar_meaning", now + timedelta(minutes=12))
    assert started_work(app.store._db) == {(A, "application")}      # each ability until its first rating
    rate(app, A, "grammar_cloze", now + timedelta(minutes=13))
    assert started_work(app.store._db) == frozenset()


def test_a_lesson_without_units_until_read_and_a_new_introduction_ends_it(learning_app):
    app = learning_app
    now = app._test_clock[0]
    learning = app.learning_store
    learning.enroll([lesson(R, ()), lesson(B)], now=now)
    assert write(app, lambda db: arm(db, [entry(app, R), entry(app, B)], now=now))
    assert (R, None) in started_work(app.store._db)
    item = learning.instruction_item(R)
    learning.complete_read_only(R, expected_introduction_id=item["introduction_id"], fair_new=True)
    assert (R, None) not in started_work(app.store._db)
    with app.store._db:   # another introduction of B (what a reset to first teaching gives)
        app.store._db.execute("UPDATE learning_content SET introduction_id='other' WHERE content_id=?", (B,))
    assert started_work(app.store._db) == frozenset()


def test_a_deferred_request_counts_too(learning_app):
    app = learning_app
    now = app._test_clock[0]
    learning = app.learning_store
    learning.enroll([lesson(A), lesson(B)], now=now)
    assert write(app, lambda db: arm(db, [entry(app, A)], now=now))
    assert write(app, lambda db: claim(db, A, introduction_id=entry(app, A)["introduction_id"], now=now))
    assert write(app, lambda db: arm(db, [entry(app, B)], now=now + timedelta(seconds=1)))   # held for later
    assert started_work(app.store._db) == {(A, "understanding"), (A, "application"),
                                           (B, "understanding"), (B, "application")}
