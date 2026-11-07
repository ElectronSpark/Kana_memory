-- Current learner schema, 0.28.2 / record schema 1.
-- Generated from empty app-owned schema; contains no learner rows.
-- Content packs are separate. Runtime migration is in record_schema.py.
PRAGMA foreign_keys = ON;

CREATE TABLE app_setting (key TEXT PRIMARY KEY,value_json TEXT NOT NULL);

CREATE TABLE evidence_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);

CREATE TABLE learner_record_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);

CREATE TABLE learning_aux_adjustment (
                adjustment_id TEXT PRIMARY KEY,event_id TEXT NOT NULL,target_id TEXT NOT NULL,
                ability TEXT NOT NULL,card_kind TEXT NOT NULL CHECK(card_kind IN ('course','kana')),
                card_id TEXT NOT NULL,anchor_event_id TEXT NOT NULL,occurred_at_utc TEXT NOT NULL,
                result TEXT NOT NULL CHECK(result IN ('correct','incorrect')),fingerprint TEXT NOT NULL,
                base_due_at_utc TEXT NOT NULL,base_last_review_at_utc TEXT NOT NULL,
                before_due_at_utc TEXT NOT NULL,after_due_at_utc TEXT NOT NULL,weight REAL NOT NULL,
                retracted INTEGER NOT NULL DEFAULT 0 CHECK(retracted IN (0,1)),
                UNIQUE(card_kind,card_id,anchor_event_id,fingerprint));

CREATE TABLE learning_content (
                content_id TEXT PRIMARY KEY,family TEXT NOT NULL CHECK(family IN ('word','grammar')),
                revision TEXT NOT NULL,introduction_id TEXT NOT NULL UNIQUE,
                stage TEXT NOT NULL CHECK(stage IN ('intro','guide','bridge','check','done')),
                content_json TEXT NOT NULL,checkpoint_json TEXT NOT NULL,
                exposure_group TEXT NOT NULL,introduced_at_utc TEXT NOT NULL,
                guided_at_utc TEXT,retrieval_at_utc TEXT);

CREATE TABLE learning_event (
                event_id TEXT PRIMARY KEY,question_id TEXT NOT NULL UNIQUE REFERENCES learning_question,
                unit_id TEXT NOT NULL REFERENCES learning_unit,occurred_at_utc TEXT NOT NULL,
                rating INTEGER CHECK(rating BETWEEN 1 AND 4),role TEXT NOT NULL,
                outcome TEXT NOT NULL,assisted INTEGER NOT NULL CHECK(assisted IN (0,1)),
                state_before_json TEXT,state_after_json TEXT,review_log_json TEXT,
                result_json TEXT NOT NULL);

CREATE TABLE learning_evidence (
                event_id TEXT PRIMARY KEY,target_id TEXT NOT NULL,ability TEXT NOT NULL,
                kind TEXT NOT NULL,content_revision TEXT NOT NULL,source TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('primary','auxiliary','exposure')),
                condition_key TEXT NOT NULL,conditions_json TEXT NOT NULL,
                occurred_at_utc TEXT NOT NULL,outcome TEXT NOT NULL,
                assisted INTEGER NOT NULL CHECK(assisted IN (0,1)),
                eligible INTEGER NOT NULL CHECK(eligible IN (0,1)),
                snapshot_json TEXT NOT NULL,retracted INTEGER NOT NULL DEFAULT 0 CHECK(retracted IN (0,1)),
                retraction_reason TEXT NOT NULL DEFAULT '',retracted_at_utc TEXT);

CREATE TABLE learning_evidence_pattern (
                pattern_id TEXT PRIMARY KEY,target_id TEXT NOT NULL,ability TEXT NOT NULL,kind TEXT NOT NULL,
                condition_key TEXT NOT NULL,conditions_json TEXT NOT NULL,
                opportunities INTEGER NOT NULL,successes INTEGER NOT NULL,errors INTEGER NOT NULL,
                concern REAL NOT NULL,last_at_utc TEXT NOT NULL,
                UNIQUE(target_id,ability,kind,condition_key));

CREATE TABLE learning_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);

