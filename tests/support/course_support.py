"""The one way tests reach the state and the seams of the course line's decision (课程学习: what comes next).

Design 13 stage S5. Every entry into the course line (app.continue_learning, from the continue action or
straight from a page) first prepares the round and the queue, settles on the learner's next course step
and then takes it: one of its own pages (所属内容库已停用, 材料待核, 需要日语语音 / 题型已关闭, 本次学习已保存
with 开始新一轮, 稍后继续, 先巩固前置知识), the batch introduction, the diagnostic line, a teaching step or a
question. How the decision keeps what it settled on, where it looks names up (the round functions, the
batch route, the queueing of missing questions, the exchange of withdrawn tasks, the left-out check of a
sealed round, the next-content and extra-practice rows, the wait timer, the learner store's head and
counts) and the helpers of its pages (the paused page, the replacement action of 材料待核, the
acknowledgement of prerequisites) are known only here. Tests never name the module or the attributes
behind these functions, so the decision can move without a test changing.

What stays outside, because it is the outer contract of every page: the fake screen's app.page /
app.title / app.body, and the app's own methods (the line entries, screen, the pages' targets), which
tests may replace on the instance and which the decision must keep calling through the app object. The
page readers and the call recorders are continue_support's own, re-exported here.

Public (tools/public_source_files.txt): it imports only support modules that are public and kanamemory
modules.
"""
from dataclasses import dataclass

from support import continue_support as _continue
from support.continue_support import (Call, captions, control, controls, made_with, names, page, page_made_with,
                                      press, record_controls, record_entries, record_screens, screens,
                                      set_skipped_teaching, skipped_teaching, texts)

__all__ = ["Call", "captions", "control", "controls", "made_with", "names", "page", "page_made_with", "press",
           "record_controls", "record_entries", "record_screens", "screens", "set_skipped_teaching",
           "skipped_teaching", "texts"]

# The caption of the prerequisite page's button that lets the learner go on with the lesson itself.
SKIP_PREREQUISITES = "我已了解前置，继续本课"
# The label under which record_queueing logs one queueing of the missing questions.
QUEUEING = "_queue_missing_learning_questions"
# The calls by which the decision hands its step to another part of the app (it returns after each).
DECISION_CALLS = ("show_intro_batch", "continue_diagnostic", "show_learning_instruction", "show_learning_question")
# The calls by which the decision's pages hand the learner back to the continue action or the line itself.
HAND_BACKS = ("continue_study_path", "continue_learning", "continue_added_learning", "check_learning_wait")


@dataclass
class Asked(Call):
    """One question the decision asked: its arguments, its answer, and the course step settled on by then.
    ``inside``: for a learner-store call, the recorded store call it was made from (None: the line's own)."""
    head: object = None
    inside: object = None


def _teaching():
    from kanamemory import course_teaching_runtime
    return course_teaching_runtime


# --- what the decision keeps -------------------------------------------------------------------------------

def course_head(app):
    """The course step the line settled on last (None when it ended the round or nothing was settled yet)."""
    return _teaching().head(app)


def set_course_head(app, head):
    """As if the line had settled on ``head`` last (None: it remembers no step)."""
    _teaching().set_head(app, head)


def wait_due(app):
    """The due time the visible wait page resumes at (None when the page has none or none is shown)."""
    return getattr(app, "_learning_wait_due", None)


def wait_timer(app):
    """The timer that resumes the visible wait page at its due time, None when none is armed."""
    return getattr(app, "_review_wait_handle", None)


def is_learning_page(name):
    """Whether Back on a page of this name asks to leave the course line (or leaves it at once)."""
    from kanamemory.learning_ui import LEARNING_PAGES
    return name in LEARNING_PAGES


def leaves_at_once(name):
    """Whether Back on a page of this name returns home at once, without the 返回今日？ question."""
    from kanamemory.learning_ui import LEARNING_ACTIVE, LEARNING_PAGES
    return name in LEARNING_PAGES and name not in LEARNING_ACTIVE


def is_focused_page(name):
    """Whether a page of this name hides the app's navigation (a focused study page)."""
    from kanamemory.app import FOCUSED_PAGES
    return name in FOCUSED_PAGES


# --- the queue the decision prepares --------------------------------------------------------------------------

def queue_missing_questions(app):
    """Prepare the course queue as the decision does before a round is sealed: exchange withdrawn task forms
    (one bounded pass) and queue a question for every lesson direction that has none."""
    return app._queue_missing_learning_questions()


def withdrawn_pass(app):
    """One bounded pass that exchanges queued tasks of a withdrawn form; returns how many were exchanged."""
    return app._replace_withdrawn_questions()


