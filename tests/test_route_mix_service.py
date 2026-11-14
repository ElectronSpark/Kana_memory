"""MainRouteMix, its plan file and the StudyPathService hand-over (design 11 section 16, M10); synthetic data."""
import asyncio
from contextlib import contextmanager
from copy import deepcopy
import json
import os
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from kanamemory import content_pack
from kanamemory import learning_questions as questions
from kanamemory import study_path_catalog
from kanamemory.learning_store import LearningStore
from kanamemory.material_review import item_fingerprint
from kanamemory.route_mix import service as service_module
from kanamemory.route_mix import sources
from kanamemory.route_mix.placement import MixCancelled
from kanamemory.route_mix.plan import MixPlan, plan_mix, snapshot
from kanamemory.route_mix.params import ALGORITHM_VERSION, MixParams
from kanamemory.route_mix.readiness import Readiness, choice_key
from kanamemory.route_mix.service import REFUSED, MainRouteMix, main_route_mix, prepare_in_background
from kanamemory.route_mix.store import DIRECTORY, FILE_NAME, MAX_BYTES, TEMPORARY_NAME, PlanFile
from kanamemory.study_path import StudyPathService
from kanamemory.study_path_runtime import StudyPathRuntimeMixin

from support.study_path_service_support import BASE, MORE, lesson, setup_path  # noqa: F401 - fixture


def word_entry(cid, depends=()):
    family = "word" if cid.startswith("word:") else "grammar"
    return {"id": cid, "content_id": cid, "kind": "learning", "family": family, "domain": None,
            "title": cid, "depends_on": list(depends)}


def small_tree():
    """One stage: grammar g1, g2 and then two word sections, each needing one grammar item."""
    def section(name, unit, entries):
        return {"id": "section:" + name, "title": name, "entries": entries, "depends_on": [], "optional": False,
                "stage_id": "stage:one", "stage_title": "one", "unit_id": unit, "unit_title": unit}
    units = [{"id": "unit:g", "title": "unit:g", "sections": [section("g1", "unit:g", [word_entry("grammar:g1")]),
                                                              section("g2", "unit:g", [word_entry("grammar:g2")])]},
             {"id": "unit:w", "title": "unit:w", "sections": [section("w1", "unit:w", [word_entry("word:w1")]),
                                                              section("w2", "unit:w", [word_entry("word:w2")])]}]
    return {"version": "fixture", "stages": [{"id": "stage:one", "title": "one", "optional": False, "units": units}]}


TABLE = {"word:w1": {"use": [("1" * 64, ["grammar:g1"])]}, "word:w2": {"use": [("2" * 64, ["grammar:g2"])]}}


def section_ids(tree):
    return [s["id"] for st in tree["stages"] for u in st["units"] for s in u["sections"]]


def mix_at(path, identity=("core", "x")):
    return MainRouteMix(path, identity=lambda: None if identity is None else {"core": list(identity)},
                        readiness=lambda ids, items: Readiness(TABLE))


# --- the plan file -------------------------------------------------------------------------------

def test_plan_file_round_trip_is_atomic_and_bounded(tmp_path):
    store = PlanFile(tmp_path)
    assert store.path == tmp_path / DIRECTORY / FILE_NAME
    assert store.temporary == store.path.with_name(TEMPORARY_NAME)
    assert store.load() is None
    store.save({"a": 1, "b": "词"})
    assert store.load() == {"a": 1, "b": "词"}
    assert sorted(p.name for p in store.path.parent.iterdir()) == [FILE_NAME]  # no temporary file left
    store.temporary.write_bytes(b"left by an interrupted save")  # one fixed name: replaced, then renamed
    store.save({"c": 2})
    assert store.load() == {"c": 2} and sorted(p.name for p in store.path.parent.iterdir()) == [FILE_NAME]


def test_a_link_at_the_temporary_name_is_never_written_through(tmp_path):
    # N5: the temporary file is removed and created exclusively, so a link there changes nothing else
    store = PlanFile(tmp_path)
    store.path.parent.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"not the plan")
    try:
        os.link(outside, store.temporary)
    except OSError:
        pytest.skip("hard links are not available here")
    store.save({"a": 1})
    assert outside.read_bytes() == b"not the plan" and store.load() == {"a": 1}
    assert not store.temporary.exists()
    target = tmp_path / "target.txt"
    target.write_bytes(b"not the plan either")
    try:
        store.temporary.symlink_to(target)
    except OSError:
        return  # symbolic links need a privilege on Windows: the hard link above is the case
    store.save({"b": 2})
    assert target.read_bytes() == b"not the plan either" and store.load() == {"b": 2}
    for raw in (b"{not json", b"[1, 2]", b"\xff\xfe", b"null"):
        store.path.write_bytes(raw)
        assert store.load() is None
    store.path.write_bytes(b" " * (MAX_BYTES + 1))
    assert store.load() is None
    with pytest.raises(ValueError):
        store.save({"big": "x" * MAX_BYTES})
    assert store.path.read_bytes() == b" " * (MAX_BYTES + 1)  # a refused save leaves the old file


