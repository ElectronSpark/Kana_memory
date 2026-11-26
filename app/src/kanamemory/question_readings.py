"""Kana for the kanji words a question shows before answering (design 11 section 7, K1/K2).

Pure display rules: no Toga, learner database, platform or app import. The optional
question_readings namespace (content_pack.QUESTION_READINGS, version 1; one shard per content
id, keyed like question_dependencies by kind and raw template sha256) lists the reading of
every kanji word in a template's visible strings:
    {"ids": [lesson id], "fields": {raw field: [SPAN]}, "options": {option text: [SPAN]}}
    SPAN = [start, end, reading, ref]  code-point offsets into the raw string, hiragana
                                       reading, ref = index into ids or -1 (no lesson)
This module decides from the learner's known ids which readings a page draws. Display only:
snapshots, drafts, queues, submitted values, grading and feedback text never change.

Display rules
1. A span is LEARNED when ref >= 0, the lesson ids[ref] is known (course_planning.known_ids;
   the question's own target counts as known: a word under test turns no kana on by itself) and
   that lesson teaches the word as written and read here: its title and spellings contain every
   kanji of the span and one of its readings contains the span's reading (a lesson without a
   reading, i.e. a grammar lesson, is tested by its kanji alone). Otherwise it is UNLEARNED: no
   lesson (ref -1), a lesson not learned yet, a word taught in another spelling (上げる for あげる,
   温かい for 暖かい) or read differently from the lesson (開 ひら for 開く あく, 来 き for 来る).
   The data's ref is therefore never trusted alone: a ref to a merely related lesson cannot hide
   a reading. A span whose lesson is the question's target_id, and which that lesson teaches as
   written and read, is the TARGET.
2. Reading-sensitive questions test a reading, pitch or pronunciation, listening, kana or the
   written form of their target: READING_SENSITIVE_KINDS (vocab_reading, vocab_reading_choice,
   vocab_audio_meaning, listening_comprehension, vocab_audio_word, vocab_recall,
   vocab_orthography, pronunciation_rule, pronunciation_application, grammar_audio_meaning; derived from the kind
   registry), any question flagged tests_reading / tests_pitch / requires_audio / audio_only /
   has_hidden_transcript or carrying audio_text, pitch_practice or prosody_cue, any reading or
   listening ability or pronunciation domain, and a word question answered by typing. They get
   no kana on any option. In their prompt: never on a TARGET span, never on a span sharing a
   kanji with the target's title, spellings or accepted answers, never when the span's reading
   equals an accepted answer or a reading of the target; other UNLEARNED spans keep their kana
   and LEARNED ones get none. A pronunciation or pitch question marks no target word (any prompt
   word may be the tested reading), so it gets no kana from its entry at all.
3. Other questions, sentence text (prompt, task): UNLEARNED spans always show kana, whatever the
   日文假名注音 switch says; LEARNED spans only while the switch is on; TARGET spans are never
   added here (the reviewed-annotation path keeps deciding the target).
4. Other questions, options (choice buttons, the word-bank words of every gap, reorder blocks):
   all or nothing per question, so that kana never tells one option from another. When at least
   one option has an UNLEARNED span, every option shows inline kana after every span it has,
   learned words and the target included: 妻（つま）, 学校（がっこう）へ, 食（た）べた. When every
   span of every option is LEARNED, no option shows kana. Only for kinds of the use and
   application abilities, whose options are Japanese the learner selects or arranges; the options
   of an understanding question are Chinese meanings and get nothing (a cognate meaning such as
   学校 must never be marked by kana). The stored and submitted value stays the raw string.
5. Reviewed annotations win (merged in question_support.prompt_support): a reviewed segment keeps
   its reading and accent data, and with the switch off it still shows its reading where it
   covers an UNLEARNED span. Readings from this module never carry accent numbers.
6. Never on a gap or placeholder. Inside practice.target_spans (the highlighted form) only a word
   that is not the question's own is drawn: a span is left out there when the question is
   reading-sensitive, when its lesson is the question's target, or when it shares a kanji or its
   reading with the target or an accepted answer (rule 2's test). A highlight such as 私の or
   見学することができます marks the grammar; the noun or verb inside it is an ordinary word and follows
   rules 1 and 3 (an unlearned one keeps its kana). The fixed prompts of rule 8 get none inside a
   target span. A reorder question's cue prompt (李さんは ＿＿ ＿＿) is marked as one target span, but
   what is tested is the order, so the shown block keeps its kana like the blocks.
7. Without the namespace, without an entry for the question's template, or with known_ids None,
   nothing is added: the page shows exactly what it showed before.
8. Fixed Japanese instructions and the constant prompts of generated kinds have no entry:
   _CUE_READINGS / _PROMPT_READINGS map each exact string to its kanji words (only while the
   namespace is installed, rule 7). A word shows kana until a course word lesson with that title
   and reading is known; a word the course does not teach always shows it. Rule 2's exclusions
   apply. Instructions are drawn inline (文脈（ぶんみゃく）で選（えら）ぶ), prompts through the prompt
   plan; the stored task and prompt strings never change.
   The course-word lookup (title, reading) of this rule is course_words().
9. Teaching-card quotations follow rule 3 with the same lookup (card_quote_readings): an unlearned
   word keeps its reading, a learned one only while the switch is on; result pages show all.
"""
from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
import re

