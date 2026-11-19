"""Start a sound once for what is shown, without producing learning evidence.

Two users share one bookkeeping: the answer of a result page (autoplay_answer)
and the prompt of a question that must be heard (autoplay_prompt). Each keeps
its own bounded memory, so an answer and its question's prompt never collide.
"""
from collections import OrderedDict
from .diagnostic_events import record_error


ANSWER_PAGES = frozenset({"feedback", "learning_feedback", "learning_guide", "ai_feedback", "retention_feedback", "diagnostic_feedback"})
# Question pages whose prompt can be a sound (the page decides when it is).
PROMPT_PAGES = frozenset({"word_question", "ink_question", "learning_question", "tutor_question", "diagnostic_question"})
MAX_RECENT_ANSWERS = 64


def autoplay_answer(app, text, key):
    """Queue safe answer text after rendering; callers select the correct text.

    The bounded cache lives only in this app process. Reopening an unfinished
    answer after restart can speak once; repainting or returning from details
    does not speak again. Pending work invalidated before playback is retryable.
    The existing manual speaker remains responsible for reporting voice errors.
    """
    player = getattr(app.platform, "play", None)
    if (getattr(app, "page", None) not in ANSWER_PAGES or not isinstance(text, str) or not text.strip()
            or not callable(player)):
        return False
    spoken = text.strip()
    return _start_once(app, "_answer_autoplay_entries", key, lambda: player(spoken), "answer_autoplay")


def autoplay_prompt(app, key, play, *, heard):
    """Start a must-hear prompt once per showing, through the page's own play path.

    ``play`` is an async callable taking no arguments: the page's manual play
    path in its quiet form, so success records exactly what the button records
    (audio_ok) and failure opens no dialog. ``heard()`` is true once the prompt
    was played successfully; nothing starts then. A repaint of the same question
    does not start it again; an attempt the learner left (page changed before
    it began or while it played) is forgotten, so the next showing plays.
    """
    if (getattr(app, "page", None) not in PROMPT_PAGES or not callable(getattr(app.platform, "play", None))
            or not callable(play) or heard()):
        return False
    return _start_once(app, "_prompt_autoplay_entries", key, play, "prompt_autoplay",
                       wanted=lambda: not heard(), forget_when_left=True)


def _start_once(app, memory, key, play, operation, *, wanted=None, forget_when_left=False):
    page = app.page
    token = app._page_token
    entries = getattr(app, memory, None)
    if not isinstance(entries, OrderedDict):
        entries = OrderedDict()
        setattr(app, memory, entries)
    previous = entries.get(key)
    if previous is not None and (previous["state"] == "played" or
            previous["token"] == token and previous["serial"] == getattr(app, "_audio_serial", 0)):
        entries.move_to_end(key)
        return False
    app._audio_serial = getattr(app, "_audio_serial", 0) + 1
    serial = app._audio_serial
    entry = {"state": "pending", "token": token, "serial": serial}
    entries[key] = entry
    entries.move_to_end(key)
    while len(entries) > MAX_RECENT_ANSWERS:
        entries.popitem(last=False)

    def current():
        return (app.page == page and app._page_token == token and app._audio_serial == serial
                and entries.get(key) is entry and (wanted is None or wanted()))

    def discard_pending():
        if entries.get(key) is entry and entry["state"] == "pending":
            del entries[key]

    async def run():
        if not current():
            discard_pending()
            return
        entry["state"] = "played"
        try:
            await play()
        except Exception as exc:
            record_error(operation, "play", exc)
            # Navigation stops in-flight speech; an absent voice must likewise
            # never interrupt grading with an unsolicited dialog on each item.
            # Users can retry with the visible speaker to see the usual error.
        if forget_when_left and app.page != page and entries.get(key) is entry:
            # Left while it played: not heard here, so showing it again plays.
            del entries[key]

    def start():
        if not current():
            discard_pending()
            return
        coroutine = run()
        try:
            app.loop.create_task(coroutine)
        except Exception as exc:
            record_error(operation, "schedule", exc)
            coroutine.close()
            discard_pending()

    try:
        call_soon = getattr(app.loop, "call_soon", None)
        if callable(call_soon):
            call_soon(start)
        else:
            start()
    except Exception as exc:
        record_error(operation, "schedule", exc)
        discard_pending()
        return False
    return True
