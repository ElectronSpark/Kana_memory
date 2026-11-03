"""Compatibility API for separately packaged, non-executable teaching data."""
from copy import deepcopy
from .content_pack import load_namespace

_CONTENT = load_namespace('grammar_curriculum', {})
REVISION = _CONTENT.get('REVISION', '')
TUFS = _CONTENT.get('TUFS', '')
JF = _CONTENT.get('JF', '')
SOURCES = _CONTENT.get('SOURCES', {})
_SOURCE_FOR = _CONTENT.get('_SOURCE_FOR', {})
_SPECIFIC_SOURCE = _CONTENT.get('_SPECIFIC_SOURCE', {})
_ITEMS = _CONTENT.get('_ITEMS', [])
LEGACY_GRAMMAR_METADATA = _CONTENT.get('LEGACY_GRAMMAR_METADATA', {})
SOURCE_RECORDS = _CONTENT.get('SOURCE_RECORDS', [])

def grammar_items():
    return deepcopy(_ITEMS)

def grammar_sources():
    return deepcopy(SOURCE_RECORDS)
