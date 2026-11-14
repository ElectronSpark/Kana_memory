"""One answer composer for course, guides, tutoring and retention checks."""
from .answer_input import BASIC, VOICED, SMALL, SYMBOLS, EXTRA, katakana, public_characters, japanese_text
from .answer_input import TEXT_MODES, has_latin, romaji_reading_input, reading_input_conversion
from .question_support import learning_text_role
from .typography import configure_text_role
from .ui_components import dismiss_keyboard

INCOMPLETE_ROMAJI = "请补全罗马音，或切换假名按键修改。"
# Latin letters typed into a kana keypad field (via ABC) when the kind has no
# romaji composition: refused, never graded as a wrong kana answer.
KANA_ONLY = "请用假名作答（可切到罗马音输入）"
KANA_ONLY_PLAIN = "请用假名作答"
REFUSALS = frozenset({INCOMPLETE_ROMAJI, KANA_ONLY, KANA_ONLY_PLAIN})
SYSTEM_KEYBOARD = "系统键盘 · 直接输入假名或汉字"


def kana_only_message(question):
    """Name the romaji switch only where that keypad button exists."""
    return KANA_ONLY if romaji_reading_input(question) else KANA_ONLY_PLAIN


def _refuse(message, status, field, *, near_field=False):
    if status is not None:
        status.text = message
    if field is not None:
        field.refused_status = status
        note = getattr(field, "refusal_note", None)
        if near_field and note is not None:
            note(message)
        # The message sits below Submit: with the keyboard up it would be off screen.
        dismiss_keyboard(field)


def prepare_submission(question, answer, status, *, revealed=False, field=None):
    """Resolve current UI input only; never reinterpret stored past answers.

    `field` is the answer_controls input, whose script choice matches its preview.
    A kana keypad field (`kana_only`) refuses Latin letters it cannot convert.
    """
    result = None if revealed else reading_input_conversion(question, answer)
    if result is None:
        # Only this question's own kana keypad field (answer_controls) can
        # refuse: a pitch/choice question submits through other controls, and
        # a stale field of an earlier text question must not judge its answer.
        if (not revealed and getattr(field, "kana_only", False) and getattr(field, "kana_question", None) is question
                and question.get("input_mode", question.get("response", "choice")) in TEXT_MODES and has_latin(answer)):
            _refuse(kana_only_message(question), status, field, near_field=True)
            return False, answer
        return True, answer
    if not result["complete"]:
        _refuse(INCOMPLETE_ROMAJI, status, field)
        return False, answer
    if getattr(field, "kana_script", None) == "katakana":
        return True, katakana(result["text"])
    return True, result["text"]


