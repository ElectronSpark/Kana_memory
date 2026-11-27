"""Design 12 LS4-LS7 without the course: the line tilt setting, the line share rule and its property.

Made-up values and in-memory doubles (a settings double, a lesson-lines double,
a synthetic clock), plus synthetic stores for the backup and the reset.
Covers: study_lines_v1 (missing = 正常, strict shape, set and read, written only
when the tilt changes, study_focus_v1 untouched by a line-tilt change, the
backup chain, export and restore, the reset, the feedback snapshot); the rule
(strictly behind, equal keeps the order, the weights of each tilt); LineTurn
(lazy reads, no line without enrolled lessons, the one-sided guard); and the
synthetic property: lesson blocks of 1-3 min (word) and 4-8 min (grammar),
reviews of 10-60 s, 20 study hours: inside every line window each line's new
content and course review stay within its weight plus one item, every switch
goes to a line that is strictly behind, and the three units keep B9.
"""
import asyncio
import random
from datetime import datetime, timedelta, timezone

import pytest

from kanamemory.arrangement.model import Candidate, ClockEvent, Focus, KanaTilt, LineTilt, LineUsage, Orientation, \
    Tilt, Unit
from kanamemory.arrangement.rules import clock, lines, shares
from kanamemory.arrangement.services import ArrangementPreferences, ShareDispatcher
from kanamemory.arrangement.services import preferences as preference_keys
from kanamemory.arrangement.services.dispatch import LineTurn
from kanamemory.arrangement.settings import FOCUS_KEY, LINES_KEY, lines_of, lines_value, validate_lines

T0 = datetime(2030, 1, 1, 8, tzinfo=timezone.utc)
MISSING = object()


class MemorySettings:
    def __init__(self):
        self.values, self.writes = {}, []

    def get_setting(self, key, default=None):
        return self.values.get(key, default)

    def set_setting(self, key, value):
        self.values[key] = value
        self.writes.append(key)


# --- the setting -----------------------------------------------------------------------------------------------

def test_a_missing_key_means_normal_and_nothing_is_written_on_read():
    settings = MemorySettings()
    preferences = ArrangementPreferences(settings)
    assert preferences.lines() is LineTilt.EVEN and preferences.focus() == Focus()
    assert Focus().lines is LineTilt.EVEN and Focus().is_default
    assert not Focus(lines=LineTilt.GRAMMAR).is_default
    assert settings.writes == [] and lines_of(None) is LineTilt.EVEN


@pytest.mark.parametrize("bad", [
    {"version": 2, "tilt": "even"}, {"version": 1, "tilt": "most"}, {"version": 1, "tilt": "even", "extra": 1},
    {"version": 1}, {"tilt": "even"}, {"version": True, "tilt": "even"}, {"version": 1, "tilt": 1},
    ["even"], "even"])
def test_a_bad_shape_is_rejected_on_read_and_by_the_backup_chain(bad):
    with pytest.raises(ValueError):
        validate_lines(bad)
    with pytest.raises(ValueError):
        preference_keys.validate_stored(LINES_KEY, bad)
    settings = MemorySettings()
    settings.values[LINES_KEY] = bad
    with pytest.raises(ValueError):
        ArrangementPreferences(settings).focus()


def test_a_stored_null_is_not_missing():
    settings = MemorySettings()
    settings.values[LINES_KEY] = None
    with pytest.raises(ValueError):
        ArrangementPreferences(settings).lines()


@pytest.mark.parametrize("tilt", list(LineTilt))
def test_set_and_read(tilt):
    settings = MemorySettings()
    preferences = ArrangementPreferences(settings)
    focus = Focus(Tilt.NEW, KanaTilt.LESS, None, tilt)
    assert preferences.set_focus(focus) == focus == preferences.focus()
    assert validate_lines(lines_value(tilt)) == {"version": 1, "tilt": tilt.value}
    # A learner who never touches the line tilt gets no key.
    assert (LINES_KEY in settings.values) is (tilt is not LineTilt.EVEN)


