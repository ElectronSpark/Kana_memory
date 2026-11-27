"""Complete missing reading annotations: the course's own knowledge first, then an offline analyser.

Design: docs/sentence_annotations.txt ("补全注音") and the teaching cards of design 02. A content tool run by hand;
it writes CANDIDATES into --out and never into the app's resources: the content side reviews and lands them.

  python -X utf8 tools/annotate_readings.py --out <folder> [--analyser-python <python with pyopenjtalk-plus>]
         [--kinds sentences,cards,explanations] [--agreement] [--overrides FILE] [--markers FILE] [--limit N]

Targets
  sentences  each exact text of tools/sentence_annotation_corpus.py (example sentences and the Japanese of question
             prompts) that has kanji and no row in the sentence annotations (--annotations, default: the app's own
             resolved copy, content_pack.side_file_path): a candidate row in that file's format
             (kanamemory-sentence-annotations v1, table sentence; segments without accent numbers). A gap of a
             prompt is never analysed as text: the pieces between gaps are analysed one by one. Segments are made
             only where there is a reading; every record also carries word_boundaries: [start, end] of each word as a
             learner reads it (word_boundaries: a number with its counter, a word with its suffixes and prefix, a
             verb or adjective with its auxiliaries and て／で／ば; particles and です／だ after a noun on their own;
             kana course words of the question whole; no punctuation or spaces; a pair never cuts a segment; a
             token not placed exactly gets none and is counted). --edition landing (default) adds the reading
             rules of the landing edition (text_spans, rule 3 of the cards); development keeps the readings of
             2026-11-25.
  explanations  each Japanese line of the explanations under answers (EXPLANATION_RULE) on which the result page as
             it is today leaves a kanji run without a reading for a question that shows it is a sentence text like the
             above (label explanation_line); explanation_lines.json lists each line with the questions showing it,
             those where it has an unread run, and the unread runs.
  cards      each quotation with kanji inside 「」 of teaching-card prose (card_quote_readings.card_fields of each
             lesson's view and original sections): spans in the card_quote_readings.json format (version 2),
             keyed by content id, field and quotation. An entry of --card-readings (default: the app's resolved
             copy) is kept; a version-1 file there counts as no entries (a line in summary.json says so).
             Every card span carries its course word: [written form, reading] of the course word lesson whose
             written form is the token's dictionary or written form and whose reading is the one chosen here
             for that form (whatever the span's source), else null; only a key the app's lookup
             (question_readings.course_words) finds is written.
  An existing annotation is never overwritten: a text or quotation that has one is not a target.

Sources, in order; every span records its own
  override   a rule of --overrides (the reviewed reading corrections of the question readings) whose span, lemma
             and before/after context match (rules bound to a lesson, when_ref, are skipped);
  homograph  a word whose written title several lessons share (開く あく／ひらく): the first rule of --markers
             homograph_by_context that holds at the occurrence, else its "otherwise" rule;
  course     a word whose dictionary form or written form is a word lesson's written form with one reading (an
             inflected form takes the lesson reading's stem and the surface's kana tail: 使え from 使う つかう);
             a written form the lessons read in several ways is left to the analyser (and so to review);
  analyser   pyopenjtalk-plus (AnalyserAdapter: a subprocess under its own interpreter; tests inject a fake).
The analyser is known to read some heteronyms against the course (店は九時に開く: ひらく, where the homograph
rule says あく): the course decides first, and every analyser span of a heteronym goes to the review file.

Outputs in --out
  sentence_annotations.candidates.sqlite3  candidate rows (same tables as the app's file; meta candidate=1)
  card_quote_readings.candidates.json      the whole card file: existing entries plus the new ones
  explanation_lines.json  with explanations: {rule, lines: [{line, question_ids, unread_question_ids, unread_runs,
                  annotated, candidate}]}
  review.json     flagged spans: "heteronym" (an analyser span of a word with several readings in the course's
                  lessons or dictionary, or in HETERONYMS) and "coverage" (a text the analyser's tokens do not
                  cover exactly; "unknown" tokens without a usable reading)
  agreement.json  with --agreement: the analyser read over texts that have reviewed annotations, per span
  summary.json    totals per kind, per source and per flag
  annotation_source.json  the source record of course-decided spans (COURSE_SOURCE) that the content side adds
                  to presentation_metadata.annotation_sources before it lands sentence candidates
Exit codes: 0 done, 2 usage, 3 the analyser is not available (the message names what is missing).
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app" / "src"), str(ROOT / "tools")]

from kanamemory import card_quote_readings as card_format  # noqa: E402
from kanamemory import sentence_annotations as sentence_format  # noqa: E402

HELPER = Path(__file__).with_name("annotate_readings_analyser.py")
ANALYSER_SOURCE = "openjtalk-context-reading"  # existing source id of pyopenjtalk-plus context readings
COURSE_SOURCE = "course-lesson-reading"
COURSE_SOURCE_RECORD = {"id": COURSE_SOURCE, "title": "课程单词与读法规则：本课程所学单词的读法", "url": "",
                        "note": "按课程单词的读法、已审核的读法修正与同形词规则确定；不代表逐句人工核对。"}
EXIT_NO_ANALYSER = 3
KANJI = re.compile(r"[㐀-䶿一-鿿豈-﫿々〆]")
KANA = re.compile(r"[ぁ-ゖァ-ヺー]")
HIRAGANA_READING = re.compile(r"[ぁ-ゖー]+")
# A gap or placeholder of a question prompt (the shapes of question_support's gaps): never analysed as text.
GAP = re.compile(r"[_＿□■◯○]+|[（(\[【]\s*[）)\]】]|（[①-⑥1-6]）|［[1-6]］|\.\.\.|…")
# Common heteronyms: an analyser span of one of these always goes to review (dictionary forms and surfaces).
HETERONYMS = frozenset("今日 明日 昨日 一日 何 方 人 日 月 上手 下手 辛い 開く 行く 行う 行った 入る 他 間 生 中 上 下 後 前 家 等"
                       .split())
# Irregular stems: the inflection rule cannot derive their readings (来ます き, 来ない こ).
IRREGULAR_LEMMAS = frozenset({"来る", "來る"})
SOURCES = ("override", "homograph", "course", "analyser")


class AnalyserUnavailable(RuntimeError):
    """The analyser cannot be run here (no interpreter, no pyopenjtalk)."""


@dataclass(frozen=True)
class Token:
    surface: str
    lemma: str
    reading: str  # hiragana
    pos: str = ""
    pos1: str = ""
    pos2: str = ""
    cform: str = ""
    pron: str = ""  # hiragana; the analyser's pronunciation after its sound changes (三本: さん ぼん)

    def spec_tuple(self):
        """The token as the homograph rules' token specs read it (surface, lemma, lemma, reading, pos, p1, p2,
        -, conjugation form): the layout of the question-dependency tool's Sudachi tokens."""
        pos = "補助記号" if self.pos == "記号" else self.pos
        return (self.surface, self.lemma, self.lemma, self.reading, pos, self.pos1, self.pos2, "", self.cform)


def hiragana(text):
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in unicodedata.normalize("NFKC", text or ""))


class AnalyserAdapter:
    """The one way to the analyser: tokenise(texts) -> ({text: [Token]}, engine version)."""

    def tokenise(self, texts):
        raise NotImplementedError


class SubprocessAnalyser(AnalyserAdapter):
    """tools/annotate_readings_analyser.py under the interpreter that has pyopenjtalk-plus (never imported here)."""

    def __init__(self, python, timeout=3600):
        self.python, self.timeout = Path(python), timeout

    def tokenise(self, texts):
        if not self.python.is_file():
            raise AnalyserUnavailable(f"No analyser interpreter at {self.python} (an environment with "
                                      "pyopenjtalk-plus; pass --analyser-python)")
        texts = sorted(set(texts))
        with tempfile.TemporaryDirectory(prefix="annotate-") as folder:
            source, target = Path(folder) / "texts.json", Path(folder) / "tokens.json"
            source.write_text(json.dumps(texts, ensure_ascii=False), "utf-8")
            done = subprocess.run([str(self.python), "-I", "-X", "utf8", str(HELPER), str(source), str(target)],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=self.timeout)
            if done.returncode == EXIT_NO_ANALYSER:
                raise AnalyserUnavailable(done.stderr.strip()[-400:] or "pyopenjtalk is not installed")
            if done.returncode != 0:
                raise RuntimeError("The analyser failed: " + done.stderr.strip()[-800:])
            data = json.loads(target.read_text("utf-8"))
        tokens = {text: [Token(row[0], row[1] or row[0], hiragana(row[2]), *row[3:7],
                               pron=hiragana(row[7]) if len(row) > 7 else "") for row in rows]
                  for text, rows in data["tokens"].items()}
        return tokens, data.get("engine", "")


# ----------------------------------------------------------------------------- course knowledge

def _strings(value):
    return [v for v in value if isinstance(v, str) and v] if isinstance(value, (list, tuple)) else []


