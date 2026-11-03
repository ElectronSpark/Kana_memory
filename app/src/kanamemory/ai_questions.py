"""Read-only learner scope and conservative AI choice-question validation.

The model supplies text, never identities, scheduling decisions or safety flags.
Structural validation does not certify Japanese semantics. These snapshots are
training material and belong to AIPracticeStore, never the FSRS review queue.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from . import app_clock
from hashlib import sha256
import json
import math
import random
import re
import unicodedata

from .learning_questions import KIND_ABILITIES, KIND_LABELS, question_kinds

SCOPE_FORMAT = "kanamemory-ai-scope"
AI_CHOICE_KINDS = ("vocab_meaning", "vocab_usage", "vocab_zh_ja_contrast",
                   "reading_comprehension", "grammar_meaning", "grammar_cloze")
MAX_QUESTIONS = 20
PROPOSAL_FIELDS = frozenset({"target_id", "revision", "kind", "prompt", "choices", "answer", "explanation"})
OPTIONAL_PROPOSAL_FIELDS = frozenset({"order_translation"})
GAP = "（　）"
_MEANING = frozenset({"vocab_meaning", "vocab_zh_ja_contrast", "reading_comprehension", "grammar_meaning"})
_GAPS = re.compile(r"[_＿□■◯○]|[（(\[【]\s*[）)\]】]|\{[^}]*\}|<[^>]*>|\.\.\.|…")


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _normal(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()


def _text(value, field, maximum, *, blank=False):
    if (not isinstance(value, str) or len(value) > maximum or (not blank and not value.strip())
            or any(unicodedata.category(c) in {"Cc", "Cf"} and c not in "\n\t" for c in value)):
        raise ValueError("AI 题目字段无效：" + field)
    return value.strip()


def question_response_schema():
    """The API response contract; no model-owned scheduler/support fields."""
    from .order_translation_policy import order_translation_schema
    text = lambda maximum: {"type": "string", "minLength": 1, "maxLength": maximum}
    properties = {"target_id": text(256), "revision": text(128),
        "kind": {"type": "string", "enum": list(AI_CHOICE_KINDS)},
        "prompt": text(1600), "choices": {"type": "array", "minItems": 2, "maxItems": 4,
                                            "items": text(240)},
        "answer": text(240), "explanation": text(1600),
        "order_translation": order_translation_schema()}
    return {"type": "object", "additionalProperties": False, "required": ["questions"],
            "properties": {"questions": {"type": "array", "maxItems": MAX_QUESTIONS,
                "items": {"type": "object", "additionalProperties": False,
                          "required": list(properties), "properties": properties}}}}


def _strength(unit, success_count):
    state = unit.get("scheduler_state") or {}
    stability = state.get("stability")
    stability = stability if type(stability) in {int, float} and math.isfinite(stability) and stability > 0 else 0
    return round(min(1, success_count / 4) * (0.4 + 0.6 * stability / (stability + 14)), 6)


def learning_scope_items(learning_store):
    """Hydrate only content that could qualify for independent practice.

    This is a fresh SQL projection, not a cache of eligibility or mastery.
    The full checks below remain authoritative. A large enrolled curriculum
    may contain thousands of untouched explanations and only a few assessed
    lessons; their rich examples need not be decoded for every tutor turn.
    """
    rows = learning_store.store.queries.assessed_course_candidates()
    return [learning_store.get_item(row[0]) for row in rows]


def build_learning_scope(learning_store, enabled_kinds, *, target_ids=None, limit=24, now=None,
                         supported_kinds=None, _items=None):
    """Export only selected semantic content and coarse capability labels.

    Targets have actually attempted independent assessment, including weak
    attempts. Background content needs current independent success. Enrollment and
    guided exposure alone qualify for neither. No exact dates/events/state are
    exported. ``limit=None`` is for local queue eligibility, not an API upload.
    """
    if limit is not None and (type(limit) is not int or not 1 <= limit <= 2000):
        raise ValueError("AI 范围数量不正确。")
    if target_ids is not None and (not isinstance(target_ids, (list, tuple, set))
            or any(not isinstance(x, str) for x in target_ids)):
        raise ValueError("AI 学习目标不正确。")
    supported = tuple(AI_CHOICE_KINDS if supported_kinds is None else supported_kinds)
    if any(kind not in KIND_ABILITIES for kind in supported):
        raise ValueError("不支持这种学习题型。")
    enabled = set(enabled_kinds) & set(supported)
    selected = None if target_ids is None else set(target_ids)
    now = now or app_clock.utc_now()
    if now.tzinfo is None:
        raise ValueError("AI 范围时钟须包含时区。")
    evidence = {row["unit_id"]: dict(row) for row in learning_store.store.queries.course_independent_evidence()}
    # Pure exposure and device failures are not evidence of forgetting. Only
    # the most recent scored answer can revoke a previously known background.
    latest = {row["unit_id"]: dict(row) for row in learning_store.store.queries.latest_course_outcomes()}
    targets, backgrounds = [], []
    for item in learning_scope_items(learning_store) if _items is None else _items:
        if item.get("domain") == "pronunciation" and supported_kinds is None:
            continue
        attempted, assessed, successful, strengths, dues = [], [], [], [], []
        for unit in item.get("units", []):
            if unit.get("suspended") or not unit.get("instruction_ready"):
                continue
            instruction = learning_store.ability_instruction_row(item['id'], unit['ability'])
            if instruction['stage'] in {'intro', 'guide'}:
                continue
            proof = evidence.get(unit["unit_id"], {})
            if not proof or not unit.get("review_count"):
                continue
            ability = unit["ability"]
            assessed.append(ability)
            if not proof.get("attempts"):
                continue
            attempted.append(ability)
            successes = proof.get("successes", 0)
            last = latest.get(unit["unit_id"], {})
            if (successes and last.get("outcome") == "correct" and not last.get("assisted")
                    and (last.get("rating") or 0) > 1):
                successful.append(ability)
            strengths.append(_strength(unit, successes))
            if unit.get("due_at_utc"):
                dues.append(datetime.fromisoformat(unit["due_at_utc"]))
        if not attempted:
            continue
        available = question_kinds(item)
        allowed = [k for k in supported if k in enabled and k in available
                   and KIND_ABILITIES[k] in attempted]
        instruction = item.get("instruction", {})
        entry = {"id": item["id"], "revision": str(item["revision"]), "family": item["family"],
            "title": str(item.get("title", item["id"]))[:240],
            "reading": str(item.get("reading", ""))[:240],
            "meaning": str(item.get("meaning", instruction.get("meaning", "")))[:1000],
            "rule": str(instruction.get("rule", ""))[:1600],
            "abilities": sorted(attempted), "allowed_kinds": allowed,
            "familiarity": round(sum(strengths) / len(strengths), 6)}
        if allowed and (selected is None or item["id"] in selected):
            targets.append((not any(d <= now for d in dues), entry["familiarity"], item["id"], entry))
        known = bool(successful) and set(assessed) <= set(successful)
        if item["family"] == "word":
            same_kana = (bool(re.fullmatch(r"[ぁ-ゖァ-ヶー]+", entry["title"]))
                         and _normal(entry["title"]) == _normal(entry["reading"]))
            known = "understanding" in successful and ("reading" in successful or same_kana)
        if known:
            backgrounds.append({**entry, "abilities": sorted(successful)})
    ordered = [row[-1] for row in sorted(targets, key=lambda row: row[:3])]
    backgrounds.sort(key=lambda row: (-row["familiarity"], row["id"]))
    return {"format": SCOPE_FORMAT, "version": 1,
            "targets": ordered if limit is None else ordered[:limit],
            "background": backgrounds if limit is None else backgrounds[:limit]}


def _scope_targets(scope):
    if (not isinstance(scope, dict) or scope.get("format") != SCOPE_FORMAT or scope.get("version") != 1
            or not isinstance(scope.get("targets"), list)):
        raise ValueError("AI 学习范围版本不正确。")
    return {row["id"]: row for row in scope["targets"]}


def question_is_available(question, scope, enabled_kinds):
    target = _scope_targets(scope).get(question.get("target_id"))
    kind = question.get("kind")
    return bool(target and kind in enabled_kinds and kind in AI_CHOICE_KINDS
                and kind in target.get("allowed_kinds", [])
                and target["revision"] == question.get("revision")
                and KIND_ABILITIES[kind] in target.get("abilities", []))


def _proposal(row):
    if (not isinstance(row, dict) or not PROPOSAL_FIELDS <= set(row)
            or set(row) - PROPOSAL_FIELDS - OPTIONAL_PROPOSAL_FIELDS):
        raise ValueError("AI 题目包含未知字段或缺少必要字段。")
    result = {key: _text(row[key], key, maximum) for key, maximum in (
        ("target_id", 256), ("revision", 128), ("kind", 64), ("prompt", 1600),
        ("answer", 240), ("explanation", 1600))}
    if result["kind"] not in AI_CHOICE_KINDS:
        raise ValueError("暂不支持这种 AI 题型。")
    choices = row["choices"]
    if not isinstance(choices, list) or not 2 <= len(choices) <= 4:
        raise ValueError("AI 选择题需要 2 至 4 个选项。")
    choices = [_text(x, "choices", 240) for x in choices]
    if len({_normal(x) for x in choices}) != len(choices) or result["answer"] not in choices:
        raise ValueError("AI 选项必须互不重复，且包含唯一正确答案。")
    result["choices"] = choices
    # No explanation/answer field is used as a visible question stimulus.
    if _normal(result["answer"]) in _normal(result["prompt"]):
        raise ValueError("AI 题干已经显示答案，请重新生成。")
    if result["kind"] in {"grammar_cloze", "vocab_usage"}:
        if result["prompt"].count(GAP) != 1:
            raise ValueError("AI 填空题必须保留一个明确的空缺。")
    elif _GAPS.search(result["prompt"]):
        raise ValueError("AI 题型与填空形式不匹配。")
    if "order_translation" in row:
        value = row["order_translation"]
        if value is not None:
            from .order_translations import validate_order_translation
            value = validate_order_translation(value, source_text=order_translation_source(result),
                                               source_translation="")
        result["order_translation"] = deepcopy(value)
    return result


def order_translation_source(question):
    """Local completed text, for post-answer references only, never a stimulus."""
    prompt = question["prompt"]
    if question["kind"] in {"grammar_cloze", "vocab_usage"}:
        return prompt.replace(GAP, question["answer"])
    return prompt


def question_fingerprint(question):
    """Same target/kind/public prompt is one exercise, even if regenerated."""
    key = (question["target_id"], question["revision"], question["kind"], _normal(question["prompt"]))
    return sha256(_encoded(key).encode()).hexdigest()


def _snapshot(proposal, family):
    fingerprint = question_fingerprint(proposal)
    identity = "ai-question:" + fingerprint[:32]
    kind, prompt = proposal["kind"], proposal["prompt"]
    # A local policy can allow only the already-visible meaning-choice text.
    safe_audio = kind in _MEANING and not _GAPS.search(prompt)
    choices = sorted(proposal["choices"])
    random.Random(fingerprint).shuffle(choices)
    support = {"reviewed": safe_audio, "content_revision": proposal["revision"], "prompt": prompt,
               "reading_safe": False, "accent_safe": False, "audio_safe": safe_audio,
               "has_gap": bool(_GAPS.search(prompt)), "reading_ambiguity": False, "segments": []}
    return {"id": identity, "question_id": identity, "fingerprint": fingerprint,
        "target_id": proposal["target_id"], "content_id": proposal["target_id"],
        "revision": proposal["revision"], "content_revision": proposal["revision"], "family": family,
        "kind": kind, "label": KIND_LABELS[kind], "ability": KIND_ABILITIES[kind],
        "prompt": prompt, "task": "选择最符合本题语境的答案。", "choices": choices, "options": choices,
        "answer": proposal["answer"], "accepted_answers": [proposal["answer"]],
        "explanation": proposal["explanation"], "feedback": proposal["explanation"],
        "input_mode": "choice", "response": "choice", "strict": True,
        "support": support, "audio_text": "", "optional_audio_text": prompt if safe_audio else "",
        "requires_audio": False, "guided": False, "evidence_mode": "exposure_only",
        "ai_generated": True, "semantic_review": "unverified", "scheduled": False,
        **{key: deepcopy(proposal[key]) for key in OPTIONAL_PROPOSAL_FIELDS if key in proposal}}


def validate_ai_question_snapshot(question):
    """Reject altered identities, metadata or unsupported saved payloads."""
    if not isinstance(question, dict) or question.get("family") not in {"word", "grammar"}:
        raise ValueError("AI 题目快照无效。")
    proposal = _proposal({**{key: question.get(key) for key in PROPOSAL_FIELDS},
                          **{key: question[key] for key in OPTIONAL_PROPOSAL_FIELDS if key in question}})
    expected_family = "grammar" if proposal["kind"].startswith("grammar_") else "word"
    if question["family"] != expected_family:
        raise ValueError("AI 题型与学习类别不匹配。")
    canonical = _snapshot(proposal, question["family"])
    if (canonical != question or any(type(question.get(key)) is not bool for key in
            ("strict", "requires_audio", "guided", "ai_generated", "scheduled"))):
        raise ValueError("AI 题目快照被修改或版本不受支持。")
    return deepcopy(canonical)


def validate_question_proposals(rows, scope, enabled_kinds, existing_questions=()):
    """All-or-nothing validation; identical existing exercises are omitted."""
    if not isinstance(rows, list) or len(rows) > MAX_QUESTIONS:
        raise ValueError("AI 题目数量超过本次允许范围。")
    targets = _scope_targets(scope)
    seen = {question_fingerprint(q) for q in existing_questions}
    result = []
    for raw in rows:
        proposal = _proposal(raw)
        if not question_is_available(proposal, scope, enabled_kinds):
            raise ValueError("题目目标、版本或题型已不在当前已学范围内。")
        target = targets[proposal["target_id"]]
        expected_family = "grammar" if proposal["kind"].startswith("grammar_") else "word"
        if target["family"] != expected_family:
            raise ValueError("AI 题型与学习目标不匹配。")
        snapshot = _snapshot(proposal, target["family"])
        if snapshot["fingerprint"] not in seen:
            result.append(snapshot)
            seen.add(snapshot["fingerprint"])
    return result