def test_the_focus_key_keeps_its_shape_and_bytes_when_only_the_line_tilt_changes():
    settings = MemorySettings()
    preferences = ArrangementPreferences(settings)
    preferences.set_focus(Focus(lines=LineTilt.WORDS))
    assert settings.get_setting(FOCUS_KEY, MISSING) is MISSING   # a missing focus stays missing
    assert settings.values[LINES_KEY] == {"version": 1, "tilt": "words"}
    preferences.set_focus(Focus(Tilt.REVIEW, lines=LineTilt.WORDS))
    stored = repr(settings.values[FOCUS_KEY])
    assert set(settings.values[FOCUS_KEY]) == {"version", "tilt", "kana", "scope"}
    writes = len(settings.writes)
    preferences.set_focus(Focus(Tilt.REVIEW, lines=LineTilt.GRAMMAR))
    assert repr(settings.values[FOCUS_KEY]) == stored and settings.writes[writes:] == [LINES_KEY]
    # Back to 正常: the key now says so (it differs from the stored tilt).
    preferences.set_focus(Focus(Tilt.REVIEW))
    assert settings.values[LINES_KEY] == {"version": 1, "tilt": "even"} and preferences.focus() == Focus(Tilt.REVIEW)
    # Unchanged line tilt: the focus key is written as before, the line key is not.
    writes = len(settings.writes)
    preferences.set_focus(Focus(Tilt.NEW))
    assert settings.writes[writes:] == [FOCUS_KEY]


def test_the_key_is_registered_with_the_backup_chain_and_the_feedback_snapshot():
    from kanamemory import feedback_context
    from support.feedback_context_support import fake_app
    assert LINES_KEY in preference_keys.KEYS
    assert LINES_KEY in feedback_context._SETTING_KEYS
    app = fake_app(page="home")
    app.store.settings[LINES_KEY] = {"version": 1, "tilt": "grammar"}
    assert feedback_context.capture_feedback_context(app)["settings"][LINES_KEY] == {"version": 1, "tilt": "grammar"}


def _settings(folder):
    from kanamemory.domain import StudyStore
    from kanamemory.learning_store import LearningStore
    with StudyStore(folder, time_zone="UTC") as store:
        LearningStore(store)
        return ArrangementPreferences(store).focus(), store.get_setting(LINES_KEY, MISSING)


def _export_and_restore(source, target):
    from kanamemory.backup_worker import BackupJob
    from kanamemory.domain import StudyStore
    exported = asyncio.run(BackupJob("export", source, "UTC").run())
    StudyStore(target, time_zone="UTC").close()
    assert asyncio.run(BackupJob("restore", target, "UTC", exported.value).run()).committed


def test_a_backup_carries_the_line_tilt_and_one_without_it_restores_as_normal(tmp_path):
    from kanamemory.domain import StudyStore
    from kanamemory.learning_store import LearningStore
    source, target, plain, plain_target = (tmp_path / name for name in ("a", "a2", "b", "b2"))
    with StudyStore(source, time_zone="UTC") as store:
        LearningStore(store)
        ArrangementPreferences(store).set_focus(Focus(lines=LineTilt.GRAMMAR))
    _export_and_restore(source, target)
    assert _settings(target) == _settings(source) == (Focus(lines=LineTilt.GRAMMAR), {"version": 1, "tilt": "grammar"})
    with StudyStore(plain, time_zone="UTC") as store:
        LearningStore(store)
    _export_and_restore(plain, plain_target)
    assert _settings(plain_target) == (Focus(), MISSING)


