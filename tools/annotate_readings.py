"""Complete missing reading annotations: the course's own knowledge first, then an offline analyser.

Design: docs/sentence_annotations.txt ("补全注音") and the teaching cards of design 02. A content tool run by hand;
it writes CANDIDATES into --out and never into the app's resources: the content side reviews and lands them.

  python -X utf8 tools/annotate_readings.py --out <folder> [--analyser-python <python with pyopenjtalk-plus>]
         [--kinds sentences,cards] [--agreement] [--overrides FILE] [--markers FILE] [--limit N]

Targets
  sentences  each exact text of tools/sentence_annotation_corpus.py (example sentences and the Japanese of question
             prompts) that has kanji and no row in the sentence annotations (--annotations, default: the app's own
             resolved copy, content_pack.side_file_path): a candidate row in that file's format
             (kanamemory-sentence-annotations v1, table sentence; segments without accent numbers). A gap of a
             prompt is never analysed as text: the pieces between gaps are analysed one by one.
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
        tokens = {text: [Token(row[0], row[1] or row[0], hiragana(row[2]), *row[3:7]) for row in rows]
                  for text, rows in data["tokens"].items()}
        return tokens, data.get("engine", "")


# ----------------------------------------------------------------------------- course knowledge

def _strings(value):
    return [v for v in value if isinstance(v, str) and v] if isinstance(value, (list, tuple)) else []


class CourseKnowledge:
    """What the course knows about readings: word lessons, reviewed overrides, homograph rules, dictionary."""

    def __init__(self, words=None, overrides=(), homographs=None, dictionary=None, word_keys=()):
        self.words = {form: dict(readings) for form, readings in (words or {}).items()}  # form -> {reading: [ids]}
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
        return cls(words, overrides, homographs, dictionary, word_keys)

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


def text_spans(text, analysed, knowledge):
    """Word spans of one text: ([{start, end, surface, reading, source, lemma, heteronym}], problems)."""
    spans, problems = [], []
    for offset, piece in pieces(text):
        tokens = analysed.get(piece)
        if tokens is None:
            problems.append({"flag": "coverage", "piece": piece, "reason": "not analysed"})
            continue
        placed, status = _aligned(piece, tokens)
        if status == "failed":
            problems.append({"flag": "coverage", "piece": piece,
                             "tokens": "|".join(token.surface for token in tokens)[:300]})
        elif status == "normalised":
            problems.append({"flag": "normalised", "piece": piece})
        indexes = {id(token): n for n, token in enumerate(tokens)}
        for start, token in placed:
            if not KANJI.search(token.surface):
                continue
            if not _japanese(token.surface):
                problems.append({"flag": "unknown", "surface": token.surface, "reason": "not a Japanese kanji"})
                continue
            if not token.reading or not HIRAGANA_READING.fullmatch(token.reading):
                problems.append({"flag": "unknown", "surface": token.surface, "reason": "no usable reading"})
                continue
            reading, source = decide(knowledge, tokens, indexes[id(token)], offset + start, text)
            if reading is None or not HIRAGANA_READING.fullmatch(reading):
                continue
            spans.append({"start": offset + start, "end": offset + start + len(token.surface),
                          "surface": token.surface, "reading": reading, "source": source, "lemma": token.lemma,
                          "word": knowledge.word_key(token, reading),
                          "heteronym": source == "analyser" and knowledge.is_heteronym(token),
                          "known_readings": sorted(knowledge.readings_of(token.surface, token.lemma))})
    return spans, problems


def sentence_record(text, spans):
    """A candidate row of the sentence annotations (the app's format; no accent numbers)."""
    segments = [{"start": s["start"], "end": s["end"], "surface": s["surface"], "reading": s["reading"],
                 "reading_source_id": ANALYSER_SOURCE if s["source"] == "analyser" else COURSE_SOURCE,
                 "reading_derivation": s["source"]} for s in spans]
    covered = {i for s in spans for i in range(s["start"], s["end"])}
    unresolved = [{"start": m.start(), "end": m.end(), "surface": m.group()}
                  for m in sentence_format.KANJI_RUN.finditer(text)
                  if any(i not in covered for i in range(m.start(), m.end()))]
    return {"text": text, "segments": segments, "version": sentence_format.VERSION, "status": "candidate",
            "unresolved": unresolved}


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


def annotate(analyser, knowledge, *, sentences=(), cards=(), have_sentences=(), have_cards=None):
    """The candidates for ``sentences`` ([(text, source label)]) and ``cards`` ([(content id, field, quote)]).

    Returns {"sentences": {text: record}, "cards": {cid: {field: {quote: spans}}}, "review": {...},
    "totals": Counter, "engine": str}. Texts and quotations that already have an annotation are skipped.
    """
    have_cards, have_sentences = have_cards or {}, set(have_sentences)
    todo_sentences = {text: label for text, label in sentences if KANJI.search(text) and text not in have_sentences}
    todo_cards = [(cid, field, quote) for cid, field, quote in cards
                  if quote not in ((have_cards.get(cid) or {}).get(field) or {})]
    # text_spans reads a text piece by piece (between its gaps): collect the pieces of quotations too, or a
    # quotation with a gap (「何とも…ない」) finds no tokens when no sentence of the run shares its pieces
    texts = ({piece for text in todo_sentences for _, piece in pieces(text)}
             | {piece for _, _, quote in todo_cards for _, piece in pieces(quote)})
    analysed, engine = analyser.tokenise(sorted(texts)) if texts else ({}, "")
    totals, review = Counter(), {"heteronym": [], "coverage": [], "unknown": [], "alignment": []}
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
        spans, problems = text_spans(text, analysed, knowledge)
        note("sentence", {"kind": "sentence", "text": text, "source": label}, spans, problems)
        totals["sentence texts"] += 1
        if spans:
            result_sentences[text] = sentence_record(text, spans)
            totals["sentence texts with candidates"] += 1
    for cid, field, quote in todo_cards:
        spans, problems = text_spans(quote, analysed, knowledge)
        entry, alignment = quote_entry(quote, spans)
        note("card", {"kind": "card", "content_id": cid, "field": field, "quote": quote}, spans,
             problems + alignment)
        totals["card quotations"] += 1
        if entry:
            result_cards[cid][field][quote] = entry
            totals["card quotations with candidates"] += 1
            totals["card spans"] += len(entry)
            totals["card spans with a course word"] += sum(span[4] is not None for span in entry)
    return {"sentences": result_sentences, "cards": {cid: dict(f) for cid, f in result_cards.items()},
            "review": review, "totals": totals, "engine": engine}


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
    (out / "annotation_source.json").write_text(json.dumps(COURSE_SOURCE_RECORD, ensure_ascii=False, indent=1)
                                                + "\n", "utf-8")
    summary = {"engine": result["engine"], "totals": dict(sorted(result["totals"].items()))}
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
    parser.add_argument("--kinds", default="sentences,cards", help="sentences, cards or both (comma separated)")
    parser.add_argument("--annotations", type=Path, help="sentence annotations to complete (default: the app's)")
    parser.add_argument("--card-readings", type=Path, help="card quote readings to complete (default: the app's)")
    parser.add_argument("--overrides", type=Path, default=ROOT / "config" / "question_reading_overrides.json")
    parser.add_argument("--markers", type=Path, default=ROOT / "config" / "grammar_form_markers.json")
    parser.add_argument("--agreement", action="store_true", help="also compare the analyser with reviewed spans")
    parser.add_argument("--limit", type=int, help="at most this many texts per kind (trial runs)")
    args = parser.parse_args(argv)
    kinds = {kind.strip() for kind in args.kinds.split(",") if kind.strip()}
    if not kinds or kinds - {"sentences", "cards"}:
        parser.error("--kinds takes sentences and/or cards")
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
    sentences, cards = [], []
    if "sentences" in kinds:
        from sentence_annotation_corpus import collect_sentence_records
        for record in collect_sentence_records(items=items):
            sentences.append((record["text"], ",".join(sorted({s["category"] for s in record["sources"]}))))
        sentences = sentences[:args.limit] if args.limit else sentences
    if "cards" in kinds:
        cards = card_targets(items)
        cards = cards[:args.limit] if args.limit else cards
    analyser = SubprocessAnalyser(args.analyser_python)
    base, note = existing_cards(card_file)
    try:
        result = annotate(analyser, knowledge, sentences=sentences, cards=cards,
                          have_sentences=existing_sentences(annotations), have_cards=base)
        checked = None
        if args.agreement:
            references = reviewed_references(annotations, load_namespace(QUESTION_READINGS, {}),
                                             {"sentences", "questions"}, args.limit)
            checked = agreement(analyser, knowledge, references)
    except AnalyserUnavailable as error:
        print("No analyser: " + str(error), file=sys.stderr)
        return EXIT_NO_ANALYSER
    summary = write_outputs(args.out, result, card_base=base, agreement_result=checked,
                            notes=[note] if note else ())
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
