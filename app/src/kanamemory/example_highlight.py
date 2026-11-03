"""Exact example emphasis and ambiguity-preserving fragment links.

Stored offsets count Unicode code points. Only the Android presentation layer
converts them to UTF-16; neither punctuation nor typography rewrites the text.
"""
from __future__ import annotations

from collections.abc import Mapping

from toga.platform import current_platform


EMPHASIS_COLOR = "#9A3D00"
LINK_COLOR = "#416B60"


def _bounds(span, length=None):
    if not isinstance(span, Mapping):
        raise ValueError("Example spans must be objects")
    start, end = span.get("start"), span.get("end")
    if type(start) is not int or type(end) is not int or not 0 <= start < end:
        raise ValueError("Example spans require nonempty code-point ranges")
    if length is not None and end > length:
        raise ValueError("Example span is outside the displayed text")
    return start, end


def overlap_regions(references):
    """Split linked fragments into disjoint regions without losing any owner.

Nested, coincident and crossing references all remain selectable. Adjacent
regions with identical owners are joined; gaps never become implicit links.
"""
    edges = {}
    for reference in references:
        if not isinstance(reference, Mapping):
            raise ValueError("Example references must be objects")
        identity = reference.get("id")
        if not isinstance(identity, str) or not identity:
            raise ValueError("Example references require stable identifiers")
        for span in reference.get("spans", ()):
            start, end = _bounds(span)
            quote = span.get("quote")
            if not isinstance(quote, str) or len(quote) != end - start:
                raise ValueError("Example span quote does not match its range")
            edges.setdefault(start, []).append((identity, 1))
            edges.setdefault(end, []).append((identity, -1))
    active = {}
    result = []
    previous = None
    for position, changes in sorted(edges.items()):
        if previous is not None and previous < position and active:
            owners = sorted(active)
            if result and result[-1]["end"] == previous and result[-1]["reference_ids"] == owners:
                result[-1]["end"] = position
            else:
                result.append({"start": previous, "end": position, "reference_ids": owners})
        # Counts preserve a reference covering itself through overlapping spans.
        for identity, delta in changes:
            active[identity] = active.get(identity, 0) + delta
            if active[identity] == 0:
                del active[identity]
        previous = position
    return result


def _validated_highlights(text, spans):
    result = []
    for span in spans:
        start, end = _bounds(span, len(text))
        if span.get("quote") != text[start:end]:
            raise ValueError("Example highlight anchor no longer matches the text")
        result.append({"start": start, "end": end, "quote": text[start:end]})
    return tuple(result)


def _validated_links(text, links):
    links = tuple(links)
    for region in links:
        _bounds(region, len(text))
    result = []
    previous_end = 0
    for region in sorted(links, key=lambda value: value["start"]):
        start, end = _bounds(region, len(text))
        owners = region.get("reference_ids")
        if (not isinstance(owners, (list, tuple)) or not owners
                or any(not isinstance(value, str) or not value for value in owners)):
            raise ValueError("Example links require reference identifiers")
        if start < previous_end:
            raise ValueError("Split overlapping references into disjoint regions first")
        result.append({"start": start, "end": end, "reference_ids": list(dict.fromkeys(owners))})
        previous_end = end
    return tuple(result)


def _android_emphasis(widget, highlights):
    from android.graphics import Color, Typeface
    from android.text import SpannableString, Spanned
    from android.text.style import ForegroundColorSpan, StyleSpan

    text = str(widget.text)
    native = widget._impl.native
    # Other linked constructions stay discoverable without competing with the
    # current teaching focus. Explicit ForegroundColorSpan keeps that orange.
    native.setLinkTextColor(Color.parseColor(LINK_COLOR))
    # Copy the actual native text so clickable links and any explicit language
    # spans survive. Remove only emphasis spans previously owned by this helper.
    styled = SpannableString(native.getText())
    for span in getattr(widget, "_example_native_spans", ()):
        styled.removeSpan(span)
    created = []
    for part in highlights:
        start = len(text[:part["start"]].encode("utf-16-le")) // 2
        end = len(text[:part["end"]].encode("utf-16-le")) // 2
        for span in (ForegroundColorSpan(Color.parseColor(EMPHASIS_COLOR)), StyleSpan(Typeface.BOLD)):
            styled.setSpan(span, start, end, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            created.append(span)
    native.setText(styled)
    widget._example_native_spans = tuple(created)
    widget.refresh()


def configure_example_text(widget, highlight_spans, *, links=(), platform=None, on_select=None):
    """Emphasize one reference; optionally select all owners of tapped fragments.

    The caller creates a study-typeface label with wrapping disabled at the
    Python string layer. Android then wraps the original string at native width.
    Desktop views can use metadata to expose equivalent explicit fragment
    controls; this function never changes text, global font, or saved records.
    """
    text = str(widget.text)
    highlights = _validated_highlights(text, highlight_spans)
    regions = _validated_links(text, links)
    if regions and not callable(on_select):
        raise ValueError("Linked example fragments require a selection callback")
    # Finish validation before changing either metadata or a native widget.
    widget._example_highlights = highlights
    widget._example_link_regions = regions
    widget._example_emphasis_color = EMPHASIS_COLOR
    widget._example_link_color = LINK_COLOR
    widget._example_select = on_select
    bridge = getattr(platform, "link_kana_label", None)
    if callable(bridge):
        targets = {str(index): tuple(region["reference_ids"]) for index, region in enumerate(regions)}
        def select(token):
            owners = targets.get(str(token))
            if owners is not None and callable(on_select):
                on_select(list(owners))
        bridge(widget, [{"start": region["start"], "end": region["end"], "kana_id": str(index)}
                        for index, region in enumerate(regions)], select)
    if current_platform == "android":
        _android_emphasis(widget, highlights)
    return widget