def test_a_bad_shape_in_a_backup_is_refused_and_a_reset_keeps_the_key(tmp_path):
    from kanamemory.domain import StudyStore
    from kanamemory.learning_store import LearningStore
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        LearningStore(store)
        preferences = ArrangementPreferences(store)
        preferences.set_focus(Focus(lines=LineTilt.WORDS))
        good = store.get_setting(LINES_KEY)
        store.set_setting(LINES_KEY, {"version": 1, "tilt": "most"})
        backup = store.export_backup()
        store.set_setting(LINES_KEY, good)
        with pytest.raises(ValueError):
            store.import_backup(backup)
        assert store.get_setting(LINES_KEY) == good
        result = store.clear_learning_data()
        assert LINES_KEY not in result["deleted_settings"]
        assert preferences.lines() is LineTilt.WORDS


# --- the rule --------------------------------------------------------------------------------------------------

def test_the_weights_of_the_three_tilts():
    assert [lines.weights_of(tilt) for tilt in LineTilt] == [(85, 15), (70, 30), (50, 50)]
    assert lines.weights_of(None) == lines.LINE_WEIGHTS[lines.DEFAULT_TILT] == (70, 30)


def test_only_a_line_strictly_behind_is_turned_to():
    even = lines.weights_of(LineTilt.EVEN)
    assert lines.turn(LineUsage(), Unit.NEW, "word", even) is None                     # both zero
    assert lines.turn(LineUsage(new_word=70, new_grammar=30), Unit.NEW, "word", even) is None   # equal per weight
    assert lines.turn(LineUsage(new_word=71, new_grammar=30), Unit.NEW, "word", even) == "grammar"
    assert lines.turn(LineUsage(new_word=71, new_grammar=30), Unit.NEW, "grammar", even) is None
    assert lines.turn(LineUsage(new_word=69, new_grammar=30), Unit.NEW, "grammar", even) == "word"
    # Inside course review only the course seconds count.
    assert lines.turn(LineUsage(new_word=500, course_word=10), Unit.COURSE, "word", even) == "grammar"
    assert lines.turn(LineUsage(new_word=500), Unit.COURSE, "word", even) is None
    # The tilt moves the point of the switch.
    usage = LineUsage(new_word=80, new_grammar=20)
    assert [lines.turn(usage, Unit.NEW, "word", lines.weights_of(tilt)) for tilt in LineTilt] == [
        None, "grammar", "grammar"]
    assert lines.turn(LineUsage(new_word=86, new_grammar=14), Unit.NEW, "word", (85, 15)) == "grammar"


class Lessons:
    """ports.LessonLines double that counts its reads."""

    def __init__(self, lessons, positions=None, untaught=frozenset()):
        self._lessons, self._positions, self._untaught = lessons, positions or {}, frozenset(untaught)
        self.reads = []

    def lines(self):
        self.reads.append("lines")
        return dict(self._lessons)

    def positions(self):
        self.reads.append("positions")
        return dict(self._positions)

    def untaught(self):
        self.reads.append("untaught")
        return self._untaught


def turn_over(lessons, usage=LineUsage(), weights=(70, 30)):
    asked = []
    return LineTurn(lambda: asked.append("usage") or usage, lambda: asked.append("weights") or weights, lessons), asked


def test_the_turn_reads_nothing_until_asked_and_each_thing_once():
    lessons = Lessons({"w1": "word", "g1": "grammar"})
    turn, asked = turn_over(lessons, LineUsage(new_word=100))
    assert asked == [] and lessons.reads == []
    assert turn.new_turn("word") == "grammar" and turn.new_turn("word") == "grammar"
    assert turn.review_turn("word") is None
    assert asked == ["usage", "weights"] and lessons.reads == ["lines"]
    assert turn.line_of("g1") == "grammar" and turn.line_of("not enrolled") == "word"


def test_no_turn_to_a_line_without_enrolled_lessons():
    turn, _ = turn_over(Lessons({"w1": "word", "w2": "word"}), LineUsage(new_word=100, course_word=100))
    assert turn.new_turn("word") is None and turn.review_turn("word") is None