class CourseKnowledge:
    """What the course knows about readings: word lessons, reviewed overrides, homograph rules, dictionary."""

    def __init__(self, words=None, overrides=(), homographs=None, dictionary=None, word_keys=(), own_chars=None):
        self.words = {form: dict(readings) for form, readings in (words or {}).items()}  # form -> {reading: [ids]}
        # lesson id -> {kanji: reading} that the lesson itself states for one character (lesson_own_chars)
        self.own_chars = {key: dict(value) for key, value in (own_chars or {}).items()}
        # (written form, reading) of the course word lessons as the app looks them up (card spans' fifth element).
        self.word_keys = defaultdict(set)
        for title, reading in word_keys:
            self.word_keys[title].add(reading)
        self.overrides = [rule for rule in overrides if isinstance(rule, dict) and isinstance(rule.get("span"), str)
                          and "when_ref" not in rule and (rule.get("reading") is None
                                                          or HIRAGANA_READING.fullmatch(str(rule["reading"])))]
        self.homographs = {title: rules for title, rules in (homographs or {}).items()
                           if not title.startswith("_") and isinstance(rules, list)}
        self.dictionary = {form: set(values) for form, values in (dictionary or {}).items()}

    @classmethod
    def from_items(cls, items, *, overrides=(), homographs=None, dictionary=None, word_keys=None):
        """word_keys: the app's (title, reading) keys of the course word lessons; None: those of ``items``
        by the app's own projection (word_sound_questions.word_pool, the source of course_words)."""
        if word_keys is None:
            from kanamemory.word_sound_questions import word_pool
            word_keys = {(row[1], row[2]) for row in word_pool(item for item in items if item.get("family") == "word")}
        words = defaultdict(lambda: defaultdict(list))
        for item in items:
            if item.get("family") != "word":
                continue
            readings = [hiragana(r) for r in (item.get("reading"), *_strings(item.get("accepted_readings")))
                        if isinstance(r, str) and r]
            for form in {item.get("title"), *_strings(item.get("spellings"))}:
                if isinstance(form, str) and KANJI.search(form):
                    for reading in dict.fromkeys(readings):
                        if HIRAGANA_READING.fullmatch(reading):
                            words[form][reading].append(item["id"])
        return cls(words, overrides, homographs, dictionary, word_keys, lesson_own_chars(items))

    def word_key(self, token, reading):
        """[written form, reading] of the course word lesson that teaches ``token`` as read here (``reading``:
        the reading chosen for its surface): its dictionary form or written form is the lesson's written form and
        that reading is the lesson's (an inflected form through inflected_reading), else None."""
        for form in dict.fromkeys((token.lemma, token.surface)):
            for lesson_reading in sorted(self.word_keys.get(form, ())):
                if inflected_reading(token.surface, form, lesson_reading) == reading:
                    return [form, lesson_reading]
        return None

    def readings_of(self, *forms):
        """Every reading the lessons and the dictionary know for these written forms."""
        found = set()
        for form in forms:
            found.update(self.words.get(form, {}))
            found.update(self.dictionary.get(form, ()))
        return found

    def is_heteronym(self, token):
        forms = (token.surface, token.lemma)
        return (any(form in HETERONYMS for form in forms) or token.surface.startswith("行っ")
                or len(self.readings_of(*forms)) > 1)


def lesson_own_chars(items):
    """Rule 3: {lesson id: {kanji: reading}} of the readings a lesson states for one character: its title when the
    title is that character (日 ひ), or a kanji standing alone between kana in its title, whose part of the
    lesson's reading kanji_parts aligns (後ろ うしろ: 後 うし). A character the title holds twice with different
    readings is left out."""
    found = {}
    for item in items:
        title, reading = item.get("title"), item.get("reading")
        if not isinstance(title, str) or not isinstance(reading, str) or not reading:
            continue
        reading = hiragana(reading)
        if not HIRAGANA_READING.fullmatch(reading):
            continue
        chars, clash = {}, set()
        parts = [(0, 1, reading)] if len(title) == 1 and KANJI.match(title) else kanji_parts(title, reading) or ()
        for start, end, part in parts:
            if end - start == 1:
                char = title[start]
                if chars.get(char, part) != part:
                    clash.add(char)
                chars[char] = part
        chars = {char: part for char, part in chars.items() if char not in clash}
        if chars:
            found[item["id"]] = chars
    return found


def load_overrides(path):
    if path is None or not Path(path).is_file():
        return []
    value = json.loads(Path(path).read_text("utf-8"))
    return value.get("rules", []) if isinstance(value, dict) else []


def load_homographs(path):
    if path is None or not Path(path).is_file():
        return {}
    value = json.loads(Path(path).read_text("utf-8"))
    return value.get("homograph_by_context", {}) if isinstance(value, dict) else {}


def load_dictionary(path):
    """{written form: readings} of the lexicon's words (the course's dictionary data), or {}."""
    if path is None or not Path(path).is_file():
        return {}
    found = defaultdict(set)
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        for form, reading in db.execute("SELECT form, reading FROM word"):
            if isinstance(form, str) and KANJI.search(form) and isinstance(reading, str):
                found[form].add(hiragana(reading))
    return found


# ----------------------------------------------------------------------------- homograph rules (rule 17)

_CLAUSE_STOPS = frozenset({"補助記号", "空白", "動詞", "形容詞", "助動詞"})
_SENTENCE_ENDS = frozenset({"。", "！", "？", "!", "?"})
_CONTEXT_KEYS = ("token", "clause_has", "clause_lacks", "sentence_has", "sentence_has_run", "next", "not_next",
                 "after")


def _values(value):
    return value if isinstance(value, list) else [value]


def spec_matches(spec, tok):
    """One token spec of the homograph rules (s surface, l lemma, not_l, p, p1, p2, c / not_c form prefix)."""
    if tok is None or not isinstance(spec, dict):
        return False
    checks = (("s", tok[0] in _values(spec.get("s"))), ("l", tok[1] in _values(spec.get("l"))),
              ("p", tok[4] in _values(spec.get("p"))), ("p1", tok[5] in _values(spec.get("p1"))),
              ("p2", tok[6] in _values(spec.get("p2"))))
    if any(key in spec and not held for key, held in checks):
        return False
    if "not_l" in spec and tok[1] in _values(spec["not_l"]):
        return False
    form = tok[8] or ""
    if "c" in spec and not any(form.startswith(prefix) for prefix in _values(spec["c"])):
        return False
    return not ("not_c" in spec and any(form.startswith(prefix) for prefix in _values(spec["not_c"])))


def homograph_rule_holds(rule, toks, index):
    """Whether one homograph_by_context rule holds at toks[index] (the question-dependency tool's rule 17 over
    the analyser's tokens: own clause back to punctuation, an earlier predicate or a conjunctive particle)."""
    clause, sentence, in_clause = [], [], True
    for previous in reversed(toks[:index]):
        if previous[0] in _SENTENCE_ENDS or "\n" in previous[0]:
            break
        sentence.append(previous)
        in_clause = in_clause and not (previous[4] in _CLAUSE_STOPS or previous[4] == "助詞" and previous[5] == "接続助詞")
        if in_clause:
            clause.append(previous)
    following = toks[index + 1] if index + 1 < len(toks) else None

    def any_of(spec, candidates):
        return any(spec_matches(s, t) for s in _values(spec) if isinstance(s, dict) for t in candidates)

    if "token" in rule and not any_of(rule["token"], [toks[index]]):
        return False
    if "clause_has" in rule and not any_of(rule["clause_has"], clause):
        return False
    if "clause_lacks" in rule and any_of(rule["clause_lacks"], clause):
        return False
    if "sentence_has" in rule and not any_of(rule["sentence_has"], sentence):
        return False
    run = [spec for spec in rule.get("sentence_has_run") or () if isinstance(spec, dict)]
    forward = sentence[::-1]
    if run and not any(all(spec_matches(spec, t) for spec, t in zip(run, forward[start:start + len(run)]))
                       for start in range(len(forward) - len(run) + 1)):
        return False
    if "next" in rule and not (following is not None and any_of(rule["next"], [following])):
        return False
    if "not_next" in rule and following is not None and any_of(rule["not_next"], [following]):
        return False
    after = [spec for spec in rule.get("after") or () if isinstance(spec, dict)]
    return not (after and (index + len(after) >= len(toks) or not all(
        spec_matches(spec, t) for spec, t in zip(after, toks[index + 1:index + 1 + len(after)]))))


# ----------------------------------------------------------------------------- readings of one token

def inflected_reading(surface, lemma, lemma_reading):
    """The reading of ``surface`` from its dictionary form's reading, or None when the rule cannot tell:
    the shared beginning keeps the reading's stem, the surface's kana tail replaces the lemma's (行っ from 行く
    いく: いっ; 食べ from 食べる: たべ; 高かっ from 高い: たかかっ)."""
    if surface == lemma:
        return lemma_reading
    if lemma in IRREGULAR_LEMMAS:
        return None
    common = os.path.commonprefix([surface, lemma])
    lemma_tail, surface_tail = lemma[len(common):], surface[len(common):]
    if (not common or not KANJI.search(common) or KANJI.search(lemma_tail) or KANJI.search(surface_tail)
            or not lemma_reading.endswith(hiragana(lemma_tail))):
        return None
    return lemma_reading[:len(lemma_reading) - len(lemma_tail)] + hiragana(surface_tail)


def kanji_part(surface):
    """(start, end) of a surface from its first to its last kanji."""
    positions = [i for i, char in enumerate(surface) if KANJI.match(char)]
    return (positions[0], positions[-1] + 1) if positions else None


def kanji_parts(surface, reading):
    """[(start, end, reading)] of each kanji run of a word (取り消す とりけす: 取 と, 消 け), or None when the
    reading cannot be aligned with the surface's kana."""
    pattern, runs = "", []
    for match in re.finditer(KANJI.pattern + "+|.", surface):
        piece = match.group()
        if KANJI.match(piece):
            pattern += "(.+?)"
            runs.append((match.start(), match.end()))
        else:
            pattern += re.escape(hiragana(piece))
    found = re.fullmatch(pattern, reading)
    if not runs or found is None:
        return None
    return [(start, end, found.group(n + 1)) for n, (start, end) in enumerate(runs)]


