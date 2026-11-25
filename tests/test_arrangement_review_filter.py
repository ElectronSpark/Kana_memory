"""Design 12 H8-H10, E9 without a store: the setting, the one filter rule, its service, the parts.

Made-up members and an in-memory settings double; instants are handed in.
Covers: study_review_filter_v1's strict shape (limits, unknown kind,
duplicates, missing key, an older record without the key), its registration
with the backup validation and the feedback snapshot; the pure filter (members
of both lanes, exclusion over the scope, the lifted flag, the part, tiers 0-2
always served, the apart counts, the two "newly learned" parts); the service
(只做范围内 needs a live scope, 暂不复习 pauses a scope's own members only, the
lift lasts until a sitting break or another study day, a write rearranges the
round once, the part lives as long as the extra-practice mode).
"""
from datetime import datetime, timedelta, timezone

from support.learning_fixtures import learning_app  # noqa: F401
from support.app_fixtures import question_app, setup_app  # noqa: F401

import pytest

from kanamemory.arrangement.model import Focus, FocusScope, Part, ReviewFilterSetting
from kanamemory.arrangement.rules.focus import kana_member
from kanamemory.arrangement.rules.review_filter import (Apart, ServeFilter, allows, apart, part_lessons, serve_filter,
                                                       serves)
from kanamemory.arrangement.services import preferences as preference_keys
from kanamemory.arrangement.services.extra_session import ExtraSession
from kanamemory.arrangement.services.focus import FocusService
from kanamemory.arrangement.services.preferences import ArrangementPreferences
from kanamemory.arrangement.services.review_filter import ReviewFilterService, serving, serving_filter
from kanamemory.arrangement.settings import (MAX_EXCLUDED, REVIEW_FILTER_KEY, review_filter_of, review_filter_value,
                                             validate_review_filter)

NOW = datetime(2026, 11, 24, 12, tzinfo=timezone.utc)
SCOPE = FocusScope("family", "grammar")
ROUTE = FocusScope("route", "route:one")
LIBRARY = FocusScope("library", "lib:extra")
KANA_A = kana_member("kana:a", "hiragana")
KANA_KA = kana_member("kana:ka", "hiragana")


def value(only=False, excluded=()):
    return {"version": 1, "only": only, "excluded": [dict(item) for item in excluded]}


# --- the setting ---------------------------------------------------------------------------------------------

def test_the_setting_has_a_strict_shape_and_round_trips():
    stored = value(True, [{"kind": "route", "id": "route:one"}, {"kind": "node", "id": "route:one|unit:2"}])
    clean = validate_review_filter(stored)
    assert clean == stored and clean is not stored
    setting = review_filter_of(stored)
    assert setting == ReviewFilterSetting(True, (ROUTE, FocusScope("node", "route:one|unit:2")))
    assert review_filter_value(setting) == stored


@pytest.mark.parametrize("bad", [
    None, [], {"version": 1, "only": False},                                  # missing field
    {"version": 1, "only": False, "excluded": [], "extra": 1},                # unknown field
    {"version": 2, "only": False, "excluded": []},                            # unknown version
    {"version": True, "only": False, "excluded": []},                         # bool is not a version
    {"version": 1, "only": 1, "excluded": []},                                # the switch is a bool
    {"version": 1, "only": False, "excluded": {}},                            # a list of scopes
    {"version": 1, "only": False, "excluded": [{"kind": "ability", "id": "x"}]},        # unknown kind
    {"version": 1, "only": False, "excluded": [{"kind": "route", "id": ""}]},           # empty id
    {"version": 1, "only": False, "excluded": [{"kind": "route", "id": "x" * 1025}]},   # id too long
    {"version": 1, "only": False, "excluded": [{"kind": "route"}]},                     # no id
    {"version": 1, "only": False, "excluded": [{"kind": "route", "id": "a"}, {"kind": "route", "id": "a"}]},
    {"version": 1, "only": False, "excluded": [{"kind": "family", "id": f"f{i}"} for i in range(MAX_EXCLUDED + 1)]},
])
def test_a_bad_shape_is_rejected(bad):
    with pytest.raises(ValueError):
        validate_review_filter(bad)


def test_twenty_paused_scopes_are_allowed_and_the_kinds_are_the_focus_scopes_kinds():
    many = value(False, [{"kind": kind, "id": f"{kind}:{i}"} for i, kind in
                         enumerate(["route", "node", "library", "family"] * 5)])
    assert len(validate_review_filter(many)["excluded"]) == MAX_EXCLUDED == 20


