"""Width-aware Android text measurement for the pinned Toga 0.5.7 backend.

Toga's TextView rehint measures without a width constraint. A wrapped label
therefore expands its parent horizontally and reports only its unwrapped height.
Measure at Pack's allocated width instead, without rewriting text or link spans.
"""
from decimal import ROUND_UP
from types import MethodType

from toga.platform import current_platform
from toga.style import Pack
from toga.style.pack import NONE
from travertino.size import at_least


def font_scale():
    if current_platform != "android":
        return 1.0
    from org.beeware.android import MainActivity
    return float(MainActivity.singletonThis.getResources().getConfiguration().fontScale)


class TextPack(Pack):
    """Let native text wrap to the allocated width and grow vertically.

    As with other flexible Pack content, text in a ROW needs flex or an explicit
    width. It has no unwrapped minimum width that could widen the whole page.
    """

    def _layout_node(self, alloc_width, alloc_height, use_all_width, use_all_height):
        node = self._applicator.node
        measure = getattr(node._impl, "_measure_text_width", None)
        if measure is not None:
            width = self.width if self.width != NONE else max(1, alloc_width - self.margin_left - self.margin_right)
            measure(width)
        super()._layout_node(alloc_width, alloc_height, use_all_width, use_all_height)


# Travertino registers properties per concrete style class. This subclass adds
# layout behavior only, so use the complete Pack property catalog unchanged.
TextPack._PROPERTIES = Pack._PROPERTIES.copy()
TextPack._ALL_PROPERTIES = Pack._ALL_PROPERTIES.copy()


def text_style(**options):
    return (TextPack if current_platform == "android" else Pack)(**options)


def configure_text(widget, minimum_height=0):
    """Configure only this widget; do not patch Toga's global widget factories."""
    if current_platform != "android":
        return widget
    from android.view import View

    impl, native = widget._impl, widget._impl.native
    native.setSingleLine(False)
    native.setHorizontallyScrolling(False)
    native.setMaxLines(2147483647)
    native.setEllipsize(None)
    impl._text_width = None

    def measure(owner, width=None):
        if width is not None:
            owner._text_width = width
        spec = (View.MeasureSpec.UNSPECIFIED if owner._text_width is None else
                View.MeasureSpec.makeMeasureSpec(max(1, owner.scale_in(owner._text_width)), View.MeasureSpec.EXACTLY))
        owner.native.measure(spec, View.MeasureSpec.UNSPECIFIED)
        owner.interface.intrinsic.width = at_least(0)
        owner.interface.intrinsic.height = max(minimum_height, owner.scale_out(owner.native.getMeasuredHeight(), ROUND_UP))

    impl._measure_text_width = MethodType(measure, impl)
    impl.rehint = MethodType(measure, impl)
    widget.refresh()
    return widget