def _japanese(surface):
    """Whether each kanji of the surface is a Japanese character (Shift_JIS); a simplified Chinese one is not."""
    try:
        "".join(KANJI.findall(surface)).encode("cp932")
    except UnicodeEncodeError:
        return False
    return True


def _override(knowledge, token, start, text):
    """The reading of the token by the first matching override rule: (reading or None to drop, True) or None."""
    part = kanji_part(token.surface)
    if part is None:
        return None
    k_start, k_end = start + part[0], start + part[1]
    kanji = token.surface[part[0]:part[1]]
    for rule in knowledge.overrides:
        if rule["span"] != kanji or rule.get("lemma") not in (None, token.lemma):
            continue
        if rule.get("before") and not re.search("(?:" + rule["before"] + r")\Z", text[:k_start]):
            continue
        if rule.get("after") and not re.match(rule["after"], text[k_end:]):
            continue
        if rule.get("reading") is None:
            return None, True
        return (hiragana(token.surface[:part[0]]) + rule["reading"] + hiragana(token.surface[part[1]:])), True
    return None


def decide(knowledge, tokens, index, start, text):
    """(reading, source) of tokens[index] at ``start`` in ``text``; reading None drops the word."""
    token = tokens[index]
    override = _override(knowledge, token, start, text)
    if override is not None:
        return override[0], "override"
    rules = knowledge.homographs.get(token.lemma) or knowledge.homographs.get(token.surface)
    if rules and len(knowledge.words.get(token.lemma, knowledge.words.get(token.surface, {}))) > 1:
        specs = [t.spec_tuple() for t in tokens]
        for rule in rules:
            otherwise = rule.get("otherwise") is True and not any(key in rule for key in _CONTEXT_KEYS)
            if isinstance(rule.get("reading"), str) and (otherwise or homograph_rule_holds(rule, specs, index)):
                reading = inflected_reading(token.surface, token.lemma, hiragana(rule["reading"]))
                if reading is not None:
                    return reading, "homograph"
                break
    for form in (token.lemma, token.surface):
        readings = knowledge.words.get(form)
        if not readings:
            continue
        if len(readings) == 1:  # several readings of one written form: the analyser chooses (and is reviewed)
            derived = inflected_reading(token.surface, form, next(iter(readings)))
            if derived is not None:
                return derived, "course"
        break
    return token.reading, "analyser"


# ----------------------------------------------------------------------------- texts

def _aligned(text, tokens):
    """([(start, token)], status) of the tokens laid on ``text`` in order. status "exact": the token surfaces
    spell the text; "normalised": they spell it up to width and compatibility forms (the analyser writes Ａ for A,
    ３０ for 30) or leave out spaces and line breaks; "failed": a token could not be placed where the text went on
    (the others are still placed: see the resynchronisation below). A token is placed only where its surface is
    the text's own."""
    placed, cursor, status, failed = [], 0, "exact", False
    for token in tokens:
        surface = token.surface
        if not surface:
            continue
        while cursor < len(text) and text[cursor].isspace() and not surface[0].isspace():
            cursor, status = cursor + 1, "normalised"
        if text.startswith(surface, cursor):
            placed.append((cursor, token))
            cursor += len(surface)
            continue
        wanted = unicodedata.normalize("NFKC", surface)
        for width in range(1, len(surface) + 4):
            if unicodedata.normalize("NFKC", text[cursor:cursor + width]) == wanted:
                cursor, status = cursor + width, "normalised"
                break
        else:
            # Resynchronise: the analyser wrote this token for text it rewrote (30 -> 三十). The token is placed
            # further on only when what it skips holds no kanji or kana; else it is dropped.
            found = text.find(surface, cursor, cursor + len(surface) + 12)
            failed = True
            if found >= 0 and not KANJI.search(text[cursor:found]) and not KANA.search(text[cursor:found]):
                placed.append((found, token))
                cursor = found + len(surface)
    while cursor < len(text) and text[cursor].isspace():
        cursor, status = cursor + 1, "normalised"
    return placed, "failed" if failed or cursor != len(text) else status


def pieces(text):
    """(offset, piece) of the parts of a text between its gaps."""
    found, cursor = [], 0
    for gap in GAP.finditer(text):
        if gap.start() > cursor:
            found.append((cursor, text[cursor:gap.start()]))
        cursor = gap.end()
    if cursor < len(text):
        found.append((cursor, text[cursor:]))
    return found


NUMERAL = re.compile(r"[〇一二三四五六七八九十百千万億兆0-9０-９]+")
DIGITS = re.compile(r"[0-9０-９][0-9０-９,，.．]*")
# The blanks of question texts that rule 2 reads with the answer filled in (GAP without the ellipses, which are
# omissions in quotations, not blanks): runs of _＿□■◯○, empty brackets （　） () [] 【】, （1）–（6）, ①–⑥ in
# brackets, ［1］–［6］.
BLANK = re.compile(r"[_＿□■◯○]+|[（(\[【]\s*[）)\]】]|（[①-⑥1-6]）|［[1-6]］")


def spoken(token):
    """The token's reading after the analyser's sound changes (pron), its long vowels written as kana from the
    reading (週間 シューカン -> しゅうかん); None when the two cannot be lined up."""
    pron, reading = (token.pron or token.reading).replace("’", ""), token.reading
    if "ー" not in pron:
        return pron
    if len(pron) != len(reading):
        return None
    return "".join(r if p == "ー" and r != "ー" else p for p, r in zip(pron, reading))


# The suffixes that belong to a counted unit (三時間, 三日目, 五分前, 二時半); not 上／下 (三歳上 うえ) nor a noun the
# analyser tags as a suffix after a counter (三回薬, 三回年).
COUNTER_TAILS = frozenset("間 目 半 前 後 頃 弱 強 余 先".split())


def numeral_units(piece, tokens, placed):
    """Rule 1: [(start, end, reading or None, member token ids)] of each numeral directly followed by a counter in
    one piece: numeral tokens (pos 名詞/数, written in kanji numerals or digits; 何 is not one) and the counter token
    (接尾/助数詞) that follows them, with a suffix of COUNTER_TAILS right after it (三時間). A numeral the analyser rewrote (30 -> 三 十, width forms) is the run of digits
    of the text right before the counter. The reading is the analyser's of the whole unit (spoken); None when it is
    not kana."""
    at = {id(token): start for start, token in placed}
    units, index = [], 0
    while index < len(tokens):
        first = index
        while index < len(tokens) and tokens[index].pos == "名詞" and tokens[index].pos1 == "数" \
                and NUMERAL.fullmatch(tokens[index].surface):
            index += 1
        counter = tokens[index] if first < index < len(tokens) else None
        if counter is None or counter.pos1 != "接尾" or counter.pos2 != "助数詞" or id(counter) not in at:
            index = max(index, first + 1)
            continue
        numerals = tokens[first:index]
        stop = at[id(counter)] + len(counter.surface)
        if all(id(token) in at for token in numerals):
            begin = at[id(numerals[0])]
            if piece[begin:at[id(counter)]] != "".join(token.surface for token in numerals):
                index += 1
                continue
        else:
            run = [m for m in DIGITS.finditer(piece, 0, at[id(counter)]) if m.end() == at[id(counter)]]
            if not run:
                index += 1
                continue
            begin = run[-1].start()
        # a suffix written in kanji right after the counter belongs to it (三時間: 時 and 間)
        tail = []
        while (index + 1 + len(tail) < len(tokens) and tokens[index + 1 + len(tail)].pos1 == "接尾"
               and tokens[index + 1 + len(tail)].surface in COUNTER_TAILS
               and at.get(id(tokens[index + 1 + len(tail)])) == stop):
            stop += len(tokens[index + 1 + len(tail)].surface)
            tail.append(tokens[index + 1 + len(tail)])
        parts = [spoken(token) for token in (*numerals, counter, *tail)]
        reading = "".join(parts) if all(parts) else None
        if reading is not None and not HIRAGANA_READING.fullmatch(reading):
            reading = None
        units.append((begin, stop, reading, {id(token) for token in (*numerals, counter, *tail)}))
        index += 1 + len(tail)
    return units


def _unit_override(knowledge, surface, start, text):
    """(reading or None, True) of the first override rule whose span is exactly the whole unit, or None."""
    for rule in knowledge.overrides:
        if rule["span"] != surface or rule.get("lemma") not in (None, surface):
            continue
        if rule.get("before") and not re.search("(?:" + rule["before"] + r")\Z", text[:start]):
            continue
        if rule.get("after") and not re.match(rule["after"], text[start + len(surface):]):
            continue
        return rule.get("reading"), True
    return None


def foreign_runs(text):
    """Rule 4: (start, end) of each run of ideographs that holds a character outside JIS X 0208 (shift_jis)."""
    found = []
    for match in re.finditer(KANJI.pattern + "+", text):
        try:
            match.group().encode("shift_jis")
        except UnicodeEncodeError:
            found.append(match.span())
    return found


