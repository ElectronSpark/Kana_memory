"""Pending questions of a template the installed content retired continue as new questions.

Real disposable stores for a MADE-UP learner and synthetic lessons (no course content): the
start-up pass (retired_template_pass) replaces through learning_replacement's own transaction.
"""
import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

from kanamemory import diagnostic_events
from kanamemory import learning_questions as questions
from kanamemory import retired_template_pass as rtp
from kanamemory.domain import StudyStore
from kanamemory.learning_replacement import question_key, validate_database
from kanamemory.learning_store import LearningStore
from kanamemory.material_review import item_fingerprint
from kanamemory.progress_backup import export_backup, import_backup, inspect_backup
from support.learning_practice_policy_support import CID, lesson
from support.fake_ui import text_of
from support.app_fixtures import question_app, setup_app  # noqa: F401
from support.learning_fixtures import learning_app  # noqa: F401
from support.study_path_runtime_support import path_app  # noqa: F401
from support.learning_practice_store_support import START

OTHER = CID + "-second"
KINDS = ["grammar_cloze", "grammar_transform"]
LATER = START + timedelta(days=3)


def lessons():
    return lesson(), dict(lesson(), id=OTHER)


def rows(store, table):
    return [tuple(row) for row in store._db.execute(f"SELECT * FROM {table} ORDER BY rowid")]


def scheduling(store):
    """FSRS state, due times and review counts (learning_unit) plus history (learning_event)."""
    return rows(store, "learning_unit"), rows(store, "learning_event")


@pytest.fixture
def record(tmp_path, monkeypatch):
    """A made-up learner with two taught lessons, each with one pending cloze question (variant 0)."""
    monkeypatch.setattr(questions, "_PRACTICE", dict(questions._PRACTICE, template_retirements={}))
    with StudyStore(tmp_path / "made-up-learner", time_zone="UTC") as store:
        learning = LearningStore(store)
        store.set_setting("learning_preferences_v1", {"enabled": KINDS})
        for item in lessons():
            learning.enroll(item, now=START)
            learning.advance_instruction(item["id"], "intro_done", now=START)
            learning.advance_instruction(item["id"], "guide_correct", now=START)
            q = questions.build_learning_question(item, "grammar_cloze", variant=0)
            q["id"] = "old:" + item["id"]
            learning.append_questions(q, now=START)
        yield SimpleNamespace(store=store, learning=learning)


def retire(monkeypatch, variants=(0,), kind="grammar_cloze", ids=(CID, OTHER)):
    """The installed content retires these raw templates of each lesson (template_retirements)."""
    table = {}
    for item in lessons():
        if item["id"] in ids:
            hashes = [questions.template_fingerprint(item["question_templates"][kind][v]) for v in variants]
            table[item["id"]] = {item_fingerprint(item): {kind: hashes}}
    monkeypatch.setattr(questions, "_PRACTICE", dict(questions._PRACTICE, template_retirements=table))


def run(record, key="content-1", now=LATER):
    return rtp.RetiredTemplatePass(record.learning, key).run(now=now)


def test_a_retired_pending_question_continues_as_a_new_question_of_the_same_lesson_and_ability(
        record, monkeypatch):
    store, learning = record.store, record.learning
    old_id = "old:" + CID
    old_row = dict(store._db.execute("SELECT * FROM learning_question WHERE question_id=?", (old_id,)).fetchone())
    before = scheduling(store)
    retire(monkeypatch)
    assert run(record) == 2
    receipt = store.get_setting(question_key(old_id))
    assert receipt and receipt["before"] == old_row  # the receipt keeps the old row as it was
    old = learning.get_question(old_id)
    assert old["status"] == "superseded"
    new = learning.get_question(old["replacement_question_id"])
    assert new["status"] == "pending" and new["target_id"] == CID and new["ability"] == old["ability"]
    assert new["replacement_of"] == old_id and new["variant"] != 0
    assert not questions.template_retired(lesson(), new)  # a current template
    assert scheduling(store) == before  # no FSRS, due, review count or history write
    assert validate_database(store._db) == {old_id, "old:" + OTHER}
    replacements = {learning.get_question("old:" + cid)["replacement_question_id"] for cid in (CID, OTHER)}
    current = learning.current(KINDS, now=LATER + timedelta(days=30))
    assert current["type"] == "question" and current["question"]["id"] in replacements


