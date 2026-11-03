"""Short first exposure for newly generated kana questions, never an SRS review.

Legacy saved questions are deliberately unmarked and keep their exact order,
drafts and timing. The batch generator opts new snapshots in with
``metadata['kana_intro_v1'] = True``; this module does not rewrite snapshots.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
import json
import sqlite3
from types import SimpleNamespace

from .domain import SKILLS, WORD_SKILLS, SCRIPTS, _utc, to_script
from .kana_audio import kana_audio_plan
from .romanization import kana_learning_romaji
from .content_pack import load_namespace


KANA_INTRO_SETTING = "kana_introductions_v1"
KANA_INTRO_MARKER = "kana_intro_v1"
KANA_FAMILIARITY_MARKER = "kana_familiarity_v1"
FAMILIARITY_LEVELS = frozenset({"new", "some", "familiar"})
_KANA_KINDS = SKILLS - WORD_SKILLS
_INTRO_NOTES = load_namespace("presentation_metadata", {}).get("kana_intro_notes", {})


def _fields(question):
    return question if isinstance(question, Mapping) else vars(question) if hasattr(question, "__dict__") else {}


def _identity(question):
    q = _fields(question)
    content_id, script = q.get("content_id"), q.get("script")
    if (q.get("kind") not in _KANA_KINDS or not isinstance(content_id, str) or not content_id
            or script not in SCRIPTS):
        return None
    return content_id, script


def _key(identity):
    return identity[0] + "|" + identity[1]


def _saved(store):
    setting = store.get_setting(KANA_INTRO_SETTING, {})
    if not isinstance(setting, dict) or setting.get("version") != 1 or not isinstance(setting.get("introduced"), dict):
        return {"version": 1, "introduced": {}}
    return {**setting, "introduced": dict(setting["introduced"])}


def _introduced_keys(store):
    """Read-only membership without repeatedly decoding the whole intro map.

    Each call still reads the current SQL value, so writes, imports, resets,
    external commits and transaction rollbacks invalidate this narrow cache.
    Mutable setting readers keep using ``_saved`` and never receive the cache.
    """
    db = getattr(store, "_db", None)
    if not isinstance(db, sqlite3.Connection):
        return frozenset(_saved(store)["introduced"])
    row = db.execute("SELECT value_json FROM app_setting WHERE key=?", (KANA_INTRO_SETTING,)).fetchone()
    raw = row[0] if row else None
    cached = getattr(store, "_kana_intro_keys_cache", None)
    if cached is not None and cached[0] == raw:
        return cached[1]
    setting = json.loads(raw) if row else {}
    valid = (isinstance(setting, dict) and setting.get("version") == 1
             and isinstance(setting.get("introduced"), dict))
    keys = frozenset(setting["introduced"]) if valid else frozenset()
    store._kana_intro_keys_cache = (raw, keys)
    return keys


def initial_familiarity(store, question):
    """Return a script-specific self-report, never inferred SRS evidence."""
    identity = _identity(question)
    records = _saved(store).get("familiarity", {})
    record = records.get(_key(identity)) if identity and isinstance(records, dict) else None
    if (not isinstance(record, dict) or not isinstance(record.get("level"), str)
            or record["level"] not in FAMILIARITY_LEVELS):
        return None
    return dict(record)


def _validate_introduction_question(db, session_id, index, question):
    identity, q = _identity(question), _fields(question)
    metadata = q.get("metadata")
    if (identity is None or not isinstance(metadata, Mapping) or metadata.get(KANA_INTRO_MARKER) is not True
            or not isinstance(session_id, str) or not session_id or type(index) is not int or index < 0):
        raise ValueError("A new marked question and its current checkpoint are required")
    current = db.execute("SELECT question_index,state_json FROM session_checkpoint WHERE session_id=?",
                         (session_id,)).fetchone()
    if current is None or current["question_index"] != index:
        raise ValueError("The introduction no longer belongs to the active question")
    frozen = db.execute("SELECT snapshot_json FROM session_question WHERE session_id=? AND question_index=?",
                        (session_id, index)).fetchone()
    if frozen is None:
        raise ValueError("The introduction question no longer exists")
    original = json.loads(frozen[0])
    if (_identity(original) != identity or original.get("kind") != q.get("kind")
            or original.get("prompt") != q.get("prompt") or original.get("answer") != q.get("answer")
            or original.get("metadata", {}).get(KANA_INTRO_MARKER) is not True):
        raise ValueError("The introduction does not match the saved question")
    return identity, json.loads(current["state_json"])


def select_initial_familiarity(store, session_id, index, question, level, now=None, *, fair_new=False):
    """Choose teaching depth once, leaving queues, drafts and all SRS untouched.

    A familiar declaration bypasses teaching only; the existing independent
    question must still be answered and rated. It grants no skill or sibling
    script a successful retrieval, interval or completion credit.
    """
    if not isinstance(level, str) or level not in FAMILIARITY_LEVELS:
        raise ValueError("Unknown initial familiarity")
    now, db = _utc(now), store._db
    if db.in_transaction:
        raise RuntimeError("Cannot select familiarity inside another transaction")
    with db:
        db.execute("BEGIN IMMEDIATE")
        identity, state = _validate_introduction_question(db, session_id, index, question)
        existing = initial_familiarity(store, question)
        if existing:
            if existing["level"] != level:
                raise ValueError("Initial familiarity has already been chosen")
            return existing
        # Never turn an already visible answer/draft into an unexposed test.
        if state or not should_introduce(SimpleNamespace(store=store), question):
            raise ValueError("This kana has already started learning")
        from .explicit_learning_start import claim
        claim(db, identity[0], script=identity[1], now=now)
        saved = _saved(store)
        record = {"content_id": identity[0], "script": identity[1], "level": level,
                  "evidence": "self_report", "selected_at_utc": now.isoformat(),
                  "source_session_id": session_id, "source_question_index": index}
        records = saved.get("familiarity", {})
        saved["familiarity"] = {**(records if isinstance(records, dict) else {}), _key(identity): record}
        if level == "familiar":
            saved["introduced"][_key(identity)] = {
                "content_id": identity[0], "script": identity[1],
                "source_session_id": session_id, "source_question_index": index,
                "route": "prior_knowledge_check", "selected_at_utc": now.isoformat(),
            }
            # Earlier releases did not persist opening the teaching screen.
            # A saved legacy question may already have shown its answer; only
            # new snapshots explicitly guarantee an unexposed first test.
            if _fields(question).get("metadata", {}).get(KANA_FAMILIARITY_MARKER) is not True:
                saved["introduced"][_key(identity)]["wait_until_utc"] = (now + timedelta(minutes=1)).isoformat()
                saved["introduced"][_key(identity)]["route"] = "legacy_exposure_check"
        db.execute("""INSERT INTO app_setting VALUES (?,?)
            ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json""",
            (KANA_INTRO_SETTING, json.dumps(saved, ensure_ascii=False, allow_nan=False)))
        if fair_new and level == "familiar":
            from .new_dispatch import completed
            completed(db, "kana", kind=_fields(question)["kind"])
    return record


def should_introduce(app, question):
    """Only fresh, explicitly opted-in, wholly unexposed script items qualify."""
    q, identity = _fields(question), _identity(question)
    metadata = q.get("metadata")
    if identity is None or not isinstance(metadata, Mapping) or metadata.get(KANA_INTRO_MARKER) is not True:
        return False
    # Later timed retries and diagnostic corrections are not new content.
    if metadata.get("not_before_utc") or metadata.get("spelling_correction_of"):
        return False
    store = app.store
    if _key(identity) in _introduced_keys(store):
        return False
    content_id, script = identity
    if store.queries.kana_has_review_evidence(content_id, script):
        return False
    if store.queries.kana_has_attempt(content_id, script):
        return False
    # Contextual spelling may have exposed a symbol without creating a card.
    if store.word_spelling_events(content_id, script, limit=1):
        return False
    return True


def _example(repo, content_id, symbol, script):
    words = repo.kana_words(content_id, limit=100)
    eligible = [word for word in words if word.get("practice_approved") is True
                and isinstance(word.get("reading"), str) and symbol in word["reading"]
                and word.get("form")]
    # Prefer a short, naturally written example in the current script. Do not
    # rewrite an ordinary Japanese word into katakana just to invent a match.
    eligible.sort(key=lambda word: (len(word["reading"]), word.get("rank") or 10**9, word["id"]))
    if not eligible:
        return None
    word = eligible[0]
    gloss = str(word.get("gloss") or "").split(" | ", 1)[0]
    # A single compact meaning is enough for an introductory example.
    return {"id": word["id"], "form": word["form"], "reading": word["reading"],
            "meaning": gloss.split("；", 1)[0]}


def intro_content(repo, question):
    """Build present-day teaching content from existing attributed resources."""
    identity = _identity(question)
    if identity is None:
        raise ValueError("A kana question with a specific script is required")
    content_id, script = identity
    record = repo.get_kana(content_id)
    if not isinstance(record, dict) or not record.get(script):
        raise ValueError("The kana introduction is missing its target form")
    symbol = record[script]
    plan = kana_audio_plan(record)
    category = record.get("category")
    note_key = "wo" if to_script(symbol, "hiragana") == "を" else category
    note = _INTRO_NOTES.get(note_key, "")
    return {"content_id": content_id, "script": script, "symbol": symbol,
            "script_label": "平假名" if script == "hiragana" else "片假名",
            "paired_symbol": record.get("katakana" if script == "hiragana" else "hiragana", ""),
            "romaji": kana_learning_romaji(record), "audio_text": plan.text,
            "audio_label": plan.label or "现代日语读音", "note": note,
            "example": _example(repo, content_id, symbol, script)}


def mark_introduced(store, question):
    """Save teaching completion only. No card, review, attempt or queue write."""
    identity = _identity(question)
    if identity is None:
        raise ValueError("A kana question with a specific script is required")
    saved = _saved(store)
    key = _key(identity)
    if key in saved["introduced"]:
        return False
    saved["introduced"][key] = {"content_id": identity[0], "script": identity[1]}
    store.set_setting(KANA_INTRO_SETTING, saved)
    return True


def intro_wait_until(store, question):
    """Return the persisted first-exposure delay; never reschedule or renew it."""
    identity = _identity(question)
    q = _fields(question)
    metadata = q.get("metadata")
    if identity is None or not isinstance(metadata, Mapping) or metadata.get(KANA_INTRO_MARKER) is not True:
        return None
    record = _saved(store)["introduced"].get(_key(identity))
    if not isinstance(record, dict) or not isinstance(record.get("wait_until_utc"), str):
        return None
    try:
        return _utc(datetime.fromisoformat(record["wait_until_utc"]))
    except ValueError:
        return None


def intro_wait_times(store, questions):
    """Read the introduction setting once when planning a long saved queue."""
    identities = set()
    for question in questions:
        q = _fields(question)
        metadata = q.get("metadata")
        identity = _identity(question)
        if identity and isinstance(metadata, Mapping) and metadata.get(KANA_INTRO_MARKER) is True:
            identities.add(identity)
    if not identities:
        return {}
    introduced, result = _saved(store)["introduced"], {}
    for identity in identities:
        record = introduced.get(_key(identity))
        if not isinstance(record, dict) or not isinstance(record.get("wait_until_utc"), str):
            continue
        try:
            result[identity] = _utc(datetime.fromisoformat(record["wait_until_utc"]))
        except ValueError:
            continue
    return result


def finish_introduction(store, session_id, index, question, now=None, *, fair_new=False):
    """Atomically save exposure and a short delay without touching the queue.

    The one-minute delay is a product default for avoiding immediate copying,
    not a calibrated memory interval or an FSRS success. A future independent
    question still owns its usual first rating. Checkpoint and snapshot bytes
    remain unchanged, including pending input and previously planned reviews.
    """
    now = _utc(now)
    db = store._db
    if db.in_transaction:
        raise RuntimeError("Cannot finish introduction inside another transaction")
    with db:
        db.execute("BEGIN IMMEDIATE")
        identity, _ = _validate_introduction_question(db, session_id, index, question)
        saved = _saved(store)
        key = _key(identity)
        if key not in saved["introduced"]:
            from .explicit_learning_start import claim
            claim(db, identity[0], script=identity[1], now=now)
            saved["introduced"][key] = {
                "content_id": identity[0], "script": identity[1],
                "introduced_at_utc": now.isoformat(),
                "wait_until_utc": (now + timedelta(minutes=1)).isoformat(),
                "source_session_id": session_id, "source_question_index": index,
            }
            db.execute("""INSERT INTO app_setting VALUES (?,?)
                ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json""",
                (KANA_INTRO_SETTING, json.dumps(saved, ensure_ascii=False, allow_nan=False)))
            if fair_new:
                from .new_dispatch import completed
                completed(db, "kana", kind=_fields(question)["kind"])
    return intro_wait_until(store, question)
