"""Practice details stay in the page without changing answers or ratings."""
from pathlib import Path
import sys
from types import MethodType
from unittest.mock import AsyncMock, Mock

import pytest


from kanamemory.app import KanaMemory
from kanamemory.domain import Question
from kanamemory.navigation import page_view
from support.fake_ui import View, descendants, text_of
from support.app_fixtures import question_app, setup_app


from support.answer_option_ui_support import RECORDS, WORD, preview_app, render, control, assert_compact_target, assert_answer_block


@pytest.mark.parametrize("kind", ["kana_choice", "romaji_kana"])
@pytest.mark.parametrize("correct", [True, False])
@pytest.mark.parametrize("compound", [True, False])
def test_every_choice_has_correct_answer_summary_and_details_without_popup(preview_app, kind, correct, compound):
    app = preview_app
    question, values = render(app, kind, compound=compound)
    answer = values[0 if correct else 1]
    next(v for v in app.body.children if v.text == answer).on_press(None)
    assert app.page == "feedback" and app.question is question and app.question_index == 0
    assert app.answered_text == answer and app.answer_correct is correct
    assert app._attempt_key() in app._answered_attempts
    assert not app.answer_revealed and not app._assisted_attempts
    expected = RECORDS[2 if compound else 0]
    message = assert_compact_target(app, question)
    assert (expected["hiragana"] in message) is (kind == "kana_choice")
    assert expected["katakana"] not in message
    if not correct:
        assert RECORDS[3 if compound else 1]["hiragana"] not in message
    assert_answer_block(app, question.answer, answer, single=correct)
    if correct:
        assert sum(v.text == question.answer for v in descendants(app.body)) == 1
    assert control(app, "详细").on_press
    assert ("记住了" in text_of(app.body)) is correct
    assert app.body.children[2] is app.practice_preview_slot
    assert not hasattr(app, "opened_kana")
    app.platform.preview_index.assert_not_called()
    app.loop.create_task.assert_not_called()
    app.alert.assert_not_called()
    app.store.record_review.assert_not_called()
    control(app, "详细").on_press(None)
    assert app.page == "kana_detail" and app.opened_kana == expected["id"]
    app.go_back()
    assert app.page == "feedback" and app.answered_text == answer and app.answer_correct is correct
    assert ("记住了" in text_of(app.content_slot)) is correct


def test_detail_back_and_reopen_keep_card_answer_and_success_ratings(preview_app):
    app = preview_app
    question, values = render(app)
    app.choose_practice_answer(values[0], question)
    feedback = app.content_slot.children[0]
    slot = app.practice_preview_slot
    detail = control(app, "详细")
    detail.on_press(None)
    assert app.page == "kana_detail" and app.opened_kana == "ka"
    app.go_back()
    assert app.page == "feedback" and app.content_slot.children[0] is feedback
    assert app.practice_preview_slot is slot
    assert app.answered_text == "ka" and app.answer_correct and not app.answer_revealed
    assert app.question_index == 0 and app.session_right == 0
    detail.on_press(None)
    assert app.page == "kana_detail"
    app.go_back()
    app.store.record_review.assert_not_called()
    # Feedback replaces the question. Back must never expose an unanswered
    # copy; the focused-flow confirmation policy is covered separately.
    assert all(state.name != "question" for state in app.back_stack.pages)
    assert app.page == "feedback" and not app.answer_revealed


def test_automatic_result_has_no_collapse_and_removed_card_invalidates_detail(preview_app):
    app = preview_app
    question, values = render(app)
    app.choose_practice_answer(values[0], question)
    detail = control(app, "详细")
    assert [v.text for v in descendants(app.practice_preview_slot) if v.on_press] == ["详细"]
    app.practice_preview_slot.clear()
    assert not app.practice_preview_slot.children
    detail.on_press(None)
    assert app.page == "feedback" and not hasattr(app, "opened_kana")
    assert app.answer_correct and "记住了" in text_of(app.body)


