"""Shared study path service fixtures and assertions; no collected scenarios."""
from support.paths import ROOT as _TEST_ROOT
from kanamemory.content import ContentRepository
from kanamemory.learning_store import LearningStore
from pathlib import Path
from kanamemory.study_path import StudyPathService
from kanamemory.domain import StudyStore
from kanamemory.domain import _BatchQuestionStore
from kanamemory.domain import build_questions
from datetime import datetime
from copy import deepcopy
import pytest
from kanamemory import study_path_catalog
from datetime import timedelta
from datetime import timezone


CONTENT = _TEST_ROOT / "app/src/kanamemory/resources/content.sqlite3"


NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)


BASE = "grammar:test:path-base"


MORE = "grammar:test:path-more"


def lesson(cid, depends=()):
    return {"id": cid, "content_id": cid, "family": "grammar", "revision": "test-v1",
            "title": "测试学习项", "abilities": ["understanding", "application"],
            "depends_on": list(depends),
            "instruction": {"summary": "表示正在进行", "rule": "て形＋いる"},
            "question_templates": {}}


def rows(store, *tables):
    return {table: [tuple(row) for row in store._db.execute(
        "SELECT * FROM " + table + " ORDER BY rowid")] for table in tables}


def learner_rows(store):
    return rows(store, "study_card", "review_event", "practice_attempt",
                "session_answer", "word_spelling_attempt", "learning_content",
                "learning_unit", "learning_question", "learning_event")


@pytest.fixture
def setup_path(tmp_path, monkeypatch):
    with ContentRepository(CONTENT) as repo, StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        learning = LearningStore(store)
        kana = {symbol: next(k for k in repo.kana_list() if k["hiragana"] == symbol)["id"]
                for symbol in ("あ", "い", "う")}
        items = [lesson(BASE), lesson(MORE, [BASE])]

        def entry(symbol, script):
            return {"id": "kana:" + kana[symbol] + ":" + script, "kind": "kana",
                    "content_id": kana[symbol], "script": script, "title": symbol,
                    "depends_on": []}

        sections = [
            {"id": "section:h", "title": "平假名", "entries": [entry("あ", "hiragana"), entry("い", "hiragana")]},
            {"id": "section:k", "title": "片假名", "depends_on": ["section:h"], "entries": [entry("あ", "katakana")]},
            {"id": "section:u", "title": "下一组", "entries": [entry("う", "hiragana")]},
            {"id": "section:base", "title": "先修", "entries": [{"id": BASE, "kind": "learning", "content_id": BASE}]},
            {"id": "section:more", "title": "后续", "depends_on": ["section:base"],
             "entries": [{"id": MORE, "kind": "learning", "content_id": MORE}]},
            {"id": "section:ref", "title": "扩展", "optional": True,
             "entries": [{"id": "ref:old-kana", "kind": "reference", "content_id": "reference:old-kana"}]},
        ]
        for section in sections:
            section.setdefault("depends_on", [])
            section.setdefault("optional", False)
            section.update(stage_id="stage:test", stage_title="测试阶段",
                           unit_id="unit:test", unit_title="测试单元")
        path = {"version": "test-v1", "stages": [{"id": "stage:test", "title": "测试阶段",
                "units": [{"id": "unit:test", "title": "测试单元", "sections": sections}]}]}
        monkeypatch.setattr(study_path_catalog, "build_study_path", lambda *a, **kw: deepcopy(path))
        monkeypatch.setattr(study_path_catalog, "flatten_sections",
                            lambda value: [s for stage in value["stages"] for unit in stage["units"]
                                           for s in unit["sections"]])
        def make(target_store=store):
            return StudyPathService(repo, target_store, LearningStore(target_store), catalogue=items)
        yield repo, store, learning, kana, make, tmp_path / "learner"


def one_question(repo, store, cid, kind="kana_choice", script="hiragana"):
    question = build_questions(repo, _BatchQuestionStore(store, [cid]), kind=kind,
                               script=script, seed=41, limit=1, now=NOW, include_extended=True)[0]
    question.metadata["practice_config"].update(schedule_mode="spaced")
    return question


def old_learning_draft(learning):
    learning.enroll(lesson(BASE), now=NOW)
    q = {"id": "path-old-question", "target_id": BASE, "ability": "understanding",
         "kind": "grammar_meaning", "prompt": "本を読んでいます。",
         "answer": "正在读书", "input_mode": "choice", "revision": "test-v1"}
    learning.append_questions(q, now=NOW)
    learning.advance_instruction(BASE, "intro_done", now=NOW)
    learning.advance_instruction(BASE, "guide_correct", now=NOW)
    at = NOW + timedelta(seconds=61)
    learning.submit(q["id"], "正在读书", True, now=at)
    result = learning.finalize(q["id"], 3, now=at)
    learning.append_questions({**q, "id": "path-next-question"}, now=at)
    learning.save_draft("path-next-question", {"text": "未提交", "hint_used": False})
    return result
