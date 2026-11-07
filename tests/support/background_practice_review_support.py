"""Shared background practice review fixtures and assertions; no collected scenarios."""
from support.paths import ROOT as _TEST_ROOT
from kanamemory.content import ContentRepository
from pathlib import Path
from kanamemory.domain import StudyStore
import pytest


CONTENT = _TEST_ROOT / "app/src/kanamemory/resources/content.sqlite3"


@pytest.fixture
def real_setup(setup_app, tmp_path):
    with ContentRepository(CONTENT) as repo, StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        setup_app.repo, setup_app.store = repo, store
        yield setup_app
