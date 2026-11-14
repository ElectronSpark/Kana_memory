"""content_gap diagnostics keep allowed fields only and never affect selection."""
import json
from types import SimpleNamespace

from kanamemory import content_gaps


def test_event_fields_are_bounded_and_carry_no_text(monkeypatch):
    buffer = content_gaps.ContentGapBuffer()
    monkeypatch.setattr(content_gaps, "_buffer", buffer)
    secret = "窓を開けてください。private-answer"
    content_gaps.record_gap("word:a", "understanding", 2, ["vocab_meaning", secret * 10, 7])
    content_gaps.record_gap(secret, "understanding", 1)
    content_gaps.record_gap("word:a", secret, 1)
    content_gaps.record_gap("word:a", "understanding", "2")
    content_gaps.record_gap("word:a", "understanding", -1)
    result = content_gaps.gap_events()
    assert secret not in json.dumps(result, ensure_ascii=False)
    assert len(result) == 1 and result[0]["event"] == "content_gap"
    assert set(result[0]) == {"event", "content_id", "ability", "kinds", "missing", "first_at", "last_at", "count"}
    assert result[0]["content_id"] == "word:a" and result[0]["ability"] == "understanding"
    assert result[0]["kinds"] == ("vocab_meaning",) and result[0]["missing"] == 2 and result[0]["count"] == 1


def test_capacity_counts_and_copies_are_bounded():
    buffer = content_gaps.ContentGapBuffer()
    for index in range(content_gaps.MAX_EVENTS + 5):
        buffer.record(f"word:{index}", "reading", 1)
    assert len(buffer.snapshot()) == content_gaps.MAX_EVENTS
    assert buffer.snapshot()[0]["content_id"] == "word:5"
    for _ in range(content_gaps.MAX_COUNT + 2):
        buffer.record("word:9", "reading", content_gaps.MAX_COUNT + 50)
    latest = buffer.snapshot()[-1]
    assert latest["content_id"] == "word:9" and latest["count"] == content_gaps.MAX_COUNT
    assert latest["missing"] == content_gaps.MAX_COUNT
    latest["count"] = 0
    assert buffer.snapshot()[-1]["count"] == content_gaps.MAX_COUNT
    buffer.record("word:x", "reading", 1, [f"kind_{i}" for i in range(40)])
    assert len(buffer.snapshot()[-1]["kinds"]) == content_gaps.MAX_KINDS


def test_recording_failure_never_raises(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("lock")
    monkeypatch.setattr(content_gaps, "_buffer", SimpleNamespace(record=broken, snapshot=broken))
    content_gaps.record_gap("word:a", "reading", 1)
    assert content_gaps.gap_events() == []
