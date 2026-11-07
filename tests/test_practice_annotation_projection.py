from copy import deepcopy
from extend_practice_annotations import project_unchanged_annotations, project_context_spans, common_projection, resolve_public_plan
from sentence_annotation_corpus import public_prompt_parts


def record(text, *spans):
    return {'text': text, 'version': 1, 'segments': [
        {'start': start, 'end': end, 'surface': text[start:end], 'reading': reading}
        for start, end, reading in spans]}


def test_gap_keeps_context_reading_and_rebases_only_unchanged_whole_segments():
    source = '今日は図書館で勉強する。'
    original = record(source, (0, 2, 'きょう'), (3, 6, 'としょかん'), (7, 11, 'べんきょうする'))
    before = deepcopy(original)
    text = '今日は（　）で勉強する。'
    result = project_unchanged_annotations(text, source, original, {'start': 3, 'end': 6, 'quote': '（　）'})
    assert original == before
    assert [r['surface'] for r in result['segments']] == ['今日', '勉強する']
    assert result['segments'][0]['reading'] == 'きょう'
    expanded = '今日はとしょかんで勉強する。'
    result = project_unchanged_annotations(expanded, source, original, {'start': 3, 'end': 8, 'quote': 'としょかん'})
    assert result['segments'][1]['start'] == 9
    assert expanded[9:13] == result['segments'][1]['surface']


def test_crossing_segment_and_ambiguous_reading_are_omitted():
    source = '図書館へ行く。'
    result = project_unchanged_annotations('図（　）へ行く。', source,
        record(source, (0, 3, 'としょかん'), (4, 6, 'いく')),
        {'start': 1, 'end': 4, 'quote': '（　）'})
    assert [r['surface'] for r in result['segments']] == ['行く']
    alternative = deepcopy(result)
    alternative['segments'][0]['reading'] = 'ゆく'
    assert common_projection([result, alternative])['segments'] == []


def test_projection_rejects_nonmatching_or_answer_bearing_replacement():
    source = '本を読む。'
    original = record(source, (0, 1, 'ほん'), (2, 4, 'よむ'))
    assert project_unchanged_annotations('本を買う。', source, original,
        {'start': 2, 'end': 4, 'quote': '買う'}) is None
    assert project_unchanged_annotations('魚を（　）。', source, original,
        {'start': 2, 'end': 5, 'quote': '（　）'}) is None


def test_chinese_reconstruction_prompt_is_not_japanese_annotation_input():
    assert public_prompt_parts({'kind': 'grammar_reorder', 'prompt': '今天在图书馆学习。',
        'practice': {'task_family': 'sentence_reconstruction'}}) == ()


def test_incomplete_or_partly_failed_projections_never_call_tokenizer():
    def forbidden(text):
        raise AssertionError('Must not tokenize an incomplete context')
    text = '本を（　）。'
    valid = record(text, (0, 1, 'ほん'))
    for projections in ([], [None], [valid, None], [None, valid]):
        assert resolve_public_plan(text, projections, forbidden)['segments'] == []
    assert resolve_public_plan(text, [valid], forbidden)['segments'] == valid['segments']
    complete = '本を読む。'
    assert resolve_public_plan(complete, [], lambda s: {'text': s}) == {'text': complete}


def test_multiple_gaps_copy_only_whole_unchanged_context_tokens():
    source = 'お金を貯める。\nお金は少し残す。'
    prompt = '（1）を貯める。\n（2）は少し残す。'
    before = record(source, (0, 2, 'おかね'), (3, 6, 'ためる'), (8, 10, 'おかね'), (11, 13, 'すこし'), (13, 15, 'のこす'))
    original = deepcopy(before)
    result = project_context_spans(prompt, source, before,
        [{'start': 0, 'end': 3, 'quote': '（1）'}, {'start': 9, 'end': 12, 'quote': '（2）'}])
    assert before == original
    assert [s['surface'] for s in result['segments']] == ['貯める', '少し', '残す']
    assert all(prompt[s['start']:s['end']] == s['surface'] for s in result['segments'])
    assert result['context_projection_sha256']


def test_ambiguous_repeated_anchors_and_crossing_compound_fail_closed():
    source = '本を本を本。'
    assert project_context_spans('（1）を（2）。', source, record(source),
        [{'start': 0, 'end': 3, 'quote': '（1）'}, {'start': 4, 'end': 7, 'quote': '（2）'}]) is None
    source = '図書館へ行く。'
    result = project_context_spans('図（1）へ行く。', source,
        record(source, (0, 3, 'としょかん'), (4, 6, 'いく')),
        [{'start': 1, 'end': 4, 'quote': '（1）'}])
    assert [s['surface'] for s in result['segments']] == ['行く']
    for prompt in ('（1）を読む。', '（2）は少し残す。', '［1］を読む。'):
        assert resolve_public_plan(prompt, [], lambda s: (_ for _ in ()).throw(AssertionError(s)))['segments'] == []
