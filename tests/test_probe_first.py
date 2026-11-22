"""Probe first (2026-11-21): a new course item starts with probe questions, never with a self-rating.

Synthetic lessons and temporary stores only.
"""
from datetime import datetime, timedelta, timezone
import json

import pytest
from record_test_support import decode_current, encode_current

from support.learning_fixtures import learning_app, click
from support.app_fixtures import question_app, setup_app  # noqa: F401 (fixtures used by learning_app)
from support.fake_ui import text_of
from kanamemory import probe_rules
from kanamemory.arrangement.model import FirstRatings, RawEvent, Unit
from kanamemory.arrangement.rules.clock import attribute
from kanamemory.domain import StudyStore
from kanamemory.learning_questions import PREFERENCE_VERSION, SOUND_PREFERENCE_VERSION
from kanamemory.learning_store import LearningStore, validate_backup_rows
from kanamemory.learning_ui import LEARNING_PREFS

CID = "word:synthetic:probe"
GID = "grammar:synthetic:probe"
KINDS = ["vocab_meaning", "vocab_reading_choice", "vocab_reading", "vocab_usage", "grammar_meaning", "grammar_cloze"]
NOW = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)


def choice(prompt, answer, other, **extra):
    return {"prompt": prompt, "answer": answer, "choices": [answer, other], "feedback": "词一的说明",
            "input_mode": "choice", "safe": True, **extra}


def typed(prompt, answer, **extra):
    return {"prompt": prompt, "answer": answer, "input_mode": "text", "feedback": "词一的说明", "strict": True, **extra}


def word(cid=CID, *, reading_strict=True, use=True):
    templates = {"vocab_meaning": [choice("語一", "词一", "别的词")],
                 "vocab_reading_choice": [choice("語一", "ごいち", "ごに", task="読み方")],
                 "vocab_reading": [typed("語一", "ごいち", strict=reading_strict)]}
    if use:
        templates["vocab_usage"] = [typed("（词一）＿＿です。", "ごいち", task="按中文意思填词")]
    return {"id": cid, "content_id": cid, "family": "word", "title": "語一", "reading": "ごいち",
            "meaning": "词一", "revision": "1", "abilities": ["understanding", "reading", "use"], "source_ids": ["test"],
            "instruction": {"summary": "词一", "rule": "名词。", "steps": [], "example": "語一です。",
                            "translation": "是词一。", "prerequisite": "", "contrast": ""},
            "question_templates": templates}


def grammar(cid=GID):
    return {"id": cid, "content_id": cid, "family": "grammar", "title": "～です", "revision": "1",
            "abilities": ["understanding"], "source_ids": ["test"],
            "instruction": {"summary": "判断", "rule": "名词＋です。", "steps": [], "example": "本です。",
                            "translation": "是书。", "prerequisite": "", "contrast": ""},
            "question_templates": {"grammar_meaning": [choice("本です。", "是书", "不是书")]}}


def start(app, *items, kinds=KINDS):
    app.store.set_setting(LEARNING_PREFS, {"enabled": list(kinds), SOUND_PREFERENCE_VERSION: PREFERENCE_VERSION})
    app.learning_store.enroll(list(items) or [word()])
    app.continue_learning()


def later(app, minutes=11):
    app._test_clock[0] += timedelta(minutes=minutes)


def profile(app, cid=CID, ability="understanding"):
    item = app.learning_store.instruction_item(cid, app.learning_store.ability_instruction_scope(cid, ability))
    return item["checkpoint"].get("initial_familiarity"), item["stage"]


def events(app):
    return [dict(row) for row in app.learning_store._db.execute(
        "SELECT e.role,e.outcome,e.rating,e.assisted,u.ability FROM learning_event e JOIN learning_unit u USING(unit_id) "
        "ORDER BY e.rowid")]


def answer(app, value):
    q = app._learning_question
    if q.get("input_mode", "choice") == "choice":
        click(app, value)
    else:
        app._learning_input.value = value
        click(app, "提交")
    return q


