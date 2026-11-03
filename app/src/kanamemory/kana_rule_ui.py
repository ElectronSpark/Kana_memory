"""Contextual rule help after exposure, never on an unanswered question."""
from .kana_reading_rules import explicit_reading_rules, READING_RULE_IDS


def contextual_rule_ids(question, analysis=None):
    metadata = getattr(question, "metadata", {}) or {}
    if analysis is not None:
        if analysis.get("correct"):
            return ()
        # The explanation follows the actual mismatch, not every phenomenon
        # anywhere in the word. Ambiguity never becomes scheduling evidence.
        text = ''.join(str(error.get("expected", "")) + str(error.get("actual", ""))
                       for error in analysis.get("errors", ()) if isinstance(error, dict))
    else:
        text = str(metadata.get("kana_symbol") or metadata.get("kana") or metadata.get("symbol") or "")
        if not text:
            text = str(getattr(question, "answer", ""))
    rules = set(explicit_reading_rules(text))
    explicit = metadata.get("reading_rule_ids", ())
    if not isinstance(explicit, (list, tuple)):
        explicit = ()
    if analysis is None:
        rules.update(rule for rule in explicit if rule in READING_RULE_IDS)
    else:
        # A lexical long vowel can be explained only when this precise
        # extension is the observed error, not whenever the word contains it.
        from .kana_rule_evidence import word_rule_observations
        rules.update(observation["rule_id"] for observation in word_rule_observations(analysis)
                     if observation["outcome"] == "incorrect")
    return tuple(rule for rule in READING_RULE_IDS if rule in rules)


def rule_help_widgets(app, rule_ids, *, expanded=False):
    from . import ui_components as u
    from .learning_content import get_learning_item
    from .structured_lesson_ui import teaching_block
    from .structured_lessons import lesson_view
    widgets = []
    for identity in dict.fromkeys(rule_ids):
        item = get_learning_item(identity)
        if not item:
            continue
        body = u.col()
        opened = [expanded]
        if expanded:
            body.add(teaching_block(u, item, compact=True))
        else:
            body.add(u.label(lesson_view(item)["focus"], size=14, color=u.MUTED))
        def toggle(widget, *, target=item, container=body, state=opened, **kwargs):
            state[0] = not state[0]
            container.clear()
            if state[0]:
                container.add(teaching_block(u, target, compact=True))
            else:
                container.add(u.label(lesson_view(target)["focus"], size=14, color=u.MUTED))
            widget.text = ("▾ " if state[0] else "▸ ") + target["title"]
        trigger = u.button(("▾ " if expanded else "▸ ") + item["title"], toggle,
                           compact=True, multiline=True, font_size=14)
        panel = u.col(trigger, body, margin_bottom=4)
        panel._feedback_fragment = {"kind": "kana_rule_help", "target_id": identity,
                                    "item_snapshot": item, "initially_expanded": expanded}
        widgets.append(panel)
    return widgets
