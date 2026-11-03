"""Read visible answer content once, without producing learning evidence."""
from collections import OrderedDict
from .diagnostic_events import record_error


ANSWER_PAGES = frozenset({"feedback", "learning_feedback", "learning_guide", "ai_feedback", "retention_feedback", "diagnostic_feedback"})
MAX_RECENT_ANSWERS = 64


def autoplay_answer(app, text, key):
    """Queue safe answer text after rendering; callers select the correct text.

    The bounded cache lives only in this app process. Reopening an unfinished
    answer after restart can speak once; repainting or returning from details
    does not speak again. Pending work invalidated before playback is retryable.
    The existing manual speaker remains responsible for reporting voice errors.
    """
    page = getattr(app, "page", None)
    player = getattr(app.platform, "play", None)
    if page not in ANSWER_PAGES or not isinstance(text, str) or not text.strip() or not callable(player):
        return False
    token = app._page_token
    entries = getattr(app, "_answer_autoplay_entries", None)
    if not isinstance(entries, OrderedDict):
        entries = app._answer_autoplay_entries = OrderedDict()
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
                and entries.get(key) is entry)

    def discard_pending():
        if entries.get(key) is entry and entry["state"] == "pending":
            del entries[key]

    async def play():
        if not current():
            discard_pending()
            return
        entry["state"] = "played"
        try:
            await player(text.strip())
        except Exception as exc:
            record_error("answer_autoplay", "play", exc)
            # Navigation stops in-flight speech; an absent voice must likewise
            # never interrupt grading with an unsolicited dialog on each item.
            # Users can retry with the visible speaker to see the usual error.
            pass

    def start():
        if not current():
            discard_pending()
            return
        coroutine = play()
        try:
            app.loop.create_task(coroutine)
        except Exception as exc:
            record_error("answer_autoplay", "schedule", exc)
            coroutine.close()
            discard_pending()

    try:
        call_soon = getattr(app.loop, "call_soon", None)
        if callable(call_soon):
            call_soon(start)
        else:
            start()
    except Exception as exc:
        record_error("answer_autoplay", "schedule", exc)
        discard_pending()
        return False
    return True
