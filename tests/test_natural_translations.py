"""Display wording cannot alter original bilingual identities or learner data."""
from hashlib import sha256
import json
import sqlite3
from unittest.mock import Mock

import pytest

from kanamemory import natural_translations as natural
from kanamemory.order_translations import context_id


def database(path, rows, *, format=natural.FORMAT, version=1):
    with sqlite3.connect(path) as db:
        db.executescript('DROP TABLE IF EXISTS metadata; DROP TABLE IF EXISTS translation; '
            'CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT); '
            'CREATE TABLE translation(id TEXT PRIMARY KEY,source_text TEXT,source_translation TEXT,text TEXT,metadata_json TEXT);')
        db.execute('PRAGMA user_version=' + str(version))
        db.executemany('INSERT INTO metadata VALUES (?,?)', [('format', format), ('version', str(version))])
        db.executemany('INSERT INTO translation VALUES (?,?,?,?,?)',
            [(context_id(jp, old), jp, old, new, json.dumps({'review': 'fixture'})) for jp, old, new in rows])


def test_display_is_exact_context_read_only_and_retains_original_identity(tmp_path, monkeypatch):
    path = tmp_path / 'natural.sqlite3'
    rows = [('結構です。', '不用了。', '不需要了，谢谢。'), ('結構です。', '挺好的。', '很好。')]
    database(path, rows)
    monkeypatch.setattr(natural, '_database_path', lambda: path)
    before = sha256(path.read_bytes()).hexdigest()
    for jp, old, new in rows:
        assert natural.natural_translation_for(jp, old) == new
        assert natural.natural_translation_for(jp + ' ', old) == old
        assert natural.natural_translation_for(jp, old + ' ') == old + ' '
    assert natural.natural_translation_for('結構です。', '别的语境。') == '别的语境。'
    assert natural.natural_translation_for('結構です。', '') == ''
    assert before == sha256(path.read_bytes()).hexdigest()
    assert [p.name for p in tmp_path.iterdir()] == ['natural.sqlite3']


@pytest.mark.parametrize('state', ['absent', 'corrupt', 'format', 'version', 'pending', 'wrong_pair'])
def test_missing_incompatible_pending_and_wrong_pair_preserve_original(tmp_path, monkeypatch, state):
    path = tmp_path / 'natural.sqlite3'
    jp, old, new = '私は学生です。', '我是学生。', '我是一名学生。'
    monkeypatch.setattr(natural, '_database_path', lambda: path)
    if state == 'corrupt':
        path.write_bytes(b'not sqlite')
    elif state != 'absent':
        database(path, [] if state == 'pending' else [(jp, old, new)],
                 format='wrong' if state == 'format' else natural.FORMAT,
                 version=2 if state == 'version' else 1)
        if state == 'wrong_pair':
            with sqlite3.connect(path) as db:
                db.execute("UPDATE translation SET source_text='私は先生です。'")
    assert natural.natural_translation_for(jp, old) == old
    assert natural.natural_translation_note(jp, old) == ''
    assert path.exists() == (state != 'absent')


@pytest.mark.parametrize('bad', ['', ' ', 'a\x00b', 'a' * 4001, None, 17])
def test_invalid_display_text_is_not_shown(tmp_path, monkeypatch, bad):
    path = tmp_path / 'natural.sqlite3'
    jp, old = '私は学生です。', '我是学生。'
    database(path, [(jp, old, bad)])
    monkeypatch.setattr(natural, '_database_path', lambda: path)
    # SQLite TEXT affinity turns numbers into strings; store an invalid blob
    # to exercise an incompatible non-text value instead.
    if bad == 17:
        with sqlite3.connect(path) as db:
            db.execute('UPDATE translation SET text=?', (b'wrong type',))
    assert natural.natural_translation_for(jp, old) == old
    assert natural.natural_translation_note(jp, old) == ''


