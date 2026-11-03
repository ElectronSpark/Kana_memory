"""Independent word drills in both directions, without word-level memory cards.

The complete attributed vocabulary supplies cues. Selection remembers only
recent exposure, never word mastery; kana evidence is handled by StudyStore.
"""
from __future__ import annotations

from collections import defaultdict
import random
import re

from .domain import (PRACTICE_WORD_COLLECTIONS, Question, _check_build_cancelled,
                     _utc, _word_contexts, _word_keyboard, _word_meaning,
                     normalize_answer, to_script, word_kana_answer, word_romaji_unit_plan)
from .romanization import romanize_kana
from .kana_orthography import ORTHOGRAPHY_POLICY, allowed_word_spellings
from .word_keyboard import word_answer_letters
from .kana_question_policy import direction_allowed, question_allowed


WORD_ROMAJI_COLLECTIONS = {
    "全部词库": None,
    "英语外来语": "english_loanwords",
    "本土及汉字词": "japanese_core",
    "基础词": "foundation_listening",
    "日常补充": "everyday_supplement",
}
WORD_DRILL_DIRECTIONS = {
    "双向混合": "mixed",
    "罗马音 → 假名": "kana",
    "假名 → 选罗马音": "romaji",
}
WORD_DIRECTION_KINDS = {
    "mixed": ("word_meaning_kana", "word_romaji"),
    "kana": ("word_meaning_kana",),
    "romaji": ("word_romaji",),
}
RECENT_WORD_LIMIT = 100
_KANA = re.compile(r"[ぁ-ゖァ-ヺー]+\Z")
_HIRAGANA = re.compile(r"[ぁ-ゖ]")
_KATAKANA = re.compile(r"[ァ-ヺ]")
_ROMAJI = re.compile(r"[a-zāīūēō']+\Z")


def _validate(script, word_collection):
    if not isinstance(script, str) or script not in {"native", "hiragana", "katakana"}:
        raise ValueError("script must be native, hiragana or katakana")
    if word_collection is not None and (not isinstance(word_collection, str) or word_collection not in PRACTICE_WORD_COLLECTIONS):
        raise ValueError("Unknown word practice collection")


def _pool(repository, script, word_collection, word_direction="kana"):
    _validate(script, word_collection)
    if word_direction not in WORD_DIRECTION_KINDS:
        raise ValueError("Unknown word practice direction")
    kwargs = {"collection": word_collection} if word_collection is not None else {}
    result = []
    for row in repository.practice_word_candidates(include_research=True, **kwargs):
        original = row.get("reading") or row.get("display_reading") or ""
        if not isinstance(original, str):
            continue
        original = normalize_answer(original)
        if not _KANA.fullmatch(original):
            continue
        hira, kata = bool(_HIRAGANA.search(original)), bool(_KATAKANA.search(original))
        if not (hira or kata) or (script == "native" and hira and kata):
            continue
        alphabet = ("katakana" if kata else "hiragana") if script == "native" else script
        answer = to_script(original, alphabet)
        # Rare extended letters are not always convertible by Unicode offset.
        if (alphabet == "hiragana" and _KATAKANA.search(answer)) or (alphabet == "katakana" and _HIRAGANA.search(answer)):
            continue
        romaji = romanize_kana(original)
        if not _ROMAJI.fullmatch(romaji):
            continue
        meaning = _word_meaning(row)
        # A future gloss may quote a Japanese spelling. Keep it in the detail
        # page, but do not expose any kana in the question's meaning hint.
        meaning = re.sub(r"[ぁ-ゖァ-ヺー]+", "…", meaning)
        content_id = row.get("id", row.get("entry_id"))
        if not isinstance(content_id, str) or not content_id:
            continue
        if not any(direction_allowed(content_id, kind, alphabet, symbol=answer)
                   for kind in WORD_DIRECTION_KINDS[word_direction]):
            continue
        accepted = tuple(value for value in allowed_word_spellings(answer) if value != answer)
        result.append({**row, "id": content_id, "original": original,
                       "script": alphabet, "answer": answer,
                       "romaji": romaji, "meaning": meaning, "accepted": accepted})
    return result


