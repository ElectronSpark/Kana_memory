"""Readings for the Japanese inside 「」 of teaching cards: the pure insertion rule and the card pages.

Original made-up content only: no course text is read (design 02, teaching cards; card_quote_readings).
"""
import json
from types import SimpleNamespace

import pytest

from kanamemory import card_quote_readings as cards
from kanamemory import card_reading_options
from kanamemory import content_pack
from kanamemory import ui_components as component_ui
from kanamemory.question_readings import hides_card_reading
from kanamemory.structured_lesson_ui import block_widget, original_notes, teaching_block
from support.fake_ui import descendants

GAKKOU_WORD, IKU_WORD = ["学校", "がっこう"], ["行く", "いく"]
GAKKOU = {"学校へ行く": [[0, 2, "がっこう", "course", GAKKOU_WORD], [3, 4, "い", "course", IKU_WORD]]}


def test_each_annotated_kanji_part_gets_its_reading_inside_the_quotation():
    text = "「学校へ行く」表示去学校。"
    assert cards.insert_readings(text, GAKKOU) == "「学校（がっこう）へ行（い）く」表示去学校。"


def test_a_text_without_data_or_without_a_quotation_is_unchanged():
    text = "「学校へ行く」表示去学校。"
    assert cards.insert_readings(text, None) == text
    assert cards.insert_readings(text, {}) == text
    assert cards.insert_readings("学校的意思", GAKKOU) == "学校的意思"
    assert cards.insert_readings(text, {"学校で": [[0, 2, "がっこう", "course", None]]}) == text  # another quotation


def test_a_quotation_all_in_kana_is_never_touched():
    data = {"いく": [[0, 2, "いく", "course", None]]}
    assert cards.insert_readings("「いく」", data) == "「いく」"


def test_a_reading_the_text_already_gives_is_not_doubled():
    data = {"学校（がっこう）へ行く": [[0, 2, "がっこう", "course", None], [9, 10, "い", "course", None]]}
    assert cards.insert_readings("「学校（がっこう）へ行く」", data) == "「学校（がっこう）へ行（い）く」"
    after = {"学校": [[0, 2, "がっこう", "course", None]]}
    assert cards.insert_readings("「学校」（がっこう）", after) == "「学校」（がっこう）"
    assert cards.insert_readings("「学校」(がっこう)", after) == "「学校」(がっこう)"
    tail = {"来る → 来よう（こよう）": [[0, 1, "く", "course", None], [5, 6, "こ", "course", None]]}
    assert cards.insert_readings("「来る → 来よう（こよう）」", tail) == "「来（く）る → 来よう（こよう）」"


def test_a_malformed_entry_leaves_its_quotation_as_it_is():
    text = "「学校へ行く」与「学校」"
    broken = {"学校へ行く": [[0, 3, "がっこう", "course", None]],  # 学校へ: not only kanji
              "学校": [[0, 2, "がっこう", "course", None]]}
    assert cards.insert_readings(text, broken) == "「学校へ行く」与「学校（がっこう）」"
    for spans in ([[2, 1, "が", "x", None]], [[0, 2, "ガッコウ", "x", None]], [[0, 2, "がっこう", "", None]],
                  [[0, 9, "が", "x", None]], [[0, 2, "がっこう", "x"]], [[0, 2, "がっこう"]], "x",
                  [[0, 2, "がっこう", "x", None], [1, 2, "こう", "x", None]]):
        assert cards.insert_readings("「学校」", {"学校": spans}) == "「学校」"


