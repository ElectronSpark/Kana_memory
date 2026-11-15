"""The six pages the kana practice draws itself on the way to a question: every fixed text and the display
models (design 13).

Pure: no Toga, no store, no app object, no clock of its own. The rows that shared builders make from the app (a
note, the line of kana symbols, the route's next step, extra practice) are not part of these models: the outer
layer builds them and hands them to the page.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from ..next_step.display import HOME
from .model import KanaAction, KanaActionKind, setup_kind

DONE_PAGE = "session_done"
WAIT_PAGE = "session_wait"
PREREQ_PAGE = "dependency_wait"
SETUP = "练习设置"
# 本组单词完成
WORD_DONE_TITLE = "本组单词完成"
WORD_DONE_RESULT = "完成 {answered} 词 · 答对 {right} 词"
WORD_DONE_SAVED = "拼写记录已保存；符合条件的假名复习时间已调整。"
WORD_AGAIN = "再练一组单词"
# 本批完成
BATCH_DONE_TITLE = "本批完成"
BATCH_ITEMS = "练习 {items} 个假名记忆项 · "
BATCH_RESULT = "完成 {answered} 题\n答对 {right} 题"
BATCH_SKIPPED = "\n跳过 {skipped} 题（保留原有进度）"
RECORDS = "查看复习记录"
# 题目内容已更新
UPDATED_TITLE = "题目内容已更新"
UPDATED_KEPT = "原题和草稿已保存。更新后继续使用原有学习记录。"
USE_NEW = "使用更新后的题目"
# 题型已暂停
PAUSED_TITLE = "题型已暂停"
PAUSED_KEPT = "未完成的题目和草稿已保留。开启题型后可继续。"
# 先完成前置假名 / 先学所需规则
PREREQ_KANA_TITLE = "先完成前置假名"
PREREQ_RULE_TITLE = "先学所需规则"
PREREQ_KEPT = "原题与进度已保留。"
PREREQ_KANA_HINT = "请检查这些假名是否已暂停，或相应题型是否可用。"
PREREQ_RULE_HINT = "先看规则讲解，再继续练习。"
SYMBOL_SEPARATOR = " · "
CHECK = "检查并继续"
# 本轮先歇一会儿
REVIEW_WAIT_TITLE = "本轮先歇一会儿"
REVIEW_WAIT_DUE = "下一题可在 {due} 开始"
REVIEW_WAIT_SAVED = "已保存 {waiting} 道后续复习题，到期后自动继续。也可以先回到今日。"
CHECK_WAIT = "检查并继续复习"
WAIT_OVER = "已到期，可以继续复习。"
WAIT_LEFT = "距离下一题还有 "
WAIT_CHECKED = "已检查：尚未到期，还需 "
TIME_OF_DAY = "%H:%M:%S"
DAY_AND_TIME = "%m月%d日 %H:%M:%S"


@dataclass(frozen=True)
class KanaButton:
    """A button of the practice line's own pages: its caption and what it does."""

    caption: str
    action: KanaAction


@dataclass(frozen=True)
class WordDoneModel:
    """The page 本组单词完成."""

    page: str
    title: str
    result: str         # how many words were done and how many were right
    saved: str          # the note under it
    again: KanaButton   # 再练一组单词
    home: KanaButton


@dataclass(frozen=True)
class BatchDoneModel:
    """The page 本批完成."""

    page: str
    title: str
    summary: str        # the items, the questions, the right answers, and what was skipped or practised
    records: KanaButton
    home: KanaButton


@dataclass(frozen=True)
class ContentUpdatedModel:
    """The page 题目内容已更新."""

    page: str
    title: str
    kept: str             # the note
    use_new: KanaButton   # 使用更新后的题目
    setup: KanaButton
    home: KanaButton


@dataclass(frozen=True)
class KindsPausedModel:
    """The page 题型已暂停."""

    page: str
    title: str
    kept: str           # the note
    setup: KanaButton
    home: KanaButton


@dataclass(frozen=True)
class PrerequisiteModel:
    """The page 先完成前置假名, or 先学所需规则 when a rule's lesson is among what is missing."""

    page: str
    title: str
    symbols: str                    # the kana waited for, each once, in their order
    rules: tuple[KanaButton, ...]   # one button per rule whose lesson is offered
    kept: str
    check: KanaButton               # 检查并继续
    setup: KanaButton
    home: KanaButton


@dataclass(frozen=True)
class ReviewWaitModel:
    """The page 本轮先歇一会儿."""

    page: str
    title: str
    status: str         # how long the wait still is
    due: str            # when the next question can start
    saved: str          # the note: how many questions wait
    check: KanaButton   # 检查并继续复习
    home: KanaButton


def _button(caption: str, kind: KanaActionKind) -> KanaButton:
    return KanaButton(caption, KanaAction(kind))


def _home() -> KanaButton:
    return _button(HOME, KanaActionKind.HOME)


