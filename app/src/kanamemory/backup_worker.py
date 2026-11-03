"""Backup work with a connection owned and closed by its worker thread."""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
from threading import Event

from .domain import StudyStore
from . import progress_backup


class BackupCancelled(Exception):
    pass


@dataclass(frozen=True)
class BackupResult:
    value: object
    committed: bool = False


class BackupJob:
    def __init__(self, action, data_dir, zone, data=None):
        if action not in {'export', 'previous', 'inspect', 'restore', 'convert'}:
            raise ValueError('Unknown backup operation')
        if action in {'inspect', 'restore', 'convert'} and not isinstance(data, bytes):
            raise TypeError('The selected backup must be immutable bytes')
        self.action, self.data_dir, self.zone, self.data = action, data_dir, zone, data
        self.cancelled = Event()
        self.committed = None

    def cancel(self):
        self.cancelled.set()

    def check(self):
        if self.cancelled.is_set():
            raise BackupCancelled()

    def _did_commit(self, value):
        self.committed = value

    def _run(self):
        self.check()
        try:
            with StudyStore(self.data_dir, time_zone=self.zone) as store:
                self.check()
                if self.action == 'restore':
                    value = progress_backup.import_backup(store, self.data,
                        cancel_check=self.check, on_committed=self._did_commit)
                elif self.action == 'convert':
                    value = progress_backup.convert_legacy_backup(store, self.data,
                        cancel_check=self.check)
                elif self.action == 'inspect':
                    value = store.inspect_backup(self.data)
                elif self.action == 'previous':
                    value = store.previous_backup()
                    if value is not None and progress_backup.is_legacy_backup(value):
                        value = progress_backup.convert_legacy_backup(store, value,
                            cancel_check=self.check)
                else:
                    value = store.export_backup()
                if self.committed is None:
                    self.check()
                return BackupResult(value, self.committed is not None)
        except Exception:
            if self.committed is not None:
                # Reopening on the UI thread reloads all derived state. Never
                # label a completed transaction as a cancelled/failed restore.
                return BackupResult(self.committed, True)
            raise

    async def run(self):
        task = asyncio.create_task(asyncio.to_thread(self._run))
        while True:
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                # Cancelling an await does not stop a Python thread. Keep the
                # UI write barrier until rollback/commit and connection close.
                self.cancel()
                if task.done():
                    return task.result()
