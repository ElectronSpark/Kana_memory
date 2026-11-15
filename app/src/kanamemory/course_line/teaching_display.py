"""The stop pages of the teaching steps and the page 开始前: every fixed text and the display models (design 13).

Pure: no Toga, no store, no app object. The rows that shared builders make from the app (the replacement
action, the next content) are not part of these models: the outer layer builds them and hands them to the page.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..next_step.display import HOME, WAIT_PAGE
from .teaching_model import TeachingAction, TeachingActionKind

FAMILIARITY_PAGE = "learning_familiarity"
# 引导题待核
GUIDE_BLOCKED_TITLE = "引导题待核"
GUIDE_BLOCKED_TEXT = "说明已经更新，原引导题与草稿已保留。"
SEE_UPDATED = "查看更新后的说明"
# 学习材料尚不完整 / 材料待核验
MATERIAL_MISSING_TITLE = "学习材料尚不完整"
MATERIAL_UNVERIFIED_TITLE = "材料待核验"
MATERIAL_TEXT = "原有进度与题目已保存，暂不开始正式教学。"
SEE_MATERIAL = "查看材料"
PREVIEW_DRAFT = "预览草稿"
# 引导已保留
GUIDE_PAUSED_TITLE = "引导已保留"
GUIDE_PAUSED_TEXT = "这类练习已关闭，输入已保存。"
CONTINUE_OTHER = "继续其他学习"
PRACTICE_SETTINGS = "练习设置"
# 引导例题暂不可用
GUIDE_UNAVAILABLE_TITLE = "引导例题暂不可用"
GUIDE_UNAVAILABLE_TEXT = "这一项的引导例题暂不可用（旧例题已停用），可直接开始练习，或跳到下一项。"
GUIDE_UNAVAILABLE_NOTE = "直接开始练习不会记为已掌握，之后仍按独立作答结果安排复习。"
START_PRACTICE = "直接开始练习"
SKIP_ITEM = "跳到下一项"
NOT_SAVED = "暂未保存："
# 跟着做 (the guide)
GUIDE_EMPTY = "请先完成这一题。"
PASS_STEP = "这一步看过了，下一步"
PASS_GUIDE = "看过了，继续"
# 开始前
FAMILIARITY_TITLE = "开始前"
ABILITY_LABELS = {"reading": "读音", "use": "提取与使用", "listening": "听辨理解", "application": "应用规则"}
UNDERSTANDING_WORD = "词义"
UNDERSTANDING_OTHER = "理解规则"
FAMILIARITY_LEVELS = (("初学", "new"), ("有印象", "some"), ("已熟悉", "familiar"))


@dataclass(frozen=True)
class TeachingButton:
    """A button of a stop page or of the page 开始前: its caption and what it does."""

    caption: str
    action: TeachingAction


@dataclass(frozen=True)
class GuideBlockedModel:
    """The page 引导题待核 (the replacement action and the next content are handed-in rows)."""

    page: str
    title: str
    text: str
    detail: TeachingButton
    home: TeachingButton


@dataclass(frozen=True)
class MaterialNotReadyModel:
    """The page 学习材料尚不完整 or 材料待核验."""

    page: str
    title: str
    text: str
    detail: TeachingButton
    home: TeachingButton


@dataclass(frozen=True)
class GuidePausedModel:
    """The page 引导已保留."""

    page: str
    title: str
    text: str
    go_on: TeachingButton
    settings: TeachingButton
    home: TeachingButton


@dataclass(frozen=True)
class GuideUnavailableModel:
    """The page 引导例题暂不可用."""

    page: str
    title: str
    item_title: str
    title_role: str
    text: str
    note: str
    practice: TeachingButton
    skip: TeachingButton
    home: TeachingButton


@dataclass(frozen=True)
class FamiliarityModel:
    """The page 开始前 asking about one ability of a lesson."""

    page: str
    title: str
    item_title: str
    title_role: str
    ability: str                         # the ability asked, in the learner's words
    levels: tuple[TeachingButton, ...]   # 初学, 有印象, 已熟悉


def _button(caption: str, kind: TeachingActionKind, **values) -> TeachingButton:
    return TeachingButton(caption, TeachingAction(kind, **values))


def guide_blocked_model(item: dict) -> GuideBlockedModel:
    """The model of 引导题待核 for the lesson ``item``."""
    return GuideBlockedModel(page=WAIT_PAGE, title=GUIDE_BLOCKED_TITLE, text=GUIDE_BLOCKED_TEXT,
                             detail=_button(SEE_UPDATED, TeachingActionKind.DETAIL, item=item),
                             home=_button(HOME, TeachingActionKind.LEAVE))


def material_not_ready_model(item: dict, *, missing: bool) -> MaterialNotReadyModel:
    """The model of 学习材料尚不完整 (``missing``) or 材料待核验 for the lesson ``item``."""
    return MaterialNotReadyModel(
        page=WAIT_PAGE, title=MATERIAL_MISSING_TITLE if missing else MATERIAL_UNVERIFIED_TITLE, text=MATERIAL_TEXT,
        detail=_button(SEE_MATERIAL if missing else PREVIEW_DRAFT, TeachingActionKind.DETAIL, item=item),
        home=_button(HOME, TeachingActionKind.LEAVE))


def guide_paused_model() -> GuidePausedModel:
    """The model of 引导已保留. Its 回到今日 shows the home page directly (it does not leave through the line)."""
    return GuidePausedModel(page=WAIT_PAGE, title=GUIDE_PAUSED_TITLE, text=GUIDE_PAUSED_TEXT,
                            go_on=_button(CONTINUE_OTHER, TeachingActionKind.CONTINUE_OTHER),
                            settings=_button(PRACTICE_SETTINGS, TeachingActionKind.LEARNING_SETTINGS),
                            home=_button(HOME, TeachingActionKind.HOME))


def guide_unavailable_model(item: dict) -> GuideUnavailableModel:
    """The model of 引导例题暂不可用 for the lesson ``item``."""
    return GuideUnavailableModel(
        page=WAIT_PAGE, title=GUIDE_UNAVAILABLE_TITLE, item_title=item.get("title", ""),
        title_role="study" if item.get("family") == "word" else "ui",
        text=GUIDE_UNAVAILABLE_TEXT, note=GUIDE_UNAVAILABLE_NOTE,
        practice=_button(START_PRACTICE, TeachingActionKind.START_PRACTICE),
        skip=_button(SKIP_ITEM, TeachingActionKind.SKIP_ITEM),
        home=_button(HOME, TeachingActionKind.LEAVE))


def familiarity_model(item: dict, ability: str) -> FamiliarityModel:
    """The model of 开始前 asking about ``ability`` of the lesson ``item``."""
    labels = {"understanding": UNDERSTANDING_WORD if item["family"] == "word" else UNDERSTANDING_OTHER,
              **ABILITY_LABELS}
    return FamiliarityModel(
        page=FAMILIARITY_PAGE, title=FAMILIARITY_TITLE, item_title=item["title"],
        title_role="study" if item["family"] == "word" else "ui", ability=labels.get(ability, ability),
        levels=tuple(_button(caption, TeachingActionKind.FAMILIARITY, level=level)
                     for caption, level in FAMILIARITY_LEVELS))