def test_a_draft_stays_with_the_old_question_and_aids_carry_over(record, monkeypatch):
    learning = record.learning
    old_id = "old:" + CID
    learning.save_draft(old_id, {"answer": "途中の答え", "hint_used": True})
    retire(monkeypatch, ids=(CID,))
    assert run(record) == 1
    old = learning.get_question(old_id)
    assert old["draft"] == {"answer": "途中の答え", "hint_used": True}
    assert learning.get_question(old["replacement_question_id"])["draft"] == {"hint_used": True}
    # The other lesson's template was not retired: its question is untouched.
    assert learning.get_question("old:" + OTHER)["status"] == "pending"
    assert record.store.get_setting(question_key("old:" + OTHER)) is None


def test_answered_and_settled_questions_are_untouched(record, monkeypatch):
    store, learning = record.store, record.learning
    first, second = "old:" + CID, "old:" + OTHER
    learning.submit(first, "依頼", True, now=START + timedelta(seconds=61))  # answered, not settled
    learning.submit(second, "依頼", True, now=START + timedelta(seconds=61))
    learning.finalize(second, 3, now=START + timedelta(seconds=61))  # settled
    before = {table: rows(store, table) for table in ("learning_question", "learning_unit", "learning_event")}
    retire(monkeypatch)
    assert run(record) == 0
    assert {table: rows(store, table) for table in before} == before
    assert store.get_setting(question_key(first)) is None and store.get_setting(question_key(second)) is None


def test_without_an_eligible_candidate_the_question_stays_pending(record, monkeypatch):
    store = record.store
    store.set_setting("learning_preferences_v1", {"enabled": ["grammar_cloze"]})  # transform switched off
    before = rows(store, "learning_question")
    retire(monkeypatch, variants=(0, 1, 2))  # every cloze template retired: nothing current is left
    assert run(record) == 0
    assert rows(store, "learning_question") == before
    assert store.get_setting(rtp.SETTING) == {"content_key": "content-1"}  # every candidate was tried


def test_a_second_pass_changes_nothing(record, monkeypatch):
    store = record.store
    retire(monkeypatch)
    assert run(record) == 2
    before = {table: rows(store, table) for table in ("learning_question", "learning_unit", "learning_event",
                                                      "app_setting")}
    assert run(record, key="content-2") == 0  # another content: the pass runs, and finds nothing to do
    after = {table: rows(store, table) for table in before}
    assert {k: v for k, v in after.items() if k != "app_setting"} == {
        k: v for k, v in before.items() if k != "app_setting"}
    assert store.get_setting(rtp.SETTING) == {"content_key": "content-2"}


def test_the_pass_does_not_run_while_the_content_is_unchanged(record, monkeypatch):
    store, learning = record.store, record.learning
    retire(monkeypatch, ids=(CID,))
    assert run(record) == 1
    retire(monkeypatch)  # same content key: nothing is read or written
    work = rtp.RetiredTemplatePass(learning, "content-1")
    monkeypatch.setattr(work, "candidates", lambda: pytest.fail("scanned although the content is unchanged"))
    before = rows(store, "learning_question")
    assert work.run(now=LATER) is None
    assert rows(store, "learning_question") == before
    assert run(record, key="content-2") == 1  # the content changed: the other lesson goes on


