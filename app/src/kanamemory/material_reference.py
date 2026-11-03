"""Field-scoped human reference checks for genuinely new learning material.

These records attest who checked what against which excerpt. They do not claim
algorithmic entailment, expert review, or that model agreement is external proof.
Retention, teaching qualification and admission remain separate operations.
"""
from copy import deepcopy
from datetime import datetime
import json
from uuid import uuid4

from . import app_clock
from .material_workflow import digest, dump
from .feedback_field_support import CHECKS, category, METHOD

SOURCES = 'material_reference_source_v1:'
REVIEWS = 'material_reference_review_v1:'


def _text(value, limit, *, blank=False):
    if not isinstance(value, str) or len(value) > limit or not blank and not value.strip():
        raise ValueError('请填写限定范围内的参考资料与核据说明。')
    return value


def objects(proposal):
    from .material_verification import ITEM_FIELDS, EXAMPLE_FIELDS, REF_FIELDS
    return [(kind, row, fields) for kind, rows, fields in (
        ('item', proposal['items'], ITEM_FIELDS),
        ('example', proposal['example_pack']['examples'], EXAMPLE_FIELDS),
        ('reference', proposal['example_pack']['references'], REF_FIELDS)) for row in rows]


def claims(proposal):
    return [{'id': 'material-claim:' + digest([kind, row['id'], field]),
        'object_kind': kind, 'object_id': row['id'], 'facet': field,
        'value': deepcopy(row[field]), 'value_sha256': digest(row[field])}
        for kind, row, fields in objects(proposal) for field in fields if field in row]


def checks(claim):
    field = claim['facet']
    if field == 'question_templates': return CHECKS['question']
    if field == 'depends_on': return CHECKS['prerequisites']
    if claim['object_kind'] == 'reference': return CHECKS['link']
    return CHECKS[category({'field': field, 'resource_kind': claim['object_kind']})]


def _hashed(value):
    return value.get('id') == digest({k:v for k,v in value.items() if k != 'id'})


def validate_source(value):
    if (not isinstance(value, dict) or set(value) != {'version', 'id', 'proposal_id', 'label', 'locator', 'text', 'at'}
            or value['version'] != 1 or not _hashed(value)
            or datetime.fromisoformat(value['at']).tzinfo is None):
        raise ValueError('参考资料记录无效。')
    for key, limit in (('proposal_id', 256), ('label', 400), ('locator', 1600), ('text', 32000)):
        _text(value[key], limit)
    return value


def validate_review(value, proposal, sources):
    from .material_verification import proposal_content
    required = {'version', 'id', 'request_id', 'proposal_id', 'proposal_sha256', 'claim',
                'citations', 'status', 'reason', 'checks', 'model_comparison', 'at'}
    expected = {c['id']: c for c in claims(proposal)}
    if (not isinstance(value, dict) or set(value) != required or value['version'] != 1 or not _hashed(value)
            or value['proposal_id'] != proposal['id'] or value['proposal_sha256'] != digest(proposal_content(proposal))
            or value['claim'] != expected.get(value['claim'].get('id'))
            or value['status'] not in {'supported', 'pending', 'contradicted'}
            or datetime.fromisoformat(value['at']).tzinfo is None):
        raise ValueError('字段核据与原材料不一致。')
    _text(value['request_id'], 128)
    supported = value['status'] == 'supported'
    _text(value['reason'], 1600, blank=not supported)
    _text(value['model_comparison'], 1600, blank=not supported)
    if not isinstance(value['checks'], dict) or set(value['checks']) != set(checks(value['claim'])):
        raise ValueError('请逐项记录当前字段的核对范围。')
    for note in value['checks'].values(): _text(note, 800, blank=not supported)
    if (not isinstance(value['citations'], list) or len(value['citations']) > 8
            or supported and not value['citations']):
        raise ValueError('支持结论必须关联具体参考片段。')
    seen = set()
    for citation in value['citations']:
        if not isinstance(citation, dict) or set(citation) != {'source_id', 'start', 'end', 'quote'}:
            raise ValueError('参考片段格式无效。')
        source = sources.get(citation['source_id'])
        start, end = citation['start'], citation['end']
        key = (citation['source_id'], start, end)
        if (not source or source['proposal_id'] != proposal['id'] or type(start) is not int or type(end) is not int
                or not 0 <= start < end <= len(source['text']) or end-start > 12000
                or source['text'][start:end] != citation['quote'] or key in seen):
            raise ValueError('引文必须精确对应所选资料中的连续片段。')
        seen.add(key)
    return value


