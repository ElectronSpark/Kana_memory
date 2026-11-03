"""Compatibility API for separately packaged, non-executable teaching data."""
from copy import deepcopy
from .content_pack import load_namespace

_CONTENT = load_namespace('vocabulary_catalog', {})
WORD_LESSONS = _CONTENT.get('WORD_LESSONS', [])
