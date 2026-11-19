"""The home page 今日学习 (design 13 S3, C4): a display model and one action callback in, widgets out.

draw_home() fills the primary card (the action slot), the column under it
(the secondary slot) and the stats label from a HomeModel, in the order and
with the sizes, colours and flags the home had before the move. Every control
hands its HomeAction to ``on_action(action, button)`` (button: the pressed
control, None from design 12's rows, whose callbacks take none); the outer
layer (kanamemory.home_runtime.run_action) makes the call. Design 12's rows are
drawn by arrangement.ui.home_rows, unchanged. All widgets are made before the
two slots are cleared, so a failure leaves the drawn home as it was (design 13
S3, F1). The page keeps no state, sees no app object, store or service, and
decides nothing the model does not carry.
"""
from __future__ import annotations

from collections.abc import Callable

import kanamemory.ui_components as u
from kanamemory.arrangement.ui.home_rows import focus_rows, home_rows, invite_rows
from ..display import HomeModel
from ..model import HomeAction, choose_orientation

OnAction = Callable[[HomeAction, object], object]


def draw_home(model: HomeModel, *, action_slot, secondary_slot, stats_label, on_action: OnAction) -> None:
    """Draw ``model`` into the two slots (replacing what they hold) and set the stats label."""
    def press(action: HomeAction):
        return lambda widget: on_action(action, widget)

    def open_orientation():
        return on_action(model.open_orientation, None)

    # The never-chosen card first, so it cannot be overlooked (design 12 UI5, 2026-11-18).
    card = invite_rows(model.rows, on_open_orientation=open_orientation,
                       on_choose=lambda mode: on_action(choose_orientation(mode), None))
    # home_rows hands B10's button the note's section id, which model.note_add already carries.
    card += home_rows(model.rows, on_open_orientation=open_orientation,
                      on_add_next=lambda section_id: on_action(model.note_add, None))
    card += focus_rows(model.focus, on_open_orientation=open_orientation,
                       on_cancel=lambda: on_action(model.cancel_focus, None))
    column = []
    card += [u.label(model.heading, size=22, bold=True, color=u.GREEN),
             u.label(model.hint, size=14, color=u.MUTED),
             u.button(model.primary.caption, press(model.primary.action), primary=True, multiline=True)]
    if model.next_review_line is not None:
        card.append(u.label(model.next_review_line, size=12, color=u.MUTED))
    if model.backlog_line is not None:
        column.append(u.label(model.backlog_line, size=12, color=u.MUTED))
    if model.add_next is not None:
        column.append(u.button(model.add_next.caption, press(model.add_next.action), multiline=True))
    for other in model.other_routes:
        column.append(u.button(other.caption, press(other.action), multiline=True))
    column.append(u.row(u.button(model.route_page.caption, press(model.route_page.action), flex=1, margin_right=8),
                        u.button(model.add_content.caption, press(model.add_content.action), flex=1)))
    if model.orphans is not None:
        column.append(u.button(model.orphans.caption, press(model.orphans.action), multiline=True))
    column.append(u.button(model.word_drill.caption, press(model.word_drill.action), multiline=True))
    action_slot.clear()
    secondary_slot.clear()
    action_slot.add(*card)
    secondary_slot.add(*column)
    stats_label.text = model.stats_line
