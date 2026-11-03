"""Small, persisted word contexts earned by ordinary kana practice.

Words remain auxiliary observations. This module never enrolls a vocabulary
item or manufactures a primary review. Preview is read-only; only ``ensure``
reserves a context, in the same transaction as its frozen question snapshot.
"""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import random
from uuid import uuid4

SETTING = "kana_context_words_v1"
EVERY_REVIEWS = 6
KINDS = frozenset({"word_meaning_kana", "word_kana"})


def validate_context_marker(value):
    if (not isinstance(value, dict) or set(value) not in ({"version", "review_count"}, {"version", "review_count", "periodic"})
            or type(value["version"]) is not int or value["version"] != 1
            or 'periodic' in value and type(value['periodic']) is not bool
            or type(value["review_count"]) is not int or value["review_count"] < 0):
        raise ValueError("Invalid kana word-context reservation")
    return dict(value)


def validate_state(value):
    if (not isinstance(value, dict) or set(value) != {"version", "last_review_count", "recent_readings"}
            or type(value["version"]) is not int or value["version"] != 1
            or type(value["last_review_count"]) is not int or value["last_review_count"] < 0
            or not isinstance(value["recent_readings"], list) or len(value["recent_readings"]) > 24
            or any(not isinstance(s, str) or not 1 <= len(s) <= 16 for s in value["recent_readings"])):
        raise ValueError("Invalid kana word-context pacing state")
    return deepcopy(value)


def _state(store):
    return validate_state(store.get_setting(SETTING, {
        "version": 1, "last_review_count": 0, "recent_readings": []}))


def formal_count(store, now):
    return store._db.execute("""SELECT COUNT(*) FROM review_event e
        JOIN study_card c USING(card_id) WHERE c.skill NOT IN
        ('word_kana','word_meaning_kana','word_romaji') AND e.occurred_at_utc<=?""",
        (now.isoformat(),)).fetchone()[0]


def context_active(question, store):
    marker = question.metadata.get('kana_context_v1')
    # Legacy snapshots do not prove whether periodic pacing also caused the
    # question. Keep them, and preserve all independent correction/review work.
    if not isinstance(marker, dict) or marker.get('periodic') is not False:
        return True
    if question.metadata.get('content_replacement_v1'):
        # Explicitly requested, source-checked replacement; the invalid old
        # probe role is archived and does not regain its spent reservation.
        return True
    if any(k in question.metadata for k in ('relearning_of_index', 'spelling_correction_of', 'spelling_correction_index')):
        return True
    from .kana_rule_evidence import active_probe
    return active_probe(store, question.metadata.get('kana_rule_probe_v1')) is not None


def context_priority(question, store=None):
    if store is not None and question.metadata.get('kana_rule_probe_v1'):
        from .kana_rule_evidence import active_probe
        if not active_probe(store, question.metadata['kana_rule_probe_v1']):
            if question.metadata.get('kana_context_v1', {}).get('periodic') is not True:
                return None
    return (2.5, 0) if ((question.kind in KINDS and isinstance(
        question.metadata.get("kana_context_v1"), dict))
        or isinstance(question.metadata.get("kana_rule_probe_v1"), dict)) else None


def _knowledge(repo, store, now):
    """Use independent successful glyph evidence, never merely enrollment."""
    from .kana_reading_rules import READING_RULE_IDS, rule_for_symbol
    rows = {r.get("id", r.get("entry_id")): r for r in repo.kana_list()}
    letters = {"hiragana": {}, "katakana": {}}
    combinations = {"hiragana": {}, "katakana": {}}
    for record in store._db.execute("""SELECT c.content_id,c.script,e.event_id,e.review_log_json
        FROM study_card c JOIN review_event e ON e.rowid=(SELECT p.rowid
          FROM review_event p WHERE p.card_id=c.card_id AND julianday(p.occurred_at_utc)<=julianday(?)
          ORDER BY julianday(p.occurred_at_utc) DESC,p.rowid DESC LIMIT 1)
        WHERE c.suspended=0 AND e.assisted=0 AND e.outcome='correct' AND e.rating>=2
        AND c.skill NOT IN ('word_kana','word_meaning_kana','word_romaji')""", (now.isoformat(),)):
        cid, script = record["content_id"], record["script"]
        from .content_history import contribution_valid
        if not contribution_valid(store._db, record['event_id'], cid): continue
        if cid not in rows or script not in letters or not store.is_content_available(cid, now):
            continue
        if json.loads(record["review_log_json"]).get("kana_answer_evidence", {}).get("accepted_homophone"):
            continue
        symbol = rows[cid].get(script) or ""
        # An old successful combination is not evidence that its components
        # were independently recalled. Shared small-sign teaching is below.
        if len(symbol) == 1:
            letters[script].setdefault(symbol, cid)
        elif symbol:
            combinations[script].setdefault(symbol, cid)
    placeholders = ",".join("?" for _ in READING_RULE_IDS)
    has_courses = store._db.execute("SELECT 1 FROM sqlite_master WHERE name='learning_content'").fetchone()
    ready_rules = {r[0] for r in store._db.execute(f"""SELECT content_id FROM learning_content
        WHERE content_id IN ({placeholders}) AND stage IN ('bridge','check','done')""", READING_RULE_IDS)
        if store.is_content_available(r[0], now)} if has_courses else set()
    # Rule teaching makes its written sign available, without crediting a kana
    # card or equating the introduction with an independently recalled symbol.
    for row in rows.values():
        for script in letters:
            symbol = row.get(script) or ""
            if len(symbol) == 1 and rule_for_symbol(symbol) in ready_rules:
                letters[script].setdefault(symbol, None)
    return letters, ready_rules, combinations