def test_an_interruption_between_two_replacements_leaves_a_valid_record_and_the_next_pass_finishes(
        record, monkeypatch):
    store, learning = record.store, record.learning
    retire(monkeypatch)
    work = rtp.RetiredTemplatePass(learning, "content-1")
    first, second = work.candidates()
    assert work.replace(first, now=LATER) is not None  # ... and the app stops here
    assert store.get_setting(rtp.SETTING) is None  # not complete: the next start goes on
    data = export_backup(store)
    inspect_backup(store, data)
    assert validate_database(store._db) == {first}
    assert learning.get_question(second)["status"] == "pending"
    assert run(record) == 1
    assert learning.get_question(second)["status"] == "superseded"
    assert validate_database(store._db) == {first, second}


def test_a_failing_transaction_rolls_back_is_recorded_and_the_pass_stays_incomplete(record, monkeypatch):
    store, learning = record.store, record.learning
    retire(monkeypatch, ids=(CID,))
    before = rows(store, "learning_question")

    def broken(*args, **kwargs):
        raise RuntimeError("injected")
    monkeypatch.setattr(learning, "_append_questions_in_transaction", broken)
    counted = lambda: {(e["operation"], e["stage"]): e["count"]
                       for e in diagnostic_events.error_events()}.get(("retired_template_pass", "commit"), 0)
    already = counted()
    assert run(record) == 0
    assert counted() == already + 1
    assert rows(store, "learning_question") == before and store.get_setting(question_key("old:" + CID)) is None
    assert store.get_setting(rtp.SETTING) is None
    monkeypatch.undo()
    retire(monkeypatch, ids=(CID,))
    assert run(record) == 1  # the next start goes on


def test_a_backup_made_after_the_pass_validates_on_export_and_import(record, monkeypatch):
    store, learning = record.store, record.learning
    retire(monkeypatch)
    assert run(record) == 2
    data = export_backup(store)
    inspect_backup(store, data)
    import_backup(store, data)
    assert len(validate_database(store._db)) == 2
    inspect_backup(store, export_backup(store))
    pending = [row[0] for row in store._db.execute("SELECT question_id FROM learning_question WHERE status='pending'")]
    assert sorted(pending) == sorted(learning.get_question("old:" + cid)["replacement_question_id"]
                                     for cid in (CID, OTHER))


# --- the start-up task: one replacement per turn on the UI connection, waiting and stopping --------

def _app(record, **state):
    return SimpleNamespace(**{"store": record.store, "learning_store": record.learning, "_backup_closing": False,
                              "_backup_busy": False, "_practice_context": False, **state})


def test_the_start_up_task_waits_for_a_focused_page_and_reports_its_count(record, monkeypatch):
    retire(monkeypatch)
    app = _app(record, _practice_context=True)
    pauses = []

    async def pause(run):
        pauses.append(run)
        app._practice_context = False  # the learner left the practice page
    monkeypatch.setattr(rtp.PassRun, "pause", pause)
    work = rtp.RetiredTemplatePass(record.learning, "content-1")
    run = rtp.PassRun()
    assert asyncio.run(rtp._replace_all(app, work, work.candidates(), run)) is True
    assert len(pauses) == 1 and rtp.replaced_count(app) == 2 and run.done
    assert record.store.get_setting(rtp.SETTING) == {"content_key": "content-1"}


def test_the_start_up_task_stops_on_close_without_the_completion_mark(record, monkeypatch):
    retire(monkeypatch)
    app = _app(record)

    async def sleep(seconds):
        app._backup_closing = True  # the app closes after the first replacement
    monkeypatch.setattr(rtp.asyncio, "sleep", sleep)
    work = rtp.RetiredTemplatePass(record.learning, "content-1")
    run = rtp.PassRun()
    assert asyncio.run(rtp._replace_all(app, work, work.candidates(), run)) is False
    assert rtp.replaced_count(app) == 1 and record.store.get_setting(rtp.SETTING) is None and run.done
    validate_database(record.store._db)


def test_only_the_real_app_starts_the_pass():
    assert asyncio.run(rtp.start(SimpleNamespace())) is None