def _piece_spans(text, offset, piece, tokens, knowledge, spans, problems, landing=True):
    placed, status = _aligned(piece, tokens)
    if status == "failed":
        problems.append({"flag": "coverage", "piece": piece,
                         "tokens": "|".join(token.surface for token in tokens)[:300]})
    elif status == "normalised":
        problems.append({"flag": "normalised", "piece": piece})
    indexes = {id(token): n for n, token in enumerate(tokens)}
    units = numeral_units(piece, tokens, placed) if landing else []
    members = {member for *_, ids in units for member in ids}
    foreign = foreign_runs(text) if landing else []
    found = []
    for begin, stop, reading, _ in units:
        surface = piece[begin:stop]
        found.append((offset + begin, surface, reading, None))
    for start, token in placed:
        if id(token) not in members:
            found.append((offset + start, token.surface, token, None))
    for start, surface, what, _ in sorted(found, key=lambda entry: entry[0]):
        end = start + len(surface)
        if not KANJI.search(surface):
            continue
        if any(a < end and start < b for a, b in foreign):
            problems.append({"flag": "unknown", "surface": surface, "reason": "a run with a non-Japanese character"})
            continue
        if not isinstance(what, Token):  # a numeral with its counter: one unit
            override = _unit_override(knowledge, surface, start, text)
            reading = override[0] if override is not None else what
            if reading is None:
                problems.append({"flag": "unknown", "surface": surface, "reason": "numeral unit without a reading"})
                continue
            spans.append({"start": start, "end": end, "surface": surface, "reading": reading,
                          "source": "override" if override is not None else "analyser", "lemma": surface,
                          "word": None, "heteronym": False, "known_readings": [], "unit": "numeral"})
            continue
        token = what
        if not _japanese(token.surface):
            problems.append({"flag": "unknown", "surface": token.surface, "reason": "not a Japanese kanji"})
            continue
        if not token.reading or not HIRAGANA_READING.fullmatch(token.reading):
            problems.append({"flag": "unknown", "surface": token.surface, "reason": "no usable reading"})
            continue
        reading, source = decide(knowledge, tokens, indexes[id(token)], start, text)
        if reading is None or not HIRAGANA_READING.fullmatch(reading):
            continue
        spans.append({"start": start, "end": end,
                      "surface": token.surface, "reading": reading, "source": source, "lemma": token.lemma,
                      "word": knowledge.word_key(token, reading),
                      "heteronym": source == "analyser" and knowledge.is_heteronym(token),
                      "known_readings": sorted(knowledge.readings_of(token.surface, token.lemma))})


def filled_text(text, fills):
    """(the text with each blank replaced by its answer, [(visible start, visible end, filled start, filled end)]
    of the stretches between the blanks) or None: fills [(start, end, answer)] must be every BLANK of the text."""
    blanks = [match.span() for match in BLANK.finditer(text)]
    if not blanks or sorted((a, b) for a, b, _ in fills) != blanks:
        return None
    out, stretches, cursor = [], [], 0
    for start, end, answer in sorted(fills):
        if not isinstance(answer, str) or not answer or "\n" in answer or BLANK.search(answer):
            return None
        stretches.append((cursor, start, len("".join(out)), len("".join(out)) + start - cursor))
        out.extend((text[cursor:start], answer))
        cursor = end
    stretches.append((cursor, len(text), len("".join(out)), len("".join(out)) + len(text) - cursor))
    out.append(text[cursor:])
    return "".join(out), stretches


def text_spans(text, analysed, knowledge, *, blanks=False, fills=None, landing=True):
    """Word spans of one text: ([{start, end, surface, reading, source, lemma, heteronym}], problems).

    Rule 1: a numeral with its counter is one span (numeral_units). Rule 4: nothing in a run of ideographs with
    a non-Japanese character (foreign_runs). blanks (question texts), rule 2: a word directly beside a BLANK is
    read only from the text with the answers filled in (fills [(start, end, answer)] for every blank; filled_text,
    which must be in ``analysed`` piece by piece) and only when it maps back exactly; else it gets no reading.
    landing False (the development edition): none of rules 1, 2 and 4, the readings as on 2026-11-25.
    """
    spans, problems = [], []
    for offset, piece in pieces(text):
        tokens = analysed.get(piece)
        if tokens is None:
            problems.append({"flag": "coverage", "piece": piece, "reason": "not analysed"})
            continue
        _piece_spans(text, offset, piece, tokens, knowledge, spans, problems, landing)
    if not blanks or not landing:
        return spans, problems
    gaps = [match.span() for match in BLANK.finditer(text)]
    if not gaps:
        return spans, problems
    starts, ends = {start for start, _ in gaps}, {end for _, end in gaps}

    def beside(span):
        return span["end"] in starts or span["start"] in ends
    kept = [span for span in spans if not beside(span)]
    filled = filled_text(text, fills or ())
    if filled is not None:
        full, stretches = filled
        extra = []
        for offset, piece in pieces(full):
            if piece in analysed:
                _piece_spans(full, offset, piece, analysed[piece], knowledge, extra, [])
        for span in extra:
            for visible, visible_end, start, end in stretches:
                if start <= span["start"] and span["end"] <= end:
                    moved = {**span, "start": span["start"] - start + visible, "end": span["end"] - start + visible}
                    if text[moved["start"]:moved["end"]] == span["surface"] and beside(moved):
                        kept.append({**moved, "filled": True})
                    break
    return sorted(kept, key=lambda span: span["start"]), problems


def is_word(surface):
    """Whether a token is a word: it has a letter, digit or kana (punctuation, symbols and spaces are not)."""
    return any(unicodedata.category(char)[0] not in "PSZC" for char in surface)


UNIT_RULES = ("numeral", "suffix", "prefix", "auxiliary", "bound ending", "kana counter")
# Endings bound to a verb stem (見たり, 歩きながら, 思いつつ, 食べすぎた, 読みやすい, 分かりにくい): one word with it.
BOUND_PARTICLES = frozenset({"たり", "だり", "ながら", "つつ"})
BOUND_LEMMAS = frozenset({"すぎる", "過ぎる", "やすい", "易い", "にくい", "難い", "づらい", "辛い"})
# A number with a counter written in kana (一けん, 六にん, 四ひき, 二まん人, 三つ): placed as one unit before the
# analyser, which cuts these across words (六|にんい|ます).
_KANA_COUNTERS = (r"(?:つ|けん|げん|にん|ひき|びき|ぴき|ほん|ぼん|ぽん|まい|さつ|かい|がい|ばい|はい|ぱい|だい|"
                  r"にち|ふん|ぷん|ねん|さい|ばん|えん|ど)")
KANA_COUNTER = re.compile(r"[〇一二三四五六七八九十百千万0-9０-９]+(?:(?:まん|せん|ぜん|ひゃく|びゃく|ぴゃく)"
                          r"[〇一二三四五六七八九十百千0-9０-９]*(?:" + _KANA_COUNTERS + r"|[人円年回本冊枚匹階個時分日])?|"
                          + _KANA_COUNTERS + ")")


def kana_counter_units(text):
    """[(start, end)] of each number in ``text`` that is written with kana (KANA_COUNTER) - 一けん, 二まん人, 三つ."""
    return [m.span() for m in KANA_COUNTER.finditer(text) if KANA.search(m.group())]
_PREDICATE = {"verb", "adjective"}


def _token_kind(token):
    """The kind of a token for the unit rules (part of speech of the analyser, IPADIC tags)."""
    pos, pos1, pos2 = token.pos, token.pos1, token.pos2
    if pos == "名詞" and pos1 == "数" and NUMERAL.fullmatch(token.surface):
        return "numeral"
    if pos == "名詞" and pos1 == "接尾":
        return "counter" if pos2 == "助数詞" else "suffix"
    if pos == "接頭詞":
        return "prefix"
    if pos == "助動詞":
        return "auxiliary"
    if (pos == "助詞" and token.surface in BOUND_PARTICLES) or (pos in {"動詞", "形容詞"} and pos1 == "非自立"
                                                             and token.lemma in BOUND_LEMMAS):
        return "bound"
    if pos == "動詞":
        return "verb suffix" if pos1 == "接尾" else "verb"
    if pos == "形容詞":
        return "adjective suffix" if pos1 == "接尾" else "adjective"
    if pos == "名詞" and pos1 == "形容動詞語幹":
        return "adjective"
    if pos == "助詞" and pos1 == "接続助詞" and token.surface in {"て", "で", "ば"}:
        return "conjunctive"
    if pos == "助詞":
        return "particle"
    if pos == "名詞":
        return "noun"
    return "other"


def _units(offset, piece, tokens, counts):
    """[[start, end]] of the units of one stretch (word_boundaries); counts: merges per rule."""
    placed, _ = _aligned(piece, tokens)
    at = {id(token): start for start, token in placed}
    numeral = {}
    for begin, stop, _, members in numeral_units(piece, tokens, placed):
        for member in members:
            numeral[member] = (begin, stop)
    units, last = [], None  # last: [start, end, kind] of the unit that the next token may join
    for start, token in placed:
        end = start + len(token.surface)
        if not is_word(token.surface):
            last = None
            continue
        if id(token) in numeral:  # rule 1: a numeral with its counter, digits the analyser rewrote included
            begin, stop = numeral[id(token)]
            if last is not None and last[0] == begin:
                if end > last[1]:
                    last[1] = end
                counts["numeral"] += 1
            else:
                last = [begin, max(stop, end), "noun"]
                units.append(last)
            continue
        kind = _token_kind(token)
        adjacent = last is not None and last[1] == start
        joins = False
        if adjacent and kind == "numeral" and last[2] == "numeral":
            joins, rule = True, "numeral"
        elif adjacent and kind == "counter" and last[2] == "numeral":
            joins, rule, kind = True, "numeral", "noun"
        elif adjacent and kind in {"suffix", "counter"} and last[2] in {"noun", "numeral", "verb", "adjective"}:
            joins, rule, kind = True, "suffix", "noun"
        elif adjacent and last[2] == "prefix" and kind in {"noun", "verb", "adjective", "numeral", "other"}:
            joins, rule = True, "prefix"
        elif adjacent and kind in {"auxiliary", "conjunctive", "verb suffix", "adjective suffix"} \
                and last[2] in _PREDICATE:
            joins, rule, kind = True, "auxiliary", last[2]
        elif adjacent and kind == "bound" and last[2] in _PREDICATE:
            joins, rule = True, "bound ending"
            kind = "adjective" if token.pos == "形容詞" else last[2]
        if joins:
            last[1] = end
            last[2] = kind
            counts[rule] += 1
            continue
        if kind == "auxiliary":  # です／だ after a noun: a unit of its own, which further auxiliaries join
            kind = "verb"
        last = [start, end, kind]
        units.append(last)
    return [[offset + a, offset + b] for a, b, _ in units]


