"""Native, resumable teaching and review screens for word senses and grammar.

The original kana and word-tool queues remain owned by StudyStore.  This mixin
never replaces them, and uses the additional LearningStore for explicit study.
"""
from __future__ import annotations

from datetime import datetime, timezone
from . import app_clock
from copy import deepcopy
import asyncio
import random
import re

from .navigation import page_view
from .answer_autoplay import autoplay_answer
from .answer_speech import clean_utterance, completed_cloze_utterance, pronunciation_utterance
from .answer_result_ui import answer_text
from .learning_content import learning_items, get_learning_item, learning_item_from_word
from .learning_questions import build_learning_question, question_kinds, grade_learning_answer, TEXT_KINDS, PRONUNCIATION_KINDS, KIND_ABILITIES
from .question_presentation import question_instruction
from .question_support import clean_prompt_preferences, build_prompt_block, prompt_audio_text, prompt_support, accent_caption, learning_text_role, _plain_prompt_parts
from .typography import configure_text_role, configure_study_ranges
from .example_ui import ExampleUIMixin
from .structured_lesson_ui import teaching_block, original_notes, block_widget
from .study_path_ui import StudyPathUIMixin
from .ai_ui import AIUIMixin
from .learning_pitch_ui import LearningPitchUIMixin, answer_diagram
from .pitch_practice import is_pitch_question, answer_label as pitch_answer_label, editor_draft as pitch_editor_draft

LEARNING_ACTIVE = {"learning_familiarity", "learning_intro", "learning_prerequisite", "learning_guide", "learning_question", "learning_feedback", "study_reference"}
LEARNING_PAGES = LEARNING_ACTIVE | {"learning_wait", "learning_done"}
LEARNING_PREFS = "learning_preferences_v1"
PROMPT_PREFS = "prompt_display_v1"
EXAM_COMMON_CATEGORY = "考试与常用补充"
EXAM_COMMON_TRACK = "exam-common-20261025"
KIND_LABELS = {
    "vocab_meaning": "词义理解", "vocab_audio_meaning": "听词辨义", "vocab_reading": "单词读法",
    "vocab_recall": "按意义提取单词", "vocab_orthography": "单词写法", "vocab_usage": "语境与搭配",
    "vocab_zh_ja_contrast": "中日表达辨析", "grammar_meaning": "语法含义", "grammar_cloze": "接续与填空",
    "grammar_transform": "活用与转换", "grammar_reorder": "句子重组", "targeted_correction": "找错并修正",
    "reading_comprehension": "短篇理解", "listening_comprehension": "短句听力", "controlled_sentence_production": "受控造句",
    "pronunciation_rule": "读音规则辨析", "pronunciation_application": "读音规则应用",
}
LISTENING_KINDS = {"vocab_audio_meaning", "listening_comprehension"}
KIND_GROUPS = {
    "理解与辨析": {"vocab_meaning", "vocab_zh_ja_contrast", "grammar_meaning", "reading_comprehension"},
    "读法与词汇提取": {"vocab_reading", "vocab_recall", "vocab_orthography", "vocab_usage"},
    "语法应用": {"grammar_cloze", "grammar_transform", "grammar_reorder", "targeted_correction", "controlled_sentence_production"},
    "听力理解": LISTENING_KINDS,
    "读音规则": set(PRONUNCIATION_KINDS),
}


def ui():
    # Late import keeps the existing native helpers as the single layout source.
    from . import ui_components as app
    return app


