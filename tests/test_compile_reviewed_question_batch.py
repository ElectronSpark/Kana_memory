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
