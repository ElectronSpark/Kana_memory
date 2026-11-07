"""Reordering a saved curriculum cannot become a learner-data migration."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from kanamemory import study_path_catalog as catalog
from kanamemory.domain import Question, StudyStore
from kanamemory.learning_store import LearningStore
from kanamemory.study_path import StudyPathService, PATH_SETTING


NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
STUDENT, TEACHER, SCHOOL = ("word:upgrade:" + name for name in ("student", "teacher", "school"))
COPULA, PLACE = ("grammar:upgrade:" + name for name in ("copula", "place"))
RULES = [{"word_forms": ["学生"], "before": [COPULA]},
         {"word_forms": ["学校"], "before": [PLACE]}]


def item(cid, title, family, *, category="身份", abilities=None, depends=()):
    return {"id": cid, "content_id": cid, "family": family, "revision": "upgrade-fixture-v1",
            "title": title, "category": category, "level": "fixture",
            "abilities": abilities or ["understanding"], "depends_on": list(depends),
            "instruction": {"summary": "升级前已保存的教学快照", "rule": "保留原内容。"},
            "question_templates": {}}


@pytest.fixture
def upgrade_catalogue(monkeypatch):
    monkeypatch.setattr(catalog, "_PATH", {"VOCABULARY_WEAVE": []})
    monkeypatch.setattr(catalog, "WORD_TOPICS", [
        (3, "roles", "身份词", "学生与老师", "学生 老师"),
        (3, "places", "地点词", "学校", "学校")])
    monkeypatch.setattr(catalog, "SMALL_UNIT_PLANS", {})
    items = [item(STUDENT, "学生", "word", abilities=["understanding", "reading"]),
             item(TEACHER, "老师", "word"), item(SCHOOL, "学校", "word"),
             item(COPULA, "判断句", "grammar", depends=[STUDENT]),
             item(PLACE, "场所表达", "grammar", category="场所", depends=[COPULA])]
    # Only kana_list is needed to build the real navigation overlay. Practice
    # snapshots below are deliberately independent of any private corpus.
    return SimpleNamespace(kana_list=lambda: []), items


def service(repo, store, items):
    return StudyPathService(repo, store, LearningStore(store), catalogue=items)


def identity_snapshot(path):
    return {section["id"]: {name: deepcopy(section.get(name)) for name in (
        "id", "component_section_ids", "entries", "depends_on", "optional", "stage_id")}
        for section in path.sections}


def question(identity, target, ability, kind, answer):
    return {"id": identity, "target_id": target, "ability": ability, "kind": kind,
            "prompt": "升级前的原题", "answer": answer, "input_mode": "text",
            "revision": "upgrade-fixture-v1"}


def seed_learner(repo, store, items):
    legacy = service(repo, store, items)
    learning = legacy.learning
    legacy.add_entries([STUDENT, COPULA])
    learning.advance_instruction(STUDENT, "intro_done", now=NOW)
    learning.advance_instruction(STUDENT, "guide_correct", now=NOW)
    meaning = question("upgrade-student-first", STUDENT, "understanding", "vocab_meaning", "学生")
    reading = question("upgrade-student-reading", STUDENT, "reading", "vocab_reading", "がくせい")
    learning.append_questions([meaning, reading], now=NOW)
    when = NOW + timedelta(seconds=61)
    learning.submit(meaning["id"], meaning["answer"], True, now=when)
    learning.finalize(meaning["id"], 3, now=when)
    learning.append_questions({**meaning, "id": "upgrade-student-next"}, now=when)
    learning.save_draft(reading["id"], {"answer": "がく", "parts": ["が", "く"], "hint_used": False})
    assert learning.get_unit(STUDENT, "understanding")["review_count"] == 1
    assert learning.get_unit(STUDENT, "understanding")["due_at_utc"]
    assert learning.prerequisite_status(COPULA)["ready"]
    learning.advance_instruction(COPULA, "intro_done", now=when)
    learning.advance_instruction(COPULA, "guide_correct", now=when)
    grammar = question("upgrade-copula-pending", COPULA, "understanding", "grammar_meaning", "判断")
    learning.append_questions(grammar, now=when)
    learning.save_draft(grammar["id"], {"text": "未提交的判断", "answer": "未提交的判断"})

    store.record_review("kana-upgrade-reviewed", "kana_choice", "hiragana", 3, now=NOW)
    main = [Question("kana-upgrade-current", "kana_choice", "hiragana", "あ", "a", ["a", "i"]),
            Question("kana-upgrade-future", "kana_choice", "hiragana", "い", "i", ["i", "a"],
                     metadata={"not_before_utc": (NOW + timedelta(days=2)).isoformat()})]
    main_id = store.start_session(main, now=NOW)
    store.save_session_progress(main_id, 0, state={"page": "question", "romaji_answer": "old draft"})
    store.practice_lane = "word_romaji"
    word = Question("word:upgrade:spelling", "word_meaning_kana", "hiragana", "gakusei", "がくせい",
                    ["が", "く", "せ", "い"], metadata={"word_romaji_drill": True})
    word_id = store.start_session([word], now=NOW)
    store.save_session_progress(word_id, 0, state={"page": "word_question", "word_answer": "がく"})
    store.practice_lane = "main"
    return legacy


def learner_snapshot(store):
    learning = LearningStore(store)
    return {"database": tuple(store._db.iterdump()),
            "path_state": deepcopy(store.get_setting(PATH_SETTING)),
            "main": deepcopy(store.load_session_progress(lane="main")),
            "word": deepcopy(store.load_session_progress(lane="word_romaji")),
            "lessons": deepcopy(learning.list_items()),
            "units": [tuple(row) for row in store._db.execute("SELECT * FROM learning_unit ORDER BY unit_id")],
            "kana_cards": [tuple(row) for row in store._db.execute("SELECT * FROM study_card ORDER BY card_id")]}


def check_upgraded_navigation(repo, store, items, legacy_ids, old_word_unit, before):
    current = service(repo, store, items)
    word_section = current.entry_sections[STUDENT]
    grammar_section = current.entry_sections[COPULA]
    assert current.by_id[word_section]["unit_id"] != old_word_unit
    assert current.by_id[word_section]["unit_id"] == current.by_id[grammar_section]["unit_id"]
    assert identity_snapshot(current) == legacy_ids
    changes = store._db.total_changes
    overview = current.overview()
    plan = current.plan_next(20)
    assert plan and any(e["id"] == TEACHER for s in plan for e in s["entries"])
    # The unselected sibling remains available after its entire original
    # vocabulary section moves beside the grammar. The saved subset is exact.
    section = current.section(word_section)
    assert next(e["state"] for e in section["entries"] if e["id"] == TEACHER) == "new"
    assert {e["id"] for level in overview["stages"] for unit in level["units"]
            for section in unit["sections"] for e in section["entries"]} == {
        e["id"] for section in legacy_ids.values() for e in section["entries"]}
    assert current.preview_entries([STUDENT, COPULA])["total_count"] == 0
    assert store._db.total_changes == changes
    assert learner_snapshot(store) == before
    # Even an explicit repeat addition of the old selection is a true no-op:
    # no new teaching IDs, reset schedules, replacement questions or siblings.
    assert current.add_entries([STUDENT, COPULA, STUDENT])["total_count"] == 0
    assert store._db.total_changes == changes
    assert learner_snapshot(store) == before


def test_content_reordering_preserves_existing_progress_queues_drafts_and_due_dates(upgrade_catalogue, tmp_path):
    repo, items = upgrade_catalogue
    directory = tmp_path / "upgrade-learner"
    with StudyStore(directory, time_zone="UTC") as store:
        legacy = seed_learner(repo, store, items)
        legacy_ids = identity_snapshot(legacy)
        old_word_unit = legacy.by_id[legacy.entry_sections[STUDENT]]["unit_id"]
        before = learner_snapshot(store)
        catalog._PATH["VOCABULARY_WEAVE"] = deepcopy(RULES)
        check_upgraded_navigation(repo, store, items, legacy_ids, old_word_unit, before)
    # A clean store reopen must derive the same upgraded route from original
    # identities, without writing a migration or synthesizing learned status.
    with StudyStore(directory, time_zone="UTC") as reopened:
        assert learner_snapshot(reopened) == before
        check_upgraded_navigation(repo, reopened, items, legacy_ids, old_word_unit, before)


def test_adding_remaining_moved_sibling_appends_only_that_item_after_upgrade(upgrade_catalogue, tmp_path):
    repo, items = upgrade_catalogue
    with StudyStore(tmp_path / "append-after-upgrade", time_zone="UTC") as store:
        legacy = seed_learner(repo, store, items)
        original_section = legacy.entry_sections[STUDENT]
        before = learner_snapshot(store)
        old_questions = [tuple(row) for row in store._db.execute("SELECT * FROM learning_question ORDER BY rowid")]
        catalog._PATH["VOCABULARY_WEAVE"] = deepcopy(RULES)
        current = service(repo, store, items)
        assert current.entry_sections[TEACHER] == original_section
        result = current.add_entries([TEACHER, STUDENT])
        assert result["added_learning"] == [TEACHER]
        assert result["selected_entry_ids"] == [TEACHER]
        assert current.learning.get_item(SCHOOL) is None
        assert current.learning.get_item(PLACE) is None
        for old in before["lessons"]:
            assert current.learning.get_item(old["id"]) == old
        assert [tuple(row) for row in store._db.execute("SELECT * FROM learning_question ORDER BY rowid")] == old_questions
        assert store.load_session_progress(lane="main") == before["main"]
        assert store.load_session_progress(lane="word_romaji") == before["word"]
        assert original_section in store.get_setting(PATH_SETTING)["added_sections"]
        after = learner_snapshot(store)
        assert current.add_entries([TEACHER, STUDENT])["total_count"] == 0
        assert learner_snapshot(store) == after
