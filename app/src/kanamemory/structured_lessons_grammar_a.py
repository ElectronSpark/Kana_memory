"""Compatibility API for separately packaged, non-executable teaching data."""
from copy import deepcopy
from .content_pack import load_namespace

_CONTENT = load_namespace('structured_lessons_grammar_a', {})
LESSONS = _CONTENT.get('LESSONS', {})