def test_checked_spans_reads_version_two_and_rejects_a_malformed_word():
    assert cards.VERSION == 2
    assert cards.checked_spans("学校へ行く", GAKKOU["学校へ行く"]) == (
        (0, 2, "がっこう", ("学校", "がっこう")), (3, 4, "い", ("行く", "いく")))
    assert cards.checked_spans("学校", [[0, 2, "がっこう", "course", None]]) == ((0, 2, "がっこう", None),)
    for word in (["学校"], ["学校", ""], ["", "がっこう"], ["学校", 1], "学校", ["学校", "がっこう", "x"],
                 ["学" * 65, "がっこう"], {"t": "学校"}, 0, False):
        assert cards.checked_spans("学校", [[0, 2, "がっこう", "course", word]]) is None, word
    # One malformed span rejects the whole quotation entry, also the well-formed span beside it.
    entry = [[0, 2, "がっこう", "course", GAKKOU_WORD], [3, 4, "い", "course", ["行く"]]]
    assert cards.checked_spans("学校へ行く", entry) is None
    assert cards.insert_readings("「学校へ行く」", {"学校へ行く": entry}) == "「学校へ行く」"


def _learned(*words):
    return lambda word: list(word) in [list(w) for w in words]


def test_a_learned_word_loses_its_reading_only_while_the_switch_is_off():
    text = "「学校へ行く」"
    learned = _learned(GAKKOU_WORD)
    assert cards.insert_readings(text, GAKKOU, furigana=False, learned=learned) == "「学校へ行（い）く」"
    assert cards.insert_readings(text, GAKKOU, furigana=True, learned=learned) == "「学校（がっこう）へ行（い）く」"
    # Without a learned rule (no learner) the switch changes nothing; a span without a course word is never learned.
    assert cards.insert_readings(text, GAKKOU, furigana=False) == "「学校（がっこう）へ行（い）く」"
    plain = {"学校へ行く": [[0, 2, "がっこう", "analyser", None], [3, 4, "い", "course", IKU_WORD]]}
    assert cards.insert_readings(text, plain, furigana=False, learned=lambda word: True) == "「学校（がっこう）へ行く」"


def test_the_learned_rule_comes_after_hidden_and_reading_given():
    def hidden(surface, reading):
        return surface == "行"  # the question on the page hides 行
    shown = cards.insert_readings("「学校へ行く」", GAKKOU, hidden, furigana=True, learned=_learned(GAKKOU_WORD))
    assert shown == "「学校（がっこう）へ行く」"
    shown = cards.insert_readings("「学校へ行く」", GAKKOU, hidden, furigana=False, learned=_learned())
    assert shown == "「学校（がっこう）へ行く」"  # unlearned: keeps its reading; the hidden word stays hidden
    given = {"学校（がっこう）へ行く": [[0, 2, "がっこう", "course", GAKKOU_WORD], [9, 10, "い", "course", IKU_WORD]]}
    for furigana in (True, False):
        assert cards.insert_readings("「学校（がっこう）へ行く」", given, furigana=furigana,
                                     learned=_learned(IKU_WORD)) == (
            "「学校（がっこう）へ行（い）く」" if furigana else "「学校（がっこう）へ行く」")


def test_card_readings_with_and_without_known_lessons(monkeypatch):
    from kanamemory import question_readings
    monkeypatch.setattr(question_readings, "course_words", lambda: {
        ("学校", "がっこう"): frozenset({"word:test:gakkou"}), ("行く", "いく"): frozenset({"word:test:iku"})})
    monkeypatch.setattr(cards, "quotes_for", lambda content_id, field: GAKKOU)
    text = "「学校へ行く」"
    assert cards.CardReadings("x").text("f", text) == "「学校（がっこう）へ行（い）く」"
    assert cards.CardReadings("x", furigana=False).text("f", text) == "「学校（がっこう）へ行（い）く」"  # no learner
    known = {"word:test:gakkou"}
    assert cards.CardReadings("x", furigana=False, known_ids=known).text("f", text) == "「学校へ行（い）く」"
    assert cards.CardReadings("x", furigana=True, known_ids=known).text("f", text) == "「学校（がっこう）へ行（い）く」"
    assert cards.CardReadings("x", furigana=False, known_ids=set()).text("f", text) == "「学校（がっこう）へ行（い）く」"


