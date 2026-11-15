"""The course line (课程学习) on the app side: it decides the next course step, then performs it.

decide_course prepares the round and the queue (begin, the heard-word
admission, the prerequisite rows, the missing questions), checks the head the
continue action handed in, reads the learner's next head, exchanges withdrawn
task forms, seals the round, remembers the head on the app and returns one
course step (course_line.model) without drawing anything. perform_course makes
the one call that step stands for: one of the line's own six pages
(course_line.ui.line_pages), the batch introduction, the diagnostic line, a
teaching step or a question; the pages' buttons hand their CourseAction to
run_course_action, which makes the call when the button is pressed.
continue_learning calls both inside its own body, so the continue action's
read context and continue_learning's navigation window stay open around both.

This module reads the app object and imports neither Toga nor the control
components nor the app module: every app method is looked up on the app
object at the moment of the call. Module globals that tests reach through
tests/support/course_support.py: decide_course. Imports made at call time
stay at call time, so their own patch targets keep working.
"""
from __future__ import annotations

from .course_line import display
from .course_line.model import (PAUSED_DISABLED, PAUSED_NO_HEAD, Ask, Batch, CourseActionKind, Diagnostic, Done,
                                LibraryOff, MaterialBlocked, Paused, Prerequisite, Teach, Wait)
from .study_path_runtime import skipped_teaching_ids


def decide_course(app, *, restart_finished, selected_current=None, projection=None):
    """Prepare the round and the queue and settle on the learner's next course step; returns it (a CourseStep).

    ``restart_finished``: a press restarts a finished round. ``selected_current``: the head the continue
    action protected or promoted (checked again here). ``projection``: the continue action's read context
    (reused only while nothing was written; None: read live). Draws nothing and arms no timer; the round's
    writes, the remembered head (app._learning_current) and the batch route's opening are made here.
    """
    from .practice_round import begin, seal, mark_finished, sealed
    begin(app.store, restart_finished=restart_finished)
    # Sealed before this action: the queueing below adds nothing.
    sealed_before = sealed(app.store)
    enabled = app.learning_preferences()["enabled"]
    from .learning_questions import AUDIO_WORD_KIND
    if AUDIO_WORD_KIND in enabled:
        app.learning_store.admit_word_listening(enabled_kinds=enabled)
    app.learning_store.ensure_prerequisites()
    # A sealed round queues nothing (_queue_learning_questions returns at once), so the
    # read of the lessons that lack a question is skipped on every later page of the round.
    if not sealed_before:
        app._queue_missing_learning_questions()
    from .new_dispatch import enabled as fair_enabled
    selected = selected_current
    if selected and selected.get('question'):
        fresh = app.learning_store.get_question(selected['question']['id'])
        if not fresh or fresh.get('status') == 'superseded':
            selected = None
    selected_id = (selected or {}).get("content", {}).get("id")
    if (selected_id and selected.get('question') and not selected['question'].get('result')
            and app.learning_store.content_question_blocked(selected['content'], selected['question'])):
        selected = None; selected_id = None
    if selected_id and app.learning_store.prerequisite_status(selected_id, selected["content"].get("instruction_scope"))["blocked"]:
        selected = None
    if selected and selected.get("type") == "instruction":
        checkpoint = selected.get("content", {}).get("checkpoint", {})
        frozen_guide = checkpoint.get("guide_question", {})
        if frozen_guide.get("practice") and frozen_guide.get("kind") not in enabled:
            selected = None
    # projection: continue_study_path's read context for this same action
    # (reused only while nothing was written); standalone calls read live.
    shared = {} if projection is None else {"projection": projection}
    skipped = skipped_teaching_ids(app)
    if skipped and selected and selected.get("type") == "instruction" and selected_id in skipped:
        selected = None
    current = selected or app.learning_store.current(enabled, fair_new=fair_enabled(app),
                                                     **({"skip_teaching_ids": skipped} if skipped else {}), **shared)
    # A withdrawn task form is never shown while a current task can take its place: one the queue pass
    # has not reached (its time budget, a round sealed earlier) is exchanged here. One that cannot be
    # exchanged is shown as it was stored.
    from .learning_questions import withdrawn_task
    for _ in range(8):
        if not (current and current.get("type") == "question" and withdrawn_task(current.get("question"))
                and app._replace_withdrawn_questions(current["question"]["id"])):
            break
        projection, shared = None, {}  # the exchange wrote: the shared read context is no longer current
        current = app.learning_store.current(enabled, fair_new=fair_enabled(app),
                                             **({"skip_teaching_ids": skipped} if skipped else {}))
    seal(app.store)
    app._learning_current = current
    if current and current['type'] == 'content_blocked' and current.get('reason') == 'library_disabled':
        return LibraryOff(current.get('counts', {}).get('library_unavailable', 0))
    if current and current['type'] == 'content_blocked':
        return MaterialBlocked(current['question_ids'])
    # 四之二: an open batch resumes from its saved cursor; an untouched
    # original word/grammar head opens a new batch instead of the picker.
    if app.route_intro_batch(current, enabled):
        return Batch()
    # N12: course work a sealed round could not queue is not "题型已关闭";
    # end the round like an empty course lane (the next round queues it).
    left_out = getattr(app, "_sealed_round_left_out", None)
    if (sealed_before and current and current.get("type") == "disabled" and callable(left_out)
            and left_out(current, projection)):
        current = app._learning_current = None
    elif not current and app.learning_store.counts(enabled, **shared)["disabled"]:
        return Paused(PAUSED_NO_HEAD, display.PAUSED_NO_HEAD_TEXT)
    if not current or current.get("type") in {"empty", "done"}:
        mark_finished(app.store)
        return Done()
    if current["type"] == "disabled":
        return Paused(PAUSED_DISABLED, display.PAUSED_DISABLED_TEXT)
    if current['type'] == 'diagnostic':
        return Diagnostic(current['episode_id'])
    if current["type"] == "waiting":
        return Wait(current.get("due_at_utc"))
    if current["type"] == "prerequisite":
        # Design 12 Q3 (breadth first): a prerequisite already taught is met, not waiting.
        from .learning_dependencies import TAUGHT
        taught = tuple(entry["title"] for entry in (current.get("dependency_status") or {}).get("dependencies", ())
                       if entry.get("status") == TAUGHT)
        return Prerequisite(current["content"], current.get("due_at_utc"), taught)
    if current["type"] == "instruction":
        return Teach(current["content"])
    if current["type"] == "question":
        return Ask(current)
    raise ValueError("Unsupported learning dispatch state")


