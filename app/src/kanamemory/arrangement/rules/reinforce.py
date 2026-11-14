"""Which kana queue answers the credit rule judges (design 12 E3, E4, K11).

Pure. A "多轮强化" (reinforce) batch is free practice: each of its answers is
judged by rules/credit at the instant of the answer, in every round. A due
state, a new one or one whose learning or relearning step has come due is
rated as before; a state that is not due and was formally rated less than 24
hours ago is practice only. The queue serves every round and resumes as
before; the batch builder and the stored batch are unchanged.

Everything else in a kana queue is scheduled work and is rated exactly as
before, without asking anything: a spaced queue (every question the study
path's main lane builds is spaced), a spelling correction (rated "assisted,
1" by its own policy) and a word skill (no scheduling state of its own).
"""
from __future__ import annotations

from collections.abc import Mapping

from ...practice_preferences import WORD_PRACTICE_KINDS

REINFORCE = "reinforce"   # practice_config.schedule_mode of a 多轮强化 batch (the default when absent)


def _field(question: object, name: str) -> object:
    return question.get(name) if isinstance(question, Mapping) else getattr(question, name, None)


def credit_applies(question: object) -> bool:
    """True when the answer to this kana question (a domain.Question or its dict) is judged by rules/credit.

    That is a question of a reinforce batch (practice_config.schedule_mode
    "reinforce", or absent: the existing convention) that is not a word skill
    and not a spelling correction (metadata spelling_correction_of).
    """
    metadata = _field(question, "metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    config = metadata.get("practice_config")
    mode = config.get("schedule_mode", REINFORCE) if isinstance(config, Mapping) else REINFORCE
    return (mode == REINFORCE and _field(question, "kind") not in WORD_PRACTICE_KINDS
            and not metadata.get("spelling_correction_of"))
