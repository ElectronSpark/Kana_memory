"""Batch introduction cards in the native controller (synthetic lessons only)."""
from datetime import datetime, timedelta

import pytest

from support.learning_fixtures import learning_app, click, earlier_release_batches
from support.app_fixtures import question_app, setup_app
from support.fake_ui import text_of, descendants
from kanamemory import intro_batch
from kanamemory.learning_store import LearningStore
from kanamemory.learning_ui import LEARNING_PREFS


@pytest.fixture(autouse=True)
def _earlier_release(learning_app):
    """These cards and quizzes belong to batches an earlier release opened (probe first opens none)."""
    earlier_release_batches(learning_app)


KINDS = ["vocab_meaning", "grammar_meaning", "pronunciation_rule"]


def choice(prompt, answer, other):
    return [{"prompt": prompt, "answer": answer, "choices": [answer, other], "feedback": "说明",
             "input_mode": "choice", "safe": True}]


def word(n):
    cid = f"word:synthetic:w{n}"
    return {"id": cid, "content_id": cid, "family": "word", "title": f"語{n}", "reading": "ご",
            "meaning": f"词{n}", "revision": "1", "abilities": ["understanding"], "source_ids": ["test"],
            "instruction": {"summary": f"词{n}", "rule": "名词。", "steps": [], "example": f"語{n}です。",
                            "translation": f"是词{n}。", "prerequisite": "", "contrast": ""},
            "question_templates": {"vocab_meaning": choice(f"語{n}", f"词{n}", "别的词")}}


def grammar():
    cid = "grammar:synthetic:g"
    return {"id": cid, "content_id": cid, "family": "grammar", "title": "〜合成", "reading": "",
            "meaning": "合成语法", "revision": "1", "abilities": ["understanding"], "source_ids": ["test"],
            "instruction": {"summary": "表示合成。", "rule": "名词＋合成", "steps": [], "example": "本合成。",
                            "translation": "书合成。", "prerequisite": "", "contrast": "不能接动词原形。"},
            "question_templates": {"grammar_meaning": choice("本合成。", "合成", "别的")}}


def start(app, items):
    app.store.set_setting(LEARNING_PREFS, {"enabled": KINDS})
    app.learning_store.enroll(items)
    app.continue_learning()


def checkpoint(app, cid):
    return app.learning_store.instruction_item(cid)["checkpoint"]


def test_card_replaces_picker_and_marks_exposure(learning_app):
    app = learning_app
    start(app, [word(1), word(2)])
    assert app.page == "learning_card"
    assert "第 1 / 2 项" in text_of(app.body) and "語1" in text_of(app.body)
    assert "初学" not in text_of(app.body)
    state = app.learning_store.intro_batch()
    assert [e["content_id"] for e in state["entries"]] == ["word:synthetic:w1", "word:synthetic:w2"]
    saved = checkpoint(app, "word:synthetic:w1")
    assert saved["instruction_exposed_at_utc"] and saved[intro_batch.ROUTE_KEY] == intro_batch.ROUTE
    assert "initial_familiarity" not in saved


def test_next_advances_and_restart_resumes_at_saved_card(learning_app):
    app = learning_app
    start(app, [word(1), word(2), word(3)])
    click(app, "下一项")
    assert app.page == "learning_card" and "第 2 / 3 项" in text_of(app.body)
    profile = checkpoint(app, "word:synthetic:w1")["initial_familiarity"]
    assert profile["levels"] == {"understanding": "new"} and profile["source"] == intro_batch.CARD_SOURCE
    assert app.learning_store.intro_batch()["card_index"] == 1
    app.learning_store = LearningStore(app.store)
    app.page = "home"
    app.continue_learning()
    assert app.page == "learning_card" and app._learning_item["id"] == "word:synthetic:w2"
    assert app.learning_store.intro_batch()["card_index"] == 1


def test_known_writes_diagnostic_profile_and_skips_quiz(learning_app):
    app = learning_app
    start(app, [word(1), word(2)])
    now = app._test_clock[0]
    # Probe first: the card no longer offers it; a saved batch can still hold this choice.
    assert "我已经会了" not in text_of(app.body)
    app.choose_intro_card("known", expected=dict(app._intro_card_entry))
    item = app.learning_store.instruction_item("word:synthetic:w1")
    profile = item["checkpoint"]["initial_familiarity"]
    assert profile["route"] == "diagnostic" and profile["levels"] == {"understanding": "familiar"}
    assert profile["title_answer_exposed"] is True
    assert item["stage"] == "bridge"
    assert datetime.fromisoformat(item["retrieval_at_utc"]) == now + timedelta(seconds=60)
    assert not app.learning_store.history()
    click(app, "下一项")
    assert app.page == "learning_batch_quiz"
    assert app.learning_store.intro_batch()["quiz_queue"][0]["content_id"] == "word:synthetic:w2"
    click(app, app._intro_quiz_question["answer"])
    click(app, "继续")
    assert app.learning_store.intro_batch() is None
    assert app.learning_store.get_item("word:synthetic:w2")["stage"] == "bridge"
    assert all(unit["review_count"] == 0 and not unit["scheduler_state"]
               for cid in ("word:synthetic:w1", "word:synthetic:w2")
               for unit in app.learning_store.get_item(cid)["units"])
    assert not app.learning_store.history()


