"""State whose fields may never have been set, for what the kana practice line keeps (design 13).

The practice line's state used to be separate attributes of the app object: one that was never assigned did
not exist, was not kept with a page for Back, and was not given back by it. A state object keeps that with the
marker UNSET.

Pure: no Toga, no store, no app object.
"""
from __future__ import annotations

from dataclasses import fields, replace


class _Unset:
    """The value of a field that was never set since the app started."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


UNSET = _Unset()


class PartlySet:
    """A dataclass whose fields start as UNSET."""

    def get(self, name: str, default=None):
        """A field, or ``default`` when it was never set."""
        value = getattr(self, name)
        return default if value is UNSET else value

    def set_fields(self) -> dict:
        """The fields that were set, by name."""
        return {item.name: getattr(self, item.name) for item in fields(self) if getattr(self, item.name) is not UNSET}

    def restored_over(self, current):
        """What Back gives the app from a page that kept this object: the fields it holds, over what the app
        holds now. A field the kept page never held stays as it is now."""
        base = current if isinstance(current, type(self)) else type(self)()
        return replace(base, **self.set_fields())
