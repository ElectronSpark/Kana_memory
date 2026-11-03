"""Append-only, evidence-scoped human decisions, separate from activation.

A user's grounded review is attributed to that user; it is neither an automatic
linguistic certificate nor permission to change a learner's assessment.
"""
from copy import deepcopy
from datetime import datetime
import json
import re

from . import app_clock
from .feedback_impact import changes_for
from .feedback_review import FeedbackReviewStore, dependencies
from .feedback_revision import FeedbackRevisionStore
from .feedback_sources import PREFIX as SOURCES, SCOPE, validate as validate_sources, excerpt_evidence
from .feedback_bindings import current_resource, projection, validate_binding
from .material_workflow import digest, dump
from .retention_ai import _hash
from .tutor_store import _text

PREFIX = 'feedback_field_review_v1:'
DECISIONS = ('pending', 'reject', 'display_note', 'supported_by_user')


def scope_for(phase, source, review, sources):
    if not review or review['status'] != 'completed': raise ValueError('请先完成独立复核，再核对字段。')
    parent_id = source['id'] if phase == 'analysis' else source['request']['parent_id']
    if review['request']['parent_id'] != parent_id: raise ValueError('复核不属于这份建议稿。')
    changes = changes_for(source, phase)
    if not changes: raise ValueError('建议稿没有待核改动。')
    sources = validate_sources(sources) if sources else None
    evidence, claims = [], {c['binding']['id']: c for c in review['request']['claims']}
    findings = {f['claim_id']: f for f in review['response']['findings']}
    decisions = {r['binding_id']: r for r in source['response']['decisions']} if phase == 'revision' else {}
    rows = []
    for change in changes:
        bid = change['binding']['id']; claim = claims.get(bid)
        if not claim: raise ValueError('建议稿缺少该字段的独立复核范围。')
        ids = []
        for e in review['request']['evidence']:
            if e['kind'] != 'public_excerpt' or e['id'] not in claim['evidence_ids']: continue
            original = next((r for r in sources['excerpts'] if excerpt_evidence(sources, r) == e), None) if sources else None
            if not original: raise ValueError('核据摘录缺少保存的正文与准确位置。')
            enriched = {'evidence': deepcopy(e), 'relationship': original['relationship'],
                        'relationship_reason': original['relationship_reason']}
            if not any(r['evidence']['id'] == e['id'] for r in evidence): evidence.append(enriched)
            ids.append(e['id'])
        opinion = findings.get(claim['id'])
        note = (decisions[bid]['reason'] if phase == 'revision' else opinion['note'] if opinion else '')
        # Deferred fields are reviewed jointly by the third response, not by
        # pretending the blind reviewer answered under a stable premise.
        reviewed = (decisions[bid]['decision'] == 'revise' if phase == 'revision' else
                    bool(opinion and opinion['status'] == 'answerable' and not claim.get('deferred_by')))
        rows.append({**deepcopy(change), 'evidence_ids': ids, 'model_reviewed': reviewed,
                     'model_note': note, 'model_answer': json.loads(opinion['answer_json']) if opinion else None})
    return {'version': 1, 'phase': phase, 'source_id': source['id'], 'source_sha256': digest(source),
            'parent_id': parent_id, 'review_id': review['id'], 'review_sha256': digest(review),
            'changes': rows, 'dependencies': dependencies(changes), 'evidence': evidence}


def validate_scope(s):
    if (not isinstance(s, dict) or set(s) != {'version', 'phase', 'source_id', 'source_sha256', 'parent_id',
            'review_id', 'review_sha256', 'changes', 'dependencies', 'evidence'} or type(s['version']) is not int
            or s['version'] != 1 or s['phase'] not in {'analysis', 'revision'}
            or not all(_hash(s[k]) for k in ('source_id', 'source_sha256', 'parent_id', 'review_id', 'review_sha256'))
            or not isinstance(s['changes'], list) or not 1 <= len(s['changes']) <= 12
            or not isinstance(s['evidence'], list) or len(s['evidence']) > 12):
        raise ValueError('字段核对范围无效。')
    from .feedback_sources import validate_public_evidence
    known = {}
    for r in s['evidence']:
        if not isinstance(r, dict) or set(r) != {'evidence', 'relationship', 'relationship_reason'}:
            raise ValueError('字段核据关系无效。')
        validate_public_evidence(r['evidence']); eid = r['evidence']['id']
        if eid in known or r['relationship'] not in {'unknown', 'same_lineage', 'independent_user_asserted'}:
            raise ValueError('字段核据关系重复或无效。')
        _text(r['relationship_reason'], 500, blank=r['relationship'] != 'independent_user_asserted'); known[eid] = r
    seen = set()
    for c in s['changes']:
        if not isinstance(c, dict) or set(c) != {'binding', 'new_value', 'evidence_ids', 'model_reviewed', 'model_note', 'model_answer'}:
            raise ValueError('字段核对内容无效。')
        b = validate_binding(c['binding']); _text(c['new_value'], 2400); _text(c['model_note'], 1600, blank=True)
        if c['model_answer'] is not None: _text(c['model_answer'], 2400)
        if (b['id'] in seen or c['new_value'] == b['value'] or type(c['model_reviewed']) is not bool
                or not isinstance(c['evidence_ids'], list) or len(set(c['evidence_ids'])) != len(c['evidence_ids'])
                or any(e not in known or known[e]['evidence']['object_id'] != b['id'] for e in c['evidence_ids'])):
            raise ValueError('字段核对版本或依据归属不一致。')
        seen.add(b['id'])
    if s['dependencies'] != dependencies(s['changes']): raise ValueError('字段核对联动不完整。')
    return deepcopy(s)