# --- MainRouteMix ----------------------------------------------------------------------------------

def test_a_saved_plan_applies_only_to_the_same_tree_and_content(tmp_path):
    tree = small_tree()
    mix = mix_at(tmp_path)
    assert mix.mixed(tree) is None and mix.generation == 0
    job = mix.job(tree, [], None)
    assert job() is True and mix.generation == 1
    stored = json.loads(PlanFile(tmp_path).path.read_text("utf-8"))
    assert MixPlan.from_json(stored).key["content"] == {"core": ["core", "x"]}
    mixed = mix.mixed(tree)
    assert section_ids(mixed) == ["section:g1", "section:w1", "section:g2", "section:w2"]
    assert mix.mixed(tree) is mixed  # the same tree object: the same mixed tree
    assert section_ids(tree) == ["section:g1", "section:g2", "section:w1", "section:w2"]
    # a new process: the file is read once and applied to an equal tree
    fresh = mix_at(tmp_path)
    assert fresh.mixed(deepcopy(tree)) == mixed
    assert fresh.job(tree, [], None)() is False and fresh.generation == 0  # the stored plan already fits
    # another content identity or another tree: no plan applies
    assert mix_at(tmp_path, ("core", "other")).mixed(tree) is None
    changed = deepcopy(tree)
    changed["stages"][0]["units"][1]["sections"][0]["entries"][0]["depends_on"] = ["grammar:g1"]
    assert mix_at(tmp_path).mixed(changed) is None


def test_the_job_carries_the_overridden_core_ids_to_the_plan(tmp_path):
    def library_of(content_id):  # route_library_of's shape: core for M8, but one id is overridden
        return None
    library_of.overridden = lambda content_id: content_id == "word:w1"
    job = mix_at(tmp_path).job(small_tree(), [], library_of)
    assert job.args[2] == {} and job.args[3] == frozenset({"word:w1"})
    assert mix_at(tmp_path / "plain").job(small_tree(), [], None).args[3] == frozenset()


def test_without_content_identity_no_plan_file_is_read_or_written(tmp_path):
    calls = []
    plan_file = SimpleNamespace(present=lambda: True, load=lambda: calls.append("load"),
                                save=lambda value: calls.append("save"))
    mix = MainRouteMix(tmp_path, identity=lambda: None, readiness=lambda ids, items: Readiness(TABLE),
                       plan_file=plan_file)
    assert mix.mixed(small_tree()) is None
    assert mix.job(small_tree(), [], None) is None
    assert mix.prepare(small_tree(), {}, {}) is False
    assert calls == []
    # no file at all: not even the content identity is read
    asked = []
    absent = MainRouteMix(tmp_path, identity=lambda: asked.append(1), plan_file=SimpleNamespace(present=lambda: False))
    assert absent.mixed(small_tree()) is None and asked == []


def test_a_corrupt_plan_file_is_ignored_and_replaced(tmp_path):
    tree = small_tree()
    path = PlanFile(tmp_path).path
    path.parent.mkdir(parents=True)
    path.write_text('{"format": "kanamemory-main-route-mix", "version": 1, "key": {}, "stages": [7]}', "utf-8")
    mix = mix_at(tmp_path)
    assert mix.mixed(tree) is None
    assert mix.prepare(tree, {}, {}) is True and mix.generation == 1
    assert MixPlan.from_json(json.loads(path.read_text("utf-8"))).stages
    assert mix.mixed(tree) is not None


MIXED = ["section:g1", "section:w1", "section:g2", "section:w2"]


def test_a_stored_plan_that_does_not_apply_is_computed_again(tmp_path):
    # Item 3: the key fits but a section id is damaged: not current, so prepare computes it again
    tree = small_tree()
    assert mix_at(tmp_path).job(tree, [], None)() is True
    path = PlanFile(tmp_path).path
    value = json.loads(path.read_text("utf-8"))
    value["stages"][0]["sections"][0][0] = "section:damaged"
    path.write_text(json.dumps(value), "utf-8")
    fresh = mix_at(tmp_path)
    assert fresh.mixed(tree) is None
    assert fresh.prepare(snapshot(tree), {}, {}) is True and fresh.generation == 1
    assert section_ids(fresh.mixed(tree)) == MIXED
    stored = MixPlan.from_json(json.loads(path.read_text("utf-8")))
    assert "section:damaged" not in {sid for _, rows in stored.stages for sid, _ in rows}


def test_a_computed_plan_that_does_not_apply_is_not_saved(tmp_path, monkeypatch):
    real = service_module.plan_mix

    def damaged(*args, **kwargs):
        plan = real(*args, **kwargs)
        stage_id, rows = plan.stages[0]
        return MixPlan(plan.key, ((stage_id, (("section:damaged", rows[0][1]), *rows[1:])),), plan.summary)
    monkeypatch.setattr(service_module, "plan_mix", damaged)
    mix = mix_at(tmp_path)
    assert mix.prepare(snapshot(small_tree()), {}, {}) is False
    assert mix.last_error == REFUSED and mix.generation == 0 and mix.stored() is None
    assert not PlanFile(tmp_path).path.exists()