from .content_caches import content_cache, register as register_content_cache
from .content_pack import QUESTION_READINGS, load_namespace
from .question_kinds import (REGISTRY, grammar_cloze, grammar_production, vocab_choice, vocab_context,
                             vocab_text)
from . import word_sound_questions as sound

# Absent in demo builds and in packs built before the readings tool: then nothing below adds kana.
_READINGS = load_namespace(QUESTION_READINGS, {})

_KANJI = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff々]")
_READING = re.compile(r"[ぁ-ゖー]{1,40}")
# The gap and placeholder shapes of question_support._GAP (this module may not import it).
_GAP = re.compile(r"[_＿□■◯○]|[（(\[【]\s*[）)\]】]|（[①-⑥1-6]）|［[1-6]］|\{[^}]*\}|<[^>]*>|\.\.\.|…")
_MAX_IDS = 60
# A derived variant (a heard twin) has the prompt, answer and variant number of its source: its source's entry.
_ENTRY_KINDS = REGISTRY.derived()
_SOUND_ABILITIES = frozenset({"reading", "listening"})
# Rule 4: abilities whose kinds offer Japanese options (understanding kinds offer Chinese meanings).
_JAPANESE_OPTION_ABILITIES = frozenset({"use", "application"})
_SENSITIVE_FLAGS = ("tests_reading", "tests_pitch", "requires_audio", "audio_only", "has_hidden_transcript",
                    "audio_text", "pitch_practice", "prosody_cue")
# Flags of a question whose prompt words are themselves the tested readings (no marked target).
_UNMARKED_FLAGS = ("tests_reading", "tests_pitch", "pitch_practice", "prosody_cue")
_SELECTION_MODES = frozenset({"choice", "reorder", "multi_cloze"})


def _sensitive_kind(spec):
    grader = spec.grader
    return bool(spec.reads_written_form or spec.needs_audio or spec.family == "pronunciation"
                or spec.ability in _SOUND_ABILITIES
                or grader is not None and (grader.normalizer == "reading"
                                           or grader.match is vocab_text.written_form_match))


# Rule 2, from the registry: kinds that read a written form, need audio, belong to the
# pronunciation family, train the reading / listening ability, or grade a reading or written form.
READING_SENSITIVE_KINDS = frozenset(kind for kind in REGISTRY.kinds() if _sensitive_kind(REGISTRY.get(kind)))