class MaterialReferenceStore:
    def __init__(self, store):
        self.store, self.db = store, store._db

    def proposal(self, identity):
        from .ai_materials import AIMaterialStore
        proposal = AIMaterialStore(self.store).get(identity)
        if proposal is None: raise ValueError('原材料已不可用。')
        return proposal

    def sources(self, identity):
        return [validate_source(json.loads(raw)) for raw, in self.db.execute(
            "SELECT value_json FROM app_setting WHERE key GLOB ? AND json_extract(value_json,'$.proposal_id')=? ORDER BY rowid",
            (SOURCES + '*', identity))]

    def add_source(self, identity, label, locator, text):
        proposal = self.proposal(identity)
        # A submitted passage proves occurrence, not its own correctness.
        if text.strip() == proposal['source_text'].strip():
            raise ValueError('学习原文不能作为自身正确性的独立参考，请提供词典或语法参考。')
        prior = self.sources(identity)
        same = next((r for r in prior if (r['label'], r['locator'], r['text']) == (label, locator, text)), None)
        if same: return same
        if len(prior) >= 24: raise ValueError('本批最多使用24份参考，请缩小材料范围。')
        value = {'version': 1, 'proposal_id': identity, 'label': label, 'locator': locator, 'text': text,
                 'at': app_clock.utc_now().isoformat()}
        value['id'] = digest(value); validate_source(value)
        with self.db:
            self.db.execute('INSERT INTO app_setting VALUES (?,?)', (SOURCES + value['id'], dump(value)))
        return value

    def latest(self, identity):
        result = {}
        for raw, in self.db.execute("SELECT value_json FROM app_setting WHERE key GLOB ? AND json_extract(value_json,'$.proposal_id')=? ORDER BY rowid",
                (REVIEWS + '*', identity)):
            value = json.loads(raw); result[value['claim']['id']] = value
        return result

    def preview(self, identity, claim_id):
        from .material_verification import proposal_content
        proposal = self.proposal(identity)
        claim = next((row for row in claims(proposal) if row['id'] == claim_id), None)
        if claim is None: raise ValueError('字段已变化，请重新选择。')
        old = self.latest(identity).get(claim_id)
        return {'proposal_id': identity, 'proposal_sha256': digest(proposal_content(proposal)),
                'claim': claim, 'previous_id': old['id'] if old else None}

    def record(self, expected, *, citations, status, reason, check_notes, model_comparison, request_id=None):
        request_id = request_id or uuid4().hex
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            prior = self.db.execute("SELECT value_json FROM app_setting WHERE key GLOB ? AND json_extract(value_json,'$.request_id')=?",
                (REVIEWS + '*', request_id)).fetchone()
            submitted = {'proposal_id': expected['proposal_id'], 'proposal_sha256': expected['proposal_sha256'],
                'claim': expected['claim'], 'citations': citations, 'status': status, 'reason': reason,
                'checks': check_notes, 'model_comparison': model_comparison}
            if prior:
                value = json.loads(prior[0])
                if any(value[k] != v for k,v in submitted.items()): raise ValueError('同一核据请求不能改动内容。')
                return value
            if self.preview(expected['proposal_id'], expected['claim']['id']) != expected:
                raise ValueError('核据记录已变化，请重新打开当前字段。')
            value = {'version': 1, 'request_id': request_id, **deepcopy(submitted), 'at': app_clock.utc_now().isoformat()}
            value['id'] = digest(value)
            validate_review(value, self.proposal(expected['proposal_id']), {r['id']: r for r in self.sources(expected['proposal_id'])})
            self.db.execute('INSERT INTO app_setting VALUES (?,?)', (REVIEWS + value['id'], dump(value)))
            if status in {'pending', 'contradicted'}:
                from .material_validity import apply_in_transaction
                previous = self.store.get_setting(REVIEWS + expected['previous_id'], None) if expected['previous_id'] else None
                apply_in_transaction(self.store, REVIEWS + value['id'], previous)
        return value


