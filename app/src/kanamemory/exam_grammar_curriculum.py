"""Compatibility API for separately packaged, non-executable teaching data."""
from copy import deepcopy
from .content_pack import load_namespace

_CONTENT = load_namespace('exam_grammar_curriculum', {})
REVISION = _CONTENT.get('REVISION', '')
SOURCES = _CONTENT.get('SOURCES', {})
_ITEMS = _CONTENT.get('_ITEMS', [])
_REORDER = _CONTENT.get('_REORDER', {})
SOURCE_RECORDS = _CONTENT.get('SOURCE_RECORDS', [])

def grammar_items():
    return deepcopy(_ITEMS)

def grammar_sources():
    return deepcopy(SOURCE_RECORDS)
