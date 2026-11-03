"""Public source words with separate, explicitly supplied mora-pitch tracks.

No reading, expected mora count or answer is loaded here. Only source text and
already supplied levels determine the drawing and its size.
"""
from copy import deepcopy
import math
import unicodedata


def validate_surface(surface, *, version=3, cursor=None, complete=False, label=None):
    if type(version) is not int or version != 3 or not isinstance(surface, dict):
        raise ValueError('原文音拍图需要第三版公开显示数据')
    source, units = surface.get('text'), surface.get('units')
    if (not isinstance(source, str) or not 1 <= len(source) <= 512
            or any(ord(c) < 32 for c in source)):
        raise ValueError('原文必须为一至五百一十二个可显示字符')
    if not isinstance(units, list) or not 1 <= len(units) <= 64:
        raise ValueError('原文片段必须为有序列表')
    if type(complete) is not bool or (label is not None and not isinstance(label, str)):
        raise ValueError('图示状态或标题无效')
    normalized, previous, ids = [], 0, set()
    for unit in units:
        if not isinstance(unit, dict):
            raise ValueError('原文片段无效')
        start, end, uid, role = (unit.get(k) for k in ('start', 'end', 'id', 'role'))
        if (type(start) is not int or type(end) is not int or start != previous
                or not start < end <= len(source) or unit.get('text') != source[start:end]
                or not isinstance(uid, str) or not uid or len(uid) > 128 or uid in ids
                or not isinstance(role, str) or role not in ('word', 'particle', 'auxiliary', 'punctuation')):
            raise ValueError('原文片段必须完整覆盖原句且标识唯一')
        levels = unit.get('levels')
        if (not isinstance(levels, list) or len(levels) > 32
                or any(not isinstance(v, str) or v not in ('H', 'L') for v in levels)
                or type(unit.get('complete')) is not bool or type(unit.get('active')) is not bool):
            raise ValueError('每个片段只能显示已输入的零至三十二拍')
        if role == 'punctuation' and (levels or unit['active'] or not unit['complete']):
            raise ValueError('标点不包含音拍或当前输入位置')
        normalized.append({key: deepcopy(unit[key]) for key in
            ('id', 'text', 'start', 'end', 'role', 'levels', 'complete', 'active')})
        previous = end
        ids.add(uid)
    if previous != len(source) or not any(u['role'] != 'punctuation' for u in normalized):
        raise ValueError('原文必须包含完整的可作答片段')
    if cursor is not None and (type(cursor) is not int or not 0 <= cursor < len(units)
                               or normalized[cursor]['role'] == 'punctuation'):
        raise ValueError('当前原文片段无效')
    if [i for i, u in enumerate(normalized) if u['active']] != ([] if complete or cursor is None else [cursor]):
        raise ValueError('当前片段与高亮状态不一致')
    if complete and (cursor is not None or not all(u['complete'] for u in normalized)):
        raise ValueError('已完成图示不能仍有未确认片段')
    return {'version': 3, 'surface': {'text': source, 'units': normalized},
            'cursor': cursor, 'complete': complete, 'label': label}


def _text_width(text, size):
    return sum(1 if unicodedata.east_asian_width(c) in ('W', 'F') else .65 for c in text) * size


