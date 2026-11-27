"""Word boundaries of a sentence record (validated, passed through, rows break only between words) and whole
explanation lines read from their exact sentence record. Made-up records in a temporary sidecar only."""
from contextlib import closing
from hashlib import sha256
import json
import sqlite3

import pytest

from kanamemory import sentence_annotations as runtime
from kanamemory import sentence_annotation_ui as renderer
from kanamemory.answer_furigana import explanation_line_segments
from kanamemory.answer_result_ui import shown_explanation
from kanamemory.result_explanations import course_explanation, shown_course_explanation
from kanamemory.result_readings import annotate_explanation
from support.sentence_annotation_ui_support import app, bodies  # noqa: F401  (fixture)

SOURCE = {"id": "test-reading-source", "title": "test", "url": "", "note": ""}


@pytest.fixture
def sidecar(tmp_path, monkeypatch):
    path = tmp_path / "annotations.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.executescript("PRAGMA user_version=1;CREATE TABLE sentence(fingerprint TEXT PRIMARY KEY,text TEXT,"
                         "record TEXT);CREATE TABLE fallback(surface TEXT PRIMARY KEY,record TEXT);")
    monkeypatch.setattr(runtime, "_database_path", lambda: path)
    monkeypatch.setattr(runtime, "ANNOTATION_SOURCES", (SOURCE,))
    runtime._lookup.cache_clear()
    yield path
    runtime._lookup.cache_clear()


def seg(text, surface, reading, start=None):
    start = text.index(surface) if start is None else start
    return {"start": start, "end": start + len(surface), "surface": surface, "reading": reading,
            "reading_source_id": SOURCE["id"]}


def save(path, text, segments, **fields):
    record = {"text": text, "version": 1, "status": "precomputed", "segments": segments, **fields}
    with closing(sqlite3.connect(path)) as db:
        db.execute("INSERT INTO sentence VALUES (?,?,?)", (sha256(text.encode()).hexdigest(), text,
                                                          json.dumps(record, ensure_ascii=False)))
        db.commit()
    runtime._lookup.cache_clear()
    return record


TEXT = "店は九時に開く。"
WORDS = [[0, 1], [1, 2], [2, 4], [4, 5], [5, 7]]


def test_word_boundaries_are_validated_and_passed_through(sidecar):
    segments = [seg(TEXT, "店", "みせ"), seg(TEXT, "九時", "くじ"), seg(TEXT, "開く", "あく")]
    save(sidecar, TEXT, segments, word_boundaries=WORDS)
    plan = runtime.sentence_annotation_plan(TEXT)
    assert plan["word_boundaries"] == WORDS and [s["surface"] for s in plan["segments"]] == ["店", "九時", "開く"]
    assert runtime._validated("学校", {"text": "学校", "version": 1, "segments": []}).get("word_boundaries") is None


@pytest.mark.parametrize("boundaries", [
    "0-1", [[0, 1, 2]], [[0, True]], [[0, 1.0]], [[1, 1]], [[2, 4], [0, 1]],   # shape, type, empty, order
    [[0, 2], [1, 3]], [[0, 9]], [[-1, 1]], [[2, 3]],                         # overlap, bounds, cuts 九時
    [[i, i + 1] for i in range(2049)],                                       # too many
])
def test_a_malformed_field_makes_the_record_fail_closed(boundaries, monkeypatch):
    monkeypatch.setattr(runtime, "ANNOTATION_SOURCES", (SOURCE,))
    text = TEXT if not isinstance(boundaries, list) or len(boundaries) < 2049 else "あ" * 2049
    segments = [seg(TEXT, "九時", "くじ")] if text == TEXT else []
    record = {"text": text, "version": 1, "segments": segments, "word_boundaries": boundaries}
    assert runtime._validated(text, record) is None
    good = {**record, "word_boundaries": [[0, 1]] if text != TEXT else [[2, 4]]}
    assert runtime._validated(text, good) is not None  # the same record with a good field is kept


LONG = "わたしはきのうともだちといっしょにおおきいこうえんへいきました。"


def _rows(app, monkeypatch, source, width):
    monkeypatch.setattr(renderer, "_annotation_plan", lambda _: source)
    block = renderer.build_sentence_block(app, source["text"], available_width=width,
                                          preferences={"furigana": True, "number": True})
    return block._feedback_fragment["lines"]


def _long_source(words=None):
    text = "公園" + LONG
    source = {"text": text, "segments": [{"start": 0, "end": 2, "surface": "公園", "reading": "こうえん",
                                          "reading_source_id": SOURCE["id"]}]}
    if words is not None:
        source["word_boundaries"] = words
    return text, source