def test_cache_refreshes_after_resource_replacement(tmp_path, monkeypatch):
    path = tmp_path / 'natural.sqlite3'
    jp, old = '私は学生です。', '我是学生。'
    database(path, [(jp, old, '我是一名学生。')])
    monkeypatch.setattr(natural, '_database_path', lambda: path)
    assert natural.natural_translation_for(jp, old) == '我是一名学生。'
    database(path, [(jp, old, '我是学生（不是老师）。')])
    assert natural.natural_translation_for(jp, old) == '我是学生（不是老师）。'


def test_absent_original_never_queries_or_creates_a_translation(monkeypatch):
    def forbidden():
        pytest.fail('An absent translation must not trigger lookup')
    monkeypatch.setattr(natural, '_database_path', forbidden)
    assert natural.natural_translation_for('私は学生です。') == ''
    assert natural.natural_translation_for('私は学生です。', None) == ''
    assert natural.natural_translation_for('私は学生です。', '  ') == '  '
    assert natural.natural_translation_note('私は学生です。') == ''
    assert natural.natural_translation_note('私は学生です。', None) == ''


def test_existing_teaching_note_is_exact_bound_read_only_and_not_a_translation(tmp_path, monkeypatch):
    path = tmp_path / 'natural.sqlite3'
    jp, old, new = 'あめが', '雨①加が，音高是高低低。', '雨'
    note = '雨①加が，音高是高低低。'
    database(path, [(jp, old, new)])
    with sqlite3.connect(path) as db:
        db.execute('UPDATE translation SET metadata_json=?',
            (json.dumps({'teaching_note': note}, ensure_ascii=False),))
    monkeypatch.setattr(natural, '_database_path', lambda: path)
    digest = sha256(path.read_bytes()).hexdigest()
    assert natural.natural_translation_for(jp, old) == new
    assert natural.natural_translation_note(jp, old) == note
    assert natural.natural_translation_note(jp + ' ', old) == ''
    assert natural.natural_translation_note(jp, old + ' ') == ''
    assert sha256(path.read_bytes()).hexdigest() == digest
    assert [p.name for p in tmp_path.iterdir()] == ['natural.sqlite3']


@pytest.mark.parametrize('metadata', [None, '', '{broken', '[]', '{}',
    json.dumps({'teaching_note': None}), json.dumps({'teaching_note': []}),
    json.dumps({'teaching_note': ' '}), json.dumps({'teaching_note': 'a\x00b'}),
    json.dumps({'teaching_note': '\ud800'}), json.dumps({'teaching_note': 'a' * 2001}),
    '[' * 2000 + ']' * 2000])
def test_missing_or_damaged_note_is_empty_but_valid_translation_survives(tmp_path, monkeypatch, metadata):
    path = tmp_path / 'natural.sqlite3'
    jp, old, new = 'あめが', '雨①加が，音高是高低低。', '雨'
    database(path, [(jp, old, new)])
    with sqlite3.connect(path) as db:
        db.execute('UPDATE translation SET metadata_json=?', (metadata,))
    monkeypatch.setattr(natural, '_database_path', lambda: path)
    assert natural.natural_translation_for(jp, old) == new
    assert natural.natural_translation_note(jp, old) == ''


def test_unchanged_translation_never_repeats_note(tmp_path, monkeypatch):
    path = tmp_path / 'natural.sqlite3'
    jp, old = 'あめが', '雨①加が，音高是高低低。'
    database(path, [(jp, old, old)])
    with sqlite3.connect(path) as db:
        db.execute('UPDATE translation SET metadata_json=?', (json.dumps({'teaching_note': old}),))
    monkeypatch.setattr(natural, '_database_path', lambda: path)
    assert natural.natural_translation_for(jp, old) == old
    assert natural.natural_translation_note(jp, old) == ''


def pending_database(path, jp='あめが', old='雨的读音模型。', reason='片段不足，保留原有说明。'):
    database(path, [])
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE pending(id TEXT PRIMARY KEY,source_text TEXT,source_translation TEXT,reason TEXT)')
        db.execute('INSERT INTO pending VALUES (?,?,?,?)', (context_id(jp, old), jp, old, reason))


