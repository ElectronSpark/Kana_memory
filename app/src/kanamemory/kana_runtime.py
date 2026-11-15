"""The kana practice (假名练习) on the app side: it decides what the practice shows next, then performs it.

decide_kana brings the practice up to date with the store (the saved progress of its session, the frozen
questions of a queue that has just started, the kinds the queue may ask, the prerequisites of its items, the
head the store prepares), takes the position and the marks of assisted attempts from that head, seals the
round, sets the question at the place and returns one step (kana_line.model) without drawing anything.
perform_kana makes the calls that step stands for: the end of the queue (the route goes on, a practice deck
opens, extra practice takes over, or one of the two end pages), one of the line's four pages that stop instead
of asking (kana_line.ui.line_pages), the first introduction of a kana or its wait, the restore of a parked
word, or the question page. The pages' buttons hand their KanaAction to run_kana_action, which makes the call
when the button is pressed. show_question calls both inside its own body.

This module reads the app object and imports neither Toga nor the control components nor the app module; the
page module, which creates controls, is imported only where a page is drawn. The App class arrives as ``K`` from
the entry method and each of its helpers is looked up on it at the moment it is called; every app method is
looked up on the app object at the moment of the call. Imports made at call time stay at call time, so their own
patch targets keep working; the four functions of other modules bound here by name are replaced by tests through
tests/support/kana_support.py (replace_asked). The wording of the review wait has one source, kana_line.display:
this module asks it directly, and the app module's review_wait_status, which the wait's timer uses, asks it too.
"""
from __future__ import annotations

from datetime import datetime

from . import app_clock
from . import kana_attempt_runtime as kana_attempt
from . import kana_session_runtime as kana_session
from .kana_learning_intro import intro_wait_until, should_introduce
from .kana_line import attempt as attempt_rules
from .kana_line import display
from .kana_line.model import (Ask, ContentUpdated, Finished, Introduce, IntroWait, KanaActionKind, KindsPaused,
                              PrerequisiteMissing, ResumeParked, ReviewWait, Stopped, answer_started)
from .practice_availability import summarize_queue_availability
from .practice_preferences import WORD_PRACTICE_KINDS
from .study_path_runtime import current_route_next


def _lane(app):
    return getattr(app.store, "practice_lane", "main")


def decide_kana(app, K, *, first_teaching=False, first_teaching_targets=None, prepared=None):
    """What the practice shows next (a step of kana_line.model); nothing is drawn.

    ``prepared`` (a PreparedKana of the same action): the head its caller prepared; the reconcile and the
    prepare are skipped only while its proof is still current. ``first_teaching``: ask the store for a
    teaching head of ``first_teaching_targets`` first.
    """
    lazy = K._lazy_practice_questions(app)
    given = None if first_teaching else prepared
    pending = ((app.store.load_session_progress(lazy_questions=True) if lazy else
                app.store.load_session_progress()) if kana_session.session(app).get("session_id") else None)
    waiting_until, head = None, None
    if (pending is None and kana_session.session(app).get("session_id")
            and kana_session.question_index(app) == 0 and kana_session.session_right(app) == 0):
        # A just-started queue has no checkpoint yet. Its frozen snapshot
        # owns the questions: start_session may stamp metadata (the kana
        # ability route) that the builder's in-memory copies lack.
        frozen = app.store.get_session_questions(kana_session.session_id(app), lazy=lazy)
        if len(frozen) == len(kana_session.questions(app)):
            kana_session.set_questions(app, frozen)
        pending = K._join_collecting_round(app, lazy)
    if isinstance(pending, dict) and pending["session_id"] == kana_session.session_id(app):
        reused = K._prepared_pending(app, given)
        if reused is None:
            given = None
        pending = reused if reused is not None else K._reconcile_practice_kinds(app, pending)
        if pending is False:
            return Stopped()
        if not pending.get("completed"):
            if _lane(app) == "main":
                from .kana_dependency_repair import ensure_kana_prerequisites
                repaired = ensure_kana_prerequisites(app.repo, app.store,
                    K._queue_kinds(app, pending), lazy_questions=lazy,
                    audio_available=bool(app.platform.tts_ready),
                    handwriting_available=getattr(app, "model_state", None) == "ready")
                if repaired["added"]:
                    pending = app.store.load_session_progress(lazy_questions=lazy)
            if first_teaching:
                head = app.store.prepare_next_question(kana_session.session_id(app),
                    lazy_questions=lazy, fair_new=lazy, teaching_only=True,
                    teaching_kinds=K._queue_kinds(app, pending),
                    teaching_targets=first_teaching_targets)
            if (head is None and given is not None
                    and given.current(app.store, kana_session.session_id(app), K._queue_kinds(app, pending))):
                # Nothing was written since continue prepared this head.
                head = given.prepared
            if head is None:
                head = (app.store.prepare_next_question(kana_session.session_id(app),
                    lazy_questions=True, fair_new=True) if lazy else
                    app.store.prepare_next_question(kana_session.session_id(app)))
        if head is None:
            kana_session.set_question_index(app, len(kana_session.questions(app)))
        elif isinstance(head, dict):
            kana_session.set_questions(app, head["questions"])
            kana_session.set_question_index(app, head["question_index"])
            kana_session.set_session_right(app, head["session_right"])
            kana_attempt.set_assisted(app, attempt_rules.saved_assisted(kana_session.session_id(app), head["assisted_indices"]))
            if head["state"].get("answer_revealed"):
                kana_attempt.assisted(app).add((kana_session.session_id(app), kana_session.question_index(app)))
            waiting_until = head.get("waiting_until_utc")
    from .practice_round import seal
    seal(app.store)
    if kana_session.question_index(app) >= len(kana_session.questions(app)):
        return Finished()
    kana_session.set_question(app, kana_session.questions(app)[kana_session.question_index(app)])
    kana_session.set_script(app, kana_session.question(app).script)
    if isinstance(head, dict) and head.get('content_blocked'):
        return ContentUpdated()
    if isinstance(head, dict) and head.get("settings_blocked"):
        return KindsPaused()
    if isinstance(head, dict) and head.get("dependency_blocked"):
        return PrerequisiteMissing(head.get("blocked_prerequisites", ()))
    started_answer = answer_started(head)
    if hasattr(app, "learning_store") and not waiting_until and not started_answer:
        if should_introduce(app, kana_session.question(app)):
            return Introduce(kana_session.question(app))
        intro_due = intro_wait_until(app.store, kana_session.question(app))
        if intro_due and intro_due > app_clock.local_now(intro_due.tzinfo):
            return IntroWait(intro_due)
    config = kana_session.question(app).metadata.get("practice_config", {})
    app.research_enabled = kana_session.question(app).metadata.get("research_content", config.get("include_research", False))
    word_table = kana_session.question(app).metadata.get("word_table", config.get("word_table"))
    if word_table in {"partial", "full"}:
        app.use_full_keyboard = word_table == "full"
    if waiting_until:
        return ReviewWait(waiting_until, head)
    if (kana_session.question(app).kind in WORD_PRACTICE_KINDS and started_answer
            and not getattr(app, "_restoring_practice", False)):
        return ResumeParked()
    return Ask(head)


