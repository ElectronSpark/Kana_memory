"""Exact-public-text corrections for observed analyzer misreadings.

These are build-time annotations, never edits to example IDs or user data.
Every supplied numeric accent is present with that exact surface and reading in
the bundled Kanjium dictionary. Reading-only corrections carry separate sources.
Sentence context selects the appropriate entry; ambiguous person names stay null.
No correction applies to an unseen sentence or to an inflected verb stem.
"""
from __future__ import annotations


from kanamemory.content_pack import load_namespace

_DATA = load_namespace("sentence_annotation_overrides", {})
EXACT_OVERRIDES = _DATA.get("EXACT_OVERRIDES", {})
EXACT_EXCLUSIONS = _DATA.get("EXACT_EXCLUSIONS", {})