def _stretches(offset, piece, forced):
    """(offset, text) of the parts of a piece between the forced units inside it, and those units."""
    inside = sorted((a, b) for a, b in forced if offset <= a and b <= offset + len(piece))
    parts, cursor = [], offset
    for a, b in inside:
        if a > cursor:
            parts.append((cursor, piece[cursor - offset:a - offset]))
        cursor = b
    if cursor < offset + len(piece):
        parts.append((cursor, piece[cursor - offset:]))
    return parts, inside


def word_boundaries(text, analysed, spans=(), forced=(), counts=None):
    """([[start, end]], tokens not aligned): the words of ``text`` as a learner reads them, for the record field
    word_boundaries (docs/sentence_annotations.txt; owner's unit, 2026-11-26):
      a number is one unit, a number with its counter too (二十, 三回, 三時間, 30分);
      a suffix joins the word before it (郵便局, 田中様, さん, たち, 的), a prefix the word after it (お茶, ご飯);
      auxiliaries (ます, た, ない, れる, たい ...), verb and adjective suffixes and the conjunctive て／で／ば join the
      verb or adjective before them, in chains (行きました, 食べさせられた); です／だ after a noun stay a unit of
      their own, which a following auxiliary joins (学生｜でした); a verb after a て-form is a word of its own
      (食べて｜いる); particles stay units of their own.
    forced: [(start, end)] units placed before the analyser (course_word_units, kana words of the question's
    course words); the stretches between them are analysed one by one (they must be in ``analysed``).
    Only tokens placed exactly on the text count; punctuation and spaces give none and break every chain. A
    unit never cuts a reading segment: the units that overlap a segment and the segment become one. A word
    token that cannot be placed exactly leaves its stretch without a pair and is counted."""
    counts = Counter() if counts is None else counts
    pairs, unaligned = [], 0
    for offset, piece in pieces(text):
        stretches, inside = _stretches(offset, piece, forced)
        pairs.extend([a, b] for a, b in inside)
        for start, stretch in stretches:
            tokens = [token for token in analysed.get(stretch) or () if token.surface]
            placed, _ = _aligned(stretch, tokens)
            found = {id(token) for _, token in placed}
            unaligned += sum(1 for token in tokens if id(token) not in found and is_word(token.surface))
            counts["tokens"] += sum(1 for _, token in placed if is_word(token.surface))
            pairs.extend(_units(start, stretch, tokens, counts))
    for span in spans:
        joined = [pair for pair in pairs if pair[0] < span["end"] and span["start"] < pair[1]]
        if not (len(joined) == 1 and joined[0][0] <= span["start"] and span["end"] <= joined[0][1]):
            pairs = [pair for pair in pairs if pair not in joined]
            pairs.append([min([span["start"], *(p[0] for p in joined)]), max([span["end"], *(p[1] for p in joined)])])
            counts["extended to a segment"] += 1
    merged = []
    for pair in sorted(pairs):
        if merged and pair[0] < merged[-1][1]:  # an overlap made by a joined segment: one word
            merged[-1][1] = max(merged[-1][1], pair[1])
        else:
            merged.append(list(pair))
    return merged, unaligned


KANA_RUN = re.compile(r"[ぁ-ゖァ-ヺー]+")


def course_word_candidates(text, words):
    """Rule 5, first step: [(start, end, kana, written form)] where a kana reading of the question's course words
    (words: [(kana, written form)]) is an exact stretch of a kana run of ``text`` (never across punctuation or
    kanji), the longest first, never overlapping; a reading of one kana is not placed."""
    found, taken = [], set()
    for kana, written in sorted(set(words), key=lambda word: (-len(word[0]), word)):
        if len(kana) < 2 or not KANJI.search(written or ""):
            continue
        for run in KANA_RUN.finditer(text):
            for match in re.finditer(re.escape(kana), run.group()):
                start, end = run.start() + match.start(), run.start() + match.end()
                if not taken.intersection(range(start, end)):
                    found.append((start, end, kana, written))
                    taken.update(range(start, end))
    return sorted(found)


def kanji_variant(text, candidate):
    start, end, _, written = candidate
    return text[:start] + written + text[end:], start, start + len(written)


def analyser_agrees(variant, start, end, analysed):
    """Rule 5, the check: in the text with the kanji form in the kana word's place, the analyser's tokens begin at
    ``start`` and end at ``end`` (the word is cut where the kana unit would be)."""
    for offset, piece in pieces(variant):
        if offset <= start and end <= offset + len(piece):
            tokens = [token for token in analysed.get(piece) or () if token.surface]
            placed, status = _aligned(piece, tokens)
            starts = {offset + a for a, _ in placed}
            ends = {offset + a + len(token.surface) for a, token in placed}
            return status != "failed" and start in starts and end in ends
    return False


def sentence_record(text, spans, boundaries=None):
    """A candidate row of the sentence annotations (the app's format; no accent numbers); boundaries: the
    record's word_boundaries (word_boundaries), left out when None or empty."""
    segments = [{"start": s["start"], "end": s["end"], "surface": s["surface"], "reading": s["reading"],
                 "reading_source_id": ANALYSER_SOURCE if s["source"] == "analyser" else COURSE_SOURCE,
                 "reading_derivation": s["source"]} for s in spans]
    covered = {i for s in spans for i in range(s["start"], s["end"])}
    unresolved = [{"start": m.start(), "end": m.end(), "surface": m.group()}
                  for m in sentence_format.KANJI_RUN.finditer(text)
                  if any(i not in covered for i in range(m.start(), m.end()))]
    record = {"text": text, "segments": segments, "version": sentence_format.VERSION, "status": "candidate",
              "unresolved": unresolved}
    if boundaries:
        record["word_boundaries"] = [list(pair) for pair in boundaries]
    return record


# ----------------------------------------------------------------------------- explanation lines

# Chinese punctuation (a Japanese line uses 、。「」 and may use ：（） in dialogues and notes).
CHINESE_PUNCTUATION = re.compile(r"[，；“”‘’《》〈〉]")
# Chinese function words that a Japanese sentence does not write with these characters (的 only right after kana
# or a closing quotation: Japanese writes it after a kanji, as in 目的, 具体的).
CHINESE_FUNCTION_WORDS = re.compile(r"[是了吗呢吧们么这很]|(?<=[ぁ-ゖァ-ヺー」』])的")
QUOTED = re.compile(r"「[^「」]*」|『[^『』]*』")
# A word with its own reading in brackets (写真（しゃしん）): a glossary line, its reading already shown.
GIVEN_READING = re.compile(KANJI.pattern + r"（[ぁ-ゖァ-ヺー]+）")
EXPLANATION_RULE = ("A whole line of the explanation under an answer (result_explanations.course_explanation), trimmed, "
                    "with kanji and with kana (a line of kanji only only when it is a Japanese text of the question "
                    "that the course has a reading for: the word lesson's written form, a text of its readings entry); "
                    "every character in JIS X 0208 (shift_jis); no Chinese punctuation (，；“”‘’《》〈〉); no word with "
                    "its reading in brackets (写真（しゃしん）); not a conjugation note with → whose brackets give the "
                    "reading, nor any other note with → or ＋; not a reading note (人：にん); no '：' followed by text without kana (写真：照片); outside "
                    "its 「」 quotations no Chinese function word (是了吗呢吧们么这很, 的 right after kana or a closing "
                    "quotation), no Chinese explanation word (写作, 表示, 否定, 前用, 后接 ...: CHINESE_EXPLANATION_WORDS) "
                    "and at least one kana for every two kanji. Of these, only a line on which the result page as it is "
                    "today leaves a kanji run without its reading for a question that shows it.")


# Chinese words of explanations (outside 「」): a Japanese sentence does not write them this way (表示 and 否定 only
# when no する-form follows, as in 表示される).
CHINESE_EXPLANATION_WORDS = re.compile(r"写作|读作|表示(?![さしすせ])|否定(?![さしすせ的])|前用|后接|后面|前面|句中|"
                                       r"再用|礼貌|意思|用于|用来|说明|去い|去掉|加上|不加|不用|的形|形式|接续|读成|"
                                       r"前接|也可以|改成|再加|要去|要用|去ます|接ない|普通形|[てたな]形|ます形|使役|"
                                       r"受益者|做事者|不要做|[一五]段：")
# A reading note: a word, a colon and its reading, once or several times (人：にん。 / 人：にん、日：にち).
READING_NOTE = re.compile(r"(?:[^：:\s、，。]+[：:]\s*[ぁ-ゖァ-ヺー]+[。．]?[\s、，；／/]*)+")
GIVEN_KANA = re.compile(r"（[ぁ-ゖァ-ヺー]+）")


