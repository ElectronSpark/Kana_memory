"""Explicit candidate-to-canonical ability admission, never score promotion.

Candidate lessons/checks keep their identities. A receipt admits only selected
attested abilities using the current canonical lesson, not the AI explanation.
"""
from copy import deepcopy
from datetime import datetime
import json
import re
from uuid import uuid4

from . import app_clock
from .material_workflow import digest, dump, MaterialWorkflowStore
from .material_qualification import CORE, candidate_basis, meaning_supported

PREFIX = 'material_admission_v1:'


def _hash(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _source_hash(item):
    # Prerequisites are proved separately; enrollment retains its original
    # authored snapshot and may have already frozen an earlier dependency list.
    return digest({key: value for key, value in item.items() if key != 'depends_on'})


def _dependencies_hash(plan):
    return digest([(item['id'], item.get('depends_on', [])) for item in plan['items']])


def _examples(study, cid):
    pack = (study.examples.evidence_pack() if callable(getattr(study.examples, 'evidence_pack', None)) else study.examples.bundled) if study.examples is not None else {'examples': [], 'references': []}
    texts = {row['id']: row for row in pack['examples']}
    rows = [row for row in pack['references'] if row['target_id'] == cid and row['example_id'] in texts]
    return {'count': len({texts[row['example_id']]['text'] for row in rows}),
            'sha256': digest([[row, texts[row['example_id']]] for row in sorted(rows, key=dump)])}


def _edges(study, identity):
    from .assessment_policy import teaching_allowed
    from .learning_questions import question_kinds, build_learning_question
    from .learning_store import _unit_id
    result = []
    for basis in candidate_basis(study.store, identity, repo=study.repo, examples=study.examples):
        item = basis['known']
        # The meaning of a word can be attested separately from its reading.
        # Matching a grammar title/summary alone does not prove its rule.
        if item.get('family') != 'word' or not teaching_allowed(item, store=study.store):
            continue
        evidence = _examples(study, item['id'])
        kinds = question_kinds(item)
        for ability, kind in (('understanding', 'vocab_meaning'), ('reading', 'vocab_reading')):
            if (ability not in item['abilities'] or kind not in kinds
                    or ability == 'reading' and 'reading' not in basis['facets']):
                continue
            fields = (*CORE, 'reading') if ability == 'reading' else CORE
            edge = {'candidate_object_id': basis['candidate']['id'], 'candidate_id': basis['item']['id'],
                'candidate_revision': basis['item']['revision'], 'ability': ability,
                'canonical_id': item['id'], 'canonical_revision': str(item['revision']),
                'unit_id': _unit_id(item['id'], ability), 'source_kind': basis['source']['kind'],
                'source_locator': basis['locator'], 'facets': {key: basis['facets'][key] for key in fields},
                'source_sha256': _source_hash(item), 'question_sha256': digest(build_learning_question(item, kind)),
                'examples_sha256': evidence['sha256']}
            result.append({'edge': edge, 'item': item, 'example_count': evidence['count'],
                           'ready': bool(study.learning.get_unit(item['id'], ability)) or evidence['count'] >= 10})
    return result


class MaterialAdmissionStore:
    def __init__(self, study):
        self.study, self.store, self.db, self.learning = study, study.store, study.db, study.learning

    def options(self, identity):
        rows = {}
        for row in _edges(self.study, identity):
            edge, item = row['edge'], row['item']
            entry = rows.setdefault(edge['candidate_id'], {'candidate_id': edge['candidate_id'], 'item': item,
                'abilities': [], 'blocked_abilities': [], 'example_count': row['example_count']})
            entry['abilities' if row['ready'] else 'blocked_abilities'].append(edge['ability'])
        return list(rows.values())

    def preview(self, identity, selection, *, request_id=None):
        if (not isinstance(selection, dict) or not selection or len(selection) > 12
                or any(not isinstance(cid, str) or not isinstance(values, list) or not values
                    or any(value not in ('understanding', 'reading') for value in values)
                    or len(values) != len(set(values)) for cid, values in selection.items())):
            raise ValueError('请选择已核实的学习能力。')
        wanted = {(cid, ability) for cid, values in selection.items() for ability in values}
        rows = [row for row in _edges(self.study, identity)
                if (row['edge']['candidate_id'], row['edge']['ability']) in wanted]
        if len(rows) != len(wanted) or any(not row['ready'] for row in rows):
            raise ValueError('所选能力仍待核验，或尚缺十条已核关联例句。')
        edges = sorted((row['edge'] for row in rows), key=lambda row: (row['candidate_id'], row['ability']))
        items = {row['item']['id']: row['item'] for row in rows}
        self._check_existing(items, edges)
        plan = self.study.plan_targets(list(items.values()))
        proposal = MaterialWorkflowStore(self.store).get(identity)
        request_id = request_id or uuid4().hex
        if not isinstance(request_id, str) or not re.fullmatch('[0-9a-f]{32}', request_id):
            raise ValueError('入学请求标识无效。')
        from .ai_materials import AIMaterialStore
        from .material_verification import proposal_content
        raw = AIMaterialStore(self.store).get(proposal['proposal_id'])
        return {'version': 1, 'id': digest([identity, request_id]), 'request_id': request_id,
                'workflow_id': identity, 'proposal_id': proposal['proposal_id'],
                'proposal_sha256': digest(proposal_content(raw)), 'mappings': edges,
                'ordered_ids': plan['ordered_ids'], 'to_enroll': plan['to_enroll'],
                'plan_sha256': digest(plan['items']), 'dependencies_sha256': _dependencies_hash(plan)}

    def _check_existing(self, items, edges):
        from .learning_dependencies import effective_dependencies, dependencies_of
        for cid, item in items.items():
            saved = self.db.execute('SELECT content_json FROM learning_content WHERE content_id=?', (cid,)).fetchone()
            if saved:
                original = json.loads(saved[0])
                dependencies = effective_dependencies(self.learning, {**original, 'checkpoint': {}}, preserved=False)
                if _source_hash(original) != _source_hash(item) or not set(dependencies_of(item)) <= set(dependencies):
                    raise ValueError('教案版本与已学记录不同，请先核对内容修订。')
        # Do not silently retarget a candidate after a canonical identity change.
        for edge in edges:
            for raw in self.db.execute("SELECT e.value FROM app_setting s, json_each(s.value_json,'$.mappings') e "
                    "WHERE s.key GLOB ? AND json_extract(e.value,'$.candidate_id')=? "
                    "AND json_extract(e.value,'$.ability')=?", (PREFIX + '*', edge['candidate_id'], edge['ability'])):
                old = json.loads(raw[0])
                if (old['canonical_id'], old['canonical_revision']) != (edge['canonical_id'], edge['canonical_revision']):
                    raise ValueError('这项候选的正式内容已变化，请先核对内容修订。')

    def get(self, receipt_id):
        raw = self.store.get_setting(PREFIX + receipt_id, None)
        return validate(raw) if raw is not None else None

    def enroll(self, expected):
        # A retry is identified before rereading sources: stopping source use
        # after a successful admission does not revoke the user's formal work.
        request_hash = digest(expected)
        old = self.get(expected.get('id', ''))
        if old:
            if old['request_sha256'] != request_hash:
                raise ValueError('同一入学请求不能改为其他范围。')
            return old
        selection = {}
        for edge in expected.get('mappings', []):
            selection.setdefault(edge['candidate_id'], []).append(edge['ability'])
        identity = expected.get('workflow_id')
        current = self.preview(identity, selection, request_id=expected.get('request_id'))
        if current != expected:
            raise ValueError('材料依据或加入范围已变化，请重新查看。')
        rows = _edges(self.study, identity)
        target_ids = {edge['canonical_id'] for edge in expected['mappings']}
        items = {row['item']['id']: row['item'] for row in rows if row['item']['id'] in target_ids}
        plan = self.study.plan_targets(list(items.values()))
        chosen = {cid: sorted({edge['ability'] for edge in expected['mappings'] if edge['canonical_id'] == cid})
                  for cid in target_ids}
        additions = {cid: [ability for ability in values if not self.learning.get_unit(cid, ability)]
                     for cid, values in chosen.items() if self.learning.get_item(cid)}
        additions = {cid: values for cid, values in additions.items() if values}
        # Same transaction and source checks as enrollment, with no pretest
        # results passed to the scheduler or rewritten into formal questions.
        def receipt(db, added):
            fresh = _edges(self.study, identity)
            by_edge = {(row['edge']['candidate_id'], row['edge']['ability']): row for row in fresh}
            for edge in expected['mappings']:
                row = by_edge.get((edge['candidate_id'], edge['ability']))
                if row is None or row['edge'] != edge or ((edge['canonical_id'] in added
                        or edge['ability'] in additions.get(edge['canonical_id'], [])) and row['example_count'] < 10):
                    raise ValueError('核验依据已改变，尚未加入学习。')
            fresh_items = {row['item']['id']: row['item'] for row in fresh if row['item']['id'] in target_ids}
            fresh_plan = self.study.plan_targets(list(fresh_items.values()))
            if (fresh_plan['ordered_ids'] != expected['ordered_ids']
                    or _dependencies_hash(fresh_plan) != expected['dependencies_sha256']):
                raise ValueError('前置教案已变化，尚未加入学习。')
            value = {'version': 1, 'id': expected['id'], 'request_id': expected['request_id'],
                'workflow_id': identity, 'proposal_id': expected['proposal_id'],
                'proposal_sha256': expected['proposal_sha256'], 'request_sha256': request_hash,
                'at': app_clock.utc_now().isoformat(), 'mappings': expected['mappings'],
                'ordered_ids': expected['ordered_ids'], 'added_ids': list(added),
                'added_abilities': additions, 'plan_sha256': expected['plan_sha256'],
                'dependencies_sha256': expected['dependencies_sha256']}
            db.execute('INSERT INTO app_setting VALUES (?,?)', (PREFIX + value['id'], dump(validate(value))))
        admission_items = [json.loads(self.db.execute('SELECT content_json FROM learning_content WHERE content_id=?',
            (item['id'],)).fetchone()[0]) if item['id'] in additions else item for item in plan['items']]
        self.learning.enroll(admission_items, selected_abilities=chosen, _after_enroll_with_ids=receipt)
        return self.get(expected['id'])


def validate(value):
    fields = {'version', 'id', 'request_id', 'workflow_id', 'proposal_id', 'proposal_sha256', 'request_sha256',
              'at', 'mappings', 'ordered_ids', 'added_ids', 'added_abilities', 'plan_sha256', 'dependencies_sha256'}
    if (not isinstance(value, dict) or set(value) != fields or type(value['version']) is not int or value['version'] != 1
            or any(not _hash(value[key]) for key in ('id', 'workflow_id', 'proposal_sha256', 'request_sha256', 'plan_sha256', 'dependencies_sha256'))
            or not isinstance(value['request_id'], str) or not re.fullmatch('[0-9a-f]{32}', value['request_id'])
            or value['id'] != digest([value['workflow_id'], value['request_id']])
            or not isinstance(value['proposal_id'], str) or not 1 <= len(value['proposal_id']) <= 256
            or not isinstance(value['at'], str) or datetime.fromisoformat(value['at']).tzinfo is None
            or not isinstance(value['mappings'], list) or not 1 <= len(value['mappings']) <= 24
            or not isinstance(value['added_abilities'], dict) or len(dump(value).encode()) > 256 * 1024):
        raise ValueError('材料能力入学回执无效。')
    for key in ('ordered_ids', 'added_ids'):
        values = value[key]
        if (not isinstance(values, list) or len(values) > 128 or any(not isinstance(v, str) or not 1 <= len(v) <= 1024 for v in values)
                or len(set(values)) != len(values)):
            raise ValueError('材料能力前置回执无效。')
    if not set(value['added_ids']) <= set(value['ordered_ids']):
        raise ValueError('材料能力新增范围无效。')
    seen, selected = set(), {}
    from .learning_store import _unit_id
    for edge in value['mappings']:
        required = {'candidate_object_id', 'candidate_id', 'candidate_revision', 'ability', 'canonical_id',
            'canonical_revision', 'unit_id', 'source_kind', 'source_locator', 'facets',
            'source_sha256', 'question_sha256', 'examples_sha256'}
        if (not isinstance(edge, dict) or set(edge) != required
                or any(not isinstance(edge[key], str) or not 1 <= len(edge[key]) <= 1024 for key in required - {'facets', 'source_locator'})
                or not re.fullmatch('material-candidate:[0-9a-f]{64}', edge['candidate_id'])
                or edge['ability'] not in {'understanding', 'reading'}
                or edge['source_kind'] not in {'course_item', 'dictionary_sense'}
                or not isinstance(edge['source_locator'], dict) or set(edge['source_locator']) !=
                   ({'course_id'} if edge['source_kind'] == 'course_item' else {'word_id', 'sense_id'})
                or any(not isinstance(v, str) or not 1 <= len(v) <= 1024 for v in edge['source_locator'].values())
                or not isinstance(edge['facets'], dict) or set(edge['facets']) != set((*CORE, 'reading') if edge['ability'] == 'reading' else CORE)
                or any(not _hash(v) for v in edge['facets'].values())
                or any(not _hash(edge[key]) for key in ('source_sha256', 'question_sha256', 'examples_sha256'))
                or edge['canonical_id'] not in value['ordered_ids']
                or edge['unit_id'] != _unit_id(edge['canonical_id'], edge['ability'])
                or (edge['candidate_id'], edge['ability']) in seen):
            raise ValueError('材料能力映射无效。')
        seen.add((edge['candidate_id'], edge['ability']))
        selected.setdefault(edge['canonical_id'], set()).add(edge['ability'])
    for cid, abilities in value['added_abilities'].items():
        if (cid not in selected or not isinstance(abilities, list) or not abilities
                or any(not isinstance(a, str) for a in abilities) or len(set(abilities)) != len(abilities)
                or not set(abilities) <= selected[cid]):
            raise ValueError('材料新增能力范围无效。')
    return deepcopy(value)


def validate_database(db):
    from .ai_materials import SETTING, empty_state, validate_ai_material_state
    from .material_workflow import PREFIX as JOB_PREFIX, validate_job
    from .material_verification import proposal_content
    raw = db.execute('SELECT value_json FROM app_setting WHERE key=?', (SETTING,)).fetchone()
    proposals = {p['id']: p for p in validate_ai_material_state(json.loads(raw[0]) if raw else empty_state())['proposals']}
    for key, raw in db.execute("SELECT key,value_json FROM app_setting WHERE key GLOB 'material_admission_*'"):
        value = validate(json.loads(raw))
        job_raw = db.execute('SELECT value_json FROM app_setting WHERE key=?', (JOB_PREFIX + value['workflow_id'],)).fetchone()
        job = validate_job(json.loads(job_raw[0])) if job_raw else None
        proposal = proposals.get(value['proposal_id'])
        if (key != PREFIX + value['id'] or not job or not proposal or job['proposal_id'] != value['proposal_id']
                or value['proposal_sha256'] != digest(proposal_content(proposal))):
            raise ValueError('材料能力映射缺少原始提案。')
        candidates = dict(zip(job['candidate_ids'], proposal['items']))
        for edge in value['mappings']:
            candidate = candidates.get(edge['candidate_id'])
            source = db.execute('SELECT content_json FROM learning_content WHERE content_id=?', (edge['canonical_id'],)).fetchone()
            item = json.loads(source[0]) if source else None
            if (not candidate or candidate['id'] != edge['candidate_object_id'] or not item
                    or str(item['revision']) != edge['canonical_revision'] or item['family'] != 'word'
                    or _source_hash(item) != edge['source_sha256']
                    or not meaning_supported(candidate, item)
                    or any(digest(candidate.get(f, '')) != sha or f != 'meaning' and digest(item.get(f, '')) != sha
                           for f, sha in edge['facets'].items())
                    or edge['candidate_revision'] != 'material-facets-v1-' + digest([edge['candidate_id'], {f: edge['facets'][f] for f in CORE}])[:24]
                    or not db.execute('SELECT 1 FROM learning_unit WHERE unit_id=? AND content_id=? AND ability=?',
                                      (edge['unit_id'], edge['canonical_id'], edge['ability'])).fetchone()):
                raise ValueError('材料能力映射与原候选或正式学习项不一致。')
            locator = edge['source_locator']
            if (edge['source_kind'] == 'course_item' and locator['course_id'] != item['id']
                    or edge['source_kind'] == 'dictionary_sense' and
                       (locator['word_id'], locator['sense_id']) != (item.get('word_id'), item.get('sense_id'))):
                raise ValueError('材料能力来源位置不匹配。')
        for cid in value['ordered_ids']:
            if not db.execute('SELECT 1 FROM learning_content WHERE content_id=?', (cid,)).fetchone():
                raise ValueError('材料能力回执缺少前置项。')
        for cid, abilities in value['added_abilities'].items():
            from .ability_teaching import read
            teaching = read(db, cid)
            if not teaching or not set(abilities) <= set(teaching['additions']):
                raise ValueError('材料能力回执缺少独立初学记录。')


def project_exposure_times(db, times):
    """Copy only still-active ability exposure deadlines, never scores/dates."""
    candidate_ids = sorted({cid for cid, _ in times if cid.startswith('material-candidate:')})
    result = dict(times)
    for start in range(0, len(candidate_ids), 100):
        ids = candidate_ids[start:start + 100]
        placeholders = ','.join('?' for _ in ids)
        for raw, in db.execute("SELECT e.value FROM app_setting s, json_each(s.value_json,'$.mappings') e "
                f"WHERE s.key GLOB ? AND json_extract(e.value,'$.candidate_id') IN ({placeholders})", (PREFIX + '*', *ids)):
            edge = json.loads(raw)
            until = times.get((edge['candidate_id'], edge['ability']))
            key = (edge['canonical_id'], edge['ability'])
            if until and (key not in result or datetime.fromisoformat(until) > datetime.fromisoformat(result[key])):
                result[key] = until
    return result


def mapped_exposure(store, cid, ability):
    """Direct check gates need the original timestamp, without refreshing it."""
    from hashlib import sha256
    from .retention_check import EXPOSURE_PREFIX, validate_exposure
    latest = None
    for raw, in store._db.execute("SELECT DISTINCT json_extract(e.value,'$.candidate_id') "
            "FROM app_setting s, json_each(s.value_json,'$.mappings') e WHERE s.key GLOB ? "
            "AND json_extract(e.value,'$.canonical_id')=? AND json_extract(e.value,'$.ability')=?", (PREFIX + '*', cid, ability)):
        value = store.get_setting(EXPOSURE_PREFIX + sha256(raw.encode()).hexdigest(), None)
        record = validate_exposure(value)['abilities'].get(ability) if value else None
        if record and (latest is None or datetime.fromisoformat(record['at']) > datetime.fromisoformat(latest['at'])):
            latest = record
    return latest