# ---- Rule 8: fixed instruction and prompt strings
# (surface, reading, lesson title, lesson reading): the span and the course word that teaches it.
_BUNMYAKU = ("文脈", "ぶんみゃく", "文脈", "ぶんみゃく")
_ERABU = ("選", "えら", "選ぶ", "えらぶ")
_GOGUN = ("語群", "ごぐん", "語群", "ごぐん")
_KIKU = ("聞", "き", "聞く", "きく")
_TANGO = ("単語", "たんご", "単語", "たんご")
_ONSEI = ("音声", "おんせい", "音声", "おんせい")
_YOMU = ("読", "よ", "読む", "よむ")
_KATA = ("方", "かた", "方", "かた")
_IMI = ("意味", "いみ", "意味", "いみ")
_BUN = ("文", "ぶん", "文", "ぶん")
_KIHONKEI = ("基本形", "きほんけい", "基本形", "きほんけい")
_KANJI_WORD = ("漢字", "かんじ", "漢字", "かんじ")
_KAKU = ("書", "か", "書く", "かく")
_ANAUME = ("穴埋", "あなう", "穴埋め", "あなうめ")
_GOJUN = ("語順", "ごじゅん", "語順", "ごじゅん")
_CUE_SEPARATOR = " · "


def _cue(text, *words):
    """(start, end, reading, lesson key) of the listed words, found in order; a word the text lacks is skipped."""
    spans, cursor = [], 0
    for surface, reading, title, lesson_reading in words:
        start = text.find(surface, cursor)
        if start >= 0:
            cursor = start + len(surface)
            spans.append((start, cursor, reading, (title, lesson_reading)))
    return tuple(spans)


# Every instruction question_presentation can show as Japanese study text (its _JAPANESE_CUES,
# pinned by test_question_readings), plus the raw reading task of the sound kinds.
_CUE_READINGS = {text: _cue(text, *words) for text, words in (
    (vocab_context.CONTEXT_CHOICE_TASK, (_BUNMYAKU, _ERABU)),              # 文脈で選ぶ
    (vocab_context.CONTEXT_WORD_BANK_TASK, (_BUNMYAKU, _ERABU, _GOGUN)),   # 文脈で選ぶ · 語群
    (sound.AUDIO_TASK, (_KIKU, _TANGO, _ERABU)),                           # 聞いて、単語を選ぶ
    (sound.AUDIO_KANA_TASK, (_KIKU, _ERABU)),                              # 聞いて、かなを選ぶ
    (sound.READING_TASK, (_YOMU, _KATA)),                                  # 読み方
    (vocab_text.READING, (_YOMU,)),                                        # 読み
    (vocab_choice.MEANING, (_IMI,)),                                       # 意味
    ("文の意味", (_BUN, _IMI)),                                             # vocab_choice reading_comprehension cue
    (vocab_text.WORD, (_TANGO,)),                                          # 単語
    ("単語 · かな", (_TANGO,)),                                             # vocab_text recall cue
    (vocab_text.BASE_FORM, (_KIHONKEI,)),                                  # 基本形
    ("基本形 · 漢字 / かな", (_KIHONKEI, _KANJI_WORD)),                      # vocab_text recall cue
    (vocab_choice.WRITING, (_KAKU, _KATA)),                                # 書き方
    (grammar_cloze.FILL_GAP, (_ANAUME,)),                                  # 穴埋め
    ("かなで穴埋め", (_ANAUME,)),                                           # question_presentation folded-hint cue
    (grammar_production.ORDER, (_GOJUN,)),                                 # 語順
)}
# Constant Japanese prompts of the generated heard-word task.
_PROMPT_READINGS = {text: _cue(text, *words) for text, words in (
    (sound.AUDIO_PROMPT, (_ONSEI, _KIKU, _TANGO, _ERABU)),                 # 音声を聞いて、単語を選んでください。
    (sound.AUDIO_KANA_PROMPT, (_ONSEI, _KIKU, _ERABU)),                    # 音声を聞いて、かなを選んでください。
)}


def _cue_spans(text):
    """Table spans of an instruction: an exact cue, or the cue leading a composite (読み · 吃, 穴埋め · 否定)."""
    spans = _CUE_READINGS.get(text)
    if spans is None and _CUE_SEPARATOR in text:
        spans = _CUE_READINGS.get(text.split(_CUE_SEPARATOR, 1)[0])
    return spans or ()


