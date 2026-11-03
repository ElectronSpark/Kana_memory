"""Explicit sense disambiguation; a choice is not a teaching certificate."""
from copy import deepcopy
from datetime import datetime
import json
import re

from . import app_clock
from .material_workflow import digest, dump, MaterialWorkflowStore
from .material_verification import proposal_content

PREFIX = 'material_sense_choice_v1:'


def matches(candidate, evidence, repo):
    from .learning_content import get_learning_item
    from .material_verification import current_dictionary_item
    from .material_qualification import CORE, meaning_supported
    result = []
    for source in evidence:
        if source['kind'] not in {'course_item', 'dictionary_sense'} or any(
                candidate.get(field, '') != source['snapshot'].get(field, '') for field in CORE if field != 'meaning'):
            continue
        known = (current_dictionary_item(repo, source['object_id']) if source['kind'] == 'dictionary_sense'
                 else get_learning_item(source['object_id']))
        if known and meaning_supported(candidate, known): result.append((source, known))
    return result


def latest(store, proposal_id, candidate_id):
    row = store._db.execute('SELECT value_json FROM app_setting WHERE key GLOB ? '
        'AND json_extract(value_json,\'$.proposal_id\')=? AND json_extract(value_json,\'$.candidate_id\')=? '
        'ORDER BY rowid DESC LIMIT 1', (PREFIX + '*', proposal_id, candidate_id)).fetchone()
    return validate(json.loads(row[0])) if row else None


def select_matches(store, proposal, candidate, options):
    selected = latest(store, proposal['id'], candidate['id'])
    if selected is None: return options
    if selected['proposal_sha256'] != digest(proposal_content(proposal)): return []
    return [(source, known) for source, known in options if source == selected['source']]


def project_identities(store, proposal, report, *, repo=None):
    """Resolve today's ambiguity without changing the historical AI report."""
    from .material_verification import collect_evidence, _sense_matches
    evidence, _ = collect_evidence(proposal, repo=repo, store=store)
    result = deepcopy(report); decisions = []
    for candidate in proposal['items']:
        from .material_reference import explicitly_blocked
        if explicitly_blocked(store, proposal, 'item', candidate['id']) & {'family', 'domain', 'title', 'meaning', 'reading'}:
            decisions.append({'candidate_object_id': candidate['id'], 'decision': 'unresolved',
                'canonical_id': None, 'canonical_revision': None, 'evidence_ids': []})
            continue
        options = select_matches(store, proposal, candidate, matches(candidate, evidence, repo))
        exact = [(source, item) for source, item in options if _sense_matches(candidate, item)]
        source, item = exact[0] if len(options) == len(exact) == 1 else (None, None)
        decisions.append({'candidate_object_id': candidate['id'], 'decision': 'reuse_existing' if item else 'unresolved',
            'canonical_id': item['id'] if item else None, 'canonical_revision': str(item['revision']) if item else None,
            'evidence_ids': [source['id']] if source else []})
    result['identities'] = decisions
    result['evidence'] = evidence
    return result


def validate(value):
    fields = {'version', 'id', 'workflow_id', 'proposal_id', 'proposal_sha256', 'candidate_id', 'source', 'previous_id', 'at'}
    if (not isinstance(value, dict) or set(value) != fields or type(value['version']) is not int or value['version'] != 1
            or any(not isinstance(value[k], str) or not re.fullmatch('[0-9a-f]{64}', value[k])
                   for k in ('id', 'workflow_id', 'proposal_sha256'))
            or any(not isinstance(value[k], str) or not 0 < len(value[k]) <= 1024 for k in ('proposal_id', 'candidate_id'))
            or value['previous_id'] is not None and (not isinstance(value['previous_id'], str)
                or not re.fullmatch('[0-9a-f]{64}', value['previous_id']))
            or not isinstance(value['at'], str) or datetime.fromisoformat(value['at']).tzinfo is None
            or value['id'] != digest({k: v for k, v in value.items() if k != 'id'})):
        raise ValueError('材料义项选择记录无效。')
    source = value['source']
    if (not isinstance(source, dict) or set(source) != {'id', 'kind', 'object_id', 'revision', 'snapshot', 'sha256', 'source_ids'}
            or source['kind'] not in {'course_item', 'dictionary_sense'} or not isinstance(source['snapshot'], dict)
            or not isinstance(source['object_id'], str) or not isinstance(source['revision'], str)
            or not isinstance(source['source_ids'], list) or source['sha256'] != digest(source['snapshot'])
            or source['id'] != 'material-evidence:' + digest([source['kind'], source['object_id'], source['revision'], source['snapshot']])):
        raise ValueError('义项选择缺少具体词义版本。')
    return deepcopy(value)


