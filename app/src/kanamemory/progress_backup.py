"""Current learner records, validated before any live-data mutation.

Records use bounded UTF-8 JSON blocks, never executable SQL or content. Imports
replace learner tables in one transaction and retain a private rollback copy.
"""
from __future__ import annotations

from datetime import datetime
import json
import math
import os
import re
import sqlite3
from uuid import uuid4

from fsrs import Card, Scheduler
from . import record_transport, backup_stage
from .record_schema import TABLES

FORMAT = record_transport.FORMAT
VERSION = record_transport.VERSION
MAX_BYTES = 64 * 1024 * 1024
SESSION_SNAPSHOT_LIMIT = 64 * 1024 * 1024


def _teaching_tables():
    from .learning_evidence import TABLES as EVIDENCE_TABLES
    from .tutor_memory import TABLES as MEMORY_TABLES
    return (*EVIDENCE_TABLES, *MEMORY_TABLES)


def _empty_teaching_tables():
    result = {name: [] for name in _teaching_tables()}
    result["evidence_meta"] = [{"key": "schema_version", "value": "1"}, {"key": "revision", "value": "0"}]
    result["tutor_memory_meta"] = [{"key": "version", "value": "1"}, {"key": "revision", "value": "0"}]
    return result


def _fail(message):
    raise ValueError("备份无效：" + message)


def _integer(value, minimum=0, maximum=10**12):
    if type(value) is not int or not minimum <= value <= maximum:
        _fail("数值超出允许范围。")


def _date(value, optional=False):
    if optional and value is None:
        return
    try:
        date = datetime.fromisoformat(value)
        if date.tzinfo is None or date.utcoffset() is None:
            _fail("时间缺少时区。")
    except (TypeError, ValueError):
        _fail("时间格式不正确。")


def _json(value):
    try:
        return json.loads(value, object_pairs_hook=record_transport._unique, parse_constant=lambda _: _fail("不能包含非有限数值。"))
    except (TypeError, json.JSONDecodeError, RecursionError):
        _fail("JSON 内容损坏。")


def validate_content_controls(value):
    """The optional v1 setting adds controls without changing backup tables."""
    if not isinstance(value, dict) or len(value) > 1000000:
        _fail("卡片管理设置格式不正确。")
    for content_id, control in value.items():
        if not isinstance(content_id, str) or not content_id.strip() or len(content_id) > 10000:
            _fail("记忆项标识不正确。")
        if not isinstance(control, dict) or set(control) != {"suspended", "buried_until_utc"}:
            _fail("卡片管理字段不正确。")
        if type(control["suspended"]) is not bool:
            _fail("暂停状态不正确。")
        _date(control["buried_until_utc"], optional=True)
    return value


