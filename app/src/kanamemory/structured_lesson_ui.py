"""Accessible native diagrams for introductions, details and explicit hints."""
import re
import unicodedata
from copy import deepcopy

from .structured_lessons import lesson_view, original_sections
from .typography import configure_study_ranges


def lesson_label(u, text, **style):
    # Quotes are an explicit content annotation, not a guess based on shared
    # Han characters. No artificial newlines: Android wraps at the actual size.
    widget = u.label(text, wrap=False, **style)
    spans = [(match.start(1), match.end(1)) for match in re.finditer(r"「([^「」]+)」", text)]
    if spans:
        configure_study_ranges(widget, spans)
    return widget


def unbroken_groups(text):
    """Let a pattern wrap only at its ideographic-space group separators.

    Display only: a word joiner never splits a base character from its combining
    mark, and an over-wide group still wraps where Android has to. The stored
    block text and its feedback fragment stay exact.
    """
    if "　" not in text:
        return text
    groups = []
    for group in text.split("　"):
        joined = ""
        for char in group.replace(" ", " "):
            joined += char if not joined or unicodedata.combining(char) else "⁠" + char
        groups.append(joined)
    return "　".join(groups)


def block_widget(u, block, index=0):
    if block['kind'] == 'prosody':
        from .prosody_cue_ui import cue_widget
        result = cue_widget({'prompt': block['text'], 'prosody_cue': block['prosody_cue']})
        if block.get('caption'):
            result.add(lesson_label(u, block['caption'], size=14, color=u.MUTED))
        result._lesson_block_kind = 'prosody'
        result._feedback_fragment = {'kind': 'teaching_block', 'index': index, 'block': deepcopy(block)}
        return result
    if block['kind'] == 'pitch_sequence':
        from .pitch_practice import fixed_view
        from .pitch_practice_ui import fixed_pitch_diagram
        data = fixed_view({'input_mode': 'pitch_fill', 'pitch_practice': block['pitch_practice']}, reveal=True)
        data['label'] = block['label']
        result = fixed_pitch_diagram(u, **data, show_counts=False)
        if block.get('caption'):
            result.add(lesson_label(u, block['caption'], size=14, color=u.MUTED))
        result._lesson_block_kind = 'pitch_sequence'
        result._feedback_fragment = {'kind': 'teaching_block', 'index': index, 'block': deepcopy(block)}
        return result
    if block['kind'] == 'pitch':
        from .pitch_practice_ui import pitch_diagram
        data = {key: block[key] for key in ('morae', 'levels', 'word_mora_count', 'label', 'number') if key in block}
        result = pitch_diagram(u, **data, show_counts=True)
        if block.get('caption'):
            result.add(lesson_label(u, block['caption'], size=14, color=u.MUTED))
        result._lesson_block_kind = 'pitch'
        result._feedback_fragment = {'kind':'teaching_block', 'index':index, 'block':deepcopy(block)}
        return result
    children = [u.label(block["label"], size=12, bold=True, color=u.MUTED, margin_bottom=5)]
    kind = block["kind"]
    if kind == "pattern":
        children.append(lesson_label(u, unbroken_groups(block["text"]), size=19, bold=True, color=u.GREEN))
    elif kind == "flow":
        # A vertical path keeps every node readable at large accessibility
        # font sizes; it never squeezes several sentences into narrow columns.
        for n, node in enumerate(block["nodes"]):
            if n:
                children.append(u.label("↓", size=16, color=u.MUTED, margin_bottom=3))
            children.append(lesson_label(u, node, size=17, bold=True, margin_bottom=3))
    elif kind == "compare":
        for n, (title, text) in enumerate(block["rows"]):
            children.append(u.col(lesson_label(u, title, size=16, bold=True, color=u.GREEN, margin_bottom=3),
                                  lesson_label(u, text, size=16, margin_bottom=2),
                                  margin_top=5 if n else 0, padding_left=8))
    elif kind == "beats":
        # Keep a beat in its own tile. A joined text line may wrap a separator
        # onto the next line and make a small-kana combination look ambiguous.
        cells = block["cells"]
        columns = 2 if u.font_scale() > 1.3 else 3
        if max(map(len, cells)) > 9:
            columns = 1 if u.font_scale() > 1.3 else 2
        for start in range(0, len(cells), columns):
            tiles = []
            for offset, cell in enumerate(cells[start:start + columns]):
                tiles.append(u.col(u.label(str(start + offset + 1), size=11, color=u.MUTED, margin_bottom=2),
                    lesson_label(u, cell, size=19, bold=True, color=u.GREEN, margin_bottom=0),
                    flex=1, padding=5, margin_right=4, background_color=u.WHITE))
            tiles.extend(u.col(flex=1, margin_right=4) for _ in range(columns - len(tiles)))
            children.append(u.row(*tiles, margin_bottom=5))
    else:
        for value in block["items"]:
            children.append(lesson_label(u, "• " + value, size=16, margin_bottom=5))
    if block.get("caption"):
        children.append(lesson_label(u, block["caption"], size=14, color=u.MUTED, margin_top=5))
    result = u.col(*children, padding=12, margin_bottom=10,
                   background_color=u.ENTRY_BACKGROUNDS[index % len(u.ENTRY_BACKGROUNDS)])
    result._lesson_block_kind = kind
    result._feedback_fragment = {"kind": "teaching_block", "index": index, "block": deepcopy(block)}
    return result