def project_report(store, proposal, report):
    """Current review overlays do not rewrite the saved model/local report."""
    if store is None: return report
    result = deepcopy(report); current = MaterialReferenceStore(store).latest(proposal['id'])
    for claim in result['claims']:
        review = current.get(claim['id'])
        if not review or review['claim']['value_sha256'] != claim['value_sha256']: continue
        # A later explicit doubt takes precedence over an old local match too.
        status = review['status']
        claim.update(status='unknown' if status == 'pending' else status,
            method=METHOD, allowed_uses=['reference'] if status == 'supported' else ['preview'],
            model_note='已记录逐项参考核对。' if status == 'supported' else review['reason'],
            reference_review_id=review['id'])
    return result


def explicitly_blocked(store, proposal, kind, object_id):
    """Only a current explicit doubt overrides a separate local match."""
    from .material_verification import proposal_content
    latest = MaterialReferenceStore(store).latest(proposal['id'])
    return {claim['facet'] for claim in claims(proposal) if claim['object_kind'] == kind
        and claim['object_id'] == object_id and (review := latest.get(claim['id']))
        and review['status'] in {'pending', 'contradicted'}
        and review['proposal_sha256'] == digest(proposal_content(proposal))
        and review['claim']['value_sha256'] == claim['value_sha256']}


def reference_evidence(store, proposal):
    from .material_verification import _evidence
    if store is None or not proposal.get('id'): return []
    return [_evidence('reference_excerpt', {**source, 'revision': '1', 'source_ids': ['user-supplied-reference']},
        ('label', 'locator', 'text')) for source in MaterialReferenceStore(store).sources(proposal['id'])]


def _supported(service, proposal, kind, row, fields, *, latest=None):
    latest = service.latest(proposal['id']) if latest is None else latest
    for field in fields:
        claim_id = 'material-claim:' + digest([kind, row['id'], field])
        review = latest.get(claim_id)
        if not review or review['status'] != 'supported' or review['claim']['value_sha256'] != digest(row[field]):
            return False
    return True


def _item_supported(service, proposal, item):
    # Family/domain and an absent accent are validated structure, not guessed
    # linguistic facts. Every displayed teaching/answer-bearing field is scoped.
    fields = ['title', 'meaning', 'instruction', 'depends_on', 'question_templates']
    if item.get('reading'): fields.append('reading')
    if item.get('accent') is not None: fields.append('accent')
    return _supported(service, proposal, 'item', item, fields)


def qualified_items(store, identity):
    from .material_workflow import MaterialWorkflowStore
    from .material_usage import project_proposal
    job = MaterialWorkflowStore(store).get(identity)
    if not job or not job.get('proposal_id'): return []
    service = MaterialReferenceStore(store); proposal = service.proposal(job['proposal_id'])
    usable = project_proposal(store, proposal)
    if usable is None: return []
    from .material_verification import MaterialVerificationStore
    report = MaterialVerificationStore(store).get(identity)
    reused = {r['candidate_object_id'] for r in (report or {}).get('identities', []) if r['decision'] == 'reuse_existing'}
    result = []
    from .material_senses import latest as sense_choice
    for item in usable['items']:
        if (item['id'] not in reused and not sense_choice(store, proposal['id'], item['id'])
                and (current := _current_item(service, proposal, item))):
            result.append(current)
    return result


