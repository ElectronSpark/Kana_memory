"""Durable text-share inbox. Receipt does not send text or replace any draft."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import re

from .ai_ui_state import AI_FORM_SETTING, validate_ai_form_value, MAX_FORM_DRAFTS

PREFIX = "material_share_v1:"
MAX_RECORDS = 500


def draft_key(identity):
    return "analyze:share/" + identity


def validate_record(value):
    from .material_input import select_ranges
    if (not isinstance(value, dict) or set(value) != {"version", "id", "text", "sha256", "received_at", "status"}
            or type(value["version"]) is not int or value["version"] != 1
            or not isinstance(value["id"], str) or re.fullmatch(r"[a-f0-9]{32}", value["id"]) is None
            or not isinstance(value["text"], str) or value["status"] not in {"pending", "opened", "discarded"}):
        raise ValueError("分享材料格式无效。")
    select_ranges(value["text"], [(0, len(value["text"]))])
    if value["sha256"] != sha256(value["text"].encode("utf-8")).hexdigest():
        raise ValueError("分享材料原文不一致。")
    try:
        if datetime.fromisoformat(value["received_at"]).tzinfo is None:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("分享材料时间无效。") from None
    return deepcopy(value)


class MaterialShareStore:
    def __init__(self, store):
        self.store, self.db = store, store._db

    def get(self, identity):
        value = self.store.get_setting(PREFIX + identity, None)
        return validate_record(value) if value is not None else None

    def list(self):
        return sorted((validate_record(json.loads(raw)) for raw, in self.db.execute(
            "SELECT value_json FROM app_setting WHERE key GLOB ?", (PREFIX + "*",))),
            key=lambda v: (v["received_at"], v["id"]), reverse=True)

    def _write(self, key, value):
        if not self.db.in_transaction:
            raise RuntimeError("Share writes require a transaction")
        self.db.execute("INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                        (key, json.dumps(value, ensure_ascii=False, allow_nan=False)))

    def ingest(self, incoming):
        """Commit before native acknowledgement; retry the same IDs safely."""
        if not isinstance(incoming, list) or len(incoming) > 16:
            raise ValueError("分享收件箱数量无效。")
        rows = []
        for raw in incoming:
            if (not isinstance(raw, dict) or set(raw) != {"id", "text", "received_ms"}
                    or type(raw["received_ms"]) is not int or not 0 <= raw["received_ms"] <= 253402300799000
                    or not isinstance(raw["text"], str)):
                raise ValueError("分享输入无效。")
            rows.append(validate_record({"version": 1, "id": raw["id"], "text": raw["text"],
                "sha256": sha256(raw["text"].encode("utf-8")).hexdigest(), "status": "pending",
                "received_at": datetime.fromtimestamp(raw["received_ms"] / 1000, timezone.utc).isoformat()}))
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            count = self.db.execute("SELECT COUNT(*) FROM app_setting WHERE key GLOB ?", (PREFIX + "*",)).fetchone()[0]
            for row in rows:
                old = self.get(row["id"])
                if old:
                    if any(old[key] != row[key] for key in ("text", "received_at", "sha256")):
                        raise ValueError("分享标识对应不同原文，未覆盖旧内容。")
                    continue
                count += 1
                if count > MAX_RECORDS:
                    raise ValueError("分享材料已达保存上限，原文仍留在系统分享收件箱。")
                self._write(PREFIX + row["id"], row)
        return list(dict.fromkeys(row["id"] for row in rows))

    def open_draft(self, identity):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.get(identity)
            if row is None or row["status"] == "discarded":
                raise ValueError("这份分享材料已不可用。")
            forms = validate_ai_form_value(self.store.get_setting(AI_FORM_SETTING, {})) or {"drafts": {}}
            key = draft_key(identity)
            if key not in forms["drafts"]:
                if len(forms["drafts"]) >= MAX_FORM_DRAFTS:
                    raise ValueError("输入草稿已满，请先舍弃不再需要的分享草稿。")
                forms["drafts"][key] = {"source_text": row["text"], "count": "3", "topic": ""}
            forms["last"] = {"mode": "analyze", "target_id": None, "key": key, "input_id": identity}
            self._write(AI_FORM_SETTING, validate_ai_form_value(forms))
            row["status"] = "opened"
            self._write(PREFIX + identity, validate_record(row))
        return deepcopy(forms["drafts"][key])

    def discard(self, identity):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.get(identity)
            if row is None:
                raise ValueError("这份分享材料已不可用。")
            row["status"] = "discarded"
            forms = validate_ai_form_value(self.store.get_setting(AI_FORM_SETTING, {})) or {}
            if forms and draft_key(identity) in forms["drafts"]:
                forms["drafts"].pop(draft_key(identity))
                if not forms["drafts"]:
                    forms = {}
                elif forms["last"]["key"] == draft_key(identity):
                    key = next(reversed(forms["drafts"]))
                    mode, target = key.split(":", 1)
                    forms["last"] = {"mode": mode, "target_id": target if mode == "examples" else None, "key": key}
                    if mode == "analyze" and target.startswith("share/"):
                        forms["last"]["input_id"] = target[6:]
                self._write(AI_FORM_SETTING, validate_ai_form_value(forms))
            self._write(PREFIX + identity, validate_record(row))


def validate_database(db):
    raw = db.execute("SELECT value_json FROM app_setting WHERE key=?", (AI_FORM_SETTING,)).fetchone()
    forms = validate_ai_form_value(json.loads(raw[0])) if raw else {}
    for key in (forms or {}).get("drafts", {}):
        if key.startswith("analyze:share/"):
            row = db.execute("SELECT value_json FROM app_setting WHERE key=?", (PREFIX + key.split("share/", 1)[1],)).fetchone()
            if not row or validate_record(json.loads(row[0]))["status"] != "opened":
                raise ValueError("分享草稿缺少对应原文。")
