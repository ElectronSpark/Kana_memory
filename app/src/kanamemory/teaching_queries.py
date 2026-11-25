"""Owned, uncached read projections for first-teaching dispatch and the study clock.

Every query runs on StudyStore's existing connection, including inside its
transactions. Iterators keep the original lazy row consumption and ordering;
consume them on the owning thread before the store closes. These projections
never start/commit transactions, create connections or change scheduling policy.
"""
from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import datetime
import json
import sqlite3


class TeachingQueries:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def choose_lane(self, kana_priority, course_priority, *, course_started=False, inside=None, kana_started=False):
        """Use the existing lazy cursor read only when two new lanes compete.

        ``inside``: new_dispatch.choose_lane's focus-scope membership of the two heads, or None;
        ``kana_started``: the kana head is an opened teaching block (only read with ``inside``).
        """
        from .new_dispatch import choose_lane
        if inside is None:
            return choose_lane(self._connection, kana_priority, course_priority,
                               course_started=course_started)
        return choose_lane(self._connection, kana_priority, course_priority,
                           course_started=course_started, inside=inside, kana_started=kana_started)

    def explicit_entry_alive(self, entry: dict) -> bool:
        from .explicit_learning_start import _alive
        return _alive(self._connection, entry)

    def explicit_request_alive(self, request: dict) -> bool:
        from .explicit_learning_start import _request_alive
        return _request_alive(self._connection, request)

    def unanswered_course_question(self, question_id: str) -> str | None:
        """Content id of a course question that still exists, is pending and has no result; else None."""
        row = self._connection.execute('''SELECT u.content_id FROM learning_question q
            JOIN learning_unit u ON u.unit_id=q.unit_id
            WHERE q.question_id=? AND q.status='pending' AND q.result_json IS NULL''', (question_id,)).fetchone()
        return None if row is None else row[0]

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

    def due_review_candidates(self, course: bool, since: datetime, now: datetime,
                              *, newest_first: bool = False) -> Iterator[sqlite3.Row]:
        """Finalized primary logs in [since, now]; same row set either order.

        newest_first walks the table backwards by rowid (no sort), so a capped
        counter consuming this lazy iterator stops near the present instead of
        decoding the oldest history first.
        """
        table, log, suffix = (
            ('learning_event', 'result_json', " AND role='primary'") if course else
            ('review_event', 'review_log_json',
             " AND NOT EXISTS (SELECT 1 FROM study_card c WHERE c.card_id=e.card_id AND c.skill IN ('word_kana','word_meaning_kana','word_romaji'))"))
        finalized = f"COALESCE(json_extract({log},'$.finalized_at_utc'),occurred_at_utc)"
        return (row for row in self._connection.execute(f'''SELECT occurred_at_utc,state_before_json,
        {finalized} AS finalized_at FROM {table} e
        WHERE julianday({finalized})>=julianday(?)
        AND julianday({finalized})<=julianday(?){suffix}{' ORDER BY e.rowid DESC' if newest_first else ''}''',
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
        return (row for row in self._connection.execute('''SELECT c.content_id,c.script,c.skill,c.scheduler_state_json
        FROM study_card c WHERE c.review_count>0 AND NOT EXISTS (
            SELECT 1 FROM review_event e WHERE e.card_id=c.card_id)'''))

    # Study-clock reads (design 12 B6). The moment of an answer is the one the
    # dispatch probes above use: COALESCE(finalized_at_utc, occurred_at_utc)
    # for course and kana formal logs; word-spelling attempts have only
    # occurred_at_utc. Rows come newest first with a caller-chosen row limit.
    # ``after`` (a rowid) reads only rows appended since an earlier read, in
    # rowid order with the rowid as the access path (the time term is only a
    # residual filter, so the time index is not used to scan the lookback):
    # the answer logs are append-only while the app runs; clock_log_tail and
    # clock_log_holds let the caller see when that did not hold (import, reset).

    _CLOCK_LOGS = {'learning_event': 'event_id', 'review_event': 'event_id', 'word_spelling_attempt': 'attempt_id',
                   'learning_evidence': 'event_id'}
    # Evidence event ids of consolidation practice answers (design 12 N5; arrangement.sources.practice_moves).
    CONSOLIDATION_EVENTS = 'practice:consolidation:*'


    @staticmethod
    def _clock_range(rowid: str, at: str, since: datetime, after: int | None) -> tuple[str, str, tuple]:
        """WHERE terms, ORDER BY and their values: newest first by the time index, or only rows after a rowid."""
        if after is None:
            return f'julianday({at})>=julianday(?)', f'julianday({at}) DESC,{rowid} DESC', (since.isoformat(),)
        # Unary plus: the time term cannot use the expression index.
        return f'{rowid}>? AND +julianday({at})>=julianday(?)', rowid, (int(after), since.isoformat())

    def clock_course_events(self, role: str, since: datetime, limit: int,
                            after: int | None = None) -> Iterator[sqlite3.Row]:
        """Course answers of one role ('primary' or 'unresolved') at or after ``since``.

        Columns: row (rowid), unit_id, at (the finalized instant). Newest
        first, at most ``limit``; with ``after`` only rows whose rowid is
        greater, in rowid order.
        """
        if role not in ('primary', 'unresolved'):
            raise ValueError('Unknown course event role')
        finalized = "COALESCE(json_extract(result_json,'$.finalized_at_utc'),occurred_at_utc)"
        where, order, values = self._clock_range('rowid', finalized, since, after)
        return (row for row in self._connection.execute(f'''SELECT rowid AS row,unit_id,{finalized} AS at
            FROM learning_event WHERE {where} AND role=?
            ORDER BY {order} LIMIT ?''', (*values, role, limit)))

    def clock_kana_events(self, since: datetime, limit: int, after: int | None = None) -> Iterator[sqlite3.Row]:
        """Kana formal answers (review_event) at or after ``since``, word-skill cards included.

        Columns: row (rowid), content_id, script, card_skill, skill (the skill
        the answer was routed through), at (the finalized instant). Newest
        first, at most ``limit``; with ``after`` only rows whose rowid is
        greater, in rowid order.
        """
        finalized = "COALESCE(json_extract(e.review_log_json,'$.finalized_at_utc'),e.occurred_at_utc)"
        where, order, values = self._clock_range('e.rowid', finalized, since, after)
        return (row for row in self._connection.execute(f'''SELECT e.rowid AS row,c.content_id,c.script,
            c.skill AS card_skill,COALESCE(json_extract(e.review_log_json,'$.ability_route.skill'),c.skill) AS skill,
            {finalized} AS at
            FROM review_event e JOIN study_card c USING(card_id)
            WHERE {where} ORDER BY {order} LIMIT ?''', (*values, limit)))

    def clock_word_attempts(self, since: datetime, limit: int, after: int | None = None) -> Iterator[sqlite3.Row]:
        """Auxiliary word-spelling answers at or after ``since``.

        Columns: row (rowid), content_id, script, skill, at (occurred_at_utc).
        Newest first, at most ``limit``; with ``after`` only rows whose rowid
        is greater, in rowid order.
        """
        where, order, values = self._clock_range('rowid', 'occurred_at_utc', since, after)
        return (row for row in self._connection.execute(f'''SELECT rowid AS row,content_id,script,skill,
            occurred_at_utc AS at
            FROM word_spelling_attempt WHERE {where} ORDER BY {order} LIMIT ?''', (*values, limit)))

    def clock_consolidation_events(self, since: datetime, limit: int,
                                   after: int | None = None) -> Iterator[sqlite3.Row]:
        """Consolidation practice answers (design 12 N5: evidence rows of role exposure whose event id
        starts with practice:consolidation:) at or after ``since``.

        Columns: row (rowid), target_id, ability, kind, at (occurred_at_utc).
        Newest first, at most ``limit``; with ``after`` only rows whose rowid
        is greater, in rowid order.
        """
        where, order, values = self._clock_range('rowid', 'occurred_at_utc', since, after)
        return (row for row in self._connection.execute(f'''SELECT rowid AS row,target_id,ability,kind,
            occurred_at_utc AS at FROM learning_evidence
            WHERE {where} AND event_id GLOB ? AND source='course' AND role='exposure'
            ORDER BY {order} LIMIT ?''', (*values, self.CONSOLIDATION_EVENTS, limit)))

    def latest_practice_exposure(self, target_id: str, ability: str) -> sqlite3.Row | None:
        """The latest practice answer (an extra-practice or consolidation deck row, event id practice:...) of one
        course unit: kind, variant (of its frozen question), at; None without one."""
        return self._connection.execute('''SELECT kind,json_extract(snapshot_json,'$.assessment_question.variant') AS variant,
            occurred_at_utc AS at FROM learning_evidence WHERE target_id=? AND ability=? AND source='course'
            AND role='exposure' AND event_id GLOB 'practice:*'
            ORDER BY julianday(occurred_at_utc) DESC,rowid DESC LIMIT 1''', (target_id, ability)).fetchone()

    def consolidation_exposures(self, since: datetime) -> Iterator[sqlite3.Row]:
        """Every consolidation practice answer at or after ``since`` (the N5 count and last exposure).

        Columns: target_id, ability, kind, at (occurred_at_utc), oldest first.
        """
        return (row for row in self._connection.execute('''SELECT target_id,ability,kind,occurred_at_utc AS at
            FROM learning_evidence WHERE event_id GLOB ? AND source='course' AND role='exposure'
            AND julianday(occurred_at_utc)>=julianday(?) ORDER BY julianday(occurred_at_utc),rowid''',
            (self.CONSOLIDATION_EVENTS, since.isoformat())))

    def clock_log_tail(self, log: str) -> tuple[int, str] | None:
        """(rowid, id) of the newest row of one answer log, or None when it is empty."""
        row = self._connection.execute(
            f'SELECT rowid,{self._CLOCK_LOGS[log]} FROM {log} ORDER BY rowid DESC LIMIT 1').fetchone()
        return None if row is None else (row[0], row[1])

    def clock_log_holds(self, log: str, tail: tuple[int, str]) -> bool:
        """Whether a tail returned by clock_log_tail is still the same row (not removed or replaced)."""
        return self._connection.execute(
            f'SELECT 1 FROM {log} WHERE rowid=? AND {self._CLOCK_LOGS[log]}=?', tail).fetchone() is not None

    def course_first_ratings(self, unit_ids: Sequence[str]) -> Iterator[sqlite3.Row]:
        """The two earliest primary answers ever of each given course unit.

        Columns: unit_id, at (finalized instant), occurred (the answer's own
        instant, the one its review is scheduled at), n (1 or 2), last_review
        (the scheduling state's last review before that answer, or NULL).
        Exact instants are compared by the caller; SQL orders by julianday,
        then answer and rowid.
        """
        finalized = "COALESCE(json_extract(result_json,'$.finalized_at_utc'),occurred_at_utc)"
        return (row for row in self._connection.execute(f'''SELECT unit_id,at,occurred,n,last_review FROM (
            SELECT unit_id,{finalized} AS at,occurred_at_utc AS occurred,
                json_extract(state_before_json,'$.last_review') AS last_review,
                ROW_NUMBER() OVER (PARTITION BY unit_id
                ORDER BY julianday({finalized}),julianday(occurred_at_utc),rowid) AS n
            FROM learning_event WHERE role='primary' AND unit_id IN (SELECT value FROM json_each(?)))
            WHERE n<=2''', (json.dumps(list(unit_ids)),)))

    def course_earliest_answers(self, unit_ids: Sequence[str]) -> Iterator[sqlite3.Row]:
        """The earliest primary answer of each given course unit, by an index seek per unit.

        Columns: unit_id, at (finalized instant; NULL without a primary answer).
        Ordered by julianday through learning_teaching_primary_final, so the
        cost does not grow with the unit's history.
        """
        finalized = "COALESCE(json_extract(e.result_json,'$.finalized_at_utc'),e.occurred_at_utc)"
        return (row for row in self._connection.execute(f'''SELECT u.value AS unit_id,
            (SELECT {finalized} FROM learning_event e WHERE e.unit_id=u.value AND e.role='primary'
                ORDER BY julianday({finalized}) LIMIT 1) AS at
            FROM json_each(?) u''', (json.dumps(list(unit_ids)),)))

    def kana_card_earliest_answers(self, content_ids: Sequence[str],
                                   excluded_skills: Sequence[str]) -> Iterator[sqlite3.Row]:
        """The earliest formal answer of each card of these contents, by an index seek per card.

        Cards of ``excluded_skills`` are left out. Columns: content_id, script,
        skill (the skill that answer was routed through), at (its finalized
        instant). Uses reviews_card_first_finalized, so the cost does not grow
        with the card's history.
        """
        finalized = "COALESCE(json_extract(p.review_log_json,'$.finalized_at_utc'),p.occurred_at_utc)"
        return (row for row in self._connection.execute(f'''SELECT c.content_id,c.script,
            COALESCE(json_extract(e.review_log_json,'$.ability_route.skill'),c.skill) AS skill,
            COALESCE(json_extract(e.review_log_json,'$.finalized_at_utc'),e.occurred_at_utc) AS at
            FROM study_card c JOIN review_event e ON e.rowid=(
                SELECT p.rowid FROM review_event p WHERE p.card_id=c.card_id ORDER BY julianday({finalized}) LIMIT 1)
            WHERE c.content_id IN (SELECT value FROM json_each(?))
                AND c.skill NOT IN (SELECT value FROM json_each(?))''',
            (json.dumps(list(content_ids)), json.dumps(sorted(excluded_skills)))))

    def kana_first_ratings(self, content_ids: Sequence[str], excluded_skills: Sequence[str]) -> Iterator[sqlite3.Row]:
        """The two earliest formal answers ever per (content, script, routed skill, card).

        Cards of ``excluded_skills`` (the word skills) are left out. Columns:
        content_id, script, skill, at (finalized instant), occurred (the
        answer's own instant), n (1 or 2), last_review (the card state's last
        review before that answer, or NULL). Several skills and cards share
        one ability; the caller merges them (the two earliest of a union are
        among the two earliest of each part), and every card's earliest answer
        is among the rows.
        """
        finalized = "COALESCE(json_extract(e.review_log_json,'$.finalized_at_utc'),e.occurred_at_utc)"
        skill = "COALESCE(json_extract(e.review_log_json,'$.ability_route.skill'),c.skill)"
        return (row for row in self._connection.execute(f'''SELECT content_id,script,skill,at,occurred,n,last_review FROM (
            SELECT c.content_id,c.script,{skill} AS skill,{finalized} AS at,e.occurred_at_utc AS occurred,
                json_extract(e.state_before_json,'$.last_review') AS last_review,
                ROW_NUMBER() OVER (PARTITION BY c.content_id,c.script,{skill},c.card_id
                    ORDER BY julianday({finalized}),julianday(e.occurred_at_utc),e.rowid) AS n
            FROM study_card c JOIN review_event e ON e.card_id=c.card_id
            WHERE c.content_id IN (SELECT value FROM json_each(?))
                AND c.skill NOT IN (SELECT value FROM json_each(?)))
            WHERE n<=2''', (json.dumps(list(content_ids)), json.dumps(sorted(excluded_skills)))))

    def kana_attempts_between(self, start: datetime, now: datetime) -> Iterator[sqlite3.Row]:
        return (row for row in self._connection.execute('''SELECT attempt_id,content_id,skill,script,occurred_at_utc
        FROM practice_attempt WHERE julianday(occurred_at_utc)>=julianday(?)
            AND julianday(occurred_at_utc)<=julianday(?)''', (start.isoformat(),now.isoformat())))

    def course_unit_contents(self, unit_ids: Sequence[str]) -> Iterator[sqlite3.Row]:
        """Columns unit_id, content_id of the given course units that exist (primary-key lookups)."""
        return (row for row in self._connection.execute('''SELECT u.unit_id,u.content_id
            FROM learning_unit u WHERE u.unit_id IN (SELECT value FROM json_each(?))''',
            (json.dumps(list(unit_ids)),)))

    def course_content_first_answers(self, content_ids: Sequence[str]) -> Iterator[sqlite3.Row]:
        """The earliest primary answer of each unit of these lessons, by an index seek per unit.

        Every ability and scope of a lesson has its own unit. Columns:
        content_id, at (finalized instant), last_review (that answer's
        scheduling state's last review before it, or NULL). Units without a
        primary answer are left out. Units through UNIQUE(content_id,ability),
        the answer through learning_teaching_primary_final, so the cost does
        not grow with the history (design 12 UI2, UI4: when a lesson was first
        learned).
        """
        finalized = "COALESCE(json_extract(p.result_json,'$.finalized_at_utc'),p.occurred_at_utc)"
        return (row for row in self._connection.execute(f'''SELECT u.content_id,
            COALESCE(json_extract(e.result_json,'$.finalized_at_utc'),e.occurred_at_utc) AS at,
            json_extract(e.state_before_json,'$.last_review') AS last_review
            FROM learning_unit u JOIN learning_event e ON e.rowid=(
                SELECT p.rowid FROM learning_event p WHERE p.unit_id=u.unit_id AND p.role='primary'
                ORDER BY julianday({finalized}) LIMIT 1)
            WHERE u.content_id IN (SELECT value FROM json_each(?))''', (json.dumps(list(content_ids)),)))

    def kana_card_first_answers(self, content_ids: Sequence[str],
                                excluded_skills: Sequence[str]) -> Iterator[sqlite3.Row]:
        """Every card of these contents with its earliest formal answer, by an index seek per card.

        Cards of ``excluded_skills`` are left out. Columns: content_id, script,
        reviews (the card's review_count), at (the earliest answer's finalized
        instant; NULL when the card has no logged answer), last_review (that
        answer's card state's last review before it, or NULL). Cards through
        UNIQUE(content_id,skill,script), the answer through
        reviews_card_first_finalized (design 12 UI2, UI4: when a kana was
        first learned).
        """
        finalized = "COALESCE(json_extract(p.review_log_json,'$.finalized_at_utc'),p.occurred_at_utc)"
        return (row for row in self._connection.execute(f'''SELECT c.content_id,c.script,c.review_count AS reviews,
            COALESCE(json_extract(e.review_log_json,'$.finalized_at_utc'),e.occurred_at_utc) AS at,
            json_extract(e.state_before_json,'$.last_review') AS last_review
            FROM study_card c LEFT JOIN review_event e ON e.rowid=(
                SELECT p.rowid FROM review_event p WHERE p.card_id=c.card_id ORDER BY julianday({finalized}) LIMIT 1)
            WHERE c.content_id IN (SELECT value FROM json_each(?))
                AND c.skill NOT IN (SELECT value FROM json_each(?))''',
            (json.dumps(list(content_ids)), json.dumps(sorted(excluded_skills)))))
