"""Append missing kana foundations when an existing main queue is continued.

This is a queue repair, not a data migration or a scheduler. It never rewrites
old questions, drafts, reviews, suspended controls, or card due dates.
"""
from __future__ import annotations

from datetime import datetime
import secrets

from .kana_dependencies import kana_entry_id
from .kana_dependency_runtime import (
    kana_dependency_graph, kana_dependency_state, missing_kana_prerequisites,
)
from .kana_question_policy import _KANA_KINDS, direction_allowed, question_allowed


def _remaining_targets(store, pending):
    """Project scalar identities from the store's revision-guarded snapshot.

    No metadata or mutable question object escapes. Question-index, evidence,
    availability and due-time decisions are freshly evaluated on each visit.
    A snapshot replacement/append, external write or restore changes its
    identity; transactional reads never publish or reuse this derived cache.
    """
    snapshot = store._session_snapshot(pending["session_id"])
    cacheable = not store._db.in_transaction
    cached = getattr(store, "_kana_repair_targets_cache", None) if cacheable else None
    start = pending["question_index"]
    if cached is None or cached[0] is not snapshot or start < cached[2]:
        identities = tuple((index, kana_entry_id(q["content_id"], q["script"]))
            for index, q in enumerate(snapshot[start:], start)
            if q["kind"] in _KANA_KINDS and question_allowed(q))
        cached = (snapshot, identities, start)
        if cacheable:
            store._kana_repair_targets_cache = cached
    return [identity for index, identity in cached[1] if index >= pending["question_index"]]


def ensure_kana_prerequisites(repo, store, kinds, *, now=None, audio_available=False,
                              handwriting_available=False, lazy_questions=False):
    """Repair only the missing prerequisites of untouched pending kana targets.

    One unfinished question already reserves its entire script identity. A
    previously assessed foundation retains its due time, even after a change
    of enabled question direction. Paused foundations remain paused and are
    left for the ordinary blocked-prerequisite interface to explain.
    """
    from .domain import (
        HANDWRITING_SKILLS, LISTENING_SKILLS, SKILLS, _BatchQuestionStore,
        _adaptive_new_kinds, _utc, build_questions,
    )

    empty = {"added": 0, "session_id": None}
    if isinstance(kinds, (str, bytes)) or any(kind not in SKILLS for kind in kinds):
        raise ValueError("未知假名题型。")
    kinds = [kind for kind in dict.fromkeys(kinds) if kind in _KANA_KINDS
             and (audio_available or kind not in LISTENING_SKILLS)
             and (handwriting_available or kind not in HANDWRITING_SKILLS)]
    if not kinds:
        return empty
    # Internal read-only projection needs no copies of full historical tasks.
    pending = store.load_session_progress(lane="main", lazy_questions=True)
    if not pending:
        return empty
    targets = _remaining_targets(store, pending)
    if not targets:
        return empty
    state = kana_dependency_state(store)
    if all(identity in state.reviewed for identity in targets):
        return empty
    rows = repo.kana_list()
    # Repository fixtures may implement only word exercises. Never substitute
    # the bundled inventory for an explicit repository or an incomplete mock.
    if not isinstance(rows, (list, tuple)):
        return empty
    graph = kana_dependency_graph(rows)
    targets = [identity for identity in targets if identity in graph]
    if not targets:
        return empty
    active = set(targets)
    missing = [identity for identity in missing_kana_prerequisites(targets, graph, state)
               if identity not in active]
    if not missing:
        return empty
    now = _utc(now)
    from .kana_reading_rules import READING_RULE_IDS
    rule_ids = [identity for identity in missing if identity in READING_RULE_IDS]
    if rule_ids:
        from .learning_content import learning_items
        from .learning_store import LearningStore
        learning = LearningStore(store)
        catalogue = {item["id"]: item for item in learning_items()}
        learning.enroll_with_prerequisites([catalogue[rid] for rid in rule_ids if rid in catalogue],
                                          catalogue.values(), now=now)
    missing = [identity for identity in missing if identity.startswith("kana:")]
    by_identity = {}
    for row in store.scheduling_cards(aliases=True):
        if row["skill"] in _KANA_KINDS:
            by_identity.setdefault(kana_entry_id(row["content_id"], row["script"]), []).append(dict(row))
    seed, questions = secrets.randbits(32), []
    old_lane = store.practice_lane
    store.practice_lane = "main"
    try:
        token = store.session_append_token(pending["session_id"])
    finally:
        store.practice_lane = old_lane
    for identity in missing:
        cid, script = identity[len("kana:"):].rsplit(":", 1)
        if not store.is_content_available(cid, now):
            continue
        cards = by_identity.get(identity, [])
        suspended = {card["skill"] for card in cards if card["suspended"]}
        enabled = [kind for kind in kinds if kind not in suspended
                   and direction_allowed(cid, kind, script)]
        if not enabled:
            continue
        reviewed = identity in state.reviewed
        due_card = None
        established = []
        if reviewed:
            established = [card for card in cards if card["review_count"] > 0 and not card["suspended"]]
            if not established:
                continue
            preferred = [card for card in established if card["skill"] in enabled]
            due_card = min(preferred or established, key=lambda card: card["due_at_utc"])
            # A real enabled card can wait in the queue until its own due.
            # Creating a replacement direction is deferred until the old
            # direction is actually due; it never grants a fresh introduction.
            if not preferred and datetime.fromisoformat(due_card["due_at_utc"]) > now:
                continue
            if due_card["skill"] in enabled:
                enabled = [due_card["skill"], *[kind for kind in enabled if kind != due_card["skill"]]]
        else:
            enabled = list(_adaptive_new_kinds(enabled, 0))
        for kind in enabled:
            candidate_due = due_card
            if reviewed:
                actual = next((card for card in established if card["skill"] == kind), None)
                candidate_due = actual or min(established, key=lambda card: card["due_at_utc"])
                if actual is None and datetime.fromisoformat(candidate_due["due_at_utc"]) > now:
                    continue
            made = build_questions(repo, _BatchQuestionStore(store, [cid]), kind=kind,
                script=script, limit=1, seed=seed, now=now, include_extended=True)
            if not made:
                continue
            question = made[0]
            question.metadata.update(study_path_entry=identity, schedule_mode="spaced",
                                     adaptive_policy="scaffold-v1", dependency_prerequisite_v1=True)
            if reviewed:
                question.metadata.update(study_path_due_v1=True, not_before_utc=candidate_due["due_at_utc"])
            else:
                question.metadata.update(kana_intro_v1=True, kana_familiarity_v1=True, adaptive_stage=0)
            question.metadata["practice_config"].update(schedule_mode="spaced", adaptive=True,
                kinds=kinds, requested_script=script, rounds=1)
            questions.append(question)
            break
    if not questions:
        return empty
    old_lane = store.practice_lane
    store.practice_lane = "main"
    try:
        store.append_session(pending["session_id"], questions, seed=seed, expected_token=token,
                             distinct_scripts=True, curriculum_directions=True, now=now)
    finally:
        store.practice_lane = old_lane
    return {"added": len(questions), "session_id": pending["session_id"]}
