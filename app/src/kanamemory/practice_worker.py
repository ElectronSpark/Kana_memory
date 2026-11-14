"""Build practice plans without sharing SQLite connections across threads.

Every job runs on one worker thread that opens, uses and closes its own
ContentRepository and StudyStore connections; the UI connection, widgets and
in-memory practice-round plan never cross the thread boundary. A result is only
a proposal: before applying it the UI thread checks a BackgroundTicket (the
page it was requested for and the UI connection's data revision). A left page
discards it; changed data makes the caller assemble again. Commits stay with
the existing Store write transactions, which re-validate their own inputs.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import CancelledError
from dataclasses import dataclass
from threading import Event

from .content import ContentRepository
from .domain import StudyStore, prepare_session_snapshot


def study_revision(store):
    """Detect both this connection's writes and commits from other connections."""
    return store.read_revision()


@dataclass(frozen=True)
class BackgroundTicket:
    """Identity of one background request: page, page token and data revision."""
    page: str
    page_token: int
    revision: tuple

    @classmethod
    def capture(cls, page, page_token, store):
        return cls(page, page_token, tuple(study_revision(store)))

    def page_current(self, page, page_token) -> bool:
        """False once the learner left (any screen change bumps the token)."""
        return page == self.page and page_token == self.page_token

    def data_current(self, store) -> bool:
        """False after any local write, rollback or another connection's commit."""
        return not store.in_transaction and tuple(study_revision(store)) == self.revision


async def run_in_background(repository, store, work, options=None):
    """Run ``work(content, progress, **options)`` on connections owned by one worker thread.

    Reads in the worker are plain WAL reads (autocommit): they never hold the
    progress write lock, so the UI connection's writes are not delayed.
    """
    content_path, progress_dir = repository.path, store.data_dir
    zone = None if store._device_local else store.zone
    cancelled = Event()
    arguments = dict(options or {})

    def check_cancelled():
        if cancelled.is_set():
            raise CancelledError("Practice generation cancelled")

    def run():
        with ContentRepository(content_path) as content, StudyStore(progress_dir, time_zone=zone) as progress:
            progress._build_cancel_check = check_cancelled
            check_cancelled()
            result = work(content, progress, **arguments)
            check_cancelled()
            return result

    try:
        return await asyncio.to_thread(run)
    finally:
        cancelled.set()


async def build_in_background(repository, store, builder, options):
    def build(content, progress, **arguments):
        questions = builder(content, progress, **arguments)
        progress._build_cancel_check()
        prepared = prepare_session_snapshot(questions, seed=arguments.get("seed")) if questions else None
        return questions, prepared

    return await run_in_background(repository, store, build, options)
