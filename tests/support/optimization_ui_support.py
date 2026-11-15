"""Shared optimization ui fixtures and assertions; no collected scenarios."""
from kanamemory.navigation import BackStack
from kanamemory.app import KanaMemory
from types import MethodType
from unittest.mock import Mock
from kanamemory.domain import Question
from types import SimpleNamespace
from kanamemory.domain import StudyStore
import pytest
from support import kana_support


def bind(app, *methods):
    for name in methods:
        setattr(app, name, MethodType(getattr(KanaMemory, name), app))
    return app


@pytest.fixture
def learner(tmp_path):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        yield store


def session_app(store, kind="word_kana", assisted=False, feedback=False):
    question = Question("test", kind, "hiragana", "", "あ", options=["あ", "い"],
                        metadata={"content_version": "test", "kana_symbol": "あ"})
    sid = store.start_session([question])
    question = store.get_session_questions(sid)[0]  # The frozen snapshot owns the queue.
    app = kana_support.with_state(SimpleNamespace(store=store, session_id=sid, questions=[question], question=question,
        question_index=0, session_right=0, _restoring_practice=False,
        page="feedback" if feedback else ("word_question" if kind == "word_kana" else "ink_question"),
        use_full_keyboard=True, word_answer="あ", strokes=[[(10, 20, 100), (20, 30, 120)]],
        ink_width=360, ink_height=210, alert=Mock(), back_stack=BackStack()),
        answer_revealed=assisted, answered_text="あ" if feedback else None, question_audio_ok=True,
        assisted_attempts={(sid, 0)} if assisted else set(), answered_attempts=set())
    app._practice_preferences = KanaMemory._clean_preferences({})
    app._practice_preferences["mixed_kinds"] = [kind]
    bind(app, "_checkpoint", "_attempt_key", "resume_practice")
    return app
