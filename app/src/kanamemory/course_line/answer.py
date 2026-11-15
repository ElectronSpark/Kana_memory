"""Answering a course question (答题) as a service (design 13 C1): every read, write and judgment of a question
from the moment it is opened to its rating — the drafts, the aids, the submit, what its result page shows and
the rating.

The session (course_line.answer_model.AnswerSession) is handed in and, where a field of it is assigned,
handed back as a new object: this class keeps nothing between calls, so each method can be asked with a
hand-made session. The learner store owns every transaction; the methods make their calls in the order the
question page always made them.

No Toga, no control components, no app object.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from ..answer_result_ui import answer_text, shown_answer, shown_explanation
from ..learning_questions import grade_learning_answer
from ..pitch_practice import answer_label as pitch_answer_label, editor_draft as pitch_editor_draft, is_pitch_question
from ..question_support import clean_prompt_preferences, learning_text_role, prompt_support
from .answer_display import (ANSWER_COMPARISON, ANSWER_LABEL, ANSWER_PITCH, ANSWER_SEQUENCE, ASK_AI, DETAIL,
                             FEEDBACK_PAGE, FEEDBACK_TITLE, NEED_ANSWER, NEED_AUDIO, NEED_COMPLETE, NEEDS_TEACHING,
                             RATINGS, SAVE_AND_CONTINUE, SPELLING_UNRESOLVED, STRUCTURE_OTHER, STRUCTURE_WORD,
                             AnswerButton, FeedbackModel, without_self_gloss)
from .answer_model import (AnswerAction, AnswerActionKind, AnswerSession, Busy, EditFailed, EditKept, EditLeft,
                           Edited, Graded, OpenAnswered, OpenAsk, OpenCancelled, Rated, Recorded, Refused)

# The setting that holds how a prompt is displayed (kana above kanji, the accent number).
PROMPT_PREFS = "prompt_display_v1"
# What the store records for each rating button.
RATING_NUMBERS = {"again": 1, "hard": 2, "good": 3, "easy": 4}


class CourseAnswering:
    """The answering of course questions over the learner's stores.

    ``learning_store``: the learner store (public methods only); ``store``: the
    study store (settings and the known ids); ``fair_new()``: whether new items
    are shared out fairly (design 12); ``early_submit(question_id)``: the extra
    arguments of a submit that extra practice handed out early (design 12 K13);
    ``record_error(area, action, error)``: notes an error for diagnostics.
    """

    def __init__(self, learning_store, store, *, fair_new, early_submit, record_error):
        self.learning_store = learning_store
        self.store = store
        self._fair_new = fair_new
        self._early_submit = early_submit
        self._record_error = record_error

    # --- opening a question -------------------------------------------------------------------------------

    def begin(self, session: AnswerSession, current) -> AnswerSession:
        """The session of the question of ``current`` (the course head): the question and a copy of its saved
        draft; everything else stays what it was. Nothing is read or written."""
        return replace(session, question=current["question"], state=dict(current.get("draft") or {}))

    def settle_open(self, session: AnswerSession, current):
        """What opening the question of ``current`` comes to.

        A skip interrupted between submit and finalize is finished here
        (cancellation must never open answer feedback on restart); a saved
        answer leads to its result page; else the session gets what the
        question page starts from.
        """
        result = current.get("result")
        if (result or {}).get("outcome") == "cancelled":
            self.learning_store.finalize(session.question["id"], rating=None)
            return OpenCancelled()
        if result:
            return OpenAnswered(result)
        state = session.state
        return OpenAsk(replace(session, audio_ok=bool(state.get("audio_ok")), answer=state.get("answer", ""),
                               parts=list(state.get("parts", []))))

    # --- what the question page reads -------------------------------------------------------------------------

    def known_ids(self):
        """The ids the learner knows: one read per question page (unmet-word glosses and the kana of unlearned
        words share it)."""
        from ..course_planning import known_ids
        return known_ids(self.store.queries)

    def prompt_preferences(self):
        """How a prompt is displayed, as saved now."""
        return clean_prompt_preferences(self.store.get_setting(PROMPT_PREFS, {}))

    def system_japanese(self):
        """Whether the learner types with the system's Japanese keyboard."""
        from ..answer_input import system_japanese_enabled
        return system_japanese_enabled(self.store)

    def replaced(self, question):
        """The question this one replaced, as stored (None: it replaced none, or that row is gone)."""
        if not question.get("replacement_of"):
            return None
        return self.learning_store.get_question(question["replacement_of"])

    def hint_item(self, session: AnswerSession):
        """The lesson of the question on the page, for its teaching reference."""
        return self.learning_store.get_item(session.question["target_id"])

    # --- what a question being answered writes ----------------------------------------------------------------

    def presented(self, session: AnswerSession) -> None:
        """The question's page is drawn: note once that the learner has seen it. Call it after the drawing."""
        if session.state.get("presented") is not True:
            self.learning_store.save_draft(session.question["id"], {"presented": True})
            session.state["presented"] = True

    def save_draft(self, question_id, state):
        """Save the draft of a question; the store's answer."""
        return self.learning_store.save_draft(question_id, state)

    def edit_parts(self, session: AnswerSession, index):
        """Add the offered block at ``index`` to the chosen ones, or take the last one back (None), and save
        the draft at once."""
        from ..word_blocks import proposed_blocks
        question = session.question
        proposed = proposed_blocks(question["choices"], session.state, index)
        if proposed is None:
            return EditKept()
        try:
            saved = self.learning_store.save_draft(question["id"], proposed)
        except Exception as exc:
            self._record_error("learning_word_blocks", "save", exc)
            return EditFailed(exc)
        if saved["status"] != "pending" or saved.get("result"):
            return EditLeft()
        return Edited(saved["draft"])

    def mark_assisted(self, session: AnswerSession) -> AnswerSession:
        """The learner used an aid (the teaching reference, the card, the answer): the draft says so from now
        on. For a pitch question the draft becomes the editor's draft first (a new session); otherwise the
        same draft is marked. Nothing is written: save the draft afterwards."""
        if is_pitch_question(session.question):
            session = replace(session, state=pitch_editor_draft(session.question, session.state))
        session.state["assisted"] = True
        return session

    def audio_played(self, session: AnswerSession, question) -> AnswerSession:
        """The audio of ``question`` has played: save the mark, then the session carries it. A failing save
        raises and leaves the session as it was."""
        self.learning_store.save_draft(question["id"], {**session.state, "audio_ok": True})
        session.state.update(audio_ok=True)
        return replace(session, audio_ok=True)

    def skip(self, session: AnswerSession) -> bool:
        """Release a question this device cannot take an answer for (an old question that needs Japanese
        input), without revealing its answer, rewriting its snapshot or draft, or rating it: the cancelled
        answer, then its end, as two writes. False: the question can be answered here; nothing is written."""
        from ..answer_input import available, system_japanese_enabled
        question = session.question
        if available(question, system_japanese=system_japanese_enabled(self.store)):
            return False
        self.learning_store.submit(question["id"], session.state.get("answer", ""), correct=None, outcome="cancelled")
        self.learning_store.finalize(question["id"], rating=None)
        return True

    def item(self, content_id):
        """The lesson ``content_id`` as stored now (None: gone)."""
        return self.learning_store.get_item(content_id)

    # --- the submit ---------------------------------------------------------------------------------------------

    def grade(self, session: AnswerSession, answer, *, revealed: bool):
        """Judge ``answer`` (as the answer field prepared it) without writing: Refused with the text to show,
        or Graded. ``revealed``: the learner asked for the answer."""
        question = session.question
        if question.get("audio_text") and not session.audio_ok and not revealed:
            return Refused(NEED_AUDIO)
        if not str(answer).strip() and not revealed:
            return Refused(NEED_ANSWER)
        result = grade_learning_answer(question, answer)
        if is_pitch_question(question) and result.get("status") in {"empty", "invalid"} and not revealed:
            return Refused(result.get("message", NEED_COMPLETE))
        outcome = "revealed" if revealed else result["status"]
        if question.get("audio_text") and not session.audio_ok:
            outcome = "device_failure"
        return Graded(result, outcome)

    def record(self, session: AnswerSession, answer, graded: Graded):
        """Record the judged answer as the question's first answer, with what the learner was shown while
        answering: the plan of the prompt the page drew (the plain plan when the remembered prompt belongs to
        another question) and whether the audio played. Busy: the database was locked; nothing was written."""
        question = session.question
        shown, known = session.prompt_known
        plan = prompt_support(question, self.prompt_preferences(), known if shown is question else None)
        try:
            # Design 12 K13: only the course question extra practice handed out is submitted with the early permit.
            saved = self.learning_store.submit(
                question["id"], str(answer),
                None if graded.outcome in {"unresolved", "device_failure"} else graded.result["status"] == "correct",
                assisted=bool(session.state.get("assisted")), outcome=graded.outcome,
                presentation={"prompt_furigana_visible": plan["furigana_visible"],
                              "prompt_accent_visible": plan["accent_visible"], "audio_used": session.audio_ok},
                **self._early_submit(question["id"]))
        except Exception as exc:
            from ..draft_debounce import is_database_locked
            if not is_database_locked(exc):
                raise
            # Rolled back: nothing was graded. The same press can be retried.
            self._record_error("learning_submit", "save", exc)
            return Busy()
        return Recorded(saved)

    # --- the result -----------------------------------------------------------------------------------------------

    def feedback(self, session: AnswerSession, result):
        """What the result page of ``result`` shows: (the session, now holding the result as shown; the page's
        model). The lesson is read and the result is settled once more; nothing is written."""
        from ..answer_result_ui import asked_text, distinct_explanation
        from ..assessment_policy import settlement_decision
        from ..word_blocks import structure_diagnostic
        question = session.question
        originally_unresolved = result.get("outcome") == "unresolved"
        decision = settlement_decision(self.learning_store.get_item(question["target_id"]), question,
                                       result.get("assessment"), store=self.store)
        if decision["settlement_channel"] != "formal":
            # An old saved feedback may predate eligibility metadata. This is
            # a view only; its original first answer remains frozen in storage.
            result = {**result, "correct": None, "outcome": "unresolved", "assessment": decision}
        session = replace(session, feedback_view=(question["id"], deepcopy(result)))
        correct = result.get("correct", False)
        assisted = result.get("assisted", False)
        entered = result.get("answered_text", result.get("answer", ""))
        structure_error = (structure_diagnostic(question, entered)
            if originally_unresolved and decision["assessment_eligible"]
            and not assisted and not result.get("revealed")
            and not result.get("diagnostic_needs_teaching") else None)
        pitch = is_pitch_question(question)
        # An accepted sentence contour/segmentation is the learner's correct
        # answer. Do not replace it with a different canonical alternative.
        accepted_sequence = bool(correct and entered and question.get("pitch_practice", {}).get("version") in (2, 3))
        compare_reading = (question.get("kind") == "vocab_reading" and correct is False
                           and bool(entered) and not assisted and entered != question["answer"])
        answer_kind = (ANSWER_COMPARISON if compare_reading else ANSWER_SEQUENCE if accepted_sequence
                       else ANSWER_PITCH if pitch else ANSWER_LABEL)
        explanation = without_self_gloss(distinct_explanation(question.get("feedback"), answer_text(question)))
        asked = None if pitch else asked_text(question, explanation)
        # After the answer every Japanese word shown here carries its kana (design 11 section 7, rule 9);
        # the comparisons keep using the plain strings.
        shown_entered = entered
        entered_shown = None
        if entered and not correct and entered != question["answer"] and not compare_reading:
            shown_entered = pitch_answer_label(question, entered) if pitch else shown_answer(question, entered)
            entered_shown = shown_entered
        diagnosis = None
        if pitch and not correct and shown_entered:
            message = grade_learning_answer(question, entered).get("message", "")
            if message and message != explanation:
                diagnosis = message
        unresolved = None
        if result.get("outcome") in {"unresolved", "device_failure", "invalid"}:
            from ..assessment_policy import unresolved_message
            unresolved = ((STRUCTURE_WORD if question.get("family") == "word" else STRUCTURE_OTHER)
                          if structure_error else
                          SPELLING_UNRESOLVED if result.get("error_category") == "spelling"
                          else unresolved_message(result))
        rated = bool(correct and not assisted and result.get("outcome") == "correct")

        def rating(caption, name):
            return AnswerButton(caption, AnswerAction(AnswerActionKind.RATE, rating=name, question_id=question["id"]))
        return session, FeedbackModel(
            page=FEEDBACK_PAGE, title=FEEDBACK_TITLE, question=question, result=result,
            structure_error=structure_error, answer_kind=answer_kind,
            answer_text=shown_answer(question) if answer_kind == ANSWER_LABEL else None,
            answer_role=learning_text_role(question, "answer"), entered=entered,
            asked=asked, asked_role=learning_text_role(question, "prompt") if asked else None,
            needs_teaching=NEEDS_TEACHING if result.get("diagnostic_needs_teaching") else None,
            entered_shown=entered_shown, diagnosis=diagnosis,
            explanation=(shown_explanation(question, explanation)
                         if explanation and explanation != str(question["answer"]).strip() else None),
            tutor=AnswerButton(ASK_AI, AnswerAction(AnswerActionKind.TUTOR, question_id=question["id"])),
            detail=AnswerButton(DETAIL, AnswerAction(AnswerActionKind.DETAIL)),
            unresolved=unresolved,
            ratings=tuple(rating(caption, name) for caption, name in RATINGS) if rated else None,
            save=None if rated else rating(SAVE_AND_CONTINUE, "again"))

    # --- the rating -----------------------------------------------------------------------------------------------

    def rate(self, session: AnswerSession, rating):
        """Finish the question with the learner's rating ("again", "hard", "good" or "easy"), then read its
        lesson for the queueing that follows. Busy: the database was locked; the rating can be repeated."""
        question = session.question
        try:
            self.learning_store.finalize(question["id"], RATING_NUMBERS[rating], fair_new=self._fair_new())
        except Exception as exc:
            from ..draft_debounce import is_database_locked
            if not is_database_locked(exc):
                raise
            # The busy timeout aborted the write transaction; the rating can be retried.
            self._record_error("learning_submit", "commit", exc)
            return Busy()
        return Rated(self.learning_store.get_item(question["target_id"]))
