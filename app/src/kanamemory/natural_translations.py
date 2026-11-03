"""Read-only display translations anchored to the original bilingual context.

The immutable source translation remains the key for examples, saved questions,
exports, and experimental order glosses. This overlay is presentation only: it
must never provide a previously hidden meaning in a question or rewrite data.
"""
from contextlib import closing
from functools import lru_cache
import json
from pathlib import Path
import sqlite3

from .order_translations import context_id

FORMAT = 'kanamemory-natural-translations'
VERSION = 1


def _database_path():
    return Path(__file__).with_name('resources') / 'natural_translations.sqlite3'


@lru_cache(maxsize=512)
def _lookup(path, stamp, identity, text, translation):
    try:
        with closing(sqlite3.connect(Path(path).as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
            metadata = dict(db.execute('SELECT key,value FROM metadata'))
            if (metadata.get('format') != FORMAT or metadata.get('version') != str(VERSION)
                    or db.execute('PRAGMA user_version').fetchone()[0] != VERSION):
                return None
            row = db.execute('SELECT source_text,source_translation,text,metadata_json FROM translation WHERE id=?',
                             (identity,)).fetchone()
            # Translation, teaching note and pending state share one cached
            # read. Only an absent translation can be a pending decision.
            pending = None if row else db.execute(
                'SELECT source_text,source_translation,reason FROM pending WHERE id=?', (identity,)).fetchone()
        if not row:
            if not pending or (pending[0], pending[1]) != (text, translation):
                return None
            context_id(pending[2], translation)
            return translation, '', pending[2]
        if (row[0], row[1]) != (text, translation):
            return None
        # Reuse the exact-context text validation, without normalizing either
        # anchor. Empty, malformed or incompatible rows retain the old display.
        context_id(row[2], translation)
        note = ''
        # A retained teaching note is ancillary display text. Malformed notes
        # must not hide a valid translation or duplicate an unchanged original.
        if row[2] != translation:
            try:
                provenance = json.loads(row[3])
                candidate = provenance.get('teaching_note', '') if isinstance(provenance, dict) else ''
                if isinstance(candidate, str) and len(candidate) <= 2000 and candidate:
                    context_id(candidate, translation)
                    note = candidate
            except (ValueError, TypeError, RecursionError):
                pass
        return row[2], note, ''
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return None


def _display_record(text, translation):
    if not isinstance(translation, str) or not translation.strip():
        return None
    try:
        identity = context_id(text, translation)
        path = _database_path().resolve()
        stat = path.stat()
        return _lookup(str(path), (stat.st_size, stat.st_mtime_ns), identity, text, translation)
    except (OSError, ValueError, TypeError):
        return None


def natural_translation_for(text, translation=''):
    """Return the modern Chinese display text, or the unchanged original.

    No new translation is supplied for an empty original. Missing, pending,
    stale and corrupt resource entries all preserve the existing translation.
    """
    record = _display_record(text, translation)
    return record[0] if record else translation if isinstance(translation, str) else ''


def natural_translation_note(text, translation=''):
    """Return an existing teaching note only for a valid changed translation.

    Notes come from the original teaching material; this module does not infer
    or author them. Callers use exactly the same disclosure rules as meanings.
    """
    record = _display_record(text, translation)
    return record[1] if record else ''


def natural_translation_pending(text, translation=''):
    """Return a fresh pending-status snapshot only for an explicit exact row.

    Uncovered examples, missing resources and malformed records are not review
    decisions. This status is presentation metadata, never a learning input.
    """
    record = _display_record(text, translation)
    return {'status': 'pending', 'reason': record[2], 'retained_original': True} if record and record[2] else None
