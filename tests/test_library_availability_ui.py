"""Design 08 sections 5.1/8.2 at the page level: home, route, continue, library and records pages.

Synthetic courses only; orphans come from disabling the synthetic extension
library through the registry (support/library_availability_support.py).
"""
from copy import deepcopy
from datetime import timedelta

from kanamemory import learning_ui
from kanamemory.learning_ui import LEARNING_PREFS
from support.fake_ui import descendants, text_of
from support.library_availability_support import CORE_ID, EXT_ID, Learner, course
from support.study_path_runtime_support import home, path_app  # noqa: F401
from support.study_home_support import home_page
from support.learning_fixtures import learning_app  # noqa: F401
from support.app_fixtures import question_app, setup_app  # noqa: F401


def learner_for(app, tmp_path):
    app.store.set_setting(LEARNING_PREFS, {"enabled": ["grammar_meaning"]})
    return Learner(app.store, tmp_path / "libraries", learning=app.learning_store)


def due(learner, app, cid):
    start = app._test_clock[0] - timedelta(minutes=5)
    learning = app.learning_store
    learning.enroll(course(cid), now=start)
    learning.advance_instruction(cid, "intro_done", now=start)
    learning.advance_instruction(cid, "guide_correct", now=start)
    from support.library_availability_support import question
    learning.append_questions(question(cid, "q:" + cid), now=start)


def test_home_counts_and_orphan_line_read_the_same_projection(path_app, tmp_path):
    app = path_app
    learner = learner_for(app, tmp_path)
    due(learner, app, EXT_ID)
    home(app)
    assert "现在可答 1 题" in text_of(home_page(app).action_slot)
    assert "所属内容库已停用" not in text_of(home_page(app).secondary_slot)
    before = list(app.store._db.iterdump())
    learner.disable()
    home(app)
    assert "现在可答" not in text_of(home_page(app).action_slot)
    assert "1 项所属内容库已停用" in text_of(home_page(app).secondary_slot)
    learner.enable()
    home(app)
    assert "现在可答 1 题" in text_of(home_page(app).action_slot)
    after = [line for line in app.store._db.iterdump()]
    # Only the registry entry (enabled flag/state) differs.
    changed = set(after) ^ set(before)
    assert all("content_libraries_v1" in line for line in changed)


def test_unqueued_course_preview_never_plans_orphans(path_app, tmp_path, monkeypatch):
    from kanamemory import course_planning  # the preview plans through course_planning.preview_question_plans
    app = path_app
    learner = learner_for(app, tmp_path)
    start = app._test_clock[0] - timedelta(minutes=5)
    for cid in (EXT_ID, CORE_ID):
        app.learning_store.enroll(course(cid), now=start)
    planned = []
    original = course_planning.iter_question_plans

    def recording(item, **kwargs):
        planned.append(item["id"])
        return original(item, **kwargs)
    monkeypatch.setattr(course_planning, "iter_question_plans", recording)
    learner.disable()
    with app.learning_store.read_operation() as read:
        home_page(app).unqueued_courses(projection=read)
    assert planned == [CORE_ID]


def test_route_hides_orphans_and_counts_them_as_unavailable(path_app, tmp_path):
    app = path_app
    learner = learner_for(app, tmp_path)
    app.learning_store.enroll(course(EXT_ID), now=app._test_clock[0])
    service = app.study_path_service()
    assert EXT_ID in service.learning_entries and service.overview()["unavailable_added"] == 0
    learner.disable()
    service = app.study_path_service()  # registry change rebuilds the cached route
    assert EXT_ID not in service.learning_entries
    assert service.overview()["unavailable_added"] == 1
    assert EXT_ID in service.items  # the saved snapshot is still known (records stay readable)


def test_continue_with_only_orphans_shows_the_library_wait_page(path_app, tmp_path):
    app = path_app
    learner = learner_for(app, tmp_path)
    due(learner, app, EXT_ID)
    learner.disable()
    app.continue_learning()
    assert app.page == "learning_wait" and app.title == "所属内容库已停用"
    assert "学习记录、草稿和复习时间均已保留" in text_of(app.body)


def _library_items(monkeypatch):
    items = [dict(course(CORE_ID), category="基础"), dict(course(EXT_ID), category="基础"),
             dict(course("grammar:core:new"), category="基础")]
    monkeypatch.setattr(learning_ui, "learning_items", lambda family=None: [x for x in deepcopy(items)
                                                                    if family in (None, x["family"])])
    monkeypatch.setattr(learning_ui, "learning_item_views", learning_ui.learning_items)  # the library list reads views
    return items


def test_learning_library_tags_filters_and_separates_unavailable_items(learning_app, tmp_path, monkeypatch):
    app = learning_app
    _library_items(monkeypatch)
    learner = learner_for(app, tmp_path)
    learner.ids.add("grammar:core:new")
    app.learning_store.enroll(course(EXT_ID))
    app.show_learning_library(family="grammar")
    text = text_of(app.body)
    assert "所属库：核心库" in text and "所属库：测试扩展库" in text
    assert app.learning_library_owner.items == ["全部库", "核心库", "测试扩展库"]
    app.show_learning_library(family="grammar", library="测试扩展库")
    assert "共 1 项" in text_of(app.body) and "grammar:core:new" not in text_of(app.body)
    learner.disable()
    app.show_learning_library(family="grammar")
    text = text_of(app.body)
    assert "共 2 项 · 已加入 0 项 · 不可用 1 项" in text
    assert "masu · 所属内容库已停用" in text
    buttons = [node for node in descendants(app.body) if getattr(node, "on_press", None) and "masu" in str(node.text)]
    assert buttons == []  # not joinable, not mixed into the list
    assert "学习下一项" in text  # the next joinable item is an available one
    app.enroll_learning(course(EXT_ID))
    app.alert.assert_called()
    assert "内容库" in app.alert.call_args[0][0]


def test_learning_library_without_other_libraries_is_unchanged(learning_app, tmp_path, monkeypatch):
    app = learning_app
    _library_items(monkeypatch)
    app.show_learning_library(family="grammar")
    assert "所属库" not in text_of(app.body) and app.learning_library_owner is None


def test_records_page_shows_orphan_state_and_keeps_history(path_app, tmp_path):
    app = path_app
    learner = learner_for(app, tmp_path)
    due(learner, app, EXT_ID)
    learner.disable()
    app.show_learning_records()
    text = text_of(app.body)
    assert "现在可练 0 项" in text and "所属内容库已停用" in text
    assert "学习记录、草稿和复习时间均保留" in text
