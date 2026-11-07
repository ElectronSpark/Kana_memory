"""Shared retention check fixtures and assertions; no collected scenarios."""
from support.ai_question_fixtures import CID
from kanamemory.learning_questions import GRAMMAR_KINDS
from support.ai_question_fixtures import START
from kanamemory.learning_questions import WORD_KINDS


KINDS=list(WORD_KINDS)+list(GRAMMAR_KINDS)


WORD='word:tegami:letter'


def protected(store):
    names=('study_card','review_event','study_session','session_question','session_checkpoint',
           'learning_content','learning_unit','learning_question','learning_event','learning_evidence_pattern','learning_aux_adjustment')
    return {name:[tuple(r) for r in store._db.execute('SELECT * FROM '+name)] for name in names}
