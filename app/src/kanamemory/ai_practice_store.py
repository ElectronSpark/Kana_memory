"""Resumable AI training, isolated from every memory card and review queue.

Only one app setting is written. Generating, answering, revealing or repeating
an AI exercise never enrolls content or calls the FSRS scoring interfaces.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from . import app_clock
import json

from .ai_questions import (PROPOSAL_FIELDS, OPTIONAL_PROPOSAL_FIELDS, question_is_available,
                           validate_ai_question_snapshot, validate_question_proposals)

SETTING = "ai_practice_v1"
FORMAT = "kanamemory-ai-practice"
MAX_ROWS = 5000
MAX_BYTES = 16 * 1024 * 1024
_ROW_KEYS = {"id", "question", "draft", "result", "status", "created_at_utc", "completed_at_utc"}
_RESULT_KEYS = {"question_id", "answered_text", "answer", "explanation", "correct", "assisted",
                "revealed", "scheduled", "outcome", "submitted_at_utc", "training_only"}


def _now():
    return app_clock.utc_now().isoformat()


def _date(value):
    if not isinstance(value, str):
        raise ValueError("AI 练习时间格式不正确。")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("AI 练习时间缺少时区。")
    return parsed


def _not_before(*prior):
    """Device clock corrections must not strand an already saved exercise."""
    return max(_date(value) for value in (_now(), *prior)).isoformat()


def _empty():
    return {"format": FORMAT, "version": 1, "entries": []}


def _draft(value):
    if not isinstance(value, dict) or set(value) - {"answer", "assisted", "hint_used", "presented"}:
        raise ValueError("AI 练习草稿格式不正确。")
    if "answer" in value and (not isinstance(value["answer"], str) or len(value["answer"]) > 240):
        raise ValueError("AI 练习草稿过长。")
    if any(type(value[key]) is not bool for key in ("assisted", "hint_used", "presented") if key in value):
        raise ValueError("AI 练习帮助状态不正确。")
    return deepcopy(value)


def _result(question, answer, revealed, submitted_at, assisted=False):
    correct = None if revealed else answer == question["answer"]
    return {"question_id": question["id"], "answered_text": answer,
            "answer": question["answer"], "explanation": question["explanation"],
            "correct": correct, "assisted": revealed or assisted, "revealed": revealed,
            "scheduled": False, "training_only": True,
            "outcome": "revealed" if revealed else "correct" if correct else "incorrect",
            "submitted_at_utc": submitted_at}


def validate_ai_practice_value(value, known_targets=None):
    """Backup-safe validation with no reads, writes, network or scheduler."""
    if (not isinstance(value, dict) or set(value) != {"format", "version", "entries"}
            or value.get("format") != FORMAT or type(value.get("version")) is not int
            or value["version"] != 1 or not isinstance(value.get("entries"), list)
            or len(value["entries"]) > MAX_ROWS):
        raise ValueError("AI 练习保存格式不受支持。")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("AI 练习保存内容无效。") from exc
    if len(encoded.encode("utf-8")) > MAX_BYTES:
        raise ValueError("AI 练习保存内容过大。")
    identities = set()
    for row in value["entries"]:
        if not isinstance(row, dict) or set(row) - {'content_retirement'} != _ROW_KEYS:
            raise ValueError("AI 练习记录字段不正确。")
        question = validate_ai_question_snapshot(row["question"])
        if row["id"] != question["id"] or row["id"] in identities:
            raise ValueError("AI 练习标识重复或不匹配。")
        identities.add(row["id"])
        if known_targets is not None and question["target_id"] not in known_targets:
            raise ValueError("AI 练习引用不存在的学习项。")
        created = _date(row["created_at_utc"])
        _draft(row["draft"])
        if row["status"] not in {"pending", "completed", "retired"}:
            raise ValueError("AI 练习状态不正确。")
        if bool(row.get('content_retirement')) != (row['status'] == 'retired') or row['status'] == 'retired' and row['result'] is not None:
            raise ValueError('待核 AI 题不能包含完成记录。')
        result = row["result"]
        if result is not None:
            if not isinstance(result, dict) or set(result) != _RESULT_KEYS:
                raise ValueError("AI 练习结果字段不正确。")
            if type(result["revealed"]) is not bool or type(result["assisted"]) is not bool:
                raise ValueError("AI 练习提示状态不正确。")
            if (result["correct"] is not None and type(result["correct"]) is not bool
                    or result["scheduled"] is not False or result["training_only"] is not True):
                raise ValueError("AI 练习结果不能成为记忆调度证据。")
            answer = result["answered_text"]
            if not isinstance(answer, str) or len(answer) > 240:
                raise ValueError("AI 练习答案不正确。")
            if not result["revealed"] and answer not in question["choices"]:
                raise ValueError("AI 练习答案不属于选项。")
            assisted = bool(row["draft"].get("assisted") or row["draft"].get("hint_used"))
            if result != _result(question, answer, result["revealed"], result["submitted_at_utc"], assisted):
                raise ValueError("AI 练习结果与首次答案不一致。")
            if _date(result["submitted_at_utc"]) < created:
                raise ValueError("AI 练习提交早于创建。")
        if row["status"] == "completed":
            if result is None or _date(row["completed_at_utc"]) < _date(result["submitted_at_utc"]):
                raise ValueError("AI 练习完成时间不正确。")
        elif row["completed_at_utc"] is not None:
            raise ValueError("未完成的 AI 练习不应有完成时间。")
    return deepcopy(value)


class AIPracticeStore:
    def __init__(self, store):
        self.store = store

    def _load(self):
        return validate_ai_practice_value(self.store.get_setting(SETTING, _empty()))

    def _mutate(self, operation):
        db = self.store._db
        if db.in_transaction:
            raise RuntimeError("AI practice writes require their own transaction")
        with db:
            db.execute("BEGIN IMMEDIATE")
            value = self._load()
            original = deepcopy(value)
            result = operation(value)
            validate_ai_practice_value(value)
            if value != original:
                encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                db.execute("INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                           (SETTING, encoded))
        return deepcopy(result)

    @staticmethod
    def _row(value, question_id):
        row = next((r for r in value["entries"] if r["id"] == question_id), None)
        if row is None:
            raise KeyError(question_id)
        return row

    def _available(self, row, enabled_kinds, scope):
        question = row["question"]
        if question.get('material_qualification'):
            from .material_qualification import qualification
            if not qualification(self.store, question):
                return False
        return question["kind"] in enabled_kinds and (scope is None or question_is_available(question, scope, enabled_kinds))

    def all_questions(self):
        return [row["question"] for row in self._load()["entries"]]

    def append(self, questions, scope, enabled_kinds):
        if not isinstance(questions, list):
            raise ValueError("AI 题单格式不正确。")
        # A late network response cannot reuse an old scope or a disabled kind.
        for question in questions:
            validate_ai_question_snapshot(question)
        candidates = [{key: q[key] for key in PROPOSAL_FIELDS | OPTIONAL_PROPOSAL_FIELDS if key in q}
                      for q in questions]
        validated = validate_question_proposals(candidates, scope, enabled_kinds)

        def operation(value):
            known = {r["id"] for r in value["entries"]}
            added = [q for q in validated if q["id"] not in known]
            if len(value["entries"]) + len(added) > MAX_ROWS:
                raise ValueError("已保存的 AI 练习过多，请先管理学习数据。")
            stamp = _now()
            value["entries"].extend({"id": q["id"], "question": q, "draft": {}, "result": None,
                "status": "pending", "created_at_utc": stamp, "completed_at_utc": None} for q in added)
            return len(added)
        return self._mutate(operation)

    def current(self, enabled_kinds, scope=None):
        rows = [row for row in self._load()["entries"] if row["status"] == "pending"
                and (row['result'] is not None or self._available(row, enabled_kinds, scope))]
        rows.sort(key=lambda r: 0 if r["result"] is not None else 1 if r["draft"].get("answer", "").strip() else 2)
        return deepcopy(rows[0]) if rows else None

    def counts(self, enabled_kinds, scope=None):
        rows = self._load()["entries"]
        pending = [r for r in rows if r["status"] == "pending"]
        ready = sum(r['result'] is not None or self._available(r, enabled_kinds, scope) for r in pending)
        return {"total": len(rows), "remaining": len(pending), "ready": ready,
                "disabled": len(pending) - ready, "completed": sum(r['status'] == 'completed' for r in rows),
                'retired': sum(r['status'] == 'retired' for r in rows)}

    def save_draft(self, question_id, state):
        draft = _draft(state)
        def operation(value):
            row = self._row(value, question_id)
            if row["status"] == "pending" and row["result"] is None:
                original = row["draft"]
                row["draft"] = {**original, **draft}
                for key in ("assisted", "hint_used", "presented"):
                    if original.get(key):
                        row["draft"][key] = True
            return row["draft"]
        return self._mutate(operation)

    def submit(self, question_id, answer, enabled_kinds, scope, *, revealed=False):
        if type(revealed) is not bool or not isinstance(answer, str) or len(answer) > 240:
            raise ValueError("AI 练习答案格式不正确。")
        def operation(value):
            row = self._row(value, question_id)
            if row["result"] is not None:
                return row["result"]
            if row['status'] == 'retired': raise ValueError('此题依据已修订，原题与草稿已保存。')
            if not self._available(row, enabled_kinds, scope):
                raise ValueError("这个题型或学习目标当前不可用，原题和草稿已保留。")
            if not revealed and answer not in row["question"]["choices"]:
                raise ValueError("请选择本题中的一个答案。")
            assisted = bool(row["draft"].get("assisted") or row["draft"].get("hint_used"))
            row["result"] = _result(row["question"], answer, revealed, _not_before(row["created_at_utc"]), assisted)
            from .learning_evidence import LearningEvidenceStore
            from .assessment_opportunity import identity
            LearningEvidenceStore(self.store).record_attempt("ai:" + row["id"], row["question"], row["result"],
                source="ai", role="exposure", trusted=False, opportunity_id=identity("ai", row["id"]),
                now=_date(row["result"]["submitted_at_utc"]))
            return row["result"]
        return self._mutate(operation)

    def reveal(self, question_id, enabled_kinds, scope):
        return self.submit(question_id, "", enabled_kinds, scope, revealed=True)

    def complete(self, question_id):
        def operation(value):
            row = self._row(value, question_id)
            if row["result"] is None:
                raise ValueError("请先作答或查看答案。")
            if row["status"] != "completed":
                row.update(status="completed", completed_at_utc=_not_before(row["result"]["submitted_at_utc"]))
            return row["result"]
        return self._mutate(operation)
