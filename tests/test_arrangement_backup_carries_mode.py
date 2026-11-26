"""A backup carries the study mode, the focus and the review filter (design 12 section 10 (1), (5), (7)).

Through the functions the settings page uses: BackupJob("export") on one learner's data folder, then
BackupJob("restore") of those bytes into an empty data folder; the three settings come back equal and the home
page's question 学习方式还没有选 (chosen_orientation() is None) is not asked again. A record that never stored a
choice comes back without one. Synthetic stores only.
"""
import asyncio

import pytest

from kanamemory.arrangement import Focus, FocusScope, KanaTilt, Orientation, Tilt
from kanamemory.arrangement.model import ReviewFilterSetting
from kanamemory.arrangement.services import ArrangementPreferences
from kanamemory.backup_worker import BackupJob
from kanamemory.domain import StudyStore
from kanamemory.learning_store import LearningStore

FOCUS = Focus(Tilt.NEW, KanaTilt.LESS, FocusScope("route", "route:synthetic"))
FILTER = ReviewFilterSetting(only=True, excluded=(FocusScope("family", "grammar"),))


def settings(folder):
    with StudyStore(folder, time_zone="UTC") as store:
        LearningStore(store)   # as the app reopens a store after a backup job
        preferences = ArrangementPreferences(store)
        return preferences.chosen_orientation(), preferences.focus(), preferences.review_filter()


def export_and_restore(source, target):
    exported = asyncio.run(BackupJob("export", source, "UTC").run())
    StudyStore(target, time_zone="UTC").close()   # an empty learner database
    restored = asyncio.run(BackupJob("restore", target, "UTC", exported.value).run())
    assert restored.committed
    return exported.value


@pytest.mark.parametrize("mode", [Orientation.BREADTH, Orientation.THOROUGH])
def test_the_three_settings_come_back_from_a_backup_into_an_empty_database(tmp_path, mode):
    source, target = tmp_path / "learner", tmp_path / "restored"
    with StudyStore(source, time_zone="UTC") as store:
        LearningStore(store)
        preferences = ArrangementPreferences(store)
        preferences.set_orientation(mode)
        preferences.set_focus(FOCUS)
        preferences.set_review_filter(FILTER)
    assert settings(target) == (None, Focus(), ReviewFilterSetting())
    export_and_restore(source, target)
    assert settings(target) == settings(source) == (mode, FOCUS, FILTER)


def test_a_record_without_a_stored_choice_comes_back_without_one(tmp_path):
    source, target = tmp_path / "learner", tmp_path / "restored"
    with StudyStore(source, time_zone="UTC") as store:
        LearningStore(store)
    export_and_restore(source, target)
    assert settings(target)[0] is None
