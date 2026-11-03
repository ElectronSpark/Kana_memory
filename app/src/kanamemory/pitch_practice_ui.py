"""Native, answer-neutral two-level pitch diagrams for teaching and practice.

This is a display of the levels explicitly supplied by the caller. It neither
looks up an accent nor derives missing answers. Ordinary word ruby and the
user's -/_ pitch preferences continue to use pitch_display.py.
"""
from copy import deepcopy
import math
import re
import unicodedata

from .pitch_accent import morae as split_morae


def validate_pitch_data(morae, levels, word_mora_count=None, number=None, *, partial=True,
                        input_groups=False):
    if not isinstance(morae, (list, tuple)) or not 1 <= len(morae) <= 32:
        raise ValueError('音高图需要一至三十二个实际音拍')
    if any(not isinstance(m, str) or not (
            bool(re.fullmatch(r'[ぁ-ゖァ-ヶー]{1,32}', m)) if input_groups else split_morae(m) == (m,))
            for m in morae):
        raise ValueError('音高图每格必须是一个完整假名音拍')
    if not isinstance(levels, (list, tuple)) or len(levels) != len(morae):
        raise ValueError('音高位置必须与音拍一一对应')
    allowed = ('H', 'L', None) if partial else ('H', 'L')
    if any(level not in allowed for level in levels):
        raise ValueError('音高只能为 H、L 或尚未作答')
    count = len(morae) if word_mora_count is None else word_mora_count
    if type(count) is not int or not 1 <= count <= len(morae):
        raise ValueError('词拍数必须是不含助词的有效整数')
    if number is not None and (type(number) is not int or not 0 <= number <= count):
        raise ValueError('调号必须是词拍数范围内的整数')
    return count



# Character coordinates depend only on the original string and viewport. Neither
# answer levels nor learner-defined mora groups participate in line breaking.
_OPEN = frozenset('（([「『【〈《')
_CLOSE = frozenset('、。，．！？!?：；:;）)]」』】〉》…ーぁぃぅぇぉっゃゅょゎゕゖァィゥェォッャュョヮヵヶ')
_WORD_COLORS = ('#195D50', '#6B4388', '#8A5426', '#246B94')


def pitch_token_colors(tokens):
    """One readable foreground per supplied word; particles stay with it."""
    result, word = {}, -1
    for index, token in enumerate(tokens):
        if token.get('role', 'word') == 'word':
            word += 1
        result[index] = _WORD_COLORS[max(0, word) % len(_WORD_COLORS)]
    return result


def pitch_separator(character):
    return character.isspace() or unicodedata.category(character).startswith('P')


def _span_list(values, length, name):
    if not isinstance(values, (list, tuple)):
        raise ValueError(name + '必须是跨度列表')
    result, end = [], 0
    for value in values:
        if not isinstance(value, dict):
            raise ValueError(name + '必须使用 start/end 字符位置')
        start, stop = value.get('start'), value.get('end')
        if (type(start) is not int or type(stop) is not int
                or not 0 <= start < stop <= length or start < end):
            raise ValueError(name + '跨度必须有序、不重叠且位于原文内')
        result.append(deepcopy(value)); end = stop
    return result


def validate_fixed_data(characters, levels, *, groups=(), tokens=(), cursor=None,
                        active_span=None, number=None):
    if (not isinstance(characters, (list, tuple, str)) or not 1 <= len(characters) <= 512
            or any(not isinstance(c, str) or len(c) != 1 or (ord(c) < 32 and c != '\n')
                   for c in characters)):
        raise ValueError('音高图需要一至五百一十二个固定字符')
    if (not isinstance(levels, (list, tuple)) or len(levels) != len(characters)
            or any(v not in ('H', 'L', None) for v in levels)):
        raise ValueError('音高必须与固定字符一一对应')
    if any(c.isspace() and v is not None for c, v in zip(characters, levels)):
        raise ValueError('空白与换行不带音高')
    groups = _span_list(groups, len(characters), '音拍')
    tokens = _span_list(tokens, len(characters), '词')
    if cursor is not None and (type(cursor) is not int or not 0 <= cursor <= len(characters)):
        raise ValueError('当前位置超出原文')
    if active_span is not None:
        _span_list([active_span], len(characters), '当前字符')
    if number is not None and (type(number) is not int or not 0 <= number <= len(characters)):
        raise ValueError('调号必须是有效整数')
    return groups, tokens