def explanation_verdict(line, own_texts=()):
    """None when one trimmed line of an explanation is a Japanese sentence (a candidate; people check them), else
    the reason it is not: no kanji; a character outside JIS X 0208 (shift_jis), so simplified Chinese; Chinese
    punctuation; no kana (a line of kanji only is taken only when it is one of ``own_texts``: the question's
    Japanese texts the course has a reading for); a word with its reading in brackets (写真（しゃしん）); a
    conjugation note with → whose brackets give the reading (歌う→歌って（うたって）); a reading note (人：にん);
    a note with → or ＋ (受け取って → 受け取る, 姓＋さん); a '：' followed by text without kana (写真：照片); outside its 「」 quotations a Chinese function word (是了吗
    呢吧们么这很, 的 right after kana or 」) or a Chinese explanation word (写作, 表示, 否定, 前用, 后接 ...); fewer
    than one kana for every two kanji outside its quotations."""
    if not line or line != line.strip() or not KANJI.search(line):
        return "no kanji"
    try:
        line.encode("shift_jis")
    except UnicodeEncodeError:
        return "not JIS X 0208"
    if CHINESE_PUNCTUATION.search(line):
        return "Chinese punctuation"
    if not KANA.search(line):
        return None if line in own_texts else "no kana"
    if GIVEN_READING.search(line):
        return "reading in brackets"
    if "→" in line and GIVEN_KANA.search(line):
        return "conjugation note"
    if "→" in line or "＋" in line:
        return "note with → or ＋"  # a Japanese sentence of the course never writes them
    if READING_NOTE.fullmatch(line):
        return "reading note"
    if any(not KANA.search(after) for after in line.split("：")[1:]):
        return "gloss"
    rest = QUOTED.sub("", line)
    if CHINESE_FUNCTION_WORDS.search(rest):
        return "Chinese function word"
    if CHINESE_EXPLANATION_WORDS.search(rest):
        return "Chinese explanation word"
    kana = len(KANA.findall(rest))
    if kana == 0 or 2 * kana < len(KANJI.findall(rest)):
        return "few kana"
    return None


def japanese_line(line, own_texts=()):
    """Whether one trimmed line of an explanation is a Japanese sentence (explanation_verdict gives no reason)."""
    return explanation_verdict(line, own_texts) is None


UNREAD_RUN = re.compile("(?<!" + KANJI.pattern + ")" + KANJI.pattern + "+(?!" + KANJI.pattern + "|（)")


def unread_runs(drawn):
    """The kanji runs of a drawn line (inline_kana: 学校（がっこう）) that carry no reading."""
    return [match.group() for match in UNREAD_RUN.finditer(drawn)]


def explanation_targets(questions, counts=None):
    """[{line, question_ids, unread_question_ids, unread_runs}] of the explanation lines that need a record: a
    Japanese line (japanese_line) on which the result page as it is today (result_readings.annotate_explanation,
    the question's own readings, no sentence record) leaves a kanji run without a reading for at least one
    question that shows it. questions: [(question id, explanation, own texts, drawn explanation)] (course_explanations).
    counts (a Counter, optional) receives the numbers of each stage."""
    shown, unread, runs, verdicts = defaultdict(list), defaultdict(list), defaultdict(dict), {}
    for identity, explanation, own, drawn in questions:
        lines, drawn_lines = str(explanation or "").split("\n"), str(drawn or "").split("\n")
        if len(lines) != len(drawn_lines):
            continue
        for line, picture in zip(lines, drawn_lines):
            line = line.strip()
            verdict = explanation_verdict(line, own)
            if verdict is not None and KANJI.search(line):
                verdicts.setdefault(line, verdict)
            if verdict is not None:
                continue
            verdicts[line] = None
            if identity not in shown[line]:
                shown[line].append(identity)
            missing = unread_runs(picture)
            if missing:
                if identity not in unread[line]:
                    unread[line].append(identity)
                runs[line].update(dict.fromkeys(missing))
    if counts is not None:
        counts["explanation Japanese lines"] = len(shown)
        counts["explanation Japanese line places"] = sum(map(len, shown.values()))
        counts["explanation lines with an unread kanji run"] = len(unread)
        counts["explanation questions with an unread kanji run"] = len({q for ids in unread.values() for q in ids})
        for line, verdict in verdicts.items():
            if verdict is not None and line not in shown:
                counts[f"explanation lines left out: {verdict}"] += 1
        counts["explanation lines of kanji only kept (the question's own text with a course reading)"] = sum(
            1 for line in unread if not KANA.search(line))
    return [{"line": line, "question_ids": shown[line], "unread_question_ids": unread[line],
             "unread_runs": list(runs[line])} for line in sorted(unread)]


def course_read_texts(item, question):
    """The question's Japanese texts the course has a reading for: the word lesson's written form, and each text
    of the question's readings entry that carries readings (its prompt lines, its options). A Chinese translation
    or a Chinese answer is never one of them."""
    from kanamemory.question_readings import question_entry
    found = set()
    if item.get("family") == "word" and isinstance(item.get("title"), str) and item.get("reading"):
        found.add(item["title"].strip())
    entry = question_entry(question)
    if entry is not None:
        for text, spans in entry["fields"].values():
            if spans and isinstance(text, str):
                found.add(text.strip())
                found |= {line.strip() for line in text.splitlines()}
        found |= {text.strip() for text, spans in entry["options"].items() if spans and isinstance(text, str)}
    return found


def course_explanations(items):
    """[(question id, course explanation, own texts, drawn explanation)] of every question the course builds from
    ``items`` whose result page shows an explanation; drawn: the line readings of the page as it is today."""
    from kanamemory import learning_questions as lq
    from kanamemory.result_explanations import course_explanation
    from kanamemory.result_readings import annotate_explanation
    found = []
    for item in items:
        try:
            templates = lq.practice_templates(item)
            kinds = lq.question_kinds(item, templates=templates)
        except (ValueError, KeyError, TypeError):
            continue
        for kind in kinds:
            for variant in range(len(templates.get(kind) or ())):
                try:
                    question = lq.build_learning_question(item, kind, variant=variant)
                except (ValueError, KeyError, TypeError):
                    continue
                explanation = course_explanation(question)
                if not explanation or explanation == str(question.get("answer", "")).strip():
                    continue  # the page shows no explanation (result_explanations.shown_course_explanation)
                found.append((question.get("question_id") or question.get("id"), explanation,
                              course_read_texts(item, question), annotate_explanation(question, explanation)))
    return found


def quote_entry(quote, spans):
    """Card spans [start, end, reading, source, word] of one quotation: the kanji parts, never one the quotation
    already reads right after it (「学校（がっこう）」); every part of a word carries that word's course word."""
    result, problems = [], []
    for span in spans:
        parts = kanji_parts(span["surface"], span["reading"])
        if parts is None:
            problems.append({"flag": "alignment", "surface": span["surface"], "reading": span["reading"]})
            continue
        for start, end, reading in parts:
            start, end = span["start"] + start, span["start"] + end
            if not card_format.reading_given(quote, end, reading=reading) and HIRAGANA_READING.fullmatch(reading):
                result.append([start, end, reading, span["source"], span.get("word")])
    return result, problems


# ----------------------------------------------------------------------------- the run

def question_fills(question, text):
    """[(start, end, answer)] of the blanks of ``text`` (a part of the question's prompt) with the question's single
    answer filled in, or None: a multi-blank question with exactly one accepted combination (multi_cloze slots), or
    one BLANK and a plain one-line answer."""
    prompt = question.get("prompt")
    if not isinstance(prompt, str) or text not in prompt:
        return None
    offset = prompt.index(text)
    cloze = question.get("multi_cloze")
    if isinstance(cloze, dict):
        slots, combinations = cloze.get("slots") or [], cloze.get("accepted_combinations") or []
        if len(combinations) != 1 or len(combinations[0]) != len(slots):
            return None
        fills = [(slot.get("start", -1) - offset, slot.get("end", -1) - offset, answer)
                 for slot, answer in zip(slots, combinations[0])]
    else:
        answer, blanks = question.get("answer"), list(BLANK.finditer(prompt))
        if len(blanks) != 1 or not isinstance(answer, str) or answer.startswith("cloze"):
            return None
        fills = [(blanks[0].start() - offset, blanks[0].end() - offset, answer)]
    inside = [(a, b, answer) for a, b, answer in fills if 0 <= a < b <= len(text)]
    return inside if inside and all(text[a:b] == m.group() for (a, b, _), m in
                                    zip(inside, BLANK.finditer(text))) else None


def corpus_context(records, items):
    """({text: fills} of the question texts with blanks, {text: [(kana, written form)]} of the course words of
    the lessons that show the text and of the word lessons they depend on) for rules 2 and 5."""
    from kanamemory.learning_questions import build_learning_question
    by_id = {item["id"]: item for item in items}
    answers, words = {}, defaultdict(set)

    def lesson_words(item):
        if item.get("family") != "word" or not isinstance(item.get("title"), str):
            return set()
        readings = [item.get("reading"), *_strings(item.get("accepted_readings"))]
        return {(hiragana(r), item["title"]) for r in readings if isinstance(r, str) and r}

    for record in records:
        text = record["text"]
        for source in record["sources"]:
            item = by_id.get(source.get("id"))
            if item is None:
                continue
            words[text] |= lesson_words(item)
            for dependency in item.get("depends_on") or ():
                if dependency in by_id:
                    words[text] |= lesson_words(by_id[dependency])
            if source.get("category") == "public_question_prompt" and BLANK.search(text):
                try:
                    question = build_learning_question(item, source["question_kind"], variant=source["variant"])
                except (ValueError, KeyError, TypeError):
                    answers[text] = None
                    continue
                fills = question_fills(question, text)
                if text in answers and answers[text] != fills:
                    fills = None  # several questions show the text with different answers: no single answer
                answers[text] = fills
    return {text: fills for text, fills in answers.items() if fills}, {t: sorted(w) for t, w in words.items() if w}


