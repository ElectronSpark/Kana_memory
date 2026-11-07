"""Small injected failures across durable actions and optional presentation."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from kanamemory.ai_ui import AIUIMixin
from kanamemory.tutor_ui import TutorUIMixin
from kanamemory.material_study_ui import MaterialStudyUIMixin


def test_tutor_audio_draft_failure_keeps_previous_answer_and_allows_retry():
    draft = {"answer": "途中"}
    service = SimpleNamespace(save_draft=Mock(side_effect=OSError("private path")))
    app = SimpleNamespace(page="tutor_question", _page_token=1,
        _tutor_current={"id": "row", "question": {"kind": "vocab_audio_meaning", "audio_text": "手紙"}},
        _tutor_answer_state=draft, _tutor_status=SimpleNamespace(text=""),
        tutor_store=lambda: service, platform=SimpleNamespace(play=AsyncMock()),
        loop=SimpleNamespace(create_task=asyncio.run))
    TutorUIMixin.play_tutor_prompt(app, row_id="row")
    assert draft == {"answer": "途中"} and "语音已播放" in app._tutor_status.text
    assert "未保存" in app._tutor_status.text
    service.save_draft.side_effect = None
    TutorUIMixin.play_tutor_prompt(app, row_id="row")
    assert draft == {"answer": "途中", "audio_ok": True}


@pytest.mark.parametrize("mode", ["revision", "review", "generation"])
@pytest.mark.parametrize("new_operation", [False, True])
def test_failed_budget_cleanup_releases_only_matching_request(monkeypatch, mode, new_operation):
    from kanamemory import material_budget, ai_ui
    operation = object()
    run = {"id": "run", "token": "token", "operation": operation}
    provider = SimpleNamespace(revise_material=Mock(), verify_material=Mock(), generate=Mock(), last_usage={})
    service = SimpleNamespace(fail=Mock())
    app = SimpleNamespace(page="ai_form", _page_token=1, store=object(),
        _ai_request_operation=object() if new_operation else operation, _ai_material_run=run,
        _ai_form_status=SimpleNamespace(text=""), _set_ai_form_busy=Mock(),
        ai_client=lambda: provider, material_workflow=lambda: service)
    before_operation = app._ai_request_operation
    monkeypatch.setattr(material_budget, "authorize", Mock())
    monkeypatch.setattr(material_budget, "settle", Mock(side_effect=OSError("budget database")))
    background = AsyncMock(side_effect=RuntimeError("network"))
    monkeypatch.setattr(ai_ui.asyncio, "to_thread", background)
    if mode == "revision":
        coroutine = AIUIMixin._run_material_revision(app, {"request": {}}, run, 1)
    elif mode == "review":
        coroutine = AIUIMixin._run_material_review(app, {"review_request": {}}, run, 1)
    else:
        coroutine = AIUIMixin._run_ai_generation(app, {"request_id": "request"}, operation, 1, material_run=run)
    asyncio.run(coroutine)
    assert background.await_count == 1
    service.fail.assert_called_once()
    assert app._ai_request_operation is (before_operation if new_operation else None)
    assert app._set_ai_form_busy.call_count == int(not new_operation)


@pytest.mark.parametrize("raises", [False, True])
def test_material_import_failed_save_retains_input_and_does_not_claim_import(monkeypatch, raises):
    from kanamemory import material_input
    origin = Mock()
    monkeypatch.setattr(material_input, "save_origin", origin)
    app = SimpleNamespace(page="ai_form", _page_token=1, store=object(),
        _ai_form_context={"mode": "analyze"}, _ai_form_status=SimpleNamespace(text="precise rejection"),
        _ai_form_fields={"source_text": SimpleNamespace(value="old draft")},
        platform=SimpleNamespace(import_material_text=AsyncMock(return_value="教室。".encode())),
        save_ai_form=Mock(side_effect=OSError("private storage") if raises else None, return_value=False))
    asyncio.run(MaterialStudyUIMixin.read_material_file(app))
    assert app._ai_form_fields["source_text"].value == "教室。"
    assert not app._material_file_busy and not origin.called
    assert "保存未完成" in app._ai_form_status.text if raises else app._ai_form_status.text == "precise rejection"


def test_example_import_failure_after_navigation_does_not_alert_new_page():
    from kanamemory.example_ui import ExampleUIMixin
    app = SimpleNamespace(_page_token=1, alert=Mock())
    async def read():
        app._page_token += 1
        raise OSError("old file request")
    app.platform = SimpleNamespace(import_backup=read)
    asyncio.run(ExampleUIMixin.read_examples(app))
    app.alert.assert_not_called()


@pytest.mark.parametrize("save_fails", [False, True])
def test_feedback_failure_status_survives_final_render_without_resending(monkeypatch, save_fails):
    from kanamemory import feedback_analysis_ui as module
    from kanamemory.material_workflow import digest
    job = {"id": "job", "request": {}, "status": "prepared"}
    service = SimpleNamespace(create_report=Mock(return_value=job), begin=Mock(return_value="token"),
        finish=Mock(side_effect=[OSError("save"), {"status": "failed"}] if save_fails else None,
                    return_value={"status": "completed"}))
    view = {"record": {"id": "feedback"}, "selected": {"field"}, "status": SimpleNamespace(text="")}
    generate = Mock(return_value={})
    monkeypatch.setattr(module, "generate", generate)
    app = SimpleNamespace(page="feedback_analysis", _feedback_analysis_view=view,
        ai_connection_status=lambda: {"ready": True}, feedback_analysis=lambda: service,
        tutor_client=lambda: SimpleNamespace(model="fixture", config={}),
        _feedback_analysis_consents={digest(['feedback_text_and_selected_content_v1', '', '', 'fixture', 'single'])},
        _render_feedback_reports=Mock(side_effect=None if save_fails else RuntimeError("render")),
        loop=SimpleNamespace(create_task=asyncio.run))
    module.FeedbackAnalysisUIMixin.run_feedback_analysis(app, view)
    assert not view['busy'] and view['status'].text
    assert '核查未完成' in view['status'].text if save_fails else '报告显示未完成' in view['status'].text
    assert generate.call_count == 1 and service.finish.call_count == (2 if save_fails else 1)


def test_tutor_saved_reply_is_not_cancelled_when_render_fails():
    service = SimpleNamespace(finish_request=Mock(return_value=True), cancel_request=Mock())
    context = {'context_fingerprint': 'same'}
    app = SimpleNamespace(page='tutor_loading', _page_token=1, _tutor_request_id='request', _tutor_busy=True,
        _tutor_loading_status=SimpleNamespace(text=''), tutor_store=lambda: service,
        tutor_client=lambda: SimpleNamespace(tutor=Mock(return_value={})), _tutor_context=lambda: context,
        _tutor_accept_response=Mock(side_effect=RuntimeError('render')))
    asyncio.run(TutorUIMixin._run_tutor_request(app, {'request_id': 'request'}, context,
        'same', 'fingerprint', 1, 'ask', 'question', {}))
    service.finish_request.assert_called_once()
    service.cancel_request.assert_not_called()
    assert not app._tutor_busy and '回复已保存' in app._tutor_loading_status.text
