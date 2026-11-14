"""Publishing gates use original synthetic lessons, never private course prose."""
from copy import deepcopy

import pytest

from build_varied_practice import digest
from compile_reviewed_question_batch import FORMAT, compile_batch
from support.multi_cloze_integration_support import CID, BACKGROUND, lesson, background_lesson


def batch():
    raw = deepcopy(lesson()['question_templates']['grammar_cloze'][0])
    practice = raw.pop('practice')
    row = {'slot_id': 'synthetic|multi|1', 'target_id': CID, 'kind': 'grammar_cloze',
           'question': raw, 'task_family': 'multi_gap', 'context_text': 'original synthetic trip',
           'target_spans': practice['target_spans'], 'background_ids': [BACKGROUND],
           'assessed_ids': [CID, BACKGROUND], 'coverage_scope': 'cumulative',
           'source_refs': ['synthetic:authored-original']}
    authored = {'format': FORMAT, 'version': 1, 'revision': 'synthetic-v1',
                'expected_count': 1, 'questions': [row]}
    # This inert fixture tests the gate; it is not a real semantic review.
    review = {'format': 'kanamemory-exact-question-review', 'version': 1,
              'reviewer': 'gpt-6.1-sol', 'authoring_sha256': digest(authored),
              'questions': [{'slot_id': row['slot_id'], 'candidate_sha256': digest(row),
                             'status': 'pass', 'evidence_refs': ['synthetic:test-only']} ]}
    return authored, review


def test_append_preserves_exact_old_templates_and_repeat_compile_is_idempotent():
    authored, review = batch()
    items = [lesson(), background_lesson()]
    first = compile_batch(authored, review, items, None)
    frozen = deepcopy(first['items'][CID]['templates'])
    second = compile_batch(authored, review, items, first)
    assert second['items'][CID]['templates'] == frozen
    assert second['provenance'] == first['provenance']
    raw = frozen['grammar_cloze'][0]
    assert raw['practice']['assessed_ids'] == [CID, BACKGROUND]
    assert raw['practice']['reviewed'] is True
    provenance = first['provenance'][0]
    assert provenance['candidate_sha256'] == digest(authored['questions'][0])
    assert provenance['review_sha256'] == digest(review['questions'][0])
    assert 'source_refs' not in provenance
    assert first['coverage']['expansion_0291']['authoring_sha256'] == digest(authored)
    assert first['coverage']['expansion_0291']['review_sha256'] == digest(review)


@pytest.mark.parametrize('damage', ['stale-answers', 'stale-background', 'missing-review', 'uncertain',
                                    'missing-slot', 'duplicate-review', 'wrong-reviewer'])
def test_partial_or_stale_approval_never_publishes(damage):
    authored, review = batch()
    if damage == 'stale-answers':
        authored['questions'][0]['question']['multi_cloze']['accepted_combinations'].pop()
    elif damage == 'stale-background':
        authored['questions'][0]['background_ids'] = []
    elif damage == 'missing-review':
        review['questions'] = []
    elif damage == 'uncertain':
        review['questions'][0]['status'] = 'uncertain'
    elif damage == 'missing-slot':
        authored['expected_count'] = 2
    elif damage == 'duplicate-review':
        review['questions'] *= 2
    else:
        review['reviewer'] = 'unverified-generator'
    with pytest.raises(ValueError):
        compile_batch(authored, review, [lesson(), background_lesson()], None)


def test_self_asserted_review_flag_in_a_draft_cannot_grant_authority():
    authored, review = batch()
    authored['questions'][0]['question']['practice_source'] = {'approved': True}
    review['authoring_sha256'] = digest(authored)
    review['questions'][0]['candidate_sha256'] = digest(authored['questions'][0])
    with pytest.raises(ValueError):
        compile_batch(authored, review, [lesson(), background_lesson()], None)


