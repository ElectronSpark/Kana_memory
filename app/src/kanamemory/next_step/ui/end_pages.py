"""The three end pages of the continue action (design 13 S4, C4): a display model and one action callback in,
the page's children out.

Each function returns the children in the order and with the sizes and
primary flags the page has; the outer layer hands them to screen() with the
model's page name and title. Rows that shared builders make from the app are
handed in already built and stand where the page has them. Every button hands
its EndAction to ``on_action(action, button)`` (button: the pressed control,
None from B10's note row, whose callback takes none). The pages keep no state,
see no app object, store or service, and decide nothing the model does not
carry.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence

import kanamemory.ui_components as u
from kanamemory.arrangement.ui.section_note import section_note_rows
from ..display import NoKanaKindModel, RoundEndModel, StudyEndModel
from ..model import EndAction

OnAction = Callable[[EndAction, object], object]


def _press(on_action: OnAction, action: EndAction):
    return lambda widget: on_action(action, widget)


def round_end_page(model: RoundEndModel, *, next_content_rows: Sequence, extra_rows: Sequence,
                   on_action: OnAction) -> list:
    """本轮已保存: the text, 开始新一轮, the next content's rows, extra practice's rows, 回到今日."""
    return [u.label(model.text, size=17),
            u.button(model.new_round.caption, _press(on_action, model.new_round.action), primary=True),
            *next_content_rows,
            *extra_rows,
            u.button(model.home.caption, _press(on_action, model.home.action))]


def no_kana_kind_page(model: NoKanaKindModel, *, on_action: OnAction) -> list:
    """暂时没有可进行的假名题型: the text and the four ways out (the practice settings first)."""
    return [u.label(model.text, size=17),
            u.button(model.practice_hub.caption, _press(on_action, model.practice_hub.action), primary=True),
            u.button(model.settings.caption, _press(on_action, model.settings.action)),
            u.button(model.route_page.caption, _press(on_action, model.route_page.action)),
            u.button(model.home.caption, _press(on_action, model.home.action))]


def study_end_page(model: StudyEndModel, *, next_rows: Sequence, extra_rows: Sequence,
                   on_action: OnAction) -> list:
    """本次学习已保存: B10's note first (if any), the text, the route's next rows, extra practice's rows,
    the practice settings and 回到今日."""
    # section_note_rows hands the note's button the note's section id, which model.note_add already carries.
    note = (section_note_rows(model.note, on_add_next=lambda section_id: on_action(model.note_add, None))
            if model.note is not None else [])
    return [*note,
            u.label(model.text, size=17),
            *next_rows,
            *extra_rows,
            u.button(model.practice_hub.caption, _press(on_action, model.practice_hub.action)),
            u.button(model.home.caption, _press(on_action, model.home.action))]