def run_kana_action(app, action, widget=None):
    """Make the call a button of the practice line's own pages stands for (KanaAction's docstring)."""
    kind = action.kind
    if kind is KanaActionKind.WORD_SETUP:
        return app.show_word_drill_setup(widget)
    if kind is KanaActionKind.PRACTICE_SETUP:
        return app.show_practice_setup(widget)
    if kind is KanaActionKind.PRACTICE_HUB:
        return app.show_practice_hub(widget)
    if kind is KanaActionKind.RECORDS:
        return app.show_progress(widget)
    if kind is KanaActionKind.HOME:
        return app.show_home(widget)
    if kind is KanaActionKind.USE_NEW_CONTENT:
        return app.replace_content_question(action.session_id, action.retired_id)
    if kind is KanaActionKind.LEARN_RULE:
        return app.enroll_learning(action.rule)
    if kind is KanaActionKind.CHECK_PREREQUISITES:
        return app.resume_practice(widget)
    if kind is KanaActionKind.CHECK_WAIT:
        return app.continue_spaced_practice(widget)
    raise ValueError(f"unknown practice page action {action!r}")


def _practised_line(practised):
    from .arrangement.services.deck_presenter import practised_line
    return practised_line(practised)


def _clear_attempt(app) -> None:
    """A page that stops instead of asking: nothing seen, nothing submitted, no verdict."""
    kana_attempt.set_attempt(app, attempt_rules.cleared(kana_attempt.attempt(app)))


def _finish(app, K, on_action) -> None:
    """The queue is through: the route goes on, a practice deck opens, extra practice takes over, or an end
    page is shown."""
    from .new_dispatch import study_path_available
    from .practice_round import active as fixed_round
    if (fixed_round(app.store) and study_path_available(app) and _lane(app) == "main"):
        app.continue_study_path(_checkpoint_saved=True)
        return
    from .practice_round import mark_finished
    mark_finished(app.store)
    if K._open_practice_deck(app):
        return
    from .kana_line.ui import line_pages
    summary = app.store.get_session_summary(kana_session.session_id(app)) if kana_session.session(app).get("session_id") else None
    items, answered, summary = display.batch_counts(kana_session.questions(app), summary)
    extra = display.batch_extra(summary, _practised_line)
    right = kana_session.session_right(app)
    if _lane(app) == "word_romaji":
        model = display.word_done_model(answered, right)
        app.screen(model.page, model.title,
                   *line_pages.word_done_page(model, saved_row=app.note(model.saved), on_action=on_action))
        return
    from .extra_practice_runtime import extra_button_rows, take_over
    if take_over(app):
        return
    next_rows = (app._path_next_controls(*current_route_next(app.study_path_service()))
                 if study_path_available(app) else [])
    model = display.batch_done_model(items, answered, right, extra)
    app.screen(model.page, model.title,
               *line_pages.batch_done_page(model, next_rows=next_rows, extra_rows=extra_button_rows(app),
                                           on_action=on_action))


