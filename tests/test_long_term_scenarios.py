"""Validate synthetic workload inputs separately from the app under test."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest

from long_term_scenarios import make_scenario, SyntheticLearner, PROFILES


@pytest.mark.parametrize("seed", [20261027, 724031, 170219])
def test_calendar_has_four_years_and_separate_short_and_long_absences(seed):
    scenario = make_scenario(seed)
    assert len(scenario.days) == 1461
    assert scenario.days[0].date == "2027-01-01"
    assert scenario.days[-1].date == "2030-12-31"
    assert len([g for g in scenario.gaps if g.kind == "short"]) == 36
    assert sorted(g.length_days for g in scenario.gaps if g.kind == "long") == [21, 35, 63, 105]
    occupied = set()
    for gap in scenario.gaps:
        days = set(range(gap.start_day, gap.start_day + gap.length_days))
        assert not occupied.intersection(days)
        occupied.update(days)
    assert occupied == {day.day_index for day in scenario.days if day.kind == "gap"}
    assert all(not day.sessions and day.new_items == 0 for day in scenario.days if day.kind == "gap")
    assert scenario.summary()["normal_fraction_of_active_days"] > 0.9
    assert len([d for d in scenario.days if d.kind == "surge"]) == 12
    assert len([d for d in scenario.days if d.kind == "low"]) == 48
    for day in scenario.days:
        if day.kind == "normal":
            assert 3 <= len(day.sessions) <= 5
            assert 2 <= day.new_items <= 4
        if day.kind == "surge":
            assert day.new_items >= 30
        for a, b in zip(day.sessions, day.sessions[1:]):
            assert a.start + timedelta(seconds=a.duration_seconds) < b.start
        assert all(180 <= s.duration_seconds <= 480 for s in day.sessions)


def test_profiles_preserve_admission_calendar_for_capacity_sensitivity():
    scenarios = [make_scenario(profile=profile) for profile in PROFILES]
    reference = [(d.date, d.kind, d.new_items, d.day_factor) for d in scenarios[0].days]
    assert all([(d.date, d.kind, d.new_items, d.day_factor) for d in s.days] == reference for s in scenarios)
    assert scenarios[1].summary()["planned_study_hours"] < scenarios[0].summary()["planned_study_hours"] < scenarios[2].summary()["planned_study_hours"]
    assert make_scenario().to_dict() == make_scenario().to_dict()
    assert make_scenario(1).to_dict() != make_scenario(2).to_dict()


def test_minimum_three_year_scenario_and_input_validation():
    assert len(make_scenario(days=1096).days) == 1096
    with pytest.raises(ValueError):
        make_scenario(days=1095)
    with pytest.raises(ValueError):
        make_scenario(profile="unknown")


def test_recall_drops_after_absence_without_phantom_observations():
    model = SyntheticLearner()
    now = datetime(2027, 1, 1, tzinfo=timezone.utc)
    model.teach("word:one:reading", now)
    before = model.state()
    probabilities = [model.probability("word:one:reading", now + timedelta(days=d)) for d in (0, 1, 7, 90)]
    assert all(a >= b for a, b in zip(probabilities, probabilities[1:]))
    assert probabilities[0] > probabilities[-1]
    assert model.state() == before


def test_difficulty_ability_and_profile_differences_are_fixed_not_fsrs_inputs():
    now = datetime(2027, 1, 1, tzinfo=timezone.utc)
    model = SyntheticLearner()
    difficulties = [model._traits(str(i)) for i in range(100)]
    assert max(difficulties) / min(difficulties) > 2
    assert difficulties == [model._traits(str(i)) for i in range(100)]
    model.teach("same", now)
    assert model.probability("same", now, kind="kana_handwriting") < model.probability("same", now, kind="kana_choice")
    weak, strong = SyntheticLearner(profile="constrained"), SyntheticLearner(profile="strong")
    for learner in (weak, strong):
        learner.teach("same", now)
    assert weak.probability("same", now+timedelta(days=1)) < strong.probability("same", now+timedelta(days=1))


def test_external_learner_roundtrip_keeps_results_reproducible_and_failure_rating_honest():
    now = datetime(2027, 1, 1, tzinfo=timezone.utc)
    model = SyntheticLearner(170219)
    model.teach("x", now)
    model.answer("x", now + timedelta(minutes=10), kind="grammar_cloze")
    restored = SyntheticLearner.from_state(json.loads(json.dumps(model.state())))
    for days in (1, 2, 8, 90, 180, 240):
        a = model.answer("x", now+timedelta(days=days), kind="vocab_reading", day_factor=0.9)
        b = restored.answer("x", now+timedelta(days=days), kind="vocab_reading", day_factor=0.9)
        assert a == b
        assert 0 < a.recall_probability < 1
        assert a.duration_seconds > 0
        assert (a.correct and a.rating in (2, 3, 4) and a.outcome == "correct" and not a.assisted
                or not a.correct and a.rating == 1 and a.outcome in ("incorrect", "revealed"))
    assert model.state() == restored.state()


def test_bad_clock_cannot_silently_change_external_memory():
    model = SyntheticLearner()
    now = datetime(2027, 1, 1, tzinfo=timezone.utc)
    model.teach("x", now)
    with pytest.raises(ValueError):
        model.answer("x", now-timedelta(seconds=1))
    with pytest.raises(ValueError):
        model.answer("x", datetime(2027, 1, 1))
