"""The short cue above a question's controls, with its Chinese hint one tap away."""
from .question_presentation import question_presentation

SHOW_HINT, HIDE_HINT = "中文提示", "收起提示"


def instruction_widgets(ui, question):
    """Widgets for the public cue; the hint leaves the tree while it is closed.

    Opening it is display only. It writes no draft and marks no assistance, since
    this text was always on screen before it was folded behind the button.
    """
    presentation = question_presentation(question)
    children = []
    if presentation["instruction"]:
        children.append(ui.label(presentation["instruction"], size=13, color=ui.MUTED,
                                 text_role=presentation["instruction_role"]))
    hint = presentation["folded_hint"]
    if hint:
        holder = ui.col()
        text = ui.label(hint, size=13, color=ui.MUTED)

        def toggle(widget=None, **kwargs):
            if text in holder.children:
                holder.remove(text)
                widget.text = SHOW_HINT
            else:
                holder.add(text)
                widget.text = HIDE_HINT
        children.extend((ui.button(SHOW_HINT, toggle, compact=True, font_size=12), holder))
    return children