def answer_controls(u, question, value, changed, active, *, system_japanese=False):
    """Return a normal value-bearing field and its small native control tree.

    Programmatic edits use the same draft callback as ordinary keyboard edits.
    The fixed alphabet is never filtered to letters in a correct answer. The
    separate public-character drawer can only repeat characters already shown.
    """
    native = japanese_text(question) and not system_japanese
    romaji = romaji_reading_input(question)
    state = {"script": "hiragana", "group": "清音", "editing": False}
    # Empty unrestricted reading/grammar answers open Latin input directly.
    # A saved kana draft keeps its original keypad and is never rewritten.
    if romaji and (not (value or "").strip() or reading_input_conversion(question, value) is not None):
        state["group"] = "罗马音"
    field = configure_text_role(u.toga.TextInput(value=value, readonly=native,
        placeholder="用下方按键作答" if native else "输入答案",
        style=u.text_style(font_size=18, margin_bottom=8)), learning_text_role(question, "input"))
    field.kana_script = state["script"]
    field.kana_only = native
    field.kana_question = question  # prepare_submission refuses only for this question
    original_change = changed
    preview = u.label("", size=22, text_role="study")
    pending = u.label("", size=12, color=u.MUTED)
    typed = u.col()

    def refresh_preview():
        result = reading_input_conversion(question, field.value or "")
        text = result["text"] if result else ""
        preview.text = katakana(text) if state["script"] == "katakana" else text
        pending.text = "未完成：" + result["pending"][:40] if result and not result["complete"] else ""
        if native:
            # An empty label still takes a line; keep Submit close to the keyboard.
            wanted = [w for w in (preview, pending) if w.text] if state["group"] == "罗马音" else []
            if list(typed.children) != wanted:
                typed.clear()
                typed.add(*wanted)

    # Beside the field: shown only for a refusal (an empty label still takes a line).
    note = u.label("", size=12, color=u.MUTED)
    note_slot = u.col()

    def show_note(text):
        note.text = text
        if list(note_slot.children) != ([note] if text else []):
            note_slot.clear()
            if text:
                note_slot.add(note)
    field.refusal_note = show_note

    def clear_refusal():
        # The answer changed, so the refusal no longer describes it. The pending
        # hint still names what is missing; any other status text is left alone.
        status = getattr(field, "refused_status", None)
        if status is not None and status.text in REFUSALS:
            status.text = ""
        if note.text:
            show_note("")

    def on_change(widget=None, **kwargs):
        if active() and field.enabled and not state["editing"]:
            original_change(field)
            clear_refusal()
            refresh_preview()
    field.on_change = on_change
    if not native:
        if romaji:
            field.placeholder = "输入罗马音或假名，例如 a・ka・sa"
            refresh_preview()
        return field, [field, preview, pending] if romaji else [field]
    keyboard = u.col()
    key_slot = u.col()
    system_note = u.label(SYSTEM_KEYBOARD, size=12, color=u.MUTED)

    def edit(value):
        if not active() or not field.enabled:
            return
        state["editing"] = True
        try:
            field.value = value[:10000]
        finally:
            state["editing"] = False
        original_change(field)
        clear_refusal()
        refresh_preview()

    def render():
        keyboard.clear()
        group = state["group"]
        field.readonly = group not in {"ABC", "罗马音"}
        field.placeholder = {"罗马音": "输入罗马音，例如 a・ka・sa",
                             "ABC": "用系统键盘输入假名或汉字"}.get(group, "用下方按键作答")
        refresh_preview()
        # Typing modes have no keys: a reserved key area would only push Submit
        # further from the system keyboard. Pack display="none" keeps the space.
        # ABC is the system keyboard; one line says so instead of an empty area.
        if group in {"ABC", "罗马音"}:
            key_slot.clear()
            if group == "ABC":
                key_slot.add(system_note)
            return
        if list(key_slot.children) != [keys]:
            key_slot.clear()
            key_slot.add(keys)
        letters = {"清音": BASIC, "浊音": VOICED, "小字": SMALL,
                   "符号": SYMBOLS + EXTRA, "题干用字": "".join(public_characters(question))}[group]
        if state["script"] == "katakana" and group in {"清音", "浊音", "小字"}:
            letters = katakana(letters)
        from .kana_keyboard_ui import key_table
        from .text_layout import font_scale
        table, height, _ = key_table(u, list(letters), lambda char: edit((field.value or "") + char), scale=font_scale())
        # The whole group is visible at its own height; the page scrolls vertically.
        keys.style.height = height
        keyboard.add(table)

    def select(group):
        if active() and field.enabled:
            state["group"] = group
            render()
            if field.readonly:
                dismiss_keyboard(field)  # It would cover the keys just opened.

    def switch_script(widget=None):
        if active() and field.enabled:
            state["script"] = "katakana" if state["script"] == "hiragana" else "hiragana"
            field.kana_script = state["script"]
            script.text = "アイウ" if state["script"] == "katakana" else "あいう"
            render()

    script = u.button("あいう", switch_script, compact=True, flex=1, text_role="study")
    toolbar = u.row(script, *[u.button(group, lambda w, g=group: select(g), compact=True, flex=1)
                             for group in ("清音", "浊音", "小字")])
    extra = [u.button("符号", lambda w: select("符号"), compact=True, flex=1),
             u.button("罗马音" if romaji else "ABC", lambda w: select("罗马音" if romaji else "ABC"), compact=True, flex=1)]
    if public_characters(question):
        extra.append(u.button("题干用字", lambda w: select("题干用字"), compact=True, flex=1))
    extra.extend([u.button("⌫", lambda w: edit((field.value or "")[:-1]), compact=True, flex=1),
                  u.button("清空", lambda w: edit(""), compact=True, flex=1)])
    # Horizontal-only, sized to the whole key table (as the word keypad): a
    # fixed-height inner vertical scroller lost its drags to the scrolling page
    # on Android and showed only the first four rows of 清音.
    keys = u.toga.ScrollContainer(content=keyboard, horizontal=True, vertical=False,
                                   style=u.Pack(margin_bottom=6))
    render()
    return field, [field, note_slot, toolbar, u.row(*extra), typed, key_slot]
