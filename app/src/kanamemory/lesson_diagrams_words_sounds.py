"""Compatibility API for separately packaged, non-executable teaching data."""
from copy import deepcopy
from .content_pack import load_namespace

_CONTENT = load_namespace('lesson_diagrams_words_sounds', {})
WORD_IDS = _CONTENT.get('WORD_IDS', {})
DIAGRAMS = _CONTENT.get('DIAGRAMS', {})
TEXT_ONLY = _CONTENT.get('TEXT_ONLY', {})
_JAPANESE_NODE_TEXT = _CONTENT.get('_JAPANESE_NODE_TEXT', frozenset())