def set_withdrawn_pass_seconds(monkeypatch, seconds):
    """A pass that exchanges withdrawn tasks stops after ``seconds`` (always after at least one)."""
    from kanamemory import learning_ui
    monkeypatch.setattr(learning_ui, "WITHDRAWN_PASS_SECONDS", seconds)


def replace_withdrawn_exchange(app, monkeypatch, replacement):
    """Exchanging withdrawn tasks (the pass, and the one about to be shown) becomes
    ``replacement(question_id=None)``, which returns how many were exchanged."""
    monkeypatch.setattr(app, "_replace_withdrawn_questions", replacement)


def record_withdrawn_exchange(app, monkeypatch, *, log=None, answer=None):
    """Record each exchange of withdrawn tasks with its arguments; ``answer``: None exchanges for real, a
    callable(question_id=None) answers instead. Each Asked keeps the count returned."""
    log = [] if log is None else log
    real = app._replace_withdrawn_questions

    def exchange(question_id=None):
        call = Asked("exchange", () if question_id is None else (question_id,), {}, page=getattr(app, "page", None))
        log.append(call)
        call.result = real(question_id) if answer is None else answer(question_id)
        return call.result
    monkeypatch.setattr(app, "_replace_withdrawn_questions", exchange)
    return log


def record_queueing(app, monkeypatch, *, log=None, real=True):
    """Record each queueing of the missing questions (logged as QUEUEING); ``real`` False queues nothing."""
    log = [] if log is None else log
    original = app._queue_missing_learning_questions

    def queueing(*args, **kwargs):
        log.append(Asked(QUEUEING, args, dict(kwargs), page=getattr(app, "page", None)))
        if real:
            return original(*args, **kwargs)
        return None
    monkeypatch.setattr(app, "_queue_missing_learning_questions", queueing)
    return log


# --- the round ----------------------------------------------------------------------------------------------

ROUND_STEPS = ("begin", "sealed", "seal", "mark_finished")


def record_round(monkeypatch, *, log=None):
    """Record every call of the round's begin / sealed / seal / mark_finished (by anyone), with its keyword
    arguments and answer, without changing what they do."""
    from kanamemory import practice_round
    log = [] if log is None else log
    for name in ROUND_STEPS:
        original = getattr(practice_round, name)

        def step(store, *args, _name=name, _original=original, **kwargs):
            call = Asked(_name, args, dict(kwargs))
            log.append(call)
            call.result = _original(store, *args, **kwargs)
            return call.result
        monkeypatch.setattr(practice_round, name, step)
    return log


def patch_round_sealed(monkeypatch, answer):
    """Whether the round is sealed is ``answer(store)`` for everyone who asks."""
    from kanamemory import practice_round
    monkeypatch.setattr(practice_round, "sealed", answer)


# --- the learner store's head and counts ----------------------------------------------------------------------

def record_store_calls(app, monkeypatch, *names, log=None, answers=None):
    """Record each call the course line makes to these learner-store methods (current, counts,
    admit_word_listening, ensure_prerequisites, get_question, content_question_blocked, prerequisite_status,
    acknowledge_prerequisites, …) with the exact arguments and the course step settled on at that moment.
    ``answers``: {name: callable(*args, **kwargs)} answers those instead of the store."""
    log = [] if log is None else log
    answers = answers or {}
    store = app.learning_store
    running = []
    for name in names:
        original = getattr(store, name)

        def ask(*args, _name=name, _original=original, **kwargs):
            call = Asked(_name, args, dict(kwargs), page=getattr(app, "page", None), head=course_head(app),
                         inside=running[-1] if running else None)
            log.append(call)
            running.append(_name)
            try:
                call.result = (answers[_name] if _name in answers else _original)(*args, **kwargs)
            finally:
                running.pop()
            return call.result
        monkeypatch.setattr(store, name, ask)
    return log


def record_head_reads(app, monkeypatch, *, log=None, answer=None):
    """Record each time the decision asks the learner store for the course head (current) and counts, with
    the exact arguments. ``answer``: None asks the store; a callable(name, *args, **kwargs) answers instead
    (name "current" or "counts")."""
    answers = None if answer is None else {
        name: (lambda *args, _name=name, **kwargs: answer(_name, *args, **kwargs)) for name in ("current", "counts")}
    return record_store_calls(app, monkeypatch, "current", "counts", log=log, answers=answers)


def replace_course_heads(app, monkeypatch, *heads, log=None):
    """The learner store's course head is each of ``heads`` in turn (the last one again afterwards); the
    counts stay the store's own. Returns the log of the reads."""
    remaining = list(heads)

    def answer(name, *args, **kwargs):
        if name == "counts":
            return original_counts(*args, **kwargs)
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]
    original_counts = app.learning_store.counts
    return record_head_reads(app, monkeypatch, log=log, answer=answer)


