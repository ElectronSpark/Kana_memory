"""Batch quiz (批小测) in the native controller (synthetic lessons only)."""
from datetime import datetime, timedelta

from support.learning_fixtures import learning_app, click, finish_guided_learning
from support.course_support import batch_quiz_question
from support.app_fixtures import question_app, setup_app  # noqa: F401 (fixtures used by learning_app)
from support.fake_ui import text_of
from kanamemory import intro_batch
from kanamemory.course_line.batch_display import QUIZ_PAUSED
from kanamemory.learning_store import LearningStore
from kanamemory.learning_questions import PREFERENCE_VERSION, SOUND_PREFERENCE_VERSION
from kanamemory.learning_ui import LEARNING_PREFS

KINDS = ["vocab_meaning", "grammar_meaning"]


def prefs(kinds):
    # An explicit current choice, so no newer kind is switched on for it.
    return {"enabled": list(kinds), SOUND_PREFERENCE_VERSION: PREFERENCE_VERSION}


def choice(prompt, answer, other):
    return {"prompt": prompt, "answer": answer, "choices": [answer, other], "feedback": "说明",
            "input_mode": "choice", "safe": True}


def word(n, contexts=1):
    cid = f"word:synthetic:w{n}"
    templates = [choice(f"語{n}" if i == 0 else f"語{n}の本。", f"词{n}", "别的词") for i in range(contexts)]
    return {"id": cid, "content_id": cid, "family": "word", "title": f"語{n}", "reading": "ご",
            "meaning": f"词{n}", "revision": "1", "abilities": ["understanding"], "source_ids": ["test"],
            "instruction": {"summary": f"词{n}", "rule": "名词。", "steps": [], "example": f"語{n}です。",
                            "translation": f"是词{n}。", "prerequisite": "", "contrast": ""},
            "question_templates": {"vocab_meaning": templates}}


def start_quiz(app, items):
    app.store.set_setting(LEARNING_PREFS, prefs(KINDS))
    app.learning_store.enroll(items)
    app.continue_learning()
    while app.page == "learning_card":
        click(app, "下一项")
    assert app.page == "learning_batch_quiz"


def frozen(app, cid):
    return app.learning_store.instruction_item(cid)["checkpoint"][intro_batch.ITEM_KEY]


def queue(app):
    return app.learning_store.intro_batch()["quiz_queue"]


def wrong(question):
    return next(c for c in question["choices"] if c != question["answer"])


def restart(app):
    app.learning_store = LearningStore(app.store)
    app.page = "home"
    app.continue_learning()


def test_wrong_answer_shows_answer_and_card_then_requeues_last(learning_app):
    app = learning_app
    start_quiz(app, [word(1), word(2)])
    question = batch_quiz_question(app)
    cid = question["target_id"]
    n = cid[-1]
    assert f"語{n}です。" not in text_of(app.body)
    click(app, wrong(question))
    body = text_of(app.body)
    assert question["answer"] in body and "说明卡" in body
    assert f"語{n}です。" in body and f"词{n}" in body  # the item's card content
    assert [h["content_id"] for h in queue(app)][-1] == cid and len(queue(app)) == 2
    assert frozen(app, cid)["answers"][question["id"]]["correct"] is False
    click(app, "继续")
    assert app.page == "learning_batch_quiz" and batch_quiz_question(app)["target_id"] != cid
    # A correct answer gets brief feedback without the card.
    click(app, batch_quiz_question(app)["answer"])
    assert "说明卡" not in text_of(app.body)
    click(app, "继续")
    assert batch_quiz_question(app)["target_id"] == cid


def test_reask_uses_second_frozen_question_when_available(learning_app):
    app = learning_app
    start_quiz(app, [word(1, contexts=2), word(2)])
    two, one = frozen(app, "word:synthetic:w1")["questions"], frozen(app, "word:synthetic:w2")["questions"]
    assert len(two) == 2 and two[0]["prompt"] != two[1]["prompt"] and len(one) == 1
    while batch_quiz_question(app)["target_id"] != "word:synthetic:w1":
        click(app, batch_quiz_question(app)["answer"])
        click(app, "继续")
    assert batch_quiz_question(app) == two[0]
    click(app, wrong(two[0]))
    click(app, "继续")
    if batch_quiz_question(app)["target_id"] != "word:synthetic:w1":
        click(app, batch_quiz_question(app)["answer"])
        click(app, "继续")
    assert batch_quiz_question(app) == two[1]
    click(app, wrong(two[1]))
    click(app, "继续")
    # Both questions answered: the first one comes back.
    assert batch_quiz_question(app) == two[0]


