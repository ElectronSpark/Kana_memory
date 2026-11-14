"""今日学习: every fixed text of the home page and its display model (design 13 S3, C2).

Pure: no Toga, no store, no app object. compose() turns the numbers the home
service read into the full captions (S1 note 4: the page adds no wording).
Design 12's rows keep their one model and their one producer
(ArrangementPresenter.home_model and home_focus, with the arrangement's own
wording): HomeModel carries them as fields, the home overview built around them
rather than a second model beside them.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import NamedTuple

from ..arrangement.display import FocusRowModel, HomeRowsModel
from ..arrangement.model import RouteFacts
from ..home_counts import next_review_text, review_backlog_hint, show_next_review
from .model import CANCEL_FOCUS, OPEN_ORIENTATION, HomeAction, HomeActionKind

# The primary card: the heading, the hint and the primary button.
HEADING_WORK = "接着学"
HEADING_START = "开始今天的学习"
CONTINUE = "继续学习"
START_NEXT = "开始下一小节"
SHOW_ROUTE = "查看学习路线"
GO_ON = "接着已加入的内容往下学。"   # work that no hint line counts
ALL_LEARNED = "内容已完成初学，继续按时复习。"   # after the route's short title
HINT_JOINER = " · "
READY = "现在可答 {count} 题"
TEACHING = "可开始初学 {count} 项"
WAITING = "待到期 {count} 题"
COURSE_GATED = "课程待前置 {count} 项"
MATERIAL_HELD = "材料待核 {count} 题"
INPUT_BLOCKED = "待调整作答方式 {count} 题"
KANA_GATED = "假名待前置 {count} 题"
# The column under the card.
ADD_NEXT = "加入下一小节 · "   # followed by the section title
OTHER_ROUTE = "其他路线："       # followed by the route title, a space and the parts
OTHER_IN_PROGRESS = "进行中 {count} 节"
OTHER_NEXT = "下一小节 "        # followed by the next section's title
OTHER_JOINER = " / "
ROUTE_PAGE = "学习路线"
ADD_CONTENT = "添加内容"
ORPHANS = "{count} 项所属内容库已停用"
WORD_DRILL = "单词拼写"
WORD_DRILL_LEFT = "继续单词拼写（剩 {count} 词）"
# The label under 学习概况. The core route's short title "主线" keeps the pre-route wording (no space after it).
STATS = "{title}{gap}初学完成 {completed}/{total} 节 · 已加入 {added} 节"
STATS_PARTIAL = " · 部分加入 {partial} 节"


@dataclass(frozen=True)
class HomeButton:
    """A home button: its full caption and what it does."""

    caption: str
    action: HomeAction


class OtherRoute(NamedTuple):
    """Another enabled route with work, as the column lists it (a routes_overview row)."""

    route_id: str
    title: str
    in_progress: int          # its sections in progress
    next_title: str | None    # its next section's title; None without a next section


@dataclass(frozen=True)
class HomeModel:
    """The home page of one refresh: the arrangement's rows, the primary card, the column and the stats label.

    The card: ``rows`` (B10's note, the route progress, today's minutes; the
    invite goes to the column), ``focus`` (the focus row, None without a
    focus), ``heading``, ``hint``, ``primary``, ``next_review_line`` ("下次复习
    …", only while nothing is answerable and something waits). The column:
    the invite (``rows``), ``backlog_line``, ``add_next`` (None when B10's note
    already offers that section), ``other_routes`` (at most two), the
    ``route_page`` and ``add_content`` pair, ``orphans`` and ``word_drill``.
    ``note_add``, ``open_orientation`` and ``cancel_focus`` are the actions of
    design 12's rows (B10's 加入下一小节, the invite, today's minutes and the
    focus line, 取消重点). The numbers behind the texts: ``has_work``,
    ``route`` (the current route: required sections completed and in all, the
    next section), ``added_sections``, ``partial_sections``, ``word_count`` and
    ``next_change_at``, the moment the numbers next change by themselves (the
    outer layer's timer), None without one. Strings, numbers and ids only: no
    row of the overview is kept.
    """

    rows: HomeRowsModel
    focus: FocusRowModel | None
    has_work: bool
    heading: str
    hint: str
    primary: HomeButton
    next_review_line: str | None
    backlog_line: str | None
    add_next: HomeButton | None
    other_routes: tuple[HomeButton, ...]
    route_page: HomeButton
    add_content: HomeButton
    orphans: HomeButton | None
    word_drill: HomeButton
    stats_line: str
    route: RouteFacts
    added_sections: int
    partial_sections: int
    word_count: int
    next_change_at: datetime | None
    note_add: HomeAction | None
    open_orientation: HomeAction = OPEN_ORIENTATION
    cancel_focus: HomeAction = CANCEL_FOCUS


def compose(*, rows: HomeRowsModel, focus: FocusRowModel | None, route: RouteFacts, no_new_content: bool,
            route_id: str, route_title: str, core: bool, has_work: bool, ready: int, waiting: int,
            instruction: int, blocked_content: int, blocked_material: int, blocked_input: int | None,
            blocked_kana: int, next_section: tuple[str, str] | None, others: Sequence[OtherRoute], orphans: int,
            libraries_page: bool, word_count: int, completed: int, total: int, added: int, partial: int | None,
            next_change_at: datetime | None) -> HomeModel:
    """The home page from the numbers one refresh read (HomeOverviewService.overview).

    ``route_id``: the current route (CORE_ROUTE_ID for the core route), ``core``
    whether it is the core route, ``route_title`` its short title;
    ``next_section``: (id, title) of its next section, None without one;
    ``others``: the other routes to list; ``orphans``: enrolled content of a
    disabled library, listed only when ``libraries_page``; ``completed``,
    ``total``, ``added`` and ``partial``: the overview's required sections.
    """
    lines = []
    if ready:
        lines.append(READY.format(count=ready))
    if instruction:
        lines.append(TEACHING.format(count=instruction))
    if waiting:
        lines.append(WAITING.format(count=waiting))
    if blocked_content:
        lines.append(COURSE_GATED.format(count=blocked_content))
    if blocked_material:
        lines.append(MATERIAL_HELD.format(count=blocked_material))
    if blocked_input:
        lines.append(INPUT_BLOCKED.format(count=blocked_input))
    if blocked_kana:
        lines.append(KANA_GATED.format(count=blocked_kana))
    if has_work:
        caption, hint, then = CONTINUE, HINT_JOINER.join(lines) or GO_ON, HomeAction(HomeActionKind.CONTINUE)
    elif next_section:
        caption, hint = START_NEXT, next_section[1]
        then = HomeAction(HomeActionKind.ADD_SECTIONS, section_id=next_section[0], route_id=route_id)
    else:
        caption, hint, then = SHOW_ROUTE, route_title + ALL_LEARNED, HomeAction(HomeActionKind.ROUTE_PAGE)
    primary = HomeButton(caption, HomeAction(HomeActionKind.PRIMARY, route_id=route_id, then=then, route=route,
                                             no_new_content=no_new_content))
    beside_work = next_section if has_work else None
    # With work in progress the section is enrolled and queued behind due reviews, not taught at once (review
    # 2026-11-08 2.4); B10's note may already offer the same section first in the card: not twice.
    add_next = (HomeButton(ADD_NEXT + beside_work[1], HomeAction(HomeActionKind.ADD_SECTIONS, section_id=beside_work[0],
                                                                 route_id=route_id))
                if beside_work and (rows.note is None or rows.note.section_id != beside_work[0]) else None)
    listed = []
    for other in others:
        parts = (([OTHER_IN_PROGRESS.format(count=other.in_progress)] if other.in_progress else [])
                 + ([OTHER_NEXT + other.next_title] if other.next_title is not None else []))
        listed.append(HomeButton(OTHER_ROUTE + other.title + " " + OTHER_JOINER.join(parts),
                                 HomeAction(HomeActionKind.OTHER_ROUTE, route_id=other.route_id)))
    stats = STATS.format(title=route_title, gap="" if core else " ", completed=completed, total=total, added=added)
    note = rows.note
    return HomeModel(
        rows=rows, focus=focus, has_work=has_work,
        heading=HEADING_WORK if has_work else HEADING_START, hint=hint, primary=primary,
        next_review_line=next_review_text(next_change_at) if show_next_review(ready, waiting, next_change_at) else None,
        # Design 11 V2: a large review backlog earns a hint beside the next section, never a block.
        backlog_line=review_backlog_hint(ready) if beside_work else None,
        add_next=add_next, other_routes=tuple(listed),
        route_page=HomeButton(ROUTE_PAGE, HomeAction(HomeActionKind.ROUTE_PAGE)),
        add_content=HomeButton(ADD_CONTENT, HomeAction(HomeActionKind.ADD_CONTENT)),
        orphans=(HomeButton(ORPHANS.format(count=orphans), HomeAction(HomeActionKind.LIBRARIES))
                 if orphans and libraries_page else None),
        word_drill=HomeButton(WORD_DRILL_LEFT.format(count=word_count) if word_count else WORD_DRILL,
                              HomeAction(HomeActionKind.WORD_DRILL)),
        stats_line=stats + (STATS_PARTIAL.format(partial=partial) if partial else ""),
        route=route, added_sections=added, partial_sections=partial or 0, word_count=word_count,
        next_change_at=next_change_at,
        # B10's note names the focus scope's route when its section lies there (H6), else none: the current route.
        note_add=(HomeAction(HomeActionKind.ADD_SECTIONS, section_id=note.section_id, route_id=note.route_id or route_id)
                  if note is not None else None))
