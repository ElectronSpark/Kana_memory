"""Answering a course question (答题) as a service (design 13 C1): the reads and the writes of a question
being answered, up to its submit.

The session (course_line.answer_model.AnswerSession) is handed in and, where a field of it is assigned,
handed back as a new object: this class keeps nothing between calls, so each method can be asked with a
hand-made session. The learner store owns every transaction; the methods make their calls in the order the
question page always made them.

No Toga, no control components, no app object.
"""
from __future__ import annotations

from dataclasses import replace

from ..pitch_practice import editor_draft as pitch_editor_draft, is_pitch_question
from ..question_support import clean_prompt_preferences
from .answer_model import (AnswerSession, EditFailed, EditKept, EditLeft, Edited, OpenAnswered, OpenAsk,
                           OpenCancelled)

# The setting that holds how a prompt is displayed (kana above kanji, the accent number).
PROMPT_PREFS = "prompt_display_v1"


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
