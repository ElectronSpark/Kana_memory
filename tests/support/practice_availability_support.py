"""Shared practice availability fixtures and assertions; no collected scenarios."""
from kanamemory.domain import Question


def question(content_id="a", kind="kana_choice", *, future=None):
    metadata = {"practice_config": {"schedule_mode": "spaced"}}
    if future:
        metadata["not_before_utc"] = future.isoformat()
    return Question(content_id, kind, "hiragana", "あ", "a", ["a", "i", "u", "e"], metadata=metadata)
