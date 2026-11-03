"""Native tree checkboxes with separate selection and enrollment states."""


def tree_checkbox(u, on_change):
    factory = u.toga.Switch
    if u.current_platform == "android":
        from org.example.kanamemory import KanaTreeCheckBox
        from toga_android.widgets.switch import Switch, OnCheckedChangeListener

        class AndroidTreeSwitch(Switch):
            def create(self):
                self.native = KanaTreeCheckBox(self._native_activity)
                self.native.setOnCheckedChangeListener(OnCheckedChangeListener(self))
                self.cache_textview_defaults()

        class TreeSwitch(u.toga.Switch):
            def _create(self):
                return AndroidTreeSwitch(interface=self)

        factory = TreeSwitch
    return factory("", on_change=on_change,
                   style=u.Pack(width=48, height=48, margin_right=6, margin_bottom=0))


def update_tree_checkbox(widget, u, state, counts, title):
    selectable = bool(counts["new_count"])
    checked_count = counts["added_count"] + counts["selected_count"]
    # Disabled enrolled leaves are still checked. A parent with both enrolled
    # and unselected leaves therefore keeps the normal tree's mixed indicator.
    visual = ("all" if checked_count == counts["total_count"] else "partial" if checked_count else "none") if selectable else (
        "added" if counts["total_count"] else "empty")
    widget._study_selection_state = visual
    # Native checked-change callbacks also fire for programmatic updates.
    # Never let a repaint select content or reverse a parent toggle.
    handler = getattr(widget.on_change, "_raw", widget.on_change)
    widget.on_change = None
    try:
        widget.value = visual in {"all", "added"}
        widget.enabled = selectable
        if u.current_platform == "android":
            native = widget._impl.native
            native.setMixed(visual == "partial")
            status = {"none": "未选择", "partial": "部分已加入" if state == "none" else "部分已选", "all": "已选",
                      "added": "已加入", "empty": "无内容"}[visual]
            native.setContentDescription(status + "，" + title)
        elif u.current_platform == "windows" and getattr(widget, "_impl", None) is not None:
            import System.Windows.Forms as WinForms
            native = widget._impl.native
            native.ThreeState = True
            native.CheckState = (WinForms.CheckState.Indeterminate if visual == "partial" else
                                 WinForms.CheckState.Checked if widget.value else WinForms.CheckState.Unchecked)
    finally:
        widget.on_change = handler