def test_pending_snapshot_is_exact_read_only_and_not_cached_mutably(tmp_path, monkeypatch):
    path = tmp_path / 'natural.sqlite3'
    jp, old, reason = 'あめが', '雨的读音模型。', '片段不足，保留原有说明。'
    pending_database(path, jp, old, reason)
    monkeypatch.setattr(natural, '_database_path', lambda: path)
    digest = sha256(path.read_bytes()).hexdigest()
    expected = {'status': 'pending', 'reason': reason, 'retained_original': True}
    assert natural.natural_translation_for(jp, old) == old
    assert natural.natural_translation_note(jp, old) == ''
    found = natural.natural_translation_pending(jp, old)
    assert found == expected
    found['reason'] = 'mutated'
    assert natural.natural_translation_pending(jp, old) == expected
    assert natural.natural_translation_pending(jp + ' ', old) is None
    assert natural.natural_translation_pending(jp, old + ' ') is None
    assert natural.natural_translation_pending(jp, '') is None
    assert sha256(path.read_bytes()).hexdigest() == digest
    assert [p.name for p in tmp_path.iterdir()] == ['natural.sqlite3']


@pytest.mark.parametrize('state', ['missing', 'corrupt', 'uncovered', 'future', 'bad_format', 'wrong_pair',
    'contradictory', 'empty_reason', 'long_reason', 'invalid_reason'])
def test_non_pending_or_invalid_resource_cannot_create_pending_badge(tmp_path, monkeypatch, state):
    path = tmp_path / 'natural.sqlite3'
    jp, old = 'あめが', '雨的读音模型。'
    monkeypatch.setattr(natural, '_database_path', lambda: path)
    if state == 'corrupt':
        path.write_bytes(b'broken sqlite')
    elif state == 'uncovered':
        database(path, [])
    elif state != 'missing':
        pending_database(path, jp, old)
        with sqlite3.connect(path) as db:
            if state == 'future':
                db.execute('PRAGMA user_version=2')
            elif state == 'bad_format':
                db.execute("UPDATE metadata SET value='wrong' WHERE key='format'")
            elif state == 'wrong_pair':
                db.execute("UPDATE pending SET source_text='はしが'")
            elif state == 'contradictory':
                db.execute('INSERT INTO translation VALUES (?,?,?,?,?)', (context_id(jp,old),jp,old,old,'{}'))
            elif state in {'empty_reason', 'long_reason', 'invalid_reason'}:
                reason = {'empty_reason':' ', 'long_reason':'a'*4001, 'invalid_reason':'x\x00y'}[state]
                db.execute('UPDATE pending SET reason=?', (reason,))
    assert natural.natural_translation_pending(jp, old) is None
    assert path.exists() == (state != 'missing')


def test_pending_status_refreshes_when_sidecar_is_resolved(tmp_path, monkeypatch):
    path = tmp_path / 'natural.sqlite3'
    jp, old = 'あめが', '雨的读音模型。'
    pending_database(path, jp, old)
    monkeypatch.setattr(natural, '_database_path', lambda: path)
    assert natural.natural_translation_pending(jp, old)
    database(path, [(jp, old, '雨')])
    assert natural.natural_translation_pending(jp, old) is None
    assert natural.natural_translation_for(jp, old) == '雨'


@pytest.mark.parametrize('pending', [False, True])
def test_text_note_and_status_share_one_read_only_connection(tmp_path, monkeypatch, pending):
    path = tmp_path / 'natural.sqlite3'
    jp, old = 'あめが', '雨的读音模型。'
    if pending:
        pending_database(path, jp, old)
    else:
        database(path, [(jp, old, '雨')])
    monkeypatch.setattr(natural, '_database_path', lambda: path)
    natural._lookup.cache_clear()
    connect = Mock(wraps=sqlite3.connect)
    monkeypatch.setattr(natural.sqlite3, 'connect', connect)
    for _ in range(2):
        natural.natural_translation_for(jp, old)
        natural.natural_translation_note(jp, old)
        natural.natural_translation_pending(jp, old)
    assert connect.call_count == 1
    assert 'mode=ro&immutable=1' in connect.call_args.args[0]
