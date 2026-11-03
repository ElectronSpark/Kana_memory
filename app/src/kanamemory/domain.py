"""FSRS learning state, deterministic questions, and script-sensitive grading.

No UI, audio engine, handwriting engine, network, or packaged-content mutations.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, time, timedelta, timezone, tzinfo
import hashlib
import heapq
import json
import operator
from pathlib import Path
import random
import sqlite3
import unicodedata
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fsrs import Card, Rating, Scheduler

from . import app_clock, session_questions
from .romanization import kana_learning_romaji_values, romanize_kana
from .kana_audio import kana_audio_plan
from .kana_question_policy import direction_allowed, question_allowed

UTC = timezone.utc
ALGORITHM_VERSION = "py-fsrs-6.3.2/fsrs-6"
CONTENT_CONTROLS_SETTING = "study_item_controls_v1"
SKILLS = {"kana_choice", "kana_romaji", "romaji_kana", "romaji_kana_full", "romaji_handwriting", "kana_handwriting", "word_kana", "word_meaning_kana", "word_romaji"}
WORD_SKILLS = {"word_kana", "word_meaning_kana", "word_romaji"}
WORD_KIND_PAUSE = "word_kind_pause_v1"
WORD_ROMAJI_POLICY = "word_romaji_low_weight_v1"
WORD_ROMAJI_WEIGHT = 0.25
PRACTICE_LANES = {"main", "word_romaji"}
ROMAJI_ANSWER_SKILLS = {"kana_choice", "kana_romaji"}
HANDWRITING_SKILLS = {"kana_handwriting", "romaji_handwriting"}
LISTENING_SKILLS = {"kana_handwriting", "word_kana"}
PRACTICE_WORD_COLLECTIONS = {"foundation_listening", "english_loanwords", "japanese_core", "everyday_supplement"}
SCRIPTS = {"hiragana", "katakana"}
PRACTICE_KANA_CATEGORIES = {"basic", "dakuten", "handakuten", "yoon", "foreign_combination", "foreign_voiced"}
HOMOPHONE_GROUPS = ({"じ", "ぢ"}, {"ず", "づ"}, {"お", "を"}, {"じゃ", "ぢゃ"}, {"じゅ", "ぢゅ"}, {"じょ", "ぢょ"}, {"い", "ゐ"}, {"え", "ゑ"})


def utc_now() -> datetime:
    return app_clock.utc_now()


def _utc(value: datetime | None) -> datetime:
    value = value or utc_now()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Use a timezone-aware datetime")
    return value.astimezone(UTC)


def _validate_key(content_id: str, skill: str, script: str):
    if not isinstance(content_id, str) or not content_id.strip():
        raise ValueError("content_id is required")
    if skill not in SKILLS:
        raise ValueError(f"Unknown practice skill: {skill}")
    if script not in SCRIPTS:
        raise ValueError(f"Unknown script: {script}")


def normalize_answer(text: str) -> str:
    """Canonical composition only. Never erase size, voicing, script or ー."""
    return unicodedata.normalize("NFC", str(text)).strip()


def to_script(text: str, script: str) -> str:
    if script not in SCRIPTS:
        raise ValueError("script must be hiragana or katakana")
    text = normalize_answer(text)
    if script == "hiragana":
        return "".join(chr(ord(c) - 0x60) if 0x30A1 <= ord(c) <= 0x30F6 else c for c in text)
    return "".join(chr(ord(c) + 0x60) if 0x3041 <= ord(c) <= 0x3096 else c for c in text)


def homophone_answers(text: str, script: str) -> tuple[str, ...]:
    hira = to_script(text, "hiragana")
    group = next((g for g in HOMOPHONE_GROUPS if hira in g), {hira})
    return tuple(sorted(to_script(x, script) for x in group))


@dataclass(frozen=True)
class Question:
    content_id: str
    kind: str
    script: str
    prompt: str
    answer: str
    options: list[str] = field(default_factory=list)
    audio_text: str = ""
    accepted_answers: tuple[str, ...] = ()
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class _PracticeTarget:
    """Selection-only record: no prompt, choices, keyboard, or content payload."""
    content_id: str
    kind: str
    script: str
    metadata: dict = field(default_factory=dict)
    options: tuple = ()


def _copy_json(value):
    """Copy the JSON-shaped mutable parts without dataclass/deepcopy traversal."""
    if isinstance(value, dict):
        return {key: _copy_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_json(item) for item in value]
    return value


def _question_from_snapshot(item, *, copy_metadata=True):
    if item["kind"] in WORD_SKILLS:
        from .word_keyboard import normalize_word_question_snapshot
        item = normalize_word_question_snapshot(item)
    return Question(**{**item, "options": list(item.get("options", ())),
        "metadata": (_copy_json(item.get("metadata", {})) if copy_metadata else item.get("metadata", {})),
        "accepted_answers": normalize_accepted_answers(item.get("accepted_answers", ()))})


def _json_copy_plan(value):
    """Compile a private JSON tree's mutable-container copies once.

    Snapshots are immutable between revision tokens. A plan shares only their
    scalar leaves; each invocation still owns every returned dict and list.
    This avoids rechecking thousands of historical metadata leaves per tap.
    """
    children = (value.items() if isinstance(value, dict) else enumerate(value))
    nested = [(key, _json_copy_plan(item)) for key, item in children
              if isinstance(item, (dict, list))]
    if not nested:
        return value.copy

    def clone():
        result = value.copy()
        for key, copy_child in nested:
            result[key] = copy_child()
        return result
    return clone


def _same_json_scalar_types(left, right):
    """After value equality, distinguish JSON booleans and numeric types.

    Python treats True == 1 and 1 == 1.0. Snapshot flags may deliberately
    require `is True`, so those values cannot share a presentation template.
    Dict key order and JSON whitespace have no bearing on this comparison.
    """
    if isinstance(left, dict) and isinstance(right, dict):
        return all(_same_json_scalar_types(value, right[key]) for key, value in left.items())
    value_type = type(left)
    if value_type is not type(right):
        return False
    if value_type is list:
        return all(_same_json_scalar_types(a, b) for a, b in zip(left, right))
    if value_type is float:
        return left.hex() == right.hex()  # Retain the sign of zero as well.
    return True


def _question_storage_view(item):
    """The same canonical field view as vars(_question_from_snapshot(item)).

    This private serialization view needs no Question or mutable-container
    copies. Legacy answer aliases and word keyboard projections still receive
    precisely the same normalization as the public object path.
    """
    if item['kind'] in WORD_SKILLS:
        from .word_keyboard import normalize_word_question_snapshot
        item = normalize_word_question_snapshot(item)
    if item.keys() - Question.__dataclass_fields__.keys():
        raise TypeError('Unknown saved question fields')
    return {'content_id': item['content_id'], 'kind': item['kind'], 'script': item['script'],
            'prompt': item['prompt'], 'answer': item['answer'], 'options': list(item.get('options', ())),
            'audio_text': item.get('audio_text', ''),
            'accepted_answers': normalize_accepted_answers(item.get('accepted_answers', ())),
            'metadata': item.get('metadata', {})}


class _SnapshotQuestionPolicy:
    """Lazy static direction checks over one private immutable JSON snapshot.

    No preference, time, evidence, due date or restoration decision is kept.
    Evaluate only requested rows so an unused legacy row is not newly inspected.
    """
    def __init__(self, items):
        self.items = items
        self._allowed = [None] * len(items)

    def allowed(self, index):
        value = self._allowed[index]
        if value is None:
            item = self.items[index]
            value = (item.allowed(question_allowed) if isinstance(item, session_questions.FrozenRow)
                     else question_allowed(item))
            self._allowed[index] = value
        return value


class _QuestionSequence(Sequence):
    """An opt-in private snapshot view with independently copied public items.

    Each returned view owns its own cache. Repeated indexing and slices share
    that view's objects like a list, but no mutable values escape from the raw
    store snapshot or from another view. Old views retain their old snapshot.
    """
    def __init__(self, items, policy=None):
        self._items, self._copies = items, {}
        self._policy = policy if policy is not None else _SnapshotQuestionPolicy(items)

    def __len__(self):
        return len(self._items)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[position] for position in range(*index.indices(len(self)))]
        position = operator.index(index)
        if position < 0:
            position += len(self)
        if not 0 <= position < len(self):
            raise IndexError('question index out of range')
        if position not in self._copies:
            self._copies[position] = _question_from_snapshot(self._items[position])
        return self._copies[position]

    def __eq__(self, other):
        if not isinstance(other, (list, _QuestionSequence)):
            return NotImplemented
        return len(self) == len(other) and all(a == b for a, b in zip(self, other))

    def _storage_rows(self):
        return [vars(self._copies[index]) if index in self._copies else
                item.projected(_question_storage_view) if isinstance(item, session_questions.FrozenRow) else
                _question_storage_view(item)
                for index, item in enumerate(self._items)]

    def any_metadata_truthy(self, key):
        """Inspect a flag without materializing historical Question objects."""
        for index, item in enumerate(self._items):
            if index in self._copies:
                metadata = self._copies[index].metadata
            elif item['kind'] in WORD_SKILLS:
                metadata = _question_storage_view(item)['metadata']
            else:
                metadata = item.get('metadata', {})
            if metadata.get(key, False):
                return True
        return False

    def identities(self, start=0, excluded_kinds=(), allowed_only=True):
        """Read scalar identities without constructing untouched Questions.

        A view's public Question metadata remains independently mutable. Once
        materialized, use that exact copy and recheck its current policy rather
        than substituting the original stored metadata.
        """
        result = []
        for index in range(*slice(start, None).indices(len(self))):
            copied = self._copies.get(index)
            item = self._items[index]
            kind = copied.kind if copied is not None else item["kind"]
            if kind in excluded_kinds:
                continue
            if allowed_only and not (question_allowed(copied) if copied is not None else self._policy.allowed(index)):
                continue
            result.append((copied.content_id, kind, copied.script) if copied is not None else
                          (item["content_id"], kind, item["script"]))
        return tuple(result)


def question_identities(questions, start=0, *, excluded_kinds=(), allowed_only=True):
    """Value-equivalent scalar projection for eager and lazy question views."""
    if isinstance(questions, _QuestionSequence):
        return questions.identities(start, excluded_kinds, allowed_only)
    return tuple((q.content_id, q.kind, q.script) for q in questions[start:]
                 if q.kind not in excluded_kinds and (not allowed_only or question_allowed(q)))


def any_question_metadata(questions, key):
    """Value-equivalent flag query for eager and opt-in snapshot sequences."""
    if isinstance(questions, _QuestionSequence):
        return questions.any_metadata_truthy(key)
    return any(question.metadata.get(key, False) for question in questions)


@dataclass(frozen=True)
class PreparedSession:
    """In-process worker result; never accepted as an external backup format."""
    snapshot_json: str
    random_seed: str
    policy_version: str
    question_json: tuple[str, ...]


def word_kana_answer(question) -> str:
    """Keep a word's kana identity stable across opposite answer directions."""
    if isinstance(question, Question):
        kind, answer, metadata = question.kind, question.answer, question.metadata
    else:
        kind, answer, metadata = question["kind"], question["answer"], question.get("metadata", {})
    return normalize_answer(metadata.get("kana_answer", "") if kind == "word_romaji" else answer)


def word_romaji_unit_plan(question, plan=None):
    """Resolve a saved or deterministic legacy plan without rewriting history."""
    from .word_romaji_units import build_romaji_unit_plan, validate_romaji_unit_plan
    item = vars(question) if isinstance(question, Question) else question
    if item.get("kind") != "word_romaji":
        raise ValueError("Kana-unit plans require a reverse word question")
    kana = word_kana_answer(item)
    metadata = item.get("metadata", {})
    saved = metadata.get("romaji_unit_plan")
    if "romaji_unit_plan" in metadata:
        validate_romaji_unit_plan(kana, saved)
        if plan is not None and plan != saved:
            raise ValueError("Kana-unit plan does not match the saved question")
        plan = saved
    if plan is None:
        seed = f"{metadata.get('session_seed', 0)}:{item['content_id']}:{kana}"
        plan = build_romaji_unit_plan(kana, seed=seed)
    validate_romaji_unit_plan(kana, plan)
    return _copy_json(plan)


def _validate_word_romaji_question(question):
    item = vars(question) if isinstance(question, Question) else question
    kana = word_kana_answer(item)
    if (not kana or kana != to_script(kana, item["script"])
            or not any("\u3041" <= char <= "\u3096" or "\u30a1" <= char <= "\u30f6" for char in kana)
            or item["answer"] != romanize_kana(kana)):
        raise ValueError("Invalid word romanization choice question")
    if "romaji_unit_plan" in item.get("metadata", {}):
        # Per-kana choices are authoritative for new questions. Whole-word
        # options are only a legacy snapshot format and are never needed here.
        word_romaji_unit_plan(item)
        return
    options = item.get("options", [])
    if (not isinstance(options, list) or not 2 <= len(options) <= 4
            or any(not isinstance(option, str) or not option.strip() for option in options)
            or len({normalize_romaji(option) for option in options}) != len(options)
            or item["answer"] not in options):
        raise ValueError("Invalid word romanization choice question")


def session_practice_lane(questions) -> str:
    """Derive the lane from every saved question; a queue cannot cross lanes."""
    lanes = set()
    for item in questions:
        metadata = item.metadata if isinstance(item, Question) else item.get("metadata", {})
        kind = item.kind if isinstance(item, Question) else item.get("kind")
        marker = metadata.get("word_romaji_drill", False)
        if type(marker) is not bool:
            raise ValueError("Invalid word romanization practice marker")
        if marker and (kind not in {"word_meaning_kana", "word_romaji"} or metadata.get("not_before_utc")
                       or metadata.get("spelling_correction_of")):
            raise ValueError("Word romanization practice requires untimed word questions")
        if "romaji_unit_plan" in metadata and kind != "word_romaji":
            raise ValueError("Kana-unit plans require reverse word questions")
        if kind == "word_romaji":
            if not marker:
                raise ValueError("Reverse word questions require the auxiliary practice lane")
            _validate_word_romaji_question(item)
        lanes.add("word_romaji" if marker else "main")
    if len(lanes) > 1:
        raise ValueError("Practice queues cannot mix main and word romanization lanes")
    return next(iter(lanes), "main")


def prepare_session_snapshot(questions: list[Question], seed=None) -> PreparedSession:
    """Serialize a generated batch off the UI thread, without writing progress."""
    session_practice_lane(questions)
    if seed is None and questions:
        seed = questions[0].metadata.get("session_seed")
    policy = "user-count-new-only-v2" if questions and all(q.metadata.get("new_only") is True for q in questions) else "user-count-due-first-v2"
    if questions and "kinds" in questions[0].metadata.get("practice_config", {}):
        policy = "mixed-batch-new-only-v1" if all(q.metadata.get("new_only") is True for q in questions) else "mixed-batch-due-first-v1"
    if questions and questions[0].metadata.get("practice_config", {}).get("schedule_mode") == "spaced":
        policy = "content-spaced-queue-v1"
    rows = tuple(json.dumps(vars(question), ensure_ascii=False, allow_nan=False) for question in questions)
    snapshot = "[" + ", ".join(rows) + "]"
    from .progress_backup import MAX_BYTES
    if len(snapshot.encode("utf-8")) > MAX_BYTES:
        raise ValueError("本批题目数据过大，请减少轮数或记忆项。")
    return PreparedSession(snapshot, str(seed), policy, rows)


def normalize_accepted_answers(values) -> tuple[str, ...]:
    """Upgrade 0.2 typed romanization aliases without coercing unknown data."""
    if not isinstance(values, (list, tuple)):
        raise ValueError("accepted_answers must be a list or tuple")
    result = []
    for value in values:
        if isinstance(value, dict):
            if (set(value) != {"kind", "text"} or not isinstance(value["kind"], str)
                    or not value["kind"].strip() or not isinstance(value["text"], str)
                    or not value["text"].strip()):
                raise ValueError("Invalid legacy accepted answer")
            value = value["text"]
        elif not isinstance(value, str):
            raise ValueError("Invalid accepted answer")
        result.append(value)
    return tuple(result)


def _legacy_listening_choice(question: Question) -> bool:
    # Early/external listening-choice snapshots can contain kana answers.
    # Today's kana_choice instead shows a kana and asks for Roman letters.
    answer = normalize_answer(question.answer)
    return (question.kind == "kana_choice" and bool(answer)
            and all("ぁ" <= char <= "ゖ" or "ァ" <= char <= "ヺ" or char == "ー" for char in answer))


def isolated_kana_question(question: Question) -> bool:
    """Whether the learner has only a sound cue, including old saved prompts.

    Before this policy the standard reverse-kana builder marked the mere
    presence of romanization as context_given=True. Recognize only those exact
    legacy templates; a real spelling/context prompt must keep its distinction.
    """
    legacy_choice = _legacy_listening_choice(question)
    if question.kind not in {"romaji_kana", "romaji_kana_full", "romaji_handwriting", "kana_handwriting"} and not legacy_choice:
        return False
    metadata = question.metadata
    if metadata.get("answer_scope") == "isolated_sound":
        return True
    if metadata.get("answer_scope") == "orthography":
        return False
    if question.kind == "kana_handwriting" or legacy_choice:
        return metadata.get("audio_only", True) and not metadata.get("context_given", False)
    if not metadata.get("context_given", False):
        return True
    roman = metadata.get("romaji")
    templates = {
        "romaji_kana": "选出罗马音「{roman}」对应的假名。",
        "romaji_kana_full": "从完整假名表拼出罗马音「{roman}」对应的假名。",
        "romaji_handwriting": "写出罗马音「{roman}」对应的假名，并确认识别结果。",
    }
    return (isinstance(roman, str) and bool(roman)
            and metadata.get("kana_symbol") == question.answer
            and question.prompt == templates[question.kind].format(roman=roman))


def check_answer(question: Question, answer: str, *, word_romaji_parts=None,
                 word_romaji_plan=None) -> bool:
    if word_romaji_parts is not None:
        if question.kind != "word_romaji":
            raise ValueError("Kana-unit answers require a reverse word question")
        from .word_romaji_units import compose_romaji_parts, check_romaji_parts
        plan = word_romaji_unit_plan(question, word_romaji_plan)
        if answer != compose_romaji_parts(plan, word_romaji_parts):
            raise ValueError("Submitted Roman text does not match the selected kana units")
        return check_romaji_parts(word_kana_answer(question), word_romaji_parts, plan)
    if word_romaji_plan is not None:
        raise ValueError("A kana-unit plan requires selected parts")
    if question.kind == "word_romaji":
        from .word_romaji_answer import check_word_romaji_answer
        return check_word_romaji_answer(word_kana_answer(question), answer)
    accepted = normalize_accepted_answers(question.accepted_answers)
    if question.kind in {"word_kana", "word_meaning_kana"}:
        from .kana_orthography import allowed_word_spellings
        return normalize_answer(answer) in allowed_word_spellings(
            question.answer, metadata=question.metadata, accepted_answers=accepted)
    if question.kind in ROMAJI_ANSWER_SKILLS and not _legacy_listening_choice(question):
        # Keyboard width and Latin case are cosmetic for romanization; kana
        # answers below continue to distinguish script, size and long vowels.
        expected = {normalize_romaji(question.answer), *(normalize_romaji(x) for x in accepted)}
        return normalize_romaji(answer) in expected
    expected = {normalize_answer(question.answer), *(normalize_answer(x) for x in accepted)}
    # A sound alone cannot select one spelling from a modern homophone group.
    # This applies at grading time, so old queues and drafts keep their payload.
    if isolated_kana_question(question):
        expected.update(homophone_answers(question.answer, question.script))
    return normalize_answer(answer) in expected


is_correct_answer = check_answer


def normalize_romaji(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text)).strip().casefold()


def _review_log_data(event):
    try:
        if "evidence_json" in event.keys() and event["evidence_json"] is not None:
            return {"word_spelling_evidence": json.loads(event["evidence_json"])}
        value = json.loads(event["review_log_json"])
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError, KeyError):
        return {}


def _review_spelling_units(event, log=None):
    """Ignore malformed/foreign optional evidence in historical backup logs."""
    if event["assisted"] or event["skill"] not in WORD_SKILLS:
        return []
    log = _review_log_data(event) if log is None else log
    evidence = log.get("word_spelling_evidence")
    if (not isinstance(evidence, dict) or type(evidence.get("version")) is not int
            or evidence["version"] != 1 or evidence.get("assisted") is not False
            or evidence.get("source_content_id") != event["content_id"]
            or evidence.get("source_kind") != event["skill"]
            or evidence.get("script") != event["script"]
            or not isinstance(event["answered_text"], str)
            or evidence.get("actual") != normalize_answer(event["answered_text"])
            or not isinstance(evidence.get("expected"), str)
            or type(evidence.get("source_question_index")) is not int
            or evidence["source_question_index"] < 0
            or not isinstance(evidence.get("units"), list)
            or ("practice_policy" in evidence and evidence["practice_policy"] != WORD_ROMAJI_POLICY)):
        return []
    if event["skill"] == "word_romaji" and (
            evidence.get("answer_direction") != "romaji"
            or evidence.get("expected_romaji") != romanize_kana(evidence["expected"])):
        return []
    unit_fields = {"romaji_unit_version", "romaji_parts", "romaji_unit_plan"}
    if unit_fields & set(evidence):
        if (event["skill"] != "word_romaji" or not unit_fields <= set(evidence)
                or type(evidence["romaji_unit_version"]) is not int or evidence["romaji_unit_version"] != 1
                or not isinstance(evidence["romaji_parts"], list)):
            return []
        try:
            from .word_romaji_units import validate_romaji_unit_plan, compose_romaji_parts, check_romaji_parts
            plan = evidence["romaji_unit_plan"]
            validate_romaji_unit_plan(evidence["expected"], plan)
            if (compose_romaji_parts(plan, evidence["romaji_parts"]) != evidence["actual"]
                    or check_romaji_parts(evidence["expected"], evidence["romaji_parts"], plan) != evidence.get("correct")):
                return []
        except (ValueError, TypeError, KeyError):
            return []
    seen = set()
    for unit in evidence["units"]:
        if (not isinstance(unit, dict)
                or set(unit) != {"content_id", "symbol", "result", "error_types"}
                or not isinstance(unit["content_id"], str) or not unit["content_id"].strip()
                or not isinstance(unit["symbol"], str) or not unit["symbol"]
                or not isinstance(unit["result"], str) or unit["result"] not in {"correct", "incorrect"}
                or not isinstance(unit["error_types"], list)
                or any(not isinstance(error, str) for error in unit["error_types"])
                or unit["content_id"] in seen):
            return []
        seen.add(unit["content_id"])
    return evidence["units"]