@content_cache
@lru_cache(maxsize=1)
def course_words():
    """(title, normal reading) -> ids of the course word lessons written and read so."""
    # The process-wide word projection the sound kinds already build: no second catalogue copy.
    from .learning_questions import _word_pool
    index = {}
    for identity, title, reading, *_ in _word_pool():
        index.setdefault((title, reading), set()).add(identity)
    return {key: frozenset(ids) for key, ids in index.items()}


# ---- Lessons and questions

def _strings(value):
    return [item for item in value if isinstance(item, str)] if isinstance(value, (list, tuple)) else []


def _kanji(text):
    return frozenset(_KANJI.findall(text))


@content_cache
@lru_cache(maxsize=1024)
def _lesson_forms(content_id):
    """(kanji of a lesson's title and spellings, its normal readings); empty for an unknown id.

    Cached for the process like the word pool: the catalogue is fixed while the app runs (content_caches).
    """
    from .learning_content import learning_item_view
    item = learning_item_view(content_id) or {}  # title, spellings and readings only: no copy of its templates
    written = [value for value in (item.get("title"), *_strings(item.get("spellings"))) if isinstance(value, str)]
    readings = [value for value in (item.get("reading"), *_strings(item.get("accepted_readings")),
                                    *_strings(item.get("alternative_readings"))) if isinstance(value, str) and value]
    return (frozenset().union(*map(_kanji, written)), frozenset(map(sound.normal_reading, readings)))


def reading_sensitive(question):
    """Rule 2: kana could give away what this question tests."""
    kind, ability = question.get("kind"), question.get("ability")
    mode = question.get("input_mode", question.get("response", "choice"))
    return bool(isinstance(kind, str) and kind in READING_SENSITIVE_KINDS
                or any(question.get(flag) for flag in _SENSITIVE_FLAGS)
                or isinstance(ability, str) and ability in _SOUND_ABILITIES
                or question.get("domain") == "pronunciation"
                or question.get("family") == "word" and not (isinstance(mode, str) and mode in _SELECTION_MODES))


def _unmarked_target(question):
    """Rule 2: a pronunciation or pitch question, whose tested word is not marked as its target."""
    kind = question.get("kind")
    spec = REGISTRY.get(kind) if isinstance(kind, str) else None
    return bool(question.get("domain") == "pronunciation" or spec is not None and spec.family == "pronunciation"
                or any(question.get(flag) for flag in _UNMARKED_FLAGS))


def hides_card_reading(question):
    """Rule 2 for teaching-card text shown on the page of ``question`` (card_quote_readings): a function
    (surface, reading) -> whether kana there would give away what the question tests, or None when it hides
    nothing. A reading-sensitive question hides every word sharing a kanji or its reading with its target or an
    accepted answer; a pronunciation or pitch question, whose tested word is not marked, hides every word."""
    if not isinstance(question, Mapping) or not reading_sensitive(question):
        return None
    if _unmarked_target(question):
        return lambda surface, reading: True
    return _Rules(question, ()).gives_away


def _checked_spans(text, spans, id_count):
    if not isinstance(text, str) or not isinstance(spans, list):
        return None
    result, cursor = [], 0
    for span in spans:
        if not isinstance(span, (list, tuple)) or len(span) != 4:
            return None
        start, end, reading, ref = span
        if (type(start) is not int or type(end) is not int or type(ref) is not int
                or not cursor <= start < end <= len(text) or not -1 <= ref < id_count
                or not isinstance(reading, str) or not _READING.fullmatch(reading)
                or not _KANJI.search(text[start:end])):
            return None
        result.append((start, end, reading, ref))
        cursor = end
    return tuple(result)


def _checked(entry, raw):
    """The entry with tuple spans bound to their raw strings; None when any part breaks the format."""
    if not isinstance(entry, Mapping) or set(entry) - {"ids", "fields", "options"}:
        return None
    ids, fields, options = entry.get("ids", []), entry.get("fields", {}), entry.get("options", {})
    if (not isinstance(ids, list) or len(ids) > _MAX_IDS or any(not isinstance(cid, str) or not cid for cid in ids)
            or not isinstance(fields, Mapping) or not isinstance(options, Mapping)):
        return None
    result = {"ids": tuple(ids), "fields": {}, "options": {}}
    for name, spans in fields.items():
        text = raw.get(name) if isinstance(name, str) else None
        spans = _checked_spans(text, spans, len(ids))
        if spans is None:
            return None
        result["fields"][name] = (text, spans)
    for text, spans in options.items():
        spans = _checked_spans(text, spans, len(ids))
        if spans is None:
            return None
        result["options"][text] = spans
    return result


