"""Shared ai material fixtures; no test scenarios or autouse registration."""
from datetime import datetime, timezone
import pytest
from kanamemory.domain import StudyStore, Question
from kanamemory.learning_store import LearningStore
from kanamemory.ai_materials import AIMaterialStore
from kanamemory.example_store import ExampleStore, empty_pack
from kanamemory.learning_content import get_learning_item
from kanamemory.learning_questions import build_learning_question
from support.paths import ROOT


NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)


CONTENT = ROOT / "app/src/kanamemory/resources/content.sqlite3"


def new_word(alias="candidate:stars", **updates):
    item = {"id": alias, "family": "word", "domain": "", "title": "星空", "reading": "ほしぞら",
        "meaning": "星空；布满星星的天空", "depends_on": [],
        "instruction": {"summary": "布满星星的天空", "rule": "名词。可说星空を見る。",
            "steps": ["星空（ほしぞら）", "星空を見る：看星空"], "example": "星空を見ます。",
            "translation": "看星空。", "prerequisite": "", "contrast": "星是星星，星空是天空。"},
        "question_templates": {
            "vocab_meaning": [{"prompt": "星空", "task": "选择词义。", "answer": "星空",
                "feedback": "星空表示布满星星的天空。", "choices": ["星空", "地板", "森林"],
                "input_mode": "choice"}],
            "vocab_reading": [{"prompt": "星空", "task": "写出假名读法。", "answer": "ほしぞら",
                "feedback": "星空读作ほしぞら。", "choices": [], "input_mode": "text"}]}}
    item.update(updates)
    return item


def examples(target="candidate:stars", count=1):
    sentences = ["星空を見ます。", "星空がきれいです。", "星空の写真を撮ります。", "星空を眺めます。",
        "星空が見えます。", "星空の下を歩きます。", "星空について話します。", "星空が広がっています。",
        "星空を見に行きます。", "星空を描きます。"]
    pack = {**empty_pack(), "status": "draft"}
    for index, sentence in enumerate(sentences[:count]):
        eid = f"ex{index + 1}"
        pack["examples"].append({"id": eid, "revision": "1", "text": sentence,
            "translation": "含有星空的示范句。", "reading": "", "source_ids": []})
        pack["references"].append({"id": f"ref{index + 1}", "example_id": eid, "example_revision": "1",
            "target_id": target, "spans": [{"start": 0, "end": 2, "quote": "星空"}],
            "hint": {"meaning": "星空", "reading": "ほしぞら", "rule": ""},
            "explanation": "这里指布满星星的天空。", "weight": 1})
    return pack


def progress(store):
    return {table: [tuple(row) for row in store._db.execute('SELECT * FROM "' + table + '" ORDER BY rowid')]
            for table, in store._db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if table != "app_setting"}


@pytest.fixture
def resources(tmp_path):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        learning = LearningStore(store)
        known = [get_learning_item("grammar:copula:polite")]
        store.record_review("kana-3042", "kana_choice", "hiragana", 3, outcome="correct", now=NOW)
        sid = store.start_session([Question("kana-3044", "kana_choice", "hiragana", "い", "i", ["i", "a"])], now=NOW)
        store.save_session_progress(sid, 0, 0, state={"romaji_answer": "draft"})
        learning.enroll(known[0], now=NOW)
        q = build_learning_question(known[0], "grammar_meaning")
        learning.append_questions([q], now=NOW)
        learning.save_draft(q["id"], {"answer": "未完成草稿"})
        example_store = ExampleStore(store, learning, known, bundled_pack=empty_pack())
        yield store, learning, known, example_store, AIMaterialStore(store, known, example_store)


def stage(service, *, items=None, pack=None, request="request-1"):
    return service.stage("examples", "", [new_word()] if items is None else items,
                         examples() if pack is None else pack, [], request)