def test_a_plan_saved_between_the_two_reads_is_used_by_the_next_read(tmp_path):
    # Item 4: the worker saves a plan while mixed() is between reading the plan and its key
    tree = small_tree()
    assert mix_at(tmp_path, ("core", "old")).job(tree, [], None)() is True  # a stale plan in the file
    mix = mix_at(tmp_path)
    key_for, calls = mix.key_for, []

    def racing(value):
        if not calls:
            calls.append(1)
            assert mix.prepare(snapshot(tree), {}, {}) is True  # the worker thread, meanwhile
        return key_for(value)
    mix.key_for = racing
    assert mix.mixed(tree) is None and mix.generation == 1  # read with the plan it saw: the stale one
    assert section_ids(mix.mixed(tree)) == MIXED            # cached for the old generation only


def test_a_cancelled_computation_saves_nothing(tmp_path):
    # Item 8: app exit while the worker reads; nothing is saved, no new job starts
    tree, holder = small_tree(), {}

    def readiness(ids, items):
        holder["mix"].cancel()
        return Readiness(TABLE)
    mix = MainRouteMix(tmp_path, identity=lambda: {"core": ["x"]}, readiness=readiness)
    holder["mix"] = mix
    job = mix.job(tree, [], None)
    assert job() is False and mix.cancelled and mix.generation == 0 and mix.stored() is None
    assert not PlanFile(tmp_path).path.exists()
    assert mix.job(tree, [], None) is None
    assert mix.prepare(snapshot(tree), {}, {}) is False
    with pytest.raises(MixCancelled):
        plan_mix(snapshot(tree), Readiness(TABLE), mixable=["stage:one"], library_of={}, cancelled=lambda: True)
    with pytest.raises(MixCancelled):
        sources.read_readiness(["word:w1"], {"word:w1": {"id": "word:w1"}}, load=lambda name, default: default,
                               cancelled=lambda: True)


def test_the_app_exit_cancels_its_mix(setup_path):
    repo, store, _, _, _, _ = setup_path
    app = SimpleNamespace(repo=repo, store=store, learning_store=LearningStore(store))
    StudyPathRuntimeMixin.cancel_main_route_mix(app)  # no service yet: nothing to cancel
    service = StudyPathRuntimeMixin.study_path_service(app)
    assert not service.route_mix.cancelled
    StudyPathRuntimeMixin.cancel_main_route_mix(app)
    assert service.route_mix.cancelled


def test_one_mix_per_data_directory_is_handed_on(tmp_path):
    assert main_route_mix(None, None) is None
    first = main_route_mix(None, tmp_path)
    assert isinstance(first, MainRouteMix)
    assert main_route_mix(SimpleNamespace(route_mix=first), tmp_path) is first
    assert main_route_mix(SimpleNamespace(route_mix=first), tmp_path / "other") is not first
    # the learner's switched-on kinds are part of a new mix's parameters (and so of its plan key); they
    # are read (a callable) only when a new mix is made
    reads = []

    def switches():
        reads.append(1)
        return ["vocab_recall", "vocab_meaning"]
    own = main_route_mix(None, tmp_path, switches)
    assert own.params.kinds == ("vocab_meaning", "vocab_recall") and reads == [1]
    assert main_route_mix(None, tmp_path, {"vocab_recall"}).params.kinds == ("vocab_recall",)
    # switches changed while the app runs: the mix (and the plan in use) stays until the next start-up
    assert main_route_mix(SimpleNamespace(route_mix=own), tmp_path, switches) is own and reads == [1]
    assert main_route_mix(SimpleNamespace(route_mix=own), tmp_path, ["vocab_usage"]) is own