class Settings:
    """get_setting / set_setting over a dict (the store's public settings methods)."""

    def __init__(self, values=None):
        self.values = dict(values or {})
        self.writes = []

    def get_setting(self, key, default=None):
        return self.values.get(key, default)

    def set_setting(self, key, stored):
        self.writes.append(key)
        self.values[key] = stored


def test_a_missing_key_and_an_older_record_mean_off_and_nothing_paused_and_nothing_is_written():
    store = Settings({"study_focus_v1": {"version": 1, "tilt": "even", "kana": "normal", "scope": None}})
    preferences = ArrangementPreferences(store)
    assert preferences.review_filter() == ReviewFilterSetting()
    assert store.writes == []
    store.values[REVIEW_FILTER_KEY] = None          # a stored JSON null is not "missing"
    with pytest.raises(ValueError):
        preferences.review_filter()


def test_the_key_is_registered_with_the_backup_chain_and_the_feedback_snapshot():
    assert REVIEW_FILTER_KEY in preference_keys.KEYS
    assert preference_keys.validate_stored(REVIEW_FILTER_KEY, value(True)) == value(True)
    with pytest.raises(ValueError):
        preference_keys.validate_stored(REVIEW_FILTER_KEY, value("yes"))
    from kanamemory import feedback_context
    assert REVIEW_FILTER_KEY in feedback_context._SETTING_KEYS


def test_the_focus_setting_keeps_its_shape():
    preferences = ArrangementPreferences(Settings())
    preferences.set_focus(Focus(scope=SCOPE))
    assert preferences._store.values["study_focus_v1"] == {
        "version": 1, "tilt": "even", "kana": "normal", "scope": {"kind": "family", "id": "grammar"}}


# --- the pure filter -----------------------------------------------------------------------------------------

def test_no_switch_means_no_filter_at_all():
    assert serve_filter() is None
    assert serve_filter(only=None, lifted=True) is None
    assert allows(None, "anything") and serves(None, None, 9)


def test_the_filter_covers_lessons_and_kana_and_exclusion_wins_over_the_scope():
    inside = {"lesson:a", "lesson:b", KANA_A}
    serve = serve_filter(excluded={"lesson:b", KANA_KA}, only=inside)
    assert serve.limits_to_scope
    assert serve.allows("lesson:a") and serve.allows(KANA_A)
    assert not serve.allows("lesson:b")            # inside the scope, but paused: exclusion wins
    assert not serve.allows("lesson:c")            # outside the scope
    assert not serve.allows(KANA_KA)               # paused kana
    assert not serve.allows(None)                  # work of no member is outside a scope


def test_tiers_zero_to_two_are_served_wherever_they_lie():
    serve = serve_filter(excluded={"lesson:x"}, only={"lesson:a"})
    for tier in (0, 1, 2):
        assert serve.serves("lesson:x", tier) and serve.serves("lesson:c", tier)
    for tier in (3, 4, 5, None):
        assert not serve.serves("lesson:x", tier) and not serve.serves("lesson:c", tier)
        assert serve.serves("lesson:a", tier)


def test_the_lifted_flag_removes_only_the_scope_limit():
    serve = serve_filter(excluded={"lesson:x"}, only={"lesson:a"}, lifted=True)
    assert not serve.limits_to_scope and serve.allows("lesson:c") and not serve.allows("lesson:x")
    assert serve_filter(only={"lesson:a"}, lifted=True) is None


def test_the_part_limits_on_top_of_the_switches():
    serve = serve_filter(excluded={"lesson:x"}, only={"lesson:a", "lesson:b", "lesson:x"}, part={"lesson:b", "lesson:x"})
    assert serve.allows("lesson:b")
    assert not serve.allows("lesson:a")            # inside the scope, outside the part
    assert not serve.allows("lesson:x")            # inside the part, paused
    alone = serve_filter(part={"lesson:b"})
    assert alone.allows("lesson:b") and not alone.allows(KANA_A) and not alone.limits_to_scope
    assert isinstance(alone, ServeFilter)


def test_the_apart_counts_put_each_due_review_in_one_place_exclusion_first():
    due = ["lesson:a", "lesson:x", "lesson:x", "lesson:c", KANA_KA, KANA_A]
    counted = apart(due, excluded={"lesson:x", KANA_KA}, only={"lesson:a", "lesson:x", KANA_A}, paused_parts=2)
    assert counted == Apart(outside=1, paused_parts=2, paused_due=3)
    assert apart(due, excluded=(), only=None, paused_parts=0) == Apart()


