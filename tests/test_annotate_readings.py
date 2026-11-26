"""tools/annotate_readings.py with a fake analyser: the order of sources, never overwriting, the review flags,
exact coverage, and candidate files the app reads. Made-up content only; the real analyser is never run here."""
import json
import sqlite3

import pytest

import annotate_readings as tool
from annotate_readings import AnalyserAdapter, CourseKnowledge, Token
from kanamemory import card_quote_readings as cards
from kanamemory import sentence_annotations


class FakeAnalyser(AnalyserAdapter):
    """Tokens from a fixed table: {text: [(surface, lemma, katakana reading, pos, pos1), ...]}."""

    def __init__(self, table):
        self.table, self.asked = table, []

    def tokenise(self, texts):
        self.asked.append(sorted(texts))
        return {text: [Token(s, l, tool.hiragana(r), p, p1) for s, l, r, p, p1 in self.table[text]]
                for text in texts if text in self.table}, "fake-1"


def tok(surface, lemma=None, reading="", pos="名詞", pos1="一般"):
    return (surface, lemma or surface, reading, pos, pos1)


MISE = "店は九時に開く。"
TABLE = {
    MISE: [tok("店", reading="ミセ"), tok("は", reading="ハ", pos="助詞", pos1="係助詞"), tok("九時", reading="クジ"),
           tok("に", reading="ニ", pos="助詞", pos1="格助詞"), tok("開く", reading="ヒラク", pos="動詞", pos1="自立"),
           tok("。", reading="、", pos="記号", pos1="句点")],
    "学校へ行った": [tok("学校", reading="ガッコウ"), tok("へ", reading="エ", pos="助詞", pos1="格助詞"),
                tok("行っ", "行く", "イッ", "動詞", "自立"), tok("た", reading="タ", pos="助動詞", pos1="")],
    "明日来ます": [tok("明日", reading="アシタ"), tok("来", "来る", "キ", "動詞", "自立"),
              tok("ます", reading="マス", pos="助動詞", pos1="")],
    "木の下で": [tok("木", reading="キ"), tok("の", reading="ノ", pos="助詞", pos1="連体化"), tok("下", reading="モト"),
             tok("で", reading="デ", pos="助詞", pos1="格助詞")],
    "Ａ：雨": [tok("Ａ", reading="エー", pos="記号"), tok("：", reading="、", pos="記号"), tok("雨", reading="アメ")],
    "雨だ": [tok("雨", reading="アメ")],  # the analyser lost だ: the tokens do not cover the text
    "30分待つ": [tok("三", reading="サン"), tok("十", reading="ジュッ"), tok("分", reading="プン"),
              tok("待つ", reading="マツ", pos="動詞", pos1="自立")],  # the analyser rewrote 30
}
LESSONS = [
    {"id": "word:t:mise", "family": "word", "title": "店", "reading": "みせ"},
    {"id": "word:t:aku", "family": "word", "title": "開く", "reading": "あく"},
    {"id": "word:t:hiraku", "family": "word", "title": "開く", "reading": "ひらく"},
    {"id": "word:t:gakkou", "family": "word", "title": "学校", "reading": "がっこう"},
    {"id": "word:t:iku", "family": "word", "title": "行く", "reading": "いく"},
    {"id": "word:t:kuru", "family": "word", "title": "来る", "reading": "くる"},
    {"id": "grammar:t:wa", "family": "grammar", "title": "は", "reading": ""},
]
HOMOGRAPHS = {"_feature": "test", "開く": [
    {"reading": "ひらく", "clause_has": {"s": "を", "p": "助詞", "p1": "格助詞"}},
    {"reading": "あく", "sentence_has": [{"s": "は", "p": "助詞", "p1": "係助詞"}],
     "clause_lacks": {"s": "を", "p": "助詞", "p1": "格助詞"}},
    {"reading": "あく", "otherwise": True}]}
OVERRIDES = [{"span": "下", "lemma": "下", "reading": "した", "note": "木の下で"},
             {"span": "店", "reading": "てん", "when_ref": "word:t:x", "note": "bound to a lesson: skipped"}]


