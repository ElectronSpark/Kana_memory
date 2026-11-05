"""Native Python UI for kana browsing, handwriting and spaced-repetition practice."""
from __future__ import annotations

import asyncio
from datetime import datetime
from math import ceil
from pathlib import Path
import secrets
import sys
import time
import weakref
from unicodedata import normalize
from urllib.parse import urlsplit

import toga
from toga.platform import current_platform
from toga.style import Pack

from . import app_clock
from . import ui_components as ui
from .ui_components import col, row, wrapped, switch, selection, label, button, speaker_button
from .ui_theme import GREEN, INK, MUTED, PAPER, WHITE, ENTRY_BACKGROUNDS
from .content import ContentRepository
from .card_management import CardManager
from .domain import StudyStore, build_questions, build_practice_batch, practice_plan_counts, check_answer as answer_matches, word_romaji_unit_plan, any_question_metadata, isolated_kana_question
from .kana_links import ContentText, KanaLinkIndex
from .kana_table import VOWELS, build_kana_table
from .kana_voicing import merge_voicing_sections, voicing_family
from .kana_audio import kana_audio_plan
from .audio_overview import kana_audio_choices
from .ink_candidates import ink_candidate_kind, practice_ink_candidates
from .ink_renderer import InkRenderer
from .lesson_diagram_ui import lesson_canvas
from .navigation import BackStack, PageState, page_view
from .presentation import CATEGORY_LABELS, origin_source_ids, payload, short
from .text_layout import configure_text, font_scale, text_style
from .typography import font_family, configure_text_role, configure_study_ranges
from .detail_content import dialect_entry, is_kana_combination, sense_entries
from .detail_enrichment import REFERENCE_PAGE_NOTE, history_entries, japanese_entries, reference_entries
from .pronunciation_samples import pronunciation_samples
from .origin_images import OriginImageCatalog
from .practice_worker import build_in_background, study_revision
from .practice_availability import summarize_queue_availability
from .practice_inventory import kana_scope_inventory
from .word_spelling import analyze_spelling
from .kana_orthography import allowed_word_spellings
from .word_romaji_answer import analyze_word_romaji
from .word_romaji_units import compose_romaji_parts, analyze_romaji_parts
from .spelling_display import build_spelling_comparison
from .answer_feedback import build_answer_feedback
from .answer_autoplay import autoplay_answer
from .word_spelling_practice import build_spelling_corrections
from .word_romaji_practice import (build_word_romaji_drill, word_romaji_pool_counts,
    WORD_ROMAJI_COLLECTIONS, WORD_DRILL_DIRECTIONS, WORD_DIRECTION_KINDS)
from .word_keyboard import normalize_word_keyboard_options
from .pitch_display import build_pitch_block, clean_pitch_preferences, PITCH_DISPLAY_SETTING
from .reading_audio import MIDDLE_CHINESE, MODERN, matched_reading_audio
from .previews import compact_kana_origin, compact_kana_preview, compact_word_preview, kana_preview, source_preview, word_preview, word_answer_context
from .romanization import kana_learning_romaji, romanize_kana
from .build_info import VERSION, BUILD_DATE
from .enrichment_presentation import recording_path
from .learning_ui import LearningUIMixin, LEARNING_PAGES, LEARNING_ACTIVE
from .feedback_ui import FeedbackUIMixin
from .feedback_lifecycle import FeedbackLifecycleMixin, REPORT_BINDINGS
from .ai_ui import AI_PRACTICE_PAGES
from .tutor_ui import TUTOR_PAGES
from .retention_session_ui import DIAGNOSTIC_PAGES, RUNNING_PAGES as DIAGNOSTIC_RUNNING
from .learning_store import LearningStore
from .study_path_runtime import StudyPathRuntimeMixin
from .kana_learning_intro import should_introduce, intro_wait_until

if current_platform == "android":
    from .platform_android import create_platform
else:
    from .platform_desktop import create_platform

PRACTICE_PAGES = {"question", "word_question", "ink_question", "feedback", "session_done", "session_wait", "dependency_wait"}
FOCUSED_PAGES = PRACTICE_PAGES | {"practice_setup", "word_drill_setup", "free_ink", "kana_introduction", "kana_intro_wait"} | LEARNING_PAGES | AI_PRACTICE_PAGES | TUTOR_PAGES | DIAGNOSTIC_PAGES
DETAIL_PAGES = {"kana_detail", "kana_readings", "word_detail", "word_references",
                "character_catalog", "character_readings", "source", "notices", "card_detail",
                "learning_detail", "learning_feedback_detail", "learning_sources", "learning_dependencies", "learning_settings", "learning_data", "learning_records", "prompt_settings",
                "item_examples", "example_detail", "example_sources", "example_picker", "example_editor", "example_library", "example_exchange",
                "lexical_example_detail", "lexical_example_sources", "lexical_example_library",
                "study_path", "study_stage", "study_unit", "study_section", "study_path_add", "study_path_notes",
                "ai_center", "ai_form", "ai_target_picker", "ai_inbox", "ai_proposal", "ai_question_preview", "ai_candidate", "ai_service", "material_tasks", "material_task", "material_verification", "material_study", "material_enrollment", "material_selection", "material_share_inbox"}
REPORT_PAGES = {"report_editor", "report_list", "report_detail", "feedback_analysis", "feedback_batch"}
QUESTION_PAGES = {"question", "word_question", "ink_question"}
from .practice_preferences import (
    PRACTICE_KINDS, MIXED_PRACTICE_KINDS, ALL_MIXED_KINDS, DEFAULT_MIXED_KINDS, KANA_WORD_PRACTICE_KINDS, WORD_PRACTICE_KINDS, WORD_SETUP_KINDS, LISTENING_PRACTICE_KINDS, HANDWRITING_PRACTICE_KINDS, PRACTICE_SCRIPTS, WORD_DRILL_SCRIPTS, WORD_TABLES, PRACTICE_WORD_COLLECTIONS, PRACTICE_SCOPES, DEFAULT_PRACTICE_SCOPES, PRACTICE_COUNTS, PRACTICE_BATCHES, PRACTICE_SCHEDULES,
    practice_number, clean_preferences, enabled_preference_kinds,
)
CARD_STATES = {"全部状态": "all", "已到期": "due", "未开始": "new", "学习中": "learning",
               "复习中": "review", "重新学习": "relearning", "已暂停": "suspended", "今日暂缓": "buried"}
INK_PAGES = {"free_ink", "ink_question"}
INK_BINDINGS = "free_ink strokes ink_width ink_height _ink_view_width _ink_view_height canvas ink_result_box ink_status_label recognize_button ink_model_panel".split()
PRACTICE_BINDINGS = "questions question_index session_id question session_right script use_full_keyboard answer_revealed answered_text answer_correct question_audio_ok _question_started _answer_submitted_at _answer_duration_ms question_notice practice_preview_slot word_conversion_source word_conversion_convention word_used_conversion".split()
PAGE_BINDINGS = {
    "learning_familiarity": "_learning_item _learning_familiarity_state".split(),
    "learning_intro": ["_learning_item"],
    "learning_guide": "_learning_item _learning_guide _learning_guide_result _learning_pitch_context".split(),
    "learning_question": "_learning_question _learning_question_state _learning_answer _learning_parts _learning_used _learning_input _learning_composition _learning_status _learning_audio_ok _learning_pitch_context _learning_blocks_context".split(),
    "learning_feedback": "_learning_question _learning_question_state _learning_feedback_view_result".split(),
    "learning_feedback_detail": "_learning_question _learning_question_state _learning_feedback_view_result".split(),
    "ai_service": ["ai_model_input", "ai_tutor_model_input", "ai_connection_label"],
    "material_budget_settings": ['_material_budget_config', '_material_budget_fields', '_material_budget_status'],
    "tutor_question": "_tutor_current _tutor_answer_input _tutor_answer_state _tutor_status _learning_pitch_context".split(),
    "tutor_feedback": ["_tutor_current"],
    "tutor_chat": "_tutor_chat_origin _tutor_chat_input _tutor_chat_status".split(),
    "tutor_loading": ["_tutor_loading_status"],
    "ai_form": "_ai_form_context _ai_form_fields _ai_form_status _ai_generate_button _ai_cancel_button _ai_connection_slot _ai_source_scope_label".split(),
    "material_selection": ["_material_selection_context"],
    "ai_target_picker": ["_ai_target_query"],
    "ai_proposal": "_ai_proposal_id _ai_review_controls".split(),
    "material_task": ["_material_task_id"],
    "material_study": ["_material_study_id"],
    "material_enrollment": ["_material_enrollment"],
    "material_reference_source": ["_material_reference_owner", "_material_reference_save"],
    "material_reference_claim": ["_material_reference_owner", "_material_reference_save"],
    "material_senses": ["_material_sense_owner"],
    "ai_question_preview": ["_ai_question_preview"],
    "ai_question": "_ai_current _ai_answer_input _ai_question_status".split(),
    "ai_feedback": ["_ai_current"],
    "study_unit": ["_study_path_unit"],
    "study_section": ["_study_path_section_id"],
    "study_reference": ["_study_reference_entry_id"],
    "session_wait": "_review_wait_until _review_wait_status _review_wait_button _review_wait_checked".split(),
    "study_path_add": "study_section_count study_section_preview _study_selection _study_selection_plan _study_expanded _study_shortcut_open _study_prerequisites_open _study_selection_summary _study_selection_workload _study_selection_action _study_selection_tree _study_selection_dependencies _study_selection_shortcut _study_node_controls".split(),
    "example_detail": ["_example_detail_state"],
    "item_examples": ["_item_examples_state"],
    "example_editor": "_example_editor_target _example_editor_existing _example_editor_fields _example_editor_preview".split(),
    "example_picker": ["example_target_query"],
    "example_library": ["example_query"],
    "lexical_example_library": ["lexical_example_query", "lexical_example_state", "_lexical_example_library_state"],
    "example_exchange": ["_example_json_input", "_example_import_preview"],
    "learning_library": ["learning_library_query", "learning_library_category", "_library_family"],
    "home": ["home_stats_label", "home_action_slot", "home_secondary_slot", "home_material_slot"],
    "kana": ["kana_filter", "kana_grid"],
    "words": "word_query word_collection word_results word_page_label word_offset current_words word_has_next word_previous_button word_next_button word_help".split(),
    "settings": ["model_panel", "settings_message", "pitch_contour_switch", "pitch_number_switch"],
    "practice_setup": "kind_picker mixed_kind_switches practice_kinds_slot practice_mode_hint rounds_input schedule_picker rounds_slot schedule_hint script_picker word_table_picker practice_word_collection_picker research_switch scope_switches scope_inventory_labels batch_picker batch_hint count_picker custom_count_input custom_count_slot practice_scope_card practice_scope_slot practice_scope_hint practice_description practice_script_label practice_script_hint practice_word_card practice_word_slot practice_services practice_start_button practice_count_summary practice_replace_slot _practice_setup_preferences _practice_setup_temporary".split(),
    "word_drill_setup": "word_drill_direction_picker word_drill_keyboard_slot word_drill_count_input word_drill_script_picker word_drill_collection_picker word_drill_keyboard_picker word_drill_start_button word_drill_resume_button word_drill_summary word_drill_resume_slot".split(),
    "cards": "card_query card_state_picker card_kind_picker card_research_switch card_results card_page_label card_offset card_previous_button card_next_button".split(),
    "word_question": ["word_answer", "word_answer_label", "_word_converter"],
    "romaji_tool": ["_romaji_tool_context"],
    "diagnostic_scope": ["_diagnostic_scope"],
    "diagnostic_question": ["_diagnostic_context"],
    "diagnostic_feedback": ["_diagnostic_context"],
    "diagnostic_dialogue": ["_diagnostic_context", "_diagnostic_dialogue"],
    "question": "romaji_answer romaji_input word_romaji_parts word_romaji_plan word_romaji_unit_mode word_romaji_choices word_romaji_position word_romaji_current word_romaji_parts_label word_romaji_answer_label word_romaji_submit word_romaji_delete".split(),
}


def error_message(error):
    message = str(error).splitlines()[0] if str(error) else "操作未能完成，请重试。"
    return short(message, 180)


def review_wait_status(due, now=None, checked=False):
    """Keep sub-minute waits visible instead of rounding an ETA down to a minute."""
    now = now or app_clock.local_now(due.tzinfo)
    remaining = max(0, ceil((due - now).total_seconds()))
    if not remaining:
        return "已到期，可以继续复习。"
    minutes, seconds = divmod(remaining, 60)
    hours, minutes = divmod(minutes, 60)
    duration = ((f"{hours} 小时 " if hours else "")
                + (f"{minutes} 分 " if hours or minutes else "") + f"{seconds} 秒")
    return ("已检查：尚未到期，还需 " if checked else "距离下一题还有 ") + duration + "。"





def answer_hint(app):
    question = app.question
    return ui.row(ui.col(flex=1), ui.button("显示答案", lambda w, **kw:
        app.reveal_answer() if app.question is question and app.page in QUESTION_PAGES else None,
        compact=True, font_size=12, height=40, flex=1, color=MUTED, background_color=PAPER))


def audio_file_available(audio):
    try:
        return recording_path(Path(__file__).parent / "resources", audio).is_file()
    except (OSError, ValueError):
        return False