def test_the_plan_is_keyed_by_the_switched_on_kinds_and_the_algorithm(tmp_path):
    tree, computed = small_tree(), []

    def mix(kinds):
        def readiness(ids, items):
            computed.append(kinds)
            return Readiness(TABLE)
        return MainRouteMix(tmp_path, params=MixParams(kinds=kinds), identity=lambda: {"core": ["x"]},
                            readiness=readiness)
    default = ("vocab_meaning", "vocab_recall")
    assert mix(default).job(tree, [], None)() is True and computed == [default]
    stored = MixPlan.from_json(json.loads(PlanFile(tmp_path).path.read_text("utf-8")))
    assert stored.key["params"]["kinds"] == list(default) and stored.key["algorithm"] == ALGORITHM_VERSION
    # the same switches in a new process: the stored plan (read from the file) is used, nothing computed
    same = mix(default)
    assert same.mixed(tree) is not None and same.job(tree, [], None)() is False and len(computed) == 1
    # other switches: the stored plan of the old ones stays in use (never the derivation order in between)
    # while the warm-up computes the plan of the new ones and replaces it (one file)
    other = mix(("vocab_meaning",))
    kept = other.mixed(tree)
    assert kept is not None and section_ids(kept) == section_ids(same.mixed(tree)) and other.generation == 0
    assert same.current_for(tree) and not other.current_for(tree)  # in use, but not this learner's plan yet
    assert other.job(tree, [], None)() is True and computed[-1] == ("vocab_meaning",) and other.generation == 1
    assert other.current_for(tree)
    assert other.mixed(tree) is not None and sorted(p.name for p in PlanFile(tmp_path).path.parent.iterdir()) == [
        FILE_NAME]
    stored = MixPlan.from_json(json.loads(PlanFile(tmp_path).path.read_text("utf-8")))
    assert stored.key["params"]["kinds"] == ["vocab_meaning"]  # the file now holds the plan of the new switches
    back = mix(default)  # and the old switches would go on with it until their own plan is computed again
    assert back.mixed(tree) is not None and back.job(tree, [], None)() is True and computed[-1] == default
    # only the switches may differ: another algorithm version, tree or content gives the derivation order
    newer = MainRouteMix(tmp_path, params=MixParams(kinds=default, algorithm=ALGORITHM_VERSION + 1),
                         identity=lambda: {"core": ["x"]}, readiness=lambda ids, items: Readiness(TABLE))
    assert newer.mixed(tree) is None
    changed = MainRouteMix(tmp_path, params=MixParams(kinds=default), identity=lambda: {"core": ["y"]},
                           readiness=lambda ids, items: Readiness(TABLE))
    assert changed.mixed(tree) is None
    assert newer.prepare(snapshot(tree), {}, {}) is True


def test_prepare_in_background_runs_the_job_on_a_worker_thread(tmp_path):
    import threading
    threads = []

    def job():
        threads.append(threading.current_thread())
        return True
    mix = mix_at(tmp_path)
    assert asyncio.run(prepare_in_background(SimpleNamespace(route_mix=mix, route_mix_job=lambda: job))) is True
    assert threads and threads[0] is not threading.main_thread()
    assert asyncio.run(prepare_in_background(SimpleNamespace(route_mix=mix, route_mix_job=lambda: None))) is False
    assert asyncio.run(prepare_in_background(SimpleNamespace())) is False

    def broken():
        raise RuntimeError("no plan")
    assert asyncio.run(prepare_in_background(SimpleNamespace(route_mix=mix, route_mix_job=lambda: broken))) is False
    assert mix.last_error == "RuntimeError"
    mix.last_error = None

    def unmade():  # making the job (identity read, snapshot) fails: the same, on the calling thread
        raise ValueError("no identity")
    assert asyncio.run(prepare_in_background(SimpleNamespace(route_mix=mix, route_mix_job=unmade))) is False
    assert mix.last_error == "ValueError"
    # N2: the service may come from a callable, called inside the same try
    assert asyncio.run(prepare_in_background(lambda: SimpleNamespace(route_mix=mix, route_mix_job=lambda: job))) is True

    def no_service():
        raise RuntimeError("the store is being replaced")
    assert asyncio.run(prepare_in_background(no_service)) is False


# --- StudyPathService with a mix -----------------------------------------------------------------

class FakeMix:
    """mixed() reverses the first unit's sections; records the trees it was given. ``current``: whether the
    plan it applies is the one of the learner's present switches (MainRouteMix.current_for)."""
    generation = 0

    def __init__(self, applies=True, current=True):
        self.seen, self.applies, self.current = [], applies, current

    def current_for(self, tree):
        return self.applies and self.current

    def mixed(self, tree, library_of=None):
        self.seen.append(tree)
        if not self.applies:
            return None
        result = deepcopy(tree)
        result["stages"][0]["units"][0]["sections"].reverse()
        return result

    def job(self, tree, items, library_of):
        return lambda: ("job", tree, len(items), library_of)


