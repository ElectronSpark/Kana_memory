"""Compatibility API for separately packaged, non-executable teaching data."""
from copy import deepcopy
from .content_pack import load_namespace

_CONTENT = load_namespace('example_expansion_grammar_b', {})
_DATA = _CONTENT.get('_DATA', '')
ROWS = _CONTENT.get('ROWS', {})