def knowledge(**changes):
    options = {"overrides": OVERRIDES, "homographs": HOMOGRAPHS, "dictionary": {"明日": {"あした", "あす"}}}
    options.update(changes)
    return CourseKnowledge.from_items(LESSONS, **options)


def spans_of(text, known=None):
    analysed, _ = FakeAnalyser(TABLE).tokenise([text])
    spans, problems = tool.text_spans(text, analysed, known or knowledge())
    return {s["surface"]: (s["reading"], s["source"], s["heteronym"]) for s in spans}, problems


def test_the_course_decides_before_the_analyser_and_the_homograph_rule_reads_aku():
    spans, problems = spans_of(MISE)
    assert spans["店"] == ("みせ", "course", False)
    assert spans["開く"] == ("あく", "homograph", False)  # the analyser said ひらく
    assert spans["九時"] == ("くじ", "analyser", False)
    assert problems == []


def test_an_override_comes_first_and_a_rule_bound_to_a_lesson_is_skipped():
    spans, _ = spans_of("木の下で")
    assert spans["下"] == ("した", "override", False) and spans["木"] == ("き", "analyser", False)
    assert all(rule.get("when_ref") is None for rule in knowledge().overrides)


def test_inflected_forms_take_the_lesson_stem_and_irregular_ones_stay_with_the_analyser():
    spans, _ = spans_of("学校へ行った")
    assert spans["行っ"] == ("いっ", "course", False) and spans["学校"] == ("がっこう", "course", False)
    spans, _ = spans_of("明日来ます")
    assert spans["来"] == ("き", "analyser", False)
    assert spans["明日"] == ("あした", "analyser", True)  # two readings in the dictionary data: reviewed
    assert tool.inflected_reading("食べ", "食べる", "たべる") == "たべ"
    assert tool.inflected_reading("高かっ", "高い", "たかい") == "たかかっ"
    assert tool.inflected_reading("来", "来る", "くる") is None


def test_without_the_homograph_rules_the_analyser_reading_of_a_shared_title_goes_to_review():
    spans, _ = spans_of(MISE, knowledge(homographs={}))
    assert spans["開く"] == ("ひらく", "analyser", True)


def test_coverage_exact_normalised_and_failed():
    assert spans_of("Ａ：雨")[1] == []
    analysed = {"A：雨": FakeAnalyser(TABLE).tokenise(["Ａ：雨"])[0]["Ａ：雨"]}
    spans, problems = tool.text_spans("A：雨", analysed, knowledge())
    assert [p["flag"] for p in problems] == ["normalised"] and spans[0]["surface"] == "雨"
    spans, problems = spans_of("雨だ")
    assert [p["flag"] for p in problems] == ["coverage"]
    spans, problems = spans_of("30分待つ")  # placed again after the rewritten digits; flagged all the same
    assert [p["flag"] for p in problems] == ["coverage"] and spans == {"分": ("ぷん", "analyser", False),
                                                                        "待つ": ("まつ", "analyser", False)}


def test_kanji_parts_align_the_reading_with_the_kana_of_the_word():
    assert tool.kanji_parts("取り消す", "とりけす") == [(0, 1, "と"), (2, 3, "け")]
    assert tool.kanji_parts("学校", "がっこう") == [(0, 2, "がっこう")]
    assert tool.kanji_parts("食べる", "のむ") is None


