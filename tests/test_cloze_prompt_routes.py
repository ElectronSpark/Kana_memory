"""Saved gap edits update the actual controller's retained sentence view."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kanamemory.ai_questions import build_learning_scope
from kanamemory.learning_questions import build_learning_question
from kanamemory.retention_session import DiagnosticStore
from kanamemory.tutor_engine import local_question
from kanamemory.tutor_store import AITutorStore
from kanamemory.tutor_ui import TutorUIMixin
from support.app_fixtures import setup_app, question_app
from support.learning_fixtures import learning_app
from support.fake_ui import descendants
from support.multi_cloze_integration_support import cloze_case, enqueue, score_primary, CID, KIND, scheduling_state


@pytest.mark.parametrize('route', ['course', 'guide', 'tutor', 'diagnostic'])
def test_retained_sentence_and_saved_undo_in_all_supported_routes(learning_app, cloze_case, monkeypatch, route):
    app, case = learning_app, cloze_case
    with monkeypatch.context() as patch:
        from kanamemory import ui_components as u
        button = u.button
        patch.setattr(u, 'button', lambda text, handler, **kw: button(text, handler, **dict(kw, style=SimpleNamespace())))
        patch.setattr(app, 'store', case.store)
        patch.setattr(app, 'learning_store', case.learning)
        app._test_clock[0] = case.when
        case.store.set_setting('learning_preferences_v1', {'enabled': [KIND]})
        if route == 'course':
            q = enqueue(case)
            current = case.learning.current([KIND], now=case.when)
            assert current['question']['id'] == q['id']
            app.show_learning_question(current)
            root = app.body
            read = lambda: case.learning.get_question(q['id'])['draft']
        elif route == 'guide':
            from kanamemory import ui_components as u
            q = build_learning_question(case.item, KIND, guided=True)
            app._learning_item = case.learning.instruction_item(CID)
            app._learning_guide, app.page = q, 'learning_guide'
            app._learning_guide_result = u.col()
            root = u.col(*app._learning_guide_controls(q, {}))
            read = lambda: case.learning.instruction_item(CID)['checkpoint']['guide_draft']
        else:
            score_primary(case)
            app._test_clock[0] = when = case.when + timedelta(days=30)
            if route == 'tutor':
                tutor = AITutorStore(case.store)
                tutor.start()
                scope = build_learning_scope(case.learning, [KIND], now=when, supported_kinds=[KIND])
                row = tutor.append_question(local_question(case.item, KIND), scope, [KIND], now=when)
                patch.setattr(app, 'tutor_store', lambda: tutor, raising=False)
                TutorUIMixin.show_tutor_question(app, row)
                read = lambda: tutor.current()['draft']
            else:
                enqueue(case, 'diagnostic')
                service = DiagnosticStore(case.store, case.learning)
                episode = service.start([CID], [KIND], 'cloze-inline', purpose='check_only', now=when)
                patch.setattr(app, 'diagnostics', lambda: service)
                patch.setattr(app, '_arm_diagnostic_timer', Mock())
                patch.setattr(app, '_flush_diagnostic_time', Mock())
                app.continue_diagnostic(episode['id'])
                read = lambda: service.current(episode['id'], [KIND])['draft']
            root = app.body
        before = deepcopy(scheduling_state(case.learning))
        ctx = next(node._multi_cloze_context for node in descendants(root) if hasattr(node, '_multi_cloze_context'))
        prompt = ctx['prompt']
        assert prompt is not None and '（①）' in prompt._prompt_support['prompt']
        ctx['choices'][0].on_press(None)
        assert prompt._prompt_support['prompt'] == '私は学校（②）行きます。'
        assert read()['cloze_state']['values'] == {'slot1': 'は'}
        ctx['clear'].on_press(None)
        assert prompt._prompt_support['prompt'] == '私（①）学校（②）行きます。'
        assert read()['cloze_state']['values'] == {}
        assert scheduling_state(case.learning) == before