class MaterialSenseStore:
    def __init__(self, store, *, repo=None):
        self.store, self.db, self.repo = store, store._db, repo

    def options(self, identity):
        from .ai_materials import AIMaterialStore
        from .material_usage import project_proposal
        from .material_verification import collect_evidence
        job = MaterialWorkflowStore(self.store).get(identity)
        proposal = AIMaterialStore(self.store).get(job['proposal_id']) if job and job.get('proposal_id') else None
        if proposal is None: raise ValueError('材料已不可用。')
        usable = project_proposal(self.store, proposal)
        if usable is None: return []
        evidence, _ = collect_evidence(usable, repo=self.repo, store=self.store)
        result = []
        for candidate in usable['items']:
            options = matches(candidate, evidence, self.repo)
            selected = latest(self.store, proposal['id'], candidate['id'])
            if len(options) > 1 or selected:
                result.append({'workflow_id': identity, 'proposal_id': proposal['id'],
                    'proposal_sha256': digest(proposal_content(proposal)), 'candidate': candidate,
                    'options': [{'source': source, 'item': item} for source, item in options], 'selected': selected})
        return result

    def choose(self, expected, source_id):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            current = next((row for row in self.options(expected['workflow_id'])
                            if row['candidate']['id'] == expected['candidate']['id']), None)
            if current is None: raise ValueError('词义范围已变化，请重新打开。')
            # A repeated click may return its receipt but cannot replace a newer choice.
            if (current['selected'] and current['selected']['source']['id'] == source_id
                    and current['proposal_sha256'] == expected['proposal_sha256']
                    and current['options'] == expected['options']):
                return current['selected']
            if current != expected: raise ValueError('词义范围已变化，请重新打开。')
            option = next((row for row in current['options'] if row['source']['id'] == source_id), None)
            if option is None: raise ValueError('请从当前可核对的义项中选择。')
            # An admitted candidate keeps its recorded identity. Sense migration
            # is deliberately outside this first-version disambiguation feature.
            from .material_admission import PREFIX as ADMISSION
            existing = self.db.execute('SELECT e.value FROM app_setting s, json_each(s.value_json,\'$.mappings\') e '
                'WHERE s.key GLOB ? AND json_extract(e.value,\'$.candidate_object_id\')=?',
                (ADMISSION + '*', current['candidate']['id'])).fetchall()
            if any(json.loads(row[0])['canonical_id'] != option['source']['object_id'] for row in existing):
                raise ValueError('本项已按原义项加入学习；其他义项请作为新材料处理。')
            value = {'version': 1, 'workflow_id': current['workflow_id'], 'proposal_id': current['proposal_id'],
                'proposal_sha256': current['proposal_sha256'], 'candidate_id': current['candidate']['id'],
                'source': option['source'], 'previous_id': current['selected']['id'] if current['selected'] else None,
                'at': app_clock.utc_now().isoformat()}
            value['id'] = digest(value); value = validate(value)
            self.db.execute('INSERT INTO app_setting VALUES (?,?)', (PREFIX + value['id'], dump(value)))
            if current['selected'] and current['selected']['source'] != value['source']:
                from .material_validity import apply_in_transaction
                apply_in_transaction(self.store, PREFIX + value['id'], current['selected'])
        return value


def validate_database(db):
    from .ai_materials import SETTING, empty_state, validate_ai_material_state
    from .material_workflow import PREFIX as WORKFLOWS, validate_job
    raw = db.execute('SELECT value_json FROM app_setting WHERE key=?', (SETTING,)).fetchone()
    proposals = {p['id']: p for p in validate_ai_material_state(json.loads(raw[0]) if raw else empty_state())['proposals']}
    heads = {}
    for key, raw in db.execute('SELECT key,value_json FROM app_setting WHERE key GLOB ? ORDER BY rowid', (PREFIX + '*',)):
        value = validate(json.loads(raw)); proposal = proposals.get(value['proposal_id'])
        job_raw = db.execute('SELECT value_json FROM app_setting WHERE key=?', (WORKFLOWS + value['workflow_id'],)).fetchone()
        job = validate_job(json.loads(job_raw[0])) if job_raw else None
        owner = (value['proposal_id'], value['candidate_id'])
        if (key != PREFIX + value['id'] or not proposal or digest(proposal_content(proposal)) != value['proposal_sha256']
                or not job or job['proposal_id'] != proposal['id'] or value['previous_id'] != heads.get(owner)
                or value['candidate_id'] not in {item['id'] for item in proposal['items']}):
            raise ValueError('材料义项选择缺少原文或版本链不连续。')
        candidate = next(item for item in proposal['items'] if item['id'] == value['candidate_id'])
        if any(candidate.get(k, '') != value['source']['snapshot'].get(k, '') for k in ('family', 'domain', 'title')):
            raise ValueError('义项来源属于其他候选内容。')
        heads[owner] = value['id']