def test_the_two_newly_learned_parts_use_the_study_day_and_the_recent_predicate():
    def study_day(at):   # a study day that starts at 04:00 UTC
        return (at - timedelta(hours=4)).date().isoformat()
    firsts = [("lesson:today", NOW - timedelta(hours=2)), ("lesson:today", None),
              ("lesson:night", NOW.replace(hour=3)),                  # before 04:00: yesterday's study day
              ("lesson:week", NOW - timedelta(days=6)), ("lesson:old", NOW - timedelta(days=14, seconds=1)),
              ("lesson:never", None)]
    assert part_lessons(firsts, Part.TODAY, NOW, study_day=study_day) == {"lesson:today"}
    assert part_lessons(firsts, Part.RECENT, NOW, study_day=study_day) == {
        "lesson:today", "lesson:night", "lesson:week"}
    assert part_lessons(firsts, Part.SCOPE, NOW, study_day=study_day) is None
    assert part_lessons(firsts, Part.ALL, NOW, study_day=study_day) is None


# --- the service ---------------------------------------------------------------------------------------------

class Catalog:
    """ports.ScopeCatalog: contents (members and their prerequisites) and members (without), fixed by the test."""

    def __init__(self, contents, members=None):
        self._contents, self._members = contents, members or contents

    def contents(self, scope):
        value = self._contents.get(scope)
        return None if value is None else frozenset(value)

    def members(self, scope):
        value = self._members.get(scope)
        return None if value is None else frozenset(value)

    def title(self, scope):
        return scope.id if scope in self._contents else None


def service(contents, members=None, *, focus=SCOPE, restarts=None):
    store = Settings()
    preferences = ArrangementPreferences(store)
    if focus is not None:
        preferences.set_focus(Focus(scope=focus))
    restarts = [] if restarts is None else restarts
    focus_service = FocusService(preferences, Catalog(contents, members))
    return store, ReviewFilterService(preferences, focus_service, study_day=lambda at: at.date().isoformat(),
                                      restart=lambda: restarts.append(1))


def test_only_acts_with_a_live_scope_and_a_scope_that_does_not_resolve_has_no_effect():
    _, filters = service({SCOPE: {"lesson:a", "prerequisite:p"}})
    assert filters.current(NOW) is None and not filters.only_acts(NOW)
    filters.set_only(True)
    serve = filters.current(NOW)
    assert filters.only_acts(NOW) and serve.only == {"lesson:a", "prerequisite:p"}   # prerequisites included
    _, gone = service({SCOPE: None})
    gone.set_only(True)
    assert gone.current(NOW) is None and not gone.only_acts(NOW)
    _, none = service({}, focus=None)
    none.set_only(True)
    assert none.current(NOW) is None


def test_pausing_a_scope_pauses_its_own_members_not_its_prerequisites_and_writes_rearrange_once():
    restarts = []
    store, filters = service({ROUTE: {"lesson:r1", "lesson:r2", "lesson:base"}, SCOPE: {"lesson:a"}},
                             {ROUTE: {"lesson:r1", "lesson:r2"}, SCOPE: {"lesson:a"}}, restarts=restarts)
    filters.pause(ROUTE)
    assert filters.current(NOW).excluded == {"lesson:r1", "lesson:r2"}
    assert store.values[REVIEW_FILTER_KEY] == value(False, [{"kind": "route", "id": "route:one"}])
    assert restarts == [1]
    filters.pause(ROUTE)                            # already paused: nothing written, no rearrangement
    assert restarts == [1] and store.writes.count(REVIEW_FILTER_KEY) == 1
    filters.restore(ROUTE)
    assert filters.current(NOW) is None and restarts == [1, 1]
    filters.restore(ROUTE)
    assert restarts == [1, 1]


def test_a_paused_scope_that_no_longer_resolves_pauses_nothing_and_is_kept():
    store, filters = service({SCOPE: {"lesson:a"}}, {SCOPE: {"lesson:a"}})
    filters.pause(LIBRARY)
    assert filters.paused_members() == frozenset()
    assert filters.setting().excluded == (LIBRARY,)