@pytest.mark.parametrize("change", ["page", "question", "attempt", "session", "slot"])
def test_automatic_result_detail_is_inert_outside_original_question_and_slot(preview_app, change):
    from dataclasses import replace
    app = preview_app
    question, values = render(app)
    app.choose_practice_answer(values[0], question)
    detail = control(app, "详细")
    if change == "page":
        app.page = "question"
    elif change == "question":
        app.question = replace(question)
    elif change == "attempt":
        app.question_index += 1
    elif change == "session":
        app.session_id = "other"
    else:
        app.practice_preview_slot = View()
    detail.on_press(None)
    assert not hasattr(app, "opened_kana")
    app.store.record_review.assert_not_called()


def test_double_tap_and_old_question_button_cannot_submit_another_answer(preview_app):
    app = preview_app
    old_question, values = render(app)
    old_button = next(v for v in app.body.children if v.text == values[0])
    old_button.on_press(None)
    old_card = app.practice_preview_slot.children[0]
    app.choose_practice_answer(values[1], old_question)
    assert app.answered_text == values[0] and app.practice_preview_slot.children == [old_card]
    detail = control(app, "详细")
    render(app)
    old_button.on_press(None)
    detail.on_press(None)
    assert app.page == "question" and not app.practice_preview_slot.children
    assert not hasattr(app, "opened_kana")


def test_replacing_lookup_card_makes_only_current_detail_button_active(preview_app):
    app = preview_app
    question, values = render(app)
    app.choose_practice_answer(values[0], question)
    old = control(app, "详细")
    app.open_content_kana("na")
    assert len(app.practice_preview_slot.children) == 1
    assert "平：な" in text_of(app.practice_preview_slot)
    old.on_press(None)
    assert app.page == "feedback"
    control(app, "详细").on_press(None)
    assert app.opened_kana == "na"
    app.platform.preview_index.assert_not_called()
    assert app.answer_correct and not app.answer_revealed


def test_unheard_handwriting_cannot_reveal_summary_or_submit(preview_app):
    app = preview_app
    question, values = render(app, "kana_handwriting")
    app.page, app.free_ink = "ink_question", False
    app.question_audio_ok = False
    app.confirm_ink(values[0], question)
    app.platform.preview_index.assert_not_called()
    assert not app._answered_attempts
    assert app.page == "ink_question"


def test_wrong_handwriting_confirmation_has_correct_answer_inline_summary(preview_app):
    app = preview_app
    question, values = render(app, "kana_handwriting")
    app.page, app.free_ink, app.question_audio_ok = "ink_question", False, True
    app.confirm_ink(values[1], question)
    assert app.page == "feedback" and app.answered_text == "な"
    message = assert_compact_target(app, question)
    assert "か" not in message and "な" not in message
    assert_answer_block(app, "か", "な", single=False)
    assert "记住了" not in text_of(app.body)
    control(app, "详细").on_press(None)
    assert app.page == "kana_detail" and app.opened_kana == "ka"
    app.go_back()
    assert app.page == "feedback" and app.answered_text == "な" and not app.answer_correct
    app.platform.preview_index.assert_not_called()


def test_unknown_handwriting_still_explains_only_correct_answer(preview_app):
    app = preview_app
    question, _ = render(app, "kana_handwriting")
    app.page, app.free_ink, app.question_audio_ok = "ink_question", False, True
    app.confirm_ink("漢字", question)
    assert app.page == "feedback" and not app.answer_correct
    message = assert_compact_target(app, question)
    assert "か" not in message
    assert "漢字" not in message and "未收录" not in message
    assert_answer_block(app, "か", "漢字", single=False)
    control(app, "详细").on_press(None)
    assert app.page == "kana_detail" and app.opened_kana == "ka"
    app.go_back()
    assert app.page == "feedback" and not app.answer_correct and app.answered_text == "漢字"
    assert "记住了" not in text_of(app.content_slot)
    app.platform.preview_index.assert_not_called()
    app.alert.assert_not_called()


