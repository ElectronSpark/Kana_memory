"""One answer composer for course, guides, tutoring and retention checks."""
from .answer_input import BASIC, VOICED, SMALL, SYMBOLS, EXTRA, katakana, public_characters, japanese_text
from .answer_input import romaji_reading_input, reading_input_conversion
from .question_support import learning_text_role
from .typography import configure_text_role


def prepare_submission(question, answer, status, *, revealed=False):
    """Resolve current UI input only; never reinterpret stored past answers."""
    result = None if revealed else reading_input_conversion(question, answer)
    if result is None:
        return True, answer
    if not result["complete"]:
        if status is not None:
            status.text = "请补全罗马音，或切换假名按键修改。"
        return False, answer
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
    original_change = changed
    preview = u.label("", size=22, text_role="study")
    pending = u.label("", size=12, color=u.MUTED)

    def refresh_preview():
        result = reading_input_conversion(question, field.value or "")
        text = result["text"] if result else ""
        preview.text = katakana(text) if state["script"] == "katakana" else text
        pending.text = "未完成：" + result["pending"][:40] if result and not result["complete"] else ""

    def on_change(widget=None, **kwargs):
        if active() and field.enabled and not state["editing"]:
            original_change(field)
            refresh_preview()
    field.on_change = on_change
    if not native:
        if romaji:
            field.placeholder = "输入罗马音或假名，例如 gakkou"
            refresh_preview()
        return field, [field, preview, pending] if romaji else [field]
    keyboard = u.col()

    def edit(value):
        if not active() or not field.enabled:
            return
        state["editing"] = True
        try:
            field.value = value[:10000]
        finally:
            state["editing"] = False
        original_change(field)
        refresh_preview()

    def render():
        keyboard.clear()
        group = state["group"]
        field.readonly = group not in {"ABC", "罗马音"}
        field.placeholder = "输入罗马音，例如 gakkou" if group == "罗马音" else "用下方按键作答"
        if group == "罗马音":
            refresh_preview()
            keyboard.add(preview, pending)
            return
        if group == "ABC":
            return  # Ordinary Latin/digit keyboard; Japanese IME not required.
        letters = {"清音": BASIC, "浊音": VOICED, "小字": SMALL,
                   "符号": SYMBOLS + EXTRA, "题干用字": "".join(public_characters(question))}[group]
        if state["script"] == "katakana" and group in {"清音", "浊音", "小字"}:
            letters = katakana(letters)
        from .kana_keyboard_ui import key_table
        from .text_layout import font_scale
        table, _, _ = key_table(u, list(letters), lambda char: edit((field.value or "") + char), scale=font_scale())
        keyboard.add(table)

    def select(group):
        if active() and field.enabled:
            state["group"] = group
            render()

    def switch_script(widget=None):
        if active() and field.enabled:
            state["script"] = "katakana" if state["script"] == "hiragana" else "hiragana"
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
    render()
    # Scroll the key area independently; long prompts and large fonts must not
    # put Submit below an unbounded alphabet. The outer page still scrolls.
    keys = u.toga.ScrollContainer(content=keyboard, horizontal=True,
                                   style=u.Pack(height=240, margin_bottom=6))
    return field, [field, toolbar, u.row(*extra), keys]