def existing_sentences(path):
    """The texts that already have a row in a sentence annotation file."""
    if path is None or not Path(path).is_file():
        return set()
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        return {row[0] for row in db.execute("SELECT text FROM sentence")}


def existing_cards(path):
    """(items, note) of a card quote readings file: a version-1 file (no course word per span) gives no items and a
    note for summary.json; no file gives ({}, None)."""
    if path is None or not Path(path).is_file():
        return {}, None
    value = json.loads(Path(path).read_text("utf-8"))
    if value.get("format") == card_format.FORMAT and value.get("version") == 1:
        return {}, f"{path}: version 1 (spans without their course word), treated as no existing entries"
    if value.get("format") != card_format.FORMAT or value.get("version") != card_format.VERSION:
        raise ValueError("Not a card quote readings file: " + str(path))
    return value["items"], None


def card_targets(items):
    """[(content id, field, quotation)] of every quotation with kanji in the teaching cards of ``items``."""
    from kanamemory.structured_lessons import lesson_view, original_sections
    found = []
    for item in items:
        try:
            fields = card_format.card_fields(lesson_view(item), original_sections(item))
        except (ValueError, KeyError, TypeError):
            continue
        for field, text in fields:
            for match in card_format.QUOTE.finditer(text):
                if KANJI.search(match.group(1)):
                    found.append((item["id"], field, match.group(1)))
    return list(dict.fromkeys(found))


def annotate(analyser, knowledge, *, sentences=(), cards=(), have_sentences=(), have_cards=None, explanations=(),
             landing=True, answers=None, course_words=None):
    """The candidates for ``sentences`` ([(text, source label)]), ``cards`` ([(content id, field, quote)]) and
    ``explanations`` (explanation_targets: [{line, question_ids, ...}]; each line a sentence text, label
    "explanation_line").

    Returns {"sentences": {text: record}, "cards": {cid: {field: {quote: spans}}}, "review": {...},
    "totals": Counter, "engine": str, "explanations": the entries of ``explanations`` with annotated and candidate}. Texts and
    quotations that already have an annotation are skipped.
    landing: the landing edition's reading rules (text_spans rules 1, 2, 4; card rule 3); False: the readings of
    the development edition. answers: {text: [(start, end, answer)]} of the question texts with blanks (rule 2).
    course_words: {text: [(kana, written form)]} of the course words of the questions that show the text
    (rule 5 of the word units: course_word_candidates).
    """
    have_cards, have_sentences = have_cards or {}, set(have_sentences)
    sentences = list(sentences)
    listed = {text for text, _ in sentences}
    sentences += [(entry["line"], "explanation_line") for entry in explanations if entry["line"] not in listed]
    todo_sentences = {text: label for text, label in sentences if KANJI.search(text) and text not in have_sentences}
    todo_cards = [(cid, field, quote) for cid, field, quote in cards
                  if quote not in ((have_cards.get(cid) or {}).get(field) or {})]
    # text_spans reads a text piece by piece (between its gaps): collect the pieces of quotations too, or a
    # quotation with a gap (「何とも…ない」) finds no tokens when no sentence of the run shares its pieces
    texts = ({piece for text in todo_sentences for _, piece in pieces(text)}
             | {piece for _, _, quote in todo_cards for _, piece in pieces(quote)})
    answers, course_words = answers or {}, course_words or {}
    if landing:
        for text in todo_sentences:
            filled = filled_text(text, answers.get(text) or ())
            if filled is not None:
                texts |= {piece for _, piece in pieces(filled[0])}
    analysed, engine = analyser.tokenise(sorted(texts)) if texts else ({}, "")
    totals, review = Counter(), {"heteronym": [], "coverage": [], "unknown": [], "alignment": []}
    unit_counts, forced = Counter(), {}
    # rule 5 of the word units: kana words of the question's course words, checked by the analyser with the
    # kanji form in their place, then the stretches between them analysed one by one
    candidates = {}
    for text in todo_sentences:
        found = course_word_candidates(text, course_words.get(text) or ())
        if found:
            plain, _ = word_boundaries(text, analysed)
            found = [c for c in found if not any(a <= c[0] and c[1] <= b and b - a > c[1] - c[0] for a, b in plain)]
            if found:
                candidates[text] = found
    variants = {piece for text, found in candidates.items() for c in found
                for _, piece in pieces(kanji_variant(text, c)[0])} - set(analysed)
    if variants:
        analysed.update(analyser.tokenise(sorted(variants))[0])
    for text, found in candidates.items():
        kept = [(c[0], c[1]) for c in found if analyser_agrees(*kanji_variant(text, c), analysed)]
        unit_counts["course words placed"] += len(kept)
        unit_counts["course words refused by the analyser"] += len(found) - len(kept)
        if kept:
            forced[text] = kept
    for text in todo_sentences:
        numbers = [(a, b) for a, b in kana_counter_units(text)
                   if not any(a < y and x < b for x, y in forced.get(text, ()))]
        if numbers:
            unit_counts["kana counter"] += len(numbers)
            forced[text] = sorted([*forced.get(text, ()), *numbers])
    stretches = {part for text, kept in forced.items() for offset, piece in pieces(text)
                 for _, part in _stretches(offset, piece, kept)[0]} - set(analysed)
    if stretches:
        analysed.update(analyser.tokenise(sorted(stretches))[0])
    normalised = []  # counted, not listed for review: the tokens cover the text up to width forms and spaces
    result_sentences, result_cards = {}, defaultdict(lambda: defaultdict(dict))

    def note(kind, where, spans, problems):
        for span in spans:
            totals[f"{kind} spans by {span['source']}"] += 1
            if span["heteronym"]:
                totals[f"{kind} flagged heteronym"] += 1
                review["heteronym"].append({**where, **{k: span[k] for k in ("start", "end", "surface", "reading",
                                                                             "source", "lemma", "known_readings")}})
        for problem in problems:
            totals[f"{kind} flagged {problem['flag']}"] += 1
            if problem["flag"] == "normalised":
                normalised.append(where)
            else:
                review[problem["flag"]].append({**where, **problem})

    for text, label in sorted(todo_sentences.items()):
        spans, problems = text_spans(text, analysed, knowledge, blanks=True, fills=answers.get(text),
                                     landing=landing)
        note("sentence", {"kind": "sentence", "text": text, "source": label}, spans, problems)
        totals["sentence texts"] += 1
        if spans:
            boundaries, unaligned = word_boundaries(text, analysed, spans, forced.get(text, ()), unit_counts)
            result_sentences[text] = sentence_record(text, spans, boundaries)
            totals["sentence texts with candidates"] += 1
            totals["sentence segments with a reading"] += len(spans)
            totals["sentence word boundaries"] += len(boundaries)
            totals["sentence word tokens not aligned"] += unaligned
            totals["sentence largest segment count"] = max(totals["sentence largest segment count"], len(spans))
            totals["sentence largest word boundary count"] = max(totals["sentence largest word boundary count"],
                                                                 len(boundaries))
    for cid, field, quote in todo_cards:
        if landing and len(quote) == 1 and KANJI.match(quote):
            # rule 3: a single kanji takes the lesson's own reading of that character, never the analyser's
            own = knowledge.own_chars.get(cid, {}).get(quote)
            totals["card single-kanji quotations"] += 1
            totals["card single-kanji quotations with the lesson's reading"] += own is not None
            spans = [] if own is None else [{"start": 0, "end": 1, "surface": quote, "reading": own,
                                             "source": "course", "lemma": quote, "heteronym": False,
                                             "word": knowledge.word_key(Token(quote, quote, own), own)}]
            problems = []
        else:
            spans, problems = text_spans(quote, analysed, knowledge, landing=landing)
        entry, alignment = quote_entry(quote, spans)
        note("card", {"kind": "card", "content_id": cid, "field": field, "quote": quote}, spans,
             problems + alignment)
        totals["card quotations"] += 1
        if entry:
            result_cards[cid][field][quote] = entry
            totals["card quotations with candidates"] += 1
            totals["card spans"] += len(entry)
            totals["card spans with a course word"] += sum(span[4] is not None for span in entry)
    lines = [{**entry, "annotated": entry["line"] in have_sentences, "candidate": entry["line"] in result_sentences}
             for entry in explanations]
    if explanations:
        totals["explanation lines (with an unread kanji run)"] = len(lines)
        totals["explanation lines already annotated"] = sum(entry["annotated"] for entry in lines)
        totals["explanation lines with candidates"] = sum(entry["candidate"] for entry in lines)
    totals.update({f"word units: {key}": value for key, value in unit_counts.items()})
    return {"sentences": result_sentences, "cards": {cid: dict(f) for cid, f in result_cards.items()},
            "review": review, "totals": totals, "engine": engine, "explanations": lines,
            "edition": "landing" if landing else "development"}


