"""Exclude foreign-sound hiragana kana questions, including old queues.

This is a presentation/selection policy. Historical scores, cards and their
due dates remain intact. Foreign kana use katakana in both question directions.
"""
from collections.abc import Mapping
from unicodedata import normalize


FOREIGN_CATEGORIES = frozenset({'foreign_combination', 'foreign_voiced', 'foreign_variant'})
FOREIGN_KATAKANA_FORMS = frozenset(
    'シェ ジェ チェ ツァ ツェ ツォ ティ ディ ファ フィ フェ フォ デュ '
    'イェ ウィ ウェ ウォ クァ クィ クェ クォ グァ ツィ トゥ ドゥ '
    'ヴァ ヴィ ヴェ ヴォ テュ フュ ヴュ ヴ クヮ グヮ'.split()
)
_KANA_KINDS = frozenset({'kana_choice', 'kana_romaji', 'romaji_kana', 'romaji_kana_full',
                         'romaji_handwriting', 'kana_handwriting'})
_WORD_ANSWERS = frozenset({'word_kana', 'word_meaning_kana'})
_FILTERED_KINDS = _KANA_KINDS | _WORD_ANSWERS


def _katakana(value):
    if not isinstance(value, str):
        return ''
    return ''.join(chr(ord(char) + 0x60) if 'ぁ' <= char <= 'ゖ' else char
                   for char in normalize('NFC', value))


def _legacy_symbol(content_id):
    if not isinstance(content_id, str) or not content_id.startswith('kana-'):
        return ''
    try:
        return ''.join(chr(int(part, 16)) for part in content_id[5:].split('-'))
    except (ValueError, OverflowError):
        return ''


def direction_allowed(content_id, kind, script, *, category=None, symbol=None):
    if kind in _KANA_KINDS and (_katakana(symbol) == "ン" or _katakana(_legacy_symbol(content_id)) == "ン"):
        return False
    if script != 'hiragana' or kind not in _FILTERED_KINDS:
        return True
    form = _katakana(symbol)
    if kind in _WORD_ANSWERS:
        return not any(foreign in form for foreign in FOREIGN_KATAKANA_FORMS)
    return (category not in FOREIGN_CATEGORIES
            and form not in FOREIGN_KATAKANA_FORMS
            and _katakana(_legacy_symbol(content_id)) not in FOREIGN_KATAKANA_FORMS)


def question_allowed(question):
    get = question.get if isinstance(question, Mapping) else lambda key, default=None: getattr(question, key, default)
    metadata = get('metadata') or {}
    kind = get('kind')
    return direction_allowed(get('content_id'), kind, get('script'),
        category=metadata.get('kana_category'),
        symbol=(get('answer') or metadata.get('original_reading')) if kind in _WORD_ANSWERS
               else metadata.get('kana_symbol') or get('answer'))


def preserved_retired_question(question, state):
    """A legacy nasal draft/result survives retirement of untouched questions."""
    get = question.get if isinstance(question, Mapping) else lambda key, default=None: getattr(question, key, default)
    metadata = get('metadata') or {}
    retired = get('kind') in _KANA_KINDS and (_katakana(_legacy_symbol(get('content_id'))) == 'ン'
        or _katakana(metadata.get('kana_symbol') or get('answer')) == 'ン')
    if not retired:
        return False
    from .review_dispatch import kana_resume_priority
    return kana_resume_priority(state) is not None
