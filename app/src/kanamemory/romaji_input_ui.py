"""Romaji utility and word-spelling composer using the same offline parser."""
from .romaji_input import convert_romaji

CONVENTIONS = {"本 App 罗马音": "reading", "输入法拼写": "keyboard"}
SCRIPTS = {"平假名": "hiragana", "片假名": "katakana"}


class RomajiComposer:
    def __init__(self, u, active, on_change, on_apply, *, script=None, convention="keyboard", caption="转换"):
        self.u, self.active, self.changed, self.apply = u, active, on_change, on_apply
        self.fixed_script, self.restoring = script, False
        self.source_input = u.toga.TextInput(placeholder="输入罗马音，例如 gakkou", style=u.Pack(font_size=18, margin_bottom=6))
        self.convention_picker = u.selection(items=list(CONVENTIONS),
            value=next(k for k, v in CONVENTIONS.items() if v == convention), style=u.Pack(flex=1))
        self.script_picker = u.selection(items=list(SCRIPTS), value="平假名", style=u.Pack(flex=1)) if script is None else None
        self.preview = u.label("", size=22, text_role="study")
        self.status = u.label("", size=12, color=u.MUTED)
        self.commit = u.button(caption, self.commit_value, compact=True)
        settings = [self.convention_picker] + ([self.script_picker] if self.script_picker else [])
        self.nodes = [self.source_input, u.row(*settings), self.preview, self.status, self.commit]
        for control in [self.source_input, *settings]:
            control.on_change = self.change
        self.refresh(notify=False)

    def values(self):
        return (self.source_input.value or "", self.fixed_script or SCRIPTS[self.script_picker.value],
                CONVENTIONS[self.convention_picker.value])

    def refresh(self, *, notify):
        source, script, convention = self.values()
        result = convert_romaji(source[:10000], script=script, convention=convention)
        complete = convert_romaji(source[:10000], script=script, convention=convention, final=True)
        if notify and self.changed(source, convention, bool(result["text"])) is False:
            self.preview.text = ""
            self.status.text = "输入未保存，请重试。"
            self.commit.enabled = False
            return False
        self.preview.text = result["text"]
        self.status.text = ("输入过长。" if len(source) > 10000 else
                            "未完成：" + result["pending"][:40] if result["pending"] else "")
        self.commit.enabled = len(source) <= 10000 and complete["complete"] and bool(complete["text"])
        return True

    def change(self, widget=None, **kwargs):
        if self.active() and not self.restoring:
            self.refresh(notify=True)

    def commit_value(self, widget=None, **kwargs):
        if not self.active() or not self.commit.enabled:
            return
        source, script, convention = self.values()
        result = convert_romaji(source, script=script, convention=convention, final=True)
        if result["complete"] and result["text"]:
            # Terminal n becomes visible now; record that exposure before Apply.
            if self.changed(source, convention, True) is not False:
                self.apply(result["text"])

    def restore(self, source, convention):
        self.restoring = True
        try:
            self.source_input.value = source
            self.convention_picker.value = next(k for k, v in CONVENTIONS.items() if v == convention)
            self.refresh(notify=False)
        finally:
            self.restoring = False


def word_converter(app, u, question):
    app._word_converter = None
    app.word_conversion_source = ""
    app.word_conversion_convention = "reading"
    app.word_used_conversion = False
    # A romaji-to-kana prompt tests this conversion itself. Keep its answer
    # control in kana; copying the displayed romaji is not a response mode.
    if question.kind != 'word_kana' or question.metadata.get('word_romaji_drill'):
        return []
    notice = u.label("", size=12, color=u.MUTED)

    def active():
        return app.page == "word_question" and app.question is question and getattr(app, "_word_converter", None) is composer

    def changed(source, convention, exposed):
        if not active():
            return False
        app.word_conversion_source, app.word_conversion_convention = source[:10000], convention
        app.word_used_conversion = bool(app.word_used_conversion or exposed)
        notice.text = "转换辅助 · 本题不调整假名间隔" if app.word_used_conversion else ""
        return app._checkpoint()

    def apply(text):
        # Persist the append and consumption of its source as one checkpoint.
        # A restart must see either the original draft or the consumed draft,
        # never an appended answer with the same source still ready to apply.
        old_answer, old_source = app.word_answer, app.word_conversion_source
        app.word_answer = old_answer + text
        app.word_conversion_source = ""
        if app._checkpoint() is False:
            app.word_answer, app.word_conversion_source = old_answer, old_source
            composer.status.text = "答案未保存，请重试。"
            return False
        app.word_answer_label.text = u.wrapped(app.word_answer, columns=18)
        composer.restore("", app.word_conversion_convention)
        return True

    composer = RomajiComposer(u, active, changed, apply, script=question.script, convention="reading", caption="加入答案")
    composer.notice = notice
    app._word_converter = composer
    return [*composer.nodes, notice]


def restore_word_converter(app, state):
    composer = getattr(app, "_word_converter", None)
    app.word_conversion_source = state.get("word_conversion_source", "")
    app.word_conversion_convention = state.get("word_conversion_convention", "reading")
    # Retain old drafts and the help already seen even when this question no
    # longer offers a converter. Never append its source during restoration.
    preview = convert_romaji(app.word_conversion_source, script=app.question.script,
                             convention=app.word_conversion_convention)['text']
    app.word_used_conversion = state.get("word_used_conversion") is True or bool(preview)
    if composer is None:
        return
    composer.restore(app.word_conversion_source, app.word_conversion_convention)
    composer.notice.text = "转换辅助 · 本题不调整假名间隔" if app.word_used_conversion else ""
    if app.word_used_conversion and not state.get("word_used_conversion"):
        app._checkpoint()


def show_romaji_tool(app, u):
    def active():
        return app.page == "romaji_tool" and getattr(app, "_romaji_tool_context", None) is composer

    def apply(text):
        output.value = text

    def changed(source, convention, exposed):
        if not exposed:
            return True
        try:
            affected = app.store.mark_pending_word_conversion()
            if (getattr(app, "session_id", None), getattr(app, "question_index", None)) in affected:
                app.word_used_conversion = True
            if affected:
                status.text = "未完成的单词题将按转换辅助作答处理。"
            return True
        except Exception:
            status.text = "辅助记录未保存，请重试。"
            return False

    composer = RomajiComposer(u, active, changed, apply)
    app._romaji_tool_context = composer
    output = u.toga.TextInput(readonly=True, style=u.text_style(font_size=22, margin_bottom=8))
    status = u.label("", size=12, color=u.MUTED)

    def copy(widget=None):
        if not active() or not output.value:
            return
        async def run():
            try:
                succeeded = await app.platform.copy_text(output.value)
                if active(): status.text = "已复制" if succeeded else "复制未完成"
            except Exception:
                if active(): status.text = "复制未完成，可长按选取文字。"
        app.loop.create_task(run())

    app.screen("romaji_tool", "罗马音转假名", *composer.nodes, output,
        u.button("复制假名", copy), status,
        u.label("shi→し · chi→ち · tsu→つ · xya→ゃ · xtsu→っ · n'→ん · -→ー", size=12, color=u.MUTED),
        u.label("本 App：ti→ティ、wo→ウォ；输入法：ti→ち、wo→を。不会猜测助词或同音字形。", size=12, color=u.MUTED))
