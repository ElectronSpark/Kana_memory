"""Batch introduction cards and the batch quiz for new words/grammar (四之二).

Pages call LearningStore public methods only: no SQL, connection or pending
queue access here. intro_batch owns the rules; the store owns every write.
Card choices are self-reports and quiz answers are guided evidence: neither
creates a formal question, learning event or scheduler change.
"""
from __future__ import annotations

from . import intro_batch
from . import ui_components as u
from .learning_questions import (build_learning_question, grade_learning_answer, question_kinds,
                                 practice_templates, question_requires, retired_practice_variants,
                                 sound_variants,
                                 KIND_ABILITIES, KIND_LABELS, LISTENING_KINDS, READING_CHOICE_KIND,
                                 SOUND_KINDS)
from .new_dispatch import study_path_available
from .question_instruction_ui import instruction_widgets
from .question_support import learning_text_role, option_labels
from .structured_lesson_ui import block_widget, teaching_block

INTRO_BATCH_PAGES = {"learning_card", "learning_batch_quiz"}
_RECOGNITION_KINDS = {"word": ("vocab_meaning", READING_CHOICE_KIND), "grammar": ("grammar_meaning",)}
REVIEW_HINT = "有到期复习，建议先复习"
QUIZ_PAUSED = "未完成题目已保留，重新开启对应题型后可继续。"
CARD_BUTTON = "说明卡"
# Units with fewer reviews than this are "early" (四之二 item 6).
EARLY_REVIEWS = 2
ABILITY_NAMES = {"understanding": "理解", "reading": "读法", "listening": "听辨", "use": "使用",
                 "application": "应用"}
KIND_CUES = {READING_CHOICE_KIND: "先选读音", "vocab_reading": "写出读法"}


def ability_hint(question):
    """One line for the first formal question of a later ability."""
    ability = ABILITY_NAMES.get(question.get("ability"), "练习")
    return f"新能力：{ability} — {KIND_CUES.get(question.get('kind'), KIND_LABELS.get(question.get('kind'), '练习'))}"


def background_known(question, known_ids):
    """+1 context: an authored task's background items are all known.

    Questions without author metadata name no background item.
    """
    from .learning_practice_policy import practice_metadata
    meta = practice_metadata(question) if "practice" in question else None
    return meta is None or set(meta["background_ids"]) <= {question.get("target_id"), *known_ids}


def _presentable(question):
    # The batch quiz page presents choices; other operations keep their own guide.
    return question.get("input_mode", "choice") == "choice" and question.get("answer") in question.get("choices", [])


def requirements_met(item, question, known_ids):
    """Dependency gate for one built question (design 11 section 5, D3).

    True when the question's declared requirements (reviewed background ids
    plus its question_dependencies entry) are all known, and for a question
    that declares nothing (a fallback template). Unmet requirements fail.
    """
    requires = question_requires(item, question)
    return requires is None or requires <= {question.get("target_id"), item.get("id"), *known_ids}


def _quiz_variants(item, kind, known_ids=()):
    # The quiz starts from its usual second variant and only then tries the
    # kind's other templates, so a met or fallback variant is found before
    # moving to a less preferred kind. A sound kind offers its known-word
    # variants (options from known words) in the same order.
    try:
        templates = practice_templates(item)
        sound = [number for number, _ in sound_variants(item, kind, known_ids)] if kind in SOUND_KINDS else []
    except (ValueError, KeyError, TypeError):
        templates, sound = None, []  # build_learning_question reports the lesson's own error per kind.
    if sound:
        return (*sound[1:2], *sound[:1], *sound[2:])
    count = len(templates.get(kind) or ()) if isinstance(templates, dict) else 0
    return (1, *(variant for variant in range(max(count, 2)) if variant != 1))


def _retired(item):
    try:
        templates = practice_templates(item)
    except (ValueError, KeyError, TypeError):
        return frozenset()  # build_learning_question reports the lesson's own error per kind.
    return retired_practice_variants(item, templates)


