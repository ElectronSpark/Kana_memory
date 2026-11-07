"""Shared kana ability bridge fixtures and assertions; no collected scenarios."""
from kanamemory.domain import StudyStore
from kanamemory.kana_ability_bridge import ability_unit_id
from kanamemory.kana_ability_bridge import bridge_after_review
from datetime import datetime
import pytest
from kanamemory.kana_ability_bridge import register_legacy_map
from datetime import timedelta
from datetime import timezone


START = datetime(2026, 10, 1, tzinfo=timezone.utc)


CID = "kana-3042"


UID = ability_unit_id(CID, "hiragana", "reading")


@pytest.fixture
def store(tmp_path):
    with StudyStore(tmp_path / "learner") as value:
        yield value


def legacy_rows(store):
    return {name: [tuple(r) for r in store._db.execute("SELECT * FROM " + name)] for name in
            ("study_card", "review_event", "study_session", "session_checkpoint", "word_spelling_attempt")}


def prepare(store):
    store.record_review(CID, "kana_choice", "hiragana", 4, now=START)
    store.record_review(CID, "kana_romaji", "hiragana", 4, now=START + timedelta(hours=1))
    register_legacy_map(store)
    event = store.record_review(CID, "kana_choice", "hiragana", 4, now=START + timedelta(days=5))
    return event, bridge_after_review(store, event["event_id"])