class _OneWordRepository:
    def __init__(self, repo, row): self.repo, self.row = repo, row
    def __getattr__(self, name): return getattr(self.repo, name)
    def practice_word_candidates(self, **kwargs): return [self.row]


def context_candidates(repo, store, kinds, *, audio_available=False, now=None, limit=64, preferred_rules=()):
    """Return eligible, short, script-specific snapshots; no database writes."""
    from .domain import _utc, build_questions, _BatchQuestionStore
    from .kana_reading_rules import SMALL_COMBINING_KANA, word_reading_rules, reviewed_long_vowel_spans
    now = _utc(now)
    enabled = KINDS.intersection(kinds)
    if not audio_available:
        enabled -= {"word_kana"}
    if not enabled:
        return []
    knowledge, rules, combinations = _knowledge(repo, store, now)
    from .word_spelling import _catalog, _tokenize
    kana_rows = repo.kana_list()
    token_catalogues = {script: _catalog(kana_rows, script) for script in knowledge}
    state, count = _state(store), formal_count(store, now)
    seed = int.from_bytes(hashlib.sha256(str(count).encode()).digest()[:8], "big")
    reserved = set()
    for lane in ("main", "word_romaji"):
        pending = store.load_session_progress(lane=lane, lazy_questions=True)
        if pending:
            reserved.update(q.content_id for q in pending["questions"][pending["question_index"]:])
    candidates = []
    for row in repo.practice_word_candidates(include_research=False):
        reading = row.get("reading") or row.get("display_reading") or ""
        cid = row.get("id", row.get("entry_id"))
        if (not isinstance(cid, str) or cid in reserved or row.get("practice_approved") is not True
                or not 2 <= len(reading) <= 6 or not store.is_content_available(cid, now)):
            continue
        script = "katakana" if any("ァ" <= c <= "ヺ" for c in reading) else "hiragana"
        from .domain import to_script
        answer = to_script(reading, script)
        morae = sum(char not in SMALL_COMBINING_KANA for char in answer)
        required_rules = word_reading_rules(answer)
        if not 2 <= morae <= 5 or not set(required_rules) <= rules or any(c not in knowledge[script] for c in answer):
            continue
        tokens = _tokenize(answer, *token_catalogues[script])
        groups = [token.text for token in tokens if len(token.text) > 1]
        if any(group not in combinations[script] for group in groups):
            continue
        required = list(dict.fromkeys(knowledge[script][c] for c in answer if knowledge[script][c]))
        required.extend(combinations[script][group] for group in dict.fromkeys(groups)
                        if combinations[script][group] not in required)
        if not required:
            continue
        candidates.append((reading in state["recent_readings"], len(answer), cid, script, row, required, required_rules))
    # Different counts produce different stable tie orders; restoration uses
    # the saved snapshot, not a second random draw.
    rng = random.Random(seed)
    rng.shuffle(candidates)
    candidates.sort(key=lambda row: (not bool(set(row[6]) & set(preferred_rules)), row[0], row[1]))
    result = []
    for _, _, cid, script, row, required, required_rules in candidates[:max(1, min(64, limit))]:
        kind = "word_meaning_kana" if "word_meaning_kana" in enabled else "word_kana"
        made = build_questions(_OneWordRepository(repo, row), _BatchQuestionStore(store, [cid]),
            kind=kind, script=script, limit=1, seed=seed, now=now, word_table="partial")
        if not made:
            continue
        question = made[0]
        options = list(dict.fromkeys(question.answer))
        distractors = [c for c in knowledge[script] if c not in options]
        rng.shuffle(distractors)
        options.extend(distractors[:max(0, 8-len(options))])
        rng.shuffle(options)
        question.options[:] = options
        question.metadata.update(kana_context_v1={"version": 1, "review_count": count},
            answer_tokens=list(question.answer), word_table="partial", required_kana_ids=required,
            reading_rule_ids=list(required_rules))
        spans = reviewed_long_vowel_spans(question.answer)
        if spans:
            question.metadata["long_vowel_spans"] = list(spans)
        question.metadata["practice_config"].update(kinds=list(kinds), schedule_mode="spaced", word_table="partial")
        result.append({"question": question, "content_id": cid, "context_id": "word:"+question.answer,
            "kind": kind, "script": script, "source": "word", "required_kana_ids": required,
            "rules": required_rules})
    return result


