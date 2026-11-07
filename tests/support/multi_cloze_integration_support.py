"""Real stores over isolated original lessons for multi-gap contracts."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest

from kanamemory import learning_content, learning_questions
from kanamemory.domain import StudyStore
from kanamemory.learning_store import LearningStore
from kanamemory.learning_questions import build_learning_question
from support.learning_store_fixtures import START, ready
from support.multi_cloze_support import question as structured_question

CID = "grammar:synthetic:topic"
BACKGROUND = "grammar:synthetic:destination"
KIND = "grammar_cloze"


def lesson():
    q = structured_question()
    raw = {key: deepcopy(q[key]) for key in
           ("prompt", "answer", "feedback", "input_mode", "choices", "multi_cloze")}
    primary = raw["multi_cloze"]["slots"][0]
    raw.update(task="填空", safe=False, practice={"schema_version": 1,
        "task_family": "multi_gap", "context_id": "synthetic:school-trip", "scaffold": "cued",
        "target_spans": [{key: primary[key] for key in ("start", "end", "quote")}],
        "background_ids": [BACKGROUND], "reviewed": True})
    return {"id": CID, "family": "grammar", "revision": "synthetic-v1", "title": "话题は",
        "abilities": ["application"], "depends_on": [],
        "instruction": {"rule": "は标出话题。", "example": "私は学校へ行きます。",
                        "translation": "我去学校。"},
        "question_templates": {KIND: [raw]}}


def background_lesson():
    return {"id": BACKGROUND, "family": "grammar", "revision": "synthetic-v1", "title": "方向へ",
        "abilities": ["application"], "depends_on": [],
        "instruction": {"rule": "へ表示方向。", "example": "学校へ行きます。",
                        "translation": "去学校。"},
        "question_templates": {KIND: [{"prompt": "学校（　）行きます。", "task": "填空",
            "input_mode": "choice", "choices": ["へ", "で"], "answer": "へ", "safe": False,
            "feedback": "へ表示前往的方向。"}]}}


@pytest.fixture
def cloze_case(tmp_path, monkeypatch, request):
    authored, background = lesson(), background_lesson()
    # Register exact synthetic authored sources, rather than bypassing any
    # qualification or scheduler check with a mock result.
    monkeypatch.setattr(learning_content, "_ITEMS", {CID: deepcopy(authored), BACKGROUND: deepcopy(background)})
    monkeypatch.setattr(learning_questions, "_PRACTICE", {"schema_version": 1, "items": {}})
    from kanamemory import learning_conditions, learning_dependencies
    for cached in (learning_conditions._reviewed_index, learning_dependencies._bundled_dependency_signature):
        cached.cache_clear()
        request.addfinalizer(cached.cache_clear)
    path = tmp_path / "learner"
    with StudyStore(path, time_zone="UTC") as store:
        learning = LearningStore(store)
        learning.enroll(background, now=START)
        when = ready(learning, BACKGROUND)
        seed = build_learning_question(background, KIND)
        learning.append_questions(seed, now=when)
        learning.submit(seed["id"], seed["answer"], True, now=when)
        learning.finalize(seed["id"], 3, now=when)
        learning.enroll(authored, now=START)
        ready(learning, CID)
        q = build_learning_question(authored, KIND)
        yield SimpleNamespace(store=store, learning=learning, item=authored,
                              question=q, path=path, when=START + timedelta(minutes=3))


def enqueue(case, suffix=""):
    q = deepcopy(case.question)
    if suffix:
        q["id"] += ":" + suffix
        q["question_id"] = q["id"]
    assert case.learning.append_questions(q, now=case.when) == [q["id"]]
    return case.learning.get_question(q["id"])


def score_primary(case):
    q = enqueue(case)
    case.learning.submit(q["id"], q["answer"], True, now=case.when)
    return case.learning.finalize(q["id"], 3, now=case.when)


def scheduling_state(learning, identity=CID):
    row = learning.get_unit(identity, "application")
    return {key: deepcopy(row[key]) for key in
            ("review_count", "due_at_utc", "scheduler_state_json", "probe_due_at_utc")}