# --- the routes the decision asks ---------------------------------------------------------------------------

def record_batch_route(app, monkeypatch, *, log=None, answer=None):
    """Record each question whether the batch introduction takes this step (current, enabled kinds), with
    the course step the line had settled on at that moment. ``answer``: None asks the real route (it may
    open the batch and show it); False (or a callable answering False) declines without acting. For "the
    batch takes this head" use batch_takes_head."""
    log = [] if log is None else log
    real = app.route_intro_batch
    assert answer is not True, "use batch_takes_head"

    def route(current, enabled):
        call = Asked("route_intro_batch", (current, enabled), {}, page=getattr(app, "page", None),
                     head=course_head(app))
        log.append(call)
        call.result = (real(current, enabled) if answer is None else answer(current, enabled) if callable(answer)
                       else bool(answer))
        assert call.result is not True or answer is None, "use batch_takes_head"
        return call.result
    monkeypatch.setattr(app, "route_intro_batch", route)
    return log


def batch_takes_head(app, monkeypatch, *, log=None):
    """The batch introduction takes whatever head it is asked about (without opening a batch in the store),
    and the batch page is shown as the line shows it then (app.show_intro_batch()). Each question is logged
    as record_batch_route logs it."""
    log = [] if log is None else log

    def route(current, enabled):
        call = Asked("route_intro_batch", (current, enabled), {}, page=getattr(app, "page", None),
                     head=course_head(app), result=True)
        log.append(call)
        # The route only answers; the line shows the batch page after a True (course_runtime.perform_course).
        return True
    monkeypatch.setattr(app, "route_intro_batch", route)
    return log


def record_kind_reads(app, monkeypatch, *, log=None):
    """Record each read of the learner's saved kinds ("saved_kinds") and of the kinds this device can present
    ("enabled_kinds") without changing them."""
    log = [] if log is None else log
    for label, name in (("saved_kinds", "_saved_learning_kinds"), ("enabled_kinds", "learning_preferences")):
        original = getattr(app, name)

        def read(*args, _label=label, _original=original, **kwargs):
            call = Asked(_label, args, dict(kwargs), page=getattr(app, "page", None))
            log.append(call)
            call.result = _original(*args, **kwargs)
            return call.result
        monkeypatch.setattr(app, name, read)
    return log


def record_left_out(app, monkeypatch, *, log=None, answer=None):
    """Record each question whether a "disabled" head of a sealed round is only work the round could not
    queue (current, read context). ``answer``: None asks the real check; True/False answers instead."""
    log = [] if log is None else log
    real = getattr(app, "_sealed_round_left_out", None)

    def left_out(current, read):
        call = Asked("left_out", (current, read), {}, page=getattr(app, "page", None))
        log.append(call)
        call.result = real(current, read) if answer is None else bool(answer)
        return call.result
    monkeypatch.setattr(app, "_sealed_round_left_out", left_out)
    return log


# --- the rows the decision's pages hand in ----------------------------------------------------------------------

def replace_next_content_rows(app, monkeypatch, builder, *, log=None):
    """The next-content rows (继续已加入的学习 / 继续下一节 / 添加内容 …) the course pages show become
    ``builder()``; each build is logged."""
    log = [] if log is None else log

    def rows():
        log.append(Asked("next_content_rows", (), {}, page=getattr(app, "page", None)))
        return builder()
    monkeypatch.setattr(app, "learning_next_content_controls", rows)
    return log


def next_content_rows(app):
    """The next-content rows as the course pages build them now."""
    return app.learning_next_content_controls()


def replace_extra_rows(monkeypatch, builder, *, log=None):
    """The 继续加练 rows of the wait page become ``builder(app)``; each build is logged with the app."""
    from kanamemory import extra_practice_runtime
    log = [] if log is None else log

    def rows(app):
        log.append(Asked("extra_rows", (app,), {}, page=getattr(app, "page", None)))
        return builder(app)
    monkeypatch.setattr(extra_practice_runtime, "extra_button_rows", rows)
    return log


def record_wait_arming(app, monkeypatch, *, log=None):
    """Record each arming of the wait page's timer with the due time it is armed for (it still arms)."""
    log = [] if log is None else log
    real = app.arm_learning_wait

    def arm(due):
        log.append(Asked("arm_wait", (due,), {}, page=getattr(app, "page", None)))
        return real(due)
    monkeypatch.setattr(app, "arm_learning_wait", arm)
    return log


# --- the helpers of the decision's pages ------------------------------------------------------------------------

