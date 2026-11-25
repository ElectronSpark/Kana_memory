"""Lessons that are only read, and abilities a lesson no longer lists (brief J6 R1-R4).

Made-up lessons, temporary stores and the synthetic test clock only.
"""
from datetime import timedelta

import pytest

from support.learning_fixtures import learning_app, click
from support.app_fixtures import question_app, setup_app  # noqa: F401 (fixtures used by learning_app)
from support.content_cache_fixtures import drop_content_caches  # noqa: F401
from support.fake_ui import text_of
from kanamemory import content_caches, learning_content
from kanamemory.learning_questions import PREFERENCE_VERSION, SOUND_PREFERENCE_VERSION
from kanamemory.learning_store import LearningStore, READ_ONLY_QUESTION, validate_backup_rows
from kanamemory.learning_ui import LEARNING_PREFS, READ_ONLY_DONE, READ_ONLY_LINE
from kanamemory.practice_archive import archive_previous_rounds
from kanamemory.read_only_lessons import read_lesson_ids, read_only_lessons, retired_ability

KINDS = ["vocab_meaning", "vocab_reading", "grammar_meaning", "grammar_cloze", "pronunciation_rule",
         "pronunciation_application"]
RID = "pronunciation:synthetic:read"
AID, BID = "grammar:synthetic:before", "grammar:synthetic:after"
DISABLED = "未完成题目与复习时间已保留"


def choice(prompt, answer, other):
    return {"prompt": prompt, "answer": answer, "choices": [answer, other], "feedback": "说明",
            "input_mode": "choice", "safe": True}


def lesson(cid, *, abilities=("understanding",), depends_on=(), domain=None, title=None):
    item = {"id": cid, "content_id": cid, "family": "grammar", "title": title or cid.rsplit(":", 1)[-1],
            "revision": "1", "abilities": list(abilities), "source_ids": ["test"], "depends_on": list(depends_on),
            "instruction": {"summary": "要点", "rule": "规则。", "steps": [], "example": "本です。",
                            "translation": "是书。", "prerequisite": "", "contrast": ""},
            "question_templates": {"grammar_meaning": [choice("本です。", "是书", "不是书")]} if abilities else {}}
    if domain:
        item["domain"] = domain
    return item


def read_only(cid=RID, **extra):
    return lesson(cid, abilities=(), domain="pronunciation", title="只读的一课", **extra)


def prefs(app, kinds=KINDS):
    app.store.set_setting(LEARNING_PREFS, {"enabled": list(kinds), SOUND_PREFERENCE_VERSION: PREFERENCE_VERSION})


def enroll(app, *items):
    prefs(app)
    app.learning_store.enroll(list(items))
    for item in items:
        saved = app.learning_store.get_item(item["id"])
        if saved["enrolled_abilities"]:
            app._queue_learning_questions(saved)


def stage(app, cid):
    return app.learning_store.get_item(cid)["stage"]


def restart(app):
    """What every app start does to the course queue: its pending questions end; the queue pass plans again."""
    archive_previous_rounds(app.store)
    app._queue_missing_learning_questions()


# R1: offered once, in route order, completed by 知道了

def test_a_read_only_lesson_is_valid_content_without_units(learning_app):
    app = learning_app
    enroll(app, read_only())
    item = app.learning_store.get_item(RID)
    assert item["read_only"] is True and item["units"] == [] and item["enrolled_abilities"] == []
    assert app.learning_store._db.execute("SELECT COUNT(*) FROM learning_unit").fetchone()[0] == 0
    assert app.learning_store.items_needing_questions() == []
    assert read_only_lessons(app.learning_store._db)[RID]["read"] is False
    validate_backup_rows(app.learning_store._db)


def test_offered_once_with_know_button_and_completed(learning_app):
    app = learning_app
    enroll(app, read_only())
    counts = app.learning_store.counts(KINDS)
    assert counts["instruction"] == 1 and counts["unavailable_content"] == 0 and counts["disabled"] == 0
    head = app.learning_store.current(KINDS)
    assert head["type"] == "instruction" and head["content"]["id"] == RID
    assert head["question"]["id"] == READ_ONLY_QUESTION + RID
    app.continue_learning()
    assert app.page == "learning_intro"
    shown = text_of(app.body)
    assert READ_ONLY_LINE in shown and READ_ONLY_DONE in shown and "跟着做一题" not in shown
    assert DISABLED not in shown
    click(app, READ_ONLY_DONE)
    assert stage(app, RID) == "done"
    assert app.learning_store.current(KINDS) is None
    assert app.learning_store.counts(KINDS)["instruction"] == 0
    assert RID in read_lesson_ids(app.learning_store._db)
    assert app.learning_store._db.execute("SELECT COUNT(*) FROM learning_event").fetchone()[0] == 0
    assert DISABLED not in text_of(app.body)
    validate_backup_rows(app.learning_store._db)


