"""Synthetic learners for content-library availability (design 08 sections 7–8).

Two ways to make an orphan, both on original test data only:
- registry: a synthetic extension library ``test.ext`` provides EXT_ID; the
  ContentLibraryService disables/enables it (real registry writes);
- catalogue: an id is deleted from the effective catalogue (``drop``).
The effective catalogue is replaced through ``store.content_catalogue``; no
private course content is read. No autouse fixture.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from kanamemory import content_library_rules as rules
from kanamemory.content_availability import Catalogue
from kanamemory.content_library_service import ContentLibraryService
from kanamemory.domain import StudyStore
from kanamemory.learning_store import LearningStore

START = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
LATER = START + timedelta(days=3)
EXT_LIBRARY = "test.ext"
CORE_ID = "grammar:core:desu"
EXT_ID = "grammar:ext:masu"
GONE_ID = "grammar:core:gone"
KINDS = ["grammar_meaning"]


def course(cid, **extra):
    return {"id": cid, "family": "grammar", "revision": "v1", "abilities": ["understanding"],
            "title": cid.rsplit(":", 1)[-1], "meaning": "测试", "instruction": {"title": cid, "meaning": "测试"},
            **extra}


def question(cid, qid):
    return {"id": qid, "target_id": cid, "ability": "understanding", "kind": "grammar_meaning",
            "prompt": "今、本を読んでいます。", "answer": "正在读书", "choices": ["正在读书", "读过书"],
            "input_mode": "choice", "revision": "v1"}


def extension_entry(enabled=True):
    return {"library_id": EXT_LIBRARY, "library_version": "20260102", "profile": "extension",
            "content_sha256": "a" * 64, "enabled": enabled, "installed_at_utc": "2026-10-01T00:00:00+00:00",
            "source": {"kind": "file", "name": "ext.zip", "sha256": "b" * 64, "signature_status": "unsigned"},
            "path": EXT_LIBRARY + "/20260102", "state": "ready", "summary": {"title": "测试扩展库"}}


def registry(enabled=True):
    value = rules.default_registry()
    value["libraries"].append(extension_entry(enabled))
    value["priority"].append(EXT_LIBRARY)
    return rules.validate_registry(value)


class Learner:
    """A synthetic learner whose catalogue and registry the test controls."""

    def __init__(self, store, root, learning=None):
        self.store, self.learning = store, learning or LearningStore(store)
        self.ids = {CORE_ID, EXT_ID, GONE_ID}
        self.retires = {}
        store.content_catalogue = self.catalogue
        store.set_setting(rules.REGISTRY_SETTING, registry())
        self.service = ContentLibraryService(store, root=root)

    def catalogue(self):
        ids = frozenset(self.ids)
        return Catalogue(ids=ids, providers={EXT_ID: EXT_LIBRARY} if EXT_ID in ids else {}, retires=dict(self.retires))

    def drop(self, content_id):
        """“目录中删除一项”: the effective catalogue no longer defines it."""
        self.ids.discard(content_id)

    def restore(self, content_id):
        self.ids.add(content_id)

    def disable(self):
        self.service.set_enabled(EXT_LIBRARY, False)

    def enable(self):
        self.service.set_enabled(EXT_LIBRARY, True)

    def due_course(self, cid, qid=None):
        """Enroll, teach and queue one due question for ``cid``."""
        learning = self.learning
        learning.enroll(course(cid), now=START)
        learning.advance_instruction(cid, "intro_done", now=START)
        learning.advance_instruction(cid, "guide_correct", now=START)
        learning.append_questions(question(cid, qid or "q:" + cid), now=START)
        return qid or "q:" + cid


def table_hashes(store, *, skip_settings=(rules.REGISTRY_SETTING,)):
    """sha256 of every table's rows (ordered by rowid), registry key excluded."""
    db = store._db
    names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    result = {}
    for name in names:
        rows = [list(row) for row in db.execute(f'SELECT * FROM "{name}" ORDER BY rowid')]
        if name == "app_setting":
            rows = [row for row in rows if row[0] not in skip_settings]
        result[name] = hashlib.sha256(json.dumps(rows, ensure_ascii=False, default=str).encode()).hexdigest()
    return result


@pytest.fixture
def learner(tmp_path):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        yield Learner(store, tmp_path / "libraries")
