"""One count rule for home, practice header and records (review 2026-11-08 sections 4-6, F5-C)."""
from dataclasses import replace
from datetime import datetime, timedelta
from types import MethodType
from unittest.mock import Mock

from kanamemory import practice_round
from kanamemory.app import KanaMemory, DEFAULT_SUBTITLE, KANA_ASSEMBLY_PAGE
from kanamemory.content_availability import LIBRARY_DISABLED_TEXT
from kanamemory import home_counts as counts
from kanamemory.arrangement import display
from kanamemory.home_counts import (backlog_threshold, home_counts, kana_record_lines, next_review_text,
                                    practice_header, review_backlog_hint, show_next_review, teaching_count)
from kanamemory.study_path_runtime import registry_service_key
from kanamemory.study_path_ui import ROUTE_STATUS, ROUTE_REFUSED_TEXT
from support.app_fixtures import question_app, setup_app  # noqa: F401
from support.detail_header_support import chrome  # noqa: F401
from support.fake_ui import View, descendants, text_of
from support.learning_fixtures import learning_app  # noqa: F401
from support.study_home_support import home_page, patch_home_counts
from support.study_path_runtime_support import home, kana_id, path_app  # noqa: F401


def test_home_counts_combine_sources_and_round_limits_only_ready():
    value = home_counts(kana_ready=24, course_ready=3, unqueued_course_ready=2, unqueued_kana_ready=7,
                        waiting=(1, 74, 0, 583), kana_due_chars=35)
    assert (value.ready_total, value.round_remaining, value.course_due, value.waiting,
            value.kana_due_chars) == (36, 24, 5, 658, 35)
    limited = home_counts(kana_ready=24, course_ready=3, waiting=(1, 74, 0, 583))
    assert (limited.ready_total, limited.waiting) == (27, 658)


def test_teaching_is_counted_once_per_content_id():
    # Two ability scopes of one content queued, the same content and a new one unqueued.
    queued = {("word:a", "intro-1"), ("word:a", "intro-2"), ("word:b", "intro-3")}
    assert teaching_count(3, queued, {"word:a", "word:c"}) == 3
    assert teaching_count(0, (), {"word:c", "word:c"}) == 1
    # The store filtered some queued pairs (e.g. blocked): its count is kept.
    assert teaching_count(1, queued, {"word:b", "word:d"}) == 2
    # ... but never more than the queued contents (N2: units stay content ids).
    scoped = {("word:a", None), ("word:a", "speaking"), ("word:a", "listening"), ("word:b", None)}
    assert teaching_count(3, scoped) == 2
    assert teaching_count(4, scoped, {"word:a", "word:c"}) == 3
    # Design 12 N9: a lesson whose probe sent it to teaching has no queued teaching row of its own scope
    # while a later ability's row is queued and filtered out: its unqueued teaching is still counted.
    assert teaching_count(0, {("grammar:b", "application")}, {"grammar:b"}) == 1


def test_texts():
    assert next_review_text(datetime(2026, 11, 8, 17, 25)) == "下次复习 11月08日 17:25"
    # N8: only while nothing is answerable now.
    moment = datetime(2026, 11, 8, 17, 25)
    assert show_next_review(0, 3, moment) and not show_next_review(1, 3, moment)
    assert not show_next_review(0, 0, moment) and not show_next_review(0, 3, None)
    assert practice_header(24, 0, 30) == "本轮剩 24 题 · 第 1 / 30 题"
    # The records page's kana part: kana numbers only, each line naming what it counts.
    assert kana_record_lines({"total_items": 12, "due_items": 35, "new_items_today": 2, "today_reviews": 6}) == (
        "已学 12 项 · 到期 35 字\n今日新增 2 项 · 作答 6 次")
    assert kana_record_lines({}) == "已学 0 项 · 到期 0 字\n今日新增 0 项 · 作答 0 次"
    assert ROUTE_STATUS["library_unavailable"] == LIBRARY_DISABLED_TEXT
    assert ROUTE_STATUS["refused"] == ROUTE_REFUSED_TEXT == "路线被拒（前缀不符）"


def test_backlog_hint_follows_fifty_or_three_times_the_daily_new_count():
    # Design 11 V2: default 10 new items a day (3x = 30), at most 20 counted.
    assert backlog_threshold() == 30 and backlog_threshold(20) == backlog_threshold(99) == 50
    assert backlog_threshold(0) == 3
    assert review_backlog_hint(30) is None
    assert review_backlog_hint(31) == "待复习 31 题，建议先复习，再加入新内容。"
    assert review_backlog_hint(50, daily_new=20) is None and review_backlog_hint(51, daily_new=20)


def test_home_shows_the_backlog_hint_beside_the_next_section_without_blocking(path_app, monkeypatch):
    app = path_app
    now = app._test_clock[0]
    app.store.record_review(kana_id(app, "あ"), "kana_choice", "hiragana", 1, now=now - timedelta(days=7))
    original = home_counts
    for due, shown in ((30, False), (31, True)):
        patch_home_counts(monkeypatch, lambda due=due, **kw: replace(original(**kw), ready_total=due))
        home(app)
        secondary = text_of(home_page(app).secondary_slot)
        assert ("待复习 31 题" in secondary) is shown
        assert any(view.text.startswith("加入下一小节") for view in descendants(home_page(app).secondary_slot)
                   if getattr(view, "on_press", None))