def agreement(analyser, knowledge, references):
    """How often the analyser alone agrees with reviewed spans: references [(label, text, [(start, end, reading,
    surface)])], spans of words or of kanji parts. Per label: compared, agreed, unaligned, flagged class."""
    texts = {piece for _, text, _ in references for _, piece in pieces(text)}
    analysed, _ = analyser.tokenise(sorted(texts)) if texts else ({}, "")
    plain = CourseKnowledge(dictionary=knowledge.dictionary)  # no lessons, rules or overrides: the analyser alone
    counts, samples = defaultdict(Counter), []
    for label, text, spans in references:
        words = text_spans(text, analysed, plain)[0]
        by_range = {(w["start"], w["end"]): w for w in words}
        parts = {}
        for word in words:
            for start, end, reading in kanji_parts(word["surface"], word["reading"]) or ():
                parts[(word["start"] + start, word["start"] + end)] = (reading, word)
        for start, end, reading, surface in spans:
            row = counts[label]
            found = by_range.get((start, end))
            mine = (found["reading"], found) if found else parts.get((start, end))
            if mine is None:
                row["unaligned"] += 1
                continue
            heteronym = knowledge.is_heteronym(Token(mine[1]["surface"], mine[1]["lemma"], mine[0]))
            row["compared"] += 1
            row["heteronym compared"] += heteronym
            if mine[0] == reading:
                row["agreed"] += 1
                row["heteronym agreed"] += heteronym
            elif len(samples) < 200:
                samples.append({"reference": label, "text": text[:120], "surface": surface,
                                "reviewed": reading, "analyser": mine[0], "heteronym": heteronym})
    return {"counts": {label: dict(row) for label, row in counts.items()}, "disagreements": samples}


def write_outputs(out, result, *, card_base=None, agreement_result=None, notes=()):
    """Write the candidate files, the review file and the summary into ``out`` (a new or empty folder); notes:
    lines for summary.json (an input file that was not used)."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    database = out / "sentence_annotations.candidates.sqlite3"
    database.unlink(missing_ok=True)
    with closing(sqlite3.connect(database)) as db:
        db.executescript("PRAGMA user_version=1; CREATE TABLE sentence(fingerprint TEXT PRIMARY KEY,text TEXT NOT "
                         "NULL,record TEXT NOT NULL); CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);")
        db.executemany("INSERT INTO meta VALUES (?,?)", [("format", sentence_format.FORMAT),
                                                          ("version", str(sentence_format.VERSION)),
                                                          ("engine", result["engine"]), ("candidate", "1")])
        db.executemany("INSERT INTO sentence VALUES (?,?,?)",
                       [(sha256(text.encode("utf-8")).hexdigest(), text,
                         json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                        for text, record in sorted(result["sentences"].items())])
        db.commit()
        db.execute("VACUUM")
    items = {cid: {field: dict(quotes) for field, quotes in fields.items()}
             for cid, fields in (card_base or {}).items()}
    for cid, fields in result["cards"].items():
        for field, quotes in fields.items():
            items.setdefault(cid, {}).setdefault(field, {}).update(
                {quote: spans for quote, spans in quotes.items() if quote not in items[cid][field]})
    (out / "card_quote_readings.candidates.json").write_text(json.dumps(
        {"format": card_format.FORMAT, "version": card_format.VERSION, "items": items},
        ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n", "utf-8")
    (out / "review.json").write_text(json.dumps(result["review"], ensure_ascii=False, indent=1) + "\n", "utf-8")
    if result.get("explanations"):
        (out / "explanation_lines.json").write_text(json.dumps(
            {"rule": EXPLANATION_RULE, "lines": result["explanations"]}, ensure_ascii=False, indent=1) + "\n", "utf-8")
    (out / "annotation_source.json").write_text(json.dumps(COURSE_SOURCE_RECORD, ensure_ascii=False, indent=1)
                                                + "\n", "utf-8")
    summary = {"engine": result["engine"], "edition": result.get("edition", ""),
               "totals": dict(sorted(result["totals"].items()))}
    if notes:
        summary["notes"] = list(notes)
    if agreement_result is not None:
        (out / "agreement.json").write_text(json.dumps(agreement_result, ensure_ascii=False, indent=1) + "\n",
                                            "utf-8")
        summary["agreement"] = agreement_result["counts"]
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n", "utf-8")
    return summary


def reviewed_references(annotations, readings_namespace, wanted, limit=None):
    """[(label, text, spans)] for the agreement check: rows of the sentence annotations (by reading source) and
    the prompts of the question_readings namespace (kanji parts read by the course's readings tool)."""
    references = []
    if annotations is not None and Path(annotations).is_file() and "sentences" in wanted:
        with closing(sqlite3.connect(Path(annotations).resolve().as_uri() + "?mode=ro", uri=True)) as db:
            for text, record in db.execute("SELECT text, record FROM sentence ORDER BY text"):
                segments = json.loads(record).get("segments", [])
                by_source = defaultdict(list)
                for s in segments:
                    if isinstance(s.get("reading"), str) and KANJI.search(s.get("surface", "")):
                        by_source[s.get("reading_source_id")].append((s["start"], s["end"], s["reading"],
                                                                      s["surface"]))
                for source, spans in by_source.items():
                    references.append(("sentence annotations: " + str(source), text, spans))
    if readings_namespace and "questions" in wanted:
        from kanamemory.learning_content import learning_items
        from kanamemory.learning_questions import practice_templates, template_fingerprint
        entries = readings_namespace.get("items", {})
        seen = set()
        for item in learning_items():
            for kind, variants in (practice_templates(item) or {}).items():
                for raw in variants:
                    entry = ((entries.get(item["id"]) or {}).get(kind) or {}).get(template_fingerprint(raw))
                    prompt = raw.get("prompt") if isinstance(raw, dict) else None
                    spans = (entry or {}).get("fields", {}).get("prompt")
                    if isinstance(prompt, str) and spans and prompt not in seen:
                        seen.add(prompt)
                        references.append(("question readings (prompt)", prompt,
                                           [(s[0], s[1], s[2], prompt[s[0]:s[1]]) for s in spans]))
    return references[:limit] if limit else references


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True, help="folder for the candidates (outside the resources)")
    parser.add_argument("--analyser-python", type=Path, default=os.environ.get("KANAMEMORY_ANALYSER_PYTHON"),
                        help="interpreter with pyopenjtalk-plus (default: KANAMEMORY_ANALYSER_PYTHON)")
    parser.add_argument("--kinds", default="sentences,cards",
                        help="sentences, cards and/or explanations (comma separated)")
    parser.add_argument("--annotations", type=Path, help="sentence annotations to complete (default: the app's)")
    parser.add_argument("--card-readings", type=Path, help="card quote readings to complete (default: the app's)")
    parser.add_argument("--overrides", type=Path, default=ROOT / "config" / "question_reading_overrides.json")
    parser.add_argument("--markers", type=Path, default=ROOT / "config" / "grammar_form_markers.json")
    parser.add_argument("--agreement", action="store_true", help="also compare the analyser with reviewed spans")
    parser.add_argument("--limit", type=int, help="at most this many texts per kind (trial runs)")
    parser.add_argument("--edition", choices=("landing", "development"), default="landing",
                        help="landing: the reading rules of the landing edition (numeral units, words beside a "
                             "blank, single-kanji card quotations, non-Japanese runs); development: the readings "
                             "of 2026-11-25")
    args = parser.parse_args(argv)
    kinds = {kind.strip() for kind in args.kinds.split(",") if kind.strip()}
    if not kinds or kinds - {"sentences", "cards", "explanations"}:
        parser.error("--kinds takes sentences, cards and/or explanations")
    if args.analyser_python is None:
        print("No analyser: pass --analyser-python <interpreter with pyopenjtalk-plus> "
              "(or set KANAMEMORY_ANALYSER_PYTHON).", file=sys.stderr)
        return EXIT_NO_ANALYSER
    from kanamemory.content_pack import QUESTION_READINGS, lexicon_path, load_namespace, side_file_path
    from kanamemory.learning_content import learning_items
    resources = (ROOT / "app" / "src" / "kanamemory" / "resources").resolve()
    if args.out.resolve().is_relative_to(resources):
        parser.error("--out must be outside the app's resources")
    annotations = args.annotations or side_file_path("sentence_annotations.sqlite3")
    card_file = args.card_readings or side_file_path(card_format.FILE)
    from kanamemory.question_readings import course_words
    items = learning_items()
    knowledge = CourseKnowledge.from_items(items, overrides=load_overrides(args.overrides),
                                           homographs=load_homographs(args.markers),
                                           dictionary=load_dictionary(lexicon_path()),
                                           word_keys=frozenset(course_words()))
    sentences, cards, answers, words = [], [], {}, {}
    if "sentences" in kinds:
        from sentence_annotation_corpus import collect_sentence_records
        records = collect_sentence_records(items=items)
        for record in records:
            sentences.append((record["text"], ",".join(sorted({s["category"] for s in record["sources"]}))))
        sentences = sentences[:args.limit] if args.limit else sentences
        answers, words = corpus_context(records, items)
    explanations, stages = [], Counter()
    if "explanations" in kinds:
        explanations = explanation_targets(course_explanations(items), stages)
        explanations = explanations[:args.limit] if args.limit else explanations
    if "cards" in kinds:
        cards = card_targets(items)
        cards = cards[:args.limit] if args.limit else cards
    analyser = SubprocessAnalyser(args.analyser_python)
    base, note = existing_cards(card_file)
    try:
        result = annotate(analyser, knowledge, sentences=sentences, cards=cards,
                          have_sentences=existing_sentences(annotations), have_cards=base, explanations=explanations,
                          landing=args.edition == "landing", answers=answers, course_words=words)
        checked = None
        if args.agreement:
            references = reviewed_references(annotations, load_namespace(QUESTION_READINGS, {}),
                                             {"sentences", "questions"}, args.limit)
            checked = agreement(analyser, knowledge, references)
    except AnalyserUnavailable as error:
        print("No analyser: " + str(error), file=sys.stderr)
        return EXIT_NO_ANALYSER
    result["totals"].update(stages)
    summary = write_outputs(args.out, result, card_base=base, agreement_result=checked,
                            notes=[note] if note else ())
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
