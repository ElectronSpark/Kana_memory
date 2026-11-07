"""Returning from local key configuration refreshes only the connection notice."""
from copy import deepcopy
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest

from support.ai_ui_support import ai_app
from support.example_ui_support import examples_app
from support.learning_fixtures import learning_app
from support.app_fixtures import question_app
from support.app_fixtures import setup_app
from support.learning_fixtures import click
from support.ai_ui_support import protected
from support.ai_ui_followups_support import ai_navigation_app
from support.fake_ui import text_of
from kanamemory.app import KanaMemory, PAGE_BINDINGS
from kanamemory.ai_service_ui import AIServiceUIMixin
from kanamemory.ai_ui import AI_FORM_SETTING


@pytest.fixture
def connection_app(ai_navigation_app):
    app = ai_navigation_app
    # Inert fake key, solely an in-memory store. No platform storage or network.
    value = {"key": ""}
    secrets = SimpleNamespace(get=lambda: value["key"],
        put=lambda key: value.update(key=key), remove=lambda: value.update(key=""))
    app._ai_secrets = Mock(return_value=secrets)
    app.ai_connection_status = MethodType(AIServiceUIMixin.ai_connection_status, app)
    app.show_ai_service_settings = MethodType(KanaMemory.show_ai_service_settings, app)
    return app, value


def test_save_configuration_then_back_clears_missing_notice_without_rebuilding_form(connection_app):
    app, secrets = connection_app
    before = protected(app)
    app.show_ai_form("analyze")
    fields, context, slot, status = app._ai_form_fields, app._ai_form_context, app._ai_connection_slot, app._ai_form_status
    fields["source_text"].value = "今日は雨です。"
    fields["topic"].value = "保留这个主题"
    fields["count"].value = "2"
    app.start_ai_generation()  # Local credentials gate only; saves a stable request ID.
    assert "请先配置" in status.text
    saved = deepcopy(app.store.get_setting(AI_FORM_SETTING))
    click(app, "配置 AI 服务")
    app.ai_key_input.value = "sk-" + "inertFakeCredentialForLocalTestOnly" * 2
    click(app, "保存本机配置")
    assert secrets["key"] and app.ai_key_input.value == ""
    assert app.go_back()
    assert app.page == "ai_form"
    assert app._ai_form_fields is fields and app._ai_form_context is context
    assert app._ai_connection_slot is slot and app._ai_form_status is status
    assert not slot.children and status.text == ""
    assert fields["source_text"].value == "今日は雨です。"
    assert fields["topic"].value == "保留这个主题" and fields["count"].value == "2"
    assert app.store.get_setting(AI_FORM_SETTING) == saved
    assert app._ai_generate_button.enabled and not app._ai_cancel_button.enabled
    assert "ai_key_input" not in PAGE_BINDINGS["ai_form"]
    assert "ai_key_input" not in PAGE_BINDINGS["ai_service"]
    assert protected(app) == before
    app.ai_client.assert_not_called()


def test_remove_configuration_then_back_preserves_request_status_and_draft(connection_app):
    app, secrets = connection_app
    secrets["key"] = "sk-" + "inertFakeCredentialForLocalTestOnly" * 2
    app.show_ai_form("analyze")
    fields, slot = app._ai_form_fields, app._ai_connection_slot
    fields["source_text"].value = "明日は晴れです。"
    request = app._ai_request()
    status = app._ai_form_status
    status.text = "已取消，输入已保留。"
    status._feedback_fragment = {"kind": "ai_request", "request": deepcopy(request)}
    saved = deepcopy(app.store.get_setting(AI_FORM_SETTING))
    before = protected(app)
    assert not slot.children
    app.show_ai_service_settings()
    click(app, "移除此地址的密钥")
    assert app.go_back()
    assert app.page == "ai_form" and app._ai_form_fields is fields and app._ai_connection_slot is slot
    assert app.ai_connection_status()['message'] in text_of(slot) and "配置 AI 服务" in text_of(slot)
    assert app._ai_form_status is status and status.text == "已取消，输入已保留。"
    assert status._feedback_fragment["request"] == request
    assert app.store.get_setting(AI_FORM_SETTING) == saved
    assert app._ai_generate_button.enabled and not app._ai_cancel_button.enabled
    assert protected(app) == before
    app.ai_client.assert_not_called()
