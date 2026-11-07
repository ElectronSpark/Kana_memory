"""Abandoned practice construction stops without committing learner state."""
from concurrent.futures import CancelledError
from record_test_support import decode_current
from pathlib import Path
import sys
from threading import Event
from unittest.mock import Mock

import pytest

from kanamemory import domain
from kanamemory.domain import StudyStore, build_questions, build_practice_batch
from support.domain_support import SmallContent
from support.domain_support import START


@pytest.mark.parametrize("batch,combined", [(False, False), (True, False), (True, True)])
def test_cancel_after_first_option_generation_stops_before_next_content(tmp_path, monkeypatch, batch, combined):
    with StudyStore(tmp_path) as store:
        cancelled = Event()
        def check():
            if cancelled.is_set():
                raise CancelledError()
        store._build_cancel_check = check
        original = domain._kana_choices
        calls = []
        def options(*args, **kwargs):
            calls.append(args[0]["id"])
            result = original(*args, **kwargs)
            cancelled.set()
            return result
        monkeypatch.setattr(domain, "_kana_choices", options)
        before = decode_current(store.export_backup())["tables"]
        config = dict(seed=1, now=START, limit=10)
        if batch:
            config.update(kinds=("kana_choice", "kana_romaji"), rounds=3,
                          script="both" if combined else "hiragana", adaptive=combined)
        else:
            config.update(kind="kana_choice")
        with pytest.raises(CancelledError):
            (build_practice_batch if batch else build_questions)(SmallContent(), store, **config)
        assert len(calls) == 1
        assert decode_current(store.export_backup())["tables"] == before


def test_cancel_is_checked_before_entering_combined_builder(tmp_path):
    with StudyStore(tmp_path) as store:
        def check():
            raise CancelledError()
        store._build_cancel_check = check
        repo = Mock()
        with pytest.raises(CancelledError):
            build_practice_batch(repo, store, kinds=("kana_choice", "word_kana"),
                                 script="both", adaptive=True)
        assert not repo.mock_calls


def test_only_explicit_callable_cancel_hook_is_used():
    store = Mock()
    domain._check_build_cancelled(store)
    assert not store.mock_calls
    assert "_build_cancel_check" not in store.__dict__
    store._build_cancel_check = False
    domain._check_build_cancelled(store)
    called = []
    store._build_cancel_check = lambda: called.append(True)
    domain._check_build_cancelled(domain._BatchQuestionStore(store))
    assert called == [True]
