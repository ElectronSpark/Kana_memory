"""Durable authoring jobs. A retained proposal is not a correctness certificate.

Requests contain selected material, never provider credentials. A network attempt
is charged before dispatch; unknown delivery is not retried on process restart.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
from uuid import uuid4

from . import app_clock

PREFIX = "material_workflow_v1:"
FORMAT = "kanamemory-material-workflow"
RUN_ID = uuid4().hex
MAX_BYTES = 1024 * 1024
MAX_JOBS = 1000
STAGES = ("generation", "review", "revision")
STATUSES = {"queued", "running", "awaiting_review", "completed", "failed", "cancelled", "transport_unknown"}


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return sha256(dump(value).encode("utf-8")).hexdigest()


def job_id(request_id):
    return sha256(request_id.encode("utf-8")).hexdigest()


def _text(value, limit, *, nonempty=True):
    return isinstance(value, str) and len(value) <= limit and (bool(value) or not nonempty)


def _date(value):
    try:
        return isinstance(value, str) and datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        return False


def validate_job(value):
    fields = {"format", "version", "id", "request_id", "request", "request_hash", "source",
              "intent", "status", "created_at", "updated_at", "attempts", "budget", "proposal_id",
              "error_code", "candidate_ids"}
    if not isinstance(value, dict) or set(value) not in (fields, fields | {"revision_of"}) or value["format"] != FORMAT or type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("材料任务格式无效。")
    if len(dump(value).encode("utf-8")) > MAX_BYTES:
        raise ValueError("材料任务过大。")
    if not _text(value["request_id"], 256) or value["id"] != job_id(value["request_id"]):
        raise ValueError("材料任务标识不一致。")
    request = value["request"]
    if (not isinstance(request, dict) or set(request) - {"mode", "count", "topic", "source_text", "target", "scope", "catalog", "request_id"}
            or not {"mode", "count", "source_text", "request_id"} <= set(request)
            or request["mode"] not in {"analyze", "examples"}
            or request["request_id"] != value["request_id"] or value["request_hash"] != digest(request)
            or type(request["count"]) is not int or not 1 <= request["count"] <= (8 if request["mode"] == "analyze" else 10)
            or not _text(request["source_text"], 4000, nonempty=request["mode"] == "analyze")
            or request.get("scope") is not None or not _text(request.get("topic", ""), 200, nonempty=False)):
        raise ValueError("材料任务请求无效。")
    source = value["source"]
    if (not isinstance(source, dict) or set(source) != {"id", "revision", "input_kind", "text", "sha256", "spans"}
            or source["id"] != "material-source:" + value["id"] or source["revision"] != 1
            or source["input_kind"] not in {"paste", "share", "utf8_file", "target"}
            or not _text(source["text"], 4000, nonempty=request["mode"] == "analyze")
            or len(source["text"].encode("utf-8")) > 16000
            or source["sha256"] != sha256(source["text"].encode("utf-8")).hexdigest()
            or not isinstance(source["spans"], list) or len(source["spans"]) > 8):
        raise ValueError("材料原文版本无效。")
    previous_end = 0
    for span in source["spans"]:
        if (not isinstance(span, dict) or set(span) != {"start", "end", "quote"}
                or type(span["start"]) is not int or type(span["end"]) is not int
                or not previous_end <= span["start"] < span["end"] <= len(source["text"])
                or source["text"][span["start"]:span["end"]] != span["quote"]):
            raise ValueError("材料原文片段不一致。")
        previous_end = span["end"]
    if request["mode"] == "analyze" and not source["spans"]:
        raise ValueError("请选择至少一段原文。")
    if "\n".join(span["quote"] for span in source["spans"]) != request["source_text"]:
        raise ValueError("发送文本与选择范围不一致。")
    if (value["intent"] not in {"preview", "pretest", "prelearn"} or value["status"] not in STATUSES
            or not _date(value["created_at"]) or not _date(value["updated_at"])
            or value["budget"] != {"generation": 1, "review": 1, "revision": 1}
            or not isinstance(value["attempts"], list) or len(value["attempts"]) > 3
            or not _text(value["error_code"], 80, nonempty=False)):
        raise ValueError("材料任务状态无效。")
    stages = set()
    running = []
    for attempt in value["attempts"]:
        if (not isinstance(attempt, dict) or set(attempt) != {"stage", "token", "owner", "status", "started_at", "finished_at"}
                or attempt["stage"] not in STAGES or attempt["stage"] in stages
                or not _text(attempt["token"], 64) or not _text(attempt["owner"], 64)
                or attempt["status"] not in {"running", "completed", "failed", "cancelled", "transport_unknown"}
                or not _date(attempt["started_at"])
                or (attempt["finished_at"] is not None and not _date(attempt["finished_at"]))
                or (attempt["finished_at"] is None) != (attempt["status"] == "running")):
            raise ValueError("材料任务调用记录无效。")
        stages.add(attempt["stage"])
        if attempt["status"] == "running":
            running.append(attempt)
    if len(running) > 1 or bool(running) != (value["status"] == "running"):
        raise ValueError("材料任务运行记录不一致。")
    if (value["proposal_id"] is not None and not _text(value["proposal_id"], 256)
            or not isinstance(value["candidate_ids"], list) or len(value["candidate_ids"]) > 208
            or any(not _text(identity, 128) for identity in value["candidate_ids"])
            or len(set(value["candidate_ids"])) != len(value["candidate_ids"])
            or value["status"] in {"awaiting_review", "completed"} and not value["proposal_id"]):
        raise ValueError("材料任务结果标识无效。")
    if "revision_of" in value:
        parent = value["revision_of"]
        if (not isinstance(parent, str) or len(parent) != 64 or any(c not in '0123456789abcdef' for c in parent)
                or value["request_id"] != "material-revision:" + parent or value["attempts"]
                or value["status"] != "awaiting_review" or not value["proposal_id"]):
            raise ValueError("修订稿不能成为独立调用任务。")
    return deepcopy(value)


def write_in_transaction(db, value):
    if not db.in_transaction:
        raise RuntimeError("Material task requires a transaction")
    value = validate_job(value)
    db.execute("INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
               (PREFIX + value["id"], dump(value)))


def reset_in_transaction(db):
    """Retain material and spent attempts; fence responses from before reset."""
    for key, raw in db.execute("SELECT key,value_json FROM app_setting WHERE key GLOB ?", (PREFIX + "*",)).fetchall():
        value = validate_job(json.loads(raw))
        if value["status"] == "running":
            _end_attempt(value, "transport_unknown", "learning_reset")
            write_in_transaction(db, value)


def _end_attempt(value, status, error=""):
    now = app_clock.utc_now().isoformat()
    for attempt in value["attempts"]:
        if attempt["status"] == "running":
            attempt.update(status=status, finished_at=now)
    value.update(status=status, error_code=error, updated_at=now)


def original_segments(job, proposal):
    """Exact original anchors for feedback/repair; never normalize the source."""
    from .material_input import original_spans
    source = job["source"]
    if not proposal["segments"]:
        return []
    selection = {"version": 1, "text": source["text"], "sha256": source["sha256"], "spans": source["spans"]}
    return [{"source_id": source["id"], "source_revision": source["revision"],
             "source_sha256": source["sha256"], "selected_start": part["start"], "selected_end": part["end"],
             "anchors": original_spans(selection, part["start"], part["end"])} for part in proposal["segments"]]


class MaterialWorkflowStore:
    def __init__(self, store):
        self.store, self.db = store, store._db

    def get(self, identity):
        value = self.store.get_setting(PREFIX + identity, None)
        return validate_job(value) if value is not None else None

    def for_request(self, request_id):
        return self.get(job_id(request_id))

    def budget(self, identity):
        """Read the existing workflow budget without reserving or spending it."""
        from .material_budget import read
        return read(self.db, identity)

    def list(self):
        rows = [validate_job(json.loads(raw)) for raw, in self.db.execute(
            "SELECT value_json FROM app_setting WHERE key GLOB ?", (PREFIX + "*",))]
        return sorted(rows, key=lambda row: (row["created_at"], row["id"]), reverse=True)

    def create(self, request, *, input_kind=None, intent="preview", source_selection=None):
        request = deepcopy(request)
        identity = job_id(request["request_id"])
        text = request.get("source_text", "")
        spans = [{"start": 0, "end": len(text), "quote": text}] if text else []
        if source_selection is not None:
            if request["mode"] != "analyze":
                raise ValueError("仅原文解析支持选择文本范围。")
            from .material_input import validate_selection
            selection = validate_selection(source_selection)
            text, spans = selection["text"], selection["spans"]
        now = app_clock.utc_now().isoformat()
        value = validate_job({"format": FORMAT, "version": 1, "id": identity,
            "request_id": request["request_id"], "request": request, "request_hash": digest(request),
            "source": {"id": "material-source:" + identity, "revision": 1,
                "input_kind": input_kind or ("paste" if request["mode"] == "analyze" else "target"),
                "text": text, "sha256": sha256(text.encode("utf-8")).hexdigest(),
                "spans": spans},
            "intent": intent, "status": "queued", "created_at": now, "updated_at": now,
            "attempts": [], "budget": dict.fromkeys(STAGES, 1), "proposal_id": None,
            "candidate_ids": [], "error_code": ""})
        if self.db.in_transaction:
            raise RuntimeError("Material task creation owns its transaction")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            old = self.get(identity)
            if old:
                if old["request_hash"] != value["request_hash"] or old["intent"] != intent or old["source"] != value["source"]:
                    raise ValueError("同一请求不能覆盖已有的材料任务。")
                return old
            if self.db.execute("SELECT COUNT(*) FROM app_setting WHERE key GLOB ?", (PREFIX + "*",)).fetchone()[0] >= MAX_JOBS:
                raise ValueError("材料任务已达保存上限。")
            write_in_transaction(self.db, value)
        return value

    def begin(self, identity, stage="generation", *, budget_context=None):
        if stage not in {"generation", "review"}:
            raise ValueError("该核验阶段尚未接通。")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            value = self.get(identity)
            expected = "queued" if stage == "generation" else "awaiting_review"
            if value and value.get("revision_of"):
                raise ValueError("本批修订次数已使用；修订稿仅重新核对本地依据。")
            if not value or value["status"] != expected or any(a["stage"] == stage for a in value["attempts"]):
                raise ValueError("该请求已经执行；结果未知时不会自动重发。")
            if stage == "review":
                from .material_verification import MaterialVerificationStore
                report = MaterialVerificationStore(self.store).get(identity)
                if not report or not report["review_request"]["questions"]:
                    raise ValueError("没有需要独立复核的字段。")
            token = uuid4().hex
            if budget_context is not None:
                from .material_budget import reserve
                reserve(self.db, value, token, stage,
                        value['request'] if stage == 'generation' else report['review_request'], budget_context)
            now = app_clock.utc_now().isoformat()
            value["attempts"].append({"stage": stage, "token": token, "owner": RUN_ID,
                "status": "running", "started_at": now, "finished_at": None})
            value.update(status="running", updated_at=now)
            write_in_transaction(self.db, value)
        return token

    @staticmethod
    def _owns(value, token):
        return bool(value and value["status"] == "running" and any(
            row["token"] == token and row["owner"] == RUN_ID and row["status"] == "running" for row in value["attempts"]))

    def complete_generation(self, identity, token, response, material_store):
        """Proposal and receipt commit together, even after the view is gone."""
        if material_store.store._db is not self.db:
            raise ValueError("材料与任务必须保存在同一个数据库中。")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            value = self.get(identity)
            if not self._owns(value, token):
                return None
            if not any(a['stage'] == 'generation' and a['token'] == token for a in value['attempts']):
                raise ValueError('该调用不是材料生成任务。')
            if len(response.get("items", [])) > 8 or len(response.get("segments", [])) > 8:
                raise ValueError("每批最多整理八个片段和八个新学习项。")
            request = value["request"]
            proposal = material_store._stage_in_transaction(
                "text" if request["mode"] == "analyze" else "examples", request["source_text"],
                response.get("items", []), response.get("example_pack"), response.get("segments", []), value["request_id"])
            original_segments(value, proposal)
            _end_attempt(value, "completed")
            value.update(status="awaiting_review", proposal_id=proposal["id"],
                         candidate_ids=["material-candidate:" + sha256((identity + ":" + str(index)).encode()).hexdigest()
                                        for index in range(len(proposal["items"]) + len(proposal["example_pack"]["examples"]))])
            write_in_transaction(self.db, value)
        return deepcopy(value)

    def complete_review(self, identity, token, response, *, model):
        from .material_verification import MaterialVerificationStore
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            value = self.get(identity)
            if not self._owns(value, token):
                return None
            if not any(a["stage"] == "review" and a["token"] == token for a in value["attempts"]):
                raise ValueError("该调用不是材料复核任务。")
            report = MaterialVerificationStore(self.store).complete_in_transaction(identity, response, model=model)
            _end_attempt(value, "completed")
            write_in_transaction(self.db, value)
        return report

    def fail(self, identity, token, *, code="generation_error", delivery_unknown=False):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            value = self.get(identity)
            if self._owns(value, token):
                _end_attempt(value, "transport_unknown" if delivery_unknown else "failed", code)
                write_in_transaction(self.db, value)

    def cancel(self, identity):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            value = self.get(identity)
            if value and value["status"] in {"queued", "running"}:
                _end_attempt(value, "cancelled")
                write_in_transaction(self.db, value)

    def recover_interrupted(self):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            for value in self.list():
                if value["status"] == "running" and any(a["status"] == "running" and a["owner"] != RUN_ID for a in value["attempts"]):
                    _end_attempt(value, "transport_unknown", "process_interrupted")
                    write_in_transaction(self.db, value)


def validate_database(db):
    from .ai_materials import SETTING, empty_state, validate_ai_material_state
    raw = db.execute("SELECT value_json FROM app_setting WHERE key=?", (SETTING,)).fetchone()
    proposals = {p["id"]: p for p in validate_ai_material_state(json.loads(raw[0]) if raw else empty_state())["proposals"]}
    for key, raw in db.execute("SELECT key,value_json FROM app_setting WHERE key GLOB 'material_workflow_*'"):
        value = validate_job(json.loads(raw))
        if key != PREFIX + value["id"]:
            raise ValueError("材料任务版本或标识无效。")
        if value["proposal_id"]:
            proposal = proposals.get(value["proposal_id"])
            if (not proposal or proposal["request_id"] != value["request_id"]
                    or proposal["source_text"] != value["request"]["source_text"]):
                raise ValueError("材料任务缺少原始提案。")
            expected = ["material-candidate:" + sha256((value["id"] + ":" + str(index)).encode()).hexdigest()
                        for index in range(len(proposal["items"]) + len(proposal["example_pack"]["examples"]))]
            if value["candidate_ids"] != expected:
                raise ValueError("材料候选标识不一致。")
            original_segments(value, proposal)
