"""Kana for what a page shows after an answer (design 11 section 7, rule 9).

Before an answer question_readings decides which words may carry kana: a reading can be the very
thing under test. After the answer nothing is left to give away, and the owner wants the readings
there every time (2026-11-12): every kanji word a result page shows carries its kana inline —
学校（がっこう）へ, 食（た）べます — whatever the 日文假名注音 switch says, learned or not, the
target included.

Pure display rules: no Toga, learner database, platform or app import, and nothing here changes a
snapshot, a draft, a submitted value, a grade or the stored feedback text.

Where a reading comes from, in this order:
1. the question's own entry in the question_readings namespace: its options and its sentence
   fields (prompt, task, hint), each with the reading chosen for that sentence;
2. the lesson under test: its title with its reading;
3. for the heard-word task, the candidate words its options were drawn from;
4. a course word lesson whose title is the whole text, when the course teaches one reading for it.
A kanji word for which none of these has a reading is shown as written; so is everything when no
reading data is installed for the question. Readings are never guessed from single characters: a
word is annotated only as a whole run of kanji (日 is not read inside 日曜日).

Chinese text is never annotated. The explanation under a result mixes Chinese and Japanese, and
the two share characters (学校 is a word of both). There a known word gets its kana only where it
is visibly Japanese: on a line without Chinese punctuation that contains kana, inside Japanese
quotation marks, or directly beside kana (飲む, 切符を).
"""
from __future__ import annotations

from collections.abc import Mapping
import re

from . import word_sound_questions as sound
from .question_readings import _GAP, _KANJI, _course_words, inline_kana, question_entry

_KANJI_RUN = re.compile(r"[㐀-䶿一-鿿豈-﫿々]+")
_KANA = re.compile(r"[ぁ-ゖァ-ヺー]")
_CHINESE_MARKS = re.compile(r"[，；：“”‘’！？]|[A-Za-z]{2}")
_QUOTED = re.compile(r"「[^「」]*」|『[^『』]*』")


def _hiragana(text):
    return "".join(chr(ord(char) - 0x60) if "ァ" <= char <= "ヶ" else char for char in text)


def word_spans(text, reading):
    """[(start, end, kana)] for the kanji runs of one word, from the reading of the whole word.

    食べる / たべる gives 食→た; 取り消す / とりけす gives 取→と, 消→け. A word whose kana does not
    line up with its reading keeps the whole reading on the whole word.
    """
    if not isinstance(text, str) or not isinstance(reading, str) or not reading or not _KANJI.search(text):
        return []
    target = sound.normal_reading(reading)
    parts = re.findall(r"[㐀-䶿一-鿿豈-﫿々]+|[^㐀-䶿一-鿿豈-﫿々]+", text)
    pattern = "".join("(.+?)" if _KANJI.match(part) else re.escape(_hiragana(part)) for part in parts)
    match = re.fullmatch(pattern, target)
    if match is None:
        return [(0, len(text), target)]
    spans, cursor, group = [], 0, 1
    for part in parts:
        if _KANJI.match(part):
            spans.append((cursor, cursor + len(part), match.group(group)))
            group += 1
        cursor += len(part)
    return spans


def _lesson(content_id):
    from .learning_content import learning_item_view
    item = learning_item_view(content_id) if isinstance(content_id, str) else None
    return item if isinstance(item, Mapping) else {}


def _course_reading(title):
    """The one reading the course teaches for a word written so, else None."""
    readings = {reading for (written, reading) in _course_words() if written == title}
    return next(iter(readings)) if len(readings) == 1 else None


def _add(pairs, text, spans):
    for start, end, reading in spans:
        pairs.setdefault(text[start:end], reading)


def known_pairs(question):
    """{kanji word as written: kana}, from the sources of the module text, first source wins."""
    pairs = {}
    if not isinstance(question, Mapping):
        return pairs
    entry = question_entry(question)
    if entry is not None:
        for name in ("prompt", "task", "hint"):
            located = entry["fields"].get(name)
            if located is not None:
                _add(pairs, located[0], [(start, end, reading) for start, end, reading, _ in located[1]])
        for raw, spans in entry["options"].items():
            _add(pairs, raw, [(start, end, reading) for start, end, reading, _ in spans])
    lesson = _lesson(question.get("target_id"))
    title, reading = lesson.get("title"), lesson.get("reading")
    if isinstance(title, str) and isinstance(reading, str) and lesson.get("family") == "word":
        _add(pairs, title, word_spans(title, reading))
    if question.get("kind") == sound.AUDIO_WORD and lesson:
        from .learning_questions import _word_pool
        shown = {choice for choice in question.get("choices") or () if isinstance(choice, str)}
        try:
            rows = sound._sound_rows(lesson, _word_pool())
        except (KeyError, TypeError, ValueError):
            rows = ()
        seen = set()
        for other, kana, _ in rows:
            if other in shown and other not in seen:
                seen.add(other)
                _add(pairs, other, word_spans(other, kana))
    return pairs