def _worker(monkeypatch, gate=None):
    """practice_worker.run_in_background on the same made-up record; ``gate`` holds the scan back."""
    from kanamemory import practice_worker
    jobs = []

    async def in_background(repository, store, work, options=None):
        jobs.append(work)
        if gate is not None:
            await gate.wait()
        return work(None, store, **(options or {}))  # the job reads only
    monkeypatch.setattr(practice_worker, "run_in_background", in_background)
    return jobs


def test_start_scans_in_the_worker_job_then_replaces_and_skips_an_unchanged_content(record, monkeypatch):
    retire(monkeypatch)
    monkeypatch.setattr(rtp, "content_key", lambda: "content-1")
    jobs = _worker(monkeypatch)

    async def started():
        app = _app(record, _background_assembly=True, repo=None, loop=asyncio.get_running_loop())
        task = await rtp.start(app)
        return app, (await task if task is not None else None)
    app, finished = asyncio.run(started())
    assert jobs == [rtp._scan_in_worker] and finished is True and rtp.replaced_count(app) == 2
    assert rtp.running(app) is None
    before = rows(record.store, "learning_question")
    app, finished = asyncio.run(started())  # the next start with the same content: no worker at all
    assert finished is None and rtp.replaced_count(app) == 0 and jobs == [rtp._scan_in_worker]
    assert rtp.running(app) is None and rows(record.store, "learning_question") == before


# --- the round start waits for an unfinished pass ----------------------------------------------------

class _Screen:
    """The few app members the round start's wait page uses."""

    def __init__(self, app):
        self.app, self.shown = app, []
        app.page, app._page_token, app.leave_learning, app.continue_study_path = "home", 1, None, None
        app.screen = self

    def __call__(self, name, title, *children, **kwargs):
        self.app.page = name
        self.app._page_token += 1
        self.shown.append((name, " ".join(_texts(child) for child in children)))


def _texts(widget):
    """Text of a real or fake widget tree."""
    own = getattr(widget, "text", "")
    return " ".join([str(own)] + [_texts(child) for child in getattr(widget, "children", ())])


def _collect_round(record):
    """Collect a main round as the first dispatch does (practice_round's frozen course order)."""
    from kanamemory import practice_round
    practice_round.enable(record.store)
    practice_round.begin(record.store)
    record.learning.current(KINDS, now=LATER + timedelta(days=30))
    return set(record.store._practice_run_plan.course_order)


def _round_start(app, record, collected):
    def resume():
        collected.update(_collect_round(record))
        app.screen("learning_question", "学习")
    return rtp.round_must_wait(app, resume)


def test_a_round_start_replaces_first_and_collects_only_the_new_questions(record, monkeypatch):
    retire(monkeypatch)
    monkeypatch.setattr(rtp, "content_key", lambda: "content-1")
    gate = None

    async def scenario():
        nonlocal gate
        gate = asyncio.Event()
        _worker(monkeypatch, gate)
        app = _app(record, _background_assembly=True, repo=None, loop=asyncio.get_running_loop(),
                   _practice_context=True)  # a focused page would otherwise pause the pass
        screen = _Screen(app)
        starting = asyncio.ensure_future(rtp.start(app))
        await asyncio.sleep(0)
        collected = set()
        assert _round_start(app, record, collected) is True  # the scan has not finished: it waits too
        assert screen.shown[-1][0] == rtp.WAIT_PAGE and rtp.WAIT_TEXT in screen.shown[-1][1]
        assert app.page == rtp.WAIT_PAGE and not collected
        gate.set()
        task = await starting
        await task
        for _ in range(5):
            await asyncio.sleep(0)
        return app, screen, collected
    app, screen, collected = asyncio.run(scenario())
    news = {record.learning.get_question("old:" + cid)["replacement_question_id"] for cid in (CID, OTHER)}
    assert collected == news  # the round holds only the new questions
    assert app.page == "learning_question"  # the status line went with the wait page
    assert record.store.get_setting(rtp.SETTING) == {"content_key": "content-1"}


