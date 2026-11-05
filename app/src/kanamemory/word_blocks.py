"""Pure position-based word-block editing; callers own persistence and UI."""
from copy import deepcopy
from functools import lru_cache
import re
from unicodedata import normalize


SENTENCE_END_ERROR = "sentence_end_before_remaining_blocks"


def structure_diagnostic(question, answer):
    """Diagnose a closed single-sentence arrangement, without scoring a unit.

    Only a misplaced complete sentence-ending block is currently established.
    Other orders may be legitimate Japanese and remain undecided. This reads
    immutable choices/answers, never a possibly stale on-screen block order.
    The caller owns current material eligibility and whether help was used.
    """
    practice = question.get("practice", {})
    if (question.get("input_mode") != "reorder" or not isinstance(practice, dict)
            or practice.get("task_family") != "translation_wordbank"
            or practice.get("schema_version") != 1 or practice.get("reviewed") is not True):
        return None
    choices = question.get("choices")
    if not isinstance(choices, list) or not 2 <= len(choices) <= 12:
        return None

    def compact(value):
        return re.sub(r"\s+", "", normalize("NFKC", value)) if isinstance(value, str) else ""

    blocks = tuple(compact(value) for value in choices)
    canonical, entered = compact(question.get("answer")), compact(answer)
    accepted = [compact(value) for value in question.get("accepted_answers", ())]
    if (not canonical or not entered or entered in [canonical, *accepted]
            or any(not block for block in blocks) or len(set(blocks)) != len(blocks)
            or len(entered) > 4000 or sum(map(len, blocks)) > 4000):
        return None
    # Quotes, dialogue, multiple sentences and explicit afterthought notation
    # do not support this narrowly written-sentence conclusion.
    if any(char in "「」『』（）()[]【】〈〉《》\"“”‘’…‥—.:：!?！？" for char in "".join(blocks)):
        return None
    if sum(block.count("。") for block in blocks) != 1:
        return None
    ending = next(i for i, block in enumerate(blocks) if "。" in block)
    if len(blocks[ending]) < 2 or not blocks[ending].endswith("。") or not canonical.endswith("。"):
        return None

    def unique_order(text):
        if len(text) != sum(map(len, blocks)):
            return None
        full = (1 << len(blocks)) - 1

        @lru_cache(maxsize=None)
        def visit(position, used):
            if used == full:
                return ((),) if position == len(text) else ()
            matches = []
            for i, block in enumerate(blocks):
                if not used & (1 << i) and text.startswith(block, position):
                    matches.extend((i, *tail) for tail in visit(position + len(block), used | (1 << i)))
                    if len(matches) > 1:
                        return tuple(matches[:2])
            return tuple(matches)

        orders = visit(0, 0)
        return orders[0] if len(orders) == 1 else None

    expected_order, actual_order = unique_order(canonical), unique_order(entered)
    if expected_order is None or actual_order is None or expected_order[-1] != ending:
        return None
    if actual_order[-1] != ending:
        return SENTENCE_END_ERROR
    return None


def proposed_blocks(choices, draft, index=None):
    """Return a detached draft, or None for a repeated/empty edit.

    Duplicate text is legal: choices are distinguished by position. Persist the
    proposal before replacing the active draft; a failed save leaves it retryable.
    """
    used = list(draft.get("used", []))
    if index is None:
        if not used:
            return None
        used.pop()
    else:
        if type(index) is not int or not 0 <= index < len(choices):
            raise ValueError("Invalid word-block position")
        if index in used:
            return None
        used.append(index)
    parts = [choices[i] for i in used]
    return {**deepcopy(draft), "used": used, "parts": parts, "answer": "".join(parts)}