def run_course_action(app, action, widget=None):
    """Make the call a button of the course line's own pages stands for (CourseAction's docstring)."""
    kind = action.kind
    if kind is CourseActionKind.CHECK_WAIT:
        return app.check_learning_wait(widget)
    if kind is CourseActionKind.NEW_ROUND:
        return app.continue_learning(widget)
    if kind is CourseActionKind.ACKNOWLEDGE_PREREQUISITES:
        app.learning_store.acknowledge_prerequisites(action.content_id, instruction_scope=action.instruction_scope)
        app.continue_added_learning()
        return None
    if kind is CourseActionKind.CONTENT_LIBRARIES:
        return app.show_content_libraries(widget)
    if kind is CourseActionKind.LEARNING_SETTINGS:
        return app.show_learning_settings(widget)
    if kind is CourseActionKind.DEVICE_SETTINGS:
        return app.show_settings(widget)
    if kind is CourseActionKind.HOME:
        return app.leave_learning(widget)
    raise ValueError(f"unknown course page action {action!r}")


def perform_course(app, step):
    """Make the one call ``step`` stands for. None, except for the diagnostic line: its own return value."""
    if isinstance(step, Batch):
        app.show_intro_batch()
        return None
    if isinstance(step, Diagnostic):
        return app.continue_diagnostic(step.episode_id)
    if isinstance(step, Teach):
        app.show_learning_instruction(step.content)
        return None
    if isinstance(step, Ask):
        app.show_learning_question(step.current)
        return None
    from .course_line.ui import line_pages

    def on_action(action, widget=None):
        return run_course_action(app, action, widget)
    if isinstance(step, LibraryOff):
        libraries = callable(getattr(app, "show_content_libraries", None))
        next_content = app.learning_next_content_controls()
        model = display.library_off_model(step.count, libraries=libraries)
        app.screen(model.page, model.title,
                   *line_pages.library_off_page(model, next_content_rows=next_content, on_action=on_action))
        return None
    if isinstance(step, MaterialBlocked):
        replacement = app._learning_replacement_controls(step.question_ids)
        next_content = app.learning_next_content_controls()
        model = display.material_blocked_model()
        app.screen(model.page, model.title,
                   *line_pages.material_blocked_page(model, replacement_rows=replacement,
                                                     next_content_rows=next_content, on_action=on_action))
        return None
    if isinstance(step, Paused):
        # Name the missing voice when the learner kept the heard-word kind on;
        # telling them to switch on a kind that is already on leaves them stuck.
        next_content = app.learning_next_content_controls()
        from .learning_questions import AUDIO_WORD_KIND
        voice = AUDIO_WORD_KIND in app._saved_learning_kinds() and AUDIO_WORD_KIND not in app.learning_preferences()["enabled"]
        model = display.paused_model(step.text, voice=voice)
        app.screen(model.page, model.title,
                   *line_pages.paused_page(model, next_content_rows=next_content, on_action=on_action))
        return None
    if isinstance(step, Done):
        next_content = app.learning_next_content_controls()
        model = display.done_model()
        app.screen(model.page, model.title,
                   *line_pages.done_page(model, next_content_rows=next_content, on_action=on_action))
        return None
    if isinstance(step, Wait):
        when = app._learning_due_label(step.due) if step.due else None
        from .extra_practice_runtime import extra_button_rows
        next_content = app.learning_next_content_controls()
        extra = extra_button_rows(app)
        model = display.wait_model(when)
        app.screen(model.page, model.title,
                   *line_pages.wait_page(model, next_content_rows=next_content, extra_rows=extra,
                                         on_action=on_action))
        app.arm_learning_wait(step.due)
        return None
    if isinstance(step, Prerequisite):
        due_label = app._learning_due_label(step.due) if step.due else None
        model = display.prerequisite_model(step.content, due_label, step.taught)
        next_content = app.learning_next_content_controls()
        app.screen(model.page, model.title,
                   *line_pages.prerequisite_page(model, next_content_rows=next_content, on_action=on_action))
        app.arm_learning_wait(step.due)
        return None
    raise TypeError(f"unknown course step {step!r}")
