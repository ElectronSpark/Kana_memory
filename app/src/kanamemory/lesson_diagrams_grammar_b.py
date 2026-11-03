"""Compatibility API for separately packaged, non-executable teaching data."""
from copy import deepcopy
from .content_pack import load_namespace

_CONTENT = load_namespace('lesson_diagrams_grammar_b', {})
REVIEW_SOURCES = _CONTENT.get('REVIEW_SOURCES', ())
DIAGRAMS = _CONTENT.get('DIAGRAMS', {})
TEXT_ONLY = _CONTENT.get('TEXT_ONLY', {})
_STUDY_NODE_TEXTS = _CONTENT.get('_STUDY_NODE_TEXTS', set())
