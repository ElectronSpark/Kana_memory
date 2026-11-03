"""Word annotations for teaching sentences, retaining exact fragment anchors.

Question prompts must pass their separate anti-leak policy before display;
this module is a teaching renderer, never a way to mark a question safe.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from math import isfinite
import re

from .example_highlight import configure_example_text, _validated_highlights, _validated_links
from .pitch_accent import morae
from .question_support import _prompt_lines, clean_prompt_preferences

_KANA = re.compile(r"[ぁ-ゖァ-ヶー]+\Z")


def _annotation_plan(text):
    from .sentence_annotations import sentence_annotation_plan
    return sentence_annotation_plan(text)


def _display_plan(text, source, preferences):
    from .sentence_annotations import validated_accent_phrases
    settings = clean_prompt_preferences(preferences)
    segments, position = [], 0
    valid = isinstance(source, Mapping) and source.get("text") == text
    raw = source.get("segments") if valid else None
    valid = valid and isinstance(raw, (list, tuple))
    if valid:
        for part in raw:
            if not isinstance(part, Mapping):
                valid = False
                break
            start, end = part.get("start"), part.get("end")
            if (type(start) is not int or type(end) is not int or
                    not position <= start < end <= len(text) or part.get("surface") != text[start:end]):
                valid = False
                break
            if start > position:
                segments.append(dict(start=position, end=start, surface=text[position:start],
                                     reading=None, accent_number=None))
            reading, number = part.get("reading"), part.get("accent_number")
            reading = reading if isinstance(reading, str) and _KANA.fullmatch(reading) else None
            number = (number if reading and type(number) is int and 0 <= number <= len(morae(reading))
                      and part.get("accent_scope") != "phrase_sequence" else None)
            phrases = validated_accent_phrases(dict(part))
            visible_phrases = ([{"reading": p["reading"] if settings["furigana"] else None,
                                 "accent_number": p["accent_number"]} for p in phrases]
                               if settings["number"] else [])
            segments.append({**deepcopy(dict(part)), "reading": reading if settings["furigana"] and reading != text[start:end] else None,
                             "accent_number": number if settings["number"] else None,
                             "accent_phrases": visible_phrases})
            position = end
    if not valid:
        segments, position = [], 0
    if position < len(text):
        segments.append(dict(start=position, end=len(text), surface=text[position:], reading=None, accent_number=None))
    return {"text": text, "segments": segments, "preferences": settings, "aligned": bool(valid),
            "furigana_visible": any(s["reading"] is not None for s in segments),
            "accent_visible": any(s["accent_number"] is not None or s.get("accent_phrases") for s in segments)}


def _cell_lines(text, segments, width, size, scale):
    """Use the tested prompt wrapping rules, preserving paragraph offsets."""
    result, offset = [], 0
    for paragraph in text.split("\n"):
        end = offset + len(paragraph)
        parts = []
        for segment in segments:
            start, stop = max(offset, segment["start"]), min(end, segment["end"])
            if start < stop:
                complete = start == segment["start"] and stop == segment["end"]
                parts.append({"surface": text[start:stop],
                    "reading": segment.get("reading") if complete else None,
                    "accent_number": segment.get("accent_number") if complete else None,
                    "accent_phrases": deepcopy(segment.get("accent_phrases", [])) if complete else []})
        lines = _prompt_lines(parts, width, size, scale)
        if not lines:
            lines = [[dict(surface="", annotation="", width=width, annotation_size=max(11, size * .55))]]
        cursor = offset
        for line in lines:
            cells = []
            for entry in line:
                cell = {**entry, "start": cursor, "end": cursor + len(entry["surface"])}
                cursor = cell["end"]
                # Plain text shares a native label within each visual line.
                # Exact sub-word emphasis and links remain native spans.
                if cells and not cells[-1]["annotation"] and not cell["annotation"]:
                    cells[-1]["surface"] += cell["surface"]
                    cells[-1]["end"] = cell["end"]
                    cells[-1]["width"] += cell["width"]
                else:
                    cells.append(cell)
            result.append(cells)
        offset = end + 1
    return result


def _local_spans(text, spans, start, end, *, links=False):
    local = []
    for span in spans:
        first, last = max(start, span["start"]), min(end, span["end"])
        if first < last:
            entry = {"start": first - start, "end": last - start}
            if links:
                entry["reference_ids"] = list(span["reference_ids"])
            else:
                entry["quote"] = text[first:last]
            local.append(entry)
    return local


def build_sentence_block(app, text, *, highlight_spans=(), links=(), on_select=None,
                         size=20, preferences=None, available_width=None, annotation_exclusions=()):
    """Render kana and a pitch number above each known word, without index UI.

    Links and emphasis belong to the original body text, including overlaps
    already resolved by ``overlap_regions``. Annotation labels are not links.
    Long words keep their requested font size and grow naturally in height.
    """
    from . import ui_components as u
    from .text_layout import font_scale

    if not isinstance(text, str):
        raise TypeError("A teaching sentence must be text")
    highlights = _validated_highlights(text, highlight_spans)
    regions = _validated_links(text, links)
    if regions and not callable(on_select):
        raise ValueError("Linked sentence fragments require a selection callback")
    excluded = _validated_highlights(text, annotation_exclusions)
    source = deepcopy(_annotation_plan(text))
    if isinstance(source, Mapping):
        for segment in source.get('segments', []):
            if any(segment.get('start', len(text)) < span['end'] and segment.get('end', 0) > span['start']
                   for span in excluded):
                segment.update(reading=None, accent_number=None, accent_phrases=[])
    plan = _display_plan(text, source, preferences)
    try:
        scale = float(font_scale())
    except (TypeError, ValueError):
        scale = 1
    if not isfinite(scale) or scale <= 0:
        scale = 1
    if available_width is None:
        try:
            available_width = float(app.main_window.size.width) - 32
        except (AttributeError, TypeError, ValueError):
            available_width = 328
    try:
        available_width = float(available_width)
    except (TypeError, ValueError):
        available_width = 328
    if not isfinite(available_width) or available_width < 1:
        available_width = 328

    def body(value, start, end, **style):
        widget = u.label(value, size=size, text_role="study", wrap=False, **style)
        local_links = _local_spans(text, regions, start, end, links=True)
        configure_example_text(widget, _local_spans(text, highlights, start, end), links=local_links,
            platform=getattr(app, "platform", None) if local_links else None, on_select=on_select)
        widget._sentence_text_range = (start, end)
        return widget

    lines = []
    if not plan["furigana_visible"] and not plan["accent_visible"]:
        result = body(text, 0, len(text))
    else:
        lines = _cell_lines(text, plan["segments"], available_width, size, scale)
        rows = []
        for line in lines:
            cells = []
            for cell in line:
                children = []
                if cell["annotation"]:
                    annotation = u.label(cell["annotation"], size=cell["annotation_size"], color=u.MUTED,
                        text_role="study", text_align="center", width=cell["width"], margin_bottom=0, wrap=False)
                    annotation._sentence_annotation_range = (cell["start"], cell["end"])
                    children.append(annotation)
                children.append(body(cell["surface"], cell["start"], cell["end"], width=cell["width"],
                    text_align="center" if cell["annotation"] else "left", margin_bottom=0))
                cells.append(u.col(*children, width=cell["width"]))
            rows.append(u.row(*cells, align_items="end", margin_bottom=6))
        result = u.col(*rows, margin_bottom=8)
    result._sentence_original_text = text
    result._sentence_annotation_plan = plan
    result._example_highlights = highlights
    result._example_link_regions = regions
    result._example_select = on_select
    result._feedback_fragment = {"kind": "sentence_annotation", "text": text, "plan": deepcopy(plan),
        "highlights": deepcopy(highlights), "links": deepcopy(regions), "lines": deepcopy(lines),
        "font_size": size, "available_width": available_width, "font_scale": scale,
        "annotation_exclusions": deepcopy(excluded)}
    return result