def test_route_order_among_first_teachings(learning_app):
    app = learning_app
    enroll(app, lesson(AID), read_only(), lesson(BID))
    order = []
    for _ in range(3):
        head = app.learning_store.current(KINDS)
        order.append(head["content"]["id"])
        if head["content"]["id"] == RID:
            app.learning_store.complete_read_only(RID, expected_introduction_id=head["content"]["introduction_id"])
        else:
            # Pass over the ordinary lesson's teaching for this check, as the learner's skip does.
            skipped = app.learning_store.current(KINDS, skip_teaching_ids=set(order) - {RID})
            order.append(skipped["content"]["id"])
            break
    assert order[:2] == [AID, RID]


def test_breadth_first_candidate_of_new_content(learning_app):
    app = learning_app
    enroll(app, read_only())
    head = app.learning_store.current(KINDS, fair_new=True, teaching_only=True, include_unassessed=True)
    assert head["type"] == "instruction" and head["content"]["id"] == RID and head["read_only"] is True
    lessons_only = app.learning_store.current(KINDS, fair_new=True, teaching_only=True)
    assert lessons_only["content"]["id"] == RID
    assert app.learning_store.current(KINDS, fair_new=True, teaching_only=True,
                                      teaching_content_ids={AID}) is None


def test_reading_again_changes_nothing(learning_app):
    app = learning_app
    enroll(app, read_only())
    item = app.learning_store.get_item(RID)
    assert app.learning_store.complete_read_only(RID, expected_introduction_id=item["introduction_id"]) is True
    before = [tuple(row) for row in app.learning_store._db.execute("SELECT * FROM learning_content")]
    assert app.learning_store.complete_read_only(RID, expected_introduction_id=item["introduction_id"]) is False
    app.show_learning_instruction(app.learning_store.instruction_item(RID))
    assert READ_ONLY_LINE in text_of(app.body)
    assert [tuple(row) for row in app.learning_store._db.execute("SELECT * FROM learning_content")] == before


# R2: blocks before, counts as known after

def test_dependant_blocked_before_and_free_after(learning_app):
    app = learning_app
    enroll(app, read_only(), lesson(BID, depends_on=[RID]))
    status = app.learning_store.prerequisite_status(BID)
    assert status["blocked"] and [entry["id"] for entry in status["missing"]] == [RID]
    head = app.learning_store.current(KINDS)
    assert head["content"]["id"] == RID   # the prerequisite comes first
    item = app.learning_store.get_item(RID)
    app.learning_store.complete_read_only(RID, expected_introduction_id=item["introduction_id"])
    assert not app.learning_store.prerequisite_status(BID)["blocked"]
    assert RID in app.store.queries.instruction_ready_ids()
    from kanamemory.learning_dependencies import independent_ready_ids
    assert RID in independent_ready_ids(app.learning_store)
    from kanamemory.course_planning import known_ids
    assert RID in known_ids(app.store.queries)


def test_dependant_page_leads_to_the_prerequisite(learning_app):
    app = learning_app
    enroll(app, read_only(), lesson(BID, depends_on=[RID]))
    # The dependant's own head first (the learner passed over the read-only one in this run).
    app._guide_skipped_ids = {RID}
    app.continue_learning()
    assert app.page == "learning_wait"
    shown = text_of(app.body)
    assert "先阅读「只读的一课」" in shown and "前置内容的练习暂不可用" not in shown
    click(app, "先阅读「只读的一课」")
    assert app.page == "learning_intro" and READ_ONLY_DONE in text_of(app.body)


# R3: never due, never in counts; backup; app starts

def test_app_start_before_and_after_reading(learning_app):
    app = learning_app
    enroll(app, read_only(), lesson(AID))
    restart(app)
    assert stage(app, RID) == "intro"
    assert RID in [cid for cid, _ in app.learning_store.read_only_unread()]
    item = app.learning_store.get_item(RID)
    app.learning_store.complete_read_only(RID, expected_introduction_id=item["introduction_id"])
    restart(app)
    assert stage(app, RID) == "done" and RID in read_lesson_ids(app.learning_store._db)
    assert app.learning_store.read_only_unread() == []


