"""Pending questions whose practice template the installed content retired continue as new questions.

After the content changed (another content identity than at the last completed pass: the core
teaching pack, the loaded libraries, accepted overrides and the app build, route_mix.sources.
content_identity, so also the first start of a new version), every unanswered pending question
whose own raw template the installed content retired for its exact lesson
(learning_questions.template_retired, the pack's template_retirements) is replaced by the existing
replacement machinery (learning_replacement.LearningReplacementStore.prepare_question with
retired_template=True, then commit), without a learner's decision. Everything else is that
machinery's own: the candidate (same lesson and ability, enabled kinds, prerequisites, input
availability), the receipt, the old row leaving the queue as superseded, the time floor, drafts
staying with the old question, aids carried over, and no write to FSRS, due times, review counts
or history. What it refuses (a submitted answer, a settled result, a mastery-check reservation or
hand-over, no eligible candidate) stays pending as before. Guides are left as they are (the guide
path restarts a learner's open guide group; that stays the learner's decision).

Work: the content check is one setting read; the scan is read only and runs on a worker
connection (practice_worker.run_in_background); the replacements run on the UI connection, one
transaction each, one per event-loop turn, and wait while a practice round is sealed (its frozen
order would not serve a new question), a focused page is open, a backup runs or a transaction is
open. A round about to be collected waits for an unfinished pass (round_must_wait: the first
继续学习 of a sitting and the re-arrangement after a break): the round start shows WAIT_TEXT, the
pass replaces the rest at once (no longer pausing for a focused page) and the round is collected
after it is done, failed or stopped, with what is left in its old form. A close, a crash or a replaced
record stops it with a consistent record; the completion mark (SETTING, the content key) is
written only after every candidate was tried, so the next start goes on, and a start with the
same content skips the pass. A second pass changes nothing (replaced rows are no longer pending,
and new questions never use a retired template).
"""
from __future__ import annotations

import asyncio

from .diagnostic_events import record_error

SETTING = "retired_template_pass_v1"
OPERATION = "retired_template_pass"  # diagnostic_events: "read" (the scan), "commit" (a replacement)
WAIT_SECONDS = 5.0
_COUNT = "_retired_templates_replaced"
_RUN = "_retired_template_run"
# The round start waits for an unfinished pass on its usual wait page, with this status line.
WAIT_PAGE = "content_update_wait"
WAIT_TEXT = "正在按新题库更新待做的题…"


def content_key():
    """A digest of the content this process serves and the app build; None when it cannot be told."""
    from .material_workflow import digest
    from .route_mix.sources import content_identity
    identity = content_identity()
    return None if identity is None else digest(identity)


class RetiredTemplatePass:
    """One pass over one learner record (a LearningStore) for one content key."""

    def __init__(self, learning, key):
        self.learning, self.store, self.key = learning, learning.store, key

    def due(self):
        """Not when the last completed pass was for this same content (an unknown key: always)."""
        return self.key is None or self.store.get_setting(SETTING, None) != {"content_key": self.key}

    def candidates(self):
        """Read only: the pending questions to try, soonest answerable first."""
        from .learning_replacement import LearningReplacementStore
        return LearningReplacementStore(self.learning).retired_template_ids()

    def replace(self, question_id, *, now=None):
        """One replacement through the machinery's own transaction: its receipt, or None when the
        question stays pending (refused by the machinery's rules, or already replaced). Any other
        error is raised (the caller records it and does not mark the pass complete)."""
        from .learning_replacement import LearningReplacementStore
        service = LearningReplacementStore(self.learning)
        try:
            prepared = service.prepare_question(question_id, now=now, retired_template=True)
            if "saved" in prepared:
                return None
            return service.commit(prepared)
        except (ValueError, KeyError):
            return None

    def complete(self):
        if self.key is not None:
            self.store.set_setting(SETTING, {"content_key": self.key})

    def run(self, *, now=None):
        """The whole pass in one call (tools and tests); the number replaced, None when not due."""
        if not self.due():
            return None
        replaced, complete = 0, True
        for question_id in self.candidates():
            try:
                replaced += self.replace(question_id, now=now) is not None
            except Exception as error:  # noqa: BLE001 - it stays pending; the next start tries again
                record_error("retired_template_pass", "commit", error)
                complete = False
        if complete:
            self.complete()
        return replaced


def replaced_count(app):
    """Questions this process replaced (the libraries page reports it once there are some)."""
    return getattr(app, _COUNT, 0)


def _scan_in_worker(content, progress, *, key):
    """practice_worker job: the question ids to try (read only, on the worker's own connections)."""
    from .learning_store import LearningStore
    from .material_qualification import bind_resources
    bind_resources(progress, repo=content)
    return RetiredTemplatePass(LearningStore(progress), key).candidates()


