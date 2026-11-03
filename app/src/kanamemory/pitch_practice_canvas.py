"""Character-anchored pitch scenes rendered by one native Canvas.

This module only draws explicitly supplied public/entered levels. It does not
load teaching content, infer missing pitch or change a learning record.
"""
from copy import deepcopy
import math


def _validated(characters, levels, *, groups=(), tokens=(), wrap_units=None, cursor=None,
               active_span=None, complete=False, label=None, number=None,
               show_counts=False):
    # The compatibility renderer imports us lazily, so keep this dependency
    # local as well. Its established geometry remains the single authority.
    from .pitch_practice_ui import validate_fixed_data, _span_list
    groups, tokens = validate_fixed_data(characters, levels, groups=groups,
        tokens=tokens, cursor=cursor, active_span=active_span, number=number)
    wrap_units = None if wrap_units is None else _span_list(wrap_units, len(characters), '换行单位')
    if type(complete) is not bool or type(show_counts) is not bool:
        raise ValueError('图示状态必须是布尔值')
    if label is not None and not isinstance(label, str):
        raise ValueError('音高图标题必须是文字')
    return {'characters': list(characters), 'levels': list(levels),
        'groups': groups, 'tokens': tokens, 'wrap_units': wrap_units, 'cursor': cursor,
        'active_span': deepcopy(active_span), 'complete': complete,
        'label': label, 'number': number, 'show_counts': show_counts}


def _word_counts(data):
    return [sum(token['start'] <= group['start'] and group['end'] <= token['end']
                for group in data['groups'])
            for token in data['tokens'] if token.get('role', 'word') == 'word']


def pitch_accessible_description(data):
    """Describe only visible data, including an unanswered current character."""
    parts = [data['label']] if data['label'] else []
    if data['number'] is not None:
        parts.append(f"调号 {data['number']}")
    if data['show_counts']:
        counts = _word_counts(data)
        if counts:
            parts.append('词拍数：' + '、'.join(str(n) for n in counts))
    from .pitch_practice_ui import pitch_separator
    names = {'H': '高', 'L': '低', None: '未作答'}
    for i, (character, level) in enumerate(zip(data['characters'], data['levels'])):
        if character.isspace():
            continue
        if pitch_separator(character):
            parts.append(character)
            continue
        particle = any(t.get('role') == 'particle' and t['start'] <= i < t['end']
                       for t in data['tokens'])
        parts.append(('助词 ' if particle else '') + character + ' ' + names[level])
    joined = [''.join(data['characters'][g['start']:g['end']])
              for g in data['groups'] if g['end'] - g['start'] > 1]
    if joined:
        parts.append('已合拍：' + '、'.join(joined))
    if not data['complete']:
        span = data['active_span']
        cursor = data['cursor']
        if span:
            parts.append('当前位置：' + ''.join(data['characters'][span['start']:span['end']]))
        elif cursor is not None and cursor < len(data['characters']):
            parts.append('当前位置：' + data['characters'][cursor])
    return '；'.join(parts)