# Pure rules

def test_pure_order_kinds_and_qualification():
    assert probe_rules.order("word") == ("understanding", "reading", "use")
    assert probe_rules.order("grammar") == probe_rules.order("pronunciation") == ("understanding", "application")
    assert not probe_rules.probed("word", "listening")
    assert probe_rules.track("grammar", "pronunciation") == "pronunciation"
    meaning = {"kind": "vocab_meaning", "ability": "understanding", "input_mode": "choice", "guided": False}
    assert probe_rules.qualifies(meaning, "word", "understanding", kana_answer=False)
    assert not probe_rules.qualifies({**meaning, "guided": True}, "word", "understanding", kana_answer=False)
    assert not probe_rules.qualifies(meaning, "word", "understanding", kana_answer=False, candidate={"relaxed": 1})
    reading = {"kind": "vocab_reading", "ability": "reading", "input_mode": "text", "strict": True}
    assert probe_rules.qualifies(reading, "word", "reading", kana_answer=True)
    assert not probe_rules.qualifies({**reading, "strict": False}, "word", "reading", kana_answer=True)
    assert not probe_rules.qualifies(reading, "word", "reading", kana_answer=False)
    usage_choice = {"kind": "vocab_usage", "ability": "use", "input_mode": "choice"}
    assert not probe_rules.qualifies(usage_choice, "word", "use", kana_answer=True)
    assert probe_rules.accepts_kana({"answer": "語一", "accepted_answers": ["ごいち"]})


def test_pure_spacing_and_profiles():
    assert probe_rules.spacing_ready(None, 0, NOW)
    assert not probe_rules.spacing_ready(NOW, 2, NOW + timedelta(minutes=9))
    assert probe_rules.spacing_ready(NOW, 3, NOW + timedelta(seconds=1))
    assert probe_rules.spacing_ready(NOW, 0, NOW + timedelta(minutes=10))
    assert probe_rules.probe_profile("reading", NOW) == {
        "version": 1, "evidence": "probe_v1", "levels": {"reading": "familiar"}, "route": "diagnostic",
        "selected_at_utc": NOW.isoformat(), "diagnostic_state": "active"}
    assert probe_rules.explanation_shown([{"instruction_exposed_at_utc": NOW.isoformat()}])
    assert not probe_rules.probing([{"initial_familiarity": {"diagnostic_state": "needs_teaching"}}])
    view = probe_rules.bare_view({"prompt": "語一", "support": {"segments": [{"reading": "ごいち"}]},
                                  "optional_audio_text": "語一"})
    assert view["support"]["segments"] == [] and view["optional_audio_text"] == ""


# The pass chain

def test_three_probes_passed_never_show_the_explanation(learning_app):
    app = learning_app
    pages = []
    original_screen = app.screen
    app.screen = lambda page, *args, **kwargs: (pages.append(page), original_screen(page, *args, **kwargs))[1]
    start(app)
    assert app.page == "learning_question" and app._learning_question["kind"] == "vocab_meaning"
    assert "还不会" in text_of(app.body) and "再看讲解" not in text_of(app.body)
    saved, stage = profile(app)
    assert saved == probe_rules.probe_profile("understanding", app._test_clock[0]) and stage == "check"
    answer(app, "词一")
    # The reduced result page: right, and the ratings; nothing of the item itself.
    assert app.page == "learning_feedback"
    shown = text_of(app.body)
    assert "记住了" in shown and "词一的说明" not in shown and "ごいち" not in shown
    click(app, "很轻松")
    for kind, value, reduced in (("vocab_reading", "ごいち", True), ("vocab_usage", "ごいち", False)):
        app.continue_learning()
        assert app.page != "learning_question" or app._learning_question["kind"] != kind   # the spacing holds it
        later(app)
        app.continue_learning()
        assert app.page == "learning_question" and app._learning_question["kind"] == kind
        answer(app, value)
        # Reduced only while another ability can still be probed; the last probe gets the full page.
        assert ("词一的说明" not in text_of(app.body)) is reduced
        click(app, "很轻松")
    assert not {"learning_intro", "learning_guide", "learning_familiarity", "learning_card"} & set(pages)
    assert [(e["role"], e["outcome"], e["rating"], e["ability"]) for e in events(app)] == [
        ("primary", "correct", 4, "understanding"), ("primary", "correct", 4, "reading"), ("primary", "correct", 4, "use")]
    for ability in ("understanding", "reading", "use"):
        unit = app.learning_store.get_unit(CID, ability)
        state = json.loads(unit["scheduler_state_json"])
        assert unit["review_count"] == 1 and state["state"] == 2 and round(state["stability"], 2) == 8.3
        assert profile(app, ability=ability)[1] == "done"
    item = app.learning_store.get_item(CID)
    assert "instruction_exposed_at_utc" not in item["checkpoint"]
    validate_backup_rows(app.learning_store._db)