def preview_context_word(repo, store, kinds, *, audio_available=False, now=None):
    """One potential context for home counts, with no queue/credit writes."""
    from .domain import _utc
    from .kana_rule_evidence import SETTING as PROBE_SETTING, next_rule_probe
    now = _utc(now)
    if not KINDS.intersection(kinds):
        return False
    pending = store.load_session_progress(lane="main", lazy_questions=True)
    if pending and any(q.kind in KINDS and context_active(q, store) for q in pending["questions"][pending["question_index"]:]):
        return False
    credit = formal_count(store, now) - _state(store)["last_review_count"] >= EVERY_REVIEWS
    active = [rule for rule, entry in store.get_setting(PROBE_SETTING, {}).get("rules", {}).items()
        if len(entry["probes"]) < 2 and datetime.fromisoformat(entry["not_before_utc"]) <= now]
    if not credit and not active:
        return False
    candidates = context_candidates(repo, store, kinds, audio_available=audio_available, now=now,
        limit=32 if active else 1, preferred_rules=active)
    if credit:
        return bool(candidates)
    return next_rule_probe(store, _probe_candidates(candidates), enabled_kinds=kinds,
                           now=now, reserve=False) is not None


def _probe_candidates(candidates):
    from .kana_reading_rules import SMALL_KANA_RULE
    from .kana_rule_evidence import small_kana_context
    probes = []
    for row in candidates:
        for rule in row["rules"]:
            context = small_kana_context(row["question"].answer) if rule == SMALL_KANA_RULE else row["context_id"]
            if context:
                probes.append({**row, "rule_id": rule, "context_id": context})
    return probes[:128]


def ensure_kana_context_word(repo, store, kinds, *, audio_available=False, now=None):
    """Reserve at most one word at an explicit continue boundary, atomically."""
    from .domain import _utc, prepare_session_snapshot
    from . import session_questions
    from .kana_rule_evidence import next_rule_probe
    from .progress_backup import MAX_BYTES
    now = _utc(now)
    if not KINDS.intersection(kinds):
        return {"added": 0, "session_id": None}
    if store._db.in_transaction:
        raise RuntimeError("Word context insertion requires its own transaction")
    try:
        store._db.execute("BEGIN IMMEDIATE")
        pending = store.load_session_progress(lane="main", lazy_questions=True)
        if pending and any(q.kind in KINDS and context_active(q, store) for q in pending["questions"][pending["question_index"]:]):
            store._db.commit()
            return {"added": 0, "session_id": None}
        state, count = _state(store), formal_count(store, now)
        from .kana_rule_evidence import SETTING as PROBE_SETTING
        active_rules = [rule for rule, entry in store.get_setting(PROBE_SETTING, {}).get("rules", {}).items()
            if len(entry.get("probes", [])) < 2
            and datetime.fromisoformat(entry["not_before_utc"]) <= now
            and store.is_content_available(rule, now)]
        if count - state["last_review_count"] < EVERY_REVIEWS and not active_rules:
            store._db.commit()
            return {"added": 0, "session_id": None}
        candidates = context_candidates(repo, store, kinds, audio_available=audio_available, now=now,
            limit=32 if active_rules else 1, preferred_rules=active_rules)
        probes = _probe_candidates(candidates)
        probe = next_rule_probe(store, probes, enabled_kinds=kinds, now=now, reserve=False)
        if not candidates or (count - state["last_review_count"] < EVERY_REVIEWS and probe is None):
            store._db.commit()
            return {"added": 0, "session_id": None}
        if probe:
            probe = next_rule_probe(store, probes, enabled_kinds=kinds, now=now, reserve=True)
        question = (probe or candidates[0])["question"]
        question.metadata['kana_context_v1']['periodic'] = count - state['last_review_count'] >= EVERY_REVIEWS
        if probe:
            question.metadata["kana_rule_probe_v1"] = {key: probe[key] for key in ("rule_id", "probe_id", "context_id", "trigger_event_id")}
        prepared = prepare_session_snapshot([question], count)
        if pending:
            sid = pending["session_id"]
            snapshot = json.dumps([*store._session_snapshot(sid), question.to_dict()], ensure_ascii=False, allow_nan=False)
            if len(snapshot.encode("utf-8")) > MAX_BYTES:
                raise ValueError("练习队列过大，未添加单词。")
            session_questions.append(store._db, sid, [question.to_dict()])
        else:
            sid = str(uuid4())
            store._db.execute("INSERT INTO study_session VALUES (?,?,?,?,?)", (sid, now.isoformat(),
                store.time_zone, prepared.random_seed, prepared.policy_version))
            session_questions.append(store._db, sid, [question.to_dict()])
            store._db.execute("INSERT INTO session_checkpoint VALUES (?,?,?,?,?,?)", (sid, now.isoformat(), 0, 0, "[]", "{}"))
        state.update(last_review_count=count,
            recent_readings=[*state["recent_readings"], question.metadata["original_reading"]][-24:])
        store._db.execute("INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
            (SETTING, json.dumps(state, ensure_ascii=False)))
        store._db.commit()
        return {"added": 1, "session_id": sid}
    except Exception:
        store._db.rollback()
        raise


