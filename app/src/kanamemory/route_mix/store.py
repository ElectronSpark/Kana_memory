"""Adapter: the plan file <learner data directory>/derived/main_route_mix_v1.json (design 11 section 16, M10).

Derived data only: never in the learner database or a backup. A missing,
oversized, unreadable or invalid file reads as no plan and is replaced by the
next save. Saving writes main_route_mix_v1.json.tmp next to it (a fixed name, so
an interrupted save leaves at most that one file; each save removes it and
creates it anew, exclusively, so a link there is never written through) and
renames it over the old one (os.replace): a reader sees the old or the new plan,
whole.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading

DIRECTORY = "derived"
FILE_NAME = "main_route_mix_v1.json"
TEMPORARY_NAME = FILE_NAME + ".tmp"
MAX_BYTES = 4 * 1024 * 1024
_SAVING = threading.Lock()  # one save at a time in this process: the temporary file has a fixed name


class PlanFile:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / DIRECTORY / FILE_NAME
        self.temporary = self.path.with_name(TEMPORARY_NAME)

    def present(self) -> bool:
        """A file is there (one stat; nothing is read)."""
        try:
            return self.path.is_file()
        except OSError:
            return False

    def load(self) -> dict | None:
        """The stored JSON object, or None (absent or not usable)."""
        try:
            if self.path.is_symlink() or self.path.parent.is_symlink() or not self.path.is_file():
                return None
            if self.path.stat().st_size > MAX_BYTES:
                return None
            raw = self.path.read_bytes()
            if len(raw) > MAX_BYTES:
                return None
            value = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError, RecursionError):
            return None
        return value if isinstance(value, dict) else None

    def save(self, value: dict) -> None:
        """Replace the file atomically; OSError/ValueError when it cannot be written."""
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_BYTES:
            raise ValueError("Main route mix plan exceeds its size limit")
        directory = self.path.parent
        directory.mkdir(parents=True, exist_ok=True)
        with _SAVING:
            if directory.is_symlink() or self.path.is_symlink():
                raise OSError("Main route mix plan location is a link")
            # A temporary file left there (or a link put there) is removed, never written through: the
            # new one is created exclusively.
            try:
                os.unlink(self.temporary)
            except FileNotFoundError:
                pass
            handle = os.open(self.temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
            try:
                with os.fdopen(handle, "wb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(self.temporary, self.path)
            except BaseException:
                try:
                    os.unlink(self.temporary)
                except OSError:
                    pass
                raise
