"""The typed draft of the kana practice on the app side (design 13).

What the learner has typed so far for the question on the page is one KanaDraft on the app, kept as the
attribute ``_kana_typed`` (``_kana_draft`` is the app's debouncer of the keystroke checkpoint). The practice
line reaches it through this module; Back's restore (page_restore) and a problem report (feedback_context)
name the attribute itself. It is replaced, never edited, when a field of it is assigned (assign, set_draft and
the set_* functions); the list of romaji parts inside is the line's own list. A question page kept for Back
holds the fields of the draft that page shows (keep), and Back gives those back (page_restore).

A field that was never set since the app started is UNSET: the readers below raise AttributeError for it, as
the attribute access they replace did, and draft(app).get(name, default) gives a default, as getattr with a
default did.

This module reads the app object and imports neither Toga nor the control components nor the app module.
"""
from __future__ import annotations

from dataclasses import replace

from .kana_line.draft import KanaDraft, kept_fields

BINDING = "_kana_typed"


def draft(app) -> KanaDraft:
    """The draft the app holds (one with nothing set before anything was typed)."""
    value = getattr(app, BINDING, None)
    return value if isinstance(value, KanaDraft) else KanaDraft()


def set_draft(app, value: KanaDraft) -> None:
    app._kana_typed = value


def assign(app, **fields) -> None:
    """Assign fields of the draft: the app gets a NEW draft object with them (the one before is left as it
    was, for whatever page still holds it)."""
    app._kana_typed = replace(draft(app), **fields)


def keep(app, bindings: dict, page: str, *, practice_page: bool) -> None:
    """Put into ``bindings`` what the page being kept for Back holds of the draft: the fields that page shows,
    as far as they were set. A page that holds none of them keeps nothing of the draft."""
    kept = draft(app).only(kept_fields(page, practice_page=practice_page))
    if kept.set_fields():
        bindings[BINDING] = kept


def _read(app, name: str):
    return draft(app).need(name)


def romaji_answer(app):
    """The romaji typed so far for a kana question."""
    return _read(app, "romaji_answer")


def set_romaji_answer(app, value) -> None:
    assign(app, romaji_answer=value)


def word_answer(app):
    """The kana typed so far for a word."""
    return _read(app, "word_answer")


def set_word_answer(app, value) -> None:
    assign(app, word_answer=value)


def word_romaji_parts(app):
    """The romaji parts chosen so far for a word answered part by part; the line's own list."""
    return _read(app, "word_romaji_parts")


def set_word_romaji_parts(app, value) -> None:
    assign(app, word_romaji_parts=value)


def word_romaji_plan(app):
    """The plan of the parts of a word answered part by part."""
    return _read(app, "word_romaji_plan")


def set_word_romaji_plan(app, value) -> None:
    assign(app, word_romaji_plan=value)


def word_romaji_unit_mode(app):
    """Whether the word on the page is answered part by part."""
    return _read(app, "word_romaji_unit_mode")


def set_word_romaji_unit_mode(app, value) -> None:
    assign(app, word_romaji_unit_mode=value)


def word_conversion_source(app):
    """The romaji typed into the converter of a word question and not yet taken into the answer."""
    return _read(app, "word_conversion_source")


def set_word_conversion_source(app, value) -> None:
    assign(app, word_conversion_source=value)


def word_conversion_convention(app):
    """How the converter reads romaji: by reading or by keyboard."""
    return _read(app, "word_conversion_convention")


def set_word_conversion_convention(app, value) -> None:
    assign(app, word_conversion_convention=value)


def word_used_conversion(app):
    """Whether the converter showed kana for the question on the page."""
    return _read(app, "word_used_conversion")


def set_word_used_conversion(app, value) -> None:
    assign(app, word_used_conversion=value)