def validate_decisions(scope, rows):
    changes = {c['binding']['id']: c for c in scope['changes']}
    evidence = {r['evidence']['id']: r for r in scope['evidence']}
    if not isinstance(rows, list) or len(rows) != len(changes): raise ValueError('请保留每个字段的决定，未确定项可选待核。')
    seen = set()
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {'binding_id', 'decision', 'reason', 'evidence_ids', 'evidence_reviews', 'scope_checked', 'comparison_checked'}
                or not isinstance(row['binding_id'], str) or row['binding_id'] not in changes or row['binding_id'] in seen
                or row['decision'] not in DECISIONS or not isinstance(row['evidence_ids'], list)
                or any(not isinstance(i, str) for i in row['evidence_ids']) or len(set(row['evidence_ids'])) != len(row['evidence_ids'])
                or not set(row['evidence_ids']) <= set(changes[row['binding_id']]['evidence_ids'])
                or not isinstance(row['scope_checked'], dict) or set(row['scope_checked']) != set(SCOPE)
                or any(type(v) is not bool for v in row['scope_checked'].values()) or type(row['comparison_checked']) is not bool):
            raise ValueError('字段决定或证据选择无效。')
        _text(row['reason'], 1000, blank=row['decision'] == 'pending'); seen.add(row['binding_id'])
        if not isinstance(row['evidence_reviews'], list) or len(row['evidence_reviews']) > 12:
            raise ValueError('资料的本次人工核对无效。')
        assessed = {}
        for item in row['evidence_reviews']:
            if (not isinstance(item, dict) or set(item) != {'evidence_id', 'scope', 'relationship', 'reason'}
                    or not isinstance(item['evidence_id'], str) or item['evidence_id'] not in row['evidence_ids']
                    or item['evidence_id'] in assessed or not isinstance(item['scope'], dict) or set(item['scope']) != set(SCOPE)
                    or item['relationship'] not in {'unknown', 'same_lineage', 'independent_user_asserted'}):
                raise ValueError('资料的本次人工核对与选中摘录不一致。')
            for v in item['scope'].values(): _text(v, 300, blank=True)
            _text(item['reason'], 500, blank=item['relationship'] != 'independent_user_asserted')
            assessed[item['evidence_id']] = item
        if row['decision'] != 'supported_by_user': continue
        c = changes[row['binding_id']]
        if (not c['model_reviewed'] or not row['comparison_checked'] or not all(row['scope_checked'].values())
                or not row['evidence_ids']):
            raise ValueError('依据尚不完整：核对原文、新稿、复核意见和全部适用范围，或保留待核。')
        if set(assessed) != set(row['evidence_ids']): raise ValueError('请逐条核对选中摘录的适用范围和独立性。')
        selected = list(assessed.values())
        if any(not all(r['scope'][k].strip() for k in SCOPE) for r in selected):
            raise ValueError('摘录适用范围仍有空项；不能把未知当成已经核对。')
        if not any(r['relationship'] == 'independent_user_asserted' and r['reason'].strip() for r in selected):
            raise ValueError('仍缺少经过明确核对的独立来源说明，同源资料和模型一致不足以支持修正。')
    return deepcopy(rows)


def groups(scope, decisions):
    choices = {r['binding_id']: r['decision'] for r in decisions}; links = scope['dependencies']; remaining = set(links); result = []
    while remaining:
        connected = {min(remaining)}
        while True:
            expanded = connected | {d for i in connected for d in links[i]} | {i for i, ds in links.items() if connected.intersection(ds)}
            if expanded == connected: break
            connected = expanded
        remaining -= connected; values = {choices[i] for i in connected}
        state = ('user_supported' if values == {'supported_by_user'} else 'personal_notes_only' if values <= {'supported_by_user', 'display_note'}
                 else 'rejected' if values == {'reject'} else 'pending')
        result.append({'binding_ids': sorted(connected), 'status': state, 'assessment_eligible': False, 'activation_eligible': False})
    return result


def validate(value):
    if (not isinstance(value, dict) or set(value) != {'version', 'id', 'request_id', 'previous_id', 'generation', 'at', 'scope', 'decisions', 'groups'}
            or type(value['version']) is not int or value['version'] != 1 or not _hash(value['id'])
            or not isinstance(value['request_id'], str) or not re.fullmatch('[0-9a-f]{32}', value['request_id'])
            or value['previous_id'] is not None and not _hash(value['previous_id'])
            or type(value['generation']) is not int or value['generation'] < 1
            or datetime.fromisoformat(value['at']).tzinfo is None or len(dump(value).encode('utf-8')) > 131072):
        raise ValueError('字段核对回执无效。')
    scope = validate_scope(value['scope']); validate_decisions(scope, value['decisions'])
    if (value['id'] != digest(['field-review', scope['phase'], scope['source_id'], value['request_id']])
            or value['groups'] != groups(scope, value['decisions'])): raise ValueError('字段核对回执与联动组不一致。')
    return deepcopy(value)


