"""Adding the finalized-feedback index changes access paths, never evidence."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import sys

from kanamemory.domain import StudyStore

NOW=datetime(2027,6,1,12,tzinfo=timezone.utc)


def rows(db):
    tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {name:[tuple(r) for r in db.execute('SELECT * FROM '+name+' ORDER BY rowid')] for name in tables}


def test_old_database_adds_exact_feedback_index_without_changing_any_business_row(tmp_path):
    with StudyStore(tmp_path,time_zone='UTC') as store:
        store.record_review('a','kana_choice','hiragana',3,now=NOW-timedelta(days=3),
                            finalized_at=NOW-timedelta(seconds=10),outcome='correct')
        # A later legacy event has no finalization marker: the same earlier
        # marked feedback must still govern its remaining exposure delay.
        store.record_review('a','kana_choice','hiragana',3,now=NOW-timedelta(days=1),outcome='correct')
        store.record_review('i','kana_choice','hiragana',3,now=NOW-timedelta(days=1),outcome='correct')
        store._db.execute('DROP INDEX reviews_finalized_card')
        before=rows(store._db)
        identities=[('a','kana_choice','hiragana'),('a','kana_romaji','hiragana'),
                    ('i','kana_choice','hiragana')]
        expected=store.feedback_wait_times(identities,NOW)
        assert expected=={key:NOW+timedelta(seconds=50) for key in identities[:2]}
        # A version 1 database always has its indexes, and reopening one skips
        # the DDL. The one old shape that gets indexes added is the aggregate
        # session column with no version marker, as record_schema converts it.
        store._db.execute('DROP TABLE learner_record_meta')
        store._db.execute('DROP TABLE session_question')
        store._db.execute("ALTER TABLE study_session ADD COLUMN question_snapshot_json TEXT NOT NULL DEFAULT '[]'")
    with StudyStore(tmp_path,time_zone='UTC') as restored:
        assert restored.record_upgrade['converted'] is True
        assert rows(restored._db)==before
        assert restored.feedback_wait_times(identities,NOW)==expected
        card=restored.get_card('a','kana_choice','hiragana')
        query="SELECT MAX(previous.rowid) FROM review_event previous WHERE previous.card_id=? AND instr(previous.review_log_json, '\"finalized_at_utc\"')>0"
        plan=' '.join(str(r[-1]) for r in restored._db.execute('EXPLAIN QUERY PLAN '+query,(card['card_id'],)))
        assert 'reviews_finalized_card' in plan
        assert restored.feedback_wait_times(identities,NOW+timedelta(seconds=51))=={}
