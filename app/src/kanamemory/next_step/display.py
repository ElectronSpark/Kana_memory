"""The three end pages of the continue action: page names, every fixed text and the display models (design 13 S4).

Pure: no Toga, no store, no app object. The wait page and the error page keep
their wording where they are drawn. The rows that shared builders make from the
app (the next content, the route's next sections, extra practice) are not part
of these models: the outer layer builds them and hands them to the page.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..arrangement.display import SectionNote
from .model import EndAction, EndActionKind

DONE_PAGE = "learning_done"
WAIT_PAGE = "learning_wait"
# 本轮已保存
ROUND_END_TITLE = "本轮已保存"
ROUND_END_TEXT = "作答记录和复习安排已保存。"
NEW_ROUND = "开始新一轮"
# 暂时没有可进行的假名题型
NO_KIND_TITLE = "暂时没有可进行的假名题型"
NO_KIND_TEXT = "已加入的小节仍保留。请检查题型设置、语音或手写模型，以及内容是否已暂停。"
PRACTICE_HUB = "题型与练习设置"
SETTINGS = "语音与手写模型"
ROUTE_PAGE = "学习路线"
# 本次学习已保存
STUDY_END_TITLE = "本次学习已保存"
STUDY_END_TEXT = "可以继续下一小节，或稍后按复习安排回来。"
# Every end page
HOME = "回到今日"


@dataclass(frozen=True)
class EndButton:
    """An end-page button: its caption and what it does."""

    caption: str
    action: EndAction


@dataclass(frozen=True)
class RoundEndModel:
    """The page 本轮已保存."""

    page: str
    title: str
    text: str
    new_round: EndButton
    home: EndButton


@dataclass(frozen=True)
class NoKanaKindModel:
    """The page 暂时没有可进行的假名题型."""

    page: str
    title: str
    text: str
    practice_hub: EndButton
    settings: EndButton
    route_page: EndButton
    home: EndButton


@dataclass(frozen=True)
class StudyEndModel:
    """The page 本次学习已保存; ``note`` is B10's next section under breadth (None otherwise)."""

    page: str
    title: str
    note: SectionNote | None
    note_add: EndAction | None   # what the note's button does: add the note's section in its route
    text: str
    practice_hub: EndButton
    home: EndButton


def _button(caption: str, kind: EndActionKind) -> EndButton:
    return EndButton(caption, EndAction(kind))


def round_end_model() -> RoundEndModel:
    """The model of 本轮已保存."""
    return RoundEndModel(page=DONE_PAGE, title=ROUND_END_TITLE, text=ROUND_END_TEXT,
                         new_round=_button(NEW_ROUND, EndActionKind.NEW_ROUND),
                         home=_button(HOME, EndActionKind.HOME))


def no_kana_kind_model() -> NoKanaKindModel:
    """The model of 暂时没有可进行的假名题型."""
    return NoKanaKindModel(page=WAIT_PAGE, title=NO_KIND_TITLE, text=NO_KIND_TEXT,
                           practice_hub=_button(PRACTICE_HUB, EndActionKind.PRACTICE_HUB),
                           settings=_button(SETTINGS, EndActionKind.SETTINGS),
                           route_page=_button(ROUTE_PAGE, EndActionKind.ROUTE_PAGE),
                           home=_button(HOME, EndActionKind.HOME))


def study_end_model(note: SectionNote | None, note_route_id: str | None) -> StudyEndModel:
    """The model of 本次学习已保存; ``note_route_id`` is the route the note's section is added in."""
    note_add = (EndAction(EndActionKind.ADD_SECTIONS, note.section_id, note_route_id)
                if note is not None else None)
    return StudyEndModel(page=DONE_PAGE, title=STUDY_END_TITLE, note=note, note_add=note_add, text=STUDY_END_TEXT,
                         practice_hub=_button(PRACTICE_HUB, EndActionKind.PRACTICE_HUB),
                         home=_button(HOME, EndActionKind.HOME))
