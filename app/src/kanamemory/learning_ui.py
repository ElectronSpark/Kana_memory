"""Native, resumable teaching and review screens for word senses and grammar.

The original kana and word-tool queues remain owned by StudyStore.  This mixin
never replaces them, and uses the additional LearningStore for explicit study.
"""
from __future__ import annotations

from datetime import datetime, timezone
from . import app_clock
from . import kana_attempt_runtime as kana_attempt
from . import kana_session_runtime as kana_session
from copy import deepcopy
import asyncio
import random
import re

from .navigation import page_view
from .answer_autoplay import autoplay_answer
from .answer_speech import clean_utterance, completed_cloze_utterance, pronunciation_utterance
from .answer_result_ui import answer_text, shown_answer, shown_explanation
from .learning_content import learning_items, learning_item_views, get_learning_item, learning_item_from_word
from .learning_questions import (build_learning_question, question_kinds, grade_learning_answer, TEXT_KINDS,
                                 PRONUNCIATION_KINDS, KIND_ABILITIES, LISTENING_KINDS, OPT_IN_LISTENING_KINDS,
                                 AUDIO_WORD_KIND, READING_CHOICE_KIND, CONTEXT_CHOICE_KIND,
                                 PREFERENCE_VERSION, SOUND_PREFERENCE_VERSION,
                                 audio_ready_kinds, stored_enabled_kinds)
from .question_instruction_ui import instruction_widgets
from .question_support import clean_prompt_preferences, build_prompt_block, prompt_audio_text, prompt_support, accent_caption, learning_text_role, _plain_prompt_parts, option_labels
from .typography import configure_text_role, configure_study_ranges
from .example_ui import ExampleUIMixin
from .structured_lesson_ui import teaching_block, original_notes, block_widget
from .study_path_ui import StudyPathUIMixin
from .ai_ui import AIUIMixin
from .learning_pitch_ui import LearningPitchUIMixin, answer_diagram
from .pitch_practice import is_pitch_question, answer_label as pitch_answer_label, editor_draft as pitch_editor_draft
from .new_dispatch import study_path_available
from .intro_batch_ui import IntroBatchUIMixin, INTRO_BATCH_PAGES, origin_labels
from .content_library_ui import ContentLibraryUIMixin
from . import course_answer_runtime
from . import course_teaching_runtime as teaching
from .course_answer_runtime import answer_session
from .course_line.answer import PROMPT_PREFS
from .course_line.answer_display import ANSWER_COMPARISON, ANSWER_PITCH, ANSWER_SEQUENCE, detail_model
from .course_line.answer_model import EditFailed, EditKept, EditLeft
from .course_line.teaching_model import GuideEmpty, GuideNext, GuideSaveFailed
from . import ui_components as u

LEARNING_BUSY_STATUS = "草稿暂未保存（数据库正忙），继续输入或提交时会重试。"

LEARNING_ACTIVE = {"learning_familiarity", "learning_intro", "learning_prerequisite", "learning_guide", "learning_question", "learning_feedback", "study_reference"} | INTRO_BATCH_PAGES
LEARNING_PAGES = LEARNING_ACTIVE | {"learning_wait", "learning_done"}
LEARNING_PREFS = "learning_preferences_v1"
# The orientation screen (design 12 UI1) and the settings button that opens it.
STUDY_ORIENTATION_TITLE = "学习取向"
# One queue pass spends at most this long exchanging withdrawn tasks (always at least one): a learner may
# have dozens queued, each exchange plans a task for its lesson, and the pass runs on the main thread when a
# round starts. The rest follow at later passes, the soonest answerable first, and one that is reached before
# its turn is exchanged when it would be shown (LearningUIMixin._replace_withdrawn_questions).
WITHDRAWN_PASS_SECONDS = 0.5
EXAM_COMMON_CATEGORY = "考试与常用补充"
EXAM_COMMON_TRACK = "exam-common-20261025"
KIND_LABELS = {
    "vocab_meaning": "词义理解", "vocab_audio_meaning": "听词辨义", "vocab_reading": "单词读法",
    "vocab_recall": "按意义提取单词", "vocab_orthography": "单词写法", "vocab_usage": "语境与搭配",
    "vocab_zh_ja_contrast": "中日表达辨析", "grammar_meaning": "语法含义", "grammar_cloze": "接续与填空",
    "grammar_transform": "活用与转换", "grammar_reorder": "句子重组", "targeted_correction": "找错并修正",
    "reading_comprehension": "短篇理解", "listening_comprehension": "短句听力", "controlled_sentence_production": "受控造句",
    "pronunciation_rule": "读音规则辨析", "pronunciation_application": "读音规则应用",
    AUDIO_WORD_KIND: "听词选词", READING_CHOICE_KIND: "看词选读音", CONTEXT_CHOICE_KIND: "读句选词",
}
KIND_GROUPS = {
    "理解与辨析": {"vocab_meaning", "vocab_zh_ja_contrast", "grammar_meaning", "reading_comprehension"},
    "读法与词汇提取": {"vocab_reading", READING_CHOICE_KIND, "vocab_recall", "vocab_orthography", "vocab_usage"},
    "听词选词": {AUDIO_WORD_KIND},
    "读句选词（无中文）": {CONTEXT_CHOICE_KIND},
    "语法应用": {"grammar_cloze", "grammar_transform", "grammar_reorder", "targeted_correction", "controlled_sentence_production"},
    "听力理解": OPT_IN_LISTENING_KINDS,
    "读音规则": set(PRONUNCIATION_KINDS),
}


ALL_LIBRARIES = "全部库"