def alignment_scene(*, surface, version=3, cursor=None, complete=False, label=None,
                    width=320, font_scale=1, show_counts=False,
                    ink='#193D37', muted='#56736B', green='#195D50'):
    data = validate_surface(surface, version=version, cursor=cursor, complete=complete, label=label)
    if (type(width) not in (int, float) or not math.isfinite(width) or width < 160
            or type(font_scale) not in (int, float) or not math.isfinite(font_scale)
            or not .5 <= font_scale <= 3 or type(show_counts) is not bool):
        raise ValueError('音拍图尺寸无效')
    scale = max(1, font_scale)
    size, minimum_beat, padding, gap = 24 * font_scale, 8 * scale, 4, 8
    row_content = math.ceil(16 + 66 * scale)
    row_height = row_content + 12
    units, blocks, leading = data['surface']['units'], [], ''
    for index, unit in enumerate(units):
        if unit['role'] == 'punctuation':
            if blocks:
                blocks[-1]['text'] += unit['text']
            else:
                leading += unit['text']
        else:
            blocks.append({'index': index, 'text': leading + unit['text'], 'unit': unit})
            leading = ''
    rows, left, row, content_width = [[]], 8, 0, float(width)
    for block in blocks:
        unit = block['unit']
        # The source word supplies the natural track width. Only genuinely
        # entered beats can enlarge it; a hidden correct count is never used.
        track_width = max(_text_width(unit['text'], size), len(unit['levels']) * minimum_beat)
        beat = track_width / len(unit['levels']) if unit['levels'] else minimum_beat
        block_width = math.ceil(max(_text_width(block['text'], size), track_width) + padding * 2)
        if rows[-1] and left + block_width > width - 8:
            rows.append([]); row += 1; left = 8
        top = 8 + row * row_height
        cell = {'index': block['index'], 'row': row, 'text': block['text'],
                'left': left, 'right': left + block_width, 'top': top, 'bottom': top + row_content,
                'source_x': left + padding, 'source_baseline': top + 62 * scale,
                'focus_x': left + padding + (len(unit['levels']) - .5) * beat if unit['levels'] else left + padding,
                'width': block_width, 'beat_width': beat, 'track_width': track_width,
                'active': unit['active'], 'levels': list(unit['levels'])}
        rows[-1].append(cell)
        content_width = max(content_width, left + block_width + 8)
        left += block_width + gap
    elements, glyphs, descriptions = [], [], []
    if label and label != surface['text']:
        descriptions.append(label)
    from .pitch_practice_ui import pitch_token_colors
    colors = pitch_token_colors(units)
    for cells in rows:
        for cell in cells:
            unit = units[cell['index']]
            color, beat = colors[cell['index']], cell['beat_width']
            if cell['active']:
                elements.append({'kind': 'rect', 'role': 'active_source', 'x': cell['left'],
                    'y': cell['top'] + 45 * scale, 'w': _text_width(unit['text'], size) + padding * 2, 'h': 25 * scale,
                    'fill': '#FFE6AA', 'index': cell['index']})
            notch = min(1.5 * scale, beat * .16)
            for i, value in enumerate(unit['levels']):
                x, y = cell['left'] + padding + i * beat, cell['top'] + (12 if value == 'H' else 34) * scale
                elements.append({'kind': 'line', 'role': 'pitch', 'index': cell['index'], 'mora_index': i,
                    'level': value, 'x1': x, 'y1': y, 'x2': x + beat - notch, 'y2': y,
                    'color': color, 'weight': 3})
                if i and unit['levels'][i - 1] != value:
                    previous = unit['levels'][i - 1]
                    prior_y = cell['top'] + (12 if previous == 'H' else 34) * scale
                    elements.append({'kind': 'line', 'role': 'connector', 'index': cell['index'], 'x1': x - notch,
                        'y1': prior_y, 'x2': x, 'y2': y, 'color': color, 'weight': 2})
            element = {'kind': 'text', 'role': 'source', 'text': cell['text'],
                'x': cell['source_x'], 'baseline_y': cell['source_baseline'], 'size': size,
                'color': color, 'bold': cell['active'], 'family': 'serif', 'index': cell['index'], 'row': cell['row']}
            elements.append(element); glyphs.append(deepcopy(element))
            description = unit['text'] + ('：' + '、'.join('高' if level == 'H' else '低' for level in unit['levels'])
                                         if unit['levels'] else '：尚未输入音拍')
            if unit['active']:
                description += '；当前位置'
            if show_counts and unit['levels']:
                description += f"；{len(unit['levels'])} 拍"
            descriptions.append(description)
    layout = {'rows': rows, 'width': float(width), 'content_width': content_width,
        'row_height': row_height, 'row_content_height': row_content, 'beat_width': minimum_beat,
        'source_font_size': size, 'surface': True}
    return {'data': data, 'layout': layout, 'elements': elements, 'glyphs': glyphs,
        'height': 16 + len(rows) * row_height, 'accessible_description': '；'.join(descriptions)}


