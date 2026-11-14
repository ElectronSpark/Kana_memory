"""Saving the practice kind switches and the pending queue's kind policy (design 12 D1, M4).

One write path shared by the practice-setup page and the kana depth preset
(arrangement.services.kana_depth): no App object, no Toga. ``store`` is a
StudyStore (or anything with its get_setting / set_setting /
load_session_progress); the App's in-memory preference copy stays the
caller's to keep.
"""
from __future__ import annotations

from .practice_preferences import enabled_preference_kinds

PREFERENCES_KEY = "practice_preferences"
QUEUE_KINDS_KEY = "practice_queue_kinds"


def set_queue_kind_policy(store, session_id, kinds, regular_preferences) -> None:
    """Store the kinds one pending session continues with, beside the regular kinds they replace.

    ``regular_preferences`` are the practice preferences now in force (the
    App's in-memory copy); a later change of those supersedes the policy
    (App._queue_kinds). A missing session id writes nothing.
    """
    if not isinstance(session_id, str) or not session_id:
        return
    regular = enabled_preference_kinds(regular_preferences if regular_preferences is not None else {})
    store.set_setting(QUEUE_KINDS_KEY, {
        "session_id": session_id, "kinds": list(kinds), "regular_kinds": list(regular)})


def save_kind_switches(store, preferences, *, regular_preferences, persist=True, update_queue=True,
                       lane=None) -> None:
    """Save practice preferences and point the pending queue at their enabled kinds.

    ``persist`` stores ``preferences`` under practice_preferences (the
    practice-setup page skips it while it edits a temporary plan).
    ``update_queue``: when a session is pending on ``lane`` (None: the
    store's current lane, as the practice-setup page reads it), its kind
    policy becomes the kinds ``preferences`` enable, recorded against
    ``regular_preferences``.
    """
    if persist:
        store.set_setting(PREFERENCES_KEY, preferences)
    if update_queue:
        pending = store.load_session_progress() if lane is None else store.load_session_progress(
            lane=lane, lazy_questions=True)
        if isinstance(pending, dict):
            set_queue_kind_policy(store, pending["session_id"], enabled_preference_kinds(preferences),
                                  regular_preferences)
