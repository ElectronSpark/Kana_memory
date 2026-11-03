"""Atomic removal of learner progress, preserving preferences and local files."""
from __future__ import annotations

import json


# Children precede their foreign-key parents. Keep the live database and its
# schema: other open connections must observe the same committed reset.
LEARNING_TABLES = (
    "word_spelling_attempt", "session_answer", "session_checkpoint", "review_event", "practice_attempt",
    "study_card", "session_question", "study_session",
)


def clear_learning_data(store) -> dict:
    """Remove progress and content controls in one transaction.

    Ordinary settings, scheduler configuration, packaged resources, downloaded
    models and existing backup files are retained. This does not overwrite the
    SQLite file or claim forensic erasure of its old disk pages.
    Content-feedback analysis reports are retained too: they contain frozen
    content and user feedback, not schedule state or independent-answer credit.
    """
    from .domain import CONTENT_CONTROLS_SETTING

    db = store._db
    if db.in_transaction:
        raise RuntimeError("Cannot clear learning data inside an active transaction")
    deleted_rows = {}
    deleted_settings = []
    with db:
        db.execute("BEGIN IMMEDIATE")
        from .progress_backup import _teaching_tables
        for table in reversed(_teaching_tables()):
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                if table in {"evidence_meta", "tutor_memory_meta"}:
                    db.execute("UPDATE " + table + " SET value='0' WHERE key='revision'")
                else:
                    deleted_rows[table] = db.execute("DELETE FROM " + table).rowcount
        from .learning_store import TABLES as EXPANSION_TABLES, tables_present
        if tables_present(db):
            for table in reversed(EXPANSION_TABLES):
                if table != "learning_meta":
                    deleted_rows[table] = db.execute("DELETE FROM " + table).rowcount
        for table in LEARNING_TABLES:
            deleted_rows[table] = db.execute("DELETE FROM " + table).rowcount
        from .semantic_errors import create_indexes
        create_indexes(db)
        db.execute('DELETE FROM semantic_error_seen')
        db.execute('DELETE FROM semantic_error_pattern')
        for key in (CONTENT_CONTROLS_SETTING, "new_dispatch_v1", "explicit_learning_start_v1", "practice_queue_kinds", "kana_introductions_v1", "kana_ability_bridges_v1", "study_path_v1", "kana_context_words_v1", "kana_rule_probes_v1", "ai_practice_v1", "ai_question_preview_v1", "ai_tutor_v1"):
            if db.execute("DELETE FROM app_setting WHERE key=?", (key,)).rowcount:
                deleted_settings.append(key)
        from .ai_ui_state import AI_FORM_SETTING, reset_ai_form_request_history
        from .material_workflow import reset_in_transaction
        reset_in_transaction(db)
        material_keys = [row[0] for row in db.execute("SELECT key FROM app_setting WHERE key GLOB 'material_study_v1:*'")]
        db.execute("DELETE FROM app_setting WHERE key GLOB 'material_study_v1:*'")
        material_keys.extend(row[0] for row in db.execute("SELECT key FROM app_setting WHERE key GLOB 'material_pretest_v1:*'"))
        db.execute("DELETE FROM app_setting WHERE key GLOB 'material_pretest_v1:*'")
        material_keys.extend(row[0] for row in db.execute("SELECT key FROM app_setting WHERE key GLOB 'material_admission_v1:*'"))
        db.execute("DELETE FROM app_setting WHERE key GLOB 'material_admission_v1:*'")
        material_keys.extend(row[0] for row in db.execute("SELECT key FROM app_setting WHERE key GLOB 'retention_ai_v1:*'"))
        db.execute("DELETE FROM app_setting WHERE key GLOB 'retention_ai_v1:*'")
        for prefix in ('retention_scope_form_v1:', 'retention_scope_ai_v1:', 'retention_dialogue_v1:', 'retention_dialogue_form_v1:', 'learning_replacement_v1:', 'content_history_v1:', 'content_history_status_v1:', 'content_queue_v1:', 'diagnostic_replacement_v1:', 'kana_rule_probe_history_v1:', 'kana_rule_probe_retired_v1:', 'session_content_replacement_v1:', 'material_validity_history_v1:', 'material_validity_status_v1:'):
            material_keys.extend(row[0] for row in db.execute('SELECT key FROM app_setting WHERE key GLOB ?', (prefix + '*',)))
            db.execute('DELETE FROM app_setting WHERE key GLOB ?', (prefix + '*',))
        material_keys.extend(row[0] for row in db.execute("SELECT key FROM app_setting WHERE key GLOB 'retention_source_guard_v1:*'"))
        db.execute("DELETE FROM app_setting WHERE key GLOB 'retention_source_guard_v1:*'")
        material_keys.extend(row[0] for row in db.execute("SELECT key FROM app_setting WHERE key GLOB 'ability_teaching_v1:*'"))
        db.execute("DELETE FROM app_setting WHERE key GLOB 'ability_teaching_v1:*'")
        deleted_settings.extend(material_keys)
        check_filter = "key GLOB 'retention_check_v[12]:*' OR key GLOB 'retention_exposure_v1:*' OR key GLOB 'retention_reservation_v1:*' OR key GLOB 'retention_handoff_v1:*' OR key='retention_active_v2'"
        check_keys = [row[0] for row in db.execute('SELECT key FROM app_setting WHERE ' + check_filter)]
        db.execute('DELETE FROM app_setting WHERE ' + check_filter)
        deleted_settings.extend(check_keys)
        for row in db.execute("SELECT key FROM app_setting WHERE key GLOB 'retention_confirmation_v1:*' OR key='retention_activity_v1'").fetchall():
            db.execute('DELETE FROM app_setting WHERE key=?', (row[0],))
            deleted_settings.append(row[0])
        draft_row = db.execute("SELECT value_json FROM app_setting WHERE key=?", (AI_FORM_SETTING,)).fetchone()
        if draft_row is not None:
            old_drafts = json.loads(draft_row[0])
            drafts = reset_ai_form_request_history(old_drafts)
            if drafts != old_drafts:
                db.execute("UPDATE app_setting SET value_json=? WHERE key=?",
                           (json.dumps(drafts, ensure_ascii=False), AI_FORM_SETTING))
        version = db.execute("PRAGMA data_version").fetchone()[0]

    # Publish the empty caches only after COMMIT succeeds. Choice history is
    # derived from sessions, not another app_setting. Other connections use
    # data_version; the caller's revision changes through the existing writes.
    store._snapshot_cache = None
    store._snapshot_reader.clear()
    store._choice_history = {}
    store._choice_history_rowid = 0
    store._choice_history_version = version
    return {"deleted_rows": deleted_rows, "deleted_settings": deleted_settings}