def test_annotate_never_overwrites_and_flags_for_review(tmp_path):
    analyser = FakeAnalyser(TABLE)
    have_cards = {"grammar:t:x": {"focus": {"学校へ行った": [[0, 2, "がっこう", "course", ["学校", "がっこう"]]]}}}
    result = tool.annotate(analyser, knowledge(),
                           sentences=[(MISE, "shared_example"), ("学校へ行った", "shared_example"),
                                      ("明日来ます", "public_question_prompt"), ("いく", "shared_example")],
                           cards=[("grammar:t:x", "focus", "学校へ行った"), ("grammar:t:x", "blocks.0.text", MISE)],
                           have_sentences={"学校へ行った"}, have_cards=have_cards)
    assert set(result["sentences"]) == {MISE, "明日来ます"}  # an existing row and a text without kanji: no target
    assert list(result["cards"]) == ["grammar:t:x"] and list(result["cards"]["grammar:t:x"]) == ["blocks.0.text"]
    assert result["cards"]["grammar:t:x"]["blocks.0.text"][MISE] == [
        [0, 1, "みせ", "course", ["店", "みせ"]], [2, 4, "くじ", "analyser", None],
        [5, 6, "あ", "homograph", ["開く", "あく"]]]
    assert [row["surface"] for row in result["review"]["heteronym"]] == ["明日"]
    totals = result["totals"]
    assert totals["sentence spans by homograph"] == 1 and totals["sentence flagged heteronym"] == 1
    assert sorted(analyser.asked[0]) == sorted({MISE, "明日来ます"})  # nothing that has an annotation is analysed
    tool.write_outputs(tmp_path, result, card_base=have_cards)
    written = json.loads((tmp_path / "card_quote_readings.candidates.json").read_text("utf-8"))
    assert written["items"]["grammar:t:x"]["focus"] == have_cards["grammar:t:x"]["focus"]  # kept as it was
    assert cards.insert_readings("「" + MISE + "」", written["items"]["grammar:t:x"]["blocks.0.text"]) == \
        "「店（みせ）は九時（くじ）に開（あ）く。」"
    review = json.loads((tmp_path / "review.json").read_text("utf-8"))
    assert review["heteronym"][0]["source"] == "analyser"
    assert written["version"] == cards.VERSION == 2


def test_a_card_quotation_with_a_gap_is_analysed_piece_by_piece():
    analyser = FakeAnalyser(TABLE)
    quote = "学校へ行った…木の下で"
    result = tool.annotate(analyser, knowledge(), cards=[("grammar:t:x", "focus", quote)])
    assert analyser.asked[0] == sorted({"学校へ行った", "木の下で"})  # the pieces between the gap, not the quotation
    assert result["cards"]["grammar:t:x"]["focus"][quote] == [
        [0, 2, "がっこう", "course", ["学校", "がっこう"]], [3, 4, "い", "course", ["行く", "いく"]],
        [7, 8, "き", "analyser", None], [9, 10, "した", "override", None]]
    assert result["review"]["coverage"] == [] and result["totals"]["card quotations with candidates"] == 1


def _words(text, known=None):
    analysed, _ = FakeAnalyser(TABLE).tokenise([text])
    spans, _ = tool.text_spans(text, analysed, known or knowledge())
    return {s["surface"]: s["word"] for s in spans}


def test_every_span_carries_the_course_word_that_teaches_it_as_read_here():
    # A course word in its dictionary form, and one whose chosen reading is its lesson's (homograph rule).
    assert _words(MISE) == {"店": ["店", "みせ"], "九時": None, "開く": ["開く", "あく"]}
    # An inflected form: its dictionary form is the lesson's written form and the derived reading the lesson's.
    assert _words("学校へ行った") == {"学校": ["学校", "がっこう"], "行っ": ["行く", "いく"]}
    # A word the course does not teach (木) and an override whose reading has no lesson (下 した): null.
    assert _words("木の下で") == {"木": None, "下": None}
    # A homograph whose chosen reading is not the lesson's: only 開く ひらく is taught, the rule reads あく.
    only_hiraku = knowledge(word_keys={("開く", "ひらく"), ("店", "みせ")})
    assert _words(MISE, only_hiraku) == {"店": ["店", "みせ"], "九時": None, "開く": None}
    # A key is written only when the app's lookup has it: the same lessons without the lookup's keys give null.
    assert _words("学校へ行った", knowledge(word_keys=set())) == {"学校": None, "行っ": None}
    # The irregular 来る: its stem reading cannot be derived from the lesson's, so no key.
    assert _words("明日来ます")["来"] is None


