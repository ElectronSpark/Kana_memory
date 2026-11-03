"""Compatibility API for separately packaged, non-executable teaching data."""
from copy import deepcopy
from .content_pack import load_namespace

_CONTENT = load_namespace('example_expansion_words', {})
TARGETS = _CONTENT.get('TARGETS', {})
_SENTENCES = _CONTENT.get('_SENTENCES', '')
_SURFACE_HINTS = _CONTENT.get('_SURFACE_HINTS', {})
ROWS = _CONTENT.get('ROWS', {})