def _locate(question, target_id, templates):
    """(sha256, lesson revision, prompt, answer, checked entry) of the question's raw template, or None."""
    from .learning_content import get_learning_item
    from .learning_questions import question_template, template_fingerprint
    item = get_learning_item(target_id)
    located = question_template(item, question) if item is not None else None
    if located is None or not isinstance(located[1], Mapping):
        return None
    lesson, raw = located
    sha = template_fingerprint(raw)
    return sha, lesson.get("revision"), raw.get("prompt"), raw.get("answer"), _checked(templates.get(sha), raw)


# (namespace, question key, located) of the last question: one display asks for it several times.
# located is read from the catalogue lesson, which the key does not name: dropped with the content caches.
_ENTRY_MEMO = (None, None, None)


def _clear_entry_memo():
    global _ENTRY_MEMO
    _ENTRY_MEMO = (None, None, None)


register_content_cache(_clear_entry_memo, "question_readings._ENTRY_MEMO")


def question_entry(question):
    """The checked entry of the question's raw template, or None.

    {"ids": (lesson id, ...), "fields": {name: (raw text, spans)}, "options": {raw text: spans}}
    with spans as (start, end, reading, ref) tuples; callers treat it as read-only. The located
    template must be the question's own: a frozen reviewed task by its template sha256, any other
    question by the lesson's current revision or, for an older snapshot, the identical prompt; and
    always the same answer. A question without such a template (AI, custom, legacy builder, an id
    only an alias resolves), a missing item / kind / template entry and a malformed entry give
    None; nothing is raised into the UI. An item or kind without an entry costs one shard lookup.
    """
    global _ENTRY_MEMO
    if not isinstance(question, Mapping) or question.get("ai_generated"):
        return None
    target_id, kind = question.get("target_id"), question.get("kind")
    if not isinstance(target_id, str) or not isinstance(kind, str):
        return None
    kind = _ENTRY_KINDS.get(kind, kind)
    namespace = _READINGS
    try:
        if namespace.get("schema_version") != 1:
            return None
        templates = namespace["items"].get(target_id)
        templates = templates.get(kind) if isinstance(templates, Mapping) else None
        if not isinstance(templates, Mapping) or not templates:
            return None
        source = question.get("practice_source")
        frozen = source.get("template_sha256") if isinstance(source, Mapping) else None
        key = (target_id, kind, question.get("variant"), frozen)
        memo = _ENTRY_MEMO
        if memo[0] is namespace and memo[1] == key:
            located = memo[2]
        else:
            located = _locate(question, target_id, templates)
            _ENTRY_MEMO = (namespace, key, located)
        if located is None:
            return None
        sha, revision, prompt, answer, entry = located
        if entry is None or answer != question.get("answer"):
            return None
        # After a lesson revision a variant number may name another template: then only the same sentence.
        own = (frozen == sha if frozen is not None else
               revision == question.get("content_revision", question.get("revision"))
               or prompt == question.get("prompt"))
        return entry if own else None
    except (ValueError, KeyError, TypeError, AttributeError, IndexError):
        return None


def _active():
    """Rule 7: the namespace is installed (the fixed tables of rule 8 follow it)."""
    return isinstance(_READINGS, Mapping) and _READINGS.get("schema_version") == 1


def transfer_spans(raw_text, spans, shown_text):
    """Spans of raw_text re-anchored on the text a page shows.

    Identical text keeps them. Otherwise each surface is searched in order from a moving
    cursor; a surface the shown text lacks is dropped.
    """
    if raw_text == shown_text:
        return [tuple(span) for span in spans]
    result, cursor = [], 0
    for start, end, *rest in spans:
        surface = raw_text[start:end]
        found = shown_text.find(surface, cursor)
        if found >= 0:
            cursor = found + len(surface)
            result.append((found, cursor, *rest))
    return result


