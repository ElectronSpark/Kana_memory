"""Shared example ui fixtures and assertions; no collected scenarios."""
from unittest.mock import Mock
from support.fake_ui import View
from support.app_fixtures import app_module
from support.learning_fixtures import learning_app
from support.learning_fixtures import legacy_dump
import pytest
from support.app_fixtures import question_app
from support.app_fixtures import setup_app


@pytest.fixture
def examples_app(learning_app, monkeypatch):
    monkeypatch.setattr(app_module.toga, 'MultilineTextInput', lambda **kw: View(**kw))
    learning_app.platform.link_kana_label = Mock(return_value=False)
    return learning_app


def shared(app):
    service = app.example_store()
    return next(e for e in service.all_examples() if e['text'] == '私は学生です。')