def test_study_path_service_uses_the_mixed_tree_and_hands_over_the_derivation_tree(setup_path):
    repo, store, _, _, make, _ = setup_path
    catalogue = [lesson(BASE), lesson(MORE, [BASE])]
    plain = make()
    assert plain.route_mix is None and plain.path is plain.derivation_path  # no mix: as before
    assert plain.route_mix_job() is None
    fake = FakeMix()
    service = StudyPathService(repo, store, LearningStore(store), catalogue=catalogue, route_mix=fake)
    assert fake.seen == [service.derivation_path] and service.derivation_path == plain.derivation_path
    assert service.path is not service.derivation_path
    assert {s["id"] for s in service.sections} == {s["id"] for s in plain.sections}
    assert service.entries.keys() == plain.entries.keys()
    assert [s["id"] for s in service.sections] == [s["id"] for s in service.path["stages"][0]["units"][0]["sections"]]
    assert service.overview()["stages"][0]["units"][0]["sections"][0]["id"] == service.sections[0]["id"]
    assert service.route_mix_job() is None  # the current plan is applied: nothing to compute
    # a plan of other switches is applied as well, but the job is still made (for the derivation tree)
    stale = StudyPathService(repo, store, LearningStore(store), catalogue=catalogue, route_mix=FakeMix(current=False))
    assert stale.path is not stale.derivation_path
    marker, tree, _, _ = stale.route_mix_job()()
    assert marker == "job" and tree is stale.derivation_path
    again = StudyPathService(repo, store, LearningStore(store), catalogue=catalogue, previous=service, route_mix=fake)
    assert again.derivation_path is service.derivation_path  # never a mixed tree as derivation input
    assert fake.seen[-1] is service.derivation_path and again.path is not service.path
    waiting = FakeMix(applies=False)
    pending = StudyPathService(repo, store, LearningStore(store), catalogue=catalogue, previous=again,
                               route_mix=waiting)
    assert pending.path is pending.derivation_path is service.derivation_path
    marker, tree, count, library_of = pending.route_mix_job()()
    assert marker == "job" and tree is pending.derivation_path and count == 2 and library_of is None


def test_the_app_keys_its_service_by_the_mix_generation(setup_path):
    repo, store, _, _, _, _ = setup_path
    app = SimpleNamespace(repo=repo, store=store, learning_store=LearningStore(store))
    first = StudyPathRuntimeMixin.study_path_service(app)
    mix = first.route_mix
    assert isinstance(mix, MainRouteMix) and mix.data_dir == store.data_dir
    # the learner's switched-on kinds, as plain data: no saved setting, the default kinds
    assert mix.params.kinds == tuple(sorted(questions.DEFAULT_KINDS))
    assert first.path is first.derivation_path  # no plan file yet
    assert StudyPathRuntimeMixin.study_path_service(app) is first
    mix.generation += 1  # what a saved plan does
    second = StudyPathRuntimeMixin.study_path_service(app)
    assert second is not first and second.route_mix is mix
    assert second.derivation_path is first.derivation_path
    assert not (store.data_dir / DIRECTORY).exists()  # reading never writes the plan file


def test_the_app_passes_the_learners_switched_on_kinds_and_keeps_them_for_the_run(setup_path):
    repo, store, _, _, _, _ = setup_path
    only = [questions.CONTEXT_CHOICE_KIND]
    store.set_setting("learning_preferences_v1", {"enabled": only,
                                                  questions.SOUND_PREFERENCE_VERSION: questions.PREFERENCE_VERSION})
    app = SimpleNamespace(repo=repo, store=store, learning_store=LearningStore(store))
    first = StudyPathRuntimeMixin.study_path_service(app)
    assert first.route_mix.params.kinds == tuple(only)
    # a switch flipped in this run: the same mix (and its plan key) until the next start-up
    store.set_setting("learning_preferences_v1", {"enabled": ["vocab_meaning"],
                                                  questions.SOUND_PREFERENCE_VERSION: questions.PREFERENCE_VERSION})
    first.route_mix.generation += 1  # a new service is built
    assert StudyPathRuntimeMixin.study_path_service(app).route_mix is first.route_mix
    # the next start-up (no service to hand over): the new switches
    fresh = SimpleNamespace(repo=repo, store=store, learning_store=LearningStore(store))
    assert StudyPathRuntimeMixin.study_path_service(fresh).route_mix.params.kinds == ("vocab_meaning",)


# --- the start-up step, end to end (the real MainRouteMix, a synthetic readiness source) ---------------

W1, W2 = "word:test:route-mix-w1", "word:test:route-mix-w2"


@pytest.fixture
def mix_app(setup_path, monkeypatch):
    """An app whose route is one stage: two grammar lessons, then two words, each needing one of them;
    the content identity and the readiness are synthetic (no course content)."""
    repo, store, _, _, _, _ = setup_path

    def section(name, entries, unit):
        return {"id": "section:" + name, "title": name, "entries": entries, "depends_on": [], "optional": False,
                "stage_id": "stage:test", "stage_title": "测试阶段", "unit_id": unit, "unit_title": unit}

    def learning(cid, family):
        return {"id": cid, "kind": "learning", "content_id": cid, "family": family, "domain": None,
                "depends_on": []}
    path = {"version": "test-v1", "stages": [{"id": "stage:test", "title": "测试阶段", "units": [
        {"id": "unit:g", "title": "unit:g", "sections": [section("g1", [learning(BASE, "grammar")], "unit:g"),
                                                         section("g2", [learning(MORE, "grammar")], "unit:g")]},
        {"id": "unit:w", "title": "unit:w", "sections": [section("w1", [learning(W1, "word")], "unit:w"),
                                                         section("w2", [learning(W2, "word")], "unit:w")]}]}]}
    monkeypatch.setattr(study_path_catalog, "build_study_path", lambda *a, **kw: deepcopy(path))
    app = SimpleNamespace(repo=repo, store=store, learning_store=LearningStore(store), _background_assembly=True,
                          page="home", refreshed=[], read=[])
    app.refresh_home_actions = lambda: app.refreshed.append(app.page)
    app.study_path_service = lambda: StudyPathRuntimeMixin.study_path_service(app)

    def readiness(ids, items, **kwargs):
        app.read.append(threading.current_thread())
        app.kinds = kwargs.get("kinds")
        if getattr(app, "close_while_reading", False):
            app._backup_closing = True
        return Readiness({W1: {"use": [("1" * 64, [BASE])]}, W2: {"use": [("2" * 64, [MORE])]}})
    monkeypatch.setattr(sources, "content_identity", lambda: {"core": ["synthetic"]})
    monkeypatch.setattr(sources, "read_readiness", readiness)
    return app


