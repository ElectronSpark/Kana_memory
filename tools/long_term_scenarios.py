"""Reproducible user calendars and an independent synthetic recall model.

This is test input, not a fitted model of the owner or an efficacy benchmark.
It deliberately imports neither the application nor FSRS. A harness must feed
these observations through the actual application's public learning APIs.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
import argparse
import hashlib
import json
from pathlib import Path
import random

PROFILES = {
    "steady": {"retention_factor": 1.0, "attention": 0.97,
               "session_counts": (3, 4, 4, 4, 5), "duration_range": (210, 390)},
    "constrained": {"retention_factor": 0.75, "attention": 0.93,
                    "session_counts": (3, 3, 3, 4), "duration_range": (180, 240)},
    "strong": {"retention_factor": 1.3, "attention": 0.985,
               "session_counts": (4, 4, 5), "duration_range": (300, 480)},
}
MODEL_VERSION = "synthetic-independent-half-life-v1"


@dataclass(frozen=True)
class SessionPlan:
    start_utc: str
    duration_seconds: int

    @property
    def start(self):
        return datetime.fromisoformat(self.start_utc)


@dataclass(frozen=True)
class DayPlan:
    day_index: int
    date: str
    kind: str
    new_items: int
    sessions: tuple[SessionPlan, ...]
    day_factor: float


@dataclass(frozen=True)
class Gap:
    start_day: int
    length_days: int
    kind: str


@dataclass(frozen=True)
class Scenario:
    seed: int
    profile: str
    start_date: str
    days: tuple[DayPlan, ...]
    gaps: tuple[Gap, ...]
    schema_version: int = 1

    def summary(self):
        kinds = Counter(day.kind for day in self.days)
        sessions = [s for day in self.days for s in day.sessions]
        active = len(self.days) - kinds["gap"]
        return {
            "seed": self.seed, "profile": self.profile, "start_date": self.start_date,
            "end_date": self.days[-1].date, "calendar_days": len(self.days),
            "active_days": active, "day_counts": dict(kinds),
            "normal_fraction_of_active_days": round(kinds["normal"] / active, 4),
            "short_gap_count": sum(g.kind == "short" for g in self.gaps),
            "long_gap_lengths": [g.length_days for g in self.gaps if g.kind == "long"],
            "gap_days": sum(g.length_days for g in self.gaps),
            "planned_new_items": sum(d.new_items for d in self.days),
            "surge_new_items": [d.new_items for d in self.days if d.kind == "surge"],
            "session_count": len(sessions),
            "planned_study_hours": round(sum(s.duration_seconds for s in sessions) / 3600, 2),
            "planned_minutes_per_active_day": round(sum(s.duration_seconds for s in sessions) / 60 / active, 2),
        }

    def to_dict(self):
        result = asdict(self)
        result["summary"] = self.summary()
        result["limitations"] = [
            "Synthetic calendar and behavior assumptions, not owner data or measured learning efficacy.",
            "new_items is an admission request; the actual app may add prerequisites or exhaust its catalog.",
            "Session time is a hard simulation budget; unused time and unfinished work must remain observable.",
            "UTC calendar isolates scheduler behavior; this input does not claim a DST/time-zone UI test.",
        ]
        return result


def make_scenario(seed=20261027, profile="steady", days=1461, start="2027-01-01"):
    """Four years by default; always 36 short and 4 longer separate absences."""
    if profile not in PROFILES:
        raise ValueError("Unknown synthetic learner profile")
    if type(days) is not int or days < 1096:
        raise ValueError("Long-term scenarios must cover at least 1096 days")
    start_date = date.fromisoformat(start) if isinstance(start, str) else start
    if isinstance(start_date, datetime):
        start_date = start_date.date()
    if not isinstance(start_date, date):
        raise ValueError("Invalid start date")
    # Calendar randomness is shared across profiles for a paired sensitivity
    # comparison. Capacity and recall change; vacations/admissions do not.
    rng = random.Random(seed)
    gaps, occupied = [], set()
    for fraction, length in zip((0.11, 0.30, 0.55, 0.78), (21, 35, 63, 105)):
        begin = int(days * fraction) + rng.randint(-10, 10)
        gaps.append(Gap(begin, length, "long"))
        occupied.update(range(begin, begin + length))
    for _ in range(36):
        for attempt in range(10000):
            length = rng.randint(2, 6)
            begin = rng.randrange(24, days - length - 30)
            # Leave at least four ordinary days between absences, and protect
            # the first/last month for initial learning and final recovery.
            if not occupied.intersection(range(begin - 4, begin + length + 4)):
                gaps.append(Gap(begin, length, "short"))
                occupied.update(range(begin, begin + length))
                break
        else:
            raise RuntimeError("Could not place separated absence periods")
    active = [index for index in range(days) if index not in occupied]
    surge_days = set(rng.sample(active[14:-14], 12))
    low_days = set(rng.sample([d for d in active[7:] if d not in surge_days], 48))
    calendar = []
    for index in range(days):
        day = start_date + timedelta(days=index)
        if index in occupied:
            calendar.append(DayPlan(index, day.isoformat(), "gap", 0, (), 1.0))
            continue
        # Independent per-day RNG makes changes in one day's number of
        # sessions unable to change subsequent dates or admission decisions.
        day_rng = random.Random(_stable_int(seed, "day", index))
        kind = "surge" if index in surge_days else "low" if index in low_days else "normal"
        new_items = (day_rng.choice((30, 45, 60, 90)) if kind == "surge" else
                     day_rng.choice((0, 0, 1)) if kind == "low" else
                     day_rng.choice((2, 3, 3, 3, 4)))
        profile_rng = random.Random(_stable_int(seed, "capacity", index))
        config = PROFILES[profile]
        count = profile_rng.choice(config["session_counts"])
        low, high = config["duration_range"]
        if kind == "low":
            count, low, high = profile_rng.choice((1, 2)), 180, 210
        # Slots span the day, instead of treating the daily budget as one
        # continuous run that always satisfies ten-minute learning steps.
        slots = [8 * 60 + int(13 * 60 * n / (count - 1)) for n in range(count)] if count > 1 else [20 * 60]
        sessions = []
        for slot in slots:
            minute = slot + profile_rng.randint(-18, 18)
            at = datetime.combine(day, time(), timezone.utc) + timedelta(minutes=minute)
            sessions.append(SessionPlan(at.isoformat(), profile_rng.randint(low, high)))
        daily = 0.76 if day_rng.random() < 0.07 else day_rng.uniform(0.94, 1.04)
        calendar.append(DayPlan(index, day.isoformat(), kind, new_items, tuple(sessions), round(daily, 4)))
    return Scenario(seed, profile, start_date.isoformat(), tuple(calendar), tuple(sorted(gaps, key=lambda g: g.start_day)))


def _stable_int(*parts):
    data = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    return int.from_bytes(hashlib.sha256(data).digest()[:8], "big")


def _aware(now):
    if isinstance(now, str):
        now = datetime.fromisoformat(now)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("Synthetic observations need a time-zone-aware timestamp")
    return now.astimezone(timezone.utc)


@dataclass(frozen=True)
class Attempt:
    correct: bool
    rating: int
    outcome: str
    assisted: bool
    duration_seconds: int
    recall_probability: float
    item_difficulty: float
    elapsed_days: float
    attempt_number: int
    model_version: str = MODEL_VERSION

    def to_dict(self):
        return asdict(self)


@dataclass
class _Memory:
    half_life_days: float
    last_seen: datetime | None = None
    attempts: int = 0
    exposures: int = 0


class SyntheticLearner:
    """Independent exponential forgetting with heterogeneous content/ability.

    Numeric parameters are deliberately transparent sensitivity assumptions,
    not coefficients estimated from the app or copied from FSRS. The result
    is useful for queue/invariant stress testing, not proving retention gains.
    """
    def __init__(self, seed=20261027, profile="steady"):
        if profile not in PROFILES:
            raise ValueError("Unknown synthetic learner profile")
        self.seed, self.profile = seed, profile
        self._memory = {}

    def _traits(self, key):
        rng = random.Random(_stable_int(self.seed, "item", key))
        # About one item in twelve is unusually difficult; it remains that
        # way across sessions/restarts instead of difficulty being fresh noise.
        difficulty = rng.uniform(0.7, 1.6) * (1.65 if rng.random() < 1 / 12 else 1)
        return difficulty

    def _get(self, key):
        key = str(key)
        if key not in self._memory:
            self._memory[key] = _Memory(0.7 * PROFILES[self.profile]["retention_factor"] / self._traits(key))
        return self._memory[key]

    def teach(self, memory_key, now):
        """Update only the external learner, never the application's score."""
        now = _aware(now)
        memory = self._get(memory_key)
        if memory.last_seen and now < memory.last_seen:
            raise ValueError("Synthetic exposure precedes prior observation")
        memory.half_life_days = max(memory.half_life_days,
                                    1.1 * PROFILES[self.profile]["retention_factor"] / self._traits(str(memory_key)))
        memory.last_seen = now
        memory.exposures += 1

    def probability(self, memory_key, now, *, kind="", day_factor=1.0):
        now = _aware(now)
        memory = self._get(memory_key)
        elapsed = (now - memory.last_seen).total_seconds() / 86400 if memory.last_seen else 0
        if elapsed < 0:
            raise ValueError("Synthetic answer precedes prior observation")
        retained = 2 ** (-elapsed / max(memory.half_life_days, 1 / 24)) if memory.last_seen else 0.42
        ability = (0.80 if "handwriting" in kind else 0.88 if "listen" in kind else
                   0.92 if any(word in kind for word in ("reading", "text", "cloze", "application", "romaji")) else 1.0)
        p = retained * ability * PROFILES[self.profile]["attention"] * day_factor
        # The synthetic learner may know part of a concept even after a gap;
        # this small floor is not a hidden guarantee of guessing MCQ answers.
        return min(0.995, max(0.025, p))

    def answer(self, memory_key, now, *, kind="", day_index=None, day_factor=None):
        now = _aware(now)
        key = str(memory_key)
        memory = self._get(key)
        if day_factor is None:
            rng_day = random.Random(_stable_int(self.seed, "performance-day", now.date().isoformat()))
            day_factor = 0.76 if rng_day.random() < 0.07 else rng_day.uniform(0.94, 1.04)
        probability = self.probability(key, now, kind=kind, day_factor=day_factor)
        elapsed = (now - memory.last_seen).total_seconds() / 86400 if memory.last_seen else 0
        # Per-item attempt streams support paired comparisons: moving an
        # attempt changes elapsed-time probability, not its random draw.
        rng = random.Random(_stable_int(self.seed, "answer", key, memory.attempts))
        correct = rng.random() < probability
        difficulty = self._traits(key)
        if correct:
            rating = 2 if probability < 0.65 or rng.random() < 0.16 else 4 if probability > 0.94 and rng.random() < 0.22 else 3
            assisted, outcome = False, "correct"
        else:
            assisted = rng.random() < 0.18
            rating, outcome = 1, "revealed" if assisted else "incorrect"
        duration = rng.randint(7, 19) + (10 if "handwriting" in kind else 4 if "text" in kind else 0)
        if not correct:
            duration += rng.randint(4, 13)
        attempt = Attempt(correct, rating, outcome, assisted, duration, probability, difficulty, elapsed, memory.attempts + 1)
        # Independent memory update: short repetitions provide modest gains;
        # successful retrieval after a longer actual delay gives a larger one.
        # Failure retains some learned knowledge rather than erasing the item.
        factor = PROFILES[self.profile]["retention_factor"]
        if correct:
            spacing = min(3.0, elapsed / max(memory.half_life_days, 1 / 24))
            memory.half_life_days = min(1200, memory.half_life_days * (1 + factor * (0.35 + 0.60 * spacing)))
        else:
            memory.half_life_days = max(0.35 * factor / difficulty, memory.half_life_days * 0.68)
        memory.last_seen, memory.attempts = now, memory.attempts + 1
        return attempt

    def state(self):
        return {"schema_version": 1, "model_version": MODEL_VERSION, "seed": self.seed, "profile": self.profile,
                "memory": {key: {**asdict(value), "last_seen": value.last_seen.isoformat() if value.last_seen else None}
                           for key, value in self._memory.items()}}

    @classmethod
    def from_state(cls, state):
        if state.get("schema_version") != 1 or state.get("model_version") != MODEL_VERSION:
            raise ValueError("Unsupported synthetic learner state")
        result = cls(state["seed"], state["profile"])
        result._memory = {key: _Memory(float(value["half_life_days"]),
                                      _aware(value["last_seen"]) if value["last_seen"] else None,
                                      int(value["attempts"]), int(value["exposures"]))
                          for key, value in state["memory"].items()}
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20261027)
    parser.add_argument("--profile", choices=PROFILES, default="steady")
    parser.add_argument("--days", type=int, default=1461)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    scenario = make_scenario(args.seed, args.profile, args.days)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(scenario.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(scenario.summary(), ensure_ascii=False))