def intro_quiz_question(item, enabled_kinds, *, ability=None, known_background_ids=None):
    """One guided recognition question, else any enabled guided choice question.

    With known_background_ids a question is taken only when requirements_met:
    a question with unmet requirements is never the quiz question, and a
    question declaring nothing is the fallback of its kind. Kind preference
    (recognition first) still decides before the gate; None when no enabled
    kind offers a presentable, requirement-safe question. Retired variants
    (exact retirements and the cognate-class rule, design 11 S2) are skipped
    as in ordinary selection, so an S word's quiz is its one bare task.
    """
    admitted = set(item.get("enrolled_abilities") or [unit["ability"] for unit in item.get("units", [])])
    if ability is not None:
        admitted &= {ability}
    available = [kind for kind in question_kinds(item) if kind in set(enabled_kinds)
                 and KIND_ABILITIES.get(kind) in admitted]
    preferred = [kind for kind in _RECOGNITION_KINDS.get(item.get("family"), ()) if kind in available]
    fallback = [kind for kind in available if kind not in preferred and kind not in LISTENING_KINDS]
    retired = _retired(item) if known_background_ids is not None else frozenset()
    for kind in (*preferred, *fallback):
        for variant in (_quiz_variants(item, kind, known_background_ids) if known_background_ids is not None
                        else (1,)):
            if (kind, variant) in retired:
                continue
            try:
                question = build_learning_question(item, kind, variant=variant, guided=True)
            except (ValueError, KeyError):
                continue
            if not _presentable(question):
                continue
            if known_background_ids is None or requirements_met(item, question, known_background_ids):
                return question
    return None


def intro_quiz_questions(item, enabled_kinds, *, ability=None, known_background_ids=None):
    """The recognition question, plus a second one for a re-ask when possible.

    Like guided_practice_sequence, the second question uses another actual
    context and prefers another kind; it must be an authored choice question
    of the same ability. Without such a question the item keeps only one.
    """
    from .learning_practice_policy import practice_candidates, question_context
    first = intro_quiz_question(item, enabled_kinds, ability=ability, known_background_ids=known_background_ids)
    if first is None:
        return []
    kinds = [kind for kind in question_kinds(item) if kind in set(enabled_kinds)
             and KIND_ABILITIES.get(kind) == KIND_ABILITIES.get(first["kind"]) and kind not in LISTENING_KINDS]
    context = question_context(first)
    candidates = sorted((candidate for candidate in practice_candidates(
                             item, kinds, known_background_ids=tuple(known_background_ids or ()))
                         if candidate["input_mode"] == "choice" and not candidate.get("relaxed")
                         and not candidate.get("top_up")),
                        key=lambda candidate: (candidate["kind"] == first["kind"], candidate["stage"],
                                               candidate["variant"]))
    for candidate in candidates:
        try:
            question = build_learning_question(item, candidate["kind"], variant=candidate["variant"], guided=True)
        except (ValueError, KeyError):
            continue
        if _presentable(question) and question["id"] != first["id"] and question_context(question) != context:
            return [first, question]
    return [first]


