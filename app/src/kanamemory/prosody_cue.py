"""Public, authored speech-contour stimuli; never infer pitch from spelling.

Coordinates describe an illustrative relative contour, not measured F0 or a
universal pronunciation. Word text stays on one baseline and wraps as a unit.
"""
from copy import deepcopy
from math import isfinite


def validate_cue(value, prompt):
    if (not isinstance(value, dict) or set(value) != {'version', 'units', 'source_ids'}
            or type(value['version']) is not int or value['version'] != 1):
        raise ValueError('Invalid prosody cue')
    units = value['units']
    if not isinstance(units, list) or not 1 <= len(units) <= 32:
        raise ValueError('Invalid prosody units')
    if (not isinstance(value['source_ids'], list) or not value['source_ids']
            or any(not isinstance(s, str) or not s or len(s) > 160 for s in value['source_ids'])):
        raise ValueError('Prosody cue requires attribution')
    for unit in units:
        if not isinstance(unit, dict) or set(unit) != {'text', 'mora_count', 'points'}:
            raise ValueError('Invalid prosody unit')
        if (not isinstance(unit['text'], str) or not unit['text'] or len(unit['text']) > 40
                or '\n' in unit['text'] or type(unit['mora_count']) is not int
                or not 0 <= unit['mora_count'] <= 32):
            raise ValueError('Invalid prosody text or mora count')
        points = unit['points']
        if not isinstance(points, list) or not 0 <= len(points) <= 80:
            raise ValueError('Invalid contour points')
        previous = -1
        for point in points:
            if (not isinstance(point, list) or len(point) != 2 or any(type(x) not in (int, float)
                    or not isfinite(x) for x in point) or not 0 <= point[0] <= 1
                    or point[0] < previous or not 0 <= point[1] <= 3):
                raise ValueError('Invalid contour coordinate')
            previous = point[0]
        if unit['mora_count'] and (len(points) < 2 or points[0][0] != 0 or points[-1][0] != 1):
            raise ValueError('Incomplete word contour')
        if not unit['mora_count'] and (points or any(c not in '。、？！!?「」' for c in unit['text'])):
            raise ValueError('Only punctuation may have no contour')
    if ''.join(u['text'] for u in units) != prompt:
        raise ValueError('Prosody cue differs from public prompt')
    return deepcopy(value)


def cue_layout(value, prompt, *, width=320, scale=1, measure=None):
    cue = validate_cue(value, prompt)
    if not isfinite(width) or width < 80 or not isfinite(scale) or not .5 <= scale <= 4:
        raise ValueError('Invalid contour viewport')
    measure = measure or (lambda text, size: len(text) * size)
    size, line_height = 24 * scale, 83 * scale
    result, x, row = [], 0, 0
    for index, unit in enumerate(cue['units']):
        text_width = measure(unit['text'], size)
        cell_width = min(width, max(text_width + 4, unit['mora_count'] * 10 * scale))
        if x and x + cell_width > width:
            row += 1
            x = 0
        # Very long words scale horizontally, never split kana from its curve.
        font_size = size * min(1, max(1, cell_width - 4) / max(1, text_width))
        points = [(x + 2 + p[0] * (cell_width - 4), row * line_height + (3 - p[1]) * 9 * scale + 5)
                  for p in unit['points']]
        result.append({'text': unit['text'], 'x': x + 2, 'y': row * line_height + 62 * scale,
                       'width': cell_width, 'font_size': font_size, 'row': row,
                       'color_index': index % 4, 'points': points})
        x += cell_width
    return {'units': result, 'height': (row + 1) * line_height, 'width': width}