def _tile(text, start, surfaces, depth=0):
    """Known words that cover text from the start of a kanji run up to a point where no kanji follows.

    会社員 is covered by 会社 + 員 when both are known; 日 alone never covers the start of 日曜日.
    None when the run cannot be covered completely.
    """
    if depth > 6:
        return None
    for surface in surfaces:
        if not text.startswith(surface, start):
            continue
        end = start + len(surface)
        if not _KANJI.match(text[end:end + 1]):
            return [(start, end, surface)]
        rest = _tile(text, end, surfaces, depth + 1)
        if rest is not None:
            return [(start, end, surface), *rest]
    return None


def _course_titles():
    """{first character: [(title, reading)]} of the course words with one reading, longest title first."""
    readings = {}
    for title, reading in _course_words():
        readings.setdefault(title, set()).add(reading)
    index = {}
    for title, values in readings.items():
        # A single kanji is read too many ways to be taken from a title alone (方, 日, 月).
        if len(values) == 1 and len(title) > 1 and _KANJI.match(title[:1]):
            index.setdefault(title[0], []).append((title, next(iter(values))))
    for rows in index.values():
        rows.sort(key=lambda row: -len(row[0]))
    return index


_TITLES = [None, None]  # (the course word table it was built from, the index)


def _course_word(text, start):
    """(end, spans) of the longest course word written at start with one reading, else None."""
    table = _course_words()
    if _TITLES[0] is not table:
        _TITLES[:] = [table, _course_titles()]
    for title, reading in _TITLES[1].get(text[start:start + 1], ()):
        end = start + len(title)
        if text.startswith(title, start) and not _KANJI.match(text[end:end + 1]):
            return end, [(start + first, start + last, kana) for first, last, kana in word_spans(title, reading)]
    return None


_NOTED = re.compile(r"（[ぁ-ゖァ-ヺー]+）")


def _tail(text, run, surfaces):
    """A known word that ends a run begun by other characters and is followed by kana, else None.

    In a Chinese sentence a Japanese word follows Chinese characters without a break (动词飲む): the
    run 动词飲 does not start with a known word, but it ends with one, and kana follows it.
    """
    if not _KANA.match(text[run.end():run.end() + 1]):
        return None
    for surface in surfaces:
        start = text.rfind(surface, run.start() + 1, run.end() + len(surface))
        if start > run.start() and start < run.end() <= start + len(surface) \
                and not _KANJI.match(text[start + len(surface):start + len(surface) + 1]):
            return [(start, start + len(surface), surface)]
    return None


def _spans(text, pairs, allowed=None, *, course=True, tails=False):
    """Readings for the kanji words of text, left to right; never for one already followed by （かな）.

    A word is taken only as a whole run of kanji: first the question's own words (pairs), tiled over the
    run; else, with course, the longest course word written there that the course reads one way. tails
    (mixed Chinese and Japanese text): also a known word that ends a run and has kana after it.
    """
    spans, surfaces, cursor = [], sorted(pairs, key=len, reverse=True), 0
    while True:
        run = _KANJI_RUN.search(text, cursor)
        if run is None:
            return spans
        start = run.start()
        tiles = _tile(text, start, surfaces) or (_tail(text, run, surfaces) if tails else None)
        if tiles is not None:
            found = [(first, last, pairs[surface]) for first, last, surface in tiles]
            end = tiles[-1][1]
        else:
            located = _course_word(text, start) if course else None
            if located is None:
                cursor = run.end()
                continue
            end, found = located
        spans.extend((first, last, kana) for first, last, kana in found
                     if not _NOTED.match(text, last) and (allowed is None or allowed(first, last)))
        cursor = max(end, run.end())


def _whole(text, pairs):
    """Spans for a text that is one course word, when nothing of it is in pairs."""
    title = text.strip()
    reading = _course_reading(title) if title else None
    if reading is None:
        return []
    offset = text.index(title)
    return [(offset + start, offset + end, kana) for start, end, kana in word_spans(title, reading)]


