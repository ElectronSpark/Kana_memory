"""学习重点, the third part of the orientation screen (design 12 UI6, H1): a page part under design 13 C4.

Input: display.FocusModel and the orientation picked on the screen; output:
one box of widgets and, on request, the Focus the learner picked. Display
state only: the three selections. The tilt and kana choices are shown only
while the picked orientation offers them (FocusModel.offers_tilts), else the
model's note; hidden choices keep their values, so the stored tilts survive
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

    def __init__(self, model: FocusModel, mode: Orientation):
        self._model = model
        self._tilts = {option.text: option.tilt for option in model.tilts}
        self._kana = {option.text: option.kana for option in model.kana_tilts}
        self._scopes = {option.title: option.scope for option in model.scopes}
        tilt = next(option.text for option in model.tilts if option.tilt is model.focus.tilt)
        kana = next(option.text for option in model.kana_tilts if option.kana is model.focus.kana)
        scope = next((option.title for option in model.scopes if option.scope == model.focus.scope),
                     model.scopes[0].title)
        self.tilt = u.selection(items=list(self._tilts), value=tilt)
        self.kana = u.selection(items=list(self._kana), value=kana)
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
        return u.col(u.label(self._model.heading, size=17, bold=True), self._tilt_box, *scope_rows, self.scope,
                     u.label(self._model.scope_text, size=13, color=u.MUTED),
                     u.label(self._model.change_text, size=13, color=u.MUTED))

    def picked(self) -> Focus:
        """The focus as the selections stand (a selection without a value keeps the stored part)."""
        stored = self._model.focus
        return Focus(self._tilts.get(self.tilt.value, stored.tilt), self._kana.get(self.kana.value, stored.kana),
                     self._scopes[self.scope.value] if self.scope.value in self._scopes else stored.scope)
