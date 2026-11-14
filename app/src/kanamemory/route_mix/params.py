"""Parameters of the main-route mix (design 11 section 16; pure)."""
from __future__ import annotations

from dataclasses import dataclass, fields

# 2: the M5 floor per question kind as well; 3: per settings group instead (kinds outside every group keep
# the per-kind floor); 4: readiness counts only templates open at introduction (design 11 section 18, Q8)
ALGORITHM_VERSION = 4


def kind_groups(mapping) -> tuple:
    """The learner's kind switches (learning_ui.KIND_GROUPS: title -> kinds) as plain, ordered data."""
    return tuple(sorted((str(title), tuple(sorted(str(kind) for kind in kinds))) for title, kinds in mapping.items()))


@dataclass(frozen=True)
class MixParams:
    """floor: M5 floor of gated questions per ability (min(derivation count, floor)); choice_floor: the
    same per (ability, settings group) and, for a kind in no group, per kind (min(derivation count,
    choice_floor)); groups: the settings groups (kind_groups), empty: every kind on its own.

    library_word_limit / library_word_chunk: M8(a), a library word section with
    more words is cut into sections of this many words. word_target /
    nonword_target: M6 score max(longest word run / word_target, longest
    non-word run / nonword_target); targets, never hard limits. search_width:
    the balanced placements tried are L0 ... L0 + search_width. algorithm: the
    version of these rules; part of a plan's key, like the other fields.
    """
    floor: int = 3
    choice_floor: int = 1
    library_word_limit: int = 8
    library_word_chunk: int = 4
    word_target: int = 16
    nonword_target: int = 3
    search_width: int = 24
    algorithm: int = ALGORITHM_VERSION
    groups: tuple = ()

    def __post_init__(self):
        for field in fields(self):
            if field.name == "groups":
                continue
            value = getattr(self, field.name)
            if type(value) is not int or value < (0 if field.name in {"floor", "choice_floor", "search_width"} else 1):
                raise ValueError("Invalid main route mix parameter: " + field.name)
        if not isinstance(self.groups, tuple) or any(
                not isinstance(row, tuple) or len(row) != 2 or not isinstance(row[0], str)
                or not isinstance(row[1], tuple) or not all(isinstance(kind, str) for kind in row[1])
                for row in self.groups):
            raise ValueError("Invalid main route mix parameter: groups")

    def key(self) -> dict:
        """The plain (JSON) value a stored plan is keyed by."""
        value = {field.name: getattr(self, field.name) for field in fields(self)}
        value["groups"] = [[title, list(kinds)] for title, kinds in self.groups]
        return value


DEFAULT_PARAMS = MixParams()