def teaching_block(u, item, *, compact=False, speak=None):
    view = lesson_view(item)
    blocks = deepcopy(view["blocks"][:1] if compact else view["blocks"])
    from .lesson_diagram_ui import diagram_widget
    diagram = view.get('diagram')
    duplicate_pitch = False
    if diagram and diagram['kind'] == 'pitch':
        from .pitch_practice_ui import diagram_data, validate_pitch_data
        try:
            data = diagram_data(diagram)
            validate_pitch_data(data['morae'], data['levels'], data['word_mora_count'], data.get('number'))
        except ValueError:
            data = None
        if data:
            for block in blocks:
                if block['kind'] != 'pitch' or any(block.get(k) != data.get(k)
                        for k in ('morae', 'levels', 'word_mora_count', 'number')):
                    continue
                # Keep any distinct authored wording; only the identical
                # diagram is suppressed. The persisted teaching plan is intact.
                for key in ('label', 'caption'):
                    extra = diagram.get(key)
                    if extra and extra not in (block.get(key) or ''):
                        block[key] = ' · '.join(x for x in (block.get(key), extra) if x)
                duplicate_pitch = True
                break
    diagrams = ([diagram_widget(u, diagram, speak=speak)]
                if diagram and not compact and not duplicate_pitch else [])
    result = u.col(lesson_label(u, view["focus"], size=18, margin_bottom=12),
                   *diagrams,
                   *[block_widget(u, block, index) for index, block in enumerate(blocks)])
    result._lesson_view = view
    result._feedback_fragment = {"kind": "teaching_plan", "target_id": item.get("id"),
        "content_revision": item.get("revision"), "item_snapshot": deepcopy(item),
        "plan": deepcopy(view), "compact": compact, "visible_block_count": len(blocks)}
    return result


def original_notes(u, item):
    """Secondary, local expansion; never changes navigation or study progress."""
    sections = original_sections(item)
    body = u.col()
    opened = [False]

    def toggle(widget=None, **kwargs):
        opened[0] = not opened[0]
        result._feedback_fragment["expanded"] = opened[0]
        body.clear()
        if opened[0]:
            body.add(*[u.col(u.label(label, size=12, color=u.MUTED, bold=True),
                             *[lesson_label(u, text, size=14) for text in values])
                       for label, values in sections])
        trigger.text = "收起补充说明" if opened[0] else "补充说明"

    trigger = u.button("补充说明", toggle, compact=True, font_size=12)
    result = u.col(trigger, body) if sections else body
    result._feedback_fragment = {"kind": "original_notes", "target_id": item.get("id"),
        "expanded": False, "sections": deepcopy(sections)}
    return result
