"""Shared answer option ui fixtures and assertions; no collected scenarios."""
from unittest.mock import AsyncMock
from kanamemory.app import KanaMemory
from types import MethodType
from unittest.mock import Mock
from kanamemory.domain import Question
from support.fake_ui import View
from support.fake_ui import descendants
from kanamemory.navigation import page_view
import pytest
from support.fake_ui import text_of


RECORDS = [
    {"id": "ka", "hiragana": "か", "katakana": "カ", "romaji": "ka", "category": "basic"},
    {"id": "na", "hiragana": "な", "katakana": "ナ", "romaji": "na", "category": "basic"},
    {"id": "kya", "hiragana": "きゃ", "katakana": "キャ", "romaji": "kya", "category": "yoon"},
    {"id": "gya", "hiragana": "ぎゃ", "katakana": "ギャ", "romaji": "gya", "category": "yoon"},
]


WORD = {"id": "kana-word", "form": "仮名", "reading": "かな", "gloss": "kana syllabary", "gloss_language": "eng"}


@pytest.fixture
def preview_app(question_app):
    app = question_app
    app._practice_preferences = KanaMemory._clean_preferences({})
    app._view_key, app._render_key = ("show_question", None), None
    app._navigating, app._paint_handle, app._page_scroll = False, None, None
    app._ink_revision, app._audio_serial = 0, 0
    app.content_slot, app.title_label, app.subtitle_label = View(View()), View(), View()
    app.repo.kana_list.return_value = RECORDS
    app.repo.get_kana.side_effect = lambda kid: next((r for r in RECORDS if r["id"] == kid), None)
    app.repo.get_word.side_effect = lambda wid: WORD if wid == WORD["id"] else None
    app.platform.preview_index = AsyncMock(return_value=False)
    app.platform.set_back_enabled = Mock()
    app.loop = Mock()

    def screen(name, title, *children, **kwargs):
        app.body = KanaMemory.screen(app, name, title, *children, **kwargs)
        if app._page_scroll is not None:
            app._page_scroll.position = (0, 0)
        return app.body

    app.screen = screen
    for name in ("_capture_page", "_sync_back_handler", "_content_page_key", "_preview_link",
                 "_show_practice_preview", "_mark_lookup_assisted", "open_content_kana",
                 "preview_practice_answer", "check_answer", "go_back", "confirm_ink"):
        setattr(app, name, MethodType(getattr(KanaMemory, name), app))

    @page_view(detail=True)
    def show_kana_detail(self, kana_id):
        self.opened_kana = kana_id
        self.screen("kana_detail", "详情", View(text=kana_id))

    app.show_kana_detail = MethodType(show_kana_detail, app)

    @page_view(detail=True)
    def show_word_detail(self, word_id):
        self.opened_word = word_id
        self.screen("word_detail", "词条详情", View(text=word_id))

    app.show_word_detail = MethodType(show_word_detail, app)
    return app


def render(app, kind="kana_choice", *, compound=False):
    right, wrong = RECORDS[2:4] if compound else RECORDS[:2]
    values = [r["romaji" if kind in {"kana_choice", "kana_romaji"} else "hiragana"] for r in (right, wrong)]
    app.questions = [Question(right["id"], kind, "hiragana", "", values[0], options=values,
        metadata={"kana_symbol": right["hiragana"], "romaji": right["romaji"], "content_version": "test"})]
    if kind in {"word_kana", "word_meaning_kana"}:
        app.questions = [Question(WORD["id"], kind, "hiragana", "", WORD["reading"], options=["か", "な"],
            metadata={"romaji": "kana", "meaning_hint": "假名", "content_version": "test"})]
    app.show_question()
    return app.question, values


def control(app, caption):
    return next(v for v in descendants(app.practice_preview_slot) if v.text == caption)


def assert_compact_target(app, question):
    """The automatic result link contributes context, never a second answer."""
    slot = app.practice_preview_slot
    assert app.body.children[2] is slot
    assert [v.text for v in descendants(slot) if v.on_press] == ["详细"]
    assert slot.children[0]._feedback_fragment["target_id"] == question.content_id
    message = text_of(slot)
    for redundant in ("正确答案速览", "收起", "平：", "片："):
        assert redundant not in message
    return message


def assert_answer_block(app, expected, actual, *, single):
    block = app.body.children[1]
    if single:
        assert [v.text for v in descendants(block) if not v.children] == [expected]
    else:
        cells = [cell for line in block.children for cell in line.children]
        assert "".join(cell.children[0].text.strip() for cell in cells) == expected
        assert "".join(cell.children[1].text.strip() for cell in cells) == actual
    assert "正确答案：" not in text_of(app.body)
    assert "你的答案：" not in text_of(app.body)
