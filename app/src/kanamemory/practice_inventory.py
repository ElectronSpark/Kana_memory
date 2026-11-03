"""Read-only kana coverage for the new-batch category chooser."""
from __future__ import annotations

from .domain import SCRIPTS, WORD_SKILLS, _eligible_kana


def kana_scope_inventory(repository, store, *, script="both", scopes, now=None):
    """Count learned, reserved and unseen kana once per catalog content ID.

    A kana's different question directions do not create extra inventory items.
    Selecting both scripts likewise counts the content once, matching batch
    size; evidence in either selected script counts as learned. Selecting one
    script considers only its own reviews. Valid contextual word-spelling
    units also count as practiced kana, including failed attempts: "learned"
    means encountered in practice, not mastered. Word IDs never become items.
    These are coverage counts, not a forecast of the next batch's question-type
    or audio eligibility.

    Reservations use the same live snapshot as new-batch selection, including
    unanswered drafts and entries temporarily disabled by question settings.
    A reservation holds the whole content ID, even for another script or
    direction, so adding a batch cannot select a disabled queued item again.
    Learned entries remain learned when paused, deferred or waiting to review.
    Reading these counts never creates cards or changes the saved practice.
    """
    if script not in {*SCRIPTS, "both"}:
        raise ValueError("Unknown script")
    scripts = ("hiragana", "katakana") if script == "both" else (script,)
    learned = {row["content_id"] for row in store.queries.reviewed_kana_rows()
        if row["script"] in scripts and row["skill"] not in WORD_SKILLS}
    learned.update(item["content_id"] for item in store.word_spelling_summary(now=now)
                   if item["script"] in scripts)
    reserved = store.queued_content_ids()
    eligible = [row for row in repository.kana_list()
        if any(_eligible_kana(row, alphabet, True, audio_only=False)
               for alphabet in scripts)]
    result = {}
    for title, categories in scopes.items():
        ids = {row["id"] for row in eligible if row["category"] in categories}
        studied = ids & learned
        queued = (ids & reserved) - studied
        result[title] = {
            "total": len(ids),
            "learned": len(studied),
            "queued": len(queued),
            "not_included": len(ids - studied - queued),
        }
    return result