CREATE TABLE learning_question (
                question_id TEXT PRIMARY KEY,unit_id TEXT NOT NULL REFERENCES learning_unit,
                kind TEXT NOT NULL,snapshot_json TEXT NOT NULL,status TEXT NOT NULL
                CHECK(status IN ('pending','completed')),draft_json TEXT NOT NULL,
                result_json TEXT,created_at_utc TEXT NOT NULL,not_before_utc TEXT,
                exposure_group TEXT NOT NULL);

CREATE TABLE learning_unit (
                unit_id TEXT PRIMARY KEY,content_id TEXT NOT NULL REFERENCES learning_content,
                ability TEXT NOT NULL,scheduler_state_json TEXT,scheduler_version TEXT NOT NULL,
                due_at_utc TEXT,review_count INTEGER NOT NULL DEFAULT 0 CHECK(review_count>=0),
                instruction_ready INTEGER NOT NULL DEFAULT 0 CHECK(instruction_ready IN (0,1)),
                suspended INTEGER NOT NULL DEFAULT 0 CHECK(suspended IN (0,1)),
                probe_due_at_utc TEXT,last_unassisted_success_at_utc TEXT,
                UNIQUE(content_id,ability));

CREATE TABLE practice_attempt (
        attempt_id TEXT PRIMARY KEY,content_id TEXT NOT NULL,skill TEXT NOT NULL,
        script TEXT NOT NULL,occurred_at_utc TEXT NOT NULL,outcome TEXT NOT NULL);

CREATE TABLE review_event (
        event_id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES study_session,
        card_id TEXT NOT NULL REFERENCES study_card,occurred_at_utc TEXT NOT NULL,
        rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 4),answered_text TEXT,
        assisted INTEGER NOT NULL,outcome TEXT NOT NULL,duration_ms INTEGER NOT NULL,
        state_before_json TEXT NOT NULL,state_after_json TEXT NOT NULL,
        algorithm_version TEXT NOT NULL,review_log_json TEXT NOT NULL);

CREATE TABLE session_answer (
        session_id TEXT NOT NULL REFERENCES study_session,question_index INTEGER NOT NULL,
        event_id TEXT NOT NULL UNIQUE REFERENCES review_event,correct INTEGER NOT NULL,
        PRIMARY KEY(session_id,question_index));

CREATE TABLE session_checkpoint (
        session_id TEXT PRIMARY KEY REFERENCES study_session,updated_at_utc TEXT NOT NULL,
        question_index INTEGER NOT NULL,session_right INTEGER NOT NULL,
        assisted_indices_json TEXT NOT NULL,state_json TEXT NOT NULL);

CREATE TABLE session_question (
        session_id TEXT NOT NULL REFERENCES study_session,question_index INTEGER NOT NULL CHECK(question_index>=0),
        snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json)),PRIMARY KEY(session_id,question_index));

CREATE TABLE study_card (
        card_id TEXT PRIMARY KEY,content_id TEXT NOT NULL,content_version TEXT NOT NULL,
        skill TEXT NOT NULL,script TEXT NOT NULL,scheduler_state_json TEXT NOT NULL,
        scheduler_version TEXT NOT NULL,due_at_utc TEXT NOT NULL,
        introduced_at_utc TEXT,review_count INTEGER NOT NULL DEFAULT 0,
        suspended INTEGER NOT NULL DEFAULT 0,UNIQUE(content_id,skill,script));

CREATE TABLE study_session (
    session_id TEXT PRIMARY KEY,started_at_utc TEXT NOT NULL,timezone TEXT NOT NULL,
    random_seed TEXT NOT NULL,policy_version TEXT NOT NULL);

CREATE TABLE tutor_memory_application(
                request_id TEXT PRIMARY KEY,payload_hash TEXT NOT NULL,created_at_utc TEXT NOT NULL);

CREATE TABLE tutor_memory_claim(
                claim_id TEXT PRIMARY KEY,kind TEXT NOT NULL,text TEXT NOT NULL,
                target_ids_json TEXT NOT NULL,evidence_ids_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('tentative','confirmed','retracted','superseded')),
                source TEXT NOT NULL CHECK(source IN ('model','user')),created_at_utc TEXT NOT NULL,
                expires_at_utc TEXT,withdrawn_at_utc TEXT,withdrawal_reason TEXT NOT NULL DEFAULT '');

