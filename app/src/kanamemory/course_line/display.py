"""The six pages the course line draws itself: every fixed text and the display models (design 13).

Pure: no Toga, no store, no app object. The page names and the captions the
continue action's end pages share come from next_step.display. The rows that
shared builders make from the app (the next content, the replacement action of
材料待核, extra practice) are not part of these models: the outer layer builds
them and hands them to the page.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..next_step.display import DONE_PAGE, HOME, NEW_ROUND, WAIT_PAGE
from .model import CourseAction, CourseActionKind

# 所属内容库已停用
LIBRARY_OFF_TITLE = "所属内容库已停用"
LIBRARY_OFF_TEXT = "剩下的 {count} 项课程所属的内容库已停用、缺失或已被取代，暂不出题。"
LIBRARY_OFF_KEPT = "学习记录、草稿和复习时间均已保留；重新启用该库后即可继续。"
CONTENT_LIBRARIES = "查看内容库"
# 材料待核
MATERIAL_TITLE = "材料待核"
MATERIAL_TEXT = "相关说明已更新，部分旧题仍需核对。"
MATERIAL_KEPT = "原题、草稿和复习时间均已保留。"
# 需要日语语音: the heard-word kind is kept on but this device has no Japanese voice
VOICE_TITLE = "需要日语语音"
VOICE_TEXT = "听词选词需要设备上的日语语音，安装后即可继续。"
VOICE_NOTE = "未完成题目与复习时间已保留；也可在题型设置中关闭听词选词。"
DEVICE_SETTINGS_CAPTION = "语音与手写"
# 题型已关闭
KINDS_TITLE = "题型已关闭"
KIND_SETTINGS = "题型设置"
PAUSED_NO_HEAD_TEXT = "未完成题目已保留，重新开启对应题型后可继续。"
PAUSED_DISABLED_TEXT = "未完成题目与复习时间已保留，开启对应题型后可继续。"
# 课程学习 (the round is done)
DONE_TITLE = "课程学习"
DONE_TEXT = "本次学习已保存。"
# 稍后继续
WAIT_TITLE = "稍后继续"
WAIT_NO_DUE = "当前题型没有可答题目。"
WAIT_NOTE = "到期后自动继续，也可以先离开。"
CHECK = "检查并继续"
# 先巩固前置知识
PREREQ_TITLE = "先巩固前置知识"
PREREQ_TEXT = "先接着巩固前置内容，完成后自动回到本项。"
PREREQ_UNAVAILABLE = "前置内容的练习暂不可用，请检查题型或暂停设置。"
LEARNING_SETTINGS_CAPTION = "学习设置"
TAUGHT_PREFIX = "已学过、可以接着往后学的前置："
ACKNOWLEDGE = "我已了解前置，继续本课"


@dataclass(frozen=True)
class CourseButton:
    """A button of the course line's own pages: its caption and what it does."""

    caption: str
    action: CourseAction


@dataclass(frozen=True)
class LibraryOffModel:
    """The page 所属内容库已停用; ``libraries`` is None when the app has no content library page."""

    page: str
    title: str
    text: str
    kept: str
    libraries: CourseButton | None
    home: CourseButton


@dataclass(frozen=True)
class MaterialBlockedModel:
    """The page 材料待核 (its replacement action is a handed-in row)."""

    page: str
    title: str
    text: str
    kept: str
    home: CourseButton


@dataclass(frozen=True)
class PausedModel:
    """The paused page, 需要日语语音 or 题型已关闭; the first button is the primary one."""

    page: str
    title: str
    text: str
    note: str | None
    buttons: tuple[CourseButton, ...]
    home: CourseButton


@dataclass(frozen=True)
class DoneModel:
    """The page 课程学习 after the round is done."""

    page: str
    title: str
    text: str
    new_round: CourseButton
    home: CourseButton


@dataclass(frozen=True)
class WaitModel:
    """The page 稍后继续."""

    page: str
    title: str
    when: str
    note: str
    check: CourseButton
    settings: CourseButton
    home: CourseButton


@dataclass(frozen=True)
class PrerequisiteModel:
    """The page 先巩固前置知识. With a due time it shows ``due_label`` and ``due_note``; without one,
    ``unavailable`` and the ``settings`` button. ``taught_line`` only when a dependency is already taught."""

    page: str
    title: str
    item_title: str
    text: str
    due_label: str | None
    due_note: str | None
    unavailable: str | None
    settings: CourseButton | None
    taught_line: str | None
    check: CourseButton
    acknowledge: CourseButton
    home: CourseButton