def build_pitch_scene(characters, levels, *, groups=(), tokens=(), wrap_units=None, cursor=None,
                      active_span=None, complete=False, label=None, number=None,
                      width=320, font_scale=1, show_counts=False,
                      ink='#193D37', muted='#56736B', green='#195D50'):
    """Pure vector scene; glyph anchors never depend on levels or bold width."""
    from .pitch_practice_ui import fixed_pitch_layout, pitch_token_colors, pitch_separator
    data = _validated(characters, levels, groups=groups, tokens=tokens, wrap_units=wrap_units, cursor=cursor,
        active_span=active_span, complete=complete, label=label, number=number,
        show_counts=show_counts)
    layout = fixed_pitch_layout(data['characters'], tokens=data['tokens'], wrap_units=data['wrap_units'], width=width,
                                font_scale=font_scale)
    # Geometry deliberately keeps its minimum cell size at scale 1. Text
    # follows the actual accessibility scale, just like the native labels.
    scale = font_scale
    th, middle = layout['track_height'], layout['track_gap']
    gw, gap = layout['glyph_width'], layout['connector_width']
    token_at, group_at = {}, {}
    token_colors = pitch_token_colors(data['tokens'])
    for ti, token in enumerate(data['tokens']):
        role = token.get('role', 'word')
        for i in range(token['start'], token['end']):
            token_at[i] = ti
    for gi, group in enumerate(data['groups']):
        for i in range(group['start'], group['end']):
            group_at[i] = gi
    elements, glyphs = [], []
    guide_color = '#D9E2DE'

    def line(x1, y1, x2, y2, color, weight=2, **meta):
        elements.append({'kind': 'line', 'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2,
                         'color': color, 'weight': weight, **meta})

    def text(value, x, baseline, size, color, *, bold=False, family='sans-serif', **meta):
        elements.append({'kind': 'text', 'text': value, 'x': x, 'baseline_y': baseline,
                         'size': size, 'color': color, 'bold': bold, 'family': family, **meta})

    for row in layout['rows']:
        top = 8 + row[0]['row'] * layout['row_height']
        high, low = top + th / 2, top + th + middle + th / 2
        # Native fallback label sizes are points. Scene positions are DIP:
        # retain 10pt axes/counts and 18pt kana rather than shrinking them by
        # the later Canvas font conversion from DIP back to points.
        axis_size = 10 * (4 / 3) * scale
        text('高', 8, high + axis_size * .35, axis_size, muted, role='axis')
        text('低', 8, low + axis_size * .35, axis_size, muted, role='axis')
        for cell in row:
            i, x = cell['index'], cell['x']
            ti = token_at.get(i)
            token = data['tokens'][ti] if ti is not None else None
            role = token.get('role', 'word') if token else 'unmarked'
            gi = group_at.get(i)
            group = data['groups'][gi] if gi is not None else None
            active = not data['complete'] and (
                data['active_span'] is not None and data['active_span']['start'] <= i < data['active_span']['end']
                or data['active_span'] is None and data['cursor'] == i)
            foreground = token_colors.get(ti, ink)
            if active:
                elements.append({'kind': 'rect', 'x': x, 'y': cell['baseline_y'], 'w': gw,
                    'h': layout['glyph_height'], 'fill': '#FFE6AA', 'role': 'cell',
                    'index': i, 'active': True, 'token_index': ti})
            level = data['levels'][i]
            for value, y in ([] if pitch_separator(cell['character']) else [('H', high), ('L', low)]):
                selected = level == value
                line(x, y, x + gw, y, foreground if selected else guide_color,
                     3 if selected else 1, role='pitch', index=i, level=value, selected=selected)
            # One fixed typographic em per original character. Do not center
            # using the current bold glyph's measured width: that would move
            # the same character when the cursor advances.
            size = 18 * (4 / 3) * scale
            glyph_x = x + (gw - size) / 2
            baseline = cell['baseline_y'] + (layout['glyph_height'] - size) / 2 + size
            glyph = {**deepcopy(cell), 'anchor_x': glyph_x, 'text_baseline_y': baseline, 'color': foreground,
                     'active': active, 'level': level, 'group_index': gi, 'token_index': ti}
            glyphs.append(glyph)
            text(cell['character'], glyph_x, baseline, size,
                 foreground, bold=active, family='serif',
                 role='glyph', index=i, row=cell['row'])
            tie_y = cell['baseline_y'] + layout['glyph_height'] + 1
            tied = group is not None and group['end'] - group['start'] > 1
            if tied:
                line(x, tie_y, x + gw, tie_y, foreground, 2, role='tie', index=i)
            if data['show_counts'] and group and i == group['start'] and role == 'word':
                count = sum(token['start'] <= g['start'] <= i and g['end'] <= token['end']
                            for g in data['groups'])
                count_size = 10 * (4 / 3) * scale
                text(str(count), x + (gw - count_size) / 2,
                     tie_y + layout['tie_height'] + count_size,
                     count_size, muted, role='count', index=i)
            if (i == row[-1]['index'] or pitch_separator(cell['character'])
                    or pitch_separator(data['characters'][i + 1])):
                continue
            following = data['levels'][i + 1]
            next_color = token_colors.get(token_at.get(i + 1), ink)
            connector_start, connector_end = x + gw, x + gw + gap
            for y in (high, low):
                line(connector_start, y, connector_end, y, guide_color, 1, role='guide_connector')
            if level in ('H', 'L') and following in ('H', 'L'):
                y1 = high if level == 'H' else low
                y2 = high if following == 'H' else low
                if y1 == y2:
                    center = connector_start + gap / 2
                    line(connector_start, y1, center, y2, foreground, 3, role='connector')
                    line(center, y1, connector_end, y2, next_color, 3, role='connector')
                else:
                    center = connector_start + gap / 2
                    line(connector_start, y1, center, (y1 + y2) / 2, foreground, 2, role='connector')
                    line(center, (y1 + y2) / 2, connector_end, y2, next_color, 2, role='connector')
            if tied and i + 1 < group['end']:
                line(connector_start, tie_y, connector_end, tie_y, foreground, 2, role='tie_connector')
    return {'layout': layout, 'elements': elements, 'glyphs': glyphs,
            'height': max(16, 16 + len(layout['rows']) * layout['row_height']),
            'word_counts': _word_counts(data), 'accessible_description': pitch_accessible_description(data),
            'data': data}


