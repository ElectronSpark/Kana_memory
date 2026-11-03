"""Semantic fonts: interface copy and explicitly identified Japanese content.

Shared Chinese/Japanese characters are never used to infer a text's language.
These are presentation roles only; they do not enter stored learning content.
"""
from toga.fonts import SANS_SERIF, SERIF
from toga.platform import current_platform


def _resolve_family(text_role='ui', override=None):
    if text_role not in {'ui', 'study'}:
        raise ValueError('Unknown text role: ' + str(text_role))
    return override or (SERIF if text_role == 'study' else SANS_SERIF)


font_family = _resolve_family


def configure_native_text(native, text_role='ui', font_family=None):
    """Preserve weight/style while selecting Android's Japanese serif fallback."""
    from android.graphics import Typeface
    from java.util import Locale

    family = _resolve_family(text_role, font_family)
    previous = native.getTypeface()
    style = previous.getStyle() if previous is not None else Typeface.NORMAL
    native.setTypeface(Typeface.create(family, style))
    if text_role == 'study':
        native.setTextLocale(Locale.JAPAN)
    return native


def configure_text_role(widget, text_role='ui', font_family=None):
    family = _resolve_family(text_role, font_family)
    widget._text_role = text_role
    widget.style.font_family = family
    if current_platform == 'android':
        configure_native_text(widget._impl.native, text_role, family)
    refresh = getattr(widget, 'refresh', None)
    if refresh is not None:
        refresh()
    return widget


def configure_study_ranges(widget, ranges):
    """Style explicit Japanese ranges without replacing text or kana links."""
    text = str(widget.text)
    ranges = tuple(ranges)
    if any(not (0 <= start < end <= len(text)) for start, end in ranges):
        raise ValueError('Study range outside displayed text')
    widget._study_ranges = ranges
    if current_platform != 'android' or not ranges:
        return widget
    from android.text import SpannableString, Spanned
    from android.text.style import TypefaceSpan, LocaleSpan
    from java.util import Locale
    native = widget._impl.native
    styled = SpannableString(native.getText())
    for start, end in ranges:
        # Android span offsets count UTF-16 units, not Python code points.
        first = len(text[:start].encode('utf-16-le')) // 2
        last = len(text[:end].encode('utf-16-le')) // 2
        styled.setSpan(TypefaceSpan('serif'), first, last, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
        styled.setSpan(LocaleSpan(Locale.JAPAN), first, last, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
    native.setText(styled)
    widget.refresh()
    return widget