class IntroBatchUIMixin:
    def _intro_batch_fair(self):
        from .new_dispatch import enabled
        return enabled(self)

    def _intro_batch_known_ids(self, extra=()):
        """Items a batch sentence may use besides the new one: ready items,
        introduced kana (design 11 section 5, D5) and this batch."""
        from .course_planning import known_ids
        state = self.learning_store.intro_batch()
        return {*known_ids(self.store.queries),
                *(entry["content_id"] for entry in (state or {}).get("entries", ())), *extra}

    def _intro_batch_continue(self):
        # Same continuation as finishing a single guide.
        if study_path_available(self) and callable(getattr(self, "continue_study_path", None)):
            self.continue_study_path(_checkpoint_saved=True)
        else:
            self.continue_learning()

    def route_intro_batch(self, current, enabled):
        """Resume an open batch or open one at an eligible instruction head.

        current() keeps owning dispatch order: due reviews, saved answers,
        explicit starts, waits and blocks decide as before. An open batch is
        resumed (from its saved cursor) when the dispatched instruction head
        is one of its items; another head keeps its own route, so a paused
        batch (closed kind, suspension, prerequisite) never blocks other work.
        """
        kind = (current or {}).get("type")
        state = self.learning_store.intro_batch()
        if state is not None:
            content = (current or {}).get("content") or {}
            if (kind == "instruction" and content.get("instruction_scope") is None
                    and any(entry["content_id"] == content.get("id") for entry in state["entries"])):
                self.show_intro_batch()
                return True
            return False
        if kind != "instruction":
            return False
        content = current.get("content") or {}
        if (content.get("instruction_scope") is not None or content.get("family") not in intro_batch.SIZE_RANGES
                or content.get("stage") != "intro" or (content.get("checkpoint") or {}).get("initial_familiarity")):
            return False
        candidates = self.learning_store.intro_batch_candidates(enabled, content["id"], fair_new=self._intro_batch_fair())
        # Only items whose batch quiz can actually be presented join a batch.
        def quizzable(cid):
            # The batch teaches the easiest admitted ability first (as start_intro_batch).
            item = self.learning_store.instruction_item(cid)
            admitted = item.get("enrolled_abilities") or [unit["ability"] for unit in item["units"]]
            try:
                first = intro_batch.first_ability(item["family"], admitted)
            except ValueError:
                return False
            return intro_quiz_question(item, enabled, ability=first, known_background_ids=known) is not None
        known = self._intro_batch_known_ids(candidates)
        candidates = [cid for cid in candidates if quizzable(cid)]
        if not candidates or candidates[0] != content["id"]:
            return False
        try:
            self.learning_store.start_intro_batch(candidates, family=content["family"])
        except ValueError:
            return False
        self.show_intro_batch()
        return True

    def show_intro_batch(self, widget=None, **kwargs):
        state = self.learning_store.intro_batch()
        if state is None:
            self._intro_batch_continue()
            return
        entries = state["entries"]
        if state["phase"] == "cards" and state["card_index"] < len(entries):
            self.show_intro_card(entries[state["card_index"]])
            return
        if state["phase"] == "cards" and not intro_batch.quiz_items(state):
            # Every item chose "我已经会了": an empty quiz closes the batch.
            self.learning_store.start_intro_quiz({})
            self.learning_store.complete_intro_batch(fair_new=self._intro_batch_fair())
            self._intro_batch_continue()
            return
        self.show_intro_batch_quiz()

    def intro_batch_review_due(self):
        """Due review count shown as a hint only; it never blocks a batch."""
        enabled = self.learning_preferences()["enabled"]
        count = self.learning_store.counts(enabled)["ready"]
        if not count and study_path_available(self) and hasattr(self.store, "load_session_progress"):
            from .practice_availability import summarize_queue_availability
            pending = self.store.load_session_progress(lane="main", lazy_questions=True)
            count = summarize_queue_availability(self.store, pending).ready_count
        return count

    def intro_batch_review_hint(self):
        if self.learning_store.intro_batch() is None or not self.intro_batch_review_due():
            return []
        return [u.label(REVIEW_HINT, size=13, color=u.MUTED)]

    def show_intro_card(self, entry):
        from .assessment_policy import teaching_allowed
        state = self.learning_store.intro_batch()
        item = self.learning_store.instruction_item(entry["content_id"])
        if item is None or item.get("introduction_id") != entry["introduction_id"] or not teaching_allowed(item, store=self.store):
            self.screen("learning_wait", "说明卡暂不可用", u.label("这一项的说明已变化，原有进度已保存。"),
                u.button("回到今日", self.leave_learning, primary=True))
            return
        item = self.learning_store.mark_instruction_exposed(item["id"], expected_introduction_id=item["introduction_id"])
        self._learning_item = item
        self._intro_card_entry = dict(entry)
        index = next(i for i, e in enumerate(state["entries"]) if e["content_id"] == entry["content_id"])
        expected = dict(entry)
        self.screen("learning_card", "新项说明",
            u.label(f"第 {index + 1} / {len(state['entries'])} 项", size=13, color=u.MUTED),
            *self.intro_batch_review_hint(), *self._intro_card_body(item),
            u.button("下一项", lambda w: self.choose_intro_card("next", expected=expected), primary=True),
            u.button("我已经会了", lambda w: self.choose_intro_card("known", expected=expected)),
            u.button("稍后再学", self.leave_learning))

    def _intro_card_body(self, item):
        """说明卡 content, shared by the card page and wrong-answer feedback."""
        word = item["family"] == "word"
        children = [u.label(item["title"], size=25, bold=True, color=u.GREEN, text_role="study" if word else "ui")]
        instruction = item.get("instruction", {})
        if word:
            if item.get("reading"):
                children.append(u.row(u.label(self.learning_reading_label(item), size=18, flex=1, text_role="study"),
                    *([] if item.get("personal_reading", {}).get("pending") else
                      [u.speaker_button(lambda w: self.speak_text(item["reading"]))])))
            meaning = item.get("meaning") or instruction.get("summary")
            if meaning:
                children.append(u.label(meaning, size=18))
            children.append(self.example_cards(item, limit=1, known_ids=self._intro_batch_known_ids()))
        else:
            from .structured_lessons import lesson_view
            function = instruction.get("summary") or lesson_view(item).get("focus")
            if function:
                children.append(u.label(function, size=17))
            if instruction.get("rule"):
                children.append(block_widget(u, {"kind": "pattern", "label": "结构", "text": instruction["rule"]}))
            children.append(self.example_cards(item, limit=2, known_ids=self._intro_batch_known_ids()))
            if instruction.get("contrast"):
                children.append(block_widget(u, {"kind": "points", "label": "常见误用",
                                                 "items": [instruction["contrast"]]}, 1))
            explanation = u.col()
            def toggle_explanation(widget):
                if explanation.children:
                    explanation.clear()
                    widget.text = "展开讲解"
                else:
                    explanation.add(teaching_block(u, item, speak=self.speak_text))
                    widget.text = "收起讲解"
            children.extend([u.button("展开讲解", toggle_explanation, compact=True), explanation])
        return children

    def choose_intro_card(self, choice, *, expected):
        if self.page != "learning_card" or getattr(self, "_intro_card_entry", None) != expected:
            return
        try:
            self.learning_store.choose_intro_card(expected["content_id"], choice,
                expected_introduction_id=expected["introduction_id"],
                fair_new=self._intro_batch_fair() if choice == "known" else False)
        except ValueError:
            # A stale or repeated press: the saved cursor decides what is shown.
            pass
        self.show_intro_batch()

    def _intro_batch_questions(self, state, known=None):
        enabled = self.learning_preferences()["enabled"]
        known = self._intro_batch_known_ids() if known is None else known
        questions = {}
        for cid in intro_batch.quiz_items(state):
            item = self.learning_store.instruction_item(cid)
            frozen = intro_quiz_questions(item, enabled, known_background_ids=known) if item else []
            if not frozen:
                raise ValueError("这一批里有内容暂时没有可用的小测题型，请检查题型设置。")
            questions[cid] = frozen
        return questions

    def show_intro_batch_quiz(self):
        """Rebuild the quiz from saved state: the head's frozen choice question.

        The quiz is choice-only, so the item draft slot stays unused.
        """
        state = self.learning_store.intro_batch()
        if state is None:
            self._intro_batch_continue()
            return
        known = None  # One read per page: quiz selection and the kana of unlearned words share it.
        if state["phase"] == "cards":
            known = self._intro_batch_known_ids()
            try:
                state = self.learning_store.start_intro_quiz(self._intro_batch_questions(state, known))
            except ValueError as exc:
                self.alert(str(exc))
                self.screen("learning_wait", "批小测暂不可用", u.label("说明卡进度已保存。"),
                    u.button("题型设置", self.show_learning_settings, primary=True),
                    u.button("回到今日", self.leave_learning))
                return
        head = intro_batch.quiz_head(state)
        if head is None:
            # Every item settled (answered correctly, or under breadth first
            # missed on each of its questions); resuming here never skips the
            # learner's own "继续" into formal work.
            self._learning_item = self.learning_store.instruction_item(intro_batch.quiz_items(state)[-1])
            self._intro_quiz_question = None
            self._intro_quiz_result = u.col()
            self._intro_quiz_buttons = []
            self.screen("learning_batch_quiz", "批小测", u.label("本批小测已完成。", size=18),
                u.button("继续", self.finish_intro_batch_quiz, primary=True))
            return
        item = self.learning_store.instruction_item(head["content_id"])
        frozen = item["checkpoint"][intro_batch.ITEM_KEY]["questions"]
        question = next(q for q in frozen if q["id"] == head["question_id"])
        self._learning_item = item
        if question["kind"] not in self.learning_preferences()["enabled"]:
            # Never skip or answer a closed kind: the same frozen head resumes.
            self._intro_quiz_question = None
            self._show_learning_paused(QUIZ_PAUSED)
            return
        self._intro_quiz_question = question
        self._intro_quiz_result = u.col()
        known = self._intro_batch_known_ids() if known is None else known
        # Display only: the answer callbacks keep the raw choice.
        labels = option_labels(question, known)
        self._intro_quiz_buttons = [u.button(labels.get(choice, choice), lambda w, answer=choice: self.answer_intro_batch_quiz(answer, expected=question),
                                             multiline=True, text_role=learning_text_role(question, "choice"))
                                    for choice in question.get("choices", [])]
        self.screen("learning_batch_quiz", "批小测", u.label("批小测", size=13, color=u.MUTED),
            *self.intro_batch_review_hint(), self._learning_prompt_block(question, size=20, known_ids=known),
            *instruction_widgets(u, question, known), *self._intro_quiz_buttons, self._intro_quiz_result)

    def answer_intro_batch_quiz(self, answer, *, expected):
        if self.page != "learning_batch_quiz" or getattr(self, "_intro_quiz_question", None) is not expected:
            return
        if expected["kind"] not in self.learning_preferences()["enabled"]:
            self.show_intro_batch_quiz()
            return
        from .answer_feedback import build_answer_feedback
        from .answer_result_ui import answer_text, shown_answer, shown_explanation
        result = grade_learning_answer(expected, answer)
        if result.get("status") in {"empty", "invalid"}:
            return
        correct = result.get("status") == "correct"
        try:
            self.learning_store.answer_intro_quiz(expected["target_id"], expected["id"],
                                                  answered_text=answer, correct=correct)
        except ValueError:
            self.show_intro_batch()
            return
        for button in self._intro_quiz_buttons:
            button.enabled = False
        self._intro_quiz_question = None
        self._intro_quiz_result.clear()
        self._intro_quiz_result.add(build_answer_feedback({"correct": correct}))
        if not correct:
            self._intro_quiz_result.add(u.label(shown_answer(expected), bold=True,
                                                text_role=learning_text_role(expected, "answer")))
        if expected.get("feedback"):
            self._intro_quiz_result.add(u.label(shown_explanation(expected, expected["feedback"])))
        if not correct:
            # The card again; the item then waits at the end of the quiz unless
            # the store released it (design 12 Q2, breadth first).
            self._intro_quiz_result.add(u.label("说明卡", size=13, color=u.MUTED))
            self._intro_quiz_result.add(u.col(*self._intro_card_body(self._learning_item)))
        done = intro_batch.quiz_head(self.learning_store.intro_batch() or {"phase": "cards", "quiz_queue": []}) is None
        self._intro_quiz_result.add(u.button("继续", self.finish_intro_batch_quiz if done else self.show_intro_batch,
                                             primary=True))

    def finish_intro_batch_quiz(self, widget=None, **kwargs):
        if self.page != "learning_batch_quiz":
            return
        state = self.learning_store.intro_batch()
        if state is not None and state["phase"] == "quiz" and intro_batch.quiz_head(state) is None:
            self.learning_store.complete_intro_batch(fair_new=self._intro_batch_fair())
        self._intro_batch_continue()

    def open_batch_ability(self, item):
        """A later ability of a batch item: no intro page, no picker (四之二 item 4).

        The store writes the card self-report and opens the scope; the next
        dispatch shows its first formal question with a one-line hint.
        """
        try:
            self.learning_store.open_batch_ability(item["id"], instruction_scope=item["instruction_scope"],
                expected_introduction_id=item["introduction_id"],
                enabled_kinds=self.learning_preferences()["enabled"], fair_new=self._intro_batch_fair())
        except ValueError:
            self.screen("learning_wait", "新能力暂不可用", u.label("原有进度已保存。"),
                *self.learning_next_content_controls(), u.button("回到今日", self.leave_learning))
            return
        self._intro_batch_continue()

    def intro_batch_question_help(self, current):
        """(hint labels, action buttons) for a formal question of a batch item.

        Early questions (fewer than EARLY_REVIEWS reviews) may open the card;
        opening it marks the draft assisted through learning_hint. The first
        question of a later ability also gets one short hint line.
        """
        question = current["question"]
        content = current.get("content") or {}
        marked = getattr(self.learning_store, "is_batch_route", None)
        if not callable(marked) or not marked(question["target_id"]):
            return [], []
        unit = self.learning_store.get_unit(question["target_id"], question["ability"]) or {}
        hints, actions = [], []
        profile = (content.get("checkpoint") or {}).get("initial_familiarity") or {}
        if (content.get("instruction_scope") and profile.get("source") == intro_batch.CARD_SOURCE
                and not unit.get("attempt_count")):
            hints.append(u.label(ability_hint(question), size=13, color=u.MUTED))
        if unit.get("review_count", 0) < EARLY_REVIEWS:
            actions.append(u.button(CARD_BUTTON, lambda w: self.learning_hint(w, card=True),
                                    compact=True, font_size=12, flex=1))
        return hints, actions
