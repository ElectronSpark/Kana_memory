"""One counting rule for the home page, the practice header and the records page.

Review 2026-11-08 sections 4 and 6: the three pages showed different numbers
under similar labels ("可答 36" / "可答 24" / "到期 35"). Callers pass the
numbers and id sets they already read; this module only combines them.
Pure rules: no Toga, platform, network or database.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class HomeCounts:
    """ready_total: all work answerable now (home "现在可答").
    round_remaining: answerable now in the current kana session (practice header).
    kana_due_chars: due kana, one per character (records page).
    course_due: course questions due now, queued or not yet queued.
    waiting / instruction: home "待到期" and "可开始初学".
    """
    ready_total: int = 0
    round_remaining: int = 0
    kana_due_chars: int = 0
    course_due: int = 0
    waiting: int = 0
    instruction: int = 0


def teaching_count(queued_count: int, queued: Iterable[tuple[str, str | None]] = (),
                   unqueued: Iterable[str] = ()) -> int:
    """"可开始初学" counted once per content id over both sources.

    queued_count is the course store's count of queued teaching (one per
    content x instruction scope, after its filters). queued holds the
    (content_id, instruction_scope) keys of the same queued rows before those
    filters; unqueued holds content ids whose teaching has no queued question
    yet. When the filters kept every key the queued side is its distinct
    content ids; otherwise it is the store's count capped at those ids, so
    the result is always in content units (review 2026-11-08 N2).
    An unqueued content id is always counted (its teaching is ready for the
    next round, design 12 N9), also when a queued row of the same content was
    filtered out; a queued content the store counted is taken to be one of
    those ids first, so no content is counted twice.
    """
    pairs = set(queued)
    queued_ids = {content_id for content_id, _ in pairs}
    queued_contents = len(queued_ids) if queued_count == len(pairs) else min(queued_count, len(queued_ids))
    unqueued = set(unqueued)
    return len(unqueued) + max(0, queued_contents - len(unqueued & queued_ids))


def home_counts(*, kana_ready: int = 0, course_ready: int = 0, unqueued_course_ready: int = 0,
                unqueued_kana_ready: int = 0, waiting: Iterable[int] = (), kana_due_chars: int = 0,
                queued_teaching: int = 0, queued_teaching_pairs: Iterable[tuple[str, str | None]] = (),
                unqueued_teaching: Iterable[str] = ()) -> HomeCounts:
    """Combine the per-source counts. A planned round limits only the *_ready
    inputs (the caller passes 0 for work the round cannot admit); waiting is
    always the long-term total."""
    course_due = course_ready + unqueued_course_ready
    return HomeCounts(ready_total=kana_ready + course_due + unqueued_kana_ready,
                      round_remaining=kana_ready, kana_due_chars=kana_due_chars,
                      course_due=course_due, waiting=sum(waiting),
                      instruction=teaching_count(queued_teaching, queued_teaching_pairs, unqueued_teaching))


# Design 11 section 8 (V2): new-item pacing. The course path has no live daily
# new-item limit (sections are added by the learner; StudyStore.daily_new_limit
# is a historical setting kept for backups), so these defaults only set the
# review-backlog hint beside "add the next section". Text only: nothing is
# blocked and no due date or FSRS state is read or changed.
DAILY_NEW_DEFAULT = 10
DAILY_NEW_MAX = 20
BACKLOG_REVIEW_LIMIT = 50
BACKLOG_HINT = "待复习 {count} 题，建议先复习，再加入新内容。"


def backlog_threshold(daily_new: int = DAILY_NEW_DEFAULT) -> int:
    """Due reviews above this suggest pausing new items: 50, or 3x the daily count if lower."""
    daily = min(max(int(daily_new), 1), DAILY_NEW_MAX)
    return min(BACKLOG_REVIEW_LIMIT, 3 * daily)


def review_backlog_hint(due: int, daily_new: int = DAILY_NEW_DEFAULT) -> str | None:
    """The non-blocking hint shown beside "add the next section", or None."""
    return BACKLOG_HINT.format(count=due) if due > backlog_threshold(daily_new) else None


def next_review_text(moment: datetime) -> str:
    """Home line for the earliest future due time (formerly "最近复习")."""
    return "下次复习 " + moment.strftime("%m月%d日 %H:%M")


def show_next_review(ready: int, waiting: int, moment: datetime | None) -> bool:
    """The "下次复习" line only while nothing is answerable now (review
    2026-11-08 N8): beside "现在可答 N 题" it read as "nothing due yet"."""
    return not ready and bool(waiting) and moment is not None


def practice_header(remaining: int, index: int, total: int) -> str:
    """Practice page title: what this session can still offer now, and the position."""
    return f"本轮剩 {remaining} 题 · 第 {min(index + 1, total)} / {total} 题"


# The page behind the home page's 学习记录 (design 02): every part is labelled with what it counts; the
# course's due count is in the 时间与进度 block's 课程复习 line and is not repeated in the kana part.
RECORDS_TITLE = "学习记录"
RECORDS_COURSE_HEADING = "课程"
RECORDS_COURSE_BUTTON = "课程学习记录"
RECORDS_KANA_HEADING = "假名"
RECORDS_KANA_RECENT_HEADING = "最近的假名复习"
RECORDS_KANA_EMPTY = "完成假名练习并选择评级后，这里会出现记录。"


def kana_record_lines(stats: dict) -> str:
    """The kana part's two lines from the kana store's stats(): learned and due (one per character),
    then today's new items and answers. Kana numbers only."""
    learned = stats.get("total_items", stats.get("cards", stats.get("total_cards", 0)))
    due = stats.get("due_items", stats.get("due", stats.get("due_count", 0)))
    new = stats.get("new_items_today", stats.get("new_today", 0))
    answers = stats.get("today_reviews", stats.get("reviews_today", 0))
    return f"已学 {learned} 项 · 到期 {due} 字\n今日新增 {new} 项 · 作答 {answers} 次"