def inline_kana(text, spans):
    """text with （reading） after each (start, end, reading) span: 学校（がっこう）へ, 食（た）べた."""
    parts, cursor = [], 0
    for start, end, reading in spans:
        parts.append(text[cursor:end] + "（" + reading + "）")
        cursor = end
    return "".join(parts) + text[cursor:]


# ---- Display rules

class _Rules:
    """The facts of one question that its span tests share."""

    def __init__(self, question, known_ids):
        target = question.get("target_id")
        self.question = question
        self.target = target if isinstance(target, str) else None
        self.known = known_ids if isinstance(known_ids, (set, frozenset)) else frozenset(known_ids)
        self.sensitive = reading_sensitive(question)
        self._protected = None

    def knows(self, lesson):
        return lesson == self.target or lesson in self.known

    @staticmethod
    def taught(surface, reading, lesson):
        """Rule 1: the lesson teaches this word as written and as read here."""
        kanji, readings = _lesson_forms(lesson)
        return _kanji(surface) <= kanji and (not readings or any(reading in value for value in readings))

    def learned(self, surface, reading, ref, ids):
        """Rule 1: a known lesson (or the target) that teaches the word in this spelling and reading."""
        return ref >= 0 and self.knows(ids[ref]) and self.taught(surface, reading, ids[ref])

    def gives_away(self, surface, reading):
        """Rule 2: the span shares a kanji or its reading with the target or an accepted answer."""
        if self._protected is None:
            kanji, readings = _lesson_forms(self.target) if self.target else (frozenset(), frozenset())
            kanji, readings = set(kanji), set(readings)
            question = self.question
            for value in (question.get("answer"), *_strings(question.get("accepted_answers")),
                          *_strings(question.get("alternative_answers"))):
                if isinstance(value, str):
                    kanji.update(_KANJI.findall(value))
                    readings.add(sound.normal_reading(value))
            self._protected = (kanji, readings)
        kanji, readings = self._protected
        return bool(_kanji(surface) & kanji) or reading in readings

    def shows(self, surface, reading, ref, ids, furigana):
        """Rules 2 and 3 for one span of sentence text."""
        if ref >= 0 and ids[ref] == self.target and self.taught(surface, reading, self.target):
            return False
        learned = self.learned(surface, reading, ref, ids)
        if self.sensitive:
            return not learned and not self.gives_away(surface, reading)
        return furigana or not learned

    def beside_target(self, surface, reading, ref, ids):
        """Rule 6: inside a highlighted span, a word that is not the question's own (私 in 私の)."""
        return not (self.sensitive or ref >= 0 and ids[ref] == self.target or self.gives_away(surface, reading))

    def cue_shows(self, surface, reading, lesson):
        """Rule 8 for one word of a fixed string."""
        if any(self.knows(identity) for identity in course_words().get(lesson, ())):
            return False
        return not (self.sensitive and self.gives_away(surface, reading))


def _sentence_spans(question, field, shown, rules, furigana, *, exact=False, reserved=()):
    """(start, end, reading) of the entry spans of one raw field that rules 2 and 3 show on shown.

    exact: only when shown is the raw string itself (an instruction may be a rewritten cue).
    reserved: (start, end) ranges of shown that are the highlighted target (rule 6).
    """
    if _unmarked_target(question):
        return []
    entry = question_entry(question)
    located = entry["fields"].get(field) if entry is not None else None
    if located is None or exact and located[0] != shown:
        return []
    raw, spans = located
    return [(start, end, reading) for start, end, reading, ref in transfer_spans(raw, spans, shown)
            if rules.shows(shown[start:end], reading, ref, entry["ids"], furigana)
            and (not _overlaps(start, end, reserved)
                 or rules.beside_target(shown[start:end], reading, ref, entry["ids"]))]


def _overlaps(start, end, ranges):
    return any(first < end and start < last for first, last in ranges)


def _target_ranges(question):
    practice = question.get("practice")
    spans = practice.get("target_spans") if isinstance(practice, Mapping) else None
    return [(span["start"], span["end"]) for span in (spans if isinstance(spans, list) else ())
            if isinstance(span, Mapping) and type(span.get("start")) is int and type(span.get("end")) is int]