def native_pitch_diagram(u, characters, levels, *, groups=(), tokens=(), wrap_units=None, cursor=None,
                         active_span=None, complete=False, label=None, number=None,
                         width=320, show_counts=False):
    """Render and retain one Canvas, with a bounded set of native headers."""
    if not characters:
        return None
    import toga
    from toga.fonts import BOLD, NORMAL
    from toga.platform import current_platform
    from toga.style import Pack
    from toga.widgets.canvas import Fill, Stroke, FillText, Rect, MoveTo, LineTo

    data = _validated(characters, levels, groups=groups, tokens=tokens, wrap_units=wrap_units, cursor=cursor,
        active_span=active_span, complete=complete, label=label, number=number,
        show_counts=show_counts)
    # Validate requested geometry before constructing native objects.
    from .pitch_practice_ui import fixed_pitch_layout
    fixed_pitch_layout(data['characters'], tokens=data['tokens'], wrap_units=data['wrap_units'], width=width, font_scale=u.font_scale())
    state = {'data': data, 'width': float(width), 'revision': 0, 'render_key': None,
             'root': None, 'count_header': None}
    fonts = {}

    def redraw(widget, width=None, height=None, **kwargs):
        current_width = state['width'] if width is None else width
        if not isinstance(current_width, (int, float)) or not math.isfinite(current_width) or current_width < 160:
            return
        scale = u.font_scale()
        key = (float(current_width), scale, state['revision'])
        if state['render_key'] == key:
            return
        scene = build_pitch_scene(**state['data'], width=current_width, font_scale=scale,
            ink=getattr(u, 'INK', '#193D37'), muted=u.MUTED, green=u.GREEN)

        def font(size, bold, family):
            key = (size, bold, family, scale)
            if key not in fonts:
                # Match lesson_canvas: scene coordinates are DIP; Android
                # applies SP scaling again to the point-sized Toga font.
                points = size * .75 / (scale if current_platform == 'android' else 1)
                fonts[key] = toga.Font(family=family, size=points, weight=BOLD if bold else NORMAL)
            return fonts[key]

        actions = []
        for element in scene['elements']:
            if element['kind'] == 'line':
                action = Stroke(stroke_style=element['color'], line_width=element['weight'])
                action.drawing_actions.extend([MoveTo(element['x1'], element['y1']), LineTo(element['x2'], element['y2'])])
            elif element['kind'] == 'rect':
                action = Fill(fill_style=element['fill'])
                action.drawing_actions.append(Rect(element['x'], element['y'], element['w'], element['h']))
            else:
                action = Fill(fill_style=element['color'])
                action.drawing_actions.append(FillText(element['text'], element['x'], element['baseline_y'],
                    font=font(element['size'], element['bold'], element['family'])))
            actions.append(action)
        widget.root_state.drawing_actions[:] = actions
        widget._pitch_scene = scene
        widget._feedback_fragment = {'kind': 'fixed_pitch_canvas', **deepcopy(state['data']),
                                     'width': current_width, 'font_scale': scale}
        state['width'], state['render_key'] = float(current_width), key
        if widget.style.height != scene['height']:
            widget.style.height = scene['height']
        if current_platform == 'android':
            widget._impl.native.setContentDescription(scene['accessible_description'])
            widget._impl.native.setImportantForAccessibility(1)
        if state['root'] is not None:
            state['root']._pitch_layout = scene['layout']
            state['root']._feedback_fragment = {'kind': 'fixed_pitch_diagram', **deepcopy(state['data'])}
        if state['count_header'] is not None:
            caption = '词 · ' + ' / '.join(str(n) for n in scene['word_counts']) + ' 拍'
            if state['count_header'].text != caption:
                state['count_header'].text = caption
        widget.redraw()

    canvas = toga.Canvas(on_resize=redraw, style=Pack(height=160))
    headers = []
    if data['label']:
        headers.append(u.label(data['label'], size=13, color=u.MUTED, margin_left=8,
                               margin_right=8, margin_bottom=4))
    if data['number'] is not None:
        headers.append(u.label(f"调号 [{data['number']}]", size=13, color=u.MUTED,
                               margin_left=8, margin_right=8, margin_bottom=4))
    if data['show_counts'] and _word_counts(data):
        state['count_header'] = u.label('', size=11, color=u.MUTED, margin_left=8,
                                       margin_right=8, margin_bottom=3)
        headers.append(state['count_header'])
    # The pure layout already owns the original eight-DIP margins. Do not add
    # another padding layer around the Canvas and silently change wrapping.
    result = u.col(*headers, canvas, margin_bottom=8, background_color=u.WHITE)
    result._pitch_diagram = True
    result._pitch_canvas = canvas
    state['root'] = result
    structural_keys = ('characters', 'wrap_units', 'label', 'number', 'show_counts')
    if data['wrap_units'] is None:
        structural_keys += ('tokens',)  # v1 colors still define public wrapping
    initial_identity = {key: deepcopy(data[key]) for key in structural_keys}

    def update(characters, levels, *, groups=(), tokens=(), wrap_units=None, cursor=None,
               active_span=None, complete=False, label=None, number=None,
               width=None, show_counts=show_counts):
        new = _validated(characters, levels, groups=groups, tokens=tokens, wrap_units=wrap_units, cursor=cursor,
            active_span=active_span, complete=complete, label=label, number=number,
            show_counts=show_counts)
        if any(new[key] != initial_identity[key] for key in structural_keys):
            return False
        if width is not None:
            fixed_pitch_layout(new['characters'], tokens=new['tokens'], wrap_units=new['wrap_units'], width=width, font_scale=u.font_scale())
        if new != state['data']:
            state['data'] = new
            state['revision'] += 1
        redraw(canvas, width)
        return True

    result._update_pitch = update
    redraw(canvas, width)
    return result