def test_backup_and_restore_keep_read(learning_app, tmp_path):
    app = learning_app
    enroll(app, read_only())
    item = app.learning_store.get_item(RID)
    app.learning_store.complete_read_only(RID, expected_introduction_id=item["introduction_id"])
    from kanamemory.progress_backup import export_backup, import_backup, inspect_backup
    data = export_backup(app.store)
    inspect_backup(app.store, data)
    from kanamemory.domain import StudyStore
    other = StudyStore(tmp_path / "restored", time_zone="UTC")
    try:
        import_backup(other, data)
        restored = LearningStore(other)
        assert restored.get_item(RID)["stage"] == "done"
        assert RID in read_lesson_ids(restored._db)
        assert restored.current(KINDS) is None
    finally:
        other.close()


# R4: abilities a content update removed

@pytest.fixture
def catalogue(monkeypatch, drop_content_caches):  # noqa: F811
    def put(item):
        # A course patched again in the middle drops the content caches itself (content_cache_fixtures).
        monkeypatch.setitem(learning_content._ITEMS, item["id"], item)
        content_caches.clear_all()
    return put


def test_removed_ability_is_never_planned_served_or_counted(learning_app, catalogue):
    app = learning_app
    two = lesson(AID, abilities=("understanding", "application"))
    two["question_templates"]["grammar_cloze"] = [choice("本（　）。", "です", "だ")]
    catalogue(two)
    enroll(app, two)
    assert {row["ability"] for row in app.learning_store.iter_pending_questions(KINDS)} == {
        "understanding", "application"}
    catalogue(lesson(AID, abilities=("understanding",)))
    assert retired_ability(AID, "application") and not retired_ability(AID, "understanding")
    assert {row["ability"] for row in app.learning_store.iter_pending_questions(KINDS)} == {"understanding"}
    counts = app.learning_store.counts(KINDS)
    assert counts["disabled"] == 0 and counts["pending"] == 1
    item = app.learning_store.get_item(AID)
    assert item["enrolled_abilities"] == ["understanding"] and item["retired_abilities"] == ["application"]
    restart(app)
    planned = {row["ability"] for row in app.learning_store.iter_pending_questions(KINDS)}
    assert planned == {"understanding"}
    # The unit stays stored as history.
    assert app.learning_store.get_unit(AID, "application") is not None


def test_all_removed_before_the_explanation_is_offered_once(learning_app, catalogue):
    app = learning_app
    catalogue(lesson(AID))
    enroll(app, lesson(AID))
    catalogue(lesson(AID, abilities=()))
    restart(app)
    assert app.learning_store.get_item(AID)["read_only"] is True
    head = app.learning_store.current(KINDS)
    assert head["content"]["id"] == AID and head["read_only"] is True
    assert app.learning_store.counts(KINDS)["unavailable_content"] == 0
    app.continue_learning()
    click(app, READ_ONLY_DONE)
    assert stage(app, AID) == "done" and AID in read_lesson_ids(app.learning_store._db)


def test_all_removed_after_the_explanation_counts_as_read(learning_app, catalogue):
    app = learning_app
    catalogue(lesson(AID))
    enroll(app, lesson(AID))
    item = app.learning_store.get_item(AID)
    app.learning_store.mark_instruction_exposed(AID, expected_introduction_id=item["introduction_id"])
    catalogue(lesson(AID, abilities=()))
    restart(app)
    assert AID in read_lesson_ids(app.learning_store._db)
    assert app.learning_store.current(KINDS) is None
    counts = app.learning_store.counts(KINDS)
    assert counts["instruction"] == 0 and counts["disabled"] == 0 and counts["unavailable_content"] == 0
    assert AID in app.store.queries.instruction_ready_ids()


def test_later_clock(learning_app):
    app = learning_app
    enroll(app, read_only())
    app._test_clock[0] += timedelta(days=30)
    assert app.learning_store.counts(KINDS)["waiting"] == 0
    assert app.learning_store.current(KINDS)["content"]["id"] == RID


# R12: templates of an ability the lesson no longer lists are ignored, never an error

def bind(monkeypatch, item, templates):
    """A varied_practice binding for ``item`` (its fingerprint) holding ``templates``; the leftovers are skipped,
    so their shape does not matter."""
    from kanamemory import learning_questions
    from kanamemory.material_review import item_fingerprint
    binding = {"item_fingerprint": item_fingerprint(item), "item_revision": item["revision"],
               "templates": templates, "contexts": {}}
    monkeypatch.setattr(learning_questions, "_PRACTICE", {"schema_version": 1, "items": {item["id"]: binding}})


