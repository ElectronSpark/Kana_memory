"""Queue ordering only: never change a memory state or its scheduled due date."""
from datetime import datetime, timedelta, timezone
from . import app_clock
import json


def kana_resume_priority(state):
    """Only real input or frozen feedback pins an unfinished kana attempt."""
    state = state or {}
    if state.get("page") == "session_wait":
        return None
    if state.get("feedback_pending") or state.get("page") == "feedback":
        return (0, 0)
    text = any(isinstance(state.get(key), str) and state[key].strip()
               for key in ("romaji_answer", "word_answer", "answered_text"))
    parts = state.get("word_romaji_parts") or []
    ink = state.get("strokes") or []
    if (text or any(isinstance(part, str) and part.strip() for part in parts)
            or any(stroke for stroke in ink) or state.get("answer_revealed")):
        return (1, 0)
    return None


def reviews_today(store, now, *, course=False):
    """Read actual independent ratings across visits, using the learner's day.

    No counters are persisted or reset. Reopening, backup restoration and a
    change of time zone therefore use the same underlying review evidence.
    Unresolved answers and auxiliary word spelling never spend this priority.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Review accounting requires a timezone-aware instant")
    now = now.astimezone(timezone.utc)
    start, end = store._day_bounds(now)
    rows = store.queries.review_counts_between(course, start, end, now)
    counts = {row[0]: row[1] for row in rows}
    if not course:
        from .kana_routing import active_units
        for unit in active_units(store).values():
            counts[unit["carrier_card_id"]] = sum(counts.get(cid, 0) for cid in unit["legacy_card_ids"])
    return datetime.fromisoformat(start), counts


def stored_review_priority(store, card, *, now):
    """The same kana head priority used by both UI and synthetic actor."""
    start, counts = reviews_today(store, now)
    return review_priority(card, now=now, day_start=start,
                           reviewed_today=counts.get(card.get("card_id"), 0) if card else 0)


def review_priority(card, *, due=None, now=None, day_start=None, reviewed_today=0):
    """Due short steps, other due reviews, then first independent retrieval.

    Feedback and real drafts are handled by the caller. State 1/3 alone is not
    enough to call a card an urgent intraday step. Steps from previous learning
    days, and directions already rated twice today, join ordinary due order.
    They remain answerable before new work; their due dates are never changed.
    Two is a product priority budget, not a memory threshold or daily limit.
    """
    now = now or app_clock.utc_now()
    card = dict(card) if card is not None else {}
    if not card.get("review_count"):
        return (4, 0)
    value = due or card.get("due_at_utc")
    when = datetime.fromisoformat(value) if value else now
    if when > now:
        return (4, 0)
    scheduler = card.get("scheduler_state")
    if scheduler is None:
        scheduler = json.loads(card.get("scheduler_state_json") or "{}")
    last = scheduler.get("last_review")
    last = datetime.fromisoformat(last) if last else None
    interval = when - last if last else None
    start = day_start or now.replace(hour=0, minute=0, second=0, microsecond=0)
    short_step = (scheduler.get("state") in (1, 3) and interval is not None
                  and timedelta(0) <= interval < timedelta(days=1)
                  and start <= last <= now and now-last < timedelta(days=1)
                  and reviewed_today < 2)
    return (2 if short_step else 3, when.timestamp())