class FeedbackFieldReviewStore:
    def __init__(self, analysis):
        self.analysis, self.store, self.db = analysis, analysis.store, analysis.db
        self.reviews = FeedbackReviewStore(analysis); self.revisions = FeedbackRevisionStore(self.reviews)

    def latest(self, phase, identity):
        row = self.db.execute("SELECT value_json FROM app_setting WHERE key GLOB ? AND json_extract(value_json,'$.scope.phase')=? "
            "AND json_extract(value_json,'$.scope.source_id')=? ORDER BY json_extract(value_json,'$.generation') DESC LIMIT 1", (PREFIX + '*', phase, identity)).fetchone()
        return validate(json.loads(row[0])) if row else None

    def preview(self, phase, identity):
        source = self.analysis.get(identity) if phase == 'analysis' else self.revisions.get(identity) if phase == 'revision' else None
        if not source or source['status'] != 'completed': raise ValueError('建议稿尚未完成。')
        parent_id = source['id'] if phase == 'analysis' else source['request']['parent_id']
        review = self.reviews.get(digest(['feedback-review', parent_id]))
        if not review or review['status'] != 'completed': raise ValueError('请先完成独立复核。')
        if any(r['status'] == 'stale' for r in self.reviews.findings(review['id'])):
            raise ValueError('原内容、反馈或核据已变化，请重新核查。')
        if phase == 'revision' and any(r['status'] != 'needs_evidence' for r in self.revisions.findings(identity)):
            raise ValueError('修订稿仍有未决联动字段或来源已变化。')
        scope = validate_scope(scope_for(phase, source, review, self.store.get_setting(SOURCES + parent_id, None)))
        for c in scope['changes']:
            b = c['binding']; current = current_resource(b, **self.analysis.resources)
            if current is None or digest(projection(b['resource_kind'], current)) != b['source_sha256']:
                raise ValueError('原内容版本已变化。')
        old = self.latest(phase, identity)
        return {'scope': scope, 'previous_id': old['id'] if old else None, 'generation': old['generation'] + 1 if old else 1}

    def record(self, expected, decisions, *, request_id):
        scope = validate_scope(expected['scope']); validate_decisions(scope, decisions)
        identity = digest(['field-review', scope['phase'], scope['source_id'], request_id])
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            raw = self.store.get_setting(PREFIX + identity, None)
            if raw:
                old = validate(raw)
                if (old['scope'] != scope or old['decisions'] != decisions or old['previous_id'] != expected['previous_id']
                        or old['generation'] != expected['generation']):
                    raise ValueError('同一保存请求不能改写决定。')
                return old
            if self.preview(scope['phase'], scope['source_id']) != expected:
                raise ValueError('依据或核对决定已变化，请重新打开后保存。')
            value = validate({'version': 1, 'id': identity, 'request_id': request_id, 'previous_id': expected['previous_id'],
                'generation': expected['generation'],
                'at': app_clock.utc_now().isoformat(), 'scope': deepcopy(scope), 'decisions': deepcopy(decisions), 'groups': groups(scope, decisions)})
            self.db.execute('INSERT INTO app_setting VALUES (?,?)', (PREFIX + identity, dump(value)))
        return value


def validate_database(db):
    from .feedback_analysis import PREFIX as ANALYSIS, validate as validate_analysis
    from .feedback_review import PREFIX as REVIEW, validate as validate_review
    from .feedback_revision import PREFIX as REVISION, validate as validate_revision
    def load(prefix, identity, validator):
        row = db.execute('SELECT value_json FROM app_setting WHERE key=?', (prefix + identity,)).fetchone()
        return validator(json.loads(row[0])) if row else None
    previous = {}
    for key, raw in db.execute("SELECT key,value_json FROM app_setting WHERE key GLOB ? ORDER BY json_extract(value_json,'$.generation')", (PREFIX + '*',)):
        receipt = validate(json.loads(raw)); scope = receipt['scope']; phase = scope['phase']
        source = load(ANALYSIS if phase == 'analysis' else REVISION, scope['source_id'], validate_analysis if phase == 'analysis' else validate_revision)
        review = load(REVIEW, scope['review_id'], validate_review); sources = load(SOURCES, scope['parent_id'], validate_sources)
        group = (phase, scope['source_id'])
        if (key != PREFIX + receipt['id'] or not source or scope_for(phase, source, review, sources) != scope
                or (receipt['previous_id'], receipt['generation'] - 1) != previous.get(group, (None, 0))):
            raise ValueError('字段核对回执缺少一致的建议稿、资料或历史顺序。')
        previous[group] = (receipt['id'], receipt['generation'])