def test_four_words_then_one_grammar(learning_app):
    app = learning_app
    start(app, [*(word(n) for n in range(1, 6)), grammar()])
    state = app.learning_store.intro_batch()
    assert state["family"] == "word" and len(state["entries"]) == 4
    for _ in range(4):
        click(app, "下一项")
    assert app.page == "learning_batch_quiz"
    while app.page == "learning_batch_quiz":
        click(app, app._intro_quiz_question["answer"])
        click(app, "继续")
    # The fifth word opens its own batch before the grammar in dispatch order.
    state = app.learning_store.intro_batch()
    assert app.page == "learning_card" and [e["content_id"] for e in state["entries"]] == ["word:synthetic:w5"]
    click(app, "下一项")
    click(app, app._intro_quiz_question["answer"])
    click(app, "继续")
    state = app.learning_store.intro_batch()
    assert app.page == "learning_card" and state["family"] == "grammar" and len(state["entries"]) == 1
    body = text_of(app.body)
    assert "第 1 / 1 项" in body and "名词＋合成" in body and "不能接动词原形。" in body
    click(app, "展开讲解")
    assert "收起讲解" in text_of(app.body)


def test_saved_familiarity_keeps_old_intro(learning_app):
    app = learning_app
    app.store.set_setting(LEARNING_PREFS, {"enabled": KINDS})
    app.learning_store.enroll([word(1)])
    saved = app.learning_store.get_item("word:synthetic:w1")
    app.learning_store.set_initial_familiarity("word:synthetic:w1", {"understanding": "new"},
                                               expected_introduction_id=saved["introduction_id"])
    app.continue_learning()
    assert app.page == "learning_intro"
    assert app.learning_store.intro_batch() is None


def test_pronunciation_item_starts_with_a_probe(learning_app):
    app = learning_app
    cid = "grammar:synthetic:sound"
    item = {**grammar(), "id": cid, "content_id": cid, "domain": "pronunciation",
            "question_templates": {"pronunciation_rule": [{**choice("合成", "规则", "别的")[0],
                                                           "text_roles": {"prompt": "study", "answer": "ui"}}]}}
    start(app, [item])
    # Probe first (2026-11-21): no picker and no batch; the rule's first question is its probe.
    assert app.page == "learning_question" and app._learning_question["kind"] == "pronunciation_rule"
    assert checkpoint(app, cid)["initial_familiarity"]["evidence"] == "probe_v1"
    assert app.learning_store.intro_batch() is None


def test_settings_selectors_persist_sizes(learning_app):
    app = learning_app
    app.show_learning_settings()
    assert "每批新词汇数" in text_of(app.body) and "每批新语法数" in text_of(app.body)
    app.intro_batch_word_size.value = "6"
    app.intro_batch_word_size.on_change(app.intro_batch_word_size)
    app.intro_batch_grammar_size.value = "2"
    app.intro_batch_grammar_size.on_change(app.intro_batch_grammar_size)
    assert app.learning_store.intro_batch_sizes() == {"version": 1, "word": 6, "grammar": 2}
    app.store.set_setting(LEARNING_PREFS, {"enabled": KINDS})
    app.learning_store.enroll([word(n) for n in range(1, 8)])
    app.continue_learning()
    assert len(app.learning_store.intro_batch()["entries"]) == 6


def test_review_backlog_hint_does_not_block(learning_app):
    app = learning_app
    start(app, [word(1), word(2)])
    assert "有到期复习" not in text_of(app.body)
    due = word(9)
    app.learning_store.enroll([due], now=app._test_clock[0] - timedelta(minutes=3))
    app.learning_store.advance_instruction(due["id"], "intro_done", now=app._test_clock[0] - timedelta(minutes=3))
    app.learning_store.advance_instruction(due["id"], "guide_correct", now=app._test_clock[0] - timedelta(minutes=3))
    app.continue_learning()
    # Dispatch still owns order: the due review comes first, the batch waits.
    assert app.page == "learning_question" and app._learning_question["target_id"] == due["id"]
    assert app.learning_store.intro_batch()["card_index"] == 0
    # Returning to the open batch shows the hint, which never blocks it.
    app.show_intro_batch()
    assert app.page == "learning_card" and "有到期复习，建议先复习" in text_of(app.body)
    click(app, "下一项")
    assert app.page == "learning_card" and app.learning_store.intro_batch()["card_index"] == 1
