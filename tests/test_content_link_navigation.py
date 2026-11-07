"""Content-only linking and assistance facts across real app navigation methods."""
from kanamemory import ui_components as component_ui
import asyncio
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

ROOT = Path(__file__).resolve().parents[1]
from kanamemory import app as app_module
from kanamemory.app import KanaMemory
from kanamemory.kana_links import ContentText, KanaLinkIndex
from kanamemory.navigation import BackStack, PageState


def state(page="question", session="session-1"):
    value = SimpleNamespace(
        page=page, session_id=session, question_index=0, _view_key=("show_question", None),
        back_stack=BackStack(), _assisted_attempts=set(), _answered_attempts=set(),
        answer_revealed=False, question_notice=SimpleNamespace(text=""),
        question=SimpleNamespace(content_id="kana-304b", kind="kana_choice"),
        platform=Mock(), store=Mock(is_content_available=lambda content_id: True), show_kana_detail=Mock(), alert=Mock(),
        _page_token=5, _ink_revision=2, _audio_serial=0, _paint_handle=None,
        content_slot=Mock(), title_label=SimpleNamespace(text=""),
        _render_key=None, _preview_pending=None, repo=Mock(), _checkpoint=Mock(), refresh_home_actions=Mock(),
        _show_practice_preview=Mock(),
    )
    for name in ("_attempt_key", "_sync_back_handler", "_content_page_key", "_mark_lookup_assisted", "_preview_link"):
        setattr(value, name, MethodType(getattr(KanaMemory, name), value))
    value.platform.preview_index = AsyncMock(return_value=True)
    value.repo.get_kana.return_value = {"id": "kana-304b", "hiragana": "か", "katakana": "カ", "romaji": "ka", "category": "seion"}
    return value


def complete_preview(value, opener):
    """Run the real central preview's scheduled task without a native window."""
    async def run():
        tasks = []
        def schedule(coroutine):
            task = asyncio.create_task(coroutine)
            tasks.append(task)
            return task
        value.loop = SimpleNamespace(create_task=schedule)
        opener()
        await asyncio.gather(*tasks)
    asyncio.run(run())


def test_japanese_ui_copy_is_not_linked_even_when_identical_to_content(monkeypatch):
    index = KanaLinkIndex([{"id": "ka", "hiragana": "か", "katakana": "カ"},
                           {"id": "na", "hiragana": "な", "katakana": "ナ"}])
    value = SimpleNamespace(kana_links=index, platform=Mock(), open_content_kana=Mock(), _content_page_key=lambda: None)
    value.platform.link_kana_label.return_value = True
    monkeypatch.setattr(component_ui, "label", lambda text, **kw: SimpleNamespace(text=text))
    KanaMemory.mixed_label(value, "かな", ContentText("かな"), "カナ")
    widget, spans, callback = value.platform.link_kana_label.call_args.args
    assert widget.text == "かなかなカナ"
    assert [(s["start"], s["end"], s["kana_id"]) for s in spans] == [(2, 3, "ka"), (3, 4, "na")]
    assert callback is value.open_content_kana
    value.platform.reset_mock()
    KanaMemory.mixed_label(value, "かなカナ")
    value.platform.link_kana_label.assert_not_called()


def test_question_lookup_marks_assistance_before_navigation_without_writing_review():
    value = state()
    value.show_kana_detail.side_effect = lambda kid: (
        value.answer_revealed is True and value._attempt_key() in value._assisted_attempts
    ) or (_ for _ in ()).throw(AssertionError("Lookup was not marked before capturing the view"))
    complete_preview(value, lambda: KanaMemory.open_content_kana(value, "kana-304b"))
    assert value._assisted_attempts == {("session-1", 0)}
    value.platform.preview_index.assert_not_called()
    value.show_kana_detail.assert_not_called()
    value._show_practice_preview.call_args.args[3]()
    value.show_kana_detail.assert_called_once_with("kana-304b")
    assert value.store.mock_calls == []


