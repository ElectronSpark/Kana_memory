"""Design 08 section 16.5 B: candidate lists outside 8.2's first pass read the same availability view.

Orphans come from support/library_availability_support.py (a disabled
synthetic extension library, or an id dropped from the effective catalogue).
Each list keeps its rows when every library is enabled; no SQL is added.
"""
from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace

import pytest

from kanamemory import learning_practice_policy
from kanamemory.course_planning import iter_question_plans
from support.library_availability_support import (
    CORE_ID, EXT_ID, GONE_ID, KINDS, START, course, learner, question)  # noqa: F401

READY = START + timedelta(seconds=61)
ORPHANS = pytest.mark.parametrize("how", ["registry", "catalogue"])


def _target(how):
    return EXT_ID if how == "registry" else GONE_ID


def _orphan(learner, how):
    if how == "registry":
        learner.disable()
    else:
        learner.drop(GONE_ID)


def _scored(learner, cid):
    """Enroll, teach, answer and grade one question: an assessed, unsuspended unit."""
    qid = learner.due_course(cid)
    learner.learning.submit(qid, "正在读书", True, now=READY)
    learner.learning.finalize(qid, 3, now=READY)


@ORPHANS
def test_study_queries_exclude_drops_only_unavailable_rows(learner, how):
    for cid in (CORE_ID, _target(how)):
        _scored(learner, cid)
    queries = learner.store.queries
    before = {name: [r["content_id"] for r in getattr(queries, name)()]
              for name in ("active_review_units", "assessed_course_candidates", "diagnostic_scope_rows")}
    assert all(set(ids) == {CORE_ID, _target(how)} for ids in before.values())
    _orphan(learner, how)
    unavailable = learner.learning.availability().unavailable_ids
    assert unavailable == {_target(how)}
    for name in before:
        assert [r["content_id"] for r in getattr(queries, name)(exclude=unavailable)] == [CORE_ID]
        assert [r["content_id"] for r in getattr(queries, name)()] == before[name]  # default unchanged


@ORPHANS
def test_admission_course_reviews_never_plan_orphans(learner, how, monkeypatch):
    from kanamemory import admission_workload, course_planning
    from kanamemory.admission.assembly import StoreAdmission
    for cid in (CORE_ID, _target(how)):
        _scored(learner, cid)
    planned = []

    def spy(item, **kwargs):
        planned.append(item["id"])
        return iter_question_plans(item, **kwargs)
    monkeypatch.setattr(course_planning, "iter_question_plans", spy)
    workload = StoreAdmission.for_store(learner.store, learner.learning).workload
    result = dict(ready_unit_count=0, waiting_unit_count=0)
    admission_workload._course_reviews(workload, set(KINDS), READY, result)
    assert set(planned) == {CORE_ID, _target(how)}
    _orphan(learner, how)
    planned.clear()
    admission_workload._course_reviews(workload, set(KINDS), READY, result,
                                       learner.learning.availability().unavailable_ids)
    assert planned == [CORE_ID]


def test_iter_question_plans_returns_nothing_for_unavailable_ids(learner):
    learner.learning.enroll(course(EXT_ID), now=START)
    item = learner.learning.get_item(EXT_ID)
    kwargs = dict(enabled_kinds=KINDS, queries=learner.store.queries, learning_store=learner.learning,
                  system_japanese_input=False)
    assert list(iter_question_plans(item, unavailable_ids={EXT_ID}, **kwargs)) == []


@ORPHANS
def test_practice_candidates_yield_nothing_for_unavailable_content(learner, how, monkeypatch):
    monkeypatch.setattr(learning_practice_policy, "_candidate_questions",
                        lambda item, ability, kinds, state: iter([{"kind": "grammar_meaning", "variant": 0}]))
    learner.learning.enroll([course(CORE_ID), course(_target(how))], now=START)
    _orphan(learner, how)
    view = learner.learning.availability()
    for cid, expected in ((CORE_ID, 1), (_target(how), 0)):
        item = learner.learning.get_item(cid)
        assert len(list(learning_practice_policy.practice_candidates(item, KINDS, availability=view))) == expected
        assert len(list(learning_practice_policy.practice_candidates(item, KINDS))) == 1  # no view: unchanged
    # Content without progress is judged too (a new check target of a disabled library).
    if how == "registry":
        assert not view.available(EXT_ID, {})


@ORPHANS
def test_ai_learning_scope_items_exclude_orphans(learner, how):
    from kanamemory.ai_questions import learning_scope_items
    for cid in (CORE_ID, _target(how)):
        _scored(learner, cid)
    assert {item["id"] for item in learning_scope_items(learner.learning)} == {CORE_ID, _target(how)}
    _orphan(learner, how)
    assert [item["id"] for item in learning_scope_items(learner.learning)] == [CORE_ID]


@ORPHANS
def test_diagnostic_scope_items_exclude_orphans(learner, how):
    from kanamemory.retention_session_ui import DiagnosticUIMixin
    for cid in (CORE_ID, _target(how)):
        learner.learning.enroll(course(cid), now=START)
    ui = SimpleNamespace(store=learner.store, learning_store=learner.learning)
    assert {row["content_id"] for row in DiagnosticUIMixin._diagnostic_scope_items(ui)} == {CORE_ID, _target(how)}
    _orphan(learner, how)
    assert [row["content_id"] for row in DiagnosticUIMixin._diagnostic_scope_items(ui)] == [CORE_ID]


