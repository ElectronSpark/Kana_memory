"""Owned, uncached read projections for first-teaching dispatch.

Every query runs on StudyStore's existing connection, including inside its
transactions. Iterators keep the original lazy row consumption and ordering;
consume them on the owning thread before the store closes. These projections
never start/commit transactions, create connections or change scheduling policy.
"""
from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import datetime
import sqlite3


class TeachingQueries:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def choose_lane(self, kana_priority, course_priority, *, course_started=False):
        """Use the existing lazy cursor read only when two new lanes compete."""
        from .new_dispatch import choose_lane
        return choose_lane(self._connection, kana_priority, course_priority,
                           course_started=course_started)

    def explicit_entry_alive(self, entry: dict) -> bool:
        from .explicit_learning_start import _alive
        return _alive(self._connection, entry)

    def explicit_request_alive(self, request: dict) -> bool:
        from .explicit_learning_start import _request_alive
        return _request_alive(self._connection, request)

    def ability_states(self) -> dict:
        from .ability_teaching import state_map
        return state_map(self._connection)

    def ability_first_events(self, states):
        from .ability_teaching import first_events
        return first_events(self._connection, states)

    def instruction_rows(self, states=None):
        from .ability_teaching import rows
        return rows(self._connection, states, teaching_only=True)

    def ability_scope(self, content_id: str, ability: str) -> str | None:
        from .ability_teaching import scope_for
        return scope_for(self._connection, content_id, ability)

    def course_first_primary_events(self) -> Iterator[sqlite3.Row]:
        """Indexed candidates equivalent to the original all-history grouped CTE.

        A per-unit minimum can be read from small partial expression indexes.
        Separating the equal-time bucket lets SQLite seek both branches instead
        of loading every historical result JSON. CROSS JOIN deliberately starts
        from the guided subset; it does not change the relational result.
        Exact datetime checks below still resolve offset and sub-ms boundaries.
        """
        return (row for row in self._connection.execute('''WITH first_times AS (
            SELECT u.content_id,c.guided_at_utc,MIN((
                SELECT MIN(julianday(COALESCE(json_extract(e.result_json,'$.finalized_at_utc'),e.occurred_at_utc)))
                FROM learning_event e WHERE e.role='primary' AND e.unit_id=u.unit_id
                    AND julianday(e.occurred_at_utc)>julianday(c.guided_at_utc))) AS first_jd
            FROM learning_content c CROSS JOIN learning_unit u ON u.content_id=c.content_id
            WHERE c.guided_at_utc IS NOT NULL GROUP BY u.content_id)
            SELECT u.content_id,COALESCE(json_extract(e.result_json,'$.finalized_at_utc'),e.occurred_at_utc) AS finalized_at,
                e.occurred_at_utc,c.guided_at_utc
            FROM learning_content c CROSS JOIN learning_unit u ON u.content_id=c.content_id
                CROSS JOIN learning_event e ON e.unit_id=u.unit_id
            WHERE e.role='primary' AND c.guided_at_utc IS NOT NULL
                AND julianday(e.occurred_at_utc)=julianday(c.guided_at_utc)
            UNION ALL
            SELECT f.content_id,COALESCE(json_extract(e.result_json,'$.finalized_at_utc'),e.occurred_at_utc) AS finalized_at,
                e.occurred_at_utc,f.guided_at_utc
            FROM first_times f JOIN learning_unit u ON u.content_id=f.content_id
                JOIN learning_event e ON e.unit_id=u.unit_id
            WHERE e.role='primary' AND julianday(e.occurred_at_utc)>julianday(f.guided_at_utc)
                AND julianday(COALESCE(json_extract(e.result_json,'$.finalized_at_utc'),e.occurred_at_utc))=f.first_jd'''))

    def unscored_course_events(self, start: datetime, now: datetime) -> Iterator[sqlite3.Row]:
        return (row for row in self._connection.execute('''SELECT u.content_id,u.ability,e.occurred_at_utc,e.result_json
            FROM learning_event e JOIN learning_unit u ON u.unit_id=e.unit_id
            WHERE e.role='unresolved' AND e.outcome IN ('unresolved','device_failure','invalid','cancelled')
                AND julianday(COALESCE(json_extract(e.result_json,'$.finalized_at_utc'),e.occurred_at_utc))>=julianday(?)
                AND julianday(COALESCE(json_extract(e.result_json,'$.finalized_at_utc'),e.occurred_at_utc))<=julianday(?)''',
            (start.isoformat(), now.isoformat())))

    def kana_first_primary_events(self, kinds: Sequence[str]) -> Iterator[sqlite3.Row]:
        """Seek each card's earliest bucket; preserve all equal-time slot events."""
        marks = ','.join('?' for _ in kinds)
        return (row for row in self._connection.execute(f'''WITH first_times AS (
            SELECT c.content_id,c.script,MIN((
                SELECT MIN(julianday(COALESCE(json_extract(e.review_log_json,'$.finalized_at_utc'),e.occurred_at_utc)))
                FROM review_event e WHERE e.card_id=c.card_id)) AS first_jd
            FROM study_card c WHERE c.skill IN ({marks}) GROUP BY c.content_id,c.script)
            SELECT c.content_id,c.script,COALESCE(json_extract(e.review_log_json,'$.finalized_at_utc'),e.occurred_at_utc) AS finalized_at
            FROM first_times f CROSS JOIN study_card c ON c.content_id=f.content_id AND c.script=f.script
                CROSS JOIN review_event e ON e.card_id=c.card_id
            WHERE c.skill IN ({marks})
                AND julianday(COALESCE(json_extract(e.review_log_json,'$.finalized_at_utc'),e.occurred_at_utc))=f.first_jd''',
            (*kinds, *kinds)))

    def due_review_candidates(self, course: bool, since: datetime, now: datetime) -> Iterator[sqlite3.Row]:
        table, log, suffix = (
            ('learning_event', 'result_json', " AND role='primary'") if course else
            ('review_event', 'review_log_json',
             " AND NOT EXISTS (SELECT 1 FROM study_card c WHERE c.card_id=e.card_id AND c.skill IN ('word_kana','word_meaning_kana','word_romaji'))"))
        finalized = f"COALESCE(json_extract({log},'$.finalized_at_utc'),occurred_at_utc)"
        return (row for row in self._connection.execute(f'''SELECT occurred_at_utc,state_before_json,
        {finalized} AS finalized_at FROM {table} e
        WHERE julianday({finalized})>=julianday(?)
        AND julianday({finalized})<=julianday(?){suffix}''',
        (since.isoformat(), now.isoformat())))

    def first_course_ability_events(self) -> Iterator[sqlite3.Row]:
        return (row for row in self._connection.execute('''SELECT u.unit_id,u.content_id,e.rowid AS event_row,
        e.occurred_at_utc,e.result_json FROM learning_unit u CROSS JOIN learning_event e
        ON e.unit_id=u.unit_id AND e.role='primary'
        AND julianday(COALESCE(json_extract(e.result_json,'$.finalized_at_utc'),e.occurred_at_utc))=(
            SELECT MIN(julianday(COALESCE(json_extract(p.result_json,'$.finalized_at_utc'),p.occurred_at_utc)))
            FROM learning_event p WHERE p.unit_id=u.unit_id AND p.role='primary')
        WHERE u.review_count>0'''))

    def unassessed_course_units(self) -> Iterator[sqlite3.Row]:
        return (row for row in self._connection.execute('''SELECT u.unit_id,u.content_id,u.ability,c.stage
        FROM learning_unit u JOIN learning_content c USING(content_id)
        WHERE u.review_count=0 AND NOT EXISTS (
            SELECT 1 FROM learning_event e WHERE e.unit_id=u.unit_id AND e.role='primary')'''))

    def kana_attempts_by_kind(self, kinds: Sequence[str]) -> Iterator[sqlite3.Row]:
        marks = ','.join('?' for _ in kinds)
        return (row for row in self._connection.execute(f'''SELECT attempt_id,content_id,skill,script,occurred_at_utc
        FROM practice_attempt WHERE skill IN ({marks})''',kinds))

    def first_direction_candidates(self, kinds: Sequence[str], now: datetime) -> Iterator[sqlite3.Row]:
        marks = ','.join('?' for _ in kinds)
        mode_values = ','.join('(?)' for _ in kinds)
        return (row for row in self._connection.execute(f'''WITH modes(skill) AS (VALUES {mode_values}),
        first_rows AS (SELECT c.content_id,c.script,m.skill,
            (SELECT p.rowid FROM review_event p WHERE p.card_id=c.card_id
                AND COALESCE(json_extract(p.review_log_json,'$.ability_route.skill'),c.skill)=m.skill
                AND julianday(p.occurred_at_utc)<=julianday(?)
                AND julianday(COALESCE(json_extract(p.review_log_json,'$.finalized_at_utc'),p.occurred_at_utc))<=julianday(?)
                ORDER BY julianday(COALESCE(json_extract(p.review_log_json,'$.finalized_at_utc'),p.occurred_at_utc)),
                    julianday(p.occurred_at_utc),p.rowid LIMIT 1) AS event_row
            FROM study_card c CROSS JOIN modes m WHERE c.skill IN ({marks}))
        SELECT f.content_id,f.skill,f.script,e.rowid AS ordinal,e.occurred_at_utc,e.review_log_json
        FROM first_rows f JOIN review_event e ON e.rowid=f.event_row''',
        (*kinds,now.isoformat(),now.isoformat(),*kinds)))

    def reviewed_kana_states(self) -> Iterator[sqlite3.Row]:
        return (row for row in self._connection.execute('SELECT content_id,script,scheduler_state_json FROM study_card WHERE review_count>0'))

    def legacy_kana_states_without_events(self) -> Iterator[sqlite3.Row]:
        return (row for row in self._connection.execute('''SELECT c.content_id,c.script,c.scheduler_state_json
        FROM study_card c WHERE c.review_count>0 AND NOT EXISTS (
            SELECT 1 FROM review_event e WHERE e.card_id=c.card_id)'''))

    def kana_attempts_between(self, start: datetime, now: datetime) -> Iterator[sqlite3.Row]:
        return (row for row in self._connection.execute('''SELECT attempt_id,content_id,skill,script,occurred_at_utc
        FROM practice_attempt WHERE julianday(occurred_at_utc)>=julianday(?)
            AND julianday(occurred_at_utc)<=julianday(?)''', (start.isoformat(),now.isoformat())))