def _kana_words(text):
    # made-up word boundaries of the kana sentence: 公園|わたし|は|きのう|ともだち|と|いっしょ|に|おおきい|こうえん|へ|いき|ました
    pieces = ["公園", "わたし", "は", "きのう", "ともだち", "と", "いっしょ", "に", "おおきい", "こうえん", "へ", "いき",
              "ました"]
    pairs, cursor = [], 0
    for piece in pieces:
        assert text.startswith(piece, cursor)
        pairs.append([cursor, cursor + len(piece)])
        cursor += len(piece)
    return pairs


def test_with_word_boundaries_a_row_breaks_only_between_words(app, monkeypatch):
    text, source = _long_source()
    source["word_boundaries"] = _kana_words(text)
    lines = _rows(app, monkeypatch, source, 150)
    assert len(lines) > 2
    starts = [line[0]["start"] for line in lines[1:]]
    assert all(not a < start < b for start in starts for a, b in source["word_boundaries"])
    assert "".join(cell["surface"] for line in lines for cell in line) == text  # nothing lost or added
    assert lines[0][0]["annotation"] == "こうえん"


def test_without_word_boundaries_rows_are_laid_out_as_before(app, monkeypatch):
    text, source = _long_source()
    before = _rows(app, monkeypatch, source, 150)
    # as before: plain kana wraps by character, so some row starts inside a word
    starts = [line[0]["start"] for line in before[1:]]
    assert any(a < start < b for start in starts for a, b in _kana_words(text))
    from kanamemory.furigana_ui import _prompt_lines
    plan = renderer._display_plan(text, source, {"furigana": True, "number": True})
    assert "word_boundaries" not in plan
    expected = _prompt_lines([{k: s.get(k) for k in ("surface", "reading", "accent_number")} for s in
                              plan["segments"]], 150, 20, 1)
    assert [sum(len(c["surface"]) for c in line) for line in before] == [
        sum(len(c["surface"]) for c in line) for line in expected]
    # a field that no longer fits the text (stale) is ignored the same way
    text, stale = _long_source([[0, 1]])
    assert _rows(app, monkeypatch, stale, 150) == before


QUESTION = {"id": "made-up:1", "kind": "grammar_meaning", "prompt": "どれですか。", "answer": "明天见",
            "feedback": "じゃあ、また明日。\n那明天见。\n学生\n分别时就说「また明日」。"}


def test_an_explanation_line_with_an_exact_record_is_drawn_with_its_readings(sidecar):
    save(sidecar, "じゃあ、また明日。", [seg("じゃあ、また明日。", "明日", "あした")], word_boundaries=[[0, 3], [4, 6], [6, 8]])
    shown = shown_explanation(QUESTION, "  じゃあ、また明日")  # trimmed, tried with a closing 。
    assert shown == "  じゃあ、また明日（あした）"
    assert explanation_line_segments(QUESTION, "じゃあ、また明日。")[0]["reading"] == "あした"
    assert shown_course_explanation(QUESTION).split("\n")[0] == "じゃあ、また明日（あした）。"


def test_a_line_without_a_record_stays_as_the_page_draws_it_today(sidecar):
    line = "ゆっくり明日来ます。"
    assert explanation_line_segments(QUESTION, line) is None
    assert shown_explanation(QUESTION, line) == annotate_explanation(QUESTION, line)


def test_a_chinese_line_of_kanji_only_is_never_read(sidecar):
    save(sidecar, "学生", [seg("学生", "学生", "がくせい")])
    assert explanation_line_segments(QUESTION, "学生") is None
    assert shown_explanation(QUESTION, "学生") == annotate_explanation(QUESTION, "学生")
    # the question's own Japanese of kanji only is looked up
    own = {**QUESTION, "prompt": "学生"}
    assert shown_explanation(own, "学生") == "学生（がくせい）"


def test_the_plain_explanation_for_comparisons_does_not_change(sidecar):
    plain = course_explanation(QUESTION)
    save(sidecar, "じゃあ、また明日。", [seg("じゃあ、また明日。", "明日", "あした")])
    assert course_explanation(QUESTION) == plain and "（あした）" not in plain
    assert "（あした）" in shown_course_explanation(QUESTION)


def test_a_record_never_removes_a_reading_the_line_shows_today(sidecar, monkeypatch):
    from kanamemory import result_readings
    line = "明日は学校へ行きます。"
    save(sidecar, line, [seg(line, "明日", "あした"), seg(line, "学校", "がっこう"), seg(line, "行き", "いき")])
    monkeypatch.setattr(result_readings, "known_pairs", lambda question: {"明日": "あす"})
    monkeypatch.setattr(result_readings, "_course_word", lambda text, start: None)  # the question's readings only
    today = annotate_explanation(QUESTION, line)
    assert today == "明日（あす）は学校へ行きます。"
    # today's reading of 明日 stays; the record adds only the words the line shows without kana
    assert shown_explanation(QUESTION, line) == "明日（あす）は学校（がっこう）へ行（い）きます。"