def test_with_the_mark_set_the_round_start_does_not_touch_the_pass(record, monkeypatch):
    retire(monkeypatch)
    monkeypatch.setattr(rtp, "content_key", lambda: "content-1")
    record.store.set_setting(rtp.SETTING, {"content_key": "content-1"})
    jobs = _worker(monkeypatch)

    async def scenario():
        app = _app(record, _background_assembly=True, repo=None, loop=asyncio.get_running_loop())
        screen = _Screen(app)
        assert await rtp.start(app) is None
        return app, screen, rtp.round_must_wait(app, lambda: pytest.fail("no wait, no resume"))
    app, screen, waited = asyncio.run(scenario())
    assert waited is False and jobs == [] and screen.shown == [] and app.page == "home"
    assert _collect_round(record) == {"old:" + CID, "old:" + OTHER}  # unchanged: served as stored


def test_a_failure_in_the_middle_still_lets_the_round_start(record, monkeypatch):
    retire(monkeypatch)
    work = rtp.RetiredTemplatePass(record.learning, "content-1")
    first, second = work.candidates()
    original = work.replace

    def replace(question_id, **kwargs):
        if question_id == first:
            raise RuntimeError("injected")
        return original(question_id, **kwargs)
    monkeypatch.setattr(work, "replace", replace)

    async def scenario():
        app = _app(record, loop=asyncio.get_running_loop())
        screen = _Screen(app)
        run = rtp.PassRun()
        app._retired_template_run = run
        collected = set()
        assert _round_start(app, record, collected) is True
        assert await rtp._replace_all(app, work, [first, second], run) is False
        for _ in range(5):
            await asyncio.sleep(0)
        return app, collected
    app, collected = asyncio.run(scenario())
    new = record.learning.get_question(second)["replacement_question_id"]
    assert collected == {first, new}  # the failed one in its old form, never a dead end
    assert app.page == "learning_question" and record.store.get_setting(rtp.SETTING) is None


def test_leaving_the_wait_page_does_not_start_the_round_later(record, monkeypatch):
    retire(monkeypatch)

    async def scenario():
        app = _app(record, loop=asyncio.get_running_loop())
        _Screen(app)
        run = rtp.PassRun()
        app._retired_template_run = run
        assert rtp.round_must_wait(app, lambda: pytest.fail("resumed after the learner left")) is True
        app.screen("home", "今日")  # 回到今日
        run.finish()
        for _ in range(3):
            await asyncio.sleep(0)
        return app
    assert asyncio.run(scenario()).page == "home"


def test_continue_learning_waits_for_the_pass_before_the_round_is_collected(learning_app):
    from kanamemory import practice_round
    from unittest.mock import Mock
    app = learning_app
    app.loop.call_later = Mock()
    app.loop.call_soon = Mock()
    run = rtp.PassRun()
    app._retired_template_run = run
    app.continue_learning()
    assert app.page == rtp.WAIT_PAGE and rtp.WAIT_TEXT in text_of(app.body)
    assert not practice_round.sealed(app.store)
    run.finish()
    callback, *args = app.loop.call_soon.call_args.args
    callback(*args)  # the resume: the round starts as before
    assert app.page != rtp.WAIT_PAGE and rtp.WAIT_TEXT not in text_of(app.body)


def test_continue_study_path_waits_for_the_pass_before_the_round_is_collected(path_app):
    from kanamemory import practice_round
    from unittest.mock import Mock
    app = path_app
    app.loop.call_soon = Mock()
    run = rtp.PassRun()
    app._retired_template_run = run
    app.continue_study_path()
    assert app.page == rtp.WAIT_PAGE and rtp.WAIT_TEXT in text_of(app.body)
    assert not practice_round.sealed(app.store)
    run.finish()
    callback, *args = app.loop.call_soon.call_args.args
    callback(*args)  # the resume: 继续学习 goes on as before
    assert app.page != rtp.WAIT_PAGE and rtp.WAIT_TEXT not in text_of(app.body)