def _review_spelling_weight(event, log=None):
    log = _review_log_data(event) if log is None else log
    evidence = log.get("word_spelling_evidence", {})
    return WORD_ROMAJI_WEIGHT if evidence.get("practice_policy") == WORD_ROMAJI_POLICY else 1


class DailyNewCardLimit(ValueError):
    """Legacy exception retained for callers upgrading from capped releases."""


class StudyStore:
    """A separate learner-owned SQLite database and genuine FSRS scheduler.

    New cards are counted on their first valid review, not when a screen opens.
    The learner chooses each batch's size; daily totals never limit practice.
    """

    def __init__(self, data_dir: str | Path, time_zone: str | tzinfo | None = None, daily_new_limit: int = 10):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "progress.sqlite3"
        self._device_local = time_zone is None
        if isinstance(time_zone, tzinfo):
            self.zone = time_zone
            self.time_zone = getattr(time_zone, "key", str(time_zone))
        elif isinstance(time_zone, str):
            try:
                self.zone = UTC if time_zone == "UTC" else ZoneInfo(time_zone)
                self.time_zone = time_zone
            except ZoneInfoNotFoundError:
                self._device_local = True
                self.zone = datetime.now().astimezone().tzinfo or UTC
                self.time_zone = f"device-local:{self.zone} (unavailable requested zone: {time_zone})"
        elif time_zone is None:
            self.zone = datetime.now().astimezone().tzinfo or UTC
            self.time_zone = f"device-local:{self.zone}"
        else:
            raise TypeError("time_zone must be None, an IANA name, or tzinfo")
        self.practice_lane = "main"
        self._default_daily_new_limit = max(0, int(daily_new_limit))
        self.daily_new_limit = self._default_daily_new_limit
        # Session randomness is separately seeded. Disable FSRS's global RNG so
        # the same ratings/timestamps reproduce the same memory-state sequence.
        self.scheduler = Scheduler(enable_fuzzing=False)
        self._db = sqlite3.connect(self.path, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA journal_mode=WAL")
        from .record_schema import ensure_current
        try:
            self.record_upgrade = ensure_current(self._db, self.data_dir)
        except BaseException:
            self._db.close()
            raise
        with self._db:
            self._db.execute("INSERT OR IGNORE INTO app_setting VALUES (?,?)", ("scheduler", json.dumps(self.scheduler.to_dict())))
        saved = self._db.execute("SELECT value_json FROM app_setting WHERE key='scheduler'").fetchone()
        self.scheduler = Scheduler.from_dict(json.loads(saved[0]))
        from .learning_evidence import ensure_schema as ensure_evidence_schema
        ensure_evidence_schema(self._db)
        self.reload_daily_new_limit()
        self.reset_choice_history()
        # Connection-local revisions invalidate the parsed snapshot even when a
        # restore or maintenance SQL updates it directly. No new backup schema
        # or learner-owned table is introduced. Other connections are detected
        # using SQLite's data_version below.
        self._db.executescript("""
            CREATE TEMP TABLE snapshot_revision (revision INTEGER NOT NULL);
            INSERT INTO snapshot_revision VALUES (0);
            CREATE TEMP TRIGGER snapshot_insert AFTER INSERT ON main.study_session
              BEGIN UPDATE snapshot_revision SET revision=revision+1; END;
            CREATE TEMP TRIGGER snapshot_update AFTER UPDATE ON main.study_session
              BEGIN UPDATE snapshot_revision SET revision=revision+1; END;
            CREATE TEMP TRIGGER snapshot_delete AFTER DELETE ON main.study_session
              BEGIN UPDATE snapshot_revision SET revision=revision+1; END;
            CREATE TEMP TRIGGER question_insert AFTER INSERT ON main.session_question
              BEGIN UPDATE snapshot_revision SET revision=revision+1; END;
            CREATE TEMP TRIGGER question_update AFTER UPDATE ON main.session_question
              BEGIN UPDATE snapshot_revision SET revision=revision+1; END;
            CREATE TEMP TRIGGER question_delete AFTER DELETE ON main.session_question
              BEGIN UPDATE snapshot_revision SET revision=revision+1; END;
            CREATE TEMP TABLE completed_answer_revision (revision INTEGER NOT NULL);
            INSERT INTO completed_answer_revision VALUES (0);
            CREATE TEMP TRIGGER completed_answer_insert AFTER INSERT ON main.session_answer
              BEGIN UPDATE completed_answer_revision SET revision=revision+1; END;
            CREATE TEMP TRIGGER completed_answer_update AFTER UPDATE ON main.session_answer
              BEGIN UPDATE completed_answer_revision SET revision=revision+1; END;
            CREATE TEMP TRIGGER completed_answer_delete AFTER DELETE ON main.session_answer
              BEGIN UPDATE completed_answer_revision SET revision=revision+1; END;
            CREATE TEMP TRIGGER completed_word_insert AFTER INSERT ON main.word_spelling_attempt
              BEGIN UPDATE completed_answer_revision SET revision=revision+1; END;
            CREATE TEMP TRIGGER completed_word_update AFTER UPDATE ON main.word_spelling_attempt
              BEGIN UPDATE completed_answer_revision SET revision=revision+1; END;
            CREATE TEMP TRIGGER completed_word_delete AFTER DELETE ON main.word_spelling_attempt
              BEGIN UPDATE completed_answer_revision SET revision=revision+1; END;
        """)
        self._snapshot_cache = None
        from .session_snapshot_cache import SnapshotReader, install_journal
        install_journal(self._db)
        self._snapshot_reader = SnapshotReader(self._db)
        self._completed_answer_cache = None

    def close(self):
        self._db.close()

    @property
    def queries(self):
        """Read projections over this store's connection, without cached data."""
        from .study_queries import StudyQueries
        return StudyQueries(self._db)

    @property
    def teaching_queries(self):
        """First-teaching evidence on this connection, including uncommitted rows."""
        from .teaching_queries import TeachingQueries
        return TeachingQueries(self._db)

    @property
    def in_transaction(self) -> bool:
        """Whether readers must bypass caches to see this connection's writes."""
        return self._db.in_transaction

    def read_revision(self) -> tuple[int, int]:
        """Local changes (including rollbacks) plus commits by other connections."""
        return self._db.total_changes, self._db.execute("PRAGMA data_version").fetchone()[0]

    def clear_learning_data(self) -> dict:
        """Clear this learner's progress while retaining preferences and files."""
        from .learning_reset import clear_learning_data
        return clear_learning_data(self)

    def get_setting(self, key: str, default=None):
        row = self._db.execute("SELECT value_json FROM app_setting WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key: str, value):
        if not isinstance(key, str) or not key or key == "scheduler":
            raise ValueError("A non-reserved setting key is required")
        if key == CONTENT_CONTROLS_SETTING:
            from .progress_backup import validate_content_controls
            value = validate_content_controls(value)
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        with self._db:
            self._db.execute("INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (key, encoded))

    def get_content_control(self, content_id: str, now: datetime | None = None) -> dict:
        if not isinstance(content_id, str) or not content_id.strip():
            raise ValueError("content_id is required")
        controls = self.get_setting(CONTENT_CONTROLS_SETTING, {})
        return dict(controls.get(content_id, {"suspended": False, "buried_until_utc": None}))

    def is_content_available(self, content_id: str, now: datetime | None = None) -> bool:
        control = self.get_content_control(content_id)
        until = control["buried_until_utc"]
        return not control["suspended"] and (until is None or datetime.fromisoformat(until) <= _utc(now))

    def set_content_control(self, content_id: str, action: str | None = None, *,
                            suspended: bool | None = None, buried_until_utc=None,
                            bury_today: bool = False, clear_buried: bool = False,
                            now: datetime | None = None) -> dict:
        """Pause or defer an entire item without changing any FSRS evidence."""
        now = _utc(now)
        control = self.get_content_control(content_id)
        if action is not None:
            if action not in {"suspend", "resume", "bury", "unbury"}:
                raise ValueError("Unknown content control action")
            if action in {"suspend", "resume"}:
                suspended = action == "suspend"
            else:
                bury_today, clear_buried = action == "bury", action == "unbury"
        if suspended is not None:
            if type(suspended) is not bool:
                raise ValueError("suspended must be a boolean")
            control["suspended"] = suspended
            if suspended:
                control["buried_until_utc"] = None
        if type(bury_today) is not bool or type(clear_buried) is not bool:
            raise ValueError("Content control flags must be booleans")
        if clear_buried:
            control["buried_until_utc"] = None
        elif bury_today:
            control["buried_until_utc"] = self._day_bounds(now)[1]
        elif buried_until_utc is not None:
            parsed = datetime.fromisoformat(buried_until_utc) if isinstance(buried_until_utc, str) else buried_until_utc
            if not isinstance(parsed, datetime):
                raise ValueError("buried_until_utc must be a timezone-aware datetime")
            control["buried_until_utc"] = _utc(parsed).isoformat()
        controls = self.get_setting(CONTENT_CONTROLS_SETTING, {})
        controls[content_id] = control
        self.set_setting(CONTENT_CONTROLS_SETTING, controls)
        if suspended is False:
            # An explicit item-level resume also revives legacy per-direction
            # pauses; their actual FSRS state and due dates remain untouched.
            with self._db:
                self._db.execute("UPDATE study_card SET suspended=0 WHERE content_id=?", (content_id,))
        return dict(control)

    def _unavailable_content_ids(self, now: datetime | None = None) -> set[str]:
        now = _utc(now)
        return {content_id for content_id, control in self.get_setting(CONTENT_CONTROLS_SETTING, {}).items()
                if control["suspended"] or (control["buried_until_utc"] is not None
                    and datetime.fromisoformat(control["buried_until_utc"]) > now)}

    def reload_daily_new_limit(self):
        # Preserve old settings/backup round trips without applying a quota.
        value = self.get_setting("daily_new_limit")
        self.daily_new_limit = value if type(value) is int and value > 0 else self._default_daily_new_limit

    def set_daily_new_limit(self, value: int):
        """Compatibility setter for a historical setting, never a live limit."""
        if type(value) is not int or value <= 0:
            raise ValueError("daily_new_limit must be a positive integer")
        self.set_setting("daily_new_limit", value)
        self.daily_new_limit = value

    def _session_snapshot(self, session_id: str) -> list[dict]:
        """Private cached JSON payload; callers must never mutate its children."""
        token = (self._db.execute("PRAGMA data_version").fetchone()[0],
                 self._db.execute("SELECT revision FROM snapshot_revision").fetchone()[0])
        cached = self._snapshot_cache
        if cached is not None and cached[0] == session_id and cached[1] == token:
            return cached[2]
        items = self._snapshot_reader.read(session_id)[1]
        if not self._db.in_transaction:
            self._snapshot_cache = (session_id, token, items)
        return items

    def session_question_headers(self, session_id: str) -> tuple[tuple[str, str, str], ...]:
        """Return (content_id, kind, script), including the completed prefix.

        Uses the revision-checked immutable source; never hydrates Questions,
        filters disabled directions, or exposes mutable cached payloads.
        """
        return tuple((q["content_id"], q["kind"], q["script"])
                     for q in self._session_snapshot(session_id))

    def _snapshot_question_policy(self, items):
        """Share only static checks tied to the revision-guarded raw identity.

        _session_snapshot re-reads SQLite revision/data_version before returning
        its private tree. A transaction's newly decoded temporary tree never
        publishes this derived cache; rollback cannot later reuse its flags.
        """
        cached = getattr(self, "_snapshot_policy_cache", None)
        if cached is not None and cached.items is items:
            return cached
        result = _SnapshotQuestionPolicy(items)
        if not self._db.in_transaction:
            self._snapshot_policy_cache = result
        return result

    @property
    def practice_lane(self):
        return self._practice_lane

    @practice_lane.setter
    def practice_lane(self, lane):
        if not isinstance(lane, str) or lane not in PRACTICE_LANES:
            raise ValueError("Unknown practice lane")
        self._practice_lane = lane

    def _checkpoint_row(self, lane=None):
        lane = self.practice_lane if lane is None else lane
        if not isinstance(lane, str) or lane not in PRACTICE_LANES:
            raise ValueError("Unknown practice lane")
        for row in self._db.execute("SELECT * FROM session_checkpoint ORDER BY updated_at_utc DESC"):
            if self._session_lane(row["session_id"]) == lane:
                return row
        return None

    def _session_lane(self, session_id):
        # Two live lanes must not repeatedly evict the single decoded-snapshot
        # cache just to classify unchanged queues while saving an answer draft.
        token = (self._db.execute("PRAGMA data_version").fetchone()[0],
                 self._db.execute("SELECT revision FROM snapshot_revision").fetchone()[0])
        cache = getattr(self, "_lane_cache", None)
        if cache is None or cache[0] != token:
            cache = self._lane_cache = (token, {})
        if session_id not in cache[1]:
            cache[1][session_id] = session_practice_lane(self._session_snapshot(session_id))
        return cache[1][session_id]

    def _completed_session_answers(self, session_id: str) -> tuple:
        """Reuse committed answer totals while each stroke saves its draft.

        TEMP triggers cover direct SQL/restore writes and roll back with them;
        data_version covers commits by other connections. Never read or publish
        this cache in a transaction: a rolled-back revision can later be reused
        by a different write. Keep at most the two live practice lanes cached.
        """
        cacheable = not self._db.in_transaction
        token = None
        if cacheable:
            token = (self._db.execute("PRAGMA data_version").fetchone()[0],
                     self._db.execute("SELECT revision FROM completed_answer_revision").fetchone()[0])
            cached = self._completed_answer_cache
            if cached is not None and cached[0] == token and session_id in cached[1]:
                return cached[1][session_id]
        completed = tuple(self._db.execute("""SELECT MAX(question_index),SUM(correct) FROM (
            SELECT question_index,correct FROM session_answer WHERE session_id=? UNION ALL
            SELECT question_index,correct FROM word_spelling_attempt WHERE session_id=?)""",
            (session_id, session_id)).fetchone())
        if cacheable:
            cached = self._completed_answer_cache
            if cached is None or cached[0] != token:
                cached = self._completed_answer_cache = (token, {})
            if session_id not in cached[1] and len(cached[1]) >= 2:
                del cached[1][next(iter(cached[1]))]
            cached[1][session_id] = completed
        return completed

    def save_session_progress(self, session_id: str, question_index: int,
                              session_right: int = 0, assisted_indices=(), state=None, *, question_fingerprint=None):
        """Save one unfinished session per lane; committed answers cannot replay."""
        def revision():
            return (self._db.execute('PRAGMA data_version').fetchone()[0],
                    self._db.execute('SELECT revision FROM snapshot_revision').fetchone()[0],
                    self._db.execute('SELECT revision FROM completed_answer_revision').fetchone()[0])
        before = revision()
        questions = self._session_snapshot(session_id)
        lane = self._session_lane(session_id)
        count = len(questions)
        if type(question_index) is not int or not 0 <= question_index <= count:
            raise ValueError("Invalid question position")
        if type(session_right) is not int or not 0 <= session_right <= question_index:
            raise ValueError("Invalid correct-answer count")
        assisted = sorted(set(assisted_indices))
        if any(type(index) is not int or not 0 <= index < count for index in assisted):
            raise ValueError("Invalid assisted question index")
        from .progress_backup import validate_session_state
        state = validate_session_state(state or {})
        if question_index < count and state.get('page') not in {'session_wait', 'dependency_wait'}:
            from .session_content import assert_current
            assert_current(self, session_id, question_index, questions[question_index], question_fingerprint)
        completed = self._completed_session_answers(session_id)
        if completed[0] is not None and question_index <= completed[0]:
            question_index, session_right, state = completed[0] + 1, completed[1], {}
        if question_index < count:
            validate_session_state(state, questions[question_index])
        with self._db:
            if not self._db.in_transaction:
                self._db.execute('BEGIN IMMEDIATE')
            # Do not let a content update or queue append between the read and
            # write overwrite the replacement's checkpoint with an old page.
            if revision() != before:
                raise ValueError('练习队列已更新，请重新打开当前练习。')
            previous = self._db.execute("SELECT question_index,state_json,assisted_indices_json FROM session_checkpoint WHERE session_id=?", (session_id,)).fetchone()
            if previous is not None:
                # Explicitly viewed help cannot be erased by a retained page's
                # older in-memory checkpoint after returning from feedback.
                assisted = sorted(set(assisted) | set(json.loads(previous['assisted_indices_json'])))
            if (previous is not None and previous["question_index"] == question_index
                    and json.loads(previous["state_json"]).get("word_used_conversion") is True):
                state = {**state, "word_used_conversion": True}
            obsolete = [row for row in self._db.execute("SELECT session_id,question_index FROM session_checkpoint")
                        if self._session_lane(row["session_id"]) == lane]
            boundary_changed = (len(obsolete) != 1 or obsolete[0]["session_id"] != session_id
                                or obsolete[0]["question_index"] != question_index or question_index >= count)
            self._db.executemany("DELETE FROM session_checkpoint WHERE session_id=?", [(row["session_id"],) for row in obsolete])
            if question_index < count:
                self._db.execute("INSERT INTO session_checkpoint VALUES (?,?,?,?,?,?)", (
                    session_id, utc_now().isoformat(), question_index, session_right,
                    json.dumps(assisted), json.dumps(state, ensure_ascii=False, allow_nan=False)))
            if boundary_changed:
                from .kana_ability_bridge import maintain_bridges
                maintain_bridges(self)

    def mark_pending_word_conversion(self):
        """External input-tool exposure is sticky for unsubmitted word heads."""
        changed = []
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            for row in self._db.execute("SELECT * FROM session_checkpoint").fetchall():
                original = self._session_snapshot(row["session_id"])
                revised = None
                for index in range(row["question_index"], len(original)):
                    question = original[index]
                    marker = question.get("metadata", {}).get(WORD_KIND_PAUSE)
                    draft = marker.get("state", {}) if marker else {}
                    if (question["kind"] in {"word_kana", "word_meaning_kana"}
                            and "word_answer" in draft and not draft.get("feedback_pending")
                            and not draft.get("word_used_conversion")):
                        if revised is None:
                            revised = list(original)
                        revised[index] = deepcopy(question)
                        revised[index]["metadata"][WORD_KIND_PAUSE]["state"]["word_used_conversion"] = True
                        changed.append((row["session_id"], index))
                if revised is not None:
                    session_questions.replace(self._db, row["session_id"], revised, reader=self._snapshot_reader)
                state = json.loads(row["state_json"])
                if state.get("feedback_pending") or "word_answer" not in state:
                    continue
                question = original[row["question_index"]]
                if question["kind"] not in {"word_kana", "word_meaning_kana"}:
                    continue
                if not state.get("word_used_conversion"):
                    state["word_used_conversion"] = True
                    self._db.execute("UPDATE session_checkpoint SET state_json=? WHERE session_id=?",
                        (json.dumps(state, ensure_ascii=False), row["session_id"]))
                changed.append((row["session_id"], row["question_index"]))
        return changed

    def load_session_progress(self, lane=None, *, lazy_questions=False):
        """Read this lane without changing the selected lane or either queue."""
        row = self._checkpoint_row(lane)
        if row is None:
            return None
        result = dict(row)
        result["questions"] = self.get_session_questions(result["session_id"], lazy=lazy_questions)
        result["assisted_indices"] = json.loads(result.pop("assisted_indices_json"))
        result["state"] = json.loads(result.pop("state_json"))
        return result

    def get_session_questions(self, session_id: str, *, lazy=False) -> list[Question] | Sequence:
        items = self._session_snapshot(session_id)
        if lazy:
            return _QuestionSequence(items, self._snapshot_question_policy(items))
        cached = getattr(self, "_question_copy_cache", None)
        # Identity follows the existing revision/data-version cache, including
        # external writes and transaction rollback. No learner data is cached
        # independently of its immutable source snapshot.
        if cached is None or cached[0] is not items:
            plans = []
            for index, item in enumerate(items):
                if (cached is not None and index < len(cached[0]) and item == cached[0][index]
                        and _same_json_scalar_types(item, cached[0][index])):
                    # Appending/reordering the live tail must not recompile
                    # every historical row. Compare the whole saved payload:
                    # matching identities alone could retain stale options.
                    plans.append(cached[1][index])
                else:
                    # This template never escapes. Its immutable source tree
                    # is already private; copying it here and again at the
                    # public boundary needlessly doubles the traversal.
                    question = _question_from_snapshot(item, copy_metadata=False)
                    plans.append((question, _json_copy_plan(question.metadata)))
            cached = self._question_copy_cache = (items, plans)
        return [Question(q.content_id, q.kind, q.script, q.prompt, q.answer,
                         q.options.copy(), q.audio_text, q.accepted_answers, copy_metadata())
                for q, copy_metadata in cached[1]]

    def feedback_wait_times(self, identities, now: datetime | None = None) -> dict:
        """Queue-only exposure delays shared by a kana's retrieval directions.

        Reading feedback much later cannot postpone the real FSRS due, but
        also cannot immediately become a fresh recall. Derive this brief wait
        from immutable evidence so it survives new queues, toggles and backup.
        Legacy events without a finalization time keep their existing timing.
        """
        now = _utc(now)
        groups = {}
        for identity in identities:
            if identity[1] not in WORD_SKILLS:
                groups.setdefault((identity[0], identity[2]), []).append(tuple(identity))
        result, ordered = {}, list(dict.fromkeys(cid for cid, _ in groups))
        # Bound SQLite parameters for older Android SQLite builds. The card
        # index avoids scanning a growing history whenever a queue is read.
        for offset in range(0, len(ordered), 900):
            batch = ordered[offset:offset + 900]
            placeholders = ",".join("?" for _ in batch)
            rows = self._db.execute(f"""SELECT c.content_id,c.script,e.review_log_json
                FROM study_card c JOIN review_event e ON e.rowid=(
                    SELECT MAX(previous.rowid) FROM review_event previous
                    WHERE previous.card_id=c.card_id
                    AND instr(previous.review_log_json, '"finalized_at_utc"')>0)
                WHERE c.content_id IN ({placeholders})
                AND c.skill NOT IN ('word_kana','word_meaning_kana','word_romaji')""", batch)
            for row in rows:
                finalized = json.loads(row["review_log_json"]).get("finalized_at_utc")
                if finalized is None:
                    continue
                until = _utc(datetime.fromisoformat(finalized)) + timedelta(seconds=60)
                if until > now:
                    for identity in groups.get((row["content_id"], row["script"]), []):
                        result[identity] = max(result.get(identity, until), until)
        return result

    def reconcile_session_kinds(self, session_id: str, allowed_kinds,
                                *, now: datetime | None = None, lazy_questions=False) -> dict | None:
        """Apply enabled directions without altering real review evidence.

        Skipped rows join the history prefix, so committed answer indices stay
        valid. Re-enabling may restore a skipped scheduled repetition, but never
        an ungraded introductory question or a superseded learning step.
        Surviving rows keep their exact payload and relative order, including
        timed repetitions. Unscored auxiliary words have no scheduled card from
        which to reconstruct a skipped row, so keep them and their drafts in the
        live suffix while that question kind is disabled.
        """
        if isinstance(allowed_kinds, (str, bytes)):
            raise ValueError("A non-empty collection of practice kinds is required")
        try:
            allowed = set(allowed_kinds)
        except TypeError as exc:
            raise ValueError("A non-empty collection of practice kinds is required") from exc
        if not allowed or not allowed <= SKILLS:
            raise ValueError("Choose at least one known practice kind")
        now = _utc(now)
        if self._db.in_transaction:
            raise RuntimeError("Cannot reconcile practice kinds inside an active transaction")
        try:
            self._db.execute("BEGIN IMMEDIATE")
            pending = self.load_session_progress(lazy_questions=lazy_questions)
            if pending is None or pending["session_id"] != session_id:
                self._db.commit()
                return None
            index = pending["question_index"]
            # Reorder the raw snapshot instead of reserializing Question
            # objects: legacy accepted-answer encodings remain untouched too.
            original = self._session_snapshot(session_id)
            policy = self._snapshot_question_policy(original)
            remaining = list(enumerate(original[index:], start=index))
            paused, active = [], []
            changed_payload = False
            for old, item in remaining:
                from .session_content import retired
                if retired(item):
                    paused.append((old, item))
                    continue
                metadata = item.get("metadata", {})
                valid_word = (item["kind"] in WORD_SKILLS and policy.allowed(old)
                              and not metadata.get("not_before_utc"))
                marker = metadata.get(WORD_KIND_PAUSE)
                if valid_word and item["kind"] not in allowed:
                    changed = deepcopy(item)
                    changed.setdefault("metadata", {})[WORD_KIND_PAUSE] = {
                        "version": 1, "disabled": True,
                        "state": deepcopy(marker["state"] if marker else pending["state"] if old == index else {})}
                    paused.append((old, changed))
                    changed_payload |= changed != item
                elif valid_word and marker is not None:
                    changed = deepcopy(item)
                    if marker["state"]:
                        changed["metadata"][WORD_KIND_PAUSE]["disabled"] = False
                    else:
                        changed["metadata"].pop(WORD_KIND_PAUSE)
                    active.append((old, changed))
                    changed_payload |= changed != item
                elif marker is not None:
                    # Retirement still consumes the obsolete question; its
                    # temporary settings marker cannot make it resumable.
                    changed = deepcopy(item)
                    changed["metadata"].pop(WORD_KIND_PAUSE)
                    active.append((old, changed))
                    changed_payload = True
                else:
                    active.append((old, item))
            def retained(old, item):
                from .kana_question_policy import preserved_retired_question
                permitted = policy.allowed(old) or (old == index and preserved_retired_question(item, pending["state"]))
                return item["kind"] in allowed and permitted and not (item["kind"] in WORD_SKILLS
                    and item.get("metadata", {}).get("not_before_utc"))
            skipped = [(old, item) for old, item in active if not retained(old, item)]
            surviving = [(old, item) for old, item in active if retained(old, item)]
            restored = self._restorable_session_repetitions(session_id, original,
                index, allowed, [item for _, item in surviving], now)
            if not skipped and not restored and not changed_payload:
                self._db.commit()
                return {**pending, "changed": False, "completed": False, "restored_count": 0}
            ordered = [*skipped, *surviving, *paused]
            revised = [*original[:index], *(item for _, item in ordered), *restored]
            remap = {old: index + offset for offset, (old, _) in enumerate(ordered)}
            assisted = sorted(remap.get(old, old) for old in pending["assisted_indices"])
            if restored:
                def learning_step(item):
                    metadata = item.get("metadata", {})
                    return (item["content_id"], item["kind"], item["script"],
                            metadata.get("not_before_utc"), metadata.get("relearning_of_index"))
                hinted_steps = set()
                for old in pending["assisted_indices"]:
                    if old < index:
                        step = learning_step(original[old])
                        if isinstance(step[3], str) and type(step[4]) is int:
                            hinted_steps.add(step)
                # Several archived copies may represent this same scheduled
                # step. A hint on any copy still applies after another toggle.
                assisted.extend(len(original) + offset for offset, item in enumerate(restored)
                                if learning_step(item) in hinted_steps)
            new_index = index + len(skipped)
            current_survives = (bool(surviving) and surviving[0][0] == index)
            new_state = pending["state"] if current_survives else {}
            updated_at = now.isoformat()
            if changed_payload or restored or any(old != index + offset for offset, (old, _) in enumerate(ordered)):
                snapshot = json.dumps(revised, ensure_ascii=False, allow_nan=False)
                if restored or changed_payload:
                    from .progress_backup import MAX_BYTES
                    if len(snapshot.encode("utf-8")) > MAX_BYTES:
                        raise ValueError("本次练习记录过大，请结束后重新开始复习。原学习记录与到期时间已保留。")
                session_questions.replace(self._db, session_id, revised, reader=self._snapshot_reader)
            completed = new_index >= len(revised)
            if completed:
                self._db.execute("DELETE FROM session_checkpoint WHERE session_id=?", (session_id,))
            else:
                self._db.execute("""UPDATE session_checkpoint SET question_index=?,assisted_indices_json=?,
                    state_json=CASE WHEN ? THEN state_json ELSE '{}' END,updated_at_utc=? WHERE session_id=?""",
                    (new_index, json.dumps(assisted), current_survives,
                     updated_at, session_id))
            from .kana_ability_bridge import maintain_bridges
            maintain_bridges(self)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        result = {**pending, "questions": (_QuestionSequence(revised) if lazy_questions else
                                         [_question_from_snapshot(item) for item in revised]),
                "question_index": new_index, "assisted_indices": assisted, "state": new_state,
                "updated_at_utc": updated_at, "changed": True, "completed": completed,
                "restored_count": len(restored)}
        return self._activate_word_draft(result, lazy_questions=lazy_questions) if not completed else result

    def _activate_word_draft(self, pending, *, lazy_questions=False, dry_run=False):
        """Move this question's parked draft back only when it becomes the head."""
        index = pending["question_index"]
        if index >= len(pending["questions"]):
            return pending
        question = pending["questions"][index]
        marker = question.metadata.get(WORD_KIND_PAUSE)
        from .kana_context_words import context_active
        if (marker is None or marker["disabled"] or not question_allowed(question)
                or question.metadata.get("not_before_utc") or not context_active(question, self)):
            return pending
        original = (pending["questions"]._items if lazy_questions else
                    [q.to_dict() for q in pending["questions"]] if dry_run else
                    self._session_snapshot(pending["session_id"]))
        revised = list(original)
        revised[index] = deepcopy(original[index])
        revised[index]["metadata"].pop(WORD_KIND_PAUSE)
        state = deepcopy(marker["state"])
        if not dry_run:
            with self._db:
                self._db.execute("BEGIN")
                session_questions.replace(self._db, pending["session_id"], revised, reader=self._snapshot_reader)
                self._db.execute("UPDATE session_checkpoint SET state_json=? WHERE session_id=?",
                    (json.dumps(state, ensure_ascii=False, allow_nan=False), pending["session_id"]))
        return {**pending, "state": state,
                "questions": _QuestionSequence(revised) if lazy_questions else
                             [_question_from_snapshot(item) for item in revised]}

    def _park_invalid_context_draft(self, pending, *, lazy_questions=False, dry_run=False):
        """Keep ink/text with a retired trigger's word, never with its successor."""
        from .kana_context_words import context_active
        index = pending['question_index']
        if index >= len(pending['questions']) or not pending['state']:
            return pending
        question = pending['questions'][index]
        if context_active(question, self) or WORD_KIND_PAUSE in question.metadata:
            return pending
        # A committed answer/result stays visible. Only an unsubmitted draft
        # needs parking, and the original question remains in the suffix.
        if self._db.execute('SELECT 1 FROM session_answer WHERE session_id=? AND question_index=?',
                            (pending['session_id'], index)).fetchone():
            return pending
        original = pending['questions']._items if lazy_questions else [q.to_dict() for q in pending['questions']]
        revised = list(original); revised[index] = deepcopy(original[index])
        revised[index]['metadata'][WORD_KIND_PAUSE] = {'version': 1, 'disabled': False, 'state': deepcopy(pending['state'])}
        if not dry_run:
            with self._db:
                self._db.execute('BEGIN')
                session_questions.replace(self._db, pending['session_id'], revised, reader=self._snapshot_reader)
                self._db.execute("UPDATE session_checkpoint SET state_json='{}' WHERE session_id=?", (pending['session_id'],))
        return {**pending, 'state': {}, 'questions': _QuestionSequence(revised) if lazy_questions else
                [_question_from_snapshot(item) for item in revised]}

    def _restorable_session_repetitions(self, session_id, original, index,
                                        allowed, surviving, now):
        """Recover the current learned step once, preserving its original cue.

        A skipped copy stays in the immutable prefix; a live copy goes at the
        tail. Its parent must be the card's latest actual review, which prevents
        a later toggle from reviving an answered or superseded repetition.
        """
        policy = self._snapshot_question_policy(original)
        candidates = [(old, item) for old, item in enumerate(original[:index])
            if item["kind"] in allowed and item["kind"] not in WORD_SKILLS
            and policy.allowed(old)
            and item.get("metadata", {}).get("not_before_utc")]
        if not candidates:
            return []
        answers = {row["question_index"]: (row["event_id"], row["card_id"])
            for row in self._db.execute("""SELECT a.question_index,a.event_id,e.card_id
                FROM session_answer a JOIN review_event e USING(event_id)
                WHERE a.session_id=?""", (session_id,))}
        unavailable = self._unavailable_content_ids(now)
        reserved = {(item["content_id"], item["kind"], item["script"]) for item in surviving}
        restored, cards, latest_reviews = [], {}, {}
        for old, item in candidates:
            key = item["content_id"], item["kind"], item["script"]
            metadata = item["metadata"]
            parent = metadata.get("relearning_of_index")
            if (old in answers or key in reserved or key[0] in unavailable
                    or type(parent) is not int or parent not in answers):
                continue
            if key not in cards:
                cards[key] = self.get_scheduling_card(*key)
            card = cards[key]
            if (card is None or card["suspended"] or card["review_count"] <= 0
                    or card["due_at_utc"] != metadata["not_before_utc"]
                    or answers[parent][1] != card["card_id"]):
                continue
            if key not in latest_reviews:
                latest_reviews[key] = self._db.execute("""SELECT event_id FROM review_event
                    WHERE card_id=? ORDER BY occurred_at_utc DESC,rowid DESC LIMIT 1""",
                    (card["card_id"],)).fetchone()
            latest = latest_reviews[key]
            if latest is None or latest[0] != answers[parent][0]:
                continue
            restored.append(item)
            reserved.add(key)
        return restored

    def queued_content_ids(self, lane=None) -> set[str]:
        """Reserve content in this lane's complete live snapshot, including drafts."""
        row = self._checkpoint_row(lane)
        return {item["content_id"] for item in self._session_snapshot(row["session_id"])
                if question_allowed(item)} if row else set()

    def _session_append_state(self, session_id: str):
        current = self._checkpoint_row()
        row = self._db.execute("SELECT * FROM session_checkpoint WHERE session_id=?", (session_id,)).fetchone()
        if row is None or current is None or current["session_id"] != session_id:
            raise ValueError("原练习队列已改变，请返回后重新添加新批次。")
        # The token covers only this lane's immutable payload and visible draft.
        questions = self._session_snapshot(session_id)
        token = hashlib.sha256(json.dumps([*list(row), questions], ensure_ascii=False,
            separators=(",", ":")).encode("utf-8")).hexdigest()
        return {**dict(row), "questions": questions}, token

    def session_append_token(self, session_id: str) -> str:
        """Capture the queue/draft version before a worker builds an addition."""
        return self._session_append_state(session_id)[1]

    def get_session_summary(self, session_id: str) -> dict:
        count = len(self._session_snapshot(session_id))
        row = self._db.execute("""SELECT COUNT(*) AS answered,COALESCE(SUM(a.correct),0) AS correct,
            COUNT(DISTINCT CASE WHEN c.skill NOT IN ('word_kana','word_meaning_kana','word_romaji') THEN c.content_id END) AS content_count FROM session_answer a
            JOIN review_event e USING(event_id) JOIN study_card c USING(card_id)
            WHERE a.session_id=?""", (session_id,)).fetchone()
        auxiliary = self._db.execute("""SELECT COUNT(*) AS answered,COALESCE(SUM(correct),0) AS correct
            FROM word_spelling_attempt WHERE session_id=?""", (session_id,)).fetchone()
        answered = row["answered"] + auxiliary["answered"]
        return {**dict(row), "answered": answered, "correct": row["correct"] + auxiliary["correct"],
                "total": count, "skipped": count - answered, "auxiliary_count": auxiliary["answered"]}

    def prepare_next_question(self, session_id: str, now: datetime | None = None,
                              *, lazy_questions=False, fair_new=False, teaching_only=False,
                              dry_run=False, teaching_kinds=None, teaching_targets=None,
                              teaching_unassessed=False, prefer_unassessed=False,
                              dependency_graph=None) -> dict | None:
        """Advance past paused items and safely order unseen, timed repetitions.

        The committed prefix and any visible unsubmitted answer remain fixed.
        No skipped question is recorded as an incorrect answer or an FSRS review.
        """
        now = _utc(now)
        pending = self.load_session_progress(lazy_questions=lazy_questions)
        if pending is None or pending["session_id"] != session_id:
            return None
        pending = self._park_invalid_context_draft(pending, lazy_questions=lazy_questions, dry_run=dry_run)
        pending = self._activate_word_draft(pending, lazy_questions=lazy_questions, dry_run=dry_run)
        questions, index = pending["questions"], pending["question_index"]
        state = pending["state"]
        from .review_dispatch import kana_resume_priority, review_priority, reviews_today
        from .kana_learning_intro import intro_wait_times
        intro_due = intro_wait_times(self, questions[index:])
        feedback_due = self.feedback_wait_times(
            ((q.content_id, q.kind, q.script) for q in questions[index:]), now)
        unavailable = self._unavailable_content_ids(now)
        from .kana_routing import active_units
        shared_routing = bool(active_units(self))
        paused_rows = (self.scheduling_cards(aliases=True) if shared_routing else self._db.execute(
            "SELECT content_id,skill,script,suspended FROM study_card WHERE suspended=1"))
        paused_directions = {(row["content_id"], row["skill"], row["script"])
                             for row in paused_rows if row["suspended"]}
        def is_paused(question):
            from .session_content import retired
            if retired(question): return False
            from .kana_question_policy import preserved_retired_question
            preserved_retired = question is questions[index] and preserved_retired_question(question, state)
            if not question_allowed(question) and not preserved_retired:
                return True
            if question.kind in WORD_SKILLS:
                return bool(question.metadata.get("not_before_utc"))
            return question.content_id in unavailable or (question.content_id, question.kind, question.script) in paused_directions
        def kind_paused(question):
            from .kana_context_words import context_active
            from .session_content import retired
            return (bool(question.metadata.get(WORD_KIND_PAUSE, {}).get("disabled"))
                    or retired(question) or not context_active(question, self)) and not is_paused(question)
        if index >= len(questions):
            if not dry_run and not teaching_only:
                self.clear_session_progress()
            return None
        if kana_resume_priority(state) is not None and not is_paused(questions[index]) and not kind_paused(questions[index]):
            return None if teaching_only else {**pending, "waiting_until_utc": None}
        from .kana_dependency_runtime import (
            kana_dependency_graph, kana_dependency_state, kana_prerequisite_status)
        graph = kana_dependency_graph(graph=dependency_graph)
        # Read evidence once for the whole suffix. Neither introductions nor
        # merely reserving a parent can release its unseen dependent question.
        dependency_state = kana_dependency_state(self)
        dependency_statuses = {}
        def dependency_status(question):
            key = (question.content_id, question.kind, question.script)
            if key not in dependency_statuses:
                dependency_statuses[key] = kana_prerequisite_status(question, graph, dependency_state)
            return dependency_statuses[key]
        spaced = any(q.metadata.get("practice_config", {}).get("schedule_mode") == "spaced"
                     for q in questions[index:])
        due_rows = ((self.scheduling_cards(aliases=True) if shared_routing else self._db.execute(
                    "SELECT * FROM study_card WHERE review_count>0 AND suspended=0 AND due_at_utc<=?", (now.isoformat(),))) if spaced else ())
        due_cards = ({(row["content_id"], row["skill"], row["script"]): row
                      for row in due_rows
                      if row["review_count"] > 0 and not row["suspended"] and row["due_at_utc"] <= now.isoformat()} if spaced else {})
        review_day, daily_counts = reviews_today(self, now) if due_cards else (None, {})
        learned_scripts = ({tuple(row) for row in self._db.execute(
            "SELECT DISTINCT content_id,script FROM study_card WHERE review_count>0")} if spaced else set())
        if (not teaching_only and not fair_new and not state and not unavailable and not paused_directions
                and all(question_allowed(q) for q in questions[index:])
                and not due_cards
                and not any(question.metadata.get("not_before_utc") for question in questions[index:])
                and not any(kind_paused(question) for question in questions[index:])
                and not any(due > now for due in intro_due.values())
                and not feedback_due
                and not any(dependency_status(q)["blocked"] for q in questions[index:])
                and all(left.content_id != right.content_id
                        for left, right in zip(questions[max(0, index - 1):], questions[max(1, index):]))):
            # The original interleaved queue is already ready and correctly
            # spaced. Do not heap-sort or rewrite thousands of unchanged rows.
            return {**pending, "waiting_until_utc": None}
        # Once a suspended visible item is skipped, its transient text/ink must
        # not be attached to a different question at this same queue position.
        indexed = list(enumerate(questions[index:], start=index))
        parked = [(old, question) for old, question in indexed if kind_paused(question)]
        usable = [(old, question) for old, question in indexed if not is_paused(question) and not kind_paused(question)]
        skipped = [(old, question) for old, question in indexed if is_paused(question)]
        def available_at(question):
            value = question.metadata.get("not_before_utc")
            due = datetime.fromisoformat(value) if value else now
            # First-exposure delay is not an already-scored FSRS repetition.
            # It has no card to validate and must not receive a review priority.
            teaching_due = (intro_due.get((question.content_id, question.script), now)
                            if question.metadata.get("kana_intro_v1") is True else now)
            return max(due, teaching_due, feedback_due.get(
                (question.content_id, question.kind, question.script), now))
        blocked = [(old, question) for old, question in usable if dependency_status(question)["blocked"]]
        admitted = [(old, question) for old, question in usable if not dependency_status(question)["blocked"]]
        blocked_prerequisites = tuple(dict.fromkeys(dependency
            for _, question in blocked for dependency in dependency_status(question)["missing"]))
        ready = [(old, question) for old, question in admitted if available_at(question) <= now]
        future = sorted(((old, question) for old, question in admitted if available_at(question) > now),
                        key=lambda item: available_at(item[1]))
        # A minute-long relearning step must not wait behind a large remaining
        # batch. Keep one heap head per content, so a sibling can be spaced away
        # without assigning invented weights to different retrieval directions.
        ready_by_content = {}
        from .new_dispatch import read as dispatch_cursor, band_order
        cursor = dispatch_cursor(self._db) if fair_new else None
        def priority(item):
            old, question = item
            from .kana_context_words import context_priority
            context_rank = context_priority(question, self)
            if spaced:
                if context_rank is not None:
                    return (*context_rank, old)
                card = due_cards.get((question.content_id, question.kind, question.script))
                rank = review_priority(card, now=now, day_start=review_day,
                    reviewed_today=daily_counts.get(card["card_id"], 0) if card else 0)
                if (not fair_new and rank[0] != 2 and question.metadata.get("practice_config", {}).get("adaptive")
                        and "study_path_entry" not in question.metadata
                        and "study_path_due_v1" not in question.metadata
                        and (question.content_id, question.script) in learned_scripts):
                    # The adaptive plan intentionally interleaves retrieval
                    # directions of already learned content. Treat that plan
                    # as review work, not fresh content; otherwise each visit
                    # recreates an all-choice block and hides harder forms.
                    # Curriculum queues grow continuously: their actual due
                    # cards and first independent directions retain the same
                    # review_priority used by the shared course dispatcher.
                    rank = (3, 0)
                if fair_new and rank[0] >= 4 and question.kind not in WORD_SKILLS:
                    return (4, band_order(cursor, question.kind), old)
                return (*rank, old)
            repeated = bool(question.metadata.get("not_before_utc"))
            return (0 if repeated else 1, available_at(question) if repeated else now, old)
        for item in sorted(ready, key=priority):
            ready_by_content.setdefault(item[1].content_id, deque()).append(item)
        heap = [(priority(items[0]), content_id) for content_id, items in ready_by_content.items()]
        heapq.heapify(heap)
        ready = []
        previous_content = questions[index - 1].content_id if index else None
        while heap:
            selected = heapq.heappop(heap)
            if (selected[1] == previous_content and heap
                    and (not fair_new
                         or (selected[0][0] < 4 and heap[0][0][0] < 4)
                         or heap[0][0][:2] == selected[0][:2])):
                alternative = heapq.heappop(heap)
                heapq.heappush(heap, selected)
                selected = alternative
            content_id = selected[1]
            ready.append(ready_by_content[content_id].popleft())
            previous_content = content_id
            if ready_by_content[content_id]:
                heapq.heappush(heap, (priority(ready_by_content[content_id][0]), content_id))
        teaching_trace = {}
        if teaching_only:
            from types import SimpleNamespace
            from .kana_learning_intro import should_introduce
            allowed = set(teaching_kinds) if teaching_kinds is not None else None
            targets = set(teaching_targets) if teaching_targets is not None else None
            eligible = [item for item in ready if allowed is None or item[1].kind in allowed]
            exact_directions = {target for target in targets or () if len(target) == 3}
            unseen = set()
            if teaching_unassessed or exact_directions:
                from .first_teaching_kana import unassessed_directions
                unseen = unassessed_directions(self, (item[1] for item in eligible), now)
            def identity(item):
                return item[1].content_id,item[1].kind,item[1].script
            if targets is not None:
                introductions = [item for item in eligible
                    if (item[1].content_id,item[1].script) in targets
                    or identity(item) in exact_directions & unseen]
            else:
                fresh = [item for item in eligible if should_introduce(SimpleNamespace(store=self),item[1])]
                additional = [item for item in eligible if identity(item) in unseen]
                introductions = [*additional,*fresh] if prefer_unassessed else [*fresh,*additional]
                if teaching_unassessed and introductions:
                    pool = 'additional' if identity(introductions[0]) in unseen else 'introduction'
                    teaching_trace = {'selected_kana_pool':pool,'kana_service_candidates':{
                        name:{'content_id':items[0][1].content_id,'kind':items[0][1].kind,'script':items[0][1].script}
                        for name,items in (('introduction',fresh),('additional',additional)) if items}}
            if not introductions:
                return None
            initial_positions = {item[0] for item in introductions}
            ready = [*introductions, *(item for item in ready if item[0] not in initial_positions)]
        # Keep skipped positions in the immutable history prefix. New session
        # indices are authoritative only for untouched, still-unanswered rows.
        # A dependency-blocked question remains unfinished. Unlike a disabled
        # direction it must never be consumed into the skipped history prefix.
        ordered = [*skipped, *ready, *future, *blocked, *parked]
        remap = {old: index + offset for offset, (old, _) in enumerate(ordered)}
        retired_markers = {old for old, question in skipped if WORD_KIND_PAUSE in question.metadata}
        if lazy_questions:
            # Keep the exact snapshot loaded at this dispatch's start, just
            # like the eager list, if another connection writes meanwhile.
            original = questions._items
            def stored_row(old):
                if old not in retired_markers:
                    return original[old]
                item = deepcopy(original[old])
                item["metadata"].pop(WORD_KIND_PAUSE)
                return item
            revised = _QuestionSequence([*original[:index], *(stored_row(old) for old, _ in ordered)])
        else:
            revised = [*questions[:index], *(replace(question, metadata={
                key: value for key, value in question.metadata.items() if key != WORD_KIND_PAUSE})
                if old in retired_markers else question for old, question in ordered)]
        changed = bool(retired_markers) or any(old != index + offset for offset, (old, _) in enumerate(ordered))
        new_index = index + len(skipped)
        assisted = [remap.get(old, old) for old in pending["assisted_indices"]]
        if new_index >= len(revised):
            if not dry_run:
                with self._db:
                    if changed:
                        self._db.execute("BEGIN")
                        session_questions.replace(self._db, session_id,
                            revised._storage_rows() if lazy_questions else [vars(q) for q in revised], reader=self._snapshot_reader)
                    self._db.execute("DELETE FROM session_checkpoint WHERE session_id=?", (session_id,))
                    from .kana_ability_bridge import maintain_bridges
                    maintain_bridges(self)
            return None
        wait_until = available_at(future[0][1]) if not ready and future else None
        dependency_blocked = bool(blocked and not ready and not future)
        settings_blocked = bool(parked and not ready and not future and not blocked)
        from .session_content import retired
        content_blocked = bool(settings_blocked and any(retired(q) for _, q in parked))
        new_state = ({"page": "session_wait"} if settings_blocked else
                     {"page": "dependency_wait"} if dependency_blocked
                     else {"page": "session_wait"} if wait_until else {})
        if not dry_run:
            with self._db:
                if changed:
                    self._db.execute("BEGIN")
                    session_questions.replace(self._db, session_id,
                        revised._storage_rows() if lazy_questions else [vars(q) for q in revised], reader=self._snapshot_reader)
                self._db.execute("UPDATE session_checkpoint SET question_index=?,assisted_indices_json=?,state_json=?,updated_at_utc=? WHERE session_id=?",
                    (new_index, json.dumps(sorted(assisted)), json.dumps(new_state), now.isoformat(), session_id))
                if changed or new_index != index:
                    from .kana_ability_bridge import maintain_bridges
                    maintain_bridges(self)
        result = {**pending, **teaching_trace, "questions": revised, "question_index": new_index,
                "assisted_indices": sorted(assisted), "state": new_state,
                "waiting_until_utc": wait_until.isoformat() if wait_until else None,
                 "settings_blocked": settings_blocked,
                "content_blocked": content_blocked,
                "dependency_blocked": dependency_blocked,
                "blocked_prerequisites": blocked_prerequisites}
        return self._activate_word_draft(result, lazy_questions=lazy_questions, dry_run=dry_run)

    def clear_session_progress(self, lane=None):
        row = self._checkpoint_row(lane)
        if row is not None:
            with self._db:
                self._db.execute("DELETE FROM session_checkpoint WHERE session_id=?", (row["session_id"],))
                from .kana_ability_bridge import maintain_bridges
                maintain_bridges(self)

    def reset_choice_history(self):
        # Derived from immutable session snapshots, never a separate learner
        # record. Imports invalidate it; later reads only scan new sessions.
        self._choice_history = {}
        self._choice_history_rowid = 0
        self._choice_history_version = self._db.execute("PRAGMA data_version").fetchone()[0]

    def previous_choice_options(self, kind: str, script: str) -> dict[str, list[str]]:
        version = self._db.execute("PRAGMA data_version").fetchone()[0]
        if version != self._choice_history_version:
            self.reset_choice_history()
        for row in self._db.execute(
                "SELECT rowid,session_id FROM study_session WHERE rowid>? ORDER BY rowid",
                (self._choice_history_rowid,)):
            for item in session_questions.iter_rows(self._db, row["session_id"]):
                if item["kind"] in {"kana_choice", "romaji_kana"}:
                    key = (item["kind"], item["script"], item["content_id"])
                    self._choice_history[key] = tuple(item.get("options", ()))
            self._choice_history_rowid = row["rowid"]
        return {content_id: list(options) for (skill, alphabet, content_id), options in self._choice_history.items()
                if skill == kind and alphabet == script}

    def export_backup(self) -> bytes:
        from .progress_backup import export_backup
        return export_backup(self)

    def inspect_backup(self, data: bytes) -> dict:
        from .progress_backup import inspect_backup
        return inspect_backup(self, data)

    def import_backup(self, data: bytes) -> dict:
        from .progress_backup import import_backup
        return import_backup(self, data)

    def previous_backup(self) -> bytes | None:
        """Return the latest automatic pre-restore copy for user export/undo."""
        from .progress_backup import MAX_BYTES
        candidates = [path for path in self.data_dir.glob("learning-before-restore-*.json")
                      if path.is_file() and not path.is_symlink() and path.resolve().parent == self.data_dir]
        if not candidates:
            return None
        path = max(candidates, key=lambda item: (item.stat().st_mtime_ns, item.name))
        with path.open("rb") as source:
            data = source.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("恢复前备份超过当前大小限制。")
        return data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @staticmethod
    def _card_key(content_id, skill, script):
        return hashlib.sha256(json.dumps([content_id, skill, script], ensure_ascii=False).encode()).hexdigest()

    @staticmethod
    def _record(row):
        if row is None:
            return None
        record = dict(row)
        record["scheduler_state"] = json.loads(record["scheduler_state_json"])
        return record

    def get_card(self, content_id: str, skill: str, script: str) -> dict | None:
        _validate_key(content_id, skill, script)
        return self._record(self._db.execute("SELECT * FROM study_card WHERE content_id=? AND skill=? AND script=?", (content_id, skill, script)).fetchone())

    def get_scheduling_card(self, content_id: str, skill: str, script: str) -> dict | None:
        """Current ability authority; get_card retains the original raw history."""
        _validate_key(content_id, skill, script)
        from .kana_routing import unit_for, projected_card
        unit = unit_for(self, content_id, skill, script)
        if unit is None:
            return self.get_card(content_id, skill, script)
        row = self._db.execute("SELECT * FROM study_card WHERE card_id=?", (unit["carrier_card_id"],)).fetchone()
        card = self._record(row)
        if card is None:
            if unit["status"] == "planned":
                return None
            raise ValueError("Kana ability carrier is missing")
        return projected_card(unit, card, skill)

    def scheduling_cards(self, *, aliases=False):
        from .kana_routing import project_cards
        return project_cards(self, aliases=aliases)

    def _day_bounds(self, now: datetime):
        if self._device_local:
            day = now.astimezone().date()
            # Naive astimezone uses each boundary's device-local DST offset.
            return (datetime.combine(day, time.min).astimezone(UTC).isoformat(), datetime.combine(day + timedelta(days=1), time.min).astimezone(UTC).isoformat())
        day = now.astimezone(self.zone).date()
        return (datetime.combine(day, time.min, self.zone).astimezone(UTC).isoformat(), datetime.combine(day + timedelta(days=1), time.min, self.zone).astimezone(UTC).isoformat())

    def stats(self, now: datetime | None = None) -> dict:
        now = _utc(now)
        from .kana_routing import active_units
        total_cards = (sum(card["skill"] not in WORD_SKILLS for card in self.scheduling_cards()) if active_units(self)
                       else self._db.execute("SELECT COUNT(*) FROM study_card WHERE skill NOT IN ('word_kana','word_meaning_kana','word_romaji')").fetchone()[0])
        start, end = self._day_bounds(now)
        introduced = self._db.execute("SELECT COUNT(*) FROM study_card WHERE skill NOT IN ('word_kana','word_meaning_kana','word_romaji') AND introduced_at_utc>=? AND introduced_at_utc<?", (start, end)).fetchone()[0]
        due_cards = self.get_due(now=now)
        due = len(due_cards)
        new_items = self._db.execute("""SELECT COUNT(*) FROM
            (SELECT content_id,MIN(introduced_at_utc) AS first_seen FROM study_card WHERE skill NOT IN ('word_kana','word_meaning_kana','word_romaji') GROUP BY content_id)
            WHERE first_seen>=? AND first_seen<?""", (start, end)).fetchone()[0]
        today = self._db.execute("SELECT COUNT(*) FROM review_event JOIN study_card USING(card_id) WHERE skill NOT IN ('word_kana','word_meaning_kana','word_romaji') AND occurred_at_utc>=? AND occurred_at_utc<?", (start, end)).fetchone()[0]
        return {"new_today": introduced, "new_remaining": None, "daily_new_limit": None,
                "new_items_today": new_items, "due_items": len({card["content_id"] for card in due_cards}),
                "total_items": self._db.execute("SELECT COUNT(DISTINCT content_id) FROM study_card WHERE skill NOT IN ('word_kana','word_meaning_kana','word_romaji')").fetchone()[0],
                "due": due, "due_count": due, "reviewed_today": today, "reviews_today": today,
                "total_reviews": self._db.execute("SELECT COUNT(*) FROM review_event JOIN study_card USING(card_id) WHERE skill NOT IN ('word_kana','word_meaning_kana','word_romaji')").fetchone()[0],
                "total_cards": total_cards,
                "word_exercises_today": self._db.execute("SELECT COUNT(*) FROM word_spelling_attempt WHERE occurred_at_utc>=? AND occurred_at_utc<?", (start, end)).fetchone()[0],
                "skipped_attempts": self._db.execute("SELECT COUNT(*) FROM practice_attempt").fetchone()[0]}

    def get_due(self, skill: str | None = None, script: str | None = None, limit: int | None = None, now: datetime | None = None) -> list[dict]:
        from .kana_routing import active_units
        if active_units(self):
            now_text = _utc(now).isoformat()
            unavailable = self._unavailable_content_ids(now)
            rows = [self._record(card) for card in self.scheduling_cards(aliases=bool(skill))
                    if not card["suspended"] and card["review_count"] > 0 and card["due_at_utc"] <= now_text
                    and card["skill"] not in WORD_SKILLS and card["content_id"] not in unavailable
                    and (not skill or card["skill"] == skill) and (not script or card["script"] == script)
                    and direction_allowed(card["content_id"], card["skill"], card["script"])]
            rows.sort(key=lambda card: (card["due_at_utc"], card["card_id"]))
            return rows if limit is None else rows[:max(0, int(limit))]
        clauses = ["suspended=0", "review_count>0", "due_at_utc<=?", "skill NOT IN ('word_kana','word_meaning_kana','word_romaji')"]
        params = [_utc(now).isoformat()]
        for column, value in (("skill", skill), ("script", script)):
            if value:
                clauses.append(column + "=?")
                params.append(value)
        sql = "SELECT * FROM study_card WHERE " + " AND ".join(clauses) + " ORDER BY due_at_utc,card_id"
        unavailable = self._unavailable_content_ids(now)
        rows = [self._record(row) for row in self._db.execute(sql, params)
                if row["content_id"] not in unavailable
                and direction_allowed(row["content_id"], row["skill"], row["script"])]
        return rows if limit is None else rows[:max(0, int(limit))]

    def due_groups(self, now: datetime | None = None) -> list[dict]:
        groups = {}
        for card in self.get_due(now=now):
            key = card["skill"], card["script"]
            groups[key] = groups.get(key, 0) + 1
        return [{"skill": skill, "script": script, "due": count} for (skill, script), count in sorted(groups.items())]

    def next_due_at(self, content_id: str | None = None, now: datetime | None = None) -> str | None:
        """Earliest actual availability, including a temporary deferral."""
        now = _utc(now)
        controls = self.get_setting(CONTENT_CONTROLS_SETTING, {})
        dates = []
        from .kana_routing import active_units
        cards = self.scheduling_cards() if active_units(self) else self._db.execute(
            "SELECT content_id,skill,script,due_at_utc,suspended,review_count FROM study_card WHERE suspended=0 AND review_count>0 AND skill NOT IN ('word_kana','word_meaning_kana','word_romaji')")
        for card in cards:
            if card["suspended"] or card["review_count"] <= 0 or card["skill"] in WORD_SKILLS:
                continue
            if not direction_allowed(card["content_id"], card["skill"], card["script"]):
                continue
            if content_id is not None and card["content_id"] != content_id:
                continue
            control = controls.get(card["content_id"], {})
            if control.get("suspended"):
                continue
            due = datetime.fromisoformat(card["due_at_utc"])
            until = control.get("buried_until_utc")
            dates.append(max(due, datetime.fromisoformat(until)) if until else due)
        return min(dates).isoformat() if dates else None

    def practice_counts(self, questions: list[Question], now: datetime | None = None) -> dict:
        """Describe an already selected plan without selecting or writing cards."""
        now_text = _utc(now).isoformat()
        from .kana_routing import active_units
        rows = self.scheduling_cards(aliases=True) if active_units(self) else self._db.execute(
            "SELECT content_id,skill,script,due_at_utc,suspended,review_count FROM study_card")
        existing = {(row["content_id"], row["skill"], row["script"]): row for row in rows}
        counts = {"due": 0, "new": 0}
        for question in questions:
            if question.kind in WORD_SKILLS:
                continue
            card = existing.get((question.content_id, question.kind, question.script))
            if card is None:
                counts["new"] += 1
            elif not card["suspended"] and card["review_count"] > 0 and card["due_at_utc"] <= now_text:
                counts["due"] += 1
        return counts

    def batch_counts(self, questions: list[Question], now: datetime | None = None) -> dict:
        """Count memory items once even when their questions repeat or change skill."""
        now_text = _utc(now).isoformat()
        content_ids = {question.content_id for question in questions if question.kind not in WORD_SKILLS}
        pairs = {(question.content_id, question.kind, question.script) for question in questions if question.kind not in WORD_SKILLS}
        existing, due = set(), set()
        from .kana_routing import active_units
        rows = self.scheduling_cards(aliases=True) if active_units(self) else self._db.execute(
            "SELECT content_id,skill,script,due_at_utc,suspended,review_count FROM study_card")
        for card in rows:
            if (card["content_id"], card["skill"], card["script"]) not in pairs:
                continue
            existing.add((card["content_id"], card["skill"], card["script"]))
            if not card["suspended"] and card["review_count"] > 0 and card["due_at_utc"] <= now_text:
                due.add(card["content_id"])
        new = {content_id for content_id, skill, script in pairs
               if (content_id, skill, script) not in existing} - due
        result = {"content_count": len(content_ids), "total": len(questions),
                  "due": len(due), "new": len(new)}
        auxiliary = [question for question in questions if question.kind in WORD_SKILLS]
        if auxiliary:
            result.update(auxiliary_count=len(auxiliary), word_count=len({q.content_id for q in auxiliary}))
        return result

    def start_session(self, questions: list[Question], seed=None, now: datetime | None = None) -> str:
        return self.start_prepared_session(prepare_session_snapshot(questions, seed), now=now)

    def start_prepared_session(self, prepared: PreparedSession, now: datetime | None = None) -> str:
        """Commit a worker's frozen snapshot only after the UI accepts its result."""
        if not isinstance(prepared, PreparedSession):
            raise TypeError("Expected an internally prepared session")
        items = json.loads(prepared.snapshot_json)
        session_practice_lane(items)
        sid = str(uuid4())
        with self._db:
            if not self._db.in_transaction:
                self._db.execute("BEGIN IMMEDIATE")
            from .kana_routing import bind_encoded, reserve_new_units
            reserve_new_units(self, items, sid)
            items, encoded = bind_encoded(self, items, prepared.question_json)
            self._db.execute("INSERT INTO study_session VALUES (?,?,?,?,?)", (sid, _utc(now).isoformat(), self.time_zone,
                prepared.random_seed, prepared.policy_version))
            session_questions.append_encoded(self._db, sid, encoded)
        return sid

    def append_session(self, session_id: str, questions: list[Question], seed=None,
                       *, expected_token: str, distinct_scripts: bool = False,
                       curriculum_directions: bool = False, now: datetime | None = None) -> str:
        return self.append_prepared_session(session_id,
            prepare_session_snapshot(questions, seed), expected_token=expected_token,
            distinct_scripts=distinct_scripts, curriculum_directions=curriculum_directions, now=now)

    def append_prepared_session(self, session_id: str, prepared: PreparedSession,
                                *, expected_token: str, distinct_scripts: bool = False,
                                curriculum_directions: bool = False, now: datetime | None = None) -> str:
        """Atomically extend one unfinished queue without replacing its draft.

        Existing order, scheduled repetitions, progress and FSRS records remain
        byte-for-byte unchanged. Reject stale worker results and content already
        reserved by this queue; repeated directions inside the addition are valid.
        """
        if not isinstance(prepared, PreparedSession):
            raise TypeError("Expected an internally prepared session")
        if not isinstance(expected_token, str) or not expected_token:
            raise ValueError("A queue version is required")
        addition = json.loads(prepared.snapshot_json)
        if not isinstance(addition, list) or not addition:
            raise ValueError("没有可追加的新题目。")
        for item in addition:
            _validate_key(item["content_id"], item["kind"], item["script"])
        try:
            self._db.execute("BEGIN IMMEDIATE")
            current, token = self._session_append_state(session_id)
            if token != expected_token:
                raise ValueError("原练习队列已改变，请返回后重新添加新批次。")
            from .kana_routing import bind_encoded, reserve_new_units
            reserve_new_units(self, addition, session_id)
            addition, encoded = bind_encoded(self, addition, prepared.question_json)
            original = current["questions"]
            session_practice_lane([*original, *addition])
            # A curriculum explicitly teaches the two scripts separately. Its
            # narrow opt-in may append the other script, never a second copy of
            # an already reserved script. Ordinary random batches keep their
            # stricter content-wide reservation policy.
            if type(distinct_scripts) is not bool or distinct_scripts and any(
                    item["kind"] in WORD_SKILLS for item in addition):
                raise ValueError("独立字形追加只适用于假名题目。")
            if type(curriculum_directions) is not bool or curriculum_directions and not distinct_scripts:
                raise ValueError("新方向追加只适用于假名学习路线。")
            identity = (lambda item: (item["content_id"], item["script"])) if distinct_scripts else (lambda item: item["content_id"])
            reserved = {identity(item) for item in original if question_allowed(item)}
            overlaps = [item for item in addition if identity(item) in reserved]
            if overlaps and not curriculum_directions:
                raise ValueError("新批次包含已经在练习队列中的内容，请重新生成。")
            if curriculum_directions:
                # A long-lived curriculum queue may retain months of committed
                # history. Only ungraded directions reserve an unfinished task;
                # new directions can join without replacing its text or ink.
                from .kana_routing import identity as ability_identity
                direction = lambda item: ability_identity(self, item["content_id"], item["kind"], item["script"])
                active = {direction(item) for item in original[current["question_index"]:]
                          if question_allowed(item)}
                added_directions = [direction(item) for item in addition]
                if len(added_directions) != len(set(added_directions)) or active.intersection(added_directions):
                    raise ValueError("新批次包含尚未完成的假名方向。")
                dependency_repairs = None
                for item in overlaps:
                    metadata = item.get("metadata", {})
                    card = self.get_scheduling_card(item["content_id"], item["kind"], item["script"])
                    new_direction = (card is None and metadata.get("study_path_entry")
                                     and metadata.get("adaptive_policy") == "scaffold-v1")
                    # An already graded historical question is never replayed.
                    # A fresh snapshot of its genuinely due card may be added
                    # only when no unfinished task owns the same direction.
                    due_direction = (card is not None and metadata.get("study_path_due_v1") is True
                        and card["review_count"] > 0 and not card["suspended"]
                        and datetime.fromisoformat(card["due_at_utc"]) <= _utc(now)
                        and self.is_content_available(item["content_id"], now)
                        and metadata.get("not_before_utc") == card["due_at_utc"])
                    dependency_direction = False
                    if (card is not None and metadata.get("dependency_prerequisite_v1") is True
                            and metadata.get("study_path_due_v1") is True
                            and card["review_count"] > 0 and not card["suspended"]
                            and self.is_content_available(item["content_id"], now)
                            and metadata.get("not_before_utc") == card["due_at_utc"]):
                        # A failed prerequisite can wait on its original future
                        # due date inside this queue. This opt-in is restricted
                        # to actual missing parents of untouched queued targets;
                        # it cannot replay arbitrary history or move a timer.
                        from .kana_dependency_runtime import (kana_dependency_graph,
                            kana_dependency_state, missing_kana_prerequisites)
                        from .kana_dependencies import kana_entry_id
                        if dependency_repairs is None:
                            dependency_state = kana_dependency_state(self)
                            targets = [candidate for candidate in
                                [*original[current["question_index"]:], *addition]
                                if question_allowed(candidate)]
                            dependency_repairs = set(missing_kana_prerequisites(
                                targets, kana_dependency_graph(), dependency_state)) & dependency_state.reviewed
                        dependency_direction = kana_entry_id(
                            item["content_id"], item["script"]) in dependency_repairs
                    if not new_direction and not due_direction and not dependency_direction:
                        raise ValueError("不能把已学习的假名方向重新作为新方向加入。")
            snapshot = json.dumps([*original, *addition], ensure_ascii=False, allow_nan=False)
            from .progress_backup import MAX_BYTES
            if len(snapshot.encode("utf-8")) > MAX_BYTES:
                raise ValueError("练习队列数据过大，请减少新增数量，或完成当前队列后再添加。")
            session_questions.append_encoded(self._db, session_id, encoded)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        self.reset_choice_history()
        return session_id

    def record_review(self, content_id: str, skill: str, script: str, rating: int | None,
                      *, session_id: str | None = None, answered_text: str | None = None,
                      outcome: str = "answered", audio_ok: bool = True, recognition_ok: bool = True,
                      recognition_confirmed: bool = True, assisted: bool = False, duration_ms: int = 0,
                      content_version: str = "1", now: datetime | None = None,
                      question_index: int | None = None, kana_rows=None,
                      spelling_corrections: list[Question] | None = None,
                      word_romaji_parts=None, word_romaji_plan=None,
                      finalized_at: datetime | None = None, fair_new=False, question_fingerprint=None) -> dict:
        _validate_key(content_id, skill, script)
        if (word_romaji_parts is not None or word_romaji_plan is not None) and skill != "word_romaji":
            raise ValueError("Kana-unit answers require a reverse word question")
        if word_romaji_plan is not None and word_romaji_parts is None:
            raise ValueError("A kana-unit plan requires selected parts")
        now = _utc(now)
        reason = ("audio_failed" if not audio_ok else "recognition_failed" if not recognition_ok else "recognition_unconfirmed" if not recognition_confirmed else outcome)
        if not audio_ok or not recognition_ok or not recognition_confirmed or outcome not in {"answered", "correct", "incorrect", "self_rated"}:
            with self._db:
                self._db.execute("INSERT INTO practice_attempt VALUES (?,?,?,?,?,?)", (str(uuid4()), content_id, skill, script, now.isoformat(), reason))
            return {"scheduled": False, "reason": reason, "card": self.get_scheduling_card(content_id, skill, script)}
        if skill in WORD_SKILLS:
            return self._record_word_spelling(content_id, skill, script,
                session_id=session_id, question_index=question_index, answered_text=answered_text,
                outcome=outcome, assisted=assisted, duration_ms=duration_ms,
                kana_rows=kana_rows, spelling_corrections=spelling_corrections, now=now,
                word_romaji_parts=word_romaji_parts, word_romaji_plan=word_romaji_plan,
                question_fingerprint=question_fingerprint)
        finalized_at = _utc(finalized_at) if finalized_at is not None else None
        if finalized_at is not None and finalized_at < now:
            raise ValueError("Finalization precedes answer")
        if isinstance(rating, bool) or not isinstance(rating, int) or rating not in (1, 2, 3, 4):
            raise ValueError("rating must be 1 (Again), 2 (Hard), 3 (Good), or 4 (Easy)")
        if duration_ms < 0:
            raise ValueError("duration_ms must be nonnegative")
        if (kana_rows is not None or spelling_corrections) and (skill not in WORD_SKILLS or question_index is None or session_id is None):
            raise ValueError("Word spelling evidence requires a saved word question")
        if session_id is None:
            session_id = self.start_session([], now=now)
        elif not self._db.execute("SELECT 1 FROM study_session WHERE session_id=?", (session_id,)).fetchone():
            raise ValueError("Unknown session_id")
        key = self._card_key(content_id, skill, script)
        try:
            self._db.execute("BEGIN IMMEDIATE")
            spelling = None
            corrections = []
            correction_source = None
            if question_index is not None:
                questions = self._session_snapshot(session_id)
                if type(question_index) is not int or not 0 <= question_index < len(questions):
                    raise ValueError("Invalid question position")
                expected = questions[question_index]
                if (expected["content_id"], expected["kind"], expected["script"]) != (content_id, skill, script):
                    raise ValueError("Review does not match the saved question")
                recorded = self._db.execute("SELECT event_id FROM session_answer WHERE session_id=? AND question_index=?", (session_id, question_index)).fetchone()
                if recorded is not None:
                    self._db.rollback()
                    return {"scheduled": True, "already_recorded": True, "event_id": recorded[0], "card": self.get_scheduling_card(content_id, skill, script)}
                from .session_content import assert_current
                assert_current(self, session_id, question_index, expected, question_fingerprint)
                not_before = expected.get("metadata", {}).get("not_before_utc")
                if not_before and datetime.fromisoformat(not_before) > now:
                    raise ValueError("This learning repetition is not due yet")
                from .review_dispatch import kana_resume_priority
                checkpoint = self._db.execute(
                    "SELECT question_index,state_json FROM session_checkpoint WHERE session_id=?", (session_id,)).fetchone()
                preserved_attempt = (checkpoint is not None and checkpoint["question_index"] == question_index
                                     and kana_resume_priority(json.loads(checkpoint["state_json"])) is not None)
                if not preserved_attempt and self.feedback_wait_times([(content_id, skill, script)], now):
                    raise ValueError("This learning repetition is not due after feedback yet")
                correction_source = expected.get("metadata", {}).get("spelling_correction_of")
                if correction_source:
                    # This is guided practice after the erroneous word has
                    # exposed the answer. Even a corrected response is not an
                    # independent recall: preserve the normal hinted-answer
                    # policy and schedule a short relearning step.
                    assisted, rating = True, 1
                if kana_rows is not None:
                    from .word_spelling import analyze_spelling
                    from .kana_orthography import allowed_word_spellings
                    if not isinstance(answered_text, str):
                        raise ValueError("Word spelling evidence requires the submitted answer")
                    analysis = analyze_spelling(expected["answer"], answered_text,
                        script, kana_rows, assisted=bool(assisted),
                        accepted_answers=allowed_word_spellings(expected["answer"],
                            metadata=expected.get("metadata"),
                            accepted_answers=normalize_accepted_answers(expected.get("accepted_answers", ()))))
                    accepted = check_answer(_question_from_snapshot(expected), answered_text) and not assisted
                    if outcome != ("correct" if accepted else "incorrect"):
                        raise ValueError("Word spelling outcome does not match the submitted answer")
                    if accepted and not analysis["correct"]:
                        # A saved, legitimate alternative may differ from the
                        # canonical spelling. Keep its real word rating but do
                        # not infer which canonical kana were recalled.
                        analysis = {**analysis, "correct": True, "units": [],
                            "evidence_limited": True, "accepted_alternative": True}
                    spelling = {**analysis, "source_content_id": content_id,
                        "source_kind": skill, "source_question_index": question_index}
                    if assisted or not analysis["correct"]:
                        rating = 1
                    corrections = self._validated_spelling_corrections(
                        session_id, question_index, questions, spelling,
                        spelling_corrections, kana_rows, now)
                elif spelling_corrections:
                    raise ValueError("Spelling corrections require verified spelling evidence")
            if not self.is_content_available(content_id, now):
                raise ValueError("This memory item is paused or deferred")
            from .kana_routing import resolve_review, token as ability_token, finish_review
            route = resolve_review(self, content_id, skill, script, expected if question_index is not None else None)
            storage_skill = route["carrier_skill"] if route else skill
            key = route["carrier_card_id"] if route else key
            old = self.get_card(content_id, storage_skill, script)
            if old and old["suspended"]:
                raise ValueError("This practice direction is paused")
            if old and old["scheduler_version"] != ALGORITHM_VERSION:
                raise ValueError("Scheduler version migration is required")
            if old and old["scheduler_state"].get("last_review") and datetime.fromisoformat(old["scheduler_state"]["last_review"]) > now:
                raise ValueError("A review cannot precede this card's last review")
            card = Card.from_dict(old["scheduler_state"]) if old else Card(card_id=int(key[:15], 16), due=now)
            before = card.to_json()
            updated, log = self.scheduler.review_card(card, Rating(rating), review_datetime=now, review_duration=int(duration_ms))
            after = updated.to_json()
            self._db.execute("""INSERT INTO study_card(card_id,content_id,content_version,skill,script,scheduler_state_json,scheduler_version,due_at_utc,introduced_at_utc,review_count,suspended)
                VALUES (?,?,?,?,?,?,?,?,?,1,0) ON CONFLICT(content_id,skill,script) DO UPDATE SET
                content_version=excluded.content_version,scheduler_state_json=excluded.scheduler_state_json,
                scheduler_version=excluded.scheduler_version,due_at_utc=excluded.due_at_utc,review_count=study_card.review_count+1""",
                (key, content_id, content_version, storage_skill, script, after, ALGORITHM_VERSION, updated.due.isoformat(), now.isoformat()))
            event_id = str(uuid4())
            review_log = json.loads(log.to_json())
            if route:
                review_log["ability_route"] = ability_token(route, skill)
            if finalized_at is not None and finalized_at > now:
                review_log["finalized_at_utc"] = finalized_at.isoformat()
            if question_index is not None and isolated_kana_question(_question_from_snapshot(expected)):
                actual = normalize_answer(answered_text) if isinstance(answered_text, str) else None
                canonical = normalize_answer(expected["answer"])
                # This rating belongs only to the saved sound-to-kana skill.
                # Keep the produced glyph observable; an accepted homophone
                # is not evidence of writing/recognizing the canonical glyph.
                review_log["kana_answer_evidence"] = {
                    "version": 1, "answer_scope": "isolated_sound", "script": script,
                    "canonical_answer": canonical, "actual_answer": actual,
                    "accepted_homophone": (outcome == "correct" and not assisted
                        and actual != canonical and actual in homophone_answers(canonical, script)),
                }
            if spelling is not None:
                review_log["word_spelling_evidence"] = spelling
            if correction_source:
                review_log["spelling_correction"] = {"source_content_id": correction_source,
                    "source_question_index": expected["metadata"].get("spelling_correction_index")}
            self._db.execute("INSERT INTO review_event VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (event_id, session_id, key, now.isoformat(), int(rating), answered_text, int(assisted), outcome, int(duration_ms), before, after, ALGORITHM_VERSION, json.dumps(review_log, ensure_ascii=False, allow_nan=False)))
            if route:
                finish_review(self, route, event_id, json.loads(after), skill=skill,
                              independent=not assisted and outcome in {"correct", "incorrect", "answered", "self_rated"})
            from .learning_evidence import LearningEvidenceStore
            LearningEvidenceStore(self).record_attempt("kana:" + event_id,
                {"target_id": content_id, "ability": script + ":" + (route["ability"] if route else skill), "kind": skill,
                 "revision": content_version, "prompt": expected.get("prompt", "") if question_index is not None else "",
                 "answer": expected.get("answer", "") if question_index is not None else ""},
                {"outcome": outcome, "correct": outcome == "correct", "assisted": bool(assisted),
                 "answered_text": answered_text}, source="kana", role="primary", now=now,
                trusted=question_index is not None and outcome in {"correct", "incorrect"})
            if question_index is not None:
                from .kana_reading_rules import SMALL_KANA_RULE, explicit_reading_rules
                symbol = expected.get("metadata", {}).get("kana_symbol", "")
                if len(symbol) > 1 and SMALL_KANA_RULE in explicit_reading_rules(symbol):
                    from .kana_rule_evidence import record_rule_attempt, active_probe
                    probe = active_probe(self, expected.get("metadata", {}).get("kana_rule_probe_v1")) or {}
                    record_rule_attempt(self, "kana:" + event_id, rule_id=SMALL_KANA_RULE,
                        context_id=probe.get("context_id", "kana:" + symbol),
                        outcome="correct" if outcome == "correct" else "unresolved",
                        source="kana", script=script, now=now, assisted=bool(assisted),
                        probe_id=probe.get("probe_id"))
            queue_extended = False
            if question_index is not None:
                config = expected.get("metadata", {}).get("practice_config", {})
                if (config.get("schedule_mode") == "spaced" and int(updated.state) in (1, 3)
                        and now < updated.due < now + timedelta(days=1)):
                    metadata = {**expected.get("metadata", {}), "not_before_utc": updated.due.isoformat(),
                                "relearning_of_index": question_index,
                                "relearning_count": expected.get("metadata", {}).get("relearning_count", 0) + 1}
                    # The timed next recall is no longer the immediate,
                    # recently exposed correction and earns ordinary ratings.
                    metadata.pop("spelling_correction_of", None)
                    metadata.pop("spelling_correction_index", None)
                    metadata.pop("kana_rule_probe_v1", None)
                    # Do not mutate the cached snapshot before the transaction
                    # commits; a failed queue update must leave it unchanged.
                    questions = [*questions, {**expected, "metadata": metadata}]
                    from .progress_backup import MAX_BYTES
                    snapshot = json.dumps(questions, ensure_ascii=False)
                    if len(snapshot.encode("utf-8")) > MAX_BYTES:
                        raise ValueError("本次复习记录过大，请结束后重新开始复习。")
                    session_questions.replace(self._db, session_id, questions, reader=self._snapshot_reader)
                    queue_extended = True
                if corrections:
                    from .kana_routing import reserve_new_units, bind_questions
                    added = [question.to_dict() for question in corrections]
                    reserve_new_units(self, added, session_id)
                    questions = [*questions, *bind_questions(self, added)]
                    from .progress_backup import MAX_BYTES
                    snapshot = json.dumps(questions, ensure_ascii=False, allow_nan=False)
                    if len(snapshot.encode("utf-8")) > MAX_BYTES:
                        raise ValueError("本次纠错题目过大，请减少补练内容。")
                    session_questions.replace(self._db, session_id, questions, reader=self._snapshot_reader)
                    queue_extended = True
                correct = int(outcome == "correct" and not assisted)
                self._db.execute("INSERT INTO session_answer VALUES (?,?,?,?)", (session_id, question_index, event_id, correct))
                if question_index + 1 >= len(questions):
                    self._db.execute("DELETE FROM session_checkpoint WHERE session_id=?", (session_id,))
                else:
                    self._db.execute("UPDATE session_checkpoint SET question_index=?,session_right=session_right+?,state_json='{}',updated_at_utc=? WHERE session_id=?", (question_index + 1, correct, now.isoformat(), session_id))
            if fair_new and skill not in WORD_SKILLS and not (old and old["review_count"]):
                from .new_dispatch import completed
                completed(self._db, "kana", kind=skill)
            from .kana_ability_bridge import maintain_bridges
            maintain_bridges(self, reviewed_event_id=event_id if route is None and question_index is not None
                and old and old["review_count"] else None)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        return {"scheduled": True, "event_id": event_id, "rating": int(rating), "due_at_utc": updated.due.isoformat(),
                "queue_extended": queue_extended, "correction_count": len(corrections),
                "word_spelling_evidence": spelling, "card": self.get_scheduling_card(content_id, skill, script)}

    def _record_word_spelling(self, content_id, skill, script, *, session_id,
                              question_index, answered_text, outcome, assisted,
                              duration_ms, kana_rows, spelling_corrections, now,
                              word_romaji_parts=None, word_romaji_plan=None, question_fingerprint=None):
        """Save one auxiliary exercise without creating a word memory card."""
        if (not isinstance(session_id, str) or type(question_index) is not int
                or not isinstance(answered_text, str)):
            raise ValueError("Word spelling requires a saved question and submitted answer")
        if isinstance(duration_ms, bool) or not isinstance(duration_ms, (int, float)) or duration_ms < 0:
            raise ValueError("duration_ms must be nonnegative")
        try:
            self._db.execute("BEGIN IMMEDIATE")
            questions = self._session_snapshot(session_id)
            if not 0 <= question_index < len(questions):
                raise ValueError("Invalid question position")
            expected = questions[question_index]
            if (expected["content_id"], expected["kind"], expected["script"]) != (content_id, skill, script):
                raise ValueError("Spelling attempt does not match the saved question")
            recorded = self._db.execute("""SELECT attempt_id FROM word_spelling_attempt
                WHERE session_id=? AND question_index=? UNION ALL
                SELECT event_id FROM session_answer WHERE session_id=? AND question_index=?""",
                (session_id, question_index, session_id, question_index)).fetchone()
            if recorded is not None:
                self._db.rollback()
                return {"scheduled": True, "already_recorded": True, "attempt_id": recorded[0],
                    "event_id": recorded[0], "card": None, "due_at_utc": None,
                    "queue_extended": False, "correction_count": 0}
            from .kana_context_words import context_active
            from .session_content import assert_current, REPLACED, PREFIX as REPLACEMENTS
            assert_current(self, session_id, question_index, expected, question_fingerprint)
            replacement_id = expected.get('metadata', {}).get(REPLACED)
            if replacement_id and self.get_setting(REPLACEMENTS + replacement_id)['assisted']:
                assisted = True
            if not context_active(_question_from_snapshot(expected), self):
                raise ValueError('这道补测的依据已修订，原题和草稿已保留，请继续其他题目。')
            if WORD_KIND_PAUSE in expected.get("metadata", {}):
                raise ValueError("Resume the enabled word question before submitting its saved draft")
            if expected.get("metadata", {}).get("not_before_utc"):
                raise ValueError("Legacy timed word reviews are no longer scheduled")
            if skill == "word_romaji":
                session_practice_lane([expected])
            checkpoint = self._db.execute(
                "SELECT question_index,state_json FROM session_checkpoint WHERE session_id=?", (session_id,)).fetchone()
            converted = (skill in {"word_kana", "word_meaning_kana"} and checkpoint is not None
                and checkpoint["question_index"] == question_index
                and json.loads(checkpoint["state_json"]).get("word_used_conversion") is True)
            plan = None
            if word_romaji_parts is not None:
                checkpoint = self._db.execute(
                    "SELECT question_index,state_json FROM session_checkpoint WHERE session_id=?", (session_id,)).fetchone()
                if checkpoint is not None and checkpoint["question_index"] == question_index:
                    saved_plan = json.loads(checkpoint["state_json"]).get("word_romaji_plan")
                    if saved_plan is not None:
                        if word_romaji_plan is not None and word_romaji_plan != saved_plan:
                            raise ValueError("Kana-unit plan differs from the saved draft")
                        word_romaji_plan = saved_plan
                plan = word_romaji_unit_plan(expected, word_romaji_plan)
            accepted = check_answer(_question_from_snapshot(expected), answered_text,
                word_romaji_parts=word_romaji_parts, word_romaji_plan=plan) and not assisted
            if outcome not in {"correct", "incorrect"} or outcome != ("correct" if accepted else "incorrect"):
                raise ValueError("Word spelling outcome does not match the submitted answer")
            if word_romaji_parts is not None:
                from .word_romaji_units import analyze_romaji_parts
                spelling = analyze_romaji_parts(word_kana_answer(expected), word_romaji_parts,
                    script, kana_rows if kana_rows is not None else [], plan, assisted=bool(assisted))
                spelling.update(romaji_unit_version=1, romaji_parts=list(word_romaji_parts),
                                romaji_unit_plan=plan)
            elif kana_rows is not None:
                if skill == "word_romaji":
                    from .word_romaji_answer import analyze_word_romaji
                    spelling = analyze_word_romaji(word_kana_answer(expected), answered_text,
                        script, kana_rows, assisted=bool(assisted))
                else:
                    from .word_spelling import analyze_spelling
                    from .kana_orthography import allowed_word_spellings
                    spelling = analyze_spelling(expected["answer"], answered_text, script,
                        kana_rows, assisted=bool(assisted),
                        accepted_answers=allowed_word_spellings(expected["answer"],
                            metadata=expected.get("metadata"),
                            accepted_answers=normalize_accepted_answers(expected.get("accepted_answers", ()))))
            else:
                spelling = {"version": 1, "expected": word_kana_answer(expected),
                    "actual": normalize_answer(answered_text), "script": script,
                    "assisted": bool(assisted), "correct": accepted, "units": [], "errors": [],
                    "ambiguous": False, "evidence_limited": True}
            if skill == "word_romaji":
                spelling.update(answer_direction="romaji", expected_romaji=expected["answer"],
                                correct=accepted)
            if accepted and not spelling["correct"]:
                spelling = {**spelling, "correct": True, "units": [], "errors": [],
                    "evidence_limited": True, "accepted_alternative": True}
            spelling.update(source_content_id=content_id, source_kind=skill,
                            source_question_index=question_index)
            if converted:
                spelling.update(used_conversion=True, input_method="romaji_conversion_v1",
                                evidence_limited=True, units=[])
            drill = expected.get("metadata", {}).get("word_romaji_drill") is True
            context = expected.get("metadata", {}).get("kana_context_v1")
            attempt_id = str(uuid4())
            if drill or isinstance(context, dict):
                from .word_interval import apply_word_interval_adjustments
                spelling["practice_policy"] = WORD_ROMAJI_POLICY
                if isinstance(context, dict):
                    spelling["kana_context_v1"] = dict(context)
                spelling["schedule_adjustments"] = apply_word_interval_adjustments(
                    self, spelling, script, now, event_id="word:" + attempt_id)
            from .kana_rule_evidence import record_word_rule_evidence
            record_word_rule_evidence(self, "word:" + attempt_id, spelling, now=now,
                probe=expected.get("metadata", {}).get("kana_rule_probe_v1"))
            corrections = [] if drill or converted else self._validated_spelling_corrections(session_id, question_index,
                questions, spelling, spelling_corrections, kana_rows or [], now)
            if corrections:
                from .kana_routing import reserve_new_units, bind_questions
                added = [question.to_dict() for question in corrections]
                reserve_new_units(self, added, session_id)
                questions = [*questions, *bind_questions(self, added)]
                from .progress_backup import MAX_BYTES
                snapshot = json.dumps(questions, ensure_ascii=False, allow_nan=False)
                if len(snapshot.encode("utf-8")) > MAX_BYTES:
                    raise ValueError("本次纠错题目过大，请减少补练内容。")
                session_questions.replace(self._db, session_id, questions, reader=self._snapshot_reader)
            self._db.execute("INSERT INTO word_spelling_attempt VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (attempt_id, session_id, question_index, content_id, skill, script, now.isoformat(),
                 answered_text, int(assisted), int(accepted), int(duration_ms),
                 json.dumps(spelling, ensure_ascii=False, allow_nan=False)))
            if question_index + 1 >= len(questions):
                self._db.execute("DELETE FROM session_checkpoint WHERE session_id=?", (session_id,))
            else:
                self._db.execute("""UPDATE session_checkpoint SET question_index=?,
                    session_right=session_right+?,state_json='{}',updated_at_utc=? WHERE session_id=?""",
                    (question_index + 1, int(accepted), now.isoformat(), session_id))
            from .kana_ability_bridge import maintain_bridges
            maintain_bridges(self)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        return {"scheduled": True, "attempt_id": attempt_id, "event_id": attempt_id,
                "card": None, "due_at_utc": None, "queue_extended": bool(corrections),
                "correction_count": len(corrections), "word_spelling_evidence": spelling,
                "credited_correct": bool(accepted)}

    def _validated_spelling_corrections(self, session_id, question_index, questions,
                                      spelling, proposed, kana_rows, now):
        """Validate optional real follow-ups without modifying pending questions."""
        if proposed is None:
            return []
        if not isinstance(proposed, list) or any(not isinstance(q, Question) for q in proposed):
            raise ValueError("Spelling corrections must be saved questions")
        if not proposed or spelling["assisted"] or spelling.get("accepted_alternative"):
            return []
        incorrect = {unit["content_id"]: unit for unit in spelling["units"]
                     if unit["result"] == "incorrect"}
        source = questions[question_index]
        config = source.get("metadata", {}).get("practice_config", {})
        allowed = config.get("kinds", [source["kind"]])
        policy = self.get_setting("practice_queue_kinds", {})
        if isinstance(policy, dict) and policy.get("session_id") == session_id:
            allowed = policy.get("kinds", allowed)
        if not isinstance(allowed, (list, tuple)) or any(kind not in SKILLS for kind in allowed):
            raise ValueError("Invalid enabled question kinds")
        rows = {row["id"]: row for row in kana_rows}
        reserved = {(q["content_id"], q["script"]) for q in questions[question_index + 1:]
                    if q["kind"] in allowed and question_allowed(q)}
        result = []
        for question in proposed:
            _validate_key(question.content_id, question.kind, question.script)
            unit = incorrect.get(question.content_id)
            metadata = question.metadata
            if (not isinstance(metadata, dict) or unit is None or question.script != source["script"]
                    or question.kind in WORD_SKILLS or question.kind not in allowed
                    or metadata.get("spelling_correction_of") != source["content_id"]
                    or metadata.get("spelling_correction_index") != question_index
                    or metadata.get("not_before_utc") is not None):
                raise ValueError("Spelling correction does not match the word error or enabled kinds")
            row = rows.get(question.content_id)
            if row is None or normalize_answer(row.get(question.script, "")) != unit["symbol"]:
                raise ValueError("Spelling correction has an invalid kana source")
            if question.kind in ROMAJI_ANSWER_SKILLS:
                if normalize_romaji(question.answer) not in {normalize_romaji(x) for x in _romaji_values(row)}:
                    raise ValueError("Spelling correction has an invalid romanization answer")
            elif normalize_answer(question.answer) != unit["symbol"]:
                raise ValueError("Spelling correction has an invalid kana answer")
            key = question.content_id, question.script
            card = self.get_scheduling_card(question.content_id, question.kind, question.script)
            if (not question_allowed(question) or key in reserved or not self.is_content_available(question.content_id, now)
                    or card and card["suspended"]):
                continue
            reserved.add(key)
            result.append(question)
        return result

    def _iter_word_spelling_events(self, content_id=None, script=None, now=None,
                                  *, source_content_id=None, limit=None, newest_first=False, effective_only=True):
        """Stream contextual units, selecting relevant source logs in SQLite."""
        clauses = ["occurred_at_utc<=?"]
        params = [_utc(now).isoformat()]
        for column, value in (("script", script), ("content_id", source_content_id)):
            if value is not None:
                clauses.append(column + "=?")
                params.append(value)
        if content_id is not None:
            clauses.append("""EXISTS (SELECT 1 FROM json_each(
                CASE WHEN evidence_json IS NOT NULL THEN
                    CASE WHEN json_valid(evidence_json) THEN evidence_json ELSE '{}' END
                ELSE json_extract(CASE WHEN json_valid(review_log_json) THEN review_log_json ELSE '{}' END,
                    '$.word_spelling_evidence') END, '$.units') AS unit
                WHERE CASE WHEN unit.type='object' THEN json_extract(unit.value,'$.content_id') END=?)""")
            params.append(content_id)
        order = " DESC" if newest_first else " ASC"
        count = 0
        if limit is not None and limit <= 0:
            return
        from .content_history import INDEX as VALIDITY_INDEX, contribution_batch
        has_corrections = effective_only and self._db.execute(
            'SELECT 1 FROM app_setting WHERE key GLOB ? LIMIT 1', (VALIDITY_INDEX + '*',)).fetchone() is not None
        for row in self._db.execute("""WITH spelling_sources AS (
                SELECT e.event_id,e.occurred_at_utc,e.assisted,e.answered_text,e.review_log_json,
                    NULL AS evidence_json,c.content_id,c.skill,c.script,e.rowid AS ordinal,0 AS source_order
                FROM review_event e JOIN study_card c USING(card_id)
                WHERE c.skill IN ('word_kana','word_meaning_kana','word_romaji') UNION ALL
                SELECT attempt_id,occurred_at_utc,assisted,answered_text,NULL,evidence_json,
                    content_id,skill,script,rowid,1 FROM word_spelling_attempt)
                SELECT * FROM spelling_sources WHERE """ + " AND ".join(clauses)
                + " ORDER BY occurred_at_utc" + order + ",source_order" + order + ",ordinal" + order, params):
            log = _review_log_data(row)
            units = _review_spelling_units(row, log)
            weight = _review_spelling_weight(row, log) if units else 1
            validity = contribution_batch(self._db, [(row['event_id'], u['content_id'], '') for u in units]) if has_corrections else {}
            for unit in reversed(units) if newest_first else units:
                if not validity.get((row['event_id'], unit['content_id'], ''), True):
                    continue
                if content_id is None or unit["content_id"] == content_id:
                    yield {**unit, "script": row["script"],
                        "event_id": row["event_id"], "source_content_id": row["content_id"],
                        "source_kind": row["skill"], "occurred_at_utc": row["occurred_at_utc"],
                        "weight": weight}
                    count += 1
                    if limit is not None and count >= limit:
                        return

    def word_spelling_events(self, content_id=None, script=None, now=None,
                             *, source_content_id=None, limit=None, newest_first=False):
        """Read contextual kana evidence; never invent independent FSRS reviews."""
        return list(self._iter_word_spelling_events(content_id, script, now,
            source_content_id=source_content_id, limit=limit, newest_first=newest_first, effective_only=False))

    def word_interval_history(self, content_id, now=None, limit=8):
        """Read effective auxiliary date changes without counting them as reviews."""
        if limit <= 0:
            return []
        rows = self._db.execute("""SELECT a.attempt_id,a.occurred_at_utc,a.content_id AS source_content_id,
                json_extract(a.evidence_json,'$.expected') AS expected,adjustment.value
            FROM word_spelling_attempt a,json_each(
                CASE WHEN json_valid(a.evidence_json) THEN a.evidence_json ELSE '{}' END,
                '$.schedule_adjustments') AS adjustment
            WHERE a.occurred_at_utc<=? AND adjustment.type='object'
                AND json_extract(adjustment.value,'$.content_id')=?
            ORDER BY a.occurred_at_utc DESC,a.rowid DESC,adjustment.key""",
            (_utc(now).isoformat(), content_id))
        from .content_history import contribution_valid
        result = []
        for row in rows:
            adjustment = json.loads(row['value'])
            if (not contribution_valid(self._db, row['attempt_id'], content_id)
                    or adjustment.get('evidence_event_id') and not contribution_valid(
                        self._db, adjustment['evidence_event_id'], content_id)):
                continue
            result.append({**adjustment, 'occurred_at_utc': row['occurred_at_utc'],
                'source_content_id': row['source_content_id'], 'expected': row['expected']})
            if len(result) >= limit: break
        return result

    def word_spelling_summary(self, content_id=None, now=None, *, source_content_id=None):
        """Keep raw history and a bounded weighted hint for contextual selection.

        ``weak`` remains the latest measured result. ``weak_weight`` instead
        tracks the remaining error evidence after weighted successes, capped at
        one so extensive word practice cannot dominate future selection.
        Neither value is a calibrated memory probability or an FSRS rating.
        """
        summaries = {}
        for event in self._iter_word_spelling_events(content_id, now=now, source_content_id=source_content_id):
            key = event["content_id"], event["script"]
            entry = summaries.setdefault(key, {"content_id": key[0], "script": key[1],
                "symbol": event["symbol"], "event_count": 0, "success_count": 0,
                "failure_count": 0, "successes_since_error": 0,
                "weighted_success": 0, "weighted_failure": 0, "weak_weight": 0})
            entry["event_count"] += 1
            failed = event["result"] == "incorrect"
            entry["failure_count" if failed else "success_count"] += 1
            weight = event["weight"]
            entry["weighted_failure" if failed else "weighted_success"] += weight
            # This bounded selection hint is separate from the raw last-result
            # flag. A quarter-strength success offsets one quarter-strength
            # slip, but cannot erase a full-strength error on its own.
            entry["weak_weight"] = (min(1, entry["weak_weight"] + weight) if failed
                                    else max(0, entry["weak_weight"] - weight))
            entry["successes_since_error"] = 0 if failed else entry["successes_since_error"] + 1
            entry.update(last_review_at_utc=event["occurred_at_utc"],
                recent_result=event["result"], error_types=event["error_types"],
                last_word_id=event["source_content_id"], weak=failed)
        return list(summaries.values())

    def review_history(self, content_id: str | None = None, skill: str | None = None, script: str | None = None,
                       *, limit: int | None = None, newest_first: bool = False, include_states: bool = True) -> list[dict]:
        clauses, args = [], []
        actual_skill = "COALESCE(json_extract(e.review_log_json,'$.ability_route.skill'),c.skill)"
        for column, value in (("content_id", content_id), ("skill", skill), ("script", script)):
            if value is not None:
                clauses.append((actual_skill if column == "skill" else "c." + column) + "=?")
                args.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        fields = "e.*" if include_states else "e.event_id,e.session_id,e.card_id,e.occurred_at_utc,e.rating,e.answered_text,e.assisted,e.outcome,e.duration_ms,e.algorithm_version"
        order = " DESC" if newest_first else " ASC"
        sql = "SELECT " + fields + ",c.content_id," + actual_skill + " AS skill,c.script FROM review_event e JOIN study_card c ON c.card_id=e.card_id" + where + " ORDER BY e.occurred_at_utc" + order + ",e.rowid" + order
        if limit is not None:
            sql += " LIMIT ?"
            args.append(max(0, int(limit)))
        rows = self._db.execute(sql, args)
        result = []
        for row in rows:
            item = dict(row)
            if include_states:
                item["state_before"] = json.loads(item["state_before_json"])
                item["state_after"] = json.loads(item["state_after_json"])
            result.append(item)
        return result

    def select_content_ids(self, content_ids: list[str], skill: str, script: str, limit: int = 10, seed=None, now: datetime | None = None,
                           *, new_only: bool = False) -> list[str]:
        _validate_key("selection", skill, script)
        if type(new_only) is not bool:
            raise ValueError("new_only must be a boolean")
        now = _utc(now)
        limit = max(0, int(limit))
        rng = random.Random(seed)
        candidates = list(dict.fromkeys(content_ids))
        for content_id in candidates:
            _validate_key(content_id, skill, script)
        if skill in WORD_SKILLS:
            reserved = self.queued_content_ids()
            candidates = [content_id for content_id in candidates if content_id not in reserved]
            rng.shuffle(candidates)
            return candidates[:limit]
        unavailable = self._unavailable_content_ids(now)
        if new_only:
            unavailable |= self.queued_content_ids()
        candidates = [content_id for content_id in candidates if content_id not in unavailable]
        eligible = set(candidates)
        due = [] if new_only else [r for r in self.get_due(skill, script, now=now) if r["content_id"] in eligible]
        rng.shuffle(due)
        due.sort(key=lambda r: r["due_at_utc"])
        selected = [r["content_id"] for r in due[:limit]]
        # Existing suspended/not-yet-due cards are still existing cards. Read
        # their IDs once instead of decoding up to 3,000 scheduler records.
        from .kana_routing import active_units
        existing = ({row["content_id"] for row in self.scheduling_cards(aliases=True)
                     if row["skill"] == skill and row["script"] == script} if active_units(self) else
                    {row[0] for row in self._db.execute("SELECT content_id FROM study_card WHERE skill=? AND script=?", (skill, script))})
        new = [cid for cid in candidates if cid not in existing]
        rng.shuffle(new)
        selected.extend(new[:max(0, limit - len(selected))])
        return selected

    def select_word_context_ids(self, content_ids, contexts, script, limit, seed, now, *, preferred=None):
        """Pick useful kana contexts, without word-level learned/due state."""
        reserved = self.queued_content_ids()
        candidates = list(dict.fromkeys(cid for cid in content_ids if cid not in reserved))
        if preferred is not None:
            preferred = set(preferred)
            candidates = [cid for cid in candidates if set(contexts.get(cid, ())) & preferred]
        random.Random(seed).shuffle(candidates)
        due = {card["content_id"] for card in self.get_due(script=script, now=now)}
        weak = {entry["content_id"]: entry.get("weak_weight", float(entry.get("weak", False)))
                for entry in self.word_spelling_summary(now=now) if entry["script"] == script}
        known = {row[0] for row in self._db.execute(
            "SELECT content_id FROM study_card WHERE script=? AND skill NOT IN ('word_kana','word_meaning_kana','word_romaji')", (script,))}
        candidates.sort(key=lambda cid: (-len(set(contexts.get(cid, ())) & (preferred or set())),
            -len(set(contexts.get(cid, ())) & due),
            -sum(weak.get(kana_id, 0) for kana_id in set(contexts.get(cid, ()))),
            -len(set(contexts.get(cid, ())) & known)))
        return candidates[:max(0, int(limit))]


def _eligible_kana(row: dict, script: str, include_extended: bool, audio_only: bool = True,
                   *, kind: str | None = None) -> bool:
    text = row.get(script)
    if not direction_allowed(row.get("id"), kind, script, category=row.get("category"), symbol=text):
        return False
    if not text or (audio_only and row.get("standalone_audio_quiz_eligible") is False):
        return False
    if audio_only and not kana_audio_plan(row).isolated:
        return False
    if row.get("category") in {"small", "historical_kana", "iteration_mark", "long_vowel_mark", "voicing_mark", "rare_legacy_katakana"}:
        return False
    if row.get("tier") in {"historical", "orthographic_symbol", "specialist", "variant", "rare_modern_spelling"}:
        return False
    if not include_extended and row.get("tier", "modern_core") != "modern_core":
        return False
    # Modern i/e remain safe because their historical partners are not offered.
    ambiguous = set().union(*HOMOPHONE_GROUPS[:6])
    return not audio_only or to_script(text, "hiragana") not in ambiguous


def _tokens(text: str, symbols: list[str]) -> list[str]:
    tokens, index = [], 0
    symbols = sorted(set(symbols), key=lambda x: (-len(x), x))
    while index < len(text):
        token = next((x for x in symbols if text.startswith(x, index)), text[index])
        tokens.append(token)
        index += len(token)
    return tokens


def _word_keyboard(tokens: list[str], symbols: list[str], script: str,
                   word_table: str, rng: random.Random) -> list[str]:
    """Keep necessary spelling tokens, but use ordinary letters as distractors.

    The content catalogue also contains historical kana, repetition signs,
    standalone voicing marks and whole combinations. Those are useful reference
    entries, not interchangeable letters in a beginner's partial keyboard.
    Required tokens always survive, including small kana and unusual spelling.
    """
    modern_letters = to_script(
        "あいうえおかきくけこさしすせそたちつてとなにぬねの"
        "はひふへほまみむめもやゆよらりるれろわをん"
        "がぎぐげござじずぜぞだぢづでどばびぶべぼぱぴぷぺぽゔ", script)
    if word_table == "full":
        # Compose contractions and foreign sounds from their letters, rather
        # than filling the table with every catalogue combination. All modern
        # small letters and the long vowel mark remain available in every word.
        small_letters = to_script("ぁぃぅぇぉゃゅょっゎゕゖ", script)
        from .word_keyboard import normalize_word_keyboard_options
        return normalize_word_keyboard_options("".join(tokens),
            list(dict.fromkeys([*modern_letters, *small_letters, "ー", *tokens])), script, word_table="full")
    ordinary_letters = set(modern_letters)
    options = list(dict.fromkeys(tokens))
    distractors = list(dict.fromkeys(symbol for symbol in symbols
        if symbol in ordinary_letters and symbol not in options))
    rng.shuffle(distractors)
    options.extend(distractors[:max(4, 12 - len(options))])
    rng.shuffle(options)
    from .word_keyboard import normalize_word_keyboard_options
    return normalize_word_keyboard_options("".join(tokens), options, script, word_table="partial")


def _romaji_values(row: dict) -> tuple[str, ...]:
    """Use distinct spelling labels without changing saved question snapshots."""
    return kana_learning_romaji_values(row)


def _word_meaning(row: dict) -> str:
    for key in ("practice_gloss_zh", "chinese_gloss", "gloss"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _same_kana_shape(left: dict, right: dict, script: str) -> bool:
    # A voiced kana is still one letter after canonical composition. Groups
    # such as きゃ / ティ and single letters never serve as each other's options.
    return (len(normalize_answer(left[script])) == 1) == (len(normalize_answer(right[script])) == 1)


def _kana_choices(row: dict, records: list[dict], all_records: list[dict],
                  script: str, kind: str, count: int, rng: random.Random,
                  previous: list[str] | tuple[str, ...] = ()) -> list[str]:
    primary = _romaji_values(row)[0]
    answer = primary if kind == "kana_choice" else normalize_answer(row[script])
    accepted = {normalize_romaji(value) for value in _romaji_values(row)}
    choices = []
    # Prefer the selected scope. A one-item scope (notably ヴ) still receives
    # useful distractors of the same shape; only distractors may leave scope.
    pools = []
    for candidates in (records, all_records):
        pool = []
        for candidate in candidates:
            if not _same_kana_shape(row, candidate, script):
                continue
            if kind == "romaji_kana" and normalize_answer(candidate[script]) in homophone_answers(answer, script):
                continue
            candidate_romaji = _romaji_values(candidate)
            if not candidate_romaji or accepted.intersection(normalize_romaji(value) for value in candidate_romaji):
                continue
            value = candidate_romaji[0] if kind == "kana_choice" else normalize_answer(candidate[script])
            if value != answer and value not in pool:
                pool.append(value)
        pools.append(pool)
    for pool in pools:
        pool = [value for value in pool if value not in choices]
        rng.shuffle(pool)
        choices.extend(pool[:max(0, count - 1 - len(choices))])
        if len(choices) >= count - 1:
            break
    # A fresh round changes an actual distractor, not just its position, when
    # the same set recurs. Both the preferred and fallback pools obey shape
    # and alias fairness; even a narrow group never falls back to single kana.
    if choices and set([answer, *choices]) == set(previous):
        for pool in pools:
            alternatives = [value for value in pool if value not in previous]
            if alternatives:
                choices[rng.randrange(len(choices))] = rng.choice(alternatives)
                break
    options = [answer, *choices]
    rng.shuffle(options)
    # With a genuinely exhausted pool only the order can vary.
    if len(options) > 1 and options == list(previous):
        options = options[1:] + options[:1]
    return options


def _check_build_cancelled(store):
    # Only honor an explicitly installed worker callback. In particular,
    # getattr(Mock, missing_name) must not fabricate a callable here.
    check = getattr(store, "__dict__", {}).get("_build_cancel_check")
    if callable(check):
        check()


def _word_contexts(repository, records, script):
    """Map example words to existing kana units, independent of keyboard keys."""
    getter = getattr(repository, "kana_list", None)
    if not callable(getter):
        return {}
    projection = getattr(repository, '_kana_context_symbols', None)
    # A forwarding single-word repository can use its underlying catalogue's
    # projection. A custom kana_list or a mock must retain the original path.
    from .content import ContentRepository
    symbols = (projection(script) if callable(projection)
        and getattr(getter, '__func__', None) is ContentRepository._bundled_kana_list
        and getattr(projection, '__self__', None) is getattr(getter, '__self__', None) else None)
    if symbols is None:
        by_symbol = {}
        for row in getter():
            symbol = row.get(script)
            if symbol and row.get("id"):
                by_symbol.setdefault(normalize_answer(symbol), set()).add(row["id"])
        symbols = {symbol: next(iter(ids)) for symbol, ids in by_symbol.items() if len(ids) == 1}
    result = {}
    for row in records:
        reading = to_script(row.get("reading") or row.get("display_reading") or "", script)
        cursor, units = 0, []
        while cursor < len(reading):
            token = next((reading[cursor:cursor + length] for length in range(min(8, len(reading) - cursor), 0, -1)
                          if reading[cursor:cursor + length] in symbols), reading[cursor])
            if token in symbols:
                units.append(symbols[token])
            cursor += len(token)
        result[row.get("id", row.get("entry_id"))] = list(dict.fromkeys(units))
    return result


def build_questions(repository, store: StudyStore, kind: str = "kana_choice", script: str = "hiragana", limit: int = 10,
                    seed=None, include_research: bool = False, now: datetime | None = None,
                    *, include_extended: bool = False, option_count: int = 4, word_table: str = "partial",
                    kana_categories: tuple[str, ...] | list[str] | None = None,
                    word_collection: str | None = None, new_only: bool = False,
                    _targets_only: bool = False) -> list[Question]:
    """Choose a user-sized due-first or new-only batch without a daily quota."""
    _check_build_cancelled(store)
    _validate_key("questions", kind, script)
    if kind == "word_romaji":
        raise ValueError("Reverse word questions belong to the dedicated word practice builder")
    if kind in WORD_SKILLS and word_table not in {"partial", "full"}:
        raise ValueError("word_table must be partial or full")
    if word_collection is not None:
        if kind not in WORD_SKILLS:
            raise ValueError("word_collection applies only to word practice")
        if not isinstance(word_collection, str) or word_collection not in PRACTICE_WORD_COLLECTIONS:
            raise ValueError("Unknown word practice collection")
    categories = None
    if kana_categories is not None:
        if kind in WORD_SKILLS:
            raise ValueError("kana_categories applies only to kana practice")
        if not isinstance(kana_categories, (tuple, list)) or not kana_categories:
            raise ValueError("kana_categories must be a nonempty tuple or list")
        if any(not isinstance(category, str) or category not in PRACTICE_KANA_CATEGORIES for category in kana_categories):
            raise ValueError("Unknown kana practice category")
        categories = set(kana_categories)
    now = _utc(now)
    seed = random.SystemRandom().getrandbits(64) if seed is None else seed
    rng = random.Random(seed)
    content_version = repository.metadata().get("content_version", "1")
    all_kana = []
    if kind in WORD_SKILLS:
        # Keep legacy repository fixtures compatible when no filter is used.
        kwargs = {"collection": word_collection} if word_collection is not None else {}
        records = repository.practice_word_candidates(include_research=include_research, **kwargs)
        records = [r for r in records if (reading := r.get("reading") or r.get("display_reading"))
                   and all("\u3040" <= c <= "\u30ff" or c == "ー" for c in reading)
                   and direction_allowed(r.get("id", r.get("entry_id")), kind, script, symbol=reading)
                   and (kind != "word_meaning_kana" or _word_meaning(r))]
    else:
        # Select scope before FSRS chooses due/new cards, so unrelated due cards
        # never consume this session's places. Explicit foreign categories also
        # opt into their loanword tier; all fairness/symbol exclusions remain.
        all_kana = [r for r in repository.kana_list() if _eligible_kana(r, script, True, audio_only=False, kind=kind)]
        records = [r for r in all_kana
                   if (categories is None or r.get("category") in categories)
                   and _eligible_kana(r, script, include_extended or categories is not None, audio_only=kind == "kana_handwriting", kind=kind)
                   and (kind == "kana_handwriting" or _romaji_values(r))]
    by_id = {r.get("id", r.get("entry_id")): r for r in records}
    contexts = _word_contexts(repository, records, script) if kind in WORD_SKILLS else {}
    if isinstance(store, _BatchQuestionStore):
        store.contexts = contexts
    chosen = (store.select_word_context_ids(list(by_id), contexts, script, limit, seed, now)
              if kind in WORD_SKILLS and isinstance(store, StudyStore) else
              store.select_content_ids(list(by_id), kind, script, limit, seed, now, new_only=new_only))
    if _targets_only:
        return [_PracticeTarget(content_id, kind, script, {"practice_config": {},
            **({"context_kana_ids": contexts.get(content_id, [])} if kind in WORD_SKILLS else {})}) for content_id in chosen]
    if not chosen:
        return []
    previous_options = store.previous_choice_options(kind, script) if kind in {"kana_choice", "romaji_kana"} and chosen else {}
    questions = []
    all_symbols = repository.all_kana_symbols(script) if kind in WORD_SKILLS else []
    for content_id in chosen:
        _check_build_cancelled(store)
        row = by_id[content_id]
        metadata = {"session_seed": seed, "audio_only": True, "context_given": False, "content_version": content_version,
                    "new_only": new_only, "practice_config": {
                        "kana_categories": list(kana_categories) if kana_categories is not None else None,
                        "requested_count": max(0, int(limit)), "word_collection": word_collection,
                        "include_research": bool(include_research), "include_extended": bool(include_extended),
                        "word_table": word_table, "new_only": new_only}}
        accepted = ()
        if kind in WORD_SKILLS:
            original = row.get("reading") or row["display_reading"]
            answer = to_script(original, script)
            from .kana_orthography import allowed_word_spellings, ORTHOGRAPHY_POLICY
            accepted = tuple(value for value in allowed_word_spellings(answer) if value != answer)
            from .word_keyboard import word_answer_letters
            tokens = word_answer_letters(answer)
            # Unknown symbols never become unanswerable keyboard exercises.
            if any(not ("\u3040" <= c <= "\u30ff" or c == "ー") for c in answer):
                continue
            options = _word_keyboard(tokens, all_symbols, script, word_table, rng)
            options.extend(char for char in dict.fromkeys("".join(accepted)) if char not in options)
            gloss = _word_meaning(row)
            romaji = romanize_kana(original)
            if kind == "word_meaning_kana":
                prompt = f"根据词义和罗马音，用假名拼出单词。 词义：{gloss}；罗马音：{romaji}"
            else:
                prompt = "听单词，用假名拼出读音。" + (f" 词义提示：{gloss}" if gloss else "")
            metadata.update({"context_given": bool(gloss), "original_reading": original, "answer_tokens": tokens,
                             "word_table": word_table, "research_content": row.get("practice_approved") is not True,
                             "meaning_hint": gloss, "context_kana_ids": contexts.get(content_id, []),
                             "orthography_policy": ORTHOGRAPHY_POLICY})
            if word_collection is not None:
                metadata["word_collection"] = word_collection
            if kind == "word_meaning_kana":
                metadata.update({"audio_only": False, "romaji": romaji})
            audio_text = original
        else:
            symbol = normalize_answer(row[script])
            metadata["kana_category"] = row.get("category")
            audio_plan = kana_audio_plan(row)
            audio_text = audio_plan.text if audio_plan.isolated else ""
            romaji_values = _romaji_values(row)
            romaji = romaji_values[0] if romaji_values else ""
            if kind in ROMAJI_ANSWER_SKILLS:
                answer = romaji
                prompt = f"选出「{symbol}」的罗马字。" if kind == "kana_choice" else f"拼出「{symbol}」的罗马字。"
                options = _kana_choices(row, records, all_kana, script, kind, max(1, int(option_count)), rng,
                                        previous_options.get(content_id, ())) if kind == "kana_choice" else []
                accepted = romaji_values[1:]
                metadata.update({"audio_only": False, "context_given": True, "kana_symbol": symbol})
            elif kind in {"romaji_kana", "romaji_kana_full", "romaji_handwriting"}:
                answer = symbol
                if kind == "romaji_kana":
                    prompt = f"选出罗马音「{romaji}」对应的假名。"
                    options = _kana_choices(row, records, all_kana, script, kind, max(1, int(option_count)), rng,
                                            previous_options.get(content_id, ()))
                elif kind == "romaji_kana_full":
                    prompt = f"从完整假名表拼出罗马音「{romaji}」对应的假名。"
                    # Modern characters only. Decompose groups so a group is
                    # entered with the same full table as a single character.
                    options = list(dict.fromkeys(char for candidate in all_kana
                        for char in normalize_answer(candidate[script])))
                    metadata.update({"word_table": "full", "answer_tokens": list(symbol)})
                else:
                    prompt = f"写出罗马音「{romaji}」对应的假名，并确认识别结果。"
                    options = []
                # The prompt cannot distinguish e.g. じ / ぢ. Only one appears
                # in choices; accept either if an external caller supplies it.
                accepted = tuple(value for value in dict.fromkeys([*homophone_answers(symbol, script), *(normalize_answer(candidate[script]) for candidate in all_kana
                    if _romaji_values(candidate) and normalize_romaji(_romaji_values(candidate)[0]) == normalize_romaji(romaji)
                    and normalize_answer(candidate[script]) != answer)]) if value != answer)
                metadata.update({"audio_only": False, "context_given": False, "kana_symbol": symbol,
                                 "answer_scope": "isolated_sound"})
            else:
                prompt = "听读音，写出假名，并确认识别结果。"
                answer = symbol
                options = []
            metadata["romaji"] = romaji
        questions.append(Question(content_id, kind, script, prompt, answer, options, audio_text, accepted, metadata))
    return questions


class _BatchQuestionStore:
    """Reuse the single-skill builder's eligibility and option rules for a batch.

    The first pass collects eligible IDs without building questions. The second
    supplies the already selected memory items, without selecting another batch.
    """

    def __init__(self, store, selected=(), previous_options=None):
        self.store, self.selected = store, selected
        self._build_cancel_check = getattr(store, "__dict__", {}).get("_build_cancel_check")
        self.previous_options = previous_options
        self.candidates = []
        self.contexts = {}

    def select_content_ids(self, content_ids, *args, **kwargs):
        self.candidates = list(content_ids)
        eligible = set(content_ids)
        return [content_id for content_id in self.selected if content_id in eligible]

    def previous_choice_options(self, kind, script):
        if self.previous_options is not None:
            return self.previous_options.get(kind, {})
        return self.store.previous_choice_options(kind, script)


def _interleave_batch(questions_by_content, rng, previous_content=None):
    """Spread repeated items apart, with randomized skill order and tie breaks."""
    heap = []
    for content_id, questions in questions_by_content.items():
        questions = list(questions)
        rng.shuffle(questions)
        heapq.heappush(heap, (-len(questions), rng.random(), content_id, questions))
    result = []
    while heap:
        item = heapq.heappop(heap)
        if item[2] == previous_content and heap:
            alternative = heapq.heappop(heap)
            heapq.heappush(heap, item)
            item = alternative
        count, _, content_id, questions = item
        result.append(questions.pop())
        previous_content = content_id
        if questions:
            heapq.heappush(heap, (count + 1, rng.random(), content_id, questions))
    return result


def _interleave_spaced_batch(questions_by_content, content_order, *, mix_kinds=False):
    """Serve urgent items first, spacing their remaining directions apart.

    Adaptive batches also limit runs of a single question form. The original
    priority order starts the batch, but after two equal forms an already-
    selected alternative can be presented before the rest of that run.
    This is presentation fairness, not a change to any card's due date.
    """
    queues = {content_id: list(questions_by_content[content_id]) for content_id in content_order}
    result = []
    while queues:
        for content_id in tuple(queues):
            result.append(queues[content_id].pop(0))
            if not queues[content_id]:
                del queues[content_id]
    if not mix_kinds:
        return result
    # Keep the original priority rank within each form. Only look through a
    # prefix sharing the previous content, to avoid showing its answer again
    # immediately. One scheduled round has at most two entries per content and
    # form (one per script), so this stays linear in batch size for fixed skills.
    by_kind = {}
    for index, question in enumerate(result):
        by_kind.setdefault(question.kind, deque()).append((index, question))
    mixed, previous_content, previous_kind, run = [], None, None, 0
    last_seen = {}
    while by_kind:
        choices = [next((item for item in queue if item[1].content_id != previous_content), queue[0])
                   for queue in by_kind.values()]
        separated = [item for item in choices if item[1].content_id != previous_content]
        choices = separated or choices
        alternatives = [item for item in choices if item[1].kind != previous_kind]
        if run >= 2 and alternatives:
            # Rotate the inserted forms too: otherwise the earliest new form
            # can occupy every break and conceal all the other unlocked cues.
            # Avoid the next priority cue's content as well as the previous
            # content, so the insertion doesn't force that cue out of order.
            next_priority = min(choices, key=lambda item: item[0])[1]
            if next_priority.kind == previous_kind:
                blocked = {previous_content, next_priority.content_id}
                between = [next((item for item in queue if item[1].content_id not in blocked), None)
                           for kind, queue in by_kind.items() if kind != previous_kind]
                alternatives = [item for item in between if item is not None] or alternatives
            index, question = min(alternatives,
                key=lambda item: (last_seen.get(item[1].kind, -1), item[0]))
        else:
            index, question = min(choices, key=lambda item: item[0])
        queue = by_kind[question.kind]
        queue.remove((index, question))
        if not queue:
            del by_kind[question.kind]
        mixed.append(question)
        last_seen[question.kind] = len(mixed)
        previous_content = question.content_id
        run = run + 1 if question.kind == previous_kind else 1
        previous_kind = question.kind
    return mixed


def validate_batch_size(questions: list[Question], rounds: int):
    """Check a one-round preview against the expanded snapshot's byte budget."""
    if type(rounds) is not int or rounds <= 0:
        raise ValueError("rounds must be a positive integer")
    from .progress_backup import MAX_BYTES
    largest_round = [replace(question, metadata={**question.metadata, "batch_round": rounds,
        "practice_config": {**question.metadata.get("practice_config", {}), "rounds": rounds}})
        for question in questions]
    round_bytes = sum(len(json.dumps(question.to_dict(), ensure_ascii=False).encode("utf-8")) + 2
                      for question in largest_round)
    if round_bytes * rounds + 2 > MAX_BYTES:
        raise ValueError("本批题目数据过大，请减少轮数或记忆项。")


def _adaptive_stages_uncached(store: StudyStore, script: str, now: datetime) -> dict[str, int]:
    """Choose scaffolding from actual, same-script recall since the last lapse.

    These conservative product thresholds are not a research-derived memory
    score. Two independent successful answers introduce production; successful
    answers at least a day apart introduce writing. All direction-specific
    FSRS states remain untouched and existing due directions remain eligible.
    Dedicated romanization word drills use quarter-strength contextual credit;
    this product parameter is not a research-derived transfer probability.
    """
    evidence = {}
    for event in store._db.execute("""WITH evidence_sources AS (
            SELECT c.content_id,COALESCE(json_extract(e.review_log_json,'$.ability_route.skill'),c.skill) AS skill,c.script,e.occurred_at_utc,e.rating,e.assisted,e.outcome,
                e.answered_text,e.review_log_json,NULL AS evidence_json,e.rowid AS ordinal,0 AS source_order
            FROM review_event e JOIN study_card c ON c.card_id=e.card_id UNION ALL
            SELECT content_id,skill,script,occurred_at_utc,NULL,assisted,
                CASE WHEN correct=1 THEN 'correct' ELSE 'incorrect' END,answered_text,NULL,
                evidence_json,rowid,1 FROM word_spelling_attempt)
            SELECT * FROM evidence_sources WHERE script=? AND occurred_at_utc<=?
            ORDER BY occurred_at_utc,source_order,ordinal""", (script, now.isoformat())):
        content_id = event["content_id"]
        log = _review_log_data(event)
        if event["skill"] in WORD_SKILLS:
            pass  # A word is a cue; only its measured kana units have stages.
        elif event["assisted"] or event["rating"] == 1 or event["outcome"] == "incorrect":
            evidence[content_id] = (0, None, None)
        elif (event["outcome"] in {"correct", "answered", "self_rated"}
                and not log.get("spelling_correction")):
            when = datetime.fromisoformat(event["occurred_at_utc"])
            count, first, _ = evidence.get(content_id, (0, None, None))
            evidence[content_id] = (count + 1, first or when, when)
        for unit in _review_spelling_units(event, log):
            kana_id = unit["content_id"]
            weight = _review_spelling_weight(event, log)
            count, first, last = evidence.get(kana_id, (0, None, None))
            if unit["result"] == "incorrect":
                # Auxiliary transliteration is weaker evidence than an
                # independent kana recall; a slip never clears its full history.
                remaining = max(0, count - weight) if weight < 1 else 0
                evidence[kana_id] = (remaining, first, last) if remaining else (0, None, None)
            else:
                when = datetime.fromisoformat(event["occurred_at_utc"])
                evidence[kana_id] = (count + weight, first or when, when)
    return {content_id: (2 if count >= 2 and last - first >= timedelta(days=1)
                         else 1 if count >= 2 else 0)
            for content_id, (count, first, last) in evidence.items()}


def _adaptive_evidence_monitor(store):
    """Connection-local, transactional invalidation; never a backup table."""
    if not getattr(store, '_adaptive_monitor_ready', False):
        store._db.executescript("""
            CREATE TEMP TABLE adaptive_evidence_revision (
                epoch INTEGER NOT NULL, writes INTEGER NOT NULL,
                review_high INTEGER NOT NULL, word_high INTEGER NOT NULL);
            INSERT INTO adaptive_evidence_revision SELECT 0,0,
                COALESCE((SELECT MAX(rowid) FROM main.review_event),0),
                COALESCE((SELECT MAX(rowid) FROM main.word_spelling_attempt),0);
            CREATE TEMP TRIGGER adaptive_review_replace BEFORE INSERT ON main.review_event
              WHEN EXISTS(SELECT 1 FROM review_event WHERE event_id=NEW.event_id OR rowid=NEW.rowid)
              BEGIN UPDATE adaptive_evidence_revision SET epoch=epoch+1; END;
            CREATE TEMP TRIGGER adaptive_review_insert AFTER INSERT ON main.review_event
              BEGIN UPDATE adaptive_evidence_revision SET writes=writes+1,
                epoch=epoch+CASE WHEN NEW.rowid<=review_high THEN 1 ELSE 0 END,
                review_high=MAX(review_high,NEW.rowid); END;
            CREATE TEMP TRIGGER adaptive_review_update AFTER UPDATE ON main.review_event
              BEGIN UPDATE adaptive_evidence_revision SET epoch=epoch+1,writes=writes+1; END;
            CREATE TEMP TRIGGER adaptive_review_delete AFTER DELETE ON main.review_event
              BEGIN UPDATE adaptive_evidence_revision SET epoch=epoch+1,writes=writes+1,
                review_high=CASE WHEN OLD.rowid=review_high THEN
                    COALESCE((SELECT MAX(rowid) FROM review_event),0) ELSE review_high END; END;
            CREATE TEMP TRIGGER adaptive_word_replace BEFORE INSERT ON main.word_spelling_attempt
              WHEN EXISTS(SELECT 1 FROM word_spelling_attempt WHERE attempt_id=NEW.attempt_id
                OR rowid=NEW.rowid OR (session_id=NEW.session_id AND question_index=NEW.question_index))
              BEGIN UPDATE adaptive_evidence_revision SET epoch=epoch+1; END;
            CREATE TEMP TRIGGER adaptive_word_insert AFTER INSERT ON main.word_spelling_attempt
              BEGIN UPDATE adaptive_evidence_revision SET writes=writes+1,
                epoch=epoch+CASE WHEN NEW.rowid<=word_high THEN 1 ELSE 0 END,
                word_high=MAX(word_high,NEW.rowid); END;
            CREATE TEMP TRIGGER adaptive_word_update AFTER UPDATE ON main.word_spelling_attempt
              BEGIN UPDATE adaptive_evidence_revision SET epoch=epoch+1,writes=writes+1; END;
            CREATE TEMP TRIGGER adaptive_word_delete AFTER DELETE ON main.word_spelling_attempt
              BEGIN UPDATE adaptive_evidence_revision SET epoch=epoch+1,writes=writes+1,
                word_high=CASE WHEN OLD.rowid=word_high THEN
                    COALESCE((SELECT MAX(rowid) FROM word_spelling_attempt),0) ELSE word_high END; END;
            CREATE TEMP TRIGGER adaptive_card_update AFTER UPDATE OF card_id,content_id,skill,script ON main.study_card
              WHEN OLD.card_id IS NOT NEW.card_id OR OLD.content_id IS NOT NEW.content_id
                OR OLD.skill IS NOT NEW.skill OR OLD.script IS NOT NEW.script
              BEGIN UPDATE adaptive_evidence_revision SET epoch=epoch+1,writes=writes+1; END;
            CREATE TEMP TRIGGER adaptive_card_delete AFTER DELETE ON main.study_card
              BEGIN UPDATE adaptive_evidence_revision SET epoch=epoch+1,writes=writes+1; END;
            CREATE TEMP TRIGGER adaptive_card_replace BEFORE INSERT ON main.study_card
              WHEN EXISTS(SELECT 1 FROM study_card WHERE
                    (card_id=NEW.card_id AND (content_id IS NOT NEW.content_id OR skill IS NOT NEW.skill OR script IS NOT NEW.script))
                    OR (content_id=NEW.content_id AND skill=NEW.skill AND script=NEW.script AND card_id IS NOT NEW.card_id))
                OR (NOT EXISTS(SELECT 1 FROM study_card WHERE card_id=NEW.card_id)
                    AND EXISTS(SELECT 1 FROM review_event WHERE card_id=NEW.card_id))
              BEGIN UPDATE adaptive_evidence_revision SET epoch=epoch+1,writes=writes+1; END;
        """)
        store._adaptive_monitor_ready = True
    row = store._db.execute('SELECT epoch,writes FROM adaptive_evidence_revision').fetchone()
    return (store._db.execute('PRAGMA data_version').fetchone()[0], row[0], row[1],
            store._db.execute('PRAGMA main.schema_version').fetchone()[0])


def _adaptive_event_rows(db, script, cursors=None):
    where_review = 'WHERE e.rowid>?' if cursors is not None else ''
    where_word = 'WHERE rowid>?' if cursors is not None else ''
    params = (*cursors, script) if cursors is not None else (script,)
    return list(db.execute(f"""WITH evidence_sources AS (
        SELECT c.content_id,COALESCE(json_extract(e.review_log_json,'$.ability_route.skill'),c.skill) AS skill,c.script,e.occurred_at_utc,e.rating,e.assisted,e.outcome,
            e.answered_text,e.review_log_json,NULL AS evidence_json,e.rowid AS ordinal,0 AS source_order
        FROM review_event e JOIN study_card c ON c.card_id=e.card_id {where_review} UNION ALL
        SELECT content_id,skill,script,occurred_at_utc,NULL,assisted,
            CASE WHEN correct=1 THEN 'correct' ELSE 'incorrect' END,answered_text,NULL,
            evidence_json,rowid,1 FROM word_spelling_attempt {where_word})
        SELECT * FROM evidence_sources WHERE script=?
        ORDER BY occurred_at_utc,source_order,ordinal""", params))


def _adaptive_event_key(event):
    # Match the existing SQL text ordering, including source_order before rowid.
    return event['occurred_at_utc'], event['source_order'], event['ordinal']


def _advance_adaptive_evidence(evidence, event):
    """Validate persisted payloads, then pass a normalized event to the pure fold."""
    from .adaptive_evidence import AdaptiveEvent, advance
    log = _review_log_data(event)
    primary = 'skip'
    if event['skill'] not in WORD_SKILLS:
        if event['assisted'] or event['rating'] == 1 or event['outcome'] == 'incorrect':
            primary = 'reset'
        elif event['outcome'] in {'correct', 'answered', 'self_rated'} and not log.get('spelling_correction'):
            primary = 'credit'
    units = tuple((unit['content_id'], unit['result']) for unit in _review_spelling_units(event, log))
    if primary == 'skip' and not units:
        return
    advance(evidence, AdaptiveEvent(event['content_id'], datetime.fromisoformat(event['occurred_at_utc']),
        primary, units, _review_spelling_weight(event, log) if units else 1.0))


def _adaptive_stages(store: StudyStore, script: str, now: datetime) -> dict[str, int]:
    """Reuse only a verified, chronological append prefix of the real history.

    The original replay remains the transaction/read-only fallback and oracle.
    No card, event, persistent schema or scheduler state is changed here.
    """
    if not isinstance(store, StudyStore) or store._db.in_transaction:
        return _adaptive_stages_uncached(store, script, now)
    try:
        token = _adaptive_evidence_monitor(store)
    except sqlite3.OperationalError:
        # E.g. a query_only inspection cannot create TEMP metadata.
        return _adaptive_stages_uncached(store, script, now)
    caches = getattr(store, '_adaptive_evidence_cache', {})
    cached = caches.get(script)
    from . import adaptive_evidence
    policy = (WORD_ROMAJI_POLICY, WORD_ROMAJI_WEIGHT,
              _review_log_data, _review_spelling_units, _review_spelling_weight,
              adaptive_evidence.POLICY_VERSION, adaptive_evidence.advance, adaptive_evidence.stages)
    cutoff = now.isoformat()
    reuse = (cached is not None and cached['token'][:2] == token[:2]
             and cached['token'][3] == token[3]  # Includes same-connection VACUUM.
             and cached['now'] <= now and cached['cutoff'] <= cutoff and cached['policy'] == policy)
    if reuse:
        evidence = dict(cached['evidence'])
        cursors = cached['cursors']
        added = _adaptive_event_rows(store._db, script, cursors) if token[2] != cached['token'][2] else []
        # A backfilled primary event at an auxiliary event's timestamp also
        # sorts before it. Do not merely compare dates or append row numbers.
        if added and cached['last'] is not None and _adaptive_event_key(added[0]) < cached['last']:
            reuse = False
        else:
            pending = list(heapq.merge(cached['future'], added, key=_adaptive_event_key))
            last = cached['last']
    if not reuse:
        evidence, pending, last = {}, _adaptive_event_rows(store._db, script), None
    future = []
    for event in pending:
        if event['occurred_at_utc'] > cutoff:
            future.append(event)
        else:
            _advance_adaptive_evidence(evidence, event)
            last = _adaptive_event_key(event)
    if not reuse or token[2] != cached['token'][2]:
        cursors = tuple(store._db.execute('SELECT '
            'COALESCE((SELECT MAX(rowid) FROM review_event),0),'
            'COALESCE((SELECT MAX(rowid) FROM word_spelling_attempt),0)').fetchone())
    # An external writer during any of the bounded reads invalidates the
    # assembled view. Use one fresh full SQL snapshot and publish no cache.
    if _adaptive_evidence_monitor(store) != token:
        store._adaptive_evidence_cache = {}
        return _adaptive_stages_uncached(store, script, now)
    caches[script] = {'token': token, 'now': now, 'cutoff': cutoff, 'policy': policy,
        'evidence': evidence, 'future': future, 'last': last, 'cursors': cursors}
    store._adaptive_evidence_cache = caches
    return adaptive_evidence.stages(evidence)


_ADAPTIVE_LEVELS = {"kana_choice": 0, "romaji_kana": 0, "word_meaning_kana": 0,
                    "kana_romaji": 1, "romaji_kana_full": 1, "word_kana": 1,
                    "romaji_handwriting": 2, "kana_handwriting": 2}


def _adaptive_new_kinds(kinds, stage):
    enabled = tuple(kind for kind in kinds if _ADAPTIVE_LEVELS[kind] <= stage)
    # An explicit selection containing only advanced tasks is respected. The
    # app explains this choice; never produce an empty batch merely because
    # the learner deliberately disabled all introductory forms.
    return enabled or tuple(kinds)


def _unlocked_advanced_kind(kind, stage):
    # The advanced-only fallback above permits an explicit exercise but is not
    # evidence that this learner has earned advancement priority.
    return 0 < _ADAPTIVE_LEVELS[kind] <= stage


def _combined_adaptive_batch(repository, store, *, kinds, rounds, script, limit,
                             seed, include_research, now, word_table, kana_categories,
                             word_collection, new_only, schedule_mode,
                             handwriting_available, audio_available,
                             prioritize_advancement, _targets_only=False):
    """Combine content families and scripts without combining any single cue.

    The limit is distinct content, as elsewhere in batch planning. A kana can
    therefore produce separate hiragana and katakana questions. Each question
    still owns exactly one (content, direction, script) FSRS key.
    """
    _check_build_cancelled(store)
    if script not in {*SCRIPTS, "both"}:
        raise ValueError("Unknown script")
    for kind in kinds:
        if not isinstance(kind, str) or kind not in SKILLS:
            raise ValueError("Unknown practice skill")
    kinds = tuple(dict.fromkeys(kinds))
    scripts = ("hiragana", "katakana") if script == "both" else (script,)
    now = _utc(now)
    limit = max(0, int(limit))
    seed = random.SystemRandom().getrandbits(64) if seed is None else seed
    rng = random.Random(seed)
    batches = []
    has_kana_kinds = any(kind not in WORD_SKILLS for kind in kinds)
    word_kinds = tuple(kind for kind in kinds if kind in WORD_SKILLS)
    for word_family in (False, True):
        if word_family and has_kana_kinds:
            continue
        family_kinds = tuple(kind for kind in kinds if (kind in WORD_SKILLS) == word_family)
        if not family_kinds:
            continue
        for alphabet in scripts:
            _check_build_cancelled(store)
            batches.append(build_practice_batch(repository, store, kinds=family_kinds,
                rounds=rounds, script=alphabet, limit=limit, seed=seed,
                include_research=include_research, now=now, word_table=word_table,
                kana_categories=None if word_family else kana_categories,
                word_collection=word_collection if word_family else None,
                new_only=new_only, schedule_mode=schedule_mode, adaptive=True,
                handwriting_available=handwriting_available, audio_available=audio_available,
                _prioritize_advancement=prioritize_advancement,
                _targets_only=_targets_only))
    questions_by_content = {}
    by_group = []
    for batch in batches:
        order = list(dict.fromkeys(question.content_id for question in batch))
        by_group.append(order)
        for question in batch:
            questions_by_content.setdefault(question.content_id, []).append(question)
    if not questions_by_content and not word_kinds:
        return []
    memory_ids = {cid for cid, values in questions_by_content.items() if values[0].kind not in WORD_SKILLS}
    word_ids = {cid for cid, values in questions_by_content.items() if values[0].kind in WORD_SKILLS}
    pairs = {(q.content_id, q.kind, q.script) for values in questions_by_content.values() for q in values}
    due = {}
    if not new_only:
        for card in store.scheduling_cards(aliases=True):
            if card["suspended"] or card["review_count"] <= 0 or card["due_at_utc"] > now.isoformat():
                continue
            if (card["content_id"], card["skill"], card["script"]) in pairs:
                state = json.loads(card["scheduler_state_json"]).get("state")
                priority = (0 if schedule_mode == "spaced" and state in (1, 3) else 1, card["due_at_utc"])
                content_id = card["content_id"]
                due[content_id] = min(due.get(content_id, priority), priority)
    due_ids = list(due)
    rng.shuffle(due_ids)
    due_ids.sort(key=due.get)
    chosen = due_ids[:limit]
    selected = set(chosen)
    advancement = set()
    if prioritize_advancement:
        known_pairs = {(card["content_id"], card["skill"], card["script"]) for card in store.scheduling_cards(aliases=True)}
        advancement = {q.content_id for values in questions_by_content.values() for q in values
                       if q.kind not in WORD_SKILLS
                       and (q.content_id, q.kind, q.script) not in known_pairs
                       and _unlocked_advanced_kind(q.kind, q.metadata.get("adaptive_stage", 0))}
    # Balance families within each priority tier, not ahead of pending progress.
    # Reclassify after merging scripts: a katakana advancement must not sit
    # behind a hiragana branch's fresh items. Each content still takes one slot.
    tiers = (advancement, None) if advancement else (None,)
    for tier in tiers:
        fresh_groups = [deque(dict.fromkeys(cid for group in by_group for cid in group
                        if cid not in due and (tier is None or cid in tier)
                        and cid in memory_ids))
                        for _ in (0,)]
        rng.shuffle(fresh_groups)
        while len(chosen) < limit and any(fresh_groups):
            for group in fresh_groups:
                while group and group[0] in selected:
                    group.popleft()
                if group and len(chosen) < limit:
                    content_id = group.popleft()
                    chosen.append(content_id)
                    selected.add(content_id)
    memory_count = len(chosen)
    if has_kana_kinds and word_kinds:
        for alphabet in scripts:
            batch = build_practice_batch(repository, store, kinds=word_kinds,
                rounds=rounds, script=alphabet, limit=limit, seed=seed,
                include_research=include_research, now=now, word_table=word_table,
                word_collection=word_collection, new_only=new_only, schedule_mode=schedule_mode,
                adaptive=True, handwriting_available=handwriting_available, audio_available=audio_available,
                _prioritize_advancement=False, _targets_only=_targets_only,
                _context_kana_ids=selected if selected else None)
            by_group.append(list(dict.fromkeys(q.content_id for q in batch)))
            for question in batch:
                questions_by_content.setdefault(question.content_id, []).append(question)
                word_ids.add(question.content_id)
    # Context exercises accompany selected kana without taking their places.
    # In word-only mode the requested limit instead counts example words.
    contextual = list(dict.fromkeys(cid for group in by_group for cid in group if cid in word_ids))
    if memory_ids:
        contextual = [cid for cid in contextual if any(
            set(question.metadata.get("context_kana_ids", ())) & selected
            for question in questions_by_content[cid])]
        contextual.sort(key=lambda cid: -len(set().union(*(
            set(question.metadata.get("context_kana_ids", ())) for question in questions_by_content[cid])) & selected))
    chosen.extend(contextual[:limit])
    card_priorities = {}
    if schedule_mode == "spaced":
        for card in store.scheduling_cards(aliases=True):
            if card["skill"] in WORD_SKILLS:
                continue
            state = json.loads(card["scheduler_state_json"]).get("state")
            card_priorities[card["content_id"], card["skill"], card["script"]] = (
                0 if state in (1, 3) else 1, card["due_at_utc"])
    result = []
    for round_index in range(rounds):
        _check_build_cancelled(store)
        by_content = {}
        for content_id in chosen:
            _check_build_cancelled(store)
            values = [question for question in questions_by_content[content_id]
                      if question.metadata["batch_round"] == round_index + 1]
            rng.shuffle(values)
            if schedule_mode == "spaced":
                values.sort(key=lambda question: card_priorities.get(
                    (question.content_id, question.kind, question.script), (2, "")))
            if values:
                by_content[content_id] = values
        ordered = (_interleave_spaced_batch(by_content, [cid for cid in chosen if by_content[cid]], mix_kinds=True)
                   if schedule_mode == "spaced" else
                   _interleave_batch(by_content, rng, result[-1].content_id if result else None))
        for question in ordered:
            metadata = {**question.metadata, "batch_content_count": memory_count,
                "practice_config": {**question.metadata["practice_config"],
                    "kinds": list(kinds), "requested_script": script,
                    "kana_categories": list(kana_categories) if kana_categories is not None else None,
                    "word_collection": word_collection}}
            result.append(replace(question, metadata=metadata))
    from .progress_backup import MAX_BYTES
    if not _targets_only and sum(len(json.dumps(question.to_dict(), ensure_ascii=False).encode("utf-8")) + 2
           for question in result) + 2 > MAX_BYTES:
        raise ValueError("本批题目数据过大，请减少轮数或记忆项。")
    return result


def build_practice_batch(repository, store: StudyStore, *, kinds, rounds: int = 1,
                         script: str = "hiragana", limit: int = 10, seed=None,
                         include_research: bool = False, now: datetime | None = None,
                         word_table: str = "partial", kana_categories=None,
                         word_collection: str | None = None, new_only: bool = False,
                         schedule_mode: str = "reinforce", adaptive: bool = False,
                         handwriting_available: bool = True,
                         audio_available: bool = True,
                         _targets_only: bool = False,
                         _prioritize_advancement: bool | None = None,
                         _context_kana_ids=None) -> list[Question]:
    """Select distinct memory items, then practice each through enabled skills.

    A ten-item, three-skill batch has thirty questions per round. Eligibility is
    checked per skill, so ambiguous listening items remain available in visual
    questions. Complete snapshots keep question order and choices fixed on resume.
    """
    _check_build_cancelled(store)
    if not isinstance(kinds, (tuple, list)) or not kinds:
        raise ValueError("kinds must be a nonempty tuple or list")
    if any(type(value) is not bool for value in (adaptive, handwriting_available, audio_available)):
        raise ValueError("Practice availability flags must be booleans")
    if _prioritize_advancement is None:
        _prioritize_advancement = (adaptive and schedule_mode == "spaced" and not new_only
                                   and len({kind for kind in kinds if isinstance(kind, str)}) > 1)
    if adaptive and (script == "both" or len({kind in WORD_SKILLS for kind in kinds if isinstance(kind, str)}) > 1):
        return _combined_adaptive_batch(repository, store, kinds=kinds, rounds=rounds,
            script=script, limit=limit, seed=seed, include_research=include_research, now=now,
            word_table=word_table, kana_categories=kana_categories, word_collection=word_collection,
            new_only=new_only, schedule_mode=schedule_mode,
            handwriting_available=handwriting_available, audio_available=audio_available,
            prioritize_advancement=_prioritize_advancement,
            _targets_only=_targets_only)
    for kind in kinds:
        if not isinstance(kind, str):
            raise ValueError("Unknown practice skill")
        _validate_key("batch", kind, script)
    kinds = tuple(dict.fromkeys(kinds))
    if len({kind in WORD_SKILLS for kind in kinds}) != 1:
        raise ValueError("A batch cannot mix word and kana practice kinds")
    if type(rounds) is not int or rounds <= 0:
        raise ValueError("rounds must be a positive integer")
    if schedule_mode not in {"reinforce", "spaced"}:
        raise ValueError("Unknown schedule mode")
    if schedule_mode == "spaced" and rounds != 1:
        raise ValueError("Spaced practice uses one scheduled round")
    if type(new_only) is not bool:
        raise ValueError("new_only must be a boolean")
    now = _utc(now)
    limit = max(0, int(limit))
    seed = random.SystemRandom().getrandbits(64) if seed is None else seed
    rng = random.Random(seed)
    config = dict(script=script, limit=limit, seed=seed, include_research=include_research,
                  now=now, word_table=word_table, kana_categories=kana_categories,
                  word_collection=word_collection, new_only=new_only)
    eligible_by_kind = {}
    candidates = []
    contexts = {}
    for kind in kinds:
        collector = _BatchQuestionStore(store)
        _check_build_cancelled(store)
        build_questions(repository, collector, kind=kind, _targets_only=True, **config)
        eligible_by_kind[kind] = set(collector.candidates)
        candidates.extend(collector.candidates)
        contexts.update(collector.contexts)
    candidates = list(dict.fromkeys(candidates))
    word_family = all(kind in WORD_SKILLS for kind in kinds)
    unavailable = set() if word_family else store._unavailable_content_ids(now)
    if new_only or word_family:
        unavailable |= store.queued_content_ids()
    candidates = [content_id for content_id in candidates if content_id not in unavailable]
    placeholders = ",".join("?" for _ in kinds)
    from .kana_routing import active_units, deduplicate_questions
    existing = [] if word_family else ([row for row in store.scheduling_cards(aliases=True)
        if row["script"] == script and row["skill"] in kinds] if active_units(store) else
        [dict(row) for row in store._db.execute(
        "SELECT content_id,skill,due_at_utc,suspended,review_count,scheduler_state_json FROM study_card "
        f"WHERE script=? AND skill IN ({placeholders})", (script, *kinds))])
    existing = [card for card in existing if card["content_id"] not in unavailable]
    existing_by_pair = {(card["content_id"], card["skill"]): card for card in existing}
    stages = _adaptive_stages(store, script, now) if adaptive else {}
    if word_family and adaptive:
        stages = {cid: min((stages.get(kana_id, 0) for kana_id in contexts.get(cid, ())), default=0)
                  for cid in candidates}
    if adaptive:
        available_kinds = tuple(kind for kind in kinds
            if (handwriting_available or kind not in HANDWRITING_SKILLS)
            and (audio_available or kind not in LISTENING_SKILLS))
        for content_id in candidates:
            _check_build_cancelled(store)
            stage = stages.get(content_id, 0)
            enabled = _adaptive_new_kinds(available_kinds, stage)
            for kind in kinds:
                existing_mode = existing_by_pair.get((content_id, kind))
                if kind not in available_kinds or (kind not in enabled and (existing_mode is None or existing_mode.get("mode_reviewed") is False)):
                    eligible_by_kind[kind].discard(content_id)
        candidates = [content_id for content_id in candidates
                      if any(content_id in eligible_by_kind[kind] for kind in kinds)]
    known_ids = {card["content_id"] for card in existing}
    known_pairs = {(card["content_id"], card["skill"]) for card in existing}
    suspended = {(card["content_id"], card["skill"]) for card in existing if card["suspended"]}
    due_dates = {}
    if not new_only:
        for card in existing:
            if (not card["suspended"] and card["review_count"] > 0
                    and card["due_at_utc"] <= now.isoformat()
                    and card["content_id"] in eligible_by_kind[card["skill"]]):
                content_id = card["content_id"]
                due_dates[content_id] = min(due_dates.get(content_id, card["due_at_utc"]), card["due_at_utc"])
    due_ids = list(due_dates)
    rng.shuffle(due_ids)
    due_ids.sort(key=due_dates.get)
    if schedule_mode == "spaced":
        learning_due = {card["content_id"] for card in existing
                        if not card["suspended"] and card["due_at_utc"] <= now.isoformat()
                        and json.loads(card["scheduler_state_json"]).get("state") in (1, 3)}
        due_ids.sort(key=lambda content_id: (content_id not in learning_due, due_dates[content_id]))
    chosen = due_ids[:limit]
    new_ids = [content_id for content_id in candidates if content_id not in due_dates
               and (content_id not in known_ids if new_only else any(
                   content_id in eligible_by_kind[kind] and (content_id, kind) not in known_pairs
                   for kind in kinds))]
    rng.shuffle(new_ids)
    if word_family:
        new_ids = store.select_word_context_ids(new_ids, contexts, script, len(new_ids), seed, now,
                                               preferred=_context_kana_ids)
    if _prioritize_advancement:
        advancement = {content_id for content_id in new_ids if any(
            content_id in eligible_by_kind[kind] and (content_id, kind) not in known_pairs
            and _unlocked_advanced_kind(kind, stages.get(content_id, 0)) for kind in kinds)}
        # Stable sorting preserves seeded randomness within each tier. Already
        # tested, not-yet-due cards remain excluded when questions are emitted.
        new_ids.sort(key=lambda content_id: content_id not in advancement)
    chosen.extend(new_ids[:max(0, limit - len(chosen))])
    if not chosen:
        return []
    from .progress_backup import MAX_BYTES
    questions, previous_options, snapshot_bytes = [], None, 2
    for round_index in range(rounds):
        _check_build_cancelled(store)
        by_content = {content_id: [] for content_id in chosen}
        # The first round consults prior sessions; each following round consults
        # this batch's previous round. Every generated choice list is snapshotted.
        next_options = {}
        round_config = {**config, "seed": seed if round_index == 0 else rng.getrandbits(64)}
        for kind in kinds:
            _check_build_cancelled(store)
            selected = [content_id for content_id in chosen if (content_id, kind) not in suspended
                        and content_id in eligible_by_kind[kind]]
            if schedule_mode == "spaced":
                selected = [content_id for content_id in selected
                            if (content_id, kind) not in existing_by_pair
                            or (existing_by_pair[content_id, kind]["review_count"] > 0
                                and existing_by_pair[content_id, kind]["due_at_utc"] <= now.isoformat())]
            selector = _BatchQuestionStore(store, selected, previous_options)
            for question in build_questions(repository, selector, kind=kind,
                                            _targets_only=_targets_only, **round_config):
                metadata = {**question.metadata, "session_seed": seed,
                            "batch_content_count": 0 if word_family else len(chosen), "batch_round": round_index + 1,
                            "practice_config": {**question.metadata["practice_config"],
                                                "kinds": list(kinds), "rounds": rounds,
                                                "requested_count": limit, "schedule_mode": schedule_mode}}
                if adaptive:
                    metadata.update({"adaptive_stage": stages.get(question.content_id, 0),
                                     "adaptive_policy": "scaffold-v1"})
                    metadata["practice_config"].update({"adaptive": True,
                        "handwriting_available": handwriting_available, "audio_available": audio_available,
                        "requested_script": script})
                by_content[question.content_id].append(replace(question, metadata=metadata))
                next_options.setdefault(kind, {})[question.content_id] = question.options
        previous_options = next_options
        if schedule_mode == "spaced":
            def direction_priority(question):
                card = existing_by_pair.get((question.content_id, question.kind))
                if card is None:
                    return (2, "", rng.random())
                state = json.loads(card["scheduler_state_json"]).get("state")
                return (0 if state in (1, 3) else 1, card["due_at_utc"], rng.random())
            for values in by_content.values():
                values.sort(key=direction_priority)
            ordered = _interleave_spaced_batch(by_content, [cid for cid in chosen if by_content[cid]],
                                                mix_kinds=adaptive)
            if not word_family:
                retained = deduplicate_questions(store, ordered, now, new_abilities=True)
                if len(retained) != len(ordered):
                    grouped = {}
                    for question in retained:
                        grouped.setdefault(question.content_id, []).append(question)
                    retained = _interleave_spaced_batch(grouped, list(grouped), mix_kinds=adaptive)
                ordered = retained
        else:
            ordered = _interleave_batch(by_content, rng, questions[-1].content_id if questions else None)
        if round_index == 0 and not _targets_only:
            validate_batch_size(ordered, rounds)
        if not _targets_only:
            snapshot_bytes += sum(len(json.dumps(question.to_dict(), ensure_ascii=False).encode("utf-8")) + 2
                                  for question in ordered)
        if snapshot_bytes > MAX_BYTES:
            raise ValueError("本批题目数据过大，请减少轮数或记忆项。")
        # Independent objects also prevent a stale earlier-round callback from
        # being mistaken for an answer to the current occurrence.
        questions.extend(ordered)
    return questions


def practice_plan_counts(repository, store: StudyStore, *, kinds, rounds=1, **config) -> dict:
    """Count the exact selected plan without materializing exercises or choices.

    The same eligibility, random selection, adaptive stages, script balancing,
    suspension, and due rules are used by the real builder. Only one lightweight
    round is needed; actual byte-size validation happens during generation.
    """
    if type(rounds) is not int or rounds <= 0:
        raise ValueError("rounds must be a positive integer")
    if config.get("schedule_mode", "reinforce") == "spaced" and rounds != 1:
        raise ValueError("Spaced practice uses one scheduled round")
    targets = build_practice_batch(repository, store, kinds=kinds, rounds=1,
                                   _targets_only=True, **config)
    counts = store.batch_counts(targets, now=config.get("now"))
    if "auxiliary_count" in counts:
        counts["auxiliary_count"] *= rounds
    kind_counts = {}
    for target in targets:
        kind_counts[target.kind] = kind_counts.get(target.kind, 0) + rounds
    return {**counts, "total": len(targets) * rounds, "kind_counts": kind_counts}


def batch_counts(store: StudyStore, questions: list[Question], now: datetime | None = None) -> dict:
    """Functional counterpart for callers that present a batch summary."""
    return store.batch_counts(questions, now=now)
