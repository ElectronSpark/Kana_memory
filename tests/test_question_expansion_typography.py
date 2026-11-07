"""Public Japanese keeps the study face; Chinese cues do not become targets."""
import pytest

from kanamemory.question_presentation import question_presentation
from kanamemory.question_support import learning_text_role


@pytest.mark.parametrize('kind,family,prompt,mode,role,highlight', [
    ('vocab_usage', 'translation_wordbank', '在车站买票。', 'reorder', 'ui', False),
    ('grammar_reorder', 'verb_group_order', '五段 → 一段 → 不规则', 'reorder', 'ui', False),
    ('controlled_sentence_production', 'controlled_sentence_production', '请开窗。', 'text', 'ui', False),
    ('vocab_recall', 'word_form_recall', '（　）を買います。', 'text', 'study', True),
    ('vocab_orthography', 'contextual_orthography', 'ここでほんを買います。', 'choice', 'study', True),
    ('grammar_transform', 'grammar_transform', '窓を開ける。', 'text', 'study', True),
])
def test_question_language_controls_face_and_visible_emphasis(kind, family, prompt, mode, role, highlight):
    q = {'kind': kind, 'input_mode': mode, 'prompt': prompt, 'task': '', 'target_id': 'synthetic',
        'practice': {'schema_version': 1, 'task_family': family, 'context_id': 'original-synthetic',
            'scaffold': 'recognition' if mode == 'choice' else 'cued' if mode == 'reorder' else 'retrieval',
            'target_spans': [{'start': 0, 'end': len(prompt), 'quote': prompt}],
            'background_ids': [], 'reviewed': True}}
    assert learning_text_role(q, 'prompt') == role
    assert bool(question_presentation(q)['target_spans']) is highlight
