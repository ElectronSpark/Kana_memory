"""The short cue above a question's controls, with its Chinese hint under it."""
from .question_presentation import question_presentation

SHOW_HINT, HIDE_HINT = "中文提示", "收起提示"


def instruction_view(question, known_ids=None):
    """What a question page shows of the task: (cue, its text role, the Chinese hint, whether it is folded).

    The one place that decides it (question_presentation); the ordinary
    question page and the practice decks (design 12 N12) lay it out with
    instruction_layout. With known_ids, kanji words of the cue the learner has
    not learned carry inline kana (question_readings rule 8); the stored task
    is unchanged.
    """
    presentation = question_presentation(question)
    text = presentation["instruction"]
    if text and known_ids is not None:
        from .question_readings import instruction_label
        text = instruction_label(question, text, known_ids) or text
    return text, presentation["instruction_role"], presentation["folded_hint"], bool(presentation.get("fold_hint"))


def instruction_layout(ui, view):
    """Widgets for an instruction_view: the public cue and the Chinese hint as a line under it.

    Only when the view says folded (a question the content marks as
    answerable from context) does the hint wait behind a button and leave the
    tree while it is closed. Showing or opening it is display only: it writes no
    draft and marks no assistance.
    """
    text, role, hint, folded = view
    children = []
    if text:
        children.append(ui.label(text, size=13, color=ui.MUTED, text_role=role))
    if hint and not folded:
        children.append(ui.label(hint, size=13, color=ui.MUTED))
    elif hint:
        holder = ui.col()
        line = ui.label(hint, size=13, color=ui.MUTED)

        def toggle(widget=None, **kwargs):
            if line in holder.children:
                holder.remove(line)
                widget.text = SHOW_HINT
            else:
                holder.add(line)
                widget.text = HIDE_HINT
        children.extend((ui.button(SHOW_HINT, toggle, compact=True, font_size=12), holder))
    return children


def instruction_widgets(ui, question, known_ids=None):
    """Widgets for the public cue and the Chinese hint under it (instruction_view laid out by instruction_layout)."""
    return instruction_layout(ui, instruction_view(question, known_ids))
