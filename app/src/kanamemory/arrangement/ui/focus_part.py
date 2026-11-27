"""学习重点, the third part of the orientation screen (design 12 UI6, H1): a page part under design 13 C4.

Input: display.FocusModel and the orientation picked on the screen; output:
one box of widgets and, on request, the Focus the learner picked. Display
state only: the three selections. Design 12 H8, H9: under the scope,
the pair 先做范围内 / 只做范围内 (each with its sentence; the stored one says
当前使用, the other's button stores it at once through ``on_only(bool)``), and
the section 暂不复习 (each paused scope with 恢复 -> ``on_restore(scope)``,
添加… -> ``on_pause_add()``); without those callbacks they are not drawn.
The tilt and kana choices, and the third group 课程里的词汇与语法 (the
line tilt, design 12 LS4), are shown only while the picked orientation offers
them (FocusModel.offers_tilts), else the model's note; hidden choices keep their values, so the stored tilts survive
a confirm under thorough. Nothing is written here: the composing page hands
picked() to its confirm callback and the wiring applies it through the focus
service, which does nothing when it equals the stored focus.
"""
from __future__ import annotations

import kanamemory.ui_components as u
from ..display import FocusModel
from ..model import Focus, Orientation


class FocusPart:
    """The tilts (breadth only), the scope with the current one named, and UI6's two sentences."""

    def __init__(self, model: FocusModel, mode: Orientation, *, on_only=None, on_restore=None, on_pause_add=None):
        self._model = model
        self._on_only, self._on_restore, self._on_pause_add = on_only, on_restore, on_pause_add
        self._tilts = {option.text: option.tilt for option in model.tilts}
        self._kana = {option.text: option.kana for option in model.kana_tilts}
        self._lines = {option.text: option.lines for option in model.line_tilts}
        self._scopes = {option.title: option.scope for option in model.scopes}
        tilt = next(option.text for option in model.tilts if option.tilt is model.focus.tilt)
        kana = next(option.text for option in model.kana_tilts if option.kana is model.focus.kana)
        scope = next((option.title for option in model.scopes if option.scope == model.focus.scope),
                     model.scopes[0].title)
        self.tilt = u.selection(items=list(self._tilts), value=tilt)
        self.kana = u.selection(items=list(self._kana), value=kana)
        lines = next((option.text for option in model.line_tilts if option.lines is model.focus.lines), None)
        self.lines = u.selection(items=list(self._lines), value=lines) if self._lines else None
        self.scope = u.selection(items=list(self._scopes), value=scope)
        self._tilt_box = u.col()
        self.follow(mode)

    def follow(self, mode: Orientation) -> None:
        """Show the tilt choices for an orientation that offers them, the model's note otherwise."""
        self._tilt_box.clear()
        if self._model.offers_tilts(mode):
            self._tilt_box.add(u.label(self._model.tilt_heading, size=15, bold=True), self.tilt,
                               u.label(self._model.kana_heading, size=15, bold=True), self.kana,
                               u.label(self._model.kana_note, size=13, color=u.MUTED))
            if self.lines is not None:
                self._tilt_box.add(u.label(self._model.line_heading, size=15, bold=True), self.lines,
                                   u.label(self._model.line_note, size=13, color=u.MUTED))
        else:
            self._tilt_box.add(u.label(self._model.tilt_note, size=13, color=u.MUTED))

    def _drop_scope(self) -> None:
        self.scope.value = self._model.scopes[0].title

    def box(self):
        """One box, so the screen's own two selections stay its only top-level ones."""
        scope_rows = [u.label(self._model.scope_heading, size=15, bold=True),
                      u.label(self._model.scope_line, size=13)]
        if not self._model.scope_resolves:
            scope_rows.append(u.button(self._model.drop_scope, lambda widget: self._drop_scope(), compact=True))
        switch = self._model.only_switch
        if switch is None or self._on_only is None:
            order_rows = [u.label(self._model.scope_text, size=13, color=u.MUTED)]
        else:
            order_rows = self._switch_rows(switch)
        return u.col(u.label(self._model.heading, size=17, bold=True), self._tilt_box, *scope_rows, self.scope,
                     *order_rows, u.label(self._model.change_text, size=13, color=u.MUTED), *self._paused_rows())

    def _switch_rows(self, switch) -> list:
        """The pair 先做范围内 / 只做范围内, each with its sentence: 当前使用 on the stored one, a button on the other."""
        rows = []
        for on, title, text, caption in ((False, switch.first_title, switch.first_text, switch.turn_off),
                                         (True, switch.only_title, switch.only_text, switch.turn_on)):
            parts = [u.label(title, size=14, bold=True), u.label(text, size=13, color=u.MUTED)]
            if switch.on is on:
                parts.append(u.label(switch.in_use, size=13, bold=True, color=u.GREEN))
            else:
                parts.append(u.button(caption, lambda widget, value=on: self._on_only(value), compact=True,
                                      multiline=True))
            rows.append(u.col(*parts, margin_bottom=8, padding=8, background_color=u.ENTRY_BACKGROUNDS[0]))
        return rows

    def _paused_rows(self) -> list:
        """The section 暂不复习: its note, each paused scope with 恢复, 添加… (or the limit's sentence)."""
        paused = self._model.paused
        if paused is None or self._on_restore is None or self._on_pause_add is None:
            return []
        rows = [u.label(paused.heading, size=15, bold=True), u.label(paused.text, size=13, color=u.MUTED)]
        if not paused.rows:
            rows.append(u.label(paused.none_text, size=13))
        for row in paused.rows:
            rows.append(u.row(u.label(row.title, size=14, flex=3, margin_right=8),
                              u.button(row.restore, lambda widget, scope=row.scope: self._on_restore(scope),
                                       compact=True, flex=1)))
        if paused.can_add:
            rows.append(u.button(paused.add, lambda widget: self._on_pause_add(), compact=True))
        elif paused.full_text:
            rows.append(u.label(paused.full_text, size=13, color=u.MUTED))
        return rows

    def picked(self) -> Focus:
        """The focus as the selections stand (a selection without a value keeps the stored part)."""
        stored = self._model.focus
        return Focus(self._tilts.get(self.tilt.value, stored.tilt), self._kana.get(self.kana.value, stored.kana),
                     self._scopes[self.scope.value] if self.scope.value in self._scopes else stored.scope,
                     self._lines.get(self.lines.value, stored.lines) if self.lines is not None else stored.lines)
