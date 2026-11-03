"""Pure position-based word-block editing; callers own persistence and UI."""
from copy import deepcopy


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
