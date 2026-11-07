"""One shared retained control for reviewed multi-gap exercises."""
from copy import deepcopy
import unicodedata

import kanamemory.multi_cloze as cloze
from .cloze_presentation import choice_display, draft_prompt_plan


def prompt_view(app, u, question, draft, preferences=None, *, size=20):
    """A retained sentence view; only saved edits may replace its blank markers."""
    from .question_support import prompt_support, render_prompt_plan

    base = prompt_support(question, preferences)
    holder = u.col()
    holder._feedback_fragment = {'kind': 'multi_cloze_prompt', 'question_id': question.get('id')}

    def update(value):
        plan = draft_prompt_plan(question, value, base)
        block = render_prompt_plan(app, question, plan, size=size)
        holder.clear()
        holder.add(block)
        holder._prompt_support = plan
        holder._feedback_fragment.update(plan=deepcopy(plan), draft=deepcopy(value), font_size=size)

    holder._render_cloze_draft = update
    update(draft)
    return holder


def _columns(app, u, labels):
    try:
        width = max(160, float(app.main_window.size.width) - 48)
    except (AttributeError, TypeError, ValueError):
        width = 312
    scale = getattr(u, "font_scale", lambda: 1)()
    longest = max((sum(2 if unicodedata.east_asian_width(c) in {"W", "F"} else 1
                       for c in label) for label in labels), default=1)
    return 2 if longest * 8 * max(1, scale) + 32 <= width / 2 else 1


def _style(widget, **values):
    for key, value in values.items():
        if isinstance(widget.style, dict):
            widget.style[key] = value
        else:
            setattr(widget.style, key, value)


def controls(app, u, question, draft, on_change, on_submit, is_active, *, prompt=None):
    """Persist before display; callbacks are inert after leaving or submitting."""
    spec = cloze.specification(question)
    cloze.validate_draft(question, draft)
    state = {**deepcopy(draft), **(cloze.initial_draft(question) if cloze.STATE_KEY not in draft else {})}
    holder, slots_box, choices_box = u.col(), u.col(), u.col()
    status = u.label("", size=12, color=u.MUTED)
    context = {"question": question, "draft": state, "locked": False,
               "slots": {}, "choices": [], "status": status, "prompt": prompt}
    holder._multi_cloze_context = context
    holder._feedback_fragment = {"kind": "multi_cloze", "question": deepcopy(question), "draft": deepcopy(state)}

    def active():
        return not context["locked"] and is_active()

    def save(proposal):
        if not active():
            return
        try:
            result = on_change(deepcopy(proposal))
        except Exception as exc:
            from .diagnostic_events import record_error
            record_error("multi_cloze", "save", exc)
            status.text = "输入未保存，请重试。"
            return
        if result is False:
            status.text = "输入未保存，请重试。"
            return
        context["draft"] = proposal
        holder._feedback_fragment["draft"] = deepcopy(proposal)
        status.text = ""
        render()

    def choose_slot(identity):
        if active():
            save(cloze.edit_slot(question, context["draft"], slot_id=identity))

    def choose_value(identity, value):
        if active() and context["draft"][cloze.STATE_KEY]["active_slot"] == identity:
            save(cloze.edit_slot(question, context["draft"], slot_id=identity, value=value))

    def clear(widget=None):
        if active():
            save(cloze.undo_selection(question, context["draft"]))

    def submit(widget=None):
        if not active() or not context["draft"].get("answer"):
            return
        context["locked"] = True
        render()
        try:
            result = on_submit(context["draft"]["answer"])
        except Exception as exc:
            from .diagnostic_events import record_error
            record_error("multi_cloze", "commit", exc)
            result = False
        if result is False:
            context["locked"] = False
            status.text = "未提交，请重试。"
            render()
        elif result is not True:
            # A guide can show local feedback and keep this same exercise
            # editable. Only an explicit terminal receipt locks its controls.
            context["locked"] = False
            status.text = ""
            render()

    labels = [chr(0x2460 + index) for index in range(len(spec["slots"]))]
    count = min(3, len(labels))
    buttons = []
    for index, slot in enumerate(spec["slots"]):
        button = u.button(labels[index], lambda w=None, identity=slot["id"]: choose_slot(identity),
                          flex=1, compact=True, multiline=True, text_role="study")
        context["slots"][slot["id"]] = button
        buttons.append(button)
    for start in range(0, len(buttons), count):
        slots_box.add(u.row(*buttons[start:start + count]))
    context["clear"] = u.button("⌫", clear, compact=True, flex=1)
    context["submit"] = u.button("提交", submit, primary=True, flex=2)
    holder.add(slots_box, choices_box, u.row(context["clear"], context["submit"]), status)

    def render():
        draft_state = context["draft"][cloze.STATE_KEY]
        identity, values = draft_state["active_slot"], draft_state["values"]
        for index, slot in enumerate(spec["slots"]):
            button = context["slots"][slot["id"]]
            button.text = chr(0x2460 + index) + (' ✓' if slot['id'] in values else '')
            selected = slot["id"] == identity
            _style(button, font_weight="bold" if selected else "normal",
                   background_color=u.GREEN if selected else "#E4EEE8",
                   color=u.WHITE if selected else u.GREEN)
            button.enabled = not context["locked"]
        choices_box.clear()
        slot = next(slot for slot in spec["slots"] if slot["id"] == identity)
        choices = []
        display = choice_display(slot['choices'])
        # A shortened choice only makes sense alongside its in-sentence context.
        labels = display['choices'] if prompt is not None else [{'label': value, 'value': value} for value in slot['choices']]
        for item in labels:
            button = u.button(item['label'], lambda w=None, i=identity, v=item['value']: choose_value(i, v),
                              flex=1, multiline=True, text_role="study")
            button.enabled = not context["locked"]
            choices.append(button)
        context["choices"] = choices
        count = _columns(app, u, [item['label'] for item in labels])
        for start in range(0, len(choices), count):
            choices_box.add(u.row(*choices[start:start + count]))
        context["clear"].enabled = bool(values) and not context["locked"]
        context["submit"].enabled = bool(context["draft"].get("answer")) and not context["locked"]
        if prompt is not None:
            prompt._render_cloze_draft(context['draft'])

    render()
    return holder
