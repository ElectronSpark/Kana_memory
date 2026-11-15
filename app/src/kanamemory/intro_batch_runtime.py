"""The batch introduction (成批引入) on the app side: it asks the service, then draws or hands the line back.

Each function is one moment of a batch: show whatever the batch does next, a
card, the quiz, a card's choice, an answer, the end of the quiz, a later
ability, a pressed button. The service (course_line.batch) makes every
decision and every write and answers with a value; this module performs that
value: it sets what the pages keep, builds the handed-in rows with the app's
shared builders, calls the page function (course_line.ui.batch_pages) and
screen(), or hands the line back to the continue action.

What a card or quiz page keeps between its drawing and a press lives in one
BatchPage on the app, reached through batch_page(app). It is replaced, never
edited: a page kept for Back holds the object of its own drawing.

This module reads the app object and imports neither Toga nor the control
components nor the app module; every page method and line entry is looked up
on the app object at the moment of the call, and the page module is imported
at call time.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from .course_line import batch_display
from .course_line.batch import BatchIntroduction
from .course_line.batch_model import (BatchActionKind, BatchClosed, BatchOver, CardStep, CardUnavailable, QuizPaused,
                                      QuizSettled, QuizStale, QuizUnavailable)
from .new_dispatch import study_path_available


@dataclass(eq=False)
class BatchPage:
    """What the visible card or quiz page keeps.

    ``card_entry``: the batch entry of the card on the page; ``question``: the
    quiz question that can still be answered (None once it is answered or the
    quiz is paused or settled); ``result``: the quiz page's result column;
    ``buttons``: its choice buttons. Back restores the object a page was
    drawn with.
    """

    card_entry: dict | None = None
    question: dict | None = None
    result: object = None
    buttons: list = field(default_factory=list)


def batch_page(app) -> BatchPage:
    """What the batch's page keeps now (an empty BatchPage before the first drawing)."""
    return getattr(app, "_intro_batch_page", None) or BatchPage()


def _fair_new(app):
    from .new_dispatch import enabled
    return enabled(app)


def service(app) -> BatchIntroduction:
    """The batch introduction over this app's stores, built for one call (nothing is kept)."""
    return BatchIntroduction(app.learning_store, app.store,
                             enabled_kinds=lambda: app.learning_preferences()["enabled"],
                             fair_new=lambda: _fair_new(app),
                             route_available=lambda: study_path_available(app))


def _on_action(app):
    def on_action(action, widget=None):
        return run_batch_action(app, action, widget)
    return on_action


def hand_back(app):
    """The line goes on: the same continuation as finishing a single guide."""
    if study_path_available(app) and callable(getattr(app, "continue_study_path", None)):
        app.continue_study_path(_checkpoint_saved=True)
    else:
        app.continue_learning()


def show(app):
    """Whatever the batch does next: its card, its quiz, or the line when no batch is left."""
    step = service(app).advance()
    if isinstance(step, (BatchOver, BatchClosed)):
        hand_back(app)
    elif isinstance(step, CardStep):
        show_card(app, step.entry)
    else:
        app.show_intro_batch_quiz()


def show_card(app, entry):
    """The card of ``entry``: its exposure is recorded before it is drawn; an unavailable card writes nothing."""
    from .course_line.ui import batch_pages
    opened = service(app).open_card(entry)
    if isinstance(opened, CardUnavailable):
        model = batch_display.card_unavailable_model()
        app.screen(model.page, model.title, *batch_pages.card_unavailable_page(model, on_action=_on_action(app)))
        return
    app._learning_item = opened.item
    app._intro_batch_page = replace(batch_page(app), card_entry=opened.entry)
    hint_rows = app.intro_batch_review_hint()
    body_rows = app._intro_card_body(opened.item, origin=True)
    model = batch_display.card_model(opened.index, opened.total, dict(opened.entry))
    app.screen(model.page, model.title,
               *batch_pages.card_page(model, hint_rows=hint_rows, body_rows=body_rows, on_action=_on_action(app)))