def validate_session_state(state, question=None):
    if not isinstance(state, dict):
        _fail("练习状态格式不正确。")
    try:
        encoded = json.dumps(state, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        _fail("练习状态无法保存。")
    if len(encoded.encode("utf-8")) > 2 * 1024 * 1024:
        _fail("单题练习状态过大。")
    for key in ("answer_revealed", "answer_correct", "feedback_pending", "use_full_keyboard", "question_audio_ok", "word_used_conversion"):
        if key in state and type(state[key]) is not bool:
            _fail("练习状态标记不正确。")
    for key in ("answered_text", "word_answer", "romaji_answer", "page", "word_conversion_source"):
        if key in state and (not isinstance(state[key], str) or len(state[key]) > 10000):
            _fail("练习文字格式不正确。")
    if ("word_conversion_convention" in state
            and (not isinstance(state["word_conversion_convention"], str)
                 or state["word_conversion_convention"] not in {"reading", "keyboard"})):
        _fail("罗马音输入方式不正确。")
    if (question is not None and any(key in state for key in
            ("word_conversion_source", "word_conversion_convention", "word_used_conversion"))
            and question["kind"] not in {"word_kana", "word_meaning_kana"}):
        _fail("该题不支持罗马音转换草稿。")
    if state.get("word_conversion_source"):
        from .romaji_input import convert_romaji
        if convert_romaji(state["word_conversion_source"],
                          convention=state.get("word_conversion_convention", "reading"))["text"]:
            state = {**state, "word_used_conversion": True}
    # Old feedback checkpoints omit these fields. New ones must be safe to
    # restore into the frozen-attempt path before any live tables are replaced.
    if "submitted_at_utc" in state:
        _date(state["submitted_at_utc"])
    if "answer_duration_ms" in state:
        _integer(state["answer_duration_ms"])
    has_parts = "word_romaji_parts" in state
    has_plan = "word_romaji_plan" in state
    if has_parts != has_plan:
        _fail("逐假名草稿缺少选项计划或作答记录。")
    if has_parts:
        from .word_romaji_units import compose_romaji_parts, validate_romaji_unit_plan
        plan, parts = state["word_romaji_plan"], state["word_romaji_parts"]
        if (not isinstance(parts, list) or len(parts) > 10000
                or not isinstance(plan, dict) or not isinstance(plan.get("units"), list)
                or any(not isinstance(unit, dict) or not isinstance(unit.get("kana"), str)
                       for unit in plan["units"])):
            _fail("逐假名草稿格式不正确。")
        kana = "".join(unit["kana"] for unit in plan["units"])
        validate_romaji_unit_plan(kana, plan)
        actual = compose_romaji_parts(plan, parts)
        if question is not None:
            from .domain import word_romaji_unit_plan
            word_romaji_unit_plan(question, plan)
        if (state.get("feedback_pending") and state.get("answered_text", "") != actual
                and not (state.get("answer_revealed") and not state.get("answered_text", ""))):
            _fail("逐假名反馈与保存的作答不一致。")
    for key in ("ink_width", "ink_height"):
        if key in state and (type(state[key]) not in (int, float) or not math.isfinite(state[key]) or not 0 < state[key] <= 100000):
            _fail("笔迹区域尺寸不正确。")
    if "strokes" in state:
        strokes = state["strokes"]
        if not isinstance(strokes, list) or len(strokes) > 1000:
            _fail("笔迹格式不正确。")
        for stroke in strokes:
            if not isinstance(stroke, list) or len(stroke) > 100000:
                _fail("笔迹格式不正确。")
            for point in stroke:
                if (not isinstance(point, (tuple, list)) or len(point) != 3 or
                        any(type(v) not in (int, float) or not math.isfinite(v) for v in point)):
                    _fail("笔迹坐标不正确。")
    return state


def _questions(value):
    from .domain import Question, WORD_SKILLS, WORD_KIND_PAUSE, _validate_key, normalize_accepted_answers, session_practice_lane
    questions = _json(value)
    # Mixed rounds legitimately exceed 10,000 questions. The enclosing backup's
    # byte limit bounds input size without imposing a hidden practice-count cap.
    if not isinstance(questions, list):
        _fail("题目快照格式不正确。")
    try:
        for item in questions:
            question = Question(**item)
            _validate_key(question.content_id, question.kind, question.script)
            if not all(isinstance(x, str) for x in (question.prompt, question.answer, question.audio_text)):
                _fail("题目文字不正确。")
            if not isinstance(question.metadata, dict) or not isinstance(question.options, list) or not isinstance(question.accepted_answers, (list, tuple)):
                _fail("题目选项不正确。")
            if WORD_KIND_PAUSE in question.metadata:
                marker = question.metadata[WORD_KIND_PAUSE]
                if (question.kind not in WORD_SKILLS or question.metadata.get("not_before_utc")
                        or not isinstance(marker, dict) or set(marker) != {"version", "disabled", "state"}
                        or type(marker["version"]) is not int or marker["version"] != 1
                        or type(marker["disabled"]) is not bool):
                    _fail("暂停的词例题目状态不正确。")
                validate_session_state(marker["state"], question)
            config = question.metadata.get("practice_config", {})
            if not isinstance(config, dict):
                _fail("题目安排格式不正确。")
            if "kana_context_v1" in question.metadata:
                from .kana_context_words import validate_context_marker
                validate_context_marker(question.metadata["kana_context_v1"])
                if (question.metadata['kana_context_v1'].get('periodic') is False and 'kana_rule_probe_v1' not in question.metadata
                        and 'content_replacement_v1' not in question.metadata):
                    _fail('错误触发的词例缺少其补测依据。')
                if (question.kind not in {"word_meaning_kana", "word_kana"}
                        or question.metadata.get("word_romaji_drill")
                        or question.metadata.get("not_before_utc")):
                    _fail("假名词例题目来源不正确。")
            if "kana_rule_probe_v1" in question.metadata:
                from .kana_reading_rules import READING_RULE_IDS
                probe = question.metadata["kana_rule_probe_v1"]
                if (not isinstance(probe, dict) or set(probe) not in ({"rule_id", "probe_id", "context_id"}, {"rule_id", "probe_id", "context_id", "trigger_event_id"})
                        or probe["rule_id"] not in READING_RULE_IDS
                        or any(not isinstance(probe[key], str) or not 0 < len(probe[key]) <= 1024
                               for key in set(probe) - {'rule_id'})):
                    _fail("读音规则抽查标记不正确。")
            if "schedule_mode" in config and config["schedule_mode"] not in {"reinforce", "spaced"}:
                _fail("复习安排类型不正确。")
            if "word_direction" in config and config["word_direction"] not in {"kana", "romaji", "mixed"}:
                _fail("单词练习方向不正确。")
            if "not_before_utc" in question.metadata:
                _date(question.metadata["not_before_utc"])
            for key in ("relearning_of_index", "relearning_count"):
                if key in question.metadata:
                    _integer(question.metadata[key])
            normalize_accepted_answers(question.accepted_answers)
            if not all(isinstance(x, str) for x in question.options):
                _fail("题目选项不正确。")
            # Validate accepted-answer shape without rewriting frozen snapshots.
        session_practice_lane(questions)
    except (TypeError, KeyError, ValueError):
        _fail("题目快照损坏。")
    return questions


def _validate_saved_units(units):
    seen = set()
    for unit in units:
        if (not isinstance(unit, dict) or set(unit) != {"content_id", "symbol", "result", "error_types"}
                or not isinstance(unit["content_id"], str) or not unit["content_id"].strip()
                or not isinstance(unit["symbol"], str) or not unit["symbol"]
                or unit["result"] not in {"correct", "incorrect"}
                or not isinstance(unit["error_types"], list)
                or any(not isinstance(value, str) for value in unit["error_types"])
                or unit["content_id"] in seen):
            _fail("词例假名证据格式不正确。")
        seen.add(unit["content_id"])


def _validate_interval_adjustments(row, evidence, *, drill, cards, events,
                                   adjusted_readings):
    """Validate due overrides as auxiliary evidence, never synthetic reviews.

    Historical adjustments may precede newer independent reviews, so their
    destination is checked against the cited original review and the bounded
    interval rule, rather than against the card's current due date.
    """
    if "schedule_adjustments" not in evidence:
        return
    from .word_interval import (VISUAL_INTERVAL_SKILLS, WORD_INTERVAL_POLICY,
                                WORD_INTERVAL_WEIGHT)
    if not drill:
        _fail("词例间隔调整需要新版独立练习备份。")
    adjustments = evidence["schedule_adjustments"]
    if (not isinstance(adjustments, list)
            or len(adjustments) > len(evidence["units"]) * len(VISUAL_INTERVAL_SKILLS)
            or (row["assisted"] or evidence.get("accepted_alternative")) and adjustments):
        _fail("词例间隔调整格式不正确。")
    fields = {"policy", "weight", "card_id", "content_id", "skill", "script",
              "anchor_event_id", "result", "before_due_at_utc", "after_due_at_utc",
              "base_due_at_utc", "base_interval_seconds"}
    units = {unit["content_id"]: unit for unit in evidence["units"]}
    occurred = datetime.fromisoformat(row["occurred_at_utc"])
    seen = set()
    for adjustment in adjustments:
        extra = {"evidence_event_id"}
        if not isinstance(adjustment, dict) or not fields <= set(adjustment) or set(adjustment) - fields - extra:
            _fail("词例间隔调整字段不正确。")
        if (any(not isinstance(adjustment[key], str) for key in fields - {"weight", "base_interval_seconds"})
                or adjustment["policy"] != WORD_INTERVAL_POLICY
                or type(adjustment["weight"]) not in (int, float)
                or not 0 < adjustment["weight"] <= WORD_INTERVAL_WEIGHT
                or type(adjustment["base_interval_seconds"]) not in (int, float)
                or not math.isfinite(adjustment["base_interval_seconds"])
                or adjustment["base_interval_seconds"] <= 0):
            _fail("词例间隔调整策略不正确。")
        card = cards.get(adjustment["card_id"])
        unit = units.get(adjustment["content_id"])
        anchor = events.get(adjustment["anchor_event_id"])
        if (card is None or unit is None or anchor is None
                or adjustment["card_id"] in seen
                or adjustment["skill"] not in VISUAL_INTERVAL_SKILLS
                or (card["content_id"], card["skill"], card["script"]) !=
                    (adjustment["content_id"], adjustment["skill"], adjustment["script"])
                or adjustment["script"] != row["script"]
                or adjustment["result"] != unit["result"]
                or anchor["card_id"] != adjustment["card_id"]):
            _fail("词例间隔调整与假名或原复习记录不一致。")
        seen.add(adjustment["card_id"])
        for key in ("before_due_at_utc", "after_due_at_utc", "base_due_at_utc"):
            _date(adjustment[key])
        base = Card.from_dict(_json(anchor["state_after_json"]))
        if (int(base.state) != 2 or base.last_review is None
                or base.last_review != datetime.fromisoformat(anchor["occurred_at_utc"])
                or base.last_review > occurred):
            _fail("词例间隔调整的原复习状态不正确。")
        base_due = datetime.fromisoformat(adjustment["base_due_at_utc"])
        before_due = datetime.fromisoformat(adjustment["before_due_at_utc"])
        after_due = datetime.fromisoformat(adjustment["after_due_at_utc"])
        interval = (base.due - base.last_review).total_seconds()
        if (base_due != base.due or interval <= 0
                or abs(adjustment["base_interval_seconds"] - interval) > 0.000001
                or abs((before_due - base_due).total_seconds()) > interval * WORD_INTERVAL_WEIGHT + 0.000001):
            _fail("词例间隔调整超出原复习间隔范围。")
        from .learning_evidence import bounded_due
        expected_due = bounded_due(base_due, base.last_review, before_due, adjustment["result"], occurred, adjustment["weight"])
        if expected_due is None or after_due != expected_due:
            _fail("词例间隔调整的到期时间不正确。")
        reading_key = (adjustment["card_id"], adjustment["anchor_event_id"], evidence["expected"])
        if reading_key in adjusted_readings:
            _fail("同一词例重复调整了同一轮假名复习间隔。")
        adjusted_readings.add(reading_key)


def export_backup(store):
    """Export only the current record format from one consistent DB snapshot."""
    if store._db.in_transaction:
        raise RuntimeError("Cannot export a public backup inside another transaction")
    backup_stage.reclaim_orphans(store.data_dir)
    with store._db:
        store._db.execute("BEGIN")
        return record_transport.encode(store, outer_limit=MAX_BYTES)


def _export_locked_snapshot(store):
    if not store._db.in_transaction:
        raise RuntimeError("Recovery backup requires the replacement transaction")
    return record_transport.encode(store, outer_limit=MAX_BYTES)


def is_legacy_backup(data):
    from .legacy_record_conversion import is_legacy_backup as detect
    return detect(data)


def convert_legacy_backup(store, data, *, cancel_check=None):
    from .legacy_record_conversion import convert_legacy_backup as convert
    return convert(store, data, cancel_check=cancel_check)


def _validated(store, data, *, cancel_check=None):
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_BYTES:
        _fail("请选择 64 MiB 以内的学习记录 JSON 文件。")
    if is_legacy_backup(data):
        _fail("这是旧版学习记录，请先转换为新版格式。")
    payload = record_transport.decode(data, store, outer_limit=MAX_BYTES, cancel_check=cancel_check)
    try:
        return _validated_payload(store, payload, cancel_check=cancel_check)
    finally:
        payload["tables"].close()


def _validated_payload(store, payload, *, cancel_check=None):
    from .domain import ALGORITHM_VERSION, WORD_SKILLS, WORD_ROMAJI_POLICY, _validate_key, normalize_answer, session_practice_lane, word_kana_answer
    from .learning_store import TABLES as LEARNING_TABLES, ensure_schema, validate_backup_rows
    from .record_validation import validate_row
    check = cancel_check or (lambda: None)
    check()
    if (not isinstance(payload, dict) or set(payload) != {"format", "version", "algorithm_version", "created_at_utc", "tables"}
            or payload.get("format") != FORMAT or type(payload.get("version")) is not int or payload["version"] != VERSION):
        _fail("学习记录格式或版本不兼容。")
    if payload.get("algorithm_version") != ALGORITHM_VERSION:
        _fail("复习算法版本不兼容，需先迁移备份。")
    _date(payload.get("created_at_utc"))
    tables = payload["tables"]
    from . import learning_evidence, tutor_memory
    from .record_schema import create_base
    all_tables = (*TABLES, *LEARNING_TABLES, *_teaching_tables())
    if not isinstance(tables, dict) or set(tables) != set(all_tables):
        _fail("学习记录缺少必要的记录集合。")
    staging = record_transport.DiskTables(store.data_dir)
    temporary = staging.db
    keep_staging = False
    temporary.row_factory = sqlite3.Row
    temporary.execute("PRAGMA foreign_keys=ON")
    try:
        create_base(temporary)
        temporary.commit()
        ensure_schema(temporary)
        learning_evidence.ensure_schema(temporary)
        tutor_memory.ensure_schema(temporary)
        schemas = [tuple(row) for row in temporary.execute("""SELECT type,name,sql
            FROM sqlite_master WHERE type IN ('table','index') AND sql IS NOT NULL
            AND name NOT LIKE 'sqlite_%' ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END,rowid""")]
        for name in reversed(all_tables):
            temporary.execute('DELETE FROM ' + name)
        for name in all_tables:
            rows = tables[name]
            if not isinstance(rows, list) or len(rows) > 1000000:
                _fail("数据表格式不正确。")
            schema = list(temporary.execute("PRAGMA table_info(" + name + ")"))
            columns = [row[1] for row in schema]
            if isinstance(tables, record_transport.DiskTables) and tables.columns.get(name, columns) != columns:
                _fail("备份分块表字段不匹配。")
            for row in rows:
                validate_row(row, schema)
                check()
                if name == "session_question" and (not isinstance(row.get("snapshot_json"), str)
                        or len(row["snapshot_json"].encode("utf-8")) > SESSION_SNAPSHOT_LIMIT):
                    _fail("单题快照超过安全大小限制。")
                temporary.execute("INSERT INTO " + name + " (" + ",".join(columns) + ") VALUES (" + ",".join("?" for _ in columns) + ")", [row[c] for c in columns])
        meta = dict(temporary.execute("SELECT key,value FROM learner_record_meta"))
        if meta != {"schema_version": "1"}:
            _fail("学习记录存储版本不正确。")
        scheduler = temporary.execute("SELECT value_json FROM app_setting WHERE key='scheduler'").fetchone()
        if scheduler is None:
            _fail("备份缺少复习算法设置。")
        for row in tables["app_setting"]:
            if not isinstance(row["key"], str) or not 0 < len(row["key"]) <= 256:
                _fail("设置名称不正确。")
            setting = _json(row["value_json"])
            if row["key"] == "daily_new_limit":
                # Historical setting only: old backups stay readable, and a
                # restored value must never reintroduce a practice quota.
                if type(setting) is not int or setting <= 0:
                    _fail("每日新增历史设置须为正整数。")
            if row["key"] == "study_item_controls_v1":
                validate_content_controls(setting)
            if row["key"] == "new_dispatch_v1":
                from .new_dispatch import validate
                validate(setting)
            if row["key"] == "explicit_learning_start_v1":
                from .explicit_learning_start import validate
                validate(setting)
            if row["key"] == "study_path_v1":
                from .study_path import validate_path_state
                validate_path_state(setting)
            if row["key"] == "kana_context_words_v1":
                from .kana_context_words import validate_state
                validate_state(setting)
            if row["key"] == "kana_rule_probes_v1":
                from .kana_rule_evidence import validate_rule_probe_state
                validate_rule_probe_state(setting)
            if row["key"] == "shared_examples_v1":
                from .example_store import validate_pack
                if validate_pack(setting)['status'] != 'reviewed':
                    _fail("备份中的例句必须是已启用的材料。")
            if row["key"] == "ai_materials_v1":
                from .ai_materials import validate_ai_material_state
                validate_ai_material_state(setting)
            if row["key"].startswith("material_input_"):
                from .material_input import SETTING as MATERIAL_INPUT_SETTING, validate_origin
                if row["key"] != MATERIAL_INPUT_SETTING:
                    _fail("材料输入版本无效。")
                validate_origin(setting)
            if row["key"].startswith("material_selection_"):
                from .material_input import SELECTION_SETTING, validate_selection
                if row["key"] != SELECTION_SETTING and re.fullmatch(re.escape(SELECTION_SETTING) + r":[a-f0-9]{64}", row["key"]) is None:
                    _fail("材料选择版本无效。")
                validate_selection(setting)
            if row["key"].startswith("material_share_"):
                from .material_share import PREFIX as SHARE_PREFIX, validate_record as validate_share_record
                share = validate_share_record(setting)
                if row["key"] != SHARE_PREFIX + share["id"]:
                    _fail("分享材料版本或标识无效。")
            if row['key'].startswith('material_budget_'):
                from .material_budget import SETTINGS, PREFIX as BUDGET_PREFIX, validate_settings, validate_ledger
                if row['key'] == SETTINGS:
                    validate_settings(setting)
                else:
                    ledger = validate_ledger(setting)
                    if row['key'] != BUDGET_PREFIX + ledger['id']:
                        _fail('材料预算版本或标识无效。')
            if row["key"].startswith("material_workflow_"):
                from .material_workflow import validate_job, PREFIX as MATERIAL_WORKFLOW_PREFIX
                job = validate_job(setting)
                if row["key"] != MATERIAL_WORKFLOW_PREFIX + job["id"]:
                    _fail("材料任务版本或标识无效。")
            if row["key"].startswith("material_verification_"):
                from .material_verification import validate_report, PREFIX as MATERIAL_VERIFICATION_PREFIX
                report = validate_report(setting)
                if row["key"] != MATERIAL_VERIFICATION_PREFIX + report["id"]:
                    _fail("材料核验版本或标识无效。")
            if row["key"].startswith("material_revision_"):
                from .material_revision import validate_record, PREFIX as MATERIAL_REVISION_PREFIX
                revision = validate_record(setting)
                if row["key"] != MATERIAL_REVISION_PREFIX + revision["id"]:
                    _fail("材料修订版本或标识无效。")
            if row['key'] == 'material_usage_v1':
                from .material_usage import validate as validate_material_usage
                validate_material_usage(setting)
            if row['key'] == 'material_source_decisions_v1':
                from .material_source_resolution import validate as validate_material_sources
                validate_material_sources(setting)
            if row["key"].startswith("material_study_"):
                from .material_study import validate_state as validate_material_study, PREFIX as MATERIAL_STUDY_PREFIX
                study = validate_material_study(setting)
                if row["key"] != MATERIAL_STUDY_PREFIX + study["id"]:
                    _fail("材料学习记录版本或标识无效。")
            if row['key'].startswith('material_pretest_'):
                from .material_pretest import validate, receipt_key
                receipt = validate(setting)
                if row['key'] not in {receipt_key(receipt, first=True), receipt_key(receipt)}:
                    _fail('材料先测回执版本或标识无效。')
            if row['key'].startswith('material_admission_'):
                from .material_admission import validate, PREFIX as ADMISSION_PREFIX
                receipt = validate(setting)
                if row['key'] != ADMISSION_PREFIX + receipt['id']:
                    _fail('材料能力入学回执版本或标识无效。')
            if row['key'].startswith('retention_ai_'):
                from .retention_ai import validate, PREFIX as RETENTION_AI_PREFIX
                plan = validate(setting)
                if row['key'] != RETENTION_AI_PREFIX + plan['id']:
                    _fail('AI 检查建议记录版本或标识无效。')
            if row['key'].startswith('retention_scope_form_'):
                from .retention_scope_form import validate, PREFIX as SCOPE_FORMS
                form = validate(setting)
                if row['key'] != SCOPE_FORMS + form['id']: _fail('检查范围草稿标识无效。')
            if row['key'].startswith('retention_scope_ai_'):
                from .retention_scope_ai import validate, PREFIX as SCOPE_AI
                plan = validate(setting)
                if row['key'] != SCOPE_AI + plan['id']: _fail('范围建议标识无效。')
            if row['key'].startswith('retention_source_guard_'):
                from .retention_sources import validate_guard, guard_key
                guard = validate_guard(setting)
                if row['key'] != guard_key(guard['question_id']):
                    _fail('词典检查来源约束标识无效。')
            if row['key'].startswith('retention_dialogue_'):
                from .retention_dialogue import validate, validate_form, PREFIX as DIALOGUE, FORM_PREFIX as DIALOGUE_FORM
                form = row['key'].startswith('retention_dialogue_form_')
                checked = validate_form(setting) if form else validate(setting)
                if row['key'] != (DIALOGUE_FORM if form else DIALOGUE) + checked['id']:
                    _fail('检查问答记录版本或标识无效。')
            if row['key'].startswith('feedback_analysis_'):
                from .feedback_analysis import validate, PREFIX as FEEDBACK_ANALYSIS
                checked = validate(setting)
                if row['key'] != FEEDBACK_ANALYSIS + checked['id']:
                    _fail('反馈核查记录版本或标识无效。')
            if row['key'].startswith('feedback_batch_'):
                from .feedback_batch import validate, PREFIX as FEEDBACK_BATCH
                checked = validate(setting)
                if row['key'] != FEEDBACK_BATCH + checked['id']:
                    _fail('反馈批次版本或标识无效。')
            if row['key'].startswith('content_correction_plan_'):
                from .content_correction_plan import validate, PREFIX as CORRECTION_PLAN
                checked = validate(setting)
                if row['key'] != CORRECTION_PLAN + checked['id']:
                    _fail('内容修订计划身份无效。')
            if row['key'].startswith('personal_content_note_v'):
                from .personal_content_notes import validate, PREFIX as PERSONAL_NOTE
                checked = validate(setting)
                if row['key'] != PERSONAL_NOTE + checked['id']:
                    _fail('个人注释版本或身份无效。')
            if row['key'].startswith('feedback_field_support_'):
                from .feedback_field_support import validate, PREFIX as FIELD_SUPPORT
                checked = validate(setting)
                if row['key'] != FIELD_SUPPORT + checked['id']:
                    _fail('字段支持记录版本或身份无效。')
            if row['key'].startswith('personal_content_version_v'):
                from .personal_content_versions import validate, PREFIX as PERSONAL_VERSION
                checked = validate(setting)
                if row['key'] != PERSONAL_VERSION + checked['id']:
                    _fail('个人内容版本或身份无效。')
            if row['key'].startswith('personal_content_version_withdrawal_'):
                from .personal_content_compensation import validate, PREFIX as WITHDRAWAL
                checked = validate(setting)
                if row['key'] != WITHDRAWAL + checked['id']:
                    _fail('内容补偿版本或身份无效。')
            if row['key'].startswith('feedback_review_'):
                from .feedback_review import validate, PREFIX as FEEDBACK_REVIEW
                checked = validate(setting)
                if row['key'] != FEEDBACK_REVIEW + checked['id']:
                    _fail('反馈复核记录版本或标识无效。')
            if row['key'].startswith('feedback_revision_'):
                from .feedback_revision import validate, PREFIX as FEEDBACK_REVISION
                checked = validate(setting)
                if row['key'] != FEEDBACK_REVISION + checked['id']:
                    _fail('反馈修订记录版本或标识无效。')
            if row['key'].startswith('feedback_impact_'):
                from .feedback_impact import validate, PREFIX as FEEDBACK_IMPACT
                checked = validate(setting)
                if row['key'] != FEEDBACK_IMPACT + checked['id']:
                    _fail('反馈影响报告版本或标识无效。')
            if row['key'].startswith('feedback_sources_'):
                from .feedback_sources import validate, PREFIX as FEEDBACK_SOURCES
                checked = validate(setting)
                if row['key'] != FEEDBACK_SOURCES + checked['parent_id']:
                    _fail('反馈核据资料版本或标识无效。')
            if row['key'].startswith('feedback_field_review_'):
                from .feedback_field_review import validate, PREFIX as FEEDBACK_FIELD_REVIEW
                checked = validate(setting)
                if row['key'] != FEEDBACK_FIELD_REVIEW + checked['id']:
                    _fail('字段核对回执版本或标识无效。')
            if row["key"].startswith("retention_check_v1:"):
                from .retention_check import validate_episode, PREFIX
                checked = validate_episode(setting)
                if row["key"] != PREFIX + checked["id"]:
                    _fail("检查记录标识不匹配。")
            if row["key"].startswith("retention_check_v2:"):
                from .retention_session import validate_episode as validate_diagnostic, PREFIX as DIAGNOSTIC_PREFIX
                checked = validate_diagnostic(setting)
                if row['key'] != DIAGNOSTIC_PREFIX + checked['id']:
                    _fail('检查记录标识不匹配。')
            if row['key'].startswith('retention_reservation_v1:'):
                from .retention_session import validate_reservation, RESERVATION_PREFIX
                checked = validate_reservation(setting)
                if row['key'] != RESERVATION_PREFIX + checked['unit_id']:
                    _fail('检查预约标识不匹配。')
            if row['key'] == 'retention_active_v2':
                from .retention_session import _identity
                if setting is not None and not _identity(setting):
                    _fail('当前检查标识不合法。')
            if row['key'].startswith('retention_handoff_v1:'):
                from .retention_session import validate_handoff, handoff_key
                checked = validate_handoff(setting)
                if row['key'] != handoff_key(checked['question_id']):
                    _fail('检查转交标识不匹配。')
            if row['key'] == 'retention_activity_v1':
                from .retention_schedule import validate_activity
                validate_activity(setting)
            if row['key'] == 'retention_schedule_policy_v1':
                from .retention_schedule import validate_policy
                validate_policy(setting)
            if row['key'].startswith('retention_confirmation_v1:'):
                from .retention_schedule import validate_confirmation, PREFIX as CONFIRMATIONS
                checked = validate_confirmation(setting)
                if row['key'] != CONFIRMATIONS + checked['id']: _fail('确认安排标识不匹配。')
            if row['key'].startswith(('retention_activity_', 'retention_confirmation_', 'retention_schedule_policy_')):
                if row['key'] not in {'retention_activity_v1', 'retention_schedule_policy_v1'} and not row['key'].startswith('retention_confirmation_v1:'):
                    _fail('不支持这个版本的复学安排。')
            if row['key'].startswith(('retention_check_', 'retention_reservation_', 'retention_active_', 'retention_exposure_', 'retention_handoff_')):
                if row['key'] != 'retention_active_v2' and not row['key'].startswith((
                        'retention_check_v1:', 'retention_check_v2:', 'retention_reservation_v1:', 'retention_exposure_v1:', 'retention_handoff_v1:')):
                    _fail('不支持这个版本的检查记录。')
            if row["key"].startswith("retention_exposure_v1:"):
                from .retention_check import validate_exposure, EXPOSURE_PREFIX
                from hashlib import sha256
                checked = validate_exposure(setting)
                if row["key"] != EXPOSURE_PREFIX + sha256(checked['target_id'].encode()).hexdigest():
                    _fail("检查曝光记录标识不匹配。")
            if row["key"] == "ai_practice_v1":
                from .ai_practice_store import validate_ai_practice_value
                validate_ai_practice_value(setting)
            if row["key"] == "ai_tutor_v1":
                from .tutor_store import validate_tutor_value
                validate_tutor_value(setting)
            if row["key"] == "ai_question_preview_v1":
                from .ai_ui_state import validate_ai_preview_value
                validate_ai_preview_value(setting)
            if row["key"] == "ai_form_drafts_v1":
                from .ai_ui_state import validate_ai_form_value
                validate_ai_form_value(setting)
            if row["key"] == "word_drill_preferences" and isinstance(setting, dict) and "direction" in setting:
                # Like the other persisted picker preferences, this stores a
                # display label; question config keeps the internal enum.
                from .word_romaji_practice import WORD_DRILL_DIRECTIONS
                if setting["direction"] not in WORD_DRILL_DIRECTIONS:
                    _fail("单词练习方向设置不正确。")
        Scheduler.from_dict(_json(scheduler[0]))
        sessions = {}
        for row in tables["study_session"]:
            _date(row["started_at_utc"])
            sessions[row["session_id"]] = record_transport.QuestionIndex(
                temporary, row["session_id"], _questions, cancel_check=check)
        cards = record_transport.RowIndex(temporary, "study_card", "card_id")
        for row in tables["study_card"]:
            _validate_key(row["content_id"], row["skill"], row["script"])
            if row["skill"] == "word_romaji":
                _fail("反向词例练习不能建立独立记忆卡片。")
            if row["card_id"] != store._card_key(row["content_id"], row["skill"], row["script"]):
                _fail("复习卡片标识不匹配。")
            if row["scheduler_version"] != ALGORITHM_VERSION:
                _fail("卡片复习算法不兼容。")
            card = Card.from_dict(_json(row["scheduler_state_json"]))
            _date(row["due_at_utc"])
            if card.due != datetime.fromisoformat(row["due_at_utc"]):
                _fail("卡片到期时间不一致。")
            _date(row["introduced_at_utc"], optional=True)
            _integer(row["review_count"])
            _integer(row["suspended"], maximum=1)
        events = record_transport.RowIndex(temporary, "review_event", "event_id")
        for row in tables["review_event"]:
            _date(row["occurred_at_utc"])
            _integer(row["rating"], 1, 4)
            _integer(row["assisted"], maximum=1)
            _integer(row["duration_ms"])
            if row["algorithm_version"] != ALGORITHM_VERSION:
                _fail("复习记录算法不兼容。")
            Card.from_dict(_json(row["state_before_json"]))
            Card.from_dict(_json(row["state_after_json"]))
            review_log = _json(row["review_log_json"])
            if not isinstance(review_log, dict):
                _fail("复习记录格式不正确。")
            if "finalized_at_utc" in review_log:
                _date(review_log["finalized_at_utc"])
                if datetime.fromisoformat(review_log["finalized_at_utc"]) < datetime.fromisoformat(row["occurred_at_utc"]):
                    _fail("保存时间不能早于实际作答时间。")
        for row in tables["practice_attempt"]:
            _validate_key(row["content_id"], row["skill"], row["script"])
            _date(row["occurred_at_utc"])
        from .kana_routing import validate_database as validate_kana_routes, validate_review_route
        kana_routes = validate_kana_routes(temporary)
        for row in tables["session_answer"]:
            questions = sessions[row["session_id"]]
            _integer(row["question_index"], maximum=len(questions) - 1)
            _integer(row["correct"], maximum=1)
            event = events[row["event_id"]]
            question, card = questions[row["question_index"]], cards[event["card_id"]]
            if question.get("metadata", {}).get("word_romaji_drill") is True:
                _fail("独立词例练习不能包含正式卡片评分。")
            routed = validate_review_route(event, card, question, kana_routes)
            if event["session_id"] != row["session_id"] or (not routed and (question["content_id"], question["kind"], question["script"]) != (card["content_id"], card["skill"], card["script"])):
                _fail("已完成题目与复习记录不一致。")
        adjusted_readings = set()
        for row in tables["word_spelling_attempt"]:
            _validate_key(row["content_id"], row["skill"], row["script"])
            if row["skill"] not in WORD_SKILLS:
                _fail("词例练习的题型不正确。")
            _date(row["occurred_at_utc"])
            _integer(row["assisted"], maximum=1)
            _integer(row["correct"], maximum=1)
            _integer(row["duration_ms"])
            if not isinstance(row["answered_text"], str) or len(row["answered_text"]) > 10000:
                _fail("词例练习的答案不正确。")
            questions = sessions[row["session_id"]]
            _integer(row["question_index"], maximum=len(questions) - 1)
            question = questions[row["question_index"]]
            if (question["content_id"], question["kind"], question["script"]) != (row["content_id"], row["skill"], row["script"]):
                _fail("词例作答与题目不一致。")
            if temporary.execute("SELECT 1 FROM session_answer WHERE session_id=? AND question_index=?",
                    (row["session_id"], row["question_index"])).fetchone():
                _fail("同一题目包含重复的完成记录。")
            evidence = _json(row["evidence_json"])
            unit_fields = {"romaji_unit_version", "romaji_parts", "romaji_unit_plan"}
            unit_record = isinstance(evidence, dict) and bool(unit_fields & set(evidence))
            if unit_record:
                if (question["kind"] != "word_romaji"
                        or not unit_fields <= set(evidence)
                        or type(evidence["romaji_unit_version"]) is not int or evidence["romaji_unit_version"] != 1
                        or not isinstance(evidence["romaji_parts"], list)):
                    _fail("逐假名作答证据不正确。")
            # A saved observation is authoritative; current grading policy must
            # not rewrite or reject an earlier valid judgement.
            accepted = bool(row["correct"])
            if row["assisted"] and accepted:
                _fail("提示作答不能记为独立答对。")
            required = {"version", "expected", "actual", "script", "assisted", "correct", "units", "errors",
                        "ambiguous", "evidence_limited", "source_content_id", "source_kind", "source_question_index"}
            if (not isinstance(evidence, dict) or not required <= set(evidence)
                    or type(evidence["version"]) is not int or evidence["version"] != 1
                    or evidence["expected"] != word_kana_answer(question)
                    or evidence["actual"] != normalize_answer(row["answered_text"])
                    or evidence["script"] != row["script"]
                    or evidence["source_content_id"] != row["content_id"]
                    or evidence["source_kind"] != row["skill"]
                    or type(evidence["source_question_index"]) is not int
                    or evidence["source_question_index"] != row["question_index"]
                    or any(type(evidence[key]) is not bool for key in ("assisted", "correct", "ambiguous", "evidence_limited"))
                    or evidence["assisted"] != bool(row["assisted"])
                    or not row["assisted"] and evidence["correct"] != accepted
                    or not isinstance(evidence["units"], list) or not isinstance(evidence["errors"], list)
                    or row["assisted"] and evidence["units"]):
                _fail("词例假名证据不一致。")
            reverse = question["kind"] == "word_romaji"
            if "used_conversion" in evidence or "input_method" in evidence:
                if (evidence.get("used_conversion") is not True
                        or evidence.get("input_method") != "romaji_conversion_v1" or reverse
                        or not evidence["evidence_limited"] or evidence["units"]
                        or evidence.get("schedule_adjustments")):
                    _fail("罗马音转换作答不能记为独立回忆。")
            if reverse:
                if (evidence.get("answer_direction") != "romaji"
                        or evidence.get("expected_romaji") != question["answer"]
                        or evidence["correct"] != accepted):
                    _fail("反向词例证据与题目不一致。")
            elif "answer_direction" in evidence or "expected_romaji" in evidence:
                _fail("词例证据的方向不正确。")
            drill = question.get("metadata", {}).get("word_romaji_drill") is True
            context_marker = question.get("metadata", {}).get("kana_context_v1")
            context = context_marker is not None
            if context:
                from .kana_context_words import validate_context_marker
                validate_context_marker(context_marker)
                if (drill or question["kind"] not in {"word_meaning_kana", "word_kana"}
                        or question.get("metadata", {}).get("not_before_utc")
                        or evidence.get("kana_context_v1") != context_marker):
                    _fail("假名词例证据与来源题目不一致。")
            elif "kana_context_v1" in evidence:
                _fail("假名词例证据缺少来源标记。")
            if (((drill or context) and evidence.get("practice_policy") != WORD_ROMAJI_POLICY)
                    or (not (drill or context) and "practice_policy" in evidence)):
                _fail("词例练习权重与来源题目不一致。")
            _validate_saved_units(evidence["units"])
            if reverse and unit_record:
                from .word_romaji_units import validate_romaji_unit_plan, compose_romaji_parts
                validate_romaji_unit_plan(evidence["expected"], evidence["romaji_unit_plan"])
                if compose_romaji_parts(evidence["romaji_unit_plan"], evidence["romaji_parts"]) != evidence["actual"]:
                    _fail("逐假名作答与原始选择不一致。")
            _validate_interval_adjustments(row, evidence, drill=drill or context,
                cards=cards, events=events,
                adjusted_readings=adjusted_readings)
        checkpoint_lanes = set()
        for row in tables["session_checkpoint"]:
            lane = session_practice_lane(sessions[row["session_id"]])
            if lane in checkpoint_lanes:
                _fail("同一练习入口包含多个当前练习。")
            checkpoint_lanes.add(lane)
            count = len(sessions[row["session_id"]])
            _date(row["updated_at_utc"])
            _integer(row["question_index"], maximum=count - 1)
            _integer(row["session_right"], maximum=row["question_index"])
            assisted = _json(row["assisted_indices_json"])
            if not isinstance(assisted, list):
                _fail("提示记录不正确。")
            for index in assisted:
                _integer(index, maximum=count - 1)
            state = _json(row["state_json"])
            validate_session_state(state, sessions[row["session_id"]][row["question_index"]])
            if temporary.execute("SELECT 1 FROM session_answer WHERE session_id=? AND question_index>=?", (row["session_id"], row["question_index"])).fetchone():
                _fail("练习进度指向已完成的题目。")
            if temporary.execute("SELECT 1 FROM word_spelling_attempt WHERE session_id=? AND question_index>=?", (row["session_id"], row["question_index"])).fetchone():
                _fail("练习进度指向已完成的词例。")
        if temporary.execute("PRAGMA foreign_key_check").fetchone():
            _fail("数据关系不完整。")
        from .domain import WORD_KIND_PAUSE
        checkpoints_by_session = {row["session_id"]: row for row in tables["session_checkpoint"]}
        for sid, questions in sessions.items():
            parked = [index for index, question in enumerate(questions)
                      if WORD_KIND_PAUSE in question.get("metadata", {})]
            if not parked:
                continue
            checkpoint = checkpoints_by_session.get(sid)
            # An explicitly abandoned/replaced queue may keep its ungraded
            # draft in history. Only a live checkpoint can reactivate it.
            if checkpoint is not None and any(index < checkpoint["question_index"] for index in parked):
                _fail("暂停词例不属于当前未完成题单。")
            answered = {row[0] for row in temporary.execute(
                "SELECT question_index FROM session_answer WHERE session_id=? UNION ALL "
                "SELECT question_index FROM word_spelling_attempt WHERE session_id=?", (sid, sid))}
            if answered.intersection(parked):
                _fail("已完成的词例不能保留暂停草稿。")
        try:
            validate_backup_rows(temporary)
            learning_evidence.validate_backup_rows(temporary, kana_routes=kana_routes)
            tutor_memory.validate_database(temporary)
            from .retention_session import validate_database as validate_diagnostics
            validate_diagnostics(temporary)
            from .retention_schedule import validate_database as validate_confirmations
            validate_confirmations(temporary)
            from .material_workflow import validate_database as validate_material_tasks
            validate_material_tasks(temporary)
            from .material_budget import validate_database as validate_material_budgets
            validate_material_budgets(temporary)
            from .material_verification import validate_database as validate_material_reports
            validate_material_reports(temporary)
            from .material_reference import validate_database as validate_material_references
            validate_material_references(temporary)
            from .material_revision import validate_database as validate_material_revisions
            validate_material_revisions(temporary)
            from .material_usage import validate_database as validate_material_usage
            validate_material_usage(temporary)
            from .material_source_resolution import validate_database as validate_material_sources
            validate_material_sources(temporary)
            from .material_study import validate_database as validate_material_learning
            validate_material_learning(temporary)
            from .material_pretest import validate_database as validate_material_pretests
            validate_material_pretests(temporary)
            from .material_senses import validate_database as validate_material_senses
            validate_material_senses(temporary)
            from .material_validity import validate_database as validate_material_validity
            validate_material_validity(temporary)
            from .material_admission import validate_database as validate_material_admissions
            validate_material_admissions(temporary)
            from .retention_ai import validate_database as validate_retention_ai
            validate_retention_ai(temporary)
            from .retention_scope_form import validate_database as validate_scope_forms
            validate_scope_forms(temporary)
            from .retention_scope_ai import validate_database as validate_scope_ai
            validate_scope_ai(temporary)
            from .retention_dialogue import validate_database as validate_diagnostic_dialogue
            validate_diagnostic_dialogue(temporary)
            from .feedback_analysis import validate_database as validate_feedback_analysis
            validate_feedback_analysis(temporary)
            from .feedback_batch import validate_database as validate_feedback_batch
            validate_feedback_batch(temporary)
            from .feedback_review import validate_database as validate_feedback_review
            from .feedback_sources import validate_database as validate_feedback_sources
            validate_feedback_sources(temporary)
            validate_feedback_review(temporary)
            from .feedback_revision import validate_database as validate_feedback_revision
            validate_feedback_revision(temporary)
            from .feedback_impact import validate_database as validate_feedback_impact
            validate_feedback_impact(temporary)
            from .feedback_field_review import validate_database as validate_feedback_field_review
            validate_feedback_field_review(temporary)
            from .content_correction_plan import validate_database as validate_correction_plans
            validate_correction_plans(temporary)
            from .personal_content_notes import validate_database as validate_personal_notes
            validate_personal_notes(temporary)
            from .feedback_field_support import validate_database as validate_field_support
            validate_field_support(temporary)
            from .personal_content_versions import validate_database as validate_personal_versions
            validate_personal_versions(temporary)
            from .content_history import validate_database as validate_content_history
            validate_content_history(temporary)
            from .content_queue import validate_database as validate_content_queues
            validate_content_queues(temporary)
            from .diagnostic_replacement import validate_database as validate_diagnostic_replacements
            validate_diagnostic_replacements(temporary)
            from .kana_rule_evidence import validate_probe_history
            validate_probe_history(temporary)
            from .retention_sources import validate_database as validate_retention_sources
            validate_retention_sources(temporary)
            from .material_share import validate_database as validate_material_shares
            validate_material_shares(temporary)
            for row in tables["word_spelling_attempt"]:
                for adjustment in _json(row["evidence_json"]).get("schedule_adjustments", []):
                    evidence_id = adjustment.get("evidence_event_id")
                    if evidence_id is not None:
                        actual = temporary.execute("SELECT * FROM learning_aux_adjustment WHERE event_id=? AND card_id=?",
                            (evidence_id, adjustment["card_id"])).fetchone()
                        if actual is None or any(actual[key] != adjustment[key] for key in
                                ("anchor_event_id", "before_due_at_utc", "after_due_at_utc", "base_due_at_utc", "weight")):
                            raise ValueError("词例辅助记录没有对应的证据账本")
        except (ValueError, TypeError, KeyError) as exc:
            _fail("词汇／语法学习数据损坏：" + str(exc))
        active_card_ids = {row["card_id"] for row in tables["study_card"] if row["skill"] not in WORD_SKILLS}
        summary = {"created_at_utc": payload["created_at_utc"], "cards": len(active_card_ids),
                   "reviews": sum(row["card_id"] in active_card_ids for row in tables["review_event"]),
                   "sessions": len(tables["study_session"]),
                   "word_exercises": len(tables["word_spelling_attempt"]),
                   "has_unfinished_session": bool(tables["session_checkpoint"])}
        summary.update(learning_content=len(tables["learning_content"]),
                           learning_reviews=len(tables["learning_event"]),
                           learning_pending=sum(row["status"] == "pending" for row in tables["learning_question"]))
        temporary.commit()
        tables = staging.populate(all_tables)
        keep_staging = True
        return tables, summary, schemas
    except (sqlite3.Error, TypeError, KeyError, OverflowError) as exc:
        raise ValueError("备份无效：数据损坏或不兼容。") from exc
    finally:
        if staging is not None:
            if not keep_staging:
                staging.close()
        else:
            temporary.close()


def inspect_backup(store, data):
    tables, summary, _ = _validated(store, data)
    try:
        return summary
    finally:
        if isinstance(tables, record_transport.DiskTables):
            tables.close()


def import_backup(store, data, *, cancel_check=None, on_committed=None):
    tables, summary, schemas = _validated(store, data, cancel_check=cancel_check)
    try:
        return _import_validated(store, tables, summary, schemas, cancel_check=cancel_check, on_committed=on_committed)
    finally:
        if isinstance(tables, record_transport.DiskTables):
            tables.close()


def _import_validated(store, tables, summary, schemas, *, cancel_check=None, on_committed=None):
    from .domain import utc_now
    from .learning_store import TABLES as LEARNING_TABLES
    check = cancel_check or (lambda: None)
    check()
    # Keep the original records recoverable, including when the user chooses
    # the wrong valid backup. The live SQLite file is never replaced while open.
    rollback = store.data_dir / ("learning-before-restore-" + utc_now().strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8] + ".json")
    with store._db:
        store._db.execute("BEGIN IMMEDIATE")
        # A concurrent writer cannot create records after the recovery snapshot
        # and before replacement. Never bypass recovery if its export fails.
        original = _export_locked_snapshot(store)
        from .material_budget import snapshot as budget_snapshot, merge_after_restore
        retained_budgets = budget_snapshot(store._db)
        check()
        _write_recovery_file(rollback, original)
        del original
        check()
        # Trusted app-owned DDL, never backup SQL. SQLite rolls these additions
        # back with the rows if cancellation or insertion fails later.
        for kind, name, sql in schemas:
            if store._db.execute('SELECT 1 FROM sqlite_master WHERE type=? AND name=?', (kind,name)).fetchone() is None:
                store._db.execute(sql)
        for name in reversed((*TABLES, *LEARNING_TABLES, *_teaching_tables())):
            store._db.execute("DELETE FROM " + name)
        for name in (*TABLES, *LEARNING_TABLES, *_teaching_tables()):
            check()
            for index, row in enumerate(tables[name]):
                if index % 256 == 0:
                    check()
                columns = list(row)
                store._db.execute("INSERT INTO " + name + " (" + ",".join(columns) + ") VALUES (" + ",".join("?" for _ in columns) + ")", list(row.values()))
        merge_after_restore(store._db, retained_budgets)
        from .semantic_errors import rebuild
        rebuild(store._db)
        check()
    if on_committed is not None:
        on_committed({**summary, "rollback_path": str(rollback)})
    store.scheduler = Scheduler.from_dict(store.get_setting("scheduler"))
    store.reload_daily_new_limit()
    store.reset_choice_history()
    return {**summary, "rollback_path": str(rollback)}


def _write_recovery_file(path, data):
    # Shorter than the final name: do not introduce a new Windows path limit.
    temporary = path.with_name(".learning-" + uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            # Never mask the original failure; .tmp is not a recoverable file.
            pass