class PassRun:
    """The pass of this process while it is not finished: scanning, then replacing.

    urgent: a round start waits for it (round_must_wait), so it no longer pauses for a focused page;
    a sealed round, a backup or an open transaction still pause it. done: finished, failed or
    stopped; the callbacks of waiting round starts then run once.
    """

    def __init__(self):
        self.done, self.urgent, self._callbacks, self._wake = False, False, [], None

    def wake(self):
        if self._wake is not None:
            self._wake.set()

    async def pause(self):
        """Wait up to WAIT_SECONDS, or until a round start asks for the rest at once."""
        self._wake = asyncio.Event()
        try:
            if not self.urgent:
                await asyncio.wait_for(self._wake.wait(), WAIT_SECONDS)
        except (asyncio.TimeoutError, TimeoutError):
            pass
        finally:
            self._wake = None

    def when_done(self, callback):
        if self.done:
            callback()
        else:
            self._callbacks.append(callback)

    def finish(self):
        if self.done:
            return
        self.done = True
        callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            try:
                callback()
            except Exception as error:  # noqa: BLE001 - one waiting page never stops the others
                record_error("retired_template_pass", "callback", error)


def running(app):
    """The unfinished pass of this process, or None (none needed, or finished)."""
    run = getattr(app, _RUN, None)
    return run if run is not None and not run.done else None


async def start(app):
    """Start-up step (first in KanaMemory._warm_content, before any learner action can begin a round).

    The content check is one setting read on the UI connection: with an unchanged content nothing
    else happens and a round start never waits. Otherwise the scan runs on a worker, then the
    replacements as their own task on the UI connection. Only the real app runs it."""
    if not getattr(app, "_background_assembly", False):
        return None
    store, learning = app.store, app.learning_store
    try:
        key = content_key()
        if not RetiredTemplatePass(learning, key).due():
            return None
    except Exception as error:  # noqa: BLE001 - the questions stay as they are until the next start
        record_error("retired_template_pass", "read", error)
        return None
    run = PassRun()
    setattr(app, _RUN, run)  # from here a round start waits for the scan too
    from .practice_worker import run_in_background
    try:
        question_ids = await run_in_background(app.repo, store, _scan_in_worker, {"key": key})
    except Exception as error:  # noqa: BLE001 - a waiting round start goes on with the old questions
        record_error("retired_template_pass", "read", error)
        run.finish()
        return None
    if _ended(app, store, learning):
        run.finish()
        return None
    work = RetiredTemplatePass(learning, key)
    task = app.loop.create_task(_replace_all(app, work, question_ids, run))
    app._retired_template_task = task  # a running task keeps a reference
    return task


async def _replace_all(app, work, question_ids, run=None):
    run = run if run is not None else PassRun()
    try:
        return await _replace_each(app, work, question_ids, run)
    finally:
        run.finish()


async def _replace_each(app, work, question_ids, run):
    store, learning = work.store, work.learning
    complete = True
    for question_id in question_ids:
        while not _ended(app, store, learning) and _waiting(app, store, urgent=run.urgent):
            await run.pause()
        if _ended(app, store, learning):
            return False
        try:
            if work.replace(question_id) is not None:
                setattr(app, _COUNT, replaced_count(app) + 1)
        except Exception as error:  # noqa: BLE001 - it stays pending; the next start tries again
            record_error("retired_template_pass", "commit", error)
            complete = False
        await asyncio.sleep(0)  # one transaction per turn: the UI stays responsive
    if not complete or _ended(app, store, learning):
        return False
    try:
        work.complete()
    except Exception as error:  # noqa: BLE001 - the next start scans again; nothing is lost
        record_error("retired_template_pass", "commit", error)
        return False
    return True


def _ended(app, store, learning):
    """The app is closing, or its record was replaced (a restore): stop without the completion mark."""
    return (getattr(app, "_backup_closing", False) or getattr(app, "store", None) is not store
            or getattr(app, "learning_store", None) is not learning)


def _waiting(app, store, *, urgent=False):
    from .practice_round import sealed
    return (getattr(app, "_backup_busy", False) or (getattr(app, "_practice_context", False) and not urgent)
            or store.in_transaction or sealed(store))


# ------------------------------------------------------------ the round start waits for the pass

def round_must_wait(app, resume):
    """Called where a round is about to be collected (the first 继续学习 of a sitting, the
    re-arrangement after a break), never inside a sealed round. With the pass of the current content
    unfinished (also while it still scans): show WAIT_TEXT on the round start's wait page, let the
    pass replace the rest at once (one transaction per turn), and call ``resume()`` when it is done,
    failed or stopped, if that page is still shown. True when the caller must return now."""
    run = running(app)
    if run is None:
        return False
    from .practice_round import sealed
    if sealed(app.store):
        return False  # a round already collected goes on; the pass waits for its end
    from .study_path_runtime import show_continue_wait, cancel_continue_wait
    if not show_continue_wait(app, WAIT_PAGE, app.loop, text=WAIT_TEXT):
        return False  # no page for it: the round starts as today
    token = app._page_token
    run.urgent = True
    run.wake()

    def done():
        if getattr(app, "page", None) != WAIT_PAGE or app._page_token != token:
            return  # the learner left; the next 继续学习 starts the round
        cancel_continue_wait(app)
        app.loop.call_soon(_resume, app, resume)
    run.when_done(done)
    return True


def _resume(app, resume):
    try:
        resume()
    except Exception as error:  # noqa: BLE001 - the continue action shows its own error page
        from .study_path_runtime import continue_failed
        continue_failed(app, error, source="async_task")