CREATE TABLE tutor_memory_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);

CREATE TABLE tutor_user_message(
                message_id TEXT PRIMARY KEY,request_id TEXT NOT NULL UNIQUE,text TEXT NOT NULL,
                target_id TEXT NOT NULL,created_at_utc TEXT NOT NULL);

CREATE TABLE word_spelling_attempt (
        attempt_id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES study_session,
        question_index INTEGER NOT NULL,content_id TEXT NOT NULL,skill TEXT NOT NULL,
        script TEXT NOT NULL,occurred_at_utc TEXT NOT NULL,answered_text TEXT NOT NULL,
        assisted INTEGER NOT NULL CHECK(assisted IN (0,1)),
        correct INTEGER NOT NULL CHECK(correct IN (0,1)),duration_ms INTEGER NOT NULL,
        evidence_json TEXT NOT NULL,UNIQUE(session_id,question_index));

CREATE INDEX card_due ON study_card(due_at_utc,skill,script);

CREATE INDEX evidence_adjustment_anchor ON learning_aux_adjustment(card_kind,card_id,anchor_event_id);

CREATE INDEX evidence_adjustment_event ON learning_aux_adjustment(event_id);

CREATE INDEX evidence_context ON learning_evidence(target_id,ability,kind,condition_key,occurred_at_utc,event_id);

CREATE INDEX evidence_pattern_target ON learning_evidence_pattern(target_id,concern DESC);

CREATE INDEX evidence_target_time ON learning_evidence(target_id,occurred_at_utc DESC);

CREATE INDEX learning_dispatch_finalized ON learning_event(
                julianday(COALESCE(json_extract(result_json,'$.finalized_at_utc'),occurred_at_utc)))
                WHERE role='primary';

CREATE INDEX learning_due ON learning_unit(due_at_utc);

CREATE INDEX learning_events_day ON learning_event(occurred_at_utc,unit_id) WHERE role='primary';

CREATE INDEX learning_events_unit ON learning_event(unit_id);

CREATE UNIQUE INDEX learning_one_pending_unit ON learning_question(unit_id) WHERE status='pending';

CREATE INDEX learning_ready_evidence ON learning_event(unit_id)
                WHERE role='primary' AND outcome='correct' AND assisted=0 AND rating>1;

CREATE INDEX learning_teaching_guided ON learning_content(guided_at_utc,content_id)
                WHERE guided_at_utc IS NOT NULL;

CREATE INDEX learning_teaching_primary_final ON learning_event(
                unit_id,julianday(COALESCE(json_extract(result_json,'$.finalized_at_utc'),occurred_at_utc)),
                julianday(occurred_at_utc)) WHERE role='primary';

CREATE INDEX learning_teaching_primary_occurred ON learning_event(
                unit_id,julianday(occurred_at_utc),
                julianday(COALESCE(json_extract(result_json,'$.finalized_at_utc'),occurred_at_utc))) WHERE role='primary';

CREATE INDEX learning_unresolved_finalized ON learning_event(
                julianday(COALESCE(json_extract(result_json,'$.finalized_at_utc'),occurred_at_utc)))
                WHERE role='unresolved';

CREATE INDEX reviews_card ON review_event(card_id);

CREATE INDEX reviews_card_first_finalized ON review_event(card_id,
        julianday(COALESCE(json_extract(review_log_json,'$.finalized_at_utc'),occurred_at_utc)));

CREATE INDEX reviews_dispatch_finalized ON review_event(
        julianday(COALESCE(json_extract(review_log_json,'$.finalized_at_utc'),occurred_at_utc)));

CREATE INDEX reviews_finalized_card ON review_event(card_id)
        WHERE instr(review_log_json, '"finalized_at_utc"')>0;

CREATE INDEX reviews_time ON review_event(occurred_at_utc);

CREATE INDEX spelling_time ON word_spelling_attempt(occurred_at_utc);

CREATE INDEX tutor_memory_current ON tutor_memory_claim(status,created_at_utc DESC);
