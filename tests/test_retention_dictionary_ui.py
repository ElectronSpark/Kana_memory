from support.ai_ui_support import ai_app
from support.example_ui_support import examples_app
from support.learning_fixtures import learning_app
from support.app_fixtures import question_app
from support.app_fixtures import setup_app
from support.learning_fixtures import click
from support.fake_ui import text_of
from support.retention_dictionary_support import prepare


def test_dictionary_sense_is_available_from_normal_scope_and_preserves_result(ai_app):
    app = ai_app
    service, word, item, _ = prepare(app.store, app.learning_store)
    app.repo = service.repo
    app.store.set_setting('learning_preferences_v1', {'enabled': ['vocab_meaning']})
    app.show_diagnostic_scope(scope_ids=[item['id']])
    click(app, '开始检查')
    assert app.page == 'diagnostic_question'
    context = app._diagnostic_context
    assert context['row']['question']['target_id'] == item['id']
    app.submit_diagnostic(context, context['row']['question']['answer'])
    assert app.page == 'diagnostic_feedback' and '✓' in text_of(app.body)
    assert app._learning_answer_audio_text(context['row']['question']) == item['reading']
    app.continue_diagnostic(context['episode'])
    assert app.page == 'diagnostic_feedback' and '复习间隔不变' in text_of(app.body)