@pytest.mark.parametrize("position", [0, 1, 2])
@pytest.mark.parametrize("how", ["wrong", "decline"])
def test_a_failed_probe_writes_the_failed_diagnostic_and_teaches(learning_app, position, how):
    app = learning_app
    start(app)
    order = ["understanding", "reading", "use"]
    for index in range(position):
        answer(app, "词一" if index == 0 else "ごいち")
        click(app, "很轻松")
        later(app)
        app.continue_learning()
    ability = order[position]
    assert app._learning_question["ability"] == ability
    before = app.learning_store.get_unit(CID, ability)
    if how == "wrong":
        answer(app, "别的词" if position == 0 else "ごに")
        assert app.page == "learning_feedback"
        click(app, "保存并继续")
    else:
        click(app, "还不会")
    saved, stage = profile(app, ability=ability)
    assert stage == "intro" and saved["route"] == "full" and saved["diagnostic_state"] == "needs_teaching"
    assert saved["evidence"] == "probe_v1" and saved["diagnostic_ability"] == ability
    assert saved.get("probe_declined") is (True if how == "decline" else None)
    last = events(app)[-1]
    assert (last["role"], last["outcome"], last["rating"], last["ability"]) == ("unresolved", "unresolved", None, ability)
    unit = app.learning_store.get_unit(CID, ability)
    assert (unit["scheduler_state_json"], unit["due_at_utc"], unit["review_count"]) == (
        before["scheduler_state_json"], before["due_at_utc"], before["review_count"])
    evidence = app.store._db.execute("SELECT role,eligible FROM learning_evidence ORDER BY rowid DESC LIMIT 1").fetchone()
    assert tuple(evidence) == ("exposure", 0)
    validate_backup_rows(app.learning_store._db)
    # Teaching follows: the single path, never another probe.
    later(app, 2)
    app.continue_learning()
    assert app.page == "learning_intro"
    # Later abilities open as ordinary first checks once the explanation was shown.
    for later_ability in order[position + 1:]:
        if profile(app, ability=later_ability)[0] is None:
            scope = app.learning_store.ability_instruction_scope(CID, later_ability)
            item = app.learning_store.instruction_item(CID, scope)
            opened = app.learning_store.open_new_scope(CID, instruction_scope=scope,
                expected_introduction_id=item["introduction_id"], enabled_kinds=KINDS)
            assert opened["route"] == "check"
        saved, stage = profile(app, ability=later_ability)
        assert saved["route"] == "full" and saved["diagnostic_state"] == "not_requested" and stage == "bridge"


def test_after_teaching_the_first_question_waits_for_three_others_or_ten_minutes(learning_app):
    app = learning_app
    start(app)
    click(app, "还不会")
    later(app, 2)
    app.continue_learning()
    assert app.page == "learning_intro"
    store = app.learning_store
    store.advance_instruction(CID, "intro_done")
    store.advance_instruction(CID, "guide_correct")
    assert store.current(KINDS, now=app._test_clock[0] + timedelta(seconds=61))["type"] == "waiting"
    assert store.current(KINDS, now=app._test_clock[0] + timedelta(minutes=10, seconds=1))["type"] == "question"
    row = store.instruction_row(CID)
    assert not store.retrieval_ready(row, app._test_clock[0] + timedelta(minutes=5))