def answers_in_japanese(question):
    """Whether the answer and the options of this question are Japanese (else Chinese meanings).

    A meaning must never be marked: 大学 and 学校 are words of both languages, and the answer of an
    understanding question is the Chinese one. Decided by what the question trains, never by the
    characters: understanding answers with a meaning; listening answers with the written word in
    the heard-word task and with a meaning in the heard-meaning tasks; use, application and reading
    answer in Japanese. A question of another ability counts as Japanese when its answer has kana
    or is the word under test as written.
    """
    if not isinstance(question, Mapping):
        return False
    answer, ability = question.get("answer"), question.get("ability")
    if ability == "understanding" or not isinstance(answer, str):
        return False
    if ability == "listening":
        return question.get("kind") == sound.AUDIO_WORD
    if ability in {"use", "application", "reading"}:
        return True
    lesson = _lesson(question.get("target_id"))
    written = {lesson.get("title"), *(lesson.get("spellings") or ())}
    return bool(_KANA.search(answer) or answer in written)


def annotate(question, text):
    """A Japanese string a result page shows (an answer, an option, a sentence), with inline kana.

    The caller decides that the string is Japanese (answers_in_japanese for an answer or an option).
    """
    spans = annotation_spans(question, text)
    return inline_kana(text, spans) if spans else text


def annotation_spans(question, text):
    """[(start, end, kana)] that annotate writes inline: the same readings, for a page that sets them above."""
    return annotation_reader(question)(text)


def annotation_reader(question):
    """annotation_spans of one question as a function of the text. Its entry and known words are looked up
    once, at the first text that needs them, for a caller that reads many texts of the same question (the
    judge's accepted answers)."""
    looked_up = {}

    def spans(text):
        if not isinstance(text, str) or not _KANJI.search(text):
            return []
        if "entry" not in looked_up:
            looked_up["entry"] = question_entry(question) if isinstance(question, Mapping) else None
        entry = looked_up["entry"]
        if entry is not None and text in entry["options"]:
            return [(start, end, reading) for start, end, reading, _ in entry["options"][text]]
        if entry is not None:
            for raw, located in entry["fields"].values():
                if raw == text:  # the sentence itself: the readings chosen for it, at their own places
                    return [(start, end, reading) for start, end, reading, _ in located
                            if not _NOTED.match(text, end)]
        if "pairs" not in looked_up:
            looked_up["pairs"] = known_pairs(question)
        pairs = looked_up["pairs"]
        # A text that is one course word (an option of a generated kind) before any word-by-word reading.
        return ([] if any(surface in text for surface in pairs) else _whole(text, pairs)) or _spans(text, pairs)
    return spans


def annotate_explanation(question, text):
    """The explanation under a result: kana only where a known word is visibly Japanese (module text)."""
    if not isinstance(text, str) or not _KANJI.search(text):
        return text
    pairs = known_pairs(question)
    lines = []
    for line in text.split("\n"):
        if not _KANJI.search(line):
            lines.append(line)
            continue
        # A Japanese sentence: no Chinese punctuation and at least one kana for every two kanji
        # (动词行く的学校在这里。 has kana too, but it is a Chinese sentence about a Japanese verb).
        japanese = (not _CHINESE_MARKS.search(line)
                    and 2 * len(_KANA.findall(line)) >= len(_KANJI.findall(line)) > 0)
        quoted = [match.span() for match in _QUOTED.finditer(line)]

        def allowed(start, end, line=line, japanese=japanese, quoted=quoted):
            return (japanese or any(first < start and end < last for first, last in quoted)
                    or start > 0 and bool(_KANA.match(line[start - 1]))
                    or end < len(line) and bool(_KANA.match(line[end])))
        spans = _spans(line, pairs, allowed, tails=True)
        lines.append(inline_kana(line, spans) if spans else line)
    return "\n".join(lines)


def stimulus(question):
    """The Japanese the question was about, for a result page that would otherwise not show it.

    A Japanese prompt sentence, completed with the answer where it has exactly one gap; else, for a
    word question, the word itself. None for a Chinese prompt of a rule question and when the
    question has no target lesson. Plain text: the page annotates it like everything else.
    """
    if not isinstance(question, Mapping):
        return None
    prompt, answer = question.get("prompt"), question.get("answer")
    lesson = _lesson(question.get("target_id"))
    title = lesson.get("title") if lesson.get("family") == "word" else None
    entry = question_entry(question)
    located = entry["fields"].get("prompt") if entry is not None else None
    hidden = question.get("prompt_visible") is False or question.get("audio_only")
    if isinstance(prompt, str) and not hidden and (located is not None and located[0] == prompt or prompt == title):
        gaps = list(_GAP.finditer(prompt))
        if len(gaps) == 1 and isinstance(answer, str) and answer and "\n" not in answer:
            return prompt[:gaps[0].start()] + answer + prompt[gaps[0].end():]
        return prompt if not gaps else None
    return title if isinstance(title, str) and title else None