def native_alignment_diagram(u, *, surface, version=3, cursor=None, complete=False, label=None,
                             width=320, show_counts=False):
    import toga
    from toga.fonts import BOLD, NORMAL
    from toga.platform import current_platform
    from toga.style import Pack
    from toga.widgets.canvas import Fill, Stroke, FillText, Rect, MoveTo, LineTo

    data = validate_surface(surface, version=version, cursor=cursor, complete=complete, label=label)
    initial = alignment_scene(**data, width=width, font_scale=u.font_scale(), show_counts=show_counts)
    identity = [(unit['id'], unit['text'], unit['start'], unit['end'], unit['role']) for unit in data['surface']['units']]
    state = {'data': data, 'width': float(width), 'revision': 0, 'key': None, 'root': None}
    fonts = {}

    def redraw(widget, width=None, height=None, **kwargs):
        # A wide source/entered track gives the Canvas a wider native surface;
        # its resize callback must not mistake that for a wider viewport.
        available = state['width']
        scale = u.font_scale()
        key = (available, scale, state['revision'])
        if state['key'] == key:
            return
        scene = alignment_scene(**state['data'], width=available, font_scale=scale,
            show_counts=show_counts, ink=getattr(u, 'INK', '#193D37'), muted=u.MUTED, green=u.GREEN)
        actions = []
        for element in scene['elements']:
            if element['kind'] == 'line':
                action = Stroke(stroke_style=element['color'], line_width=element['weight'])
                action.drawing_actions.extend([MoveTo(element['x1'], element['y1']), LineTo(element['x2'], element['y2'])])
            elif element['kind'] == 'rect':
                action = Fill(fill_style=element['fill'])
                action.drawing_actions.append(Rect(element['x'], element['y'], element['w'], element['h']))
            else:
                font_key = (element['size'], element['bold'], scale)
                if font_key not in fonts:
                    points = element['size'] * .75 / (scale if current_platform == 'android' else 1)
                    fonts[font_key] = toga.Font(family='serif', size=points, weight=BOLD if element['bold'] else NORMAL)
                action = Fill(fill_style=element['color'])
                action.drawing_actions.append(FillText(element['text'], element['x'], element['baseline_y'], font=fonts[font_key]))
            actions.append(action)
        widget.root_state.drawing_actions[:] = actions
        widget._pitch_scene = scene
        widget._feedback_fragment = {'kind': 'surface_pitch_canvas', **deepcopy(state['data']),
                                     'width': available, 'font_scale': scale}
        if widget.style.height != scene['height']:
            widget.style.height = scene['height']
        if widget.style.width != scene['layout']['content_width']:
            widget.style.width = scene['layout']['content_width']
        if current_platform == 'android':
            widget._impl.native.setContentDescription(scene['accessible_description'])
            widget._impl.native.setImportantForAccessibility(1)
        if state['root'] is not None:
            state['root']._pitch_layout = scene['layout']
            state['root']._feedback_fragment = {'kind': 'surface_pitch_diagram', **deepcopy(state['data'])}
        state['key'] = key
        widget.redraw()

    canvas = toga.Canvas(on_resize=redraw, style=Pack(height=initial['height'], width=initial['layout']['content_width']))
    headers = []
    if label and label != surface['text']:
        headers.append(u.label(label, size=13, color=u.MUTED, margin_bottom=4))
    result = u.col(*headers, canvas, margin_bottom=8, background_color=u.WHITE)
    result._pitch_diagram, result._pitch_canvas = True, canvas
    state['root'] = result

    initial_counts = show_counts

    def update(*, surface, version=3, cursor=None, complete=False, label=None, width=None, show_counts=show_counts):
        new = validate_surface(surface, version=version, cursor=cursor, complete=complete, label=label)
        if ([(unit['id'], unit['text'], unit['start'], unit['end'], unit['role']) for unit in new['surface']['units']] != identity
                or new['label'] != data['label'] or show_counts != initial_counts):
            return False
        if width is not None:
            alignment_scene(**new, width=width, font_scale=u.font_scale(), show_counts=show_counts)
            state['width'] = float(width)
        if new != state['data']:
            state['data'] = new; state['revision'] += 1
        redraw(canvas)
        return True

    result._update_pitch = update
    redraw(canvas)
    return result