@pytest.mark.parametrize("target,wrong", [("ji", "di"), ("di", "ji")])
def test_isolated_homophone_choice_is_correct_and_keeps_target_details(preview_app, target, wrong):
    app = preview_app
    records = {
        "ji": {"id": "ji", "hiragana": "じ", "katakana": "ジ", "romaji": "ji", "category": "dakuten"},
        "di": {"id": "di", "hiragana": "ぢ", "katakana": "ヂ", "romaji": "ji", "category": "dakuten"},
    }
    app.repo.get_kana.side_effect = records.get
    right, other = records[target], records[wrong]
    app.questions = [Question(target, "romaji_kana", "hiragana", "", right["hiragana"],
        options=[right["hiragana"], other["hiragana"]],
        metadata={"kana_symbol": right["hiragana"], "romaji": "ji", "content_version": "test"})]
    app.show_question()
    app.choose_practice_answer(other["hiragana"], app.question)
    assert app.page == "feedback" and app.answer_correct
    assert app.answered_text == other["hiragana"]
    message = assert_compact_target(app, app.question)
    assert right["hiragana"] not in message
    assert other["hiragana"] not in message and "同音还有" not in message
    assert_answer_block(app, right["hiragana"], other["hiragana"], single=True)
    assert "同音答案可接受" in text_of(app.body)
    control(app, "详细").on_press(None)
    assert app.page == "kana_detail" and app.opened_kana == target
    app.go_back()
    assert app.page == "feedback" and app.answered_text == other["hiragana"] and app.answer_correct
    app.repo.kana_list.assert_not_called()
    app.platform.preview_index.assert_not_called()


@pytest.mark.parametrize("kind", ["kana_choice", "romaji_kana", "kana_handwriting", "kana_romaji"])
def test_show_answer_has_question_details_and_only_again_rating(preview_app, kind):
    app = preview_app
    render(app, kind)
    if kind == "kana_handwriting":
        app.page, app.question_audio_ok = "ink_question", True
    app.reveal_answer()
    assert app.page == "feedback" and app.answer_revealed
    message = assert_compact_target(app, app.question)
    assert ("か" in message) is (kind in {"kana_choice", "kana_romaji"})
    assert_answer_block(app, app.question.answer, "", single=True)
    assert "再学一次" in text_of(app.body)
    for caption in ("有些困难", "记住了", "很轻松"):
        assert caption not in text_of(app.body)
    control(app, "详细").on_press(None)
    assert app.page == "kana_detail" and app.opened_kana == "ka"
    app.go_back()
    assert app.page == "feedback" and app.answer_revealed and not app.answer_correct
    assert app.answered_text == ""
    app.platform.preview_index.assert_not_called()
    app.store.record_review.assert_not_called()


@pytest.mark.parametrize("answer,correct", [("ka", True), ("na", False), ("not-a-kana", False)])
def test_romaji_typing_always_opens_target_details_without_changing_result(preview_app, answer, correct):
    app = preview_app
    render(app, "kana_romaji")
    app.romaji_input.value = answer
    app.romaji_input.on_change(app.romaji_input)
    app.romaji_input.on_confirm(app.romaji_input)
    assert app.page == "feedback" and app.answer_correct is correct
    message = assert_compact_target(app, app.question)
    assert "か" in message and "な" not in message
    assert_answer_block(app, app.question.answer, answer, single=correct)
    control(app, "详细").on_press(None)
    assert app.page == "kana_detail" and app.opened_kana == "ka"
    app.go_back()
    assert app.page == "feedback" and app.answered_text == answer and app.answer_correct is correct
    assert ("记住了" in text_of(app.content_slot)) is correct
    app.platform.preview_index.assert_not_called()
    app.store.record_review.assert_not_called()