def test_retirement_requires_exact_review_and_replacement_and_keeps_original():
    from kanamemory.material_review import item_fingerprint
    authored, review = batch()
    item = lesson()
    raw = item['question_templates']['grammar_cloze'][0]
    retirement = {'slot_id': 'synthetic-old-slot', 'target_id': CID, 'kind': 'grammar_cloze',
        'item_fingerprint': item_fingerprint(item), 'template_sha256': digest(raw),
        'replacement_slot_id': authored['questions'][0]['slot_id']}
    authored['retirements'] = [retirement]
    review['authoring_sha256'] = digest(authored)
    with pytest.raises(ValueError):
        compile_batch(authored, review, [item, background_lesson()], None)
    review['retirements'] = [{'slot_id': retirement['slot_id'], 'candidate_sha256': digest(retirement),
                             'status': 'retire', 'evidence_refs': ['synthetic:explicit-retirement-review']}]
    result = compile_batch(authored, review, [item, background_lesson()], None)
    assert result['template_retirements'][CID][item_fingerprint(item)]['grammar_cloze'] == [digest(raw)]
    assert item['question_templates']['grammar_cloze'][0] == raw
    authored['retirements'][0]['template_sha256'] = '0' * 64
    review['authoring_sha256'] = digest(authored)
    review['retirements'][0]['candidate_sha256'] = digest(authored['retirements'][0])
    with pytest.raises(ValueError):
        compile_batch(authored, review, [item, background_lesson()], None)


# ------------------------------------------------------------------ optional authoring fields (design 11 section 18)

HALL = 'word:synthetic:hall'
GLOSSES = [{'surface': '公民館', 'reading': 'こうみんかん', 'meaning': '社区活动中心', 'lesson_id': HALL},
           {'surface': '囲碁', 'reading': 'いご', 'meaning': '围棋'}]


def hall_lesson():
    return {'id': HALL, 'family': 'word', 'revision': 'synthetic-v1', 'title': '公民館', 'reading': 'こうみんかん',
            'meaning': '社区活动中心', 'abilities': ['understanding'], 'depends_on': []}


def choice_batch(**optional):
    """One reviewed single-gap choice slot for the background lesson, with optional authoring fields."""
    row = {'slot_id': 'synthetic|choice|1', 'target_id': BACKGROUND, 'kind': 'grammar_cloze',
           'question': {'prompt': '公民館（　）囲碁を打ちに行きます。', 'task': '填空', 'input_mode': 'choice',
                        'choices': ['へ', 'を', 'が'], 'answer': 'へ', 'feedback': 'へ表示前往的方向。'},
           'task_family': 'single_gap', 'context_text': 'original synthetic club visit',
           'target_spans': [{'start': 3, 'end': 6, 'quote': '（　）'}], 'background_ids': [],
           'assessed_ids': [BACKGROUND], 'coverage_scope': 'focused',
           'source_refs': ['synthetic:authored-original'], **optional}
    authored = {'format': FORMAT, 'version': 1, 'revision': 'synthetic-v2', 'expected_count': 1, 'questions': [row]}
    review = {'format': 'kanamemory-exact-question-review', 'version': 1, 'reviewer': 'gpt-6.1-sol',
              'authoring_sha256': digest(authored),
              'questions': [{'slot_id': row['slot_id'], 'candidate_sha256': digest(row), 'status': 'pass',
                             'evidence_refs': ['synthetic:test-only']}]}
    return authored, review


def lessons():
    return [lesson(), background_lesson(), hall_lesson()]


def test_optional_fields_pace_the_practice_and_store_glosses_beside_the_template():
    plain = compile_batch(*choice_batch(), lessons(), None)
    paced = compile_batch(*choice_batch(opens_after_reviews=4, scenario_id='club_visit', glosses=GLOSSES),
                          lessons(), None, scenarios={'club_visit'})
    raw = paced['items'][BACKGROUND]['templates']['grammar_cloze'][0]
    assert raw['practice']['opens_after_reviews'] == 4 and raw['practice']['scenario_id'] == 'club_visit'
    fingerprint = digest(raw)
    assert paced['provenance'][0]['template_sha256'] == fingerprint
    assert fingerprint != digest(plain['items'][BACKGROUND]['templates']['grammar_cloze'][0])
    assert paced['items'][BACKGROUND]['contexts'][fingerprint] == {'background_glosses': [
        {'start': 0, 'end': 3, 'surface': '公民館', 'reading': 'こうみんかん', 'meaning': '社区活动中心', 'target_id': HALL},
        {'start': 6, 'end': 8, 'surface': '囲碁', 'reading': 'いご', 'meaning': '围棋'}]}
    # A level of 0 is not written: the template is exactly the one without the field.
    level0 = compile_batch(*choice_batch(opens_after_reviews=0), lessons(), None)
    assert level0['items'][BACKGROUND]['templates'] == plain['items'][BACKGROUND]['templates']


@pytest.mark.parametrize('damage', ['unknown-scenario', 'unknown-key', 'bad-level', 'bad-gloss', 'unknown-lesson',
                                    'fields-after-review'])