def alignment_diagram(u, *, surface, version=3, cursor=None, complete=False, label=None,
                      width=320, show_counts=False, viewport_owned=False):
    from toga.platform import current_platform
    if type(viewport_owned) is not bool:
        raise ValueError('音拍视口归属无效')
    if current_platform == 'android':
        result = native_alignment_diagram(u, surface=surface, version=version, cursor=cursor,
            complete=complete, label=label, width=width, show_counts=show_counts)
        return _static_viewport(u, result, width=width, label=label, source=surface['text'], owned=viewport_owned)
    scene = alignment_scene(surface=surface, version=version, cursor=cursor, complete=complete,
                            label=label, width=width, font_scale=u.font_scale(), show_counts=show_counts)
    from .pitch_practice_ui import pitch_token_colors
    scale = max(1, u.font_scale())
    colors = pitch_token_colors(scene['data']['surface']['units'])
    children = []
    for row in scene['layout']['rows']:
        blocks = []
        for cell in row:
            unit = scene['data']['surface']['units'][cell['index']]
            beat, color = cell['beat_width'], colors[cell['index']]
            marks = []
            for value in unit['levels']:
                marks.append(u.col(u.col(height=(4 if value == 'H' else 26) * scale),
                    u.col(width=beat - min(1.5 * scale, beat * .16), height=3, background_color=color),
                    width=beat, height=40 * scale))
            track = u.row(*marks, height=40 * scale)
            source = u.label(cell['text'], size=18, text_role='study', bold=unit['active'], wrap=False,
                margin_bottom=0, width=cell['width'] - 8, color=color,
                background_color='#FFE6AA' if unit['active'] else None)
            block = u.col(track, source, padding=4, width=cell['width'],
                height=scene['layout']['row_content_height'], margin_right=8)
            block._pitch_cell = deepcopy(cell)
            blocks.append(block)
        children.append(u.row(*blocks, height=scene['layout']['row_content_height'], margin_bottom=12))
    result = u.col(*children, padding=8, width=scene['layout']['content_width'])
    result._pitch_diagram, result._pitch_layout = True, scene['layout']
    result._feedback_fragment = {'kind': 'surface_pitch_diagram', **deepcopy(scene['data'])}
    return _static_viewport(u, result, width=width, label=None, source=surface['text'], owned=viewport_owned)


def _static_viewport(u, diagram, *, width, label, source, owned):
    """Static lessons/results still allow an intact oversized word to be read.

    Editing controls own their bidirectional viewport and cursor following.
    This wrapper is only for otherwise unbounded presentation surfaces.
    """
    layout = diagram._pitch_layout
    if owned or layout['content_width'] <= width:
        return diagram
    import toga
    from toga.style import Pack
    height = 24 + len(layout['rows']) * layout['row_height']
    if label and label != source:
        scale = u.font_scale()
        height += max(1, math.ceil(_text_width(label, 18 * scale) / max(1, width - 16))) * 28 * scale + 8
    viewport = toga.ScrollContainer(content=diagram, horizontal=True, vertical=False,
        style=Pack(width=width, height=height))
    # The ordinary wrapper can receive lesson captions. Only its real child
    # owns _pitch_diagram / feedback, avoiding duplicate captured fragments.
    result = u.col(viewport, margin_bottom=8)
    result._pitch_static_viewport = viewport
    return result
