"""Presentation-only answer signals shared by every practice lane."""
from __future__ import annotations


def feedback_state(result: dict) -> str:
    """Describe this attempt, without inferring mastery or changing scheduling."""
    outcome = result.get("diagnostic_outcome") or result.get("outcome")
    if outcome and outcome not in {"correct", "incorrect", "revealed"}:
        return "ungraded"
    if outcome == "revealed" or result.get("revealed") or result.get("assisted"):
        return "assisted"
    if outcome == "correct" or result.get("correct") is True:
        return "correct"
    if outcome == "incorrect" or result.get("correct") is False:
        return "incorrect"
    return "ungraded"


# The shape and accessible name carry the meaning independently of colour.
SIGNALS = {
    "correct": ("✓", "#176B3A", "#E1F3E6", "回答正确", ""),
    "incorrect": ("×", "#B3261E", "#FCE6E4", "回答错误", ""),
    "assisted": ("↪", "#865800", "#FFF1CF", "已使用提示或查看答案", "已用提示"),
    "ungraded": ("…", "#52616B", "#E8EDF0", "本题未评分", "未评分"),
}


def build_answer_feedback(result: dict):
    from . import ui_components as u

    state = feedback_state(result)
    symbol, foreground, background, accessible_name, caption = SIGNALS[state]
    # A kana spelling can be wrong even when it represents the same sound.
    # This short distinction belongs in the existing signal, not a second
    # large explanation competing with the review controls.
    if not result.get("assisted") and not result.get("revealed"):
        if state in {"incorrect", "ungraded"}:
            caption = {"spelling": "写法有误 · 读音相同", "reading": "读音不符",
                       "mixed": "写法与读音有误"}.get(result.get("error_category"), caption)
        elif state == "correct":
            if result.get("accepted_alternative") or result.get("accepted_spelling"):
                caption = "允许的写法"
            elif result.get("accepted_homophone"):
                caption = "同音答案可接受"
        if caption:
            accessible_name = accessible_name + "，" + caption
    icon = u.label(symbol, size=30, color=foreground, bold=True,
                   text_align="center", margin_bottom=0,
                   **({"width": 52 * u.font_scale()} if caption else {"flex": 1}))
    children = [icon]
    if caption:
        children.append(u.label(caption, size=13, color=foreground, flex=1, margin_bottom=0))
    signal = u.row(*children, align_items="center", background_color=background,
                   padding=4, margin_bottom=4)
    signal._feedback_fragment = {"kind": "answer_feedback", "state": state}
    if u.current_platform == "android":
        # TalkBack needs a worded result even though sighted users only see a
        # symbol. Announce new feedback politely without moving input focus.
        icon._impl.native.setContentDescription(accessible_name)
        icon._impl.native.setAccessibilityLiveRegion(1)
    return signal
