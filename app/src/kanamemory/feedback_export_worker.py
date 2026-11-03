"""Export preparation owns its connections; delivery remains on the UI thread."""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
from dataclasses import dataclass
from threading import Event
from time import perf_counter

from .feedback_store import FeedbackStore, PreparedFeedbackExport


class FeedbackExportCancelled(Exception):
    pass


@dataclass(frozen=True)
class FeedbackExportResult:
    prepared: PreparedFeedbackExport
    content_stamp: object
    timings_ms: dict[str, float]


class FeedbackExportJob:
    """One preparation/delivery receipt; no borrowed DB connections or widgets.

    Cancellation waits for the worker to close its connections. Archive is only
    invoked after successful delivery and independently revalidates the receipt.
    """
    def __init__(self, feedback_path, data_dir=None, zone=None, identities=None, format="zip"):
        self.feedback_path, self.data_dir, self.zone = feedback_path, data_dir, zone
        self.identities = None if identities is None else tuple(identities)
        self.format = format
        self.cancelled = Event()

    def cancel(self):
        self.cancelled.set()

    def check(self):
        if self.cancelled.is_set():
            raise FeedbackExportCancelled()

    def _run(self, receipt=None, stamp=None):
        from .domain import StudyStore
        from .personal_content_compensation import sync, export_stamp, archive_current
        self.check()
        with ExitStack() as resources:
            feedback = FeedbackStore(self.feedback_path)
            resources.callback(feedback.close)
            store = resources.enter_context(StudyStore(self.data_dir, time_zone=self.zone)) if self.data_dir else None
            start = perf_counter()
            ids = self.identities if receipt is None else [row[0] for row in receipt.records]
            sync(store, feedback, identities=ids, complete=True)
            synchronized = perf_counter()
            self.check()
            if receipt is not None:
                return archive_current(store, feedback, receipt, stamp)
            stamp = export_stamp(store)
            prepared = feedback.prepare_export(self.identities, format=self.format, cancel_check=self.check)
            self.check()
            return FeedbackExportResult(prepared, stamp, {
                "sync": round((synchronized - start) * 1000, 2),
                "prepare": round((perf_counter() - synchronized) * 1000, 2)})

    async def _await_worker(self, *args):
        task = asyncio.create_task(asyncio.to_thread(self._run, *args))
        while True:
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                self.cancel()
                if task.done():
                    return task.result()

    async def run(self):
        return await self._await_worker()

    async def archive(self, receipt, stamp):
        return await self._await_worker(receipt, stamp)