LEFTOVER = [{"prompt": "本（　）。", "answer": "です"}]


def test_a_leftover_extension_kind_is_skipped_and_the_rest_is_served(learning_app, catalogue, monkeypatch):
    from kanamemory.learning_questions import practice_templates, question_kinds
    app = learning_app
    two = lesson(AID, abilities=("understanding", "application"))
    two["question_templates"]["grammar_cloze"] = [choice("本（　）。", "です", "だ")]
    catalogue(two)
    enroll(app, two)
    one = lesson(AID, abilities=("understanding",))
    one["question_templates"]["grammar_cloze"] = [choice("本（　）。", "です", "だ")]   # left in the lesson too
    catalogue(one)
    from kanamemory import learning_questions
    shipped = learning_questions._PRACTICE
    bind(monkeypatch, one, {"grammar_cloze": LEFTOVER})
    templates = practice_templates(one)   # loads: no "cannot create a memory ability"
    assert set(templates) == {"grammar_meaning"} and question_kinds(one) == ["grammar_meaning"]
    # The saved lesson still lists the ability, so its own (reviewed) extension is read as before; the made-up
    # leftover above is not reviewed, so the queue pass below runs on the shipped extension.
    monkeypatch.setattr(learning_questions, "_PRACTICE", shipped)
    restart(app)
    assert {row["ability"] for row in app.learning_store.iter_pending_questions(KINDS)} == {"understanding"}
    counts = app.learning_store.counts(KINDS)
    assert counts["disabled"] == 0 and counts["unavailable_content"] == 0
    assert app.learning_store.current(KINDS)["content"]["id"] == AID


def test_a_read_only_lesson_with_leftover_kinds_behaves_as_read_only(learning_app, catalogue, monkeypatch):
    from kanamemory.learning_questions import practice_templates, question_kinds
    app = learning_app
    item = read_only()
    item["question_templates"] = {"pronunciation_rule": [choice("じ", "ぢ", "ず")]}
    catalogue(item)
    bind(monkeypatch, item, {"pronunciation_rule": LEFTOVER, "pronunciation_application": LEFTOVER})
    assert practice_templates(item) == {} and question_kinds(item) == []
    enroll(app, item)
    assert app.learning_store.get_item(RID)["read_only"] is True
    head = app.learning_store.current(KINDS)
    assert head["content"]["id"] == RID and head["read_only"] is True
    app.continue_learning()
    assert READ_ONLY_LINE in text_of(app.body)
    click(app, READ_ONLY_DONE)
    assert RID in read_lesson_ids(app.learning_store._db)
    restart(app)
    assert app.learning_store.current(KINDS) is None and app.learning_store.counts(KINDS)["disabled"] == 0


def test_a_retired_unit_is_never_due_recent_or_in_a_deck(learning_app, catalogue):
    """J6M meeting 5: the dense-review readers of units leave a retired ability's unit out."""
    from kanamemory.arrangement.sources.consolidation import StoreConsolidation
    from kanamemory.arrangement.sources.due_reviews import DueReviews
    app = learning_app
    two = lesson(AID, abilities=("understanding", "application"))
    two["question_templates"]["grammar_cloze"] = [choice("本（　）。", "です", "だ")]
    catalogue(two)
    enroll(app, two)
    learning = app.learning_store
    # A made-up rated state for both abilities (one review, due in a week).
    due = (app._test_clock[0] + timedelta(days=7)).isoformat()
    with learning._db:
        learning._db.execute("UPDATE learning_unit SET review_count=1,due_at_utc=?,instruction_ready=1 WHERE content_id=?",
                             (due, AID))
    rated = {row["ability"] for row in app.store.queries.active_review_units()}
    assert rated == {"understanding", "application"}
    catalogue(lesson(AID, abilities=("understanding",)))
    assert {row["ability"] for row in app.store.queries.active_review_units()} == {"understanding"}
    assert {row["ability"] for row in app.store.queries.review_unit_queue_rows()} == {"understanding"}
    later = app._test_clock[0] + timedelta(days=60)
    assert DueReviews(app.store).course(later, KINDS) == [AID]   # one entry: the listed ability only
    facts = StoreConsolidation(app.store, learning).facts(app._test_clock[0])
    assert all(fact.ref.ability != "application" for fact in facts)
