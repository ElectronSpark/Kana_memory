"""App-owned assessment eligibility; model assertions never grant credit.

This first policy keeps existing reviewed local courses working, while retained
AI drafts remain observations. Submission and settlement call the same policy
with current saved content, inside the progress database transaction.
"""
from .material_review import fingerprint, reviewed_question

VERSION = 1
CONTRACT_FIELDS = (
    "target_id", "revision", "family", "domain", "ability", "kind", "prompt",
    "task", "answer", "accepted_answers", "choices", "options", "input_mode",
    "response", "audio_text", "pitch_practice", "practice", "guided",
    "retired_ambiguous_answers",
    "alternative_answers", "strict", "known_wrong", "support", "optional_audio_text",
)


def contract_values(question):
    value = {key: question.get(key) for key in CONTRACT_FIELDS}
    if 'material_qualification' in question:
        value['material_qualification'] = question['material_qualification']
    return value
MESSAGES = {
    "ai_material_unverified": "材料尚待核验；保留本次回答，不调整复习间隔。",
    "material_blocked": "这道题的内容需要核查；本次不调整复习间隔。",
    "target_mismatch": "题目与当前学习内容不一致；本次不调整复习间隔。",
    "contract_changed": "题目内容在作答后发生变化；保留原回答，不调整复习间隔。",
    "opportunity_consumed": "这次作答已记录；保留回答，不重复调整复习间隔。",
    "personal_content_changed": "相关说明已修订，这道旧题仍待核对；保留回答，不调整复习间隔。",
}


def _ai_origin(item, question):
    return any(
        str(value.get("id", "")).startswith("local:ai:")
        or str(value.get("id", value.get("target_id", ""))).startswith("material-candidate:")
        or str(value.get("content_id", "")).startswith("local:ai:")
        or str(value.get("revision", "")).startswith("ai-material-v1-")
        or value.get("material_status") == "ai_user_retained"
        or "ai-user-reviewed" in (value.get("source_ids") or [])
        or bool(value.get("ai_generated"))
        for value in (item, question)
    )


def teaching_allowed(item, *, store=None):
    """Completeness and a user's retention decision do not verify AI semantics."""
    if item.get('instruction_status') == 'instruction_missing' or item.get('personal_content_unavailable'): return False
    if not _ai_origin(item, {}): return True
    from .material_reference import item_ready
    return item_ready(store, item)


def assessment_decision(item, question, *, purpose="scheduled_review", enrolled=True, exposed=False, formal_check=False, store=None):
    """Return a detached decision for a saved question, not an AI certificate.

    Do not use frozen feedback's material_view for current eligibility: an
    intervening exact local correction must be checked again at settlement.
    """
    if (purpose not in {"scheduled_review", "check_only", "check_and_adjust", "guided_practice", "preview"}
            or type(enrolled) is not bool or type(exposed) is not bool or type(formal_check) is not bool):
        raise ValueError("Invalid assessment purpose")
    current = {k: v for k, v in question.items() if k not in {"result", "status"}}
    # Old course queues allowed an omitted revision; they are owned by the
    # immutable enrolled snapshot. Do not rewrite those saved questions.
    current.setdefault("revision", item.get("revision"))
    current = reviewed_question(current)
    reason = None
    if (question.get("target_id") != item.get("id", item.get("content_id"))
            or str(current.get("revision")) != str(item.get("revision"))):
        reason = "target_mismatch"
    elif current.get("assessment_blocked_reason"):
        reason = "material_blocked"
    elif question.get('material_qualification'):
        from .material_qualification import qualification
        if store is None or not qualification(store, question):
            reason = 'material_blocked'
    elif _ai_origin(item, question):
        from .material_reference import question_ready
        if not question_ready(store, item, question): reason = "ai_material_unverified"
    personal = {}
    if reason is None and store is not None:
        from .personal_content_versions import question_review
        personal = question_review(store, item, question)
        if personal['blocked']: reason = 'personal_content_changed'
    validity = "unverified" if reason == "ai_material_unverified" else "blocked" if reason else "eligible_local"
    qualified = reason is None
    channel = "observation"
    if qualified and enrolled and not exposed:
        channel = {"scheduled_review": "formal", "check_and_adjust": "auxiliary"}.get(purpose, "observation")
        if purpose == "check_and_adjust" and formal_check:
            channel = "formal"
    return {
        "version": VERSION,
        "contract_sha256": fingerprint(contract_values(current)),
        "content_validity": validity,
        "assessment_eligible": qualified,
        "permitted_purposes": (["preview", "guided_practice", "check_only"]
            + (["check_and_adjust", "scheduled_review"] if enrolled and not exposed else [])) if qualified else ["preview"],
        "purpose": purpose,
        "enrolled": enrolled,
        "exposed": exposed,
        "settlement_channel": channel,
        "reason": reason,
        **({'reading_qualification': personal['reading_qualification']} if personal.get('reading_qualification') and qualified else {}),
        **({'rubric_qualification': personal['rubric_qualification']} if personal.get('rubric_qualification') and qualified else {}),
    }


def settlement_decision(item, question, submitted, *, formal_check=False, store=None):
    purpose = (submitted or {}).get("purpose", "scheduled_review")
    if purpose not in {"scheduled_review", "check_only", "check_and_adjust", "guided_practice", "preview"}:
        purpose = "preview"
    decision = assessment_decision(item, question, purpose=purpose, formal_check=formal_check, store=store)
    if (decision["reason"] is None and submitted is not None
            and (submitted.get("version") != VERSION
                 or submitted.get("contract_sha256") != decision["contract_sha256"])):
        decision.update(content_validity="blocked", assessment_eligible=False, permitted_purposes=['preview'],
                        settlement_channel="observation", reason="contract_changed")
    elif submitted is not None and submitted.get("settlement_channel") != "formal":
        # An observation never acquires credit later, even if permission or
        # readiness changed after the learner saw the answer.
        decision["settlement_channel"] = "observation"
    return decision


def assessment_message(decision):
    return MESSAGES.get((decision or {}).get("reason"), "这题暂不计入复习。")
