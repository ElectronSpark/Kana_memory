"""The teaching steps of the course line (讲解与引导) as a service (design 13 C1, C3): what a teaching head
becomes, the familiarity question, and the guide — its checkpoints, its judgment, "seen, go on" and its end.

step() decides which of ten things a teaching head becomes and makes the writes that decision has always
made on its way — the shared introduction passed over, the guide sequence and the guide question frozen
before they are shown, the exposure of an introduction recorded when it is shown — in the order the page
always made them, and answers with a value of course_line.teaching_model. The learner store owns every
transaction; this class keeps nothing between calls.

No Toga, no control components, no app object.
"""
from __future__ import annotations

from copy import deepcopy

from .. import intro_batch
from ..learning_questions import KIND_ABILITIES, LISTENING_KINDS, grade_learning_answer, question_kinds
from ..pitch_practice import is_pitch_question
from .teaching_display import GUIDE_EMPTY, PASS_GUIDE, PASS_STEP
from .teaching_model import (AskFamiliarity, BatchOwns, FamiliarityOpened, Guide, GuideAnswered, GuideBlocked,
                             GuideEmpty, GuideFinished, GuideNext, GuidePaused, GuideSaveFailed, GuideUnavailable,
                             Introduce, MaterialNotReady, OpenAbility)