def _button(caption: str, kind: CourseActionKind) -> CourseButton:
    return CourseButton(caption, CourseAction(kind))


def library_off_model(count, *, libraries: bool) -> LibraryOffModel:
    """The model of 所属内容库已停用 for ``count`` waiting items; ``libraries``: the app has a content library page."""
    return LibraryOffModel(page=WAIT_PAGE, title=LIBRARY_OFF_TITLE, text=LIBRARY_OFF_TEXT.format(count=count),
                           kept=LIBRARY_OFF_KEPT,
                           libraries=_button(CONTENT_LIBRARIES, CourseActionKind.CONTENT_LIBRARIES) if libraries else None,
                           home=_button(HOME, CourseActionKind.HOME))


def material_blocked_model() -> MaterialBlockedModel:
    """The model of 材料待核."""
    return MaterialBlockedModel(page=WAIT_PAGE, title=MATERIAL_TITLE, text=MATERIAL_TEXT, kept=MATERIAL_KEPT,
                                home=_button(HOME, CourseActionKind.HOME))


def paused_model(text: str, *, voice: bool) -> PausedModel:
    """The paused page: 需要日语语音 when ``voice`` (``text`` is not shown then), else 题型已关闭 with ``text``."""
    if voice:
        return PausedModel(page=WAIT_PAGE, title=VOICE_TITLE, text=VOICE_TEXT, note=VOICE_NOTE,
                           buttons=(_button(DEVICE_SETTINGS_CAPTION, CourseActionKind.DEVICE_SETTINGS),
                                    _button(KIND_SETTINGS, CourseActionKind.LEARNING_SETTINGS)),
                           home=_button(HOME, CourseActionKind.HOME))
    return PausedModel(page=WAIT_PAGE, title=KINDS_TITLE, text=text, note=None,
                       buttons=(_button(KIND_SETTINGS, CourseActionKind.LEARNING_SETTINGS),),
                       home=_button(HOME, CourseActionKind.HOME))


def done_model() -> DoneModel:
    """The model of 课程学习 after the round is done."""
    return DoneModel(page=DONE_PAGE, title=DONE_TITLE, text=DONE_TEXT,
                     new_round=_button(NEW_ROUND, CourseActionKind.NEW_ROUND),
                     home=_button(HOME, CourseActionKind.HOME))


def wait_model(when: str | None) -> WaitModel:
    """The model of 稍后继续; ``when`` is the due time's label (None: the head has no due time)."""
    return WaitModel(page=WAIT_PAGE, title=WAIT_TITLE, when=WAIT_NO_DUE if when is None else when, note=WAIT_NOTE,
                     check=_button(CHECK, CourseActionKind.CHECK_WAIT),
                     settings=_button(KIND_SETTINGS, CourseActionKind.LEARNING_SETTINGS),
                     home=_button(HOME, CourseActionKind.HOME))


def prerequisite_model(content: dict, due_label: str | None, taught) -> PrerequisiteModel:
    """The model of 先巩固前置知识 for the waiting ``content``; ``due_label`` is the due time's label (None: the
    head has no due time), ``taught`` the titles of the dependencies already taught."""
    due = due_label is not None
    acknowledge = CourseAction(CourseActionKind.ACKNOWLEDGE_PREREQUISITES, content["id"],
                               content.get("instruction_scope"))
    return PrerequisiteModel(
        page=WAIT_PAGE, title=PREREQ_TITLE, item_title=content["title"], text=PREREQ_TEXT,
        due_label=due_label, due_note=WAIT_NOTE if due else None, unavailable=None if due else PREREQ_UNAVAILABLE,
        settings=None if due else _button(LEARNING_SETTINGS_CAPTION, CourseActionKind.LEARNING_SETTINGS),
        taught_line=TAUGHT_PREFIX + "、".join(taught) if taught else None,
        check=_button(CHECK, CourseActionKind.CHECK_WAIT),
        acknowledge=CourseButton(ACKNOWLEDGE, acknowledge),
        home=_button(HOME, CourseActionKind.HOME))