def test_at_most_twenty_scopes_can_be_paused():
    _, filters = service({})
    for index in range(MAX_EXCLUDED):
        filters.pause(FocusScope("family", f"f{index}"))
    with pytest.raises(ValueError):
        filters.pause(FocusScope("family", "one more"))
    assert len(filters.setting().excluded) == MAX_EXCLUDED


def test_the_lift_lasts_until_a_sitting_break_or_another_study_day_and_is_never_written():
    store, filters = service({SCOPE: {"lesson:a"}})
    filters.set_only(True)
    writes = list(store.writes)
    filters.lift(NOW)
    assert filters.current(NOW) is None and not filters.only_acts(NOW)
    for minutes in (10, 20, 29):                    # each "继续学习" inside the sitting keeps it
        filters.touch(NOW + timedelta(minutes=minutes))
        assert filters.lifted(NOW + timedelta(minutes=minutes))
    assert filters.lifted(NOW + timedelta(minutes=29 + 30))
    assert not filters.lifted(NOW + timedelta(minutes=29 + 31))      # more than 30 minutes since the last one
    filters.touch(NOW + timedelta(minutes=29 + 31))
    assert filters.current(NOW + timedelta(minutes=61)).limits_to_scope
    filters.lift(NOW.replace(hour=23, minute=50))
    assert not filters.lifted(NOW.replace(hour=23, minute=50) + timedelta(minutes=20))   # another study day
    assert store.writes == writes
    filters.lift(NOW)
    filters.set_only(True)                          # unchanged setting: still forgets the lift
    assert filters.only_acts(NOW)


def test_the_part_lives_as_long_as_the_extra_practice_mode():
    _, filters = service({SCOPE: {"lesson:a"}})

    class Pool:
        def read(self, now, **kwargs):
            return []
    session = ExtraSession(Pool(), new_sitting=lambda: None, new_deck=lambda items, sitting: None,
                           on_stop=lambda: filters.set_part(None))
    filters.set_part({"lesson:b"})
    session.start(NOW)
    assert filters.current(NOW).part == {"lesson:b"}
    assert filters.current(NOW, with_part=False) is None
    session.stop()
    assert filters.current(NOW) is None


def test_serving_puts_the_filter_where_the_lanes_read_it_and_restores_the_outer_one():
    class Store:
        pass
    store, outer, inner = Store(), serve_filter(excluded={"a"}), serve_filter(excluded={"b"})
    assert serving_filter(store) is None
    with serving(store, outer):
        assert serving_filter(store) is outer
        with serving(store, inner):
            assert serving_filter(store) is inner
        assert serving_filter(store) is outer
    assert serving_filter(store) is None


# --- storage: backup and restore, reset, the feedback snapshot, an unknown key ------------------------------------

@pytest.fixture
def store(tmp_path):
    from kanamemory.domain import StudyStore
    with StudyStore(tmp_path / "learner", time_zone="UTC") as value:
        yield value


def test_backup_and_restore_keep_the_key_and_reject_a_bad_shape(store):
    preferences = ArrangementPreferences(store)
    setting = ReviewFilterSetting(True, (ROUTE, LIBRARY))
    preferences.set_review_filter(setting)
    store.import_backup(store.export_backup())
    assert preferences.review_filter() == setting
    good = store.get_setting(REVIEW_FILTER_KEY)
    store.set_setting(REVIEW_FILTER_KEY, value(True, [{"kind": "planet", "id": "x"}]))
    backup = store.export_backup()
    store.set_setting(REVIEW_FILTER_KEY, good)
    with pytest.raises(ValueError):
        store.import_backup(backup)
    assert store.get_setting(REVIEW_FILTER_KEY) == good


def test_a_settings_key_this_version_does_not_know_passes_the_backup_chain_unchanged(store):
    # How an app version that does not know study_review_filter_v1 treats it: the chain validates only the keys
    # it knows, so an unknown key is restored as it is and nothing reads it.
    unknown = {"version": 7, "anything": [1, 2]}
    store.set_setting("study_review_filter_v9", unknown)
    store.import_backup(store.export_backup())
    assert store.get_setting("study_review_filter_v9") == unknown


def test_a_learner_data_reset_keeps_the_key(store):
    from kanamemory.learning_store import LearningStore
    LearningStore(store)
    preferences = ArrangementPreferences(store)
    preferences.set_review_filter(ReviewFilterSetting(True, (ROUTE,)))
    result = store.clear_learning_data()
    assert REVIEW_FILTER_KEY not in result["deleted_settings"]
    assert preferences.review_filter() == ReviewFilterSetting(True, (ROUTE,))


