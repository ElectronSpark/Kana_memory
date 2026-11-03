"""Candidate learning checkpoints, separate from enrollment and formal cards."""
from copy import deepcopy
from datetime import datetime
import json

from . import app_clock
from .material_workflow import digest, dump, MaterialWorkflowStore
from .material_verification import MaterialVerificationStore, proposal_content

PREFIX = "material_study_v1:"
FORMAT = "kanamemory-material-study"


def validate_state(value):
    fields = {"format", "version", "id", "proposal_id", "proposal_sha256", "target_ids", "lessons", "diagnostic_id", "enrollment", "created_at"}
    if (not isinstance(value, dict) or set(value) - {'additional_enrollments', 'scope_history', 'lesson_revisions'} != fields or value["format"] != FORMAT
            or type(value["version"]) is not int or value["version"] != 1
            or not isinstance(value["id"], str) or len(value["id"]) != 64
            or not isinstance(value["target_ids"], list) or not 1 <= len(value["target_ids"]) <= 24
            or len(set(value["target_ids"])) != len(value["target_ids"])
            or not isinstance(value["lessons"], list) or not 1 <= len(value["lessons"]) <= 128
            or len(dump(value).encode("utf-8")) > 4 * 1024 * 1024):
        raise ValueError("材料学习记录无效。")
    seen, incomplete = set(), False
    for row in value["lessons"]:
        if (not isinstance(row, dict) or set(row) != {"item", "sha256", "presented_at", "completed_at", "exposures"}
                or not isinstance(row["item"], dict) or row["sha256"] != digest(row["item"])
                or row["item"].get("id") in seen or not isinstance(row["exposures"], dict)
                or set(row["exposures"]) - {"teaching", "tts"}):
            raise ValueError("材料讲解检查点无效。")
        seen.add(row["item"]["id"])
        if row["completed_at"] and (incomplete or not row["presented_at"]):
            raise ValueError("讲解进度不连续。")
        incomplete |= row["completed_at"] is None
        for at in (row["presented_at"], row["completed_at"], *row["exposures"].values()):
            if at is not None and (not isinstance(at, str) or datetime.fromisoformat(at).tzinfo is None):
                raise ValueError("材料接触时间无效。")
    if not set(value["target_ids"]) <= seen:
        raise ValueError("材料讲解缺少所选内容。")
    revisions = value.get('lesson_revisions', [])
    if not isinstance(revisions, list) or len(revisions) > 128:
        raise ValueError('材料讲解版本记录无效。')
    heads = {}
    for revision in revisions:
        if (not isinstance(revision, dict) or set(revision) != {'index', 'before', 'replacement_sha256', 'at', 'request_sha256'}
                or type(revision['index']) is not int or not 0 <= revision['index'] < len(value['lessons'])
                or not isinstance(revision['before'], dict) or not isinstance(revision['before'].get('item'), dict)
                or revision['before'].get('completed_at') is not None
                or any(not isinstance(revision[k], str) or len(revision[k]) != 64 for k in ('replacement_sha256', 'request_sha256'))
                or not isinstance(revision['at'], str) or datetime.fromisoformat(revision['at']).tzinfo is None):
            raise ValueError('材料讲解替换回执无效。')
        before = revision['before']; index = revision['index']
        # Validate an archived checkpoint under the same original v1 contract.
        validate_state({k: deepcopy(v) for k, v in {**value,
            'target_ids': [before['item'].get('id')], 'lessons': [before], 'diagnostic_id': None, 'enrollment': None}.items()
            if k in fields})
        if (before['item']['id'] != value['lessons'][index]['item']['id']
                or index in heads and before['sha256'] != heads[index]):
            raise ValueError('材料讲解替换不能改变学习项或断开版本链。')
        heads[index] = revision['replacement_sha256']
    if any(value['lessons'][index]['sha256'] != sha for index, sha in heads.items()):
        raise ValueError('当前讲解与替换回执不一致。')
    history = value.get('scope_history', [])
    if not isinstance(history, list) or len(history) > 23:
        raise ValueError('材料追加范围无效。')
    if any(not isinstance(row, dict) or not isinstance(row.get('target_ids'), list)
           or any(not isinstance(cid, str) for cid in row['target_ids']) for row in history):
        raise ValueError('材料追加范围无效。')
    scopes = [row['target_ids'] for row in history] + [value['target_ids']]
    lesson_ids = [row['item']['id'] for row in value['lessons']]
    for index, row in enumerate(history):
        if (not isinstance(row, dict) or set(row) != {'target_ids', 'lesson_ids', 'at', 'request_sha256'}
                or not isinstance(row['target_ids'], list) or not row['target_ids']
                or not isinstance(row['lesson_ids'], list) or not row['lesson_ids']
                or row['lesson_ids'] != lesson_ids[:len(row['lesson_ids'])]
                or not set(row['target_ids']) <= set(row['lesson_ids'])
                or len(scopes[index + 1]) <= len(row['target_ids'])
                or row['target_ids'] != scopes[index + 1][:len(row['target_ids'])]
                or not isinstance(row['request_sha256'], str) or len(row['request_sha256']) != 64
                or not isinstance(row['at'], str) or datetime.fromisoformat(row['at']).tzinfo is None):
            raise ValueError('材料追加不能替换原范围或讲解。')
    def receipt_scope(receipt):
        sha = receipt.get('study_scope_sha256')
        return (any(sha == digest(scope) and set(receipt['target_ids']) <= set(scope) for scope in scopes)
                if sha else receipt['target_ids'] in scopes)
    if value["diagnostic_id"] is not None and (not isinstance(value["diagnostic_id"], str) or len(value["diagnostic_id"]) != 64):
        raise ValueError("材料检查标识无效。")
    if value["enrollment"] is not None:
        receipt = value["enrollment"]
        if (not isinstance(receipt, dict) or set(receipt) - {'abilities', 'added_abilities', 'study_scope_sha256'} != {"at", "target_ids", "ordered_ids", "added_ids", "plan_sha256"}
                or not receipt_scope(receipt) or not isinstance(receipt["ordered_ids"], list)
                or not isinstance(receipt["added_ids"], list) or not set(receipt["added_ids"]) <= set(receipt["ordered_ids"])
                or not set(receipt["target_ids"]) <= set(receipt["ordered_ids"])):
            raise ValueError("材料入学回执无效。")
        if 'abilities' in receipt:
            validate_selection(receipt['abilities'], receipt['target_ids'])
    extras = value.get('additional_enrollments', [])
    if not isinstance(extras, list) or len(extras) > 48:
        raise ValueError('材料能力追加回执无效。')
    seen_abilities = set()
    for receipt in [*extras, *([value['enrollment']] if value['enrollment'] and 'added_abilities' in value['enrollment'] else [])]:
        if (not isinstance(receipt, dict) or set(receipt) - {'study_scope_sha256'} != {'at', 'target_ids', 'ordered_ids',
                'added_ids', 'plan_sha256', 'abilities', 'added_abilities'}
                or not receipt_scope(receipt) or not isinstance(receipt['added_abilities'], dict)
                or not (receipt['added_abilities'] or receipt['added_ids']) or not isinstance(receipt['ordered_ids'], list)
                or not isinstance(receipt['added_ids'], list)
                or any(not isinstance(cid, str) for cid in [*receipt['ordered_ids'], *receipt['added_ids']])
                or not set(receipt['target_ids']) <= set(receipt['ordered_ids'])
                or not set(receipt['added_ids']) <= set(receipt['ordered_ids'])
                or not isinstance(receipt['plan_sha256'], str) or len(receipt['plan_sha256']) != 64
                or not isinstance(receipt['at'], str) or datetime.fromisoformat(receipt['at']).tzinfo is None):
            raise ValueError('材料能力追加回执无效。')
        validate_selection(receipt['abilities'], receipt['target_ids'])
        for cid, added in receipt['added_abilities'].items():
            if (cid not in receipt['abilities'] or not isinstance(added, list) or not added
                    or any(not isinstance(a, str) for a in added) or len(set(added)) != len(added)
                    or not set(added) <= set(receipt['abilities'][cid])):
                raise ValueError('材料追加能力不在所选范围。')
            for ability in added:
                if (cid, ability) in seen_abilities:
                    raise ValueError('材料能力重复追加。')
                seen_abilities.add((cid, ability))
    return deepcopy(value)