class LearningUIMixin(IntroBatchUIMixin, LearningPitchUIMixin, AIUIMixin, StudyPathUIMixin, ExampleUIMixin,
                      ContentLibraryUIMixin):
    def _learning_prompt_block(self, question, preferences=None, *, size=20, known_ids=None):
        return build_prompt_block(self, question, preferences, size=size, known_ids=known_ids)

    def _learning_context_support(self, question, known=None):
        """Show only locally reviewed background help, never raw hint fields.

        known: the page's one course_planning.known_ids read; read here when the caller has none.
        """
        from .question_support import learning_question_display
        from .learning_dependencies import independent_ready_ids
        display = learning_question_display(question)
        children = []
        if display.get("context_hint"):
            children.append(u.label(display["context_hint"], size=13, color=u.MUTED))
        hints, ready = [], None
        if display.get("background_glosses"):
            ready = independent_ready_ids(self.learning_store)
            hints = [hint for hint in display["background_glosses"] if hint.get("target_id") not in ready]
        # Design 11 D5 (1T): an unmet declared word (not yet introduced) is glossed from its lesson,
        # unless an earlier successful review already made it familiar (the same rule as above).
        from .question_support import shared_requirements, unmet_requirement_glosses
        if known is None:
            from .course_planning import known_ids
            known = known_ids(self.store.queries)
        # One lookup of the question's template for the word glosses and the rule hints below.
        requires = shared_requirements(question)
        unmet = unmet_requirement_glosses(question, known, requires=requires)
        if unmet:
            ready = independent_ready_ids(self.learning_store) if ready is None else ready
            spans = [(old["start"], old["end"]) for old in hints
                     if type(old.get("start")) is int and type(old.get("end")) is int]
            unmet = [hint for hint in unmet if hint["target_id"] not in ready
                     and not any(start < hint["end"] and hint["start"] < end for start, end in spans)]
            hints = sorted([*hints, *unmet], key=lambda hint: hint.get("start", 0))
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
        children.extend(self._unmet_rule_hint_rows(question, known, requires))
        return children

    def _unmet_rule_hint_rows(self, question, known, requires=None):
        """One collapsed row per rule the question shows before its lesson (design 11 section 5, D8).

        Opening a row shows the rule lesson's short text in place; nothing is saved
        and it is not counted as help on the target.
        """
        from .question_support import unmet_rule_hints
        return [self._unmet_rule_hint_row(hint) for hint in unmet_rule_hints(question, known, requires=requires)]

    @staticmethod
    def _unmet_rule_hint_row(hint):
        title = "还没学的规则：" + hint["title"]
        body = u.col()
        opened = False

        def toggle(widget=None, **kwargs):
            nonlocal opened
            opened = not opened
            body.clear()
            if opened:
                body.add(*(u.label(line, size=13, color=u.MUTED) for line in hint["lines"]))
            trigger.text = title + (" ▾" if opened else " ▸")

        trigger = u.button(title + " ▸", toggle, compact=True, multiline=True,
                           font_size=12, color=u.MUTED, margin_bottom=2)
        row = u.col(trigger, body, margin_bottom=4)
        row._unmet_rule_hint = hint["id"]
        return row

    def _ensure_kana_introduction_checkpoint(self, question):
        """Persist a just-created queue before teaching can return from show_question.

        A normal question screen creates this checkpoint through _checkpoint;
        teaching returns before that screen exists. Never use the current UI's
        input bindings here: they may still contain a previous question's draft.
        """
        sid, index = kana_session.session_id(self), kana_session.question_index(self)
        pending = self.store.load_session_progress()
        if pending is not None:
            if (pending["session_id"] != sid or pending["question_index"] != index
                    or pending["questions"][index] != question):
                raise ValueError("The introduction no longer belongs to the active question")
            # Existing drafts and assistance state are owned by their checkpoint.
            return
        questions = self.store.get_session_questions(sid)
        if (index != 0 or kana_session.session_right(self) != 0 or not questions or questions[0] != question
                or self.store._session_lane(sid) != self.store.practice_lane):
            raise ValueError("The introduction does not match a newly started queue")
        assisted = [i for session, i in kana_attempt.assisted(self) if session == sid]
        self.store.save_session_progress(sid, index, kana_session.session_right(self), assisted, {})
        pending = self.store.load_session_progress()
        if pending is None or pending["session_id"] != sid or pending["question_index"] != index:
            raise ValueError("The introduction no longer belongs to the active question")

    def show_kana_introduction(self, question):
        from .kana_learning_intro import initial_familiarity, intro_content
        try:
            self._ensure_kana_introduction_checkpoint(question)
        except ValueError as exc:
            self.alert("无法开始介绍，已保存的练习保持不变：" + str(exc))
            return
        data = intro_content(self.repo, question)
        self._kana_intro_question = question
        context = (kana_session.session_id(self), kana_session.question_index(self), question)
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
        current = (kana_session.session_id(self), kana_session.question_index(self), self._kana_intro_question)
        if self.page != "kana_introduction" or (expected is not None and expected != current):
            return
        try:
            from .new_dispatch import enabled
            select_initial_familiarity(self.store, *current, level, fair_new=enabled(self))
        except ValueError as exc:
            self.alert("熟悉程度未更改：" + str(exc))
            return
        if level == "familiar" and study_path_available(self):
            self.continue_study_path(_checkpoint_saved=True)
        elif study_path_available(self):
            # A selected teaching depth must open this introduction, not let
            # ordinary due work replace the page before its content is shown.
            question = current[2]
            self.show_question(_first_teaching=True,
                _first_teaching_targets={(question.content_id,question.script)})
        else:
            self.show_question()

    def finish_kana_introduction(self, widget=None, *, expected=None, **kwargs):
        from .kana_learning_intro import finish_introduction
        current = (kana_session.session_id(self), kana_session.question_index(self), self._kana_intro_question)
        if self.page != "kana_introduction" or (expected is not None and expected != current):
            return
        from .new_dispatch import enabled
        finish_introduction(self.store, *current, fair_new=enabled(self))
        if study_path_available(self):
            self.continue_study_path(_checkpoint_saved=True)
        else:
            self.show_question()

    def show_kana_intro_wait(self, due):
        self.screen("kana_intro_wait", "稍后试一题", u.label(self._learning_due_label(due), size=19),
            u.label("刚学的内容已保存，到期后自动继续。也可以先离开。", color=u.MUTED),
            u.button("检查并继续", self.check_learning_wait, primary=True),
            *(self.learning_next_content_controls() if study_path_available(self) else []),
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
            from .draft_debounce import call_later_awake
            # Armed from native-callback chains too: wake Toga Android's loop.
            self._review_wait_handle = call_later_awake(self.loop,
                max(1, min(60, delay)), self.tick_learning_wait, token, due)

    def continue_added_learning(self, widget=None, **kwargs):
        if study_path_available(self):
            self.continue_study_path()
        else:
            self.continue_learning()

    def check_learning_wait(self, widget=None, **kwargs):
        """A waiting screen checks all main study work, not just its old lane."""
        if (self.page not in {"kana_intro_wait", "learning_wait"}
                or getattr(self, "_leave_confirmation_active", False)):
            return
        if (study_path_available(self)
                and getattr(self.store, "practice_lane", "main") == "main"):
            self.continue_study_path()
        elif self.page == "kana_intro_wait":
            self.show_question()
        else:
            self.continue_learning()

    def learning_preferences(self):
        audio_ready = bool(getattr(getattr(self, "platform", None), "tts_ready", False))
        return {"enabled": audio_ready_kinds(self._saved_learning_kinds(), audio_ready)}

    def _saved_learning_kinds(self):
        """The learner's choice, whether or not this device can speak right now."""
        enabled = stored_enabled_kinds(self.store.get_setting(LEARNING_PREFS, {}))
        return [k for k in KIND_LABELS if k in enabled]

    def learning_items_available(self, *, family=None, views=False):
        """Catalogue, enrolled and retained AI items, resolved; with ``family`` only that family's, in the same order.

        Catalogue rows of other families are then never copied: with a large
        library that copy, not the page, was most of a library render (N8UI).
        ``views``: for a page that only reads (the library list), catalogue
        rows are learning_item_views (their question templates are neither
        copied nor, for an extension library, decoded); deepcopy(row) is the
        plain item, which is what the list hands to its handlers.
        """
        keep = lambda item: family is None or item.get("family") == family  # noqa: E731
        listed = learning_item_views if views else learning_items
        items = {item["id"]: item for item in (listed() if family is None else listed(family))}
        catalogue = set(items)
        for item in self.learning_store.list_items():
            if keep(item):
                items.setdefault(item["id"], item)
        from .ai_materials import AIMaterialStore
        # retained_items() reads only the store; known items would be a full copy of the catalogue for nothing.
        for item in AIMaterialStore(self.store).retained_items():
            if keep(item):
                items.setdefault(item["id"], item)
        from .kana_rule_evidence import teaching_overlay
        from .personal_content_versions import resolve as personal_version, teaching_source
        # Catalogue rows are already reviewed, independent snapshots: copying
        # all of them again on every library open is what made it slow.
        return [personal_version(self.store, teaching_overlay(item), owned=True) if item["id"] in catalogue
                else personal_version(self.store, teaching_source(item)) for item in items.values()]

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
        show_romaji_tool(self, u)

    @page_view()
    def show_learning_library(self, widget=None, *, family="grammar", offset=0, query="", category="全部主题",
                              library=ALL_LIBRARIES, **kwargs):
        from .content_availability import AVAILABLE, state_text
        from .content_library_rules import CORE_LIBRARY_ID
        from .content_library_service import library_titles
        self._library_family = family
        # 08 section 5.1: one availability read for this page (no SQL here).
        availability = self.learning_store.availability()
        titles = library_titles(self.store)

        def library_label(item):
            if item["id"].startswith("local:"):
                return "个人材料"
            provider = availability.catalogue.provider(item["id"])
            if provider is None and availability.available(item["id"]):
                provider = CORE_LIBRARY_ID
            return titles.get(provider, "不可用的库") if provider else "不可用的库"
        # Two columns remain readable at enlarged system font sizes.
        children = [u.row(
            u.button("假名", self.show_kana, flex=1, compact=True),
            u.button("单词", lambda w: self.show_learning_library(family="word"), flex=1, compact=True)),
            u.row(u.button("语法", lambda w: self.show_learning_library(family="grammar"), flex=1, compact=True),
                  u.button("读音规则", lambda w: self.show_learning_library(family="pronunciation"), flex=1, compact=True))]
        all_items = [item for item in self.learning_items_available(family=None if family == "pronunciation" else family,
                                                                    views=True)
                     if (item.get("domain") == "pronunciation" if family == "pronunciation" else
                         item["family"] == family and item.get("domain") != "pronunciation")]
        states = {item["id"]: availability.state_of(item["id"], {"dictionary_only": item.get("dictionary_only") is True})
                  for item in all_items}
        # Library tags/filter appear once more than one library is involved.
        multiple = len(titles) > 1 or any(state != AVAILABLE for state in states.values())
        labels = {item["id"]: library_label(item) for item in all_items} if multiple else {}
        library_names = [ALL_LIBRARIES, *dict.fromkeys(labels.values())]
        library = library if library in library_names else ALL_LIBRARIES
        tracks = [EXAM_COMMON_CATEGORY] if any(item.get("curriculum_track") == EXAM_COMMON_TRACK for item in all_items) else []
        categories = ["全部主题", *tracks, *dict.fromkeys(item.get("category", "基础") for item in all_items)]
        category = category if category in categories else "全部主题"
        self.learning_library_query = u.toga.TextInput(value=query, placeholder="搜索形式、读法或中文说明", style=u.Pack(margin_bottom=6))
        self.learning_library_category = u.selection(items=categories, value=category, style=u.Pack(margin_bottom=8))
        self.learning_library_owner = (u.selection(items=library_names, value=library, style=u.Pack(margin_bottom=8))
                                       if multiple else None)

        def filter_library(widget=None):
            self.show_learning_library(family=family, query=self.learning_library_query.value or "",
                                       category=self.learning_library_category.value,
                                       library=self.learning_library_owner.value if self.learning_library_owner else ALL_LIBRARIES)
        self.learning_library_category.on_change = filter_library
        self.learning_library_query.on_confirm = filter_library
        if self.learning_library_owner is not None:
            self.learning_library_owner.on_change = filter_library
        # Android EditText keeps a large intrinsic minimum width. A sibling
        # button in that row can be pushed completely off-screen, even at 100%.
        children.extend([self.learning_library_query, u.button("搜索", filter_library, compact=True),
                         self.learning_library_category, *([self.learning_library_owner] if multiple else [])])
        if family == "word":
            children.append(u.button("查找全部词库", self.show_words))
        children.append(u.button("例句库", self.show_example_library, compact=True))
        children.append(u.button("AI 学习助手", self.show_ai_center, compact=True))
        needle = query.strip().casefold()
        items = [item for item in all_items if (category == "全部主题" or item.get("category", "基础") == category
                 or category == EXAM_COMMON_CATEGORY and item.get("curriculum_track") == EXAM_COMMON_TRACK)
                 and (not needle or needle in " ".join(str(item.get(k, "")) for k in ("title", "reading", "meaning", "category")).casefold())
                 and (library == ALL_LIBRARIES or labels.get(item["id"]) == library)]
        # Unavailable content is listed after the joinable items, never mixed in.
        unavailable = [item for item in items if states[item["id"]] != AVAILABLE]
        items = [item for item in items if states[item["id"]] == AVAILABLE]
        if category == EXAM_COMMON_CATEGORY:
            # Editorial study priority, not a fabricated corpus frequency. This
            # only orders the library; saved queues and FSRS dates stay intact.
            items.sort(key=lambda item: item.get("priority", 3))
        enrolled_ids = {item["id"] for item in self.learning_store.list_items()}
        children.append(u.label(f"共 {len(items)} 项 · 已加入 {sum(item['id'] in enrolled_ids for item in items)} 项"
                                + (f" · 不可用 {len(unavailable)} 项" if unavailable else ""), size=13, color=u.MUTED))
        next_item = next((item for item in items if item["id"] not in enrolled_ids), None)
        if next_item:
            children.append(u.button("学习下一项", lambda w: self.enroll_learning(deepcopy(next_item)), primary=True))
        offset = max(0, min(offset, ((len(items) - 1) // 12) * 12)) if items else 0
        children[-1]._feedback_fragment = {"kind": "learning_library_page", "family": family,
            "query": query, "category": category, "offset": offset,
            "ordered_ids": [item["id"] for item in items[offset:offset + 12]]}
        for item in items[offset:offset + 12]:
            state = self.learning_store.get_item(item["id"])
            status = "尚未加入" if not state else {"intro": "初次学习", "guide": "跟着做", "bridge": "待独立练习", "check": "学习中", "done": "已加入复习"}.get(state.get("stage"), "学习中")
            entry = u.button(item["title"] + "\n" + item.get("meaning", item.get("instruction", {}).get("summary", "")) + " · " + status
                             + (" · 所属库：" + labels[item["id"]] if multiple else ""),
                lambda w, item=item: self.show_learning_detail(deepcopy(item)), multiline=True)
            # The rows of this render are this page's own objects and the handlers above hand out copies, so the
            # row itself is the render-time snapshot; feedback_context copies fragments when a report is made.
            entry._feedback_fragment = {"kind": "learning_library_entry", "target_id": item["id"],
                "item_snapshot": item, "displayed_status": status}
            if item["family"] == "word" and item["title"]:
                configure_study_ranges(entry, [(0, len(item["title"]))])
            children.append(entry)
        if offset:
            children.append(u.button("上一页", lambda w: self.show_learning_library(family=family, offset=offset - 12, query=query, category=category, library=library)))
        if offset + 12 < len(items):
            children.append(u.button("下一页", lambda w: self.show_learning_library(family=family, offset=offset + 12, query=query, category=category, library=library)))
        if unavailable:
            children.append(u.label(f"暂不可用 {len(unavailable)} 项（学习记录保留，不能加入或练习）", size=13, bold=True))
            for item in unavailable[:50]:
                status = state_text(states[item["id"]])
                entry = u.label(item.get("title", item["id"]) + " · " + status
                                + (" · 所属库：" + labels[item["id"]] if multiple else ""), size=13, color=u.MUTED)
                entry._feedback_fragment = {"kind": "learning_library_entry", "target_id": item["id"],
                                            "displayed_status": status}
                children.append(entry)
        self.screen("learning_library", "学习库", *children)
        self.retention_checks().expose_many([{'id':item['id'],'abilities':['understanding']}
            for item in items[offset:offset + 12]],'summary')

    @page_view(detail=True)
    def show_learning_detail(self, item, *, answer_context=None):
        if isinstance(item, str):
            # The one lesson asked for is copied (deepcopy of a view is the plain lesson), not the whole catalogue.
            item = (self.learning_store.get_item(item)
                    or deepcopy(next((entry for entry in self.learning_items_available(views=True) if entry["id"] == item), None))
                    or get_learning_item(item))
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
        children.extend(origin_labels(item))
        # The same known set as the batch card (design 11 section 5, D3): shared
        # examples whose other references are not yet introduced stay hidden.
        children.extend([teaching_block(u, item, speak=self.speak_text),
                         self.example_cards(item, known_ids=self._intro_batch_known_ids())])
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
        plan = build_dependency_plan([item["id"]], self.learning_items_available(views=True), self.learning_store,
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
        refusal = self.learning_store.availability().refusal(
            item["id"], {"dictionary_only": item.get("dictionary_only") is True})
        if refusal:
            self.alert(refusal)
            return
        if not teaching_allowed(item, store=self.store) and item.get("instruction_status") != "instruction_missing":
            self.alert("材料仍待内容核验；已保留的内容和原有进度不会删除。")
            return
        if item.get("instruction_status") == "instruction_missing":
            self.alert("这个读法与义项的材料尚不完整，暂不加入学习。")
            return
        if item.get("domain") == "pronunciation" and not set(PRONUNCIATION_KINDS) & set(self.learning_preferences()["enabled"]):
            # Existing explicit settings stay authoritative. Enrollment offers
            # a targeted opt-in instead of silently enabling unrelated kinds.
            self.screen("learning_wait", "读音规则题型尚未开启",
                u.label("开启后可学习本课；原有题型选择和未完成练习会保留。"),
                u.button("开启读音规则并学习", lambda w: self.enable_pronunciation_learning(item), primary=True),
                u.button("题型设置", self.show_learning_settings), u.button("稍后再学", self.leave_learning))
            return
        plan = self.learning_store.enroll_with_prerequisites([item], self.learning_items_available(views=True))
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
        enabled = prior if isinstance(prior, list) and all(isinstance(kind, str) for kind in prior) else self._saved_learning_kinds()
        updated["enabled"] = list(dict.fromkeys([*enabled, *PRONUNCIATION_KINDS]))
        self.store.set_setting(LEARNING_PREFS, updated)
        self.enroll_learning(item)

    @page_view(detail=True)
    def open_word_learning(self, word):
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

    def _queue_learning_questions(self, item, exposure_tables=None, *, early_ability=None):
        """``exposure_tables``: learning_store.exposure_tables() of a pass over several lessons.

        ``early_ability``: the ability of ``item`` an early answer of extra practice just rated (design 12
        K13). Inside a sealed round only that unit's missing question is queued (it keeps the unit among the
        states extra practice reads; the sealed round's frozen order does not serve it before the next
        round); outside one the call is unchanged.
        """
        from .practice_round import sealed
        inside = sealed(self.store)
        if inside and early_ability is None:
            return
        for state, plan in self._learning_question_plans(item):
            if inside and plan.get("ability") != early_ability:
                continue
            if "answer" in plan:
                q = plan
            else:
                q = build_learning_question(item, plan["kind"], variant=plan["variant"])
                q["id"] = plan["id"]
            if exposure_tables is None:
                self.learning_store.append_questions([q])
            else:
                self.learning_store.append_questions([q], exposure_tables=exposure_tables)

    def _replace_withdrawn_questions(self, question_id=None):
        """Exchange queued tasks of a form the app no longer asks (2026-11-12: hear a word, pick its kana
        spelling) for current tasks; returns how many were exchanged.

        Each unanswered one goes through the replacement record, exactly as a blocked task does: the old
        row is kept with a receipt and the new question takes its place and its earliest time. One that
        cannot be exchanged now (no eligible task yet, an open check, a waiting prerequisite) stays queued
        and is tried again at the next pass.

        Without question_id: the queue pass of an unsealed round, bounded by WITHDRAWN_PASS_SECONDS (a
        sealed round queues nothing, so the pass does nothing there). With question_id: that one task,
        which is about to be shown — also inside a sealed round, like the replacement of a blocked task.
        """
        if question_id is None:
            from .practice_round import sealed
            if sealed(self.store):
                return 0
            question_ids = self.learning_store.withdrawn_pending_ids()
        else:
            question_ids = [question_id]
        if not question_ids:
            return 0
        from time import monotonic
        from .learning_replacement import LearningReplacementStore
        service = LearningReplacementStore(self.learning_store)
        deadline, done = monotonic() + WITHDRAWN_PASS_SECONDS, 0
        for current in question_ids:
            try:
                service.commit(service.prepare_question(current, withdrawn=True))
                done += 1
            except (ValueError, KeyError):
                pass
            if monotonic() >= deadline:
                break
        return done

    def _queue_missing_learning_questions(self):
        """Queue a question for every lesson direction that has none; the exposure records are read once."""
        self._replace_withdrawn_questions()
        tables = None
        for item in self.learning_store.items_needing_questions():
            if tables is None:
                tables = self.learning_store.exposure_tables()
            self._queue_learning_questions(item, tables)

    @page_view()
    def show_learning_settings(self, widget=None, **kwargs):
        enabled = set(self._saved_learning_kinds())
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
        from .intro_batch import SIZE_RANGES
        sizes = self.learning_store.intro_batch_sizes()
        def size_picker(family):
            low, high = SIZE_RANGES[family]
            return u.selection(items=[str(n) for n in range(low, high + 1)], value=str(sizes[family]),
                               on_change=lambda w: self.change_intro_batch_sizes())
        # Applies to the next batch; an open batch keeps its saved entries.
        self.intro_batch_word_size = size_picker("word")
        self.intro_batch_grammar_size = size_picker("grammar")
        self.screen("learning_settings", "学习安排", self.heading("学习题型"), *controls,
            self.learning_system_input,
            u.label("每批新词汇数", bold=True), self.intro_batch_word_size,
            u.label("每批新语法数", bold=True), self.intro_batch_grammar_size,
            u.button("继续学习", self.continue_added_learning, primary=True),
            u.button("加入新内容", self.show_study_path_add),
            u.button("假名练习设置", self.show_practice_setup),
            u.button(STUDY_ORIENTATION_TITLE, lambda w: self.show_study_orientation(w)))

    @page_view()
    def show_study_orientation(self, widget=None, *, then=None, **kwargs):
        """The 学习取向 screen (design 12 UI1, D1, D2); ``then``: what to do after 确定 (default: back).

        Models from the presenter (with the focus part, UI6), page from
        arrangement/ui; this method only hands the widgets to screen().
        Going back writes nothing.
        """
        from .arrangement.ui.orientation_page import orientation_page
        from .domain import _utc
        from .study_path_runtime import StudyPathRuntimeMixin
        presenter = StudyPathRuntimeMixin.study_arrangement(self).presenter
        model = presenter.orientation_model(_utc(None))
        self.screen("study_orientation", STUDY_ORIENTATION_TITLE, *orientation_page(
            model, focus=presenter.focus_model(),
            on_confirm=lambda orientation, depth, focus: self._confirm_study_orientation(
                orientation, depth, then, focus=focus)))

    def _confirm_study_orientation(self, orientation, depth, then, *, focus=None):
        """确定 on the orientation screen: the depth (unless kept), the focus, the orientation, then ``then`` or back.

        The focus goes through the focus service's change(), which writes and
        rearranges the current round once only when it differs from the
        stored one. The orientation is written last, so an interrupted
        confirm leaves it never chosen. The applied practice preferences are
        adopted as a backup restore reloads them.
        """
        if self.page != "study_orientation":
            return
        from .study_path_runtime import StudyPathRuntimeMixin
        arrangement = StudyPathRuntimeMixin.study_arrangement(self)
        preferences = arrangement.preferences
        if depth is not None:
            self._practice_preferences = preferences.apply_kana_depth(depth)
        if focus is not None:
            arrangement.focus.change(focus)
        preferences.set_orientation(orientation)
        if then is not None:
            then()
            return
        retained = self.back_stack.pages[-1] if self.back_stack.pages else None
        if retained is not None and retained.name == "practice_setup":
            # The kept practice-setup page shows the old switches and would save
            # them again on return: open it afresh in its place instead.
            self.back_stack.pop()
            self._view_key = None
            self.show_practice_setup()
            return
        self.go_back()

    def change_intro_batch_sizes(self):
        word, grammar = self.intro_batch_word_size.value, self.intro_batch_grammar_size.value
        if word is None or grammar is None:
            return
        self.learning_store.set_intro_batch_sizes({"version": 1, "word": int(word), "grammar": int(grammar)})

    def change_learning_kinds(self):
        enabled = [kind for title, kinds in KIND_GROUPS.items() if self.learning_group_switches[title].value for kind in sorted(kinds)]
        self.store.set_setting(LEARNING_PREFS, {"enabled": enabled, SOUND_PREFERENCE_VERSION: PREFERENCE_VERSION})

    @page_view()
    def show_settings_hub(self, widget=None, **kwargs):
        from .build_info import VERSION, BUILD_NUMBER
        self.screen("settings_hub", "设置",
            u.button("练习方式", self.show_learning_settings), u.button("显示", self.show_prompt_settings),
            u.button("语音与手写", self.show_settings),
            u.button("学习数据", self.show_learning_data),
            u.button("内容库", self.show_content_libraries),
            u.button("反馈记录", self.show_feedback_manager),
            u.button("AI 服务", self.show_ai_service_settings),
            u.button("关于与资料", self.show_notices),
            u.label("本地学习可离线使用。", size=12, color=u.MUTED),
            u.label(f"版本 {VERSION}（build {BUILD_NUMBER}）", size=12, color=u.MUTED))

    @page_view()
    def show_learning_data(self, widget=None, **kwargs):
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
        body = u.col()
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
                teaching.set_head(self, None)
                self._learning_guide_context = {}
                if guided:
                    self.show_learning_instruction(self.learning_store.instruction_item(current['id'], current.get('instruction_scope')))
                else:
                    self.continue_learning()
            return [u.button('使用更新后的练习继续', apply, primary=True)]
        return []

    @page_view(replace=True)
    def continue_learning(self, widget=None, **kwargs):
        # The decision returns the next course step and draws nothing; perform makes its one call. Both run
        # here, inside this entry, so the navigation window and the continue action's read context hold.
        from . import course_runtime
        return course_runtime.perform_course(self, course_runtime.decide_course(
            self, restart_finished=widget is not None, selected_current=kwargs.get("_selected_current"),
            projection=kwargs.get("_projection")))

    def learning_next_content_controls(self):
        # Standalone course controllers can still be embedded without the
        # route service; the native app always supplies this service.
        if not study_path_available(self) or not callable(getattr(self, "study_path_service", None)):
            return [u.button("加入新内容", self.show_learning_library, primary=True)]
        # Design 10 section 8.2: the current route's next section, as on the
        # continue finished page; the core route keeps its pre-route call.
        from .study_path_runtime import current_route_next
        from .study_path_display import other_route_rows, route_kwargs
        service = self.study_path_service()
        overview, *current = current_route_next(service)
        route = route_kwargs(current[0] if current else None)
        controls = list(self.intro_batch_review_hint())
        available = getattr(self, "study_path_can_continue", None)
        can_continue = callable(available) and available()
        if can_continue:
            controls.append(u.button("继续已加入的学习", self.continue_study_path, primary=True))
        if overview.get("next_section"):
            controls.append(u.button("继续下一节",
                lambda w, identity=overview["next_section"]["id"]: self._add_study_sections((identity,), **route),
                primary=not can_continue))
            controls.append(u.label(overview["next_section"]["title"], size=13, color=u.MUTED))
        else:
            others = [row for row in other_route_rows(service, route.get("route_id")) if row.get("next_section")][:3]
            if others:
                controls.append(u.label("其他路线的下一小节", size=13, bold=True))
            for row in others:
                controls.append(u.button(row["title"] + " · " + row["next_section"]["title"],
                    lambda w, sid=row["next_section"]["id"], rid=row["route_id"]:
                        self._add_study_sections((sid,), **route_kwargs(rid)), multiline=True))
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
        return teaching.show_familiarity(self, item)

    def _learning_guide_controls(self, question, draft, known_ids=None):
        """Use the independent task's controls while keeping all work exposure-only.

        The question and each edit are checkpointed before UI state changes.
        Rebuilding or revisiting a guide must not select another demonstration.
        known_ids adds display-only kana to the options (option_labels: all of
        them or none); drafts and answers keep the raw options.
        """
        context = {"question": question, "draft": deepcopy(draft or {}),
                   "introduction_id": teaching.item(self).get("introduction_id"),
                   "buttons": [], "locked": False}
        self._learning_guide_context = context
        mode = question.get("input_mode", "choice")
        labels = option_labels(question, known_ids) if mode in {"choice", "reorder"} else {}
        status = u.label("", size=12, color=u.MUTED)
        context["status"] = status

        def active():
            return (self.page == "learning_guide" and not context["locked"]
                    and getattr(self, "_learning_guide_context", None) is context
                    and teaching.guide(self) is question
                    and teaching.item(self).get("introduction_id") == context["introduction_id"])

        def render():
            value = context["draft"]
            if mode == "reorder":
                text = "".join(labels.get(part, part) for part in value.get("parts", []))
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
                teaching.service(self).checkpoint_guide(teaching.item(self), {
                    "guide_question": deepcopy(question), "guide_draft": deepcopy(value),
                    "guide_answer": None, "guide_correct": False})
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
                course_answer_runtime.service(self).prompt_preferences(), known_ids=known_ids)

            def submit_cloze_guide(answer):
                self.answer_learning_guide(answer, draft=context['draft'], question_id=question['id'])
                return True if context['locked'] else False if context['status'].text else None

            controls = [cloze_controls(self, u, question, context['draft'], save,
                submit_cloze_guide, active, prompt=context['prompt'], known_ids=known_ids)]
        elif mode == "choice":
            controls = []
            for choice in question.get("choices", []):
                def choose(widget=None, answer=choice):
                    if active():
                        self.answer_learning_guide(answer, draft={**context["draft"], "answer": answer}, question_id=question["id"])
                controls.append(u.button(labels.get(choice, choice), choose, multiline=True,
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
                context["choices"].append(u.button(labels.get(choice, choice), append, multiline=True, text_role="study"))

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
            from .answer_input import available
            context["input"], controls = answer_controls(u, question,
                context["draft"].get("answer", ""), changed, active,
                system_japanese=course_answer_runtime.service(self).system_japanese())
            context["submit"] = u.button("提交", submit, primary=True)
            controls.append(context["submit"])
            context["buttons"] = [context["input"], context["submit"]]
            if not available(question, system_japanese=course_answer_runtime.service(self).system_japanese()):
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

    def _learning_guide_demonstration(self, question, checkpoint):
        """Fade the second example's model without turning teaching into SRS."""
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
                    or teaching.guide(self) is not question):
                return
            value = {**context.get("draft", {}), "demonstration_open": True}
            try:
                teaching.service(self).checkpoint_guide(teaching.item(self), {
                    "guide_question": deepcopy(question), "guide_draft": value})
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
        teaching.perform_teaching(self, teaching.decide_teaching(self, item))

    def _new_guide_result(self):
        """A new, empty column for the result of a guide answer."""
        return u.col()

    def _draw_learning_guide(self, step):
        """The guide page (跟着做) of a teaching step: the frozen question with its controls, the demonstration
        of a practice guide and the result column; the saved result when the question was already answered."""
        item, q, checkpoint, known, shared_intro = step.item, step.question, step.checkpoint, step.known, step.shared_intro
        practice = q.get("practice")
        # known (read above for the guided sequence) also decides the kana of unlearned words.
        labels = option_labels(q, known)
        choices = ([self.learning_pitch_controls(q, checkpoint.get("guide_draft") or {}, guided=True)]
            if is_pitch_question(q) else
            self._learning_guide_controls(q, checkpoint.get("guide_draft") or {}, known) if practice or q.get('input_mode') == 'multi_cloze' else
            [u.button(labels.get(choice, choice), lambda w, answer=choice, expected=q: self.answer_learning_guide(answer, expected_question=expected),
                      multiline=True, text_role=learning_text_role(q, "choice")) for choice in q.get("choices", [])])
        task = instruction_widgets(u, q, known)
        if is_pitch_question(q):
            task = [u.label(q["task"].replace("并分开助词", ""), size=17)]
        stimulus = []
        if q.get('audio_text'):
            def play_guide(widget=None):
                if self.page == 'learning_guide' and teaching.guide(self) is q:
                    self.speak_text(q['audio_text'])
            speaker = u.speaker_button(play_guide, '播放引导题目')
            speaker._feedback_fragment = {'kind': 'guide_audio', 'question_id': q['id']}
            stimulus.append(speaker)
        reference = self._shared_learning_guide_reference(item, q) if shared_intro else []
        guide_prompt = (self._learning_guide_context['prompt'] if q.get('input_mode') == 'multi_cloze'
                        else self._learning_prompt_block(q, size=20, known_ids=known))
        if practice:
            demonstration = self._learning_guide_demonstration(q, checkpoint)
            self.screen("learning_guide", "跟着做", *stimulus, *([guide_prompt] if guide_prompt is not None else []),
                *task,
                *self._learning_context_support(q, known),
                demonstration, *choices, self._learning_guide_result, *reference, *self._learning_guide_archive(checkpoint))
        else:
            self.screen("learning_guide", "跟着做",
                *([] if shared_intro else [teaching_block(u, item, compact=True)]), *task,
                *stimulus,
                *([] if is_pitch_question(q) else [guide_prompt]),
                *reference, *choices, self._learning_guide_result, *self._learning_guide_archive(checkpoint))
        if checkpoint.get("guide_answer") is not None:
            self.render_learning_guide_result(bool(checkpoint.get("guide_correct")))

    def _draw_learning_intro(self, step):
        """The introduction page (初次学习 / 快速回顾) of a teaching step."""
        item, familiarity, enrolled = step.item, step.familiarity, step.enrolled
        instruction = item.get("instruction", {})
        compact = bool(familiarity and familiarity.get("route") == "compact"
                       and familiarity.get("diagnostic_state") != "needs_teaching")
        children = [u.label(item["title"], size=25, bold=True, color=u.GREEN,
                            text_role="study" if item["family"] == "word" else "ui")]
        if item.get("reading"):
            children.append(u.row(u.label(self.learning_reading_label(item), size=18, flex=1, text_role="study"),
                *([] if item.get('personal_reading', {}).get('pending') else [u.speaker_button(lambda w: self.speak_text(item["reading"]))])))
        children.extend(origin_labels(item))
        admitted = item.get('enrolled_abilities', [])
        focused_reading = item['family'] == 'word' and admitted == ['reading']
        conceptual = teaching.service(self).understanding_unit(item) if enrolled else None
        practiced_explanation = bool(conceptual and conceptual.get('instruction_ready'))
        focused_application = admitted in (['use'], ['application'], ['listening']) and practiced_explanation
        focused = focused_reading or focused_application
        if not focused:
            children.append(teaching_block(u, item, compact=compact, speak=self.speak_text))
        from .learning_questions import practice_templates
        has_practice = any(raw.get("practice") for variants in (practice_templates(item) or {}).values() for raw in variants)
        children.append(u.button("跟着练习" if has_practice else "跟着做一题", self.start_learning_guide, primary=True))
        if not compact or focused:
            children.append(self.example_cards(item, known_ids=self._intro_batch_known_ids()))
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
        body = u.col()
        def toggle(widget):
            if self.page != 'learning_guide' or teaching.guide(self) is not question:
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
        event = teaching.service(self).guide_archive(checkpoint)
        if not event:
            return []
        old = event['before']
        return [self._learning_archive_control(old['guide_question'], old.get('guide_draft', {}))]

    def show_learning_prerequisite(self, widget=None, **kwargs):
        value = teaching.item(self)["instruction"].get("prerequisite", "")
        text = "\n".join(str(v) for v in value.values()) if isinstance(value, dict) else str(value)
        self.screen("learning_prerequisite", "接续说明",
            block_widget(u, {"kind": "points", "label": "先掌握", "items": [part for part in text.split("\n") if part]}),
            u.button("返回讲解", lambda w: self.show_learning_instruction(teaching.item(self)), primary=True))

    def start_learning_guide(self, widget=None, **kwargs):
        # A review can become due while the introduction is being read. Finish
        # the guide the user explicitly opened before interleaving other work.
        self.show_learning_instruction(teaching.service(self).start_guide(teaching.item(self)))

    def answer_learning_guide(self, answer, *, draft=None, question_id=None, expected_question=None):
        guide = teaching.guide(self)
        if (self.page != "learning_guide" or (question_id is not None and guide["id"] != question_id)
                or expected_question is not None and guide is not expected_question):
            return
        from .answer_input_ui import prepare_submission
        ready, answer = prepare_submission(guide, answer,
            getattr(self, "_learning_guide_context", {}).get("status"),
            field=getattr(self, "_learning_guide_context", {}).get("input"))
        if not ready:
            return
        answered = teaching.service(self).answer_guide(teaching.item(self), guide, answer, draft)
        context = getattr(self, "_learning_guide_context", {})
        if isinstance(answered, GuideEmpty):
            if context.get("question") is guide:
                context["status"].text = answered.message
            return
        if isinstance(answered, GuideSaveFailed):
            if context.get("question") is guide:
                context["status"].text = "未能保存本次作答，请重试。"
            return
        if context.get("question") is guide and answered.draft is not None:
            context["draft"] = deepcopy(answered.draft)
        self.render_learning_guide_result(answered.correct)

    def render_learning_guide_result(self, correct):
        from .answer_feedback import build_answer_feedback
        self._learning_guide_result.clear()
        self._learning_guide_result.add(build_answer_feedback({"correct": correct}))
        if is_pitch_question(teaching.guide(self)):
            context = getattr(self, "_learning_pitch_context", {})
            if not correct:
                self._learning_guide_result.add(answer_diagram(self, teaching.guide(self), reveal=True))
                entered = context.get("draft", {}).get("answer")
                if not entered:
                    entered = teaching.service(self).saved_guide_answer(teaching.item(self))
                diagnosis = grade_learning_answer(teaching.guide(self), entered).get("message", "")
                if diagnosis and diagnosis != teaching.guide(self).get("feedback", ""):
                    self._learning_guide_result.add(u.label(diagnosis, size=13, color=u.MUTED))
            self._learning_guide_result.add(u.label(teaching.guide(self).get("feedback", "")))
            if correct:
                if context.get("question") is teaching.guide(self):
                    context["locked"] = True
                    for control in [*context["buttons"].values(), context["submit"]]:
                        control.enabled = False
                self._learning_guide_result.add(u.button("继续",
                    lambda w, expected=teaching.guide(self): self.finish_learning_guide(expected_question=expected), primary=True))
            else:
                self._learning_guide_result.add(*self._learning_guide_pass_controls())
            self._reveal_learning_guide_result()
            return
        speaker = self._learning_answer_speaker(teaching.guide(self), guided=True)
        if correct:
            context = getattr(self, "_learning_guide_context", {})
            if context.get("question") is teaching.guide(self):
                context["locked"] = True
                for control in context["buttons"]:
                    control.enabled = False
            # Result of a guided question: its Japanese carries kana like every result (design 11 §7 rule 9).
            answer = u.label(shown_explanation(teaching.guide(self), teaching.guide(self).get("feedback", "")),
                             flex=1 if speaker else 0)
            self._learning_guide_result.add(u.row(answer, speaker) if speaker else answer,
                u.button("继续", lambda w, expected=teaching.guide(self):
                    self.finish_learning_guide(expected_question=expected), primary=True))
        else:
            answer = u.label(shown_answer(teaching.guide(self)), bold=True, flex=1 if speaker else 0,
                text_role=learning_text_role(teaching.guide(self), "answer"))
            self._learning_guide_result.add(u.row(answer, speaker) if speaker else answer,
                u.label(shown_explanation(teaching.guide(self), teaching.guide(self).get("feedback", ""))),
                *self._learning_guide_pass_controls())
        # The result is already attached to the visible guide. Its introduction
        # identity survives rebuilding this page, so returning cannot replay it.
        if self.page == "learning_guide":
            autoplay_answer(self, self._learning_answer_audio_text(teaching.guide(self)),
                ("learning-guide", teaching.item(self).get("introduction_id") or teaching.item(self)["id"],
                 teaching.guide(self)["id"]))
        if teaching.guide(self).get("practice"):
            self._reveal_learning_guide_result()

    def _learning_guide_pass_caption(self):
        """Design 12 Q1: the "seen, go on" caption after a recorded wrong answer, or None."""
        return teaching.service(self).pass_caption(teaching.item(self), teaching.guide(self))

    def _learning_guide_pass_controls(self):
        caption = self._learning_guide_pass_caption()
        if caption is None:
            return []
        return [u.button(caption, lambda w, expected=teaching.guide(self): self.pass_learning_guide(expected_question=expected))]

    def pass_learning_guide(self, widget=None, *, expected_question=None, **kwargs):
        """Go on after a wrong guide answer, as a correct one does, with the 'bridge' checkpoint."""
        if (self.page != "learning_guide"
                or expected_question is not None and teaching.guide(self) is not expected_question):
            return
        if self._learning_guide_pass_caption() is None:
            # The answer or the orientation changed since the result was drawn.
            self.show_learning_instruction(teaching.service(self).lesson(teaching.item(self)))
            return
        self.finish_learning_guide(expected_question=expected_question, passed=True)

    def finish_learning_guide(self, widget=None, *, question_id=None, expected_question=None, passed=False, **kwargs):
        guide = teaching.guide(self)
        if (self.page != "learning_guide" or (question_id is not None and guide["id"] != question_id)
                or expected_question is not None and guide is not expected_question):
            return
        finished = teaching.service(self).finish_guide(teaching.item(self), guide, passed)
        if isinstance(finished, GuideNext):
            self.show_learning_instruction(finished.item)
            return
        if study_path_available(self):
            self.continue_study_path(_checkpoint_saved=True)
        else:
            self.continue_learning()

    def show_learning_question(self, current):
        if not course_answer_runtime.open_question(self, current):
            return
        answering = course_answer_runtime.service(self)
        q, state = answer_session(self).question, answer_session(self).state
        parts = state.get("parts", [])
        # Secondary help follows the answering area: hint, card, AI, and
        # "show answer" last, so active recall starts from the task itself.
        actions = [u.button("再看讲解", self.learning_hint, compact=True, font_size=12, flex=1)]
        batch_hints, batch_actions = self.intro_batch_question_help(current)
        actions.extend(batch_actions)
        actions.append(u.button("问 AI", lambda w, qid=q["id"]: self.open_tutor_question(
            source="learning", question_id=qid), compact=True, font_size=12, flex=1))
        actions.append(u.button("显示答案", self.reveal_learning_answer, compact=True, font_size=12, flex=1))
        prompt_speaker = ([u.speaker_button(self.play_learning_audio, "朗读题干")]
                          if prompt_audio_text(q) else [])
        # One read per question: unmet-word glosses and the kana of unlearned words share it.
        known = answering.known_ids()
        task = ([u.label(q["task"].replace("并分开助词", ""), size=13, color=u.MUTED)] if is_pitch_question(q)
                else instruction_widgets(u, q, known))
        children = []
        prompt = None
        if q.get("audio_text"):
            children.append(u.speaker_button(self.play_learning_audio, "播放题目"))
        elif not is_pitch_question(q):
            if q.get('input_mode') == 'multi_cloze':
                from .multi_cloze_ui import prompt_view
                prompt = prompt_view(self, u, q, state, answering.prompt_preferences(), known_ids=known)
            else:
                prompt = self._learning_prompt_block(q, answering.prompt_preferences(), size=20, known_ids=known)
            children.append(prompt)
        # The submit path records the plan of the prompt this page drew (none: the plain plan as before).
        course_answer_runtime.assign(self, prompt_known=(q, known if prompt is not None else None))
        # The Japanese utterance or listening stimulus owns the first position.
        # Keep indispensable context and one short response cue above the
        # answer controls; the help row is appended after them (see below).
        children.append(u.col(*batch_hints, *task, *([u.row(*prompt_speaker)] if prompt_speaker else [])))
        if q.get("gloss"):
            children.append(u.label(q["gloss"], size=12, color=u.MUTED))
        children.extend(self._learning_context_support(q, known))
        mode = q.get("input_mode", "choice")
        # Display only: buttons may carry kana (all options or none), callbacks and drafts keep the raw option.
        labels = option_labels(q, known) if mode in {"choice", "reorder"} and not is_pitch_question(q) else {}
        if is_pitch_question(q):
            children.append(self.learning_pitch_controls(q, state))
        elif mode == 'multi_cloze':
            from .multi_cloze_ui import controls as cloze_controls

            def save_cloze(updated):
                course_answer_runtime.service(self).save_draft(q['id'], updated)
                answer_session(self).state.update(deepcopy(updated))

            def submit_cloze(answer):
                self.submit_learning_answer(answer, question_id=q['id'])
                return self.page != 'learning_question' or answer_session(self).question is not q

            children.append(cloze_controls(self, u, q, state, save_cloze,
                submit_cloze,
                lambda: self.page == 'learning_question' and answer_session(self).question is q, prompt=prompt,
                known_ids=known))
        elif mode == "choice":
            choices = q.get("choices", [])
            children.extend(u.button(labels.get(choice, choice), lambda w, answer=choice, qid=q["id"]: self.submit_learning_answer(answer, question_id=qid), multiline=True,
                                     text_role=learning_text_role(q, "choice")) for choice in choices)
        elif mode == "reorder":
            self._learning_composition = u.label("".join(labels.get(part, part) for part in parts) or "点击词块组成句子", size=20,
                                                 text_role="study" if parts else "ui")
            children.append(self._learning_composition)
            # Duplicate blocks are identified by position, not just text.
            used = list(state.get("used", []))
            course_answer_runtime.assign(self, used=used)
            context = {"question": q, "buttons": [], "labels": labels}
            self._learning_blocks_context = context
            for index, choice in enumerate(q.get("choices", [])):
                b = u.button(labels.get(choice, choice), lambda w, i=index, question=q: self.add_learning_part(i, expected_question=question), multiline=True, text_role="study")
                b.enabled = index not in used
                context["buttons"].append(b)
                children.append(b)
            context["undo"] = u.button("撤回末项", lambda w, question=q: self.delete_learning_part(expected_question=question))
            context["undo"].enabled = bool(used)
            context["submit"] = u.button("提交", lambda w, question=q: self.submit_learning_answer(
                "".join(answer_session(self).parts), question_id=question["id"]), primary=True)
            children.extend([context["undo"], context["submit"]])
        else:
            from .answer_input_ui import answer_controls
            from .answer_input import available
            system_input = answering.system_japanese()
            self._learning_input, controls = answer_controls(u, q, answer_session(self).answer,
                self.save_learning_input, lambda: self.page == "learning_question"
                and answer_session(self).question is q, system_japanese=system_input)
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
        # Its own row below Submit and the status line, never beside Submit,
        # so "show answer" cannot be mistaken for submitting a draft.
        children.append(u.row(*actions))
        old = answering.replaced(q)
        if old:
            children.append(self._learning_archive_control(old, old['draft']))
        self.screen("learning_question", KIND_LABELS.get(q["kind"], "练习"), *children)
        # The learner has seen the question once its page is drawn (never before).
        answering.presented(answer_session(self))

    def save_learning_input(self, widget=None, **kwargs):
        """Keep the in-memory draft current; persist it at most 0.25 s later.

        The same draft is written as before (save_draft), only coalesced.
        flush_learning_draft forces it before submit/reveal/hint, navigation,
        exit and backgrounding.
        """
        if self.page != "learning_question":
            return
        session = answer_session(self)
        session.state["answer"] = self._learning_input.value
        draft = self._learning_draft_debouncer()
        if draft is None:
            return self._persist_learning_draft(session.question["id"], session.state)
        draft.accept(session.question["id"], deepcopy(session.state))

    def _learning_draft_debouncer(self):
        draft = getattr(self, "_learning_draft", None)
        if draft is None:
            loop = getattr(self, "loop", None)
            if loop is None or not callable(getattr(loop, "call_later", None)):
                return None  # No event loop: persist synchronously.
            from .draft_debounce import DraftDebouncer, loop_scheduler
            draft = self._learning_draft = DraftDebouncer(loop_scheduler(loop), self.flush_learning_draft)
        return draft

    def flush_learning_draft(self):
        """Synchronously persist a pending course-input draft.

        None: nothing pending; False: the write failed and a status says so.
        """
        draft = getattr(self, "_learning_draft", None)
        pending = draft.flush_now() if draft is not None else None
        if pending is None:
            return None
        question_id, snapshot = pending
        current = answer_session(self).question or {}
        if self.page == "learning_question" and current.get("id") == question_id:
            # Live state also carries flags saved by other paths (audio_ok...).
            state = answer_session(self).state
        else:
            state = snapshot  # The last on-screen draft of a page already left.
        return self._persist_learning_draft(question_id, state)

    def _persist_learning_draft(self, question_id, state):
        from .draft_debounce import persist_with_lock_notice
        from .diagnostic_events import record_error

        def busy():
            status = getattr(self, "_learning_status", None)
            if status is not None and self.page == "learning_question":
                status.text = LEARNING_BUSY_STATUS
        # A lock timeout requeues this draft, so a later forced flush (leave,
        # background, exit) still writes it even without another keystroke.
        saved = persist_with_lock_notice(lambda: course_answer_runtime.service(self).save_draft(question_id, state),
            record=lambda exc: record_error("learning_draft", "save", exc),
            debouncer=getattr(self, "_learning_draft", None), key=question_id, value=deepcopy(state),
            notice=busy)
        if saved is False:
            return False
        status = getattr(self, "_learning_status", None)
        if status is not None and getattr(status, "text", None) == LEARNING_BUSY_STATUS:
            status.text = ""
        return saved

    def skip_unavailable_learning_input(self, question_id):
        session = answer_session(self)
        if self.page != "learning_question" or session.question["id"] != question_id:
            return
        # Explicitly release the old pending slot without revealing its answer,
        # rewriting the snapshot/draft, or awarding a success/failure rating
        # (nothing happens when this device can take the answer after all).
        if course_answer_runtime.service(self).skip(session):
            self.continue_added_learning()

    def _edit_learning_parts(self, index, expected_question=None):
        session = answer_session(self)
        question = session.question
        if (self.page != "learning_question" or question.get("input_mode") != "reorder"
                or expected_question is not None and question is not expected_question):
            return
        context = getattr(self, "_learning_blocks_context", {})
        if context.get("question") is not question:
            return
        edited = course_answer_runtime.service(self).edit_parts(session, index)
        if isinstance(edited, EditKept):
            return
        if isinstance(edited, EditFailed):
            self._learning_status.text = "输入未保存，请重试。"
            return
        if isinstance(edited, EditLeft):
            self.continue_learning()
            return
        parts, used = list(edited.draft.get("parts", [])), list(edited.draft.get("used", []))
        course_answer_runtime.assign(self, state=edited.draft, parts=parts, used=used)
        labels = context.get("labels", {})
        self._learning_composition.text = "".join(labels.get(part, part) for part in parts) or "点击词块组成句子"
        configure_text_role(self._learning_composition, "study" if parts else "ui")
        for position, control in enumerate(context["buttons"]):
            control.enabled = position not in used
        context["undo"].enabled = bool(used)
        self._learning_status.text = ""

    def add_learning_part(self, index, *, expected_question=None):
        self._edit_learning_parts(index, expected_question)

    def delete_learning_part(self, widget=None, *, expected_question=None, **kwargs):
        self._edit_learning_parts(None, expected_question)

    def play_learning_audio(self, widget=None, **kwargs):
        q = answer_session(self).question
        if self.page != "learning_question":
            return
        text = (q.get("audio_text") if q.get("kind") in LISTENING_KINDS else None) or prompt_audio_text(q)
        if not text:
            return
        token = self._page_token
        async def play():
            from .diagnostic_events import record_error
            if token != self._page_token or self.page != "learning_question" or answer_session(self).question["id"] != q["id"]:
                return
            try:
                await self.platform.play(text)
            except Exception as exc:
                record_error("learning_audio", "play", exc)
                if token == self._page_token:
                    self._learning_status.text = "播放失败，请检查日语语音后重试；本题尚未评分。"
                return
            if token == self._page_token:
                try:
                    course_answer_runtime.audio_played(self, q)
                except Exception as exc:
                    record_error("learning_audio", "save", exc)
                    self._learning_status.text = "语音已播放，但播放状态未保存。请重试；本题尚未评分。"
                    return
        from .draft_debounce import create_task_awake
        create_task_awake(self.loop, play())  # Started from a native button callback.

    def _learning_instruction_example(self, instruction):
        text = instruction.get("example", "")
        role = instruction.get("example_text_role", "study")
        if role == "study":
            return self.annotated_sentence(text, size=20, audio=True)
        return u.label(text, size=20, text_role=role)

    def learning_hint(self, widget=None, *, card=False, **kwargs):
        """Teaching reference during a question; opening it marks the draft assisted.

        card=True shows the batch 说明卡 body instead (四之二 item 6); the
        assisted rule is the same.
        """
        item = course_answer_runtime.service(self).hint_item(answer_session(self))
        if self._unverified_learning_reference(item, "返回题目"):
            return
        course_answer_runtime.assist(self)
        if card:
            self.screen("learning_prerequisite", "说明卡", *self._intro_card_body(item),
                u.button("返回题目", self.continue_learning, primary=True))
            return
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
        missing = item.get("instruction_status") == "instruction_missing"
        self.screen(page, "学习材料尚不完整" if missing else "材料待核验",
            u.label("学习材料尚不完整。" if missing else "材料尚待核验，仅可预览草稿。", size=15),
            u.button("查看材料" if missing else "预览草稿", lambda w: self.show_learning_detail(item)),
            u.button(return_caption, on_return or self.continue_learning, primary=True))
        return True

    def reveal_learning_answer(self, widget=None, **kwargs):
        course_answer_runtime.assist(self)
        self.submit_learning_answer(answer_session(self).state.get("answer", ""), revealed=True)

    def submit_learning_answer(self, answer, *, revealed=False, question_id=None):
        if self.page != "learning_question" or (question_id is not None and question_id != answer_session(self).question["id"]):
            return
        q = answer_session(self).question
        # The typed draft reaches storage before the answer is graded.
        LearningUIMixin.flush_learning_draft(self)
        from .answer_input_ui import prepare_submission
        ready, answer = prepare_submission(q, answer, self._learning_status, revealed=revealed,
            field=getattr(self, "_learning_input", None))
        if not ready:
            return
        refusal = course_answer_runtime.submit(self, answer, revealed=revealed)
        if refusal is not None:
            self._learning_status.text = refusal

    def _reveal_learning_guide_result(self):
        """Bring new inline feedback into view after native layout settles."""
        scroll = getattr(self, "_page_scroll", None)
        result = self._learning_guide_result
        question = teaching.guide(self)
        if scroll is None or not hasattr(result, "layout"):
            return

        async def reveal():
            await asyncio.sleep(.05)
            if (self.page != "learning_guide" or teaching.guide(self) is not question
                    or self._learning_guide_result is not result
                    or getattr(self, "_page_scroll", None) is not scroll):
                return
            # Use content coordinates, not a saved pixel offset: large fonts
            # and wrapping change the result's position. The scroll container
            # clamps the requested position to its current native bounds.
            top = result.layout.absolute_content_top - scroll.content.layout.absolute_content_top
            scroll.vertical_position = max(0, int(top) - 8)

        from .draft_debounce import create_task_awake
        create_task_awake(self.loop, reveal())  # Started from a native button callback.

    def show_learning_feedback(self, result):
        course_answer_runtime.show_feedback(self, result)

    def _learning_answer_control(self, model, speaker):
        """The answer of a result page as a control: the right spelling against the entered one, the learner's
        accepted pitch contour, the pitch diagram revealed, or the answer as text (which makes room for the
        speaker beside it)."""
        q = model.question
        if model.answer_kind == ANSWER_COMPARISON:
            from .spelling_display import build_spelling_comparison
            return build_spelling_comparison(self, q["answer"], model.entered,
                text_role=model.answer_role, size=20, reserved_width=48 if speaker else 0)
        if model.answer_kind == ANSWER_SEQUENCE:
            return answer_diagram(self, q, answer=model.entered)
        if model.answer_kind == ANSWER_PITCH:
            return answer_diagram(self, q, reveal=True)
        return u.label(model.answer_text, size=20, flex=1 if speaker else 0, text_role=model.answer_role)

    def _learning_feedback_notes(self, question):
        """The learner's own notes on this question, as rows of its result page."""
        from .personal_content_notes_ui import append_notes
        rows = []
        append_notes(self, u, rows, 'question', question, page='learning_feedback')
        return rows

    def _learning_answer_speaker(self, question, *, guided=False):
        """One post-answer control; never changes attempts or listening credit."""
        text = self._learning_answer_audio_text(question)
        if not text:
            return None
        page = "learning_guide" if guided else "learning_feedback"

        def play(widget=None, **kwargs):
            if self.page == page and course_answer_runtime.shown_question(self, guided) is question:
                self.speak_text(text)

        return u.speaker_button(play, "朗读答案对应的日语")

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
        if kind in {"listening_comprehension", "vocab_audio_meaning", AUDIO_WORD_KIND}:
            return clean_utterance(question.get("audio_text") or "")
        if kind in {"vocab_reading", READING_CHOICE_KIND}:
            return clean_utterance(answer)
        if question.get("family") == "word" and kind in {
                "vocab_meaning", "vocab_recall", "vocab_orthography", "vocab_zh_ja_contrast"}:
            item = course_answer_runtime.service(self).item(question.get("target_id")) or {}
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
        from .semantic_errors import feedback_nodes
        q = answer_session(self).question
        identity, result = answer_session(self).feedback_view
        error_details = feedback_nodes(u, q, result) if identity == q['id'] else []
        item = course_answer_runtime.service(self).item(q["target_id"])
        if self._unverified_learning_reference(item, "返回结果",
                page="learning_feedback_detail", on_return=self.go_back):
            return
        instruction = item["instruction"]
        teaching_reference = self.order_translation_rows(instruction.get('example', ''),
            instruction.get('translation', ''), explicit=instruction.get('order_translation'))
        question_reference = []
        reference = q.get('order_translation')
        # Old local prompts have no ordinary translation to bind. Only new
        # authored metadata may add an answer-only question reference; the
        # teaching example remains available without inventing a translation.
        source = learning_order_translation_source(q) if isinstance(reference, dict) else None
        if source and (source != instruction.get('example') or not teaching_reference):
            question_reference = self.order_translation_rows(source, '',
                explicit=reference,
                title='本题语序参考译文（实验）', show_source=True)
        from .course_line.ui import answer_pages
        model = detail_model(item)
        self.screen(model.page, model.title, *answer_pages.feedback_detail_page(model, rows=[
            *origin_labels(item),
            *error_details,
            *question_reference,
            teaching_block(u, item, speak=self.speak_text),
            self._learning_instruction_example(instruction),
            self.natural_translation_label(instruction.get('example', ''), instruction.get('translation', '')),
            *teaching_reference],
            on_action=lambda action, widget=None: course_answer_runtime.run_answer_action(self, action, widget)))

    def finish_learning_question(self, rating, *, question_id=None):
        q = answer_session(self).question
        if self.page != "learning_feedback" or (question_id is not None and question_id != q["id"]):
            return
        course_answer_runtime.rate(self, rating)

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
                confirmed = await self.main_window.dialog(u.toga.ConfirmDialog("返回今日？", "已作答的题与草稿都会保留。"))
                if confirmed and token == self._page_token:
                    self.leave_learning()
            finally:
                self._leave_confirmation_active = False
        self.loop.create_task(confirm())