@ORPHANS
def test_diagnostic_candidates_pass_the_store_view(learner, how, monkeypatch):
    from kanamemory import retention_sources
    from kanamemory.retention_session import DiagnosticStore
    learner.learning.enroll([course(CORE_ID), course(_target(how))], now=START)
    _orphan(learner, how)
    seen = []

    def candidates(item, kinds, **kwargs):
        seen.append((item["id"], kwargs["availability"].available(item["id"])))
        return iter(())
    monkeypatch.setattr(learning_practice_policy, "practice_candidates", candidates)
    monkeypatch.setattr(retention_sources, "current_item",
                        lambda store, cid, repo=None: learner.learning.get_item(cid))
    store = DiagnosticStore(learner.store, learner.learning)
    for cid in (CORE_ID, _target(how)):
        assert store._candidates(cid, KINDS) == []
    assert seen == [(CORE_ID, True), (_target(how), False)]


@ORPHANS
def test_retention_check_refuses_orphan_targets(learner, how, monkeypatch):
    from kanamemory import learning_content
    from kanamemory.retention_check import RetentionCheckStore
    learner.learning.enroll(course(_target(how)), now=START)
    _orphan(learner, how)
    monkeypatch.setattr(learning_content, "get_learning_item", lambda cid: course(cid))
    # The check passes its admission rule (design 11 D4: no relaxed candidates) as admit=.
    monkeypatch.setattr(learning_practice_policy, "_candidate_questions",
                        lambda item, ability, kinds, state, **kwargs: iter([{"kind": "grammar_meaning", "variant": 0}]))
    with pytest.raises(ValueError, match="没有已启用的可靠检查题"):
        RetentionCheckStore(learner.store).start([_target(how)], KINDS, "req-1")


@dataclass
class _Question:
    kind: str
    content_id: str
    prompt: str = "あ"


def test_native_routes_skip_heads_of_unavailable_content(monkeypatch):
    from kanamemory import practice_availability
    from kanamemory.tutor_native_routes import native_route_candidates
    monkeypatch.setattr(practice_availability, "summarize_queue_availability",
                        lambda store, head, now=None: SimpleNamespace(ready_count=1))
    pending = {"question_index": 0, "questions": [_Question("kana_choice", "kana-3042")], "session_id": "s",
               "state": {}, "assisted_indices": []}
    reads = []

    def store(unavailable):
        def ids():
            reads.append(1)
            return frozenset(unavailable)
        return SimpleNamespace(load_session_progress=lambda lane, lazy_questions: pending if lane == "main" else None,
                               library_unavailable_ids=ids)
    routes, _ = native_route_candidates(store(()), lambda p: ["kana_choice"], audio_ready=True, handwriting_ready=True)
    assert [r["target_id"] for r in routes] == ["kana-3042"]
    routes, _ = native_route_candidates(store({"kana-3042"}), lambda p: ["kana_choice"], audio_ready=True,
                                        handwriting_ready=True)
    assert routes == [] and len(reads) == 2  # read once per call, only when a head exists


def test_tutor_retrieval_passes_the_unavailable_set(learner, monkeypatch):
    from kanamemory.tutor_retrieval import retrieve_question_material
    _scored(learner, CORE_ID)
    _scored(learner, EXT_ID)
    learner.disable()
    calls = []
    original = type(learner.store.queries).tutor_material_candidates

    def spy(self, message, terms, limit, *, exclude=()):
        rows = original(self, message, terms, limit, exclude=exclude)
        calls.append(({r["content_id"] for r in rows}, set(exclude)))
        return rows
    monkeypatch.setattr(type(learner.store.queries), "tutor_material_candidates", spy)
    retrieve_question_material(learner.store, "请解释 " + EXT_ID + " 和 " + CORE_ID)
    assert calls and calls[0][1] == {EXT_ID} and EXT_ID not in calls[0][0]


def test_content_blocked_current_declares_the_optional_reason():
    from kanamemory.learning_dispatch_types import ContentBlockedCurrent
    assert ContentBlockedCurrent.__optional_keys__ == {"reason"}
    assert ContentBlockedCurrent.__required_keys__ == {"type", "question_ids", "counts"}


def test_example_references_mark_library_unavailable_targets(tmp_path):
    from kanamemory import content_library_rules as rules
    from kanamemory.content_availability import Catalogue
    from kanamemory.domain import StudyStore
    from kanamemory.example_store import ExampleStore
    from kanamemory.learning_store import LearningStore
    from support.example_store_support import pack, targets
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        learning = LearningStore(store)
        ids = {target["id"] for target in targets()}
        store.content_catalogue = lambda: Catalogue(ids=frozenset(ids))
        store.set_setting(rules.REGISTRY_SETTING, rules.default_registry())
        value = pack()
        resources = ExampleStore(store, learning, targets(), value)
        learning.enroll(targets()[1], now=START)  # grammar:polite
        eid = value["examples"][0]["id"]
        with resources.read_operation():
            refs = {ref["target_id"]: ref for ref in resources.references(eid)}
        assert not any(ref.get("library_unavailable") for ref in refs.values())
        assert not refs["grammar:polite"]["target"].get("unavailable")
        ids.discard("grammar:polite")  # its library no longer serves it
        with resources.read_operation():
            refs = {ref["target_id"]: ref for ref in resources.references(eid)}
            assert resources.references(eid)[0]["target_id"] in refs  # cached view inside one read
        assert refs["grammar:polite"]["library_unavailable"] and refs["grammar:polite"]["target"]["unavailable"]
        assert not refs["grammar:topic"].get("library_unavailable")  # never enrolled: no learned row to judge
