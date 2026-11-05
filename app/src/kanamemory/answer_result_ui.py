"""Shared result typography; grading, revelation and scheduling stay in callers."""


def answer_text(question, answer=None):
    """Display structured answers as ordinary Japanese, never their encoding."""
    from .multi_cloze import is_cloze_question, answer_label
    if is_cloze_question(question):
        return answer_label(question, answer)
    return str(question.get('answer', '') if answer is None else answer)


def distinct_explanation(explanation, answer):
    """Remove exact answer-only lines, retaining translations and explanations."""
    return "\n".join(line for line in str(explanation or "").splitlines()
                     if line.strip() != str(answer).strip()).strip()


def entered_answer_row(u, text, *, text_role):
    width = (5 * 12 * 96 / 72 + 4) * u.font_scale()
    return u.row(u.label("我的回答：", size=12, color=u.MUTED, width=width),
                 u.label(text, size=12, color=u.MUTED, flex=1, text_role=text_role))
