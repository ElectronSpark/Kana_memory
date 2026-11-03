"""Compatibility API for separately packaged, non-executable teaching data."""
from copy import deepcopy
from .content_pack import load_namespace

_CONTENT = load_namespace('example_expansion_grammar_a_tail', {})
ROWS = _CONTENT.get('ROWS', {})