def show_paused(app, message):
    """Draw the paused page (需要日语语音 when the heard-word kind is kept on without a voice, else 题型已关闭
    with ``message``), as the batch quiz draws it for a closed kind."""
    from kanamemory.course_line.model import PAUSED_QUIZ, Paused
    from kanamemory.course_runtime import perform_course
    return perform_course(app, Paused(PAUSED_QUIZ, message))


def replacement_controls(app, question_ids=(), item=None):
    """The replacement action of the material page (使用更新后的练习继续) for these questions or this
    teaching item, prepared now; [] when nothing can be replaced."""
    return app._learning_replacement_controls(question_ids, item)


def replace_replacement_controls(app, monkeypatch, builder, *, log=None):
    """The material page's replacement rows become ``builder(question_ids, item)``; each build is logged with
    its exact arguments."""
    log = [] if log is None else log

    def rows(*args, **kwargs):
        log.append(Asked("replacement_rows", args, dict(kwargs), page=getattr(app, "page", None)))
        bound = dict(zip(("question_ids", "item"), args), **kwargs)
        return builder(bound.get("question_ids", ()), bound.get("item"))
    monkeypatch.setattr(app, "_learning_replacement_controls", rows)
    return log


def patch_replacement_service(monkeypatch, **methods):
    """The replacement record's prepare_question / prepare_guide / commit become the given callables
    (self first), for every replacement the course pages prepare or commit."""
    from kanamemory.learning_replacement import LearningReplacementStore
    for name, method in methods.items():
        assert name in {"prepare_question", "prepare_guide", "commit"}, name
        monkeypatch.setattr(LearningReplacementStore, name, method)


def patch_guide_blocked(monkeypatch, answer):
    """Whether a teaching item's frozen guide question is blocked by a newer material version is
    ``answer(store, item, guide)`` when the replacement action is prepared."""
    from kanamemory import personal_content_versions
    monkeypatch.setattr(personal_content_versions, "question_blocked", answer)


def press_skip_prerequisites(app):
    """Press 我已了解前置，继续本课 on the prerequisite page (with itself as the widget)."""
    return press(app, SKIP_PREREQUISITES)


# --- the calls the decision makes ------------------------------------------------------------------------------

def watch(app, monkeypatch, *, real=False, names=DECISION_CALLS + HAND_BACKS, log=None):
    """One ordered log of the decision's hand-off calls, the pages' hand-backs (``names``) and screen.

    ``real``: whether the recorded methods still run (a callable is asked at each call); screen always
    draws. Install it before the page is drawn: a control holds the method it was drawn with.
    """
    log = [] if log is None else log
    record_entries(app, monkeypatch, real=real, log=log, names=names)
    record_screens(app, monkeypatch, log=log)
    return log


def record_presses(app, *names, log=None):
    """The app's page targets ``names`` only record their calls from now on (for an app object the test owns
    alone; nothing is restored). Install it before the page is drawn."""
    log = [] if log is None else log
    for name in names:
        def target(*args, _name=name, **kwargs):
            log.append(Call(_name, args, dict(kwargs), page=getattr(app, "page", None)))
        setattr(app, name, target)
    return log


def calls(log, *wanted):
    """(name, args, kwargs) of the calls in a log, in order (only ``wanted`` names when given), leaving out
    the learner store's calls to itself."""
    return [(call.name, call.args, call.kwargs) for call in log
            if (not wanted or call.name in wanted) and getattr(call, "inside", None) is None]


def own_calls(log):
    """The calls of a log that the course line made itself (not the learner store's calls to itself)."""
    return [call for call in log if getattr(call, "inside", None) is None]


def record_decisions(monkeypatch, *, log=None, answer=None):
    """Record each course step the decision settles on, once it has settled (logged as "decide" with its
    keyword arguments and the step as result). ``answer``: None decides for real; a callable(app, **kwargs)
    decides instead (it may raise)."""
    from kanamemory import course_runtime
    log = [] if log is None else log
    real = course_runtime.decide_course

    def decide(app, **kwargs):
        step = (real if answer is None else answer)(app, **kwargs)
        log.append(Asked("decide", (), dict(kwargs), result=step, page=getattr(app, "page", None)))
        return step
    monkeypatch.setattr(course_runtime, "decide_course", decide)
    return log


def round_plan(app):
    """The in-memory round plan: (sealed, finished, promoted, course_order_token)."""
    return _continue.round_plan(app)


# --- the batch introduction (成批引入: the cards 新项说明 and the quiz 批小测) -----------------------------------

# The learner-store calls the batch introduction makes: opening a batch, recording that a card was seen,
# the learner's choice on a card, freezing the quiz, answering it, closing the batch, opening a later
# ability of a batch item, and reading the open batch.
BATCH_STORE_CALLS = ("start_intro_batch", "mark_instruction_exposed", "choose_intro_card", "start_intro_quiz",
                     "answer_intro_quiz", "complete_intro_batch", "open_batch_ability", "intro_batch")
