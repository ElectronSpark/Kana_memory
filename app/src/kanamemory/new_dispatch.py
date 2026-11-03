"""Fair service for new work only; no scheduler, queue or mastery state.

The caller opts in from the unified learning route. Reads never advance the
cursor. A real first completion advances it inside the caller's transaction.
An opened course's teaching block stays together until its guide completes.
"""
import json

SETTING = "new_dispatch_v1"


def validate(value):
    if (not isinstance(value, dict)
            or set(value) != {"version", "last_lane", "last_kana_band"}
            or type(value["version"]) is not int or value["version"] != 1
            or value["last_lane"] not in (None, "course", "kana")
            or (value["last_kana_band"] is not None and (
                type(value["last_kana_band"]) is not int or value["last_kana_band"] not in (0, 1, 2)))):
        raise ValueError("Invalid new-work dispatch cursor")
    return dict(value)


def read(db):
    row = db.execute("SELECT value_json FROM app_setting WHERE key=?", (SETTING,)).fetchone()
    return validate(json.loads(row[0])) if row else {
        "version": 1, "last_lane": None, "last_kana_band": None}


def enabled(app):
    return (getattr(app, "_study_path_enabled", False)
            and getattr(app.store, "practice_lane", "main") == "main")


def band(kind):
    from .domain import _ADAPTIVE_LEVELS
    return _ADAPTIVE_LEVELS[kind]


def band_order(cursor, kind):
    previous = cursor["last_kana_band"]
    start = 0 if previous is None else (previous + 1) % 3
    return (band(kind) - start) % 3


def completed(db, lane, *, kind=None):
    """Must share the existing completion transaction and its rollback."""
    if not db.in_transaction:
        raise RuntimeError("New-work cursor requires a completion transaction")
    if lane not in {"course", "kana"} or (lane == "kana" and kind is None):
        raise ValueError("Invalid new-work lane")
    value = read(db)
    value["last_lane"] = lane
    if lane == "kana":
        value["last_kana_band"] = band(kind)
    db.execute("INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
               (SETTING, json.dumps(value, ensure_ascii=False, allow_nan=False)))


def choose_lane(db, kana_priority, course_priority, *, course_started=False):
    """Priority 0..3 wins unchanged; rotate only when both candidates are new."""
    if kana_priority is None:
        return "course" if course_priority is not None else None
    if course_priority is None:
        return "kana"
    if min(kana_priority[0], course_priority[0]) < 4:
        return "kana" if kana_priority <= course_priority else "course"
    if course_started:
        return "course"
    # No historical cross-lane timestamp is invented for legacy queues.
    # Start with a course once, then every persisted completion alternates.
    return "kana" if read(db)["last_lane"] == "course" else "course"