def _current_item(service, proposal, raw):
    if not _item_supported(service, proposal, raw): return None
    from .personal_content_versions import resolve, teaching_source
    current = resolve(service.store, teaching_source(raw))
    return None if current.get('personal_content_unavailable') else current


def trusted_item(store, item, *, base_snapshot=False):
    """Resolve provenance first; caller version flags never authorize content."""
    if store is None or not str(item.get('id', '')).startswith('local:ai:'): return None
    from .ai_materials import AIMaterialStore
    from .material_usage import allowed, read
    from .material_verification import ITEM_FIELDS
    service = MaterialReferenceStore(store); usage = read(store)
    owned = store._db.execute("SELECT 1 FROM sqlite_master WHERE name='learning_content'").fetchone()
    owned = bool(owned and store._db.execute('SELECT 1 FROM learning_content WHERE content_id=?', (item['id'],)).fetchone())
    fields = (*ITEM_FIELDS, 'id', 'revision', 'abilities', 'source_ids', 'source_urls', 'register',
              'personal_revision', 'personal_base_revision', 'personal_reading',
              'personal_question_versions', 'personal_prerequisites')
    for proposal in AIMaterialStore(store)._state()['proposals']:
        raw = next((row for row in proposal['items'] if row['id'] == item['id']), None)
        if raw is None or not owned and not allowed(usage, proposal, 'items', raw['id']): continue
        current = _current_item(service, proposal, raw)
        expected = raw if base_snapshot else current
        if current and all(digest(item.get(k)) == digest(expected.get(k)) for k in fields):
            return current
    return None


def item_ready(store, item):
    """Resolve the exact AI-origin snapshot; caller flags cannot grant credit."""
    return trusted_item(store, item) is not None


def question_ready(store, item, question):
    current = trusted_item(store, item) or trusted_item(store, item, base_snapshot=True)
    if current is None: return False
    from .learning_questions import build_learning_question
    from .material_eligibility import same_contract
    try:
        rebuilt = build_learning_question(current, question['kind'], variant=question.get('variant', 0), guided=question.get('guided') is True)
        return same_contract(rebuilt, question)
    except (KeyError, ValueError, TypeError):
        return False


def retained_evidence_pack(store, pack):
    """Only specifically checked retained bodies and per-target references."""
    from .ai_materials import AIMaterialStore
    from .material_usage import allowed, read, snapshot_hash
    from .material_verification import EXAMPLE_FIELDS, REF_FIELDS
    service = MaterialReferenceStore(store); usage = read(store)
    current = {kind: {r['id']: r for r in pack[kind]} for kind in ('examples', 'references')}
    bodies, refs = {}, {}
    for proposal in AIMaterialStore(store)._state()['proposals']:
        latest = service.latest(proposal['id'])
        if not latest: continue
        for kind, fields in (('examples', EXAMPLE_FIELDS), ('references', REF_FIELDS)):
            for row in proposal['example_pack'][kind]:
                saved = current[kind].get(row['id'])
                if (proposal['decisions'][kind][row['id']] != 'kept' or not saved
                        or not allowed(usage, proposal, kind, row['id'])
                        or snapshot_hash(saved, kind) != snapshot_hash(row, kind)):
                    continue
                required = [key for key in fields if key in row and (key not in {'reading', 'order_translation'} or row[key])]
                if _supported(service, proposal, 'example' if kind == 'examples' else 'reference', row, required, latest=latest):
                    (bodies if kind == 'examples' else refs)[row['id']] = deepcopy(saved)
    return list(bodies.values()), [r for r in refs.values() if r['example_id'] in bodies]


