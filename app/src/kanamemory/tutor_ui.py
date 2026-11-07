"""Resumable, bounded tutoring beside the existing deterministic practice lanes.

Network work runs away from the UI thread. A response is useful only while the
same page, request and learning context are still current; navigation never
silently appends a late question. Generated practice is not a new SRS unit.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from hashlib import sha256
import json

from .navigation import page_view
from .answer_result_ui import answer_text
from .question_instruction_ui import instruction_widgets
from .question_support import (build_prompt_block, clean_prompt_preferences, learning_question_display,
                               learning_text_role, prompt_audio_text)
from .typography import configure_study_ranges, configure_text_role
from . import ui_components as u


TUTOR_PAGES = {"tutor_center", "tutor_question", "tutor_feedback", "tutor_chat", "tutor_wait", "tutor_loading", "tutor_profile"}


def _current_question_ids(app):
    return {'learning': (getattr(app, '_learning_question', None) or {}).get('id'),
            'ai': (getattr(app, '_ai_current', None) or {}).get('id'),
            'tutor': (getattr(app, '_tutor_current', None) or {}).get('id')}


def _presented_attempt(source, row, current_ids):
    """A real active attempt, as opposed to an unseen scheduled snapshot."""
    from .learning_dependencies import has_started_answer
    draft = row.get('draft', {})
    parts = draft.get('parts')
    started = has_started_answer(draft) or (isinstance(parts, list)
        and any(type(part) is int and part >= 0 for part in parts))
    return (draft.get('presented') is True or started or row['id'] == current_ids[source]
            or source == 'tutor' and 'presented' not in draft)


def _split_tutor_history(messages):
    """Keep the newest user/assistant turn visible, with at most eight messages."""
    messages = messages[-8:]
    if not messages:
        return [], []
    count = 1
    if len(messages) > 1 and messages[-1]['role'] == 'assistant' and messages[-2]['role'] == 'user':
        user, assistant = messages[-2:]
        user_turn, assistant_turn = user.get('turn_id', ''), assistant.get('turn_id', '')
        same_turn = (not user_turn and not assistant_turn or
                     user_turn.endswith(':user') and assistant_turn.endswith(':assistant')
                     and user_turn[:-5] == assistant_turn[:-10])
        if same_turn and user.get('target_id') == assistant.get('target_id'):
            count = 2
    return messages[-count:], messages[:-count]


def _tutor_message_targets(message):
    return {identity for identity in [message.get('target_id'), *message.get('related_target_ids', [])] if identity}


def _profile_target_ids(store, context, state):
    """Show actual recent work before filling with practice-scope suggestions.

    The bounded raw tail avoids replaying years of history. Only independent,
    unretracted records from that tail can add evidence targets. This is a
    display choice and never changes error weights or review scheduling.
    """
    targets = []
    def add(value):
        if isinstance(value, str) and value and value not in targets and len(targets) < 6:
            targets.append(value)
    add((context.get('question') or {}).get('target_id'))
    add((context.get('target') or {}).get('id'))
    for row in reversed(state.get('entries', [])[-12:]):
        if len(targets) >= 3:
            break
        if row.get('result') is not None:
            add(row['question']['target_id'])
    for row in store.queries.recent_evidence_rows():
        if row['eligible'] and not row['retracted']:
            add(row['target_id'])
        if len(targets) >= 6:
            break
    for row in context.get('scope', {}).get('targets', []):
        add(row['id'])
    return targets


class TutorUIMixin:
    def tutor_store(self):
        from .tutor_store import AITutorStore
        cached = getattr(self, "_tutor_store_cache", None)
        if cached is None or cached.store is not self.store:
            cached = self._tutor_store_cache = AITutorStore(self.store)
        return cached

    def _tutor_context(self):
        from .tutor_engine import prepare_tutor_context
        state = self.tutor_store().snapshot()
        context = prepare_tutor_context(self.learning_store, self.learning_preferences()["enabled"],
                                        state)
        if context.get("target"):
            context["target"] = self.learning_store.get_item(context["target"]["id"]) or context["target"]
        completed = [row for row in state["entries"] if row["status"] == "completed"]
        latest = completed[-1] if completed else None
        # Cooling down prevents another test, not a useful explanation of the
        # error. Keep that question's full frozen context separate from the
        # legal new-question scope, and coach a given attempt only once.
        if (not any(row["status"] == "pending" for row in state["entries"])
                and latest and not latest.get("coached")
                and latest["question"]["kind"] in self.learning_preferences()["enabled"]
                and latest.get("source") == "local"
                and latest["result"].get("outcome") in {"incorrect", "revealed"}):
            context["question"] = deepcopy(latest["question"])
            context["result"] = deepcopy(latest["result"])
            context["target"] = self.learning_store.get_item(latest["question"]["target_id"])
            context["correction_row_id"] = latest["id"]
        if hasattr(self, "_queue_kinds"):
            from .tutor_native_routes import native_route_candidates, attach_native_routes
            routes, enabled = native_route_candidates(self.store, self._queue_kinds,
                audio_ready=bool(self.platform.tts_ready), handwriting_ready=self.model_state == "ready")
            attach_native_routes(context, routes, enabled)
        return context

    def _tutor_row(self, row_id):
        return next((row for row in self.tutor_store().snapshot()["entries"] if row["id"] == row_id), None)

    @page_view()
    def show_tutor_center(self, widget=None, **kwargs):
        self.tutor_store().start()
        state = self.tutor_store().snapshot()
        pending = any(row["status"] == "pending" for row in state["entries"])
        caption = "继续针对性练习" if pending else "开始针对性练习"
        self.screen("tutor_center", "针对性练习",
            u.label("结合已学内容和刚才的作答，选择下一步练习。", size=14, color=u.MUTED),
            u.button(caption, self.continue_tutor, primary=True),
            u.button("问 AI", lambda w: self.show_tutor_chat(general=True)),
            *([u.button("继续上次提问", lambda w: self.show_tutor_chat())]
              if state.get("chat_draft") or state.get("chat_context") else []),
            u.button("当前学习画像", self.show_tutor_profile, compact=True),
            u.label("补充训练会小幅影响相关项目的复习间隔。", size=12, color=u.MUTED),
            u.button("AI 服务", self.show_ai_service_settings, compact=True))

    @page_view(replace=True)
    def continue_tutor(self, widget=None, *, use_ai=True, force_ai=False, **kwargs):
        if getattr(self, "_tutor_busy", False):
            return
        self.tutor_store().start()
        context = self._tutor_context()
        row = self.tutor_store().current(self.learning_preferences()["enabled"], context["scope"])
        if row:
            self.show_tutor_question(row)
            return
        harness = context.get("harness", {})
        if harness.get("action") in {"instruction", "continue_course"}:
            target = harness.get("target_id")
            if target:
                # The course controller retains its own first-study, pending
                # question and prerequisite ordering. Never enroll by AI guess.
                self.continue_learning()
            else:
                self.show_study_path_add()
            return
        correction = bool(context.get("correction_row_id"))
        if context.get("decision") == "disabled" or context.get("decision") == "wait" and not correction:
            self._show_tutor_wait(context)
            return
        state = self.tutor_store().snapshot()
        completed = [row for row in state["entries"] if row["status"] == "completed"]
        # Local retrieval has no network latency. Ask the model when recent
        # evidence warrants help, at bounded variation points, or when the local
        # pool is exhausted; an opening exercise never needs an API selection.
        completed_count = state.get("completed_count", len(completed))
        native_routes = any(c.get("native_lane") for c in context.get("candidates", []))
        needs_model = (force_ai or correction or bool(completed_count and completed_count % 3 == 0)
                       or not context.get("candidates") or native_routes)
        if harness.get("mode") == "independent_check" and not correction:
            needs_model = False
        if (use_ai and needs_model and self.ai_connection_status().get("ready")
                and (context.get("scope", {}).get("targets") or correction or native_routes)):
            self.start_tutor_request("next", context=context)
            return
        self._tutor_use_local(context)

    def _show_tutor_wait(self, context):
        when = context.get("wait_until")
        message = ("这一项刚看过答案，先休息或学习其他内容，稍后再独立检验。" if when
                   else "当前题型没有可用题目；原题和草稿已保留。")
        self.screen("tutor_wait", "稍后再练", u.label(message, size=15),
            u.button("检查并继续", self.continue_tutor, primary=True),
            u.button("返回 AI 助手", self.show_ai_center))

    def _tutor_use_local(self, context=None):
        from .tutor_engine import local_next
        context = context or self._tutor_context()
        candidate = next(iter(context.get("candidates", [])), None)
        if candidate and candidate.get("native_lane"):
            self._tutor_resume_native(candidate)
            return
        question = local_next(context)
        if question is None:
            if context.get("decision") in {"wait", "disabled"}:
                self._show_tutor_wait(context)
            else:
                self.screen("tutor_wait", "暂时没有合适的题目",
                    u.label("先完成一些独立学习，或调整练习题型后继续。", size=15),
                    u.button("返回 AI 助手", self.show_ai_center, primary=True))
            return
        self.tutor_store().append_question(question, context["scope"], self.learning_preferences()["enabled"], source="local")
        row = self.tutor_store().current(self.learning_preferences()["enabled"], context["scope"])
        if row:
            self.show_tutor_question(row)
        else:
            self._show_tutor_wait(self._tutor_context())

    def _tutor_resume_native(self, candidate):
        from .tutor_native_routes import resume_native_route
        if not resume_native_route(self, candidate):
            self._show_tutor_wait(self._tutor_context())

    def show_tutor_question(self, current):
        self._tutor_current = deepcopy(current)
        if current.get("result"):
            self.show_tutor_feedback(current["result"])
            return
        q, draft = current["question"], current.get("draft") or {}
        self._tutor_answer_state = deepcopy(draft)
        row_id = current["id"]
        actions = [u.button("显示答案", lambda w: self.submit_tutor_answer("", revealed=True, row_id=row_id),
                            compact=True, font_size=12, flex=1),
                   u.button("问 AI", lambda w: self.open_tutor_question(source="tutor", question_id=row_id),
                            compact=True, font_size=12, flex=1)]
        public_audio = prompt_audio_text(q)
        if public_audio:
            actions.append(u.speaker_button(lambda w: self.play_tutor_prompt(row_id=row_id), "朗读题干"))
        children = []
        prompt = None
        if q.get("audio_text"):
            children.append(u.speaker_button(lambda w: self.play_tutor_prompt(row_id=row_id), "播放题目"))
        elif q.get("input_mode") not in {"pitch_fill", "pitch_identify"}:
            if q.get('input_mode') == 'multi_cloze':
                from .multi_cloze_ui import prompt_view
                prompt = prompt_view(self, u, q, draft,
                    clean_prompt_preferences(self.store.get_setting('prompt_display_v1', {})))
            else:
                prompt = build_prompt_block(self, q, clean_prompt_preferences(
                    self.store.get_setting("prompt_display_v1", {})), size=20)
            prompt._feedback_fragment.update({"kind": "tutor_question", "row_id": row_id,
                                         "question": deepcopy(q), "draft": deepcopy(draft)})
            children.append(prompt)
        children.extend(instruction_widgets(u, q))
        children.append(u.row(*actions))
        if q.get("gloss"):
            children.append(u.label(q["gloss"], size=12, color=u.MUTED))
        display = learning_question_display(q)
        if display.get("context_hint"):
            children.append(u.label(display["context_hint"], size=13, color=u.MUTED))
        if display.get("background_glosses"):
            from .learning_dependencies import independent_ready_ids
            known = independent_ready_ids(self.learning_store)
            glosses = [hint for hint in display["background_glosses"] if hint.get("target_id") not in known]
            if glosses:
                parts, ranges, offset = [], [], 0
                for surface, meaning in dict.fromkeys((hint["surface"], hint["meaning"]) for hint in glosses):
                    part = surface + "：" + meaning
                    parts.append(part)
                    ranges.append((offset, offset + len(surface)))
                    offset += len(part) + 3
                note = u.label(" · ".join(parts), size=13, color=u.MUTED)
                configure_study_ranges(note, ranges)
                children.append(note)
        mode = q.get("input_mode", "choice")
        if mode in {"pitch_fill", "pitch_identify"}:
            def save_pitch(updated):
                self.tutor_store().save_draft(row_id, updated)
                self._tutor_answer_state.update(deepcopy(updated))
            controls = self.learning_pitch_controls(q, draft,
                on_change=save_pitch,
                on_submit=lambda answer: self.submit_tutor_answer(answer, row_id=row_id),
                is_active=lambda: self.page == "tutor_question" and self._tutor_current["id"] == row_id)
            controls._feedback_fragment = {"kind": "tutor_question", "row_id": row_id,
                                           "question": deepcopy(q), "draft": deepcopy(draft)}
            children.append(controls)
        elif mode == 'multi_cloze':
            from .multi_cloze_ui import controls as cloze_controls

            def save_cloze(updated):
                self.tutor_store().save_draft(row_id, updated)
                self._tutor_answer_state.update(deepcopy(updated))

            def submit_cloze(answer):
                self.submit_tutor_answer(answer, row_id=row_id)
                return self.page != 'tutor_question' or self._tutor_current['id'] != row_id

            children.append(cloze_controls(self, u, q, draft, save_cloze,
                submit_cloze,
                lambda: self.page == 'tutor_question' and self._tutor_current['id'] == row_id, prompt=prompt))
        elif mode == "choice":
            children.extend(u.button(choice, lambda w, answer=choice: self.submit_tutor_answer(answer, row_id=row_id),
                multiline=True, text_role=learning_text_role(q, "choice")) for choice in q.get("choices", []))
        elif mode == "reorder":
            used = list(draft.get("parts", []))
            choices = q.get("choices", [])
            text = "".join(choices[index] for index in used)
            children.append(u.label(text or "点击词块组成句子", size=20, text_role="study" if used else "ui"))
            for index, choice in enumerate(choices):
                button = u.button(choice, lambda w, index=index: self.add_tutor_part(index, row_id=row_id),
                                  multiline=True, text_role="study")
                button.enabled = index not in used
                children.append(button)
            children.extend([u.button("撤回末项", lambda w: self.delete_tutor_part(row_id=row_id), compact=True),
                             u.button("提交", lambda w: self.submit_tutor_answer(text, row_id=row_id), primary=True)])
        else:
            from .answer_input_ui import answer_controls
            from .answer_input import available, system_japanese_enabled
            self._tutor_answer_input, controls = answer_controls(u, q, draft.get("answer", ""),
                self.save_tutor_answer, lambda: self.page == "tutor_question"
                and self._tutor_current["id"] == row_id, system_japanese=system_japanese_enabled(self.store))
            children.extend(controls)
            if available(q, system_japanese=system_japanese_enabled(self.store)):
                children.append(u.button("提交", lambda w: self.submit_tutor_answer(
                    self._tutor_answer_input.value or "", row_id=row_id), primary=True))
            else:
                def skip(widget=None):
                    if self.page == "tutor_question" and self._tutor_current["id"] == row_id:
                        self.tutor_store().skip_unavailable_input(row_id)
                        self.continue_tutor()
                children.extend([u.label("这道旧题需要额外的日文输入。可换题，草稿保留且不计错。", size=13, color=u.MUTED),
                    u.button("换一道题", skip, primary=True),
                    u.button("作答设置", self.show_learning_settings, compact=True)])
        self._tutor_status = u.label("", size=12, color=u.MUTED)
        children.append(self._tutor_status)
        self.screen("tutor_question", "针对性练习", *children)
        if draft.get('presented') is not True:
            self.tutor_store().save_draft(row_id, {'presented': True})
            self._tutor_answer_state['presented'] = True
            self._tutor_current.setdefault('draft', {})['presented'] = True

    def save_tutor_answer(self, widget=None, **kwargs):
        if self.page != "tutor_question" or (widget is not None and widget is not getattr(self, "_tutor_answer_input", None)):
            return
        self._tutor_answer_state["answer"] = self._tutor_answer_input.value or ""
        self.tutor_store().save_draft(self._tutor_current["id"], self._tutor_answer_state)

    def add_tutor_part(self, index, *, row_id):
        if self.page != "tutor_question" or self._tutor_current["id"] != row_id:
            return
        draft = deepcopy(self._tutor_answer_state)
        used = list(draft.get("parts", []))
        if index in used:
            return
        used.append(index)
        draft["parts"] = used
        self.tutor_store().save_draft(row_id, draft)
        self.show_tutor_question(self._tutor_row(row_id))

    def delete_tutor_part(self, *, row_id):
        if self.page != "tutor_question" or self._tutor_current["id"] != row_id:
            return
        draft = deepcopy(self._tutor_answer_state)
        draft["parts"] = list(draft.get("parts", []))[:-1]
        self.tutor_store().save_draft(row_id, draft)
        self.show_tutor_question(self._tutor_row(row_id))

    def play_tutor_prompt(self, widget=None, *, row_id, **kwargs):
        if self.page != "tutor_question" or self._tutor_current["id"] != row_id:
            return
        q = self._tutor_current["question"]
        text = q.get("audio_text") or prompt_audio_text(q)
        if not text:
            return
        token = self._page_token

        async def play():
            from .diagnostic_events import record_error
            if token != self._page_token or self.page != "tutor_question":
                return
            try:
                await self.platform.play(text)
            except Exception as exc:
                record_error("tutor_audio", "play", exc)
                if token == self._page_token:
                    self._tutor_status.text = "播放失败，本题尚未评分。请检查日语语音后重试。"
                return
            if token == self._page_token and self.page == "tutor_question":
                draft = {**self._tutor_answer_state, "audio_ok": True}
                try:
                    self.tutor_store().save_draft(row_id, draft)
                except Exception as exc:
                    record_error("tutor_audio", "save", exc)
                    self._tutor_status.text = "语音已播放，但播放状态未保存。请重试；本题尚未评分。"
                    return
                self._tutor_answer_state.update(audio_ok=True)
        self.loop.create_task(play())

    def submit_tutor_answer(self, answer, *, revealed=False, row_id=None):
        if self.page != "tutor_question" or row_id is not None and self._tutor_current["id"] != row_id:
            return
        q = self._tutor_current["question"]
        from .answer_input_ui import prepare_submission
        ready, answer = prepare_submission(q, answer, self._tutor_status, revealed=revealed,
            field=getattr(self, "_tutor_answer_input", None))
        if not ready:
            return
        if not revealed and not str(answer or "").strip():
            self._tutor_status.text = "请先作答。"
            return
        if q.get("audio_text") and not self._tutor_answer_state.get("audio_ok") and not revealed:
            self._tutor_status.text = "请先成功播放题目。"
            return
        try:
            context = self._tutor_context()
            result = self.tutor_store().submit(self._tutor_current["id"], str(answer or ""),
                self.learning_preferences()["enabled"], context["scope"], revealed=revealed)
        except (KeyError, ValueError):
            self._tutor_status.text = "题型或学习范围已变化，原题和草稿已保留。"
            return
        self.platform.stop()
        self._tutor_current = self._tutor_row(self._tutor_current["id"])
        self.show_tutor_feedback(result)

    def show_tutor_feedback(self, result):
        from .answer_feedback import build_answer_feedback
        from .answer_autoplay import autoplay_answer
        q, row_id = self._tutor_current["question"], self._tutor_current["id"]
        from .pitch_practice import is_pitch_question, answer_label
        from .learning_pitch_ui import answer_diagram
        pitch = is_pitch_question(q)
        answer = (answer_diagram(self, q, reveal=True) if pitch else
                  u.label(answer_text(q), size=20, flex=1, text_role=learning_text_role(q, "answer")))
        answer._feedback_fragment = {"kind": "tutor_feedback", "question": deepcopy(q), "result": deepcopy(result)}
        text = self._learning_answer_audio_text(q)

        def play_answer(widget=None, **kwargs):
            if self.page == "tutor_feedback" and self._tutor_current["id"] == row_id:
                self.tutor_store().mark_exposed(q["target_id"])
                self.speak_text(text)

        children = [build_answer_feedback(result), u.row(answer, u.speaker_button(
            play_answer, "朗读答案对应的日语")) if text else answer]
        entered = result.get("answered_text", "")
        if not result.get("correct") and entered and entered != q["answer"]:
            if pitch:
                entered = answer_label(q, entered)
            else:
                entered = answer_text(q, entered)
            children.append(u.label("我的回答：" + entered, size=12, color=u.MUTED))
        explanation = str(result.get("explanation") or q.get("feedback") or "").strip()
        if explanation and explanation != answer_text(q).strip():
            children.append(u.label(explanation, size=15))
        actions = [u.button("问 AI", lambda w: self.open_tutor_question(source="tutor", question_id=row_id,
            after_answer=True), compact=True, flex=1)]
        item = self.learning_store.get_item(q["target_id"])
        if item:
            actions.append(u.button("详细", lambda w: self.show_learning_detail(item,
                answer_context=(deepcopy(q), deepcopy(result))), compact=True, flex=1))
        children.extend([u.row(*actions), u.button("下一题", lambda w: self.finish_tutor_question(row_id=row_id), primary=True)])
        self.screen("tutor_feedback", "训练反馈", *children)
        # Reopening frozen feedback is another answer exposure even when the
        # automatic speaker deduplicates playback after returning to this page.
        self.tutor_store().mark_exposed(q["target_id"])
        autoplay_answer(self, text, ("tutor", row_id))

    def finish_tutor_question(self, widget=None, *, row_id=None, **kwargs):
        if self.page != "tutor_feedback" or row_id is not None and self._tutor_current["id"] != row_id:
            return
        self.tutor_store().complete(self._tutor_current["id"])
        self.continue_tutor()

    def _tutor_origin_row(self, origin):
        source, question_id = origin.get("source"), origin.get("question_id")
        if source == "learning":
            question = self.learning_store.get_question(question_id)
            if question is None:
                return None
            return {"id": question["id"], "question": question,
                    "draft": question.get("draft") or {}, "result": question.get("result"),
                    "status": question.get("status")}
        if source == "tutor":
            return self._tutor_row(question_id)
        if source == "ai":
            # AIPracticeStore's private loader is avoided: the visible current
            # record remains selected until the user explicitly advances it.
            row = self.ai_practice_store().current(self.learning_preferences()["enabled"], self._ai_scope())
            return row if row and row["id"] == question_id else None
        return None

    def _tutor_unanswered_rows(self):
        """Read pending destinations without advancing any practice lane."""
        rows = [('tutor', row) for row in self.tutor_store().snapshot()['entries']
                if row['status'] == 'pending' and not row.get('result')]
        from .ai_practice_store import SETTING
        value = self.store.get_setting(SETTING, {})
        rows.extend(('ai', row) for row in value.get('entries', [])
                    if row['status'] == 'pending' and not row.get('result'))
        for record in self.store.queries.unanswered_course_rows():
            rows.append(('learning', {'id':record[0], 'question':{'target_id':record[1], 'ability':record[3]},
                                      'draft':json.loads(record[2]) or {}}))
        return rows

    def _mark_visible_tutor_help(self, target_ids, *, abilities_by_target=None, sources=None):
        current_ids = _current_question_ids(self)
        for source, row in self._tutor_unanswered_rows():
            if sources is not None and source not in sources:
                continue
            if row['question']['target_id'] not in target_ids:
                continue
            if abilities_by_target is not None and row['question'].get('ability') not in abilities_by_target.get(row['question']['target_id'], ()):
                continue
            old = row.get('draft', {})
            if not _presented_attempt(source, row, current_ids):
                # Unseen future reviews must still allow independent recall
                # after today's answer-exposure cooldown has elapsed.
                continue
            draft = {**old, 'assisted': True, 'hint_used': True}
            if source == 'learning':
                self.learning_store.save_draft(row['id'], draft)
                if row['id'] == current_ids[source]:
                    self._learning_question_state = deepcopy(draft)
            elif source == 'ai':
                self.ai_practice_store().save_draft(row['id'], draft)
            else:
                self.tutor_store().save_draft(row['id'], draft)

    def open_tutor_question(self, *, source, question_id, after_answer=False):
        if source not in {"learning", "ai", "tutor"}:
            return
        expected = {"learning": {"learning_question", "learning_feedback"},
                    "ai": {"ai_question", "ai_feedback"}, "tutor": {"tutor_question", "tutor_feedback"}}
        if self.page not in expected[source]:
            return
        origin = {"source": source, "question_id": question_id, "target_id": "", "after_answer": bool(after_answer)}
        row = self._tutor_origin_row(origin)
        if row is None:
            return
        origin["target_id"] = row["question"]["target_id"]
        origin["after_answer"] = bool(row.get("result"))
        self.tutor_store().start()
        state = self.tutor_store().snapshot()
        draft = state.get("chat_draft", "") if state.get("chat_context") == origin else ""
        self.tutor_store().save_chat_draft(draft, origin)
        self.show_tutor_chat()

    def _visible_tutor_history(self, state, origin, row):
        # Apply answer protection before splitting or offering an expansion.
        show_history = origin.get('source') == 'general' or bool(origin.get('after_answer')) or bool(
            row and (row.get('draft', {}).get('assisted') or row.get('result')))
        if not show_history:
            return []
        messages = [message for message in state.get('messages', [])
                    if not origin.get('target_id') or origin['target_id'] in _tutor_message_targets(message)]
        current_ids = _current_question_ids(self)
        unassisted = {pending['question']['target_id'] for source, pending in self._tutor_unanswered_rows()
                      if not pending.get('draft', {}).get('assisted')
                      and _presented_attempt(source, pending, current_ids)}
        return [message for message in messages if not _tutor_message_targets(message).intersection(unassisted)]

    def _tutor_message_nodes(self, messages, origin):
        nodes = []
        for message in messages:
            node = u.label(message['text'], size=15 if message['role'] == 'assistant' else 13,
                           color=u.INK if message['role'] == 'assistant' else u.MUTED)
            node._feedback_fragment = {'kind': 'tutor_message', 'message': deepcopy(message), 'origin': deepcopy(origin)}
            nodes.append(node)
        for target_id in {target for message in messages if message['role'] == 'assistant'
                          for target in _tutor_message_targets(message)}:
            self.tutor_store().mark_exposed(target_id)
        return nodes

    def toggle_tutor_chat_history(self, widget=None, **kwargs):
        if (self.page != 'tutor_chat' or not getattr(self, '_tutor_history_previous_ids', None)
                or widget is not None and widget is not getattr(self, '_tutor_history_toggle', None)):
            return
        # Change only this container: retain the actual input widget, keyboard,
        # suggestions and in-flight work. Toggling never sends a question.
        self.save_tutor_chat_input()
        self._tutor_history_box.clear()
        self._tutor_history_expanded = not self._tutor_history_expanded
        if self._tutor_history_expanded:
            state = self.tutor_store().snapshot()
            origin = self._tutor_chat_origin
            messages = self._visible_tutor_history(state, origin, self._tutor_origin_row(origin))
            previous = [message for message in messages if message['id'] in self._tutor_history_previous_ids]
            self._tutor_history_box.add(*self._tutor_message_nodes(previous, origin))
        self._tutor_history_toggle.text = '收起之前的对话' if self._tutor_history_expanded else '之前的对话'

    @page_view()
    def show_tutor_chat(self, widget=None, *, general=False, **kwargs):
        self.tutor_store().start()
        state = self.tutor_store().snapshot()
        if general:
            origin = {"source": "general", "question_id": "", "target_id": "", "after_answer": False}
            if state.get("chat_context") != origin:
                self.tutor_store().save_chat_draft("", origin)
                state = self.tutor_store().snapshot()
        origin = state.get("chat_context") or {"source": "general", "question_id": "", "target_id": "", "after_answer": False}
        self._tutor_chat_origin = deepcopy(origin)
        self._tutor_chat_input = u.toga.MultilineTextInput(value=state.get("chat_draft", ""),
            placeholder="例如：为什么这里不能用另一个选项？", on_change=self.save_tutor_chat_input,
            style=u.Pack(height=96, margin_bottom=8))
        row = self._tutor_origin_row(origin)
        latest, previous = _split_tutor_history(self._visible_tutor_history(state, origin, row))
        history = self._tutor_message_nodes(latest, origin)
        if not history:
            history.append(u.label("可以直接提问；答题中求助后会保留提示记录。" if row and not row.get("result")
                                    else "结合你已学的内容提问。", size=14, color=u.MUTED))
        self._tutor_chat_status = u.label("", size=12, color=u.MUTED)
        if getattr(self, '_tutor_memory_notice', ''):
            self._tutor_chat_status.text = self._tutor_memory_notice
        finished = origin.get("source") == "tutor" and row and row["status"] == "completed"
        children = [self._tutor_chat_input, u.button("发送", self.send_tutor_message, primary=not finished),
                    self._tutor_chat_status]
        if origin.get('source') == 'general':
            children.append(u.button('检查掌握情况', self.diagnostic_from_tutor, compact=True))
        children.extend(history)
        self._tutor_suggestions_box = u.col()
        children.append(self._tutor_suggestions_box)
        if finished:
            children.append(u.button("继续练习", self.continue_after_tutor_explanation, primary=True))
        else:
            children.append(u.button("返回题目" if origin.get("question_id") else "返回练习",
                                     self.tutor_chat_back, compact=True))
        self._tutor_history_expanded = False
        self._tutor_history_previous_ids = {message['id'] for message in previous}
        self._tutor_history_box = u.col()
        self._tutor_history_toggle = None
        if previous:
            self._tutor_history_toggle = u.button('之前的对话', self.toggle_tutor_chat_history,
                                                  compact=True, multiline=True)
            children.extend([self._tutor_history_toggle, self._tutor_history_box])
        self.screen("tutor_chat", "问 AI", *children)
        # Render editable input first. Suggestions never replace typed text,
        # send keystrokes, move a queue, or mark the current answer assisted.
        previous = getattr(self, '_tutor_suggestions_task', None)
        if previous is not None and not previous.done():
            previous.cancel()
        if self.ai_connection_status().get('ready'):
            try:
                task_loop = asyncio.get_running_loop()
            except RuntimeError:
                task_loop = self.loop
            self._tutor_suggestions_task = task_loop.create_task(self._load_tutor_suggestions(
                deepcopy(origin), deepcopy(row), self._page_token, self._tutor_suggestions_box))

    async def _load_tutor_suggestions(self, origin, row, token, box):
        from .tutor_suggestions import generate_suggestions
        question = (row or {}).get('question') or {}
        key = (origin.get('source'), origin.get('question_id'), bool((row or {}).get('result')),
               question.get('kind'), question.get('target_id'))
        cache = getattr(self, '_tutor_suggestions_cache', {})
        try:
            suggestions = cache.get(key)
            if suggestions is None:
                suggestions = await asyncio.to_thread(generate_suggestions, self.tutor_client(),
                    kind=question.get('kind', ''), target=question.get('target_id', ''),
                    after_answer=bool((row or {}).get('result')), general=origin.get('source') == 'general')
                cache[key] = suggestions
                self._tutor_suggestions_cache = dict(list(cache.items())[-16:])
            if self.page != 'tutor_chat' or self._page_token != token or box is not self._tutor_suggestions_box:
                return
            for text in suggestions:
                box.add(u.button(text, lambda w, value=text: self.use_tutor_suggestion(value),
                                    compact=True, multiline=True))
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            # Suggestions are optional; failure must not disturb immediate input.
            from .diagnostic_events import record_error
            record_error('tutor_suggestions', 'request', exc)

    def use_tutor_suggestion(self, text):
        if self.page != 'tutor_chat' or getattr(self, '_tutor_busy', False):
            return
        existing = str(self._tutor_chat_input.value or '')
        if existing.strip():
            # The learner can decide whether to replace their own draft.
            self._tutor_chat_status.text = '输入框已有问题；清空后可选用建议。'
            return
        self._tutor_chat_input.value = text
        self.save_tutor_chat_input()

    def save_tutor_chat_input(self, widget=None, **kwargs):
        if self.page != "tutor_chat" or widget is not None and widget is not getattr(self, "_tutor_chat_input", None):
            return False
        try:
            self.tutor_store().save_chat_draft(str(self._tutor_chat_input.value or ""), self._tutor_chat_origin)
        except ValueError:
            self._tutor_chat_status.text = '这段草稿过长或包含不支持的字符，尚未保存；请缩短或整理后再离开。'
            return False
        return True

    def _mark_tutor_origin_assisted(self, origin):
        row = self._tutor_origin_row(origin)
        if not row or row.get("result"):
            return
        draft = deepcopy(row.get("draft") or {})
        draft.update(assisted=True, hint_used=True)
        if origin["source"] == "learning":
            self.learning_store.save_draft(origin["question_id"], draft)
            if getattr(self, "_learning_question", {}).get("id") == origin["question_id"]:
                self._learning_question_state = deepcopy(draft)
        elif origin["source"] == "ai":
            self.ai_practice_store().save_draft(origin["question_id"], draft)
        else:
            self.tutor_store().save_draft(origin["question_id"], draft)
        self.tutor_store().mark_exposed(origin["target_id"])

    def _tutor_chat_context(self, context, origin):
        result = deepcopy(context)
        row = self._tutor_origin_row(origin)
        if row:
            result["question"] = deepcopy(row["question"])
            result["target"] = self.learning_store.get_item(row["question"]["target_id"])
            result["result"] = deepcopy(row.get("result"))
            result["after_answer"] = bool(row.get("result"))
        elif origin.get("source") == "general":
            result["question"], result["target"], result["result"] = None, None, None
        return result

    def send_tutor_message(self, widget=None, **kwargs):
        if self.page != "tutor_chat" or getattr(self, "_tutor_busy", False):
            return
        message = str(self._tutor_chat_input.value or "").strip()
        if not message:
            self._tutor_chat_status.text = "先写下想问的问题。"
            return
        if len(message) > 2000:
            self._tutor_chat_status.text = "问题请控制在 2000 字以内。"
            return
        if not self.ai_connection_status().get("ready"):
            self._tutor_chat_status.text = "请先在 AI 服务中配置个人密钥。"
            return
        if not self.save_tutor_chat_input():
            return
        self._mark_tutor_origin_assisted(self._tutor_chat_origin)
        self.start_tutor_request("ask", message=message)

    def tutor_chat_back(self, widget=None, **kwargs):
        if self.page == "tutor_chat" and not self.save_tutor_chat_input():
            return
        origin = self.tutor_store().snapshot().get("chat_context") or {}
        if origin.get("source") == "learning":
            self.continue_learning()
        elif origin.get("source") == "ai":
            self.continue_ai_practice()
        elif origin.get("source") == "tutor":
            row = self._tutor_origin_row(origin)
            if row and row["status"] == "completed":
                self.continue_after_tutor_explanation()
            else:
                self.continue_tutor(use_ai=False)
        else:
            self.show_tutor_center()

    def continue_after_tutor_explanation(self, widget=None, **kwargs):
        if self.page == "tutor_chat" and not self.save_tutor_chat_input():
            return
        context = self._tutor_context()
        # Interleave a ready local task after coaching without another network
        # selection. Exhausted local variants may still request a fresh task.
        self.continue_tutor(use_ai=not bool(context.get("candidates")))

    def start_tutor_request(self, intent="next", *, context=None, message=""):
        from .ai_service import AIServiceError
        from .tutor_service import make_tutor_request
        if getattr(self, "_tutor_busy", False):
            return
        context = context or self._tutor_context()
        base_fingerprint = context["context_fingerprint"]
        state = self.tutor_store().snapshot()
        origin = deepcopy(state.get("chat_context") or {}) if intent == "ask" else {}
        if intent == "ask":
            context = self._tutor_chat_context(context, origin)
        messages = state.get("messages", [])
        if intent == "ask" and origin.get("target_id"):
            messages = [row for row in messages if origin["target_id"] in _tutor_message_targets(row)]
        messages = [{"role": row["role"], "text": row["text"]} for row in messages[-6:]]
        request_fingerprint = sha256(json.dumps([base_fingerprint, intent, message, origin, messages],
            sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        saved = self.tutor_store().begin_request(intent, request_fingerprint)
        try:
            from .tutor_context import build_learner_context
            from .tutor_memory import TutorMemoryStore
            evidence_id = ''
            if intent == 'ask' and message:
                evidence_id = TutorMemoryStore(self.store).record_message(saved['request_id'], message,
                    (context.get('target') or {}).get('id', ''))
            context['learner_context'] = build_learner_context(self.store, context,
                message_evidence=evidence_id, message=message if intent == 'ask' else '')
            request = make_tutor_request(context, intent, saved["request_id"], message=message,
                                         messages=messages)
        except (ValueError, AIServiceError):
            self.tutor_store().cancel_request(saved["request_id"])
            if intent == "ask":
                self._tutor_chat_status.text = "当前题目暂不能请求讲解，请返回练习后重试。"
            else:
                self._tutor_use_local(context)
            return
        # A process may have stopped after the response was durably saved but
        # before rendering it. Replay the local result, never pay for it twice.
        if saved["status"] == "completed" and saved.get("response"):
            self._tutor_accept_response(saved["response"], context, intent, message, origin, saved["request_id"])
            return
        self._tutor_busy = True
        self._tutor_request_id = saved["request_id"]
        self._tutor_request_intent = intent
        self._tutor_loading_status = u.label("正在准备下一步…" if intent == "next" else "正在回应你的问题…", size=15)
        children = [self._tutor_loading_status, u.button("取消", self.cancel_tutor_request, compact=True)]
        if intent == "next":
            children.append(u.button("先做本地题", self.tutor_continue_local, primary=True))
        self.screen("tutor_loading", "针对性练习" if intent == "next" else "问 AI", *children)
        token = self._page_token
        self._tutor_request_task = self.loop.create_task(self._run_tutor_request(request, context, base_fingerprint,
            request_fingerprint, token, intent, message, origin))

    async def _run_tutor_request(self, request, context, base_fingerprint, request_fingerprint, token, intent, message, origin):
        request_id = request["request_id"]
        saved = False
        phase = 'request'
        try:
            response = await asyncio.to_thread(self.tutor_client().tutor, request)
            if (self.page != "tutor_loading" or self._page_token != token
                    or getattr(self, "_tutor_request_id", None) != request_id):
                return
            current = self._tutor_context()
            if current["context_fingerprint"] != base_fingerprint:
                self.tutor_store().cancel_request(request_id)
                self._tutor_busy = False
                self._tutor_loading_status.text = "学习范围已变化，已保留原题和草稿。请取消后继续。"
                return
            phase = 'save'
            if not self.tutor_store().finish_request(request_id, request_fingerprint, response):
                return
            saved = True
            self._tutor_busy = False
            self._tutor_accept_response(response, context, intent, message, origin, request_id)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            from .diagnostic_events import record_error
            record_error('tutor_request', 'refresh' if saved else phase, exc)
            if self.page == "tutor_loading" and self._page_token == token:
                if not saved:
                    try:
                        self.tutor_store().cancel_request(request_id)
                    except Exception as failure:
                        record_error('tutor_request', 'record_failure', failure)
                self._tutor_busy = False
                self._tutor_loading_status.text = ("回复已保存，但显示未完成；请返回后继续，无需重新请求。" if saved else
                    "暂时未取得可用回复。原题和输入已保存，可以取消后重试。")
        finally:
            if getattr(self, "_tutor_request_id", None) == request_id:
                self._tutor_busy = False

    def _tutor_accept_response(self, response, context, intent, message, origin, request_id):
        # Recheck saved replies too: an older dispatcher response must not
        # reveal a native answer without that queue's assistance bookkeeping.
        if (intent == "next" and any(c.get("native_lane") for c in context.get("candidates", []))
                and response.get("action") != "retrieve"):
            self._tutor_use_local(context)
            return
        store = self.tutor_store()
        self._tutor_memory_notice = ''
        if response.get('memory_updates') and context.get('learner_context'):
            from .tutor_context import reference_sets
            from .tutor_memory import TutorMemoryStore
            memory = TutorMemoryStore(self.store)
            try:
                applied = memory.apply(request_id, response['memory_updates'], **reference_sets(context['learner_context']))
                skipped = memory.last_skipped
            except (ValueError, KeyError):
                skipped = True
            if skipped:
                self._tutor_memory_notice = '本次画像提议未全部应用；保留你较新的纠正和学习记录。'
            elif applied:
                self._tutor_memory_notice = '已保存这次画像更新。'
        action = response.get("action")
        if intent == "ask" or action == "explain":
            correction_row_id = context.get("correction_row_id", "") if intent == "next" else ""
            if intent == "ask" and origin.get("source") == "tutor" and origin.get("after_answer"):
                row = self._tutor_origin_row(origin)
                if row and (row.get("result") or {}).get("outcome") in {"incorrect", "revealed", "unresolved"}:
                    correction_row_id = row["id"]
            target_id = (origin.get("target_id") or (context.get("question") or {}).get("target_id")
                         or response.get("target_id")
                         or (context.get("target") or {}).get("id", ""))
            exposed = {target_id, origin.get("target_id"), response.get("target_id"),
                       (context.get("target") or {}).get("id"),
                       (context.get("question") or {}).get("target_id")}
            # General explanations may legitimately have no primary target.
            # Only the bounded literal matches actually retained in this wire
            # context count; broad scope/history and trimmed facts do not.
            learner = context.get('learner_context') or {}
            sent_facts = {fact['id'] for fact in learner.get('facts', [])}
            exposed.update(match['target_id'] for match in learner.get('retrieval', [])
                           if match.get('source') == 'saved_learning_content' and match.get('target_id') in sent_facts)
            self._mark_visible_tutor_help(exposed - {None, ''})
            if intent != "ask":
                origin = {"source": "tutor" if correction_row_id else "general", "question_id": correction_row_id,
                          "target_id": (context.get("question") or {}).get("target_id", target_id),
                          "after_answer": bool(correction_row_id)}
            store.commit_chat_response(request_id, message, target_id=target_id,
                                       exposed_target_ids=sorted(exposed - {None, ""}), chat_context=origin,
                                       coached_row_id=correction_row_id)
            self.show_tutor_chat()
            return
        if action == "retrieve":
            candidate = next((item for item in context["candidates"] if item["candidate_id"] == response["candidate_id"]), None)
            if candidate and candidate.get("native_lane"):
                self._tutor_resume_native(candidate)
                return
            question = candidate["question"] if candidate else None
            source = "local"
        elif action == "generate":
            question, source = response.get("question"), "ai"
        else:
            question, source = None, "local"
        if question is None:
            self._tutor_use_local(self._tutor_context())
            return
        store.append_question(question, context["scope"], self.learning_preferences()["enabled"], source=source)
        current = store.current(self.learning_preferences()["enabled"], context["scope"])
        if current:
            self.show_tutor_question(current)
        else:
            self._show_tutor_wait(self._tutor_context())

    def cancel_tutor_request(self, widget=None, *, navigate=True, **kwargs):
        request_id = getattr(self, "_tutor_request_id", None)
        self._tutor_request_id = None
        self._tutor_busy = False
        if request_id:
            self.tutor_store().cancel_request(request_id)
        task = getattr(self, "_tutor_request_task", None)
        if task is not None and not task.done():
            task.cancel()
        if navigate:
            if getattr(self, "_tutor_request_intent", None) == "ask":
                self.show_tutor_chat()
            else:
                self.show_tutor_center()

    def tutor_continue_local(self, widget=None, **kwargs):
        self.cancel_tutor_request(navigate=False)
        self.continue_tutor(use_ai=False)

    @page_view()
    def show_tutor_profile(self, widget=None, **kwargs):
        from .tutor_memory import TutorMemoryStore
        from .learning_evidence import LearningEvidenceStore
        self.tutor_store().start()
        notes = TutorMemoryStore(self.store).current(limit=20)
        context = self._tutor_context()
        targets = _profile_target_ids(self.store, context, self.tutor_store().snapshot())
        evidence = LearningEvidenceStore(self.store).context(targets, limit=6)
        exposed = {}
        children = [u.label('画像只用于辅导；可以纠正其中不符合当前情况的描述。', size=13, color=u.MUTED)]
        for note in notes:
            identity = note['claim_id']
            field = u.toga.MultilineTextInput(value=note['text'],
                style=u.Pack(height=max(96, round(80 * u.font_scale())), margin_bottom=4))
            field._feedback_fragment = {'kind': 'tutor_profile_note', 'note': deepcopy(note)}
            children.extend([u.label('你的说明' if note['source'] == 'user' else '待确认的观察', size=12, color=u.MUTED), field,
                u.row(u.button('保存纠正', lambda w, cid=identity, editor=field: self.correct_tutor_note(cid, editor.value),
                               compact=True, flex=1, multiline=True),
                      u.button('移除', lambda w, cid=identity: self.correct_tutor_note(cid, ''),
                               compact=True, flex=1, multiline=True))])
        if not notes:
            children.append(u.label('目前没有长期描述。', size=14))
        for target in evidence['targets']:
            item = self.learning_store.get_item(target['target_id'])
            title = item.get('title', target['target_id']) if item else target['target_id']
            children.append(u.label(f"{title} · 独立应用 {target['successes']}/{target['opportunities']}", size=15))
            from .semantic_errors import pattern_label
            seen_events = set()
            for pattern in [*target.get('semantic_patterns', []), *target['patterns'][:2]]:
                label = pattern_label(target['target_id'], pattern) if pattern.get('semantic') else None
                if label and pattern.get('errors', 0) > 0:
                    exposed.setdefault(target['target_id'], set()).add(pattern['ability'])
                    status = {'recurring': '多次出现', 'tentative': '近期观察', 'stale': '较早记录'}.get(pattern.get('status'), '近期观察')
                    node = u.col(u.label(label['title'], size=14),
                        u.label(label['context'] + ' · ' + status, size=12, color=u.MUTED))
                    node._feedback_fragment = {'kind': 'semantic_error_pattern', 'target_id': target['target_id'],
                                              'pattern': deepcopy(pattern), 'label': label}
                    children.append(node)
                for event in pattern['examples'][:2]:
                    if event['outcome'] == 'incorrect' and event['event_id'] not in seen_events:
                        exposed.setdefault(target['target_id'], set()).add(pattern['ability'])
                        seen_events.add(event['event_id'])
                        node = u.label(f"{event.get('prompt', '')}\n{event['answered_text']} → {event['answer']}", size=13)
                        node._feedback_fragment = {'kind': 'error_evidence', 'target_id': target['target_id'], 'event': deepcopy(event)}
                        children.extend([node,
                            u.button('这条错误记录不适用', lambda w, eid=event['event_id']: self.retract_tutor_evidence(eid), compact=True)])
        children.append(u.button('返回练习', self.show_tutor_center))
        # Save assistance before rendering any recalled answer. Only already
        # presented attempts of the displayed ability lose independent credit;
        # unseen future questions and frozen results are preserved.
        if exposed:
            self._mark_visible_tutor_help(set(exposed), abilities_by_target=exposed)
            self.retention_checks().expose_many(
                [{'id': cid, 'abilities': sorted(abilities)} for cid, abilities in exposed.items()], 'summary')
            for cid in exposed:
                self.tutor_store().mark_exposed(cid)
        self.screen('tutor_profile', '当前学习画像', *children)

    def correct_tutor_note(self, claim_id, text):
        from .tutor_memory import TutorMemoryStore
        TutorMemoryStore(self.store).correct(claim_id, str(text or '')[:240])
        self.show_tutor_profile()

    def retract_tutor_evidence(self, event_id):
        from .learning_evidence import LearningEvidenceStore
        LearningEvidenceStore(self.store).retract(event_id, '用户报告该归因不适用')
        self.show_tutor_profile()

    def tutor_back(self):
        """Return from chat/requests without advancing or recreating a question."""
        if self.page == "tutor_loading":
            self.cancel_tutor_request()
        elif self.page == "tutor_chat":
            self.tutor_chat_back()
        elif self.page in {"tutor_question", "tutor_feedback"}:
            if getattr(self, "_leave_confirmation_active", False):
                return True
            self._leave_confirmation_active = True
            self.platform.stop()
            token = self._page_token

            async def confirm():
                try:
                    accepted = await self.main_window.dialog(u.toga.ConfirmDialog(
                        "返回首页？", "练习和已输入的答案已保存，可以稍后继续。"))
                    if accepted and self._page_token == token:
                        self.leave_tutor_home()
                finally:
                    self._leave_confirmation_active = False
            self.loop.create_task(confirm())
        else:
            self.leave_tutor_home()
        return True

    def leave_tutor_home(self, widget=None, **kwargs):
        self.platform.stop()
        self.back_stack.pages.clear()
        self._practice_context = False
        self._view_key = None
        self._returning_home = True
        try:
            self.show_home()
        finally:
            self._returning_home = False
        self.back_stack.pages.clear()
