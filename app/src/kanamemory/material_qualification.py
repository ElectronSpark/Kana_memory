"""Current field witnesses and app-owned questions for un-enrolled candidates.

The saved verification report is a historical review, not a certificate. This
resolver reads today's local resources and binds only the fields a question
uses. Neither model agreement nor a retained snapshot can grant eligibility.
"""
from copy import deepcopy
import json
import re

from .material_workflow import MaterialWorkflowStore, digest

VERSION = 1
CORE = ('family', 'domain', 'title', 'meaning')
BINDING = 'material_qualification'


def validate_binding(question):
    binding = question.get(BINDING)
    required = {'version', 'workflow_id', 'candidate_object_id', 'candidate_id', 'source_kind', 'source_locator', 'facets', 'source_facts', 'template'}
    if (not isinstance(binding, dict) or set(binding) != required or type(binding['version']) is not int or binding['version'] != VERSION
            or not isinstance(binding['workflow_id'], str) or not re.fullmatch('[0-9a-f]{64}', binding['workflow_id'])
            or binding['candidate_id'] != question.get('target_id')
            or not isinstance(binding['candidate_id'], str) or not re.fullmatch('material-candidate:[0-9a-f]{64}', binding['candidate_id'])
            or not isinstance(binding['candidate_object_id'], str) or not binding['candidate_object_id'].startswith('local:ai:')
            or binding['source_kind'] not in {'course_item', 'dictionary_sense'}
            or not isinstance(binding['source_locator'], dict) or set(binding['source_locator']) !=
                ({'course_id'} if binding['source_kind'] == 'course_item' else {'word_id', 'sense_id'})
            or any(not isinstance(value, str) or not 0 < len(value) <= 1024 for value in binding['source_locator'].values())
            or not isinstance(binding['facets'], dict) or not set(CORE) <= set(binding['facets']) <= {*CORE, 'reading'}
            or any(not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value) for value in binding['facets'].values())):
        raise ValueError('材料题目的字段依据无效。')
    if (not isinstance(binding['source_facts'], dict)
            or set(binding['source_facts']) - {'alternative_readings', 'accepted_meanings'}
            or any(not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value) for value in binding['source_facts'].values())):
        raise ValueError('材料题目的来源变体依据无效。')
    template = binding['template']
    if (not isinstance(template, dict) or template.get('kind') != question.get('kind')
            or set(template) != ({'factory', 'kind'} if template.get('factory') == 'attested-word-recall-v1' else {'factory', 'kind', 'sha256'})
            or template.get('factory') not in {'attested-word-recall-v1', 'exact-authored-template-v1'}
            or 'sha256' in template and (not isinstance(template['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', template['sha256']))):
        raise ValueError('材料题目的模板依据无效。')
    return binding


def bind_resources(store, *, repo=None, examples=None):
    previous = getattr(store, '_material_resources', (None, None))
    store._material_resources = (repo if repo is not None else previous[0],
                                 examples if examples is not None else previous[1])


def _resources(store, repo, examples):
    saved = getattr(store, '_material_resources', (None, None))
    return repo if repo is not None else saved[0], examples if examples is not None else saved[1]


def meaning_supported(candidate, known):
    """Exact attested gloss of one sense, never model paraphrase similarity.

    This proves a lexical sense, not that this sense applies in the user's
    sentence. Aliases are read only from the independently resolved source.
    """
    meaning = candidate.get('meaning', '')
    return bool(meaning and (meaning == known.get('meaning', '') or
        candidate.get('family') == known.get('family') == 'word'
        and meaning in known.get('accepted_meanings', [])))


def candidate_basis(store, identity, *, repo=None, examples=None):
    from .ai_materials import AIMaterialStore
    from .material_usage import project_proposal
    from .material_verification import collect_evidence, MaterialVerificationStore, proposal_content, current_dictionary_item
    repo, examples = _resources(store, repo, examples)
    job = MaterialWorkflowStore(store).get(identity)
    proposal = AIMaterialStore(store).get(job['proposal_id']) if job and job['proposal_id'] else None
    report = MaterialVerificationStore(store).get(identity) if proposal else None
    if not report or report['proposal_sha256'] != digest(proposal_content(proposal)):
        return []
    usable = project_proposal(store, proposal)
    if usable is None:
        return []
    allowed = {row['id'] for row in usable['items']}
    evidence, _ = collect_evidence(usable, repo=repo, store=store)
    result = []
    for index, candidate in enumerate(proposal['items']):
        if candidate['id'] not in allowed:
            continue
        from .material_reference import explicitly_blocked
        blocked = explicitly_blocked(store, proposal, 'item', candidate['id'])
        if blocked & set(CORE): continue
        from .material_senses import matches as source_matches, select_matches
        matches = select_matches(store, proposal, candidate, source_matches(candidate, evidence, repo))
        if len(matches) != 1:
            continue  # One gloss shared by distinct senses remains ambiguous.
        source, known = matches[0]
        locator = ({'word_id': known['word_id'], 'sense_id': known['sense_id']}
                   if source['kind'] == 'dictionary_sense' else {'course_id': source['object_id']})
        supported = {field: digest(candidate.get(field, '')) for field in (*CORE, 'reading')
                     if field not in blocked and (field == 'meaning' or candidate.get(field, '') == source['snapshot'].get(field, ''))}
        cid = job['candidate_ids'][index]
        revision = 'material-facets-v1-' + digest([cid, {field: supported.get(field) for field in CORE}])[:24]
        reading = candidate.get('reading', '') if 'reading' in supported else ''
        item = {'id': cid, 'revision': revision, 'family': candidate['family'],
                'domain': candidate.get('domain', ''), 'title': candidate['title'], 'meaning': candidate['meaning'],
                'reading': reading, 'abilities': ['understanding'] + (['reading'] if reading else []),
                'instruction_status': 'instruction_missing', 'material_status': 'ai_candidate',
                'material_workflow_id': identity, 'candidate_object_id': candidate['id']}
        result.append({'item': item, 'candidate': candidate, 'source': source, 'facets': supported, 'locator': locator,
                       'known': known or {}, 'blocked_facets': sorted(blocked)})
    return result


def _binding(basis, facets, template, source_facts=None):
    return {'version': VERSION, 'workflow_id': basis['item']['material_workflow_id'],
            'candidate_object_id': basis['candidate']['id'], 'candidate_id': basis['item']['id'],
            'source_kind': basis['source']['kind'], 'source_locator': basis['locator'],
            'facets': {field: basis['facets'][field] for field in facets},
            'source_facts': {key: digest(value) for key, value in (source_facts or {}).items()}, 'template': template}


def _finish(question, item, binding):
    from .assessment_contract import for_question
    from .learning_conditions import for_question as conditions
    question.update(target_id=item['id'], content_id=item['id'], revision=item['revision'],
                    content_revision=item['revision'], exposure_group=item['id'] + ':' + item['revision'])
    question[BINDING] = binding
    # Conditions from another target's graph must not masquerade as candidate
    # knowledge links. The frozen question still retains its actual stimulus.
    for key in ('assessment_contract', 'learning_conditions', 'learning_condition_source', 'learning_condition_revision'):
        question.pop(key, None)
    question.update(conditions(question))
    question['id'] = question['question_id'] = 'material-question:' + digest(question)
    question['assessment_contract'] = for_question(question)
    return question


def _word_question(basis, kind):
    from .learning_questions import KIND_ABILITIES, KIND_LABELS
    item = basis['item']
    reading = kind == 'vocab_reading'
    from .learning_content import _kana_identity
    if reading and (not item['reading'] or _kana_identity(item['reading']) == _kana_identity(item['title'])):
        return None
    answer = item['reading'] if reading else item['meaning']
    alternatives = list(dict.fromkeys(value for value in [*basis['known'].get('alternative_readings', []),
                        *basis['known'].get('accepted_readings', [])] if _kana_identity(value) != _kana_identity(item['reading']))) if reading else []
    accepted = [answer] if reading else list(dict.fromkeys([answer, *basis['known'].get('accepted_meanings', [])]))
    source_facts = {'alternative_readings': alternatives} if reading else {'accepted_meanings': accepted}
    # No guessed distractors or answer variants. An unfamiliar translation is
    # unresolved, not wrong. Unknown ruby, accent and TTS are never presented.
    q = {'family': 'word', 'kind': kind, 'ability': KIND_ABILITIES[kind],
         'dimension': KIND_ABILITIES[kind], 'label': KIND_LABELS[kind], 'prompt': item['title'],
         'task': (f"本课义项：{item['meaning']}。写出日语假名读法。" if reading else
                  '写出所列义项的中文或英文意思。也可显示答案后核对。'),
         'answer': answer, 'accepted_answers': accepted, 'alternative_answers': alternatives,
         'choices': [], 'options': [], 'input_mode': 'text', 'response': 'text',
         'audio_text': '', 'optional_audio_text': '', 'requires_audio': False,
         'support': {'safe': False, 'segments': []}, 'spans': [], 'guide': '',
         'feedback': answer, 'evidence_mode': 'primary', 'strict': reading,
         'known_wrong': [], 'guided': False, 'variant': 0}
    facets = (*CORE, 'reading') if reading else CORE
    return _finish(q, item, _binding(basis, facets, {'factory': 'attested-word-recall-v1', 'kind': kind}, source_facts))


def _template_questions(basis, *, frozen=False):
    if 'question_templates' in basis.get('blocked_facets', []): return []
    from .learning_content import get_learning_item
    from .learning_questions import build_learning_question, question_kinds
    source = basis['known'] if frozen else get_learning_item(basis['source']['object_id'])
    if source is None:
        return []
    result = []
    def submitted_fields(raw):
        from .ai_materials import TEMPLATE_FIELDS
        value = {key: deepcopy(raw[key]) for key in TEMPLATE_FIELDS if key in raw}
        mode = value.setdefault('input_mode', 'choice')
        for key, default in {'task': '', 'choices': [], 'accepted_answers': [], 'known_wrong': [],
                             'strict': mode in {'choice', 'reorder'}}.items():
            value.setdefault(key, default)
        return value
    for kind, proposed in basis['candidate'].get('question_templates', {}).items():
        if kind not in question_kinds(source):
            continue
        for raw in proposed:
            matches = [(index, template) for index, template in enumerate(source.get('question_templates', {}).get(kind, []))
                       if submitted_fields(raw) == submitted_fields(template)]
            if not matches:
                continue
            index, template = matches[0]
            q = build_learning_question(source, kind, variant=index)
            # An authored question may use reading in its stimulus, feedback,
            # support or scoring. These variants require that facet separately.
            if 'reading' not in basis['facets']:
                continue
            binding = _binding(basis, (*CORE, 'reading'),
                               {'factory': 'exact-authored-template-v1', 'kind': kind, 'sha256': digest(template)})
            result.append(_finish(q, basis['item'], binding))
    return result


def candidate_questions(store, identity, *, repo=None, examples=None, target_id=None, _bases=None):
    questions = []
    bases = candidate_basis(store, identity, repo=repo, examples=examples) if _bases is None else _bases
    for basis in bases:
        if target_id is not None and basis['item']['id'] != target_id:
            continue
        if basis['item']['family'] == 'word':
            questions.extend(q for kind in ('vocab_meaning', 'vocab_reading') if (q := _word_question(basis, kind)))
        questions.extend(_template_questions(basis))
    return questions


def qualification(store, question, *, repo=None, examples=None):
    binding = question.get(BINDING)
    if not isinstance(binding, dict) or binding.get('version') != VERSION or question.get('ai_generated') or question.get('guided'):
        return False
    identity = binding.get('workflow_id')
    if not isinstance(identity, str) or len(identity) != 64:
        return False
    from .material_eligibility import same_contract
    try:
        validate_binding(question)
        return any(same_contract(q, question) for q in candidate_questions(store, identity, repo=repo,
                    examples=examples, target_id=question.get('target_id')))
    except (ValueError, KeyError, TypeError):
        return False


def frozen_factory_matches(store, question, source):
    """Replay a saved material factory, not today's dictionary or model output."""
    if not isinstance(source, dict) or set(source) != {'material_candidate_basis'}: return False
    from .material_eligibility import same_contract
    from .material_verification import _evidence, PREFIX as REPORTS, SOURCE_FACT_FIELDS
    from .ai_materials import AIMaterialStore
    try:
        binding = validate_binding(question); basis = source['material_candidate_basis']
        job = MaterialWorkflowStore(store).get(binding['workflow_id'])
        proposal = AIMaterialStore(store).get(job['proposal_id']) if job else None
        candidate = next((r for r in proposal['items'] if r['id'] == binding['candidate_object_id']), None) if proposal else None
        if candidate != basis['candidate'] or basis['item']['id'] != question['target_id']: return False
        index = proposal['items'].index(candidate)
        if job['candidate_ids'][index] != question['target_id']: return False
        known = basis['known']; evidence = basis['source']
        if evidence != _evidence(evidence['kind'], known, tuple(evidence['snapshot'])): return False
        if any(basis['item'].get(k, '') != candidate.get(k, '') for k in CORE): return False
        if any(sha != digest(candidate.get(k, '')) for k, sha in basis['facets'].items()): return False
        if basis['item']['reading'] != (candidate['reading'] if 'reading' in basis['facets'] else ''): return False
        report = store.get_setting(REPORTS + job['id'], {})
        from .material_senses import PREFIX as CHOICES
        sources = report.get('evidence', []) + [json.loads(raw)['source'] for (raw,) in store._db.execute(
            'SELECT value_json FROM app_setting WHERE key GLOB ? AND json_extract(value_json,\'$.candidate_id\')=?',
            (CHOICES + '*', candidate['id']))]
        if evidence not in sources: return False
        facts = _evidence('source_facts', known, SOURCE_FACT_FIELDS)
        if facts not in report.get('evidence', []): return False
        options = _template_questions(basis, frozen=True)
        if basis['item']['family'] == 'word':
            options += [q for kind in ('vocab_meaning', 'vocab_reading') if (q := _word_question(basis, kind))]
        return any(same_contract(q, question) for q in options)
    except (ValueError, KeyError, TypeError, StopIteration):
        return False


def targets(store, identity, *, repo=None, examples=None):
    bases = candidate_basis(store, identity, repo=repo, examples=examples)
    questions = candidate_questions(store, identity, _bases=bases)
    return [{**deepcopy(row['item']), 'abilities': sorted({q['ability'] for q in questions if q['target_id'] == row['item']['id']})}
            for row in bases]


def fact_lessons(store, identity, *, repo=None, examples=None, scopes=None):
    """Short, attested facts; neither a complete lesson nor an admission grant.

    A resumed reading keeps its original facet scope. New evidence may make
    another field available, but cannot silently add it to an opened lesson.
    """
    result = []
    for basis in candidate_basis(store, identity, repo=repo, examples=examples):
        item = basis['item']
        if item['family'] != 'word':
            continue
        wanted = (scopes or {}).get(item['id'])
        fields = tuple(wanted) if wanted is not None else (*CORE, *(['reading'] if item['reading'] else []))
        if (not set(CORE) <= set(fields) <= {*CORE, 'reading'} or not set(fields) <= set(basis['facets'])):
            continue
        reading = item['reading'] if 'reading' in fields else ''
        scope = {'version': 1, 'workflow_id': identity, 'candidate_object_id': basis['candidate']['id'],
                 'candidate_id': item['id'], 'source_kind': basis['source']['kind'],
                 'source_locator': basis['locator'], 'facets': {field: basis['facets'][field] for field in fields}}
        result.append({**item, 'reading': reading, 'abilities': ['understanding'] + (['reading'] if reading else []),
            'dictionary_only': True, 'material_fact_scope': scope,
            'instruction': {'summary': item['meaning'], 'rule': item['meaning'],
                'steps': [item['title'], item['meaning']], 'example': item['title'],
                'translation': item['meaning'], 'prerequisite': '', 'contrast': ''}})
    return result


def fact_lesson_current(store, item, *, repo=None, examples=None):
    scope = item.get('material_fact_scope') or {}
    identity = scope.get('workflow_id')
    if not isinstance(identity, str):
        return False
    current = fact_lessons(store, identity, repo=repo, examples=examples,
                          scopes={item['id']: tuple(scope.get('facets', {}))})
    return any(row == item for row in current)