def fixed_pitch_layout(characters, *, tokens=(), wrap_units=None, width=320, font_scale=1):
    """Immutable character cells with Japanese punctuation-aware wrapping."""
    _, tokens = validate_fixed_data(characters, [None] * len(characters), tokens=tokens)
    # Explicit public wrap units are independent from learner-colored words.
    # None retains the v1 whole-word wrapping contract; [] means fixed text only.
    layout_tokens = tokens if wrap_units is None else _span_list(wrap_units, len(characters), '换行单位')
    if (isinstance(width, bool) or not isinstance(width, (int, float))
            or not math.isfinite(width) or width < 160):
        raise ValueError('音高图可用宽度必须至少为 160')
    if (isinstance(font_scale, bool) or not isinstance(font_scale, (int, float))
            or not math.isfinite(font_scale) or not .5 <= font_scale <= 3):
        raise ValueError('音高图字号缩放无效')
    scale = max(1, font_scale); size = 18
    axis = math.ceil(10 * scale * 1.35) + 4
    # An 18 pt Japanese glyph occupies one 24 DIP em. A short vector corridor
    # joins the pitch above adjacent characters without spacing out the word.
    glyph_width = math.ceil(size * (4 / 3) * scale)
    connector = max(2, math.ceil(2 * scale))
    cell_width = glyph_width + connector
    capacity = max(1, int((width - 16 - axis) // cell_width))
    if axis + 16 + cell_width > width:
        raise ValueError('可用宽度不足以显示固定字符')
    track = math.ceil(10 * scale * 1.6) + 4
    track_gap = math.ceil(10 * scale) + 2
    glyph_height = math.ceil(size * scale * 1.8) + 8
    tie_height = math.ceil(5 * scale) + 2
    count_height = math.ceil(12 * scale * 1.6) + 2
    # row_height includes the fixed inter-row gap; headers are outside this
    # geometry. Native rows use row_content_height plus that same margin.
    row_content_height = track * 2 + track_gap + glyph_height + tie_height + count_height
    row_height = row_content_height + 8
    # Fixed lexical units, not learner mora groups, decide preferred wraps.
    units, cursor = [], 0
    for token in layout_tokens:
        units.extend({'start': i, 'end': i + 1, 'role': 'unmarked'} for i in range(cursor, token['start']))
        piece = {'start': token['start'], 'end': token['end'], 'role': token.get('role', 'word')}
        if (piece['role'] == 'particle' and units and units[-1]['role'] == 'word'
                and units[-1]['end'] == piece['start'] and piece['end'] - units[-1]['start'] <= capacity):
            units[-1]['end'] = piece['end']
        else:
            units.append(piece)
        cursor = token['end']
    units.extend({'start': i, 'end': i + 1, 'role': 'unmarked'} for i in range(cursor, len(characters)))
    rows, breaks, index = [], [], 0
    while index < len(characters):
        if characters[index] == '\n':
            breaks.append(index); index += 1
            continue
        end = min(len(characters), index + capacity)
        # A public unit longer than a full row must split. Do not strand its
        # single final character on a separate line; reserve two instead.
        # This never consults learner tokens when explicit wrap units exist.
        current_unit = next((unit for unit in units if unit['start'] <= index < unit['end']), None)
        if current_unit and capacity >= 3 and current_unit['end'] - index == capacity + 1:
            end -= 1
        word_ends = [unit['end'] for unit in units if index < unit['end'] <= end]
        if word_ends:
            end = max(word_ends)
        newline = next((i for i in range(index, end) if characters[i] == '\n'), None)
        if newline is not None:
            end = newline
        elif end < len(characters) and characters[end] != '\n':
            legal_word_ends = [stop for stop in word_ends if stop <= end
                               and characters[stop] not in _CLOSE
                               and characters[stop - 1] not in _OPEN]
            if legal_word_ends:
                # Move the whole final word when its punctuation would be
                # stranded. Split inside it only if no lexical cut fits.
                end = max(legal_word_ends)
            else:
                cut = end
                while cut > index + 1 and (characters[cut] in _CLOSE or characters[cut - 1] in _OPEN):
                    cut -= 1
                if cut > index and not (characters[cut] in _CLOSE or characters[cut - 1] in _OPEN):
                    end = cut
        row_number = len(rows)
        cells = [{'index': i, 'character': characters[i], 'width': cell_width,
                  'glyph_width': glyph_width, 'x': 8 + axis + (i - index) * cell_width,
                  'row': row_number, 'baseline_y': 8 + row_number * row_height + track * 2 + track_gap}
                 for i in range(index, end)]
        if cells:
            rows.append(cells)
        index = end
    return {'rows': rows, 'linebreaks': breaks, 'width': width, 'axis_width': axis,
            'cell_width': cell_width, 'glyph_width': glyph_width, 'connector_width': connector,
            'font_size': size, 'track_height': track, 'track_gap': track_gap,
            'glyph_height': glyph_height, 'tie_height': tie_height, 'count_height': count_height,
            'row_height': row_height, 'row_content_height': row_content_height}


def _legacy_data(morae, levels, count):
    characters, values, groups = [], [], []
    for mora, level in zip(morae, levels):
        start = len(characters); characters.extend(mora); values.extend([level] * len(mora))
        groups.append({'start': start, 'end': len(characters)})
    boundary = sum(map(len, morae[:count]))
    tokens = [{'start': 0, 'end': boundary, 'role': 'word', 'id': 'word'}]
    if boundary < len(characters):
        tokens.append({'start': boundary, 'end': len(characters), 'role': 'particle', 'id': 'particle'})
    return characters, values, groups, tokens


def pitch_layout(morae, levels, *, word_mora_count=None, width=320, font_scale=1,
                 input_groups=False):
    count = validate_pitch_data(morae, levels, word_mora_count, input_groups=input_groups)
    characters, _, _, tokens = _legacy_data(morae, levels, count)
    return fixed_pitch_layout(characters, tokens=tokens, width=width, font_scale=font_scale)


def fixed_pitch_diagram(u, characters=None, levels=None, *, groups=(), tokens=(), wrap_units=None, cursor=None,
                        active_span=None, complete=False, label=None, number=None,
                        width=320, show_counts=False, version=None, surface=None, viewport_owned=False):
    """Fixed kana baseline, with two pitch tracks above and optional mora ties.

    All spans are half-open indices into the complete original character list.
    Partial answers must pass that complete list too. No level or segmentation
    is inferred; punctuation and unannotated sentence pieces remain neutral.
    """
    if surface is not None or version == 3:
        from .pitch_alignment_canvas import alignment_diagram
        return alignment_diagram(u, surface=surface, version=version, cursor=cursor,
            complete=complete, label=label, width=width, show_counts=show_counts,
            viewport_owned=viewport_owned)
    if not characters:
        return None
    groups, tokens = validate_fixed_data(characters, levels, groups=groups, tokens=tokens,
                                         cursor=cursor, active_span=active_span, number=number)
    if type(complete) is not bool or type(show_counts) is not bool:
        raise ValueError('图示状态必须是布尔值')
    # Android keeps one native drawing surface alive while answers change.
    # The widget fallback remains useful for desktop and headless UI adapters.
    try:
        from toga.platform import current_platform
    except ImportError:
        current_platform = None
    if current_platform == 'android':
        from .pitch_practice_canvas import native_pitch_diagram
        return native_pitch_diagram(u, characters, levels, groups=groups, tokens=tokens, wrap_units=wrap_units,
            cursor=cursor, active_span=active_span, complete=complete, label=label,
            number=number, width=width, show_counts=show_counts)
    layout = fixed_pitch_layout(characters, tokens=tokens, wrap_units=wrap_units, width=width, font_scale=u.font_scale())
    ink, muted, green = getattr(u, 'INK', '#193D37'), u.MUTED, u.GREEN
    line_color, guide_color = green, '#D9E2DE'
    gw, gap = layout['glyph_width'], layout['connector_width']
    th, middle = layout['track_height'], layout['track_gap']
    token_at, group_at, counts = {}, {}, {}
    token_colors = pitch_token_colors(tokens)
    for token_index, token in enumerate(tokens):
        role = token.get('role', 'word')
        for i in range(token['start'], token['end']):
            token_at[i] = token_index
        counts[token_index] = sum(token['start'] <= g['start'] and g['end'] <= token['end'] for g in groups)
    for group_index, group in enumerate(groups):
        for i in range(group['start'], group['end']):
            group_at[i] = group_index
    children = []
    if label:
        children.append(u.label(label, size=13, color=muted, margin_bottom=4))
    if number is not None:
        children.append(u.label(f'调号 [{number}]', size=13, color=muted, margin_bottom=4))
    if show_counts:
        word_counts = [counts[i] for i, t in enumerate(tokens) if t.get('role', 'word') == 'word']
        if word_counts:
            children.append(u.label('词 · ' + ' / '.join(str(n) for n in word_counts) + ' 拍',
                                    size=11, color=muted, margin_bottom=3))

    def blank(w, h, color=None):
        style = {'width': w, 'height': h}
        if color:
            style['background_color'] = color
        return u.col(**style)

    def line(w, color, thickness=2):
        top = (th - thickness) // 2
        return u.col(blank(w, top), blank(w, thickness, color), blank(w, th - thickness - top), width=w, height=th)

    def connector(previous, following, previous_color, following_color, tie_to_next=False):
        # Every character owns an invariant corridor. Only its ink changes.
        known = previous in ('H', 'L') and following in ('H', 'L')
        left, right = (gap - 2) // 2, gap - 2 - (gap - 2) // 2
        def side(w, level, color):
            return u.col(line(w, color if level == 'H' else guide_color, 3 if level == 'H' else 1),
                         blank(w, middle),
                         line(w, color if level == 'L' else guide_color, 3 if level == 'L' else 1), width=w)
        if known and previous != following:
            center = u.col(blank(2, th // 2), blank(2, middle + th, previous_color),
                           blank(2, th - th // 2), width=2)
        else:
            center = side(2, previous if known else None, previous_color)
        tracks = u.row(side(left, previous if known else None, previous_color), center,
                       side(right, following if known else None, following_color), width=gap)
        tie = u.col(blank(gap, 2, previous_color if tie_to_next else None),
                    blank(gap, layout['tie_height'] - 2), width=gap)
        boundary = blank(gap, layout['glyph_height'])
        return u.col(tracks, boundary, tie,
                     blank(gap, layout['count_height']), width=gap)

    for row in layout['rows']:
        axis = u.col(u.label('高', size=10, color=muted, width=layout['axis_width'], height=th, wrap=False, margin_bottom=0),
                     blank(layout['axis_width'], middle),
                     u.label('低', size=10, color=muted, width=layout['axis_width'], height=th, wrap=False, margin_bottom=0),
                     width=layout['axis_width'])
        track = [axis]
        for cell in row:
            i, character = cell['index'], cell['character']; level = levels[i]
            ti = token_at.get(i); token = tokens[ti] if ti is not None else None
            role = token.get('role', 'word') if token else 'unmarked'
            background = None
            active = (not complete and ((active_span is not None and active_span['start'] <= i < active_span['end'])
                                       or (active_span is None and cursor == i)))
            foreground = token_colors.get(ti, ink)
            glyph = u.label(character, size=18, text_role='study', color=foreground,
                            bold=active, wrap=False, text_align='center', width=gw,
                            height=layout['glyph_height'], margin_bottom=0,
                            background_color='#FFE6AA' if active else None)
            glyph._pitch_character = {'index': i, 'character': character, 'row': cell['row'],
                                      'x': cell['x'], 'baseline_y': cell['baseline_y'], 'active': active}
            gi = group_at.get(i); group = groups[gi] if gi is not None else None
            tied = group is not None and group['end'] - group['start'] > 1
            tie_color = foreground if tied else None
            tie = u.col(blank(gw, 2, tie_color), blank(gw, layout['tie_height'] - 2), width=gw)
            count = ''
            if show_counts and group and i == group['start'] and role == 'word':
                count = str(sum(tokens[ti]['start'] <= g['start'] <= i and g['end'] <= tokens[ti]['end'] for g in groups))
            content = u.col(blank(gw, th) if pitch_separator(character) else line(gw, foreground if level == 'H' else guide_color, 3 if level == 'H' else 1),
                            blank(gw, middle),
                            blank(gw, th) if pitch_separator(character) else line(gw, foreground if level == 'L' else guide_color, 3 if level == 'L' else 1),
                            glyph, tie,
                            u.label(count, size=10, color=muted, width=gw, height=layout['count_height'],
                                    wrap=False, text_align='center', margin_bottom=0),
                            width=gw)
            following = levels[i + 1] if i + 1 < len(characters) and i != row[-1]['index'] else None
            next_color = token_colors.get(token_at.get(i + 1), ink)
            tile = u.row(content, connector(level, following, foreground, next_color,
                tied and i + 1 < group['end'] and i != row[-1]['index']), width=layout['cell_width'])
            tile._pitch_cell = {**deepcopy(cell), 'level': level, 'active': active, 'token_index': ti,
                                'group_index': gi, 'group': role, 'background': background,
                                'foreground': foreground}
            track.append(tile)
        children.append(u.row(*track, height=layout['row_content_height'], margin_bottom=8))
    result = u.col(*children, padding=8, margin_bottom=8, background_color=u.WHITE)
    result._pitch_diagram = True; result._pitch_layout = layout
    result._feedback_fragment = {'kind': 'fixed_pitch_diagram', 'characters': list(characters),
        'levels': list(levels), 'groups': deepcopy(groups), 'tokens': deepcopy(tokens),
        'wrap_units': deepcopy(wrap_units),
        'cursor': cursor, 'active_span': deepcopy(active_span), 'complete': complete,
        'label': label, 'number': number, 'show_counts': show_counts}
    return result


def pitch_diagram(u, morae, levels, *, word_mora_count=None, label=None,
                  number=None, width=320, show_counts=False, input_groups=False):
    """Compatibility adapter for authored mora data; native text never moves."""
    if morae == [] or morae == ():
        return None
    count = validate_pitch_data(morae, levels, word_mora_count, number, input_groups=input_groups)
    characters, values, groups, tokens = _legacy_data(morae, levels, count)
    result = fixed_pitch_diagram(u, characters, values, groups=groups, tokens=tokens,
                                 label=label, number=number, width=width, show_counts=show_counts)
    result._feedback_fragment.update(morae=list(morae), word_mora_count=count, input_groups=input_groups)
    return result


def diagram_data(diagram):
    """Adapt only explicit authored vector data; never invent kana for labels."""
    from .lesson_diagrams import validate_diagram
    validate_diagram(diagram)
    return {'morae': [node['text'] for node in diagram['nodes']],
            'levels': ['H' if level == 1 else 'L' for level in diagram['levels']],
            'word_mora_count': diagram.get('particle_from', len(diagram['nodes'])),
            'label': diagram['label'], **({'number': diagram['number']} if 'number' in diagram else {})}