class CourseTeaching:
    """The teaching steps over the learner's stores.

    ``learning_store``: the learner store (public methods only); ``store``: the
    study store (the known ids, the material checks); ``enabled_kinds()``: the
    kinds this device can present now — asked each time it is needed, never
    kept; ``fair_new()``: whether new items are shared out fairly (design 12);
    ``record_error(area, action, error)``: notes an error for diagnostics.
    """

    def __init__(self, learning_store, store, *, enabled_kinds, fair_new, record_error=None):
        self.learning_store = learning_store
        self.store = store
        self._enabled_kinds = enabled_kinds
        self._fair_new = fair_new
        self.record_error = record_error or (lambda *args: None)

    def step(self, item, keep):
        """What the teaching head ``item`` becomes.

        ``keep(item=...)`` / ``keep(guide=...)`` is called at each moment the line starts to hold another
        lesson object or guide question — after the two checks that stop before anything is held, after each
        write that returns the lesson anew, and before the guide question's kind is tested — so that the
        caller's session holds, at every moment and on every path, what it always held there.
        """
        from ..assessment_policy import teaching_allowed
        from ..personal_content_versions import question_blocked
        frozen_guide = item.get('checkpoint', {}).get('guide_question') if item.get('stage') == 'guide' else None
        if frozen_guide and question_blocked(self.store, item, frozen_guide):
            return GuideBlocked(item)
        if not teaching_allowed(item, store=self.store):
            return MaterialNotReady(item, item.get("instruction_status") == "instruction_missing")
        keep(item=item)
        stage = item.get("stage", "intro")
        familiarity = item.get("checkpoint", {}).get("initial_familiarity")
        enrolled = bool(item.get("introduction_id") and item.get("units"))
        if (enrolled and item.get("instruction_scope") and stage == "intro" and not familiarity
                and self.learning_store.is_batch_route(item["id"])):
            # A later ability of a batch item: no full intro, no picker.
            return OpenAbility(item)
        batch_checkpoint = item.get("checkpoint", {})
        if (enrolled and not item.get("instruction_scope")
                and (stage == "intro" and not familiarity
                     or stage == "guide" and intro_batch.ITEM_KEY in batch_checkpoint)
                and batch_checkpoint.get(intro_batch.ROUTE_KEY) == intro_batch.ROUTE
                and any(entry["content_id"] == item["id"]
                        for entry in (self.learning_store.intro_batch() or {}).get("entries", ()))):
            # A batch item is never asked familiarity and its quiz is never
            # the single-item guide: the open batch owns both.
            return BatchOwns()
        # Batch items always have initial_familiarity once their card is done,
        # so they never reach the picker below. A failed "我已经会了" check
        # (route full, diagnostic_state needs_teaching, stage intro again)
        # deliberately falls back to this ordinary intro -> guide route.
        if enrolled and stage == "intro" and not familiarity:
            return AskFamiliarity(item)
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
            keep(item=item)
            stage = item['stage']
        if stage == "guide":
            enabled = set(self._enabled_kinds())
            admitted = item.get("enrolled_abilities", item.get("abilities", []))
            guide_kinds = [kind for kind in question_kinds(item)
                           if kind in enabled and KIND_ABILITIES[kind] in admitted]
            levels = (familiarity or {}).get("levels", {})
            unfamiliar = [kind for kind in guide_kinds
                          if levels.get(KIND_ABILITIES[kind]) in {"new", "some"}]
            if unfamiliar:
                guide_kinds = unfamiliar
            checkpoint = item.get("checkpoint", {})
            from ..course_planning import known_ids
            known = {item["id"], *known_ids(self.store.queries)}
            sequence = self.learning_store.guided_sequence(item, guide_kinds, known_background_ids=known)
            if sequence and "guide_sequence" not in checkpoint:
                item = self.learning_store.advance_instruction(item["id"], "guide", state={
                    "guide_sequence": sequence, "guide_index": 0, "guide_completed_ids": [],
                    "guide_question": sequence[0], "guide_draft": {}}, instruction_scope=item.get("instruction_scope"))
                keep(item=item)
                checkpoint = item["checkpoint"]
            if not guide_kinds:
                return GuidePaused()
            guide_kind = ("pronunciation_rule" if item.get("domain") == "pronunciation" else
                          "vocab_meaning" if item["family"] == "word" else "grammar_meaning")
            if any(raw.get("input_mode") == "pitch_fill" for raw in
                   item.get("question_templates", {}).get("pronunciation_application", [])):
                guide_kind = "pronunciation_application"
            candidates = []
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
            # Same preference order; a kind whose only example is retired
            # (no background-ready replacement) yields None: try the next.
            q = None
            for kind in dict.fromkeys([guide_kind, *candidates,
                                       *(k for k in guide_kinds if k not in LISTENING_KINDS)]):
                q = self.learning_store.guided_question(item, kind, variant=1)
                if q is not None:
                    break
            if q is None:
                return GuideUnavailable(item)
            keep(guide=q)
            if q["kind"] not in enabled:
                return GuidePaused()
            if q.get("practice") and "guide_question" not in checkpoint:
                item = self.learning_store.advance_instruction(item["id"], "guide", state={
                    "guide_question": deepcopy(q), "guide_draft": {}}, instruction_scope=item.get("instruction_scope"))
                keep(item=item)
                checkpoint = item["checkpoint"]
            return Guide(item, q, checkpoint, known, shared_intro)
        if enrolled:
            self.learning_store.mark_instruction_exposed(item["id"], expected_introduction_id=item["introduction_id"],
                                                         instruction_scope=item.get("instruction_scope"))
            keep(item=self.learning_store.instruction_item(item["id"], item.get("instruction_scope")))
        return Introduce(item, familiarity, enrolled)

    def understanding_unit(self, item):
        """The lesson's unit of understanding as stored (the introduction page asks whether its explanation was
        already practised)."""
        return self.learning_store.get_unit(item['id'], 'understanding')

    # --- the page 开始前 -------------------------------------------------------------------------------------------

    def open_familiarity(self, item, head, keep):
        """Prepare the familiarity question for the lesson ``item`` (``head``: the course step settled on
        last, whose ability is asked first when it belongs to this introduction). None: the lesson may not be
        taught — nothing is prepared. ``keep(item=...)`` is called once the question is prepared."""
        from ..assessment_policy import teaching_allowed
        if not teaching_allowed(item, store=self.store):
            return None
        available = {KIND_ABILITIES[k] for k in self._enabled_kinds() if k in KIND_ABILITIES}
        admitted = [unit['ability'] for unit in item['units']]
        current = head or {}
        chosen_ability = ((current.get('question') or {}).get('ability')
                          if current.get('content', {}).get('introduction_id') == item['introduction_id'] else None)
        if chosen_ability not in available or chosen_ability not in admitted:
            chosen_ability = next((a for a in admitted if a in available), admitted[0])
        item = self.learning_store.prepare_initial_familiarity(item['id'],
            expected_introduction_id=item['introduction_id'], instruction_scope=item.get('instruction_scope'), ability=chosen_ability)
        keep(item=item)
        abilities = [unit['ability'] for unit in item['units']]
        levels = dict(item.get('checkpoint', {}).get('familiarity_draft', {}))
        ability = next(a for a in abilities if a not in levels)
        state = {'content_id': item['id'], 'introduction_id': item['introduction_id'],
                 'instruction_scope': item.get('instruction_scope'), 'ability': ability, 'levels': levels, 'busy': False}
        return FamiliarityOpened(item, state, tuple(abilities), levels, ability)

    def save_familiarity_draft(self, opened: FamiliarityOpened, chosen):
        """Save the levels chosen so far while abilities of the lesson remain to be asked; the lesson as
        stored."""
        return self.learning_store.save_familiarity_draft(
            opened.item['id'], chosen, expected_introduction_id=opened.state['introduction_id'],
            instruction_scope=opened.state['instruction_scope'])

    def set_familiarity(self, opened: FamiliarityOpened, chosen):
        """Record the learner's answers for every ability of the lesson; the lesson as stored."""
        return self.learning_store.set_initial_familiarity(
            opened.item['id'], chosen, expected_introduction_id=opened.state['introduction_id'],
            title_answer_exposed=opened.item.get('domain') == 'pronunciation',
            fair_new=self._fair_new(), instruction_scope=opened.state['instruction_scope'])

    # --- the page 引导例题暂不可用 ---------------------------------------------------------------------------------

    def bridge_without_guide(self, content_id, instruction_scope):
        """Go on to independent practice without a guide — the ordinary guide -> bridge transition: no
        rating, no review event; independent practice follows after its delay."""
        self.learning_store.advance_instruction(content_id, "bridge", fair_new=self._fair_new(),
                                                instruction_scope=instruction_scope)

    # --- the guide ---------------------------------------------------------------------------------------------------

    def lesson(self, item):
        """The lesson ``item`` as stored now."""
        return self.learning_store.instruction_item(item["id"], item.get("instruction_scope"))

    def start_guide(self, item):
        """The learner opens the guide from the introduction: the lesson moves on to its guide; the lesson as
        stored afterwards."""
        self.learning_store.advance_instruction(item["id"], "intro_done", instruction_scope=item.get("instruction_scope"))
        return self.learning_store.instruction_item(item["id"], item.get("instruction_scope"))

    def checkpoint_guide(self, item, state):
        """Save ``state`` (the frozen question with its draft; an open demonstration) into the lesson's guide
        checkpoint, before the page shows the change. The store's refusal is raised."""
        return self.learning_store.advance_instruction(item["id"], "guide", state=state,
                                                       instruction_scope=item.get("instruction_scope"))

    def guide_archive(self, checkpoint):
        """What a replaced guide question looked like before it was replaced (the saved record), or None."""
        from ..learning_replacement import PREFIX
        identity = checkpoint.get('guide_replacement_id')
        return self.store.get_setting(PREFIX + 'guide:' + identity, None) if identity else None

    def answer_guide(self, item, question, answer, draft):
        """Judge and save one answer to the guide question. GuideEmpty: nothing to judge, nothing written;
        GuideSaveFailed: it could not be saved; GuideAnswered: the lesson's guide holds the answer (a right one
        stays in the guide stage, a wrong one is marked failed), with the draft of a pitch or practice question."""
        result = grade_learning_answer(question, answer)
        if result.get("status") in {"empty", "invalid"}:
            return GuideEmpty(result.get("message", GUIDE_EMPTY))
        state = {"guide_answer": answer, "guide_correct": result["status"] == "correct",
                 "guide_question": deepcopy(question)}
        if draft is not None and (is_pitch_question(question) or question.get("practice")):
            state["guide_draft"] = deepcopy(draft)
            if question.get("input_mode") == "text":
                state["guide_draft"]["answer"] = answer
        try:
            self.learning_store.advance_instruction(item["id"], "guide" if result["status"] == "correct" else "guide_failed",
                state=state, instruction_scope=item.get("instruction_scope"))
        except Exception as exc:
            self.record_error("learning_guide", "submit", exc)
            return GuideSaveFailed()
        return GuideAnswered(result["status"] == "correct", state.get("guide_draft"))

    def saved_guide_answer(self, item):
        """The answer saved in the lesson's guide (what a pitch guide's remark is drawn from after a restart)."""
        saved = self.learning_store.instruction_item(item["id"], item.get("instruction_scope")) or {}
        return saved.get("checkpoint", {}).get("guide_answer")

    def pass_caption(self, item, question):
        """Design 12 Q1: the caption of "seen, go on" after a recorded wrong answer, or None.

        gates.guide_may_continue decides (breadth first only); the store checks
        again for a replaced guide and for each step of a guided sequence.
        """
        from ..arrangement.rules.gates import guide_may_continue
        if not guide_may_continue(self.learning_store.arrangement_orientation(), answered_wrong=True):
            return None
        saved = self.learning_store.instruction_item(item["id"], item.get("instruction_scope")) or {}
        checkpoint = saved.get("checkpoint", {})
        if (saved.get("stage") != "guide" or checkpoint.get("guide_answer") is None or checkpoint.get("guide_correct") is True
                or (checkpoint.get("guide_question") or {}).get("id") != question.get("id")):
            return None
        sequence = checkpoint.get("guide_sequence") or []
        if sequence and checkpoint.get("guide_index", 0) < len(sequence) - 1:
            return PASS_STEP
        return PASS_GUIDE

    def finish_guide(self, item, question, passed):
        """Go on after the guide question: the next question of a guided sequence (GuideNext), or the end of the
        guide — the lesson moves on ("bridge" for a guide passed after a wrong answer, whose checkpoint keeps
        guide_correct false) and the line goes on (GuideFinished)."""
        saved = self.learning_store.instruction_item(item["id"], item.get("instruction_scope"))
        if saved.get("checkpoint", {}).get("guide_sequence"):
            advanced = self.learning_store.advance_guided_sequence(saved["id"], question_id=question["id"],
                                                                   instruction_scope=item.get("instruction_scope"))
            if not advanced["finished"]:
                return GuideNext(advanced["item"])
        self.learning_store.advance_instruction(item["id"], "bridge" if passed else "guide_correct",
                                                fair_new=self._fair_new(), instruction_scope=item.get("instruction_scope"))
        return GuideFinished()