@pytest.mark.parametrize("kind", ["word_kana", "word_meaning_kana"])
@pytest.mark.parametrize("answer", ["かな", "なか", ""])
def test_word_feedback_always_has_target_word_details_without_repeated_card(preview_app, monkeypatch, kind, answer):
    app = preview_app
    render(app, kind)
    pitch = Mock(return_value=View(text="调号 [0]"))
    monkeypatch.setattr(KanaMemory, "_pitch_slot", pitch)
    app._show_practice_preview = Mock(side_effect=AssertionError("Result must not use the generic lookup card"))
    app.question_audio_ok = True
    if answer:
        for kana in answer:
            app.append_word_key(kana)
        next(v for v in descendants(app.body) if v.text == "提交答案").on_press(None)
    else:
        app.reveal_answer()
    correct = answer == WORD["reading"]
    assert app.page == "feedback" and app.answer_correct is correct
    message = assert_compact_target(app, app.question)
    assert WORD["form"] in message and "kana" in message and WORD["gloss"] in message
    assert "调号 [0]" in message
    assert pitch.call_args.kwargs["reading"] == WORD["reading"]
    assert pitch.call_args.kwargs["plain"] is False
    assert_answer_block(app, WORD["reading"], answer, single=correct or not answer)
    if correct or not answer:
        assert sum(v.text == WORD["reading"] for v in descendants(app.body)) == 1
    control(app, "详细").on_press(None)
    assert app.page == "word_detail" and app.opened_word == WORD["id"]
    app.go_back()
    assert app.page == "feedback" and app.answered_text == answer and app.answer_correct is correct
    assert "保存并继续" in text_of(app.content_slot)
    assert "记住了" not in text_of(app.content_slot)
    assert app.answer_revealed is (not answer)
    app.platform.preview_index.assert_not_called()
    app.store.record_review.assert_not_called()


@pytest.mark.parametrize("kind", ["kana_choice", "kana_romaji", "kana_handwriting", "word_meaning_kana"])
def test_missing_target_record_keeps_answer_and_missing_notice_without_dead_detail_link(preview_app, kind):
    app = preview_app
    render(app, kind)
    app.repo.get_kana.side_effect = lambda kid: None
    app.repo.get_word.side_effect = lambda wid: None
    if kind == "kana_handwriting":
        app.page, app.question_audio_ok = "ink_question", True
    app.reveal_answer()
    assert app.page == "feedback" and not app.answer_correct
    assert "资料尚未收录" in text_of(app.practice_preview_slot)
    assert not [v for v in descendants(app.practice_preview_slot) if v.on_press]
    assert_answer_block(app, app.question.answer, "", single=True)
    assert not any(v.text == "详细" and v.on_press for v in descendants(app.practice_preview_slot))
    app.platform.preview_index.assert_not_called()
    app.alert.assert_not_called()


def test_word_keyboard_remains_incremental_input_without_summary(preview_app):
    app = preview_app
    render(app, "word_meaning_kana")
    app.append_word_key("か")
    app.append_word_key("な")
    assert app.word_answer == "かな"
    assert app.page == "word_question" and not app.practice_preview_slot.children
    app.platform.preview_index.assert_not_called()


@pytest.mark.parametrize("kind", ["kana_choice", "kana_romaji", "word_meaning_kana"])
@pytest.mark.parametrize("outcome", ["correct", "incorrect", "revealed"])
def test_resumed_feedback_rebuilds_inline_card_without_new_review(preview_app, tmp_path, kind, outcome):
    from kanamemory.domain import StudyStore
    app = preview_app
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        question, values = render(app, kind)
        answer = question.answer if outcome == "correct" else "" if outcome == "revealed" else "na"
        session = store.start_session([question])
        store.save_session_progress(session, 0, state={"feedback_pending": True,
            "answered_text": answer, "answer_revealed": outcome == "revealed", "question_audio_ok": True})
        app.store = store
        app._checkpoint = MethodType(KanaMemory._checkpoint, app)
        app.resume_practice = MethodType(KanaMemory.resume_practice, app)
        app.resume_practice()
        assert app.page == "feedback" and app.answer_correct is (outcome == "correct")
        assert app.answer_revealed is (outcome == "revealed")
        message = assert_compact_target(app, app.question)
        assert ("か" in message) is (kind != "word_meaning_kana")
        assert (WORD["form"] in message) is (kind == "word_meaning_kana")
        assert (WORD["gloss"] in message) is (kind == "word_meaning_kana")
        assert_answer_block(app, question.answer, answer, single=outcome != "incorrect")
        assert control(app, "详细").on_press
        assert store.stats()["total_reviews"] == 0
        app.platform.preview_index.assert_not_called()
        app.alert.assert_not_called()