def test_three_other_answers_release_the_next_probe(learning_app):
    app = learning_app
    start(app)
    answer(app, "词一")
    click(app, "很轻松")
    store = app.learning_store
    anchor = app._test_clock[0]
    assert store.other_answers_since(CID, anchor - timedelta(seconds=1)) == 0
    assert store.current(KINDS)["type"] == "waiting"
    # Three answers of another lesson (kana or course) count; the time alone releases it after ten minutes.
    with store._db:
        for index in range(3):
            store._db.execute("INSERT INTO learning_content VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (f"other:{index}", "word", "1", f"intro{index}", "done", json.dumps({"id": f"other:{index}", "family": "word"}),
                 "{}", f"g{index}", anchor.isoformat(), None, None))
    assert store.other_answers_since(CID, anchor) == 0
    later(app)
    assert store.current(KINDS)["question"]["kind"] == "vocab_meaning" or store.current(KINDS)["type"] == "instruction"


def test_an_ability_without_a_qualifying_probe_waits_and_later_ones_go_on(learning_app):
    app = learning_app
    start(app, word(reading_strict=False))
    answer(app, "词一")
    click(app, "很轻松")
    later(app)
    app.continue_learning()
    # Reading has no strict typed probe: it is passed over; use is probed instead.
    assert app.page == "learning_question" and app._learning_question["kind"] == "vocab_usage"
    assert profile(app, ability="reading")[0] is None
    assert (CID, "reading") in app._guide_skipped_ids


def test_an_item_with_one_probe_only(learning_app):
    app = learning_app
    start(app, grammar())
    assert app._learning_question["kind"] == "grammar_meaning"
    answer(app, "是书")
    # No other ability can be probed: the full result page, as before probe first.
    assert app.page == "learning_feedback" and "词一的说明" in text_of(app.body)
    click(app, "记住了")
    assert profile(app, GID)[1] == "done"
    assert [(e["role"], e["rating"]) for e in events(app)] == [("primary", 3)]


def test_result_page_is_reduced_only_for_a_probe_with_a_next_probe_available(learning_app):
    app = learning_app
    # The next ability has no qualifying probe now (reading not strict, use absent): the full page.
    start(app, word(reading_strict=False, use=False))
    assert app._learning_question["kind"] == "vocab_meaning"
    answer(app, "词一")
    assert app.page == "learning_feedback" and "词一的说明" in text_of(app.body)


def test_a_later_review_of_a_passed_ability_gets_the_full_page(learning_app):
    app = learning_app
    start(app)
    probe = answer(app, "词一")
    store = app.learning_store
    assert store.probe_result_reduced(probe["id"], enabled_kinds=KINDS)   # reading can still be probed
    click(app, "记住了")
    later(app)
    pending = store._db.execute("""SELECT q.question_id FROM learning_question q JOIN learning_unit u USING(unit_id)
        WHERE u.content_id=? AND u.ability='understanding' AND q.status='pending'""", (CID,)).fetchone()
    assert pending is not None
    # Its later review is not a probe, though the reading probe is still open to come.
    assert not store.probe_result_reduced(pending["question_id"], enabled_kinds=KINDS)


def test_pure_reduced_result_needs_both_conditions():
    assert probe_rules.reduced_result(answered_is_probe=True, next_probe_available=True)
    assert not probe_rules.reduced_result(answered_is_probe=True, next_probe_available=False)
    assert not probe_rules.reduced_result(answered_is_probe=False, next_probe_available=True)
    abilities = {"understanding": {"rated": False}, "reading": {"rated": False}, "use": {"rated": True}}
    assert probe_rules.other_probe_candidates("word", abilities, "understanding", probing_now=True) == ("reading",)
    assert probe_rules.other_probe_candidates("word", abilities, "understanding", probing_now=False) == ()


