"""A lesson's validated template table is built once and shared, read-only (design 07, the template table cache).

practice_templates hands every caller the same table for the same lesson value and content; under the
shared-value guard (kanamemory.shared_tables; these tests switch it on for themselves, with the table cache
dropped around each one) the table refuses every change, and a copy of it is an ordinary value. The cache is dropped with the other content caches and keyed by the lesson's value, so
a changed lesson (a personal version, a reviewed overlay) or a replaced content namespace gets its own table.
Synthetic course only.
"""
from copy import copy, deepcopy

import pytest

from kanamemory import content_caches, course_planning, learning_content, learning_questions, shared_tables
from kanamemory.shared_tables import ReadOnlyValue
from support.app_fixtures import question_app, setup_app  # noqa: F401
from support.content_cache_fixtures import drop_content_caches  # noqa: F401 (a fixture)
from support.learning_fixtures import learning_app  # noqa: F401
from support.study_path_runtime_support import path_app  # noqa: F401

TEA = "word:synthetic:tea"
RECALL = {"prompt": "茶", "task": "写出本课所学的单词。", "answer": "お茶", "accepted_answers": ["お茶", "おちゃ"],
          "choices": [], "input_mode": "text", "strict": False, "safe": False, "feedback": "お茶（おちゃ）"}
MEANING = {"prompt": "お茶", "task": "这个词的意思是？", "answer": "茶", "choices": ["茶", "水", "饭", "酒"],
           "feedback": "お茶：茶", "safe": True}


def tea_lesson(**changes):
    lesson = {"id": TEA, "family": "word", "revision": "synthetic-v1", "title": "お茶", "reading": "おちゃ",
              "meaning": "茶", "abilities": ["understanding", "use"], "depends_on": [],
              "instruction": {"rule": "お茶：茶", "example": "お茶を飲みます。"},
              "question_templates": {"vocab_meaning": [deepcopy(MEANING)], "vocab_recall": [deepcopy(RECALL)]}}
    lesson.update(changes)
    return lesson


@pytest.fixture
def guard(monkeypatch, drop_content_caches):
    """The shared-value guard on for this test, whatever the suite's configuration: the tables handed out are
    the read-only ones. The cache is dropped before (no table shared without the guard is handed out) and
    after (no read-only table outlives the test)."""
    monkeypatch.setattr(shared_tables, "GUARD", True)
    content_caches.clear_all()
    yield
    content_caches.clear_all()


@pytest.fixture
def course(monkeypatch, guard):
    monkeypatch.setattr(learning_content, "_ITEMS", {TEA: tea_lesson()})
    monkeypatch.setattr(learning_questions, "_PRACTICE", {"schema_version": 1, "items": {}})
    content_caches.clear_all()
    built = []
    original = learning_questions._built_templates

    def counted(item):
        built.append(item["id"])
        return original(item)
    monkeypatch.setattr(learning_questions, "_built_templates", counted)
    return built


def test_a_mutation_of_a_shared_value_raises_and_a_copy_does_not():
    value = shared_tables.read_only({"kind": [{"prompt": "a", "choices": ["x", "y"]}], "n": 1})
    with pytest.raises(ReadOnlyValue):
        value["kind"] = []
    with pytest.raises(ReadOnlyValue):
        value["kind"].append({})
    with pytest.raises(ReadOnlyValue):
        value["kind"][0]["prompt"] = "b"
    with pytest.raises(ReadOnlyValue):
        value["kind"][0]["choices"].sort()
    with pytest.raises(ReadOnlyValue):
        value.update(n=2)
    with pytest.raises(ReadOnlyValue):
        del value["n"]
    # Reading is unchanged.
    assert value == {"kind": [{"prompt": "a", "choices": ["x", "y"]}], "n": 1} and isinstance(value, dict)
    # Copies are ordinary values: a deep copy all the way down, dict()/list()/copy() at their own level.
    own = deepcopy(value)
    own["kind"][0]["choices"].append("z")
    assert type(own) is dict and type(own["kind"][0]["choices"]) is list
    template = dict(value["kind"][0], prompt="b")
    assert template["prompt"] == "b" and value["kind"][0]["prompt"] == "a"
    options = list(value["kind"][0]["choices"])
    options.append("z")
    assert copy(value)["n"] == 1 and type(value.copy()) is dict
    with pytest.raises(ReadOnlyValue):
        dict(value)["kind"].append({})  # a shallow copy still shares the inner values


