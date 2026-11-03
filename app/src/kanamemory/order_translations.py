"""Experimental Chinese order glosses, bound to an exact bilingual context.

This read-only resource is not an answer, audio transcript, or scheduling input.
Callers may show it in teaching material or after an answer, never as quiz help
merely because a matching entry exists.
"""
from copy import deepcopy
from contextlib import closing
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

FORMAT = 'kanamemory-order-translations'
VERSION = 1
FIELDS = frozenset({'version', 'status', 'source_text', 'source_translation', 'text'})


def _plain(value, *, empty=False):
    if (not isinstance(value, str) or len(value) > 4000 or
            (not empty and not value.strip()) or
            any(ord(c) < 32 and c not in '\n\r\t' for c in value)):
        raise ValueError('Invalid order-translation text')
    try:
        value.encode('utf-8', errors='strict')
    except UnicodeError:
        raise ValueError('Invalid order-translation Unicode') from None
    return value


def context_id(text, translation=''):
    _plain(text)
    _plain(translation, empty=True)
    return sha256(json.dumps([text, translation], ensure_ascii=False,
                            separators=(',', ':')).encode('utf-8')).hexdigest()


def validate_order_translation(value, *, source_text, source_translation=''):
    """Strict ancillary-field validation without editing the supplied snapshot."""
    _plain(source_text)
    _plain(source_translation, empty=True)
    if (not isinstance(value, dict) or set(value) != FIELDS or
            type(value.get('version')) is not int or value['version'] != VERSION or
            value.get('status') != 'experimental' or
            value.get('source_text') != source_text or
            value.get('source_translation') != source_translation):
        raise ValueError('Order translation does not match its bilingual source')
    _plain(value.get('text'))
    return deepcopy(value)


def _database_path():
    return Path(__file__).with_name('resources') / 'order_translations.sqlite3'


@lru_cache(maxsize=512)
def _lookup(path, stamp, identity, text, translation):
    try:
        with closing(sqlite3.connect(Path(path).as_uri()+'?mode=ro&immutable=1', uri=True)) as db:
            metadata = dict(db.execute('SELECT key,value FROM metadata'))
            if (metadata.get('format') != FORMAT or metadata.get('version') != str(VERSION)
                    or db.execute('PRAGMA user_version').fetchone()[0] != VERSION):
                return None
            row = db.execute('SELECT source_text,source_translation,text FROM translation WHERE id=?',
                             (identity,)).fetchone()
        if not row or (row[0], row[1]) != (text, translation):
            return None
        return validate_order_translation({'version': VERSION, 'status': 'experimental',
            'source_text': row[0], 'source_translation': row[1], 'text': row[2]},
            source_text=text, source_translation=translation)
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return None


def order_translation_for(text, translation='', explicit=None):
    """Return an exact-context gloss or None, without requests or learner writes."""
    try:
        identity = context_id(text, translation)
        if explicit is not None:
            return validate_order_translation(explicit, source_text=text,
                                              source_translation=translation)
        path = _database_path().resolve()
        stat = path.stat()
        result = _lookup(str(path), (stat.st_size, stat.st_mtime_ns), identity, text, translation)
        return deepcopy(result) if result else None
    except (OSError, ValueError, TypeError):
        return None
