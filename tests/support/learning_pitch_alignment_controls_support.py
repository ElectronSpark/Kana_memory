"""Shared learning pitch alignment controls fixtures and assertions; no collected scenarios."""
from kanamemory.learning_ui import LEARNING_PREFS
from support.learning_fixtures import NOW
from support.learning_pitch_sequence_controls_support import act
from support.pitch_alignment_support import registry as alignment_registry
from support.app_fixtures import app_module
from kanamemory.learning_questions import build_learning_question
from copy import deepcopy
from support.learning_fixtures import learning_app
from support.learning_fixtures import legacy_dump
from support.pitch_alignment_support import lesson
import pytest
from support.app_fixtures import question_app
from support.app_fixtures import setup_app
from datetime import timedelta


@pytest.fixture
def registry(alignment_registry):
    from kanamemory.content_pack import load_namespace
    bundled = load_namespace('pitch_sequences', {})
    alignment_registry['sources'].extend(deepcopy(bundled.get('sources', [])))
    alignment_registry['records'].extend(deepcopy(bundled.get('records', [])))
    return alignment_registry


def prepare(app, registry):
    item = lesson(registry)
    app.store.set_setting(LEARNING_PREFS, {'enabled': ['pronunciation_application']})
    app.learning_store.enroll(item, now=NOW - timedelta(minutes=2))
    app.learning_store.advance_instruction(item['id'], 'intro_done', now=NOW - timedelta(minutes=2))
    app.learning_store.advance_instruction(item['id'], 'guide_correct', now=NOW - timedelta(minutes=2))
    q = build_learning_question(item, 'pronunciation_application')
    app.learning_store.append_questions(q)
    app.show_learning_question({'question': q, 'draft': {}})
    return item, q