def _prerequisites(app, blocked):
    """(the kana symbols, the rules' lessons) of the identities a head waits for; what the content does not
    know is left out."""
    symbols, rules = [], []
    for identity in blocked:
        if identity.startswith("pronunciation:"):
            from .learning_content import get_learning_item
            rule = get_learning_item(identity)
            if rule:
                rules.append(rule)
            continue
        content_id, script = identity.removeprefix("kana:").rsplit(":", 1)
        item = app.repo.get_kana(content_id)
        if item and item.get(script):
            symbols.append(item[script])
    return symbols, rules


def _review_wait(app, K, step, on_action) -> None:
    """The page 本轮先歇一会儿, with its timer armed."""
    from .extra_practice_runtime import extra_button_rows
    from .kana_line.ui import line_pages
    from .new_dispatch import study_path_available
    _clear_attempt(app)
    app._review_wait_until = app_clock.localize(datetime.fromisoformat(step.until))
    due = display.wait_due_text(app._review_wait_until, app_clock.local_now().date())
    app._review_wait_checked = False
    status = display.wait_status(app._review_wait_until, app_clock.local_now(app._review_wait_until.tzinfo))
    model = display.review_wait_model(status, due, summarize_queue_availability(app.store, step.prepared).waiting_count)
    saved_row = app.note(model.saved)
    # Standalone course controllers have no route runtime.
    next_rows = (app._path_next_controls(*current_route_next(app.study_path_service()), compact=True)
                 if study_path_available(app) else [])
    page = line_pages.review_wait_page(model, saved_row=saved_row, next_rows=next_rows,
                                       extra_rows=extra_button_rows(app), on_action=on_action)
    app._review_wait_status = page.status
    app._review_wait_button = page.check
    app.screen(model.page, model.title, *page.children)
    # Start from ready work: a timer scheduled directly in an Android
    # touch callback does not wake Toga's idle Python event loop.
    app._review_wait_handle = app.loop.call_soon(K._tick_review_wait, app, app._page_token)


def perform_kana(app, K, step) -> None:
    """Make the calls ``step`` stands for."""
    if isinstance(step, Stopped):
        return
    if isinstance(step, Introduce):
        app.show_kana_introduction(step.question)
        return
    if isinstance(step, IntroWait):
        app.show_kana_intro_wait(step.due)
        return
    if isinstance(step, ResumeParked):
        # A parked word can become current after rating another question,
        # without reopening the app. Use the same guarded restore path so
        # rendering an empty input cannot overwrite its saved draft/feedback.
        app.resume_practice(preserve_audio=True)
        return
    if isinstance(step, Ask):
        K._ask_question(app, step.prepared)
        return

    def on_action(action, widget=None):
        return run_kana_action(app, action, widget)
    if isinstance(step, Finished):
        _finish(app, K, on_action)
        return
    if isinstance(step, ReviewWait):
        _review_wait(app, K, step, on_action)
        return
    from .kana_line.ui import line_pages
    if isinstance(step, ContentUpdated):
        from .session_content import RETIRE, retired
        old = next(q for q in kana_session.questions(app)[kana_session.question_index(app):] if retired(q))
        _clear_attempt(app)
        model = display.content_updated_model(kana_session.session_id(app), old.metadata[RETIRE],
                                              word_lane=_lane(app) == "word_romaji")
        app.screen(model.page, model.title,
                   *line_pages.content_updated_page(model, kept_row=app.note(model.kept), on_action=on_action))
        return
    if isinstance(step, KindsPaused):
        _clear_attempt(app)
        model = display.kinds_paused_model(word_lane=_lane(app) == "word_romaji")
        app.screen(model.page, model.title,
                   *line_pages.kinds_paused_page(model, kept_row=app.note(model.kept), on_action=on_action))
        return
    if isinstance(step, PrerequisiteMissing):
        _clear_attempt(app)
        symbols, rules = _prerequisites(app, step.blocked)
        model = display.prerequisite_model(symbols, rules)
        symbols_row = app.content_label(model.symbols, **line_pages.SYMBOLS_STYLE)
        app.screen(model.page, model.title,
                   *line_pages.prerequisite_page(model, symbols_row=symbols_row, on_action=on_action))
        return
    raise ValueError(f"unknown practice step {step!r}")