def test_the_guard_is_one_sided_and_needs_a_route_position():
    positions = {"w-early": 0, "w-taught": 1, "g": 2, "w-late": 3, "g-late": 4}
    lessons = Lessons({"w-early": "word", "w-taught": "word", "w-late": "word", "w-off": "word", "g": "grammar",
                       "g-off": "grammar", "g-late": "grammar"},
                      positions, untaught={"w-early", "w-late", "w-off", "g", "g-off", "g-late"})
    turn, _ = turn_over(lessons)
    assert turn.waiting("grammar", "g") == {"w-early"}               # before it and not taught yet
    assert turn.waiting("grammar", "g-late") == {"w-early", "w-late"}
    assert turn.waiting("grammar", "g-off") == frozenset()            # no position: not guarded
    assert turn.waiting("word", "w-late") == frozenset()              # one-sided: a word lesson is never held
    no_words_before = Lessons({"w-late": "word", "g": "grammar"}, positions, untaught={"w-late", "g"})
    turn, _ = turn_over(no_words_before)
    assert turn.waiting("grammar", "g") == frozenset() and "untaught" not in no_words_before.reads


def test_a_futile_line_read_is_remembered_for_the_round_the_unit_the_line_and_the_interval():
    from kanamemory.arrangement.services.probe_memo import RETRY_AFTER, ProbeMemo
    memo, lessons = ProbeMemo(), Lessons({"w1": "word", "g1": "grammar"})
    usage = LineUsage(new_word=100, course_word=100)

    def turn(at, round_id="r1"):
        return LineTurn(lambda: usage, lambda: (70, 30), lessons, memo=memo, round_id=round_id, now=at)
    assert turn(T0).new_turn("word") == "grammar" and turn(T0).review_turn("word") == "grammar"
    turn(T0).found_nothing("course", "grammar")
    assert turn(T0 + timedelta(seconds=30)).review_turn("word") is None      # remembered: no read
    assert turn(T0 + timedelta(seconds=30)).new_turn("word") == "grammar"   # another unit is not
    assert turn(T0 + RETRY_AFTER).review_turn("word") == "grammar"          # the interval is over
    assert turn(T0 + timedelta(seconds=30), "r2").review_turn("word") == "grammar"   # a new round
    turn(T0, None).found_nothing("new", "grammar")                       # nothing is kept without a round
    assert turn(T0, None).new_turn("word") == "grammar"


# --- the property (synthetic, like B9's test_arrangement_tilt) --------------------------------------------------

class Clock:
    """Event source and synthetic clock: each served item is one event after its own time."""

    def __init__(self):
        self.now, self.events = T0, []

    def clock_events(self, now):
        since = now - timedelta(hours=3)   # beyond the 2-hour line window
        return [event for event in self.events[-2000:] if since <= event.at <= now]


class Heads:
    """Both lines always have a candidate in both units: the share decides the unit, the line share the line."""

    def review_candidates(self):
        return (Candidate(Unit.COURSE), Candidate(Unit.KANA))

    def new_candidate(self):
        return Candidate(Unit.NEW)


BLOCK = {"word": (60, 180), "grammar": (240, 480)}
REVIEW = (10, 60)


