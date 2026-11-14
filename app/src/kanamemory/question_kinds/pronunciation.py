"""Pronunciation kinds: reading rules, told apart or applied.

Pure rules: no Toga, database, platform, network or content loading. Both
kinds exist only for pronunciation lessons with reviewed templates (there is
no legacy builder or guessed distractor). This module owns their snapshot
rule: every such task concerns a reading phenomenon, so TTS and optional ruby
could reveal its answer and generic voice output cannot certify pitch; the
snapshot drops audio and carries explicit prompt/answer text roles. Pitch and
contour shapes are built and graded by pitch_practice and prosody_cue, and
other answers by the generic local grader.
"""
from __future__ import annotations

from collections.abc import Mapping, MutableMapping

RULE_KIND = "pronunciation_rule"
APPLICATION_KIND = "pronunciation_application"
KINDS = (RULE_KIND, APPLICATION_KIND)
DOMAIN = "pronunciation"


def finish(result: MutableMapping, raw: Mapping, target: Mapping) -> None:
    """snapshot_finisher: reading-test fields; templates must name text roles."""
    result.update(domain=DOMAIN, tests_reading=True,
                  audio_text="", optional_audio_text="", requires_audio=False)
    roles = raw.get("text_roles", {})
    if not isinstance(roles, dict) or any(roles.get(field) not in {"ui", "study"}
                                           for field in ("prompt", "answer")):
        raise ValueError("Pronunciation templates require explicit prompt and answer text roles")
    result["text_roles"] = {field: roles[field] for field in ("prompt", "answer")}
    if target.get("question_template_revision"):
        result["question_template_revision"] = target["question_template_revision"]
