"""Shared learning pitch sequence controls fixtures and assertions; no collected scenarios."""
from support.pitch_sequence_support import CORRECT
from kanamemory.learning_ui import LEARNING_PREFS
from support.learning_fixtures import NOW
from support.app_fixtures import app_module
from kanamemory.learning_questions import build_learning_question
from support.learning_pitch_controls_support import button
from support.learning_fixtures import click
from copy import deepcopy
from support.learning_pitch_controls_support import diagrams
from support.learning_fixtures import learning_app
from support.learning_fixtures import legacy_dump
from support.pitch_sequence_support import lesson
import pytest
from support.app_fixtures import question_app
from support.app_fixtures import setup_app
from support.pitch_sequence_support import registry as synthetic_registry
from support.pitch_sequence_support import synthetic_spec
from datetime import timedelta


SYMBOL = {'H': '▔', 'L': '▁', 'merge': '‿', 'split': '│', 'undo': '⌫'}


@pytest.fixture
def registry(synthetic_registry):
    # Real UI catalogue construction also validates bundled teaching diagrams.
    # Preserve their actual trusted records while adding this synthetic case;
    # do not relax the production registry validator or rely on cache order.
    from kanamemory.content_pack import load_namespace
    bundled = load_namespace('pitch_sequences', {})
    synthetic_registry['sources'].extend(deepcopy(bundled.get('sources', [])))
    synthetic_registry['records'].extend(deepcopy(bundled.get('records', [])))
    return synthetic_registry


def prepare(app, registry, *, mode='pitch_segmentation', learner=True):
    item = lesson(registry, spec=synthetic_spec(mode=mode, learner=learner))
    app.store.set_setting(LEARNING_PREFS, {'enabled': ['pronunciation_application']})
    app.learning_store.enroll(item, now=NOW - timedelta(minutes=2))
    app.learning_store.advance_instruction(item['id'], 'intro_done', now=NOW - timedelta(minutes=2))
    app.learning_store.advance_instruction(item['id'], 'guide_correct', now=NOW - timedelta(minutes=2))
    q = build_learning_question(item, 'pronunciation_application')
    app.learning_store.append_questions(q)
    app.show_learning_question({'question': q, 'draft': {}})
    return item, q


def act(app, actions):
    for action in actions:
        assert button(app, SYMBOL[action]).enabled
        click(app, SYMBOL[action])
