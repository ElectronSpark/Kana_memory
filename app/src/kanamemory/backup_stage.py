"""Private backup staging with nonblocking OS ownership locks.

Windows uses msvcrt byte locks; POSIX (including Android/Linux) uses flock.
No PID/age heuristic, recursive traversal, or unknown-file deletion is used.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import tempfile
from uuid import uuid4

FORMAT = 'kanamemory-backup-stage'
MARKER = 'owner.json'
LOCK = 'owner.lock'
DATABASE_FILES = {'stage.sqlite3', 'stage.sqlite3-journal', 'stage.sqlite3-wal', 'stage.sqlite3-shm'}
FILES = DATABASE_FILES | {MARKER, LOCK}
NAME = re.compile(r'\.backup-stage-([0-9a-f]{32})-[a-z0-9_]{8}\Z')


def _plain_file(path):
    try:
        value = path.lstat()
        return (stat.S_ISREG(value.st_mode) and value.st_nlink == 1
            and not (getattr(value, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)))
    except OSError:
        return False


def _directory(path):
    try:
        value = path.lstat()
        return (stat.S_ISDIR(value.st_mode) and not path.is_symlink()
            and not (getattr(value, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)))
    except OSError:
        return False


def _structure(root, path):
    """Check after taking the lock too; a prior scan does not authorize delete."""
    match = NAME.fullmatch(path.name)
    if not match or path.parent != root or not _directory(path):
        return False
    try:
        entries = list(path.iterdir())
        if ({entry.name for entry in entries} - FILES
                or not {MARKER, LOCK} <= {entry.name for entry in entries}
                or any(not _plain_file(entry) for entry in entries)
                or (path/MARKER).stat().st_size > 1024
                or (path/LOCK).stat().st_size != 1):
            return False
        marker = json.loads((path/MARKER).read_text(encoding='utf-8'))
        return (isinstance(marker, dict) and set(marker) == {'format','version','token'}
            and marker['format'] == FORMAT and type(marker['version']) is int
            and marker['version'] == 1 and marker['token'] == match.group(1))
    except (OSError, ValueError, UnicodeError):
        return False


def _acquire(handle):
    if os.name == 'nt':
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    elif os.name == 'posix':
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    else:
        raise RuntimeError('当前平台不支持安全的备份暂存锁。')


def _release(handle):
    if handle.closed:
        return
    try:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        elif os.name == 'posix':
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _open_existing(path):
    if not _plain_file(path):
        raise ValueError('Not an ordinary staging lock')
    descriptor = os.open(path, os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0))
    handle = os.fdopen(descriptor, 'r+b', buffering=0)
    try:
        # Reject a link/replacement between lstat and open.
        actual, current = os.fstat(handle.fileno()), path.lstat()
        if (actual.st_dev, actual.st_ino) != (current.st_dev, current.st_ino) or not _plain_file(path):
            raise ValueError('Staging lock changed while opening')
        _acquire(handle)
        return handle
    except BaseException:
        handle.close()
        raise


def _remove(root, path, handle):
    """Lock is held throughout all potentially large-file removals."""
    try:
        if not _structure(root, path):
            return False
        for name in DATABASE_FILES:
            candidate = path/name
            if candidate.exists():
                candidate.unlink()
        # A crash before this point remains recognizable/retryable. After it,
        # no database remains, so a lock-removal failure leaves only tiny files.
        (path/MARKER).unlink()
    finally:
        _release(handle)
    try:
        (path/LOCK).unlink()
        path.rmdir()
        return True
    except OSError:
        return False


def reclaim_orphans(directory):
    root = Path(directory).resolve()
    result = {'reclaimed':0, 'busy':0, 'unrecognized':0, 'errors':0}
    for path in root.iterdir():
        if not path.name.startswith('.backup-stage-'):
            continue
        if not _structure(root, path):
            result['unrecognized'] += 1
            continue
        try:
            handle = _open_existing(path/LOCK)
        except (PermissionError, BlockingIOError):
            result['busy'] += 1
            continue
        except (OSError, ValueError):
            result['errors'] += 1
            continue
        try:
            if _remove(root, path, handle):
                result['reclaimed'] += 1
            else:
                result['unrecognized'] += 1
        except OSError:
            result['errors'] += 1
            _release(handle)
    return result


class StageOwner:
    def __init__(self, directory):
        self.root = Path(directory).resolve()
        reclaim_orphans(self.root)
        token = uuid4().hex
        self.path = Path(tempfile.mkdtemp(prefix=f'.backup-stage-{token}-', dir=self.root))
        self.name = str(self.path)
        self.handle = None
        try:
            self.handle = (self.path/LOCK).open('x+b', buffering=0)
            self.handle.write(b'\0')
            _acquire(self.handle)
            with (self.path/MARKER).open('x',encoding='utf-8') as marker:
                json.dump({'format':FORMAT,'version':1,'token':token},marker,separators=(',',':'))
                marker.flush()
                os.fsync(marker.fileno())
        except BaseException:
            if self.handle is not None:
                try:
                    _release(self.handle)
                except OSError:
                    pass
            # Creation has not exposed this directory to any database yet.
            for name in (MARKER,LOCK):
                (self.path/name).unlink(missing_ok=True)
            self.path.rmdir()
            raise
    def cleanup(self):
        if self.handle is None:
            return
        handle, self.handle = self.handle, None
        try:
            _remove(self.root,self.path,handle)
        except OSError:
            # Keep marker and release the lock for a safe later retry.
            _release(handle)
