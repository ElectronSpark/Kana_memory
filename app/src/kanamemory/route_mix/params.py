"""Parameters of the main-route mix (design 11 section 16; pure)."""
from __future__ import annotations

from dataclasses import dataclass, fields

# 2: the M5 floor per question kind as well; 3: per settings group instead (kinds outside every group keep
# the per-kind floor); 4: readiness counts only templates open at introduction (design 11 section 18, Q8);
# 5: the choice floor follows the kinds the learner has switched on (one choice per ability), not the groups
ALGORITHM_VERSION = 5
# The flag of a section of the main route's given beginning (main_route_order.GIVEN_ORDER): the mix keeps
# those sections first, in their order and units, and mixes only what follows them.
GIVEN_ORDER = "given_order"


def switched_kinds(kinds) -> tuple:
    """The learner's switched-on question kinds (learning_questions.stored_enabled_kinds) as plain, ordered
    data: the distinct strings, sorted. Anything that is not a list, tuple or set of kinds (a malformed
    setting) is no switch at all: (), the plan then has no choice floor."""
    if not isinstance(kinds, (list, tuple, set, frozenset)):
        return ()
    return tuple(sorted({kind for kind in kinds if isinstance(kind, str)}))


@dataclass(frozen=True)
class MixParams:
    """floor: M5 floor of gated questions per ability (min(derivation count, floor)); choice_floor: the
    same over the templates of the switched-on kinds of each ability (min(derivation count, choice_floor));
    kinds: the learner's switched-on kinds (switched_kinds), empty: no choice floor.

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
    kinds: tuple = ()

    def __post_init__(self):
        for field in fields(self):
            if field.name == "kinds":
                continue
            value = getattr(self, field.name)
            if type(value) is not int or value < (0 if field.name in {"floor", "choice_floor", "search_width"} else 1):
                raise ValueError("Invalid main route mix parameter: " + field.name)
        if not isinstance(self.kinds, tuple) or self.kinds != switched_kinds(self.kinds):
            raise ValueError("Invalid main route mix parameter: kinds")

    def key(self) -> dict:
        """The plain (JSON) value a stored plan is keyed by (the switched-on kinds included)."""
        value = {field.name: getattr(self, field.name) for field in fields(self)}
        value["kinds"] = list(self.kinds)
        return value


DEFAULT_PARAMS = MixParams()