# The calls by which the batch hands the learner back to the line when it is over.
BATCH_HAND_BACKS = ("continue_study_path", "continue_learning")


def batch_quiz_question(app):
    """The quiz question the batch quiz page shown now asks (None once it was answered, on the page
    本批小测已完成 and before the paused page)."""
    from kanamemory.intro_batch_runtime import batch_page
    return batch_page(app).question


def batch_card_entry(app):
    """The batch entry (content id and introduction id) of the card the page 新项说明 shows, as drawn."""
    from kanamemory.intro_batch_runtime import batch_page
    return batch_page(app).card_entry


def batch_lesson(app):
    """The lesson the batch page shown is about (the card's item, or the quiz item asked or settled last)."""
    return _teaching().item(app)


def batch_quiz_result(app):
    """The result area of the batch quiz page shown: its last row (empty until an answer is given)."""
    return app.body.children[-1]


def batch_quiz_result_texts(app):
    """Every non-empty text of the quiz page's result area, in drawing order."""
    from support.fake_ui import descendants
    return [str(view.text) for view in descendants(batch_quiz_result(app)) if view.text]


def batch_quiz_choices(app):
    """(caption, enabled) of each answer button of the quiz page shown, in order: the pressable rows of the
    page itself (the result area's controls are not among them)."""
    return [(view.text, view.enabled) for view in app.body.children if view.on_press]


def record_batch_store_calls(app, monkeypatch, *names, log=None, answers=None):
    """Record each learner-store call of the batch introduction (BATCH_STORE_CALLS, or ``names``) with its
    exact arguments, as record_store_calls does; ``answers`` answers those instead of the store."""
    return record_store_calls(app, monkeypatch, *(names or BATCH_STORE_CALLS), log=log, answers=answers)


def record_batch_hand_back(app, monkeypatch, *, study_path=True, log=None):
    """Record the batch's hand-back to the line without running it. ``study_path``: the app offers the study
    path (a recording continue_study_path is installed); False: it offers none (continue_study_path is
    removed). continue_learning is recorded in both cases. Install it before the page is drawn."""
    log = [] if log is None else log
    if study_path:
        def study(*args, **kwargs):
            log.append(Call("continue_study_path", args, dict(kwargs), page=getattr(app, "page", None)))
        monkeypatch.setattr(app, "continue_study_path", study, raising=False)
    elif hasattr(app, "continue_study_path"):
        monkeypatch.delattr(app, "continue_study_path")
    record_entries(app, monkeypatch, real=False, log=log, names=("continue_learning",))
    return log


def record_known_reads(monkeypatch, *, log=None):
    """Record each read of the learner's known items (the ready items and the introduced kana) that a
    page makes, logged as "known_ids"; the reads still answer."""
    from kanamemory import course_planning
    log = [] if log is None else log
    real = course_planning.known_ids

    def read(*args, **kwargs):
        call = Asked("known_ids", args, dict(kwargs))
        log.append(call)
        call.result = real(*args, **kwargs)
        return call.result
    monkeypatch.setattr(course_planning, "known_ids", read)
    return log


# --- the question being answered (答题) -----------------------------------------------------------------------

def _answering():
    from kanamemory import course_answer_runtime
    return course_answer_runtime


def course_question(app):
    """The course question on the page: the one being answered or, on its result page, the one just answered
    (None when the line holds none)."""
    return _answering().answer_session(app).question


def set_course_question(app, question):
    """As if ``question`` were the course question on the page (None: the line holds none)."""
    _answering().assign(app, question=question)


def answer_draft(app):
    """The draft the line holds for the question on the page: what was typed or chosen so far and the aids
    used. It is the line's own dict — changing it changes the draft the line goes on with."""
    return _answering().answer_session(app).state


def set_answer_draft(app, draft):
    """As if the line held ``draft`` for the question on the page."""
    _answering().assign(app, state=draft)


def typed_answer(app):
    """The text the page's answer field started with (the draft's answer when the page was drawn)."""
    return _answering().answer_session(app).answer


def chosen_blocks(app):
    """The blocks chosen so far for a question answered by putting blocks together, in their order."""
    return _answering().answer_session(app).parts


def set_chosen_blocks(app, parts):
    """As if these blocks were the ones chosen so far."""
    _answering().assign(app, parts=parts)


def used_blocks(app):
    """The positions of the offered blocks that the chosen ones use."""
    return _answering().answer_session(app).used


