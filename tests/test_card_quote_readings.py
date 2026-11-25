"""Readings for the Japanese inside 「」 of teaching cards: the pure insertion rule and the card pages.

Original made-up content only: no course text is read (design 02, teaching cards; card_quote_readings).
"""
import json

import pytest

from kanamemory import card_quote_readings as cards
from kanamemory import content_pack
from kanamemory import ui_components as component_ui
from kanamemory.question_readings import hides_card_reading
from kanamemory.structured_lesson_ui import original_notes, teaching_block
from support.fake_ui import descendants

GAKKOU = {"学校へ行く": [[0, 2, "がっこう", "course"], [3, 4, "い", "course"]]}


def test_each_annotated_kanji_part_gets_its_reading_inside_the_quotation():
    text = "「学校へ行く」表示去学校。"
    assert cards.insert_readings(text, GAKKOU) == "「学校（がっこう）へ行（い）く」表示去学校。"


def test_a_text_without_data_or_without_a_quotation_is_unchanged():
    text = "「学校へ行く」表示去学校。"
    assert cards.insert_readings(text, None) == text
    assert cards.insert_readings(text, {}) == text
    assert cards.insert_readings("学校的意思", GAKKOU) == "学校的意思"
    assert cards.insert_readings(text, {"学校で": [[0, 2, "がっこう", "course"]]}) == text  # another quotation


def test_a_quotation_all_in_kana_is_never_touched():
    data = {"いく": [[0, 2, "いく", "course"]]}
    assert cards.insert_readings("「いく」", data) == "「いく」"


def test_a_reading_the_text_already_gives_is_not_doubled():
    data = {"学校（がっこう）へ行く": [[0, 2, "がっこう", "course"], [9, 10, "い", "course"]]}
    assert cards.insert_readings("「学校（がっこう）へ行く」", data) == "「学校（がっこう）へ行（い）く」"
    after = {"学校": [[0, 2, "がっこう", "course"]]}
    assert cards.insert_readings("「学校」（がっこう）", after) == "「学校」（がっこう）"
    assert cards.insert_readings("「学校」(がっこう)", after) == "「学校」(がっこう)"
    tail = {"来る → 来よう（こよう）": [[0, 1, "く", "course"], [5, 6, "こ", "course"]]}
    assert cards.insert_readings("「来る → 来よう（こよう）」", tail) == "「来（く）る → 来よう（こよう）」"


def test_a_malformed_entry_leaves_its_quotation_as_it_is():
    text = "「学校へ行く」与「学校」"
    broken = {"学校へ行く": [[0, 3, "がっこう", "course"]],  # 学校へ: not only kanji
              "学校": [[0, 2, "がっこう", "course"]]}
    assert cards.insert_readings(text, broken) == "「学校へ行く」与「学校（がっこう）」"
    for spans in ([[2, 1, "が", "x"]], [[0, 2, "ガッコウ", "x"]], [[0, 2, "がっこう", ""]], [[0, 9, "が", "x"]],
                  [[0, 2, "がっこう"]], "x", [[0, 2, "がっこう", "x"], [1, 2, "こう", "x"]]):
        assert cards.insert_readings("「学校」", {"学校": spans}) == "「学校」"


def test_a_word_the_question_hides_keeps_no_reading():
    hidden = {"学校"}
    shown = cards.insert_readings("「学校へ行く」", GAKKOU, hidden=lambda surface, reading: surface in hidden)
    assert shown == "「学校へ行（い）く」"


def test_a_reading_question_hides_its_target_and_answers_a_meaning_question_hides_nothing(monkeypatch):
    from kanamemory import question_readings
    monkeypatch.setattr(question_readings, "_lesson_forms", lambda cid: (frozenset("学校"), frozenset({"がっこう"})))
    reading = {"kind": "vocab_reading", "target_id": "word:test:gakkou", "answer": "がっこう", "family": "word"}
    hides = hides_card_reading(reading)
    assert hides("学校", "がっこう") and hides("校", "こう") and not hides("行", "い")
    meaning = {"kind": "vocab_meaning", "target_id": "word:test:gakkou", "answer": "学校", "family": "word",
               "domain": "vocabulary", "ability": "understanding", "input_mode": "choice"}
    assert hides_card_reading(meaning) is None
    pitch = {"kind": "pronunciation_rule", "target_id": "pronunciation:test", "domain": "pronunciation"}
    assert hides_card_reading(pitch)("行", "い")