def test_the_feedback_settings_snapshot_carries_the_key():
    from kanamemory import feedback_context
    from support.feedback_context_support import fake_app
    app = fake_app(page="home")
    app.store.settings[REVIEW_FILTER_KEY] = value(True, [{"kind": "route", "id": "route:one"}])
    result = feedback_context.capture_feedback_context(app)
    assert result["settings"][REVIEW_FILTER_KEY] == value(True, [{"kind": "route", "id": "route:one"}])


# --- the consolidation deck and the parts of the page 加练, over doubles --------------------------------------------

def test_the_consolidation_deck_leaves_out_what_the_filter_does_not_allow():
    from kanamemory.arrangement.model import ConsolidationFacts, StateRef
    from kanamemory.arrangement.services.consolidation import Consolidation
    taught = NOW - timedelta(hours=1)
    facts = [ConsolidationFacts(StateRef.course(f"lesson:{name}", "understanding"), taught, taught,
                                NOW + timedelta(days=2), last_exposure=taught) for name in ("a", "b", "c", "d")]
    serve = [serve_filter(excluded={"lesson:b"})]
    service = Consolidation(lambda now: facts, lambda items: items, serve=lambda now: serve[0])
    ready = {item.ref.content_id for item in service.ready(NOW)}
    assert ready == {"lesson:a", "lesson:c", "lesson:d"}
    assert {item.ref.content_id for item in service.due_deck(NOW)} == {"lesson:a", "lesson:c", "lesson:d"}
    serve[0] = serve_filter(only={"lesson:a"}, part={"lesson:a", "lesson:c"})
    assert {item.ref.content_id for item in service.due_deck(NOW, nothing_else=True)} == {"lesson:a"}
    serve[0] = None
    assert len(service.due_deck(NOW)) == 4


def test_the_parts_count_what_can_be_practised_now_inside_each_part():
    from kanamemory.arrangement.model import ExtraEntry, StateRef, StateView
    from kanamemory.arrangement.services.extra_parts import ExtraParts, PartCount

    def entry(ref, *, due=NOW + timedelta(days=3), in_step=False, rated=NOW - timedelta(hours=2)):
        return ExtraEntry(ref, StateView(ref.key, due, rated, in_step=in_step), "card:1" if ref.script else "")
    pool = [entry(StateRef.course("lesson:today", "understanding")),
            entry(StateRef.course("lesson:today", "usage"), in_step=True),           # inside its steps: not counted
            entry(StateRef.course("lesson:week", "understanding")),
            entry(StateRef.course("lesson:old", "understanding"), due=NOW - timedelta(hours=1)),   # due: not counted
            entry(StateRef.kana("kana:a", "kana_choice", "hiragana"))]
    firsts = [("lesson:today", NOW - timedelta(hours=3)), ("lesson:week", NOW - timedelta(days=5)),
              ("lesson:old", NOW - timedelta(days=40))]
    scope = [frozenset({KANA_A, "lesson:week"})]
    parts = ExtraParts(lambda now: pool, lambda: firsts, study_day=lambda at: at.date().isoformat(),
                       scope=lambda: scope[0])
    assert parts.counts(NOW) == (PartCount(Part.TODAY, 1), PartCount(Part.RECENT, 2), PartCount(Part.SCOPE, 2),
                                 PartCount(Part.ALL, 3))
    assert parts.members(Part.TODAY, NOW) == {"lesson:today"}
    assert parts.members(Part.ALL, NOW) is None
    scope[0] = None
    assert [item.part for item in parts.counts(NOW)] == [Part.TODAY, Part.RECENT, Part.ALL]


# --- the two owners under the filter: a lesson that is only read is new content like any other ----------------

