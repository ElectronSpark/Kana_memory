"""The native route keeps long saved history lazy without losing page evidence."""
from copy import deepcopy

from support.app_fixtures import setup_app, question_app
from support.learning_fixtures import learning_app
from support.study_path_runtime_support import path_app
from support.study_path_runtime_support import source_question
from support.feedback_context_support import fake_app
from support.feedback_context_support import Widget
from support.session_snapshots import unrouted
from kanamemory import domain, feedback_context
from kanamemory.app import KanaMemory


def test_resume_keeps_history_lazy_and_preserves_live_typing_draft(path_app, monkeypatch):
    app = path_app
    app._practice_preferences.update(kind='看假名拼罗马音', mixed_kinds=['kana_romaji'])
    old = source_question(app, 'あ', 'kana_romaji')
    current = source_question(app, 'い', 'kana_romaji')
    sid = app.store.start_session([old] * 1000 + [current], now=app._test_clock[0])
    draft = {'page': 'question', 'romaji_answer': 'draft', 'use_full_keyboard': False}
    app.store.save_session_progress(sid, 1000, state=draft)
    before = deepcopy(app.store.load_session_progress())
    copies = []
    original = domain._question_from_snapshot
    def observe(item, **kwargs):
        copies.append(item['content_id'])
        return original(item, **kwargs)
    monkeypatch.setattr(domain, '_question_from_snapshot', observe)
    app.resume_practice()
    assert isinstance(app.questions, domain._QuestionSequence)
    assert app.page == 'question' and unrouted(app.question) == current
    assert app.question_index == 1000 and app.romaji_input.value == 'draft'
    assert len(copies) < 20
    after = app.store.load_session_progress(lazy_questions=True)
    assert after['state'] == before['state'] and after['session_id'] == sid
    assert KanaMemory._lazy_practice_questions(app)
    app.store.practice_lane = 'word_romaji'
    assert not KanaMemory._lazy_practice_questions(app)


def test_feedback_capture_retains_lazy_queue_size_without_exporting_old_answers(tmp_path):
    with domain.StudyStore(tmp_path) as store:
        question = domain.Question('kana-a', 'kana_romaji', 'hiragana', 'あ', 'a', [], metadata={})
        sid = store.start_session([question] * 1000)
        queue = store.get_session_questions(sid, lazy=True)
        app = fake_app(page='question', root=Widget(text='当前题目'), store=store,
                       question=queue[-1], questions=queue, session_id=sid, question_index=999)
        result = feedback_context.capture_feedback_context(app)
        assert result['practice']['queue_length'] == 1000
        assert result['practice']['queue_scope'] == 'current_question_only; other queued answers are not exported'
        assert len(queue._copies) == 1
        assert result['practice']['maintainer_only']['hidden_solution_and_metadata']['answer'] == 'a'