def test_lookup_via_word_page_marks_suspended_question_and_survives_stale_snapshot():
    value = state(page="words")
    notice = SimpleNamespace(text="")
    old = PageState(("show_question", None), "question", "练习", object(),
                    {"session_id": "session-1", "question_index": 0, "question": value.question,
                     "answer_revealed": False, "question_notice": notice})
    value.back_stack.pages.append(old)
    complete_preview(value, lambda: KanaMemory.open_content_kana(value, "kana-304b"))
    assert value._assisted_attempts == {("session-1", 0)}
    value.page = "kana_detail"
    assert KanaMemory.go_back(value) is True
    assert value.page == "question" and value.answer_revealed is True
    assert "仅安排再学一次" in notice.text
    # Back checks the saved queue against current types, without grading it.
    assert value.store.mock_calls == [call.load_session_progress(),
                                      call.get_setting('personal_content_version_generation_v1', 0)]


def test_post_answer_lookup_does_not_change_independently_completed_answer():
    value = state(page="feedback")
    value._answered_attempts.add(value._attempt_key())
    value.back_stack.pages.append(PageState(("show_question", None), "question", "练习", object(),
                                           {"session_id": value.session_id, "question_index": 0}))
    complete_preview(value, lambda: KanaMemory.open_content_kana(value, "kana-304b"))
    assert not value.answer_revealed and not value._assisted_attempts
    value.platform.preview_index.assert_not_called()
    value.show_kana_detail.assert_not_called()
    value._show_practice_preview.call_args.args[3]()
    value.show_kana_detail.assert_called_once_with("kana-304b")


def test_normal_back_rebuilds_current_content_before_mounting_retained_widgets(monkeypatch):
    from kanamemory import personal_content_refresh
    value = state(page="kana_detail")
    previous = PageState(("show_example_detail", "example"), "example_detail", "", object(),
                         {"_example_detail_state": ("example", None)})
    value.back_stack.pages.append(previous)
    refresh = Mock(return_value=True)
    monkeypatch.setattr(personal_content_refresh, "refresh_return", refresh)
    assert KanaMemory.go_back(value)
    assert value._example_detail_state == ("example", None)
    refresh.assert_called_once_with(value, previous)
    value.content_slot.add.assert_not_called()
    value._checkpoint.assert_not_called()


def test_normal_back_rebuilds_diagnostic_without_mounting_stale_page():
    value = state(page="kana_detail")
    context = {"episode": "saved-episode"}
    value.back_stack.pages.append(PageState(("diagnostic_question", None), "diagnostic_question", "",
        object(), {"_diagnostic_context": context}))
    value.continue_diagnostic = Mock()
    assert KanaMemory.go_back(value)
    value.continue_diagnostic.assert_called_once_with("saved-episode")
    value.content_slot.add.assert_not_called()
    value._checkpoint.assert_not_called()


def test_stale_feedback_controls_cannot_save_good_after_assisted_lookup():
    value = state(page="feedback")
    value._assisted_attempts.add(value._attempt_key())
    KanaMemory.save_rating(value, 3)
    assert value.answer_revealed
    value.store.record_review.assert_not_called()
    value.alert.assert_called_once()


def test_nonpractice_lookup_and_current_detail_have_no_review_side_effects():
    value = state(page="home", session=None)
    complete_preview(value, lambda: KanaMemory.open_content_kana(value, "kana-304b"))
    assert not value._assisted_attempts
    assert value.store.mock_calls == []
    value.page, value._view_key = "kana_detail", ("show_kana_detail", "kana-304b")
    value.show_kana_detail.reset_mock()
    KanaMemory.open_content_kana(value, "kana-304b")
    value.show_kana_detail.assert_not_called()


def test_assistance_does_not_follow_into_another_attempt():
    value = state(page="words")
    value._assisted_attempts.add(("session-1", 0))
    value.question_index = 1
    assert value._attempt_key() not in value._assisted_attempts
    value.session_id, value.question_index = "session-2", 0
    assert value._attempt_key() not in value._assisted_attempts
