"""Shared ai ui fixtures and assertions; no collected scenarios."""
from support.ai_question_fixtures import CID
from kanamemory.learning_ui import LEARNING_PREFS
from unittest.mock import Mock
from types import SimpleNamespace
from support.fake_ui import View
from support.app_fixtures import app_module
from kanamemory.learning_questions import build_learning_question
from support.learning_fixtures import click
from datetime import datetime
from support.fake_ui import descendants
from kanamemory.example_store import empty_pack
from kanamemory.example_store import example_id_for
from support.example_ui_support import examples_app
from support.learning_fixtures import learning_app
from support.ai_question_fixtures import proposal
import pytest
from support.app_fixtures import question_app
from kanamemory.example_store import reference_id_for
from support.ai_question_fixtures import scheduling_rows
from support.ai_question_fixtures import score
from support.app_fixtures import setup_app
from support.ai_question_fixtures import teach
from support.fake_ui import text_of


@pytest.fixture
def ai_app(examples_app, monkeypatch):
    app = examples_app
    monkeypatch.setattr(app_module.toga, 'PasswordInput', lambda **kw: View(**kw))
    monkeypatch.setattr(app_module.toga, 'ConfirmDialog', lambda title, message: SimpleNamespace(title=title, message=message))
    app.ai_connection_status = Mock(return_value={'ready': True, 'message': '已配置本机密钥'})
    from kanamemory.ai_connection import preset_config
    config = preset_config('OpenAI')
    app.ai_client = Mock(return_value=SimpleNamespace(generate=Mock(), model=config['model'], config=config,
        last_usage={}, bind_material_request=Mock()))
    return app


def protected(app):
    return {key: value for key, value in scheduling_rows(app.store).items() if key != 'app_setting'}


def make_pack(target=CID, texts=('あちらも教室です。',)):
    pack = empty_pack()
    pack['status'] = 'draft'
    for text in texts:
        eid = example_id_for(text)
        pack['examples'].append(dict(id=eid, revision='ai-test-1', text=text, translation='那边也是教室。',
                                     reading='', source_ids=[]))
        focus = 'です' if 'です' in text else text[0]
        start = text.index(focus)
        ref = dict(example_id=eid, example_revision='ai-test-1', target_id=target,
                   spans=[dict(start=start, end=start+len(focus), quote=focus)],
                   hint={'rule': '名词后礼貌地说明类别。'}, explanation='这一片段作礼貌判断。', weight=1)
        ref['id'] = reference_id_for(ref)
        pack['references'].append(ref)
    return pack


def enable_questions(app):
    teach(app.learning_store)
    score(app.learning_store)
    # A second ability of the same just-seen lesson must wait for the shared
    # feedback cooldown, even though it has no FSRS due date of its own yet.
    question = build_learning_question(app.learning_store.get_item(CID), 'grammar_cloze')
    unit = app.learning_store.get_unit(CID, question['ability'])
    when = datetime.fromisoformat(app.learning_store.effective_question_due(unit, question))
    app.learning_store.append_questions(question, now=when)
    app.learning_store.submit(question['id'], question['answer'], True, now=when)
    app.learning_store.finalize(question['id'], 3, now=when)
    app.store.set_setting(LEARNING_PREFS, {'enabled': ['grammar_meaning', 'grammar_cloze']})
    return [proposal(app.learning_store), proposal(app.learning_store, 'grammar_cloze')]


def exercise_material_prelearn_continues_without_enrolling_then_pretest_returns_to_material(ai_app):
    from support.material_study_support import material_fixture
    from support.retention_check_support import WORD
    from support.retention_check_support import KINDS
    from kanamemory.retention_session import DiagnosticStore
    app = ai_app
    job, study = material_fixture(app.store, app.learning_store)
    app.material_study = lambda: study
    app.show_material_study(job['id'])
    assert app.page == 'material_study' and app.learning_store.get_item(WORD) is None
    while any(row['completed_at'] is None for row in study.get(job['id'])['lessons']):
        click(app, '继续')
    assert app.learning_store.get_item(WORD) is None
    app.store.set_setting(LEARNING_PREFS, {'enabled': KINDS})
    episode = study.start_pretest(job['id'], KINDS)
    service = DiagnosticStore(app.store, app.learning_store)
    while row := service.current(episode['id'], KINDS):
        service.present(episode['id'], row['id'])
        service.submit(episode['id'], row['id'], row['question']['answer'], KINDS)
        service.advance(episode['id'], row['id'], KINDS)
    app.show_diagnostic_summary(service.get(episode['id']))
    assert any(node.text == '回到这批材料' and node.on_press for node in descendants(app.body))
    assert not any(node.text == '继续原练习' and node.on_press for node in descendants(app.body))
    assert app.learning_store.get_item(WORD) is None


def exercise_partial_facts_teach_in_sequence_without_unknown_audio_or_cards(ai_app, monkeypatch):
    from support.material_fact_teaching_support import two_words
    from kanamemory.material_study import MaterialStudyStore
    app = ai_app
    job, study, _ = two_words(app.store, app.learning_store)
    app.repo = study.repo
    monkeypatch.setattr(app, 'example_store', lambda: study.examples)
    monkeypatch.setattr(app, 'material_study', lambda: MaterialStudyStore(app.store, app.learning_store, repo=study.repo, examples=study.examples))
    before = protected(app)
    app.show_material_task(job['id'])
    click(app, '先看讲解')
    assert app.page == 'material_study' and '1/2' in text_of(app.body)
    assert not any(node.text == '🔊' for node in descendants(app.body))
    assert 'みかくにん' not in text_of(app.body)
    click(app, '继续')
    assert app.page == 'material_study' and '2/2' in text_of(app.body)
    click(app, '稍后继续')
    click(app, '继续看讲解')
    assert '2/2' in text_of(app.body)
    click(app, '继续')
    assert all(row['completed_at'] for row in study.get(job['id'])['lessons'])
    assert any(node.text == '检查相关内容' for node in descendants(app.body))
    assert not any(node.text == '加入学习计划' for node in descendants(app.body))
    assert protected(app) == before