def test_restart_mid_quiz_resumes_same_frozen_head(learning_app):
    app = learning_app
    start_quiz(app, [word(1), word(2), word(3)])
    done = batch_quiz_question(app)["target_id"]
    click(app, batch_quiz_question(app)["answer"])
    click(app, "继续")
    click(app, wrong(batch_quiz_question(app)))
    head = queue(app)[0]
    expected = next(q for q in frozen(app, head["content_id"])["questions"] if q["id"] == head["question_id"])
    before = {cid: frozen(app, cid) for cid in ("word:synthetic:w1", "word:synthetic:w2", "word:synthetic:w3")}
    restart(app)
    assert app.page == "learning_batch_quiz" and batch_quiz_question(app) == expected
    assert {cid: frozen(app, cid) for cid in before} == before
    asked = []
    while app.page == "learning_batch_quiz" and batch_quiz_question(app) is not None:
        asked.append(batch_quiz_question(app)["target_id"])
        click(app, batch_quiz_question(app)["answer"])
        click(app, "继续")
    assert done not in asked and len(asked) == 2
    assert app.learning_store.intro_batch() is None


def test_closed_kind_pauses_the_same_head(learning_app):
    app = learning_app
    start_quiz(app, [word(1), word(2)])
    question, saved = batch_quiz_question(app), queue(app)
    # A kept page cannot answer once the kind is closed.
    app.store.set_setting(LEARNING_PREFS, prefs(["grammar_meaning"]))
    click(app, question["choices"][0])
    assert app.page == "learning_wait" and QUIZ_PAUSED in text_of(app.body)
    assert queue(app) == saved and frozen(app, question["target_id"])["answers"] == {}
    app.show_intro_batch()
    assert app.page == "learning_wait" and QUIZ_PAUSED in text_of(app.body)
    restart(app)
    assert app.page != "learning_batch_quiz" and queue(app) == saved
    app.store.set_setting(LEARNING_PREFS, prefs(KINDS))
    restart(app)
    assert app.page == "learning_batch_quiz" and batch_quiz_question(app) == question


def test_completion_bridges_without_formal_evidence(learning_app):
    app = learning_app
    app.store.set_setting(LEARNING_PREFS, prefs(KINDS))
    app.learning_store.enroll([word(1), word(2)])
    app.continue_learning()
    ids = ("word:synthetic:w1", "word:synthetic:w2")
    db = app.learning_store._db
    def formal():
        return [tuple(r) for r in db.execute("""SELECT q.* FROM learning_question q JOIN learning_unit u
            USING(unit_id) WHERE u.content_id IN (?,?) ORDER BY q.rowid""", ids)]
    def schedule():
        return [tuple(r) for r in db.execute("""SELECT unit_id,scheduler_state_json,due_at_utc,review_count,
            probe_due_at_utc,last_unassisted_success_at_utc FROM learning_unit WHERE content_id IN (?,?)
            ORDER BY rowid""", ids)]
    questions, units = formal(), schedule()
    # _queue_learning_questions already created each item's pending formal question.
    assert len(questions) == 2 and all(row[4] == "pending" and row[6] is None for row in questions)
    while app.page == "learning_card":
        click(app, "下一项")
    click(app, wrong(batch_quiz_question(app)))
    click(app, "继续")
    while queue(app):
        assert db.execute("SELECT COUNT(*) FROM learning_event").fetchone()[0] == 0
        click(app, batch_quiz_question(app)["answer"])
        assert formal() == questions and schedule() == units
        if queue(app):
            click(app, "继续")
    assert all(app.learning_store.get_item(cid)["stage"] == "guide" for cid in ids)
    now = app._test_clock[0]
    click(app, "继续")
    assert app.learning_store.intro_batch() is None
    for cid in ids:
        row = app.learning_store.instruction_row(cid)
        assert row["stage"] == "bridge" and row["guided_at_utc"] == now.isoformat()
        assert datetime.fromisoformat(row["retrieval_at_utc"]) == now + timedelta(seconds=60)
        assert app.learning_store.get_unit(cid, "understanding")["instruction_ready"] == 1
    assert db.execute("SELECT COUNT(*) FROM learning_event").fetchone()[0] == 0
    assert formal() == questions and schedule() == units


def test_finish_guided_learning_helper_closes_the_batch(learning_app):
    app = learning_app
    app.store.set_setting(LEARNING_PREFS, prefs(KINDS))
    app.learning_store.enroll([word(1, contexts=2), word(2)])
    app.continue_learning()
    assert app.page == "learning_card"
    assert finish_guided_learning(app) not in {"learning_card", "learning_batch_quiz"}
    assert app.learning_store.intro_batch() is None