def set_used_blocks(app, used):
    """As if the chosen blocks used these positions of the offered ones."""
    _answering().assign(app, used=used)


def audio_heard(app):
    """Whether the audio of the question on the page has played successfully (a heard question can be
    answered only then)."""
    return _answering().answer_session(app).audio_ok


def set_audio_heard(app, heard):
    """As if the audio of the question on the page had (or had not) played successfully."""
    _answering().assign(app, audio_ok=heard)


def result_view(app):
    """(question id, result) of the result page drawn last."""
    return _answering().answer_session(app).feedback_view


# --- answering a question, up to the submit: the answer field, the status line, what one drawing reads ------

def answer_field(app):
    """The text field of the question page drawn last (None when that page has none)."""
    return getattr(app, "_learning_input", None)


def type_into(field, text):
    """Type ``text`` into an answer field as the keyboard does: the value changes, then its change handler runs
    with the field itself."""
    field.value = text
    field.on_change(field)


def status_text(app):
    """The status line under the answer controls of the question page drawn last ("" when it says nothing)."""
    status = getattr(app, "_learning_status", None)
    return None if status is None else status.text


ANSWER_WRITES = ("save_draft", "submit", "finalize")


def record_answer_writes(app, monkeypatch, *, log=None, before=None):
    """Record each write of the learner store that answering makes (save_draft, submit, finalize) with its
    arguments AS THEY WERE AT THE CALL (copied: the line passes its live draft, which changes later). The
    writes still happen. ``before``: {name: callable(*args, **kwargs)} run first at each such call; it may
    look at the app at that moment or raise to make that write fail (nothing is then written)."""
    from copy import deepcopy
    log = [] if log is None else log
    before = {} if before is None else before
    store = app.learning_store
    originals = {name: getattr(store, name) for name in ANSWER_WRITES}

    def answer(name):
        def write(*args, **kwargs):
            call = log[-1]
            call.args, call.kwargs = deepcopy(args), deepcopy(kwargs)
            if name in before:
                before[name](*args, **kwargs)
            return originals[name](*args, **kwargs)
        return write
    return record_store_calls(app, monkeypatch, *ANSWER_WRITES, log=log,
                              answers={name: answer(name) for name in ANSWER_WRITES})


def record_question_page_reads(app, monkeypatch, *, log=None):
    """Record each read of the prompt display preferences ("prompt_preferences": the setting the prompt and the
    submit are drawn with), each computation of the learner's known ids ("known_ids") and each read of the
    kinds this device can present ("enabled_kinds"), without changing them."""
    from kanamemory import course_planning
    from kanamemory.learning_ui import PROMPT_PREFS
    log = [] if log is None else log
    get_setting = app.store.get_setting

    def setting(key, *args, **kwargs):
        if key == PROMPT_PREFS:
            log.append(Asked("prompt_preferences", (key, *args), dict(kwargs), page=getattr(app, "page", None)))
        return get_setting(key, *args, **kwargs)
    monkeypatch.setattr(app.store, "get_setting", setting)
    known = course_planning.known_ids

    def known_ids(*args, **kwargs):
        log.append(Asked("known_ids", args, dict(kwargs), page=getattr(app, "page", None)))
        return known(*args, **kwargs)
    monkeypatch.setattr(course_planning, "known_ids", known_ids)
    preferences = app.learning_preferences

    def enabled(*args, **kwargs):
        log.append(Asked("enabled_kinds", args, dict(kwargs), page=getattr(app, "page", None)))
        return preferences(*args, **kwargs)
    monkeypatch.setattr(app, "learning_preferences", enabled)
    return log


def record_diagnostic_errors(monkeypatch, *, log=None):
    """Record each error the course line reports to the app's diagnostics (area, action, the exception), without
    changing what is reported."""
    from kanamemory import diagnostic_events
    log = [] if log is None else log
    real = diagnostic_events.record_error

    def record(area, action, error, *args, **kwargs):
        log.append(Asked("record_error", (area, action, error, *args), dict(kwargs)))
        return real(area, action, error, *args, **kwargs)
    monkeypatch.setattr(diagnostic_events, "record_error", record)
    return log


# --- the submit, the result page and the rating (提交、作答结果、评级) --------------------------------------------

def record_prompt_plans(monkeypatch, *, log=None, plan=None):
    """Record each display plan of the prompt that a submit asks for to fill in what the learner was shown
    (furigana, accent marks), with the question, the prompt preferences and the known ids it is asked with.
    ``plan``: None answers with the real plan; a dict answers the real plan updated with these fields."""
    from kanamemory.course_line import answer as home
    log = [] if log is None else log
    real = home.prompt_support

    def support(question, preferences=None, known_ids=None):
        call = Asked("prompt_plan", (question, preferences, known_ids), {})
        log.append(call)
        call.result = real(question, preferences, known_ids)
        if plan is not None:
            call.result = {**call.result, **plan}
        return call.result
    monkeypatch.setattr(home, "prompt_support", support)
    return log