class KanaMemory(FeedbackLifecycleMixin, FeedbackUIMixin, StudyPathRuntimeMixin, LearningUIMixin, toga.App):
    def startup(self):
        self._study_path_enabled = True
        # Refuse missing/mismatched teaching data before opening learner state.
        from .content_pack import validate_installed_pack
        validate_installed_pack()
        self.repo = ContentRepository(Path(__file__).parent / "resources" / "content.sqlite3")
        self.store = StudyStore(self.paths.data, time_zone=None)
        from .material_qualification import bind_resources
        bind_resources(self.store, repo=self.repo)
        self.learning_store = LearningStore(self.store)
        from .practice_archive import archive_previous_rounds
        from .practice_round import enable
        archive_previous_rounds(self.store)
        enable(self.store)
        from .retention_schedule import visit as retention_visit
        retention_visit(self.store)
        self._practice_preferences = self._clean_preferences(self.store.get_setting("practice_preferences", {}))
        self._restoring_practice = False
        self.platform = create_platform()
        self.kana_links = KanaLinkIndex(self.repo.kana_list())
        self.source_map = {s["id"]: s for s in self.repo.sources()}
        self.origin_images = OriginImageCatalog(Path(__file__).parent / "resources" / "glyph_origins")
        self._origin_image_views = weakref.WeakSet()
        self.model_state = "checking"
        self.model_message = ""
        self._model_request = 0
        self._model_checking = False
        self._model_downloading = False
        self.service_message = "正在检查语音与手写服务…"
        self.script = "hiragana"
        self.research_enabled = False
        self.questions, self.question_index = [], 0
        self.session_id = None
        self.question = None
        self.session_right = 0
        # These are attempt facts, never rolled back with a retained page view.
        self._assisted_attempts = set()
        self._answered_attempts = set()
        self._page_token = 0
        self._paint_handle = None
        self._review_wait_handle = None
        self._ink_busy = False
        self._ink_revision = 0
        self._starting_practice = False
        self._audio_serial = 0
        self.back_stack = BackStack()
        self._navigating = False
        self._view_key = None
        self._render_key = None
        self._preview_pending = None
        self._practice_context = False
        self._leave_confirmation_active = False
        self._returning_home = False
        self._page_scroll = None
        self.strokes, self._active_stroke = [], None
        self.main_window = toga.MainWindow(title="假名记忆", size=(440, 820), on_resize=self.resize_origin_images)
        self.install_feedback()
        self.title_label = ui.label("假名记忆", size=24, color=WHITE, bold=True, wrap=False, margin_bottom=2)
        self.subtitle_label = ui.label("每天一点，听懂也写得出", color="#D7ECE1", size=12, margin_bottom=0)
        self.header_back_slot = ui.col()
        self.header_back_button = ui.button("‹", self.go_back, compact=True, width=44, font_size=18,
                                         color=WHITE, background_color=GREEN, margin_right=8, margin_bottom=0)
        if current_platform == "android":
            self.header_back_button._impl.native.setContentDescription("返回首页")
        self.header_title_box = ui.col(self.title_label, self.subtitle_label, flex=1)
        self.header_feedback_button = ui.button("反馈", self.begin_page_feedback, compact=True,
            width=max(56, round(40 * ui.font_scale())), font_size=12, color=WHITE, background_color=GREEN, margin_left=4, margin_bottom=0)
        if current_platform == "android":
            self.header_feedback_button._impl.native.setContentDescription("反馈当前页面")
        self.header = ui.row(self.header_back_slot, self.header_title_box, self.header_feedback_button,
                          background_color=GREEN, margin=0, padding=16, align_items="center")
        self.content_slot = ui.col(flex=1)
        self.navigation = ui.row(*[
            ui.button(title, handler, flex=1, margin=2, height=46, compact=True)
            for title, handler in [("今日", self.show_home), ("练习", self.show_practice_hub), ("学习库", self.show_learning_library), ("设置", self.show_settings_hub)]
        ], padding=6, background_color=WHITE)
        self.root = ui.col(self.header, self.content_slot, self.navigation, background_color=PAPER, flex=1)
        self.main_window.content = self.root
        self.show_home()
        self.main_window.show()
        self.platform.register_back_handler(self.go_back)
        register_visibility = getattr(self.platform, 'register_activity_visibility', None)
        if callable(register_visibility):
            register_visibility(lambda visible, at: self.loop.call_soon_threadsafe(self.activity_visibility, visible, at))
        self._sync_back_handler()

    async def on_running(self):
        from .learning_conditions import _reviewed_index
        await asyncio.gather(self.check_model(), self.refresh_services(), asyncio.to_thread(_reviewed_index))

    def on_exit(self):
        if getattr(self, "_backup_busy", False) and not getattr(self, "_backup_reopen_failed", False):
            from .backup_ui import request_cancel
            request_cancel(self)
            return False
        if not KanaMemory.diagnostic_leaving(self):
            return False
        self._backup_closing = True
        KanaMemory.clear_ai_secret_input(self)
        KanaMemory.cancel_ai_generation(self, shutdown=True)
        KanaMemory.cancel_tutor_request(self, navigate=False)
        KanaMemory._cancel_review_wait(self)
        KanaMemory._cancel_practice_start(self)
        for task in tuple(getattr(self, "_kana_render_tasks", ())):
            task.cancel()
        if not getattr(self, "_backup_reopen_failed", False):
            if self._checkpoint() is False:
                self._backup_closing = False
                return False
            try:
                from .practice_archive import archive_previous_rounds
                archive_previous_rounds(self.store)
            except Exception as exc:
                self._backup_closing = False
                self.alert("本轮记录尚未归档，请重试退出：" + error_message(exc))
                return False
        self._preview_pending = None
        self._page_token += 1
        if self._paint_handle:
            self._paint_handle.cancel()
        self.platform.close()
        if getattr(self, "_feedback_store", None) is not None:
            self._feedback_store.close()
        self.repo.close()
        self.store.close()
        return True

    def screen(self, name, title, *children, scroll=True):
        if getattr(self, "_backup_busy", False):
            return
        if getattr(self, 'page', None) in DIAGNOSTIC_RUNNING and not self.diagnostic_leaving(name):
            return
        if getattr(self, "page", None) == "ai_service" and name != "ai_service":
            KanaMemory.clear_ai_secret_input(self)
        if name != getattr(self, "page", None):
            KanaMemory.cancel_ai_generation(self, navigation=True)
            if getattr(self, "page", None) == "tutor_loading" and getattr(self, "_tutor_busy", False):
                KanaMemory.cancel_tutor_request(self, navigate=False)
        KanaMemory._cancel_review_wait(self)
        self.platform.stop()
        if self._paint_handle:
            self._paint_handle.cancel()
            self._paint_handle = None
        self.page = name
        if name in {"learning_wait", "kana_intro_wait"}:
            self._learning_wait_due = None
        if hasattr(self, "_feedback_events"):
            self._feedback_events.append({"event": "screen", "page": name, "title": str(title),
                                          "at": datetime.now().isoformat()})
            del self._feedback_events[:-20]
        if name in FOCUSED_PAGES:
            self._practice_context = True
        elif name == "home":
            self._practice_context = False
        KanaMemory._update_chrome(self)
        self._page_token += 1
        self._active_stroke = None
        self.title_label.text = title
        self.subtitle_label.text = "假名 · 字源 · 听写 · 间隔复习"
        self.content_slot.clear()
        if name in PRACTICE_PAGES:
            self.practice_preview_slot = ui.col()
            position = 2 if name == "feedback" else 0
            children = (*children[:position], self.practice_preview_slot, *children[position:])
        body = ui.col(*children, padding=16, background_color=PAPER, **({} if scroll else {"flex": 1}))
        if scroll:
            container = toga.ScrollContainer(content=body, horizontal=False, style=Pack(flex=1))
        else:
            container = body
        self._page_scroll = container if scroll else None
        self.content_slot.add(container)
        self._checkpoint()
        return body

    def _capture_page(self):
        KanaMemory.save_material_reference_draft(self)
        if self._view_key is None:
            return None
        names = list(PAGE_BINDINGS.get(self.page, [])) + list(REPORT_BINDINGS.get(self.page, []))
        if self.page in PRACTICE_PAGES:
            names += PRACTICE_BINDINGS
        if self.page in INK_PAGES:
            names += INK_BINDINGS
        bindings = {name: getattr(self, name) for name in names if hasattr(self, name)}
        bindings["_practice_context"] = getattr(self, "_practice_context", False)
        if self.page in {"learning_wait", "kana_intro_wait"}:
            bindings["_learning_wait_due"] = getattr(self, "_learning_wait_due", None)
        if self.page == 'example_library':
            bindings['_example_library_state'] = getattr(self, '_example_library_state', ('', 0))
        from .personal_content_refresh import generation
        return PageState(self._view_key, self.page, self.title_label.text,
                         self.content_slot.children[0], bindings, self._page_scroll,
                         tuple(self._page_scroll.position) if self._page_scroll else (0, 0), generation(self))

    def _sync_back_handler(self):
        # Back stays inside the app even at its root; Android Home still works.
        self.platform.set_back_enabled(True)

    def _update_chrome(self):
        if not hasattr(self, "navigation"):
            return
        focused = self.page in FOCUSED_PAGES
        detail = self.page in DETAIL_PAGES | REPORT_PAGES
        hide_navigation = focused or self.page in {"report_editor", "report_detail", "feedback_analysis", "feedback_batch"} or getattr(self, "_practice_context", False)
        attached = self.navigation in self.root.children
        if hide_navigation and attached:
            self.root.remove(self.navigation)
        elif not hide_navigation and not attached:
            self.root.add(self.navigation)
        self.header_back_slot.clear()
        if focused or detail:
            self.header_back_slot.add(self.header_back_button)
            if current_platform == "android":
                self.header_back_button._impl.native.setContentDescription("返回首页" if focused else "返回上一页")
        if hasattr(self, "header"):
            word_drill = (self.page == "word_drill_setup" or
                          self.page in PRACTICE_PAGES and getattr(getattr(self, "store", None), "practice_lane", "main") == "word_romaji")
            compact_header = detail or self.page in LEARNING_PAGES | AI_PRACTICE_PAGES | TUTOR_PAGES | DIAGNOSTIC_PAGES or self.page in {"home", "practice_hub", "learning_library", "settings_hub", "feedback"} or word_drill or (self.page == "kana" and ui.font_scale() > 1.2)
            self.header.style.padding = (4, 8) if compact_header else 16
            self.title_label.style.font_size = 18 if compact_header else 24
            self.title_label.style.margin_bottom = 0 if compact_header else 2
            subtitle_attached = self.subtitle_label in self.header_title_box.children
            if compact_header and subtitle_attached:
                self.header_title_box.remove(self.subtitle_label)
            elif not compact_header and not subtitle_attached:
                self.header_title_box.add(self.subtitle_label)

    def _return_home_from_practice(self):
        if self._checkpoint() is False:
            return
        KanaMemory._cancel_review_wait(self)
        KanaMemory._cancel_practice_start(self)
        self.platform.stop()
        self._ink_revision += 1
        self._audio_serial += 1
        self.back_stack.pages.clear()
        self._view_key = None
        self._returning_home = True
        try:
            self.show_home()
        finally:
            self._returning_home = False
        self.back_stack.pages.clear()

    def request_practice_exit(self, widget=None, **kwargs):
        if self.page in TUTOR_PAGES:
            return KanaMemory.tutor_back(self)
        if self.page in AI_PRACTICE_PAGES:
            return KanaMemory.request_ai_practice_exit(self)
        if self.page in LEARNING_PAGES:
            self.request_learning_exit()
            return
        if getattr(self, "_leave_confirmation_active", False):
            return
        if self.page in {"session_wait", "session_done", "kana_intro_wait", "dependency_wait"}:
            KanaMemory._return_home_from_practice(self)
            return
        self._leave_confirmation_active = True
        token = self._page_token
        message = ("返回后会清空这次自由试写的笔迹。" if self.page == "free_ink" else
                   "练习设置会保留。" if self.page in {"practice_setup", "word_drill_setup"} else
                   "拼写进度已保存，可从首页或词库继续单词拼写。" if getattr(self.store, "practice_lane", "main") == "word_romaji" else
                   "已保存的复习记录会保留。未完成的练习可从首页继续。")

        async def confirm():
            try:
                confirmed = await self.main_window.dialog(toga.ConfirmDialog("返回首页？", message))
                if confirmed and token == self._page_token:
                    KanaMemory._return_home_from_practice(self)
            finally:
                self._leave_confirmation_active = False
        self.loop.create_task(confirm())

    def go_back(self, widget=None, **kwargs):
        if getattr(self, "_backup_busy", False):
            from .backup_ui import request_cancel
            request_cancel(self)
            return True
        KanaMemory.save_material_reference_draft(self)
        if self.page in DIAGNOSTIC_PAGES:
            return self.diagnostic_back()
        if self.page == "ai_service":
            KanaMemory.clear_ai_secret_input(self)
        if self.page in TUTOR_PAGES:
            return KanaMemory.tutor_back(self)
        if self.page in AI_PRACTICE_PAGES:
            return KanaMemory.request_ai_practice_exit(self)
        KanaMemory.cancel_ai_generation(self, navigation=True)
        if self.page == 'feedback_batch':
            self.show_feedback_manager()
            return True
        if self.page == 'feedback_analysis':
            self.show_feedback_detail(self._feedback_analysis_view['record']['id'])
            return True
        if self.page in {"report_editor", "report_detail"}:
            return self.request_report_back()
        if self.page in LEARNING_PAGES:
            self.request_learning_exit()
            return True
        if self.page in FOCUSED_PAGES:
            KanaMemory.request_practice_exit(self)
            return True
        retained = self.back_stack.pages[-1] if self.back_stack.pages else None
        if retained is not None and retained.name in PRACTICE_PAGES:
            revised = KanaMemory._reconcile_practice_kinds(self)
            if revised is False:
                return True
            if isinstance(revised, dict) and revised.get("changed"):
                if revised.get("completed"):
                    self.show_question()
                else:
                    self.resume_practice()
                return True
        previous = self.back_stack.pop()
        if previous is None:
            return True
        self._sync_back_handler()
        KanaMemory._cancel_review_wait(self)
        saw_feedback = self.page == "feedback"
        feedback_question = self.question
        self.platform.stop()
        if self._paint_handle:
            self._paint_handle.cancel()
            self._paint_handle = None
        self._page_token += 1
        self._ink_revision += 1
        self._audio_serial += 1
        self._active_stroke = None
        self.page, self._view_key = previous.name, previous.key
        from .page_restore import restore_bindings, mount_retained_content, schedule_scroll_restore
        restore_bindings(self, previous.bindings)
        from .personal_content_refresh import refresh_return
        if refresh_return(self, previous): return True
        if self.page in DIAGNOSTIC_RUNNING:
            if self.page == 'diagnostic_dialogue':
                self.show_diagnostic_dialogue(self._diagnostic_context['episode'], self._diagnostic_context['row']['id'])
            else:
                self.continue_diagnostic(self._diagnostic_context['episode'])
            return True
        KanaMemory._update_chrome(self)
        self.title_label.text = previous.title
        mount_retained_content(self, previous)
        self._page_scroll = previous.scroll
        if (self.page in QUESTION_PAGES | {"feedback"} and self.question is not None
                and self.store.is_content_available(self.question.content_id) is False):
            self.back_stack.discard(PRACTICE_PAGES)
            self._navigating = True
            try:
                self.show_question()
                self._view_key = ("show_question", None)
            finally:
                self._navigating = False
            self._sync_back_handler()
            return True
        if saw_feedback and self.page in {"question", "word_question", "ink_question"} and self.question is feedback_question:
            # Returning after seeing the answer must not count as fresh recall.
            self._assisted_attempts.add(self._attempt_key())
            self._answered_attempts.discard(self._attempt_key())
        if self.page in QUESTION_PAGES:
            self.answer_revealed = self.answer_revealed or self._attempt_key() in self._assisted_attempts
            if self.answer_revealed:
                self.question_notice.text = ("已查看答案或学习提示，本题不计入无提示回忆。"
                    if self.question.kind in WORD_PRACTICE_KINDS else "已查看答案或学习提示，本题仅安排再学一次。")
        if self.page == "home":
            self.store.practice_lane = "main"
            self.home_stats_label.text = self._stats_summary(self.store.stats())
            self.refresh_model_ui()
            self.refresh_home_actions()
            self.refresh_shared_material_notice()
        elif self.page == "ai_service":
            self.show_ai_service_settings()
        elif self.page == "ai_form":
            self.refresh_ai_form_connection()
            self._set_ai_form_busy(getattr(self, "_ai_request_operation", None) is not None)
        elif self.page == "material_task":
            self.show_material_task(self._material_task_id)
        elif self.page == "material_tasks":
            self.show_material_tasks()
        elif self.page == "material_study":
            self.show_material_study(self._material_study_id)
        elif self.page == "settings":
            self.settings_message.text = self.service_message
            self.refresh_model_ui()
        elif self.page == "ai_proposal":
            self._render_ai_proposal(self._ai_proposal_id)
        elif self.page == "example_detail":
            self._render_example_detail(*self._example_detail_state)
        elif self.page == "study_path_add":
            self._refresh_study_selection(rebase=True)
        elif self.page in {"learning_wait", "kana_intro_wait"}:
            self.arm_learning_wait(getattr(self, "_learning_wait_due", None))
        elif self.page == "session_wait":
            self._review_wait_handle = self.loop.call_soon(KanaMemory._tick_review_wait, self, self._page_token)
        elif self.page == "word_drill_setup":
            self.store.practice_lane = "word_romaji"
            self.refresh_word_drill_setup()
        elif self.page == "practice_setup":
            self.store.practice_lane = "main"
            self._scope_inventory_cache = {}
            if hasattr(self, "practice_replace_slot"):
                self.practice_replace_slot.clear()
            self.refresh_practice_setup()
        elif self.page == "cards":
            self.refresh_cards(reset=False)
        elif self.page in INK_PAGES:
            self.refresh_model_ui()
            self._schedule_ink_draw()
        schedule_scroll_restore(self, previous, only_if_changed=True)
        self._checkpoint()
        return True

    def _pitch_preferences(self):
        store = getattr(self, "store", None)
        return clean_pitch_preferences(store.get_setting(PITCH_DISPLAY_SETTING, {}) if store else {})

    def _pitch_slot(self, word, *, reading=None, size=20, masked=False,
                    plain=False, link_kana=False, compact=False, show_missing=False):
        """Refresh displayed annotations without touching saved questions or answers."""
        slot = ui.col()
        slot_ref = weakref.ref(slot)
        def render():
            current = slot_ref()
            if current is None:
                return
            preferences = KanaMemory._pitch_preferences(self)
            block = build_pitch_block(self, word, reading=reading, size=size,
                masked=masked, compact=compact, show_missing=show_missing,
                link_kana=link_kana, **preferences)
            current.clear()
            # Number-only/unknown views still need their ordinary kana reading.
            if plain and (not preferences["contour"] or block is None or
                          not getattr(block, "_pitch_has_contour", False)):
                reading_label = self.content_label if link_kana else ui.label
                current.add(reading_label(reading, size=size, bold=True, text_role="study"))
            if block is not None:
                current.add(block)
        slot._render_pitch = render
        views = [ref for ref in getattr(self, "_pitch_views", []) if ref() is not None]
        views.append(slot_ref)
        self._pitch_views = views
        render()
        return slot

    def change_pitch_display(self, widget=None, **kwargs):
        if self.page != "settings" or widget not in (self.pitch_contour_switch, self.pitch_number_switch):
            return
        value = {"contour": bool(self.pitch_contour_switch.value), "number": bool(self.pitch_number_switch.value)}
        self.store.set_setting(PITCH_DISPLAY_SETTING, value)
        for reference in tuple(getattr(self, "_pitch_views", [])):
            view = reference()
            if view is not None:
                view._render_pitch()

    def note(self, text):
        return ui.col(ui.label(text, size=12, color=MUTED, margin_bottom=0), padding=12, margin_bottom=12, background_color="#E5EEE8")

    def heading(self, text):
        return ui.label(text, size=18, bold=True, margin_top=12)

    def entry_card(self, *children, index=0):
        return ui.col(*children, padding=12, margin_bottom=10,
                   background_color=ENTRY_BACKGROUNDS[index % len(ENTRY_BACKGROUNDS)])

    def detail_entry(self, entry, index=0):
        children = [ui.label(entry.label, size=12, color=MUTED, bold=True)] if entry.label else []
        styles = {"lead": {"size": 20, "bold": True, "color": GREEN},
                  "body": {"size": 15}, "meta": {"size": 12, "color": MUTED},
                  "note": {"size": 12, "color": MUTED}}
        audio = [item for item in entry.audio if audio_file_available(item.audio)]
        anchor = next((i for i, line in enumerate(entry.lines) if line.role == "lead"), 0)
        for i, line in enumerate(entry.lines):
            if audio and i == anchor:
                children.append(ui.row(
                    self.mixed_label(*line.parts, **styles.get(line.role, styles["body"]), flex=1, columns=24),
                    *[ui.speaker_button(lambda w, a=item.audio, **kw: self.play_recording(a),
                                     f"{item.play_label}：{item.character} · {item.reading}") for item in audio]))
            else:
                children.append(self.mixed_label(*line.parts, **styles.get(line.role, styles["body"])))
        for item in audio:
            recording = item.audio
            children.append(ui.label(item.recording_label, size=12, color=MUTED))
            if recording.get("ipa") and recording["ipa"] != item.reading:
                children.append(ui.label("录音 IPA：" + recording["ipa"], size=12, color=MUTED))
            if recording.get("source_locality_label") and recording["source_locality_label"] != item.place_or_system:
                children.append(ui.label("来源地点说明：" + recording["source_locality_label"], size=12, color=MUTED))
            if recording.get("recording_scope"):
                children.append(ui.label("音频范围：" + recording["recording_scope"], size=12, color=MUTED))
            if item.kind == MIDDLE_CHINESE:
                children.append(ui.label("记音：" + item.notation_label + ("；" + item.tone_label if item.tone_label else ""), size=12, color=MUTED))
                children.append(ui.label("按所列重建体系演示，不是古代实录。", size=12, color=MUTED))
            children.extend([
                ui.label("署名：" + recording["author"] + " · " + recording["license"], size=12, color=MUTED),
                ui.button("音频出处", lambda w, a=recording, **kw: self.open_url(a["source_url"], title="音频出处",
                       summary=a["character"] + " · " + a["reading"])),
            ])
        return self.entry_card(*children, index=index)

    def detail_entries(self, entries):
        entries = tuple(entries)
        if len(entries) <= 6:
            return [self.detail_entry(entry, index) for index, entry in enumerate(entries)]
        # Android measures every native label even below the visible viewport.
        # Keep long reference lists small at first; retained views keep their
        # expansion state when the user opens an index and returns with Back.
        visible = 4
        cards = ui.col(*(self.detail_entry(entry, index) for index, entry in enumerate(entries[:visible])))
        more = ui.button(f"显示更多（还有 {len(entries) - visible} 条）", None)

        def expand(widget=None, **kwargs):
            nonlocal visible
            end = min(len(entries), visible + 6)
            # Construct this batch before attaching it to the live layout.
            batch = [self.detail_entry(entries[index], index) for index in range(visible, end)]
            cards.add(*batch)
            visible = end
            if visible == len(entries):
                section.remove(more)
            else:
                more.text = f"显示更多（还有 {len(entries) - visible} 条）"

        more.on_press = expand
        section = ui.col(cards, more)
        return [section]

    def _content_action(self, callback):
        identity = KanaMemory._content_page_key(self)

        def active(widget=None, **kwargs):
            if KanaMemory._content_page_key(self) == identity:
                return callback(widget, **kwargs)

        return active

    def disclosure(self, title, builder):
        """Keep optional information in place and retain it across Back."""
        identity = KanaMemory._content_page_key(self)
        slot = ui.col()
        toggle = ui.button(title + " ▸", None)
        opened = False

        def expand(widget=None, **kwargs):
            nonlocal opened
            if KanaMemory._content_page_key(self) != identity:
                return
            slot.clear()
            if not opened:
                slot.add(*builder())
            opened = not opened
            toggle.text = title + (" ▾" if opened else " ▸")

        toggle.on_press = expand
        return ui.col(toggle, slot)

    def pronunciation_sample_card(self, sample, index=0):
        identity = KanaMemory._content_page_key(self)
        slot = ui.col()
        toggle = ui.button("详细", None, compact=True, width=68, font_size=12, margin_left=4, margin_bottom=0)
        opened = False

        def expand(widget=None, **kwargs):
            nonlocal opened
            if KanaMemory._content_page_key(self) != identity:
                return
            slot.clear()
            if not opened:
                # Evidence only: the sample model strips duplicate readings
                # and speakers from its expandable detail entries.
                slot.add(*(self.detail_entry(entry, i) for i, entry in enumerate(sample.details)))
                slot.add(*self.source_buttons(sample.source_ids))
                urls = dict.fromkeys(audio.get("source_url") for audio in sample.audio if audio.get("source_url"))
                for url in urls:
                    slot.add(ui.button("音频出处", KanaMemory._content_action(self, lambda w, target=url, **kw:
                        self.open_url(target, title="音频出处", summary=sample.title))))
            opened = not opened
            toggle.text = "收起" if opened else "详细"

        toggle.on_press = expand
        controls = []
        for audio in sample.audio:
            controls.append(ui.speaker_button(KanaMemory._content_action(self, lambda w, a=audio, **kw: self.play_recording(a)),
                "播放" + sample.title + "：" + sample.reading))
        if sample.tts_language:
            controls.append(ui.speaker_button(KanaMemory._content_action(self, lambda w, c=sample.character, lang=sample.tts_language, **kw:
                self.speak_chinese(c, lang)), "播放" + sample.title))
        lines = [ui.label(sample.title, size=12, color=MUTED, margin_bottom=3),
            self.content_label(sample.reading, size=19, bold=True, color=GREEN, margin_bottom=3)]
        if sample.notation != "未标注":
            lines.append(ui.label(sample.notation, size=11, color=MUTED, margin_bottom=0))
        summary = ui.col(*lines, flex=1)
        return self.entry_card(ui.row(summary, ui.col(*controls), toggle), slot, index=index)

    def pronunciation_panel(self, *, dialects=(), historical=(), characters=(), context=None):
        characters = tuple(dict.fromkeys(characters))
        demonstrations = [audio for character in characters
                          for audio in self.repo.character_synthesis_for_hanzi(character)]
        samples = pronunciation_samples(dialects=dialects, historical=historical,
            demonstrations=demonstrations, system_characters=characters, context=context)
        if not samples:
            return []
        playable = [sample for sample in samples if sample.playable]
        written = [sample for sample in samples if not sample.playable]
        children = [self.heading("古汉语与方言发音")]
        children.extend(self.pronunciation_sample_card(sample, index) for index, sample in enumerate(playable))
        if written:
            children.append(self.disclosure(f"更多读音资料（仅注音 · {len(written)}）",
                lambda: [self.pronunciation_sample_card(sample, index) for index, sample in enumerate(written)]))
        related = [character for character in characters
                   if ("show_character_readings", character) != KanaMemory._content_page_key(self)]
        if related:
            children.append(self.disclosure("字源字参照", lambda: [
                ui.button("字音与异体参照 · " + character, KanaMemory._content_action(self,
                    lambda w, c=character, **kw: self.open_character_readings(c))) for character in related]))
        return children

    def kana_pronunciation_card(self, item):
        rows, recordings = [], []
        for member in voicing_family(item, self.repo.kana_list()):
            member = item if member["id"] == item["id"] else self.repo.get_kana(member["id"])
            forms = "　".join(form for form in (member.get("hiragana"), member.get("katakana")) if form)
            marked = any(mark in normalize("NFD", forms) for mark in ("\u3099", "\u309a", "゛", "゜"))
            available = [audio for audio in payload(member).get("recordings", []) if audio_file_available(audio)]
            plan = kana_audio_plan(member)
            # Prefer one consistent installed Japanese voice. Retain recordings
            # as an offline fallback and as explicitly selectable source audio.
            def play(w, p=plan, clips=tuple(available), **kw):
                if p.playable and (getattr(getattr(self, "platform", None), "tts_ready", False) or not clips):
                    self.speak_text(p.text)
                elif clips:
                    self.play_recording(clips[0])
            recordings.extend((forms, audio) for audio in available)
            controls = [ui.label(forms, size=17 if marked else 28, bold=not marked, text_role="study",
                color=GREEN, margin_bottom=0, flex=1),
                ui.label(kana_learning_romaji(member) or "依语境读音", size=17, flex=0.5, margin_right=12, margin_bottom=0)]
            if plan.playable or available:
                description = "播放日语：" + forms + (" · " + plan.label if plan.label else "")
                controls.append(ui.speaker_button(KanaMemory._content_action(self, play), description))
            rows.append(ui.row(*controls, margin_bottom=6))
            if plan.label:
                rows.append(ui.label(plan.label, size=12, color=MUTED))
        if item.get("hiragana") == "を" or item.get("katakana") == "ヲ":
            rows.append(ui.label("wo 用于区分字形；现代读音通常为 o。", size=12, color=MUTED))
        if recordings:
            def attribution():
                result = []
                for forms, audio in recordings:
                    result.extend([ui.row(ui.label(forms + " · 原录音 · " + audio["author"] + " · " + audio["license"], size=12, color=MUTED, flex=1),
                        ui.speaker_button(KanaMemory._content_action(self, lambda w, a=audio, **kw: self.play_recording(a)), "播放原录音：" + forms)),
                        ui.button("音频出处", KanaMemory._content_action(self, lambda w, a=audio, **kw:
                            self.open_url(a["source_url"], title="音频出处", summary=a.get("text", ""))))])
                return result
            rows.append(self.disclosure("音频资料", attribution))
        return self.entry_card(*rows)

    def kana_audio_overview(self, item):
        """Keep every playable version in one compact, immediately usable grid."""
        data = payload(item)
        characters = () if is_kana_combination(item) else tuple(dict.fromkeys(data.get("enrichment", {}).get("source_hanzi", [])))
        samples = ()
        if not is_kana_combination(item):
            samples = pronunciation_samples(dialects=item.get("dialects", []),
                historical=data.get("historical_reading_references") or data.get("component_historical_reading_references") or [],
                demonstrations=[audio for char in characters for audio in self.repo.character_synthesis_for_hanzi(char)],
                system_characters=characters, context=item)
        family = [item if member["id"] == item["id"] else self.repo.get_kana(member["id"])
                  for member in voicing_family(item, self.repo.kana_list())]
        choices = kana_audio_choices(item, family, samples, available=audio_file_available)
        selected = ui.col()

        def describe(choice):
            selected.clear()
            lines = [ui.label(choice.full_title, size=12, bold=True, color=GREEN, margin_bottom=3)]
            if choice.reading:
                lines.append(ui.label(choice.reading, size=15, margin_bottom=3))
            if choice.notation and choice.notation != "未标注":
                lines.append(ui.label(choice.notation, size=11, color=MUTED, margin_bottom=3))
            if choice.plan and choice.plan.label and choice.plan.label not in choice.full_title:
                lines.append(ui.label(choice.plan.label, size=12, color=MUTED, margin_bottom=3))
            if choice.kana and (choice.kana.get("hiragana") == "を" or choice.kana.get("katakana") == "ヲ"):
                lines.append(ui.label("wo 用于区分字形；现代读音通常为 o。", size=12, color=MUTED))

            def evidence():
                if choice.sample:
                    children = [self.detail_entry(entry, i) for i, entry in enumerate(choice.sample.details)]
                    children.extend(self.source_buttons(choice.sample.source_ids))
                    clips = choice.sample.audio
                else:
                    children = []
                    clips = (choice.recording,) if choice.recording else choice.fallback_recordings
                    for clip in clips:
                        children.append(ui.label(clip.get("author", "") + " · " + clip.get("license", ""), size=12, color=MUTED))
                for url in dict.fromkeys(clip.get("source_url") for clip in clips if clip.get("source_url")):
                    children.append(ui.button("音频出处", KanaMemory._content_action(self,
                        lambda w, target=url, **kw: self.open_url(target, title="音频出处", summary=choice.full_title))))
                return children

            if choice.sample or choice.recording or choice.fallback_recordings:
                lines.append(self.disclosure("读音资料", evidence))
            selected.add(*lines)

        def play(choice):
            describe(choice)
            if choice.kind == "japanese":
                if choice.plan.playable and (getattr(self.platform, "tts_ready", False) or not choice.fallback_recordings):
                    self.speak_text(choice.plan.text)
                elif choice.fallback_recordings:
                    self.play_recording(choice.fallback_recordings[0])
            elif choice.kind in {"recording", "sample_audio"}:
                self.play_recording(choice.recording)
            else:
                self.speak_chinese(choice.sample.character, choice.sample.tts_language)

        controls = []
        for choice in choices:
            control = ui.button(choice.title + " 🔊", KanaMemory._content_action(self,
                lambda w, c=choice, **kw: play(c)), compact=True, multiline=True,
                font_size=12, height=48, flex=1, margin=2)
            if current_platform == "android":
                control._impl.native.setContentDescription("播放版本：" + choice.key + " · " + choice.full_title)
            controls.append(control)
        grid = []
        for start in range(0, len(controls), 3):
            cells = controls[start:start + 3]
            cells += [ui.col(flex=1, margin=2) for _ in range(3 - len(cells))]
            grid.append(ui.row(*cells))
        if choices:
            describe(next((choice for choice in choices if choice.kind == "japanese" and choice.kana["id"] == item["id"]), choices[0]))
        else:
            forms = "　".join(dict.fromkeys(form for form in (item.get("hiragana"), item.get("katakana")) if form))
            selected.add(ui.label(forms, size=22, bold=True, color=GREEN, text_role="study"), ui.label(kana_audio_plan(item).label, size=12, color=MUTED))
        current_forms = "／".join(dict.fromkeys(form for form in (item.get("hiragana"), item.get("katakana")) if form))
        children = [ui.label(current_forms + " · 读音对照", size=14, bold=True, color=GREEN, margin_bottom=4), *grid, selected]
        written = [sample for sample in samples if not sample.playable]
        if written:
            children.append(self.disclosure(f"更多读音资料（仅注音 · {len(written)}）",
                lambda: [self.pronunciation_sample_card(sample, i) for i, sample in enumerate(written)]))
        if characters:
            children.append(self.disclosure("字源字参照", lambda: [
                ui.button("字音与异体参照 · " + char, KanaMemory._content_action(self,
                    lambda w, c=char, **kw: self.open_character_readings(c))) for char in characters]))
        return ui.col(*children, margin_bottom=10)

    def resize_origin_images(self, window=None, **kwargs):
        window = window or self.main_window
        # Screen padding (16 each side), then the card's padding (12 each side).
        for view in tuple(self._origin_image_views):
            columns = getattr(view, "_origin_columns", 1)
            width = max(1, int((int(window.size.width) - 32 - 8 * (columns - 1)) / columns) - 24)
            if view.style.width != width:
                view.style.width = width
                view.refresh()

    def origin_image_cards(self, item, *, compact=False, references=False):
        cards, source_ids = [], []
        columns = 2 if compact else 1
        width = max(1, int((int(self.main_window.size.width) - 32 - 8 * (columns - 1)) / columns) - 24)
        for image in self.origin_images.for_kana(item):
            if references:
                image = image.teaching_reference()
                if image is None:
                    continue
            try:
                # Only width is set so every backend preserves the aspect ratio.
                # Each widget owns its image; Windows can dispose its native bitmap.
                view = toga.ImageView(image=image.path, style=Pack(width=width, margin_bottom=0))
            except (OSError, ValueError):
                continue
            self._origin_image_views.add(view)
            view._origin_columns = columns
            card = self.entry_card(ui.label(image.label, size=12, color=MUTED, bold=True, margin_bottom=2),
                                   view, index=len(cards))
            if compact:
                card.style.flex = 1
                card.style.margin_right = 4
            cards.append(card)
            source_ids.extend(image.source_ids)
        if compact:
            cards = [ui.row(*cards[start:start + 2]) for start in range(0, len(cards), 2)]
        return cards, list(dict.fromkeys(source_ids))

    def origin_review_cards(self, item):
        notes = list(dict.fromkeys(image.review_note for image in self.origin_images.for_kana(item)
                                  if image.review_note))
        children = [ui.label(note, size=13, color=MUTED) for note in notes]
        cards, _ = self.origin_image_cards(item, references=True)
        if cards and not notes:
            children.append(ui.label("以下为现代作者的教学示意，尚不能作为历史中间字形或年代顺序的证据。",
                                  size=12, color=MUTED))
        children.extend(cards)
        return children

    def chinese_tts_cards(self, characters):
        characters = list(dict.fromkeys(c for c in characters if isinstance(c, str) and len(c) == 1))
        if not characters:
            return []
        cards = [self.heading("现代汉字 · 系统合成"),
                 self.note("系统按字自动选音，多音字可能与词典所列读法不同。首次需在设置中下载普通话或粤语音包；不代替地方字音和中古拟音。")]
        for index, character in enumerate(characters):
            cards.append(self.entry_card(ui.label(character, size=23, bold=True, color=GREEN),
                ui.row(ui.label("普通话 · 自动选音", flex=1), ui.speaker_button(lambda w, c=character, **kw: self.speak_chinese(c, "cmn"), f"普通话系统示范：{character}")),
                ui.row(ui.label("香港粤语 · 自动选音", flex=1), ui.speaker_button(lambda w, c=character, **kw: self.speak_chinese(c, "yue"), f"香港粤语系统示范：{character}")), index=index))
        return cards

    def character_synthesis_cards(self, characters):
        cards, sources = [], []
        for character in dict.fromkeys(characters):
            for audio in self.repo.character_synthesis_for_hanzi(character):
                try:
                    if not recording_path(Path(__file__).parent / "resources", audio).is_file():
                        continue
                except (OSError, ValueError):
                    continue
                if not cards:
                    cards += [self.heading("方言 · 合成示范"),
                              self.note("模型按字形自动选音，尚未经过母语者审听；不表示与各条词典注音一致。")]
                cards.append(self.entry_card(
                    ui.label(audio["locality_label"], size=12, color=MUTED, bold=True),
                    ui.row(ui.label(character, size=23, bold=True, color=GREEN, flex=1),
                        ui.speaker_button(lambda w, a=audio, **kw: self.play_recording(a), f"播放{audio['locality_label']}示范：{character}")),
                    ui.label("合成示范 · 待审听", size=13),
                    ui.label("模型：" + audio["model_id"] + " · " + audio["model_variant"], size=12, color=MUTED),
                    ui.label(short(audio.get("limitations_zh", ""), 150), size=12, color=MUTED),
                    ui.label("署名：" + audio["author"] + " · " + audio["license"], size=12, color=MUTED),
                    ui.button("合成音出处", lambda w, a=audio, **kw: self.open_url(a["source_url"], title="合成音出处",
                        summary=a["character"] + " · " + a["locality_label"] + "\n" + a.get("method_zh", ""))), index=len(cards) - 2))
                sources.extend([*audio["source_ids"], *audio.get("prompt_source_ids", [])])
        return cards, list(dict.fromkeys(sources))

    def mixed_label(self, *parts, size=14, color=INK, bold=False, wrap=True, columns=None, study_content=False, **style):
        """Only ContentText is linkable; ordinary strings remain UI copy."""
        text, spans = self.kana_links.render(parts, columns=(columns or max(12, round(40 * 14 / max(14, size)))) if wrap and current_platform != "android" else None)
        current_key = self._content_page_key()
        if current_key and current_key[0] == "show_kana_detail":
            spans = [span for span in spans if span["kana_id"] != current_key[1]]
        widget = ui.label(text, size=size, color=color, bold=bold, wrap=False, **style)
        linked = not spans or self.platform.link_kana_label(widget, spans, self.open_content_kana)
        if study_content and current_platform == "android":
            # Caller identifies the Japanese fields; ContentText by itself only
            # grants link eligibility and also occurs in Chinese/English prose.
            offset, ranges = 0, []
            for part in parts:
                value = part.text if isinstance(part, ContentText) else part
                if isinstance(part, ContentText) and value:
                    ranges.append((offset, offset + len(value)))
                offset += len(value)
            configure_study_ranges(widget, ranges)
        if linked:
            return widget
        # Desktop preview fallback: native Android uses inline text links.
        targets = {span["kana_id"]: span["text"] for span in spans}
        actions = [ui.button(text, lambda w, kid=kid, **kw: self.open_content_kana(kid), compact=True, flex=1, margin=2, text_role="study")
                   for kid, text in targets.items()]
        return ui.col(widget, *[ui.row(*actions[i:i + 5]) for i in range(0, len(actions), 5)])

    def content_label(self, text, **style):
        return self.mixed_label(ContentText(str(text)), **style)

    def _attempt_key(self):
        return (self.session_id, self.question_index)

    def _content_page_key(self):
        # Page builders create labels before screen() commits the new identity.
        key = getattr(self, "_render_key", None) or self._view_key
        return ("show_kana_detail", key[1]) if key and key[0] == "show_kana_readings" else key

    def _mark_lookup_assisted(self):
        key = self._attempt_key()
        pending_question = self.page in QUESTION_PAGES or any(
            page.name in QUESTION_PAGES and (page.bindings.get("session_id"), page.bindings.get("question_index")) == key
            for page in self.back_stack.pages
        )
        if self.session_id is not None and pending_question and key not in self._answered_attempts:
            self._assisted_attempts.add(key)
            self.answer_revealed = True
            self._checkpoint()
            if self.page in QUESTION_PAGES:
                self.question_notice.text = ("已查看学习提示，本题不计入无提示回忆。"
                    if self.question.kind in WORD_PRACTICE_KINDS else "已查看学习提示，本题仅安排再学一次。")

    def _preview_link(self, key, title, message, open_detail, *, assisted=False, action="查看详情"):
        """Show a terminal text preview; only its explicit action may navigate."""
        if key == self._content_page_key() or self._preview_pending is not None:
            return
        if self.page in PRACTICE_PAGES:
            if assisted:
                self._mark_lookup_assisted()
            self._show_practice_preview(key, title, message, open_detail,
                                        action="详细" if action == "查看详情" else action)
            return
        pending = self._preview_pending = object()
        page_token, origin_key = self._page_token, self._view_key

        async def show():
            try:
                if self._preview_pending is not pending or self._page_token != page_token:
                    return
                if assisted:
                    self._mark_lookup_assisted()
                pitch = None
                if key[0] == "show_word_detail":
                    preferences = KanaMemory._pitch_preferences(self)
                    word = self.repo.get_word(key[1])
                    if isinstance(word, dict) and any(preferences.values()):
                        pitch = {"record": word, "preferences": preferences}
                accepted = await self.platform.preview_index(title, message, action=action,
                    **({"pitch": pitch} if pitch else {}))
                if (accepted and self._preview_pending is pending and self._page_token == page_token
                        and self._view_key == origin_key and key != self._content_page_key()):
                    open_detail()
            except Exception as exc:
                if self._preview_pending is pending and self._page_token == page_token:
                    self.alert(str(exc))
            finally:
                if self._preview_pending is pending:
                    self._preview_pending = None
        self.loop.create_task(show())

    def _show_practice_preview(self, key, title, message, open_detail=None, *, action="详细"):
        """Keep practice explanations in the page, with one explicit detail action."""
        slot = self.practice_preview_slot
        origin_page, attempt = self.page, self._attempt_key()
        card = None

        def active():
            return (self.page == origin_page and self._attempt_key() == attempt
                    and self.practice_preview_slot is slot and card in slot.children)

        def open_selected(widget=None, **kwargs):
            if active() and key != self._content_page_key():
                open_detail()

        def collapse(widget=None, **kwargs):
            if active():
                slot.clear()

        actions = [ui.button(action, open_selected, width=52 if action == "详细" else 76,
                          compact=True, font_size=13, margin_left=4, margin_bottom=0)] if open_detail else []
        actions.append(ui.button("收起", collapse, width=48, compact=True, font_size=13,
                              margin_left=4, margin_bottom=0))
        summary = ui.col(ui.label(title, size=11, color=MUTED, margin_bottom=2),
                      ui.label(message, size=13, margin_bottom=0), flex=1)
        content = ui.col(ui.row(summary, *actions), padding=8, margin_bottom=8,
                      background_color=ENTRY_BACKGROUNDS[1])
        if key and key[0] == "show_word_detail":
            word = self.repo.get_word(key[1])
            if isinstance(word, dict):
                content.add(KanaMemory._pitch_slot(self, word, size=15, compact=True))
        # These pages reserve the remaining height for a keyboard or ink canvas.
        card = (toga.ScrollContainer(content=content, horizontal=False, style=Pack(height=120))
                if self.page in {"word_question", "ink_question"} else content)
        slot.clear()
        slot.add(card)

    def open_content_kana(self, kana_id):
        key = ("show_kana_detail", kana_id)
        if key == self._content_page_key():
            return
        item = self.repo.get_kana(kana_id)
        if item:
            preview = compact_kana_preview(item) if self.page in PRACTICE_PAGES else kana_preview(item)
            self._preview_link(key, "假名速览", preview, lambda: self.show_kana_detail(kana_id), assisted=True)

    def open_table_kana(self, kana_id):
        """The kana table is the one direct-navigation index."""
        if ("show_kana_detail", kana_id) == self._content_page_key():
            return
        if self.repo.get_kana(kana_id):
            self._mark_lookup_assisted()
            self.show_kana_detail(kana_id)

    def open_content_word(self, word_id):
        key = ("show_word_detail", word_id)
        if key == self._content_page_key():
            return
        word = self.repo.get_word(word_id)
        if word:
            preview = compact_word_preview(word) if self.page in PRACTICE_PAGES else word_preview(word)
            self._preview_link(key, "词条速览", preview, lambda: self.show_word_detail(word_id), assisted=True)

    def open_content_source(self, source):
        key = ("show_source", source.get("id", source.get("title")))
        self._preview_link(key, "来源速览", source_preview(source), lambda: self.show_source(source), assisted=True)

    def word_link(self, word, reading=None):
        reading = reading or word["reading"]
        reading_view = KanaMemory._pitch_slot(self, word, reading=reading, size=18,
            plain=True, compact=True, link_kana=True)
        description = ui.col(flex=1, margin_right=6)
        if word["form"] != reading:
            description.add(self.content_label(word["form"], size=18, bold=True, text_role="study"))
        description.add(reading_view, ui.label(romanize_kana(reading), size=14, color=GREEN))
        children = [description]
        if ("show_word_detail", word["id"]) != self._content_page_key():
            children.append(ui.button("词条", lambda w, wid=word["id"], **kw: self.open_content_word(wid), width=68))
        return ui.col(ui.row(*children), margin_bottom=4)

    @page_view(replace=True)
    def show_home(self, widget=None, **kwargs):
        if (getattr(self, "page", None) in FOCUSED_PAGES and
                not getattr(self, "_returning_home", False)):
            KanaMemory.request_practice_exit(self)
            return
        if KanaMemory._activate_practice_lane(self, "main") is False:
            return
        # The study-path home supplies its own progress text below. Do not read
        # the entire review history for a legacy summary that is never shown.
        summary = "" if getattr(self, "_study_path_enabled", False) else self._stats_summary(self.store.stats())
        self.home_stats_label = ui.label(summary, size=14,
                                      margin_bottom=0)
        self.home_action_slot = ui.col(padding=16, margin_bottom=12, background_color=WHITE)
        self.home_secondary_slot = ui.col(margin_bottom=8)
        self.home_material_slot = ui.col(margin_bottom=8)
        self.refresh_home_actions()
        self.refresh_shared_material_notice()
        self.screen("home", "今日学习",
            self.home_action_slot, self.home_secondary_slot, self.home_material_slot,
            self.entry_card(ui.label("学习概况", size=14, color=MUTED, bold=True),
                            self.home_stats_label),
            ui.button("针对性练习 · 问 AI", self.show_tutor_center, multiline=True),
            ui.button("继续掌握检查" if isinstance(self.store.get_setting('retention_active_v2', None), str) else "检查掌握情况", self.show_retention_check_center, multiline=True),
            ui.button("学习记录", self.show_progress, multiline=True),
        )
        KanaMemory._arm_home_review_refresh(self)

    def refresh_home_actions(self):
        if getattr(self, "_study_path_enabled", False):
            self.refresh_study_home()
            return
        self.home_action_slot.clear()
        self.home_secondary_slot.clear()
        pending = self.store.load_session_progress()
        availability = summarize_queue_availability(self.store, pending,
            allowed_kinds=KanaMemory._queue_kinds(self, pending) if pending else None)
        remaining, waiting = availability.ready_count, availability.waiting_count
        self._home_queue_due = (app_clock.localize(datetime.fromisoformat(availability.next_due_utc))
                                if availability.next_due_utc else None)
        wait_hint = ""
        if waiting:
            due = self._home_queue_due
            when = due.strftime("%H:%M:%S" if due.date() == app_clock.local_now().date()
                                else "%m月%d日 %H:%M:%S")
            wait_hint = f"待到期 {waiting} 题，最早 {when} 可复习。"
        titles = {value: key for key, value in PRACTICE_KINDS.items()}
        # The shortcut opens adaptive mixed practice, whose enabled directions
        # are the saved mixed selection, even after using a single-kind mode.
        # A different direction is a separate card; it cannot discharge a
        # disabled direction's due review merely by sharing the same kana.
        preferences = KanaMemory._clean_preferences(getattr(self, "_practice_preferences", {}))
        enabled = set(preferences["mixed_kinds"])
        due = [card for card in self.store.get_due() if card["skill"] not in WORD_PRACTICE_KINDS]
        # Count only cards the current exercise builder can actually offer.
        # Imported old/unknown content and ambiguous handwriting are excluded.
        from .domain import _eligible_kana
        kana_records = {item["id"]: item for item in self.repo.kana_list()} if due else {}
        categories = {category for values in PRACTICE_SCOPES.values() for category in values}
        due_count = 0
        for card in due:
            skill, script = card["skill"], card["script"]
            if skill not in enabled or skill not in titles or script not in {"hiragana", "katakana"}:
                continue
            item = kana_records.get(card["content_id"])
            if (item is None or item.get("category") not in categories or
                    not _eligible_kana(item, script, True, audio_only=skill == "kana_handwriting", kind=skill)):
                continue
            due_count += 1
        if remaining:
            title = "接着上次学"
            hint = wait_hint or "未完成题目和复习时间已保存。"
            caption, handler = f"继续上次练习（可答 {remaining} 题）", self.resume_practice
        elif waiting:
            title, hint = "等待下次复习", "现在可答 0 题。" + wait_hint
            caption, handler = f"查看复习安排（{waiting} 题待到期）", self.resume_practice
        elif availability.content_blocked_count:
            title, hint = '题目内容已更新', '原题和草稿已保存，可更新待练题目。'
            caption, handler = '更新待练题目', self.resume_practice
        elif availability.blocked_count:
            title, hint = "先完成前置假名", f"{availability.blocked_count} 题等待前置假名，原题与进度已保留。"
            caption, handler = "继续学习", self.resume_practice
        elif due_count:
            title, hint = "该复习了", "先巩固已学假名，再开始新内容。"
            caption = f"开始复习（{due_count} 张卡到期）"
            handler = lambda w, **kw: self.show_due_practice({"research": False, "due": due_count})
        else:
            title, hint = "开始今天的学习", "选择假名范围、数量和题型，再开始练习。"
            caption, handler = "开始练习", self.show_practice_setup
        self.home_action_slot.add(
            ui.label(title, size=22, bold=True, color=GREEN),
            ui.label(hint, size=13, color=MUTED, margin_bottom=16),
            ui.button(caption, handler, primary=True, multiline=True, margin_bottom=0),
        )
        if remaining or waiting or availability.blocked_count or availability.content_blocked_count or due_count:
            self.home_secondary_slot.add(ui.row(
                ui.button("学习新假名", self.show_new_batch, flex=1, multiline=True,
                       margin_right=8, font_size=13),
                ui.button("练习设置", self.show_practice_setup, flex=1, multiline=True, font_size=13),
            ))
        word_pending = self.store.load_session_progress(lane="word_romaji")
        word_count = KanaMemory._word_drill_remaining(self, word_pending)
        word_caption = (f"继续单词拼写（剩 {word_count} 词）" if word_count else
            '更新待练单词' if KanaMemory._word_drill_needs_update(self, word_pending) else '单词拼写')
        self.home_secondary_slot.add(ui.button(word_caption,
            lambda w, **kw: KanaMemory.open_home_word_drill(self), multiline=True))
        if hasattr(self, "learning_store"):
            counts = self.learning_store.counts(self.learning_preferences()["enabled"])
            if counts.get("next_due_at_utc"):
                learning_due = app_clock.localize(datetime.fromisoformat(counts["next_due_at_utc"]))
                self._home_queue_due = min(self._home_queue_due, learning_due) if self._home_queue_due else learning_due
            if counts["ready"] or counts["instruction"]:
                caption = ("继续学习新内容" if counts["instruction"] else f"继续课程学习（可答 {counts['ready']} 题）")
                self.home_action_slot.clear()
                self.home_action_slot.add(ui.label("接着学", size=22, bold=True, color=GREEN),
                    ui.label("已保留学习进度与复习时间。", size=13, color=MUTED),
                    ui.button(caption, self.continue_learning, primary=True, multiline=True))
                self.home_secondary_slot.add(ui.button("假名专项", handler, multiline=True))
            elif counts["pending"]:
                caption = "课程学习 · 查看安排"
                self.home_secondary_slot.add(ui.button(caption, self.continue_learning, multiline=True))
            else:
                caption = "学习课程"
                self.home_secondary_slot.add(ui.button(caption, self.show_learning_library, multiline=True))
            if counts["content"]:
                self.home_stats_label.text = "假名：" + self._stats_summary(self.store.stats()) + f"\n课程：已加入 {counts['content']} 项 · 现在可答 {counts['ready']} 题"
        KanaMemory._arm_home_review_refresh(self)

    def open_home_word_drill(self, widget=None, **kwargs):
        if self.page != "home":
            return
        # Re-read at activation so an old button cannot start over a saved
        # word queue or try to resume a checkpoint that has since completed.
        pending = self.store.load_session_progress(lane="word_romaji")
        if KanaMemory._word_drill_remaining(self, pending) or KanaMemory._word_drill_needs_update(self, pending):
            self.resume_word_drill()
        else:
            self.show_word_drill_setup()

    def _arm_home_review_refresh(self):
        if getattr(self, "page", None) != "home" or not hasattr(self, "loop"):
            return
        KanaMemory._cancel_review_wait(self)
        if getattr(self, "_home_queue_due", None):
            # Start on the next event-loop turn, as on the waiting page.
            self._review_wait_handle = self.loop.call_soon(
                KanaMemory._tick_home_review, self, self._page_token)

    def _tick_home_review(self, token):
        if self.page != "home" or self._page_token != token:
            return
        self._review_wait_handle = None
        due = self._home_queue_due
        delay = (due - app_clock.local_now(due.tzinfo)).total_seconds() if due else 0
        if delay <= 0:
            if hasattr(self, "home_stats_label"):
                self.home_stats_label.text = KanaMemory._stats_summary(self, self.store.stats())
            self.refresh_home_actions()
        else:
            # Recheck infrequently for clock changes, without saving progress.
            self._review_wait_handle = self.loop.call_later(
                min(60, delay), KanaMemory._tick_home_review, self, token)

    def show_due_practice(self, group):
        preferences = self._clean_preferences(getattr(self, "_practice_preferences", {}))
        preferences.update(kind="综合自适应练习", script="平假名与片假名（逐题分开）",
            scopes=list(PRACTICE_SCOPES), word_collection="全部可用词汇",
            batch=next(iter(PRACTICE_BATCHES)), schedule="间隔复习")
        preferences["research"] = False
        self.show_practice_setup(temporary_preferences=preferences)

    def show_new_batch(self, widget=None, **kwargs):
        preferences = self._clean_preferences(getattr(self, "_practice_preferences", {}))
        # A queue can contain several appended batches. Continue the latest
        # batch's settings, ignoring later timed repetitions of earlier ones.
        completed_question = (next((question for question in reversed(self.questions)
                                   if not question.metadata.get("not_before_utc")), self.questions[-1])
                              if self.page == "session_done" and getattr(self, "questions", None) else None)
        completed_config = completed_question.metadata.get("practice_config", {}) if completed_question else {}
        temporary_due = isinstance(completed_config, dict) and completed_config.get("temporary_due_shortcut") is True
        if (self.page == "session_done" and getattr(self, "questions", None)
                and not temporary_due):
            # A resumed session can have different settings from the last form
            # the learner opened. Continue the completed batch's own scope.
            question = completed_question
            preferences.update(script="片假名" if question.script == "katakana" else "平假名")
            config = question.metadata.get("practice_config")
            if isinstance(config, dict):
                categories = config.get("kana_categories")
                if isinstance(categories, list):
                    categories = {value for value in categories if isinstance(value, str)}
                    preferences["scopes"] = [title for title, values in PRACTICE_SCOPES.items()
                                             if set(values).intersection(categories)]
                elif categories is None and question.kind not in WORD_PRACTICE_KINDS:
                    preferences["scopes"] = list(PRACTICE_SCOPES if config.get("include_extended") else DEFAULT_PRACTICE_SCOPES)
                count = config.get("requested_count", len(self.questions))
                preferences["count"] = next((title for title, value in PRACTICE_COUNTS.items() if value == count), "自定义")
                preferences["custom_count"] = str(count)
                preferences["word_collection"] = next((title for title, value in PRACTICE_WORD_COLLECTIONS.items()
                                                         if value == config.get("word_collection")), "全部可用词汇")
                preferences["word_table"] = next((title for title, value in WORD_TABLES.items()
                                                   if value == config.get("word_table")), "部分假名（较容易）")
                preferences["research"] = config.get("include_research") is True
                preferences["rounds"] = str(config.get("rounds", 1))
                preferences["schedule"] = "间隔复习" if config.get("schedule_mode") == "spaced" else "多轮强化"
                requested_script = config.get("requested_script", question.script)
                preferences["script"] = next((title for title, value in PRACTICE_SCRIPTS.items()
                                                if value == requested_script), preferences["script"])
            else:
                preferences["rounds"] = "1"
        preferences["batch"] = "只学新内容"
        self._practice_preferences = self._clean_preferences(preferences)
        self.show_practice_setup()

    @staticmethod
    def _clean_preferences(value):
        return clean_preferences(value)

    @staticmethod
    def _enabled_preference_kinds(preferences):
        return enabled_preference_kinds(preferences)

    def _set_queue_kind_policy(self, session_id, kinds):
        if not isinstance(session_id, str) or not session_id:
            return
        regular = KanaMemory._enabled_preference_kinds(getattr(self, "_practice_preferences", {}))
        self.store.set_setting("practice_queue_kinds", {
            "session_id": session_id, "kinds": list(kinds), "regular_kinds": list(regular)})

    def _queue_kinds(self, pending):
        """Resolve the same effective policy for display and actual resumption."""
        if pending and pending.get("questions") and all(
                q.metadata.get("word_romaji_drill") is True for q in pending["questions"]):
            return list(KanaMemory._word_drill_kinds(self))
        regular = list(KanaMemory._enabled_preference_kinds(getattr(self, "_practice_preferences", {})))
        policy = self.store.get_setting("practice_queue_kinds", {})
        kinds = regular
        # A deliberately chosen temporary due plan can differ from the regular
        # defaults. A later change of those defaults supersedes its old policy.
        if (isinstance(policy, dict) and policy.get("session_id") == pending["session_id"]
                and policy.get("regular_kinds") == regular
                and isinstance(policy.get("kinds"), list)
                and all(isinstance(kind, str) and kind in ALL_MIXED_KINDS for kind in policy["kinds"])):
            kinds = policy["kinds"]
        return kinds

    def _lazy_practice_questions(self):
        return (getattr(self, "_study_path_enabled", False)
                and getattr(self.store, "practice_lane", "main") == "main")

    def _reconcile_practice_kinds(self, pending=None):
        lazy = KanaMemory._lazy_practice_questions(self)
        pending = (self.store.load_session_progress(lazy_questions=True) if lazy else
                   self.store.load_session_progress()) if pending is None else pending
        if not isinstance(pending, dict):
            return pending
        kinds = KanaMemory._queue_kinds(self, pending)
        if not kinds:
            self.alert("请至少启用一种提问方式，再继续练习。原题单与复习安排已保留。")
            return False
        # Re-enabled scheduled reviews can be in the skipped history prefix.
        # Reconcile even when every currently pending question is allowed.
        try:
            revised = (self.store.reconcile_session_kinds(pending["session_id"], kinds, lazy_questions=True)
                       if lazy else self.store.reconcile_session_kinds(pending["session_id"], kinds))
        except Exception as exc:
            self.alert("题型设置暂未应用，原练习已保留：" + error_message(exc))
            return False
        if not isinstance(revised, dict):
            return pending
        if revised.get("changed"):
            self.session_id, self.questions = revised["session_id"], revised["questions"]
            self.question_index, self.session_right = revised["question_index"], revised["session_right"]
            self.question = self.questions[self.question_index] if self.question_index < len(self.questions) else None
            self._assisted_attempts = {(self.session_id, index) for index in revised["assisted_indices"]}
            self._answered_attempts.clear()
            self.back_stack.discard(PRACTICE_PAGES)
            KanaMemory._cancel_review_wait(self)
            self.platform.stop()
            self._ink_revision = getattr(self, "_ink_revision", 0) + 1
            self._audio_serial = getattr(self, "_audio_serial", 0) + 1
        return revised

    def _checkpoint(self):
        if getattr(self, "_backup_busy", False):
            return False
        KanaMemory.save_material_reference_draft(self)
        if getattr(self, "page", "").startswith("ai_"):
            return
        if getattr(self, "page", "") in REPORT_PAGES:
            return
        if getattr(self, "page", None) in LEARNING_PAGES | {"learning_feedback_detail"}:
            return
        if getattr(self, "_restoring_practice", False) or not getattr(self, "session_id", None):
            return
        if getattr(self, "page", None) not in PRACTICE_PAGES:
            try:
                pending = (self.store.load_session_progress(lazy_questions=True)
                           if KanaMemory._lazy_practice_questions(self) else self.store.load_session_progress())
                if pending and pending["session_id"] == self.session_id:
                    assisted = [index for sid,index in self._assisted_attempts if sid == self.session_id]
                    from .session_content import fingerprint
                    index = pending["question_index"]
                    question = pending["questions"][index] if index < len(pending["questions"]) else None
                    self.store.save_session_progress(self.session_id, index, pending["session_right"], assisted, pending["state"],
                        question_fingerprint=fingerprint(question) if question is not None else None)
            except Exception as exc:
                self.alert("未完成练习暂未保存：" + error_message(exc))
                return False
            return
        if self.question_index >= len(self.questions):
            self.store.clear_session_progress()
            return
        state = {"page": self.page, "answer_revealed": bool(getattr(self, "answer_revealed", False)),
                 "feedback_pending": self.page == "feedback", "answered_text": getattr(self, "answered_text", None) or "",
                 "question_audio_ok": bool(getattr(self, "question_audio_ok", False)),
                 "use_full_keyboard": bool(getattr(self, "use_full_keyboard", False))}
        if self.page in {"session_wait", "dependency_wait"}:
            state = {"page": self.page, "use_full_keyboard": bool(getattr(self, "use_full_keyboard", False))}
        if self.page == "feedback" and getattr(self, "_answer_submitted_at", None):
            state.update(submitted_at_utc=self._answer_submitted_at,
                         answer_duration_ms=self._answer_duration_ms,
                         answer_correct=bool(self.answer_correct))
        if self.page not in {"session_wait", "dependency_wait"} and self.question.kind in KANA_WORD_PRACTICE_KINDS | {"romaji_kana_full"}:
            state["word_answer"] = getattr(self, "word_answer", "")
            if self.question.kind in WORD_PRACTICE_KINDS:
                state.update(word_conversion_source=getattr(self, "word_conversion_source", ""),
                    word_conversion_convention=getattr(self, "word_conversion_convention", "reading"),
                    word_used_conversion=getattr(self, "word_used_conversion", False) is True)
        if (self.page not in {"session_wait", "dependency_wait"} and self.question.kind == "word_romaji"
                and getattr(self, "word_romaji_unit_mode", False)):
            state["word_romaji_parts"] = list(self.word_romaji_parts)
            state["word_romaji_plan"] = self.word_romaji_plan
        if self.page not in {"session_wait", "dependency_wait"} and self.question.kind == "kana_romaji":
            state["romaji_answer"] = getattr(self, "romaji_answer", "")
        if self.page not in {"session_wait", "dependency_wait"} and self.question.kind in {"kana_handwriting", "romaji_handwriting"}:
            state.update(strokes=getattr(self, "strokes", []), ink_width=getattr(self, "ink_width", 360), ink_height=getattr(self, "ink_height", 210))
        assisted = [index for sid,index in self._assisted_attempts if sid == self.session_id]
        try:
            from .session_content import fingerprint
            self.store.save_session_progress(self.session_id, self.question_index, self.session_right, assisted, state,
                question_fingerprint=fingerprint(self.question))
        except Exception as exc:
            self.alert("未完成练习暂未保存：" + error_message(exc))
            return False

    def resume_practice(self, widget=None, *, preserve_audio=False, **kwargs):
        from .practice_round import begin
        begin(self.store, restart_finished=True)
        pending = KanaMemory._reconcile_practice_kinds(self)
        if pending is False:
            return
        if not pending:
            self.show_home()
            return
        self.session_id, self.questions = pending["session_id"], pending["questions"]
        self.question_index, self.session_right = pending["question_index"], pending["session_right"]
        if self.question_index >= len(self.questions):
            self.back_stack.discard(PRACTICE_PAGES)
            self.show_question()
            return
        self.script = self.questions[self.question_index].script
        self.research_enabled = any_question_metadata(self.questions, "research_content")
        state = pending["state"]
        self.use_full_keyboard = state.get("use_full_keyboard", False)
        self._assisted_attempts = {(self.session_id, index) for index in pending["assisted_indices"]}
        if state.get("answer_revealed"):
            self._assisted_attempts.add(self._attempt_key())
        self._answered_attempts.clear()
        self.back_stack.discard(PRACTICE_PAGES)
        self._restoring_practice = True
        try:
            original_question = self.questions[self.question_index]
            original_index = self.question_index
            if kwargs.get("_first_teaching"):
                self.show_question(_first_teaching=True,
                    _first_teaching_targets=kwargs.get("_first_teaching_targets"))
            else:
                self.show_question()
            if self.page not in QUESTION_PAGES:
                self._restoring_practice = False
                self._checkpoint()
                return
            if state.get("page") in {"session_wait", "dependency_wait"} or self.question_index != original_index or self.question != original_question:
                # Dispatch may have advanced past another question and
                # activated a parked word's draft. Restore that head's state,
                # never the state belonging to the question before dispatch.
                active = self.store.load_session_progress()
                state = (active["state"] if active and active["session_id"] == self.session_id
                         and active["question_index"] == self.question_index
                         and active["questions"][self.question_index] == self.question else {})
                if state.get("page") in {"session_wait", "dependency_wait"}:
                    state = {}
            if preserve_audio and state.get("question_audio_ok"):
                # Appending keeps the current attempt alive. A normal reopen
                # still asks for fresh playback before an unsubmitted answer.
                self.question_audio_ok = True
            if self.question.kind in KANA_WORD_PRACTICE_KINDS | {"romaji_kana_full"}:
                self.word_answer = state.get("word_answer", "")
                self.word_answer_label.text = ui.wrapped(self.word_answer or "＿", columns=18)
                if self.question.kind in WORD_PRACTICE_KINDS:
                    from .romaji_input_ui import restore_word_converter
                    restore_word_converter(self, state)
            elif self.question.kind == "word_romaji":
                if "word_romaji_parts" in state:
                    self.word_romaji_plan = word_romaji_unit_plan(self.question, state["word_romaji_plan"])
                    self.word_romaji_parts = list(state["word_romaji_parts"])
                    KanaMemory._refresh_word_romaji_units(self)
                elif state.get("feedback_pending"):
                    # Preserve already selected legacy whole-word feedback.
                    self.word_romaji_unit_mode = False
            elif self.question.kind == "kana_romaji":
                self.romaji_answer = state.get("romaji_answer", "")
                self.romaji_input.value = self.romaji_answer
            elif self.question.kind in {"kana_handwriting", "romaji_handwriting"}:
                self.strokes = state.get("strokes", [])
                points = [point for stroke in self.strokes for point in stroke]
                if points:
                    # A reboot/import changes the monotonic clock epoch. Keep
                    # stroke timing differences but allow new ink to follow it.
                    offset = int(time.monotonic() * 1000) - 1 - max(point[2] for point in points)
                    self.strokes = [[(x, y, timestamp + offset) for x,y,timestamp in stroke] for stroke in self.strokes]
                self.ink_width, self.ink_height = state.get("ink_width", 360), state.get("ink_height", 210)
                self._redraw_ink()
            if self.answer_revealed:
                self.question_notice.text = ("已查看答案或学习提示，本题不计入无提示回忆。"
                    if self.question.kind in WORD_PRACTICE_KINDS else "已查看答案或学习提示，本题仅安排再学一次。")
            if state.get("feedback_pending"):
                self.question_audio_ok = (self.question.kind not in LISTENING_PRACTICE_KINDS or state.get("question_audio_ok", False))
                self._answer_submitted_at = state.get("submitted_at_utc")
                self._answer_duration_ms = state.get("answer_duration_ms", 0)
                self.check_answer(state.get("answered_text", ""))
        finally:
            self._restoring_practice = False
        self._checkpoint()

    async def export_learning_backup(self, widget=None, **kwargs):
        from .backup_ui import export
        await export(self)

    async def export_previous_backup(self, widget=None, **kwargs):
        from .backup_ui import export
        await export(self, previous=True)

    async def import_learning_backup(self, widget=None, **kwargs):
        from .backup_ui import restore
        await restore(self)

    async def convert_legacy_learning_records(self, widget=None, **kwargs):
        from .backup_ui import convert_legacy
        await convert_legacy(self)

    async def delete_learning_data(self, widget=None, **kwargs):
        origin = self.page
        if (origin not in {"settings", "learning_data"} or getattr(self, "_deleting_learning_data", False)
                or getattr(self, "_backup_busy", False)):
            return
        self._deleting_learning_data = True
        token = self._page_token
        try:
            confirmed = await self.main_window.dialog(toga.ConfirmDialog("删除学习数据？",
                "将清空学习进度、答题记录、复习计划、未完成练习及暂停／暂缓标记。\n"
                "练习设置、词库、语音与手写模型、备份文件会保留。\n"
                "此操作无法撤销；需要保留记录，请先取消并导出学习备份。"))
            if (not confirmed or self.page != origin or self._page_token != token
                    or getattr(self, "_backup_busy", False)):
                return
            self.store.clear_learning_data()
        except Exception as exc:
            self.alert("未能删除学习数据：" + error_message(exc))
            return
        finally:
            self._deleting_learning_data = False
        # Invalidate retained exercise callbacks and views before another page
        # can checkpoint or score the deleted batch back into the learner DB.
        KanaMemory._cancel_practice_start(self)
        self._ink_revision += 1
        self._audio_serial += 1
        self.platform.stop()
        self.session_id, self.questions, self.question = None, [], None
        self.question_index = self.session_right = 0
        self._assisted_attempts.clear()
        self._answered_attempts.clear()
        self.strokes, self._active_stroke = [], None
        self._preview_pending = None
        self._practice_context = False
        self.back_stack.pages.clear()
        if origin == "learning_data":
            self.show_learning_data()
        else:
            self.show_settings()
        self.back_stack.pages.clear()
        self.alert("学习数据已删除，可以重新开始学习。练习设置和已安装资源已保留。")

    def _stats_summary(self, stats):
        return f"已学内容 {stats.get('total_items', stats.get('cards', stats.get('total_cards', 0)))} 项　到期 {stats.get('due_items', stats.get('due', stats.get('due_count', 0)))} 项\n今日新增 {stats.get('new_items_today', stats.get('new_today', 0))} 项 · 作答 {stats.get('today_reviews', stats.get('reviews_today', 0))} 次"

    def model_label(self):
        return {"ready": "已下载，可离线识别", "checking": "正在检查日语模型…", "not_downloaded": "尚未下载日语模型", "downloading": "正在下载日语模型…", "error": "模型尚未就绪", "unavailable": "请在安卓版本使用"}.get(self.model_state, self.model_state)

    def _services_summary(self):
        return f"手写模型：{self.model_label()}\n日语语音：{'可用（合成音）' if self.platform.tts_ready else '尚未就绪'}"

    def model_controls(self, compact=False):
        children = [ui.label("手写模型：" + self.model_label(), size=12, color=GREEN, bold=True)]
        if self.model_state == "ready" and compact:
            return ui.col(*children)
        if self.model_message:
            children.append(ui.label(self.model_message, size=12, color=MUTED))
        elif self.model_state == "not_downloaded":
            children.append(ui.label("首次联网下载，完成后可离线识别。", size=12, color=MUTED))
        elif self.model_state == "downloading":
            children.append(ui.label("请保持联网；完成后会自动启用识别。", size=12, color=MUTED))
        actions = []
        if self.model_state not in {"ready", "unavailable"}:
            title = {"downloading": "模型下载中…", "checking": "检查模型中…", "error": "重试下载日语模型"}.get(self.model_state, "下载日语手写模型")
            download = ui.button(title, self.download_model, primary=True)
            download.enabled = self.model_state not in {"downloading", "checking"}
            actions.append(download)
        if self.model_state not in {"downloading", "checking", "unavailable"}:
            actions.append(ui.button("重新检查模型", self.check_model))
        # Full-width buttons keep the download action readable on narrow phones.
        return ui.col(*children, *actions, margin_bottom=4)

    def refresh_model_ui(self):
        if self.page == "settings":
            self.model_panel.clear()
            self.model_panel.add(self.model_controls())
        elif self.page in INK_PAGES:
            self.ink_model_panel.clear()
            self.ink_model_panel.add(self.model_controls(compact=True))
            KanaMemory._refresh_ink_action(self)
        elif self.page == "practice_setup":
            self.refresh_practice_setup()

    async def check_model(self, widget=None, **kwargs):
        if self._model_downloading or self._model_checking:
            return
        self._model_checking = True
        self._model_request += 1
        request = self._model_request
        self.model_state, self.model_message = "checking", ""
        self.refresh_model_ui()
        try:
            state = await self.platform.model_status()
            if request == self._model_request:
                self.model_state = state
        except asyncio.CancelledError:
            if request == self._model_request:
                self.model_state, self.model_message = "error", "检查已中断，请重新检查模型。"
            raise
        except Exception as exc:
            if request == self._model_request:
                self.model_state, self.model_message = "error", error_message(exc)
        finally:
            self._model_checking = False
            self.refresh_model_ui()

    @page_view()
    def show_kana(self, widget=None, **kwargs):
        choices = ["全部", "基础假名（含浊音、半浊音）"] + [title for category, title in CATEGORY_LABELS.items()
            if category not in {"basic", "dakuten", "handakuten"}]
        self.kana_filter = ui.selection(items=choices, on_change=self.refresh_kana, style=Pack(margin_bottom=12))
        self.kana_grid = ui.col()
        large_table = ui.font_scale() > 1.2
        table = (toga.ScrollContainer(content=self.kana_grid, horizontal=True, style=Pack(flex=1))
                 if large_table else self.kana_grid)
        guidance = [ui.label("可横向滑动表格", size=12, color=MUTED)] if large_table else [
                    ui.label("横向为元音，纵向按辅音行排列。点按字形查看详情。", size=12, color=MUTED),
                    ui.label("行名沿用五十音分组；し、ち、つ等以格内罗马音为准。", size=12, color=MUTED)]
        self.screen("kana", "假名表", self.kana_filter, *guidance,
                    ui.button("历史字音资料目录", self.open_character_catalog), table, scroll=not large_table)
        self.refresh_kana()

    def refresh_kana(self, widget=None, **kwargs):
        if not hasattr(self, "kana_grid"):
            return
        category = next((k for k, v in CATEGORY_LABELS.items() if v == self.kana_filter.value), None)
        items = self.repo.kana_list(category=category)
        if self.kana_filter.value == "基础假名（含浊音、半浊音）":
            items = [item for item in items if item["category"] in {"basic", "dakuten", "handakuten"}]
        grid = self.kana_grid
        grid.clear()
        task = getattr(grid, "_kana_render_task", None)
        if task:
            task.cancel()
        rows = KanaMemory._kana_table_rows(self, items)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if current_platform != "android" or loop is None:
            grid.add(*rows)
            return
        status = ui.label("正在展开全部假名…", size=12, color=MUTED)
        grid.add(status)

        async def render():
            started = time.perf_counter()
            children = []
            # Let the new page paint, then yield between native row builds.
            # Everything expands automatically, without a pagination control.
            await asyncio.sleep(0.02)
            try:
                for child in rows:
                    children.append(child)
                    await asyncio.sleep(0)
                grid.clear()
                grid.add(*children)
                print(f"Kana table ready: {len(items)} items in {time.perf_counter() - started:.3f}s")
            except asyncio.CancelledError:
                return
        task = loop.create_task(render())
        grid._kana_render_task = task
        if not hasattr(self, "_kana_render_tasks"):
            self._kana_render_tasks = set()
        self._kana_render_tasks.add(task)
        task.add_done_callback(self._kana_render_tasks.discard)

    def _kana_table_rows(self, items):
        # Reserve a narrow row-name gutter and five equal vowel columns. Compact
        # native buttons have no horizontal padding, so combinations fit phones.
        longest_form = max((len(item.get(script) or "") for item in items
                            for script in ("hiragana", "katakana")), default=1)
        cell_width = max(round((longest_form * 17 * 96 / 72 + 4) * ui.font_scale()) if ui.font_scale() > 1.2 else 42,
                         int((self.main_window.size.width - 86) / 5))
        gutter_width = round(30 * ui.font_scale())

        def cell(entries, background):
            children = []
            for item in entries:
                forms = list(dict.fromkeys(f for f in (item.get("hiragana"), item.get("katakana")) if f))
                children.append(ui.button("\n".join(forms), lambda w, kid=item["id"], **kw: self.open_table_kana(kid),
                                       width=cell_width, height=74 if len(forms) > 1 else 46,
                                       font_size=17, margin_bottom=0, multiline=True, text_role="study",
                                       background_color=background, compact=True))
                children[-1]._feedback_fragment = {"kind": "kana_table_cell", "target_id": item["id"],
                    "forms": list(forms), "category": item["category"], "romaji": item.get("romaji")}
                children.append(ui.label(kana_learning_romaji(item) or "—", size=12, wrap=False,
                                      text_align="center", color=MUTED, margin_bottom=6))
            return ui.col(*children, width=cell_width, margin=2, background_color=background)

        history_heading = False
        for section in merge_voicing_sections(build_kana_table(items)):
            if section.historical and not history_heading:
                yield self.heading("历史字形 · 现代通常不用")
                yield ui.label("单独保留历史资料；不混入上方现代假名。", size=12, color=MUTED)
                history_heading = True
            yield self.heading(section.title)
            if section.rows:
                yield ui.row(ui.label("行", width=gutter_width, size=12, wrap=False, color=MUTED),
                    *[ui.label(vowel, width=cell_width, margin=2, size=15, bold=True,
                            wrap=False, text_align="center", color=GREEN) for vowel in VOWELS])
                for index, spec in enumerate(section.rows):
                    background = ENTRY_BACKGROUNDS[index % len(ENTRY_BACKGROUNDS)]
                    yield ui.row(ui.label(spec.label, width=gutter_width, size=12,
                                              wrap=False, color=GREEN, bold=True, margin_top=14),
                                        *[cell(entries, background) for entries in spec.cells])
            if section.extras:
                if section.key == "basic":
                    yield ui.label("拨音（不归入五个元音列）", size=12, color=MUTED)
                for start in range(0, len(section.extras), 5):
                    entries = section.extras[start:start + 5]
                    cells = [cell((item,), ENTRY_BACKGROUNDS[(start // 5) % len(ENTRY_BACKGROUNDS)]) for item in entries]
                    cells.extend(ui.col(width=cell_width, margin=2) for _ in range(5 - len(cells)))
                    yield ui.row(ui.col(width=gutter_width), *cells)

    def source_buttons(self, source_ids):
        result = []
        for sid in dict.fromkeys(source_ids):
            if ("show_source", sid) == self._content_page_key():
                continue
            source = self.source_map.get(sid) or self.repo.source(sid)
            if source:
                result.append(ui.button("来源 · " + short(source.get("title", sid), 22), lambda w, s=source, **kw: self.open_content_source(s),
                                     background_color=ENTRY_BACKGROUNDS[len(result) % len(ENTRY_BACKGROUNDS)]))
        return result

    def recording_controls(self, data):
        children = []
        for audio in data.get("recordings", []):
            if not audio_file_available(audio):
                continue
            children.extend([
                ui.row(ui.label(ui.wrapped(f"真人录音 · {audio['author']} · {audio['license']}", columns=32), size=12, color=MUTED, flex=1, wrap=False),
                    ui.speaker_button(lambda w, a=audio, **kw: self.play_recording(a), "播放真人录音：" + audio.get("text", ""))),
                ui.button("录音出处", lambda w, a=audio, **kw: self.open_url(a["source_url"], title="录音出处", summary=a.get("text", ""))),
            ])
        return children

    def japanese_reading(self, text, *, size=23, romanized=True, word=None):
        # Reserve horizontal room for the speaker before laying out long content.
        lines = ([KanaMemory._pitch_slot(self, word, reading=text, size=size, plain=True,
                    link_kana=True, show_missing=True)] if word is not None else
                 [self.content_label(text, size=size, bold=True, columns=20, text_role="study")])
        if romanized:
            lines.append(ui.label(ui.wrapped(romanize_kana(text), columns=26), size=16, color=GREEN, wrap=False))
        return ui.row(ui.col(*lines, flex=1), ui.speaker_button(lambda w, t=text, **kw: self.speak_text(t),
                                                     "播放日语：" + text))

    @page_view(detail=True)
    def show_kana_detail(self, kana_id):
        item = self.repo.get_kana(kana_id)
        data = payload(item)
        origins = item.get("origins", [])
        combination = is_kana_combination(item)
        children = []
        source_ids = list(data.get("source_ids", []))
        if not combination:
            cards, image_sources = self.origin_image_cards(item, compact=True)
            children.extend(cards)
            source_ids.extend(image_sources)
            for origin in origins:
                source_ids.extend(origin_source_ids(origin))
        overview = self.kana_audio_overview(item)
        height = getattr(getattr(getattr(self, "main_window", None), "size", None), "height", 1000)
        if ui.font_scale() > 1.2 and height < 1000:
            # The taller two-panel comparison and wrapped labels otherwise push
            # a dense fifth sound row behind navigation at 200% on phone screens.
            children.insert(0, overview)
        else:
            children.append(overview)
        from .kana_diagrams import kana_diagram
        from .lesson_diagram_ui import diagram_widget
        for script in ('hiragana', 'katakana'):
            diagram = kana_diagram(item, script)
            if diagram:
                children.append(diagram_widget(ui, diagram))
        if data.get("special_context"):
            children.append(self.entry_card(ui.label("用法提示", size=12, color=MUTED, bold=True),
                                           self.content_label("；".join(str(s) for s in data["special_context"]), size=15)))
        if combination:
            children.append(self.heading("组合方式"))
            components = data.get("components", [])
            children.append(ui.label("点按组成部分可先查看假名速览。" if components else "该项的单独字源尚未采集。", size=12, color=MUTED))
            for component_id in dict.fromkeys(components):
                if component_id == kana_id:
                    continue
                component = self.repo.get_kana(component_id)
                if component:
                    children.append(ui.button(f"组成：{component.get('hiragana') or ''} {component.get('katakana') or ''}", lambda w, cid=component_id, **kw: self.open_content_kana(cid)))
        children.append(self.heading("例词索引"))
        words = item.get("words") or self.repo.kana_words(kana_id, limit=10)
        if any(word.get("kana_match_kind") == "component_of_combination" for word in words[:10]):
            children.append(ui.label("标有“组合相关”的词仅含组合／变体中的字形。", size=12, color=MUTED))
        for index, word in enumerate(words[:10]):
            reading = word.get("matched_reading") or word["reading"]
            entry = [self.word_link(word, reading)]
            if word.get("kana_match_kind") == "component_of_combination":
                entry.append(ui.label("组合相关", size=12, color=MUTED))
            children.append(self.entry_card(*entry, index=index))
        if not words:
            children.append(ui.label("该项的合适例词仍待整理。", color=MUTED))
        children.append(ui.button("生成例句", lambda w: self.show_ai_form("examples", target_id=kana_id), compact=True))
        children.append(ui.button("共享例句", lambda w: self.show_item_examples(kana_id), compact=True))
        if not combination and any(image.review_note for image in self.origin_images.for_kana(item)):
            children.append(self.disclosure("字形核查与待核示意", lambda: self.origin_review_cards(item)))
        if source_ids:
            children.append(self.disclosure("字形与词例来源", lambda: self.source_buttons(source_ids)))
        self.screen("kana_detail", "假名详情", *children)

    @page_view(detail=True)
    def show_kana_readings(self, kana_id):
        item = self.repo.get_kana(kana_id)
        if not item or is_kana_combination(item):
            return
        self.screen("kana_readings", "读音对照", self.kana_audio_overview(item))

    @page_view()
    def show_words(self, widget=None, **kwargs):
        self.word_offset = 0
        self.word_query = toga.TextInput(placeholder="汉字、假名、罗马音或释义", style=Pack(flex=1, margin_right=6))
        self.word_collection = ui.selection(items=["全部词汇", "基础听词", "英语借词候选", "核心词候选", "日常补充"], on_change=self.reset_words, style=Pack(margin_bottom=8))
        self.word_results = ui.col()
        self.word_page_label = ui.label("", color=MUTED, size=12)
        self.word_help = ui.col()
        self.word_previous_button = ui.button("上一页", lambda w, **kw: self.page_words(-1), flex=1, margin_right=6)
        self.word_next_button = ui.button("下一页", lambda w, **kw: self.page_words(1), flex=1)
        self.screen("words", "词库与例句", ui.button("单词拼写", self.show_word_drill_setup, primary=True), ui.row(self.word_query, ui.button("搜索", self.reset_words, width=68)), self.word_collection,
                    ui.button("搜索与资料说明", self.toggle_word_help), self.word_help, self.word_page_label, self.word_results,
                    ui.row(self.word_previous_button, self.word_next_button))
        self.refresh_words()

    def toggle_word_help(self, widget=None, **kwargs):
        if self.word_help.children:
            self.word_help.clear()
        else:
            self.word_help.add(self.note("可搜索不同写法、读音及罗马音。点假名或词条先看摘要，再选择详情。基础词的主要读音经 AI 辅助核对；其他词汇和词源、音译、例句保留审核状态。"))

    def reset_words(self, widget=None, **kwargs):
        if not hasattr(self, "word_results"):
            return
        self.word_offset = 0
        self.refresh_words()

    def page_words(self, direction):
        if direction > 0 and not self.word_has_next:
            return
        self.word_offset = max(0, self.word_offset + 30 * direction)
        self.refresh_words()

    def refresh_words(self):
        collection = {"英语借词候选": "english_loanwords", "核心词候选": "japanese_core", "基础听词": "foundation_listening", "日常补充": "everyday_supplement"}.get(self.word_collection.value)
        result = self.repo.word_list_page(self.word_query.value or "", limit=30, offset=self.word_offset, collection=collection)
        self.current_words, self.word_has_next = result["words"], result["has_next"]
        self.word_previous_button.enabled = self.word_offset > 0
        self.word_next_button.enabled = self.word_has_next
        # Build detached cards before updating the live container. Toga refreshes
        # the entire layout on every add(); one add avoids repeatedly measuring
        # all preceding pitch cells for every word on this page.
        cards = [ui.col(self.word_link(word), self.content_label(short(word.get("gloss", ""), 120),
                     size=12, color=MUTED), margin_bottom=6) for word in self.current_words]
        if not cards:
            cards = [ui.label("没有找到对应词条。可试试其他写法、假名、罗马音或释义。")]
        self.word_results.clear()
        self.word_page_label.text = f"第 {self.word_offset // 30 + 1} 页 · 本页 {len(self.current_words)} 词"
        self.word_results.add(*cards)

    @page_view(detail=True)
    def show_word_detail(self, word_id):
        word = self.repo.get_word(word_id)
        data = payload(word)
        forms = "・".join(k["text"] for k in data.get("kanji", []))
        form_entries, primary_reading_notes = [], []
        if forms:
            form_entries.append(self.entry_card(ui.label("收录字形", size=12, color=MUTED), self.content_label(forms, size=15, text_role="study")))
        for index, reading in enumerate(data.get("readings", [])):
            lines = [ui.label("读法", size=12, color=MUTED), self.japanese_reading(reading["text"], size=20, word=word)]
            if reading.get("no_kanji"):
                lines.append(ui.label("假名表记；词典未关联上述汉字字形。", size=12, color=MUTED))
            if reading.get("applies_to_kanji"):
                lines.append(self.mixed_label("仅对应：", ContentText("・".join(reading["applies_to_kanji"])), size=12, color=MUTED, study_content=True))
            if reading.get("info"):
                lines.append(self.content_label("、".join(reading["info"]), size=12, color=MUTED))
            if reading["text"] == word["reading"]:
                # The primary pronunciation already has a card at the top.
                # Keep its spelling/use restrictions there instead of repeating it.
                primary_reading_notes.extend(lines[2:])
            else:
                form_entries.append(self.entry_card(*lines, index=index + 1))
        children = [self.content_label(word["form"], size=34, color=GREEN, bold=True, text_role="study"),
                    self.entry_card(self.japanese_reading(word["reading"], word=word), *primary_reading_notes, *self.recording_controls(data)),
                    self.note(("基础词主要读音与简明词义经 AI 辅助核对，可用于听词练习；其他资料保留原审核状态。" if data.get("practice_approved") is True else "研究候选，尚待教学审核；中文释义含 AI 辅助译文。") + "罗马音按假名离线转换，不标音调。"),
                    self.heading("日语词义与词性"), *self.detail_entries(sense_entries(word)),
                    self.heading("字形与读法"), *form_entries,
                    self.mixed_label("长音符 → ā / ī / ū / ē / ō；", ContentText("おう"), "、", ContentText("えい"), "保留 ou、ei。", size=12, color=MUTED),
                    self.heading("词源与英语原词")]
        if hasattr(self, "learning_store"):
            children.insert(2, ui.button("加入词汇学习", lambda w: self.open_word_learning(word), primary=True))
        origin = data.get("origin", {})
        english_candidates = data.get("english_review_candidates") or []
        expression = "; ".join(dict.fromkeys(c["english_source_expression"] for c in english_candidates if c.get("english_source_expression")))
        expressions = origin.get("source_expressions", [])
        if expression:
            children.append(self.entry_card(ui.label("英语来源形式", size=12, color=MUTED), self.content_label(expression, size=20, bold=True, color=GREEN)))
        elif expressions:
            children.append(self.entry_card(ui.label("词典来源表达", size=12, color=MUTED), self.content_label("; ".join(str(x.get("text", x)) if isinstance(x, dict) else str(x) for x in expressions), size=20, bold=True, color=GREEN)))
        else:
            children.append(ui.label("语源仍待核实。包含汉字不等于借自汉语。", color=MUTED))
        entries = japanese_entries(data, include_pitch=False)
        if entries:
            children.append(self.heading("发音与语源资料"))
            children.extend(self.detail_entries(entries))
        if data.get("english_enrichment") or data.get("japanese_enrichment", {}).get("same_spelling_chinese_reference"):
            children.append(ui.button("英语原词与汉语参照", lambda w, wid=word_id, **kw: self.open_word_references(wid)))
        characters = list(dict.fromkeys(c for c in forms if '\u3400' <= c <= '\u9fff'))
        available = [(c, self.repo.dialects_for_hanzi(c)) for c in characters]
        available = [(c, readings) for c, readings in available if readings]
        if available:
            children += [self.heading("汉字的方言对照"), self.note("可展开各字的现代单字音；不是整词读音，不能据此拼出吴语连读变调，也不证明日语词源。")]
            for character, readings in available:
                children.append(ui.button("字音参照 · " + character, lambda w, c=character, **kw: self.open_character_readings(c)))
        children.append(self.heading("日语例句"))
        examples = word.get("examples") or self.repo.word_examples(word_id)
        if any(not example.get("review") and payload(example).get("content_origin") == "ai_generated" for example in examples):
            children.append(self.note("AI 例句及中文译文待人工校订；所标义项为编写目标，尚未获教学审核。"))
        if any(not example.get("review") and payload(example).get("content_origin") != "ai_generated" for example in examples):
            children.append(self.note("未标义项的来源例句尚未对齐词义；检索候选的句中读法、义项待核。"))
        for index, example in enumerate(examples):
            if example.get("review"):
                children.append(self.lexical_example_card(example, word_id, index))
                continue
            info = payload(example)
            generated = info.get("content_origin") == "ai_generated"
            badge = "AI 例句" if generated else "来源例句"
            sense_id = example.get("matched_sense_id") or info.get("target_sense_id")
            if sense_id:
                badge += (" · 目标义项 " if generated else " · 义项 ") + str(sense_id).rsplit(":", 1)[-1]
            elif info.get("curation_status") == "long_exact_surface_word_candidate_not_editor_approved":
                badge += " · 检索候选"
            sentence = example.get("text") or info.get("text", "")
            sentence_view = self.annotated_sentence(sentence, size=19)
            sentence_view.style.flex = 1
            sentence_row = (ui.row(sentence_view, ui.speaker_button(
                lambda w, text=sentence, **kw: self.speak_text(text), "播放例句：" + sentence))
                if sentence else sentence_view)
            entry = [ui.label(badge, size=12, color=MUTED, bold=True), sentence_row]
            if generated:
                entry.append(self.mixed_label("目标词：", ContentText(info["target_form"]), " · ", ContentText(info["target_reading"]), size=13, color=GREEN, study_content=True))
                if info.get("usage_note_zh"):
                    entry.append(self.mixed_label("用法：", ContentText(info["usage_note_zh"]), size=13))
            elif info.get("attribution"):
                credit = info["attribution"]
                if info.get("license") and info["license"] not in credit:
                    credit += " · " + info["license"]
                entry.append(ui.label(credit, size=12, color=MUTED))
            for translation in info.get("translations", []):
                prefix = "中文：" if translation.get("language") in ("zh", "cmn") else "英文："
                if translation.get("language") in ("zh", "cmn"):
                    entry.append(self.natural_translation_label(sentence, translation["text"], prefix=prefix, size=15))
                else:
                    entry.append(self.mixed_label(prefix, ContentText(translation["text"]), size=15))
                attribution = translation.get("attribution") or translation.get("license", "")
                if translation.get("license") and translation["license"] not in attribution:
                    attribution += " · " + translation["license"]
                if attribution and not generated:
                    entry.append(ui.label(attribution, size=12, color=MUTED))
                if translation.get("attribution_url"):
                    entry.append(ui.button("译句出处", lambda w, t=translation, **kw: self.open_url(t["attribution_url"], title="译句出处", summary=t["text"])))
            chinese = next((translation['text'] for translation in info.get('translations', [])
                            if translation.get('language') in ('zh', 'cmn')), '')
            entry.extend(self.order_translation_rows(sentence, chinese,
                explicit=info.get('order_translation', example.get('order_translation'))))
            if info.get("attribution_url"):
                entry.append(ui.button("查看例句出处", lambda w, url=info["attribution_url"], text=example.get("text") or info.get("text", ""), **kw: self.open_url(url, title="例句出处", summary=text)))
            children.append(self.entry_card(*entry, index=index))
        if not examples:
            children.append(ui.label("该词的可用例句仍待收集。", color=MUTED))
        children.extend([self.heading("词典与证据"), *self.source_buttons(data.get("source_ids", []))])
        if data.get("source_url"):
            children.append(ui.button("打开原词典条目", lambda w, **kw: self.open_url(data["source_url"], title="原词典条目", summary=word["form"] + " · " + word["reading"])))
        for candidate in english_candidates:
            evidence = candidate.get("etymology_evidence", {})
            if evidence.get("permalink"):
                children.append(ui.button("英语词源证据 · " + short(candidate.get("english_source_expression", ""), 18), lambda w, url=evidence["permalink"], text=candidate.get("english_source_expression", ""), **kw: self.open_url(url, title="英语词源证据", summary=text)))
        self.screen("word_detail", "词条详情", *children)

    def open_word_references(self, word_id):
        word = self.repo.get_word(word_id)
        summary = word["form"] + " · " + word["reading"] + "\n独立英语词义、发音及汉语对应资料。英语与日语义项尚未逐项对齐；同形汉语词不证明日语词源。"
        key = ("show_word_references", (word_id, 0))
        self._preview_link(key, "词源参照速览", summary, lambda: self.show_word_references((word_id, 0)), assisted=True)

    @page_view(detail=True)
    def show_word_references(self, identity):
        word_id, page = identity
        word = self.repo.get_word(word_id)
        blocks = reference_entries(payload(word))
        pages = max(1, (len(blocks) + 7) // 8)
        page = max(0, min(page, pages - 1))
        children = [self.content_label(word["form"], size=26, text_role="study"),
                    self.note(REFERENCE_PAGE_NOTE), ui.label(f"第 {page+1} / {pages} 页", size=12, color=MUTED)]
        children.extend(self.detail_entries(blocks[page*8:(page+1)*8]))
        if page:
            children.append(ui.button("上一页资料", lambda w, **kw: self.show_word_references((word_id, page-1))))
        if page + 1 < pages:
            children.append(ui.button("下一页资料", lambda w, **kw: self.show_word_references((word_id, page+1))))
        children += [self.heading("来源与许可"), *self.source_buttons(payload(word).get("source_ids", []))]
        self.screen("word_references", "英语与汉语参照", *children)

    def open_character_readings(self, character):
        key = ("show_character_readings", character)
        readings = self.repo.dialects_for_hanzi(character)
        summary = character + " · " + str(len(readings)) + " 条单字音资料\n按地点和来源分别展示；不是整词或连续语流的读音。"
        self._preview_link(key, "汉字读音速览", summary, lambda: self.show_character_readings(character), assisted=True)

    def open_character_catalog(self, widget=None, **kwargs):
        self._preview_link(("show_character_catalog", None), "历史资料速览", "按汉字查看有来源的中古音、上代日语音符和异体字资料。历史比较记录不等于假名首次出现时的唯一实音。",
                           self.show_character_catalog, assisted=True)

    @page_view()
    def show_character_catalog(self, widget=None, **kwargs):
        children = [self.note("收录字源字与异体比较字的历史资料，按字分别查看。ゐ、ゑ等直接以假名讨论的音系证据见各假名详情。")]
        characters = self.repo.historical_characters()

        def make_button(character):
            return ui.button(character, lambda w, c=character, **kw: self.open_character_readings(c))

        if len(characters) <= 20:
            children.extend(make_button(character) for character in characters)
        else:
            visible = 20
            entries = ui.col(*(make_button(character) for character in characters[:visible]))
            more = ui.button(f"显示更多汉字（还有 {len(characters) - visible} 字）", None)

            def expand(widget=None, **kwargs):
                nonlocal visible
                if visible >= len(characters):
                    return
                end = min(len(characters), visible + 20)
                batch = [make_button(character) for character in characters[visible:end]]
                entries.add(*batch)
                visible = end
                if visible == len(characters):
                    section.remove(more)
                else:
                    more.text = f"显示更多汉字（还有 {len(characters) - visible} 字）"

            more.on_press = expand
            section = ui.col(entries, more)
            children.append(section)
        self.screen("character_catalog", "历史字音目录", *children)

    @page_view(detail=True)
    def show_character_readings(self, character):
        children = [self.content_label(character, size=42)]
        source_ids = []
        children.extend(self.pronunciation_panel(dialects=self.repo.dialects_for_hanzi(character),
            historical=self.repo.historical_for_hanzi(character), characters=[character]))
        related = set()
        for relation in self.repo.variants_for_hanzi(character):
            related.update([relation["character"], relation["related_character"]])
            source_ids.extend(relation.get("source_ids", []))
        related.discard(character)
        if related:
            children += [self.heading("异体与相关字形"), self.note("资料库中的字形关系不等于各词义、时代和方言读音可以互换；每个字的资料单独显示。")]
            for other in sorted(related):
                children.append(ui.button("相关字形 · " + other, lambda w, c=other, **kw: self.open_character_readings(c)))
        if source_ids:
            children.append(self.disclosure("字形关系来源", lambda: self.source_buttons(source_ids)))
        self.screen("character_readings", "汉字读音参照", *children)

    @page_view(detail=True)
    def show_source(self, source):
        info = payload(source)
        children = [ui.label(source["title"], size=24, bold=True, color=GREEN),
                    self.entry_card(ui.label("许可", size=12, color=MUTED, bold=True),
                                    ui.label(str(source.get("license") or "逐项核查"), size=18, bold=True),
                                    ui.label(info.get("license_url", ""), size=12, color=MUTED)),
                    self.entry_card(ui.label("署名", size=12, color=MUTED, bold=True),
                                    ui.label(str(info.get("attribution") or info.get("author") or info.get("publisher") or "见原始来源"), size=15), index=1),
                    self.entry_card(ui.label("使用说明", size=12, color=MUTED, bold=True),
                                    ui.label(info.get("usage_note") or info.get("attribution_requirement") or info.get("changes") or "保留来源及版本。个人可阅读不自动等于可再分发。", size=14), index=2)]
        if source.get("url"):
            children += [ui.label(source["url"], size=11, color=MUTED), ui.button("在浏览器查看来源", lambda w, **kw: self.open_url(source["url"], title="资料来源", summary=source["title"]))]
        self.screen("source", "资料出处", *children)

    @page_view()
    def show_settings(self, widget=None, **kwargs):
        self.model_panel = ui.col(self.model_controls())
        self.settings_message = ui.label(self.service_message, size=12, color=MUTED)
        pitch = KanaMemory._pitch_preferences(self)
        self.pitch_contour_switch = ui.switch("高低线（- 高 / _ 低）", value=pitch["contour"],
            on_change=lambda w, **kw: KanaMemory.change_pitch_display(self, w))
        self.pitch_number_switch = ui.switch("数字调号", value=pitch["number"],
            on_change=lambda w, **kw: KanaMemory.change_pitch_display(self, w))
        self.screen("settings", "模型、语音与资料",
                    self.heading("单词音高标注"), self.pitch_contour_switch, self.pitch_number_switch,
                    self.note("两种标注可同时显示。- 表示高音，_ 表示低音；0 为无下降，n 为第 n 拍后下降。只标词典读法；不要求在答案中输入。"),
                    self.heading("离线手写识别"), self.model_panel,
                    self.note("只处理笔迹；笔顺和美观评分不在本版范围。"),
                    ui.button("自由试写识别", self.show_free_ink),
                    self.heading("日语合成语音"), self.settings_message,
                    self.note("日语声音与手写模型需要分别下载。首次联网安装系统日语音包后，可离线朗读假名、单词和例句。"),
                    ui.button("安装日语语音数据", self.install_tts_data, primary=True),
                    self.note("在打开的语音页面选择日语／日本語并下载。若进入系统设置，请点首选语音引擎旁的齿轮 → 安装语音数据。完成后返回这里，重新检查并试播。"),
                    ui.row(ui.button("重新检查声音", self.refresh_services, flex=1, margin_right=6), ui.button("系统语音设置", self.open_tts_settings, flex=1)),
                    self.mixed_label("试播内容：", ContentText("こんにちは")), ui.button("试播日语", lambda w, **kw: self.speak_text("こんにちは")),
                    self.heading("普通话与香港粤语合成"),
                    *[self.entry_card(ui.label(title, size=17, bold=True),
                        ui.label(self.platform.chinese_voice_status(language)["detail"], size=13, color=MUTED),
                        ui.button("试播 · " + title, lambda w, lang=language, **kw: self.speak_chinese("你好", lang)), index=index)
                      for index, (language, title) in enumerate((("cmn", "普通话"), ("yue", "香港粤语")))],
                    self.note("字源及汉字详情中可按字朗读。系统自动选择多音字读法，声音数据由手机语音引擎管理。"),
                    ui.button("安装中文语音数据", self.install_tts_data, primary=True),
                    self.note("选择中文（中国）／Chinese (China)、粤语（香港）／Cantonese (Hong Kong) 下载；完成后返回并重新检查。"),
                    ui.button("重新检查语音包", self.refresh_services),
                    self.heading("数据与学习记录"), self.note("学习记录与词库分开保存。练习设置和未完成练习自动保留；未完成听写的笔迹仅保留在本机进度及手动备份中。"),
                    ui.button("导出学习备份", self.export_learning_backup), ui.button("从文件恢复学习备份", self.import_learning_backup),
                    ui.button("转换旧版学习记录", self.convert_legacy_learning_records),
                    ui.button("导出恢复前备份", self.export_previous_backup),
                    ui.button("删除学习数据", self.delete_learning_data, color="#A62A37"),
                    ui.button("查看内容与许可说明", self.show_notices), ui.label(f"KanaMemory {VERSION} · {BUILD_DATE}", size=12, color=MUTED))

    async def refresh_services(self, widget=None, **kwargs):
        self.service_message = "正在检查…"
        if hasattr(self, "settings_message"):
            self.settings_message.text = self.service_message
        try:
            await self.platform.prepare_tts()
            self.service_message = self.platform.tts_detail
        except Exception as exc:
            self.service_message = "声音检查失败：" + str(exc)
        if self.page == "settings":
            self.show_settings()
        elif self.page == "practice_setup":
            self.refresh_practice_setup()
        elif self.page in INK_PAGES:
            self.refresh_model_ui()

    async def download_model(self, widget=None, **kwargs):
        if self._model_downloading or self.model_state in {"ready", "unavailable"}:
            return
        self._model_downloading = True
        self._model_request += 1  # A previous startup/status check must not overwrite this download.
        self.model_state, self.model_message = "downloading", ""
        self.refresh_model_ui()
        try:
            await self.platform.download_model()
            self.model_message = "下载已完成，正在核验模型…"
            self.refresh_model_ui()
            self.model_state = await self.platform.model_status()
            if self.model_state != "ready":
                self.model_state = "error"
                self.model_message = "模型尚未确认就绪，请重新检查；仍未就绪可重试下载。"
            else:
                self.model_message = ""
        except asyncio.CancelledError:
            self.model_state = "error"
            self.model_message = "等待已中断，下载可能仍在继续；请重新检查模型。"
            raise
        except Exception as exc:
            self.model_state = "error"
            self.model_message = error_message(exc)
        finally:
            self._model_downloading = False
            self.refresh_model_ui()

    def install_tts_data(self, widget=None, **kwargs):
        try:
            self.platform.open_tts_installation()
        except Exception as exc:
            self.alert(str(exc))

    def open_tts_settings(self, widget=None, **kwargs):
        try:
            self.platform.open_tts_settings()
        except Exception as exc:
            self.alert(str(exc))

    @page_view()
    def show_notices(self, widget=None, page=0, **kwargs):
        notice_path = Path(__file__).parent / "resources" / "NOTICE.txt"
        text = notice_path.read_text(encoding="utf-8") if notice_path.exists() else "来源和许可逐条保留在内容数据库中。"
        pages = max(1, (len(text) + 4999) // 5000)
        page = max(0, min(page, pages - 1))
        controls = []
        if page:
            controls.append(ui.button("上一页", lambda w, **kw: self.show_notices(page=page-1)))
        if page + 1 < pages:
            controls.append(ui.button("下一页", lambda w, **kw: self.show_notices(page=page+1)))
        self.screen("notices", "来源与内容说明", ui.label(f"第 {page+1} / {pages} 页"), ui.label(text[page*5000:(page+1)*5000], size=12), *controls)

    @page_view()
    def show_progress(self, widget=None, **kwargs):
        stats = self.store.stats()
        records = [event for event in self.store.review_history(limit=20, newest_first=True, include_states=False)
                   if event["skill"] not in WORD_PRACTICE_KINDS]
        children = [ui.label(self._stats_summary(stats), size=18, bold=True), self.note("假名按题型和字形保留记忆记录；单词用于练习其中的假名。识别失败、语音失败和未提交的题目不记作遗忘。"), ui.button("管理记忆卡片", self.show_cards), self.heading("最近复习")]
        for event in records:
            skill = next((title for title, value in PRACTICE_KINDS.items() if value == event["skill"]), event["skill"])
            item = self.repo.get_word(event["content_id"]) if event["skill"] in WORD_PRACTICE_KINDS else self.repo.get_kana(event["content_id"])
            title = (item.get("form") or item.get(event["script"]) or event["content_id"]) if item else event["content_id"]
            rating = {1: "再学一次", 2: "有些困难", 3: "记住了", 4: "很轻松"}.get(event["rating"], "")
            when = app_clock.localize(datetime.fromisoformat(event["occurred_at_utc"])).strftime("%m-%d %H:%M")
            children.append(self.mixed_label(ContentText(title), f" · {skill}\n{rating} · {when}", size=14))
        if not records:
            children.append(ui.label("完成练习并选择评级后，这里会出现记录。"))
        self.screen("progress", "复习进度", *children)

    def _activate_practice_lane(self, lane):
        if getattr(self.store, "practice_lane", "main") == lane:
            return True
        # Save the old page before switching which checkpoint is visible.
        if self._checkpoint() is False:
            return False
        self.store.practice_lane = lane
        return True

    def _word_drill_kinds(self):
        preferences = self.store.get_setting("word_drill_preferences", {})
        direction = preferences.get("direction") if isinstance(preferences, dict) else None
        return WORD_DIRECTION_KINDS[WORD_DRILL_DIRECTIONS.get(direction, "mixed")]

    def _word_drill_remaining(self, pending):
        from .kana_question_policy import question_allowed
        from .session_content import retired
        if not isinstance(pending, dict):
            return 0
        kinds = KanaMemory._word_drill_kinds(self)
        return sum(q.kind in kinds and question_allowed(q) and not retired(q)
                   for q in pending["questions"][pending["question_index"]:])

    def _word_drill_needs_update(self, pending):
        from .session_content import retired
        return bool(pending and any(retired(q) for q in pending['questions'][pending['question_index']:]))

    @page_view()
    def show_word_drill_setup(self, widget=None, **kwargs):
        if KanaMemory._activate_practice_lane(self, "word_romaji") is False:
            return
        preferences = self.store.get_setting("word_drill_preferences", {})
        preferences = preferences if isinstance(preferences, dict) else {}
        self.word_drill_count_input = toga.TextInput(value=str(preferences.get("count", "10")),
            placeholder="本组单词数", style=Pack(margin_bottom=8))
        def picker(mapping, key):
            value = preferences.get(key)
            return ui.selection(items=list(mapping), value=value if value in mapping else next(iter(mapping)),
                             style=Pack(margin_bottom=8))
        self.word_drill_direction_picker = picker(WORD_DRILL_DIRECTIONS, "direction")
        self.word_drill_script_picker = picker(WORD_DRILL_SCRIPTS, "script")
        self.word_drill_collection_picker = picker(WORD_ROMAJI_COLLECTIONS, "collection")
        self.word_drill_keyboard_picker = picker(WORD_TABLES, "keyboard")
        self.word_drill_summary = ui.label("", size=13, color=MUTED)
        self.word_drill_start_button = ui.button("开始拼写", self.start_word_drill, primary=True)
        self.word_drill_resume_button = ui.button("继续单词拼写", self.resume_word_drill, primary=True)
        self.word_drill_resume_slot = ui.col()
        self.word_drill_keyboard_slot = ui.col()
        self.screen("word_drill_setup", "单词拼写",
            self.word_drill_resume_slot,
            self.note("用单词双向练习假名，以较低权重调整相关假名的复习间隔。单词不单独记忆。"),
            ui.label("练习方向", bold=True), self.word_drill_direction_picker,
            ui.label("词汇范围", bold=True), self.word_drill_collection_picker,
            ui.label("假名字形", bold=True), self.word_drill_script_picker,
            self.word_drill_keyboard_slot,
            ui.label("本组单词数", bold=True), self.word_drill_count_input,
            self.word_drill_summary, self.word_drill_start_button,
            ui.label("使用完整词库的主要读音；字源、词义等资料的审核状态可在词条详情查看。", size=12, color=MUTED))
        for control in (self.word_drill_direction_picker, self.word_drill_count_input, self.word_drill_script_picker,
                        self.word_drill_collection_picker, self.word_drill_keyboard_picker):
            control.on_change = self.refresh_word_drill_setup
        self.refresh_word_drill_setup()

    def _word_drill_config(self):
        return dict(limit=practice_number(self.word_drill_count_input.value, "单词数"),
                    script=WORD_DRILL_SCRIPTS[self.word_drill_script_picker.value],
                    word_collection=WORD_ROMAJI_COLLECTIONS[self.word_drill_collection_picker.value],
                    word_table=WORD_TABLES[self.word_drill_keyboard_picker.value],
                    word_direction=WORD_DRILL_DIRECTIONS[self.word_drill_direction_picker.value])

    def refresh_word_drill_setup(self, widget=None, **kwargs):
        if self.page != "word_drill_setup":
            return
        self.store.set_setting("word_drill_preferences", dict(count=self.word_drill_count_input.value,
            script=self.word_drill_script_picker.value, collection=self.word_drill_collection_picker.value,
            keyboard=self.word_drill_keyboard_picker.value, direction=self.word_drill_direction_picker.value))
        if self.word_drill_direction_picker.value == "假名 → 选罗马音":
            self.word_drill_keyboard_slot.clear()
        elif not self.word_drill_keyboard_slot.children:
            self.word_drill_keyboard_slot.add(ui.label("假名键盘", bold=True), self.word_drill_keyboard_picker)
        pending = self.store.load_session_progress(lane="word_romaji")
        self.word_drill_resume_slot.clear()
        count = KanaMemory._word_drill_remaining(self, pending)
        needs_update = KanaMemory._word_drill_needs_update(self, pending)
        if count or needs_update:
            self.word_drill_resume_button.text = f"继续单词拼写 · 剩 {count} 词" if count else '更新待练单词'
            self.word_drill_resume_slot.add(self.word_drill_resume_button)
        self.word_drill_start_button.text = "追加一组单词" if count or needs_update else "开始拼写"
        try:
            config = KanaMemory._word_drill_config(self)
            key = (config["script"], config["word_collection"], config["word_direction"])
            cache = getattr(self, "_word_drill_pool_cache", {})
            if key not in cache:
                cache[key] = word_romaji_pool_counts(self.repo, script=config["script"],
                    word_collection=config["word_collection"], word_direction=config["word_direction"])
                self._word_drill_pool_cache = cache
            counts = cache[key]
            self.word_drill_summary.text = (f"范围内 {counts['word_count']} 个词条，{counts['question_count']} 种拼写。"
                f"本次最多加入 {min(config['limit'], counts['question_count'])} 词；同组相同拼写只出一次，跳过待做词。")
            self.word_drill_start_button.enabled = counts["question_count"] > 0 and not getattr(self, "_starting_practice", False)
        except (ValueError, TypeError, KeyError) as exc:
            self.word_drill_summary.text = str(exc)
            self.word_drill_start_button.enabled = False

    def resume_word_drill(self, widget=None, **kwargs):
        if KanaMemory._activate_practice_lane(self, "word_romaji") is not False:
            self.resume_practice()

    def replace_content_question(self, session_id, receipt_id):
        if self.page != 'session_wait' or self.session_id != session_id: return
        from .session_content import replace
        pending = self.store.load_session_progress()
        try:
            replace(self.store, self.repo, session_id, receipt_id, KanaMemory._queue_kinds(self, pending))
        except ValueError as exc:
            self.alert(str(exc))
            return
        self.resume_practice()

    async def start_word_drill(self, widget=None, **kwargs):
        if getattr(self, "_starting_practice", False) or self.page != "word_drill_setup":
            return
        try:
            config = KanaMemory._word_drill_config(self)
        except (ValueError, TypeError, KeyError) as exc:
            self.alert(str(exc))
            return
        self._starting_practice = True
        token, start_token = self._page_token, object()
        self._practice_start_token = start_token
        self._practice_start_task = asyncio.current_task()
        self.word_drill_start_button.enabled = False
        self.word_drill_start_button.text = "正在准备…"
        try:
            if KanaMemory._activate_practice_lane(self, "word_romaji") is False:
                return
            if KanaMemory._reconcile_practice_kinds(self) is False:
                return
            pending = self.store.load_session_progress()
            session_id = pending["session_id"] if pending else None
            append_token = self.store.session_append_token(session_id) if session_id else None
            seed = secrets.randbits(32)
            options = dict(config, seed=seed)
            prepared = None
            if isinstance(self.repo, ContentRepository) and isinstance(self.store, StudyStore):
                revision = study_revision(self.store)
                questions, prepared = await build_in_background(self.repo, self.store, build_word_romaji_drill, options)
                if (self.page != "word_drill_setup" or self._page_token != token
                        or getattr(self, "_leave_confirmation_active", False)
                        or KanaMemory._word_drill_config(self) != config
                        or revision != study_revision(self.store)):
                    return
            else:
                questions = build_word_romaji_drill(self.repo, self.store, **options)
            if not questions:
                self.alert("当前范围的拼写已在本组中。可继续原练习，或调整词汇范围。")
                return
            if session_id:
                if prepared is not None:
                    self.store.append_prepared_session(session_id, prepared, expected_token=append_token)
                else:
                    self.store.append_session(session_id, questions, seed=seed, expected_token=append_token)
                self.back_stack.discard(PRACTICE_PAGES)
                from .practice_round import restart
                restart(self.store)
                self.resume_practice(preserve_audio=True)
            else:
                self.session_id = (self.store.start_prepared_session(prepared) if prepared is not None
                                   else self.store.start_session(questions, seed=seed))
                self.questions = questions
                from .practice_round import restart
                restart(self.store)
                self.question_index, self.session_right = 0, 0
                self.script, self.research_enabled = questions[0].script, True
                self.use_full_keyboard = config["word_table"] == "full"
                self._assisted_attempts.clear()
                self._answered_attempts.clear()
                self.back_stack.discard(PRACTICE_PAGES)
                self.show_question()
        except Exception as exc:
            if self.page == "word_drill_setup" and self._page_token == token:
                self.alert("无法准备单词拼写：" + error_message(exc))
        finally:
            if getattr(self, "_practice_start_token", None) is start_token:
                self._starting_practice = False
                self._practice_start_task = None
                if self.page == "word_drill_setup":
                    self.refresh_word_drill_setup()

    @page_view()
    def show_practice_setup(self, widget=None, *, temporary_preferences=None, **kwargs):
        if KanaMemory._activate_practice_lane(self, "main") is False:
            return
        preferences = temporary_preferences if temporary_preferences is not None else getattr(self, "_practice_preferences", {
            "kind": "综合自适应练习", "script": "平假名与片假名（逐题分开）",
            "word_table": "部分假名（较容易）", "research": False,
        })
        # Shortcut filters belong to this retained form, not the learner's
        # ordinary preferences. Background refreshes and Back restore the same
        # context, while opening the normal setup starts from saved preferences.
        self._practice_setup_temporary = temporary_preferences is not None
        self._practice_setup_preferences = dict(preferences)
        self._scope_inventory_cache = {}
        selected_scopes = preferences.get("scopes", DEFAULT_PRACTICE_SCOPES)
        # Install callbacks only once every control belongs to the new view.
        self.kind_picker = ui.selection(items=list(PRACTICE_KINDS), value=preferences["kind"], style=Pack(margin_bottom=8))
        self.mixed_kind_switches = {kind: ui.switch(title,
            value=kind in preferences.get("mixed_kinds", DEFAULT_MIXED_KINDS), style=Pack(margin_bottom=6))
            for title, kind in PRACTICE_KINDS.items() if kind not in MIXED_PRACTICE_KINDS}
        self.practice_kinds_slot = ui.col()
        self.practice_mode_hint = ui.col()
        self.script_picker = ui.selection(items=list(PRACTICE_SCRIPTS), value=preferences["script"], style=Pack(margin_bottom=8))
        self.word_table_picker = ui.selection(items=list(WORD_TABLES), value=preferences["word_table"], style=Pack(margin_bottom=8))
        self.practice_word_collection_picker = ui.selection(items=list(PRACTICE_WORD_COLLECTIONS), value=preferences.get("word_collection", "全部可用词汇"), style=Pack(margin_bottom=8))
        self.research_switch = ui.switch("使用待审核词汇", value=preferences["research"], style=Pack(margin_bottom=0))
        self.scope_switches = {
            title: ui.switch(title, value=title in selected_scopes, style=Pack(margin_bottom=6))
            for title in PRACTICE_SCOPES
        }
        self.count_picker = ui.selection(items=list(PRACTICE_COUNTS), value=preferences.get("count", "10 项"), style=Pack(margin_bottom=8))
        self.batch_picker = ui.selection(items=list(PRACTICE_BATCHES), value=preferences.get("batch", next(iter(PRACTICE_BATCHES))), style=Pack(margin_bottom=8))
        self.batch_hint = ui.label("", size=12, color=MUTED)
        self.custom_count_input = toga.TextInput(value=preferences.get("custom_count", "10"), placeholder="输入本批数量", style=Pack(margin_bottom=8))
        self.rounds_input = toga.TextInput(value=preferences.get("rounds", "1"), placeholder="每种题型练习几轮", style=Pack(margin_bottom=8))
        self.schedule_picker = ui.selection(items=list(PRACTICE_SCHEDULES), value=preferences.get("schedule", "间隔复习"), style=Pack(margin_bottom=8))
        self.schedule_hint = ui.label("", size=12, color=MUTED)
        self.rounds_slot = ui.col()
        self.custom_count_slot = ui.col()
        if current_platform == "android":
            self.custom_count_input._impl.native.setInputType(2)
            self.rounds_input._impl.native.setInputType(2)
        self.practice_scope_hint = ui.label("", size=12, color=MUTED, margin_bottom=0)
        self.scope_inventory_labels = {title: ui.label("", size=12, color=MUTED, margin_bottom=0)
                                       for title in PRACTICE_SCOPES}
        scope_rows = [ui.col(self.scope_switches[title], self.scope_inventory_labels[title],
                          padding=8, margin_bottom=6,
                          background_color=ENTRY_BACKGROUNDS[index % len(ENTRY_BACKGROUNDS)])
                      for index, title in enumerate(PRACTICE_SCOPES)]
        self.practice_scope_card = self.entry_card(
            ui.label("3 · 假名范围（可多选）", size=15, bold=True), *scope_rows,
            ui.label("按当前字形统计作答记录；两种字形同时选时，同一假名只计一项。已加入的内容不会重复抽取。",
                  size=12, color=MUTED), self.practice_scope_hint, index=2)
        self.practice_scope_slot = ui.col()
        self.practice_description = ui.label("", size=12, color=MUTED, margin_bottom=0)
        self.practice_script_label = ui.label("", size=15, bold=True)
        self.practice_script_hint = ui.label("", size=12, color=MUTED, margin_bottom=0)
        self.practice_word_card = self.entry_card(
            ui.label("3 · 词汇组与作答范围", size=15, bold=True), self.practice_word_collection_picker,
            ui.label("答题时提供的假名", size=13, bold=True), self.word_table_picker,
            ui.label("部分：答案所需假名与干扰项。\n完整：从整张假名表中查找。", size=12, color=MUTED),
            ui.label("默认使用基础读音核对清单（AI 辅助核对）。\n开启下方选项可扩展到其他待审核词汇。", size=12, color=MUTED),
            self.research_switch, index=2)
        self.practice_word_slot = ui.col()
        self.practice_services = ui.col()
        self.practice_start_button = ui.button("开始练习", self.start_practice, primary=True)
        self.practice_replace_slot = ui.col()
        self.practice_count_summary = ui.label("", size=13, color=GREEN, bold=True)
        self.practice_count_caption = ui.label("本批记忆个数", size=13, bold=True)
        self.practice_count_hint = ui.label("记忆个数按不同假名计算，题目可以更多；每日不限新增。", size=12, color=MUTED, margin_bottom=0)
        self.screen("practice_setup", "选择练习",
                    self.practice_start_button, self.practice_replace_slot,
                    self.practice_count_summary, self.practice_services,
                    *([self.note("这些设置仅用于本次到期复习；日常练习方式保持不变。")] if self._practice_setup_temporary else []),
                    self.entry_card(ui.label("1 · 练习方式", size=15, bold=True), self.kind_picker, self.practice_description, self.practice_mode_hint, self.practice_kinds_slot),
                    self.entry_card(self.practice_script_label, self.script_picker, self.practice_script_hint, index=1),
                    self.practice_scope_slot, self.practice_word_slot,
                    self.entry_card(ui.label("4 · 本批内容与数量", size=15, bold=True), self.schedule_picker, self.schedule_hint, self.batch_picker, self.batch_hint,
                                    self.practice_count_caption, self.count_picker, self.custom_count_slot,
                                    self.rounds_slot,
                                    self.practice_count_hint, index=0))
        for control in (self.kind_picker, self.script_picker, self.word_table_picker, self.practice_word_collection_picker,
                        self.research_switch, self.count_picker, self.custom_count_input, self.batch_picker, self.rounds_input, self.schedule_picker,
                        *self.mixed_kind_switches.values(), *self.scope_switches.values()):
            control.on_change = self.refresh_practice_setup
        self.refresh_practice_setup()

    def enable_mixed_practice(self, widget=None, **kwargs):
        if self.page != "practice_setup":
            return
        self.kind_picker.value = "综合自适应练习"
        for kind, control in self.mixed_kind_switches.items():
            control.value = kind in DEFAULT_MIXED_KINDS
        self.refresh_practice_setup(self.kind_picker)

    @page_view()
    def show_cards(self, widget=None, **kwargs):
        self.card_query = toga.TextInput(placeholder="搜索假名或罗马音", style=Pack(margin_bottom=8))
        self.card_state_picker = ui.selection(items=list(CARD_STATES), style=Pack(margin_bottom=8))
        self.card_results, self.card_offset = ui.col(), 0
        self.card_page_label = ui.label("", size=12, color=MUTED)
        self.card_previous_button = ui.button("上一页", lambda w, **kw: self.page_cards(-1), flex=1)
        self.card_next_button = ui.button("下一页", lambda w, **kw: self.page_cards(1), flex=1)
        self.screen("cards", "管理记忆卡片",
            self.note("同一内容统一暂停或暂缓；各题型、平片假名保留各自记录。尚未测试的能力不算已掌握。"),
            self.card_query, self.card_state_picker,
            self.card_page_label, self.card_results, ui.row(self.card_previous_button, self.card_next_button))
        for control in (self.card_query, self.card_state_picker):
            control.on_change = self.refresh_cards
        self.refresh_cards()

    def page_cards(self, direction):
        if self.page == "cards":
            self.card_offset = max(0, self.card_offset + direction * 20)
            self.refresh_cards(reset=False)

    def refresh_cards(self, widget=None, *, reset=True, **kwargs):
        if self.page != "cards":
            return
        controls = (self.card_query, self.card_state_picker)
        if widget is not None and widget not in controls:
            return
        if reset:
            self.card_offset = 0
        result = CardManager(self.repo, self.store).list_items(
            query=self.card_query.value or "", status=CARD_STATES[self.card_state_picker.value],
            kind="kana", offset=self.card_offset, limit=20)
        self.card_results.clear()
        for index, item in enumerate(result["items"]):
            self.card_results.add(self.entry_card(
                ui.label(item["title"], size=23, bold=True), ui.label(item["subtitle"], size=13, color=MUTED),
                ui.label(item["state_label"] + (" · 已到期" if item["due"] else "") + f" · 已复习 {item['review_count']} 次", size=12),
                ui.button("记录与管理", lambda w, ident=item["content_id"], **kw: self.show_card_detail(ident)), index=index))
        if not result["items"]:
            self.card_results.add(ui.label("没有符合条件的内容。"))
        self.card_page_label.text = f"共 {result['total']} 项 · 第 {self.card_offset // 20 + 1} 页"
        self.card_previous_button.enabled = self.card_offset > 0
        self.card_next_button.enabled = result["has_next"]

    @staticmethod
    def _local_due(value):
        return app_clock.localize(datetime.fromisoformat(value)).strftime("%m-%d %H:%M") if value else "尚未安排"

    @page_view(detail=True)
    def show_card_detail(self, content_id):
        item = CardManager(self.repo, self.store).detail(content_id, include_research=True)
        if not item:
            self.alert("未找到这条记忆内容。")
            return
        controls = [ui.button("恢复练习" if item["suspended"] else "暂停此内容",
            lambda w, **kw: self.change_card_status(content_id, "resume" if item["suspended"] else "suspend"))]
        if not item["suspended"]:
            buried = item["state"] == "buried"
            controls.append(ui.button("取消今日暂缓" if buried else "今天暂缓",
                lambda w, **kw: self.change_card_status(content_id, "unbury" if buried else "bury")))
        children = [ui.label(item["title"], size=32, bold=True, color=GREEN), ui.label(item["subtitle"]),
            ui.label(("词中已练 · 独立题型尚未测试" if item.get("word_spelling_only") else item["state_label"])
                  + (" · 已到期" if item["due"] else ""), bold=True),
            self.note("暂停后需手动恢复；今日暂缓在本地次日自动结束。两者均保留记忆记录，并作用于本条内容的所有题型和字形。"),
            *controls, ui.button("内容详细", lambda w, **kw: self.show_kana_detail(content_id) if item["kind"] == "kana" else self.show_word_detail(content_id)),
            self.heading("各题型的记忆记录")]
        titles = {value: title for title, value in PRACTICE_KINDS.items()}
        for index, card in enumerate(item["cards"]):
            script = "片假名" if card["script"] == "katakana" else "平假名"
            children.append(self.entry_card(ui.label(f"{titles.get(card['skill'], card['skill'])} · {script}", bold=True),
                ui.label(f"{card.get('state_label', card['state'])} · 下次 {self._local_due(card.get('due_at_utc'))}", size=13),
                ui.label(f"复习 {card['review_count']} 次 · 无提示通过 {card.get('success_count', 0)} 次 · 需重学 {card.get('failure_count', 0)} 次 · 提示 {card.get('assisted_count', 0)} 次", size=12, color=MUTED), index=index))
        children.append(self.note("没有记录的题型或字形尚未测试；某一题型答对不会自动清除其他题型的遗忘记录。"))
        if item.get("word_spelling_summary"):
            children.append(self.heading("词中的假名拼写"))
            for index, evidence in enumerate(item["word_spelling_summary"]):
                script = "片假名" if evidence["script"] == "katakana" else "平假名"
                children.append(self.entry_card(
                    ui.label(f"{evidence['symbol']} · {script}", bold=True),
                    ui.label(f"拼对 {evidence['success_count']} 次 · 拼错 {evidence['failure_count']} 次", size=13),
                    ui.label("待巩固" if evidence["weak"] else "已纳入熟悉程度", size=12, color=MUTED),
                    index=index))
            children.append(self.note("罗马音拼单词也会轻调已学假名的识读复习间隔；已到期的题、短期学习步骤和听写手写安排保留。"))
        if item.get("word_interval_history"):
            children.append(self.heading("词中拼写调整"))
            for index, event in enumerate(item["word_interval_history"][:4]):
                script = "片假名" if event["script"] == "katakana" else "平假名"
                result = "拼对" if event["result"] == "correct" else "拼错"
                children.append(self.entry_card(
                    ui.label(f"{event['expected']} · {result} · {script}", bold=True),
                    ui.label(titles.get(event["skill"], event["skill"]), size=12, color=MUTED),
                    ui.label(f"{self._local_due(event['before_due_at_utc'])} → {self._local_due(event['after_due_at_utc'])}", size=13),
                    index=index))
        if item["history"]:
            children.append(self.heading("最近作答"))
            for event in item["history"][:8]:
                rating = {1: "再学一次", 2: "有些困难", 3: "记住了", 4: "很轻松"}[event["rating"]]
                children.append(ui.label(f"{self._local_due(event['occurred_at_utc'])} · {titles.get(event['skill'], event['skill'])}\n{rating}" + (" · 使用提示" if event["assisted"] else ""), size=12, color=MUTED))
        self.screen("card_detail", "记忆卡片", *children)

    def change_card_status(self, content_id, action):
        if self.page != "card_detail":
            return
        CardManager(self.repo, self.store).set_status(content_id, action)
        # Re-render this card without adding another copy to the Back stack.
        KanaMemory.show_card_detail.__wrapped__(self, content_id)

    def _practice_config(self):
        kind = PRACTICE_KINDS[self.kind_picker.value]
        kinds = self._practice_kinds()
        has_words = any(item in WORD_PRACTICE_KINDS for item in kinds)
        has_kana = any(item not in WORD_PRACTICE_KINDS for item in kinds)
        script = PRACTICE_SCRIPTS[self.script_picker.value]
        return (kind, script, has_words and self.research_switch.value,
                WORD_TABLES[self.word_table_picker.value] if has_words else "partial",
                None if not has_kana else tuple(category for title, categories in PRACTICE_SCOPES.items()
                                                      if self.scope_switches[title].value for category in categories),
                practice_number(self.custom_count_input.value, "本批记忆个数") if self.count_picker.value == "自定义" else PRACTICE_COUNTS[self.count_picker.value])

    def _practice_new_only(self):
        return PRACTICE_BATCHES[self.batch_picker.value]

    def _practice_kinds(self):
        kind = PRACTICE_KINDS[self.kind_picker.value]
        return tuple(item for item in MIXED_PRACTICE_KINDS[kind] if self.mixed_kind_switches[item].value) if kind in MIXED_PRACTICE_KINDS else (kind,)

    def _practice_rounds(self):
        if self._practice_schedule_mode() == "spaced":
            return 1
        return practice_number(self.rounds_input.value, "每种题型重复轮数")

    def _practice_schedule_mode(self):
        picker = getattr(self, "schedule_picker", None)
        return PRACTICE_SCHEDULES.get(picker.value, "reinforce") if picker is not None else "reinforce"

    def _practice_word_collection(self):
        if not any(kind in WORD_PRACTICE_KINDS for kind in self._practice_kinds()):
            return None
        return PRACTICE_WORD_COLLECTIONS[self.practice_word_collection_picker.value]

    def _practice_available_kinds(self, kinds):
        return tuple(kind for kind in kinds
                     if (kind not in HANDWRITING_PRACTICE_KINDS or self.model_state == "ready")
                     and (kind not in LISTENING_PRACTICE_KINDS or self.platform.tts_ready))

    def practice_plan_summary(self, config):
        kind, script, research, word_table, categories, count = config
        counts = practice_plan_counts(self.repo, self.store, kinds=self._practice_kinds(), rounds=self._practice_rounds(),
            schedule_mode=self._practice_schedule_mode(), adaptive=kind in MIXED_PRACTICE_KINDS or script == "both",
            handwriting_available=self.model_state == "ready", audio_available=bool(self.platform.tts_ready),
            script=script, limit=count, seed=0,
            include_research=research, word_table=word_table, kana_categories=categories,
            word_collection=self._practice_word_collection(),
            new_only=self._practice_new_only())
        titles = {mode: title for title, mode in PRACTICE_KINDS.items()}
        return {**counts, "kind_counts": {titles[mode]: number for mode, number in counts["kind_counts"].items()}}

    def refresh_practice_setup(self, widget=None, **kwargs):
        if self.page != "practice_setup":
            return
        if widget is not None and widget not in (self.kind_picker, self.script_picker, self.word_table_picker,
                self.practice_word_collection_picker, self.research_switch, self.count_picker, self.custom_count_input,
                self.batch_picker, self.rounds_input, self.schedule_picker, *self.mixed_kind_switches.values(), *self.scope_switches.values()):
            return
        if widget is not None:
            self.practice_replace_slot.clear()
        previous_preferences = getattr(self, "_practice_setup_preferences", self._practice_preferences)
        old_custom_count = previous_preferences.get("custom_count", "10")
        try:
            custom_count = str(practice_number(self.custom_count_input.value, "本批记忆个数"))
        except ValueError:
            custom_count = old_custom_count
        try:
            rounds = str(practice_number(self.rounds_input.value, "每种题型重复轮数"))
        except ValueError:
            rounds = previous_preferences.get("rounds", "1")
        preferences = {
            "kind": self.kind_picker.value, "script": self.script_picker.value,
            "word_table": self.word_table_picker.value, "research": self.research_switch.value,
            "scopes": tuple(title for title, control in self.scope_switches.items() if control.value),
            "count": self.count_picker.value,
            "custom_count": custom_count, "word_collection": self.practice_word_collection_picker.value, "batch": self.batch_picker.value,
            "rounds": rounds, "mixed_kinds": [kind for kind in ALL_MIXED_KINDS if self.mixed_kind_switches[kind].value],
            "schedule": self.schedule_picker.value,
            "question_plan_version": 2,
            "listening_handwriting_opt_in_version": 1,
        }
        self._practice_setup_preferences = preferences
        if not getattr(self, "_practice_setup_temporary", False):
            self._practice_preferences = preferences
            self.store.set_setting("practice_preferences", preferences)
        if widget is self.kind_picker or any(widget is control for control in self.mixed_kind_switches.values()):
            pending = self.store.load_session_progress()
            if isinstance(pending, dict):
                KanaMemory._set_queue_kind_policy(self, pending["session_id"],
                    KanaMemory._enabled_preference_kinds(preferences))
        spaced = self._practice_schedule_mode() == "spaced"
        self.schedule_hint.text = ui.wrapped("各题型和字形分别保留记忆记录，共同安排到期与未测试内容。综合练习按已有记录安排本批题型，熟悉后在之后的综合练习中加入进阶形式。尚未验证的能力不算已掌握。" if spaced else "根据已有记录安排本批可用题型，再重复指定轮数；单一题型按指定方式练习。集中练习不代表已经长期记住。")
        if spaced:
            self.rounds_slot.clear()
        elif not self.rounds_slot.children:
            self.rounds_slot.add(ui.label("每种题型重复轮数", size=13, bold=True), self.rounds_input)
        custom = self.count_picker.value == "自定义"
        if custom and not self.custom_count_slot.children:
            self.custom_count_slot.add(self.custom_count_input)
        elif not custom and self.custom_count_slot.children:
            self.custom_count_slot.clear()
        number_error = None
        try:
            config = self._practice_config()
            self._practice_rounds()
        except (ValueError, TypeError) as exc:
            number_error = str(exc)
        kind = PRACTICE_KINDS[self.kind_picker.value]
        kinds = self._practice_kinds()
        mixed = kind in MIXED_PRACTICE_KINDS
        available_kinds = KanaMemory._practice_available_kinds(self, kinds)
        new_only = self._practice_new_only()
        pending = self.store.load_session_progress() if new_only else None
        appending = isinstance(pending, dict)
        self.batch_hint.text = ui.wrapped("仅加入未学过且不在当前队列中的内容；继续时跳过已关闭的题型，保留其余题目、作答和复习安排。" if appending else
            "仅加入未学过的内容；继续已学内容的进阶练习，请选“到期优先”。" if new_only else
            "先复习到期内容，再练已解锁的进阶题型，最后补充全新内容。" if mixed and spaced else
            "先选有到期题型的内容，再补充有尚未学过题型的内容。")
        self.practice_start_button.text = "追加到当前练习" if appending else "开始新批次" if new_only else "开始练习"
        research = self.research_switch.value
        categories = tuple(category for title, values in PRACTICE_SCOPES.items() if self.scope_switches[title].value for category in values)
        descriptions = {
            "adaptive_mixed": ("假名题与单词拼写交错练习；单词用于巩固其中的假名。先辨认，熟悉后逐步加入拼写、完整假名表和手写；下方勾选允许出现的题型。", "2 · 练习字形", "两种字形逐题分开：题目与选项不混排，平假名和片假名分别记录熟悉程度。"),
            "kana_mixed": ("根据每项的已有记录，交错安排当前阶段的提问方式，逐步加入其他勾选题型。", "2 · 练习字形", "每道题与选项统一使用一种字形。"),
            "word_mixed": ("用词义题与听力题巩固假名；只记录其中假名的拼写表现。", "2 · 作答字形", "每道词语题统一用一种字形作答，小假名单独选择。"),
            "kana_choice": ("看字形，从罗马音选项中选出读音。", "2 · 练习字形", "选择题目中显示的假名字形。"),
            "kana_romaji": ("看假名，用英文字母输入对应的罗马音。", "2 · 练习字形", "选择题目中显示的假名字形。"),
            "romaji_kana": ("看罗马音，从选项中选出对应假名。", "2 · 作答字形", "选择假名选项使用的字形。"),
            "romaji_kana_full": ("看罗马音，从完整的现代假名表中选出对应假名。", "2 · 作答字形", "每题只展示一种字形的完整假名表。"),
            "romaji_handwriting": ("看罗马音，手写假名并确认识别结果。", "2 · 书写字形", "无需播放声音；按本题指定的字形书写。"),
            "word_meaning_kana": ("看单词的中文或英文词义和罗马音，按顺序选出假名。", "2 · 作答字形", "所有词语统一用所选字形作答。"),
            "kana_handwriting": ("听读音，手写假名并确认识别结果。", "2 · 书写字形", "按所选字形书写；平、片假名分别复习。"),
            "word_kana": ("听单词，依次点击假名拼出读音。", "2 · 作答字形", "所有词语统一用所选字形作答。"),
        }
        description, caption, hint = descriptions[kind]
        self.practice_description.text = ui.wrapped(description)
        self.practice_mode_hint.clear()
        if len(kinds) == 1:
            self.practice_mode_hint.add(
                ui.label("当前仅启用 1 种题型，整批会使用同一种提问方式。", size=12, color=MUTED),
                ui.button("启用综合题型", lambda w, **kw: KanaMemory.enable_mixed_practice(self)))
        self.practice_script_label.text = caption
        self.practice_script_hint.text = ui.wrapped(hint + (" 平片逐题分开，分别复习。"
            if self.script_picker.value == "平假名与片假名（逐题分开）" and kind != "adaptive_mixed" else ""))
        wanted_controls = [self.mixed_kind_switches[item] for item in MIXED_PRACTICE_KINDS.get(kind, ())]
        if list(self.practice_kinds_slot.children) != wanted_controls:
            self.practice_kinds_slot.clear()
            self.practice_kinds_slot.add(*wanted_controls)
        show_words = any(item in WORD_PRACTICE_KINDS for item in kinds)
        show_kana = any(item not in WORD_PRACTICE_KINDS for item in kinds)
        words_only = show_words and not show_kana
        self.practice_count_caption.text = "本批单词练习数" if words_only else "本批假名记忆个数"
        self.practice_count_hint.text = ui.wrapped("单词只是练习材料，拼写结果计入相关假名的熟悉程度。" if words_only else
            "记忆个数按不同假名计算；单词辅助题另加，每日不限新增。" if show_words else
            "记忆个数按不同假名计算，题目可以更多；每日不限新增。")
        if words_only:
            self.schedule_hint.text = ui.wrapped("单词不设独立记忆卡或到期时间；拼错时按当前设置追加假名补练。")
            self.batch_hint.text = ui.wrapped("从所选词汇组取练习材料，当前队列中已有的词例不重复加入。")
        self.practice_scope_hint.text = ui.wrapped("无法仅凭声音区分的同音假名不出听写题，仍可练习其他已选题型。" if "kana_handwriting" in kinds else
            "题目来自所选范围；选项始终区分单个假名和假名组。" if kind in {"kana_choice", "romaji_kana"} else "题目来自所选范围；可单选或混合练习。")
        if show_kana:
            script = PRACTICE_SCRIPTS[self.script_picker.value]
            # Count historical spelling evidence once per script while editing
            # this form, rather than on every quantity keystroke/service update.
            if script not in self._scope_inventory_cache:
                self._scope_inventory_cache[script] = kana_scope_inventory(
                    self.repo, self.store, script=script, scopes=PRACTICE_SCOPES)
            for title, counts in self._scope_inventory_cache[script].items():
                self.scope_inventory_labels[title].text = ui.wrapped(
                    f"学过 {counts['learned']} · 已加入 {counts['queued']} · 尚未加入 {counts['not_included']}")
        if show_kana and not self.practice_scope_slot.children:
            self.practice_scope_slot.add(self.practice_scope_card)
        elif not show_kana and self.practice_scope_slot.children:
            self.practice_scope_slot.clear()
        if show_words and not self.practice_word_slot.children:
            self.practice_word_slot.add(self.practice_word_card)
        elif not show_words and self.practice_word_slot.children:
            self.practice_word_slot.clear()
        services, blocked = [], bool(number_error)
        if number_error:
            services.append(ui.label(number_error, size=12, color=MUTED))
        if not kinds:
            blocked = True
            services.append(ui.label("请至少选择一种提问方式。", size=12, color=MUTED))
        if show_kana and not categories:
            blocked = True
            services.append(ui.label("请至少选择一种假名范围。", size=12, color=MUTED))
        audio_ready = self.platform.tts_ready
        needs_audio = any(item in LISTENING_PRACTICE_KINDS for item in kinds)
        needs_handwriting = any(item in HANDWRITING_PRACTICE_KINDS for item in kinds)
        if needs_audio:
            services.append(ui.label("日语声音：" + ("已就绪" if audio_ready else "未就绪"), size=12, color=MUTED))
            if not audio_ready:
                services.append(ui.button("准备日语声音", self.show_settings))
        if needs_handwriting:
            services.append(self.model_controls(compact=True))
        unavailable = tuple(item for item in kinds if item not in available_kinds)
        if unavailable:
            titles = {mode: title for title, mode in PRACTICE_KINDS.items()}
            services.append(ui.label(("本批暂时跳过：" if mixed else "尚未就绪：") + "、".join(titles[item] for item in unavailable), size=12, color=MUTED))
            if not mixed or not available_kinds:
                blocked = True
        if show_words and not research and not self.repo.approved_word_count():
            if not show_kana:
                blocked = True
            services.append(ui.label("词汇资料尚待审核；" + ("本批可继续练假名。" if show_kana else "开启“使用待审核词汇”后可开始。"), size=12, color=MUTED))
        try:
            if number_error or not kinds:
                raise ValueError(number_error or "请至少选择一种提问方式。")
            counts = self.practice_plan_summary(self._practice_config())
            distribution = " · ".join(f"{title} {count}题" for title, count in counts.get("kind_counts", {}).items())
            self.practice_count_summary.text = ui.wrapped(
                (f"预计 {counts['total']} 道单词辅助题" if words_only else
                 f"预计 {counts.get('content_count', counts['total'])} 个记忆项 · {'起始' if spaced else '共'} {counts['total']} 题\n"
                 f"到期假名 {counts['due']} 项 · 新学假名 {counts['new']} 项") + ("\n" + distribution if distribution else ""))
            if appending:
                from .kana_question_policy import question_allowed
                remaining = sum(question.kind in kinds and question_allowed(question)
                                for question in pending["questions"][pending["question_index"]:])
                self.practice_count_summary.text = ui.wrapped(
                    (f"预计追加 {counts['total']} 道单词辅助题\n" if words_only else
                     f"预计新增 {counts.get('content_count', counts['total'])} 个记忆项 · {counts['total']} 题\n") +
                    f"原队列未完成 {remaining} 题 · 追加后待完成 {remaining + counts['total']} 题"
                    + ("\n" + distribution if distribution else ""))
            if not counts['total']:
                blocked = True
                collection = self._practice_word_collection() if show_words else None
                if show_words and not research and collection and not self.repo.practice_word_candidates(collection=collection):
                    services.append(ui.label("所选词汇组尚无已核对的练习词。可以开启上方“使用待审核词汇”，或改选“基础词”。", size=12, color=MUTED))
                else:
                    services.append(ui.label("当前题型与字形下没有剩余新内容，可调整范围或切换为到期优先。" if new_only else "当前范围暂无可练习题目，可调整范围或稍后复习。", size=12, color=MUTED))
        except Exception as exc:
            self.practice_count_summary.text = "题量暂无法计算：" + error_message(exc)
            blocked = True
        self.practice_services.clear()
        self.practice_services.add(*services)
        self.practice_start_button.enabled = not blocked and not self._starting_practice

    async def start_practice(self, widget=None, *, replace_session_id=None, **kwargs):
        if self._starting_practice or self.page != "practice_setup":
            return
        self._starting_practice = True
        start_token = self._practice_start_token = object()
        self._practice_start_task = asyncio.current_task()
        self.practice_start_button.enabled = False
        try:
            await self._start_practice(self._page_token, replace_session_id=replace_session_id)
        finally:
            if getattr(self, "_practice_start_token", None) is start_token:
                self._starting_practice = False
                self._practice_start_task = None
                if self.page == "practice_setup":
                    self.refresh_practice_setup()

    def _cancel_practice_start(self):
        self._practice_start_token = None
        self._starting_practice = False
        task = getattr(self, "_practice_start_task", None)
        self._practice_start_task = None
        if task and not task.done():
            task.cancel()

    async def _start_practice(self, page_token, *, replace_session_id=None):
        try:
            config = self._practice_config()
            rounds = self._practice_rounds()
        except (ValueError, TypeError) as exc:
            self.alert(str(exc))
            return
        word_collection = self._practice_word_collection()
        new_only = self._practice_new_only()
        kind, script, research, word_table, categories, count = config
        kinds = self._practice_kinds()
        schedule_mode = self._practice_schedule_mode()
        mixed = kind in MIXED_PRACTICE_KINDS
        has_words = any(item in WORD_PRACTICE_KINDS for item in kinds)
        has_kana = any(item not in WORD_PRACTICE_KINDS for item in kinds)
        if not kinds:
            self.alert("请至少选择一种提问方式。")
            return
        if has_kana and not categories:
            self.alert("请至少选择一种假名范围。")
            return
        if has_words and not has_kana and not research and not self.repo.approved_word_count():
            self.alert("词汇尚待审核。请先开启“使用待审核词汇”，或选择假名练习。")
            return
        if not mixed and any(item in LISTENING_PRACTICE_KINDS for item in kinds) and not self.platform.tts_ready:
            self.alert("日语声音尚未就绪。请先在设置页准备声音，再开始听力练习。")
            return
        if any(item in HANDWRITING_PRACTICE_KINDS for item in kinds) and (not mixed or self.model_state == "ready"):
            try:
                self.model_state = await self.platform.model_status()
            except Exception as exc:
                if not mixed:
                    self.alert(str(exc))
                    return
                self.model_state, self.model_message = "error", error_message(exc)
            try:
                unchanged = (config == self._practice_config() and word_collection == self._practice_word_collection()
                             and new_only == self._practice_new_only() and kinds == self._practice_kinds()
                             and rounds == self._practice_rounds() and schedule_mode == self._practice_schedule_mode())
            except (ValueError, TypeError):
                unchanged = False
            if page_token != self._page_token or not unchanged:
                return
            if not mixed and self.model_state != "ready":
                self.alert("请点击本页的“下载日语手写模型”，完成后再开始。")
                return
        if not KanaMemory._practice_available_kinds(self, kinds):
            self.alert("所选题型需要的声音或手写模型尚未就绪。可准备服务，或勾选不依赖这些服务的题型。")
            return
        pending = self.store.load_session_progress()
        if isinstance(pending, dict):
            KanaMemory._set_queue_kind_policy(self, pending["session_id"], kinds)
            pending = KanaMemory._reconcile_practice_kinds(self, pending)
            if pending is False:
                return
            if isinstance(pending, dict) and pending.get("completed"):
                pending = None
        append_session_id = pending["session_id"] if new_only and isinstance(pending, dict) else None
        if isinstance(pending, dict) and not new_only and pending["session_id"] != replace_session_id:
            preferences = dict(getattr(self, "_practice_setup_preferences", self._practice_preferences))

            async def replace_pending(widget=None, **kwargs):
                current_preferences = getattr(self, "_practice_setup_preferences", self._practice_preferences)
                if self.page == "practice_setup" and self._page_token == page_token and current_preferences == preferences:
                    await self.start_practice(widget, replace_session_id=pending["session_id"])

            self.practice_replace_slot.clear()
            self.practice_replace_slot.add(self.entry_card(
                ui.label("还有未完成的练习", bold=True),
                self.note("原批次和未提交答案仍已保存。另开批次会结束原批次的续练；已经保存的复习记录会保留。"),
                ui.button("继续原练习", lambda w, **kw: self.resume_practice()),
                ui.button("结束原批次并开始", replace_pending)))
            return
        seed = secrets.randbits(32)
        try:
            append_token = self.store.session_append_token(append_session_id) if append_session_id else None
            prepared = None
            builder = build_practice_batch if schedule_mode == "spaced" or mixed or rounds > 1 or script == "both" else build_questions
            modes = {"kinds": kinds, "rounds": rounds} if builder is build_practice_batch else {"kind": kind}
            if builder is build_practice_batch:
                modes.update(schedule_mode=schedule_mode, adaptive=mixed or script == "both",
                             handwriting_available=self.model_state == "ready", audio_available=bool(self.platform.tts_ready))
            options = dict(**modes, script=script, limit=count, seed=seed, include_research=research,
                           word_table=word_table, kana_categories=categories, word_collection=word_collection, new_only=new_only)
            if getattr(self, "_practice_setup_temporary", False):
                original_builder = builder
                def builder(content, progress, **arguments):
                    questions = original_builder(content, progress, **arguments)
                    for question in questions:
                        question.metadata.setdefault("practice_config", {})["temporary_due_shortcut"] = True
                    return questions
            if hasattr(self, "learning_store"):
                underlying_builder = builder
                def builder(content, progress, _underlying=underlying_builder, **arguments):
                    generated = _underlying(content, progress, **arguments)
                    for question in generated:
                        if question.kind not in WORD_PRACTICE_KINDS:
                            question.metadata.update(kana_intro_v1=True, kana_familiarity_v1=True)
                    return generated
            if isinstance(self.repo, ContentRepository) and isinstance(self.store, StudyStore):
                revision = study_revision(self.store)
                self.practice_count_summary.text = ("正在准备追加内容…原队列中仍启用的题目和作答已保留，可以返回首页取消。" if append_session_id else
                                                    "正在准备练习…数量较多时需要一些时间，可以返回首页取消。")
                self.practice_start_button.text = "正在准备…"
                questions, prepared = await build_in_background(self.repo, self.store, builder, options)
                try:
                    unchanged = (config == self._practice_config() and word_collection == self._practice_word_collection()
                                 and new_only == self._practice_new_only() and kinds == self._practice_kinds()
                                 and rounds == self._practice_rounds() and schedule_mode == self._practice_schedule_mode())
                except (ValueError, TypeError):
                    unchanged = False
                if (self.page != "practice_setup" or page_token != self._page_token or not unchanged
                        or getattr(self, "_leave_confirmation_active", False)
                        or revision != study_revision(self.store)):
                    return
            else:
                questions = builder(self.repo, self.store, **options)
            if not questions:
                self.alert("当前题型与字形下没有剩余新内容，可调整范围或切换为到期优先。" if new_only else "所选范围内没有可练习的到期或新题目。可调整范围，或继续浏览假名与词库。")
                return
            if append_session_id:
                if prepared is not None:
                    self.store.append_prepared_session(append_session_id, prepared, expected_token=append_token)
                else:
                    self.store.append_session(append_session_id, questions, seed=seed, expected_token=append_token)
            else:
                session_id = (self.store.start_prepared_session(prepared) if prepared is not None
                              else self.store.start_session(questions, seed=seed))
        except Exception as exc:
            if self.page == "practice_setup" and page_token == self._page_token:
                self.alert(f"无法生成练习：{exc}")
            return
        KanaMemory._set_queue_kind_policy(self, append_session_id or session_id, kinds)
        from .practice_round import restart
        restart(self.store)
        if append_session_id:
            # Retained old views must never put a shorter queue back in memory.
            self.back_stack.discard(PRACTICE_PAGES)
            self.resume_practice(preserve_audio=True)
            return
        self.questions, self.session_id = questions, session_id
        self.script, self.research_enabled, self.use_full_keyboard = script, research, word_table == "full"
        self.question_index, self.session_right = 0, 0
        self._assisted_attempts.clear()
        self._answered_attempts.clear()
        self.back_stack.discard(PRACTICE_PAGES)
        self.show_question()

    @page_view(replace=True)
    def show_question(self, *, _first_teaching=False, _first_teaching_targets=None):
        lazy = KanaMemory._lazy_practice_questions(self)
        pending = ((self.store.load_session_progress(lazy_questions=True) if lazy else
                    self.store.load_session_progress()) if getattr(self, "session_id", None) else None)
        waiting_until, prepared = None, None
        if isinstance(pending, dict) and pending["session_id"] == self.session_id:
            pending = KanaMemory._reconcile_practice_kinds(self, pending)
            if pending is False:
                return
            if not pending.get("completed"):
                if getattr(self.store, "practice_lane", "main") == "main":
                    from .kana_dependency_repair import ensure_kana_prerequisites
                    repaired = ensure_kana_prerequisites(self.repo, self.store,
                        KanaMemory._queue_kinds(self, pending), lazy_questions=lazy,
                        audio_available=bool(self.platform.tts_ready),
                        handwriting_available=getattr(self, "model_state", None) == "ready")
                    if repaired["added"]:
                        pending = self.store.load_session_progress(lazy_questions=lazy)
                if _first_teaching:
                    prepared = self.store.prepare_next_question(self.session_id,
                        lazy_questions=lazy, fair_new=lazy, teaching_only=True,
                        teaching_kinds=KanaMemory._queue_kinds(self, pending),
                        teaching_targets=_first_teaching_targets)
                if prepared is None:
                    prepared = (self.store.prepare_next_question(self.session_id,
                        lazy_questions=True, fair_new=True) if lazy else
                        self.store.prepare_next_question(self.session_id))
            if prepared is None:
                self.question_index = len(self.questions)
            elif isinstance(prepared, dict):
                self.questions = prepared["questions"]
                self.question_index = prepared["question_index"]
                self.session_right = prepared["session_right"]
                self._assisted_attempts = {(self.session_id, index) for index in prepared["assisted_indices"]}
                if prepared["state"].get("answer_revealed"):
                    self._assisted_attempts.add((self.session_id, self.question_index))
                waiting_until = prepared.get("waiting_until_utc")
        from .practice_round import seal
        seal(self.store)
        if self.question_index >= len(self.questions):
            from .practice_round import active as fixed_round
            if (fixed_round(self.store) and getattr(self, "_study_path_enabled", False)
                    and getattr(self.store, "practice_lane", "main") == "main"):
                self.continue_study_path(_checkpoint_saved=True)
                return
            from .practice_round import mark_finished
            mark_finished(self.store)
            item_count = len({getattr(q, "content_id", index) for index, q in enumerate(self.questions)})
            summary = self.store.get_session_summary(self.session_id) if getattr(self, "session_id", None) else None
            answered = len(self.questions)
            extra = ""
            if isinstance(summary, dict):
                answered, item_count = summary["answered"], summary["content_count"]
                if summary["skipped"]:
                    extra = f"\n跳过 {summary['skipped']} 题（保留原有进度）"
            completion = (f"练习 {item_count} 个假名记忆项 · " if item_count else "") + f"完成 {answered} 题\n答对 {self.session_right} 题"
            if getattr(self.store, "practice_lane", "main") == "word_romaji":
                self.screen("session_done", "本组单词完成",
                    ui.label(f"完成 {answered} 词 · 答对 {self.session_right} 词", size=24, bold=True),
                    self.note("拼写记录已保存；符合条件的假名复习时间已调整。"),
                    ui.button("再练一组单词", self.show_word_drill_setup, primary=True),
                    ui.button("回到今日", self.show_home))
                return
            next_controls = (self._path_next_controls(self.study_path_service().overview())
                if getattr(self, "_study_path_enabled", False) else [ui.button("增加新批次", self.show_new_batch, primary=True)])
            self.screen("session_done", "本批完成", ui.label(completion + extra, size=24, bold=True),
                *next_controls, ui.button("查看复习记录", self.show_progress), ui.button("回到今日", self.show_home))
            return
        self.question = self.questions[self.question_index]
        self.script = self.question.script
        if isinstance(prepared, dict) and prepared.get('content_blocked'):
            from .session_content import RETIRE, retired
            old = next(q for q in self.questions[self.question_index:] if retired(q))
            self.answer_revealed, self.answered_text, self.answer_correct = False, None, False
            self.screen('session_wait', '题目内容已更新',
                self.note('原题和草稿已保存。更新后继续使用原有学习记录。'),
                ui.button('使用更新后的题目', lambda w, sid=self.session_id, rid=old.metadata[RETIRE]:
                    self.replace_content_question(sid, rid), primary=True),
                ui.button('练习设置', self.show_word_drill_setup if getattr(self.store, 'practice_lane', 'main') == 'word_romaji' else self.show_practice_setup),
                ui.button('回到今日', self.show_home))
            return
        if isinstance(prepared, dict) and prepared.get("settings_blocked"):
            self.answer_revealed, self.answered_text, self.answer_correct = False, None, False
            self.screen("session_wait", "题型已暂停",
                self.note("未完成的题目和草稿已保留。开启题型后可继续。"),
                ui.button("练习设置", self.show_word_drill_setup
                       if getattr(self.store, "practice_lane", "main") == "word_romaji"
                       else self.show_practice_setup, primary=True),
                ui.button("回到今日", self.show_home))
            return
        if isinstance(prepared, dict) and prepared.get("dependency_blocked"):
            self.answer_revealed, self.answered_text, self.answer_correct = False, None, False
            symbols, rule_controls = [], []
            for identity in prepared.get("blocked_prerequisites", ()):
                if identity.startswith("pronunciation:"):
                    from .learning_content import get_learning_item
                    rule = get_learning_item(identity)
                    if rule:
                        rule_controls.append(ui.button(rule["title"],
                            lambda w, value=rule: self.enroll_learning(value), primary=True, multiline=True))
                    continue
                content_id, script = identity.removeprefix("kana:").rsplit(":", 1)
                item = self.repo.get_kana(content_id)
                if item and item.get(script):
                    symbols.append(item[script])
            self.screen("dependency_wait", "先学所需规则" if rule_controls else "先完成前置假名",
                self.content_label(" · ".join(dict.fromkeys(symbols)), size=26, color=GREEN, text_role="study"),
                *rule_controls,
                ui.label("原题与进度已保留。" + ("先看规则讲解，再继续练习。" if rule_controls else "请检查这些假名是否已暂停，或相应题型是否可用。"), size=15, color=MUTED),
                ui.button("检查并继续", self.resume_practice, primary=True),
                ui.button("练习设置", self.show_practice_hub),
                ui.button("回到今日", self.show_home))
            return
        started_answer = (isinstance(prepared, dict)
                          and prepared["state"].get("page") in QUESTION_PAGES | {"feedback"})
        if hasattr(self, "learning_store") and not waiting_until and not started_answer:
            if should_introduce(self, self.question):
                self.show_kana_introduction(self.question)
                return
            intro_due = intro_wait_until(self.store, self.question)
            if intro_due and intro_due > app_clock.local_now(intro_due.tzinfo):
                self.show_kana_intro_wait(intro_due)
                return
        config = self.question.metadata.get("practice_config", {})
        self.research_enabled = self.question.metadata.get("research_content", config.get("include_research", False))
        word_table = self.question.metadata.get("word_table", config.get("word_table"))
        if word_table in {"partial", "full"}:
            self.use_full_keyboard = word_table == "full"
        if waiting_until:
            self.answer_revealed, self.answered_text, self.answer_correct = False, None, False
            self._review_wait_until = app_clock.localize(datetime.fromisoformat(waiting_until))
            due = self._review_wait_until.strftime("%H:%M:%S")
            if self._review_wait_until.date() != app_clock.local_now().date():
                due = self._review_wait_until.strftime("%m月%d日 %H:%M:%S")
            self._review_wait_checked = False
            self._review_wait_status = ui.label(review_wait_status(self._review_wait_until), size=20, bold=True, color=GREEN)
            self._review_wait_button = ui.button("检查并继续复习", self.continue_spaced_practice, primary=True)
            self.screen("session_wait", "本轮先歇一会儿",
                self._review_wait_status,
                ui.label(f"下一题可在 {due} 开始", size=15, color=MUTED),
                self.note(f"已保存 {summarize_queue_availability(self.store, prepared).waiting_count} 道后续复习题，到期后自动继续。也可以先回到今日。"),
                self._review_wait_button,
                *(self._path_next_controls(self.study_path_service().overview(), compact=True)
                  if getattr(self, "_study_path_enabled", False) else []),
                ui.button("回到今日", self.show_home))
            # Start from ready work: a timer scheduled directly in an Android
            # touch callback does not wake Toga's idle Python event loop.
            self._review_wait_handle = self.loop.call_soon(KanaMemory._tick_review_wait, self, self._page_token)
            return
        if (self.question.kind in WORD_PRACTICE_KINDS and started_answer
                and not getattr(self, "_restoring_practice", False)):
            # A parked word can become current after rating another question,
            # without reopening the app. Use the same guarded restore path so
            # rendering an empty input cannot overwrite its saved draft/feedback.
            self.resume_practice(preserve_audio=True)
            return
        self.answer_revealed = self._attempt_key() in self._assisted_attempts
        self.answered_text = None
        self.question_audio_ok = self.question.kind not in LISTENING_PRACTICE_KINDS
        self._question_started = time.monotonic()
        self._answer_submitted_at = None
        self._answer_duration_ms = 0
        self.question_notice = ui.label("", size=12, color=MUTED, margin_bottom=2)
        ready_count = (summarize_queue_availability(self.store, prepared).ready_count
                       if isinstance(prepared, dict) else len(self.questions) - self.question_index)
        title = (f"拼写 · 剩 {ready_count} 词" if self.question.metadata.get("word_romaji_drill") else
                 f"练习 · 现在可答 {ready_count} 题")
        if self.question.kind in {"kana_handwriting", "romaji_handwriting"}:
            self.show_ink_page(free=False, title=title)
            return
        if self.question.kind == "word_romaji":
            KanaMemory.show_reverse_word_question(self, title)
            return
        if self.question.kind in KANA_WORD_PRACTICE_KINDS | {"romaji_kana_full"}:
            self.show_word_question(title)
            return
        if self.question.kind == "kana_romaji":
            self.romaji_answer = ""
            self.romaji_input = toga.TextInput(placeholder="输入罗马音", value="", style=Pack(font_size=24, margin_bottom=12))
            if current_platform == "android":
                self.romaji_input._impl.native.setInputType(1 | 0x80000)
            self.romaji_input.on_change = self.change_romaji_answer
            self.romaji_input.on_confirm = lambda w, question=self.question, **kw: self.check_answer(self.romaji_answer) if self.question is question else None
            self.screen("question", title, self.question_notice,
                        self.content_label(self.question.metadata["kana_symbol"], size=52, color=GREEN, bold=True, wrap=False, text_role="study"),
                        answer_hint(self), self.romaji_input,
                        ui.button("提交答案", lambda w, question=self.question, **kw: self.check_answer(self.romaji_answer) if self.question is question else None, primary=True))
            return
        reverse = self.question.kind == "romaji_kana"
        prompt = ui.label(self.question.metadata["romaji"], size=44, color=GREEN, bold=True, wrap=False) if reverse else self.content_label(self.question.metadata["kana_symbol"], size=52, color=GREEN, bold=True, wrap=False, text_role="study")
        choices = [self.question_notice, prompt, answer_hint(self)]
        for option in self.question.options:
            choices.append(ui.button(option, lambda w, answer=option, question=self.question, **kw: self.choose_practice_answer(answer, question), height=54, font_size=22, text_role="study" if reverse else "ui"))
        self.screen("question", title, *choices)

    def _cancel_review_wait(self):
        handle = getattr(self, "_review_wait_handle", None)
        if handle is not None:
            handle.cancel()
            self._review_wait_handle = None

    def _tick_review_wait(self, token):
        if self.page != "session_wait" or self._page_token != token:
            return
        self._review_wait_handle = None
        now = app_clock.local_now(self._review_wait_until.tzinfo)
        self._review_wait_status.text = review_wait_status(self._review_wait_until, now, self._review_wait_checked)
        ready = now >= self._review_wait_until
        self._review_wait_button.text = "继续复习" if ready else "检查并继续复习"
        # Re-dispatch once at the deadline. Waiting ticks never save progress
        # or navigate over a confirmation/another page.
        if ready and not getattr(self, "_leave_confirmation_active", False):
            self.continue_spaced_practice()
        else:
            self._review_wait_handle = self.loop.call_later(1, KanaMemory._tick_review_wait, self, token)

    def continue_spaced_practice(self, widget=None, **kwargs):
        if self.page == "session_wait" and not getattr(self, "_leave_confirmation_active", False):
            if (getattr(self, "_study_path_enabled", False)
                    and getattr(self.store, "practice_lane", "main") == "main"):
                self.continue_study_path()
            else:
                self.show_question()
            if self.page == "session_wait":
                self._review_wait_checked = True
                self._review_wait_status.text = review_wait_status(self._review_wait_until, checked=True)

    def choose_practice_answer(self, answer, question):
        """Commit the answer before showing study material about the option."""
        if self.question is not question or self._preview_pending is not None:
            return
        handwriting = question.kind in {"kana_handwriting", "romaji_handwriting"}
        expected_page = "ink_question" if handwriting else "question"
        if self.page != expected_page or question.kind not in {"kana_choice", "romaji_kana", "kana_handwriting", "romaji_handwriting"}:
            return
        if not handwriting and answer not in question.options:
            return
        self.check_answer(answer)

    def preview_practice_answer(self, answer, question):
        """Keep unique answer context and one detail action after every outcome."""
        if self.page != "feedback" or self.question is not question:
            return
        slot, attempt = self.practice_preview_slot, self._attempt_key()
        slot.clear()
        context, symbol, meaning = "", "", ""
        if question.kind in WORD_PRACTICE_KINDS:
            item = self.repo.get_word(question.content_id)
            open_detail = lambda: self.show_word_detail(question.content_id)
            if item:
                context, meaning = word_answer_context(item, question.answer)
        else:
            item = self.repo.get_kana(question.content_id)
            open_detail = lambda: self.show_kana_detail(question.content_id)
            if item:
                data = {**payload(item), **item}
                base_ids = data.get("enrichment", {}).get("origin_base_kana_ids", [])
                bases = [self.repo.get_kana(base_ids[0])] if len(base_ids) == 1 else []
                origin = compact_kana_origin(item, script=question.script, base_records=bases)
                # A reading answer still needs its one-script target context.
                # Kana answers already display that symbol in the answer block.
                if question.kind in {"kana_choice", "kana_romaji"}:
                    symbol = question.metadata.get("kana_symbol") or item.get(question.script, "")
                roman = kana_learning_romaji(data)
                reading = roman if roman != question.answer else ""
                category = CATEGORY_LABELS.get(data.get("category"), "假名")
                context = " · ".join(part for part in (symbol, reading, category, origin) if part)
        if not item:
            slot.add(ui.label("本题词条资料尚未收录。" if question.kind in WORD_PRACTICE_KINDS else
                           "本题假名资料尚未收录。", size=12, color=MUTED))
            return

        def open_target(widget=None, **kwargs):
            if (self.page == "feedback" and self.question is question and self._attempt_key() == attempt
                    and self.practice_preview_slot is slot and card in slot.children):
                open_detail()

        info = ui.label(context, size=13, color=MUTED, flex=1, margin_bottom=0,
                     text_role="study" if question.kind in WORD_PRACTICE_KINDS else "ui") if context else ui.col(flex=1)
        if symbol:
            configure_study_ranges(info, [(0, len(symbol))])
        summary = ui.col(info, flex=1)
        if meaning:
            summary.add(ui.label(meaning, size=13, margin_bottom=0))
        card = ui.col(ui.row(summary, ui.button("详细", open_target, compact=True, width=72, font_size=13,
                               margin_left=6, margin_bottom=0), align_items="center"), margin_bottom=8)
        if question.kind in WORD_PRACTICE_KINDS:
            # Pitch belongs to the word introduction, not the submitted answer.
            # Its kana are needed to align the marks by mora; do not also print
            # that reading in the summary. Without a contour, show it only if
            # the canonical answer does not already supply it.
            data = {**payload(item), **item}
            reading = data.get("reading") or data.get("display_reading") or ""
            card.add(KanaMemory._pitch_slot(self, item, reading=reading, size=15,
                compact=True, plain=bool(reading and reading != question.answer)))
        card._feedback_fragment = {"kind": "practice_answer_detail", "target_id": question.content_id,
                                   "script": question.script, "context": context, "meaning": meaning}
        slot.add(card)

    def practice_answer_heading(self, heading):
        """One answer-only speaker; it never changes scoring or audio readiness."""
        from .answer_speech import clean_utterance
        question, attempt = self.question, self._attempt_key()
        text, caption = None, ""
        if question.kind in WORD_PRACTICE_KINDS:
            from .domain import word_kana_answer
            text = word_kana_answer(question)
        else:
            item = self.repo.get_kana(question.content_id)
            if isinstance(item, dict):
                plan = kana_audio_plan(item)
                text = plan.text
                caption = plan.label if not plan.isolated else ""
        text = clean_utterance(text or "")
        self._practice_feedback_audio_text = text
        if not text:
            return heading

        def play(widget=None, **kwargs):
            if self.page == "feedback" and self.question is question and self._attempt_key() == attempt:
                self.speak_text(text)

        lines = [heading]
        if caption:
            lines.append(ui.label(caption, size=12, color=MUTED, margin_bottom=0))
        return ui.row(ui.col(*lines, flex=1), ui.speaker_button(play, "朗读正确答案" + (" · " + caption if caption else "")),
                   align_items="start")

    def change_romaji_answer(self, widget, **kwargs):
        if self.page != "question" or self.question.kind != "kana_romaji" or widget is not self.romaji_input:
            return
        self.romaji_answer = widget.value
        self._checkpoint()

    def show_reverse_word_question(self, title):
        question = self.question
        self.word_romaji_plan = word_romaji_unit_plan(question)
        self.word_romaji_parts = []
        self.word_romaji_unit_mode = True
        self.word_romaji_choices = ui.col()
        self.word_romaji_position = ui.label("", size=14, color=MUTED)
        self.word_romaji_current = ui.label("", size=22, bold=True, color=GREEN)
        self.word_romaji_parts_label = ui.label("", size=13, color=MUTED)
        self.word_romaji_answer_label = ui.label("＿", size=26, bold=True, color=GREEN)
        self.word_romaji_delete = ui.button("删除末项",
            lambda w, question=question, **kw: self.delete_word_romaji_unit() if self.question is question else None,
            flex=1, margin_right=6, multiline=True)
        self.word_romaji_submit = ui.button("提交答案",
            lambda w, question=question, **kw: self.submit_word_romaji() if self.question is question else None,
            primary=True, flex=1, multiline=True)
        prompts = [self.question_notice,
            self.content_label(question.metadata["kana_answer"], size=30, bold=True, color=GREEN, text_role="study"),
            ui.label("罗马音", size=13, color=MUTED),
            self.mixed_label("词义：", ContentText(short(question.metadata.get("meaning_hint"), 160)), size=15)]
        word = self.repo.get_word(question.content_id)
        if isinstance(word, dict):
            prompts.append(KanaMemory._pitch_slot(self, word, reading=question.metadata["kana_answer"],
                size=16, masked=True, compact=True))
        prompts.extend([answer_hint(self), self.word_romaji_parts_label, self.word_romaji_answer_label,
            self.word_romaji_position, self.word_romaji_current, self.word_romaji_choices,
            ui.row(self.word_romaji_delete, self.word_romaji_submit)])
        self.screen("question", title, *prompts)
        KanaMemory._refresh_word_romaji_units(self)

    def _refresh_word_romaji_units(self):
        units, index = self.word_romaji_plan["units"], len(self.word_romaji_parts)
        self.word_romaji_parts_label.text = " · ".join(self.word_romaji_parts)
        self.word_romaji_answer_label.text = compose_romaji_parts(self.word_romaji_plan, self.word_romaji_parts) or "＿"
        self.word_romaji_choices.clear()
        self.word_romaji_delete.enabled = bool(index)
        self.word_romaji_submit.enabled = bool(index)
        if index >= len(units):
            self.word_romaji_position.text = f"{len(units)} / {len(units)}"
            self.word_romaji_current.text = ""
            return
        unit = units[index]
        self.word_romaji_position.text = f"{index + 1} / {len(units)}"
        self.word_romaji_current.text = unit["kana"] + (" · 延长音" if unit["operation"] == "length" else "")
        configure_study_ranges(self.word_romaji_current, [(0, len(unit["kana"]))])
        choices = [ui.button(option,
            lambda w, answer=option, question=self.question, step=index, **kw:
                self.choose_word_romaji_unit(answer, question, step),
            flex=1, margin_right=6 if position % 2 == 0 else 0, font_size=20, multiline=True)
            for position, option in enumerate(unit["options"])]
        for start in range(0, len(choices), 2):
            self.word_romaji_choices.add(ui.row(*choices[start:start + 2]))

    def choose_word_romaji_unit(self, answer, question, unit_index):
        if (self.page != "question" or self.question is not question or question.kind != "word_romaji"
                or self._preview_pending is not None or unit_index != len(self.word_romaji_parts)):
            return
        units = self.word_romaji_plan["units"]
        if not 0 <= unit_index < len(units) or answer not in units[unit_index]["options"]:
            return
        self.word_romaji_parts.append(answer)
        KanaMemory._refresh_word_romaji_units(self)
        self._checkpoint()

    def delete_word_romaji_unit(self, widget=None, **kwargs):
        if self.page != "question" or self.question.kind != "word_romaji" or not self.word_romaji_parts:
            return
        self.word_romaji_parts.pop()
        KanaMemory._refresh_word_romaji_units(self)
        self._checkpoint()

    def submit_word_romaji(self, widget=None, **kwargs):
        if self.page != "question" or self.question.kind != "word_romaji":
            return
        self.check_answer(compose_romaji_parts(self.word_romaji_plan, self.word_romaji_parts))

    def _word_romaji_submission(self, answer):
        if (self.question.kind != "word_romaji" or self.answer_revealed
                or not getattr(self, "word_romaji_unit_mode", False)
                or answer != compose_romaji_parts(self.word_romaji_plan, self.word_romaji_parts)):
            return {}
        return {"word_romaji_parts": list(self.word_romaji_parts), "word_romaji_plan": self.word_romaji_plan}

    def show_word_question(self, title):
        from .text_layout import font_scale

        scale = font_scale()
        window_size = self.main_window.size
        # Pitch variants can add several prompt rows. Keep the entire word
        # exercise scrollable, including a retained view after settings change.
        scroll_page = (self.question.kind in WORD_PRACTICE_KINDS or scale > 1.2
                       or getattr(window_size, "height", 820) < 740)
        self.word_answer = ""
        self.word_answer_label = ui.label("＿", size=28, bold=True, color=GREEN, text_role="study")
        keys = (normalize_word_keyboard_options(self.question.answer, self.question.options,
                    self.question.script, word_table=self.question.metadata.get("word_table", "partial"))
                if self.question.kind in WORD_PRACTICE_KINDS else list(dict.fromkeys(self.question.options)))
        available_width = max(1, window_size.width - 32)
        from .kana_keyboard_ui import candidate_keys, key_table
        from . import ui_components as ui_module
        question = self.question
        full_table = (question.kind == "romaji_kana_full"
                      or question.metadata.get("word_table", "partial") == "full")
        keyboard_layout = key_table if full_table else candidate_keys
        keyboard, table_height, table_width = keyboard_layout(ui_module, keys,
            lambda value: self.append_word_key(value) if self.question is question and self.page == "word_question" else None,
            width=available_width, scale=scale)
        if scroll_page and table_width > available_width:
            keyboard = toga.ScrollContainer(content=keyboard, horizontal=True, vertical=False,
                                             style=Pack(height=table_height, margin_bottom=6))
        from .romaji_input_ui import word_converter
        conversion = word_converter(self, ui_module, question)
        meaning_mode = self.question.kind == "word_meaning_kana"
        prompts = [ui.label("片假名" if self.script == "katakana" else "平假名", size=13, color=MUTED),
                   *([self.note("含待审核词汇")] if self.research_enabled else []),
                   self.mixed_label("词义：" if meaning_mode else "词义提示：", ContentText(short(self.question.metadata.get("meaning_hint"), 160)) if self.question.metadata.get("meaning_hint") else "暂无", size=16 if meaning_mode else 12)]
        if self.question.metadata.get("word_romaji_drill"):
            prompts = [ui.label(self.question.metadata["romaji"], size=30, bold=True, color=GREEN),
                       ui.label("片假名" if self.script == "katakana" else "平假名", size=13, color=MUTED),
                       self.mixed_label("词义：", ContentText(short(self.question.metadata.get("meaning_hint"), 160)), size=15)]
        elif self.question.kind == "romaji_kana_full":
            prompts = [ui.label(self.question.metadata["romaji"], size=36, bold=True, color=GREEN),
                       ui.label("片假名" if self.script == "katakana" else "平假名", size=13, color=MUTED)]
        elif meaning_mode:
            prompts.append(ui.label(self.question.metadata["romaji"], size=24, bold=True, color=GREEN))
        else:
            prompts.append(ui.button("播放词语", self.play_question_audio, primary=True))
        if self.question.kind in WORD_PRACTICE_KINDS:
            word = self.repo.get_word(self.question.content_id)
            if isinstance(word, dict):
                prompts.append(KanaMemory._pitch_slot(self, word, reading=self.question.answer,
                    size=16, masked=True, compact=True))
        self.screen("word_question", title, self.question_notice, *prompts, answer_hint(self), self.word_answer_label,
                     *conversion,
                     ui.row(ui.button("删除末字", lambda w, question=self.question, **kw: self.delete_word_key() if self.question is question and self.page == "word_question" else None, flex=1, margin_right=6), ui.button("提交答案", lambda w, question=self.question, **kw: self.check_answer(self.word_answer) if self.question is question and self.page == "word_question" else None, flex=1)),
                     keyboard if scroll_page else toga.ScrollContainer(content=keyboard, horizontal=True, style=Pack(flex=1, margin_bottom=6)), scroll=scroll_page)

    def append_word_key(self, text):
        self.word_answer += text
        self.word_answer_label.text = ui.wrapped(self.word_answer, columns=18)
        self._checkpoint()

    def delete_word_key(self, widget=None, **kwargs):
        self.word_answer = self.word_answer[:-1]
        self.word_answer_label.text = ui.wrapped(self.word_answer or "＿", columns=18)
        self._checkpoint()

    @page_view()
    def show_free_ink(self, widget=None, **kwargs):
        self.show_ink_page(free=True, title="自由试写识别")

    def show_ink_page(self, free, title):
        from .text_layout import font_scale

        window_size = getattr(getattr(self, "main_window", None), "size", None)
        scroll_page = (font_scale() > 1.2 or getattr(window_size, "height", 820) < 740
                       or self.model_state != "ready")
        self.free_ink = free
        self._ink_revision += 1
        self.strokes, self._active_stroke = [], None
        self.ink_width, self.ink_height = 360, 210
        self._ink_view_width, self._ink_view_height = 360, 210
        self.ink_result_box = ui.col()
        self.ink_model_panel = ui.col(self.model_controls(compact=True))
        visual = not free and self.question.kind == "romaji_handwriting"
        self.ink_status_label = ui.label("写一个假名或组合，然后提交识别。" if free else
            "根据罗马音书写，再确认识别结果。" if visual else "先播放声音，再写出要求的假名。", size=12, color=MUTED)
        def drawing_press(widget, x, y, **kwargs):
            # An outer Android ScrollView otherwise intercepts vertical pen
            # movement. Only a gesture starting on this canvas is reserved.
            if scroll_page and current_platform == "android":
                parent = widget._impl.native.getParent()
                if parent is not None:
                    parent.requestDisallowInterceptTouchEvent(True)
            self.ink_press(widget, x, y, **kwargs)

        def drawing_release(widget, x, y, **kwargs):
            try:
                self.ink_release(widget, x, y, **kwargs)
            finally:
                if scroll_page and current_platform == "android":
                    parent = widget._impl.native.getParent()
                    if parent is not None:
                        parent.requestDisallowInterceptTouchEvent(False)

        self.canvas = toga.Canvas(on_press=drawing_press, on_drag=self.ink_drag, on_release=drawing_release, on_resize=self.ink_resize, style=Pack(height=210, background_color=WHITE, margin_bottom=8))
        if current_platform == "android":
            canvas = self.canvas
            self.platform.bind_ink_touch(canvas,
                lambda phase, points: self.ink_touch_batch(canvas, phase, points))
        # Keep the theme inset: white text must stay inside the colored button.
        self.recognize_button = ui.button("识别笔迹", self.recognize_ink, primary=True, flex=1)
        KanaMemory._refresh_ink_action(self)
        children = [ui.label("自由书写，不计入复习" if free else ("请写片假名" if self.script == "katakana" else "请写平假名"), size=17, bold=True)]
        children.append(self.ink_model_panel)
        if not free:
            children.append(self.question_notice)
            children.append(ui.label(self.question.metadata["romaji"], size=30, color=GREEN, bold=True) if visual else
                            ui.button("播放声音", self.play_question_audio))
            children.append(answer_hint(self))
        children += [self.ink_status_label, self.canvas, ui.row(ui.button("撤销一笔", self.undo_ink, flex=1, margin_right=5), ui.button("清空", self.clear_ink, flex=1, margin_right=5), self.recognize_button),
                     self.ink_result_box if scroll_page else toga.ScrollContainer(content=self.ink_result_box, horizontal=False, style=Pack(flex=1))]
        self.screen("free_ink" if free else "ink_question", title, *children, scroll=scroll_page)
        self._redraw_ink()

    def _refresh_ink_action(self):
        ready = self.model_state == "ready" and not self._ink_busy
        self.recognize_button.enabled = ready
        self.recognize_button.text = ("识别中…" if self._ink_busy else {
            "ready": "识别笔迹", "not_downloaded": "未下载模型", "checking": "检查中…",
            "downloading": "下载中…", "error": "模型未就绪", "unavailable": "安卓可用",
        }.get(self.model_state, "模型未就绪"))
        # Explicit text and neutral colors remain understandable even when a
        # platform keeps a disabled button's custom background color.
        if getattr(self.recognize_button, "style", None) is not None:
            self.recognize_button.style.color = WHITE if ready else MUTED
            self.recognize_button.style.background_color = GREEN if ready else "#E0E4E1"

    def ink_resize(self, widget, width, height, **kwargs):
        if not hasattr(self, "strokes") or widget is not self.canvas or width <= 0 or height <= 0:
            return
        # Recognition and stored strokes use stable coordinates; resizing only
        # changes the display transform, never the ink or recognition candidates.
        self._ink_view_width, self._ink_view_height = width, height
        self._schedule_ink_draw()

    def _ink_transform(self):
        width = getattr(self, "_ink_view_width", self.ink_width)
        height = getattr(self, "_ink_view_height", self.ink_height)
        scale = min(width / self.ink_width, height / self.ink_height)
        return scale, (width - self.ink_width * scale) / 2, (height - self.ink_height * scale) / 2

    def _ink_point(self, x, y):
        scale, left, top = self._ink_transform()
        return (x - left) / scale, (y - top) / scale

    def ink_touch_batch(self, widget, phase, points):
        """Keep every Android history sample, with one paint per event batch."""
        if self._ink_busy or widget is not self.canvas or self.page not in INK_PAGES:
            return
        if phase == "down":
            self._active_stroke = None
            if not points:
                return
            x, y = self._ink_point(points[0][0], points[0][1])
            if not (0 <= x <= self.ink_width and 0 <= y <= self.ink_height):
                return
            # MotionEvent uses uptime; stored drafts use our monotonic epoch.
            # Rebase once per gesture, preserving actual within-stroke timing.
            previous = next((s[-1][2] for s in reversed(self.strokes) if s), 0)
            self._ink_event_offset = max(int(time.monotonic() * 1000), previous + 1) - points[0][2]
            self.ink_result_box.clear()
            self._active_stroke = []
            self.strokes.append(self._active_stroke)
        changed = False
        for x, y, timestamp in points:
            if self._active_stroke is None:
                break
            x, y = self._ink_point(x, y)
            if not (0 <= x <= self.ink_width and 0 <= y <= self.ink_height):
                self._active_stroke = None
                break
            self._active_stroke.append((float(x), float(y), timestamp + self._ink_event_offset))
            changed = True
        if changed:
            self._schedule_ink_draw()
        if phase in {"up", "cancel"}:
            self._active_stroke = None
            self._checkpoint()

    def ink_press(self, widget, x, y, **kwargs):
        if self._ink_busy or widget is not self.canvas:
            return
        x, y = self._ink_point(x, y)
        if not (0 <= x <= self.ink_width and 0 <= y <= self.ink_height):
            return
        self.ink_result_box.clear()
        self._active_stroke = [(float(x), float(y), int(time.monotonic() * 1000))]
        self.strokes.append(self._active_stroke)
        self._schedule_ink_draw()

    def ink_drag(self, widget, x, y, **kwargs):
        if self._active_stroke is None or self._ink_busy or widget is not self.canvas:
            return
        x, y = self._ink_point(x, y)
        if not (0 <= x <= self.ink_width and 0 <= y <= self.ink_height):
            self._active_stroke = None
            return
        self._active_stroke.append((float(x), float(y), int(time.monotonic() * 1000)))
        self._schedule_ink_draw()

    def ink_release(self, widget, x, y, **kwargs):
        if widget is not self.canvas:
            return
        self.ink_drag(widget, x, y)
        self._active_stroke = None
        self._checkpoint()

    def _schedule_ink_draw(self):
        if self._paint_handle is None:
            # Native touch callbacks can arrive while the Python loop is idle.
            # Toga Android wakes that loop for call_soon, but not a bare timer.
            # Coalesce pending points; Canvas.invalidate handles frame pacing.
            self._paint_handle = self.loop.call_soon(self._redraw_ink)

    def _redraw_ink(self):
        self._paint_handle = None
        if not hasattr(self, "canvas") or self.page not in INK_PAGES:
            return
        renderer = getattr(self, "_ink_renderer", None)
        if renderer is None or renderer.canvas is not self.canvas:
            renderer = self._ink_renderer = InkRenderer(self.canvas, GREEN)
        renderer.render(self.strokes, self.ink_width, self.ink_height, self._ink_transform())

    def clear_ink(self, widget=None, **kwargs):
        if self._ink_busy:
            return
        self.strokes, self._active_stroke = [], None
        self.ink_result_box.clear()
        self.ink_status_label.text = "已清空，请重新书写。"
        self._redraw_ink()
        self._checkpoint()

    def undo_ink(self, widget=None, **kwargs):
        if self._ink_busy:
            return
        if self.strokes:
            self.strokes.pop()
        self._active_stroke = None
        self.ink_result_box.clear()
        self._redraw_ink()
        self._checkpoint()

    async def recognize_ink(self, widget=None, **kwargs):
        if self._ink_busy:
            return
        if self.model_state != "ready":
            self.ink_status_label.text = "请先完成上方的日语手写模型准备。"
            self.refresh_model_ui()
            return
        if not self.strokes:
            self.ink_status_label.text = "请先在方框中写字。"
            return
        token = self._page_token
        revision = self._ink_revision
        strokes = [list(s) for s in self.strokes]
        self._active_stroke = None
        self._ink_busy = True
        KanaMemory._refresh_ink_action(self)
        self.ink_status_label.text = "正在识别笔迹…"
        try:
            candidates = await self.platform.recognize(strokes, self.ink_width, self.ink_height)
            if token != self._page_token or revision != self._ink_revision:
                return
            self.ink_result_box.clear()
            if not candidates:
                self.ink_status_label.text = "没有识别到文字，请重写；不影响复习记录。"
                return
            choices = (list(dict.fromkeys(candidates))[:5] if self.free_ink else
                       practice_ink_candidates(candidates, self.question.script))
            if not choices:
                script_name = "片假名" if self.question.script == "katakana" else "平假名"
                self.ink_status_label.text = f"没有识别出本题要求的{script_name}，请重写；不影响复习记录。"
                return
            self.ink_status_label.text = "识别候选（请确认你实际写的字）："
            question = None if self.free_ink else self.question
            for start in range(0, len(choices), 3):
                cells = []
                for text in choices[start:start + 3]:
                    choice = ui.button(text, lambda w, value=text, q=question, **kw: self.confirm_ink(value, q),
                                    flex=1, margin=2, font_size=20, compact=True, text_role="study")
                    cells.append(ui.col(choice, ui.label(ink_candidate_kind(text), size=11, color=MUTED,
                                                   text_align="center", margin_bottom=4), flex=1)
                                 if self.free_ink else choice)
                self.ink_result_box.add(ui.row(*cells))
            self.ink_result_box.add(ui.label("候选不符可重写。候选排名不是正确率或字形分数。", size=11, color=MUTED))
        except Exception as exc:
            if token == self._page_token:
                self.ink_status_label.text = ui.wrapped(error_message(exc))
        finally:
            self._ink_busy = False
            if self.page in INK_PAGES:
                KanaMemory._refresh_ink_action(self)

    def confirm_ink(self, text, question=None):
        if self.free_ink:
            if self.page != "free_ink" or question is not None:
                return
            self.ink_status_label.text = "已确认以下文字（未生成复习记录）："
            self.ink_result_box.clear()
            self.ink_result_box.add(self.content_label(text, size=24, bold=True, text_role="study"))
            self.ink_result_box.add(ui.label(ink_candidate_kind(text), size=12, color=MUTED))
        else:
            self.choose_practice_answer(text, question)

    async def play_question_audio(self, widget=None, **kwargs):
        question, token = self.question, self._page_token
        self._audio_serial += 1
        serial = self._audio_serial
        try:
            text = question.audio_text
            # Old saved questions keep their answers, draft and schedule. Resolve
            # only their sound at playback, using the same policy as the detail.
            if question.kind not in WORD_PRACTICE_KINDS:
                catalog = self.repo.kana_list()
                record = next((r for r in catalog if r["id"] == question.content_id), None) if isinstance(catalog, (list, tuple)) else None
                if record is not None:
                    plan = kana_audio_plan(record)
                    if not plan.isolated:
                        raise ValueError("此符号需在词语中示范，不能作为独立听音题。")
                    text = plan.text
            await self.platform.play(text)
            if token == self._page_token and question is self.question and serial == self._audio_serial:
                self.question_audio_ok = True
                self._checkpoint()
        except Exception as exc:
            if token == self._page_token and serial == self._audio_serial:
                self.alert(str(exc))

    def speak_text(self, text):
        token = self._page_token
        self._audio_serial += 1
        serial = self._audio_serial
        async def play():
            try:
                if token != self._page_token or serial != self._audio_serial:
                    return
                await self.platform.play(text)
            except Exception as exc:
                if token == self._page_token and serial == self._audio_serial:
                    self.alert(str(exc))
        self.loop.create_task(play())

    def speak_chinese(self, text, language):
        token = self._page_token
        self._audio_serial += 1
        serial = self._audio_serial
        async def play():
            try:
                if token != self._page_token or serial != self._audio_serial:
                    return
                await self.platform.play_chinese(text, language)
            except Exception as exc:
                if token == self._page_token and serial == self._audio_serial:
                    self.alert(error_message(exc))
        self.loop.create_task(play())

    def play_recording(self, audio):
        token = self._page_token
        self._audio_serial += 1
        serial = self._audio_serial
        async def play():
            try:
                if token != self._page_token or serial != self._audio_serial:
                    return
                path = recording_path(Path(__file__).parent / "resources", audio)
                await self.platform.play_recording(path)
            except Exception as exc:
                if token == self._page_token and serial == self._audio_serial:
                    self.alert(error_message(exc))
        self.loop.create_task(play())

    def open_url(self, url, *, title="外部资料", summary=""):
        if not url or urlsplit(url).scheme not in {"http", "https"}:
            self.alert("该资料尚无可打开的网页地址。")
            return
        message = f"{short(summary, 140)}\n来源网站：{urlsplit(url).hostname}\n将在系统浏览器查看原始资料。".strip()
        self._preview_link(("external_url", url), title, message, lambda: self._open_external_url(url), assisted=True, action="查看原文")

    def _open_external_url(self, url):
        try:
            self.platform.open_url(url)
        except Exception as exc:
            self.alert(str(exc))

    def reveal_answer(self, widget=None, **kwargs):
        if not self.question_audio_ok:
            self.alert("请先完整播放声音。语音失败不会计入复习。")
            return
        self.answer_revealed = True
        self.check_answer("")

    def _word_spelling_plan(self, answer):
        rows = self.repo.kana_list()
        # Lightweight view-test repositories may not supply the kana catalog.
        rows = rows if isinstance(rows, list) else []
        unit_submission = KanaMemory._word_romaji_submission(self, answer)
        if unit_submission:
            analysis = analyze_romaji_parts(self.question.metadata["kana_answer"],
                unit_submission["word_romaji_parts"], self.question.script, rows,
                unit_submission["word_romaji_plan"], assisted=self.answer_revealed)
        elif self.question.kind == "word_romaji":
            analysis = analyze_word_romaji(self.question.metadata["kana_answer"], answer, self.question.script,
                                          rows, assisted=self.answer_revealed)
        else:
            analysis = analyze_spelling(self.question.answer, answer, self.question.script,
                rows, assisted=self.answer_revealed,
                accepted_answers=allowed_word_spellings(self.question.answer,
                    metadata=self.question.metadata, accepted_answers=self.question.accepted_answers))
        if not self.answer_revealed and answer_matches(self.question, answer, **unit_submission) and not analysis["correct"]:
            analysis = {**analysis, "correct": True, "units": [], "errors": [],
                        "evidence_limited": True, "accepted_alternative": True}
        if self.question.kind in WORD_PRACTICE_KINDS and getattr(self, "word_used_conversion", False):
            analysis.update(used_conversion=True, input_method="romaji_conversion_v1",
                            evidence_limited=True, units=[])
            return rows, analysis, []
        if self.question.metadata.get("word_romaji_drill"):
            return rows, analysis, []
        regular = list(KanaMemory._enabled_preference_kinds(getattr(self, "_practice_preferences", {})))
        policy = self.store.get_setting("practice_queue_kinds", {})
        kinds = regular
        if (isinstance(policy, dict) and policy.get("session_id") == self.session_id
                and policy.get("regular_kinds") == regular
                and isinstance(policy.get("kinds"), list)
                and all(kind in ALL_MIXED_KINDS for kind in policy["kinds"])):
            kinds = policy["kinds"]
        preferences = KanaMemory._clean_preferences(getattr(self, "_practice_preferences", {}))
        categories = [category for scope in preferences["scopes"] for category in PRACTICE_SCOPES[scope]]
        corrections = (build_spelling_corrections(self.repo, self.store, self.question,
            analysis, kinds=kinds, question_index=self.question_index,
            pending=getattr(self, "questions", [])[self.question_index + 1:],
            handwriting_available=getattr(self, "model_state", "") == "ready",
            audio_available=bool(getattr(self.platform, "tts_ready", False)),
            kana_categories=categories or None) if rows and categories and isinstance(self.store, StudyStore) else [])
        return rows, analysis, corrections

    @page_view(replace=True)
    def check_answer(self, answer):
        if self.page not in {"question", "word_question", "ink_question"}:
            return
        if self.question.kind in WORD_PRACTICE_KINDS and getattr(self, "word_used_conversion", False):
            if self._checkpoint() is False:
                return
        if not self.question_audio_ok:
            self.alert("请先完整播放声音。语音失败不会计入复习。")
            return
        self.answer_revealed = self.answer_revealed or self._attempt_key() in self._assisted_attempts
        if not answer.strip() and not self.answer_revealed:
            self.alert("请先输入答案，或选择显示答案。")
            return
        self.answered_text = answer
        # Freeze retrieval time before showing feedback. Time spent reading the
        # explanation or resuming days later is not extra retention evidence.
        if not getattr(self, "_answer_submitted_at", None):
            from .domain import utc_now
            self._answer_submitted_at = utc_now().isoformat()
            self._answer_duration_ms = max(0, int((time.monotonic() - self._question_started) * 1000))
        self._answered_attempts.add(self._attempt_key())
        if self.answer_revealed:
            self._assisted_attempts.add(self._attempt_key())
        self.answer_correct = answer_matches(self.question, answer,
            **KanaMemory._word_romaji_submission(self, answer)) and not self.answer_revealed
        feedback_info = {"correct": self.answer_correct, "assisted": self.answer_revealed}
        word_practice = self.question.kind in WORD_PRACTICE_KINDS
        if word_practice:
            _, analysis, corrections = KanaMemory._word_spelling_plan(self, answer)
            feedback_info.update(error_category=analysis.get("error_category"),
                                 accepted_alternative=analysis.get("accepted_alternative", False))
            children = [
                build_answer_feedback(feedback_info),
                build_spelling_comparison(self, self.question.answer, answer,
                    correct=self.answer_correct, assisted=self.answer_revealed),
            ]
            unit_submission = KanaMemory._word_romaji_submission(self, answer)
            if unit_submission and not self.answer_correct and answer == self.question.answer:
                mismatches = [f"第 {index + 1} 个「{unit['kana']}」应选 {unit['answer']}（你选 {part}）"
                    for index, (unit, part) in enumerate(zip(self.word_romaji_plan["units"], self.word_romaji_parts))
                    if unit["answer"] != part]
                children.append(ui.label("请按假名对应选择片段：" + "；".join(mismatches), size=13, color=MUTED))
            if self.answer_revealed:
                children.append(ui.label("本次不计入假名掌握记录。", size=12, color=MUTED))
            elif analysis.get("used_conversion"):
                children.append(ui.label("转换辅助 · 本题不调整假名间隔", size=12, color=MUTED))
            elif analysis.get("evidence_limited") and not analysis.get("accepted_alternative"):
                children.append(ui.label("本次无法确定错处对应的假名，不调整相关记忆记录。" if self.question.kind == "word_romaji" else
                                      "输入过长，本次不调整单个假名的记忆记录。", size=12, color=MUTED))
            elif analysis.get("error_category") == "spelling":
                children.append(ui.label("本次仅记录写法差异，不降低读音掌握程度。", size=12, color=MUTED))
            elif self.question.metadata.get("word_romaji_drill"):
                children.append(ui.label("仅按有效证据轻调相关假名的识读复习间隔。", size=12, color=MUTED))
            elif corrections:
                from .practice_round import active as fixed_round
                children.append(ui.label("错处会用于下一轮安排。" if fixed_round(self.store)
                    else f"保存后追加 {len(corrections)} 道错处补练。", size=12, color=MUTED))
            elif not self.answer_correct:
                children.append(ui.label("保存后记录错处；补练遵循当前题型和范围。", size=12, color=MUTED))
            if analysis.get("ambiguous"):
                children.append(ui.label("对应位置有多种可能，仅记录能确定的假名表现。", size=12, color=MUTED))
        else:
            japanese_answer = self.question.kind in {"romaji_kana", "romaji_kana_full", "romaji_handwriting", "kana_handwriting"}
            if isolated_kana_question(self.question) and self.answer_correct and normalize("NFC", answer).strip() != normalize("NFC", self.question.answer).strip():
                feedback_info["accepted_homophone"] = True
            children = [build_answer_feedback(feedback_info), build_spelling_comparison(self, self.question.answer, answer,
                        correct=self.answer_correct, assisted=self.answer_revealed,
                        text_role="study" if japanese_answer else "ui", size=24)]
        children[0] = KanaMemory.practice_answer_heading(self, children[0])
        if not self.answer_correct or self.answer_revealed:
            from .kana_rule_ui import contextual_rule_ids, rule_help_widgets
            rules = contextual_rule_ids(self.question, analysis if word_practice else None)
            if not word_practice:
                from .kana_reading_rules import dependencies_for_kana, rule_for_kana
                record = self.repo.get_kana(self.question.content_id)
                if isinstance(record, dict):
                    rules = tuple(dict.fromkeys((*rules, *dependencies_for_kana(record),
                        *((rule_for_kana(record),) if rule_for_kana(record) else ()))))
            children.extend(rule_help_widgets(self, rules))
        correction = bool(self.question.metadata.get("spelling_correction_of"))
        labels = [(1, "再学一次")]
        if word_practice:
            labels = [(None, "保存并继续")]
        elif self.answer_correct and correction:
            labels = [(1, "已改对，稍后再复习")]
        elif self.answer_correct:
            labels += [(2, "有些困难"), (3, "记住了"), (4, "很轻松")]
        for rating, text in labels:
            action = ui.button(text, lambda w, r=rating, question=self.question, **kw: self.save_rating(r) if self.question is question else None, primary=word_practice or rating == (3 if self.answer_correct else 1))
            action.enabled = word_practice or self.answer_correct or rating == 1
            children.append(action)
        self.screen("feedback", "拼写反馈" if word_practice else "作答结果", *children)
        self._checkpoint()
        # Every outcome has the same compact details entry, including revealed
        # answers and restored feedback. Grading remains independent of lookup.
        self.preview_practice_answer(answer, self.question)
        autoplay_answer(self, self._practice_feedback_audio_text, ("practice", self._attempt_key()))

    def save_rating(self, rating):
        if self.page != "feedback":
            return
        self.answer_revealed = self.answer_revealed or self._attempt_key() in self._assisted_attempts
        if self.question.kind in WORD_PRACTICE_KINDS:
            rating = None
        if self.answer_revealed and rating != 1 and self.question.kind not in WORD_PRACTICE_KINDS:
            self.alert("已查看学习提示，本题只能选择再学一次。")
            return
        try:
            extra = {}
            from .domain import utc_now
            from .session_content import fingerprint
            finalized_at = utc_now()
            if self.question.kind in WORD_PRACTICE_KINDS:
                rows, _, corrections = KanaMemory._word_spelling_plan(self, self.answered_text)
                if corrections:
                    KanaMemory._set_queue_kind_policy(self, self.session_id,
                        corrections[0].metadata["practice_config"]["kinds"])
                extra = {"kana_rows": rows, "spelling_corrections": corrections,
                         **KanaMemory._word_romaji_submission(self, self.answered_text)}
            result = self.store.record_review(self.question.content_id, self.question.kind, self.question.script, rating,
                                     session_id=self.session_id, question_index=self.question_index, answered_text=self.answered_text,
                                     outcome="correct" if self.answer_correct else "incorrect", audio_ok=self.question_audio_ok,
                                     content_version=self.question.metadata["content_version"], assisted=self.answer_revealed,
                                     duration_ms=getattr(self, "_answer_duration_ms", 0),
                                     now=(finalized_at if self.question.kind in WORD_PRACTICE_KINDS else
                                          datetime.fromisoformat(self._answer_submitted_at) if getattr(self, "_answer_submitted_at", None) else None),
                                     finalized_at=finalized_at, fair_new=KanaMemory._lazy_practice_questions(self),
                                     question_fingerprint=fingerprint(self.question),
                                     **extra)
            if not result["scheduled"]:
                self.alert("这次作答未安排复习：" + result.get("reason", "请重试"))
                return
        except Exception as exc:
            self.alert(f"复习记录尚未保存：{exc}")
            return
        if self.answer_correct and not self.question.metadata.get("spelling_correction_of"):
            self.session_right += 1
        self.question_index += 1
        if (self.question.metadata.get("practice_config", {}).get("schedule_mode") == "spaced"
                or result.get("queue_extended") is True):
            self.questions = (self.store.get_session_questions(self.session_id, lazy=True)
                              if KanaMemory._lazy_practice_questions(self) else
                              self.store.get_session_questions(self.session_id))
        # Retire all earlier question/feedback views after the durable write.
        # Advancing is a replacement, never a route back to a submitted rating.
        self.back_stack.discard(PRACTICE_PAGES)
        self._navigating = True
        try:
            if (getattr(self, "_study_path_enabled", False)
                    and getattr(self.store, "practice_lane", "main") == "main"):
                # record_review has already advanced the durable checkpoint.
                # Capturing this old feedback page would attach its answer to
                # the next question, so dispatch without another checkpoint.
                self.continue_study_path(_checkpoint_saved=True)
                self._view_key = ("continue_study_path", None)
            else:
                self.show_question()
                self._view_key = ("show_question", None)
            self._sync_back_handler()
        finally:
            self._navigating = False

    def alert(self, text):
        async def show():
            await self.main_window.dialog(toga.InfoDialog("提示", error_message(text)))
        self.loop.create_task(show())


def main():
    return KanaMemory()