def word_romaji_pool_counts(repository, *, script="native", word_collection=None, word_direction="kana"):
    """Catalog coverage, plus the actual number of distinct spelling questions.

    Counts are independent of learner history and do not construct keyboards.
    Homonymous entries remain separate dictionary words but share one slot.
    """
    records = _pool(repository, script, word_collection, word_direction)
    keys = {(row["script"], row["answer"]) for row in records}
    return {"word_count": len(records), "question_count": len(keys),
            "hiragana_count": sum(alphabet == "hiragana" for alphabet, _ in keys),
            "katakana_count": sum(alphabet == "katakana" for alphabet, _ in keys)}


def _recent_exposures(store, now):
    """Bounded exposure history, including assisted answers and former word cards."""
    words, spellings = {}, {}
    rows = store.queries.recent_word_exposures(now, RECENT_WORD_LIMIT)
    for row in rows:
        words.setdefault(row["content_id"], row["occurred_at_utc"])
        if isinstance(row["expected"], str):
            spellings.setdefault((row["script"], normalize_answer(row["expected"])), row["occurred_at_utc"])
    return words, spellings


def reverse_word_question(row, metadata):
    """One fixed reverse spelling target, shared by batches and replacements."""
    answer = row['answer']
    metadata['kana_answer'] = answer
    question = Question(row['id'], 'word_romaji', row['script'],
        '看单词假名，逐个选择罗马音片段：' + answer,
        row['romaji'], [], row['original'], (), metadata)
    metadata['romaji_unit_plan'] = word_romaji_unit_plan(question)
    return question


