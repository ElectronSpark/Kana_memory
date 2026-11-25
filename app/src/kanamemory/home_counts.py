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


def record_due_text(kana_due_chars: int, course_due: int | None = None) -> str:
    """Records page due line; course_due None when no course store is attached."""
    text = f"假名到期 {kana_due_chars} 字"
    return text if course_due is None else text + f" · 课程到期 {course_due} 题"
