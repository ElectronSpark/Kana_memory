"""Bounded resumable tutoring, with auditable auxiliary evidence.

Only independently answered reviewed local questions can adjust a future due
date through the shared auxiliary budget; they never create an FSRS review.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from . import app_clock
from hashlib import sha256
import json
import re
import unicodedata
from uuid import uuid4

from .learning_questions import KIND_ABILITIES, grade_learning_answer

SETTING = "ai_tutor_v1"
FORMAT = "kanamemory-ai-tutor"
MAX_ENTRIES = 200
MAX_MESSAGES = 60
MAX_SEEN = 2000
MAX_BYTES = 8 * 1024 * 1024
MAX_CHAT_DRAFT = 65536
COOLDOWN_SECONDS = 60
REPEAT_COOLDOWN = timedelta(hours=24)
_FIELDS = {"format", "version", "session_id", "revision", "entries", "messages", "request",
           "exposures", "seen", "chat_draft", "chat_context", "completed_count"}
_ROW_FIELDS = {"id", "question", "source", "fingerprint", "draft", "result", "status",
               "created_at_utc", "completed_at_utc"}
_RESULT_FIELDS = {"question_id", "answered_text", "answer", "explanation", "correct", "outcome",
                  "assisted", "revealed", "scheduled", "training_only", "submitted_at_utc",
                  "error_category"}
_REQUEST_FIELDS = {"request_id", "intent", "context_fingerprint", "status", "response", "created_at_utc"}
_SECRET = re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{16,}")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _text(value, maximum=4000, *, blank=False):
    if (not isinstance(value, str) or len(value) > maximum or (not blank and not value.strip())
            or any((unicodedata.category(c) in {"Cc", "Cf"} and c not in "\n\t")
                   or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise ValueError("辅导文字格式不正确。")
    return value


def _date(value):
    _text(value, 80)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("辅导记录时间缺少时区。")
    return parsed


def _now(value=None):
    value = value or app_clock.utc_now()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("辅导记录时间缺少时区。")
    return value.astimezone(timezone.utc)


def _stamp(now=None, *prior):
    return max([_now(now), *[_date(p) for p in prior if p]]).isoformat()


def _empty():
    return {"format": FORMAT, "version": 1, "session_id": "", "revision": 0,
            "entries": [], "messages": [], "request": None, "exposures": {}, "seen": [],
            "chat_draft": "", "chat_context": None, "completed_count": 0}


def question_fingerprint(question):
    """A different variant ID or shuffled options does not make a fresh task."""
    def normal(text):
        return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text))).casefold()
    data = [question.get("target_id"), str(question.get("revision")), question.get("kind"),
            normal(question.get("prompt")), normal(question.get("audio_text", ""))]
    return sha256(_json(data).encode()).hexdigest()


def question_seen_recently(question, state, now=None):
    """Keep pending drafts intact, but permit spaced reuse of finite templates."""
    fingerprint = question_fingerprint(question)
    entries = [row for row in state.get("entries", []) if row.get("fingerprint") == fingerprint]
    if entries and all(row.get('status') == 'retired' for row in entries):
        return False
    entries = [row for row in entries if row.get('status') != 'retired']
    if any(row.get("status") != "completed" for row in entries):
        return True
    if entries:
        latest = max(_date(row["completed_at_utc"]) for row in entries)
        return latest + REPEAT_COOLDOWN > _now(now)
    if fingerprint not in state.get("seen", []):
        return False
    retained = state.get("entries", [])
    # Legacy seen-only fingerprints have no precise timestamp. Retention order
    # provides a conservative lower bound before permitting a fresh attempt.
    return not retained or min(_date(row["created_at_utc"]) for row in retained) + REPEAT_COOLDOWN > _now(now)


def question_available(question, scope, enabled_kinds):
    if not isinstance(scope, dict):
        return False
    target = next((row for row in scope.get("targets", []) if row.get("id") == question.get("target_id")), None)
    kind = question.get("kind")
    return bool(target and kind in enabled_kinds and kind in target.get("allowed_kinds", [])
                and str(target.get("revision")) == str(question.get("revision"))
                and KIND_ABILITIES.get(kind) in target.get("abilities", []))


def _question(question, source):
    if source == "ai":
        from .ai_questions import validate_ai_question_snapshot
        return validate_ai_question_snapshot(question)
    if source != "local" or not isinstance(question, dict):
        raise ValueError("辅导题目来源不正确。")
    for key in ("id", "question_id", "target_id", "revision", "kind", "ability", "family", "prompt", "answer"):
        _text(question.get(key), 4000)
    kind = question["kind"]
    if (kind not in KIND_ABILITIES or question["ability"] != KIND_ABILITIES[kind]
            or question["family"] not in {"grammar", "word"}
            or question.get("id") != question.get("question_id")
            or question.get("scheduled") is not False or question.get("tutor_practice") is not True
            or question.get("evidence_mode") != "exposure_only" or question.get("guided") is not False
            or question.get("ai_generated") is True or type(question.get("variant")) is not int
            or question["variant"] < 0 or question.get("input_mode") not in {"choice", "text", "reorder", "pitch_fill", "pitch_identify", "multi_cloze"}):
        raise ValueError("辅导题目不能冒充独立复习证据。")
    for key in ("choices", "options", "accepted_answers"):
        rows = question.get(key, [])
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError("辅导选项格式不正确。")
        for text in rows:
            _text(text, 4000)
    if question["input_mode"] == "choice" and (len(question.get("choices", [])) < 2
            or question["answer"] not in question["choices"]):
        raise ValueError("辅导选择题没有有效答案。")
    if len(_json(question).encode()) > 64 * 1024:
        raise ValueError("辅导题目过大。")
    from .pitch_practice import validate_snapshot
    validate_snapshot(question)
    from .multi_cloze import validate_snapshot as validate_cloze_snapshot
    validate_cloze_snapshot(question)
    return deepcopy(question)


def _draft(value, question=None):
    if not isinstance(value, dict) or set(value) - {"answer", "parts", "assisted", "hint_used", "audio_ok", "presented", "pitch_state", "cloze_state"}:
        raise ValueError("辅导草稿字段不正确。")
    if "answer" in value:
        _text(value["answer"], 4000, blank=True)
    for key in ("assisted", "hint_used", "audio_ok", "presented"):
        if key in value and type(value[key]) is not bool:
            raise ValueError("辅导草稿状态不正确。")
    if "parts" in value and (not isinstance(value["parts"], list) or len(value["parts"]) > 100
            or any(type(i) is not int or not 0 <= i < 100 for i in value["parts"])):
        raise ValueError("辅导排列草稿不正确。")
    parts = value.get("parts", [])
    if len(parts) != len(set(parts)) or question is not None and (any(i >= len(question.get("choices", [])) for i in parts)
            or parts and question.get("input_mode") != "reorder"):
        raise ValueError("辅导排列草稿超出本题范围。")
    from .pitch_practice import is_pitch_question, validate_draft
    if "pitch_state" in value:
        if not isinstance(value["pitch_state"], dict) or len(_json(value).encode()) > 64000:
            raise ValueError("音高草稿格式不正确。")
        if question is not None and not is_pitch_question(question):
            raise ValueError("音高草稿缺少对应题目。")
        if question is not None:
            validate_draft(question, value)
    if 'cloze_state' in value:
        if not isinstance(value['cloze_state'], dict) or len(_json(value).encode()) > 64000:
            raise ValueError('多空草稿格式不正确。')
        if question is not None:
            from .multi_cloze import validate_draft as validate_cloze_draft
            validate_cloze_draft(question, value)
    return deepcopy(value)


def _response(value, intent):
    required = {"action", "candidate_id", "target_id", "text", "question"}
    if (not isinstance(value, dict) or not required <= set(value) or set(value) - required - {"memory_updates"}
            or value["action"] not in {"retrieve", "generate", "explain"}
            or intent in {"ask", "explain"} and value["action"] != "explain"):
        raise ValueError("辅导响应字段不正确。")
    if "memory_updates" in value:
        from .tutor_memory import validate_updates_structure
        validate_updates_structure(value["memory_updates"])
    for field, maximum in (("candidate_id", 256), ("target_id", 256), ("text", 1600)):
        _text(value[field], maximum, blank=True)
    if value["action"] == "retrieve":
        if not value["candidate_id"] or not value["target_id"] or value["question"] is not None or value["text"]:
            raise ValueError("辅导调题响应不正确。")
    elif value["action"] == "generate":
        from .ai_questions import validate_ai_question_snapshot
        question = validate_ai_question_snapshot(value["question"])
        if value["candidate_id"] or value["text"] or question["target_id"] != value["target_id"]:
            raise ValueError("辅导生成响应不正确。")
    elif value["question"] is not None or value["candidate_id"] or not value["text"].strip():
        raise ValueError("辅导讲解响应不正确。")
    return deepcopy(value)


def _chat_context(value):
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {"source", "question_id", "target_id", "after_answer"}
            or value["source"] not in {"learning", "ai", "tutor", "general"}
            or type(value["after_answer"]) is not bool):
        raise ValueError("辅导提问位置不正确。")
    _text(value["question_id"], 256, blank=True)
    _text(value["target_id"], 256, blank=True)
    return deepcopy(value)


def _result(question, draft, answer, revealed, submitted, *, cancelled=False):
    judgement = ({"correct": None, "status": "cancelled"} if cancelled else
                 {"correct": None, "status": "revealed"} if revealed
                 else grade_learning_answer(question, answer))
    assisted = bool(revealed or draft.get("assisted") or draft.get("hint_used"))
    if question.get("requires_audio") and not draft.get("audio_ok") and not cancelled:
        judgement = {"correct": None, "status": "device_failure"}
    return {"question_id": question["id"], "answered_text": answer,
            "answer": question["answer"], "explanation": question.get("explanation", question.get("feedback", "")),
            "correct": judgement["correct"], "outcome": judgement["status"],
            "assisted": assisted, "revealed": revealed, "scheduled": False, "training_only": True,
            "submitted_at_utc": submitted, "error_category": judgement.get("error_category", ""),
            **{key: judgement[key] for key in ('slot_results', 'primary_correct', 'primary_scorable',
                'overall_correct', 'background_errors') if key in judgement}}


def validate_tutor_value(value):
    """Strict backup validation. It never enrolls, grades or changes a scheduler."""
    if (not isinstance(value, dict) or set(value) != _FIELDS or value.get("format") != FORMAT
            or type(value.get("version")) is not int or value["version"] != 1
            or type(value.get("revision")) is not int or value["revision"] < 0
            or type(value.get("completed_count")) is not int or not 0 <= value["completed_count"] <= 10**12):
        raise ValueError("辅导保存版本不受支持。")
    try:
        encoded = _json(value)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("辅导保存内容不正确。") from exc
    if len(encoded.encode()) > MAX_BYTES or _SECRET.search(encoded):
        raise ValueError("辅导保存内容过大或含敏感信息。")
    _text(value["session_id"], 100, blank=True)
    if not isinstance(value["entries"], list) or len(value["entries"]) > MAX_ENTRIES:
        raise ValueError("辅导记录数量过多。")
    identities, fingerprints, prior_attempts = set(), set(), {}
    pending = 0
    for row in value["entries"]:
        if (not isinstance(row, dict) or not _ROW_FIELDS <= set(row)
                or set(row) - _ROW_FIELDS - {"coached", "anchor_event_id", "content_retirement", "replacement_of", "opportunity_root"} or type(row.get("coached", False)) is not bool):
            raise ValueError("辅导记录字段不正确。")
        if "anchor_event_id" in row:
            _text(row["anchor_event_id"], 100, blank=True)
        for key in ('content_retirement', 'replacement_of', 'opportunity_root'):
            if key in row: _text(row[key], 100)
        _text(row["id"], 100)
        q = _question(row["question"], row["source"])
        if row["id"] in identities or row["fingerprint"] != question_fingerprint(q):
            raise ValueError("辅导题目标识重复或不匹配。")
        identities.add(row["id"]); fingerprints.add(row["fingerprint"])
        created = _date(row["created_at_utc"])
        previous = prior_attempts.get(row["fingerprint"])
        if previous is not None and not (previous['status'] == 'retired' and row.get('replacement_of') == previous['id']) and (
                previous["status"] != "completed" or _date(previous["completed_at_utc"]) + REPEAT_COOLDOWN > created):
            raise ValueError("同一辅导题目重复过早。")
        prior_attempts[row["fingerprint"]] = row
        _draft(row["draft"], q)
        if row["status"] not in {"pending", "completed", "retired"}:
            raise ValueError("辅导题目状态不正确。")
        if bool(row.get('content_retirement')) != (row['status'] == 'retired') or row['status'] == 'retired' and row['result'] is not None:
            raise ValueError('待核辅导题不能含作答结果。')
        pending += row["status"] == "pending"
        result = row["result"]
        if result is not None:
            from .multi_cloze import is_cloze_question
            extra = ({'slot_results', 'primary_correct', 'primary_scorable', 'overall_correct', 'background_errors'}
                     if is_cloze_question(q) else set())
            if (not isinstance(result, dict) or set(result) - extra != _RESULT_FIELDS
                    or type(result.get("revealed")) is not bool):
                raise ValueError("辅导结果字段不正确。")
            _text(result["answered_text"], 4000, blank=True)
            if _date(result["submitted_at_utc"]) < created:
                raise ValueError("辅导结果早于题目。")
            cancelled = result.get("outcome") == "cancelled"
            if cancelled and (result["revealed"] or result["answered_text"] != row["draft"].get("answer", "")):
                raise ValueError("取消的辅导不能包含评分或答案曝光。")
            if _result(q, row["draft"], result["answered_text"], result["revealed"], result["submitted_at_utc"], cancelled=cancelled) != result:
                raise ValueError("辅导结果被修改。")
        if row["status"] == "completed":
            if result is None or _date(row["completed_at_utc"]) < _date(result["submitted_at_utc"]):
                raise ValueError("辅导完成记录不正确。")
        elif row["completed_at_utc"] is not None:
            raise ValueError("未完成的辅导含完成时间。")
        if row.get("coached") and (result is None
                or result["outcome"] not in {"incorrect", "revealed", "unresolved"}):
            raise ValueError("辅导讲解标记与题目结果不符。")
    if pending > 1:
        raise ValueError("辅导有多道活动题目。")
    if value["completed_count"] < sum(r['status'] == 'completed' for r in value['entries']):
        raise ValueError("辅导完成计数不正确。")
    if not isinstance(value["messages"], list) or len(value["messages"]) > MAX_MESSAGES:
        raise ValueError("辅导对话过长。")
    message_ids = set()
    for row in value["messages"]:
        if (not isinstance(row, dict) or set(row) - {"related_target_ids"} != {"id", "role", "text", "target_id", "turn_id", "created_at_utc"}
                or row["role"] not in {"user", "assistant"} or row["id"] in message_ids):
            raise ValueError("辅导对话格式不正确。")
        for key, maximum in (("id", 100), ("text", 4000), ("target_id", 256), ("turn_id", 100)):
            _text(row[key], maximum, blank=key in {"target_id", "turn_id"})
        _date(row["created_at_utc"])
        if 'related_target_ids' in row:
            related = row['related_target_ids']
            if (not isinstance(related, list) or len(related) > 32
                    or any(not isinstance(cid, str) for cid in related) or len(set(related)) != len(related)):
                raise ValueError('辅导对话关联范围不正确。')
            for cid in related:
                _text(cid, 256)
        message_ids.add(row["id"])
    request = value["request"]
    if request is not None:
        if (not isinstance(request, dict) or set(request) != _REQUEST_FIELDS
                or request["intent"] not in {"next", "ask", "explain"}
                or request["status"] not in {"pending", "completed", "cancelled"}):
            raise ValueError("辅导请求格式不正确。")
        _text(request["request_id"], 100)
        if not isinstance(request["context_fingerprint"], str) or not re.fullmatch(r"[a-f0-9]{64}", request["context_fingerprint"]):
            raise ValueError("辅导请求上下文不正确。")
        _date(request["created_at_utc"])
        response = request["response"]
        if request["status"] == "completed":
            _response(response, request["intent"])
            if len(_json(response).encode()) > 128 * 1024:
                raise ValueError("辅导响应内容不正确。")
        elif response is not None:
            raise ValueError("未完成辅导请求含响应。")
    if not isinstance(value["exposures"], dict) or len(value["exposures"]) > 2000:
        raise ValueError("辅导曝光记录过大。")
    for target, stamp in value["exposures"].items():
        _text(target, 256); _date(stamp)
    if (not isinstance(value["seen"], list) or len(value["seen"]) > MAX_SEEN
            or len(set(value["seen"])) != len(value["seen"])
            or any(not isinstance(x, str) or not re.fullmatch(r"[a-f0-9]{64}", x) for x in value["seen"])
            or not fingerprints <= set(value["seen"])):
        raise ValueError("辅导去重记录不正确。")
    _text(value["chat_draft"], MAX_CHAT_DRAFT, blank=True)
    _chat_context(value["chat_context"])
    return deepcopy(value)


def exposure_times(store, now=None):
    """Read only the small guard map; no review date or checkpoint is written."""
    value = store.get_setting(SETTING, None)
    if value is None:
        return {}
    if not isinstance(value, dict) or value.get("format") != FORMAT or value.get("version") != 1:
        raise ValueError("辅导曝光记录版本不正确。")
    rows = value.get("exposures")
    if not isinstance(rows, dict) or len(rows) > 2000:
        raise ValueError("辅导曝光记录不正确。")
    current = _now(now)
    result = {}
    for target, stamp in rows.items():
        _text(target, 256)
        until = _date(stamp) + timedelta(seconds=COOLDOWN_SECONDS)
        if until > current:
            result[target] = until.isoformat()
    return result


class AITutorStore:
    def __init__(self, store):
        from .learning_store import LearningStore
        self.store = store
        self.learning = LearningStore(store)
        from . import tutor_memory
        tutor_memory.ensure_schema(store._db)

    def snapshot(self):
        return validate_tutor_value(self.store.get_setting(SETTING, _empty()))

    def _formal_anchor(self, question):
        from .learning_store import _unit_id
        return self.store.queries.latest_primary_event(_unit_id(question["target_id"], question["ability"]))

    def _available(self, question, scope, enabled_kinds):
        if not question_available(question, scope, enabled_kinds):
            return False
        from .learning_questions import question_kinds
        learning = self.learning
        item = learning.get_item(question["target_id"])
        instruction = learning.ability_instruction_row(question['target_id'], question['ability'])
        if question.get('material_qualification'):
            from .assessment_policy import assessment_decision
            if item is None or not assessment_decision(item, question, store=self.store)['assessment_eligible']:
                return False
        if (item is None or instruction['stage'] in {"intro", "guide"}
                or str(item["revision"]) != str(question["revision"])
                or question["kind"] not in question_kinds(item)
                or learning.prerequisite_status(item["id"], dict(instruction).get('instruction_scope'))["blocked"]):
            return False
        prefs = self.store.get_setting("learning_preferences_v1", {})
        from .learning_practice_policy import practice_metadata
        from .learning_dependencies import independent_ready_ids
        meta = practice_metadata(question)
        if ("practice" in question and (meta is None or not set(meta["background_ids"])
                <= independent_ready_ids(learning) | {item["id"]})):
            return False
        from .learning_questions import stored_enabled_kinds
        if isinstance(prefs, dict) and isinstance(prefs.get("enabled"), list) and question["kind"] not in stored_enabled_kinds(prefs):
            return False
        unit = learning.get_unit(item["id"], question["ability"])
        return bool(unit and not unit.get("suspended") and unit.get("instruction_ready")
            and unit.get("review_count") and self.store._db.execute("""SELECT 1 FROM learning_event
                WHERE unit_id=? AND role='primary' AND assisted=0 AND outcome IN ('correct','incorrect') LIMIT 1""",
                (unit["unit_id"],)).fetchone())

    def _mutate(self, operation):
        db = self.store._db
        if db.in_transaction:
            raise RuntimeError("Tutor writes require their own transaction")
        with db:
            db.execute("BEGIN IMMEDIATE")
            state = self.snapshot()
            before = deepcopy(state)
            result = operation(state)
            if state != before:
                state["revision"] += 1
                validate_tutor_value(state)
                db.execute("INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                           (SETTING, _json(state)))
        return deepcopy(result)

    @staticmethod
    def _row(state, row_id):
        row = next((entry for entry in state["entries"] if entry["id"] == row_id), None)
        if row is None:
            raise KeyError(row_id)
        return row

    @staticmethod
    def _expose(state, target, now=None):
        _text(target, 256)
        current = _now(now)
        stamp = _stamp(current, state["exposures"].get(target))
        state["exposures"] = {cid: prior for cid, prior in state["exposures"].items()
                              if _date(prior) + timedelta(seconds=COOLDOWN_SECONDS) > current}
        state["exposures"][target] = stamp

    def start(self):
        def operation(state):
            if not state["session_id"]:
                state["session_id"] = uuid4().hex
            return state["session_id"]
        self._mutate(operation)
        return self.snapshot()

    def current(self, enabled_kinds=None, scope=None):
        row = next((row for row in self.snapshot()["entries"] if row["status"] == "pending"), None)
        if row and enabled_kinds is not None and (row["question"]["kind"] not in enabled_kinds
                or scope is not None and not self._available(row["question"], scope, enabled_kinds)):
            return None
        return row

    def append_question(self, question, scope, enabled_kinds, *, source="local", now=None):
        question = _question(question, source)
        if not self._available(question, scope, enabled_kinds):
            raise ValueError("题目已不符合当前学习范围或题型设置。")
        if source == "local":
            from .tutor_engine import local_question
            item = self.learning.get_item(question["target_id"])
            if item is None or local_question(item, question["kind"], question["variant"]) != question:
                raise ValueError("本地辅导题目与已学习内容不一致。")
        def operation(state):
            fingerprint = question_fingerprint(question)
            active = next((row for row in state["entries"] if row["status"] == "pending"), None)
            if active is not None:
                if active["fingerprint"] == fingerprint:
                    return active
                raise ValueError("请先完成当前辅导题，原题和草稿已保留。")
            if question_seen_recently(question, state, now):
                raise ValueError("本轮已练过这道题，请换一个例子。")
            exposure = state["exposures"].get(question["target_id"])
            if exposure and _date(exposure) + timedelta(seconds=COOLDOWN_SECONDS) > _now(now):
                raise ValueError("这项内容刚看过答案或讲解，请先练习其他内容。")
            if not state["session_id"]:
                state["session_id"] = uuid4().hex
            state["entries"] = state["entries"][-(MAX_ENTRIES - 1):]
            row = {"id": uuid4().hex, "question": question, "source": source, "fingerprint": fingerprint,
                   "draft": {"presented": False}, "result": None, "status": "pending", "created_at_utc": _stamp(now),
                   "completed_at_utc": None, "coached": False}
            anchor = self._formal_anchor(question)
            row["anchor_event_id"] = anchor["event_id"] if anchor else ""
            replaced = {r.get('replacement_of') for r in state['entries']}
            prior = next((r for r in reversed(state['entries']) if r['status'] == 'retired' and r['id'] not in replaced
                and (r['question']['target_id'], r['question']['ability']) == (question['target_id'], question['ability'])), None)
            if prior:
                row.update(replacement_of=prior['id'], opportunity_root=prior.get('opportunity_root', prior['id']),
                           anchor_event_id=prior.get('anchor_event_id', ''))
                row['draft'].update({k: True for k in ('assisted', 'hint_used') if prior['draft'].get(k)})
            state["entries"].append(row)
            state["seen"] = [*[fp for fp in state["seen"] if fp != fingerprint], fingerprint][-MAX_SEEN:]
            return row
        return self._mutate(operation)

    def save_draft(self, row_id, state):
        draft = _draft(state)
        def operation(value):
            row = self._row(value, row_id)
            _draft(draft, row["question"])
            if row["status"] == "pending" and row["result"] is None:
                old = row["draft"]
                draft.update({key: True for key in ("assisted", "hint_used", "presented") if old.get(key)})
                old.update(draft)
            return row["draft"]
        return self._mutate(operation)

    def submit(self, row_id, answer, enabled_kinds, scope, *, revealed=False, now=None):
        _text(answer, 4000, blank=True)
        if type(revealed) is not bool:
            raise ValueError("辅导答案状态不正确。")
        def operation(state):
            row = self._row(state, row_id)
            if row['status'] == 'retired':
                raise ValueError('这道题的依据已修订，原题与草稿已保存，请继续当前题。')
            if row["result"] is not None:
                return row["result"]
            q = row["question"]
            if not self._available(q, scope, enabled_kinds):
                raise ValueError("题目已不符合当前学习范围或题型设置。")
            if not revealed and not answer.strip():
                raise ValueError("请先作答。")
            if not revealed and q.get("input_mode") == "choice" and answer not in q["choices"]:
                raise ValueError("请选择本题中的一个答案。")
            from .pitch_practice import is_pitch_question, grade_answer
            if (not revealed and is_pitch_question(q)
                    and grade_answer(q, answer).get("status") in {"empty", "invalid"}):
                raise ValueError("请先完成音高作答。")
            from .multi_cloze import is_cloze_question, grade_answer as grade_cloze_answer
            if (not revealed and is_cloze_question(q)
                    and grade_cloze_answer(q, answer).get('status') in {'empty', 'invalid'}):
                raise ValueError('请先完成各空。')
            row["result"] = _result(q, row["draft"], answer, revealed, _stamp(now, row["created_at_utc"]))
            from .learning_evidence import LearningEvidenceStore, reviewed_question
            from .assessment_opportunity import identity
            from .assessment_policy import assessment_decision
            item = self.learning.get_item(q['target_id'])
            qualified = bool(item and assessment_decision(item, q, purpose='check_and_adjust', store=self.store)['assessment_eligible'])
            from .retention_sources import trusted_question
            anchor = self._formal_anchor(q)
            same_anchor = bool(anchor and (anchor["event_id"] == row["anchor_event_id"] if "anchor_event_id" in row
                else _date(anchor["occurred_at_utc"]) <= _date(row["created_at_utc"])))
            LearningEvidenceStore(self.store).record_attempt("tutor:" + row["id"], q, row["result"],
                source="tutor", role="auxiliary", now=_date(row["result"]["submitted_at_utc"]),
                trusted=row["source"] == "local" and same_anchor and qualified and trusted_question(self.store, q, item=item), adjust=True,
                opportunity_id=identity("tutor", row.get('opportunity_root', row["id"])), episode_id=state['session_id'])
            self._expose(state, q["target_id"], _date(row["result"]["submitted_at_utc"]))
            return row["result"]
        return self._mutate(operation)

    def complete(self, row_id, *, now=None):
        def operation(state):
            row = self._row(state, row_id)
            if row["result"] is None:
                raise ValueError("请先作答或查看答案。")
            if row["status"] != "completed":
                row.update(status="completed", completed_at_utc=_stamp(now, row["result"]["submitted_at_utc"]))
                state["completed_count"] += 1
            return row["result"]
        return self._mutate(operation)

    def skip_unavailable_input(self, row_id, *, now=None):
        """An explicit tool barrier is not an error, exposure or auxiliary review."""
        from .answer_input import available, system_japanese_enabled
        def operation(state):
            row = self._row(state, row_id)
            if row["result"] is not None:
                return row["result"]
            if available(row["question"], system_japanese=system_japanese_enabled(self.store)):
                raise ValueError("这道题可以用当前输入方式作答。")
            stamp = _stamp(now, row["created_at_utc"])
            row["result"] = _result(row["question"], row["draft"], row["draft"].get("answer", ""),
                                    False, stamp, cancelled=True)
            row.update(status="completed", completed_at_utc=stamp)
            state["completed_count"] += 1
            return row["result"]
        return self._mutate(operation)

    def mark_exposed(self, target_id, *, now=None):
        return self._mutate(lambda state: self._expose(state, target_id, now))

    def save_chat_draft(self, text, context=None):
        """Commit every edit while reusing only an exact, validated JSON version.

        This narrow mutation cannot touch history. General writes and snapshots
        still perform full validation. Cache publication follows COMMIT, and the
        next edit compares actual stored bytes even inside a write transaction.
        """
        text = _SECRET.sub("[已移除密钥]", _text(text, MAX_CHAT_DRAFT, blank=True))
        context = _chat_context(context)
        db = self.store._db
        if db.in_transaction:
            raise RuntimeError("Tutor writes require their own transaction")
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT value_json FROM app_setting WHERE key=?", (SETTING,)).fetchone()
                raw = row[0] if row else None
                cached = getattr(self, "_chat_draft_cache", None)
                state = (cached[1] if cached is not None and cached[0] == raw else
                         validate_tutor_value(json.loads(raw) if raw is not None else _empty()))
                changed = state["chat_draft"] != text or state["chat_context"] != context
                # Keep the validated history private and untouched; only these
                # three fields may differ from the exact saved version.
                value = {**state, "chat_draft": text, "chat_context": context,
                         "revision": state["revision"] + int(changed)}
                encoded = _json(value)
                if len(encoded.encode()) > MAX_BYTES or _SECRET.search(encoded):
                    raise ValueError("辅导保存内容过大或含敏感信息。")
                if changed:
                    db.execute("INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                               (SETTING, encoded))
            self._chat_draft_cache = (encoded if changed else raw, value)
        except Exception:
            self._chat_draft_cache = None
            raise
        return text

    def record_chat(self, role, text, *, target_id="", turn_id="", now=None):
        if role not in {"user", "assistant"}:
            raise ValueError("辅导对话角色不正确。")
        text = _SECRET.sub("[已移除密钥]", _text(text, 4000))
        _text(target_id, 256, blank=True); _text(turn_id, 100, blank=True)
        def operation(state):
            # One network response/role is one message even after recovery.
            prior = next((row for row in state["messages"] if turn_id and row["turn_id"] == turn_id and row["role"] == role), None)
            if prior:
                return prior
            row = {"id": uuid4().hex, "role": role, "text": text, "target_id": target_id,
                   "turn_id": turn_id, "created_at_utc": _stamp(now)}
            state["messages"] = [*state["messages"], row][-MAX_MESSAGES:]
            if role == "assistant" and target_id:
                self._expose(state, target_id, now)
            return row
        return self._mutate(operation)

    def commit_chat_response(self, request_id, user_message, *, target_id="",
                             exposed_target_ids=(), chat_context=None, coached_row_id="", now=None):
        """Apply a saved response in one transaction, including draft clearing.

        A crash before this transaction leaves the full response recoverable;
        a crash after it cannot duplicate messages or extend the exposure timer.
        """
        message = _SECRET.sub("[已移除密钥]", _text(user_message, 4000, blank=True))
        _text(target_id, 256, blank=True)
        _text(coached_row_id, 100, blank=True)
        if not isinstance(exposed_target_ids, (tuple, list, set)) or len(exposed_target_ids) > 32:
            raise ValueError("辅导解释关联范围过大。")
        targets = set(exposed_target_ids)
        if target_id:
            targets.add(target_id)
        for cid in targets:
            _text(cid, 256)
        origin = _chat_context(chat_context)

        def operation(state):
            request = state["request"]
            if (not request or request["request_id"] != request_id or request["status"] != "completed"
                    or request["response"]["action"] != "explain"):
                return False
            assistant_id = request_id + ":assistant"
            if any(row["turn_id"] == assistant_id and row["role"] == "assistant" for row in state["messages"]):
                return False
            coached = None
            if coached_row_id:
                coached = self._row(state, coached_row_id)
                automatic = request["intent"] == "next" and coached["status"] == "completed"
                answered_question = (request["intent"] == "ask" and origin
                    and origin["source"] == "tutor" and origin["after_answer"]
                    and origin["question_id"] == coached_row_id
                    and origin["target_id"] == coached["question"]["target_id"])
                if (not (automatic or answered_question)
                        or coached["result"] is None or coached["result"]["outcome"] not in {"incorrect", "revealed", "unresolved"}
                        or coached["question"]["target_id"] not in targets):
                    raise ValueError("讲解必须对应本次已提交的错题。")
            stamp = _stamp(now, request["created_at_utc"])
            messages = []
            if message:
                messages.append({"id": uuid4().hex, "role": "user", "text": message, "target_id": target_id,
                                 "turn_id": request_id + ":user", "created_at_utc": stamp})
            messages.append({"id": uuid4().hex, "role": "assistant", "text": request["response"]["text"],
                             "target_id": target_id, "turn_id": assistant_id, "created_at_utc": stamp})
            related = sorted(targets - {target_id})
            if related:
                for row in messages:
                    row['related_target_ids'] = related
            state["messages"] = [*state["messages"], *messages][-MAX_MESSAGES:]
            for cid in targets:
                self._expose(state, cid, _date(stamp))
            if coached is not None:
                coached["coached"] = True
            state.update(chat_draft="", chat_context=origin)
            return True
        return self._mutate(operation)

    def begin_request(self, intent, context_fingerprint, *, now=None):
        if intent not in {"next", "ask", "explain"} or not re.fullmatch(r"[a-f0-9]{64}", context_fingerprint or ""):
            raise ValueError("辅导请求内容不正确。")
        def operation(state):
            old = state["request"]
            if old and old["status"] in {"pending", "completed"} and old["intent"] == intent and old["context_fingerprint"] == context_fingerprint:
                return old
            state["request"] = {"request_id": uuid4().hex, "intent": intent, "context_fingerprint": context_fingerprint,
                                "status": "pending", "response": None, "created_at_utc": _stamp(now)}
            return state["request"]
        return self._mutate(operation)

    def finish_request(self, request_id, context_fingerprint, response, *, now=None):
        if not isinstance(response, dict):
            raise ValueError("辅导响应格式不正确。")
        def operation(state):
            request = state["request"]
            if not request or request["request_id"] != request_id or request["context_fingerprint"] != context_fingerprint:
                return False
            if request["status"] == "completed":
                return request["response"] == response
            if request["status"] != "pending":
                return False
            request.update(status="completed", response=_response(response, request["intent"]))
            return True
        return self._mutate(operation)

    def cancel_request(self, request_id=None):
        def operation(state):
            request = state["request"]
            if request and request["status"] == "pending" and (request_id is None or request["request_id"] == request_id):
                request["status"] = "cancelled"
                return True
            return False
        return self._mutate(operation)