def simulate(tilt, *, share_lines=True, hours=20, seed=7):
    """The head of each unit is of the word line (words come first on the route); the line share may turn it."""
    rng = random.Random(seed)
    preferences = ArrangementPreferences(MemorySettings())
    preferences.set_orientation(Orientation.BREADTH)
    preferences.set_focus(Focus(lines=tilt))
    source = Clock()
    share = ShareDispatcher(preferences, source, Lessons({"w": "word", "g": "grammar"}))
    weights, by_unit = lines.weights_of(tilt), shares.unit_weights(shares.weights_for(Focus()))
    record = {"switches": 0, "not_behind": 0, "over": {}, "b9": 0}
    while source.now < T0 + timedelta(hours=hours):
        usage = share.usage(source.now)
        unit = share.decide(Heads(), source.now).unit
        per_weight = {other: usage.of(other) / by_unit[other] for other in Unit}
        record["b9"] += any(per_weight[unit] > value for value in per_weight.values())
        line_usage = share.line_usage(source.now)
        if source.now > T0 + timedelta(seconds=lines.LINE_WINDOW):
            for each in (Unit.NEW, Unit.COURSE):
                word, grammar = line_usage.of(each, "word"), line_usage.of(each, "grammar")
                for name, used, weight in (("word", word, weights[0]), ("grammar", grammar, weights[1])):
                    key = (each, name)
                    record["over"][key] = max(record["over"].get(key, 0.0),
                                              used - (word + grammar) * weight / sum(weights))
        line = None
        if unit in (Unit.NEW, Unit.COURSE):
            line = "word"
            turn = share.line_turn(source.now)
            other = (turn.new_turn(line) if unit is Unit.NEW else turn.review_turn(line)) if share_lines else None
            if other is not None:
                record["switches"] += 1
                record["not_behind"] += lines.behind(line_usage, unit, weights) != other
                line = other
        seconds = rng.uniform(*BLOCK[line]) if unit is Unit.NEW else rng.uniform(*REVIEW)
        source.now += timedelta(seconds=seconds)
        source.events.append(ClockEvent(source.now, "d1", unit, teaching=unit is Unit.NEW, line=line))
    totals = {}
    for event, seconds in clock.charges(source.events):
        totals[(event.unit, event.line)] = totals.get((event.unit, event.line), 0.0) + seconds
    return record, totals


def unit_shares(totals):
    spent = {unit: sum(value for (each, _), value in totals.items() if each is unit) for unit in Unit}
    return {unit: spent[unit] / sum(spent.values()) for unit in Unit}


@pytest.mark.parametrize("tilt", list(LineTilt))
def test_each_line_keeps_its_weight_in_every_window_and_every_switch_goes_to_the_line_behind(tilt):
    record, totals = simulate(tilt)
    assert record["switches"] > 50 and record["not_behind"] == 0
    # Inside every line window (after the first one) a line is above its weighted share by at most one block:
    # the item it was chosen for (a line is chosen only while strictly behind), plus what the window's far edge
    # takes from the other line as the window slides on before that unit's next decision -- in new content one
    # grammar block and one word block (8 + 3 min), in course review three reviews (a new-content block between
    # two course decisions moves the edge over older reviews). Seeds 1-8: at most 596 s and 137 s.
    for (unit, line), over in record["over"].items():
        bound = BLOCK["grammar"][1] + BLOCK["word"][1] if unit is Unit.NEW else 3 * REVIEW[1]
        assert over <= bound, (tilt, unit, line, over)
    # Over the 20 hours both lines are served in both units near their weights. Grammar blocks are chunky
    # against a 2-hour window, so grammar runs ahead in new content by part of a block (as B9 (4) for the
    # units): word share 0.79-0.82 for 85, 0.65-0.68 for 70, 0.47-0.48 for 50 (seeds 1-8).
    for unit, tolerance in ((Unit.NEW, 0.07), (Unit.COURSE, 0.02)):
        word, grammar = totals[(unit, "word")], totals[(unit, "grammar")]
        weight = lines.weights_of(tilt)[0] / sum(lines.weights_of(tilt))
        assert abs(word / (word + grammar) - weight) < tolerance, (tilt, unit, word / (word + grammar))


@pytest.mark.parametrize("tilt", list(LineTilt))
def test_the_three_units_keep_b9_with_the_line_share(tilt):
    record, totals = simulate(tilt)
    _, without = simulate(tilt, share_lines=False)
    assert record["b9"] == 0
    with_lines, plain = unit_shares(totals), unit_shares(without)
    assert all(abs(with_lines[unit] - plain[unit]) < 0.02 for unit in Unit), (with_lines, plain)
