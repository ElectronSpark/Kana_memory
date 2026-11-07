"""Shared pitch alignment fixtures and assertions; no collected scenarios."""
from kanamemory import pitch_alignment as alignment
from copy import deepcopy
from kanamemory import pitch_practice as pitch
import pytest


def synthetic_spec():
    value = {'version': 3, 'stimulus_id': 'synthetic:written-pitch', 'text': '今日、雨。',
        'source_units': [
            {'id': 'today', 'start': 0, 'end': 2, 'role': 'word', 'reading': 'きょう', 'morae': ['きょ', 'う']},
            {'id': 'comma', 'start': 2, 'end': 3, 'role': 'punctuation', 'reading': '', 'morae': []},
            {'id': 'rain', 'start': 3, 'end': 4, 'role': 'word', 'reading': 'あめ', 'morae': ['あ', 'め']},
            {'id': 'stop', 'start': 4, 'end': 5, 'role': 'punctuation', 'reading': '', 'morae': []}],
        'contexts': [{'id': 'reference', 'prompt_zh': '仅为程序测试的合成参考谱。', 'source_refs': ['synthetic']}],
        'reviewed_patterns': [{'id': 'reference', 'context_id': 'reference', 'levels': ['L', 'H', 'H', 'L'], 'source_refs': ['synthetic']}],
        'selected_pattern_id': 'reference', 'accepted_pattern_ids': ['reference'],
        'representation': 'mora_plateau_hl', 'task_mode': 'source_alignment',
        'review': {'review_id': 'synthetic', 'content_sha256': '', 'source_refs': ['synthetic'], 'status': 'approved'}}
    value['review']['content_sha256'] = alignment.semantic_fingerprint(value)
    return value


@pytest.fixture
def registry(monkeypatch):
    value = {'format': 'kanamemory-pitch-sequences', 'version': 1,
             'sources': [{'id': 'synthetic', 'title': 'Synthetic protocol fixture', 'url': 'https://example.test/synthetic'}], 'records': []}
    monkeypatch.setattr(alignment, 'load_namespace', lambda name, default=None: deepcopy(value) if name == 'pitch_sequences' else default)
    return value


def register(registry, spec):
    spec['review']['content_sha256'] = alignment.semantic_fingerprint(spec)
    registry['records'].append(deepcopy(spec))
    return spec


def lesson(registry, *, kind='pronunciation_application', spec=None):
    spec = register(registry, spec or synthetic_spec())
    raw = {'input_mode': 'pitch_fill', 'prompt': spec['text'], 'pitch_practice': spec,
           'choices': [], 'safe': False, 'feedback': '参考谱标注完成。', 'text_roles': {'prompt': 'study', 'answer': 'ui'}}
    raw['task'] = alignment.public_task(raw)
    raw['answer'] = pitch.canonical_answer(raw)
    return {'id': 'pronunciation:synthetic_alignment', 'revision': 'synthetic-v1', 'family': 'grammar',
        'domain': 'pronunciation', 'abilities': ['understanding', 'application'], 'depends_on': [],
        'title': '原文音拍测试', 'meaning': '测试', 'instruction': {'rule': '每词填拍，确认后继续。', 'example': spec['text']},
        'question_templates': {kind: [raw]}}


CORRECT = ['L', 'H', 'split', 'H', 'L', 'split']
