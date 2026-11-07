"""Opt-in synthetic responses, not a fitted human model or app scheduler.

All coefficients below are transparent sensitivity assumptions. The actor sees
answer keys to generate input; only Response.visible() may enter the app. No
application, FSRS state, real learner database, network or model is imported.
Observing an answer never strengthens memory: feedback consumption does that.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import math
import random
import re

from long_term_scenarios import PROFILES, _aware, _stable_int

MODEL_VERSION = "synthetic-observation-feedback-v2"
RESPONSES = {"choice", "text", "reorder", "handwriting"}
RECENT_RECEIPTS = 128


@dataclass(frozen=True)
class Response:
    answer: str
    correct: bool
    assisted: bool
    rating: int
    outcome: str
    duration_seconds: int
    reason: str
    recall_probability: float
    item_difficulty: float
    elapsed_days: float
    attempt_number: int
    model_version: str = MODEL_VERSION

    @property
    def probability(self):
        return self.recall_probability

    def visible(self):
        """Only actual user input; hidden causes/probabilities never reach SRS."""
        return {key: getattr(self, key) for key in
                ("answer", "assisted", "rating", "outcome", "duration_seconds")}

    def to_dict(self):
        return asdict(self)


class ResponseLearner:
    """Small external-memory actor with reproducible, delayed feedback effects.

    memory_key must explicitly identify the ability. ``kind`` is accepted only
    for compatibility with the old observer, never parsed into difficulty.
    Confusions are caller-reviewed {answer, error_type, condition_key} records;
    no semantic relationship is inferred from text. Completed receipt payloads
    are bounded; compact retired-ID hashes reject stale, evicted callbacks.
    """

    def __init__(self, seed=20261027, profile="steady", *, fatigue=False):
        if profile not in PROFILES or type(fatigue) is not bool:
            raise ValueError("Unknown response-model profile or fatigue switch")
        self.seed, self.profile, self.fatigue = seed, profile, fatigue
        self._memory, self._receipts, self._retired = {}, {}, set()
        self._session = {"session_id": "", "started_at": None, "answers": 0, "consecutive_errors": 0,
                         "elapsed_seconds": 0, "stop": False}

    def _traits(self, key):
        rng = random.Random(_stable_int(self.seed, "item", key))
        return rng.uniform(0.7, 1.6) * (1.65 if rng.random() < 1 / 12 else 1)

    def _blank(self, key):
        return {"half_life_days": .7 * PROFILES[self.profile]["retention_factor"] / self._traits(key),
                "last_learning": None, "last_exposure": None, "last_observation": None,
                "attempts": 0, "exposures": 0, "contexts": {}}

    def _check_time(self, memory, now):
        for key in ("last_learning", "last_exposure", "last_observation"):
            if memory[key] and now < _aware(memory[key]):
                raise ValueError("Synthetic time moved backwards")

    def teach(self, memory_key, now, *, context_id="", condition_key=""):
        now, key = _aware(now), str(memory_key)
        memory = self._memory.get(key, self._blank(key))
        self._check_time(memory, now)
        memory["half_life_days"] = max(memory["half_life_days"],
            1.1 * PROFILES[self.profile]["retention_factor"] / self._traits(key))
        memory["last_learning"] = memory["last_exposure"] = now.isoformat()
        memory["exposures"] += 1
        self._learn_context(memory, condition_key, context_id)
        self._memory[key] = memory

    @staticmethod
    def _context(condition, context):
        return json.dumps([condition, context], ensure_ascii=False, separators=(",", ":"))

    def _learn_context(self, memory, condition, context):
        if not condition and not context:
            return
        key = self._context(condition, context)
        # Small recency cache, not an ever-growing history of sentence strings.
        count = memory["contexts"].pop(key, 0)
        memory["contexts"][key] = min(4, count + 1)
        while len(memory["contexts"]) > 12:
            del memory["contexts"][next(iter(memory["contexts"]))]

    def probability(self, memory_key, now, *, kind="", day_factor=1.0):
        now, key = _aware(now), str(memory_key)
        if not isinstance(day_factor, (int, float)) or not math.isfinite(day_factor) or not 0 <= day_factor <= 2:
            raise ValueError("Invalid external day factor")
        memory = self._memory.get(key, self._blank(key))
        self._check_time(memory, now)
        if memory["last_learning"]:
            days = (now - _aware(memory["last_learning"])).total_seconds() / 86400
            retained = .88 * 2 ** (-days / memory["half_life_days"])
        else:
            retained = .18
        if memory["last_exposure"]:
            seconds = (now - _aware(memory["last_exposure"])).total_seconds()
            retained += .10 * math.exp(-seconds / 900)
        tired = max(.88, 1 - .01 * max(0, self._session["answers"] - 4)) if self.fatigue else 1
        return max(.01, min(.995, retained * PROFILES[self.profile]["attention"] * day_factor * tired))

    def _rng(self, key, attempt):
        return random.Random(_stable_int(self.seed, "response", key, attempt))

    @staticmethod
    def _id_digest(value):
        return hashlib.sha256(value.encode("utf8")).hexdigest()

    def get_observation(self, attempt_id):
        row = self._receipts.get(attempt_id)
        return Response(**row["response"]) if row else None

    def observe(self, attempt_id, memory_key, now, *, response, answer, options=(),
                confusions=(), condition_key="", context_id="", help_available=True,
                day_factor=1.0):
        now, key = _aware(now), str(memory_key)
        if not isinstance(attempt_id, str) or not attempt_id or response not in RESPONSES:
            raise ValueError("An explicit attempt ID and response operation are required")
        if not isinstance(answer, str) or not answer or type(help_available) is not bool:
            raise ValueError("Invalid answer or help flag")
        options = tuple(options)
        if any(not isinstance(x, str) for x in options) or len(options) > 64 or len(set(options)) != len(options):
            raise ValueError("Invalid visible choices")
        if response == "choice" and (answer not in options or len(options) < 2):
            raise ValueError("Choice questions require their correct answer and a distractor")
        if not isinstance(condition_key, str) or not isinstance(context_id, str) or len(confusions) > 8:
            raise ValueError("Invalid bounded context or confusion set")
        checked = []
        for item in confusions:
            if (not isinstance(item, dict) or set(item) - {"answer", "error_type", "condition_key"}
                    or not isinstance(item.get("answer"), str) or not item["answer"] or item["answer"] == answer
                    or not isinstance(item.get("error_type"), str) or not item["error_type"]
                    or not isinstance(item.get("condition_key", ""), str)):
                raise ValueError("Confusions must be explicit reviewed wrong answers")
            if response == "choice" and item["answer"] not in options:
                raise ValueError("A confusion cannot invent an invisible choice")
            checked.append(dict(item))
        request = {"key": key, "at": now.isoformat(), "response": response, "answer": answer,
                   "options": list(options), "confusions": checked, "condition_key": condition_key,
                   "context_id": context_id, "help_available": help_available, "day_factor": day_factor}
        old = self._receipts.get(attempt_id)
        if old:
            # A retry may arrive after a restart; it does not create a second
            # observation at the later wall/virtual time.
            if now < _aware(old["request"]["at"]) or {**old["request"], "at": now.isoformat()} != request:
                raise ValueError("An attempt ID cannot identify different observations")
            return Response(**old["response"])
        if self._id_digest(attempt_id) in self._retired:
            raise ValueError("Stale evicted attempt; it cannot create another observation")
        if any(row["request"]["key"] == key and row["feedback"] is None for row in self._receipts.values()):
            raise ValueError("Consume or preserve the pending observation before another attempt")
        probability = self.probability(key, now, day_factor=day_factor)
        memory = self._memory.get(key, self._blank(key))
        rng = self._rng(key, memory["attempts"])
        known_context = memory["contexts"].get(self._context(condition_key, context_id), 0)
        alternatives = [x for x in options if x != answer] if response == "choice" else []
        applicable = [x for x in checked if x.get("condition_key", "") in ("", condition_key)]
        selected, reason, assisted = "", "forgotten", False
        if rng.random() < probability:
            selected, reason = answer, "recall"
            if rng.random() < .025:
                selected, reason = (alternatives[int(rng.random() * len(alternatives))] if alternatives else ""), "slip"
        elif response == "choice" and rng.random() < .12 + .04 * known_context:
            selected, reason = answer, "recognition"
        elif help_available and rng.random() < .18:
            selected, reason, assisted = answer, "hint", True
        elif applicable and rng.random() < .65:
            chosen = applicable[int(rng.random() * len(applicable))]
            selected, reason = chosen["answer"], "confusion:" + chosen["error_type"]
        elif response == "choice":
            selected = options[int(rng.random() * len(options))]
            reason = "guess" if selected == answer else "wrong_guess"
        if not selected and help_available:
            # The real UI cannot submit an empty answer. Model the user's
            # explicit reveal action, not a fabricated wrong string or an
            # independent correct answer. Learning still waits for feedback.
            selected, reason, assisted = answer, "requested_answer", True
        correct = selected == answer and not assisted
        rating = (2 if reason in {"recognition", "guess"} or probability < .65 else
                  4 if probability > .94 and rng.random() < .22 else 3) if correct else 1
        duration = 7 + int(rng.random() * 13) + {"choice": 0, "text": 4, "reorder": 6, "handwriting": 10}[response]
        duration += 8 if not correct or assisted else 0
        elapsed = (now - _aware(memory["last_learning"])).total_seconds() / 86400 if memory["last_learning"] else 0
        result = Response(selected, correct, assisted, rating,
            "revealed" if assisted else "correct" if correct else "incorrect", duration, reason,
            probability, self._traits(key), elapsed, memory["attempts"] + 1)
        memory["attempts"] += 1
        memory["last_observation"] = now.isoformat()
        self._memory[key] = memory
        self._receipts[attempt_id] = {"request": request, "response": result.to_dict(), "feedback": None}
        self._session["answers"] += 1
        self._session["elapsed_seconds"] += duration
        self._session["consecutive_errors"] = 0 if correct else self._session["consecutive_errors"] + 1
        if self.fatigue and self._session["answers"] >= 6 and self._session["consecutive_errors"] >= 3:
            self._session["stop"] = self._session["stop"] or rng.random() < .15
        return result

    def feedback(self, attempt_id, now, *, understood=True):
        now = _aware(now)
        if type(understood) is not bool or attempt_id not in self._receipts:
            raise ValueError("Feedback needs a retained actual observation")
        row = self._receipts[attempt_id]
        if row["feedback"] is not None:
            if row["feedback"]["understood"] != understood:
                raise ValueError("Committed feedback cannot change its meaning")
            return False
        request, observed = row["request"], Response(**row["response"])
        memory = self._memory[request["key"]]
        self._check_time(memory, now)
        if understood:
            spacing = min(3, observed.elapsed_days / memory["half_life_days"])
            gain = (.35 + .60 * spacing if observed.reason == "recall" else
                    .12 if observed.reason in {"recognition", "slip"} else
                    .06 if observed.assisted else .04)
            memory["half_life_days"] = min(1200, memory["half_life_days"] *
                (1 + PROFILES[self.profile]["retention_factor"] * gain))
            memory["last_learning"] = memory["last_exposure"] = now.isoformat()
            memory["exposures"] += 1
            self._learn_context(memory, request["condition_key"], request["context_id"])
        row["feedback"] = {"at": now.isoformat(), "understood": understood}
        completed = [name for name, receipt in self._receipts.items() if receipt["feedback"] is not None]
        for name in completed[:-RECENT_RECEIPTS]:
            self._retired.add(self._id_digest(name))
            del self._receipts[name]
        return True

    def begin_session(self, session_id, now=None):
        if now is None:
            now = session_id
            session_id = _aware(now).isoformat()
        now = _aware(now)
        if not isinstance(session_id, (str, int)) or isinstance(session_id, bool):
            raise ValueError("Invalid synthetic session identity")
        session_id = str(session_id)
        if session_id == self._session["session_id"]:
            if now.isoformat() != self._session["started_at"]:
                raise ValueError("Session identity cannot be reused at another time")
            return False
        if self._session["started_at"] and now < _aware(self._session["started_at"]):
            raise ValueError("Session clock moved backwards")
        self._session = {"session_id": session_id, "started_at": now.isoformat(), "answers": 0, "consecutive_errors": 0,
                         "elapsed_seconds": 0, "stop": False}
        return True

    def should_stop(self, now=None):
        if now is not None:
            now = _aware(now)
            if self._session["started_at"] and now < _aware(self._session["started_at"]):
                raise ValueError("Session query precedes its start")
        return self.fatigue and self._session["stop"]

    def state(self):
        # JSON roundtrip isolates all mutable containers from the caller.
        return json.loads(json.dumps({"schema_version": 2, "model_version": MODEL_VERSION,
            "seed": self.seed, "profile": self.profile, "fatigue": self.fatigue,
            "memory": self._memory, "receipts": self._receipts,
            "retired_ids": sorted(self._retired), "session": self._session}))

    @classmethod
    def from_state(cls, state):
        state = json.loads(json.dumps(state, allow_nan=False))
        if state.get("schema_version") != 2 or state.get("model_version") != MODEL_VERSION:
            raise ValueError("Unsupported response learner state")
        result = cls(state["seed"], state["profile"], fatigue=state["fatigue"])
        if not isinstance(state.get("memory"), dict) or not isinstance(state.get("receipts"), dict):
            raise ValueError("Synthetic memory and receipt maps are required")
        for memory in state["memory"].values():
            if (not isinstance(memory, dict) or not isinstance(memory.get("half_life_days"), (int, float))
                    or not math.isfinite(memory["half_life_days"]) or not 0 < memory["half_life_days"] <= 1200
                    or not isinstance(memory.get("contexts"), dict) or len(memory["contexts"]) > 12
                    or any(type(memory.get(key)) is not int or memory[key] < 0 for key in ("attempts", "exposures"))
                    or any(type(count) is not int or not 1 <= count <= 4 for count in memory["contexts"].values())):
                raise ValueError("Invalid synthetic memory state")
            for key in ("last_learning", "last_exposure", "last_observation"):
                if memory[key]:
                    _aware(memory[key])
        pending_keys, completed = set(), 0
        for identity, row in state["receipts"].items():
            observed, request = Response(**row["response"]), row["request"]
            if not isinstance(identity, str) or not identity or request["key"] not in state["memory"]:
                raise ValueError("Observation references missing synthetic memory")
            if (observed.model_version != MODEL_VERSION or type(observed.correct) is not bool
                    or type(observed.assisted) is not bool or type(observed.rating) is not int
                    or observed.rating not in (1, 2, 3, 4)
                    or type(observed.duration_seconds) is not int or observed.duration_seconds <= 0
                    or not 0 <= observed.recall_probability <= 1 or not 0 < observed.item_difficulty
                    or observed.elapsed_days < 0 or type(observed.attempt_number) is not int
                    or not 1 <= observed.attempt_number <= state["memory"][request["key"]]["attempts"]
                    or observed.correct != (observed.answer == request["answer"] and not observed.assisted)
                    or observed.outcome != ("revealed" if observed.assisted else "correct" if observed.correct else "incorrect")):
                raise ValueError("Invalid saved response")
            at = _aware(request["at"])
            if row["feedback"] is None:
                if request["key"] in pending_keys:
                    raise ValueError("Duplicate pending observation for an ability")
                pending_keys.add(request["key"])
            else:
                completed += 1
                if type(row["feedback"].get("understood")) is not bool or _aware(row["feedback"]["at"]) < at:
                    raise ValueError("Invalid feedback receipt")
        if completed > RECENT_RECEIPTS or not isinstance(state.get("retired_ids"), list):
            raise ValueError("Invalid receipt retention state")
        if any(not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value) for value in state["retired_ids"]):
            raise ValueError("Invalid retired attempt digest")
        session = state.get("session", {})
        if (not isinstance(session.get("session_id"), str) or type(session.get("stop")) is not bool
                or any(type(session.get(key)) is not int or session[key] < 0
                       for key in ("answers", "consecutive_errors", "elapsed_seconds"))
                or session["consecutive_errors"] > session["answers"]):
            raise ValueError("Invalid synthetic session")
        if session.get("started_at"):
            _aware(session["started_at"])
        result._memory, result._receipts = state["memory"], state["receipts"]
        result._retired, result._session = set(state["retired_ids"]), state["session"]
        return result