def ensure_kana_rule_probe(repo, store, kinds, *, now=None):
    """Reserve an already-due, independently known combination in its queue.

    No new review is invented and no future timer is removed. A genuine answer
    receives its normal rating; every other combination keeps its exact state.
    This works even with all word exercise kinds disabled.
    """
    from .domain import _utc, WORD_SKILLS
    from . import session_questions
    from .kana_reading_rules import SMALL_KANA_RULE
    from .kana_rule_evidence import next_rule_probe, small_kana_context, SETTING as PROBE_SETTING
    from .kana_question_policy import question_allowed
    from .kana_dependency_runtime import kana_dependency_state, kana_dependency_graph, kana_prerequisite_status
    from .review_dispatch import kana_resume_priority
    now = _utc(now)
    empty = {"added": 0, "session_id": None}
    trigger = store.get_setting(PROBE_SETTING, {}).get("rules", {}).get(SMALL_KANA_RULE)
    if (not trigger or len(trigger["probes"]) >= 2
            or datetime.fromisoformat(trigger["not_before_utc"]) > now):
        return empty
    if store._db.in_transaction:
        raise RuntimeError("Rule reservation requires its own transaction")
    try:
        store._db.execute("BEGIN IMMEDIATE")
        pending = store.load_session_progress(lane="main", lazy_questions=True)
        if not pending:
            store._db.commit()
            return empty
        index = pending["question_index"]
        suffix = pending["questions"][index:]
        from .kana_rule_evidence import active_probe
        if any(active_probe(store, q.metadata.get("kana_rule_probe_v1")) for q in suffix):
            store._db.commit()
            return empty
        catalogue = {r.get("id", r.get("entry_id")): r for r in repo.kana_list()}
        state, graph = kana_dependency_state(store), kana_dependency_graph()
        candidates = []
        for position, q in enumerate(suffix, start=index):
            if (q.kind not in kinds or q.kind in WORD_SKILLS or not question_allowed(q)
                    or position == index and kana_resume_priority(pending["state"]) is not None):
                continue
            card = store.get_scheduling_card(q.content_id, q.kind, q.script)
            if (not card or not card["review_count"] or card["suspended"]
                    or datetime.fromisoformat(card["due_at_utc"]) > now
                    or q.metadata.get("not_before_utc") and datetime.fromisoformat(q.metadata["not_before_utc"]) > now
                    or kana_prerequisite_status(q, graph, state)["blocked"]):
                continue
            symbol = q.metadata.get("kana_symbol") or catalogue.get(q.content_id, {}).get(q.script, "")
            context = small_kana_context(symbol)
            if context:
                candidates.append({"rule_id": SMALL_KANA_RULE, "context_id": context,
                    "content_id": q.content_id, "kind": q.kind, "script": q.script,
                    "source": "kana", "question": q, "position": position, "symbol": symbol})
            if len(candidates) >= 128:
                break
        proposal = next_rule_probe(store, candidates, enabled_kinds=kinds, now=now, reserve=True)
        if proposal is None:
            store._db.commit()
            return empty
        rows = deepcopy(store._session_snapshot(pending["session_id"]))
        rows[proposal["position"]].setdefault("metadata", {})["kana_rule_probe_v1"] = {
            key: proposal[key] for key in ("rule_id", "probe_id", "context_id", "trigger_event_id")}
        rows[proposal["position"]]["metadata"].setdefault("kana_symbol", proposal["symbol"])
        rows[proposal["position"]]["metadata"]["reading_rule_ids"] = [SMALL_KANA_RULE]
        session_questions.replace(store._db, pending["session_id"], rows)
        store._db.commit()
        return {"added": 0, "session_id": pending["session_id"], "probe_reserved": True}
    except Exception:
        store._db.rollback()
        raise
