"""Optional teaching glosses stay collapsed and separate from quiz controls."""
from copy import deepcopy

TITLE = '语序参考译文（实验）'


def order_translation_rows(app, text, translation='', *, explicit=None, title=TITLE, show_source=False):
    """Return a fresh disclosure without changing content or learner records.

    Call only from teaching, authoring previews, or post-answer feedback. The
    annotated-sentence and question-support renderers deliberately do not call
    this function: even a collapsed control would offer an answer shortcut.
    Bundled material is looked up against its existing Chinese translation.
    Question views require explicit authored metadata after submission; they
    must not invent translations for legacy prompts without an existing one.
    """
    from . import ui_components as u
    from .order_translations import order_translation_for

    record = order_translation_for(text, translation, explicit=explicit)
    if record is None:
        return []
    snapshot = deepcopy(record)
    body = u.col()
    opened = False

    def toggle(widget=None, **kwargs):
        nonlocal opened
        # A stale callback from a previously visible hint cannot add meaning
        # support while the learner has returned to an independent question.
        if getattr(app, 'page', '') in {'question', 'learning_question', 'ai_question'}:
            return
        opened = not opened
        body.clear()
        if opened:
            if show_source:
                body.add(u.label(text, size=17, wrap=False, text_role='study'))
            body.add(u.label(snapshot['text'], size=15, wrap=False, text_role='ui'))
        trigger.text = title + (' ▾' if opened else ' ▸')
        result._feedback_fragment['expanded'] = opened

    trigger = u.button(title + ' ▸', toggle, compact=True, multiline=True,
                       font_size=12, color=u.MUTED, margin_bottom=2)
    result = u.col(trigger, body, margin_bottom=6)
    result._order_translation_disclosure = True
    result._feedback_fragment = {'kind': 'order_translation', 'source_text': text,
        'source_translation': translation, 'record': snapshot, 'expanded': False,
        'experimental': True, 'title': title, 'show_source': show_source}
    return [result]