def test_the_start_up_step_saves_a_plan_and_the_app_then_serves_the_mixed_route(mix_app):
    first = mix_app.study_path_service()
    assert [s["id"] for s in first.sections] == ["section:g1", "section:g2", "section:w1", "section:w2"]
    assert asyncio.run(StudyPathRuntimeMixin.prepare_main_route_mix(mix_app)) is True
    assert mix_app.read and mix_app.read[0] is not threading.main_thread()  # computed on a worker thread
    assert mix_app.kinds == tuple(sorted(questions.DEFAULT_KINDS))          # the learner's switches, as data
    assert mix_app.refreshed == ["home"]                                    # home refreshed once
    assert (mix_app.store.data_dir / DIRECTORY / FILE_NAME).is_file()
    second = mix_app.study_path_service()
    assert second is not first and second.path is not second.derivation_path
    assert [s["id"] for s in second.sections] == MIXED
    # the next start: the plan is current, nothing is computed and home is not refreshed again
    assert asyncio.run(StudyPathRuntimeMixin.prepare_main_route_mix(mix_app)) is False
    assert mix_app.refreshed == ["home"] and len(mix_app.read) == 1


def test_after_a_switch_change_the_next_start_keeps_the_route_mixed_and_computes_the_new_plan(mix_app):
    assert asyncio.run(StudyPathRuntimeMixin.prepare_main_route_mix(mix_app)) is True and len(mix_app.read) == 1
    mix_app.store.set_setting("learning_preferences_v1", {"enabled": ["vocab_meaning"],
                                                         questions.SOUND_PREFERENCE_VERSION: questions.PREFERENCE_VERSION})
    # the next start-up: a new app object over the same data directory
    fresh = SimpleNamespace(repo=mix_app.repo, store=mix_app.store, learning_store=LearningStore(mix_app.store),
                            _background_assembly=True, page="home", refreshed=[])
    fresh.refresh_home_actions = lambda: fresh.refreshed.append(fresh.page)
    fresh.study_path_service = lambda: StudyPathRuntimeMixin.study_path_service(fresh)
    service = fresh.study_path_service()
    mix = service.route_mix
    assert mix.params.kinds == ("vocab_meaning",)
    # the stored plan of the old switches is in use at once: never the derivation order in between ...
    assert [s["id"] for s in service.sections] == MIXED and not mix.current_for(service.derivation_path)
    # ... and the warm-up still computes the plan of the new switches and replaces the stored one
    assert asyncio.run(StudyPathRuntimeMixin.prepare_main_route_mix(fresh)) is True
    assert len(mix_app.read) == 2 and mix_app.kinds == ("vocab_meaning",)
    stored = json.loads((mix_app.store.data_dir / DIRECTORY / FILE_NAME).read_text("utf-8"))
    assert stored["key"]["params"]["kinds"] == ["vocab_meaning"]
    after = fresh.study_path_service()
    assert after is not service and mix.current_for(after.derivation_path)
    assert [s["id"] for s in after.sections] == MIXED
    assert asyncio.run(StudyPathRuntimeMixin.prepare_main_route_mix(fresh)) is False and len(mix_app.read) == 2


def test_the_start_up_step_refreshes_only_home_and_never_while_closing(mix_app):
    mix_app.page = "settings"
    assert asyncio.run(StudyPathRuntimeMixin.prepare_main_route_mix(mix_app)) is True
    assert mix_app.refreshed == []  # saved, but another page is open: nothing refreshed


@pytest.mark.parametrize("state", ["closing", "closing_meanwhile", "backup_busy", "not_started"])
def test_the_start_up_step_never_runs_or_refreshes_while_closing_or_outside_the_started_app(mix_app, state):
    if state == "closing":
        mix_app._backup_closing = True
    elif state == "closing_meanwhile":
        mix_app.close_while_reading = True
    elif state == "backup_busy":
        mix_app._backup_busy = True  # N2: a backup or restore is running
    else:
        del mix_app._background_assembly
    saved = asyncio.run(StudyPathRuntimeMixin.prepare_main_route_mix(mix_app))
    assert mix_app.refreshed == []
    if state == "closing_meanwhile":
        assert saved is True  # computed before exit began; home is left alone
    else:
        assert saved is False and mix_app.read == []
        assert not (mix_app.store.data_dir / DIRECTORY).exists()


