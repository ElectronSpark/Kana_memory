"""The short cue above a question's controls, with its Chinese hint under it."""
from .question_presentation import question_presentation

SHOW_HINT, HIDE_HINT = "中文提示", "收起提示"


def instruction_widgets(ui, question, known_ids=None):
    """Widgets for the public cue and the Chinese hint as a line under it.

    Only when the presentation's fold_hint is True (a question the content marks
    as answerable from context) does the hint wait behind a button and leave the
    tree while it is closed. Showing or opening it is display only: it writes no
    draft and marks no assistance.
    With known_ids, kanji words of the cue the learner has not learned carry
    inline kana (question_readings rule 8); the stored task is unchanged.
    """
    presentation = question_presentation(question)
    children = []
    if presentation["instruction"]:
        text = presentation["instruction"]
        if known_ids is not None:
            from .question_readings import instruction_label
            text = instruction_label(question, text, known_ids) or text
        children.append(ui.label(text, size=13, color=ui.MUTED,
                                 text_role=presentation["instruction_role"]))
    hint = presentation["folded_hint"]
    if hint and not presentation.get("fold_hint"):
        children.append(ui.label(hint, size=13, color=ui.MUTED))
    elif hint:
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
