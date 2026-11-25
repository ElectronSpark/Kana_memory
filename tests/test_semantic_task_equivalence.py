"""Semantic-error entries belong to a question by sentence, answer and kind; the task wording plays no part.

Made-up lesson and pack (no course content): the pack keeps its stored keys, a reworded task finds the entry
through semantic_errors.sentence_key, and the learner's recorded sentence key is unchanged.
"""
import pytest

from kanamemory import content_pack, learning_content, learning_questions
from kanamemory import semantic_errors as se

CID = 'grammar:made_up:copula'
KIND = 'grammar_cloze'


def _question(prompt, task, answer='です', *, mode='choice', choices=('です', 'ます', 'を'), accepted=('です',),
              revision='r1'):
    return {'target_id': CID, 'revision': revision, 'ability': 'application', 'kind': KIND, 'prompt': prompt,
            'task': task, 'answer': answer, 'input_mode': mode, 'choices': list(choices),
            'accepted_answers': list(accepted)}


OLD = _question('これは本（　）。', '按给定意思补全句子：这是书。')
NEW = _question('これは本（　）。', '补全句子：是（礼貌体）')
OTHER = _question('あれは駅（　）。', '按给定意思补全句子：那是车站。')


def _entry(source_variant, wrong='ます'):
    return {'revision': 'semantic-slot-v1', 'conditions': ['affirmative', 'noun_predicate'],
            'source_id': f'course:{CID}:{KIND}:{source_variant}',
            'opportunities': [{'code': f'substitution:{wrong}:for:です', 'slot': 'answer', 'wrong_answers': [wrong]}]}


@pytest.fixture
def lesson(monkeypatch):
    """templates[variant] -> question; pack[...] -> the installed semantic_assessments."""
    state = {'templates': [OLD, OTHER, NEW], 'pack': {'version': 1, 'questions': {}, 'labels': {}}, 'builds': 0}

    def build(content_id, kind, *, variant=0, item=None, **_):
        assert content_id == CID and kind == KIND
        state['builds'] += 1
        return dict(state['templates'][variant])

    def load(name, default=None):
        return state['pack'] if name == 'semantic_assessments' else default

    monkeypatch.setattr(learning_content, 'get_learning_item', lambda cid: {'id': cid} if cid == CID else None)
    monkeypatch.setattr(learning_questions, 'practice_templates', lambda item: {KIND: state['templates']})
    monkeypatch.setattr(learning_questions, 'build_learning_question', build)
    monkeypatch.setattr(content_pack, 'load_namespace', load)
    se._SHARED.clear()
    yield state
    se._SHARED.clear()


def test_reworded_task_finds_the_entry_written_for_the_old_task(lesson):
    lesson['pack']['questions'] = {se.question_key(OLD): _entry(0)}
    old, new = se.for_question(OLD), se.for_question(NEW)
    assert old and new and new['opportunities'] == old['opportunities'] and new['conditions'] == old['conditions']
    assert new['source_id'] == old['source_id']
    # The learner's rows: the recorded sentence key is the stored one and the same for both tasks.
    assert new['source_key'] == old['source_key'] == se.source_key(CID, OLD['prompt'], OLD['answer'])
    assert se.sentence_key(NEW) == se.sentence_key(OLD) and se.question_key(NEW) != se.question_key(OLD)
    assert se.for_question(OTHER) is None
    # A pattern recorded under the old question ranks the reworded one (the tutor's targeted slot).
    pattern = {'semantic': True, 'error_code': 'substitution:ます:for:です', 'ability': 'application',
               'conditions': old['conditions'], 'condition_revision': old['revision'], 'concern': .4}
    assert se.priority(new, [pattern]) == .4


@pytest.mark.parametrize('changed', [{'prompt': 'これは車（　）。'}, {'answer': 'ます'}, {'kind': 'grammar_meaning'},
                                     {'revision': 'r2'}, {'target_id': 'grammar:made_up:other'}])
def test_sentence_answer_kind_lesson_and_revision_still_decide(lesson, changed):
    lesson['pack']['questions'] = {se.question_key(OLD): _entry(0)}
    assert se.for_question({**NEW, **changed}) is None


def test_mode_and_answer_set_do_not_decide_but_the_guards_stay(lesson):
    lesson['pack']['questions'] = {se.question_key(OLD): _entry(0)}
    typed = {**NEW, 'input_mode': 'text', 'choices': [], 'accepted_answers': ['です', 'だ']}
    assert se.for_question(typed)['opportunities'] == se.for_question(OLD)['opportunities']
    # An entry whose wrong answer the question accepts, or does not offer as a choice, is still refused.
    assert se.for_question({**typed, 'accepted_answers': ['です', 'ます']}) is None
    assert se.for_question({**NEW, 'choices': ['です', 'を', 'に']}) is None


def test_entry_without_its_original_template_is_not_guessed(lesson):
    lesson['pack']['questions'] = {se.question_key(OLD): _entry(0)}
    lesson['templates'] = [NEW, OTHER]  # the old template was replaced, not retired
    assert se.resolve_entry(se.question_key(OLD), _entry(0)) is None
    assert se.for_question(NEW) is None


def test_source_variant_is_a_hint_only_and_resolution_is_built_once(lesson):
    lesson['pack']['questions'] = {se.question_key(OLD): _entry(1)}  # source_id names another template
    assert se.resolve_entry(se.question_key(OLD), _entry(1)) == se.sentence_key(OLD)
    se._SHARED.clear()
    assert se.for_question(NEW)
    builds = lesson['builds']
    for _ in range(3):
        assert se.for_question(NEW) and se.for_question(OTHER) is None
    assert lesson['builds'] == builds


def test_two_entries_claiming_one_sentence_keep_only_their_stored_keys(lesson):
    second = _question('これは本（　）。', '补全句子：这是书。')
    lesson['templates'] = [OLD, OTHER, NEW, second]
    lesson['pack']['questions'] = {se.question_key(OLD): _entry(0), se.question_key(second): _entry(3, 'を')}
    assert se.for_question(OLD)['opportunities'][0]['wrong_answers'] == ['ます']
    assert se.for_question(second)['opportunities'][0]['wrong_answers'] == ['を']
    assert se.for_question(NEW) is None


def test_stored_key_wins_over_the_sentence(lesson):
    lesson['pack']['questions'] = {se.question_key(OLD): _entry(0), se.question_key(NEW): _entry(2, 'を')}
    assert se.entry_for(NEW)[0] == se.question_key(NEW)
    assert se.entry_for(OLD)[0] == se.question_key(OLD)