def permit_early_answers(monkeypatch, *question_ids):
    """Extra practice has handed out these course questions to be answered before they are due (their
    submit carries the early permit, and the rating queues the unit's next question as an early one)."""
    from kanamemory import extra_practice_runtime
    permitted = set(question_ids)
    monkeypatch.setattr(extra_practice_runtime, "early_submit",
                        lambda app, question_id: {"early": True} if question_id in permitted else {})


def record_answer_autoplay(monkeypatch, *, log=None):
    """Record each request to play the answer aloud after a result page is drawn (its text and its key),
    without changing what it does."""
    from kanamemory import course_answer_runtime as home
    log = [] if log is None else log
    real = home.autoplay_answer

    def autoplay(app, text, key):
        call = Asked("autoplay", (text, key), {}, page=getattr(app, "page", None))
        log.append(call)
        call.result = real(app, text, key)
        return call.result
    monkeypatch.setattr(home, "autoplay_answer", autoplay)
    return log


def replace_settlement(monkeypatch, answer, *, log=None):
    """How a result is settled (formal review or not) becomes ``answer(real, item, question, submitted,
    **kwargs)``, ``real`` being the settlement as it was; each question is logged with its arguments."""
    from kanamemory import assessment_policy
    log = [] if log is None else log
    real = assessment_policy.settlement_decision

    def decide(item, question, submitted, **kwargs):
        call = Asked("settlement", (item, question, submitted), dict(kwargs))
        log.append(call)
        call.result = answer(real, item, question, submitted, **kwargs)
        return call.result
    monkeypatch.setattr(assessment_policy, "settlement_decision", decide)
    return log


def replace_error_details(monkeypatch, builder, *, log=None):
    """The error details of the result's detail page become ``builder(question, result)``; each build is
    logged with the question and the result it was given."""
    from kanamemory import semantic_errors
    log = [] if log is None else log

    def nodes(u, question, result):
        log.append(Asked("error_details", (question, result), {}))
        return builder(question, result)
    monkeypatch.setattr(semantic_errors, "feedback_nodes", nodes)
    return log


def replace_teaching_allowed(monkeypatch, answer):
    """Whether a course item's teaching material may be shown (it has been checked) is ``answer(item)``."""
    from kanamemory import assessment_policy
    monkeypatch.setattr(assessment_policy, "teaching_allowed", lambda item, *, store=None: answer(item))


def record_errors(monkeypatch, *, log=None):
    """Record each error the app notes for its diagnostics as (operation, stage, error), noting nothing."""
    from kanamemory import diagnostic_events
    log = [] if log is None else log
    monkeypatch.setattr(diagnostic_events, "record_error",
                        lambda operation, stage, error: log.append((operation, stage, error)))
    return log


def record_speech_stops(app, monkeypatch, *, log=None):
    """Log each time the app stops any speech that is playing (as "stop"), into ``log``."""
    log = [] if log is None else log
    real = app.platform.stop

    def stop(*args, **kwargs):
        log.append(Call("stop", args, dict(kwargs), page=getattr(app, "page", None)))
        return real(*args, **kwargs)
    monkeypatch.setattr(app.platform, "stop", stop)
    return log


def record_setting_reads(app, monkeypatch, *keys, log=None):
    """Record each read of these learner settings (logged under the key), without changing the answer."""
    log = [] if log is None else log
    real = app.store.get_setting

    def get_setting(key, *args, **kwargs):
        if key in keys:
            log.append(Call(key, args, dict(kwargs), page=getattr(app, "page", None)))
        return real(key, *args, **kwargs)
    monkeypatch.setattr(app.store, "get_setting", get_setting)
    return log


# --- the teaching step (讲解与引导) ---------------------------------------------------------------------------

def taught_item(app):
    """The lesson the teaching pages are about: the item being introduced or led through a guide, or the one
    whose card or batch quiz is shown (None when the line holds none)."""
    return _teaching().item(app)


def set_taught_item(app, item):
    """As if ``item`` were the lesson the teaching pages are about (None: the line holds none)."""
    _teaching().set_item(app, item)


def guide_question(app):
    """The guide question on the page: the frozen question the learner is led through (None when there is
    none)."""
    return _teaching().guide(app)


def set_guide_question(app, question):
    """As if ``question`` were the guide question on the page (None: there is none)."""
    _teaching().set_guide(app, question)