def validate_selection(selection, target_ids, items=None):
    if (not isinstance(selection, dict) or set(selection) != set(target_ids)
            or any(not isinstance(values, list) or not values
                   or any(not isinstance(value, str) for value in values)
                   or len(set(values)) != len(values) for values in selection.values())):
        raise ValueError('请为每项内容选择至少一种学习能力。')
    if items is not None:
        by_id = {item['id']: item for item in items}
        if any(not set(values) <= set(by_id[cid]['abilities']) for cid, values in selection.items()):
            raise ValueError('所选能力不属于当前教案。')
    return deepcopy(selection)


def write_in_transaction(db, state):
    if not db.in_transaction:
        raise RuntimeError("Material study requires caller transaction")
    state = validate_state(state)
    db.execute("INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (PREFIX + state["id"], dump(state)))


class MaterialStudyStore:
    def __init__(self, store, learning, *, repo=None, examples=None):
        self.store, self.db, self.learning = store, store._db, learning
        self.repo, self.examples = repo, examples
        from .material_qualification import bind_resources
        bind_resources(store, repo=repo, examples=examples)

    def get(self, identity):
        value = self.store.get_setting(PREFIX + identity, None)
        return validate_state(value) if value is not None else None

    def for_diagnostic(self, identity):
        row = self.db.execute("SELECT value_json FROM app_setting WHERE key GLOB ? AND json_extract(value_json,'$.diagnostic_id')=? LIMIT 1",
                              (PREFIX + "*", identity)).fetchone()
        if row:
            return validate_state(json.loads(row[0]))
        from .material_pretest import PREFIX as PRETEST_PREFIX, validate
        row = self.db.execute("SELECT value_json FROM app_setting WHERE key GLOB ? AND json_extract(value_json,'$.diagnostic_id')=? LIMIT 1",
                              (PRETEST_PREFIX + '*', identity)).fetchone()
        return validate(json.loads(row[0])) if row else None

    def _material(self, identity):
        from .ai_materials import AIMaterialStore
        job = MaterialWorkflowStore(self.store).get(identity)
        proposal = AIMaterialStore(self.store).get(job["proposal_id"]) if job and job["proposal_id"] else None
        if not proposal:
            raise ValueError("材料已不可用。")
        report = MaterialVerificationStore(self.store).current(identity, proposal, repo=self.repo, examples=self.examples)
        if not report:
            raise ValueError("请先核对本地资料。")
        return proposal, report

    def available_targets(self, identity):
        from .assessment_policy import teaching_allowed
        from .material_eligibility import current_targets
        self._material(identity)
        # Reuse canonical lessons unchanged. Unknown senses and unreviewed
        # generated questions cannot become a new parallel memory unit.
        return [item for item in current_targets(self.store, identity, repo=self.repo, examples=self.examples) if teaching_allowed(item, store=self.store)]

    def dependency_groups(self, identity, *, enrollment=False, new_target_ids=()):
        """Only a group's own missing prerequisite blocks that group."""
        targets = self.available_targets(identity)
        ready, blocked = [], []
        for item in targets:
            try:
                plan = self.plan_targets([item], catalog_targets=targets)
            except ValueError:
                blocked.append({'target_id': item['id'], 'reason': 'prerequisite_unavailable'})
                continue
            if enrollment:
                missing = [row for dependency in plan['items']
                           if (row := self._admission_block(dependency, new_target_ids=new_target_ids))]
                if missing:
                    blocked.append({**missing[0], 'target_id': item['id'],
                                    'dependency_id': missing[0]['target_id']})
                    continue
            ready.append(item)
        return {'targets': ready, 'blocked': blocked, 'catalog': targets}

    def _plan(self, identity, *, enrollment=False, new_target_ids=()):
        groups = self.dependency_groups(identity, enrollment=enrollment, new_target_ids=new_target_ids)
        return self.plan_targets(groups['targets'], catalog_targets=groups['catalog'])

    def plan_targets(self, targets, *, catalog_targets=()):
        """Current additive prerequisite closure for explicitly chosen sources."""
        from .learning_content import learning_items
        from .learning_dependencies import build_dependency_plan
        from .assessment_policy import teaching_allowed
        if not targets:
            raise ValueError("暂没有可以可靠关联的学习项，可先保留待核材料。")
        catalog = {row["id"]: row for row in learning_items()}
        catalog.update({row['id']: row for row in catalog_targets})
        catalog.update({row["id"]: row for row in targets})
        from .learning_dependencies import effective_dependencies
        from types import SimpleNamespace
        saved = self.learning.list_items()
        by_id = {row['id']: row for row in targets}
        projected = []
        for row in saved:
            target = by_id.get(row['id'])
            if target and set(target['abilities']) - set(row['enrolled_abilities']):
                row = {**row, 'depends_on': effective_dependencies(self.learning,
                    {**row, 'checkpoint': {}}, preserved=False)}
            projected.append(row)
        view = SimpleNamespace(_db=self.db, queries=self.learning.queries, list_items=lambda: projected, get_item=self.learning.get_item)
        plan = build_dependency_plan([row["id"] for row in targets], list(catalog.values()), view)
        if len(plan["items"]) > 128 or any(not teaching_allowed(row, store=self.store) for row in plan["items"]):
            raise ValueError("所需前置材料仍待核验或范围过大，请分批处理。")
        return plan

    def teaching_targets(self, identity):
        from .material_qualification import fact_lessons, candidate_basis
        canonical = self.available_targets(identity)
        ids = {item['id'] for item in canonical}
        canonical = self.dependency_groups(identity)['targets']
        bases = {row['item']['id']: row for row in candidate_basis(self.store, identity, repo=self.repo, examples=self.examples)}
        facts = [item for item in fact_lessons(self.store, identity, repo=self.repo, examples=self.examples)
                 if bases[item['id']]['source']['object_id'] not in ids]
        return [*canonical, *facts][:12]

    def _preview_plan(self, identity):
        targets = self.teaching_targets(identity)
        if not targets:
            raise ValueError('暂没有可可靠说明的内容，可先保留待核材料。')
        canonical = [item for item in targets if not item.get('material_fact_scope')]
        plan = self._plan(identity) if canonical else {'items': [], 'ready_ids': [], 'target_ids': []}
        facts = [item for item in targets if item.get('material_fact_scope')]
        if len(plan['items']) + len(facts) > 128:
            raise ValueError('本批前置和介绍较多，请分批处理。')
        return {**plan, 'items': [*plan['items'], *facts], 'target_ids': [item['id'] for item in targets]}

    def prepare(self, identity):
        old = self.get(identity)
        if old:
            return old
        proposal, _ = self._material(identity)
        plan = self._preview_plan(identity)
        # Familiar prerequisites need not be read again; selected targets are
        # retained so a requested explanation always has a visible checkpoint.
        lessons = [item for item in plan["items"] if item["id"] not in plan["ready_ids"] or item["id"] in plan["target_ids"]]
        value = {"format": FORMAT, "version": 1, "id": identity, "proposal_id": proposal["id"],
            "proposal_sha256": digest(proposal_content(proposal)), "target_ids": plan["target_ids"],
            "lessons": [{"item": deepcopy(item), "sha256": digest(item), "presented_at": None,
                         "completed_at": None, "exposures": {}} for item in lessons],
            "diagnostic_id": None, "enrollment": None, "created_at": app_clock.utc_now().isoformat()}
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if existing := self.get(identity):
                return existing
            write_in_transaction(self.db, value)
        return value

    def _current_lesson(self, state):
        return next((row for row in state["lessons"] if row["completed_at"] is None), None)

    def extension_preview(self, identity):
        state = self.get(identity)
        if state is None:
            return None
        plan = self._preview_plan(identity)
        added_targets = [cid for cid in plan['target_ids'] if cid not in state['target_ids']]
        if not added_targets:
            return None
        existing = {row['item']['id'] for row in state['lessons']}
        lessons = [item for item in plan['items'] if item['id'] not in existing
                   and (item['id'] not in plan['ready_ids'] or item['id'] in added_targets)]
        return {'id': identity, 'state_sha256': digest(state), 'added_target_ids': added_targets,
                'lessons': deepcopy(lessons)}

    def extend(self, identity, expected):
        """Explicit append; old teaching checkpoints and receipts stay intact."""
        request_sha = digest(expected)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            state = self.get(identity)
            if state and any(row['request_sha256'] == request_sha for row in state.get('scope_history', [])):
                return state
            if not expected or self.extension_preview(identity) != expected:
                raise ValueError('材料或阅读进度已变化，请重新查看追加范围。')
            state.setdefault('scope_history', []).append({'target_ids': list(state['target_ids']),
                'lesson_ids': [row['item']['id'] for row in state['lessons']],
                'at': app_clock.utc_now().isoformat(), 'request_sha256': request_sha})
            state['target_ids'].extend(expected['added_target_ids'])
            state['lessons'].extend({'item': deepcopy(item), 'sha256': digest(item), 'presented_at': None,
                'completed_at': None, 'exposures': {}} for item in expected['lessons'])
            write_in_transaction(self.db, state)
        return state

    def lesson_revision_preview(self, identity):
        state = self.get(identity)
        row = self._current_lesson(state) if state else None
        if row is None: return None
        from .material_review import presentation_fingerprint
        from .assessment_policy import teaching_allowed
        current = next((item for item in self._preview_plan(identity)['items'] if item['id'] == row['item']['id']), None)
        if not current or not (current.get('material_fact_scope') or teaching_allowed(current, store=self.store)):
            return None
        if digest(current) == row['sha256']: return None
        # Runtime-only metadata does not require a new teaching checkpoint.
        if (presentation_fingerprint(current) == presentation_fingerprint(row['item'])
                and current.get('material_fact_scope') == row['item'].get('material_fact_scope')):
            return None
        return {'id': identity, 'state_sha256': digest(state), 'index': state['lessons'].index(row), 'item': current}

    def refresh_lesson(self, identity, expected):
        request_sha = digest(expected)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE'); state = self.get(identity)
            if state and any(row['request_sha256'] == request_sha for row in state.get('lesson_revisions', [])):
                return state
            if not expected or self.lesson_revision_preview(identity) != expected:
                raise ValueError('讲解依据或阅读位置已变化，请重新打开。')
            index = expected['index']; item = expected['item']
            state.setdefault('lesson_revisions', []).append({'index': index, 'before': deepcopy(state['lessons'][index]),
                'replacement_sha256': digest(item), 'request_sha256': request_sha, 'at': app_clock.utc_now().isoformat()})
            state['lessons'][index] = {'item': deepcopy(item), 'sha256': digest(item),
                'presented_at': None, 'completed_at': None, 'exposures': {}}
            write_in_transaction(self.db, state)
        return state

    def present(self, identity, *, surface="teaching"):
        from .learning_content import get_learning_item
        from .retention_session import DiagnosticStore
        if surface not in {"teaching", "tts"}:
            raise ValueError("不支持的材料呈现方式。")
        state = self.prepare(identity)
        row = self._current_lesson(state)
        if row is None:
            return state
        if row['item'].get('material_fact_scope'):
            from .material_qualification import fact_lesson_current
            if not fact_lesson_current(self.store, row['item'], repo=self.repo, examples=self.examples):
                raise ValueError('相关字段依据或使用许可已变化，原阅读进度已保留。')
            current = row['item']
        else:
            available = {item["id"]: item for item in self.available_targets(identity)}
            current = (available.get(row["item"]["id"]) if row["item"]["id"] in state["target_ids"]
                       else get_learning_item(row["item"]["id"]))
            if current is None and row['item']['id'].startswith('local:ai:'):
                from .material_reference import item_ready
                saved = self.learning.get_item(row['item']['id'])
                if saved and item_ready(self.store, saved): current = saved
        # Enrolled snapshots carry learner-only keys; compare the actual
        # teaching fields rather than replacing their learning checkpoints.
        from .material_review import presentation_fingerprint
        if current is None or presentation_fingerprint(current) != presentation_fingerprint(row["item"]):
            raise ValueError("相关材料已更新，请重新核对；原阅读进度已保留。")
        if surface == 'tts' and current.get('material_fact_scope') and not current.get('reading'):
            raise ValueError('该读音尚待核实。')
        abilities = [(current["id"], ability) for ability in current.get("abilities", ("understanding",))]
        DiagnosticStore(self.store, self.learning).expose_visible(abilities)
        now = app_clock.utc_now().isoformat()
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            latest = self.get(identity)
            pending = self._current_lesson(latest)
            if pending is None or pending["item"]["id"] != current["id"]:
                raise ValueError("阅读位置已改变，请重新打开。")
            pending["presented_at"] = pending["presented_at"] or now
            pending["exposures"][surface] = now
            write_in_transaction(self.db, latest)
        return latest

    def advance(self, identity, target_id):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            state = self.get(identity)
            row = self._current_lesson(state) if state else None
            if row is None or row["item"]["id"] != target_id:
                return state  # Repeated old button cannot skip another lesson.
            if not row["presented_at"]:
                raise ValueError("请先查看当前讲解。")
            if row['item'].get('material_fact_scope'):
                from .material_qualification import fact_lesson_current
                if not fact_lesson_current(self.store, row['item'], repo=self.repo, examples=self.examples):
                    raise ValueError('相关字段依据或使用许可已变化，原阅读位置已保留。')
            else:
                available = {item['id'] for item in self.available_targets(identity)}
                if not available or target_id in state['target_ids'] and target_id not in available:
                    raise ValueError('本批材料已停止使用，原阅读位置已保留。')
            row["completed_at"] = app_clock.utc_now().isoformat()
            write_in_transaction(self.db, state)
        return state

    def start_pretest(self, identity, enabled, *, append=False):
        from .material_pretest import start
        return start(self, identity, enabled, append=append)

    def _admission_block(self, item, *, new_target_ids=()):
        existing = self.learning.get_item(item['id'])
        if item['id'].startswith('local:ai:'):
            from .material_reference import admission_readiness
            readiness = admission_readiness(self.store, item)
            return None if readiness['ready'] else {'target_id': item['id'], 'reason': readiness['reason'],
                'example_count': readiness['example_count'], 'minimum_examples': 10}
        if not item.get('dictionary_only') or (item['id'] not in new_target_ids
                and existing and set(item['abilities']) <= set(existing['enrolled_abilities'])):
            return None
        # Activation of a personal reference is not a semantic certificate.
        bundled = (self.examples.evidence_pack() if callable(getattr(self.examples, 'evidence_pack', None)) else self.examples.bundled) if self.examples is not None else {"examples": [], "references": []}
        texts = {row['id']: row['text'] for row in bundled['examples']}
        examples = {texts[row['example_id']] for row in bundled['references']
                    if row['target_id'] == item['id'] and row['example_id'] in texts}
        return None if len(examples) >= 10 else {'target_id': item['id'],
                                               'example_count': len(examples), 'minimum_examples': 10}

    def enrollment_readiness(self, identity, *, new_target_ids=()):
        groups = self.dependency_groups(identity, enrollment=True, new_target_ids=new_target_ids)
        blocked = groups['blocked']
        partial = [item for item in self.teaching_targets(identity) if item.get('material_fact_scope')]
        blocked.extend({'target_id': item['id'], 'reason': 'partial_fact_preview', 'minimum_examples': 10} for item in partial)
        return {'ready': bool(groups['targets']), 'blocked': blocked,
                'target_ids': [item['id'] for item in groups['targets']]}

    def _require_enrollment_ready(self, identity, *, new_target_ids=()):
        if not self.enrollment_readiness(identity, new_target_ids=new_target_ids)["ready"]:
            raise ValueError("可先检查或查看已核说明；加入前还需完成相应能力的核验和教案，包括十条已核关联例句。")

    def enrollment_preview(self, identity, selected_abilities=None):
        state = self.prepare(identity)
        self._require_enrollment_ready(identity)
        plan = self._plan(identity, enrollment=True)
        if not set(plan['target_ids']) <= set(state['target_ids']):
            raise ValueError('请先追加新核实内容，再查看加入范围。')
        preview = {"target_ids": plan["target_ids"], "ordered_ids": plan["ordered_ids"],
                "to_enroll": plan["to_enroll"], "plan_sha256": digest(plan["items"])}
        if selected_abilities is not None:
            preview['abilities'] = validate_selection(selected_abilities, plan['target_ids'], plan['items'])
        return preview

    def enroll(self, identity, expected):
        state = self.prepare(identity)
        selection = expected.get('abilities')
        additions = {}
        if selection is not None:
            validate_selection(selection, expected['target_ids'])
            for cid, values in selection.items():
                item = self.learning.get_item(cid)
                if item:
                    missing = [a for a in values if a not in item['enrolled_abilities']]
                    if missing:
                        additions[cid] = missing
        if state["enrollment"]:
            if not additions and all(self.learning.get_item(cid) for cid in expected['target_ids']):
                saved_receipts = [state['enrollment'], *state.get('additional_enrollments', [])]
                matched = next((row for row in reversed(saved_receipts)
                    if row['target_ids'] == expected['target_ids']
                    and (selection is None or row.get('abilities') == selection)), None)
                if matched:
                    return matched
        self._require_enrollment_ready(identity)
        plan = self._plan(identity, enrollment=True)
        preview = {"target_ids": plan["target_ids"], "ordered_ids": plan["ordered_ids"],
                   "to_enroll": plan["to_enroll"], "plan_sha256": digest(plan["items"])}
        if 'abilities' in expected:
            preview['abilities'] = validate_selection(expected['abilities'], plan['target_ids'], plan['items'])
        if preview != expected or not set(preview["target_ids"]) <= set(state["target_ids"]):
            raise ValueError("材料或学习计划已变化，请重新查看加入范围。")
        current_items = {item['id']: item for item in self.available_targets(identity)}
        for cid in additions:
            # An old canonical ID is not permission to use a newly changed
            # template under the old revision. Activation belongs to S5.
            original = json.loads(self.db.execute('SELECT content_json FROM learning_content WHERE content_id=?', (cid,)).fetchone()[0])
            fields = set(original) - {'depends_on'}
            from .personal_content_versions import original_source
            current_base = original_source(self.store, current_items[cid])
            from .learning_dependencies import effective_dependencies, dependencies_of
            fresh_dependencies = effective_dependencies(self.learning, {**original, 'checkpoint': {}}, preserved=False)
            if (digest({k: current_base.get(k) for k in fields}) != digest({k: original[k] for k in fields})
                    or not set(dependencies_of(current_items[cid])) <= set(fresh_dependencies)):
                raise ValueError('新能力的教案版本与原记录不同，请先核对内容修订。')
        def receipt(db, added):
            self._require_enrollment_ready(identity, new_target_ids={*plan['to_enroll'], *additions})
            current = self.enrollment_targets_in_transaction(identity)
            if current != plan["target_ids"]:
                raise ValueError("核验依据已改变，尚未加入学习。")
            latest = self.get(identity)
            if latest != state:
                raise ValueError('材料阅读范围已改变，请重新查看加入范围。')
            saved = {"at": app_clock.utc_now().isoformat(), "target_ids": plan["target_ids"],
                "ordered_ids": plan["ordered_ids"], "added_ids": list(added), "plan_sha256": preview["plan_sha256"],
                "study_scope_sha256": digest(state['target_ids'])}
            if 'abilities' in preview:
                saved['abilities'] = preview['abilities']
            if additions:
                saved['added_abilities'] = additions
            if latest['enrollment']:
                saved.setdefault('abilities', {item['id']: list(item['abilities']) for item in plan['items']
                                               if item['id'] in plan['target_ids']})
                saved.setdefault('added_abilities', {})
                latest.setdefault('additional_enrollments', []).append(saved)
            else:
                latest['enrollment'] = saved
            write_in_transaction(db, latest)
        # The reviewed prerequisite closure is additive. Existing authored
        # snapshots remain byte-for-byte owned by their original introduction.
        admission_items = [json.loads(self.db.execute('SELECT content_json FROM learning_content WHERE content_id=?',
            (item['id'],)).fetchone()[0]) if item['id'] in additions else item for item in plan['items']]
        self.learning.enroll(admission_items, selected_abilities=preview.get('abilities'), _after_enroll_with_ids=receipt)
        latest = self.get(identity)
        return latest.get('additional_enrollments', [])[-1] if state['enrollment'] else latest['enrollment']

    def enrollment_targets_in_transaction(self, identity):
        # Re-read the report and current references on the same connection as
        # enrollment; user/model-supplied cached metadata cannot grant entry.
        self._require_enrollment_ready(identity)
        return self._plan(identity, enrollment=True)['target_ids']


def validate_database(db):
    from .material_workflow import validate_job, PREFIX as WORKFLOW_PREFIX
    from .retention_session import validate_episode, PREFIX as DIAGNOSTIC_PREFIX
    for key, raw in db.execute("SELECT key,value_json FROM app_setting WHERE key GLOB 'material_study_*'"):
        state = validate_state(json.loads(raw))
        raw_job = db.execute("SELECT value_json FROM app_setting WHERE key=?", (WORKFLOW_PREFIX + state["id"],)).fetchone()
        if key != PREFIX + state["id"] or not raw_job or validate_job(json.loads(raw_job[0]))["proposal_id"] != state["proposal_id"]:
            raise ValueError("材料学习记录缺少原任务。")
        from .ai_materials import SETTING, empty_state, validate_ai_material_state
        raw_materials = db.execute('SELECT value_json FROM app_setting WHERE key=?', (SETTING,)).fetchone()
        proposals = validate_ai_material_state(json.loads(raw_materials[0]) if raw_materials else empty_state())['proposals']
        proposal = next((p for p in proposals if p['id'] == state['proposal_id']), None)
        if not proposal or digest(proposal_content(proposal)) != state['proposal_sha256']:
            raise ValueError('材料介绍与原始提案版本不一致。')
        job = validate_job(json.loads(raw_job[0]))
        by_candidate = {job['candidate_ids'][index]: item for index, item in enumerate(proposal['items'])}
        for lesson in state['lessons']:
            item = lesson['item']
            if not item.get('material_fact_scope'):
                continue
            scope = item['material_fact_scope']
            from .material_qualification import CORE
            candidate = by_candidate.get(item['id'])
            if (not isinstance(scope, dict) or set(scope) != {'version', 'workflow_id', 'candidate_object_id',
                    'candidate_id', 'source_kind', 'source_locator', 'facets'} or type(scope['version']) is not int or scope['version'] != 1
                    or scope['source_kind'] not in {'course_item', 'dictionary_sense'}
                    or not isinstance(scope['source_locator'], dict) or set(scope['source_locator']) !=
                       ({'course_id'} if scope['source_kind'] == 'course_item' else {'word_id', 'sense_id'})
                    or any(not isinstance(value, str) or not 0 < len(value) <= 1024 for value in scope['source_locator'].values())
                    or not candidate or scope['workflow_id'] != state['id'] or scope['candidate_id'] != item['id']
                    or scope['candidate_object_id'] != candidate['id'] or not isinstance(scope['facets'], dict)
                    or not set(CORE) <= set(scope['facets']) <= {*CORE, 'reading'}
                    or any(digest(candidate.get(field, '')) != sha for field, sha in scope['facets'].items())
                    or any(item.get(field, '') != candidate.get(field, '') for field in CORE)
                    or item.get('reading', '') != (candidate['reading'] if 'reading' in scope['facets'] else '')
                    or item['abilities'] != ['understanding'] + (['reading'] if item.get('reading') else [])):
                raise ValueError('分项介绍缺少原候选或字段范围不匹配。')
        if state["diagnostic_id"]:
            row = db.execute("SELECT value_json FROM app_setting WHERE key=?", (DIAGNOSTIC_PREFIX + state["diagnostic_id"],)).fetchone()
            episode = validate_episode(json.loads(row[0])) if row else None
            scopes = [row['target_ids'] for row in state.get('scope_history', [])] + [state['target_ids']]
            if not episode or episode["intent"] != "material" or episode["scope_ids"] not in scopes:
                raise ValueError("材料学习记录与检查范围不一致。")
            if episode.get("material_id") is not None and episode["material_id"] != state["id"]:
                raise ValueError("材料检查绑定了其他原文。")
        if state["enrollment"]:
            for receipt in [state['enrollment'], *state.get('additional_enrollments', [])]:
                for cid in receipt['ordered_ids']:
                    if not db.execute('SELECT 1 FROM learning_content WHERE content_id=?', (cid,)).fetchone():
                        raise ValueError('材料加入回执缺少学习项。')
                for cid, selected in receipt.get('abilities', {}).items():
                    actual = {row[0] for row in db.execute('SELECT ability FROM learning_unit WHERE content_id=?', (cid,))}
                    if not set(selected) <= actual:
                        raise ValueError('材料入学回执缺少所选能力。')
                for cid, added in receipt.get('added_abilities', {}).items():
                    from .ability_teaching import read
                    teaching = read(db, cid)
                    if not teaching or not set(added) <= set(teaching['additions']):
                        raise ValueError('材料追加回执缺少独立初学记录。')
