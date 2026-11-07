"""Indexed first-rating reads must retain the former SQL's exact candidates."""
from datetime import datetime, timedelta, timezone
import json
import random
import sqlite3
import pytest
from kanamemory.teaching_queries import TeachingQueries

KINDS = ('kana_choice', 'romaji_input', 'kana_handwriting')
OLD = '''WITH timed AS (
 SELECT c.content_id,c.script,COALESCE(json_extract(e.review_log_json,'$.finalized_at_utc'),
 e.occurred_at_utc) AS finalized_at FROM review_event e JOIN study_card c USING(card_id)
 WHERE c.skill IN (?,?,?)),
 minimum AS (SELECT content_id,script,MIN(julianday(finalized_at)) AS first_jd
 FROM timed GROUP BY content_id,script)
 SELECT t.content_id,t.script,t.finalized_at FROM timed t JOIN minimum m
 ON m.content_id=t.content_id AND m.script=t.script AND julianday(t.finalized_at)=m.first_jd'''
INDEX = '''CREATE INDEX reviews_card_first_finalized ON review_event(card_id,
 julianday(COALESCE(json_extract(review_log_json,'$.finalized_at_utc'),occurred_at_utc)))'''

def compare(db):
    before = '\n'.join(db.iterdump())
    assert sorted(tuple(row) for row in TeachingQueries(db).kana_first_primary_events(KINDS)) == sorted(tuple(row) for row in db.execute(OLD, KINDS))
    assert '\n'.join(db.iterdump()) == before

@pytest.mark.parametrize('seed', [7, 19, 113, 20261027])
def test_offsets_ties_future_nulls_scripts_skills_and_reordered_history(seed):
    rng = random.Random(seed)
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE study_card(card_id TEXT PRIMARY KEY,content_id TEXT,script TEXT,skill TEXT)')
        db.execute('CREATE TABLE review_event(event_id TEXT PRIMARY KEY,card_id TEXT,occurred_at_utc TEXT,review_log_json TEXT)')
        db.execute(INDEX)
        base = datetime(2027, 3, 14, 7, tzinfo=timezone.utc)
        events = []
        for content in range(8):
            for script in ('hiragana', 'katakana'):
                for skill in (*KINDS, 'word_kana'):
                    card = f'{content}/{script}/{skill}'
                    db.execute('INSERT INTO study_card VALUES(?,?,?,?)', (card, f'kana-{content}', script, skill))
                    for n in range(rng.randrange(6)):
                        at = base + timedelta(days=rng.choice([-3, 0, 0, 500]), microseconds=rng.choice([0, 50, 100, 499, 500]))
                        zone = timezone(timedelta(hours=rng.choice([-5, 0, 9])))
                        finished = (at + timedelta(microseconds=rng.choice([0, 100, 400]))).astimezone(zone).isoformat()
                        log = rng.choice([{}, {'finalized_at_utc': None}, {'finalized_at_utc': finished}])
                        events.append((f'{card}/{n}', card, at.astimezone(zone).isoformat(), json.dumps(log)))
        rng.shuffle(events)
        db.executemany('INSERT INTO review_event VALUES(?,?,?,?)', events)
        db.commit()
        compare(db)
        # Same candidate set when an earliest record is edited, deleted or
        # rolled back; no stale minimum can survive a real data change.
        db.execute('BEGIN')
        db.execute("UPDATE review_event SET occurred_at_utc='2020-01-01T00:00:00+00:00',review_log_json='{}' WHERE rowid=1")
        compare(db)
        db.rollback()
        compare(db)
        db.execute('DELETE FROM review_event WHERE rowid IN (1,2,3)')
        compare(db)
        assert not list(TeachingQueries(db).kana_first_primary_events(()))

def test_indexed_minimum_keeps_every_equal_time_direction():
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE study_card(card_id TEXT PRIMARY KEY,content_id TEXT,script TEXT,skill TEXT)')
        db.execute('CREATE TABLE review_event(event_id TEXT PRIMARY KEY,card_id TEXT,occurred_at_utc TEXT,review_log_json TEXT)')
        db.execute(INDEX)
        for n, skill in enumerate(KINDS):
            db.execute('INSERT INTO study_card VALUES(?,?,?,?)', (str(n), 'a', 'hiragana', skill))
            for k in range(2):
                db.execute('INSERT INTO review_event VALUES(?,?,?,?)', (f'{n}-{k}', str(n), '2027-01-01T00:00:00+00:00', '{}'))
        compare(db)
        assert len(list(TeachingQueries(db).kana_first_primary_events(KINDS))) == 6