def test_registry_key_follows_library_enabled_and_state():
    on = {"libraries": [{"library_id": "core", "enabled": True, "state": "installed"},
                        {"library_id": "ext", "enabled": True, "state": "installed"}]}
    off = {"libraries": [dict(on["libraries"][0]), {**on["libraries"][1], "enabled": False}]}
    damaged = {"libraries": [dict(on["libraries"][0]), {**on["libraries"][1], "state": "damaged"}]}
    assert len({registry_service_key(on), registry_service_key(off), registry_service_key(damaged)}) == 3
    assert registry_service_key(None) == () == registry_service_key({"libraries": None})


def test_planned_round_keeps_long_term_waiting_and_next_review(path_app, monkeypatch):
    app = path_app
    now = app._test_clock[0]
    app.store.record_review(kana_id(app, "あ"), "kana_choice", "hiragana", 1, now=now - timedelta(days=7))
    app.store.record_review(kana_id(app, "い"), "kana_choice", "hiragana", 4, now=now)
    free = home_page(app).unqueued_reviews()
    assert free["ready"] == 1 and free["waiting"] == 1 and free["next_due"]
    home(app)
    assert "现在可答 1 题" in text_of(home_page(app).action_slot)
    assert "待到期 1 题" in text_of(home_page(app).action_slot)
    # N8: "现在可答" is shown, so the next due time is not.
    assert "下次复习" not in text_of(home_page(app).action_slot) and "最近复习" not in text_of(home_page(app).action_slot)
    monkeypatch.setattr(practice_round, "planned_round_running", lambda store, **kw: True)
    limited = home_page(app).unqueued_reviews()
    assert limited == {**free, "ready": 0}
    assert home_page(app).unqueued_courses()["ready"] == 0
    home(app)
    text = text_of(home_page(app).action_slot)
    assert "现在可答" not in text and "待到期 1 题" in text and "下次复习 " in text


def test_records_page_names_kana_characters_and_course_questions(path_app):
    app = path_app
    app.store.record_review(kana_id(app, "あ"), "kana_choice", "hiragana", 1,
                            now=app._test_clock[0] - timedelta(days=7))
    app.show_progress = MethodType(KanaMemory.show_progress.__wrapped__, app)
    app.show_cards = Mock()
    app.show_learning_records = Mock()
    app.show_progress()
    text = text_of(app.body)
    assert "已学 1 项 · 到期 1 字" in text and "课程复习：到期未答 0 题" in text
    assert "假名到期" not in text and "课程到期" not in text
    # 学习记录 (design 02): 时间与进度, 课程 with its button, 假名 with the kana figures, the recent kana reviews.
    assert app.page == "progress" and app.title == counts.RECORDS_TITLE == "学习记录"
    texts = [str(view.text) for view in descendants(app.body) if view.text]
    order = [display.PROGRESS_HEADING, counts.RECORDS_COURSE_HEADING, counts.RECORDS_COURSE_BUTTON,
             counts.RECORDS_KANA_HEADING, kana_record_lines(app.store.stats()), "管理记忆卡片",
             counts.RECORDS_KANA_RECENT_HEADING]
    positions = [texts.index(part) for part in order]
    assert positions == sorted(positions)
    assert texts[positions[-1] + 1:] and counts.RECORDS_KANA_EMPTY not in texts  # one kana record below
    next(view for view in descendants(app.body) if view.text == counts.RECORDS_COURSE_BUTTON).on_press(None)
    app.show_learning_records.assert_called_once()


def test_records_page_without_a_course_store_and_without_kana_records(path_app, monkeypatch):
    app = path_app
    monkeypatch.setattr(app, "learning_store", None)
    app.show_cards = Mock()
    KanaMemory.show_progress.__wrapped__(app)
    texts = [str(view.text) for view in descendants(app.body) if view.text]
    assert display.PROGRESS_HEADING not in texts and counts.RECORDS_COURSE_HEADING not in texts
    assert counts.RECORDS_COURSE_BUTTON not in texts
    assert texts.index(counts.RECORDS_KANA_HEADING) < texts.index(counts.RECORDS_KANA_RECENT_HEADING)
    assert texts[texts.index(counts.RECORDS_KANA_RECENT_HEADING) + 1:] == [counts.RECORDS_KANA_EMPTY]
    assert "已学 0 项 · 到期 0 字\n今日新增 0 项 · 作答 0 次" in texts


def test_service_rebuilds_when_registry_changes(path_app):
    app = path_app
    first = app.study_path_service()
    assert app.study_path_service() is first
    view = app.learning_store.availability()
    registry = view.registry or {"libraries": []}
    changed = {**registry, "libraries": [*registry["libraries"],
                                         {"library_id": "test.extra", "enabled": False, "state": "installed"}]}

    class Disabled:
        """The same view, after a library without learned content was disabled."""
        registry = changed

        def __getattr__(self, name):
            return getattr(view, name)
    app.learning_store.availability = lambda *a, **kw: Disabled()
    assert Disabled().unavailable_ids == view.unavailable_ids
    assert app.study_path_service() is not first


def test_wait_and_library_pages_get_their_own_subtitle(chrome):
    chrome.screen(KANA_ASSEMBLY_PAGE, "继续学习", View(text="正在准备题目…"))
    assert chrome.subtitle_label.text == "" and chrome.subtitle_label not in chrome.header_title_box.children
    chrome.screen("content_libraries", "内容库", View(text="核心库"))
    assert chrome.subtitle_label.text == "内容库与路线" and chrome.subtitle_label in chrome.header_title_box.children
    chrome.screen("kana", "假名", View(text="表"))
    assert chrome.subtitle_label.text == DEFAULT_SUBTITLE
    chrome.screen("kana", "假名", View(text="表"), subtitle="自定义")
    assert chrome.subtitle_label.text == "自定义"
