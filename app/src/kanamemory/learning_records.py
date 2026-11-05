"""Ability-level learning records, using the app's native wrapping widgets."""
from __future__ import annotations

from datetime import datetime

from .domain import _utc
from .learning_questions import DEFAULT_KINDS
from .learning_store import _ability_due, _latest_due

ABILITY_LABELS = {"understanding": "理解", "reading": "读法", "use": "提取与使用",
                  "listening": "听辨理解", "application": "应用"}
RATING_LABELS = {1: "再学一次", 2: "有些困难", 3: "记住了", 4: "很轻松"}
STAGE_LABELS = {"intro": "初次学习", "guide": "引导练习", "bridge": "待独立练习",
                "check": "学习中", "done": "已加入复习"}
PAGE_SIZE = 12


def learning_records_snapshot(learning, *, offset=0, limit=PAGE_SIZE, now=None):
    """Counts refer to content; per-ability review details are subordinate."""
    if type(offset) is not int or offset < 0 or type(limit) is not int or limit < 1:
        raise ValueError("Invalid learning-record page")
    now = _utc(now)
    queries = learning.store.queries
    total = queries.course_count()
    offset = min(offset, max(0, ((total - 1) // limit) * limit)) if total else 0
    reviewed = queries.reviewed_course_count()
    preferences = learning.store.get_setting("learning_preferences_v1", {})
    enabled = set(preferences.get("enabled", DEFAULT_KINDS)) if isinstance(preferences, dict) else set()
    # Use the same queued opportunities as the home screen. Counting the
    # underlying FSRS cards here would miss earlier free-retrieval probes and
    # would include disabled, paused or answer-feedback cooling opportunities.
    ready_content = set()
    dependency_states = {}
    def dependency_state(cid):
        if cid not in dependency_states:
            dependency_states[cid] = learning.prerequisite_status(cid)
        return dependency_states[cid]
    for pending in learning.iter_pending_questions(enabled):
        if dependency_state(pending["content_id"])["blocked"]:
            continue
        content = queries.course_row(pending["content_id"])
        if content["stage"] in {"intro", "guide"}:
            continue
        if content["stage"] == "bridge" and not learning._retrieval_ready(content, now):
            continue
        question = learning.get_question(pending["question_id"])
        effective_due = _latest_due(_ability_due(pending, question), pending["not_before_utc"])
        if question["result"] is not None or not effective_due or datetime.fromisoformat(effective_due) <= now:
            ready_content.add(pending["content_id"])
    due = len(ready_content)
    items = []
    for content_id in queries.course_ids_page(limit, offset):
        item = learning.get_item(content_id)
        item["dependency_status"] = dependency_state(content_id)
        content = queries.course_row(content_id)
        for unit in item["units"]:
            event = queries.latest_primary_rating(unit["unit_id"])
            unit["latest_rating"] = dict(event) if event is not None else None
            pending = queries.first_pending_question_id(unit["unit_id"])
            question = learning.get_question(pending) if pending else None
            unit["question_enabled"] = bool(question and question["kind"] in enabled)
            unit["has_pending"] = question is not None
            unit["awaiting_rating"] = bool(question and question["result"] is not None)
            unit["prerequisite_blocked"] = item["dependency_status"]["blocked"]
            effective_due = _latest_due(_ability_due(unit, question), question["not_before_utc"]) if question else None
            if content["stage"] == "bridge" and not learning._retrieval_ready(content, now):
                effective_due = _latest_due(effective_due, content["retrieval_at_utc"])
            unit["practice_due_at_utc"] = effective_due
            unit["due"] = bool(question and unit["question_enabled"] and not unit["suspended"]
                and not unit["prerequisite_blocked"]
                and content["stage"] not in {"intro", "guide"}
                and (unit["awaiting_rating"] or not effective_due or datetime.fromisoformat(effective_due) <= now))
        items.append(item)
    return {"items": items, "total": total, "reviewed": reviewed, "due": due,
            "offset": offset, "limit": limit, "has_previous": offset > 0, "has_next": offset + limit < total}


def _ui():
    from . import ui_components as app
    return app


def build_learning_records(app):
    """Return a live Box; host it on the `learning_records` application page."""
    u = _ui()
    panel = u.col()
    app._learning_records_panel = panel

    def active():
        return getattr(app, "page", None) == "learning_records" and getattr(app, "_learning_records_panel", None) is panel

    def date_label(value):
        return datetime.fromisoformat(value).astimezone(app.store.zone).strftime("%m月%d日 %H:%M:%S")

    def change(content_id, ability, paused):
        if not active():
            return
        app.learning_store.set_suspended(content_id, ability, paused)
        render()

    def move(offset):
        if not active():
            return
        app._learning_records_offset = offset
        render()

    def render():
        result = learning_records_snapshot(app.learning_store, offset=getattr(app, "_learning_records_offset", 0))
        app._learning_records_offset = result["offset"]
        panel.clear()
        panel.add(u.label(f"已加入 {result['total']} 项 · 已练 {result['reviewed']} 项 · 现在可练 {result['due']} 项", size=15))
        from .practice_archive import archived_attempts
        observations = archived_attempts(app.store)
        if observations:
            history = u.col()
            def toggle_history(widget=None, **kwargs):
                if not active():
                    return
                if history.children:
                    history.clear()
                    return
                history.add(u.label("已保存作答，未选择复习评分；不自动修改到期时间。", size=12, color=u.MUTED))
                for entry in observations:
                    question = entry["question"]
                    title = question.get("prompt") or question.get("title") or entry["content_id"]
                    history.add(u.label(str(title), bold=True),
                        u.label(entry["answered_text"], text_role="study"),
                        u.label(date_label(entry["at"]), size=12, color=u.MUTED))
            panel.add(u.button("未评级作答 · 最近记录", toggle_history, compact=True), history)
        if not result["items"]:
            panel.add(u.label("还没有加入课程学习。", color=u.MUTED))
            return
        for item in result["items"]:
            panel.add(u.label(item.get("title", item["id"]), size=20, bold=True, color=u.GREEN),
                      u.label(("读音规则" if item.get("domain") == "pronunciation" else "单词" if item["family"] == "word" else "语法") + " · " + STAGE_LABELS[item["stage"]], size=12, color=u.MUTED))
            for unit in item["units"]:
                label = ABILITY_LABELS[unit["ability"]]
                status = ("已暂停" if unit["suspended"] else "题型未启用" if unit["has_pending"] and not unit["question_enabled"]
                    else "待前置" if unit["prerequisite_blocked"]
                    else "待评分" if unit["awaiting_rating"] else "现在可练" if unit["due"]
                    else "待介绍" if unit["has_pending"] and item["stage"] == "intro"
                    else "引导中" if unit["has_pending"] and item["stage"] == "guide"
                    else "待练" if unit["has_pending"] else "暂未安排" if unit["review_count"] else "未练")
                pause = not bool(unit["suspended"])
                panel.add(u.row(u.label(label + " · " + status, bold=True, flex=1),
                    u.button("暂停" if pause else "恢复", lambda w, cid=item["id"], a=unit["ability"], p=pause: change(cid, a, p), compact=True)))
                if unit["practice_due_at_utc"] and not unit["awaiting_rating"]:
                    panel.add(u.label(("原待练：" if unit["suspended"] else "待练：") + date_label(unit["practice_due_at_utc"]), size=13, color=u.MUTED))
                elif unit["due_at_utc"] and not unit["has_pending"]:
                    panel.add(u.label("复习时间：" + date_label(unit["due_at_utc"]), size=13, color=u.MUTED))
                if unit["latest_rating"]:
                    event = unit["latest_rating"]
                    panel.add(u.label("最近：" + RATING_LABELS[event["rating"]] + " · " + date_label(event["occurred_at_utc"]), size=13, color=u.MUTED))
        if result["total"] > result["limit"]:
            previous = u.button("上一页", lambda w: move(result["offset"] - result["limit"]), compact=True, flex=1)
            following = u.button("下一页", lambda w: move(result["offset"] + result["limit"]), compact=True, flex=1)
            previous.enabled, following.enabled = result["has_previous"], result["has_next"]
            panel.add(u.label(f"{result['offset'] + 1}–{min(result['offset'] + result['limit'], result['total'])} / {result['total']} 项", size=12, color=u.MUTED),
                      u.row(previous, following))
    render()
    return panel