def build_word_romaji_drill(repository, store, *, limit=10, seed=None,
                            script="native", word_table="partial",
                            word_collection=None, word_direction="kana", now=None):
    """Build a read-only randomized batch for the independent word_romaji lane.

    Recent words/spellings wait behind other candidates. Within that boundary,
    a word containing a due or weak kana has at most twice the random weight;
    extra syllables do not multiply its priority or exclude unfamiliar words.
    The actual batch is bounded by the available distinct spelling pool only.
    """
    _validate(script, word_collection)
    if type(limit) is not int or limit < 0:
        raise ValueError("limit must be a nonnegative integer")
    if word_table not in {"partial", "full"}:
        raise ValueError("word_table must be partial or full")
    if word_direction not in WORD_DIRECTION_KINDS:
        raise ValueError("Unknown word practice direction")
    _check_build_cancelled(store)
    if not limit:
        return []
    now = _utc(now)
    seed = random.SystemRandom().getrandbits(64) if seed is None else seed
    rng = random.Random(seed)
    records = _pool(repository, script, word_collection, word_direction)
    pending = store.load_session_progress(lane="word_romaji")
    reserved_ids, reserved_spellings = set(), set()
    direction_counts = dict.fromkeys(WORD_DIRECTION_KINDS["mixed"], 0)
    if isinstance(pending, dict):
        for question in pending.get("questions", ()):
            if not question_allowed(question):
                continue
            reserved_ids.add(question.content_id)
            reserved_spellings.add((question.script, normalize_answer(word_kana_answer(question))))
            if question.kind in direction_counts:
                direction_counts[question.kind] += 1
    groups = defaultdict(list)
    for row in records:
        key = row["script"], row["answer"]
        if row["id"] not in reserved_ids and key not in reserved_spellings:
            groups[key].append(row)
    recent_words, recent_spellings = _recent_exposures(store, now)
    contexts = {alphabet: _word_contexts(repository,
        [row for row in records if row["script"] == alphabet], alphabet)
        for alphabet in ("hiragana", "katakana")}
    due = {(card["content_id"], card["script"]) for card in store.get_due(now=now)}
    weak = {(entry["content_id"], entry["script"]):
            entry.get("weak_weight", 1 if entry.get("weak") else 0)
            for entry in store.word_spelling_summary(now=now)}
    ranked = []
    for key, values in groups.items():
        _check_build_cancelled(store)
        rng.shuffle(values)
        row = values[0]
        # Same-reading entries share recency even when the dictionary id differs.
        recent = max([recent_spellings.get(key, ""),
                      *(recent_words.get(value["id"], "") for value in values)])
        units = {(cid, row["script"]) for cid in contexts[row["script"]].get(row["id"], ())}
        weakness = max((weak.get(unit, 0) for unit in units), default=0)
        weight = 1 + .5 * bool(units & due) + .5 * weakness
        random_priority = rng.random() ** (1 / weight)
        ranked.append((bool(recent), recent, -random_priority, row))
    ranked.sort(key=lambda item: item[:3])
    symbols = {alphabet: repository.all_kana_symbols(alphabet)
               for alphabet in ("hiragana", "katakana")}
    content_version = repository.metadata().get("content_version", "1")
    questions = []
    for _, _, _, row in ranked:
        _check_build_cancelled(store)
        answer, alphabet = row["answer"], row["script"]
        enabled = tuple(kind for kind in WORD_DIRECTION_KINDS[word_direction]
                        if direction_allowed(row["id"], kind, alphabet, symbol=answer))
        fewest = min(direction_counts[kind] for kind in enabled)
        candidates = [kind for kind in enabled if direction_counts[kind] == fewest]
        kind = candidates[0] if len(candidates) == 1 else rng.choice(candidates)
        accepted = row["accepted"]
        tokens = word_answer_letters(answer)
        options = (_word_keyboard(tokens, symbols[alphabet], alphabet, word_table, rng)
                   if kind == "word_meaning_kana" else [])
        # Partial keyboards must permit the explicitly approved spelling too.
        additions = [letter for value in accepted for letter in value if letter not in options]
        if additions and kind == "word_meaning_kana":
            options.extend(dict.fromkeys(additions))
            if word_table == "partial":
                rng.shuffle(options)
        config = {"kinds": list(WORD_DIRECTION_KINDS[word_direction]), "rounds": 1,
                  "requested_count": limit, "requested_script": script,
                  "schedule_mode": "reinforce", "adaptive": False,
                  "include_research": True, "include_extended": True,
                  "word_table": word_table, "word_collection": word_collection,
                  "kana_categories": None, "new_only": False,
                  "handwriting_available": False, "audio_available": False,
                  "word_romaji_drill": True}
        if word_direction != "kana":
            config["word_direction"] = word_direction
        metadata = {"word_romaji_drill": True, "session_seed": seed,
                    "orthography_policy": ORTHOGRAPHY_POLICY,
                    "content_version": content_version,
                    "audio_only": False, "context_given": True,
                    "new_only": False, "batch_content_count": 0, "batch_round": 1,
                    "original_reading": row["original"], "romaji": row["romaji"],
                    "meaning_hint": row["meaning"], "answer_tokens": tokens,
                    "word_table": word_table, "word_collection": word_collection,
                    "research_content": row.get("practice_approved") is not True,
                    "context_kana_ids": contexts[alphabet].get(row["id"], []),
                    "practice_config": config}
        if kind == "word_romaji":
            question = reverse_word_question(row, metadata)
        else:
            question = Question(row["id"], kind, alphabet,
                "根据罗马音，用" + ("平假名" if alphabet == "hiragana" else "片假名") +
                "拼出单词。罗马音：" + row["romaji"] + ("；词义：" + row["meaning"] if row["meaning"] else ""),
                answer, options, row["original"], accepted, metadata)
        questions.append(question)
        direction_counts[kind] += 1
        if len(questions) >= limit:
            break
    return questions
