"""Compatibility API for separately packaged, non-executable teaching data."""
from copy import deepcopy
from .content_pack import load_namespace

_CONTENT = load_namespace('pronunciation_curriculum', {})
REVISION = _CONTENT.get('REVISION', '')
TRACK = _CONTENT.get('TRACK', '')
TUFS = _CONTENT.get('TUFS', '')
SOURCES = _CONTENT.get('SOURCES', {})
_ITEMS = _CONTENT.get('_ITEMS', [])
_PITCH_EXAMPLES = _CONTENT.get('_PITCH_EXAMPLES', {})
_AUDIO_EXAMPLES = _CONTENT.get('_AUDIO_EXAMPLES', {})
SOURCE_RECORDS = _CONTENT.get('SOURCE_RECORDS', [])

def pronunciation_items():
    return deepcopy(_ITEMS)

def pronunciation_sources():
    return deepcopy(SOURCE_RECORDS)