def prompt_readings(question, known_ids, *, furigana=True):
    """Sorted (start, end, reading) spans to draw on the displayed prompt (rules 1-3 and 6-8).

    furigana is the 日文假名注音 switch: off, only unlearned words are listed.
    """
    if (known_ids is None or not isinstance(question, Mapping) or not _active()
            or question.get("prompt_visible") is False or question.get("audio_only")):
        return []
    shown = question.get("prompt")
    if not isinstance(shown, str) or not shown:
        return []
    rules = _Rules(question, known_ids)
    # Rule 6: the whole cue line of a reorder task is its target span; the order is tested, not a reading.
    reserved = [] if question.get("input_mode") == "reorder" else _target_ranges(question)
    fixed = _PROMPT_READINGS.get(shown)
    if fixed is not None:
        spans = [(start, end, reading) for start, end, reading, lesson in fixed
                 if rules.cue_shows(shown[start:end], reading, lesson) and not _overlaps(start, end, reserved)]
    else:
        spans = _sentence_spans(question, "prompt", shown, rules, furigana, reserved=reserved)
    return [(start, end, reading) for start, end, reading in spans if not _GAP.search(shown[start:end])]


def option_labels(question, known_ids, shown=None):
    """Raw option -> label with inline kana (rule 4); an option without a label is shown as written.

    All or nothing: {} unless some option of the template has an unlearned word; then every
    option is labelled with all its readings, so the answer never differs from its distractors.
    shown maps a raw option to the fragment a page displays in its place (a shortened word-bank
    choice); the label is then that fragment with the readings that fall inside it. Callbacks
    keep submitting the raw option.
    """
    if (known_ids is None or not isinstance(question, Mapping) or not _active()
            or reading_sensitive(question)):
        return {}
    kind = question.get("kind")
    spec = REGISTRY.get(kind) if isinstance(kind, str) else None
    if spec is None or spec.ability not in _JAPANESE_OPTION_ABILITIES:
        return {}
    entry = question_entry(question)
    if entry is None or not entry["options"]:
        return {}
    rules, ids = _Rules(question, known_ids), entry["ids"]
    # Decided on the raw options of the whole template (every gap of a word bank), not on what one page shows.
    if not any(not rules.learned(raw[start:end], reading, ref, ids)
               for raw, spans in entry["options"].items() for start, end, reading, ref in spans):
        return {}
    labels = {}
    for raw, spans in entry["options"].items():
        text = raw if shown is None else shown.get(raw)
        if not isinstance(text, str):
            continue
        visible = [(start, end, reading) for start, end, reading, _ in transfer_spans(raw, spans, text)]
        if visible:
            labels[raw] = inline_kana(text, visible)
    return labels


def instruction_label(question, instruction, known_ids):
    """The displayed instruction with inline kana for its unlearned words, or None when nothing is added.

    A fixed cue uses the table of rule 8; a Japanese task shown as written uses its entry spans.
    """
    if (known_ids is None or not isinstance(question, Mapping) or not isinstance(instruction, str)
            or not instruction or not _active()):
        return None
    rules = _Rules(question, known_ids)
    table = _cue_spans(instruction)
    if table:
        spans = [(start, end, reading) for start, end, reading, lesson in table
                 if rules.cue_shows(instruction[start:end], reading, lesson)]
    else:
        spans = _sentence_spans(question, "task", instruction, rules, False, exact=True)
    return inline_kana(instruction, spans) if spans else None


def visible_readings(question, known_ids, *, furigana=True):
    """Everything this module adds to one displayed question.

    {"prompt": [(start, end, reading)] on the displayed prompt, "options": {raw option: label},
    "instruction": the displayed instruction with inline kana, or None}.
    """
    from .question_presentation import question_presentation
    instruction = question_presentation(question)["instruction"] if isinstance(question, Mapping) else ""
    return {"prompt": prompt_readings(question, known_ids, furigana=furigana),
            "options": option_labels(question, known_ids),
            "instruction": instruction_label(question, instruction, known_ids)}
