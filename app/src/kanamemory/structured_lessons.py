"""Read-only, versioned teaching views; never replace saved learning snapshots.

The authored blocks describe the current catalogue only. A changed or imported
instruction keeps its own explanation, rather than silently inheriting another
sense's diagram. Neither this module nor its renderer touches review state.
"""
from copy import deepcopy
from functools import lru_cache

FORMAT = "kanamemory-lesson-view"
VERSION = 1
KINDS = {"pattern", "flow", "compare", "points", "beats", "pitch", "pitch_sequence"}
SOURCE_FIELDS = ("id", "revision", "family", "domain", "title", "reading", "meaning", "instruction")


def source_matches(item, original):
    if all(item.get(key) == original.get(key) for key in SOURCE_FIELDS):
        return True
    from .kana_reading_rules import TEACHING_RULE_IDS
    # A reviewed pronunciation overlay intentionally retains the enrolled
    # revision. Match every actual display field before using today's diagram;
    # a marker alone must never attach a diagram to edited or generated prose.
    return (item.get("id") in TEACHING_RULE_IDS and not item.get("ai_generated")
            and item.get("teaching_revision") == original.get("revision")
            and all(item.get(key) == original.get(key) for key in SOURCE_FIELDS if key != "revision"))


@lru_cache(maxsize=1)
def catalogue():
    from .learning_content import learning_items
    from .structured_lessons_grammar_a import LESSONS as a
    from .structured_lessons_grammar_b import LESSONS as b
    from .structured_lessons_words import LESSONS as words
    from .structured_lessons_pronunciation import LESSONS as pronunciation
    plans = {}
    for source in (a, b, words, pronunciation):
        if plans.keys() & source.keys():
            raise ValueError("Duplicate structured lesson")
        plans.update(source)
    from .material_review import reviewed_plan
    plans = {cid: reviewed_plan(cid, plan) for cid, plan in plans.items()}
    originals = {item["id"]: item for item in learning_items()}
    for plan in plans.values():
        validate_plan(plan)
    return originals, plans


def validate_plan(plan):
    def text(value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Lesson block needs nonempty text")
    text(plan.get("focus"))
    blocks = plan.get("blocks")
    if not isinstance(blocks, list) or not 1 <= len(blocks) <= 3:
        raise ValueError("Use one to three teaching blocks")
    for block in blocks:
        kind = block.get("kind")
        if kind not in KINDS:
            raise ValueError("Unknown lesson block")
        text(block.get("label"))
        if kind == "pitch_sequence":
            from .pitch_practice import specification
            specification({"input_mode": "pitch_fill", "pitch_practice": block.get("pitch_practice")})
        elif kind == "pitch":
            from .pitch_practice_ui import validate_pitch_data
            validate_pitch_data(block.get('morae'), block.get('levels'),
                                block.get('word_mora_count'), block.get('number'), partial=False)
        elif kind == "pattern":
            text(block.get("text"))
        elif kind == "compare":
            rows = block.get("rows")
            if not isinstance(rows, list) or not 2 <= len(rows) <= 4:
                raise ValueError("A comparison needs two to four rows")
            for row in rows:
                if not isinstance(row, (list, tuple)) or len(row) != 2:
                    raise ValueError("A comparison needs a label and explanation")
                for value in row:
                    text(value)
        else:
            key = {"flow": "nodes", "points": "items", "beats": "cells"}[kind]
            values = block.get(key)
            if not isinstance(values, list) or not values:
                raise ValueError("A diagram needs labelled parts")
            for value in values:
                text(value)
        if "caption" in block:
            text(block["caption"])


def original_sections(item):
    """Deduplicate intact authored sections, without slicing linguistic clauses."""
    instruction = item.get("instruction") or {}
    seen = {str(instruction.get("summary") or item.get("meaning") or "")}
    sections = []
    for label, values in (("用法", [instruction.get("rule")]),
                          ("步骤", instruction.get("steps") or []),
                          ("辨别", [instruction.get("contrast")]),
                          ("使用场合", [item.get("register")])):
        unique = []
        for value in values:
            if value and str(value) not in seen:
                unique.append(str(value))
                seen.add(str(value))
        if unique:
            sections.append((label, unique))
    return sections


def lesson_view(item):
    originals, plans = catalogue()
    key = item.get("id")
    original = originals.get(key)
    if original and key in plans and source_matches(item, original):
        from .lesson_diagrams import diagram_for
        diagram = diagram_for(key)
        return {"format": FORMAT, "version": VERSION, "authored": True,
                **deepcopy(plans[key]), **({'diagram': diagram} if diagram else {})}
    instruction = item.get("instruction") or {}
    # Imported/older material retains all its own information. Do not invent a
    # construction or hide a warning merely because the ID resembles a lesson.
    return {"format": FORMAT, "version": VERSION, "authored": False,
            "focus": str(instruction.get("summary") or item.get("meaning") or item.get("title") or ""),
            "blocks": [{"kind": "points", "label": label, "items": values}
                       for label, values in original_sections(item)]}