def admission_readiness(store, item, *, base_snapshot=False):
    from .ai_materials import AIMaterialStore
    from .example_store import SETTING, empty_pack
    if trusted_item(store, item, base_snapshot=base_snapshot) is None:
        return {'ready': False, 'reason': 'unverified', 'example_count': 0}
    # A verified preview is still distinct from deciding to keep the material.
    retained = any(p['decisions']['items'].get(item['id']) == 'kept'
        for p in AIMaterialStore(store)._state()['proposals'])
    bodies, refs = retained_evidence_pack(store, store.get_setting(SETTING, empty_pack()))
    texts = {e['id']: e['text'] for e in bodies}
    count = len({texts[r['example_id']] for r in refs if r['target_id'] == item['id']})
    return {'ready': retained and count >= 10, 'reason': 'not_retained' if not retained else 'examples' if count < 10 else '',
            'example_count': count}


def refresh_unsent_review(store, identity):
    """Freeze added excerpts before the sole review call, never rewrite a call."""
    from .material_verification import MaterialVerificationStore, make_review_request, write_in_transaction
    from .material_workflow import MaterialWorkflowStore
    job = MaterialWorkflowStore(store).get(identity)
    if not job or any(a['stage'] == 'review' for a in job['attempts']): return
    service = MaterialReferenceStore(store); proposal = service.proposal(job['proposal_id'])
    with store._db:
        store._db.execute('BEGIN IMMEDIATE')
        current = MaterialWorkflowStore(store).get(identity)
        if current != job: raise ValueError('复核任务已变化，请重新打开。')
        report = MaterialVerificationStore(store).get(identity)
        if report is None or report['model_review'] is not None: return
        existing = {row['id'] for row in report['evidence']}
        report['evidence'].extend(row for row in reference_evidence(store, proposal) if row['id'] not in existing)
        if len(report['evidence']) > 128: raise ValueError('本批参考范围过大，请分批核对。')
        report['review_request'] = make_review_request(report, proposal)
        write_in_transaction(store._db, report)


def validate_database(db):
    from .ai_materials import SETTING, empty_state, validate_ai_material_state
    row = db.execute('SELECT value_json FROM app_setting WHERE key=?', (SETTING,)).fetchone()
    proposals = {p['id']: p for p in validate_ai_material_state(json.loads(row[0]) if row else empty_state())['proposals']}
    sources = {}
    for key, raw in db.execute('SELECT key,value_json FROM app_setting WHERE key GLOB ?', (SOURCES + '*',)):
        value = validate_source(json.loads(raw)); proposal = proposals.get(value['proposal_id'])
        if key != SOURCES + value['id'] or not proposal or value['text'].strip() == proposal['source_text'].strip():
            raise ValueError('参考资料缺少原材料，或使用原文自证。')
        sources[value['id']] = value
    requests = set()
    for key, raw in db.execute('SELECT key,value_json FROM app_setting WHERE key GLOB ?', (REVIEWS + '*',)):
        value = json.loads(raw); proposal = proposals.get(value.get('proposal_id'))
        if not proposal: raise ValueError('字段核据缺少原材料。')
        validate_review(value, proposal, sources)
        if key != REVIEWS + value['id'] or value['request_id'] in requests: raise ValueError('核据重复或标识无效。')
        requests.add(value['request_id'])
    from .material_verification import PREFIX as REPORTS, _evidence
    for raw, in db.execute('SELECT value_json FROM app_setting WHERE key GLOB ?', (REPORTS + '*',)):
        report = json.loads(raw)
        for row in report['evidence']:
            if row['kind'] != 'reference_excerpt': continue
            source = sources.get(row['object_id'])
            if (not source or source['proposal_id'] != report['proposal_id'] or row != _evidence('reference_excerpt',
                    {**source, 'revision': '1', 'source_ids': ['user-supplied-reference']}, ('label', 'locator', 'text'))):
                raise ValueError('AI复核引用的资料没有对应原始摘录。')
