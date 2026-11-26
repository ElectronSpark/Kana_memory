"""The app side of the add-content use case (design 13 S2): where the app's state is handed to the admission package.

The package takes no app object. This module reads what it needs from the
app when it is asked: the store pair the StoreAdmission is built for, the
current path service and repository, and the device abilities and settings
(AddInputs) at every workload count. It creates no controls;
StudyPathUIMixin.study_admission keeps the object on the app.
"""
from __future__ import annotations

from .admission.assembly import StoreAdmission
from .admission.model import AddInputs


def current_admission(app, current: StoreAdmission | None) -> StoreAdmission:
    """``current`` while it serves the app's stores, else a new StoreAdmission for them."""
    store, learning_store = app.store, app.learning_store
    if current is not None and current.serves(store, learning_store):
        return current
    from .study_path_runtime import StudyPathRuntimeMixin
    return StoreAdmission.for_store(store, learning_store, paths=lambda: app.study_path_service(),
                                    repository=lambda: app.repo, inputs=lambda pending: admission_inputs(app, pending),
                                    arrangement=lambda: StudyPathRuntimeMixin.study_arrangement(app))


def admission_inputs(app, pending) -> AddInputs:
    """The app's current kinds and device state for a workload count (``pending``: the main lane's checkpoint).

    The kana kinds follow the pending queue's policy (app._queue_kinds) when a
    queue is pending, else the practice preferences; the device filter is
    admission_workload's.
    """
    from .practice_preferences import enabled_preference_kinds
    course_kinds = tuple(app.learning_preferences()["enabled"])
    kana_kinds = tuple(app._queue_kinds(pending) if pending else
                       enabled_preference_kinds(getattr(app, "_practice_preferences", {})))
    return AddInputs(course_kinds, kana_kinds, speech_ready=bool(getattr(app.platform, "tts_ready", False)),
                     handwriting_ready=getattr(app, "model_state", None) == "ready")