def test_the_start_up_step_returns_false_when_the_service_cannot_be_built(mix_app):
    # N2: an error getting the service never escapes the warm-up (on_running)
    def broken():
        raise RuntimeError("the store is being replaced")
    mix_app.study_path_service = broken
    assert asyncio.run(StudyPathRuntimeMixin.prepare_main_route_mix(mix_app)) is False
    assert mix_app.read == [] and mix_app.refreshed == []


# --- the adapters: content identity, readiness, shard reads -----------------------------------------

def test_the_content_identity_names_the_app_build_and_the_accepted_overrides(monkeypatch):
    from kanamemory.build_info import BUILD_NUMBER, VERSION
    base = sources.content_identity()
    if base is None:
        pytest.skip("no readable teaching content pack")
    assert base["app"] == [VERSION, BUILD_NUMBER] and "overrides" in base
    layer = SimpleNamespace(library_id="library.test", damaged=False,
                            manifest={"content_sha256": "a" * 64, "library_version": "1"})
    catalogue = SimpleNamespace(extension_ids=("library.test",), fallback=False,
                                accepted_overrides=frozenset({("library.test", "word:b"), ("library.test", "word:a")}))
    monkeypatch.setattr(content_pack, "library_layers", lambda: [layer])
    monkeypatch.setattr(content_pack, "effective_catalogue", lambda: catalogue)
    identity = sources.content_identity()
    assert identity["libraries"] == [["library.test", "1", "a" * 64]]
    assert identity["overrides"] == [["library.test", "word:a"], ["library.test", "word:b"]]
    catalogue.accepted_overrides = frozenset({("library.test", "word:a")})
    assert sources.content_identity() != identity  # another accepted override: another key


def test_readiness_follows_the_selection_rules(monkeypatch):
    """Item 9: retirement table and cognate rule, oov, background_ids merged, sound kinds and abilities."""
    def template(prompt, **extra):
        return {"prompt": prompt, "answer": "答", **extra}

    def word(cid, templates, abilities=("understanding", "use", "listening")):
        return {"id": cid, "family": "word", "title": "本", "reading": "ほん", "revision": "t1",
                "abilities": list(abilities), "question_templates": templates}
    a, b, c = template("a"), template("b", practice={"background_ids": ["word:bg"]}), template(
        "c", practice={"background_ids": ["grammar:y"]})
    d, e, r = template("d", practice={"background_ids": ["word:q", "word:main"]}), template("e"), template("r")
    sound = template("s")
    main = word("word:main", {"vocab_meaning": [a, b, c, d, e], "vocab_recall": [r], "vocab_audio_word": [sound]})
    narrow = word("word:narrow", {"vocab_meaning": [a], "vocab_recall": [r]}, abilities=("understanding",))
    retired = word("word:retired", {"vocab_meaning": [a, c]})
    bare, sentence = template("本"), template("本を読む", practice={"background_ids": ["word:read"]})
    recall, base_form = template("x", task="写出"), template("y", task="基本形是什么")
    cognate = word("word:cognate", {"vocab_meaning": [bare, sentence], "vocab_recall": [recall, base_form]})
    sha = questions.template_fingerprint
    declared = {
        "word:main": {"vocab_meaning": {sha(a): {"words": ["word:x"]}, sha(b): {"oov": ["未知"], "words": []},
                                        sha(c): {"words": ["word:z", "word:main"]}},
                      "vocab_recall": {sha(r): {}}, "vocab_audio_word": {sha(sound): {}}},
        "word:narrow": {"vocab_meaning": {sha(a): {}}, "vocab_recall": {sha(r): {}}},
        "word:retired": {"vocab_meaning": {sha(a): {}, sha(c): {}}},
        "word:cognate": {"vocab_meaning": {sha(bare): {}, sha(sentence): {}},
                         "vocab_recall": {sha(recall): {}, sha(base_form): {}}}}
    monkeypatch.setattr(questions, "_PRACTICE", {"template_retirements": {
        "word:retired": {item_fingerprint(retired): {"vocab_meaning": [sha(c)]}}}})
    monkeypatch.setattr(questions, "_SOUND_MIX", {"cognate_words": {
        "word:cognate": {"title": "本", "reading": "ほん", "class": "S"}}})
    namespaces = {content_pack.QUESTION_DEPENDENCIES: {"schema_version": 1, "items": declared}}
    items = {item["id"]: item for item in (main, narrow, retired, cognate)}
    readiness = sources.read_readiness(list(items), items, load=lambda name, default: namespaces.get(name, default))
    meaning = tuple(sorted([(sha(a), ("word:x",)),                  # declared
                            (sha(c), ("grammar:y", "word:z")),      # declared + background, not itself
                            (sha(d), ("word:q",))]))                # background only
    # each template under its ability; b: oov, never; e: nothing declared; the sound kind is left out;
    # no switched-on kinds given: no choice keys
    assert readiness.abilities("word:main") == {"understanding": meaning, "use": ((sha(r), ()),)}
    assert set(readiness.abilities("word:narrow")) == {"understanding"}  # its abilities
    retired_rows = ((sha(a), ()),)  # the retirement table
    assert readiness.abilities("word:retired") == {"understanding": retired_rows}
    # the cognate rule (class S): one bare meaning task and the base-form recall task stay
    assert readiness.abilities("word:cognate") == {"understanding": ((sha(bare), ()),),
                                                   "use": ((sha(base_form), ()),)}

    def switched(kinds):
        return sources.read_readiness(["word:main"], items, kinds=kinds,
                                      load=lambda name, default: namespaces.get(name, default))
    # with the learner's switched-on kinds: the templates of those kinds once more, per ability; a kind
    # switched on that this item has no template of (grammar_cloze) adds nothing
    on = switched(("grammar_cloze", "vocab_meaning"))
    assert on.abilities("word:main") == {"understanding": meaning, choice_key("understanding"): meaning,
                                         "use": ((sha(r), ()),)}
    both = switched(("vocab_audio_word", "vocab_meaning", "vocab_recall"))  # a sound kind is never counted
    assert both.abilities("word:main") == {"understanding": meaning, choice_key("understanding"): meaning,
                                           "use": ((sha(r), ()),), choice_key("use"): ((sha(r), ()),)}