def test_the_default_keys_are_the_apps_projection_of_the_lessons():
    from kanamemory.word_sound_questions import word_pool
    keys = {(row[1], row[2]) for row in word_pool([item for item in LESSONS if item["family"] == "word"])}
    assert knowledge().word_keys == {title: {r for t, r in keys if t == title} for title, _ in keys}


def test_a_version_one_card_file_counts_as_no_existing_entries(tmp_path):
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"format": cards.FORMAT, "version": 1,
                               "items": {"grammar:t:x": {"focus": {"学校": [[0, 2, "がっこう", "course"]]}}}}), "utf-8")
    items, note = tool.existing_cards(old)
    assert items == {} and "version 1" in note
    current = tmp_path / "current.json"
    current.write_text(json.dumps({"format": cards.FORMAT, "version": 2, "items": {"a": {}}}), "utf-8")
    assert tool.existing_cards(current) == ({"a": {}}, None)
    assert tool.existing_cards(tmp_path / "absent.json") == ({}, None)
    result = tool.annotate(FakeAnalyser(TABLE), knowledge(), cards=[("grammar:t:x", "focus", MISE)])
    summary = tool.write_outputs(tmp_path / "out", result, card_base=items, notes=[note])
    assert summary["notes"] == [note] and summary["totals"]["card spans with a course word"] == 2
    assert json.loads((tmp_path / "out" / "summary.json").read_text("utf-8"))["notes"] == [note]


def test_the_candidate_rows_are_the_app_format_once_the_course_source_is_known(tmp_path, monkeypatch):
    result = tool.annotate(FakeAnalyser(TABLE), knowledge(), sentences=[(MISE, "shared_example")])
    tool.write_outputs(tmp_path, result)
    with sqlite3.connect(tmp_path / "sentence_annotations.candidates.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == sentence_annotations.VERSION
        text, record = db.execute("SELECT text, record FROM sentence").fetchone()
    record = json.loads(record)
    assert text == MISE and [s["reading_derivation"] for s in record["segments"]] == ["course", "analyser",
                                                                                     "homograph"]
    source = json.loads((tmp_path / "annotation_source.json").read_text("utf-8"))
    assert sentence_annotations._validated(text, record) is None  # an unknown source fails closed
    monkeypatch.setattr(sentence_annotations, "ANNOTATION_SOURCES",
                        (*sentence_annotations.ANNOTATION_SOURCES, source,
                         {"id": tool.ANALYSER_SOURCE, "title": "x", "url": "", "note": ""}))
    checked = sentence_annotations._validated(text, record)
    assert [s["reading"] for s in checked["segments"]] == ["みせ", "くじ", "あく"]


def test_without_an_analyser_the_tool_says_so_and_exits_with_its_code(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("KANAMEMORY_ANALYSER_PYTHON", raising=False)
    assert tool.main(["--out", str(tmp_path / "out")]) == tool.EXIT_NO_ANALYSER
    assert "No analyser" in capsys.readouterr().err
    with pytest.raises(tool.AnalyserUnavailable, match="No analyser interpreter"):
        tool.SubprocessAnalyser(tmp_path / "absent" / "python.exe").tokenise(["雨"])


def test_the_agreement_check_counts_per_reference_and_samples_disagreements():
    references = [("reviewed", MISE, [(0, 1, "みせ", "店"), (5, 7, "あく", "開く"), (2, 4, "くじ", "九時")]),
                  ("reviewed", "学校へ行った", [(3, 5, "いっ", "行っ"), (0, 1, "がく", "学")])]
    checked = tool.agreement(FakeAnalyser(TABLE), knowledge(), references)
    assert checked["counts"]["reviewed"] == {"compared": 4, "agreed": 3, "heteronym compared": 2,
                                             "heteronym agreed": 1, "unaligned": 1}
    assert checked["disagreements"] == [{"reference": "reviewed", "text": MISE, "surface": "開く", "reviewed": "あく",
                                         "analyser": "ひらく", "heteronym": True}]
