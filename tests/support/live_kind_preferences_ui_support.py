"""Shared live kind preferences ui fixtures and assertions; no collected scenarios."""
from kanamemory.app import KanaMemory
from types import MethodType
from unittest.mock import Mock


def preferences(*kinds):
    value = KanaMemory._clean_preferences({})
    value.update(kind="综合自适应练习", mixed_kinds=list(kinds))
    return value


def configure(app, store, *kinds):
    app.store = store
    app._practice_preferences = preferences(*kinds)
    store.set_setting("practice_preferences", app._practice_preferences)
    app._clean_preferences = KanaMemory._clean_preferences
    for name in ("_checkpoint", "resume_practice"):
        setattr(app, name, MethodType(getattr(KanaMemory, name), app))
    app.show_home, app.show_new_batch, app.show_progress = Mock(), Mock(), Mock()
    app._redraw_ink = Mock()
    app.page = "home"


def evidence(store):
    return {table: list(store._db.execute("SELECT * FROM " + table)) for table in
            ("study_card", "review_event", "session_answer", "practice_attempt")}
