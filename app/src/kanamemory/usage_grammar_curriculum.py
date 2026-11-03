"""Compatibility API for separately packaged, non-executable teaching data."""
from copy import deepcopy
from .content_pack import load_namespace

_CONTENT = load_namespace('usage_grammar_curriculum', {})
REVISION = _CONTENT.get('REVISION', '')
TRACK = _CONTENT.get('TRACK', '')
_SOURCES = _CONTENT.get('_SOURCES', {})
_ITEMS = _CONTENT.get('_ITEMS', [])
SOURCE_RECORDS = _CONTENT.get('SOURCE_RECORDS', [])

def grammar_items():
    return deepcopy(_ITEMS)

def grammar_sources():
    return deepcopy(SOURCE_RECORDS)
