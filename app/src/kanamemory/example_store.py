"""Versioned shared examples, contextual references and read-only progress ranking.

Examples are teaching resources, never memory cards. Their stable IDs and exact
Unicode anchors do not depend on the currently selected course or scheduler.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import unicodedata

FORMAT = "kanamemory-example-pack"
VERSION = 1
SETTING = "shared_examples_v1"
MAX_BYTES = 16 * 1024 * 1024
MAX_IMPORT_BYTES = 32 * 1024 * 1024
MAX_EXAMPLES = 20000
MAX_REFERENCES = 50000
HINT_KINDS = ("meaning", "reading", "rule")


def empty_pack():
    return {"format": FORMAT, "version": VERSION, "status": "reviewed",
            "examples": [], "references": []}


def _encoded(value):
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             allow_nan=False, separators=(",", ":"))
        size = len(encoded.encode("utf-8"))
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise ValueError("例句材料必须是有效的 JSON。") from exc
    if size > MAX_BYTES:
        raise ValueError("例句材料超过 16 MiB 上限；请分批导入。")
    return encoded


def _string(value, name, limit, *, blank=False):
    if not isinstance(value, str) or len(value) > limit or (not blank and not value.strip()):
        raise ValueError("例句字段不正确：" + name)
    if any(unicodedata.category(c) in {"Cc", "Cs"} and c not in "\n\t" for c in value):
        raise ValueError("例句字段包含不支持的控制字符：" + name)
    return value


def _identity(value, name="id", limit=256):
    result = _string(value, name, limit)
    if any(c.isspace() for c in result):
        raise ValueError("标识不可包含空白：" + name)
    return result


def example_id_for(text):
    """Hash the exact display text; no hidden normalization changes its spans."""
    _string(text, "text", 4000)
    return "example:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def reference_id_for(reference):
    value = {key: item for key, item in reference.items() if key != "id"}
    return "reference:" + hashlib.sha256(_encoded(value).encode("utf-8")).hexdigest()[:24]


def merge_example_metadata(first, second):
    """Keep the first display snapshot and retain every distinct source snapshot."""
    result = deepcopy(first)
    for field in ("source_ids", "lexical_review_ids"):
        if field in first or field in second:
            result[field] = sorted(set(first.get(field, []) + second.get(field, [])))
    if "lexical_provenance" in first or "lexical_provenance" in second:
        snapshots = {}
        for snapshot in first.get("lexical_provenance", []) + second.get("lexical_provenance", []):
            snapshots.setdefault(_encoded(snapshot), deepcopy(snapshot))
        result["lexical_provenance"] = list(snapshots.values())
    if (first.get("order_translation") is None and second.get("order_translation") is not None
            and all(first.get(key) == second.get(key) for key in ("id", "revision", "text"))
            and first.get("translation", "") == second.get("translation", "")):
        # An explicitly retained additive reference can complete an old custom
        # example. Existing prose, contextual meaning and references still win;
        # an already present experimental translation is never overwritten.
        from .order_translations import validate_order_translation
        result["order_translation"] = validate_order_translation(
            second["order_translation"], source_text=first["text"],
            source_translation=first.get("translation", ""))
    return result


def _validate_lexical_provenance(example):
    """Optional v1 extension; old packs need neither review IDs nor snapshots."""
    identities = example.get("lexical_review_ids", [])
    if not isinstance(identities, list) or len(identities) > 100:
        raise ValueError("例句审校来源标识不正确。")
    for identity in identities:
        _identity(identity, "lexical_review_id")
    snapshots = example.get("lexical_provenance", [])
    if not isinstance(snapshots, list) or len(snapshots) > 100:
        raise ValueError("例句来源快照不正确。")
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            raise ValueError("例句来源快照必须是对象。")
        _identity(snapshot.get("review_id"), "lexical_provenance.review_id")
        original = snapshot.get("original")
        if not isinstance(original, dict):
            raise ValueError("例句来源快照缺少原始资料。")
        translations = original.get("translations", [])
        if not isinstance(translations, list) or len(translations) > 100:
            raise ValueError("例句来源译文不正确。")
        for source in [original, *translations]:
            if not isinstance(source, dict):
                raise ValueError("例句来源记录必须是对象。")
            _string(source.get("text"), "lexical_provenance.text", 4000)
            for key in ("language", "example_id", "attribution", "attribution_url", "license",
                        "license_url", "owner_at_export", "source_modified", "content_origin"):
                if key in source:
                    _string(source[key], "lexical_provenance." + key, 4000, blank=True)
            source_ids = source.get("source_ids", [])
            if not isinstance(source_ids, list) or len(source_ids) > 40:
                raise ValueError("例句来源快照的来源标识不正确。")
            for identity in source_ids:
                _identity(identity, "lexical_provenance.source_id")
        for key in ("reviewed_text", "chinese_translation", "review_method", "notes"):
            if key in snapshot:
                _string(snapshot[key], "lexical_provenance." + key, 20000 if key == "notes" else 4000, blank=True)
        urls = snapshot.get("sources", [])
        if not isinstance(urls, list) or len(urls) > 100:
            raise ValueError("例句来源快照的核对资料不正确。")
        for url in urls:
            _string(url, "lexical_provenance.sources", 4000)


def validate_pack(pack, known_targets=None):
    """Return a detached validated v1 snapshot; never repair anchors by guessing.

    `known_targets=None` permits old references whose course is currently absent,
    so backup restoration does not make their text unusable. Import callers can
    supply the current catalogue to catch mistyped targets before activation.
    """
    _encoded(pack)
    if not isinstance(pack, dict) or pack.get("format") != FORMAT:
        raise ValueError("不是受支持的例句材料格式。")
    if type(pack.get("version")) is not int or pack["version"] != VERSION:
        raise ValueError("不支持此例句格式版本；原材料未修改。")
    if pack.get("status", "reviewed") not in {"reviewed", "draft"}:
        raise ValueError("例句材料审核状态不正确。")
    examples, references = pack.get("examples"), pack.get("references")
    if (not isinstance(examples, list) or len(examples) > MAX_EXAMPLES
            or not isinstance(references, list) or len(references) > MAX_REFERENCES):
        raise ValueError("例句或关联数量超出范围。")
    targets = None if known_targets is None else {
        value["id"] if isinstance(value, dict) else value for value in known_targets}
    by_id = {}
    for example in examples:
        if not isinstance(example, dict):
            raise ValueError("例句记录必须是对象。")
        eid = _identity(example.get("id"))
        if eid in by_id:
            raise ValueError("例句标识重复：" + eid)
        _identity(example.get("revision"), "revision")
        _string(example.get("text"), "text", 4000)
        _string(example.get("translation", ""), "translation", 4000, blank=True)
        if example.get("order_translation") is not None:
            from .order_translations import validate_order_translation
            validate_order_translation(example["order_translation"], source_text=example["text"],
                                       source_translation=example.get("translation", ""))
        _string(example.get("reading", ""), "reading", 4000, blank=True)
        sources = example.get("source_ids", [])
        if not isinstance(sources, list) or len(sources) > 40:
            raise ValueError("例句来源标识不正确。")
        for source in sources:
            _identity(source, "source_id")
        _validate_lexical_provenance(example)
        by_id[eid] = example
    ref_ids = set()
    for ref in references:
        if not isinstance(ref, dict):
            raise ValueError("例句关联必须是对象。")
        rid = _identity(ref.get("id"))
        if rid in ref_ids:
            raise ValueError("例句关联标识重复：" + rid)
        ref_ids.add(rid)
        eid = _identity(ref.get("example_id"), "example_id")
        target = _identity(ref.get("target_id"), "target_id", 1024)
        if targets is not None and target not in targets:
            raise ValueError("找不到关联学习项：" + target)
        if eid not in by_id or ref.get("example_revision") != by_id[eid]["revision"]:
            raise ValueError("关联的例句或文本版本不匹配。")
        spans = ref.get("spans")
        if not isinstance(spans, list) or not 1 <= len(spans) <= 100:
            raise ValueError("每个关联需要 1 至 100 个明确片段。")
        text = by_id[eid]["text"]
        for span in spans:
            if not isinstance(span, dict):
                raise ValueError("强调片段格式不正确。")
            start, end, quote = span.get("start"), span.get("end"), span.get("quote")
            if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text)
                    or not isinstance(quote, str) or text[start:end] != quote):
                raise ValueError("强调片段与原句不一致；请重新确认位置和原文。")
        hint = ref.get("hint")
        if not isinstance(hint, dict) or not set(hint) <= set(HINT_KINDS):
            raise ValueError("提示仅支持 meaning、reading、rule 三类。")
        for key, value in hint.items():
            _string(value, "hint." + key, 300, blank=True)
        if not any(value.strip() for value in hint.values()):
            raise ValueError("每个关联至少需要一条简短提示。")
        if 'surface_hint' in ref and type(ref['surface_hint']) is not bool:
            raise ValueError("surface_hint 必须为布尔值。")
        if ref.get('surface_hint') and not hint.get('rule', '').strip():
            raise ValueError("表层读法提示需要 rule 中的具体词形及读法说明。")
        if 'reading_scope' in ref and (ref['reading_scope'] not in {'lemma', 'span'} or not hint.get('reading')):
            raise ValueError("读法范围应为 lemma 或 span，并附有读法提示。")
        _string(ref.get("explanation", ""), "explanation", 2000, blank=True)
        weight = ref.get("weight", 1.0)
        if type(weight) not in {int, float} or not math.isfinite(weight) or not 0 < weight <= 10:
            raise ValueError("关联权重必须大于 0 且不超过 10。")
    value = deepcopy(pack)
    value.setdefault("status", "reviewed")
    return value


def serialize_pack(pack, known_targets=None):
    """Export validated UTF-8 within the same compact budget used on import."""
    return _encoded(validate_pack(pack, known_targets)).encode("utf-8")


def parse_pack(raw, known_targets=None, *, require_status=False):
    """Allow formatting overhead while keeping the normalized data budget fixed."""
    if not isinstance(raw, (str, bytes)):
        raise ValueError("例句材料必须是 UTF-8 JSON 文本。")
    try:
        size = len(raw) if isinstance(raw, bytes) else len(raw.encode("utf-8"))
        if size > MAX_IMPORT_BYTES:
            raise ValueError("单次导入文本不能超过 32 MiB。")
        text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw.removeprefix("\ufeff")
        pack = json.loads(text)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("例句材料必须是有效的 UTF-8 JSON。") from exc
    if require_status and (not isinstance(pack, dict) or pack.get("status") not in {"draft", "reviewed"}):
        raise ValueError("例句包缺少有效的审核状态。")
    return validate_pack(pack, known_targets)


def _merge_packs(*packs):
    result = empty_pack()
    for field in ("examples", "references"):
        values = {}
        for pack in packs:
            for row in pack[field]:
                previous = values.get(row["id"])
                if previous is not None and previous != row:
                    if field == "examples" and all(previous.get(key) == row.get(key) for key in ("text", "revision")):
                        # Bibliographic or global translation metadata may evolve
                        # while anchors remain immutable. First pack wins display;
                        # retained custom snapshots and contextual hints stay intact.
                        values[row["id"]] = merge_example_metadata(previous, row)
                        continue
                    raise ValueError("已有标识对应不同材料，不能覆盖：" + row["id"])
                values[row["id"]] = deepcopy(row)
        result[field] = list(values.values())
    return validate_pack(result)


class ExampleStore:
    def __init__(self, store, learning_store, items, bundled_pack=None, lexical_review=None):
        self.store, self.learning_store = store, learning_store
        self.items = {item["id"]: deepcopy(item) for item in items}
        self.lexical_review = lexical_review
        self._lexical_cache = None
        if bundled_pack is None:
            from .example_catalogue import bundled_example_pack
            from .personal_content_versions import original_source
            bundled_pack = bundled_example_pack([original_source(store, item) for item in self.items.values()])
        self.bundled = validate_pack(bundled_pack)
        if self.bundled["status"] != "reviewed":
            raise ValueError("随包例句必须完成审核。")
        self._token = None
        self.refresh()

    def _database_token(self):
        db = getattr(self.store, "_db", None)
        if db is None or db.in_transaction:
            return None
        return id(db), db.total_changes, db.execute("PRAGMA data_version").fetchone()[0]

    def _ensure_fresh(self):
        if self._token is None or self._database_token() != self._token:
            self.refresh()

    def refresh(self):
        """Re-read restored/custom resources without touching learning rows."""
        from .personal_content_versions import changed_resources, GENERATION
        self._personal_changed = changed_resources(self.store)
        generation = self.store.get_setting(GENERATION, 0)
        if generation != getattr(self, '_personal_generation', None):
            self._graph_cache = None
            self._personal_generation = generation
        raw_custom = self.store.get_setting(SETTING, empty_pack())
        # Learning writes also change the database token. Only resource changes
        # require rebuilding these indexes; familiarity is queried separately.
        # JSON equality preserves type distinctions such as True versus 1.
        source_json = _encoded(raw_custom)
        from .material_usage import read as usage_state, active_pack
        from .material_workflow import digest
        usage = usage_state(self.store)
        usage_fingerprint = digest(usage)
        if source_json == getattr(self, "_custom_source_json", None) and usage_fingerprint == getattr(self, '_usage_fingerprint', None):
            self._token = self._database_token()
            return self
        custom = validate_pack(raw_custom)
        if custom["status"] != "reviewed":
            raise ValueError("草稿不能作为已启用的例句材料。")
        active = active_pack(self.store, custom, state=usage)
        if not active["examples"] and not active["references"]:
            # The bundle is already validated and these internal views are read
            # only. Public resource accessors return detached copies.
            merged = self.bundled
        else:
            from .material_review import reviewed_pack
            merged = _merge_packs(self.bundled, reviewed_pack(active))
        self.custom = custom
        self._graph_cache = None
        self.active_custom = active
        self._usage_fingerprint = usage_fingerprint
        self.examples = {row["id"]: row for row in merged["examples"]}
        self.refs = {row["id"]: row for row in merged["references"]}
        self.by_example, self.by_target = {}, {}
        for ref in self.refs.values():
            self.by_example.setdefault(ref["example_id"], []).append(ref)
            self.by_target.setdefault(ref["target_id"], []).append(ref)
        self._custom_source_json = source_json
        self._token = self._database_token()
        return self

    def get(self, example_id):
        self._ensure_fresh()
        return self._get(example_id)

    def _get(self, example_id):
        graph = self._graph_resources()
        if graph is not None:
            if example_id in graph['examples']: return deepcopy(graph['examples'][example_id])
            if example_id in graph['retired']:
                old = self._get_raw(example_id)
                if old: old.update(personal_replacement_id=graph['retired'][example_id], personal_content_unavailable=True)
                return old
            if isinstance(example_id, str) and example_id.startswith('personal:example:'):
                old = self._get_raw(example_id)
                if old: old['personal_content_unavailable'] = True
                return old
        return self._personal_projection('example', self._get_raw(example_id))

    def _graph_resources(self):
        if not any(fields & {'text', 'link'} for fields in self._personal_changed.values()): return None
        if getattr(self, '_graph_cache', None) is None:
            from .personal_graph import project_pack
            pack, retired = project_pack(self.store, self.export_pack(include_bundled=True))
            by_example, by_target = {}, {}
            for ref in pack['references']:
                by_example.setdefault(ref['example_id'], []).append(ref)
                by_target.setdefault(ref['target_id'], []).append(ref)
            self._graph_cache = {'pack': pack, 'retired': retired,
                'examples': {e['id']: e for e in pack['examples']}, 'by_example': by_example, 'by_target': by_target}
        return self._graph_cache

    def _personal_projection(self, kind, value):
        if value and (kind, value['id']) in self._personal_changed:
            from .personal_content_versions import resolve
            return resolve(self.store, value, kind=kind)
        return value

    def get_raw(self, example_id):
        """Canonical identity/normalization input; never a personal fact claim."""
        self._ensure_fresh()
        return self._get_raw(example_id)

    def _get_raw(self, example_id):
        lexical, _, _ = self._lexical_resources()
        source, extra = self.examples.get(example_id), lexical.get(example_id)
        value = deepcopy(source or extra)
        if value is None and isinstance(example_id, str) and example_id.startswith('personal:example:'):
            from .personal_graph import stored_example
            value = stored_example(self.store, example_id)
        if value and source and extra:
            value = merge_example_metadata(source, extra)
        return value

    def _lexical_resources(self):
        """Lazy indexes contain only accepted references to available courses."""
        if self._lexical_cache is None:
            pack = self.lexical_review.shared_pack(self.items) if self.lexical_review else empty_pack()
            examples = {e["id"]: e for e in pack["examples"]}
            by_example, by_target = {}, {}
            for ref in pack["references"]:
                by_example.setdefault(ref["example_id"], []).append(ref)
                by_target.setdefault(ref["target_id"], []).append(ref)
            self._lexical_cache = examples, by_example, by_target
        return self._lexical_cache

    def _references_for(self, example_id):
        graph = self._graph_resources()
        if graph is not None: return deepcopy(graph['by_example'].get(example_id, []))
        _, by_example, _ = self._lexical_resources()
        refs = {r["id"]: r for r in self.by_example.get(example_id, [])}
        refs.update({r["id"]: r for r in by_example.get(example_id, [])})
        return [self._personal_projection('reference', ref) for ref in refs.values()]

    def get_reference(self, identity, example_id=None):
        """Maintenance may inspect an inactive relation without teaching it."""
        self._ensure_fresh()
        source = self.refs.get(identity)
        if source is None:
            _, by_example, _ = self._lexical_resources()
            groups = [by_example.get(example_id, [])] if example_id else by_example.values()
            source = next((r for group in groups for r in group if r['id'] == identity), None)
        if source is None:
            graph = self._graph_resources()
            if graph:
                source = next((r for group in graph['by_example'].values() for r in group if r['id'] == identity), None)
        return self._personal_projection('reference', deepcopy(source)) if source else None

    def evidence_pack(self):
        """Raw trusted package minus replaced facts; personal prose is not evidence."""
        return self._evidence_pack()

    def evidence_for_texts(self, texts):
        """Detached trusted evidence for this request, without copying the corpus."""
        return self._evidence_pack(texts=set(texts))

    def _evidence_pack(self, *, texts=None):
        self._ensure_fresh()
        changed = self._personal_changed
        graph = self._graph_resources()
        retired = graph['retired'] if graph else {}
        examples = [e for e in self.bundled['examples'] if ('example', e['id']) not in changed and e['id'] not in retired]
        ids = {e['id'] for e in examples}
        refs = [r for r in self.bundled['references'] if r['example_id'] in ids
                and ('reference', r['id']) not in changed and ('lesson', r['target_id']) not in changed]
        from .material_reference import retained_evidence_pack
        added, linked = retained_evidence_pack(self.store, self.active_custom)
        added = [e for e in added if e['id'] not in retired and ('example', e['id']) not in changed]
        trusted = {e['id'] for e in added}
        linked = [r for r in linked if r['example_id'] in trusted
                  and ('reference', r['id']) not in changed and ('lesson', r['target_id']) not in changed]
        examples = list({e['id']: e for e in [*examples, *added]}.values())
        refs = list({r['id']: r for r in [*refs, *linked]}.values())
        if texts is not None:
            examples = [e for e in examples if e['text'] in texts]
            ids = {e['id'] for e in examples}
            refs = [r for r in refs if r['example_id'] in ids]
        return {**self.bundled, 'examples': deepcopy(examples), 'references': deepcopy(refs)}

    def display_pack(self):
        """Explicit current teaching inventory for impact review, not an export."""
        self._ensure_fresh()
        graph = self._graph_resources()
        if graph is not None: return deepcopy(graph['pack'])
        result = self.export_pack(include_bundled=True)
        result['examples'] = [self._personal_projection('example', r) for r in result['examples']]
        result['references'] = [self._personal_projection('reference', r) for r in result['references']]
        return result

    def export_pack(self, *, include_bundled=False):
        self._ensure_fresh()
        from .material_review import reviewed_pack
        if not include_bundled:
            return deepcopy(self.active_custom)
        extra, by_example, _ = self._lexical_resources()
        pack = {**empty_pack(), "examples": list(extra.values()),
                "references": [r for refs in by_example.values() for r in refs]}
        return _merge_packs(self.bundled, reviewed_pack(self.active_custom), pack)

    def prepare_import(self, pack, *, known_targets=None):
        """Validate an additive import without writing, for larger transactions.

        A caller activating new retained lessons may pass their already-validated
        identities. This does not enroll them or mutate this service's catalogue.
        """
        self._ensure_fresh()
        incoming = validate_pack(pack, self.items if known_targets is None else known_targets)
        if incoming["status"] != "reviewed":
            raise ValueError("请先预览并确认草稿，再导入已审核材料。")
        extra, by_example, _ = self._lexical_resources()
        for row in incoming["examples"]:
            existing = extra.get(row["id"])
            if existing and any(existing.get(key) != row.get(key) for key in ("text", "revision")):
                raise ValueError("已有标识对应不同材料，不能覆盖：" + row["id"])
        external_refs = {r["id"]: r for refs in by_example.values() for r in refs}
        for row in incoming["references"]:
            if row["id"] in external_refs and row != external_refs[row["id"]]:
                raise ValueError("已有标识对应不同材料，不能覆盖：" + row["id"])
        # Check all collisions and anchors before writing the one setting.
        from .material_review import reviewed_pack
        _merge_packs(self.bundled, reviewed_pack(self.custom), reviewed_pack(incoming))
        bundled_examples = {row["id"]: row for row in self.bundled["examples"]}
        bundled_refs = {row["id"]: row for row in self.bundled["references"]}
        merged = _merge_packs(self.custom, incoming)
        # An imported ref may use bundled text; keep that immutable text snapshot
        # with it so exports remain independently valid across future releases.
        used = {row["example_id"] for row in merged["references"] if row["id"] not in bundled_refs}
        merged["references"] = [row for row in merged["references"] if row["id"] not in bundled_refs]
        def adds_sources(row):
            bundled = bundled_examples.get(row["id"])
            if bundled is None:
                return True
            combined = merge_example_metadata(bundled, row)
            return any(combined.get(key) != bundled.get(key)
                       for key in ("source_ids", "lexical_review_ids", "lexical_provenance", "order_translation"))
        merged["examples"] = [row for row in merged["examples"]
                              if row["id"] in used or adds_sources(row)]
        validate_pack(merged)
        return merged

    def import_pack(self, pack):
        """Atomic additive activation; duplicate imports are no-ops, not reviews."""
        merged = self.prepare_import(pack)
        from .material_usage import register_in_transaction
        db = self.store._db
        with db:
            db.execute('BEGIN IMMEDIATE')
            if merged != self.custom:
                db.execute('INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json',
                           (SETTING, _encoded(merged)))
            register_in_transaction(db, pack)
        self.refresh()
        return {"examples": len(merged["examples"]), "references": len(merged["references"])}

    def add_example(self, text, translation, target_id, spans, hint, explanation="", reading="", example_id=None):
        self._ensure_fresh()
        if target_id not in self.items:
            raise ValueError("找不到关联学习项：" + str(target_id))
        extra, _, _ = self._lexical_resources()
        existing = self.get_raw(example_id) if example_id is not None else next(
            (self._get_raw(row["id"]) for row in [*self.examples.values(), *extra.values()] if row["text"] == text), None)
        if existing and existing["text"] != text:
            raise ValueError("已有例句标识的原文不可被改写。")
        example = deepcopy(existing) if existing else {
            "id": example_id or example_id_for(text), "revision": "1", "text": text,
            "translation": translation, "reading": reading, "source_ids": ["user-added"]}
        displayed = self._personal_projection('example', existing) if existing else None
        if existing and translation and translation != displayed.get("translation"):
            explanation = "\n".join(part for part in (translation, explanation) if part)
        reference = {"example_id": example["id"], "example_revision": example["revision"],
            "target_id": target_id, "spans": deepcopy(spans), "hint": deepcopy(hint),
            "explanation": explanation, "weight": 1.0}
        reference["id"] = reference_id_for(reference)
        self.import_pack({**empty_pack(), "examples": [example], "references": [reference]})
        return self.get(example["id"])

    @staticmethod
    def _ability_familiarity(unit):
        # A bounded UI-ranking heuristic, not a claimed recall probability.
        # Merely enrolling, reading an explanation or acknowledging a prerequisite
        # supplies no independent-success evidence and therefore scores zero.
        if not unit or not unit.get("review_count") or not unit.get("scheduler_state"):
            return 0.0
        successes = unit.get("independent_success_count", unit["review_count"]
                             if unit.get("last_unassisted_success_at_utc") else 0)
        if not successes:
            return 0.0
        state = unit["scheduler_state"]
        stability = state.get("stability")
        if type(stability) not in {int, float} or not math.isfinite(stability) or stability <= 0:
            return 0.0
        practice = min(1.0, successes / 4)
        strength = stability / (stability + 14)
        return round(practice * (0.4 + 0.6 * strength), 6)

    def _status_from_saved(self, target_id, saved):
        if saved is None:
            return {"is_added": False, "label": "未加入", "stage": None,
                    "familiarity": 0.0, "abilities": {}}
        abilities = {unit["ability"]: self._ability_familiarity(unit) for unit in saved.get("units", [])}
        # Reading an example needs lexical sense/reading, not every optional
        # listening or production ability. Grammar uses understanding/application.
        target = self.items.get(target_id, saved)
        relevant = ([abilities[key] for key in ("understanding", "reading") if key in abilities]
                    if target.get("family") == "word" else list(abilities.values()))
        familiarity = sum(relevant) / len(relevant) if relevant else 0.0
        if familiarity >= 0.65:
            label = "较熟悉"
        elif saved.get("stage") in {"intro", "guide"}:
            label = "已加入·待学习"
        else:
            label = "学习中"
        return {"is_added": True, "label": label, "stage": saved.get("stage"),
                "familiarity": round(familiarity, 6), "abilities": abilities}

    def _statuses(self, target_ids):
        """Two small table scans for a screen, not N unit/history lookups."""
        identities = set(target_ids)
        db = getattr(self.learning_store, "_db", None)
        if db is None:
            return {cid: self._status_from_saved(cid, self.learning_store.get_item(cid)) for cid in identities}
        saved = {row["content_id"]: {"stage": row["stage"], "units": []}
                 for row in db.execute("SELECT content_id,stage FROM learning_content")
                 if row["content_id"] in identities}
        for source in db.execute("""SELECT u.*, COALESCE(e.success_count,0) AS independent_success_count
                FROM learning_unit u LEFT JOIN (
                    SELECT unit_id,COUNT(*) AS success_count FROM learning_event
                    WHERE outcome='correct' AND assisted=0 AND rating>1 GROUP BY unit_id
                ) e USING(unit_id)"""):
            if source["content_id"] in saved:
                unit = dict(source)
                unit["scheduler_state"] = json.loads(unit["scheduler_state_json"]) if unit["scheduler_state_json"] else None
                saved[unit["content_id"]]["units"].append(unit)
        result = {cid: self._status_from_saved(cid, saved.get(cid)) for cid in identities}
        kana_ids = {cid for cid in identities if self.items.get(cid, {}).get("family") == "kana"}
        if kana_ids:
            result.update(self._kana_statuses(kana_ids, db))
        return result

    def _kana_statuses(self, identities, db):
        """Bridge legacy kana progress read-only, without creating course rows."""
        from .domain import WORD_SKILLS
        cards = {cid: [] for cid in identities}
        raw = [dict(source) for source in db.execute("""SELECT c.*, COALESCE(e.success_count,0) AS independent_success_count
            FROM study_card c LEFT JOIN (
                SELECT card_id,COUNT(*) AS success_count FROM review_event
                WHERE outcome='correct' AND assisted=0 AND rating>1 GROUP BY card_id
            ) e USING(card_id)""")]
        from .kana_routing import project_cards
        successes = {row["card_id"]: row["independent_success_count"] for row in raw}
        for source in project_cards(self.store, raw):
            if source["content_id"] in cards and source["skill"] not in WORD_SKILLS:
                row = dict(source)
                row["independent_success_count"] = sum(successes.get(cid, 0)
                    for cid in row.get("history_card_ids", [row["card_id"]]))
                row["scheduler_state"] = json.loads(row["scheduler_state_json"])
                cards[row["content_id"]].append(row)
        queued = set()
        from .session_questions import iter_rows
        for row in db.execute("SELECT session_id,question_index FROM session_checkpoint"):
            for question in iter_rows(db, row["session_id"], row["question_index"]):
                if question.get("kind") not in WORD_SKILLS:
                    queued.add(question.get("content_id"))
        reserved = self.store.get_setting("study_path_v1", {}).get("reserved_entries", [])
        result = {}
        for cid, rows in cards.items():
            added = bool(rows) or cid in queued or any(
                f"kana:{cid}:{script}" in reserved for script in ("hiragana", "katakana"))
            # A known hiragana form alone is not evidence for its katakana.
            by_script = [max((self._ability_familiarity(row) for row in rows if row["script"] == script),
                             default=0.0) for script in ("hiragana", "katakana")]
            familiarity = round(sum(by_script) / 2, 6)
            has_review = any(row["review_count"] for row in rows)
            result[cid] = {"is_added": added,
                "label": ("较熟悉" if familiarity >= 0.65 else "学习中" if has_review
                          else "已加入·待学习" if added else "未加入"),
                "stage": "done" if has_review else "intro" if added else None,
                "familiarity": familiarity,
                "abilities": {"reading": familiarity, "understanding": familiarity},
                "legacy_kana": True}
        return result

    def item_status(self, target_id):
        return self._statuses([target_id])[target_id]

    def familiarity(self, target_id, ability=None):
        status = self.item_status(target_id)
        return status["abilities"].get(ability, 0.0) if ability else status["familiarity"]

    def display_hints(self, reference, current_target_id=None, *, _status=None):
        """Keep the current sense/rule accessible, suppress known background cues."""
        if reference.get('personal_withdrawal_id'):
            return [{'kind': 'rule', 'text': '说明待重新核对。'}]
        target_id = reference["target_id"]
        target = self.items.get(target_id, {})
        hints = reference.get("hint", {})
        status = _status if _status is not None else self.item_status(target_id)
        focus = current_target_id == target_id
        main = "meaning" if target.get("family") == "word" else "rule"
        if not hints.get(main):
            main = next((kind for kind in HINT_KINDS if hints.get(kind)), None)
        result = []
        for kind in HINT_KINDS:
            text = hints.get(kind)
            if not text:
                continue
            ability = "reading" if kind == "reading" else "understanding"
            known = status["abilities"].get(ability, 0.0) >= 0.65
            # A surface-form cue includes the reading of the actual inflection.
            # Knowing the meaning alone must not suppress it while reading is
            # still weak, leaving e.g. 見て paired only with lemma reading みる.
            surface = (kind == "rule" and target.get("family") == "word"
                       and reference.get("surface_hint", False))
            if surface:
                known = known and status["abilities"].get("reading", 0.0) >= 0.65
            if (focus and kind == main) or not known:
                # Explanation of the current rule is sufficient; an equivalent
                # meaning string should not create a second redundant label.
                if kind == "meaning" and main == "rule" and focus and hints.get("rule"):
                    continue
                if text not in {value["text"] for value in result}:
                    result.append({"kind": kind, "text": text,
                        **({"scope": reference["reading_scope"]} if kind == "reading" and reference.get("reading_scope") else {}),
                        **({"surface": True} if surface else {})})
        return result

    def references(self, example_id, current_target_id=None):
        self._ensure_fresh()
        result = []
        sources = self._references_for(example_id)
        statuses = self._statuses(ref["target_id"] for ref in sources)
        for source in sources:
            ref = deepcopy(source)
            ref["target"] = deepcopy(self.items.get(ref["target_id"], {
                "id": ref["target_id"], "title": ref["target_id"], "unavailable": True}))
            ref["status"] = statuses[ref["target_id"]]
            ref["familiarity"] = ref["status"]["familiarity"]
            ref["hints"] = self.display_hints(ref, current_target_id, _status=ref["status"])
            result.append(ref)
        return sorted(result, key=lambda ref: (ref["target_id"] != current_target_id,
                                               -ref["familiarity"], ref["target_id"], ref["id"]))

    def overlapping_references(self, example_id, start, end, current_target_id=None):
        if type(start) is not int or type(end) is not int or start < 0 or start >= end:
            raise ValueError("片段位置不正确。")
        return [ref for ref in self.references(example_id, current_target_id)
                if any(span["start"] < end and span["end"] > start for span in ref["spans"])]

    def for_item(self, target_id):
        self._ensure_fresh()
        rows = []
        statuses = self._statuses(self.items)
        target = self.items.get(target_id, {})
        instruction = target.get("instruction", {})
        # Introductory examples are authored for this particular sense/rule.
        # At equal progress (especially a new learner's all-zero scores), they
        # are a better starting point than an arbitrary sentence-ID order.
        introductory = {instruction.get("example", "")}
        for examples in (instruction.get("examples", []), target.get("examples", [])):
            introductory.update(example.get("text", "") for example in examples if isinstance(example, dict))
        order = {}
        grouped = {}
        graph = self._graph_resources()
        if graph is not None:
            refs = {r['id']: r for r in graph['by_target'].get(target_id, [])}
        else:
            _, _, extra_targets = self._lexical_resources()
            refs = {r["id"]: r for r in self.by_target.get(target_id, [])}
            refs.update({r["id"]: r for r in extra_targets.get(target_id, [])})
        for ref in refs.values():
            if graph is None: ref = self._personal_projection('reference', ref)
            grouped.setdefault(ref["example_id"], []).append(ref)
        for alternatives in grouped.values():
            # Multiple readings/explanations of one sentence remain selectable
            # in its detail page, without inflating the example count or score.
            alternatives = sorted(alternatives, key=lambda ref: ref["id"])
            source = alternatives[0]
            example = self._get(source["example_id"])
            others = {}
            for ref in self._references_for(example["id"]):
                if ref["target_id"] != target_id:
                    others[ref["target_id"]] = max(others.get(ref["target_id"], 0), ref.get("weight", 1.0))
            denominator = sum(others.values())
            score = (sum(weight * statuses.get(cid, {}).get("familiarity", 0) for cid, weight in others.items()) / denominator
                     if denominator else 0.0)
            example.update(reference=deepcopy(source), score=round(score, 8),
                           reference_options=deepcopy(alternatives),
                           hints=self.display_hints(source, target_id, _status=statuses.get(target_id)), related_count=len(others))
            unknown_load = sum(weight * (1 - statuses.get(cid, {}).get("familiarity", 0))
                               for cid, weight in others.items())
            order[example["id"]] = (example["text"] not in introductory,
                                    round(unknown_load, 8), len(example["text"]), example["text"])
            rows.append(example)
        return sorted(rows, key=lambda row: (-row["score"], *order[row["id"]],
                                            row["id"], row["reference"]["id"]))

    def all_examples(self, query=""):
        """Browse/search with a progress-weighted mean, stable equal-score order."""
        return self.examples_page(query, limit=None)["examples"]

    def examples_page(self, query="", *, offset=0, limit=10):
        """Rank from current progress, then copy only the requested display rows."""
        if type(offset) is not int or offset < 0 or (limit is not None and (type(limit) is not int or limit < 0)):
            raise ValueError("例句分页范围不正确。")
        self._ensure_fresh()
        statuses = self._statuses(self.items)
        query = str(query).strip().casefold()
        rows = []
        extra, _, _ = self._lexical_resources()
        graph = self._graph_resources()
        sources = graph['examples'] if graph is not None else {**extra, **self.examples}
        for identity in sources:
            # Merged metadata preserves these display fields from the first
            # snapshot. Source/provenance merging is needed only for displayed
            # rows, not for thousands of off-screen examples.
            source = sources[identity] if graph is not None else self._personal_projection('example', sources[identity])
            refs = self._references_for(source["id"])
            if query:
                searchable = "\n".join([source["text"], source.get("translation", ""), source.get("reading", ""),
                                         *[self.items.get(ref["target_id"], {}).get("title", "") for ref in refs]])
                if query not in searchable.casefold():
                    continue
            weights = {}
            for ref in refs:
                weights[ref["target_id"]] = max(weights.get(ref["target_id"], 0), ref.get("weight", 1.0))
            denominator = sum(weights.values())
            score = (sum(weight * statuses.get(cid, {}).get("familiarity", 0) for cid, weight in weights.items()) / denominator
                     if denominator else 0.0)
            rows.append((round(score, 8), identity, refs[0] if refs else None, len(weights)))
        rows.sort(key=lambda row: (-row[0], row[1]))
        selected = rows[offset:None if limit is None else offset + limit]
        return {"total": len(rows), "examples": [
            {**self._get(identity), "reference": deepcopy(reference), "score": score,
             "hints": [], "related_count": related_count}
            for score, identity, reference, related_count in selected]}
