"""Reviewable AI material proposals: append resources, never learner state.

Model output is inert data. Retention and dismissal are explicit, transactional
choices. A retained lesson still needs ten linked examples and ordinary first
learning before any independent review can occur.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from . import app_clock
import hashlib
import json

from .example_store import (SETTING as EXAMPLE_SETTING, empty_pack, example_id_for,
                            reference_id_for, validate_pack)
from .learning_dependencies import validate_catalog
from .learning_questions import (KIND_ABILITIES, WORD_KINDS, GRAMMAR_KINDS,
                                 PRONUNCIATION_KINDS, build_learning_question,
                                 grade_learning_answer, learning_order_translation_source, _normal)
from .material_review import without_order_translations

SETTING = "ai_materials_v1"
FORMAT = "kanamemory-ai-materials"
VERSION = 1
MIN_EXAMPLES = 10
MAX_STATE_BYTES = 16 * 1024 * 1024
MAX_PROPOSALS = 1000
MAX_ITEMS_PER_PROPOSAL = 20
MAX_EXAMPLES_PER_PROPOSAL = 200
MAX_SOURCE_CHARS = 20000
MODES = {"examples", "text"}
DECISIONS = {"pending", "kept", "discarded"}
ITEM_FIELDS = {"id", "content_id", "family", "domain", "title", "reading", "meaning",
               "instruction", "question_templates", "depends_on", "revision", "abilities",
               "source_ids", "accent", "material_status", "instruction_status"}
INSTRUCTION_FIELDS = {"summary", "rule", "steps", "example", "translation", "prerequisite", "contrast",
                      "order_translation"}
TEMPLATE_FIELDS = {"prompt", "task", "answer", "feedback", "choices", "input_mode",
                   "accepted_answers", "strict", "known_wrong", "text_roles", "order_translation"}


def _json(value):
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                             separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_STATE_BYTES:
            raise ValueError("AI 材料超过本机保存上限，请先导出并整理旧提案。")
        return encoded
    except (TypeError, UnicodeError, RecursionError) as exc:
        raise ValueError("AI 材料必须是有效的 JSON 数据。") from exc


def _text(value, name, maximum=2000, *, blank=False):
    if (not isinstance(value, str) or len(value) > maximum or (not blank and not value.strip())
            or any(ord(c) < 32 and c not in "\n\r\t" for c in value)
            or any(0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise ValueError("AI 材料字段无效：" + name)
    return value


def _strings(value, name, maximum=100, *, blank=False):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError("AI 材料列表无效：" + name)
    for part in value:
        _text(part, name, blank=blank)
    return value


def empty_state():
    return {"format": FORMAT, "version": VERSION, "items": [], "proposals": []}


def sanitize_anchor_positions(example_pack):
    """Normalize model offsets only when the exact quote has one location.

    This pure, opt-in provider boundary returns a detached pack and auditable
    corrections. It is deliberately not called by stage or backup/import
    validators: authored and saved anchors remain strict. No text, target,
    revision, quote or explanation is rewritten, and ambiguous occurrences
    (including overlapping matches) are never guessed.
    """
    _json(example_pack)
    if not isinstance(example_pack, dict):
        raise ValueError("AI 例句必须使用版本化例句包。")
    pack = deepcopy(example_pack)
    if (not isinstance(pack.get("examples"), list)
            or len(pack["examples"]) > MAX_EXAMPLES_PER_PROPOSAL
            or not isinstance(pack.get("references"), list)):
        raise ValueError("AI 例句或关联列表无效。")
    examples = {}
    for row in pack["examples"]:
        if not isinstance(row, dict):
            raise ValueError("AI 例句记录无效。")
        eid = _text(row.get("id"), "example.id", 256)
        if eid in examples:
            raise ValueError("AI 例句标识重复。")
        examples[eid] = _text(row.get("text"), "example.text", 4000)
    corrections = []
    for ref_index, reference in enumerate(pack["references"]):
        if not isinstance(reference, dict):
            raise ValueError("AI 例句关联无效。")
        eid = _text(reference.get("example_id"), "reference.example_id", 256)
        rid = _text(reference.get("id"), "reference.id", 256)
        if eid not in examples:
            raise ValueError("关联的例句不存在。")
        spans = reference.get("spans")
        if not isinstance(spans, list) or not 1 <= len(spans) <= 100:
            raise ValueError("例句关联需要明确片段。")
        text = examples[eid]
        for span_index, span in enumerate(spans):
            if not isinstance(span, dict):
                raise ValueError("强调片段格式不正确。")
            start, end = span.get("start"), span.get("end")
            quote = _text(span.get("quote"), "span.quote", 4000)
            if type(start) is not int or type(end) is not int:
                raise ValueError("强调片段位置必须是整数。")
            if 0 <= start < end <= len(text) and text[start:end] == quote:
                continue
            position = text.find(quote)
            if position < 0:
                raise ValueError("强调片段原文不存在，不能校正位置。")
            if text.find(quote, position + 1) >= 0:
                raise ValueError("强调片段有多处匹配，不能猜测位置。")
            original = deepcopy(span)
            span.update(start=position, end=position + len(quote))
            corrections.append({"reference_index": ref_index, "span_index": span_index,
                "example_id": eid, "reference_id": rid, "text": text,
                "original": original, "normalized": deepcopy(span), "rule": "unique_exact_quote"})
    # Re-run the unchanged strict validator after the deterministic correction.
    # Other malformed metadata, references and versions still fail normally.
    return validate_pack(pack), corrections


def learning_item_id_for(item):
    identity = {key: item.get(key, "") for key in ("family", "domain", "title", "reading", "meaning")}
    return "local:ai:" + hashlib.sha256(_json(identity).encode()).hexdigest()[:32]


def _normalize_item(source, *, stored=False):
    if not isinstance(source, dict) or set(source) - ITEM_FIELDS:
        raise ValueError("新学习项包含未支持字段；不能设置学习进度或复习安排。")
    item = deepcopy(source)
    original_family = item.get("family")
    if original_family == "vocabulary":
        item["family"] = "word"
    elif original_family == "reading":
        item.update(family="grammar", domain="pronunciation")
    family = item.get("family")
    if (not isinstance(family, str) or family not in {"word", "grammar"}
            or not isinstance(item.get("domain", ""), str)
            or item.get("domain", "") not in {"", "pronunciation"}):
        raise ValueError("只支持词汇、语法或读音规则学习项。")
    if item.get("domain") == "pronunciation" and family != "grammar":
        raise ValueError("读音规则应使用语法能力结构。")
    for field in ("title", "meaning"):
        _text(item.get(field), field, 1000)
    _text(item.get("reading", ""), "reading", 1000, blank=family != "word")
    item.setdefault("reading", "")
    item.setdefault("domain", "")
    if item.get("accent") is not None:
        raise ValueError("AI 新材料不能把未经核对的调号当作词典证据。")
    instruction = item.get("instruction")
    if not isinstance(instruction, dict) or set(instruction) - INSTRUCTION_FIELDS:
        raise ValueError("新学习项需要结构化的首次学习材料。")
    for field in ("summary", "rule", "example", "translation"):
        _text(instruction.get(field), "instruction." + field, 4000)
    if instruction.get("order_translation") is not None:
        from .order_translations import validate_order_translation
        instruction["order_translation"] = validate_order_translation(
            instruction["order_translation"], source_text=instruction["example"],
            source_translation=instruction["translation"])
    steps = _strings(instruction.get("steps"), "instruction.steps", 12)
    if not steps:
        raise ValueError("首次学习需要至少一个讲解步骤。")
    for field in ("prerequisite", "contrast"):
        _text(instruction.get(field, ""), "instruction." + field, blank=True)
        instruction.setdefault(field, "")
    dependencies = _strings(item.get("depends_on", []), "depends_on", 30)
    if len(set(dependencies)) != len(dependencies):
        raise ValueError("学习项前置关系不能重复。")
    item["depends_on"] = list(dependencies)
    allowed = (PRONUNCIATION_KINDS if item["domain"] == "pronunciation" else
               WORD_KINDS if family == "word" else GRAMMAR_KINDS)
    templates = item.get("question_templates")
    if (not isinstance(templates, dict) or not templates
            or set(templates) - set((*WORD_KINDS, *GRAMMAR_KINDS, *PRONUNCIATION_KINDS))
            or any(kind not in allowed and variants != [] for kind, variants in templates.items())):
        raise ValueError("新学习项必须提供明确的配套题目。")
    active = {}
    for kind in allowed:
        if kind not in templates:
            continue
        variants = templates[kind]
        if not isinstance(variants, list) or len(variants) > 12:
            raise ValueError("配套题目数量无效。")
        if not variants:
            continue
        for q in variants:
            if not isinstance(q, dict) or set(q) - TEMPLATE_FIELDS:
                raise ValueError("配套题目包含未支持字段。")
            for field in ("prompt", "answer", "feedback"):
                _text(q.get(field), "question." + field, 4000)
            _text(q.get("task", ""), "question.task", 2000, blank=True)
            mode = q.get("input_mode", "choice")
            if not isinstance(mode, str) or mode not in {"choice", "text", "reorder"}:
                raise ValueError("配套题目作答方式无效。")
            choices = _strings(q.get("choices", []), "question.choices", 30)
            if mode == "choice" and (len(choices) < 2 or len(set(choices)) != len(choices)
                                     or q["answer"] not in choices):
                raise ValueError("选择题必须有互不重复的选项和唯一已列出的正确答案。")
            if mode == "text" and choices:
                raise ValueError("输入题不应带选择项。")
            if mode == "reorder" and (not choices or "".join(choices) != q["answer"]):
                raise ValueError("重组题的词块必须能按原顺序组成示范答案。")
            for field in ("accepted_answers", "known_wrong"):
                if field in q:
                    _strings(q[field], "question." + field, 30)
            if "strict" in q and type(q["strict"]) is not bool:
                raise ValueError("题目严格判断标记必须是布尔值。")
            if mode == "choice":
                if q.get("strict", True) is not True:
                    raise ValueError("选择题必须按明确的唯一答案判断。")
                if any(_normal(answer) != _normal(q["answer"])
                       for answer in q.get("accepted_answers", [])):
                    raise ValueError("选择题不能隐藏额外的正确答案。")
            if item["domain"] == "pronunciation":
                # Locally assigned presentation metadata cannot enable audio,
                # ruby or any other answer-revealing support channel.
                role = "ui" if kind == "pronunciation_rule" else "study"
                roles = q.setdefault("text_roles", {"prompt": role, "answer": role})
                if (not isinstance(roles, dict) or set(roles) != {"prompt", "answer"}
                        or any(not isinstance(value, str) or value not in {"ui", "study"}
                               for value in roles.values())):
                    raise ValueError("读音题只允许明确的界面或日文文字样式。")
            elif "text_roles" in q:
                raise ValueError("此类 AI 题目不支持额外文字样式字段。")
            if q.get("order_translation") is not None:
                from .order_translations import validate_order_translation
                source = learning_order_translation_source({**q, "kind": kind})
                if source is None:
                    raise ValueError("配套题目无法确定完整日文原句，请将实验译文留空。")
                q["order_translation"] = validate_order_translation(
                    q["order_translation"], source_text=source, source_translation="")
        active[kind] = variants
    abilities = list(dict.fromkeys(KIND_ABILITIES[kind] for kind in active))
    required = {"understanding", "application"} if family == "grammar" else {"understanding"}
    if not required <= set(abilities):
        raise ValueError("新规则需要理解与应用题，新词至少需要词义理解题。")
    if stored and item.get("abilities") != abilities:
        raise ValueError("保存的学习能力与配套题型不匹配。")
    item["question_templates"] = active
    item["abilities"] = abilities
    identity = learning_item_id_for(item)
    if stored or str(item.get("id", "")).startswith("local:ai:"):
        if item.get("id") != identity:
            raise ValueError("新学习项的稳定标识与内容不一致。")
    item.update(id=identity, content_id=identity, accent=None, source_ids=["ai-user-reviewed"],
                material_status="ai_user_retained", instruction_status="ready")
    revision_material = without_order_translations(
        {k: v for k, v in item.items() if k not in {"revision", "id", "content_id"}})
    revision = "ai-material-v1-" + hashlib.sha256(_json(revision_material).encode()).hexdigest()[:20]
    if stored and item.get("revision") != revision:
        raise ValueError("保存的学习项内容版本不匹配。")
    item["revision"] = revision
    # These builders also validate template/family behavior used by the actual
    # teaching screens. No fallback grammar/word generator is used here.
    for kind, variants in active.items():
        for index, template in enumerate(variants):
            question = build_learning_question(item, kind, variant=index, apply_review=False)
            if template.get("input_mode", "choice") == "choice":
                for choice in template["choices"]:
                    result = grade_learning_answer(question, choice)
                    if (result.get("correct") is True) != (choice == template["answer"]):
                        raise ValueError("配套选择题的选项不能有重复正确答案或无法评分。")
            if any(grade_learning_answer(question, value).get("correct") is True
                   for value in template.get("known_wrong", [])):
                raise ValueError("已知错误答案不能与正确或可接受答案重叠。")
    return item


def _validate_segments(source, segments, pack, mode):
    _text(source, "source_text", MAX_SOURCE_CHARS, blank=mode == "examples")
    if not isinstance(segments, list) or len(segments) > 200:
        raise ValueError("分句结果数量无效。")
    examples = {e["id"]: e for e in pack["examples"]}
    end = 0
    for segment in segments:
        if not isinstance(segment, dict) or set(segment) - {"start", "end", "quote", "example_id"}:
            raise ValueError("分句只允许原文位置、原文和例句关联。")
        start, stop, quote = segment.get("start"), segment.get("end"), segment.get("quote")
        if (type(start) is not int or type(stop) is not int or not end <= start < stop <= len(source)
                or source[start:stop] != quote or source[end:start].strip()):
            raise ValueError("分句与原文不一致、有遗漏或重叠。")
        _text(quote, "segment.quote", 4000)
        eid = segment.get("example_id", "")
        if not isinstance(eid, str) or (eid and (eid not in examples or examples[eid]["text"] != quote)):
            raise ValueError("分句必须关联到内容完全相同的例句。")
        end = stop
    if mode == "text" and (not segments or source[end:].strip()):
        raise ValueError("分句未完整覆盖输入的原文。")
    if mode == "examples" and segments:
        raise ValueError("生成例句任务不能提交原文分割结果。")
    return deepcopy(segments)


def _status(decisions):
    values = [value for group in decisions.values() for value in group.values()]
    if all(v == "pending" for v in values):
        return "draft"
    if "pending" in values:
        return "partial"
    if len(set(values)) == 1:
        return values[0]
    return "resolved"


def _validate_restored_dependencies(items):
    """Check internal cycles while allowing unavailable old external lessons."""
    catalogue = {item["id"]: item for item in items}
    for item in list(catalogue.values()):
        for dependency in item["depends_on"]:
            catalogue.setdefault(dependency, {"id": dependency, "depends_on": []})
    validate_catalog(catalogue)


def _validate_lesson_dependencies(items, catalogue):
    # A read-only kana or dictionary reference is not a course prerequisite.
    # Ordinary lessons with that identity replace the read-only registry entry.
    for item in items:
        if any(catalogue.get(cid, {}).get("reference_only") for cid in item["depends_on"]):
            raise ValueError("只读假名或词典关联不能直接作为新课程的前置学习项。")


def _assert_same_example_display(existing, candidate):
    if existing is not None and any(existing.get(key, "") != candidate.get(key, "")
                                    for key in ("translation", "reading")):
        text = candidate["text"]
        caption = text if len(text) <= 30 else text[:30] + "…"
        raise ValueError("已有例句「" + caption + "」的译文或读法与候选不同，请分别审核语境，不能自动合并。")


def validate_ai_material_state(value):
    """Validate an optional backup setting without requiring today's catalogue.

    External legacy reference IDs stay preserved if a future bundled catalogue
    no longer supplies them. New proposals/activation use current known IDs.
    """
    _json(value)
    value = deepcopy(value)
    if (not isinstance(value, dict) or set(value) != {"format", "version", "items", "proposals"}
            or value.get("format") != FORMAT or type(value.get("version")) is not int
            or value["version"] != VERSION):
        raise ValueError("不支持此 AI 材料保存格式，原材料未修改。")
    if not isinstance(value["items"], list) or len(value["items"]) > 5000:
        raise ValueError("保留的 AI 学习项列表无效。")
    retained = {}
    for source in value["items"]:
        item = _normalize_item(source, stored=True)
        if item["id"] in retained:
            raise ValueError("保留的 AI 学习项标识重复。")
        retained[item["id"]] = item
    proposals = value["proposals"]
    if not isinstance(proposals, list) or len(proposals) > MAX_PROPOSALS:
        raise ValueError("AI 提案列表无效。")
    seen, requests = set(), set()
    fields = {"id", "status", "mode", "source_text", "items", "example_pack", "segments",
              "request_id", "created_at_utc", "decisions"}
    for proposal in proposals:
        if not isinstance(proposal, dict) or set(proposal) != fields:
            raise ValueError("AI 提案字段不完整。")
        for field in ("id", "request_id"):
            _text(proposal.get(field), field, 256)
        if proposal["id"] in seen or proposal["request_id"] in requests:
            raise ValueError("AI 提案或请求标识重复。")
        if proposal["id"] != "ai-proposal:" + hashlib.sha256(proposal["request_id"].encode()).hexdigest()[:32]:
            raise ValueError("AI 提案身份不一致。")
        seen.add(proposal["id"]); requests.add(proposal["request_id"])
        try:
            if datetime.fromisoformat(proposal["created_at_utc"]).utcoffset() is None:
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("AI 提案时间无效。") from None
        if not isinstance(proposal["mode"], str) or proposal["mode"] not in MODES:
            raise ValueError("AI 材料任务类型无效。")
        if not isinstance(proposal["items"], list) or len(proposal["items"]) > MAX_ITEMS_PER_PROPOSAL:
            raise ValueError("新学习项数量无效。")
        items = [_normalize_item(item, stored=True) for item in proposal["items"]]
        ids = {item["id"] for item in items}
        if len(ids) != len(items):
            raise ValueError("同一提案包含重复学习项。")
        _validate_restored_dependencies([*retained.values(), *items])
        pack = validate_pack(proposal["example_pack"])
        if pack["status"] != "draft" or len(pack["examples"]) > MAX_EXAMPLES_PER_PROPOSAL:
            raise ValueError("提案例句应保持草稿状态且数量有限。")
        _validate_segments(proposal["source_text"], proposal["segments"], pack, proposal["mode"])
        decisions = proposal["decisions"]
        if not isinstance(decisions, dict) or set(decisions) not in ({"items", "examples"}, {"items", "examples", "references"}):
            raise ValueError("提案保留决定无效。")
        if "references" not in decisions:
            # Old decisions activated/discarded every reference with its body.
            # Project that fact; never reset an old kept link to pending.
            if not isinstance(decisions.get("examples"), dict):
                raise ValueError("提案正文决定无效。")
            decisions["references"] = {ref["id"]: decisions["examples"].get(ref["example_id"], "pending") for ref in pack["references"]}
        for group, expected in (("items", ids), ("examples", {e["id"] for e in pack["examples"]}),
                                ("references", {r["id"] for r in pack["references"]})):
            if (not isinstance(decisions[group], dict) or set(decisions[group]) != expected
                    or any(not isinstance(v, str) or v not in DECISIONS for v in decisions[group].values())):
                raise ValueError("提案保留决定与材料不匹配。")
        if not ids and not pack["examples"]:
            raise ValueError("AI 提案没有可选择的材料。")
        if proposal["status"] != _status(decisions):
            raise ValueError("提案状态与保留决定不一致。")
        for item in items:
            if decisions["items"][item["id"]] == "kept" and retained.get(item["id"]) != item:
                raise ValueError("已保留的新学习项与资源快照不一致。")
        for ref in pack["references"]:
            if decisions["references"][ref["id"]] == "kept" and decisions["examples"][ref["example_id"]] != "kept":
                raise ValueError("保留关联前需要保留例句正文。")
    # Validate cycles among retained new items; unknown external IDs are old
    # unavailable references, represented by inert leaves during restoration.
    _validate_restored_dependencies(retained.values())
    return deepcopy(value)


class AIMaterialStore:
    def __init__(self, store, known_items=(), example_store=None):
        self.store, self.example_store = store, example_store
        values = known_items.values() if isinstance(known_items, dict) else known_items
        self.known = {item["id"]: deepcopy(item) for item in values}

    def _state(self):
        return validate_ai_material_state(self.store.get_setting(SETTING, empty_state()))

    def retained_items(self):
        state = self._state()
        from .material_usage import read, allowed, active_pack, flags
        usage = read(self.store)
        permissions = {p['id']: flags(usage, p) for p in state['proposals']}
        owned, active = set(), set()
        for proposal in state['proposals']:
            for cid, decision in proposal['decisions']['items'].items():
                if decision == 'kept':
                    owned.add(cid)
                    if allowed(usage, proposal, 'items', cid, _flags=permissions[proposal['id']]): active.add(cid)
        items = [item for item in state['items'] if item['id'] not in owned or item['id'] in active]
        pack = validate_pack(self.store.get_setting(EXAMPLE_SETTING, empty_pack()))
        pack = active_pack(self.store, pack, materials=state, state=usage)
        texts = {example["id"]: example["text"] for example in pack["examples"]}
        counts = {}
        bodies = {}
        for ref in pack["references"]:
            # Imported packs may use several valid IDs for the same sentence.
            # They remain immutable resources, but cannot inflate readiness.
            counts.setdefault(ref["target_id"], set()).add(texts[ref["example_id"]])
        for proposal in state["proposals"]:
            kept = {row["id"]: row["text"] for row in proposal["example_pack"]["examples"]
                    if proposal["decisions"]["examples"][row["id"]] == "kept" and allowed(usage, proposal, 'examples', row['id'], _flags=permissions[proposal['id']])}
            for ref in proposal["example_pack"]["references"]:
                if (ref["example_id"] in kept and proposal["decisions"]["references"][ref["id"]] != "discarded"
                        and allowed(usage, proposal, 'references', ref['id'], _flags=permissions[proposal['id']])):
                    bodies.setdefault(ref["target_id"], set()).add(kept[ref["example_id"]])
        for item in items:
            count = len(counts.get(item["id"], ()))
            item.update(instruction_status="ready" if count >= MIN_EXAMPLES else "instruction_missing",
                        instruction_missing=count < MIN_EXAMPLES,
                        retained_example_count=count, minimum_examples=MIN_EXAMPLES,
                        retained_body_example_count=len(bodies.get(item["id"], set()) | counts.get(item["id"], set())),
                        pending_example_count=len(bodies.get(item["id"], set()) - counts.get(item["id"], set())))
        return items

    def ready_for_learning(self, item_id):
        item = next((item for item in self.retained_items() if item["id"] == item_id), None)
        if item is None:
            raise ValueError("找不到保留的新学习项。")
        from .assessment_policy import teaching_allowed
        structure_ready = not item["instruction_missing"]
        from .material_reference import admission_readiness
        verified = admission_readiness(self.store, item)
        return {"ready": structure_ready and teaching_allowed(item, store=self.store) and verified['ready'], "example_count": verified['example_count'],
                "minimum_examples": MIN_EXAMPLES,
                "missing": (["至少需要十个已保留的关联例句。"] if not structure_ready else
                            [] if verified['ready'] else ["教案、题目或十条关联例句仍待核实。"])}

    def get(self, proposal_id):
        return next((p for p in self._state()["proposals"] if p["id"] == proposal_id), None)

    def list_proposals(self, status=None):
        rows = self._state()["proposals"]
        if status == "pending":
            rows = [p for p in rows if p["status"] in {"draft", "partial"}]
        elif status is not None:
            rows = [p for p in rows if p["status"] == status]
        return list(reversed(rows))

    def stage(self, mode, source_text, items, example_pack, segments, request_id):
        return self._stage(mode, source_text, items, example_pack, segments, request_id)

    def _stage_in_transaction(self, mode, source_text, items, example_pack, segments, request_id):
        if not self.store._db.in_transaction:
            raise RuntimeError("Material workflow requires an active transaction")
        return self._stage(mode, source_text, items, example_pack, segments, request_id, _in_transaction=True)

    def _stage_revision_in_transaction(self, parent, items, pack, segments, request_id):
        if not self.store._db.in_transaction or self.get(parent["id"]) != parent:
            raise ValueError("修订必须基于当前原稿并在同一事务中保存。")
        return self._stage(parent["mode"], parent["source_text"], items, pack, segments, request_id,
                           _in_transaction=True, _revision_parent=parent)

    def _stage(self, mode, source_text, items, example_pack, segments, request_id, *, _in_transaction=False, _revision_parent=None):
        _text(request_id, "request_id", 256)
        if (not isinstance(mode, str) or mode not in MODES
                or not isinstance(items, list) or len(items) > MAX_ITEMS_PER_PROPOSAL):
            raise ValueError("AI 材料任务或学习项数量无效。")
        if not isinstance(segments, list):
            raise ValueError("分句结果必须是列表。")
        state = self._state()
        previous = next((p for p in state["proposals"] if p["request_id"] == request_id), None)
        aliases, normalized = {}, []
        for source in items:
            alias = _text(source.get("id") if isinstance(source, dict) else None, "item.id", 256)
            if alias in aliases:
                raise ValueError("新学习项的临时标识重复。")
            # Identity-changing edits retain an explicit old-to-new lineage;
            # neither stable learner IDs nor retained snapshots are overwritten.
            item = _normalize_item({**source, "id": "revision-item:" + str(len(normalized))} if _revision_parent else source)
            aliases[alias] = item["id"]
            normalized.append(item)
        # Dependencies participate in the revision, after temporary aliases
        # have been mapped to their stable identities.
        for index, item in enumerate(normalized):
            item["depends_on"] = [aliases.get(cid, cid) for cid in item["depends_on"]]
            item.pop("revision", None)
            normalized[index] = _normalize_item(item)
        if len({item["id"] for item in normalized}) != len(normalized):
            raise ValueError("提案重复描述了同一个学习项，请合并。")
        retained = {item["id"]: item for item in state["items"]}
        known = {**self.known, **retained}
        for item in normalized:
            existing = known.get(item["id"])
            if (_revision_parent is None and existing is not None and _normalize_item({k: v for k, v in existing.items()
                                                        if k in ITEM_FIELDS}, stored=True) != item):
                raise ValueError("已有学习项内容不能被 AI 提案覆盖。")
        combined = {**known, **{item["id"]: item for item in normalized}}
        _validate_lesson_dependencies(normalized, combined)
        validate_catalog(combined)
        try:
            pack, example_aliases = self._normalize_examples(example_pack, aliases, combined,
                existing_examples=(previous or _revision_parent)["example_pack"]["examples"] if previous or _revision_parent else None,
                pending_revision=_revision_parent is not None)
        except ValueError:
            if previous is not None:
                raise ValueError("同一请求已保存不同材料，不能覆盖原提案。") from None
            raise
        segments = deepcopy(segments)
        # Example aliases are mapped by their exact original text/identity.
        for segment in segments:
            if (isinstance(segment, dict) and isinstance(segment.get("example_id"), str)
                    and segment["example_id"] in example_aliases):
                segment["example_id"] = example_aliases[segment["example_id"]]
        _validate_segments(source_text, segments, pack, mode)
        proposal = {"id": "ai-proposal:" + hashlib.sha256(request_id.encode()).hexdigest()[:32],
                    "request_id": request_id, "mode": mode, "source_text": source_text,
                    "items": normalized, "example_pack": pack, "segments": segments,
                    "created_at_utc": app_clock.utc_now().isoformat(),
                    "decisions": {"items": {i["id"]: "pending" for i in normalized},
                                  "examples": {e["id"]: "pending" for e in pack["examples"]},
                                  "references": {r["id"]: "pending" for r in pack["references"]}}}
        proposal["status"] = _status(proposal["decisions"])
        if previous is not None:
            fields = ("mode", "source_text", "items", "example_pack", "segments")
            if any(previous[k] != proposal[k] for k in fields):
                raise ValueError("同一请求已保存不同材料，不能覆盖原提案。")
            return previous
        state["proposals"].append(proposal)
        validate_ai_material_state(state)
        self._write(state, _in_transaction=_in_transaction)
        return deepcopy(proposal)

    def _normalize_examples(self, source, aliases, known, *, existing_examples=None, pending_revision=False):
        if not isinstance(source, dict):
            raise ValueError("AI 例句必须使用版本化例句包。")
        pack = deepcopy(source)
        if pack.get("format") != empty_pack()["format"] or pack.get("version") != 1:
            raise ValueError("AI 例句格式版本无效。")
        pack["status"] = "draft"
        if not isinstance(pack.get("examples"), list) or len(pack["examples"]) > MAX_EXAMPLES_PER_PROPOSAL:
            raise ValueError("AI 例句数量无效。")
        if not isinstance(pack.get("references"), list):
            raise ValueError("AI 例句关联必须是列表。")
        example_aliases = {}
        text_known = {}
        frozen = None if existing_examples is None else {e["id"]: e for e in existing_examples}
        if frozen is not None:
            text_known = {e["text"]: e for e in frozen.values()}
        elif self.example_store is not None:
            self.example_store.refresh()
            text_known = {e['text']: e for e in [*self.example_store.examples.values(), *self.example_store.custom['examples']]}
        unique_examples = {}
        for example in pack["examples"]:
            if not isinstance(example, dict):
                raise ValueError("AI 例句记录无效。")
            alias = _text(example.get("id"), "example.id", 256)
            if alias in example_aliases:
                raise ValueError("AI 例句临时标识重复。")
            text = _text(example.get("text"), "example.text", 4000)
            existing = (frozen.get(alias) if frozen is not None else
                        self.example_store.get_raw(alias) if self.example_store is not None else None)
            old = existing
            if pending_revision and existing is not None and existing["text"] != text:
                existing = None
            if existing is not None and existing["text"] != text:
                raise ValueError("已有例句标识的原文不能被改写。")
            existing = existing or text_known.get(text)
            if existing is None and frozen is None and self.example_store is not None:
                existing = self.example_store.get_raw(example_id_for(text))
            if not pending_revision:
                _assert_same_example_display(existing, example)
            new_id = existing["id"] if existing else example_id_for(text)
            example_aliases[alias] = new_id
            revision = existing["revision"] if existing else "1"
            display = ("text", "translation", "reading", "order_translation")
            if pending_revision and old and any(old.get(k) != example.get(k) for k in display):
                revision = "material-revision-" + hashlib.sha256(_json({k: example.get(k) for k in display}).encode()).hexdigest()[:20]
            example.update(id=new_id, revision=revision,
                           source_ids=["ai-user-reviewed"])
            previous = unique_examples.get(new_id)
            if previous is not None and previous != example:
                raise ValueError("同一原句存在不同译文、读法或语境资料，请分开审核，不能自动合并。")
            unique_examples[new_id] = example
        pack["examples"] = list(unique_examples.values())
        unique_references = {}
        for ref in pack.get("references", []):
            if not isinstance(ref, dict):
                raise ValueError("AI 例句关联无效。")
            _text(ref.get("example_id"), "reference.example_id", 256)
            _text(ref.get("target_id"), "reference.target_id", 1024)
            ref["example_id"] = example_aliases.get(ref.get("example_id"), ref.get("example_id"))
            ex = next((e for e in pack["examples"] if e["id"] == ref["example_id"]), None)
            if ex is not None:
                ref["example_revision"] = ex["revision"]
            ref["target_id"] = aliases.get(ref.get("target_id"), ref.get("target_id"))
            ref.pop("id", None)
            ref["id"] = reference_id_for(ref)
            previous = unique_references.get(ref["id"])
            if previous is not None and previous != ref:
                raise ValueError("例句关联标识冲突，不能合并。")
            unique_references[ref["id"]] = ref
        pack["references"] = list(unique_references.values())
        return validate_pack(pack, known), example_aliases

    def _selected(self, proposal, item_ids, example_ids, reference_ids):
        if item_ids is None and example_ids is None and reference_ids is None:
            return {group: [key for key, decision in values.items() if decision == "pending"]
                    for group, values in proposal["decisions"].items()}
        result = {"items": [] if item_ids is None else item_ids,
                  "examples": [] if example_ids is None else example_ids,
                  "references": [] if reference_ids is None else reference_ids}
        for group, ids in result.items():
            if (not isinstance(ids, list)
                    or any(not isinstance(cid, str) or cid not in proposal["decisions"][group] for cid in ids)
                    or len(set(ids)) != len(ids)):
                raise ValueError("所选材料不属于此提案。")
        return result

    def keep(self, proposal_id, item_ids=None, example_ids=None, reference_ids=None):
        return self._decide(proposal_id, item_ids, example_ids, reference_ids, "kept")

    def discard(self, proposal_id, item_ids=None, example_ids=None, reference_ids=None):
        return self._decide(proposal_id, item_ids, example_ids, reference_ids, "discarded")

    def _decide(self, proposal_id, item_ids, example_ids, reference_ids, decision):
        db = self.store._db
        if db.in_transaction:
            raise RuntimeError("Material retention owns its transaction")
        with db:
            db.execute("BEGIN IMMEDIATE")
            result = self._decide_in_transaction(proposal_id, item_ids, example_ids, reference_ids, decision)
        if self.example_store is not None:
            self.example_store.refresh()
        return result

    def eligible_reference_ids(self, proposal):
        from .material_usage import read, allowed
        usage = read(self.store)
        if not allowed(usage, proposal): return set()
        """Retention is a user choice; activation needs current field evidence."""
        from .material_workflow import MaterialWorkflowStore
        from .material_verification import MaterialVerificationStore
        if self.example_store is None:
            return set()
        job = MaterialWorkflowStore(self.store).for_request(proposal["request_id"])
        if not job:
            return set()
        report = MaterialVerificationStore(self.store).current(job["id"], proposal, examples=self.example_store)
        if not report:
            return set()
        supported = {}
        for row in report["claims"]:
            if row["status"] == "supported":
                supported.setdefault((row["object_kind"], row["object_id"]), set()).add(row["facet"])
        examples = {row["id"]: row for row in proposal["example_pack"]["examples"]}
        eligible = set()
        for ref in proposal["example_pack"]["references"]:
            example = examples[ref["example_id"]]
            required = {"text", "translation"} | {key for key in ("reading", "order_translation") if example.get(key)}
            ref_fields = {key for key in ("target_id", "spans", "hint", "explanation") if key in ref}
            if (required <= supported.get(("example", example["id"]), set())
                    and ref_fields <= supported.get(("reference", ref["id"]), set())):
                if allowed(usage, proposal, 'references', ref['id']): eligible.add(ref["id"])
        return eligible

    def _decide_in_transaction(self, proposal_id, item_ids, example_ids, reference_ids, decision):
        state = self._state()
        proposal = next((p for p in state["proposals"] if p["id"] == proposal_id), None)
        if proposal is None:
            raise ValueError("找不到 AI 材料提案。")
        selected = self._selected(proposal, item_ids, example_ids, reference_ids)
        if decision == "discarded":
            # References to a discarded body cannot remain actionable or become
            # orphan links. Already-kept bodies still fail the opposite-decision gate.
            related = [r["id"] for r in proposal["example_pack"]["references"] if r["example_id"] in selected["examples"]
                       and proposal["decisions"]["references"][r["id"]] == "pending"]
            selected["references"] = list(dict.fromkeys([*selected["references"], *related]))
        for group, ids in selected.items():
            if any(proposal["decisions"][group][cid] not in {"pending", decision} for cid in ids):
                raise ValueError("此材料已有相反的保留决定；原决定不会被覆盖。")
        selected = {group: [cid for cid in ids if proposal["decisions"][group][cid] == "pending"]
                    for group, ids in selected.items()}
        if not any(selected.values()):
            # Repeating an old confirmed decision must stay a no-op even if
            # today's display catalogue or import checks have since changed.
            return deepcopy(proposal)
        retained = {item["id"]: item for item in state["items"]}
        prepared = None
        if decision == "kept":
            from .material_usage import read, allowed
            usage = read(self.store)
            if any(not allowed(usage, proposal, group, cid) for group, ids in selected.items() for cid in ids):
                raise ValueError('该材料已停止使用；请先在使用设置中明确恢复。')
            chosen = {item["id"]: item for item in proposal["items"] if item["id"] in selected["items"]}
            for cid, item in chosen.items():
                if cid in retained and retained[cid] != item:
                    raise ValueError("已有学习项内容不能被覆盖。")
            available = {**self.known, **retained, **chosen}
            _validate_lesson_dependencies(chosen.values(), available)
            for item in chosen.values():
                if any(dep not in available for dep in item["depends_on"]):
                    raise ValueError("请同时保留所需的新前置学习项，或先保留这些前置内容。")
            validate_catalog(available)
            kept_bodies = {eid for eid, status in proposal["decisions"]["examples"].items() if status == "kept"} | set(selected["examples"])
            chosen_refs = [ref for ref in proposal["example_pack"]["references"] if ref["id"] in selected["references"]]
            if any(ref["example_id"] not in kept_bodies for ref in chosen_refs):
                raise ValueError("请先保留关联对应的例句正文，或同时选择正文。")
            eligible = self.eligible_reference_ids(proposal)
            active_refs = [ref for ref in chosen_refs if ref["id"] in eligible and ref["target_id"] in available]
            body_ids = set(selected["examples"]) | {ref["example_id"] for ref in active_refs}
            incoming = {**empty_pack(),
                        "examples": [e for e in proposal["example_pack"]["examples"] if e["id"] in body_ids],
                        "references": active_refs}
            if incoming["examples"]:
                if self.example_store is None:
                    raise ValueError("保留例句需要当前例句库。")
                for example in incoming["examples"]:
                    _assert_same_example_display(self.example_store.get(example["id"]), example)
                prepared = self.example_store.prepare_import(incoming, known_targets=available)
            retained.update(chosen)
            state["items"] = list(retained.values())
        for group, ids in selected.items():
            for cid in ids:
                proposal["decisions"][group][cid] = decision
        proposal["status"] = _status(proposal["decisions"])
        validate_ai_material_state(state)
        previous_pack = self.store.get_setting(EXAMPLE_SETTING, empty_pack())
        self._write(state, examples=prepared, _in_transaction=True)
        if prepared is not None:
            from .material_usage import register_in_transaction
            actual = {group: [row for row in prepared[group] if row['id'] in {r['id'] for r in incoming[group]}]
                      for group in ('examples', 'references')}
            register_in_transaction(self.store._db, actual, proposal=proposal, previous_pack=previous_pack)
        return deepcopy(proposal)

    def activate_references(self, proposal_id, reference_ids):
        """Explicitly enable previously retained links after evidence is ready."""
        db = self.store._db
        if db.in_transaction or self.example_store is None:
            raise ValueError("启用关联需要当前例句库。")
        with db:
            db.execute("BEGIN IMMEDIATE")
            state = self._state()
            proposal = next((row for row in state["proposals"] if row["id"] == proposal_id), None)
            if proposal is None:
                raise ValueError("找不到材料。")
            selected = self._selected(proposal, [], [], reference_ids)["references"]
            if any(proposal["decisions"]["references"][rid] != "kept" for rid in selected):
                raise ValueError("请先保留所选关联。")
            eligible = self.eligible_reference_ids(proposal)
            available = {**self.known, **{row["id"]: row for row in state["items"]}}
            refs = [r for r in proposal["example_pack"]["references"] if r["id"] in selected]
            if any(r["id"] not in eligible or r["target_id"] not in available for r in refs):
                raise ValueError("所选关联或目标仍待核实，尚未启用。")
            body_ids = {r["example_id"] for r in refs}
            incoming = {**empty_pack(), "examples": [e for e in proposal["example_pack"]["examples"] if e["id"] in body_ids], "references": refs}
            for example in incoming["examples"]:
                _assert_same_example_display(self.example_store.get(example["id"]), example)
            prepared = self.example_store.prepare_import(incoming, known_targets=available)
            previous_pack = self.store.get_setting(EXAMPLE_SETTING, empty_pack())
            self._write(state, examples=prepared, _in_transaction=True)
            from .material_usage import register_in_transaction
            actual = {group: [row for row in prepared[group] if row['id'] in {r['id'] for r in incoming[group]}]
                      for group in ('examples', 'references')}
            register_in_transaction(db, actual, proposal=proposal, previous_pack=previous_pack)
        self.example_store.refresh()
        return len(refs)

    def _write(self, state, *, examples=None, _in_transaction=False):
        encoded = _json(state)
        example_json = _json(examples) if examples is not None else None
        db = self.store._db
        if _in_transaction:
            if not db.in_transaction:
                raise RuntimeError("Material workflow requires an active transaction")
            self._write_in_transaction(db, encoded, example_json)
            return
        if db.in_transaction:
            raise RuntimeError("AI material changes require their own transaction")
        with db:
            db.execute("BEGIN IMMEDIATE")
            self._write_in_transaction(db, encoded, example_json)

    @staticmethod
    def _write_in_transaction(db, encoded, example_json):
        for key, payload in ((SETTING, encoded), (EXAMPLE_SETTING, example_json)):
            if payload is not None:
                old = db.execute("SELECT value_json FROM app_setting WHERE key=?", (key,)).fetchone()
                if old is not None and json.loads(old[0]) == json.loads(payload):
                    continue
                db.execute("INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                           (key, payload))


def material_output_schema():
    """Strict Responses JSON schema; structural output still needs local review.

    Empty irrelevant template arrays are intentional: strict schemas require
    every property. The local validator selects the family's actual templates.
    Models use request-local candidate IDs; stage assigns permanent identities.
    """
    from .order_translation_policy import order_translation_schema
    def obj(properties):
        return {"type": "object", "properties": properties, "required": list(properties),
                "additionalProperties": False}
    def string(limit=2000):
        return {"type": "string", "maxLength": limit}
    def array(items, limit=200):
        return {"type": "array", "items": items, "maxItems": limit}
    template = obj({"prompt": string(4000), "task": string(), "answer": string(4000),
        "feedback": string(4000), "choices": array(string(), 30),
        "input_mode": {"type": "string", "enum": ["choice", "text", "reorder"]},
        "accepted_answers": array(string(), 30), "strict": {"type": "boolean"},
        "known_wrong": array(string(), 30), "order_translation": order_translation_schema()})
    kinds = ["vocab_meaning", "vocab_reading", "grammar_meaning", "grammar_cloze",
             "pronunciation_rule", "pronunciation_application"]
    item = obj({"id": string(256), "family": {"type": "string", "enum": ["word", "grammar"]},
        "domain": {"type": "string", "enum": ["", "pronunciation"]},
        "title": string(1000), "reading": string(1000), "meaning": string(1000),
        "depends_on": array(string(1024), 30),
        "instruction": obj({"summary": string(4000), "rule": string(4000),
            "steps": array(string(), 12), "example": string(4000), "translation": string(4000),
            "order_translation": order_translation_schema(),
            "prerequisite": string(), "contrast": string()}),
        "question_templates": obj({kind: array(deepcopy(template), 2) for kind in kinds})})
    example = obj({"id": string(256), "revision": string(256), "text": string(4000),
        "translation": string(4000), "order_translation": order_translation_schema(),
        "reading": string(4000), "source_ids": array(string(256), 40)})
    span = obj({"start": {"type": "integer", "minimum": 0},
                "end": {"type": "integer", "minimum": 1}, "quote": string(4000)})
    reference = obj({"id": string(256), "example_id": string(256), "example_revision": string(256),
        "target_id": string(1024), "spans": array(span, 100),
        "hint": obj({"meaning": string(300), "reading": string(300), "rule": string(300)}),
        "explanation": string(2000), "weight": {"type": "number", "minimum": 0.01, "maximum": 10}})
    pack = obj({"format": {"type": "string", "enum": ["kanamemory-example-pack"]},
        "version": {"type": "integer", "enum": [1]},
        "status": {"type": "string", "enum": ["draft"]},
        "examples": array(example, MAX_EXAMPLES_PER_PROPOSAL), "references": array(reference, 1000)})
    segment = obj({**span["properties"], "example_id": string(256)})
    return obj({"items": array(item, MAX_ITEMS_PER_PROPOSAL), "example_pack": pack,
                "segments": array(segment, 200)})


def kana_example_targets(repo):
    """Reference-only identities for legacy kana; never new lesson records."""
    return [{"id": row["id"], "kana_id": row["id"], "family": "kana",
             "title": "／".join(value for value in (row.get("hiragana"), row.get("katakana")) if value),
             "reading": row.get("romaji") or "", "meaning": "假名 " + (row.get("romaji") or ""),
             "hiragana": row.get("hiragana", ""), "katakana": row.get("katakana", ""),
             "category": row.get("category", ""), "reference_only": True,
             "depends_on": [], "revision": "kana-example-target-v1"}
            for row in repo.kana_list()]


def ai_reference_targets(repo, source_text=None, *, target_ids=None):
    """Existing dictionary senses and kana for references, without enrollment.

    Build once per immutable content repository. Only attested legal primary
    reading/sense pairs enter the registry; no reading/sense Cartesian product
    or new identity is inferred from an AI spelling.
    """
    if target_ids is not None:
        # Ordinary lesson pages need only references already present in their
        # example packs. Resolve those without scanning the whole dictionary.
        if not isinstance(target_ids, (list, tuple, set, frozenset)):
            raise ValueError("例句目标标识必须是列表。")
        identities = _strings(list(target_ids), "target_ids", 20000)
        values = _selected_reference_targets(repo, identities)
        if source_text is not None:
            _text(source_text, "source_text", MAX_SOURCE_CHARS, blank=True)
            values = [item for item in values if any(item.get(key) and item[key] in source_text
                      for key in ("title", "reading", "hiragana", "katakana"))]
        return deepcopy(values)
    cache = getattr(repo, "_ai_reference_targets_v1", None)
    if cache is None:
        from .learning_content import learning_item_from_word
        result = {item["id"]: item for item in kana_example_targets(repo)}
        offset = 0
        while True:
            words = repo.search_words(limit=1000, offset=offset)
            for word in words:
                for index in range(len(word.get("senses") or [])):
                    item = learning_item_from_word(word, index, enrich=False)
                    if item.get("instruction_status") != "ready":
                        continue
                    item["reference_only"] = True
                    result.setdefault(item["id"], item)
            if len(words) < 1000:
                break
            offset += len(words)
        cache = tuple(result.values())
        repo._ai_reference_targets_v1 = cache
    values = cache
    if source_text is not None:
        _text(source_text, "source_text", MAX_SOURCE_CHARS, blank=True)
        values = [item for item in values if any(item.get(key) and item[key] in source_text
                  for key in ("title", "reading", "hiragana", "katakana"))]
    return deepcopy(list(values))


def _selected_reference_targets(repo, identities):
    cache = getattr(repo, "_ai_reference_targets_lookup_v1", None)
    if cache is None:
        cache = {}
        repo._ai_reference_targets_lookup_v1 = cache
    complete = getattr(repo, "_ai_reference_targets_v1", None)
    if complete is not None:
        cache.update({item["id"]: item for item in complete})
    if any(cid.startswith("kana-") and cid not in cache for cid in identities):
        cache.update({item["id"]: item for item in kana_example_targets(repo)})
    from .learning_content import learning_item_from_word
    for cid in identities:
        if cid in cache:
            continue
        cache[cid] = None
        if not cid.startswith("word:"):
            continue
        # Source IDs themselves may contain colons (e.g. jmdict:12345).
        # Probe prefixes, then verify the full canonical reading/sense ID;
        # never infer a new target from an untrusted ID's pieces.
        suffix = cid[len("word:"):]
        for index, character in enumerate(suffix):
            if character != ":":
                continue
            word = repo.get_word(suffix[:index])
            if word is None:
                continue
            for sense_index in range(len(word.get("senses") or [])):
                item = learning_item_from_word(word, sense_index, enrich=False)
                if item["instruction_status"] == "ready":
                    item["reference_only"] = True
                    cache[item["id"]] = item
            if cache.get(cid) is not None:
                break
    return [cache[cid] for cid in dict.fromkeys(identities) if cache.get(cid) is not None]
