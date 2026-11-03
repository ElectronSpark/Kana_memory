"""Pure incremental scaffolding evidence; no FSRS, storage, clock or UI access.

The persistence adapter supplies already validated, chronologically ordered
events. Full history replay remains an independent oracle in domain.py.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

POLICY_VERSION = 1
Evidence = dict[str, tuple[float, datetime | None, datetime | None]]


@dataclass(frozen=True)
class AdaptiveEvent:
    content_id: str
    when: datetime
    primary: Literal["skip", "reset", "credit"]
    units: tuple[tuple[str, str], ...] = ()
    weight: float = 1.0


def advance(evidence: Evidence, event: AdaptiveEvent) -> None:
    """Fold one normalized event into the caller-owned projection only."""
    if event.primary == "reset":
        evidence[event.content_id] = (0, None, None)
    elif event.primary == "credit":
        count, first, _ = evidence.get(event.content_id, (0, None, None))
        evidence[event.content_id] = (count + 1, first or event.when, event.when)
    for identity, result in event.units:
        count, first, last = evidence.get(identity, (0, None, None))
        if result == "incorrect":
            remaining = max(0, count - event.weight) if event.weight < 1 else 0
            evidence[identity] = (remaining, first, last) if remaining else (0, None, None)
        else:
            evidence[identity] = (count + event.weight, first or event.when, event.when)


def stages(evidence: Evidence) -> dict[str, int]:
    return {identity: (2 if count >= 2 and last - first >= timedelta(days=1)
                       else 1 if count >= 2 else 0)
            for identity, (count, first, last) in evidence.items()}
