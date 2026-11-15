"""The batch introduction (成批引入: the cards 新项说明 and the quiz 批小测) as a service (design 13 C1, C3).

Every decision and every write of a batch is a method here: whether a head
opens or resumes a batch, what the batch does next, the exposure of a card,
the card's choice, the quiz's frozen questions, an answer, the batch's end, a
later ability. intro_batch owns the state rules, intro_batch_quiz the choice
of the quiz questions, the learner store every transaction; this class asks
them in the order the pages always did and answers with the values of
course_line.batch_model.

No Toga, no control components, no app object: it is built from the two
stores and three callables and keeps nothing between calls.
"""
from __future__ import annotations

from .. import intro_batch
from ..intro_batch_quiz import EARLY_REVIEWS, intro_quiz_question, intro_quiz_questions
from ..learning_questions import grade_learning_answer
from ..question_support import learning_text_role, option_labels
from .batch_display import NO_QUIZ_KIND, ability_hint
from .batch_model import (BatchClosed, BatchHelp, BatchOver, CardOpened, CardStep, CardUnavailable, QuizAnswered,
                          QuizAsked, QuizPaused, QuizSettled, QuizStale, QuizStep, QuizUnavailable)


class BatchIntroduction:
    """One batch introduction over the learner's stores.

    ``learning_store``: the learner store (public methods only); ``store``: the
    study store (the known ids and the main lane's queue); ``enabled_kinds()``:
    the kinds this device can present now — asked each time it is needed, never
    kept; ``fair_new()``: whether new items are shared out fairly (design 12);
    ``route_available()``: whether the continue action serves this learner.
    """

    def __init__(self, learning_store, store, *, enabled_kinds, fair_new, route_available):
        self.learning_store = learning_store
        self.store = store
        self._enabled_kinds = enabled_kinds
        self._fair_new = fair_new
        self._route_available = route_available

    def batch_open(self):
        """Whether a batch is open."""
        return self.learning_store.intro_batch() is not None

    def known_ids(self, extra=()):
        """Items a batch sentence may use besides the new one: ready items,
        introduced kana (design 11 section 5, D5) and this batch."""
        from ..course_planning import known_ids
        state = self.learning_store.intro_batch()
        return {*known_ids(self.store.queries),
                *(entry["content_id"] for entry in (state or {}).get("entries", ())), *extra}

    def route(self, current, enabled):
        """Resume an open batch or open one at an eligible instruction head.

        current() keeps owning dispatch order: due reviews, saved answers,
        explicit starts, waits and blocks decide as before. An open batch is
        resumed (from its saved cursor) when the dispatched instruction head
        is one of its items; another head keeps its own route, so a paused
        batch (closed kind, suspension, prerequisite) never blocks other work.
        True: the batch owns this head (a new batch is written); the caller shows it.
        """
        kind = (current or {}).get("type")
        state = self.learning_store.intro_batch()
        if state is not None:
            content = (current or {}).get("content") or {}
            if (kind == "instruction" and content.get("instruction_scope") is None
                    and any(entry["content_id"] == content.get("id") for entry in state["entries"])):
                return True
            return False
        if kind != "instruction":
            return False
        content = current.get("content") or {}
        if (content.get("instruction_scope") is not None or content.get("family") not in intro_batch.SIZE_RANGES
                or content.get("stage") != "intro" or (content.get("checkpoint") or {}).get("initial_familiarity")):
            return False
        candidates = self.learning_store.intro_batch_candidates(enabled, content["id"], fair_new=self._fair_new())
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
        known = self.known_ids(candidates)
        candidates = [cid for cid in candidates if quizzable(cid)]
        if not candidates or candidates[0] != content["id"]:
            return False
        try:
            self.learning_store.start_intro_batch(candidates, family=content["family"])
        except ValueError:
            return False
        return True

    def advance(self):
        """What the batch does next. An all-"我已经会了" batch is closed here: the empty quiz is started and
        the batch completed, in this order."""
        state = self.learning_store.intro_batch()
        if state is None:
            return BatchOver()
        entries = state["entries"]
        if state["phase"] == "cards" and state["card_index"] < len(entries):
            return CardStep(entries[state["card_index"]])
        if state["phase"] == "cards" and not intro_batch.quiz_items(state):
            # Every item chose "我已经会了": an empty quiz closes the batch.
            self.learning_store.start_intro_quiz({})
            self.learning_store.complete_intro_batch(fair_new=self._fair_new())
            return BatchClosed()
        return QuizStep()

    def open_card(self, entry):
        """The card of ``entry`` is about to be shown: its exposure is recorded, unless the card is unavailable
        (then nothing is written)."""
        from ..assessment_policy import teaching_allowed
        state = self.learning_store.intro_batch()
        item = self.learning_store.instruction_item(entry["content_id"])
        if item is None or item.get("introduction_id") != entry["introduction_id"] or not teaching_allowed(item, store=self.store):
            return CardUnavailable()
        item = self.learning_store.mark_instruction_exposed(item["id"], expected_introduction_id=item["introduction_id"])
        index = next(i for i, e in enumerate(state["entries"]) if e["content_id"] == entry["content_id"])
        return CardOpened(item, dict(entry), index, len(state["entries"]))

    def choose_card(self, entry, choice):
        """The learner's choice on the card of ``entry`` ("next" or "known"). A stale or repeated press is
        refused by the store and changes nothing: the saved cursor decides what is shown."""
        try:
            self.learning_store.choose_intro_card(entry["content_id"], choice,
                expected_introduction_id=entry["introduction_id"],
                fair_new=self._fair_new() if choice == "known" else False)
        except ValueError:
            pass

    def quiz_questions(self, state, known=None):
        """The frozen questions of every quiz item of ``state``: {content id: [question, ...]}."""
        enabled = self._enabled_kinds()
        known = self.known_ids() if known is None else known
        questions = {}
        for cid in intro_batch.quiz_items(state):
            item = self.learning_store.instruction_item(cid)
            frozen = intro_quiz_questions(item, enabled, known_background_ids=known) if item else []
            if not frozen:
                raise ValueError(NO_QUIZ_KIND)
            questions[cid] = frozen
        return questions

    def open_quiz(self):
        """The quiz is about to be shown, rebuilt from the saved state: at its first showing the questions are
        frozen and written. None: no batch is open.

        The quiz is choice-only, so the item draft slot stays unused.
        """
        state = self.learning_store.intro_batch()
        if state is None:
            return None
        known = None  # One read per page: quiz selection and the kana of unlearned words share it.
        if state["phase"] == "cards":
            known = self.known_ids()
            try:
                state = self.learning_store.start_intro_quiz(self.quiz_questions(state, known))
            except ValueError as exc:
                return QuizUnavailable(str(exc))
        head = intro_batch.quiz_head(state)
        if head is None:
            # Every item settled (answered correctly, or under breadth first
            # missed on each of its questions); resuming here never skips the
            # learner's own "继续" into formal work.
            return QuizSettled(self.learning_store.instruction_item(intro_batch.quiz_items(state)[-1]))
        item = self.learning_store.instruction_item(head["content_id"])
        frozen = item["checkpoint"][intro_batch.ITEM_KEY]["questions"]
        question = next(q for q in frozen if q["id"] == head["question_id"])
        if question["kind"] not in self._enabled_kinds():
            # Never skip or answer a closed kind: the same frozen head resumes.
            return QuizPaused(item)
        known = self.known_ids() if known is None else known
        # Display only: the answer callbacks keep the raw choice.
        labels = option_labels(question, known)
        return QuizAsked(item, question, known, labels, learning_text_role(question, "choice"))

    def answer(self, question, answer):
        """Judge and record one answer to the quiz question ``question``.

        QuizPaused: its kind is closed by now (nothing is judged or written);
        None: the answer is empty or invalid (nothing happens); QuizStale: the
        store refused it; QuizAnswered: it is recorded.
        """
        if question["kind"] not in self._enabled_kinds():
            return QuizPaused(None)
        result = grade_learning_answer(question, answer)
        if result.get("status") in {"empty", "invalid"}:
            return None
        correct = result.get("status") == "correct"
        try:
            self.learning_store.answer_intro_quiz(question["target_id"], question["id"],
                                                  answered_text=answer, correct=correct)
        except ValueError:
            return QuizStale()
        return QuizAnswered(correct)

    def quiz_done(self):
        """Whether the quiz has no question left to ask (read from the saved state)."""
        return intro_batch.quiz_head(self.learning_store.intro_batch() or {"phase": "cards", "quiz_queue": []}) is None

    def finish(self):
        """Complete the batch when its quiz is settled; anything else is left as it is."""
        state = self.learning_store.intro_batch()
        if state is not None and state["phase"] == "quiz" and intro_batch.quiz_head(state) is None:
            self.learning_store.complete_intro_batch(fair_new=self._fair_new())

    def open_ability(self, item):
        """A later ability of a batch item: no intro page, no picker (四之二 item 4).

        The store writes the card self-report and opens the scope; the next
        dispatch shows its first formal question with a one-line hint. False:
        the store refused and nothing was written.
        """
        try:
            self.learning_store.open_batch_ability(item["id"], instruction_scope=item["instruction_scope"],
                expected_introduction_id=item["introduction_id"],
                enabled_kinds=self._enabled_kinds(), fair_new=self._fair_new())
        except ValueError:
            return False
        return True

    def question_help(self, current):
        """The help a formal question of a batch item offers.

        Early questions (fewer than EARLY_REVIEWS reviews) may open the card.
        The first question of a later ability also gets one short hint line.
        """
        question = current["question"]
        content = current.get("content") or {}
        marked = getattr(self.learning_store, "is_batch_route", None)
        if not callable(marked) or not marked(question["target_id"]):
            return BatchHelp(None, False)
        unit = self.learning_store.get_unit(question["target_id"], question["ability"]) or {}
        profile = (content.get("checkpoint") or {}).get("initial_familiarity") or {}
        hint = None
        if (content.get("instruction_scope") and profile.get("source") == intro_batch.CARD_SOURCE
                and not unit.get("attempt_count")):
            hint = ability_hint(question)
        return BatchHelp(hint, unit.get("review_count", 0) < EARLY_REVIEWS)

    def review_due(self):
        """Due review count shown as a hint only; it never blocks a batch."""
        enabled = self._enabled_kinds()
        count = self.learning_store.counts(enabled)["ready"]
        if not count and self._route_available() and hasattr(self.store, "load_session_progress"):
            from ..practice_availability import summarize_queue_availability
            pending = self.store.load_session_progress(lane="main", lazy_questions=True)
            count = summarize_queue_availability(self.store, pending).ready_count
        return count
