"""Shared kana context words fixtures and assertions; no collected scenarios."""
from support.word_context_memory_support import ContextContent
from support.word_context_memory_support import KA
from support.word_context_memory_support import KI
from support.word_context_memory_support import KU
from support.word_context_memory_support import NOW
from kanamemory.domain import StudyStore
import pytest
from datetime import timedelta


KINDS = ["kana_choice", "word_meaning_kana"]


@pytest.fixture
def setup(tmp_path):
    repo = ContextContent()
    with StudyStore(tmp_path / "learner", "UTC") as store:
        from kanamemory.learning_store import LearningStore
        LearningStore(store)
        yield repo, store, tmp_path


def learn(store, n=6):
    for i in range(n):
        store.record_review((KA, KI, KU)[i % 3], "kana_choice", "hiragana", 4,
            outcome="correct", now=NOW-timedelta(days=2)+timedelta(seconds=i))
