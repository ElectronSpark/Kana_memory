"""Width-aware, multiline Android Selection labels without changing their values."""
from decimal import ROUND_UP
from types import MethodType
import weakref

from toga.platform import current_platform
from travertino.size import at_least


def configure_selection(widget):
    """Preserve values/source semantics with multiline text and safe callbacks.

    The widget must use ``text_layout.text_style`` so every layout pass supplies
    its allocated width through ``_measure_text_width`` before height allocation.
    """
    if current_platform != "android":
        return widget
    impl = widget._impl
    if getattr(impl, "_kana_multiline_selection", False):
        return widget

    from android.view import View
    from android.widget import AdapterView
    from java import dynamic_proxy
    from toga_android.libs.events import PythonRunnable
    from org.example.kanamemory import KanaSelectionAdapter

    native, original_adapter = impl.native, impl.adapter
    selected = native.getSelectedItemPosition()
    adapter = KanaSelectionAdapter(impl._native_activity)
    for index in range(original_adapter.getCount()):
        adapter.add(original_adapter.getItem(index))
    impl.adapter = adapter
    native.setAdapter(adapter)
    if selected >= 0:
        native.setSelection(selected)
    impl._kana_multiline_selection = True
    impl._selection_width = None
    impl._selection_popup_width = None

    def measure(owner, width=None):
        if width is not None:
            owner._selection_width = width
        # Initial refresh happens before Pack knows this control's allocation.
        # Screen width is only a fallback; every actual layout replaces it.
        pixels = (owner._native_activity.getResources().getDisplayMetrics().widthPixels
                  if owner._selection_width is None else owner.scale_in(owner._selection_width))
        pixels = max(1, int(pixels))
        content_width = max(1, pixels - native.getPaddingLeft() - native.getPaddingRight())
        adapter.setContentWidth(content_width)
        if owner._selection_popup_width != content_width:
            native.setDropDownWidth(content_width)
            owner._selection_popup_width = content_width
        native.measure(View.MeasureSpec.makeMeasureSpec(pixels, View.MeasureSpec.EXACTLY),
                       View.MeasureSpec.UNSPECIFIED)
        owner.interface.intrinsic.width = at_least(0)
        owner.interface.intrinsic.height = max(48, owner.scale_out(native.getMeasuredHeight(), ROUND_UP))

    impl._measure_text_width = MethodType(measure, impl)
    impl.rehint = MethodType(measure, impl)

    original_on_change = impl.on_change

    def on_change(owner, index):
        previous = owner.last_selection
        original_on_change(index)
        if index != previous:
            owner.interface.refresh()

    impl.on_change = MethodType(on_change, impl)

    class SelectionListener(dynamic_proxy(AdapterView.OnItemSelectedListener)):
        def __init__(self, owner):
            super().__init__()
            self.owner = weakref.proxy(owner)

        def deliver(self, index):
            try:
                owner = self.owner
                selected = owner.native.getSelectedItemPosition()
                selected = selected if selected >= 0 else None
                # Programmatic changes or source edits may have superseded this
                # native notification while it waited for the UI queue.
                if selected == index:
                    owner.on_change(index)
            except ReferenceError:
                pass  # The page/widget was disposed before the queued callback.

        def post(self, index):
            try:
                # Finish Spinner's native selection/dismissal before an app
                # callback rebuilds the form and measures the popup's adapter.
                # Programmatic select_item still invokes on_change immediately;
                # Toga's last_selection suppresses its later native duplicate.
                self.owner.native.post(PythonRunnable(lambda: self.deliver(index)))
            except ReferenceError:
                pass

        def onItemSelected(self, parent, view, position, item_id):
            self.post(position)

        def onNothingSelected(self, parent):
            self.post(None)

    impl._kana_selection_listener = SelectionListener(impl)
    native.setOnItemSelectedListener(impl._kana_selection_listener)

    # Item text can change without changing the selection. Preserve the original
    # source implementations (including their event ordering) and remeasure after.
    for name in ("source_insert", "source_change", "source_remove", "source_clear"):
        original = getattr(impl, name)

        def update(owner, *args, _original=original, **kwargs):
            result = _original(*args, **kwargs)
            owner.interface.refresh()
            return result

        setattr(impl, name, MethodType(update, impl))

    widget.refresh()
    return widget