def test_one_lesson_value_builds_one_table_and_every_caller_shares_it(course):
    item = learning_content.get_learning_item(TEA)
    first = learning_questions.practice_templates(item)
    again = learning_questions.practice_templates(deepcopy(item))  # an equal value, another object
    assert first is again and course == [TEA]
    with pytest.raises(ReadOnlyValue):
        first["vocab_recall"][0]["answer"] = "x"
    # The kinds and a whole planning of the lesson read the same table.
    assert learning_questions.question_kinds(item) and course == [TEA]
    assert learning_questions.build_learning_question(item, "vocab_meaning")["answer"] == "茶" and course == [TEA]


def test_a_changed_lesson_value_has_its_own_table(course):
    # A personal version or a reviewed overlay arrives as another lesson value: another key, another table.
    item = learning_content.get_learning_item(TEA)
    first = learning_questions.practice_templates(item)
    changed = deepcopy(item)
    changed["question_templates"]["vocab_meaning"][0]["prompt"] = "おちゃ"
    second = learning_questions.practice_templates(changed)
    assert second is not first and second["vocab_meaning"][0]["prompt"] == "おちゃ"
    assert first["vocab_meaning"][0]["prompt"] == "お茶" and course == [TEA, TEA]
    reviewed = dict(item, personal_reading={"version_id": "v1", "pending": False})
    assert learning_questions.practice_templates(reviewed) is not first and len(course) == 3


def test_a_content_change_drops_the_tables(course, monkeypatch):
    item = learning_content.get_learning_item(TEA)
    first = learning_questions.practice_templates(item)
    # A library change ends in clear_all (content_pack builds a catalogue with another token).
    content_caches.clear_all()
    assert learning_questions._TABLES == {}
    assert learning_questions.practice_templates(item) is not first and course == [TEA, TEA]
    # A replaced namespace (a synthetic course patched in) is another key even without clear_all.
    kept = learning_questions.practice_templates(item)
    monkeypatch.setattr(learning_questions, "_PRACTICE", {"schema_version": 1, "items": {}})
    assert learning_questions.practice_templates(item) is not kept and len(course) == 3


def test_a_refused_table_is_not_kept_and_an_unkeyable_lesson_is_built_each_time(course, monkeypatch):
    broken = tea_lesson()
    monkeypatch.setattr(learning_questions, "_PRACTICE", {"schema_version": 1, "items": {TEA: {
        "item_fingerprint": __import__("kanamemory.material_review", fromlist=["x"]).item_fingerprint(broken),
        "templates": {"vocab_meaning": [dict(MEANING)]}}}})  # an extension without reviewed metadata
    for _ in range(2):
        with pytest.raises(ValueError):
            learning_questions.practice_templates(broken)
    assert len(course) == 2 and not learning_questions._TABLES
    odd = dict(tea_lesson(), callback=lambda: None)  # a value pickle cannot write
    monkeypatch.setattr(learning_questions, "_PRACTICE", {"schema_version": 1, "items": {}})
    learning_questions.practice_templates(odd)
    learning_questions.practice_templates(odd)
    assert len(course) == 4


def test_the_table_cache_is_bounded(course, monkeypatch):
    monkeypatch.setattr(learning_questions, "TEMPLATE_TABLE_LIMIT", 3)
    for index in range(5):
        learning_questions.practice_templates(tea_lesson(revision=f"synthetic-v{index}"))
    assert len(learning_questions._TABLES) == 3


def test_planning_a_lesson_twice_builds_its_table_once(course, path_app):
    app = path_app
    item = tea_lesson()
    app.learning_store.enroll(item)
    saved = app.learning_store.get_item(TEA)
    reader = dict(enabled_kinds=["vocab_meaning", "vocab_recall"], queries=app.store.queries,
                  learning_store=app.learning_store, system_japanese_input=False)
    first = list(course_planning.iter_question_plans(saved, **reader))
    again = list(course_planning.iter_question_plans(app.learning_store.get_item(TEA), **reader))
    assert first == again and first and course.count(TEA) == 1