def test_card_fields_name_every_prose_text_of_a_view():
    view = {"focus": "「学校」", "blocks": [
        {"kind": "points", "label": "L", "items": ["a", "b"], "caption": "c"},
        {"kind": "compare", "label": "L", "rows": [["t", "x"], ["u", "y"]]},
        {"kind": "pattern", "label": "L", "text": "p"}]}
    fields = cards.card_fields(view, [("用法", ["n0"]), ("步骤", ["n1", "n2"])])
    assert fields == [("focus", "「学校」"), ("blocks.0.caption", "c"), ("blocks.0.items.0", "a"),
                      ("blocks.0.items.1", "b"), ("blocks.1.rows.0.0", "t"), ("blocks.1.rows.0.1", "x"),
                      ("blocks.1.rows.1.0", "u"), ("blocks.1.rows.1.1", "y"), ("blocks.2.text", "p"),
                      ("notes.0.0", "n0"), ("notes.1.0", "n1"), ("notes.1.1", "n2")]


ITEM = {"id": "word:test:card", "revision": 1, "family": "word", "domain": "vocabulary", "title": "学校",
        "reading": "がっこう", "meaning": "学校",
        "instruction": {"summary": "「学校」是上学的地方。", "rule": "「学校へ行く」表示去学校。",
                        "steps": ["说「学校で勉強する」。"]}}


@pytest.fixture
def card_data(tmp_path, monkeypatch):
    path = tmp_path / cards.FILE
    path.write_text(json.dumps({"format": cards.FORMAT, "version": cards.VERSION, "items": {ITEM["id"]: {
        "focus": {"学校": [[0, 2, "がっこう", "course"]]},
        "blocks.0.items.0": GAKKOU,
        "notes.1.0": {"学校で勉強する": [[0, 2, "がっこう", "course"], [3, 5, "べんきょう", "course"]]}}}},
        ensure_ascii=False), "utf-8")
    real = content_pack.side_file_path
    monkeypatch.setattr(content_pack, "side_file_path", lambda name: path if name == cards.FILE else real(name))
    cards._items.cache_clear()
    yield path
    cards._items.cache_clear()


def _texts(node):
    return [str(n.text) for n in descendants(node) if isinstance(getattr(n, "text", None), str)]


def test_the_card_page_shows_the_readings_from_the_data(card_data):
    texts = _texts(teaching_block(component_ui, ITEM))
    assert "「学校（がっこう）」是上学的地方。" in texts
    assert "• 「学校（がっこう）へ行（い）く」表示去学校。" in texts
    notes = original_notes(component_ui, ITEM)
    notes.children[0].on_press()  # 补充说明
    assert "说「学校（がっこう）で勉強（べんきょう）する」。" in _texts(notes)


def test_the_card_page_hides_what_the_question_on_it_tests(card_data, monkeypatch):
    from kanamemory import question_readings
    monkeypatch.setattr(question_readings, "_lesson_forms", lambda cid: (frozenset("学校"), frozenset({"がっこう"})))
    question = {"kind": "vocab_reading", "target_id": ITEM["id"], "answer": "がっこう", "family": "word"}
    texts = _texts(teaching_block(component_ui, ITEM, question=question))
    assert "「学校」是上学的地方。" in texts
    assert "• 「学校へ行（い）く」表示去学校。" in texts


def test_without_data_the_card_page_shows_the_text_as_it_is(tmp_path, monkeypatch):
    real = content_pack.side_file_path
    monkeypatch.setattr(content_pack, "side_file_path",
                        lambda name: tmp_path / "absent.json" if name == cards.FILE else real(name))
    cards._items.cache_clear()
    try:
        texts = _texts(teaching_block(component_ui, ITEM))
    finally:
        cards._items.cache_clear()
    assert "「学校」是上学的地方。" in texts and "• 「学校へ行く」表示去学校。" in texts
    block = teaching_block(component_ui, ITEM)
    assert block._feedback_fragment["plan"]["focus"] == "「学校」是上学的地方。"  # the stored text never changes
