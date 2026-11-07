"""Shared pitch alignment canvas fixtures and assertions; no collected scenarios."""



def display(words=('雨', 'が', '降る', '。'), *, entered=None, cursor=0, complete=False):
    units, offset = [], 0
    for i, word in enumerate(words):
        punctuation = word in ('。', '、', '！', '？')
        units.append({'id': f'u{i}', 'start': offset, 'end': offset + len(word), 'text': word,
            'role': 'punctuation' if punctuation else 'word',
            'levels': list((entered or {}).get(i, [])), 'active': i == cursor and not complete,
            'complete': punctuation or complete or (cursor is not None and i < cursor)})
        offset += len(word)
    return {'version': 3, 'surface': {'text': ''.join(words), 'units': units},
            'cursor': None if complete else cursor, 'complete': complete, 'label': ''.join(words)}


def cells(scene):
    return [cell for row in scene['layout']['rows'] for cell in row]


def anchors(scene):
    return [(e['text'], e['x'], e['baseline_y']) for e in scene['glyphs']]