def test_optional_fields_are_checked_and_bound_by_the_review(damage):
    items, scenarios = lessons(), {'club_visit'}
    if damage == 'unknown-scenario':
        authored, review = choice_batch(scenario_id='harbour')
    elif damage == 'unknown-key':
        authored, review = choice_batch(release=2)
    elif damage == 'bad-level':
        authored, review = choice_batch(opens_after_reviews=41)
    elif damage == 'bad-gloss':
        authored, review = choice_batch(glosses=[{**GLOSSES[1], 'surface': '将棋'}])
    elif damage == 'unknown-lesson':
        authored, review = choice_batch(glosses=GLOSSES)
        items = [lesson(), background_lesson()]
    else:
        authored, review = choice_batch(glosses=GLOSSES[1:])
        authored['questions'][0]['glosses'] = GLOSSES
    with pytest.raises(ValueError):
        compile_batch(authored, review, items, None, scenarios=scenarios)


def test_glosses_never_replace_the_context_of_an_already_published_template_silently():
    first = compile_batch(*choice_batch(), lessons(), None)
    again = compile_batch(*choice_batch(), lessons(), first)
    assert again['items'][BACKGROUND]['contexts'] == first['items'][BACKGROUND]['contexts']
    with pytest.raises(ValueError, match='already has other glosses'):
        compile_batch(*choice_batch(glosses=GLOSSES[1:]), lessons(), first)


def test_the_size_guard_uses_the_pack_shard_and_namespace_limits(monkeypatch):
    import build_teaching_content
    from kanamemory import content_pack
    from compile_reviewed_question_batch import pack_size_problem
    value = {'schema_version': 1, 'provenance': [], 'items': {'a': {'templates': {}, 'blob': 'x' * 300},
                                                               'b': {'templates': {}, 'blob': 'y' * 300}}}
    assert pack_size_problem('varied_practice', value) is None
    monkeypatch.setattr(build_teaching_content, '_LIMIT', 200)
    assert 'one shard may hold at most 200' in pack_size_problem('varied_practice', value)
    monkeypatch.setattr(build_teaching_content, '_LIMIT', 400)
    assert pack_size_problem('varied_practice', value) is None
    monkeypatch.setattr(content_pack, '_MAX_SHARDED_BYTES', 500)
    assert 'at most 500' in pack_size_problem('varied_practice', value)


def test_main_reads_the_options_and_prints_the_coverage_row_it_wrote(tmp_path, monkeypatch, capsys):
    import json
    from kanamemory import learning_content
    from kanamemory.content_pack import decode_value, namespace_document
    from compile_reviewed_question_batch import main
    monkeypatch.setattr(learning_content, '_ITEMS', {item['id']: item for item in lessons()})
    authored, review = choice_batch(scenario_id='club_visit', glosses=GLOSSES[1:])
    review['reviewer'] = authored_by = 'synthetic-reviewer'
    files = {'authoring': authored, 'review': review,
             'previous': namespace_document('varied_practice', {'schema_version': 1, 'items': {}, 'provenance': []}),
             'scenarios': {'schema_version': 1, 'scenarios': {'club_visit': {'title': '社团', 'summary': '去活动。'}}}}
    for name, value in files.items():
        (tmp_path / f'{name}.json').write_text(json.dumps(value, ensure_ascii=False), 'utf8')
    args = [f'--{name}={tmp_path / f"{name}.json"}' for name in files] + [
        f'--output={tmp_path / "out" / "varied_practice.json"}', f'--reviewer={authored_by}',
        '--coverage-key=synthetic_batch', '--method=synthetic_method']
    main(args)
    printed = json.loads(capsys.readouterr().out)
    assert printed['reviewer'] == authored_by and printed['authored_slots'] == 1 and printed['payload_bytes'] > 0
    written = decode_value(json.loads((tmp_path / 'out' / 'varied_practice.json').read_text('utf8'))['payload'])
    assert set(written['coverage']) == {'synthetic_batch'}
    assert written['provenance'][0]['method'] == 'synthetic_method'
    files['scenarios']['scenarios'] = {'harbour': {'title': '港口', 'summary': '看船。'}}
    (tmp_path / 'scenarios.json').write_text(json.dumps(files['scenarios'], ensure_ascii=False), 'utf8')
    with pytest.raises(ValueError, match='not in question_scenarios'):
        main(args)
