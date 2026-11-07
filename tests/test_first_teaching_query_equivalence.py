"""Compare optimized reads with the former production query, not a mock policy."""
from datetime import datetime, timedelta, timezone
import json
import random

import pytest
from kanamemory import first_teaching_dispatch as dispatch
from kanamemory.domain import StudyStore
from kanamemory.teaching_queries import TeachingQueries
from kanamemory.learning_store import LearningStore
from support.learning_store_fixtures import item, question

NOW = datetime(2027, 3, 14, 7, tzinfo=timezone.utc)
OLD_PRIMARY = '''WITH timed_events AS (
 SELECT u.content_id,COALESCE(json_extract(e.result_json,'$.finalized_at_utc'),
 e.occurred_at_utc) AS finalized_at,e.occurred_at_utc,c.guided_at_utc
 FROM learning_event e JOIN learning_unit u ON u.unit_id=e.unit_id
 JOIN learning_content c ON c.content_id=u.content_id WHERE e.role='primary'),
 first_times AS (SELECT content_id,MIN(CASE
 WHEN julianday(occurred_at_utc)>julianday(guided_at_utc)
 THEN julianday(finalized_at) END) AS first_jd
 FROM timed_events GROUP BY content_id)
 SELECT e.content_id,e.finalized_at,e.occurred_at_utc,e.guided_at_utc
 FROM timed_events e JOIN first_times f ON f.content_id=e.content_id
 WHERE julianday(e.occurred_at_utc)=julianday(e.guided_at_utc)
 OR (julianday(e.occurred_at_utc)>julianday(e.guided_at_utc)
 AND julianday(e.finalized_at)=f.first_jd)'''
OLD_UNSCORED = '''SELECT u.content_id,u.ability,e.occurred_at_utc,e.result_json
 FROM learning_event e JOIN learning_unit u ON u.unit_id=e.unit_id
 WHERE e.role='unresolved' AND e.outcome IN ('unresolved','device_failure','invalid','cancelled')'''


def seed(store):
    learning = LearningStore(store)
    start = NOW-timedelta(days=10)
    for n in range(6):
        cid = f'query-content-{n}'
        learning.enroll(item(cid), now=start)
        learning.mark_instruction_exposed(cid, now=start)
        learning.advance_instruction(cid, 'intro_done', now=start)
        learning.advance_instruction(cid, 'guide_correct', now=start)
        for k in range(6):
            at = start+timedelta(days=k+1)
            q = question(cid, qid=f'{cid}-{k}', ability=('understanding' if k % 2 else 'application'))
            learning.append_questions(q, now=at)
            unresolved = k == 3
            learning.submit(q['id'], '' if unresolved else q['answer'], None if unresolved else True,
                            outcome='device_failure' if unresolved else None, now=at)
            learning.finalize(q['id'], None if unresolved else 1, now=at+timedelta(seconds=4))
    # Untaught and partially taught content must not invent a first rating.
    learning.enroll(item('untaught'), now=NOW)
    learning.enroll(item('guide-without-answer'), now=NOW-timedelta(minutes=3))
    learning.mark_instruction_exposed('guide-without-answer', now=NOW-timedelta(minutes=3))
    learning.advance_instruction('guide-without-answer', 'intro_done', now=NOW-timedelta(minutes=2))
    learning.advance_instruction('guide-without-answer', 'guide_correct', now=NOW-timedelta(minutes=1))
    return learning


def same_service(store, monkeypatch, now):
    before = tuple(store._db.iterdump())
    actual_rows = sorted(tuple(row) for row in list(store.teaching_queries.course_first_primary_events()))
    assert actual_rows == sorted(tuple(row) for row in store._db.execute(OLD_PRIMARY))
    actual = dispatch._teaching_service(store, now)
    with monkeypatch.context() as context:
        context.setattr(TeachingQueries, 'course_first_primary_events', lambda self: self._connection.execute(OLD_PRIMARY))
        context.setattr(TeachingQueries, 'unscored_course_events', lambda self, start, now: self._connection.execute(OLD_UNSCORED))
        expected = dispatch._teaching_service(store, now)
    assert actual == expected
    assert tuple(store._db.iterdump()) == before


@pytest.mark.parametrize('zone', ['UTC', 'America/Toronto'])
def test_actual_history_reopen_and_restore_equal_original_query(tmp_path, monkeypatch, zone):
    path = tmp_path/'learner'
    with StudyStore(path, time_zone=zone) as store:
        seed(store)
        for now in (NOW-timedelta(days=7), NOW, NOW+timedelta(days=1)):
            same_service(store, monkeypatch, now)
        payload = store.export_backup()
    with StudyStore(path, time_zone=zone) as reopened:
        same_service(reopened, monkeypatch, NOW)
    with StudyStore(tmp_path/'restored', time_zone=zone) as restored:
        restored.import_backup(payload)
        same_service(restored, monkeypatch, NOW)


@pytest.mark.parametrize('seed_number', [7, 19, 113])
def test_offsets_submillisecond_reteaching_future_and_replayed_row_order(tmp_path, monkeypatch, seed_number):
    rng = random.Random(seed_number)
    with StudyStore(tmp_path/'learner', time_zone='America/Toronto') as store:
        seed(store)
        guides = {}
        with store._db:
            for n in range(6):
                cid = f'query-content-{n}'
                anchor = NOW+timedelta(days=n % 3-1, microseconds=rng.choice([-100, 0, 100]))
                guides[cid] = anchor
                store._db.execute("UPDATE learning_content SET guided_at_utc=?,stage='check' WHERE content_id=?",
                                  (anchor.isoformat() if n != 5 else None, cid))
            for row in list(store._db.execute('SELECT e.*,u.content_id FROM learning_event e JOIN learning_unit u USING(unit_id)')):
                anchor = guides[row['content_id']]
                at = anchor+timedelta(microseconds=rng.choice([-100, 0, 100, 1000, 60000000]))
                finalized = at+timedelta(microseconds=rng.choice([0, 100, 1000, 86400000000]))
                offset = timezone(timedelta(hours=rng.choice([-12, -5, 0, 9, 14])))
                result = json.loads(row['result_json'])
                if rng.random() < .2:
                    result.pop('finalized_at_utc', None)
                else:
                    result['finalized_at_utc'] = finalized.astimezone(offset).isoformat()
                store._db.execute('UPDATE learning_event SET occurred_at_utc=?,result_json=? WHERE event_id=?',
                    (at.astimezone(offset).isoformat(), json.dumps(result), row['event_id']))
            # Imported databases do not promise original row insertion order.
            records = [dict(row) for row in store._db.execute('SELECT * FROM learning_event')]
            store._db.execute('DELETE FROM learning_event')
            for row in reversed(records):
                store._db.execute('INSERT INTO learning_event VALUES ('+','.join('?' for _ in row)+')',tuple(row.values()))
        for day in (-1, 0, 1, 2):
            for microseconds in (-100, 0, 100):
                same_service(store, monkeypatch, NOW+timedelta(days=day, microseconds=microseconds))
        # Additive indexes optimize reads, but old schemas still return the
        # exact same state before an app initialization creates those indexes.
        with store._db:
            for index in ('learning_teaching_primary_final', 'learning_teaching_primary_occurred',
                          'learning_teaching_guided', 'learning_unresolved_finalized'):
                store._db.execute('DROP INDEX '+index)
        same_service(store, monkeypatch, NOW)