def test_the_course_page_options_read_the_switch_and_the_known_lessons(monkeypatch):
    from kanamemory import course_planning
    reads = []
    monkeypatch.setattr(course_planning, "known_ids", lambda queries: reads.append(queries) or frozenset({"a"}))
    settings = {}
    store = SimpleNamespace(queries="Q", get_setting=lambda key, default: settings.get(key, default))
    app = SimpleNamespace(store=store)
    assert card_reading_options.course_page(app) == {"furigana": True, "known_ids": frozenset({"a"})}
    settings["prompt_display_v1"] = {"furigana": False}
    assert card_reading_options.course_page(app, {"b"}) == {"furigana": False, "known_ids": frozenset({"b"})}
    assert reads == ["Q"]  # the page's own set is used when it has one


def test_drop_kana_echo_removes_only_a_bracket_that_repeats_the_whole_kana_run():
    echo = cards.drop_kana_echo
    assert echo("こちらこそ（こちらこそ）：我才是") == "こちらこそ：我才是"
    assert echo("ああ(ああ)：那样") == "ああ：那样"
    assert echo("テレビ（テレビ）") == "テレビ" and echo("コーヒー(コーヒー)") == "コーヒー"
    assert echo("あ（あ）") == "あ（あ）"  # a single kana stays
    assert echo("ここ（そこ）") == "ここ（そこ）" and echo("テレビ（てれび）") == "テレビ（てれび）"  # a different reading
    assert echo("食べる（たべる）") == "食べる（たべる）" and echo("来る（る）") == "来る（る）"  # kanji before the bracket
    assert echo("そこここ（ここ）") == "そこここ（ここ）"  # the run is the whole kana run before the bracket
    assert echo("あげる→あげました（あげました）：x；ここ（ここ）、そこ（そこ）") == "あげる→あげました：x；ここ、そこ"
    assert echo("「学校（がっこう）」") == "「学校（がっこう）」" and echo(None) is None and echo("") == ""


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
        "focus": {"学校": [[0, 2, "がっこう", "course", GAKKOU_WORD]]},
        "blocks.0.items.0": GAKKOU,
        "notes.1.0": {"学校で勉強する": [[0, 2, "がっこう", "course", GAKKOU_WORD],
                                     [3, 5, "べんきょう", "course", ["勉強", "べんきょう"]]]}}}},
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


def test_a_version_one_file_is_ignored_like_a_malformed_file(card_data):
    data = json.loads(card_data.read_text("utf-8"))
    data["version"] = 1
    data["items"][ITEM["id"]]["focus"] = {"学校": [[0, 2, "がっこう", "course"]]}
    card_data.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    cards._items.cache_clear()
    texts = _texts(teaching_block(component_ui, ITEM))
    assert "「学校」是上学的地方。" in texts and "• 「学校へ行く」表示去学校。" in texts


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


def test_a_card_quotation_with_an_inserted_reading_and_a_kana_echo(tmp_path, monkeypatch):
    item = {"id": "word:test:echo", "revision": 1, "family": "word", "domain": "vocabulary", "title": "こちらこそ",
            "reading": "こちらこそ", "meaning": "我才是",
            "instruction": {"summary": "「こちらこそ（こちらこそ）：我才是」", "rule": "「学校へ行く」与「ああ(ああ)」。"}}
    path = tmp_path / cards.FILE
    path.write_text(json.dumps({"format": cards.FORMAT, "version": cards.VERSION, "items": {item["id"]: {
        "blocks.0.items.0": GAKKOU}}}, ensure_ascii=False), "utf-8")
    real = content_pack.side_file_path
    monkeypatch.setattr(content_pack, "side_file_path", lambda name: path if name == cards.FILE else real(name))
    cards._items.cache_clear()
    try:
        block = teaching_block(component_ui, item)
    finally:
        cards._items.cache_clear()
    texts = _texts(block)
    assert "「こちらこそ：我才是」" in texts
    assert "• 「学校（がっこう）へ行（い）く」与「ああ」。" in texts
    assert block._feedback_fragment["plan"]["focus"] == "「こちらこそ（こちらこそ）：我才是」"  # stored text unchanged
    # A block drawn without card readings (a page's own block) drops the echo as well.
    assert "• ああ" in _texts(block_widget(component_ui, {"kind": "points", "label": "L", "items": ["ああ（ああ）"]}))
