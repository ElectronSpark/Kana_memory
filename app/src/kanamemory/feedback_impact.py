"""Persistent local impact previews; no content or learner state is changed."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

from . import app_clock
from .content_impact import Inventory
from .feedback_analysis import feedback_version
from .feedback_bindings import current_resource, projection, validate_binding
from .material_workflow import digest, dump
from .retention_ai import _hash
from .tutor_store import _text

PREFIX = 'feedback_impact_v1:'
MAX_BYTES = 8 * 1024 * 1024


def changes_for(source, phase):
    if phase == 'analysis':
        from .feedback_review import proposals
        return [{'binding': r['binding'], 'new_value': r['suggestion']} for r in proposals(source)]
    if phase != 'revision': raise ValueError('未知修订来源。')
    from .feedback_revision import validate
    source = validate(source)
    if source['status'] != 'completed': raise ValueError('修订稿尚未完成。')
    items = {i['binding']['id']: i for i in source['request']['issues']}
    return [{'binding': deepcopy(items[r['binding_id']]['binding']), 'new_value': r['new_value']}
            for r in source['response']['decisions'] if r['decision'] == 'revise']


def token(db):
    return (db.execute('PRAGMA data_version').fetchone()[0], db.total_changes)


def calculate(path, inputs, **budget):
    """A worker owns its read-only connection; the UI's connection isn't shared."""
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        db.execute('BEGIN')
        return Inventory(db, inputs, **budget).run()
    finally: db.close()


def validate(value):
    if (not isinstance(value, dict) or set(value) != {'version', 'id', 'phase', 'source_id', 'source_sha256',
            'feedback_id', 'feedback_revision', 'changes', 'created_at', 'report', 'changed_during_scan'}
            or type(value['version']) is not int or value['version'] != 1 or value['phase'] not in {'analysis', 'revision'}
            or not all(_hash(value[k]) for k in ('id', 'source_id', 'source_sha256', 'feedback_revision'))
            or value['id'] != digest({k: v for k, v in value.items() if k != 'id'})
            or type(value['changed_during_scan']) is not bool or not isinstance(value['changes'], list)
            or not 1 <= len(value['changes']) <= 12 or len(dump(value).encode('utf-8')) > MAX_BYTES):
        raise ValueError('影响报告格式无效或过大。')
    from datetime import datetime
    if datetime.fromisoformat(value['created_at']).tzinfo is None: raise ValueError('影响报告缺少时间。')
    from .feedback_store import _id
    _id(value['feedback_id'])
    roots = set()
    for c in value['changes']:
        if not isinstance(c, dict) or set(c) != {'binding', 'new_value'}: raise ValueError('影响变更无效。')
        b = validate_binding(c['binding']); _text(c['new_value'], 2400)
        if b['id'] in roots or c['new_value'] == b['value']: raise ValueError('影响变更重复或未变。')
        roots.add(b['id'])
    report = value['report']
    if (not isinstance(report, dict) or set(report) - {'graph_review'} != {'version', 'rows', 'coverage', 'gaps', 'enumeration_complete',
            'has_potential', 'counts', 'scanned_rows', 'inventory_sha256', 'input_sha256', 'assessment_eligible', 'activation_eligible'}
            or report['version'] != 1 or report['assessment_eligible'] is not False or report['activation_eligible'] is not False
            or not all(_hash(report[k]) for k in ('inventory_sha256', 'input_sha256'))
            or not isinstance(report['rows'], list) or len(report['rows']) > 50001
            or type(report['scanned_rows']) is not int or report['scanned_rows'] < 0
            or not isinstance(report['gaps'], list) or not isinstance(report['coverage'], dict)
            or type(report['enumeration_complete']) is not bool):
        raise ValueError('影响范围无效；报告不能提供评分或激活权限。')
    for gap in report['gaps']: _text(gap, 2048)
    for name, c in report['coverage'].items():
        _text(name, 100)
        if (not isinstance(c, dict) or set(c) != {'rows', 'state'} or type(c['rows']) is not int or c['rows'] < 0
                or c['state'] not in {'complete', 'absent', 'scanning'}): raise ValueError('影响枚举状态无效。')
    if report['enumeration_complete'] and (report['gaps'] or any(c['state'] == 'scanning' for c in report['coverage'].values())):
        raise ValueError('未完成的枚举不能标为完整。')
    seen = set()
    for r in report['rows']:
        if (not isinstance(r, dict) or set(r) != {'id', 'kind', 'locator', 'binding_id', 'certainty', 'reason',
                'target_id', 'state', 'version_match', 'snapshot_sha256'} or r['binding_id'] not in roots
                or r['id'] != digest([r['kind'], r['locator'], r['binding_id'], r['reason']]) or r['id'] in seen
                or r['kind'] not in {'resource', 'reference', 'anchor', 'teaching', 'template', 'relation', 'cache',
                                     'question', 'history', 'attribution', 'derived', 'adjustment', 'profile'}
                or r['certainty'] not in {'potential', 'declared_resource', 'declared_field'}
                or r['version_match'] not in {'unknown', 'same', 'different'}
                or r['snapshot_sha256'] and not _hash(r['snapshot_sha256'])):
            raise ValueError('影响条目无效。')
        for key in ('kind', 'locator', 'reason'): _text(r[key], 4096)
        for key in ('target_id', 'state'): _text(r[key], 2048, blank=True)
        seen.add(r['id'])
    counts = {kind: len({r['locator'] for r in report['rows'] if r['kind'] == kind}) for kind in {r['kind'] for r in report['rows']}}
    if report['counts'] != counts or report['has_potential'] != any(r['certainty'] == 'potential' for r in report['rows']):
        raise ValueError('影响汇总不一致。')
    if 'graph_review' in report:
        from .feedback_graph_review import validate as validate_graph
        graph = validate_graph(report['graph_review'])
        if graph['snapshot']['changes'] != value['changes']: raise ValueError('图谱审查与建议稿范围不一致。')
    return deepcopy(value)


class FeedbackImpactStore:
    def __init__(self, analysis):
        self.analysis, self.store, self.db = analysis, analysis.store, analysis.db

    def source(self, phase, identity):
        if phase == 'analysis': return self.analysis.get(identity)
        if phase != 'revision': raise ValueError('未知影响来源。')
        from .feedback_review import FeedbackReviewStore
        from .feedback_revision import FeedbackRevisionStore
        return FeedbackRevisionStore(FeedbackReviewStore(self.analysis)).get(identity)

    def get(self, phase, identity):
        row = self.db.execute("SELECT value_json FROM app_setting WHERE key GLOB ? AND json_extract(value_json,'$.phase')=? "
            "AND json_extract(value_json,'$.source_id')=? ORDER BY rowid DESC LIMIT 1", (PREFIX + '*', phase, identity)).fetchone()
        return validate(json.loads(row[0])) if row else None

    def prepare(self, phase, identity):
        source = self.source(phase, identity)
        if not source: raise ValueError('找不到建议稿。')
        changes = changes_for(source, phase)
        if not changes: raise ValueError('建议稿没有内容改动。')
        from .learning_content import learning_items
        before = token(self.db); gaps = []
        resources = self.analysis.resources
        roots = []
        for c in changes:
            resource = current_resource(c['binding'], **resources)
            if resource is None or digest(projection(c['binding']['resource_kind'], resource)) != c['binding']['source_sha256']:
                gaps.append('changed_or_missing_source:' + c['binding']['id'])
            root = {**deepcopy(c), 'resource': resource, 'lexical_links': []}
            if c['binding']['resource_kind'] == 'lexical_example':
                repo = resources.get('repo')
                if repo is not None and callable(getattr(repo, 'example_links', None)):
                    root['lexical_links'] = repo.example_links(c['binding']['resource_id'])
                else: gaps.append('lexical_reverse_links_unavailable:' + c['binding']['resource_id'])
            elif c['binding']['resource_kind'] == 'example' and c['binding']['field'] == 'text' and resource:
                lexical_ids = set(resource.get('lexical_review_ids', []))
                lexical_ids.update(p.get('review_id') for p in resource.get('lexical_provenance', []) if p.get('review_id'))
                repo = resources.get('repo')
                for lexical_id in sorted(lexical_ids):
                    lexical = repo.get_example(lexical_id) if repo is not None else None
                    if lexical is None or not callable(getattr(repo, 'example_links', None)):
                        gaps.append('lexical_reverse_links_unavailable:' + lexical_id); continue
                    root.setdefault('lexical_owners', []).append({'id': lexical_id,
                        'source_sha256': digest(projection('lexical_example', lexical))})
                    root['lexical_links'].extend({**link, 'lexical_example_id': lexical_id,
                        'lexical_source_sha256': digest(projection('lexical_example', lexical))}
                        for link in repo.example_links(lexical_id))
            roots.append(root)
        examples = resources.get('examples')
        if examples is None:
            pack = None; gaps.append('shared_example_inventory_unavailable')
        else: pack = examples.display_pack() if callable(getattr(examples, 'display_pack', None)) else examples.export_pack(include_bundled=True)
        # Several audited dictionary examples can share one sentence identity.
        # Freeze every related lexical owner before replacing any one of them.
        for root in roots:
            b = root['binding']
            if b['resource_kind'] != 'lexical_example' or b['field'] != 'text': continue
            siblings = {identity for e in (pack or {}).get('examples', [])
                if b['resource_id'] in e.get('lexical_review_ids', []) for identity in e.get('lexical_review_ids', [])} - {b['resource_id']}
            repo = resources.get('repo')
            for identity in sorted(siblings):
                sibling = repo.get_example(identity) if repo is not None else None
                if sibling is None or not callable(getattr(repo, 'example_links', None)):
                    gaps.append('lexical_reverse_links_unavailable:' + identity); continue
                root.setdefault('lexical_owners', []).append({'id': identity,
                    'source_sha256': digest(projection('lexical_example', sibling))})
                root['lexical_links'].extend({**link, 'lexical_example_id': identity,
                    'lexical_source_sha256': digest(projection('lexical_example', sibling))}
                    for link in repo.example_links(identity))
        from .personal_content_versions import resolve as personal_version, teaching_source
        catalogue_items = [personal_version(self.store, teaching_source(i)) for i in learning_items()]
        items = {r['id']: r for r in catalogue_items}
        if examples is not None:
            items.update({key: personal_version(self.store, teaching_source(i))
                          for key, i in getattr(examples, 'items', {}).items()})
        inputs = {'changes': roots, 'items': list(items.values()), 'catalogue_items': catalogue_items, 'pack': pack,
            'custom_pack': self.store.get_setting('shared_examples_v1', None), 'gaps': gaps}
        parent = source if phase == 'analysis' else self.analysis.get(source['request']['parent_id'])
        if not parent: raise ValueError('影响建议稿缺少原报告。')
        f = parent['request']['feedback']
        envelope = {'version': 1, 'phase': phase, 'source_id': identity,
            'source_sha256': digest(source), 'feedback_id': f['id'], 'feedback_revision': f['input_revision'],
            'changes': changes, 'created_at': app_clock.utc_now().isoformat(), 'changed_during_scan': False}
        return {'envelope': envelope, 'inputs': inputs, 'token': before, 'path': str(self.store.path)}

    def finish(self, prepared, report):
        e = prepared['envelope']
        if report['input_sha256'] != digest(prepared['inputs']): raise ValueError('影响计算使用了其他输入。')
        if report.get('graph_review'):
            snapshot = report['graph_review']['snapshot']
            if (snapshot['visible_pack_sha256'] != digest(prepared['inputs']['pack'])
                    or snapshot['retained_pack_sha256'] != digest(prepared['inputs']['custom_pack'])):
                raise ValueError('图谱审查使用了不同内容层。')
        value = {**e, 'report': report, 'changed_during_scan': False}
        value['id'] = digest(value); value = validate(value)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            source = self.source(e['phase'], e['source_id']); feedback = self.analysis.feedback.get(e['feedback_id'])
            parent = source if e['phase'] == 'analysis' else self.analysis.get(source['request']['parent_id']) if source else None
            changed = (token(self.db) != prepared['token'] or not source or digest(source) != e['source_sha256']
                or not feedback or feedback_version(feedback) != e['feedback_revision']
                or not parent or not self.analysis.feedbacks_current(parent['request']))
            # Only this status and its enclosing identity change under lock.
            # Full structural validation above never runs in the write lock.
            value['changed_during_scan'] = bool(changed)
            value['id'] = digest({k: v for k, v in value.items() if k != 'id'})
            self.db.execute('INSERT OR IGNORE INTO app_setting VALUES (?,?)',
                            (PREFIX + value['id'], dump(value)))
            guarded_token = token(self.db)
        # Short-lived readiness proof for the next local planning transaction.
        # Restored reports remain readable but must be rescanned, not trusted
        # as current merely because their saved hash is self-consistent.
        guards = getattr(self.store, '_feedback_impact_guards', {})
        guards[value['id']] = {'token': guarded_token, 'resources': {
            c['binding']['id']: digest(c['resource']) for c in prepared['inputs']['changes']}}
        self.store._feedback_impact_guards = dict(list(guards.items())[-8:])
        return value


def validate_database(db):
    from .feedback_analysis import PREFIX as ANALYSIS, validate as analysis_value
    from .feedback_revision import PREFIX as REVISION, validate as revision_value
    for key, raw in db.execute('SELECT key,value_json FROM app_setting WHERE key GLOB ?', (PREFIX + '*',)):
        value = validate(json.loads(raw)); phase = value['phase']
        row = db.execute('SELECT value_json FROM app_setting WHERE key=?',
            ((ANALYSIS if phase == 'analysis' else REVISION) + value['source_id'],)).fetchone()
        source = (analysis_value if phase == 'analysis' else revision_value)(json.loads(row[0])) if row else None
        if not source or key != PREFIX + value['id'] or digest(source) != value['source_sha256'] or changes_for(source, phase) != value['changes']:
            raise ValueError('影响报告缺少一致的原建议稿。')
        if phase == 'revision':
            parent = db.execute('SELECT value_json FROM app_setting WHERE key=?', (ANALYSIS + source['request']['parent_id'],)).fetchone()
            source = analysis_value(json.loads(parent[0])) if parent else None
        f = source['request']['feedback'] if source else {}
        if value['feedback_id'] != f.get('id') or value['feedback_revision'] != f.get('input_revision'):
            raise ValueError('影响报告反馈版本不一致。')