def test_a_paused_read_only_lesson_is_neither_offered_nor_counted(learning_app):
    from kanamemory.learning_questions import PREFERENCE_VERSION, SOUND_PREFERENCE_VERSION
    from kanamemory.learning_ui import LEARNING_PREFS
    app = learning_app
    kinds = ["grammar_meaning"]
    app.store.set_setting(LEARNING_PREFS, {"enabled": kinds, SOUND_PREFERENCE_VERSION: PREFERENCE_VERSION})

    def item(cid, abilities):
        return {"id": cid, "content_id": cid, "family": "grammar", "title": cid, "revision": "1",
                "abilities": list(abilities), "source_ids": ["test"], "depends_on": [],
                "instruction": {"summary": "要点", "rule": "规则。", "steps": [], "example": "本です。",
                                "translation": "是书。", "prerequisite": "", "contrast": ""},
                "question_templates": {"grammar_meaning": [{"prompt": "本です。", "answer": "是书", "feedback": "说明",
                                                            "choices": ["是书", "不是书"], "input_mode": "choice",
                                                            "safe": True}]} if abilities else {}}
    read, other = item("pronunciation:synthetic:paused-read", ()), item("grammar:synthetic:open", ("understanding",))
    read["domain"] = "pronunciation"
    app.learning_store.enroll([read, other])
    app._queue_learning_questions(app.learning_store.get_item(other["id"]))
    learning = app.learning_store
    assert learning.current(kinds, fair_new=True)["content"]["id"] == read["id"]   # first in route order
    before = learning.counts(kinds)["instruction"]
    with serving(app.store, serve_filter(excluded={read["id"]})):
        head = learning.current(kinds, fair_new=True)
        assert head["content"]["id"] == other["id"] and not head.get("read_only")
        assert learning.counts(kinds)["instruction"] == before - 1
    with serving(app.store, serve_filter(only={read["id"]})):
        assert learning.current(kinds, fair_new=True)["content"]["id"] == read["id"]


# --- section 22 (brief J11 F3): work the learner started by hand -----------------------------------------------

def test_work_started_by_hand_is_served_as_tiers_zero_to_two_until_its_first_rating():
    serve = serve_filter(excluded={"lesson:x"}, only={"lesson:a"}, started={("lesson:x", "use"), ("lesson:r", None)})
    for tier in (3, 4, None):
        assert serve.serves("lesson:x", tier, "use")                 # paused, but started by hand: served
        assert not serve.serves("lesson:x", tier, "understanding")   # its other ability is rated already
        assert not serve.serves("lesson:x", tier)
        assert serve.serves("lesson:r", tier) and serve.serves("lesson:r", tier, "use")   # a lesson as a whole
        assert not serve.serves("lesson:c", tier, "use")
    assert serves(serve, "lesson:x", 3, "use") and serves(None, "lesson:c", 3, "use")
    # allows() (the deck, extra practice's pool, the apart counts) is not changed by it.
    assert not serve.allows("lesson:x") and serve.started_work("lesson:x", "use")
    assert not serve.started_work(None, None)
    # Started work alone restricts nothing.
    assert serve_filter(started={("lesson:x", "use")}) is None


def test_the_service_asks_for_started_work_only_when_a_filter_restricts():
    store = Settings()
    preferences = ArrangementPreferences(store)
    preferences.set_focus(Focus(scope=SCOPE))
    asked = []

    def started():
        asked.append(1)
        return {("lesson:b", "use")}
    filters = ReviewFilterService(preferences, FocusService(preferences, Catalog({SCOPE: {"lesson:a"}})),
                                  study_day=lambda at: at.date().isoformat(), started=started)
    assert filters.current(NOW) is None and asked == []
    filters.set_only(True)
    serve = filters.current(NOW)
    assert serve.started == {("lesson:b", "use")} and asked == [1]
    assert serve.serves("lesson:b", 3, "use") and not serve.serves("lesson:b", 3, "understanding")


def test_the_home_entry_asks_the_pool_for_its_first_state_and_leaves_the_counts_to_the_page():
    """Brief J11 F5: ExtraSession.learned uses the reader's any_learned (no full read); a reader without it is
    read in full as before; a fresh read of the 继续加练 button is reused."""
    calls = []

    class Pool:
        def __init__(self, found):
            self.found = found

        def read(self, now, **kwargs):
            calls.append("read")
            return ["state"] if self.found else []

        def any_learned(self, now):
            calls.append("any")
            return self.found

    class OldPool:
        def read(self, now, **kwargs):
            calls.append("read")
            return ["state"]

    def session(pool):
        return ExtraSession(pool, new_sitting=lambda: None, new_deck=lambda items, sitting: None)
    assert session(Pool(True)).learned(NOW) is True and calls == ["any"]
    del calls[:]
    assert session(Pool(False)).learned(NOW) is False and calls == ["any"]
    del calls[:]
    assert session(OldPool()).learned(NOW) is True and calls == ["read"]
    del calls[:]
    reused = session(Pool(False))
    reused.available(NOW)                  # the button's full read ...
    assert reused.learned(NOW + timedelta(seconds=10)) is False and calls == ["read"]   # ... is reused
