"""Read-only content impact inventory, not a historical grading decision.

All rows are traversed. Budget exhaustion is explicit incomplete coverage, never
an empty/full-looking first page. Missing field provenance stays potential.
"""
from hashlib import sha256
import json
import time

from .feedback_bindings import projection
from .material_workflow import digest, dump


class Incomplete(Exception):
    pass


def walk(value, path=''):
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            if isinstance(child, (dict, list)): yield from walk(child, path + '/' + key)
    elif isinstance(value, list):
        for index, child in enumerate(value): yield from walk(child, path + '/' + str(index))


class Inventory:
    def __init__(self, db, inputs, *, seconds=20, max_rows=200000, max_hits=50000):
        self.db, self.inputs = db, inputs
        self.roots = {r['binding']['id']: r for r in inputs['changes']}
        self.scope = {key: {r['binding']['target_id']} - {''} for key, r in self.roots.items()}
        self.examples = {key: {r['binding']['resource_id']} if r['binding']['resource_kind'] == 'example' else set()
                         for key, r in self.roots.items()}
        self.rows, self.coverage, self.gaps = {}, {}, list(inputs.get('gaps', []))
        self.questions, self.events, self.opportunities, self.derived = {}, {}, {}, []
        self.deadline, self.max_rows, self.max_hits = time.monotonic() + seconds, max_rows, max_hits
        self.scanned = 0; self.fingerprint = sha256()
        self.tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def tick(self):
        self.scanned += 1
        if self.scanned > self.max_rows or len(self.rows) > self.max_hits or time.monotonic() > self.deadline:
            raise Incomplete('inventory_budget_exceeded')

    def rows_for(self, table, sql=None):
        if table not in self.tables:
            self.coverage[table] = {'rows': 0, 'state': 'absent'}
            return
        self.coverage[table] = {'rows': 0, 'state': 'scanning'}
        for raw in self.db.execute(sql or ('SELECT * FROM ' + table + ' ORDER BY rowid')):
            self.tick(); row = dict(raw)
            self.coverage[table]['rows'] += 1
            self.fingerprint.update(dump([table, row]).encode('utf-8'))
            yield row
        self.coverage[table]['state'] = 'complete'

    def add(self, kind, identity, root, *, certainty='potential', reason, snapshot=None, target='', state='', version='unknown'):
        self.tick()
        key = digest([kind, identity, root, reason])
        self.rows[key] = {'id': key, 'kind': kind, 'locator': identity, 'binding_id': root,
            'certainty': certainty, 'reason': reason, 'target_id': target, 'state': state,
            'version_match': version, 'snapshot_sha256': digest(snapshot) if snapshot is not None else ''}
        return key

    def resources(self):
        # Both enabled resources and retained/inactive originals are included.
        pack = self.inputs['pack'] or {'examples': [], 'references': []}
        custom = self.inputs['custom_pack'] or {'examples': [], 'references': []}
        for key, change in self.roots.items():
            for index, link in enumerate(change.get('lexical_links', [])):
                self.scope[key].add(link['word_id'])
                self.add('reference', 'lexical:' + change['binding']['resource_id'] + ':' + str(index), key,
                    certainty='declared_resource', reason='lexical_word_sense_link', snapshot=link, target=link['word_id'])
        for label, source in (('active_examples', pack), ('retained_examples', custom)):
            count = 0
            for e in source.get('examples', []):
                self.tick(); count += 1
                for key, change in self.roots.items():
                    b = change['binding']
                    if b['resource_kind'] == 'lexical_example' and (b['resource_id'] in e.get('lexical_review_ids', []) or any(
                            p.get('review_id') == b['resource_id'] or p.get('original', {}).get('example_id') == b['resource_id']
                            for p in e.get('lexical_provenance', []))):
                        self.examples[key].add(e['id'])
                        self.add('resource', label + ':' + e['id'], key, certainty='declared_resource',
                            reason='lexical_provenance', snapshot=e)
            self.coverage[label] = {'rows': count, 'state': 'complete' if source else 'absent'}
        for label, source in (('active_references', pack), ('retained_references', custom)):
            count = 0
            for ref in source.get('references', []):
                self.tick(); count += 1
                for key, change in self.roots.items():
                    b = change['binding']
                    hit = (b['resource_kind'] in {'example', 'lexical_example'} and ref.get('example_id') in self.examples[key]
                           or b['resource_kind'] == 'reference' and ref.get('id') == b['resource_id']
                           or b['resource_kind'] == 'lesson' and ref.get('target_id') == b['resource_id'])
                    if not hit: continue
                    self.scope[key].add(ref['target_id'])
                    # Also inspect questions where the affected rule/reference
                    # supplied background context, without claiming that other
                    # targets' scores measured this rule.
                    self.examples[key].add(ref['example_id'])
                    self.add('reference', label + ':' + ref['id'], key, certainty='declared_resource',
                        reason='example_target_reference', snapshot=ref, target=ref['target_id'],
                        version=('same' if str(ref.get('example_revision')) == b['resource_revision'] else 'unknown'))
                    if b['resource_kind'] in {'example', 'lexical_example'} and b['field'] == 'text':
                        self.add('anchor', label + ':' + ref['id'], key, certainty='declared_resource',
                            reason='text_requires_reanchoring', snapshot=ref, target=ref['target_id'])
            self.coverage[label] = {'rows': count, 'state': 'complete'}
        items = [(i, 'catalogue') for i in self.inputs['items']]
        for row in self.rows_for('learning_content'):
            items.append((json.loads(row['content_json']), 'enrolled'))
        for item, origin in items:
            self.tick()
            for key, change in self.roots.items():
                if any(item.get('word_id') == link['word_id'] and (not link.get('sense_id') or item.get('sense_id') == link['sense_id'])
                       for link in change.get('lexical_links', [])):
                    self.scope[key].add(item['id'])
                if item.get('id') in self.scope[key]:
                    self.add('teaching', origin + ':' + item['id'], key, reason='target_field_dependency_missing', snapshot=item, target=item['id'])
                    for path, template in walk(item.get('question_templates', {})):
                        if not {'prompt', 'task', 'answer'}.intersection(template): continue
                        self.add('template', origin + ':' + item['id'] + path, key,
                            reason='template_field_dependency_missing', snapshot=template, target=item['id'])
                for dep in item.get('depends_on', []):
                    if dep in self.scope[key]:
                        # A prerequisite is not evidence that the dependent answer
                        # was wrong; do not propagate it into unrelated schedules.
                        self.add('relation', origin + ':' + item['id'] + '->' + dep, key,
                            certainty='declared_resource', reason='prerequisite_background', snapshot=item, target=item['id'])
        self.coverage['catalogue'] = {'rows': len(items), 'state': 'complete'}
        for key, change in self.roots.items():
            b = change['binding']
            components = ['rendered_content']
            if b['field'] in {'text', 'reading', 'title', 'instruction.example', 'prompt', 'answer'}:
                components += ['sentence_annotations', 'tts_audio', 'question_support']
            for component in components:
                self.add('cache', component + ':' + b['resource_kind'] + ':' + b['resource_id'], key,
                    reason='derived_cache_version_required', state='review_required')

    def match_question(self, q):
        hits = []
        if not isinstance(q, dict): return hits
        target = q.get('target_id') or q.get('content_id') or ''
        practice = q.get('practice') or {}; meta = q.get('metadata') or {}
        example_ids = {q.get('learning_condition_source'), practice.get('example_id')}
        for key, change in self.roots.items():
            b = change['binding']; certainty, reason = '', ''
            original = change.get('resource') or {}
            if b['resource_kind'] == 'question' and q.get('id') == b['resource_id']:
                from .personal_question import addressable
                addressed = addressable(q) if b['version'] >= 2 else q
                certainty = 'declared_field' if digest(projection('question', addressed)) == b['source_sha256'] else 'declared_resource'
                reason = 'exact_question_identity'
            elif self.examples[key].intersection(example_ids):
                certainty, reason = 'declared_resource', 'explicit_example_identity'
            elif target in self.scope[key] or self.scope[key].intersection(practice.get('background_ids', [])):
                certainty, reason = 'potential', 'target_without_field_provenance'
            # A broad scope hit must not mask stronger sense/field provenance.
            # Component adjudication needs the exact frozen word source to
            # preserve independent kana that a reading correction did not alter.
            if certainty in {'', 'potential'} and b['resource_kind'] == 'lesson' and b['field'] in {'reading', 'meaning'}:
                declared = meta.get('content_source_v1', {})
                old_sense = (original.get('family') == 'word' and original.get('word_id') == target
                    and meta.get('original_reading') == original.get('reading')
                    and meta.get('meaning_hint') in {original.get('meaning'), *original.get('accepted_meanings', [])})
                if old_sense or declared.get('lesson_id') == b['resource_id']:
                    certainty, reason = 'declared_resource', 'exact_word_sense_source'
                    if declared.get(b['field']) == b['value']:
                        certainty, reason = 'declared_field', 'exact_word_sense_field'
            # Qualified material questions carry concrete original facet hashes.
            qualification = q.get('material_qualification') or {}
            locator = qualification.get('source_locator') or {}
            original = change.get('resource') or {}
            source_matches = (locator.get('course_id') == b['resource_id'] or
                bool(original.get('word_id') and locator.get('word_id') == original['word_id'] and locator.get('sense_id') == original.get('sense_id')))
            if source_matches and qualification.get('facets', {}).get(b['field']) == digest(b['value']):
                certainty, reason = 'declared_field', 'qualified_source_facet'
            from .question_dependencies import matching_uses
            uses = matching_uses(q, b, change.get('resource'))
            if uses:
                certainty, reason = 'declared_field', 'factory_field_uses:' + ','.join(uses)
            if not certainty: continue
            version = q.get('revision') or q.get('content_revision') or meta.get('content_version')
            match = ('unknown' if not version or b['resource_kind'] not in {'lesson', 'question'} else
                     'same' if str(version) == b['resource_revision'] else 'different')
            if reason == 'exact_question_identity': match = 'same' if certainty == 'declared_field' else 'different'
            elif reason == 'exact_word_sense_field': match = 'same'
            elif reason == 'qualified_source_facet': match = 'same'
            elif reason.startswith('factory_field_uses:'): match = 'same'
            hits.append((key, certainty, reason, match, target))
        return hits

    def question(self, q, locator, state, *, result=None):
        # Frozen actually displayed view/contract can differ from today's item.
        effective = q
        if isinstance(result, dict) and isinstance(result.get('material_view'), dict):
            from .material_review import QUESTION_VIEW_FIELDS
            effective = {**q, **{k: v for k, v in result['material_view'].items() if k in QUESTION_VIEW_FIELDS}}
        hits = self.match_question(effective)
        if effective != q:
            for key, certainty, reason, match, target in self.match_question(q):
                self.add('question', locator + ':stored_original', key, certainty=certainty, reason='stored_original:' + reason,
                    snapshot=q, target=target, state='not_actual_display', version=match)
        for key, certainty, reason, match, target in hits:
            self.add('question', locator, key, certainty=certainty, reason=reason, snapshot=effective,
                target=target, state=state, version=match)
        if q.get('id'): self.questions.setdefault(q['id'], []).extend(hits)
        return hits

    @staticmethod
    def event_links(node):
        ids = {node[k] for k in ('event_id', 'evidence_event_id', 'trigger_event_id') if isinstance(node.get(k), str)}
        ids.update(x for x in node.get('evidence_ids', []) if isinstance(x, str))
        return ids

    def component_links(self, locator, value, hits):
        for path, data in walk(value):
            links = self.event_links(data)
            for eid in links:
                if hits: self.events.setdefault(eid, []).extend(hits)
            if hits and (links or any(k in data for k in ('target_id', 'kana_id', 'content_id'))):
                for root, certainty, reason, match, target in hits:
                    self.add('attribution', locator + path, root, certainty=certainty,
                        reason='spelling_component:' + reason, snapshot=data, target=data.get('target_id') or data.get('kana_id') or data.get('content_id') or target,
                        version=match)

    def history(self, locator, hits, snapshot, *, event_ids=()):
        for key, certainty, reason, match, target in hits:
            self.add('history', locator, key, certainty=certainty, reason='frozen_question:' + reason,
                snapshot=snapshot, target=target, state='not_adjudicated', version=match)
        for eid in event_ids:
            if hits: self.events.setdefault(eid, []).extend(hits)

    def persisted_questions(self):
        for row in self.rows_for('learning_question'):
            q = json.loads(row['snapshot_json']); result = json.loads(row['result_json']) if row['result_json'] else None
            hits = self.question(q, 'learning_question:' + row['question_id'], row['status'], result=result)
            self.questions[row['question_id']] = hits
        for row in self.rows_for('learning_event'):
            self.history('learning_event:' + row['event_id'], self.questions.get(row['question_id'], []), row,
                event_ids=[row['event_id'], 'course:' + row['event_id']])
        ordinary = {}
        question_sql = '''SELECT q.*,a.event_id AS answer_event,w.attempt_id AS spelling_event
            FROM session_question q LEFT JOIN session_answer a USING(session_id,question_index)
            LEFT JOIN word_spelling_attempt w USING(session_id,question_index)
            ORDER BY q.session_id,q.question_index'''
        for row in self.rows_for('session_question', question_sql):
            key = (row['session_id'], row['question_index']); q = json.loads(row['snapshot_json'])
            hits = self.question(q, 'session_question:' + row['session_id'] + ':' + str(row['question_index']),
                'completed' if row['answer_event'] or row['spelling_event'] else 'saved_ungraded')
            if hits: ordinary[key] = hits
        joins = {row['event_id']: ordinary.get((row['session_id'], row['question_index']), [])
                 for row in self.rows_for('session_answer')}
        card_targets = {row['card_id']: row['content_id'] for row in self.rows_for('study_card')}
        for row in self.rows_for('review_event'):
            hits = joins.get(row['event_id'], [])
            if row['event_id'] not in joins:
                # The row has been enumerated; absent old question provenance
                # is uncertainty about this target, not an incomplete scan of
                # every unrelated resource. Never infer a corrected grade.
                target = card_targets.get(row['card_id'])
                if target is None: raise Incomplete('legacy_event_card_unavailable')
                hits = [(key, 'potential', 'legacy_event_without_question', 'unknown', target)
                        for key, targets in self.scope.items() if target in targets]
            self.history('review_event:' + row['event_id'], hits, row, event_ids=[row['event_id'], 'kana:' + row['event_id']])
            log = json.loads(row['review_log_json'])
            if isinstance(log.get('word_spelling_evidence'), dict):
                self.component_links('review_event:' + row['event_id'], log['word_spelling_evidence'], hits)
        for row in self.rows_for('word_spelling_attempt'):
            hits = ordinary.get((row['session_id'], row['question_index']), [])
            self.history('word_spelling_attempt:' + row['attempt_id'], hits, row, event_ids=[row['attempt_id']])
            if hits: self.opportunities['word:' + row['attempt_id']] = hits
            evidence = json.loads(row['evidence_json'])
            self.component_links(row['attempt_id'], evidence, hits)

    def settings(self):
        # Explicit learning namespaces only: never inspect credential settings.
        names = ('ai_practice_v1', 'ai_tutor_v1', 'kana_rule_probes_v1', 'shared_examples_v1', 'material_usage_v1')
        prefixes = ('retention_check_v', 'retention_dialogue_v', 'retention_ai_v', 'material_', 'ability_teaching_v')
        # This SQL is a fixed application allowlist; no settings or model text
        # supply table names or predicates. Credential rows never enter Python.
        selected_sql = "SELECT key,value_json FROM app_setting WHERE key IN ('ai_practice_v1','ai_tutor_v1','kana_rule_probes_v1','shared_examples_v1','material_usage_v1') OR key GLOB 'retention_check_v*' OR key GLOB 'retention_dialogue_v*' OR key GLOB 'retention_ai_v*' OR key GLOB 'material_*' OR key GLOB 'ability_teaching_v*' ORDER BY key"
        for row in self.rows_for('app_setting', selected_sql):
            key = row['key']
            if key not in names and not key.startswith(prefixes): continue
            value = json.loads(row['value_json'])
            for path, node in walk(value):
                self.tick()
                q = node.get('question')
                if isinstance(q, dict) and ('target_id' in q or 'content_id' in q):
                    hits = self.question(q, key + path, str(node.get('status') or ('completed' if node.get('completed') else 'saved')),
                                         result=node.get('result'))
                    if node.get('result') is not None:
                        entry_id = node.get('id', '')
                        self.history(key + path, hits, node['result'], event_ids=[entry_id, *[p + entry_id for p in ('ai:', 'tutor:', 'diagnostic:')]])
                # Explicit references in frozen support, suggestions, chat and
                # teaching materials stay distinct from an answer opportunity.
                ids = {node.get(k) for k in ('target_id', 'content_id', 'course_id', 'example_id', 'condition_source', 'object_id') if isinstance(node.get(k), str)}
                if ('family' in node or 'instruction' in node) and isinstance(node.get('id'), str): ids.add(node['id'])
                ids.update(x for x in node.get('target_ids', []) if isinstance(x, str))
                linked = self.event_links(node)
                if linked: self.derived.append((key + path, linked, digest(node)))
                for root in self.roots:
                    if ids.intersection(self.scope[root] | self.examples[root]):
                        self.add('derived', key + path, root, reason='stored_context_dependency', snapshot=node)

    def evidence(self):
        for row in self.rows_for('learning_evidence'):
            snapshot = json.loads(row['snapshot_json'])
            frozen = snapshot.get('assessment_question')
            hits = self.match_question(frozen) if isinstance(frozen, dict) else []
            if not hits: hits = list(self.events.get(row['event_id'], []))
            if not hits: hits = list(self.opportunities.get(snapshot.get('opportunity', {}).get('id'), []))
            if not hits:
                hits = [(root, 'potential', 'evidence_question_id_without_version', 'unknown', target)
                        for root, _, _, _, target in self.questions.get(snapshot.get('question_id'), [])]
            hits = [(root, certainty, reason, match, row['target_id']) for root, certainty, reason, match, _ in hits]
            for root in self.roots:
                if not any(h[0] == root for h in hits) and (row['target_id'] in self.scope[root] or snapshot.get('condition_source') in self.examples[root]):
                    hits.append((root, 'potential', 'evidence_missing_full_question', 'unknown', row['target_id']))
            self.history('learning_evidence:' + row['event_id'], hits, row, event_ids=[row['event_id']])
        for locator, event_ids, fingerprint in self.derived:
            for root, certainty, reason, match, target in {h for eid in event_ids for h in self.events.get(eid, [])}:
                self.add('derived', locator, root, certainty=certainty, reason='derived_evidence_link:' + reason,
                    snapshot=fingerprint, version=match, target=target)
        for row in self.rows_for('learning_aux_adjustment'):
            for root, certainty, reason, match, target in self.events.get(row['event_id'], []):
                self.add('adjustment', row['adjustment_id'], root, certainty=certainty, reason='linked_auxiliary_event:' + reason,
                    snapshot=row, target=row['target_id'], state='not_replayed', version=match)
        for row in self.rows_for('tutor_memory_claim'):
            linked = {h for eid in json.loads(row['evidence_ids_json']) for h in self.events.get(eid, [])}
            targets = set(json.loads(row['target_ids_json']))
            for root in self.roots:
                matches = [h for h in linked if h[0] == root]
                if matches:
                    for _, certainty, reason, match, target in matches:
                        self.add('profile', row['claim_id'], root, certainty=certainty, reason='profile_evidence:' + reason,
                            snapshot=row, state=row['status'], version=match, target=target)
                elif targets.intersection(self.scope[root]):
                    self.add('profile', row['claim_id'], root, certainty='potential', reason='profile_target_only', snapshot=row, state=row['status'])

    def run(self):
        completed = False; graph = None
        try:
            self.resources(); self.persisted_questions(); self.settings(); self.evidence()
            from .feedback_graph_review import create
            graph = create(self.inputs, self.db, self.tick)
            completed = True
        except Incomplete as exc: self.gaps.append(str(exc))
        except (ValueError, KeyError, TypeError, RecursionError) as exc:
            self.gaps.append('unreadable_inventory:' + type(exc).__name__)
        rows = sorted(self.rows.values(), key=lambda r: (r['kind'], r['locator'], r['binding_id'], r['reason']))
        result = {'version': 1, 'rows': rows, 'coverage': self.coverage, 'gaps': sorted(set(self.gaps)),
            'enumeration_complete': completed and not self.gaps, 'has_potential': any(r['certainty'] == 'potential' for r in rows),
            'counts': {kind: len({r['locator'] for r in rows if r['kind'] == kind}) for kind in sorted({r['kind'] for r in rows})}, 'scanned_rows': self.scanned,
            'inventory_sha256': self.fingerprint.hexdigest(), 'input_sha256': digest(self.inputs),
            'assessment_eligible': False, 'activation_eligible': False}
        if graph is not None: result['graph_review'] = graph
        return result