def batch_counts(questions, summary) -> tuple[int, int, object]:
    """(items, questions answered, the summary if it is one) at the end of a queue: what the store's summary
    says, else the queue's own distinct items and its length."""
    items = len({getattr(question, "content_id", index) for index, question in enumerate(questions)})
    answered = len(questions)
    if isinstance(summary, dict):
        answered, items = summary["answered"], summary["content_count"]
        return items, answered, summary
    return items, answered, None


def batch_extra(summary, practised_line) -> str:
    """The lines a summary adds under the result: what was skipped, and what was practised (worded by
    ``practised_line``, which is asked only when something was)."""
    extra = ""
    if summary is not None:
        if summary["skipped"]:
            extra = BATCH_SKIPPED.format(skipped=summary["skipped"])
        if summary.get("practised"):
            extra += "\n" + practised_line(summary["practised"])
    return extra


def word_done_model(answered, right) -> WordDoneModel:
    """The model of 本组单词完成."""
    return WordDoneModel(page=DONE_PAGE, title=WORD_DONE_TITLE,
                         result=WORD_DONE_RESULT.format(answered=answered, right=right), saved=WORD_DONE_SAVED,
                         again=_button(WORD_AGAIN, KanaActionKind.WORD_SETUP), home=_home())


def batch_done_model(items, answered, right, extra: str) -> BatchDoneModel:
    """The model of 本批完成; ``extra``: the lines batch_extra made."""
    summary = (BATCH_ITEMS.format(items=items) if items else "") + BATCH_RESULT.format(answered=answered, right=right)
    return BatchDoneModel(page=DONE_PAGE, title=BATCH_DONE_TITLE, summary=summary + extra,
                          records=_button(RECORDS, KanaActionKind.RECORDS), home=_home())


def content_updated_model(session_id, retired_id, *, word_lane: bool) -> ContentUpdatedModel:
    """The model of 题目内容已更新 for the retired question ``retired_id`` of ``session_id``."""
    use_new = KanaAction(KanaActionKind.USE_NEW_CONTENT, session_id=session_id, retired_id=retired_id)
    return ContentUpdatedModel(page=WAIT_PAGE, title=UPDATED_TITLE, kept=UPDATED_KEPT,
                               use_new=KanaButton(USE_NEW, use_new), setup=_button(SETUP, setup_kind(word_lane)),
                               home=_home())


def kinds_paused_model(*, word_lane: bool) -> KindsPausedModel:
    """The model of 题型已暂停."""
    return KindsPausedModel(page=WAIT_PAGE, title=PAUSED_TITLE, kept=PAUSED_KEPT,
                            setup=_button(SETUP, setup_kind(word_lane)), home=_home())


def prerequisite_model(symbols, rules) -> PrerequisiteModel:
    """The model of the prerequisite page: ``symbols`` are the kana waited for (repeats are shown once),
    ``rules`` the lessons of the rules waited for (each with a title)."""
    buttons = tuple(KanaButton(rule["title"], KanaAction(KanaActionKind.LEARN_RULE, rule=rule)) for rule in rules)
    return PrerequisiteModel(
        page=PREREQ_PAGE, title=PREREQ_RULE_TITLE if buttons else PREREQ_KANA_TITLE,
        symbols=SYMBOL_SEPARATOR.join(dict.fromkeys(symbols)), rules=buttons,
        kept=PREREQ_KEPT + (PREREQ_RULE_HINT if buttons else PREREQ_KANA_HINT),
        check=_button(CHECK, KanaActionKind.CHECK_PREREQUISITES),
        setup=_button(SETUP, KanaActionKind.PRACTICE_HUB), home=_home())


def wait_status(due, now, checked: bool = False) -> str:
    """How long the review wait still is at ``now`` (sub-minute waits stay visible instead of rounding down to
    a minute); ``checked``: the learner has just asked and it is not over."""
    remaining = max(0, ceil((due - now).total_seconds()))
    if not remaining:
        return WAIT_OVER
    minutes, seconds = divmod(remaining, 60)
    hours, minutes = divmod(minutes, 60)
    duration = ((f"{hours} 小时 " if hours else "")
                + (f"{minutes} 分 " if hours or minutes else "") + f"{seconds} 秒")
    return (WAIT_CHECKED if checked else WAIT_LEFT) + duration + "。"


def wait_due_text(until, today) -> str:
    """When the next question can start: the time of day, with the date when that is not ``today``."""
    return until.strftime(TIME_OF_DAY if until.date() == today else DAY_AND_TIME)


def review_wait_model(status: str, due: str, waiting) -> ReviewWaitModel:
    """The model of 本轮先歇一会儿: ``status`` from wait_status, ``due`` from wait_due_text, ``waiting`` the
    number of questions that wait."""
    return ReviewWaitModel(page=WAIT_PAGE, title=REVIEW_WAIT_TITLE, status=status,
                           due=REVIEW_WAIT_DUE.format(due=due), saved=REVIEW_WAIT_SAVED.format(waiting=waiting),
                           check=_button(CHECK_WAIT, KanaActionKind.CHECK_WAIT), home=_home())