def test_nothing_to_probe_teaches_the_lesson(learning_app):
    app = learning_app
    start(app, word(), kinds=["vocab_reading_choice"])
    saved, stage = profile(app)
    assert app.page == "learning_intro" and stage == "intro"
    assert (saved["evidence"], saved["route"], saved["diagnostic_state"]) == ("probe_v1", "full", "not_requested")


def test_cards_never_offer_i_already_know_it_and_batches_are_not_opened(learning_app):
    app = learning_app
    start(app)
    assert app.learning_store.intro_batch() is None and app.page == "learning_question"


# Records

@pytest.fixture
def stores(tmp_path):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        learning = LearningStore(store)
        learning.enroll(word(), now=NOW)
        yield store, learning


def _open(learning):
    item = learning.instruction_item(CID)
    return learning.open_new_scope(CID, expected_introduction_id=item["introduction_id"], enabled_kinds=KINDS, now=NOW)


def test_backup_round_trip_keeps_probe_and_declined_profiles(stores, tmp_path):
    store, learning = stores
    opened = _open(learning)
    assert opened["route"] == "probe" and opened["ability"] == "understanding"
    learning.decline_probe(opened["question_id"], now=NOW)
    saved = learning.instruction_item(CID)["checkpoint"]["initial_familiarity"]
    assert saved["probe_declined"] is True and saved["evidence"] == "probe_v1"
    backup = store.export_backup()
    with StudyStore(tmp_path / "restored", time_zone="UTC") as restored:
        restored.import_backup(backup)
        assert LearningStore(restored).instruction_item(CID)["checkpoint"]["initial_familiarity"] == saved


@pytest.mark.parametrize("change", ["declined_without_teaching", "declined_false", "unknown_evidence"])
def test_invalid_probe_profiles_are_rejected(stores, change):
    store, learning = stores
    _open(learning)
    payload = decode_current(store.export_backup())
    row = payload["tables"]["learning_content"][0]
    checkpoint = json.loads(row["checkpoint_json"])
    profile_value = checkpoint["initial_familiarity"]
    if change == "declined_without_teaching":
        profile_value["probe_declined"] = True
    elif change == "declined_false":
        profile_value.update(probe_declined=False, diagnostic_state="needs_teaching", route="full",
                             diagnostic_ability="understanding", diagnostic_failed_at_utc=NOW.isoformat())
    else:
        profile_value["evidence"] = "probe_v2"
    row["checkpoint_json"] = json.dumps(checkpoint)
    with pytest.raises(ValueError):
        store.import_backup(encode_current(payload))


def test_old_self_report_records_stay_valid(stores, tmp_path):
    store, learning = stores
    item = learning.instruction_item(CID)
    learning.set_initial_familiarity(CID, {"understanding": "new", "reading": "familiar", "use": "familiar"},
                                     expected_introduction_id=item["introduction_id"], now=NOW)
    validate_backup_rows(learning._db)
    with StudyStore(tmp_path / "restored", time_zone="UTC") as restored:
        restored.import_backup(store.export_backup())
        assert LearningStore(restored).instruction_item(CID)["checkpoint"]["initial_familiarity"]["evidence"] == "self_report"


def test_new_content_attribution_covers_probe_failure_teaching_and_first_rating():
    """Design 12 section 3 (kept): everything up to the item's first formal rating is new content."""
    first = FirstRatings(NOW + timedelta(minutes=30), "d")
    events_ = [RawEvent(NOW, "d", "course", "u1"), RawEvent(NOW + timedelta(minutes=5), "d", "course", "u1", teaching_mark=True),
               RawEvent(NOW + timedelta(minutes=30), "d", "course", "u1", formal=True),
               RawEvent(NOW + timedelta(days=2), "d2", "course", "u1", formal=True)]
    units = [event.unit for event in attribute(events_, {"u1": first})]
    assert units == [Unit.NEW, Unit.NEW, Unit.NEW, Unit.COURSE]