class _Connection:
    """A read-only stand-in for one SQLite connection: who opened it, whether it is closed."""

    def __init__(self, log, path):
        self.path, self.thread, self.closed = path, threading.get_ident(), False
        log.append(self)

    def execute(self, sql, params):
        assert not self.closed and threading.get_ident() == self.thread  # never used closed or on another thread
        if params[1] == "broken":
            raise sqlite3.OperationalError("broken")
        return SimpleNamespace(fetchone=lambda: (b"payload " + params[1].encode(),))

    def close(self):
        self.closed = True


@pytest.fixture
def shard_log(monkeypatch):
    opened = []
    monkeypatch.setattr(content_pack, "_connect", lambda path: _Connection(opened, path))
    monkeypatch.setattr(content_pack, "_decode_payload", lambda raw, sha, size: raw)
    return opened


def test_shard_reads_outside_the_context_open_and_close_a_connection_each(shard_log):
    for key in ("k1", "k2", "k3"):
        assert content_pack._read_shard("pack.sqlite3", "ns", key, "sha", 1) == b"payload " + key.encode()
    assert len(shard_log) == 3 and all(connection.closed for connection in shard_log)
    with pytest.raises(content_pack.ContentPackError):  # errors are reported as before
        content_pack._read_shard("pack.sqlite3", "ns", "broken", "sha", 1)
    assert shard_log[-1].closed


def test_shard_reads_inside_the_context_share_one_connection_per_pack_on_this_thread_only(shard_log):
    with content_pack.shard_reads():
        for key in ("k1", "k2", "k3"):
            content_pack._read_shard("a.sqlite3", "ns", key, "sha", 1)
        content_pack._read_shard("b.sqlite3", "ns", "k1", "sha", 1)
        assert [c.path for c in shard_log] == ["a.sqlite3", "b.sqlite3"] and not any(c.closed for c in shard_log)
        other = threading.Thread(target=content_pack._read_shard, args=("a.sqlite3", "ns", "k9", "sha", 1))
        other.start()
        other.join()
        assert len(shard_log) == 3 and shard_log[2].closed and shard_log[2].thread != shard_log[0].thread
        with content_pack.shard_reads():  # nested: the outer context's connections
            content_pack._read_shard("a.sqlite3", "ns", "k4", "sha", 1)
        assert len(shard_log) == 3 and not shard_log[0].closed
        with pytest.raises(content_pack.ContentPackError):
            content_pack._read_shard("a.sqlite3", "ns", "broken", "sha", 1)
    assert all(connection.closed for connection in shard_log)
    content_pack._read_shard("a.sqlite3", "ns", "k1", "sha", 1)  # after it: a connection per read again
    assert len(shard_log) == 4 and shard_log[3].closed
    with pytest.raises(RuntimeError), content_pack.shard_reads():
        content_pack._read_shard("a.sqlite3", "ns", "k1", "sha", 1)
        raise RuntimeError("the worker fails")
    assert all(connection.closed for connection in shard_log)  # closed when the context ends, however


def test_read_readiness_reads_its_shards_inside_one_shard_reads_context(monkeypatch):
    entered = []

    @contextmanager
    def recording():
        entered.append(threading.get_ident())
        yield
    monkeypatch.setattr(content_pack, "shard_reads", recording)
    items = {"word:w1": {"id": "word:w1", "family": "word", "title": "本", "reading": "ほん", "abilities": ["use"],
                         "question_templates": {}}}
    sources.read_readiness(list(items), items, chunk=1, load=lambda name, default: default)
    assert entered == [threading.get_ident()]
