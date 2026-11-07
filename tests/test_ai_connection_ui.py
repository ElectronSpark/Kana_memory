"""Settings interaction with in-memory vaults and synthetic model responses."""
import asyncio
import json
from unittest.mock import Mock

import pytest

from kanamemory.ai_connection import credential_slot, preset_config
from kanamemory.ai_service import AIServiceError
from kanamemory.ai_service_ui import AIServiceUIMixin, CONNECTION_SETTING
from support.ai_key_save_support import Vault
from support.ai_key_save_support import FAKE
from support.ai_ui_support import ai_app
from support.example_ui_support import examples_app
from support.learning_fixtures import learning_app
from support.app_fixtures import question_app
from support.app_fixtures import setup_app
from support.learning_fixtures import click
from support.ai_ui_support import protected
from support.fake_ui import text_of, View


def service(app):
    vaults={'openai_api_key': Vault(FAKE)}
    app._ai_secrets=lambda config=None: vaults.setdefault(credential_slot(config or preset_config('OpenAI')), Vault())
    app.ai_connection_status=lambda: AIServiceUIMixin.ai_connection_status(app)
    app.show_ai_service_settings()
    return vaults


def choose(app, preset):
    app.ai_preset_input.value=preset
    app._change_ai_preset()


def test_changing_service_never_reuses_openai_key_or_changes_progress(ai_app):
    app=ai_app;vaults=service(app);before=protected(app)
    choose(app,'DeepSeek')
    app.ai_model_input.value=app.ai_tutor_model_input.value='synthetic-model'
    app._save_ai_device_settings()
    assert '尚未填写此服务' in app.ai_connection_label.text
    assert app.store.get_setting(CONNECTION_SETTING,None) is None
    app.ai_key_input.value='synthetic-provider-key'
    app._save_ai_device_settings()
    assert app.ai_connection_status()['ready']
    client=AIServiceUIMixin.ai_client(app)
    assert client._key=='synthetic-provider-key' and client.model=='synthetic-model'
    assert vaults['openai_api_key'].get()==FAKE
    assert protected(app)==before
    app.show_ai_service_settings()
    assert not app.ai_key_input.value and app.ai_preset_input.value=='DeepSeek'


def test_local_service_saves_without_key_and_has_independent_models(ai_app):
    app=ai_app;service(app);before=protected(app)
    choose(app,'Ollama（本地）')
    app.ai_url_input.value='http://192.168.1.23:11434'
    app._change_ai_endpoint()
    app.ai_model_input.value='org/large:q4';app.ai_tutor_model_input.value='small:latest'
    app._save_ai_device_settings()
    assert app.ai_connection_status()['ready']
    assert AIServiceUIMixin.ai_client(app)._key==''
    assert AIServiceUIMixin.tutor_client(app).model=='small:latest'
    assert protected(app)==before


def test_model_discovery_does_not_save_or_overwrite_ids_and_selection_applies(ai_app,monkeypatch):
    from kanamemory import ai_service_ui
    app=ai_app;service(app);before=protected(app)
    old_model=app.ai_model_input.value
    discovery=Mock(return_value=['fixture-a','fixture-b'])
    monkeypatch.setattr(ai_service_ui,'fetch_models',discovery)
    asyncio.run(app._fetch_ai_models())
    assert app.ai_model_input.value==old_model
    assert '2 个模型' in app.ai_discovery_label.text
    assert app.store.get_setting(CONNECTION_SETTING,None) is None
    app.ai_model_picker.value='fixture-a';app._choose_ai_model(False)
    app.ai_tutor_model_picker.value='fixture-b';app._choose_ai_model(True)
    app._save_ai_device_settings()
    assert app.store.get_setting(CONNECTION_SETTING)['model']=='fixture-a'
    assert app.store.get_setting(CONNECTION_SETTING)['tutor_model']=='fixture-b'
    assert protected(app)==before
    assert discovery.call_args.args[1]==FAKE


def test_failed_discovery_preserves_manual_choice_and_does_not_claim_ready(ai_app,monkeypatch):
    from kanamemory import ai_service_ui
    app=ai_app;service(app);choose(app,'LM Studio（本地）')
    app.ai_model_input.value=app.ai_tutor_model_input.value='manual-id'
    monkeypatch.setattr(ai_service_ui,'fetch_models',Mock(side_effect=AIServiceError('network','无法连接服务。')))
    asyncio.run(app._fetch_ai_models())
    assert '手动填写' in app.ai_discovery_label.text and app.ai_fetch_button.enabled
    assert app.ai_model_input.value=='manual-id'
    app._save_ai_device_settings()
    assert app.ai_connection_status()['ready']  # Local configuration only, not a network success.


@pytest.mark.parametrize('change',['endpoint','reopen','leave'])
def test_stale_async_model_list_never_enters_a_different_form(ai_app,monkeypatch,change):
    from kanamemory import ai_service_ui
    app=ai_app;service(app)
    async def delayed(function,*args):
        if change=='endpoint': choose(app,'Ollama（本地）')
        elif change=='reopen': app.show_ai_service_settings()
        else: app.page='home';app._page_token+=1
        return ['old-service-model']
    monkeypatch.setattr(ai_service_ui.asyncio,'to_thread',delayed)
    asyncio.run(app._fetch_ai_models())
    assert 'old-service-model' not in app.ai_model_picker.items
    assert not app._ai_models_running
    if change!='leave': assert app.ai_fetch_button.enabled


def test_duplicate_clicks_cannot_create_parallel_discovery(ai_app,monkeypatch):
    from kanamemory import ai_service_ui
    app=ai_app;service(app);calls=[]
    async def delayed(function,*args):
        calls.append(1)
        choose(app,'Ollama（本地）')
        await app._fetch_ai_models()
        return ['old-model']
    monkeypatch.setattr(ai_service_ui.asyncio,'to_thread',delayed)
    asyncio.run(app._fetch_ai_models())
    assert calls==[1] and app.ai_fetch_button.enabled


def test_secret_pasted_into_endpoint_or_key_never_enters_feedback(ai_app):
    from kanamemory.feedback_context import capture_feedback_context
    app=ai_app;service(app)
    app.ai_key_input.value='SYNTHETIC-CREDENTIAL-NEVER-EXPORT'
    app.ai_url_input.value='https://fixture.invalid/?key=SYNTHETIC-URL-NEVER-EXPORT'
    app._save_ai_device_settings()
    app.content_slot=View(app.body);app.title_label=View(text='AI 服务')
    exported=json.dumps(capture_feedback_context(app),ensure_ascii=False)
    assert 'SYNTHETIC-CREDENTIAL' not in exported and 'SYNTHETIC-URL' not in exported
    assert app.store.get_setting(CONNECTION_SETTING,None) is None


def test_remove_only_draft_endpoint_key_and_preserve_other_service(ai_app):
    app=ai_app;vaults=service(app)
    choose(app,'DeepSeek')
    slot=credential_slot(app._ai_form_config(require_models=False))
    vaults[slot]=Vault('synthetic-provider-key')
    app._remove_ai_device_key()
    assert vaults[slot].get()=='' and vaults['openai_api_key'].get()==FAKE