def familiarity_state(app):
    """What the page 开始前 holds while the learner says how familiar a lesson is: the lesson, the ability
    asked now, the levels chosen so far and whether a choice is being saved. It is the line's own dict."""
    return _teaching().familiarity(app)


def set_familiarity_state(app, state):
    """As if the page 开始前 held ``state``."""
    _teaching().set_familiarity(app, state)


# --- what a teaching head becomes, the page 开始前 and the teaching stop pages -----------------------------------

# The learner-store calls the teaching step makes while it decides what a teaching head becomes and while the
# page 开始前 is shown and answered (reads and writes together, so that their order can be seen).
TEACHING_STORE_CALLS = ("is_batch_route", "intro_batch", "prepare_initial_familiarity", "save_familiarity_draft",
                        "set_initial_familiarity", "has_other_instruction_exposure", "prerequisite_status",
                        "mark_instruction_exposed", "advance_instruction", "instruction_item", "guided_sequence",
                        "guided_question")


@dataclass
class TeachingMoment:
    """What the teaching pages held when a learner-store call was made: the lesson they were about and the
    guide question on the page (both as held, not copied)."""
    item: object = None
    guide: object = None


def record_teaching_calls(app, monkeypatch, *names, log=None, answers=None):
    """Record each call the teaching step makes to these learner-store methods (TEACHING_STORE_CALLS when none
    are named) with its exact arguments; each Asked also keeps ``moment``, a TeachingMoment of the call.
    ``answers``: {name: callable(*args, **kwargs)} answers those instead of the store (it may raise)."""
    log = [] if log is None else log
    answers = dict(answers or {})
    store = app.learning_store
    names = names or TEACHING_STORE_CALLS
    originals = {name: getattr(store, name) for name in names}

    def answer(name):
        def call(*args, **kwargs):
            log[-1].moment = TeachingMoment(taught_item(app), guide_question(app))
            return (answers[name] if name in answers else originals[name])(*args, **kwargs)
        return call
    return record_store_calls(app, monkeypatch, *names, log=log,
                              answers={name: answer(name) for name in names})


def guide_result_column(app):
    """The column under a guide question where its result is shown (the guide page's own widget; None before
    any guide was prepared)."""
    return getattr(app, "_learning_guide_result", None)


# --- the guide page (引导题) and the introduction page's buttons: what the guide page shows and holds ----------

def _guide_page_controls(app):
    """The live controls of the guide page drawn last ({} for a guide answered by plain choice buttons)."""
    return getattr(app, "_learning_guide_context", None) or {}


def guide_draft(app):
    """What the learner has entered on the guide page and not submitted yet (None when the page holds none).
    It is the page's own dict."""
    return _guide_page_controls(app).get("draft")


def guide_locked(app):
    """Whether the guide page's answer controls are locked (after a right answer)."""
    return bool(_guide_page_controls(app).get("locked"))


def guide_status(app):
    """The status line under the guide's answer controls ("" when it says nothing, None without one)."""
    status = _guide_page_controls(app).get("status")
    return None if status is None else status.text


def guide_field(app):
    """The text field of the guide page drawn last (None when it has none)."""
    return _guide_page_controls(app).get("input")


def guide_answer_controls(app):
    """The answer controls of the guide page drawn last (choices, blocks, field, 撤回末项, 提交, 跟随示范完成)."""
    return list(_guide_page_controls(app).get("buttons") or [])


def guide_result(app):
    """The result column of the guide page: what is shown under the answer once it is graded."""
    return getattr(app, "_learning_guide_result", None)


def guide_result_texts(app):
    """Every non-empty text of the guide's result column, in drawing order."""
    from support.fake_ui import descendants
    column = guide_result(app)
    return [] if column is None else [str(view.text) for view in descendants(column) if view.text]


def guide_result_made_with(app):
    """(kind, text, keyword arguments) of every control of the guide's result column (record_controls first)."""
    column = guide_result(app)
    return [] if column is None else made_with(column)


def set_orientation(app, mode):
    """The learner's arrangement: "breadth" (breadth first) or "thorough" (depth first)."""
    from kanamemory.arrangement.services.preferences import ArrangementPreferences
    ArrangementPreferences(app.store).set_orientation(mode)


def record_guide_autoplay(monkeypatch, *, log=None):
    """Record each request to play a guide's answer aloud after its result is drawn (its text and its key),
    without changing what it does."""
    from kanamemory import learning_ui as home
    log = [] if log is None else log
    real = home.autoplay_answer

    def autoplay(app, text, key):
        call = Asked("autoplay", (text, key), {}, page=getattr(app, "page", None))
        log.append(call)
        call.result = real(app, text, key)
        return call.result
    monkeypatch.setattr(home, "autoplay_answer", autoplay)
    return log
