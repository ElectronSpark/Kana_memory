"""Compatibility API for separately packaged, non-executable teaching data."""
from copy import deepcopy
from .content_pack import load_namespace

_CONTENT = load_namespace('structured_lessons_words', {})
_IDS = _CONTENT.get('_IDS', {})
_DATA = _CONTENT.get('_DATA', '')
LESSONS = _CONTENT.get('LESSONS', {})
_COMPARISONS = _CONTENT.get('_COMPARISONS', {})
