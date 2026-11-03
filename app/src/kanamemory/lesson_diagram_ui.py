"""Static vector lessons drawn natively, sharing geometry with SVG exports."""
from copy import deepcopy
import re

from .lesson_diagram_scene import build_scene
from .lesson_diagrams import accessible_description


def lesson_canvas(diagram):
    import toga
    from toga.fonts import BOLD, NORMAL
    from toga.platform import current_platform
    from toga.style import Pack
    from toga.widgets.canvas import Fill, Stroke, FillText, Rect, Ellipse, MoveTo, LineTo
    from .text_layout import font_scale

    def redraw(widget, width, height=None, **kwargs):
        if width <= 0:
            return
        scale = font_scale()
        key = (round(width, 1), scale)
        if getattr(widget, '_diagram_geometry', None) == key:
            return
        widget._diagram_geometry = key
        fonts = {}

        def font(size, bold=False, family='sans-serif'):
            key = (size, bold, family)
            if key not in fonts:
                # Canvas coordinates are DIP; Toga font sizes are points and
                # Android already applies SP accessibility scaling internally.
                points = size * .75 / (scale if current_platform == 'android' else 1)
                fonts[key] = toga.Font(family=family, size=points, weight=BOLD if bold else NORMAL)
            return fonts[key]

        def measure(value, size):
            # Bold serif is the conservative choice for either content role.
            return max(widget.measure_text(value, font(size, True, family))[0]
                       for family in ('sans-serif', 'serif'))

        scene = build_scene(diagram, width=width, font_scale=scale, measure=measure)
        actions = []
        for element in scene['elements']:
            kind = element['kind']
            if kind == 'line':
                action = Stroke(stroke_style=element['color'], line_width=element['weight'],
                                line_dash=[5,4] if element['dashed'] else None)
                action.drawing_actions.extend([MoveTo(element['x1'], element['y1']), LineTo(element['x2'], element['y2'])])
                actions.append(action)
            elif kind in {'rect', 'ellipse'}:
                shape = (Rect(element['x'], element['y'], element['w'], element['h']) if kind == 'rect' else
                         Ellipse(element['x'], element['y'], element['rx'], element['ry']))
                filled = Fill(fill_style=element['fill'])
                filled.drawing_actions.append(shape)
                border = Stroke(stroke_style=element['stroke'], line_width=1.5,
                                line_dash=[5,4] if element.get('dashed') else None)
                border.drawing_actions.append(shape)
                actions.extend([filled, border])
            else:
                filled = Fill(fill_style=element['color'])
                filled.drawing_actions.append(FillText(element['text'], element['x'], element['y']+element['size'],
                    font=font(element['size'], element['bold'], element['family'])))
                actions.append(filled)
        widget.root_state.drawing_actions[:] = actions
        widget._diagram_scene = scene
        widget._feedback_fragment = {'kind':'lesson_diagram', 'diagram':deepcopy(diagram),
                                     'width':width, 'font_scale':scale}
        if widget.style.height != scene['height']:
            widget.style.height = scene['height']
        widget.redraw()

    canvas = toga.Canvas(on_resize=redraw, style=Pack(height=240, margin_bottom=8))
    canvas._lesson_vector = True
    if current_platform == 'android':
        canvas._impl.native.setContentDescription(accessible_description(diagram))
        canvas._impl.native.setImportantForAccessibility(1)
    redraw(canvas, 320)
    return canvas


def diagram_widget(u, diagram, *, speak=None):
    if diagram['kind'] == 'pitch':
        from .pitch_practice_ui import diagram_data, pitch_diagram
        try:
            result = pitch_diagram(u, **diagram_data(diagram), show_counts=True)
        except ValueError:
            # Private/older teaching may still use ordinal placeholders. Keep
            # its own explanation readable without fabricating a kana example.
            result = u.col(u.label(accessible_description(diagram), size=14),
                           padding=8, margin_bottom=10, background_color=u.WHITE)
            result._pitch_legacy_description = True
        else:
            if diagram.get('caption'):
                result.add(u.label(diagram['caption'], size=14, color=u.MUTED))
        result._lesson_vector = True
        result._feedback_fragment = {'kind':'lesson_diagram', 'diagram':deepcopy(diagram)}
        return result
    canvas = lesson_canvas(diagram)
    label = u.label(diagram['label'], size=12, bold=True, color=u.MUTED, margin_bottom=5,
                    flex=1 if speak else 0)
    text = ''.join(node['text'] for node in diagram['nodes'])
    if speak and diagram['kind'] == 'beats' and re.fullmatch(r'[ぁ-ゖァ-ヺー]+', text):
        speaker = u.speaker_button(lambda w, value=text: speak(value))
        speaker._example_audio_text = text
        label = u.row(label, speaker)
    result = u.col(label,
                   canvas, padding=8, margin_bottom=10, background_color=u.WHITE)
    result._feedback_fragment = {'kind':'lesson_diagram', 'diagram':deepcopy(diagram)}
    return result
