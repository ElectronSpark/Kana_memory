"""One continue action routes to the existing kana and course queues."""
from datetime import datetime
from .practice_preferences import clean_preferences, enabled_preference_kinds
from .course_planning import iter_question_plans
from .answer_input import system_japanese_enabled
from . import app_clock

from .navigation import page_view
from .study_path import StudyPathService
from .practice_availability import summarize_queue_availability


_UNREAD = object()


class StudyPathRuntimeMixin:
    def _study_path_unqueued_courses(self):
        """Preview the gap between finalizing a course and saving its next Q.

        A process interruption can leave genuine ability schedules without a
        pending question. Use the continuation planner and append timing rules
        without materializing or saving question snapshots on the home page.
        """
        from .domain import _utc
        from .learning_dependencies import independent_ready_ids, prerequisite_status
        from .learning_questions import KIND_ABILITIES
        now = _utc(None)
        enabled = self.learning_preferences()["enabled"]
        result = {"ready": 0, "waiting": 0, "instruction": 0, "blocked": 0,
                  "blocked_content": 0, "blocked_material": 0, "next_due": None}
        if not enabled:
            return result
        visible_rows = self.store.queries.pending_course_headers(enabled)
        visible_content = {row['content_id'] for row in visible_rows}
        known_teaching = set()
        for row in visible_rows:
            current = self.learning_store.ability_instruction_row(row['content_id'], row['ability'])
            if current['stage'] in {'intro', 'guide'}:
                known_teaching.add(current['introduction_id'])
        blocked_content = set()
        abilities = sorted({KIND_ABILITIES[kind] for kind in enabled})
        missing = self.store.queries.unqueued_course_ids(abilities)
        ready_ids, teaching, future = independent_ready_ids(self.learning_store), set(), []
        for row in missing:
            item = self.learning_store.get_item(row[0])
            # Already assessed lessons are grandfathered by the dependency
            # contract. Only new lessons need a fresh prerequisite inspection.
            for unit, plan in iter_question_plans(item, enabled_kinds=enabled,
                    queries=self.store.queries, learning_store=self.learning_store,
                    system_japanese_input=system_japanese_enabled(self.store)):
                if unit["suspended"]:
                    continue
                if self.learning_store.content_question_blocked(item, plan):
                    result['blocked_material'] += 1
                    continue
                content_row = self.learning_store.ability_instruction_row(item['id'], unit['ability'])
                blocked = self.learning_store.prerequisite_status(item['id'],
                    dict(content_row).get('instruction_scope'), ready_ids=ready_ids)['blocked']
                if blocked:
                    result["blocked"] += 1
                    # A pending sibling ability already contributes this content
                    # to LearningStore.counts()["blocked_content"].
                    if item["id"] not in visible_content:
                        blocked_content.add(item["id"])
                    continue
                if content_row["stage"] in {"intro", "guide"}:
                    if content_row['introduction_id'] not in known_teaching:
                        teaching.add(content_row['introduction_id'])
                    continue
                due = self.learning_store.question_not_before(unit, plan)
                if content_row["stage"] == "bridge" and not self.learning_store._retrieval_ready(content_row, now):
                    due = max(due or "", content_row["retrieval_at_utc"])
                if due and datetime.fromisoformat(due) > now:
                    result["waiting"] += 1
                    future.append(due)
                else:
                    result["ready"] += 1
        result["instruction"] = len(teaching)
        result["blocked_content"] = len(blocked_content)
        result["next_due"] = min(future) if future else None
        return result

    def _study_path_unqueued_reviews(self, pending=_UNREAD):
        """Preview due cards and the next home refresh time without writing.

        A finished queue can leave real review cards but no checkpoint. Count
        their original directions only, using the builder's eligibility rules
        and the same queue policy/service availability as actual resumption.
        Only unfinished directions reserve work; history cannot hide due cards
        or newly unlocked recall directions in a continuously extended queue.
        """
        from . import ui_components as u
        from .app import KanaMemory
        from .domain import (WORD_SKILLS, LISTENING_SKILLS, HANDWRITING_SKILLS,
                             CONTENT_CONTROLS_SETTING, _BatchQuestionStore,
                             build_questions, _utc)
        now = _utc(None)
        pending = self.store.load_session_progress(lane="main", lazy_questions=True) if pending is _UNREAD else pending
        kinds = (KanaMemory._queue_kinds(self, pending) if pending else
                 enabled_preference_kinds(getattr(self, "_practice_preferences", {})))
        from .kana_context_words import preview_context_word
        context_count = int(preview_context_word(self.repo, self.store, kinds,
            audio_available=bool(self.platform.tts_ready), now=now))
        kinds = {kind for kind in kinds if kind not in WORD_SKILLS
                 and (self.platform.tts_ready or kind not in LISTENING_SKILLS)
                 and (self.model_state == "ready" or kind not in HANDWRITING_SKILLS)}
        if not kinds:
            return {"ready": context_count, "waiting": 0, "next_due": None}
        queued = {(q.content_id, q.kind, q.script) for q in
                  (pending["questions"][pending["question_index"]:] if pending else [])
                  if q.kind not in WORD_SKILLS}
        grouped, due_by_key = {}, {}
        controls = self.store.get_setting(CONTENT_CONTROLS_SETTING, {})
        from .kana_routing import candidate_cards, identity, prospective_identities
        cards = candidate_cards(self.store, kinds, now)
        occupied = {identity(self.store, cid, kind, script) for cid, kind, script in queued}
        feedback_due = self.store.feedback_wait_times(
            ((c["content_id"], c["skill"], c["script"]) for c in cards), now)
        for card in cards:
            cid, kind, script = card["content_id"], card["skill"], card["script"]
            control = controls.get(cid, {})
            if kind not in kinds or identity(self.store, cid, kind, script) in occupied or control.get("suspended"):
                continue
            due = datetime.fromisoformat(card["due_at_utc"])
            due = max(due, feedback_due.get((cid, kind, script), due))
            if control.get("buried_until_utc"):
                due = max(due, datetime.fromisoformat(control["buried_until_utc"]))
            grouped.setdefault((kind, script), []).append(cid)
            due_by_key[cid, kind, script] = due
        advancing = self.study_path_service().kana_advancement_targets(kinds, now=now, pending=pending)
        advancement_due = self.store.feedback_wait_times(
            ((entry["content_id"], kind, entry["script"]) for entry, kind, _ in advancing), now)
        for entry, kind, _ in advancing:
            cid, script = entry["content_id"], entry["script"]
            grouped.setdefault((kind, script), []).append(cid)
            due_by_key[cid, kind, script] = advancement_due.get((cid, kind, script), now)
        # Group by direction to avoid rebuilding the whole eligible catalogue
        # once for every due card. Target-only previews create no choices.
        ready, future, by_ability = context_count, [], {}
        count_identities = prospective_identities(self.store, due_by_key) if due_by_key else {}
        for (kind, script), content_ids in grouped.items():
            targets = build_questions(self.repo, _BatchQuestionStore(self.store, content_ids),
                kind=kind, script=script, limit=len(content_ids), seed=0, now=now,
                include_extended=True, _targets_only=True)
            for target in targets:
                due = due_by_key[target.content_id, kind, script]
                key = count_identities[target.content_id, kind, script]
                by_ability[key] = min(by_ability.get(key, due), due)
        ready += sum(due <= now for due in by_ability.values())
        future.extend(due for due in by_ability.values() if due > now)
        # Only future instants arm the home timer. Keeping an already-due
        # instant here would cause a refresh loop until the learner answered.
        return {"ready": ready, "waiting": len(future),
                "next_due": min(future).isoformat() if future else None}

    def study_path_can_continue(self):
        from . import ui_components as u
        from .app import KanaMemory
        pending = self.store.load_session_progress(lane="main", lazy_questions=True)
        available = summarize_queue_availability(self.store, pending,
            allowed_kinds=KanaMemory._queue_kinds(self, pending) if pending else None)
        counts = self.learning_store.counts(self.learning_preferences()["enabled"])
        if available.ready_count or available.blocked_count or counts["ready"] or counts["instruction"]:
            return True
        courses = self._study_path_unqueued_courses()
        return bool(courses["ready"] or courses["instruction"]
                    or self._study_path_unqueued_reviews(pending)["ready"]
                    or self.study_path_service().next_reference_entry() is not None)

    def study_path_service(self):
        # Learner IDs, not the number of rows, detect restored/custom courses.
        ids = self.store.queries.course_ids()
        key = (id(self.store), ids)
        if getattr(self, "_study_path_service_key", None) != key:
            self._study_path_service_value = StudyPathService(self.repo, self.store, self.learning_store)
            self._study_path_service_key = key
        return self._study_path_service_value

    def refresh_study_home(self):
        from . import ui_components as u
        from .app import KanaMemory
        from .study_path_ui import progress_text
        path = self.study_path_service().overview()
        pending = self.store.load_session_progress(lane="main", lazy_questions=True)
        availability = summarize_queue_availability(self.store, pending,
            allowed_kinds=KanaMemory._queue_kinds(self, pending) if pending else None)
        counts = self.learning_store.counts(self.learning_preferences()["enabled"])
        courses = self._study_path_unqueued_courses()
        for name in ("ready", "waiting", "instruction", "blocked", "blocked_content", "blocked_material"):
            counts[name] += courses[name]
        unqueued = self._study_path_unqueued_reviews(pending)
        ready = availability.ready_count + counts["ready"] + unqueued["ready"]
        waiting = availability.waiting_count + counts["waiting"] + unqueued["waiting"]
        dues = [date for date in (availability.next_due_utc, counts.get("next_due_at_utc"),
                                 unqueued["next_due"], courses["next_due"]) if date]
        self._home_queue_due = min(app_clock.localize(datetime.fromisoformat(d)) for d in dues) if dues else None
        next_section = path.get("next_section")
        self.home_action_slot.clear()
        self.home_secondary_slot.clear()
        has_work = (ready or counts["instruction"] or waiting or availability.blocked_count or counts["disabled"] or counts["blocked"]
                    or counts.get('blocked_material') or counts.get('blocked_input') or path["in_progress_sections"])
        if has_work:
            caption = "继续学习"
            handler = self.continue_study_path
            lines = []
            if ready:
                lines.append(f"现在可答 {ready} 题")
            if counts["instruction"]:
                lines.append(f"可开始初学 {counts['instruction']} 项")
            if waiting:
                lines.append(f"待到期 {waiting} 题")
            if counts["blocked_content"]:
                lines.append(f"课程待前置 {counts['blocked_content']} 项")
            if counts.get('blocked_material'):
                lines.append(f"材料待核 {counts['blocked_material']} 题")
            if counts.get('blocked_input'):
                lines.append(f"待调整作答方式 {counts['blocked_input']} 题")
            if availability.blocked_count:
                lines.append(f"假名待前置 {availability.blocked_count} 题")
            hint = " · ".join(lines) or "接着已加入的内容往下学。"
        elif next_section:
            caption, hint = "开始下一小节", next_section["title"]
            handler = lambda w, identity=next_section["id"]: self._add_study_sections([identity])
        else:
            caption, hint, handler = "查看学习路线", "主线内容已完成初学，继续按时复习。", self.show_study_path
        self.home_action_slot.add(u.label("接着学" if has_work else "开始今天的学习", size=22, bold=True, color=u.GREEN),
            u.label(hint, size=14, color=u.MUTED), u.button(caption, handler, primary=True, multiline=True))
        if waiting and self._home_queue_due:
            self.home_action_slot.add(u.label("最近复习 " + self._home_queue_due.strftime("%m月%d日 %H:%M"), size=12, color=u.MUTED))
        if has_work and next_section:
            self.home_secondary_slot.add(u.button("学习下一小节 · " + next_section["title"],
                lambda w, identity=next_section["id"]: self._add_study_sections([identity]), multiline=True))
        self.home_secondary_slot.add(u.row(u.button("学习路线", self.show_study_path, flex=1, margin_right=8),
                                          u.button("添加内容", self.show_study_path_add, flex=1)))
        word_pending = self.store.load_session_progress(lane="word_romaji", lazy_questions=True)
        word_count = KanaMemory._word_drill_remaining(self, word_pending)
        self.home_secondary_slot.add(u.button(f"继续单词拼写（剩 {word_count} 词）" if word_count else "单词拼写",
            lambda w: KanaMemory.open_home_word_drill(self), multiline=True))
        self.home_stats_label.text = (f"主线初学完成 {path['required_completed_sections']}/{path['required_total_sections']} 节"
            f" · 已加入 {path['required_added_sections']} 节")
        if path.get("required_partial_sections"):
            self.home_stats_label.text += f" · 部分加入 {path['required_partial_sections']} 节"
        KanaMemory._arm_home_review_refresh(self)

    @page_view(replace=True)
    def continue_study_path(self, widget=None, *, _checkpoint_saved=False, **kwargs):
        from . import ui_components as u
        from .app import KanaMemory
        from .domain import _utc
        from .review_dispatch import kana_resume_priority, stored_review_priority
        if not _checkpoint_saved and self._checkpoint() is False:
            return
        if KanaMemory._activate_practice_lane(self, "main") is False:
            return
        self.learning_store.ensure_prerequisites()
        service = self.study_path_service()
        preferences = clean_preferences(getattr(self, "_practice_preferences", {}))
        existing = self.store.load_session_progress(lane="main", lazy_questions=True)
        kinds = (KanaMemory._queue_kinds(self, existing) if existing
                 else enabled_preference_kinds(preferences))
        queued = service.ensure_kana_queue(kinds,
            audio_available=bool(self.platform.tts_ready), handwriting_available=self.model_state == "ready",
            lazy_questions=True)
        if queued["session_id"]:
            # Do not overwrite the previous policy of an existing legacy queue.
            pending = self.store.load_session_progress(lazy_questions=True)
            if pending and self.store.get_setting("practice_queue_kinds", {}).get("session_id") != queued["session_id"]:
                KanaMemory._set_queue_kind_policy(self, queued["session_id"], kinds)
        for item in self.learning_store.items_needing_questions():
            self._queue_learning_questions(item)
        pending = self.store.load_session_progress(lane="main", lazy_questions=True)
        now = _utc(None)
        if pending and KanaMemory._queue_kinds(self, pending):
            pending = KanaMemory._reconcile_practice_kinds(self, pending)
            if pending is False:
                return
            # Select the same eligible head that resume will display. This
            # keeps cross-lane ordering consistent with pauses and timed steps.
            pending = (None if pending.get("completed") else
                       self.store.prepare_next_question(pending["session_id"], now=now, lazy_questions=True, fair_new=True))
        availability = summarize_queue_availability(self.store, pending,
            allowed_kinds=KanaMemory._queue_kinds(self, pending) if pending else None, now=now)
        current = self.learning_store.current(self.learning_preferences()["enabled"], now=now, fair_new=True)
        kana_priority = None
        if availability.ready_count and pending:
            q = pending["questions"][pending["question_index"]]
            kana_priority = kana_resume_priority(pending.get("state"))
            if kana_priority is None:
                kana_priority = stored_review_priority(self.store,
                    self.store.get_scheduling_card(q.content_id, q.kind, q.script), now=now)
                from .kana_context_words import context_priority
                # A short contextual word can interrupt ordinary review, but
                # never an already opened course teaching block.
                if not (current and current.get("new_dispatch_continue")):
                    kana_priority = context_priority(q, self.store) or kana_priority
                if kana_priority[0] == 4:
                    from .kana_learning_intro import should_introduce
                    if should_introduce(self, q):
                        kana_priority = (5, 0)
        course_priority = (current.get("dispatch_priority", (4 if current["type"] == "question" else 5, 0))
                           if current and current["type"] in {"question", "instruction"} else None)
        from .first_teaching_dispatch import choose_protected
        normal_kana = ({"type": "feedback" if pending.get("state", {}).get("feedback_pending") else
                        "introduction" if kana_priority[0] == 5 else "question",
                        "question": pending["questions"][pending["question_index"]],
                        "pending": pending, "state": pending.get("state", {}),
                        "session_id": pending["session_id"], "question_index": pending["question_index"],
                        "dispatch_priority": kana_priority} if pending and kana_priority else None)
        protected = choose_protected(self.store, self.learning_store, normal_kana, current,
            enabled_kinds=self.learning_preferences()["enabled"],
            kana_kinds=KanaMemory._queue_kinds(self, pending) if pending else (), now=now)
        if protected:
            protected_lane, protected_head = protected
            if protected_lane == "course":
                self.continue_learning(_selected_current=protected_head)
            else:
                selected_question = protected_head["question"]
                self.resume_practice(preserve_audio=True, _first_teaching=True,
                    _first_teaching_targets=protected_head.get('teaching_targets', {(selected_question.content_id,selected_question.script)}))
            return
        lane = self.store.teaching_queries.choose_lane(kana_priority, course_priority,
                           course_started=bool(current and current.get("new_dispatch_continue")))
        if lane == "kana":
            self.resume_practice(preserve_audio=True)
        elif lane == "course":
            self.continue_learning()
        else:
            # Reference-only symbols have no made-up independent audio cards.
            path = service.overview()
            reference = service.next_reference_entry()
            if reference:
                self.show_study_reference(reference["id"])
            elif (availability.waiting_count and availability.next_due_utc
                  and (not current or not current.get("due_at_utc")
                       or datetime.fromisoformat(availability.next_due_utc)
                       < datetime.fromisoformat(current["due_at_utc"]))):
                self.resume_practice()
            elif current:
                self.continue_learning()
            elif availability.waiting_count:
                self.resume_practice()
            elif availability.blocked_count:
                self.resume_practice()
            elif any(e["kind"] == "kana" and e["state"] in {"added", "started"}
                     for stage in path["stages"] for unit in stage["units"] for section in unit["sections"] for e in section["entries"]):
                self.screen("learning_wait", "暂时没有可进行的假名题型",
                    u.label("已加入的小节仍保留。请检查题型设置、语音或手写模型，以及内容是否已暂停。", size=17),
                    u.button("题型与练习设置", self.show_practice_hub, primary=True),
                    u.button("语音与手写模型", self.show_settings),
                    u.button("学习路线", self.show_study_path), u.button("回到今日", self.leave_learning))
            else:
                self.screen("learning_done", "本次学习已保存",
                    u.label("可以继续下一小节，或稍后按复习安排回来。", size=17),
                    *self._path_next_controls(path),
                    u.button("题型与练习设置", self.show_practice_hub),
                    u.button("回到今日", self.leave_learning))
