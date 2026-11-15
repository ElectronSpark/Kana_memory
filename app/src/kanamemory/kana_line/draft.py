"""The typed draft of the answer on a question page of the kana practice (design 13).

While a question is on the page the practice holds what the learner has typed so far: the romaji of a kana
question, the kana of a word, the romaji parts chosen for a word that is answered part by part (with their
plan and the mode), and the three values of the romaji converter of a word question. The saved checkpoint
carries the values of the question's kind, and a restore gives them back.

Pure: no Toga, no store, no app object.
"""
from __future__ import annotations

from dataclasses import dataclass

from .fields import UNSET, PartlySet


@dataclass(eq=False)
class KanaDraft(PartlySet):
    """What the learner has typed so far.

    The app side keeps one of these and REPLACES it whenever a field is assigned; it never edits one in
    place. The list of parts inside is the line's own list and is changed in place where the line always
    changed it. A question page kept for Back holds the fields that page shows (kept_fields).
    """

    romaji_answer: object = UNSET                # str: the romaji typed for a kana question
    word_answer: object = UNSET                  # str: the kana typed for a word
    word_romaji_parts: object = UNSET            # list: the romaji parts chosen so far, in their order
    word_romaji_plan: object = UNSET             # dict: the plan of the parts of a word answered part by part
    word_romaji_unit_mode: object = UNSET        # bool: the word on the page is answered part by part
    word_conversion_source: object = UNSET       # str: romaji typed into the converter and not yet taken over
    word_conversion_convention: object = UNSET   # "reading" | "keyboard": how the converter reads romaji
    word_used_conversion: object = UNSET         # bool: the converter showed kana for this question


# Every practice page kept for Back holds the converter's values; the two pages on which something is typed
# hold their own values beside them (a word answered part by part is drawn on the page "question").
CONVERTER_FIELDS = ("word_conversion_source", "word_conversion_convention", "word_used_conversion")
PAGE_FIELDS = {
    "question": ("romaji_answer", "word_romaji_parts", "word_romaji_plan", "word_romaji_unit_mode"),
    "word_question": ("word_answer",),
}


def kept_fields(page: str, *, practice_page: bool) -> tuple:
    """The fields of the draft a page kept for Back holds: what that page shows, and on a practice page the
    converter's values."""
    return PAGE_FIELDS.get(page, ()) + (CONVERTER_FIELDS if practice_page else ())
