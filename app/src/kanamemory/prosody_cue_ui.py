"""Small vector reference curves shared by course, guide and tutor prompts."""
from copy import deepcopy

from .prosody_cue import cue_layout, validate_cue


def cue_widget(question, *, width=320):
    import toga
    from toga.style import Pack
    from toga.widgets.canvas import Fill, Stroke, FillText, MoveTo, LineTo
    from toga.platform import current_platform
    from .text_layout import font_scale
    from .ui_components import col, label, MUTED

    cue = validate_cue(question['prosody_cue'], question['prompt'])
    colors = ('#175E55', '#935121', '#4356A0', '#824467')

    def redraw(widget, width, height=None, **kwargs):
        if width < 80:
            return
        scale = max(.5, min(4, float(font_scale())))
        if getattr(widget, '_cue_size', None) == (width, scale):
            return
        widget._cue_size = width, scale
        def font(size):
            return toga.Font('serif', size=size * .75 / (scale if current_platform == 'android' else 1))
        layout = cue_layout(cue, question['prompt'], width=width, scale=scale,
                            measure=lambda text, size: widget.measure_text(text, font(size))[0])
        actions = []
        for unit in layout['units']:
            color = colors[unit['color_index']]
            if unit['points']:
                stroke = Stroke(stroke_style=color, line_width=2.5)
                stroke.drawing_actions.extend([MoveTo(*unit['points'][0]),
                                              *(LineTo(*p) for p in unit['points'][1:])])
                actions.append(stroke)
            fill = Fill(fill_style=color)
            fill.drawing_actions.append(FillText(unit['text'], unit['x'], unit['y'], font=font(unit['font_size'])))
            actions.append(fill)
        widget.root_state.drawing_actions[:] = actions
        widget.style.height = layout['height']
        widget._prosody_layout = layout
        widget.redraw()

    canvas = toga.Canvas(on_resize=redraw, style=Pack(height=83, margin_bottom=4))
    if current_platform == 'android':
        canvas._impl.native.setContentDescription(question['prompt'] + '，参考语调曲线')
        canvas._impl.native.setImportantForAccessibility(1)
    redraw(canvas, width)
    block = col(label('参考读法', size=12, color=MUTED), canvas)
    block._feedback_fragment = {'kind': 'question_prompt', 'question_id': question.get('id'),
        'target_id': question.get('target_id'), 'prosody_cue': deepcopy(cue),
        'instruction': question.get('task', '')}
    return block
