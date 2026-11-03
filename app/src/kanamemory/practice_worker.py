"""Build practice plans without sharing SQLite connections across threads."""
from __future__ import annotations

import asyncio
from concurrent.futures import CancelledError
from threading import Event

from .content import ContentRepository
from .domain import StudyStore, prepare_session_snapshot


def study_revision(store):
    """Detect both this connection's writes and commits from other connections."""
    return store.read_revision()


async def build_in_background(repository, store, builder, options):
    content_path, progress_dir = repository.path, store.data_dir
    zone = None if store._device_local else store.zone
    cancelled = Event()

    def check_cancelled():
        if cancelled.is_set():
            raise CancelledError("Practice generation cancelled")

    def build():
        with ContentRepository(content_path) as content, StudyStore(progress_dir, time_zone=zone) as progress:
            progress._build_cancel_check = check_cancelled
            check_cancelled()
            questions = builder(content, progress, **options)
            check_cancelled()
            prepared = prepare_session_snapshot(questions, seed=options.get("seed")) if questions else None
            return questions, prepared

    try:
        return await asyncio.to_thread(build)
    finally:
        cancelled.set()
