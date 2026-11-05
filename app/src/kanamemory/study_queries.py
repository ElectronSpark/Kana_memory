"""Named read projections owned by StudyStore's existing SQLite connection.

This reader never opens/closes a connection, starts a transaction, caches rows,
or writes. Call it on the owning thread. Each method documents its filters;
pending headers deliberately differ from eligible pending learning questions.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import datetime
import sqlite3


class StudyQueries:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def has_course_table(self) -> bool:
        return bool(self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='learning_content'").fetchone())

    def has_course_history(self) -> bool:
        tables = {row[0] for row in self._connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('learning_content','learning_unit','learning_event')")}
        return len(tables) == 3

    def taught_course_ids(self, content_ids: Iterable[str]) -> set[str]:
        selected = set(content_ids)
        rows = [r for r in self.course_progress_rows() if r['content_id'] in selected]
        return {r['content_id'] for r in rows} - {r['content_id'] for r in rows if r['stage'] not in ('bridge', 'check', 'done')}

    def contribution_valid(self, identity: str, content_id: str, ability: str) -> bool:
        from .content_history import contribution_valid
        return contribution_valid(self._connection, identity, content_id, ability)

    def course_progress_rows(self) -> list:
        """Every admitted introduction scope; consumers aggregate per content."""
        from .ability_teaching import rows
        return list(rows(self._connection, teaching_only=True))

    def reviewed_kana_rows(self) -> list[sqlite3.Row]:
        """All scored legacy facts, including word skills and suspended cards."""
        return self._connection.execute(
            "SELECT content_id,script,skill FROM study_card WHERE review_count>0").fetchall()

    def course_ids(self) -> tuple[str, ...]:
        """Stable content identities only; no lesson decoding or event counting."""
        return tuple(row[0] for row in self._connection.execute(
            "SELECT content_id FROM learning_content ORDER BY content_id"))

    def unfinished_course_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(row['content_id'] for row in self.course_progress_rows() if row['stage'] != 'done'))

    def active_review_units(self) -> list[sqlite3.Row]:
        """Scored, unsuspended abilities, regardless of pending/reservation state."""
        return self._connection.execute(
            "SELECT * FROM learning_unit WHERE suspended=0 AND review_count>0").fetchall()

    def pending_course_headers(self, enabled: Iterable[str]) -> list[sqlite3.Row]:
        """Visible course queue headers; includes retained diagnostic reservations."""
        kinds = tuple(enabled)
        if not kinds:
            return []
        placeholders = ",".join("?" for _ in kinds)
        return self._connection.execute(f"""SELECT DISTINCT u.content_id,u.ability,c.stage
            FROM learning_question q JOIN learning_unit u ON u.unit_id=q.unit_id
            JOIN learning_content c ON c.content_id=u.content_id
            WHERE q.status='pending' AND u.suspended=0
            AND q.kind IN ({placeholders})""", kinds).fetchall()

    def unqueued_course_ids(self, abilities: Iterable[str]) -> list[sqlite3.Row]:
        """Unsuspended abilities with no pending question of any kind.

        A disabled or reserved pending question still owns that ability's queue
        position. This is intentionally not the public eligible-pending reader.
        """
        abilities = tuple(abilities)
        if not abilities:
            return []
        placeholders = ",".join("?" for _ in abilities)
        return self._connection.execute(f"""SELECT DISTINCT u.content_id
            FROM learning_unit u WHERE u.suspended=0 AND u.ability IN ({placeholders})
            AND NOT EXISTS (SELECT 1 FROM learning_question q
                WHERE q.unit_id=u.unit_id AND q.status='pending')""", abilities).fetchall()

    def pending_abilities(self, content_id: str) -> set[str]:
        """Queue ownership includes paused, reserved and disabled questions."""
        return {row[0] for row in self._connection.execute("""SELECT u.ability
            FROM learning_question q JOIN learning_unit u ON u.unit_id=q.unit_id
            WHERE u.content_id=? AND q.status='pending'""", (content_id,))}

    def instruction_ready_ids(self) -> set[str]:
        return {row[0] for row in self._connection.execute(
            "SELECT DISTINCT content_id FROM learning_unit WHERE instruction_ready=1")}

    def course_count(self) -> int:
        return self._connection.execute("SELECT COUNT(*) FROM learning_content").fetchone()[0]

    def reviewed_course_count(self) -> int:
        return self._connection.execute(
            "SELECT COUNT(DISTINCT content_id) FROM learning_unit WHERE review_count>0").fetchone()[0]

    def course_ids_page(self, limit: int, offset: int) -> tuple[str, ...]:
        """Insertion order, independently of content-id sorting or due dates."""
        return tuple(row[0] for row in self._connection.execute(
            "SELECT content_id FROM learning_content ORDER BY rowid LIMIT ? OFFSET ?", (limit, offset)))

    def course_row(self, content_id: str) -> sqlite3.Row | None:
        """Raw content-level teaching state; no per-ability introduction projection."""
        return self._connection.execute(
            "SELECT * FROM learning_content WHERE content_id=?", (content_id,)).fetchone()

    def course_introduction_header(self, content_id: str) -> sqlite3.Row | None:
        """Small enrollment-transaction view, without loading the lesson payload."""
        return self._connection.execute(
            'SELECT stage,checkpoint_json,introduction_id FROM learning_content WHERE content_id=?',
            (content_id,)).fetchone()

    def latest_primary_rating(self, unit_id: str) -> sqlite3.Row | None:
        return self._connection.execute("""SELECT rating,occurred_at_utc FROM learning_event
            WHERE unit_id=? AND role='primary' ORDER BY rowid DESC LIMIT 1""", (unit_id,)).fetchone()

    def first_pending_question_id(self, unit_id: str) -> str | None:
        """Any retained pending snapshot, without eligibility/reservation filters."""
        row = self._connection.execute(
            "SELECT question_id FROM learning_question WHERE unit_id=? AND status='pending'", (unit_id,)).fetchone()
        return row[0] if row else None

    def diagnostic_scope_rows(self) -> list[sqlite3.Row]:
        return self._connection.execute("""SELECT c.content_id,c.family,
            json_extract(c.content_json,'$.title') AS title,
            json_extract(c.content_json,'$.domain') AS domain,
            SUM(u.review_count) AS reviews,
            COALESCE(AVG(json_extract(u.scheduler_state_json,'$.stability')),0) AS stability
            FROM learning_content c JOIN learning_unit u ON u.content_id=c.content_id
            WHERE u.suspended=0 GROUP BY c.content_id ORDER BY c.introduced_at_utc,c.content_id""").fetchall()

    def recent_evidence_rows(self) -> list[sqlite3.Row]:
        """Bounded raw 64-row tail; callers keep eligibility/retraction policy."""
        from .learning_evidence import tables_present
        if not tables_present(self._connection):
            return []
        return self._connection.execute("""SELECT target_id,eligible,retracted FROM (
            SELECT rowid AS event_order,target_id,eligible,retracted FROM learning_evidence
            ORDER BY rowid DESC LIMIT 64) ORDER BY event_order DESC""").fetchall()

    def unanswered_course_rows(self) -> list[sqlite3.Row]:
        """All unsubmitted retained course drafts, including paused/reserved units."""
        return self._connection.execute("""SELECT q.question_id,u.content_id,q.draft_json,u.ability
            FROM learning_question q JOIN learning_unit u ON u.unit_id=q.unit_id
            WHERE q.status='pending' AND q.result_json IS NULL""").fetchall()

    def course_suspensions(self, content_id: str) -> dict[str, int]:
        return {row['ability']: row['suspended'] for row in self._connection.execute(
            'SELECT ability,suspended FROM learning_unit WHERE content_id=?', (content_id,))}

    def pending_course_modes(self, content_id: str) -> set[tuple[str, str]]:
        return {(row['kind'], row['ability']) for row in self._connection.execute(
            """SELECT q.kind,u.ability FROM learning_question q JOIN learning_unit u USING(unit_id)
            WHERE u.content_id=? AND q.status='pending'""", (content_id,))}

    def assessed_course_candidates(self) -> list[sqlite3.Row]:
        return self._connection.execute("""SELECT c.content_id FROM learning_content c
        WHERE EXISTS (
            SELECT 1 FROM learning_unit u WHERE u.content_id=c.content_id
            AND u.suspended=0 AND u.instruction_ready<>0 AND u.review_count>0
            AND EXISTS (SELECT 1 FROM learning_event e WHERE e.unit_id=u.unit_id
                AND e.role='primary' AND e.assisted=0
                AND e.outcome IN ('correct','incorrect')))
        ORDER BY c.rowid""").fetchall()

    def course_independent_evidence(self) -> Iterator[sqlite3.Row]:
        return (row for row in self._connection.execute("""
        SELECT unit_id,
            SUM(CASE WHEN assisted=0 AND outcome IN ('correct','incorrect') THEN 1 ELSE 0 END) AS attempts,
            SUM(CASE WHEN outcome='correct' AND assisted=0 AND rating>1 THEN 1 ELSE 0 END) AS successes
        FROM learning_event WHERE role='primary' GROUP BY unit_id"""))

    def latest_course_outcomes(self) -> Iterator[sqlite3.Row]:
        return (row for row in self._connection.execute("""
        SELECT unit_id,outcome,assisted,rating FROM learning_event
        WHERE rowid IN (SELECT MAX(rowid) FROM learning_event
                        WHERE role='primary' GROUP BY unit_id)"""))

    def kana_mode_evidence(self) -> Iterator[sqlite3.Row]:
        return (row for row in self._connection.execute("""
        SELECT e.card_id,COALESCE(json_extract(e.review_log_json,'$.ability_route.skill'),c.skill) AS actual_skill,
            COUNT(*) AS event_count,
            SUM(CASE WHEN assisted=0 AND outcome IN ('correct','answered','self_rated')
                AND rating>1 THEN 1 ELSE 0 END) AS success_count,
            SUM(CASE WHEN assisted=0 AND (outcome='incorrect' OR rating=1) THEN 1 ELSE 0 END) AS failure_count,
            SUM(assisted) AS assisted_count,MAX(occurred_at_utc) AS last_review_at_utc
        FROM review_event e JOIN study_card c USING(card_id) GROUP BY e.card_id,actual_skill"""))

    def recent_kana_history(self, content_id: str) -> Iterator[sqlite3.Row]:
        return (row for row in self._connection.execute("""
        SELECT e.event_id,e.session_id,e.card_id,e.occurred_at_utc,e.rating,
            e.answered_text,e.assisted,e.outcome,e.duration_ms,e.algorithm_version,
            c.content_id,COALESCE(json_extract(e.review_log_json,'$.ability_route.skill'),c.skill) AS skill,c.script
        FROM review_event e JOIN study_card c USING(card_id)
        WHERE c.content_id=? AND c.skill NOT IN ('word_kana','word_meaning_kana','word_romaji')
        ORDER BY e.occurred_at_utc DESC,e.rowid DESC LIMIT 20""", (content_id,)))

    def kana_skipped_attempts(self, content_id: str) -> sqlite3.Row | None:
        return self._connection.execute(
        """SELECT COUNT(*) FROM practice_attempt WHERE content_id=?
           AND skill NOT IN ('word_kana','word_meaning_kana','word_romaji')""", (content_id,)).fetchone()

    def independent_prerequisite_rows(self) -> Iterator[sqlite3.Row]:
        return (row for row in self._connection.execute("""SELECT DISTINCT u.content_id
        FROM learning_event e JOIN learning_unit u ON u.unit_id=e.unit_id
        WHERE e.role='primary' AND e.outcome='correct' AND e.assisted=0
        AND e.rating>1 AND u.ability IN ('understanding','application','reading','use')"""))

    def course_has_primary_event(self, cid: str) -> sqlite3.Row | None:
        return self._connection.execute("""SELECT 1
        FROM learning_event e JOIN learning_unit u ON u.unit_id=e.unit_id
        WHERE u.content_id=? AND e.role='primary' LIMIT 1""", (cid,)).fetchone()

    def course_pending_drafts(self, cid: str) -> Iterator[sqlite3.Row]:
        return (row for row in self._connection.execute("""SELECT q.draft_json,q.result_json
        FROM learning_question q JOIN learning_unit u ON u.unit_id=q.unit_id
        WHERE u.content_id=? AND q.status='pending'""", (cid,)))

    def suspended_kana_rows(self) -> Iterator[sqlite3.Row]:
        return (row for row in self._connection.execute(
        "SELECT content_id,skill,script,suspended FROM study_card WHERE suspended=1"))

    def kana_has_review_evidence(self, content_id: str, script: str) -> sqlite3.Row | None:
        return self._connection.execute("""SELECT 1 FROM study_card c WHERE c.content_id=? AND c.script=?
        AND (c.review_count>0 OR EXISTS (SELECT 1 FROM review_event e WHERE e.card_id=c.card_id))
        LIMIT 1""", (content_id, script)).fetchone()

    def kana_has_attempt(self, content_id: str, script: str) -> sqlite3.Row | None:
        return self._connection.execute("SELECT 1 FROM practice_attempt WHERE content_id=? AND script=? LIMIT 1",
        (content_id, script)).fetchone()

    def saved_question_snapshots(self, identity: str) -> Iterator[sqlite3.Row]:
        return (row for row in self._connection.execute("""SELECT json_extract(e.value,'$.question')
        FROM app_setting a, json_each(a.value_json,'$.entries') e
        WHERE (a.key IN ('ai_tutor_v1','ai_practice_v1') OR a.key GLOB 'retention_check_v[12]:*')
        AND json_extract(e.value,'$.question.id')=?""", (identity,)))

    def recent_word_exposures(self, now: datetime, limit: int) -> Iterator[sqlite3.Row]:
        return (row for row in self._connection.execute("""WITH exposures AS (
        SELECT content_id,script,occurred_at_utc,evidence_json FROM word_spelling_attempt
        UNION ALL
        SELECT c.content_id,c.script,e.occurred_at_utc,
            json_extract(CASE WHEN json_valid(e.review_log_json) THEN e.review_log_json ELSE '{}' END,
                '$.word_spelling_evidence') AS evidence_json
        FROM review_event e JOIN study_card c USING(card_id)
        WHERE c.skill IN ('word_kana','word_meaning_kana'))
        SELECT content_id,script,occurred_at_utc,
            json_extract(CASE WHEN json_valid(evidence_json) THEN evidence_json ELSE '{}' END,
                '$.expected') AS expected
        FROM exposures WHERE occurred_at_utc<=?
        ORDER BY occurred_at_utc DESC LIMIT ?""", (now.isoformat(), limit)))

    def first_unfinished_course(self) -> sqlite3.Row | None:
        identities = self.unfinished_course_ids()
        return self._connection.execute('SELECT content_id FROM learning_content WHERE content_id=?',
                                        (identities[0],)).fetchone() if identities else None

    def first_pending_course(self, enabled) -> sqlite3.Row | None:
        return self._connection.execute("""SELECT u.content_id FROM learning_question q
        JOIN learning_unit u USING(unit_id) WHERE q.status='pending' AND u.suspended=0
        AND q.kind IN (""" + ",".join("?" for _ in enabled) + ") ORDER BY q.created_at_utc,q.rowid LIMIT 1",
        enabled).fetchone()

    def review_counts_between(self, course: bool, start: str, end: str, now: datetime) -> Iterator[sqlite3.Row]:
        table, identity = ("learning_event", "unit_id") if course else ("review_event", "card_id")
        primary = " AND role='primary'" if course else ""
        return (row for row in self._connection.execute(f"""SELECT {identity},COUNT(*) FROM {table}
        WHERE occurred_at_utc>=? AND occurred_at_utc<? AND occurred_at_utc<=?{primary}
        GROUP BY {identity}""", (start, end, now.isoformat())))

    def kana_prerequisite_rows(self, kinds) -> Iterator[sqlite3.Row]:
        log = "CASE WHEN json_valid(e.review_log_json) THEN e.review_log_json ELSE '{}' END"
        return (row for row in self._connection.execute(f"""SELECT c.content_id,c.script,
        MAX(EXISTS(SELECT 1 FROM review_event e WHERE e.card_id=c.card_id
            AND e.outcome='correct' AND e.assisted=0 AND e.rating>1
            AND COALESCE(json_extract({log},'$.kana_answer_evidence.accepted_homophone'),0)<>1
            AND json_type({log},'$.spelling_correction') IS NULL)) AS ready
        FROM study_card c
        WHERE c.skill IN ({','.join('?' for _ in kinds)})
            AND c.script IN ('hiragana','katakana')
            AND EXISTS(SELECT 1 FROM review_event assessed WHERE assessed.card_id=c.card_id)
        GROUP BY c.content_id,c.script""", kinds))

    def tutor_material_candidates(self, message: str, terms: Iterable[str], candidate_limit: int) -> list[sqlite3.Row]:
        """Scalar matching against independently attempted, unsuspended content."""
        direct = "((length(title)>=2 AND instr(?,title)>0) OR (length(reading)>=2 AND instr(?,reading)>0) OR instr(?,content_id)>0)"
        params = [message, message, message]
        clauses = [direct]
        for term in terms:
            clauses.append('(instr(title,?)>0 OR reading=? OR content_id=?)')
            params.extend([term, term, term])
        return self._connection.execute("""SELECT c.content_id,c.family,
            json_extract(c.content_json,'$.title') AS title,
            json_extract(c.content_json,'$.reading') AS reading
            FROM learning_content c WHERE ("""
            + ' OR '.join(clauses) + """) AND EXISTS (
                SELECT 1 FROM learning_unit u WHERE u.content_id=c.content_id
                AND u.suspended=0 AND u.instruction_ready=1 AND u.review_count>0
                AND EXISTS (SELECT 1 FROM learning_event e WHERE e.unit_id=u.unit_id
                    AND e.role='primary' AND e.assisted=0 AND e.outcome IN ('correct','incorrect')))
            ORDER BY (instr(?,content_id)>0) DESC,
                (length(title)>=2 AND instr(?,title)>0) DESC,
                (length(reading)>=2 AND instr(?,reading)>0) DESC,c.rowid LIMIT ?""",
            [*params, message, message, message, candidate_limit]).fetchall()

    def course_content_json(self, content_id: str) -> sqlite3.Row | None:
        return self._connection.execute(
            'SELECT content_json FROM learning_content WHERE content_id=?', (content_id,)).fetchone()
