"""The sentence units of a question: what "the same sentence" means when a lesson's questions are compared.

One definition for the app and the content checks (ONE SENTENCE PER POINT): a correction, a reorder and a
controlled production are compared by their answer (the target sentence); any other question by its prompt,
with its first gap filled with the answer when it has one. Each line is taken alone, a leading speaker label
is dropped, the line is split into sentences, and each sentence is compared without punctuation, brackets,
spaces and bracketed [...] notes. A part shorter than MIN_LEN characters is no unit.
Pure: no store, no content and no clock are read.
"""
from __future__ import annotations

import re

MIN_LEN = 6
ANSWER_KINDS = frozenset({"targeted_correction", "grammar_reorder", "controlled_sentence_production"})
_GAP = re.compile(r"（\s*）|\(\s*\)")
_LABEL = re.compile(r"^[^：:\s]{1,4}[：:]")
_SPLIT = re.compile(r"[。！？!?]")
_DROP = re.compile(r"[\s　、。，．,.!！?？「」『』（）()【】…・〜~―ー—\-：:；;\"'“”‘’]|\[[^\]]*\]")


def without_speaker_labels(text) -> str:
    """text line after line, each line stripped and without its leading speaker label (A：, 店员：)."""
    return "\n".join(_LABEL.sub("", line.strip()) for line in str(text).split("\n"))


def question_sentences(kind, question) -> frozenset[str]:
    """The sentence units of ``question`` (a template or snapshot with prompt and answer) of ``kind``."""
    prompt, answer = str(question.get("prompt") or ""), str(question.get("answer") or "")
    if kind in ANSWER_KINDS:
        text = answer
    elif _GAP.search(prompt):
        text = _GAP.sub(lambda _match: answer, prompt, count=1)
    else:
        text = prompt
    units = set()
    for line in text.split("\n"):
        line = _LABEL.sub("", line.strip())
        for part in _SPLIT.split(line):
            unit = _DROP.sub("", part)
            if len(unit) >= MIN_LEN:
                units.add(unit)
    return frozenset(units)