def show_quiz(app):
    """The quiz, rebuilt from the saved state: the head's frozen choice question, the settled page, the paused
    page for a closed kind, or the line when no batch is open."""
    from .course_line.ui import batch_pages
    asked = service(app).open_quiz()
    if asked is None:
        hand_back(app)
        return
    if isinstance(asked, QuizUnavailable):
        app.alert(asked.message)
        model = batch_display.quiz_unavailable_model()
        app.screen(model.page, model.title, *batch_pages.quiz_unavailable_page(model, on_action=_on_action(app)))
        return
    app._learning_item = asked.item
    if isinstance(asked, QuizSettled):
        model = batch_display.quiz_settled_model()
        view = batch_pages.quiz_settled_page(model, on_action=_on_action(app))
        app._intro_batch_page = replace(batch_page(app), question=None, result=view.result, buttons=view.buttons)
        app.screen(model.page, model.title, *view.children)
        return
    if isinstance(asked, QuizPaused):
        # Never skip or answer a closed kind: the same frozen head resumes.
        app._intro_batch_page = replace(batch_page(app), question=None)
        from .course_line.model import PAUSED_QUIZ, Paused
        from .course_runtime import perform_course
        perform_course(app, Paused(PAUSED_QUIZ, batch_display.QUIZ_PAUSED))
        return
    hint_rows = app.intro_batch_review_hint()
    prompt = app._learning_prompt_block(asked.question, size=20, known_ids=asked.known)
    model = batch_display.quiz_model(asked.question, asked.labels, asked.role, asked.known)
    view = batch_pages.quiz_page(model, hint_rows=hint_rows, prompt=prompt, on_action=_on_action(app))
    app._intro_batch_page = replace(batch_page(app), question=asked.question, result=view.result, buttons=view.buttons)
    app.screen(model.page, model.title, *view.children)


def choose_card(app, choice, expected):
    """A press of 下一项 ("next") or 我已经会了 ("known") on the card whose entry is ``expected``; a press from
    another page or for another card does nothing."""
    if app.page != batch_display.CARD_PAGE or batch_page(app).card_entry != expected:
        return
    service(app).choose_card(expected, choice)
    app.show_intro_batch()


def answer(app, question, answer):
    """A press of a choice of the quiz question ``question``; it counts only while that very question object
    is the one on the page (not after an answer, and not from an earlier drawing)."""
    if app.page != batch_display.QUIZ_PAGE or batch_page(app).question is not question:
        return
    batch = service(app)
    result = batch.answer(question, answer)
    if isinstance(result, QuizPaused):
        app.show_intro_batch_quiz()
        return
    if result is None:
        return
    if isinstance(result, QuizStale):
        app.show_intro_batch()
        return
    from .course_line.ui import batch_pages
    page = batch_page(app)
    app._intro_batch_page = replace(page, question=None)
    card_rows = [] if result.correct else app._intro_card_body(app._learning_item, origin=True)
    model = batch_display.quiz_result_model(question, correct=result.correct, done=batch.quiz_done())
    batch_pages.show_quiz_result(page.result, page.buttons, model, card_rows=card_rows, on_action=_on_action(app))


def finish(app):
    """继续 after the quiz's last answer or on its settled page: complete the batch, then the line goes on."""
    if app.page != batch_display.QUIZ_PAGE:
        return
    service(app).finish()
    hand_back(app)


def open_ability(app, item):
    """A later ability of a batch item (四之二 item 4): the store opens it and the line goes on, or the page
    新能力暂不可用 when the store refuses."""
    if service(app).open_ability(item):
        hand_back(app)
        return
    from .course_line.ui import batch_pages
    next_content = app.learning_next_content_controls()
    model = batch_display.ability_unavailable_model()
    app.screen(model.page, model.title,
               *batch_pages.ability_unavailable_page(model, next_content_rows=next_content, on_action=_on_action(app)))


def run_batch_action(app, action, widget=None):
    """Make the call a button of the batch introduction's pages stands for (BatchAction's docstring)."""
    kind = action.kind
    if kind is BatchActionKind.CARD_NEXT:
        return app.choose_intro_card("next", expected=action.entry)
    if kind is BatchActionKind.CARD_KNOWN:
        return app.choose_intro_card("known", expected=action.entry)
    if kind is BatchActionKind.LEAVE:
        return app.leave_learning(widget)
    if kind is BatchActionKind.LEARNING_SETTINGS:
        return app.show_learning_settings(widget)
    if kind is BatchActionKind.QUIZ_ANSWER:
        return answer(app, action.question, action.answer)
    if kind is BatchActionKind.QUIZ_CONTINUE:
        return app.show_intro_batch(widget)
    if kind is BatchActionKind.QUIZ_FINISH:
        return finish(app)
    raise ValueError(f"unknown batch action {action!r}")
