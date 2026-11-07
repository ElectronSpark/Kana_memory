"""Shared new dispatch candidate fixtures and assertions; no collected scenarios."""
from copy import deepcopy

from kanamemory.app import KanaMemory
from kanamemory.learning_ui import LEARNING_PREFS
from kanamemory.learning_store import LearningStore
from kanamemory.domain import StudyStore
from support.learning_fixtures import legacy_lesson_snapshot
from support.study_path_runtime_support import source_question


KINDS = ["kana_choice", "romaji_kana", "kana_romaji", "romaji_kana_full", "romaji_handwriting", "kana_handwriting"]


def all_kinds(app, kinds=None):
    app._practice_preferences.update(kind="综合自适应练习", mixed_kinds=kinds or KINDS,
                                     listening_handwriting_opt_in_version=1)
    app.model_state = "ready"
    app.platform.tts_ready = True
    app.store.set_setting(LEARNING_PREFS, {"enabled": ["grammar_meaning"]})


def new_course(app, suffix="one"):
    # A custom course carries its reviewed templates. The old legacy fallback
    # silently gave arbitrary renamed IDs an unrelated location-particle quiz.
    item = legacy_lesson_snapshot("grammar:copula:polite")
    item["id"] = item["content_id"] = "grammar:fair:" + suffix
    item["abilities"] = ["understanding"]
    app.learning_store.enroll(item, now=app._test_clock[0])
    app._queue_learning_questions(app.learning_store.get_item(item["id"]))
    return item


def queued(app, specs):
    questions = [source_question(app, symbol, kind) for symbol, kind in specs]
    for index, q in enumerate(questions):
        q.metadata.update(study_path_entry="fair-test:" + str(index))
    original = deepcopy(questions)
    sid = app.store.start_session(questions, now=app._test_clock[0])
    app.store.save_session_progress(sid, 0, state={})
    assert questions == original  # The public constructor never edits its inputs.
    from support.session_snapshots import assert_fresh_native_snapshot
    saved = assert_fresh_native_snapshot(app.store, sid, original)
    return sid, saved


def reopen(app):
    data_dir = app.store.data_dir
    app.store.close()
    app.store = StudyStore(data_dir, time_zone="UTC")
    app.learning_store = LearningStore(app.store)
    app.page = "home"


def rate_current(app):
    q = app.question
    app.page = "feedback"
    app.answer_correct, app.answer_revealed = True, False
    app.answered_text = q.answer
    app.question_audio_ok = True
    app._answer_submitted_at = None
    app.alert.reset_mock()
    KanaMemory.save_rating(app, 4)
    assert not app.alert.called, app.alert.call_args_list
    return q