class LearningUIMixin(LearningPitchUIMixin, AIUIMixin, StudyPathUIMixin, ExampleUIMixin):
    def _learning_prompt_block(self, question, preferences=None, *, size=20):
        return build_prompt_block(self, question, preferences, size=size)

    def _learning_context_support(self, question):
        """Show only locally reviewed background help, never raw hint fields."""
        from .question_support import learning_question_display
        from .learning_dependencies import independent_ready_ids
        u = ui()
        display = learning_question_display(question)
        children = []
        if display.get("context_hint"):
            children.append(u.label(display["context_hint"], size=13, color=u.MUTED))
        if display.get("background_glosses"):
            known = independent_ready_ids(self.learning_store)
            hints = [hint for hint in display["background_glosses"] if hint.get("target_id") not in known]
            if hints:
                parts, ranges, offset = [], [], 0
                for surface, meaning in dict.fromkeys((hint["surface"], hint["meaning"]) for hint in hints):
                    part = surface + "：" + meaning
                    parts.append(part)
                    ranges.append((offset, offset + len(surface)))
                    offset += len(part) + 3
                note = u.label(" · ".join(parts), size=13, color=u.MUTED)
                configure_study_ranges(note, ranges)
                children.append(note)
        return children

    def _ensure_kana_introduction_checkpoint(self, question):
        """Persist a just-created queue before teaching can return from show_question.

        A normal question screen creates this checkpoint through _checkpoint;
        teaching returns before that screen exists. Never use the current UI's
        input bindings here: they may still contain a previous question's draft.
        """
        sid, index = self.session_id, self.question_index
        pending = self.store.load_session_progress()
        if pending is not None:
            if (pending["session_id"] != sid or pending["question_index"] != index
                    or pending["questions"][index] != question):
                raise ValueError("The introduction no longer belongs to the active question")
            # Existing drafts and assistance state are owned by their checkpoint.
            return
        questions = self.store.get_session_questions(sid)
        if (index != 0 or self.session_right != 0 or not questions or questions[0] != question
                or self.store._session_lane(sid) != self.store.practice_lane):
            raise ValueError("The introduction does not match a newly started queue")
        assisted = [i for session, i in self._assisted_attempts if session == sid]
        self.store.save_session_progress(sid, index, self.session_right, assisted, {})
        pending = self.store.load_session_progress()
        if pending is None or pending["session_id"] != sid or pending["question_index"] != index:
            raise ValueError("The introduction no longer belongs to the active question")

    def show_kana_introduction(self, question):
        from .kana_learning_intro import initial_familiarity, intro_content
        u = ui()
        try:
            self._ensure_kana_introduction_checkpoint(question)
        except ValueError as exc:
            self.alert("无法开始介绍，已保存的练习保持不变：" + str(exc))
            return
        data = intro_content(self.repo, question)
        self._kana_intro_question = question
        context = (self.session_id, self.question_index, question)
        familiarity = initial_familiarity(self.store, question)
        if familiarity is None:
            # Reuse only this question's cue: do not reveal a reverse question's
            # kana answer, or a reading question's romanization, before testing.
            cue = u.label(question.prompt, size=24)
            configure_study_ranges(cue, [(match.start(), match.end())
                for match in re.finditer(r"[ぁ-ゖァ-ヺー]+", question.prompt)])
            children = [u.label(data["script_label"], size=15, color=u.MUTED), cue]
            if question.kind == "kana_handwriting":
                children.append(u.speaker_button(lambda w: self.speak_text(question.audio_text), "试听"))
            children.append(u.label("你对这一项有多熟悉？", size=18))
            for level, caption in (("new", "初学"), ("some", "有印象"), ("familiar", "已熟悉")):
                children.append(u.button(caption,
                    lambda w, value=level, expected=context: self.select_kana_familiarity(value, expected=expected),
                    primary=level == "new"))
            children.append(u.label("熟悉的内容先试一题，再按实际作答安排复习。", size=13, color=u.MUTED))
            self.screen("kana_introduction", "开始前", *children)
            return
        children = [u.label(data["symbol"], size=42, color=u.GREEN, bold=True, text_role="study"),
            u.label(data["script_label"] + " · " + data["romaji"], size=19)]
        if data["audio_text"]:
            children.append(u.row(u.label(data["audio_label"], flex=1), u.speaker_button(lambda w: self.speak_text(data["audio_text"]))))
        if familiarity["level"] != "some":
            from .kana_diagrams import kana_diagram
            from .lesson_diagram_ui import diagram_widget
            diagram = kana_diagram(self.repo.get_kana(data['content_id']), data['script'])
            if diagram:
                children.append(diagram_widget(u, diagram))
        if data["note"]:
            children.append(block_widget(u, {"kind": "points", "label": "记住这一点", "items": [data["note"]]}))
        from .kana_reading_rules import dependencies_for_kana
        from .kana_rule_ui import rule_help_widgets
        children.extend(rule_help_widgets(self, dependencies_for_kana(self.repo.get_kana(data["content_id"]))))
        example = data.get("example")
        if example and familiarity["level"] != "some":
            children.extend([u.row(u.label(f"{example['form']}（{example['reading']}）", size=17, text_role="study", flex=1),
                                   u.speaker_button(lambda w: self.speak_text(example['reading'] or example['form']),
                                                    "朗读例词：" + example['form'])),
                             u.label(example['meaning'], size=14, color=u.MUTED)])
        children.append(u.button("学好了，稍后试一题",
            lambda w, expected=context: self.finish_kana_introduction(expected=expected), primary=True))
        self.screen("kana_introduction", "快速回顾" if familiarity["level"] == "some" else "初次学习", *children)

    def select_kana_familiarity(self, level, *, expected=None):
        from .kana_learning_intro import select_initial_familiarity
        current = (self.session_id, self.question_index, self._kana_intro_question)
        if self.page != "kana_introduction" or (expected is not None and expected != current):
            return
        try:
            from .new_dispatch import enabled
            select_initial_familiarity(self.store, *current, level, fair_new=enabled(self))
        except ValueError as exc:
            self.alert("熟悉程度未更改：" + str(exc))
            return
        if level == "familiar" and getattr(self, "_study_path_enabled", False):
            self.continue_study_path(_checkpoint_saved=True)
        elif getattr(self, "_study_path_enabled", False):
            # A selected teaching depth must open this introduction, not let
            # ordinary due work replace the page before its content is shown.
            question = current[2]
            self.show_question(_first_teaching=True,
                _first_teaching_targets={(question.content_id,question.script)})
        else:
            self.show_question()

    def finish_kana_introduction(self, widget=None, *, expected=None, **kwargs):
        from .kana_learning_intro import finish_introduction
        current = (self.session_id, self.question_index, self._kana_intro_question)
        if self.page != "kana_introduction" or (expected is not None and expected != current):
            return
        from .new_dispatch import enabled
        finish_introduction(self.store, *current, fair_new=enabled(self))
        if getattr(self, "_study_path_enabled", False):
            self.continue_study_path(_checkpoint_saved=True)
        else:
            self.show_question()

    def show_kana_intro_wait(self, due):
        u = ui()
        self.screen("kana_intro_wait", "稍后试一题", u.label(self._learning_due_label(due), size=19),
            u.label("刚学的内容已保存，到期后自动继续。也可以先离开。", color=u.MUTED),
            u.button("检查并继续", self.check_learning_wait, primary=True),
            *(self.learning_next_content_controls() if getattr(self, "_study_path_enabled", False) else []),
            u.button("回到今日", self.show_home))
        self.arm_learning_wait(due)

    def arm_learning_wait(self, due):
        """Resume a visible wait at its real deadline, without polling the DB."""
        self._learning_wait_due = due
        loop = getattr(self, "loop", None)
        if not due or not callable(getattr(loop, "call_soon", None)):
            return
        handle = getattr(self, "_review_wait_handle", None)
        if handle is not None:
            handle.cancel()
        self._review_wait_handle = loop.call_soon(self.tick_learning_wait, self._page_token, due)

    def tick_learning_wait(self, token, due):
        if self.page not in {"learning_wait", "kana_intro_wait"} or self._page_token != token:
            return
        self._review_wait_handle = None
        instant = datetime.fromisoformat(due) if isinstance(due, str) else due
        delay = (instant - app_clock.utc_now()).total_seconds()
        if delay <= 0 and not getattr(self, "_leave_confirmation_active", False):
            self.check_learning_wait()
        else:
            self._review_wait_handle = self.loop.call_later(
                max(1, min(60, delay)), self.tick_learning_wait, token, due)

    def continue_added_learning(self, widget=None, **kwargs):
        if getattr(self, "_study_path_enabled", False):
            self.continue_study_path()
        else:
            self.continue_learning()

    def check_learning_wait(self, widget=None, **kwargs):
        """A waiting screen checks all main study work, not just its old lane."""
        if (self.page not in {"kana_intro_wait", "learning_wait"}
                or getattr(self, "_leave_confirmation_active", False)):
            return
        if (getattr(self, "_study_path_enabled", False)
                and getattr(self.store, "practice_lane", "main") == "main"):
            self.continue_study_path()
        elif self.page == "kana_intro_wait":
            self.show_question()
        else:
            self.continue_learning()

    def learning_preferences(self):
        raw = self.store.get_setting(LEARNING_PREFS, {})
        enabled = raw.get("enabled", list(set(KIND_LABELS) - LISTENING_KINDS)) if isinstance(raw, dict) else []
        return {"enabled": [k for k in KIND_LABELS if k in enabled]}

    def learning_items_available(self):
        items = {item["id"]: item for item in learning_items()}
        for item in self.learning_store.list_items():
            items.setdefault(item["id"], item)
        from .ai_materials import AIMaterialStore
        for item in AIMaterialStore(self.store, list(items.values())).retained_items():
            items.setdefault(item["id"], item)
        from .personal_content_versions import resolve as personal_version, teaching_source
        return [personal_version(self.store, teaching_source(item)) for item in items.values()]

    def learning_reading_label(self, item):
        prefs = self._pitch_preferences()
        reading = item.get('reading', '')
        if item.get('personal_reading'):
            return '读法待核' if item['personal_reading']['pending'] else reading
        if not prefs['number']:
            return reading
        from .pitch_accent import morae
        size = len(morae(reading))
        if not size:
            return reading
        options = item.get('accent_options', [])
        values = list(dict.fromkeys(n for n in options if type(n) is int and 0 <= n <= size)) if isinstance(options, (list, tuple)) else []
        if not values and type(item.get('accent')) is int and 0 <= item['accent'] <= size:
            values = [item['accent']]
        return reading + (' ' + '／'.join(accent_caption(n) for n in values) if values else '')

    def pronunciation_audio_examples(self, item, *, rendered=()):
        """Play only explicit complete teaching examples, outside quiz screens."""
        if item.get("domain") != "pronunciation":
            return None
        u = ui()
        rows = []
        # Deduplicate only speakers actually present in this view. Compact
        # teaching omits the example cards, and alternate spoken readings of
        # the same visible sentence must remain independently playable.
        played = set()
        pending = list(rendered)
        while pending:
            node = pending.pop()
            value = getattr(node, "_example_audio_text", None)
            if isinstance(value, str):
                played.add(value)
            pending.extend(getattr(node, "children", ()))
        for example in item.get("instruction", {}).get("audio_examples", []):
            text = example.get("text", "")
            if not isinstance(text, str) or not text.strip() or text in played:
                continue
            played.add(text)
            rows.append(u.row(u.label(example.get("label") or text, size=18, flex=1,
                                      text_role=example.get("text_role", "study")),
                              u.speaker_button(lambda w, value=text: self.speak_text(value))))
        return u.col(*rows) if rows else None

    def pronunciation_pitch_examples(self, item):
        """Authored teaching diagrams only; never derive an answer hint."""
        if item.get("domain") != "pronunciation":
            return None
        from .pitch_display import _mora_lines, _available_width, clean_pitch_preferences
        u = ui()
        # Sentence teaching is an explicit exercise illustration, independent
        # of optional lexical -/_ ruby. Never fabricate one sentence accent number.
        sentence_examples = item.get("instruction", {}).get("sentence_pitch_examples", [])
        if sentence_examples:
            from .structured_lessons import lesson_view
            from .learning_pitch_ui import _width
            from .pitch_practice_ui import fixed_pitch_diagram
            from . import pitch_practice
            taught = {block.get("pitch_practice", {}).get("review", {}).get("content_sha256")
                for block in lesson_view(item).get("blocks", []) if block.get("kind") == "pitch_sequence"}
            examples = [example for example in sentence_examples
                if example["pitch_practice"]["review"]["content_sha256"] not in taught]
            if not examples:
                return None
            def diagram(example):
                question = {"input_mode": "pitch_fill", "pitch_practice": example["pitch_practice"]}
                data = pitch_practice.fixed_view(question, reveal=True)
                if example.get("label"):
                    data["label"] = example["label"]
                return fixed_pitch_diagram(u, **data, width=_width(self), show_counts=False)
            # Legacy lessons without a structured sentence block still give
            # their first authored sentence before asking learners to try it.
            children = []
            if not taught:
                children.append(diagram(examples[0]))
                examples = examples[1:]
            if examples:
                body = u.col()
                def toggle_sentence(widget=None, **kwargs):
                    if body.children:
                        body.clear(); trigger.text = "更多句子示例"
                    else:
                        for example in examples:
                            body.add(diagram(example))
                        trigger.text = "收起句子示例"
                trigger = u.button("更多句子示例", toggle_sentence, compact=True)
                children.extend([trigger, body])
            return u.col(*children)
        prefs = clean_pitch_preferences(self._pitch_preferences())
        if not prefs["contour"] and not prefs["number"]:
            return None
        from .structured_lessons import lesson_view
        taught = [block for block in lesson_view(item).get("blocks", []) if block.get("kind") == "pitch"]
        if taught:
            from .pitch_practice_ui import pitch_diagram
            from .learning_pitch_ui import _width
            existing = {(tuple(block["morae"]), tuple(block["levels"]), block.get("number")) for block in taught}
            examples = [example for example in item.get("instruction", {}).get("pitch_examples", [])
                if (tuple(example.get("morae", [])), tuple(example.get("levels", [])), example.get("number")) not in existing]
            if not examples:
                return None
            body = u.col()
            def toggle(widget=None, **kwargs):
                if body.children:
                    body.clear()
                    trigger.text = "更多音高示例"
                else:
                    for example in examples:
                        body.add(pitch_diagram(u, example["morae"], example["levels"],
                            word_mora_count=example.get("word_mora_count"), label=example.get("label"),
                            number=example.get("number") if prefs["number"] else None,
                            width=_width(self), show_counts=True))
                    trigger.text = "收起音高示例"
            trigger = u.button("更多音高示例", toggle, compact=True)
            return u.col(trigger, body)
        blocks = []
        for example in item.get("instruction", {}).get("pitch_examples", []):
            morae, levels = example.get("morae", []), example.get("levels", [])
            position = example.get("number")
            if (not morae or len(morae) != len(levels) or not all(level in {"H", "L"} for level in levels)
                    or not all(isinstance(mora, str) and mora for mora in morae)
                    or type(position) is not int or not 0 <= position <= len(morae)):
                continue
            caption = example.get("label", "音高示意")
            if prefs["number"]:
                caption += f" · 调号 [{position}]"
            children = [u.label(caption, size=13, color=u.MUTED)]
            if prefs["contour"]:
                offset = 0
                for line in _mora_lines(morae, _available_width(self), 20, max(0.5, u.font_scale())):
                    cells = []
                    for mora, width, size in line:
                        mark = "-" if levels[offset] == "H" else "_"
                        offset += 1
                        cells.append(u.col(
                            u.label(mark, size=size, font_family="monospace", color=u.GREEN, bold=True,
                                    text_align="center", width=width, margin_bottom=0, wrap=False),
                            u.label(mora, size=size, text_role="study", text_align="center", width=width,
                                    margin_bottom=0), width=width))
                    children.append(u.row(*cells, margin_bottom=4))
            blocks.append(u.col(*children, margin_bottom=8))
        return u.col(*blocks) if blocks else None

    @page_view()
    def show_practice_hub(self, widget=None, **kwargs):
        u = ui()
        self.screen("practice_hub", "练习",
            u.button("假名专项", self.show_practice_setup, primary=True),
            u.button("单词拼写", self.show_word_drill_setup),
            u.button("罗马音转假名", self.show_romaji_input_tool),
            u.button("课程学习", self.continue_learning),
            u.button("AI 练习与语料", self.show_ai_center),
            u.button("题型与学习安排", self.show_learning_settings),
        )

    @page_view()
    def show_romaji_input_tool(self, widget=None, **kwargs):
        from .romaji_input_ui import show_romaji_tool
        show_romaji_tool(self, ui())

    @page_view()
    def show_learning_library(self, widget=None, *, family="grammar", offset=0, query="", category="全部主题", **kwargs):
        u = ui()
        self._library_family = family
        # Two columns remain readable at enlarged system font sizes.
        children = [u.row(
            u.button("假名", self.show_kana, flex=1, compact=True),
            u.button("单词", lambda w: self.show_learning_library(family="word"), flex=1, compact=True)),
            u.row(u.button("语法", lambda w: self.show_learning_library(family="grammar"), flex=1, compact=True),
                  u.button("读音规则", lambda w: self.show_learning_library(family="pronunciation"), flex=1, compact=True))]
        all_items = [item for item in self.learning_items_available()
                     if (item.get("domain") == "pronunciation" if family == "pronunciation" else
                         item["family"] == family and item.get("domain") != "pronunciation")]
        tracks = [EXAM_COMMON_CATEGORY] if any(item.get("curriculum_track") == EXAM_COMMON_TRACK for item in all_items) else []
        categories = ["全部主题", *tracks, *dict.fromkeys(item.get("category", "基础") for item in all_items)]
        category = category if category in categories else "全部主题"
        self.learning_library_query = u.toga.TextInput(value=query, placeholder="搜索形式、读法或中文说明", style=u.Pack(margin_bottom=6))
        self.learning_library_category = u.selection(items=categories, value=category, style=u.Pack(margin_bottom=8))
        def filter_library(widget=None):
            self.show_learning_library(family=family, query=self.learning_library_query.value or "",
                                       category=self.learning_library_category.value)
        self.learning_library_category.on_change = filter_library
        self.learning_library_query.on_confirm = filter_library
        # Android EditText keeps a large intrinsic minimum width. A sibling
        # button in that row can be pushed completely off-screen, even at 100%.
        children.extend([self.learning_library_query, u.button("搜索", filter_library, compact=True),
                         self.learning_library_category])
        if family == "word":
            children.append(u.button("查找全部词库", self.show_words))
        children.append(u.button("例句库", self.show_example_library, compact=True))
        children.append(u.button("AI 学习助手", self.show_ai_center, compact=True))
        needle = query.strip().casefold()
        items = [item for item in all_items if (category == "全部主题" or item.get("category", "基础") == category
                 or category == EXAM_COMMON_CATEGORY and item.get("curriculum_track") == EXAM_COMMON_TRACK)
                 and (not needle or needle in " ".join(str(item.get(k, "")) for k in ("title", "reading", "meaning", "category")).casefold())]
        if category == EXAM_COMMON_CATEGORY:
            # Editorial study priority, not a fabricated corpus frequency. This
            # only orders the library; saved queues and FSRS dates stay intact.
            items.sort(key=lambda item: item.get("priority", 3))
        enrolled_ids = {item["id"] for item in self.learning_store.list_items()}
        children.append(u.label(f"共 {len(items)} 项 · 已加入 {sum(item['id'] in enrolled_ids for item in items)} 项", size=13, color=u.MUTED))
        next_item = next((item for item in items if item["id"] not in enrolled_ids), None)
        if next_item:
            children.append(u.button("学习下一项", lambda w: self.enroll_learning(next_item), primary=True))
        offset = max(0, min(offset, ((len(items) - 1) // 12) * 12)) if items else 0
        children[-1]._feedback_fragment = {"kind": "learning_library_page", "family": family,
            "query": query, "category": category, "offset": offset,
            "ordered_ids": [item["id"] for item in items[offset:offset + 12]]}
        for item in items[offset:offset + 12]:
            state = self.learning_store.get_item(item["id"])
            status = "尚未加入" if not state else {"intro": "初次学习", "guide": "跟着做", "bridge": "待独立练习", "check": "学习中", "done": "已加入复习"}.get(state.get("stage"), "学习中")
            entry = u.button(item["title"] + "\n" + item.get("meaning", item.get("instruction", {}).get("summary", "")) + " · " + status,
                lambda w, item=item: self.show_learning_detail(item), multiline=True)
            entry._feedback_fragment = {"kind": "learning_library_entry", "target_id": item["id"],
                "item_snapshot": deepcopy(item), "displayed_status": status}
            if item["family"] == "word" and item["title"]:
                configure_study_ranges(entry, [(0, len(item["title"]))])
            children.append(entry)
        if offset:
            children.append(u.button("上一页", lambda w: self.show_learning_library(family=family, offset=offset - 12, query=query, category=category)))
        if offset + 12 < len(items):
            children.append(u.button("下一页", lambda w: self.show_learning_library(family=family, offset=offset + 12, query=query, category=category)))
        self.screen("learning_library", "学习库", *children)
        self.retention_checks().expose_many([{'id':item['id'],'abilities':['understanding']}
            for item in items[offset:offset + 12]],'summary')

    @page_view(detail=True)
    def show_learning_detail(self, item, *, answer_context=None):
        u = ui()
        if isinstance(item, str):
            item = self.learning_store.get_item(item) or next((entry for entry in self.learning_items_available() if entry["id"] == item), None) or get_learning_item(item)
        self._learning_detail_item = item
        from .assessment_policy import teaching_allowed
        verified_teaching = teaching_allowed(item, store=self.store)
        instruction = item.get("instruction", {})
        children = [u.label(item["title"], size=25, bold=True, color=u.GREEN,
                            text_role="study" if item["family"] == "word" else "ui")]
        if answer_context and answer_context[0].get('target_id') == item['id']:
            from .semantic_errors import feedback_nodes
            children.extend(feedback_nodes(u, *answer_context))
        if item.get("reading"):
            children.append(u.row(u.label(self.learning_reading_label(item), size=19, flex=1, text_role="study"),
                *([] if item.get('personal_reading', {}).get('pending') else [u.speaker_button(lambda w: self.speak_text(item["reading"]))])))
        children.extend([teaching_block(u, item, speak=self.speak_text), self.example_cards(item)])
        if not verified_teaching:
            notice = ("学习材料尚不完整。" if item.get("instruction_status") == "instruction_missing"
                      else "待核验草稿 · 可预览，暂不作为正式教案。")
            if item.get('instruction_status') == 'instruction_missing' and item['id'].startswith('local:ai:'):
                notice = '待核验草稿 · 学习材料尚不完整。'
            children.insert(0, u.label(notice, size=13, color=u.MUTED))
        children.append(u.button("生成例句", lambda w: self.show_ai_form("examples", target_id=item["id"]), compact=True))
        pitch = self.pronunciation_pitch_examples(item)
        if pitch is not None:
            children.append(pitch)
        audio = self.pronunciation_audio_examples(item, rendered=children)
        if audio is not None:
            children.append(audio)
        children.append(original_notes(u, item))
        from .personal_content_notes_ui import append_notes
        append_notes(self, u, children, 'lesson', item, page='learning_detail')
        if item.get("depends_on"):
            children.append(u.button("前置知识", lambda w: self.show_learning_dependencies(item), compact=True))
        enrolled = self.learning_store.get_item(item["id"])
        if verified_teaching and enrolled and enrolled["stage"] == "intro":
            self.learning_store.mark_instruction_exposed(item["id"], expected_introduction_id=enrolled["introduction_id"])
        if verified_teaching:
            children.append(u.button("继续学习" if enrolled else "学习这一用法", lambda w: self.enroll_learning(item), primary=True))
        if enrolled:
            children.append(u.button('检查掌握情况', lambda w: self.show_diagnostic_scope(scope_ids=[item['id']]), compact=True))
        children.append(u.button("资料", lambda w: self.show_learning_sources(item), compact=True))
        self.screen("learning_detail", "读音规则" if item.get("domain") == "pronunciation" else
                    "单词" if item["family"] == "word" else "语法", *children)
        self.retention_checks().expose_many([{'id':item['id'],'abilities':item.get('abilities',[])}],'instruction')

    @page_view(detail=True)
    def show_learning_sources(self, item):
        u = ui()
        from .learning_content import learning_sources
        known = {source["id"]: source for source in learning_sources()}
        sources = [known.get(key, {"title": key}) for key in item.get("source_ids", [])]
        instruction = item.get('instruction', {})
        review_rows, reviewed_pairs = [], set()
        for text, translation in [(instruction.get('example', ''), instruction.get('translation', '')),
                *[(ex.get('text', ''), ex.get('translation', '')) for ex in instruction.get('examples', [])]]:
            if (text, translation) not in reviewed_pairs:
                rows = self.natural_translation_review_rows(text, translation)
                if rows:
                    review_rows.extend([u.label(text, size=15, text_role='study'), *rows])
                reviewed_pairs.add((text, translation))
        self.screen("learning_sources", "资料", u.label(item["title"], bold=True),
            *review_rows,
            *[u.label(source.get("title", "") + ("\n" + source["url"] if source.get("url") else "")) for source in sources],
            *[u.label(url, size=12, color=u.MUTED) for url in dict.fromkeys(item.get("source_urls", []))],
            u.label("本地教材与词典资料。调号只用于匹配读法的词段，不代表整句语调。", color=u.MUTED))

    @page_view(detail=True)
    def show_learning_dependencies(self, item):
        from .learning_dependencies import build_dependency_plan
        u = ui()
        plan = build_dependency_plan([item["id"]], self.learning_items_available(), self.learning_store,
                                     allow_unresolved=True)
        children = [u.label("先理解这些内容，再学习「" + item["title"] + "」。", size=17)]
        for prerequisite in plan["items"]:
            if prerequisite["id"] != item["id"]:
                children.append(u.button(prerequisite["title"], lambda w, value=prerequisite: self.show_learning_detail(value), multiline=True))
        if plan["unresolved"]:
            children.append(u.label("这项旧内容的前置材料不完整，暂不能开始；其他课程可正常学习。", color=u.MUTED))
        else:
            children.append(u.button("按顺序开始", lambda w: self.enroll_learning(item), primary=True))
        self.screen("learning_dependencies", "前置知识", *children)

    def enroll_learning(self, item):
        from .assessment_policy import teaching_allowed
        if not teaching_allowed(item, store=self.store) and item.get("instruction_status") != "instruction_missing":
            self.alert("材料仍待内容核验；已保留的内容和原有进度不会删除。")
            return
        if item.get("instruction_status") == "instruction_missing":
            self.alert("这个读法与义项的材料尚不完整，暂不加入学习。")
            return
        if item.get("domain") == "pronunciation" and not set(PRONUNCIATION_KINDS) & set(self.learning_preferences()["enabled"]):
            # Existing explicit settings stay authoritative. Enrollment offers
            # a targeted opt-in instead of silently enabling unrelated kinds.
            u = ui()
            self.screen("learning_wait", "读音规则题型尚未开启",
                u.label("开启后可学习本课；原有题型选择和未完成练习会保留。"),
                u.button("开启读音规则并学习", lambda w: self.enable_pronunciation_learning(item), primary=True),
                u.button("题型设置", self.show_learning_settings), u.button("稍后再学", self.leave_learning))
            return
        plan = self.learning_store.enroll_with_prerequisites([item], self.learning_items_available())
        from .practice_round import restart
        restart(self.store, lane="main")
        if getattr(self, "_learning_detail_item", {}).get("id") == item["id"]:
            # Joining from an answer-bearing detail page is already exposure.
            # It must not become an immediate, supposedly unseen placement test.
            saved = self.learning_store.get_item(item["id"])
            self.learning_store.mark_instruction_exposed(item["id"], expected_introduction_id=saved["introduction_id"])
        for content_id in plan["ordered_ids"]:
            saved = self.learning_store.get_item(content_id)
            if saved:
                self._queue_learning_questions(saved)
        self.continue_added_learning()

    def enable_pronunciation_learning(self, item):
        raw = self.store.get_setting(LEARNING_PREFS, {})
        updated = dict(raw) if isinstance(raw, dict) else {}
        prior = updated.get("enabled")
        enabled = prior if isinstance(prior, list) and all(isinstance(kind, str) for kind in prior) else list(self.learning_preferences()["enabled"])
        updated["enabled"] = list(dict.fromkeys([*enabled, *PRONUNCIATION_KINDS]))
        self.store.set_setting(LEARNING_PREFS, updated)
        self.enroll_learning(item)

    @page_view(detail=True)
    def open_word_learning(self, word):
        u = ui()
        senses = word.get("senses") or word.get("payload", {}).get("senses") or [{}]
        children = [u.label(word["form"], size=24, bold=True, text_role="study"), u.label("选择本次学习的词义", size=16)]
        for index in range(len(senses)):
            item = learning_item_from_word(word, index)
            children.append(u.button(item["meaning"], lambda w, item=item: self.show_learning_detail(item), multiline=True))
        self.screen("learning_detail", "选择词义", *children)

    def _learning_question_plans(self, item):
        from .course_planning import iter_question_plans
        from .answer_input import system_japanese_enabled
        return iter_question_plans(item, enabled_kinds=self.learning_preferences()["enabled"],
            queries=self.store.queries, learning_store=self.learning_store,
            system_japanese_input=system_japanese_enabled(self.store))

    def _queue_learning_questions(self, item):
        from .practice_round import sealed
        if sealed(self.store):
            return
        for state, plan in self._learning_question_plans(item):
            if "answer" in plan:
                q = plan
            else:
                q = build_learning_question(item, plan["kind"], variant=plan["variant"])
                q["id"] = plan["id"]
            self.learning_store.append_questions([q])

    @page_view()
    def show_learning_settings(self, widget=None, **kwargs):
        u = ui()
        enabled = set(self.learning_preferences()["enabled"])
        self.learning_group_switches = {}
        controls = []
        for title, kinds in KIND_GROUPS.items():
            control = u.switch(title, value=bool(kinds & enabled), on_change=lambda w: self.change_learning_kinds())
            self.learning_group_switches[title] = control
            controls.append(control)
        from .answer_input import SETTING, system_japanese_enabled
        self.learning_system_input = u.switch("使用系统日文键盘（可选）",
            value=system_japanese_enabled(self.store),
            on_change=lambda w: self.store.set_setting(SETTING, {
                "system_japanese": self.learning_system_input.value is True}))
        self.screen("learning_settings", "学习安排", self.heading("学习题型"), *controls,
            self.learning_system_input,
            u.button("继续学习", self.continue_added_learning, primary=True),
            u.button("加入新内容", self.show_study_path_add),
            u.button("假名练习设置", self.show_practice_setup))

    def change_learning_kinds(self):
        enabled = [kind for title, kinds in KIND_GROUPS.items() if self.learning_group_switches[title].value for kind in sorted(kinds)]
        self.store.set_setting(LEARNING_PREFS, {"enabled": enabled})

    @page_view()
    def show_settings_hub(self, widget=None, **kwargs):
        u = ui()
        self.screen("settings_hub", "设置",
            u.button("练习方式", self.show_learning_settings), u.button("显示", self.show_prompt_settings),
            u.button("语音与手写", self.show_settings),
            u.button("学习数据", self.show_learning_data),
            u.button("反馈记录", self.show_feedback_manager),
            u.button("AI 服务", self.show_ai_service_settings),
            u.button("关于与资料", self.show_notices),
            u.label("本地学习可离线使用。", size=12, color=u.MUTED))

    @page_view()
    def show_learning_data(self, widget=None, **kwargs):
        u = ui()
        self.screen("learning_data", "学习数据", u.button("课程学习记录", self.show_learning_records), u.button("假名学习记录", self.show_progress),
            u.button("导出学习备份", self.export_learning_backup), u.button("从文件恢复", self.import_learning_backup),
            u.button("转换旧版学习记录", self.convert_legacy_learning_records),
            u.button("导出恢复前备份", self.export_previous_backup),
            u.button("删除学习数据", self.delete_learning_data, color="#A62A37"))

    @page_view()
    def show_learning_records(self, widget=None, **kwargs):
        from .learning_records import build_learning_records
        self.screen("learning_records", "课程学习记录", build_learning_records(self))

    @page_view()
    def show_prompt_settings(self, widget=None, **kwargs):
        u = ui()
        prefs = clean_prompt_preferences(self.store.get_setting(PROMPT_PREFS, {}))
        self.prompt_kana_switch = u.switch("日文假名注音", value=prefs["furigana"], on_change=lambda w: self.change_prompt_settings())
        self.prompt_number_switch = u.switch("日文数字调号", value=prefs["number"], on_change=lambda w: self.change_prompt_settings())
        self.screen("prompt_settings", "显示", self.prompt_kana_switch, self.prompt_number_switch,
            u.label("用于例句和安全题干；读音题自动隐藏。", size=12, color=u.MUTED),
            u.button("词条音高线与调号", self.show_settings))

    def change_prompt_settings(self):
        self.store.set_setting(PROMPT_PREFS, {"furigana": bool(self.prompt_kana_switch.value), "number": bool(self.prompt_number_switch.value)})
        self.refresh_sentence_annotations()

    def _learning_archive_control(self, question, draft):
        """Old stimulus and own input only; do not expose a hidden answer."""
        u = ui(); body = u.col()
        def open_archive(widget=None):
            body.clear()
            body.add(u.label(question.get('prompt', ''), size=17, text_role=learning_text_role(question, 'prompt')),
                u.label(draft.get('answer') or ''.join(draft.get('parts', [])) or '尚未输入', size=14, color=u.MUTED))
        return u.col(u.button('原题与草稿', open_archive, compact=True, font_size=12), body)

    def _learning_replacement_controls(self, question_ids=(), item=None):
        from .learning_replacement import LearningReplacementStore
        from .personal_content_versions import question_blocked
        service = LearningReplacementStore(self.learning_store)
        candidates = [(item, None)] if item else []
        for qid in question_ids:
            q = self.learning_store.get_question(qid)
            if q and q['status'] == 'pending' and q.get('result') is None:
                scope = self.learning_store.ability_instruction_scope(q['target_id'], q['ability'])
                candidates.append((self.learning_store.instruction_item(q['target_id'], scope), qid))
        for current, qid in candidates:
            guide = current.get('checkpoint', {}).get('guide_question') if current['stage'] == 'guide' else None
            try:
                guided = bool(guide and question_blocked(self.store, current, guide))
                prepared = (service.prepare_guide(current['id'], current.get('instruction_scope')) if guided
                            else service.prepare_question(qid))
            except (ValueError, KeyError):
                continue
            def apply(widget=None, prepared=prepared, guided=guided, current=current):
                try:
                    service.commit(prepared)
                except (ValueError, KeyError) as exc:
                    self.alert(str(exc)); self.continue_learning(); return
                self._learning_current = None
                self._learning_guide_context = {}
                if guided:
                    self.show_learning_instruction(self.learning_store.instruction_item(current['id'], current.get('instruction_scope')))
                else:
                    self.continue_learning()
            return [ui().button('使用更新后的练习继续', apply, primary=True)]
        return []

    @page_view(replace=True)
    def continue_learning(self, widget=None, **kwargs):
        u = ui()
        from .practice_round import begin, seal, mark_finished
        begin(self.store, restart_finished=widget is not None)
        enabled = self.learning_preferences()["enabled"]
        self.learning_store.ensure_prerequisites()
        for item in self.learning_store.items_needing_questions():
            self._queue_learning_questions(item)
        from .new_dispatch import enabled as fair_enabled
        selected = kwargs.get("_selected_current")
        if selected and selected.get('question'):
            fresh = self.learning_store.get_question(selected['question']['id'])
            if not fresh or fresh.get('status') == 'superseded':
                selected = None
        selected_id = (selected or {}).get("content", {}).get("id")
        if (selected_id and selected.get('question') and not selected['question'].get('result')
                and self.learning_store.content_question_blocked(selected['content'], selected['question'])):
            selected = None; selected_id = None
        if selected_id and self.learning_store.prerequisite_status(selected_id, selected["content"].get("instruction_scope"))["blocked"]:
            selected = None
        if selected and selected.get("type") == "instruction":
            checkpoint = selected.get("content", {}).get("checkpoint", {})
            frozen_guide = checkpoint.get("guide_question", {})
            if frozen_guide.get("practice") and frozen_guide.get("kind") not in enabled:
                selected = None
        current = selected or self.learning_store.current(enabled, fair_new=fair_enabled(self))
        seal(self.store)
        self._learning_current = current
        if current and current['type'] == 'content_blocked':
            self.screen('learning_wait', '材料待核', u.label('相关说明已更新，部分旧题仍需核对。'),
                u.label('原题、草稿和复习时间均已保留。', color=u.MUTED),
                *self._learning_replacement_controls(current['question_ids']),
                *self.learning_next_content_controls(), u.button('回到今日', self.leave_learning))
            return
        if not current and self.learning_store.counts(enabled)["disabled"]:
            self.screen("learning_wait", "题型已关闭", u.label("未完成题目已保留，重新开启对应题型后可继续。"),
                u.button("题型设置", self.show_learning_settings, primary=True), *self.learning_next_content_controls(), u.button("回到今日", self.leave_learning))
            return
        if not current or current.get("type") in {"empty", "done"}:
            mark_finished(self.store)
            self.screen("learning_done", "课程学习", u.label("本次学习已保存。", size=20),
                u.button("开始新一轮", self.continue_learning, primary=True),
                *self.learning_next_content_controls(), u.button("回到今日", self.leave_learning))
            return
        if current["type"] == "disabled":
            self.screen("learning_wait", "题型已关闭", u.label("未完成题目与复习时间已保留，开启对应题型后可继续。"),
                u.button("题型设置", self.show_learning_settings, primary=True), *self.learning_next_content_controls(), u.button("回到今日", self.leave_learning))
            return
        if current['type'] == 'diagnostic':
            return self.continue_diagnostic(current['episode_id'])
        if current["type"] == "waiting":
            due = current.get("due_at_utc")
            when = self._learning_due_label(due) if due else "当前题型没有可答题目。"
            self.screen("learning_wait", "稍后继续", u.label(when, size=20),
                u.label("到期后自动继续，也可以先离开。", color=u.MUTED),
                u.button("检查并继续", self.check_learning_wait, primary=True), *self.learning_next_content_controls(),
                u.button("题型设置", self.show_learning_settings), u.button("回到今日", self.leave_learning))
            self.arm_learning_wait(due)
            return
        if current["type"] == "prerequisite":
            item = current["content"]
            children = [u.label(item["title"], size=21, bold=True),
                u.label("先接着巩固前置内容，完成后自动回到本项。", size=16)]
            if current.get("due_at_utc"):
                children.append(u.label(self._learning_due_label(current["due_at_utc"]), size=14))
                children.append(u.label("到期后自动继续，也可以先离开。", color=u.MUTED))
            else:
                children.append(u.label("前置内容的练习暂不可用，请检查题型或暂停设置。", color=u.MUTED))
                children.append(u.button("学习设置", self.show_learning_settings))
            def skip_prerequisites(widget):
                self.learning_store.acknowledge_prerequisites(item["id"], instruction_scope=item.get("instruction_scope"))
                self.continue_added_learning()
            children.extend([u.button("检查并继续", self.check_learning_wait, primary=True),
                u.button("我已了解前置，继续本课", skip_prerequisites),
                *self.learning_next_content_controls(), u.button("回到今日", self.leave_learning)])
            self.screen("learning_wait", "先巩固前置知识", *children)
            self.arm_learning_wait(current.get("due_at_utc"))
            return
        if current["type"] == "instruction":
            self.show_learning_instruction(current["content"])
            return
        if current["type"] == "question":
            self.show_learning_question(current)
            return
        raise ValueError("Unsupported learning dispatch state")

    def learning_next_content_controls(self):
        u = ui()
        # Standalone course controllers can still be embedded without the
        # route service; the native app always supplies this service.
        if not getattr(self, "_study_path_enabled", False) or not callable(getattr(self, "study_path_service", None)):
            return [u.button("加入新内容", self.show_learning_library, primary=True)]
        overview = self.study_path_service().overview()
        controls = []
        available = getattr(self, "study_path_can_continue", None)
        can_continue = callable(available) and available()
        if can_continue:
            controls.append(u.button("继续已加入的学习", self.continue_study_path, primary=True))
        if overview.get("next_section"):
            controls.append(u.button("继续下一节",
                lambda w, identity=overview["next_section"]["id"]: self._add_study_sections((identity,)), primary=not can_continue))
            controls.append(u.label(overview["next_section"]["title"], size=13, color=u.MUTED))
        controls.append(u.button("添加内容", self.show_study_path_add))
        controls.append(u.button("查看学习路线", self.show_study_path, compact=True))
        return controls

    @staticmethod
    def _learning_due_label(due):
        if not due:
            return "暂时没有到期题目。"
        if isinstance(due, str):
            due = datetime.fromisoformat(due)
        return "下次复习：" + app_clock.localize(due).strftime("%m月%d日 %H:%M:%S")

    def show_learning_familiarity(self, item):
        from .assessment_policy import teaching_allowed
        if not teaching_allowed(item, store=self.store):
            return self.show_learning_instruction(item)
        u = ui()
        available = {KIND_ABILITIES[k] for k in self.learning_preferences()['enabled'] if k in KIND_ABILITIES}
        admitted = [unit['ability'] for unit in item['units']]
        current = getattr(self, '_learning_current', None) or {}
        chosen_ability = ((current.get('question') or {}).get('ability')
                          if current.get('content', {}).get('introduction_id') == item['introduction_id'] else None)
        if chosen_ability not in available or chosen_ability not in admitted:
            chosen_ability = next((a for a in admitted if a in available), admitted[0])
        item = self.learning_store.prepare_initial_familiarity(item['id'],
            expected_introduction_id=item['introduction_id'], instruction_scope=item.get('instruction_scope'), ability=chosen_ability)
        self._learning_item = item
        labels = {"understanding": "词义" if item["family"] == "word" else "理解规则",
                  "reading": "读音", "use": "提取与使用", "listening": "听辨理解", "application": "应用规则"}
        abilities = [unit['ability'] for unit in item['units']]
        levels = dict(item.get('checkpoint', {}).get('familiarity_draft', {}))
        ability = next(a for a in abilities if a not in levels)
        state = {'content_id': item['id'], 'introduction_id': item['introduction_id'],
                 'instruction_scope': item.get('instruction_scope'), 'ability': ability, 'levels': levels, 'busy': False}
        self._learning_familiarity_state = state
        children = [u.label(item["title"], size=24, bold=True, text_role="study" if item["family"] == "word" else "ui"),
                    u.label(labels.get(ability, ability), size=18, bold=True)]

        def begin(level):
            if (self.page != 'learning_familiarity' or getattr(self, '_learning_familiarity_state', None) is not state
                    or state['busy'] or self._learning_item['introduction_id'] != state['introduction_id']
                    or self._learning_item.get('instruction_scope') != state['instruction_scope']):
                return
            state['busy'] = True
            chosen = {**levels, ability: level}
            if set(chosen) != set(abilities):
                try:
                    saved = self.learning_store.save_familiarity_draft(item['id'], chosen,
                        expected_introduction_id=state['introduction_id'], instruction_scope=state['instruction_scope'])
                    self.show_learning_familiarity(saved)
                finally:
                    state['busy'] = False
                return
            from .new_dispatch import enabled as fair_enabled
            try:
                saved = self.learning_store.set_initial_familiarity(item['id'], chosen,
                    expected_introduction_id=state['introduction_id'], title_answer_exposed=item.get('domain') == 'pronunciation',
                    fair_new=fair_enabled(self), instruction_scope=state['instruction_scope'])
            finally:
                state['busy'] = False
            if saved["stage"] == "intro":
                self.show_learning_instruction(saved)
            elif getattr(self, "_study_path_enabled", False):
                self.continue_study_path(_checkpoint_saved=True)
            else:
                self.continue_learning()
        children.extend(u.button(caption, lambda w, value=value: begin(value))
                        for caption, value in [('初学', 'new'), ('有印象', 'some'), ('已熟悉', 'familiar')])
        self.screen("learning_familiarity", "开始前", *children)

    def _learning_guide_controls(self, question, draft):
        """Use the independent task's controls while keeping all work exposure-only.

        The question and each edit are checkpointed before UI state changes.
        Rebuilding or revisiting a guide must not select another demonstration.
        """
        u = ui()
        context = {"question": question, "draft": deepcopy(draft or {}),
                   "introduction_id": self._learning_item.get("introduction_id"),
                   "buttons": [], "locked": False}
        self._learning_guide_context = context
        mode = question.get("input_mode", "choice")
        status = u.label("", size=12, color=u.MUTED)
        context["status"] = status

        def active():
            return (self.page == "learning_guide" and not context["locked"]
                    and getattr(self, "_learning_guide_context", None) is context
                    and getattr(self, "_learning_guide", None) is question
                    and self._learning_item.get("introduction_id") == context["introduction_id"])

        def render():
            value = context["draft"]
            if mode == "reorder":
                text = "".join(value.get("parts", []))
                context["composition"].text = text or "点击词块组成句子"
                configure_text_role(context["composition"], text_role="study" if text else "ui")
                for index, control in enumerate(context["choices"]):
                    control.enabled = index not in value.get("used", []) and not context["locked"]
                context["undo"].enabled = bool(value.get("used")) and not context["locked"]
            if "submit" in context:
                context["submit"].enabled = bool(str(value.get("answer", "")).strip()) and not context["locked"]

        def save(value):
            if not active():
                return False
            value = {**context["draft"], **value}
            try:
                self.learning_store.advance_instruction(self._learning_item["id"], "guide", state={
                    "guide_question": deepcopy(question), "guide_draft": deepcopy(value),
                    "guide_answer": None, "guide_correct": False}, instruction_scope=self._learning_item.get("instruction_scope"))
            except (ValueError, KeyError) as exc:
                status.text = "暂未保存：" + str(exc)
                return False
            context["draft"] = deepcopy(value)
            self._learning_guide_result.clear()
            status.text = ""
            render()
            return True

        def submit(widget=None):
            if active():
                self.answer_learning_guide(context["draft"].get("answer", ""),
                    draft=context["draft"], question_id=question["id"])

        if mode == 'multi_cloze':
            from .multi_cloze_ui import controls as cloze_controls, prompt_view
            context['prompt'] = prompt_view(self, u, question, context['draft'],
                clean_prompt_preferences(self.store.get_setting(PROMPT_PREFS, {})))

            def submit_cloze_guide(answer):
                self.answer_learning_guide(answer, draft=context['draft'], question_id=question['id'])
                return True if context['locked'] else False if context['status'].text else None

            controls = [cloze_controls(self, u, question, context['draft'], save,
                submit_cloze_guide, active, prompt=context['prompt'])]
        elif mode == "choice":
            controls = []
            for choice in question.get("choices", []):
                def choose(widget=None, answer=choice):
                    if active():
                        self.answer_learning_guide(answer, draft={**context["draft"], "answer": answer}, question_id=question["id"])
                controls.append(u.button(choice, choose, multiline=True,
                    text_role=learning_text_role(question, "choice")))
            context["buttons"] = controls
        elif mode == "reorder":
            context["composition"] = u.label("", size=20, text_role="study")
            context["choices"] = []
            for index, choice in enumerate(question.get("choices", [])):
                def append(widget=None, i=index):
                    if not active() or i in context["draft"].get("used", []):
                        return
                    used = [*context["draft"].get("used", []), i]
                    parts = [question["choices"][n] for n in used]
                    save({"answer": "".join(parts), "parts": parts, "used": used})
                context["choices"].append(u.button(choice, append, multiline=True, text_role="study"))

            def undo(widget=None):
                if active():
                    used = context["draft"].get("used", [])[:-1]
                    parts = [question["choices"][n] for n in used]
                    save({"answer": "".join(parts), "parts": parts, "used": used})
            context["undo"] = u.button("撤回末项", undo)
            context["submit"] = u.button("提交", submit, primary=True)
            controls = [context["composition"], *context["choices"], context["undo"], context["submit"]]
            context["buttons"] = [*context["choices"], context["undo"], context["submit"]]
        else:
            def changed(widget=None, **kwargs):
                if active() and "input" in context:
                    save({"answer": context["input"].value})
            from .answer_input_ui import answer_controls
            from .answer_input import available, system_japanese_enabled
            context["input"], controls = answer_controls(u, question,
                context["draft"].get("answer", ""), changed, active,
                system_japanese=system_japanese_enabled(self.store))
            context["submit"] = u.button("提交", submit, primary=True)
            controls.append(context["submit"])
            context["buttons"] = [context["input"], context["submit"]]
            if not available(question, system_japanese=system_japanese_enabled(self.store)):
                def demonstrate(widget=None):
                    if active():
                        original = context["draft"].get("input_unavailable_draft", deepcopy(context["draft"]))
                        if save({"answer": question["answer"], "demonstration_open": True,
                                 "input_unavailable_draft": original}):
                            submit()
                model = u.button("跟随示范完成", demonstrate, primary=True)
                controls = [u.label("这道旧引导缺少输入控件，可跟随示范完成；不计入掌握程度。", size=13, color=u.MUTED), model]
                context["buttons"].append(model)
        render()
        return [*controls, status]

    def _show_learning_guide_paused(self):
        u = ui()
        self.screen("learning_wait", "引导已保留",
            u.label("这类练习已关闭，输入已保存。", size=16),
            u.button("继续其他学习", self.continue_added_learning, primary=True),
            u.button("练习设置", self.show_learning_settings, compact=True),
            u.button("回到今日", self.show_home))

    def _learning_guide_demonstration(self, question, checkpoint):
        """Fade the second example's model without turning teaching into SRS."""
        u = ui()
        box = u.col()
        second = bool(checkpoint.get("guide_sequence") and checkpoint.get("guide_index") == 1)
        context = getattr(self, "_learning_guide_context", {})

        def model():
            box.clear()
            box.add(u.col(u.label("本题示范" if second else "示范", size=12, color=u.MUTED),
                u.label(answer_text(question), size=18, bold=True,
                        text_role=learning_text_role(question, "answer")),
                padding=10, background_color=u.ENTRY_BACKGROUNDS[0]))

        def reveal(widget=None):
            if (self.page != "learning_guide" or context.get("locked")
                    or getattr(self, "_learning_guide_context", None) is not context
                    or getattr(self, "_learning_guide", None) is not question):
                return
            value = {**context.get("draft", {}), "demonstration_open": True}
            try:
                self.learning_store.advance_instruction(self._learning_item["id"], "guide", state={
                    "guide_question": deepcopy(question), "guide_draft": value}, instruction_scope=self._learning_item.get("instruction_scope"))
            except (ValueError, KeyError) as exc:
                context["status"].text = "暂未保存：" + str(exc)
                return
            context["draft"] = value
            model()

        if not second or checkpoint.get("guide_draft", {}).get("demonstration_open") is True:
            model()
        else:
            # Android may report zero intrinsic width for a compact button.
            # Give this short secondary action a bounded explicit width and
            # keep it directly in the column, away from horizontal flex.
            box.add(u.button("看示范", reveal, compact=True, font_size=12,
                             width=120, height=40))
        return box

    def show_learning_instruction(self, item):
        u = ui()
        from .assessment_policy import teaching_allowed
        from .personal_content_versions import question_blocked
        frozen_guide = item.get('checkpoint', {}).get('guide_question') if item.get('stage') == 'guide' else None
        if frozen_guide and question_blocked(self.store, item, frozen_guide):
            self.screen('learning_wait', '引导题待核', u.label('说明已经更新，原引导题与草稿已保留。'),
                *self._learning_replacement_controls(item=item),
                u.button('查看更新后的说明', lambda w: self.show_learning_detail(item)),
                *self.learning_next_content_controls(), u.button('回到今日', self.leave_learning))
            return
        if not teaching_allowed(item, store=self.store):
            missing = item.get("instruction_status") == "instruction_missing"
            self.screen("learning_wait", "学习材料尚不完整" if missing else "材料待核验",
                u.label("原有进度与题目已保存，暂不开始正式教学。"),
                u.button("查看材料" if missing else "预览草稿", lambda w: self.show_learning_detail(item)),
                u.button("回到今日", self.leave_learning))
            return
        self._learning_item = item
        instruction = item.get("instruction", {})
        stage = item.get("stage", "intro")
        familiarity = item.get("checkpoint", {}).get("initial_familiarity")
        enrolled = bool(item.get("introduction_id") and item.get("units"))
        if enrolled and stage == "intro" and not familiarity:
            self.show_learning_familiarity(item)
            return
        # Familiarity belongs to each ability; reopening the same overview does
        # not. Keep the first introduction and every frozen guide/draft intact.
        shared_intro = (enrolled and len(item.get('enrolled_abilities', [])) == 1
            and self.learning_store.has_other_instruction_exposure(item['id'],
                instruction_scope=item.get('instruction_scope'), personal_revision=item.get('personal_revision')))
        checkpoint = item.get('checkpoint', {})
        if (stage == 'intro' and shared_intro
                and not any(k in checkpoint for k in ('guide_question', 'guide_sequence', 'guide_draft', 'guide_answer'))
                and not self.learning_store.prerequisite_status(item['id'], item.get('instruction_scope'))['blocked']):
            # This is teaching, not a successful review or permission to skip
            # the current ability's own guided example and delayed retrieval.
            self.learning_store.mark_instruction_exposed(item['id'],
                expected_introduction_id=item['introduction_id'], instruction_scope=item.get('instruction_scope'))
            item = self.learning_store.advance_instruction(item['id'], 'intro_done',
                instruction_scope=item.get('instruction_scope'))
            self._learning_item, stage = item, item['stage']
        if stage == "guide":
            enabled = set(self.learning_preferences()["enabled"])
            admitted = item.get("enrolled_abilities", item.get("abilities", []))
            guide_kinds = [kind for kind in question_kinds(item)
                           if kind in enabled and KIND_ABILITIES[kind] in admitted]
            levels = (familiarity or {}).get("levels", {})
            unfamiliar = [kind for kind in guide_kinds
                          if levels.get(KIND_ABILITIES[kind]) in {"new", "some"}]
            if unfamiliar:
                guide_kinds = unfamiliar
            checkpoint = item.get("checkpoint", {})
            known = {item["id"], *self.store.queries.instruction_ready_ids()}
            sequence = self.learning_store.guided_sequence(item, guide_kinds, known_background_ids=known)
            if sequence and "guide_sequence" not in checkpoint:
                item = self.learning_store.advance_instruction(item["id"], "guide", state={
                    "guide_sequence": sequence, "guide_index": 0, "guide_completed_ids": [],
                    "guide_question": sequence[0], "guide_draft": {}}, instruction_scope=self._learning_item.get("instruction_scope"))
                self._learning_item = item
                checkpoint = item["checkpoint"]
            if not guide_kinds:
                self._show_learning_guide_paused()
                return
            guide_kind = ("pronunciation_rule" if item.get("domain") == "pronunciation" else
                          "vocab_meaning" if item["family"] == "word" else "grammar_meaning")
            if any(raw.get("input_mode") == "pitch_fill" for raw in
                   item.get("question_templates", {}).get("pronunciation_application", [])):
                guide_kind = "pronunciation_application"
            if "familiar" in levels.values():
                candidates = [kind for kind in guide_kinds
                    if levels.get(KIND_ABILITIES[kind]) in {"new", "some"}
                    and kind in enabled and kind not in LISTENING_KINDS]
                if candidates:
                    guide_kind = candidates[0]
            # Unopened legacy guides may follow enabled alternatives, but an
            # existing answer/draft always belongs to its frozen question.
            if (guide_kind not in guide_kinds and guide_kinds
                    and not any(key in checkpoint for key in ("guide_question", "guide_answer", "guide_draft"))):
                guide_kind = next((kind for kind in guide_kinds if kind not in LISTENING_KINDS), guide_kinds[0])
            q = self.learning_store.guided_question(item, guide_kind, variant=1)
            self._learning_guide = q
            self._learning_guide_result = u.col()
            if q["kind"] not in enabled:
                self._show_learning_guide_paused()
                return
            practice = q.get("practice")
            if practice and "guide_question" not in checkpoint:
                item = self.learning_store.advance_instruction(item["id"], "guide", state={
                    "guide_question": deepcopy(q), "guide_draft": {}}, instruction_scope=self._learning_item.get("instruction_scope"))
                self._learning_item, checkpoint = item, item["checkpoint"]
            choices = ([self.learning_pitch_controls(q, checkpoint.get("guide_draft") or {}, guided=True)]
                if is_pitch_question(q) else
                self._learning_guide_controls(q, checkpoint.get("guide_draft") or {}) if practice or q.get('input_mode') == 'multi_cloze' else
                [u.button(choice, lambda w, answer=choice, expected=q: self.answer_learning_guide(answer, expected_question=expected),
                          multiline=True, text_role=learning_text_role(q, "choice")) for choice in q.get("choices", [])])
            instruction = question_instruction(q)
            task = [u.label(instruction, size=13, color=u.MUTED)] if instruction else []
            if is_pitch_question(q):
                task = [u.label(q["task"].replace("并分开助词", ""), size=17)]
            stimulus = []
            if q.get('audio_text'):
                def play_guide(widget=None):
                    if self.page == 'learning_guide' and self._learning_guide is q:
                        self.speak_text(q['audio_text'])
                speaker = u.speaker_button(play_guide, '播放引导题目')
                speaker._feedback_fragment = {'kind': 'guide_audio', 'question_id': q['id']}
                stimulus.append(speaker)
            reference = self._shared_learning_guide_reference(item, q) if shared_intro else []
            guide_prompt = (self._learning_guide_context['prompt'] if q.get('input_mode') == 'multi_cloze'
                            else self._learning_prompt_block(q, size=20))
            if practice:
                demonstration = self._learning_guide_demonstration(q, checkpoint)
                self.screen("learning_guide", "跟着做", *stimulus, *([guide_prompt] if guide_prompt is not None else []),
                    *task,
                    *self._learning_context_support(q),
                    demonstration, *choices, self._learning_guide_result, *reference, *self._learning_guide_archive(checkpoint))
            else:
                self.screen("learning_guide", "跟着做",
                    *([] if shared_intro else [teaching_block(u, item, compact=True)]), *task,
                    *stimulus,
                    *([] if is_pitch_question(q) else [guide_prompt]),
                    *reference, *choices, self._learning_guide_result, *self._learning_guide_archive(checkpoint))
            if checkpoint.get("guide_answer") is not None:
                self.render_learning_guide_result(bool(checkpoint.get("guide_correct")))
            return
        if enrolled:
            self.learning_store.mark_instruction_exposed(item["id"], expected_introduction_id=item["introduction_id"], instruction_scope=self._learning_item.get("instruction_scope"))
            self._learning_item = self.learning_store.instruction_item(item["id"], item.get("instruction_scope"))
        compact = bool(familiarity and familiarity.get("route") == "compact"
                       and familiarity.get("diagnostic_state") != "needs_teaching")
        children = [u.label(item["title"], size=25, bold=True, color=u.GREEN,
                            text_role="study" if item["family"] == "word" else "ui")]
        if item.get("reading"):
            children.append(u.row(u.label(self.learning_reading_label(item), size=18, flex=1, text_role="study"),
                *([] if item.get('personal_reading', {}).get('pending') else [u.speaker_button(lambda w: self.speak_text(item["reading"]))])))
        admitted = item.get('enrolled_abilities', [])
        focused_reading = item['family'] == 'word' and admitted == ['reading']
        conceptual = self.learning_store.get_unit(item['id'], 'understanding') if enrolled else None
        practiced_explanation = bool(conceptual and conceptual.get('instruction_ready'))
        focused_application = admitted in (['use'], ['application'], ['listening']) and practiced_explanation
        focused = focused_reading or focused_application
        if not focused:
            children.append(teaching_block(u, item, compact=compact, speak=self.speak_text))
        from .learning_questions import practice_templates
        has_practice = any(raw.get("practice") for variants in (practice_templates(item) or {}).values() for raw in variants)
        children.append(u.button("跟着练习" if has_practice else "跟着做一题", self.start_learning_guide, primary=True))
        if not compact or focused:
            children.append(self.example_cards(item))
        if focused:
            explanation = u.col()
            def toggle_explanation(widget):
                if explanation.children:
                    explanation.clear()
                    widget.text = '展开讲解'
                else:
                    explanation.add(teaching_block(u, item, speak=self.speak_text))
                    widget.text = '收起讲解'
            children.extend([u.button('展开讲解', toggle_explanation, compact=True), explanation])
        pitch = self.pronunciation_pitch_examples(item)
        if pitch is not None:
            children.append(pitch)
        audio = self.pronunciation_audio_examples(item, rendered=children)
        if audio is not None:
            children.append(audio)
        if instruction.get("prerequisite"):
            children.append(u.button("还不熟悉接续？", self.show_learning_prerequisite, compact=True))
        children.append(u.button("稍后再学", self.leave_learning))
        children.append(original_notes(u, item))
        self.screen("learning_intro", "快速回顾" if compact else "初次学习", *children)

    def _shared_learning_guide_reference(self, item, question):
        """Optional explanation stays beside this ability's live guided work."""
        u = ui()
        body = u.col()
        def toggle(widget):
            if self.page != 'learning_guide' or self._learning_guide is not question:
                return
            if body.children:
                body.clear()
                widget.text = '讲解'
            else:
                body.add(teaching_block(u, item, speak=self.speak_text))
                widget.text = '收起讲解'
        # Current practice guides already show an ability-specific demonstration.
        # Old simple word guides still need this small first-use explanation.
        primer = []
        if not question.get('practice') and item['family'] == 'word':
            ability = question.get('ability')
            if ability == 'reading' and item.get('reading'):
                primer.append(u.row(u.label(self.learning_reading_label(item), size=18, text_role='study', flex=1),
                    *([] if item.get('personal_reading', {}).get('pending') else
                      [u.speaker_button(lambda w: self.speak_text(item['reading']))])))
            elif ability == 'understanding' and item.get('meaning'):
                primer.append(u.label(item['meaning'], size=16))
        return [*primer, u.button('讲解', toggle, compact=True, width=100, font_size=12), body]

    def _learning_guide_archive(self, checkpoint):
        from .learning_replacement import PREFIX
        identity = checkpoint.get('guide_replacement_id')
        event = self.store.get_setting(PREFIX + 'guide:' + identity, None) if identity else None
        if not event:
            return []
        old = event['before']
        return [self._learning_archive_control(old['guide_question'], old.get('guide_draft', {}))]

    def show_learning_prerequisite(self, widget=None, **kwargs):
        u = ui()
        value = self._learning_item["instruction"].get("prerequisite", "")
        text = "\n".join(str(v) for v in value.values()) if isinstance(value, dict) else str(value)
        self.screen("learning_prerequisite", "接续说明",
            block_widget(u, {"kind": "points", "label": "先掌握", "items": [part for part in text.split("\n") if part]}),
            u.button("返回讲解", lambda w: self.show_learning_instruction(self._learning_item), primary=True))

    def start_learning_guide(self, widget=None, **kwargs):
        content_id = self._learning_item["id"]
        self.learning_store.advance_instruction(content_id, "intro_done", instruction_scope=self._learning_item.get("instruction_scope"))
        # A review can become due while the introduction is being read. Finish
        # the guide the user explicitly opened before interleaving other work.
        self.show_learning_instruction(self.learning_store.instruction_item(content_id, self._learning_item.get("instruction_scope")))

    def answer_learning_guide(self, answer, *, draft=None, question_id=None, expected_question=None):
        u = ui()
        if (self.page != "learning_guide" or (question_id is not None and self._learning_guide["id"] != question_id)
                or expected_question is not None and self._learning_guide is not expected_question):
            return
        from .answer_input_ui import prepare_submission
        ready, answer = prepare_submission(self._learning_guide, answer,
            getattr(self, "_learning_guide_context", {}).get("status"))
        if not ready:
            return
        result = grade_learning_answer(self._learning_guide, answer)
        if result.get("status") in {"empty", "invalid"}:
            context = getattr(self, "_learning_guide_context", {})
            if context.get("question") is self._learning_guide:
                context["status"].text = result.get("message", "请先完成这一题。")
            return
        state = {"guide_answer": answer, "guide_correct": result["status"] == "correct",
                 "guide_question": deepcopy(self._learning_guide)}
        if draft is not None and (is_pitch_question(self._learning_guide) or self._learning_guide.get("practice")):
            state["guide_draft"] = deepcopy(draft)
            if self._learning_guide.get("input_mode") == "text":
                state["guide_draft"]["answer"] = answer
        try:
            self.learning_store.advance_instruction(self._learning_item["id"], "guide" if result["status"] == "correct" else "guide_failed",
                state=state, instruction_scope=self._learning_item.get("instruction_scope"))
        except Exception as exc:
            from .diagnostic_events import record_error
            record_error("learning_guide", "submit", exc)
            context = getattr(self, "_learning_guide_context", {})
            if context.get("question") is self._learning_guide:
                context["status"].text = "未能保存本次作答，请重试。"
            return
        context = getattr(self, "_learning_guide_context", {})
        if context.get("question") is self._learning_guide and "guide_draft" in state:
            context["draft"] = deepcopy(state["guide_draft"])
        self.render_learning_guide_result(result["status"] == "correct")

    def render_learning_guide_result(self, correct):
        from .answer_feedback import build_answer_feedback
        u = ui()
        self._learning_guide_result.clear()
        self._learning_guide_result.add(build_answer_feedback({"correct": correct}))
        if is_pitch_question(self._learning_guide):
            context = getattr(self, "_learning_pitch_context", {})
            if not correct:
                self._learning_guide_result.add(answer_diagram(self, self._learning_guide, reveal=True))
                entered = context.get("draft", {}).get("answer")
                if not entered:
                    saved = self.learning_store.instruction_item(self._learning_item["id"], self._learning_item.get("instruction_scope")) or {}
                    entered = saved.get("checkpoint", {}).get("guide_answer")
                diagnosis = grade_learning_answer(self._learning_guide, entered).get("message", "")
                if diagnosis and diagnosis != self._learning_guide.get("feedback", ""):
                    self._learning_guide_result.add(u.label(diagnosis, size=13, color=u.MUTED))
            self._learning_guide_result.add(u.label(self._learning_guide.get("feedback", "")))
            if correct:
                if context.get("question") is self._learning_guide:
                    context["locked"] = True
                    for control in [*context["buttons"].values(), context["submit"]]:
                        control.enabled = False
                self._learning_guide_result.add(u.button("继续",
                    lambda w, expected=self._learning_guide: self.finish_learning_guide(expected_question=expected), primary=True))
            self._reveal_learning_guide_result()
            return
        speaker = self._learning_answer_speaker(self._learning_guide, guided=True)
        if correct:
            context = getattr(self, "_learning_guide_context", {})
            if context.get("question") is self._learning_guide:
                context["locked"] = True
                for control in context["buttons"]:
                    control.enabled = False
            answer = u.label(self._learning_guide.get("feedback", ""), flex=1 if speaker else 0)
            self._learning_guide_result.add(u.row(answer, speaker) if speaker else answer,
                u.button("继续", lambda w, expected=self._learning_guide:
                    self.finish_learning_guide(expected_question=expected), primary=True))
        else:
            answer = u.label(answer_text(self._learning_guide), bold=True, flex=1 if speaker else 0,
                text_role=learning_text_role(self._learning_guide, "answer"))
            self._learning_guide_result.add(u.row(answer, speaker) if speaker else answer,
                u.label(self._learning_guide.get("feedback", "")))
        # The result is already attached to the visible guide. Its introduction
        # identity survives rebuilding this page, so returning cannot replay it.
        if self.page == "learning_guide":
            autoplay_answer(self, self._learning_answer_audio_text(self._learning_guide),
                ("learning-guide", self._learning_item.get("introduction_id") or self._learning_item["id"],
                 self._learning_guide["id"]))
        if self._learning_guide.get("practice"):
            self._reveal_learning_guide_result()

    def finish_learning_guide(self, widget=None, *, question_id=None, expected_question=None, **kwargs):
        from .new_dispatch import enabled
        if (self.page != "learning_guide" or (question_id is not None and self._learning_guide["id"] != question_id)
                or expected_question is not None and self._learning_guide is not expected_question):
            return
        saved = self.learning_store.instruction_item(self._learning_item["id"], self._learning_item.get("instruction_scope"))
        if saved.get("checkpoint", {}).get("guide_sequence"):
            advanced = self.learning_store.advance_guided_sequence(saved["id"], question_id=self._learning_guide["id"], instruction_scope=self._learning_item.get("instruction_scope"))
            if not advanced["finished"]:
                self.show_learning_instruction(advanced["item"])
                return
        self.learning_store.advance_instruction(self._learning_item["id"], "guide_correct", fair_new=enabled(self), instruction_scope=self._learning_item.get("instruction_scope"))
        if getattr(self, "_study_path_enabled", False):
            self.continue_study_path(_checkpoint_saved=True)
        else:
            self.continue_learning()

    def show_learning_question(self, current):
        u = ui()
        q = current["question"]
        self._learning_question = q
        self._learning_question_state = dict(current.get("draft") or {})
        if (current.get("result") or {}).get("outcome") == "cancelled":
            # Resume an explicit skip interrupted between submit/finalize;
            # cancellation must never open answer feedback on restart.
            self.learning_store.finalize(q["id"], rating=None)
            self.continue_added_learning()
            return
        if current.get("result"):
            self.show_learning_feedback(current["result"])
            return
        self._learning_audio_ok = bool(self._learning_question_state.get("audio_ok"))
        self._learning_answer = self._learning_question_state.get("answer", "")
        parts = self._learning_question_state.get("parts", [])
        self._learning_parts = list(parts)
        actions = [u.button("显示答案", self.reveal_learning_answer, compact=True, font_size=12, flex=1),
                   u.button("再看讲解", self.learning_hint, compact=True, font_size=12, flex=1)]
        actions.append(u.button("问 AI", lambda w, qid=q["id"]: self.open_tutor_question(
            source="learning", question_id=qid), compact=True, font_size=12, flex=1))
        if prompt_audio_text(q):
            actions.append(u.speaker_button(self.play_learning_audio, "朗读题干"))
        task = q["task"].replace("并分开助词", "") if is_pitch_question(q) else question_instruction(q)
        children = []
        prompt = None
        if q.get("audio_text"):
            children.append(u.speaker_button(self.play_learning_audio, "播放题目"))
        elif not is_pitch_question(q):
            if q.get('input_mode') == 'multi_cloze':
                from .multi_cloze_ui import prompt_view
                prompt = prompt_view(self, u, q, self._learning_question_state,
                    clean_prompt_preferences(self.store.get_setting(PROMPT_PREFS, {})))
            else:
                prompt = self._learning_prompt_block(q, clean_prompt_preferences(self.store.get_setting(PROMPT_PREFS, {})), size=20)
            children.append(prompt)
        # The Japanese utterance or listening stimulus owns the first position.
        # Keep indispensable context and one short response cue,
        # with stable secondary help above the answer controls, never beside
        # Submit where "show answer" could be mistaken for submitting a draft.
        children.append(u.col(*([u.label(task, size=13, color=u.MUTED)] if task else []),
                              u.row(*actions)))
        if q.get("gloss"):
            children.append(u.label(q["gloss"], size=12, color=u.MUTED))
        children.extend(self._learning_context_support(q))
        mode = q.get("input_mode", "choice")
        if is_pitch_question(q):
            children.append(self.learning_pitch_controls(q, self._learning_question_state))
        elif mode == 'multi_cloze':
            from .multi_cloze_ui import controls as cloze_controls

            def save_cloze(updated):
                self.learning_store.save_draft(q['id'], updated)
                self._learning_question_state.update(deepcopy(updated))

            def submit_cloze(answer):
                self.submit_learning_answer(answer, question_id=q['id'])
                return self.page != 'learning_question' or self._learning_question is not q

            children.append(cloze_controls(self, u, q, self._learning_question_state, save_cloze,
                submit_cloze,
                lambda: self.page == 'learning_question' and self._learning_question is q, prompt=prompt))
        elif mode == "choice":
            choices = q.get("choices", [])
            children.extend(u.button(choice, lambda w, answer=choice, qid=q["id"]: self.submit_learning_answer(answer, question_id=qid), multiline=True,
                                     text_role=learning_text_role(q, "choice")) for choice in choices)
        elif mode == "reorder":
            self._learning_composition = u.label("".join(parts) or "点击词块组成句子", size=20,
                                                 text_role="study" if parts else "ui")
            children.append(self._learning_composition)
            # Duplicate blocks are identified by position, not just text.
            used = list(self._learning_question_state.get("used", []))
            self._learning_used = used
            context = {"question": q, "buttons": []}
            self._learning_blocks_context = context
            for index, choice in enumerate(q.get("choices", [])):
                b = u.button(choice, lambda w, i=index, question=q: self.add_learning_part(i, expected_question=question), multiline=True, text_role="study")
                b.enabled = index not in used
                context["buttons"].append(b)
                children.append(b)
            context["undo"] = u.button("撤回末项", lambda w, question=q: self.delete_learning_part(expected_question=question))
            context["undo"].enabled = bool(used)
            context["submit"] = u.button("提交", lambda w, question=q: self.submit_learning_answer(
                "".join(self._learning_parts), question_id=question["id"]), primary=True)
            children.extend([context["undo"], context["submit"]])
        else:
            from .answer_input_ui import answer_controls
            from .answer_input import available, system_japanese_enabled
            system_input = system_japanese_enabled(self.store)
            self._learning_input, controls = answer_controls(u, q, self._learning_answer,
                self.save_learning_input, lambda: self.page == "learning_question"
                and self._learning_question is q, system_japanese=system_input)
            children.extend(controls)
            if available(q, system_japanese=system_input):
                children.append(u.button("提交", lambda w, qid=q["id"]: self.submit_learning_answer(
                    self._learning_input.value, question_id=qid), primary=True))
            else:
                children.extend([u.label("这道旧题需要额外的日文输入。可换题，草稿保留且不计错。", size=13, color=u.MUTED),
                    u.button("换一道题", lambda w, qid=q["id"]: self.skip_unavailable_learning_input(qid), primary=True),
                    u.button("作答设置", self.show_learning_settings, compact=True)])
        self._learning_status = u.label("", size=12, color=u.MUTED)
        children.append(self._learning_status)
        if q.get('replacement_of'):
            old = self.learning_store.get_question(q['replacement_of'])
            if old:
                children.append(self._learning_archive_control(old, old['draft']))
        self.screen("learning_question", KIND_LABELS.get(q["kind"], "练习"), *children)
        if self._learning_question_state.get('presented') is not True:
            self.learning_store.save_draft(q['id'], {'presented': True})
            self._learning_question_state['presented'] = True

    def save_learning_input(self, widget=None, **kwargs):
        if self.page != "learning_question":
            return
        self._learning_question_state["answer"] = self._learning_input.value
        self.learning_store.save_draft(self._learning_question["id"], self._learning_question_state)

    def skip_unavailable_learning_input(self, question_id):
        if self.page != "learning_question" or self._learning_question["id"] != question_id:
            return
        from .answer_input import available, system_japanese_enabled
        q = self._learning_question
        if available(q, system_japanese=system_japanese_enabled(self.store)):
            return
        # Explicitly release the old pending slot without revealing its answer,
        # rewriting the snapshot/draft, or awarding a success/failure rating.
        self.learning_store.submit(question_id, self._learning_question_state.get("answer", ""),
                                   correct=None, outcome="cancelled")
        self.learning_store.finalize(question_id, rating=None)
        self.continue_added_learning()

    def _edit_learning_parts(self, index, expected_question=None):
        if (self.page != "learning_question" or self._learning_question.get("input_mode") != "reorder"
                or expected_question is not None and self._learning_question is not expected_question):
            return
        context = getattr(self, "_learning_blocks_context", {})
        if context.get("question") is not self._learning_question:
            return
        from .word_blocks import proposed_blocks
        proposed = proposed_blocks(self._learning_question["choices"], self._learning_question_state, index)
        if proposed is None:
            return
        try:
            saved = self.learning_store.save_draft(self._learning_question["id"], proposed)
        except Exception as exc:
            from .diagnostic_events import record_error
            record_error("learning_word_blocks", "save", exc)
            self._learning_status.text = "输入未保存，请重试。"
            return
        if saved["status"] != "pending" or saved.get("result"):
            self.continue_learning()
            return
        self._learning_question_state = saved["draft"]
        self._learning_parts = list(saved["draft"].get("parts", []))
        self._learning_used = list(saved["draft"].get("used", []))
        self._learning_composition.text = "".join(self._learning_parts) or "点击词块组成句子"
        configure_text_role(self._learning_composition, "study" if self._learning_parts else "ui")
        for position, control in enumerate(context["buttons"]):
            control.enabled = position not in self._learning_used
        context["undo"].enabled = bool(self._learning_used)
        self._learning_status.text = ""

    def add_learning_part(self, index, *, expected_question=None):
        self._edit_learning_parts(index, expected_question)

    def delete_learning_part(self, widget=None, *, expected_question=None, **kwargs):
        self._edit_learning_parts(None, expected_question)

    def play_learning_audio(self, widget=None, **kwargs):
        q = self._learning_question
        if self.page != "learning_question":
            return
        text = (q.get("audio_text") if q.get("kind") in LISTENING_KINDS else None) or prompt_audio_text(q)
        if not text:
            return
        token = self._page_token
        async def play():
            from .diagnostic_events import record_error
            if token != self._page_token or self.page != "learning_question" or self._learning_question["id"] != q["id"]:
                return
            try:
                await self.platform.play(text)
            except Exception as exc:
                record_error("learning_audio", "play", exc)
                if token == self._page_token:
                    self._learning_status.text = "播放失败，请检查日语语音后重试；本题尚未评分。"
                return
            if token == self._page_token:
                draft = {**self._learning_question_state, "audio_ok": True}
                try:
                    self.learning_store.save_draft(q["id"], draft)
                except Exception as exc:
                    record_error("learning_audio", "save", exc)
                    self._learning_status.text = "语音已播放，但播放状态未保存。请重试；本题尚未评分。"
                    return
                self._learning_audio_ok = True
                self._learning_question_state.update(audio_ok=True)
        self.loop.create_task(play())

    def _learning_instruction_example(self, instruction):
        text = instruction.get("example", "")
        role = instruction.get("example_text_role", "study")
        if role == "study":
            return self.annotated_sentence(text, size=20, audio=True)
        return ui().label(text, size=20, text_role=role)

    def learning_hint(self, widget=None, **kwargs):
        u = ui()
        q = self._learning_question
        item = self.learning_store.get_item(q["target_id"])
        if self._unverified_learning_reference(item, "返回题目"):
            return
        if is_pitch_question(q):
            self._learning_question_state = pitch_editor_draft(q, self._learning_question_state)
        self._learning_question_state["assisted"] = True
        self.learning_store.save_draft(q["id"], self._learning_question_state)
        instruction = item["instruction"]
        self.screen("learning_prerequisite", "回看讲解", teaching_block(u, item, speak=self.speak_text),
            self._learning_instruction_example(instruction),
            self.natural_translation_label(instruction.get('example', ''), instruction.get('translation', '')),
            *self.order_translation_rows(instruction.get('example', ''), instruction.get('translation', ''),
                explicit=instruction.get('order_translation')),
            u.button("返回题目", self.continue_learning, primary=True))

    def _unverified_learning_reference(self, item, return_caption, *, page="learning_prerequisite", on_return=None):
        from .assessment_policy import teaching_allowed
        if teaching_allowed(item, store=self.store):
            return False
        u = ui()
        missing = item.get("instruction_status") == "instruction_missing"
        self.screen(page, "学习材料尚不完整" if missing else "材料待核验",
            u.label("学习材料尚不完整。" if missing else "材料尚待核验，仅可预览草稿。", size=15),
            u.button("查看材料" if missing else "预览草稿", lambda w: self.show_learning_detail(item)),
            u.button(return_caption, on_return or self.continue_learning, primary=True))
        return True

    def reveal_learning_answer(self, widget=None, **kwargs):
        if is_pitch_question(self._learning_question):
            self._learning_question_state = pitch_editor_draft(self._learning_question, self._learning_question_state)
        self._learning_question_state["assisted"] = True
        self.learning_store.save_draft(self._learning_question["id"], self._learning_question_state)
        self.submit_learning_answer(self._learning_question_state.get("answer", ""), revealed=True)

    def submit_learning_answer(self, answer, *, revealed=False, question_id=None):
        if self.page != "learning_question" or (question_id is not None and question_id != self._learning_question["id"]):
            return
        q = self._learning_question
        from .answer_input_ui import prepare_submission
        ready, answer = prepare_submission(q, answer, self._learning_status, revealed=revealed)
        if not ready:
            return
        if q.get("audio_text") and not self._learning_audio_ok and not revealed:
            self._learning_status.text = "请先成功播放题目。"
            return
        if not str(answer).strip() and not revealed:
            self._learning_status.text = "请先作答。"
            return
        result = grade_learning_answer(q, answer)
        if is_pitch_question(q) and result.get("status") in {"empty", "invalid"} and not revealed:
            self._learning_status.text = result.get("message", "请完成作答。")
            return
        self.platform.stop()
        outcome = "revealed" if revealed else result["status"]
        if q.get("audio_text") and not self._learning_audio_ok:
            outcome = "device_failure"
        plan = prompt_support(q, clean_prompt_preferences(self.store.get_setting(PROMPT_PREFS, {})))
        saved = self.learning_store.submit(q["id"], str(answer), None if outcome in {"unresolved", "device_failure"} else result["status"] == "correct",
            assisted=bool(self._learning_question_state.get("assisted")), outcome=outcome,
            presentation={"prompt_furigana_visible": plan["furigana_visible"], "prompt_accent_visible": plan["accent_visible"], "audio_used": self._learning_audio_ok})
        self.show_learning_feedback(saved)

    def _reveal_learning_guide_result(self):
        """Bring new inline feedback into view after native layout settles."""
        scroll = getattr(self, "_page_scroll", None)
        result = self._learning_guide_result
        question = self._learning_guide
        if scroll is None or not hasattr(result, "layout"):
            return

        async def reveal():
            await asyncio.sleep(.05)
            if (self.page != "learning_guide" or self._learning_guide is not question
                    or self._learning_guide_result is not result
                    or getattr(self, "_page_scroll", None) is not scroll):
                return
            # Use content coordinates, not a saved pixel offset: large fonts
            # and wrapping change the result's position. The scroll container
            # clamps the requested position to its current native bounds.
            top = result.layout.absolute_content_top - scroll.content.layout.absolute_content_top
            scroll.vertical_position = max(0, int(top) - 8)

        self.loop.create_task(reveal())

    def show_learning_feedback(self, result):
        from .answer_feedback import build_answer_feedback
        from .answer_result_ui import entered_answer_row, distinct_explanation
        u = ui()
        q = self._learning_question
        originally_unresolved = result.get("outcome") == "unresolved"
        from .assessment_policy import settlement_decision
        decision = settlement_decision(self.learning_store.get_item(q["target_id"]), q, result.get("assessment"), store=self.store)
        if decision["settlement_channel"] != "formal":
            # An old saved feedback may predate eligibility metadata. This is
            # a view only; its original first answer remains frozen in storage.
            result = {**result, "correct": None, "outcome": "unresolved", "assessment": decision}
        self._learning_feedback_view_result = (q['id'], deepcopy(result))
        correct = result.get("correct", False)
        assisted = result.get("assisted", False)
        speaker = self._learning_answer_speaker(q)
        entered = result.get("answered_text", result.get("answer", ""))
        from .word_blocks import structure_diagnostic
        structure_error = (structure_diagnostic(q, entered)
            if originally_unresolved and decision["assessment_eligible"]
            and not assisted and not result.get("revealed")
            and not result.get("diagnostic_needs_teaching") else None)
        # An accepted sentence contour/segmentation is the learner's correct
        # answer. Do not replace it with a different canonical alternative.
        accepted_sequence = bool(correct and entered and q.get("pitch_practice", {}).get("version") in (2, 3))
        compare_reading = (q.get("kind") == "vocab_reading" and correct is False
                           and bool(entered) and not assisted and entered != q["answer"])
        answer = (answer_diagram(self, q, answer=entered) if accepted_sequence else
                  answer_diagram(self, q, reveal=True) if is_pitch_question(q) else
                  u.label(answer_text(q), size=20, flex=1 if speaker else 0,
                          text_role=learning_text_role(q, "answer")))
        if compare_reading:
            from .spelling_display import build_spelling_comparison
            answer = build_spelling_comparison(self, q["answer"], entered,
                text_role=learning_text_role(q, "answer"), size=20, reserved_width=48 if speaker else 0)
        children = [build_answer_feedback(result, structure_error=structure_error),
                    u.row(answer, speaker) if speaker else answer]
        if result.get("diagnostic_needs_teaching"):
            children.append(u.label("先补学这项内容，再安排独立练习。", size=14, color=u.MUTED))
        entered = result.get("answered_text", result.get("answer", ""))
        if entered and not correct and entered != q["answer"] and not compare_reading:
            if is_pitch_question(q):
                entered = pitch_answer_label(q, entered)
            else:
                entered = answer_text(q, entered)
            children.append(entered_answer_row(u, entered, text_role=learning_text_role(q, "answer")))
        explanation = distinct_explanation(q.get("feedback"), answer_text(q))
        if is_pitch_question(q) and not correct and entered:
            diagnosis = grade_learning_answer(q, result.get("answered_text", result.get("answer", ""))).get("message", "")
            if diagnosis and diagnosis != explanation:
                children.append(u.label(diagnosis, size=13, color=u.MUTED))
        if explanation and explanation != str(q["answer"]).strip():
            children.append(u.label(explanation))
        from .personal_content_notes_ui import append_notes
        append_notes(self, u, children, 'question', q, page='learning_feedback')
        children.append(u.row(u.col(flex=1), u.button("问 AI", lambda w, qid=q["id"]: self.open_tutor_question(
            source="learning", question_id=qid, after_answer=True), compact=True, width=72, font_size=12),
            u.button("详细", lambda w: self.learning_feedback_detail(), compact=True, width=72, font_size=12)))
        if result.get("outcome") in {"unresolved", "device_failure", "invalid"}:
            from .assessment_policy import assessment_message
            message = ("句末词块后还有内容；本次不调整单词复习。" if q.get("family") == "word"
                       else "句末词块后还有内容；本次不调整复习。") if structure_error else (
                "本次不调整读音复习。" if result.get("error_category") == "spelling"
                else assessment_message(result.get("assessment")))
            children.append(u.label(message, size=13, color=u.MUTED))
        if correct and not assisted and result.get("outcome") == "correct":
            children.extend([u.row(*[u.button(title, lambda w, rating=rating, qid=q["id"]: self.finish_learning_question(rating, question_id=qid), flex=1, multiline=True)
                for title, rating in [("再学一次", "again"), ("有些困难", "hard")]]),
                u.row(*[u.button(title, lambda w, rating=rating, qid=q["id"]: self.finish_learning_question(rating, question_id=qid), flex=1, multiline=True)
                for title, rating in [("记住了", "good"), ("很轻松", "easy")]])])
        else:
            children.append(u.button("保存并继续", lambda w, qid=q["id"]: self.finish_learning_question("again", question_id=qid), primary=True))
        self.screen("learning_feedback", "作答结果", *children)
        autoplay_answer(self, self._learning_answer_audio_text(q), ("learning", q["id"]))

    def _learning_answer_speaker(self, question, *, guided=False):
        """One post-answer control; never changes attempts or listening credit."""
        text = self._learning_answer_audio_text(question)
        if not text:
            return None
        page = "learning_guide" if guided else "learning_feedback"
        attribute = "_learning_guide" if guided else "_learning_question"

        def play(widget=None, **kwargs):
            if self.page == page and getattr(self, attribute, None) is question:
                self.speak_text(text)

        return ui().speaker_button(play, "朗读答案对应的日语")

    def _learning_answer_audio_text(self, question):
        """Speak the Japanese content, not a Chinese meaning or a pitch chart.

        Gap answers are read in their completed Japanese context, so particles
        such as は/へ are not incorrectly pronounced as isolated kana names.
        This helper is used only after submission or a guided answer.
        """
        # Ordinary TTS does not certify the authored pitch contour. A result
        # chart must not accidentally offer an incompatible reference voice.
        if is_pitch_question(question):
            return ""
        from .multi_cloze import is_cloze_question
        kind = question.get("kind")
        answer = str(question.get("answer") or "").strip()
        if kind in {"listening_comprehension", "vocab_audio_meaning"}:
            return clean_utterance(question.get("audio_text") or "")
        if kind == "vocab_reading":
            return clean_utterance(answer)
        if question.get("family") == "word" and kind in {
                "vocab_meaning", "vocab_recall", "vocab_orthography", "vocab_zh_ja_contrast"}:
            item = self.learning_store.get_item(question.get("target_id")) or {}
            if item.get('personal_reading', {}).get('pending'):
                return ''
            if item.get("reading"):
                return clean_utterance(item["reading"])
        prompt = str(question.get("prompt") or "").strip()
        # Reuse the existing language partition for older mixed-language
        # snapshots; never feed their Chinese instructions into Japanese TTS.
        parts = _plain_prompt_parts(question, prompt)
        japanese = [text for text, role in parts if role == "study"]
        public_text = japanese[0] if len(japanese) == 1 else ""
        if kind in PRONUNCIATION_KINDS:
            return pronunciation_utterance(question, public_text)
        if is_cloze_question(question):
            return completed_cloze_utterance(question)
        gap = re.compile(r"（\s*）|\(\s*\)|_{2,}|＿{2,}")
        if learning_text_role(question, "answer") == "study":
            # Some reviewed answers list interchangeable forms (に／へ).
            # Read one valid completion, not an impossible merged sentence.
            spoken_answer = re.split(r"／| / ", answer, maxsplit=1)[0]
            if public_text and len(gap.findall(public_text)) == 1:
                return clean_utterance(gap.sub(lambda match: spoken_answer, public_text))
            return clean_utterance(spoken_answer)
        if public_text and not gap.search(public_text):
            return clean_utterance(re.sub(r"／| / ", "、", public_text))
        # Numeric/diagram-only pronunciation questions have no Japanese
        # utterance. A silent chart must not acquire an invented TTS reference.
        return ""

    @page_view()
    def learning_feedback_detail(self):
        # Do not navigate to enrollment from a submitted question.
        # Retain the actual result view: Back must not ask the scheduler for a
        # different question or leave an already graded result in history.
        from .learning_questions import learning_order_translation_source
        u = ui()
        from .semantic_errors import feedback_nodes
        q = self._learning_question
        identity, result = getattr(self, '_learning_feedback_view_result', (None, {}))
        error_details = feedback_nodes(u, q, result) if identity == q['id'] else []
        item = self.learning_store.get_item(self._learning_question["target_id"])
        if self._unverified_learning_reference(item, "返回结果",
                page="learning_feedback_detail", on_return=self.go_back):
            return
        instruction = item["instruction"]
        teaching_reference = self.order_translation_rows(instruction.get('example', ''),
            instruction.get('translation', ''), explicit=instruction.get('order_translation'))
        question_reference = []
        reference = self._learning_question.get('order_translation')
        # Old local prompts have no ordinary translation to bind. Only new
        # authored metadata may add an answer-only question reference; the
        # teaching example remains available without inventing a translation.
        source = learning_order_translation_source(self._learning_question) if isinstance(reference, dict) else None
        if source and (source != instruction.get('example') or not teaching_reference):
            question_reference = self.order_translation_rows(source, '',
                explicit=reference,
                title='本题语序参考译文（实验）', show_source=True)
        self.screen("learning_feedback_detail", "详细", u.label(item["title"], size=22,
            text_role="study" if item["family"] == "word" else "ui"),
            *error_details,
            *question_reference,
            teaching_block(u, item, speak=self.speak_text),
            self._learning_instruction_example(instruction),
            self.natural_translation_label(instruction.get('example', ''), instruction.get('translation', '')),
            *teaching_reference,
            u.button("返回结果", self.go_back, primary=True))

    def finish_learning_question(self, rating, *, question_id=None):
        q = self._learning_question
        if self.page != "learning_feedback" or (question_id is not None and question_id != q["id"]):
            return
        from .new_dispatch import enabled
        self.learning_store.finalize(q["id"], {"again": 1, "hard": 2, "good": 3, "easy": 4}[rating], fair_new=enabled(self))
        self._queue_learning_questions(self.learning_store.get_item(q["target_id"]))
        if getattr(self, "_study_path_enabled", False):
            self.continue_study_path(_checkpoint_saved=True)
        else:
            self.continue_learning()

    def leave_learning(self, widget=None, **kwargs):
        self.platform.stop()
        self.back_stack.pages.clear()
        self._practice_context = False
        self._returning_home = True
        try:
            self.show_home()
        finally:
            self._returning_home = False
        self.back_stack.pages.clear()

    def request_learning_exit(self):
        if self.page in {"learning_wait", "learning_done"}:
            self.leave_learning()
            return
        if getattr(self, "_leave_confirmation_active", False):
            return
        self._leave_confirmation_active = True
        token = self._page_token
        async def confirm():
            try:
                confirmed = await self.main_window.dialog(ui().toga.ConfirmDialog("返回今日？", "进度、草稿和复习时间会保留。"))
                if confirmed and token == self._page_token:
                    self.leave_learning()
            finally:
                self._leave_confirmation_active = False
        self.loop.create_task(confirm())
