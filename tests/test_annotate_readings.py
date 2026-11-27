"""tools/annotate_readings.py with a fake analyser: the order of sources, never overwriting, the review flags,
exact coverage, and candidate files the app reads. Made-up content only; the real analyser is never run here."""
from collections import Counter
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


def test_quote_entry_keeps_a_word_before_an_optional_piece_and_skips_a_given_reading():
    spans = [{"start": 3, "end": 7, "surface": "高かった", "reading": "たかかった", "source": "course",
              "word": ["高い", "たかい"]}]
    assert tool.quote_entry("高い→高かった（です）", spans) == ([[3, 4, "たか", "course", ["高い", "たかい"]]], [])
    given = [{"start": 0, "end": 2, "surface": "学校", "reading": "がっこう", "source": "course", "word": None}]
    assert tool.quote_entry("学校（がっこう）", given) == ([], [])


def _boundaries(text, spans=None, table=None):
    analysed, _ = FakeAnalyser(table or TABLE).tokenise([piece for _, piece in tool.pieces(text)])
    if spans is None:
        spans = tool.text_spans(text, analysed, knowledge())[0]
    return tool.word_boundaries(text, analysed, spans)


def test_word_boundaries_are_the_word_tokens_placed_exactly_on_the_text():
    pairs, unaligned = _boundaries(MISE)
    assert [MISE[a:b] for a, b in pairs] == ["店", "は", "九時", "に", "開く"]  # no pair for 。
    assert unaligned == 0 and pairs == sorted(pairs)
    assert all(a < b <= c for (a, b), (c, _) in zip(pairs, pairs[1:]))  # in order, no overlap
    # Tokens the text does not spell exactly (三 十 for 30) give no pair and are counted; 分 and 待つ still do.
    pairs, unaligned = _boundaries("30分待つ")
    assert [("30分待つ")[a:b] for a, b in pairs] == ["分", "待つ"] and unaligned == 2


def test_a_reading_segment_that_the_tokens_would_split_stays_one_word():
    table = {"食べてみる": [tok("食べ", "食べる", "タベ", "動詞", "自立"), tok("て", reading="テ", pos="助詞"),
                       tok("みる", reading="ミル", pos="動詞", pos1="非自立")]}
    segment = {"start": 0, "end": 3, "surface": "食べて", "reading": "たべて"}
    pairs, _ = _boundaries("食べてみる", [segment], table)
    assert pairs == [[0, 3], [3, 5]]
    # A segment inside one token: the token's pair contains it and stays as it is.
    pairs, _ = _boundaries("食べてみる", [{"start": 0, "end": 1, "surface": "食", "reading": "た"}], table)
    assert pairs == [[0, 2], [2, 3], [3, 5]]


def test_kana_words_get_a_boundary_and_never_a_segment_or_a_reading(tmp_path):
    result = tool.annotate(FakeAnalyser(TABLE), knowledge(), sentences=[(MISE, "shared_example")])
    record = result["sentences"][MISE]
    assert [s["surface"] for s in record["segments"]] == ["店", "九時", "開く"]  # segments exactly as before
    assert all(s.get("reading") for s in record["segments"])
    assert [MISE[a:b] for a, b in record["word_boundaries"]] == ["店", "は", "九時", "に", "開く"]
    totals = result["totals"]
    assert totals["sentence segments with a reading"] == 3 and totals["sentence word boundaries"] == 5
    assert totals["sentence word tokens not aligned"] == 0
    tool.write_outputs(tmp_path, result)
    with sqlite3.connect(tmp_path / "sentence_annotations.candidates.sqlite3") as db:
        written = json.loads(db.execute("SELECT record FROM sentence").fetchone()[0])
    assert written["word_boundaries"] == record["word_boundaries"]


@pytest.mark.parametrize("line, own, taken", [
    ("じゃあ、また明日。", (), True),
    ("A：お疲れ様でした。また明日。", (), True),
    ("那明天见。", (), False),                                   # simplified Chinese: not JIS X 0208
    ("分别时，确定明天还会见面，就说「また明日」；常加ね：また明日ね。", (), False),
    ("ではありません是です的礼貌否定。", (), False),            # Chinese function words around kana
    ("名词＋ではありません。", (), False),
    ("日本語", (), False),                                       # kanji only: not the question's own text
    ("日本語", ("日本語",), True),
    ("目的地に着きました。", (), True),                          # 的 before に is Japanese
    ("学生", ("がくせい",), False),
    ("ありがとう", (), False),                                   # no kanji: nothing to read
    (" 学校へ行く。", (), False),                                # not trimmed
    ("写真（しゃしん）：照片。", (), False),                     # a glossary line: its own reading, a Chinese gloss
    ("A：駅はどこですか。", (), True),                          # a dialogue label before Japanese
])
def test_the_explanation_line_rule(line, own, taken):
    assert tool.japanese_line(line, own) is taken


def test_unread_runs_are_the_kanji_runs_drawn_without_their_kana():
    assert tool.unread_runs("学校（がっこう）へ行った") == ["行"]
    assert tool.unread_runs("食（た）べた。日本語（にほんご）") == []
    assert tool.unread_runs("日本（にほん）語の本") == ["語", "本"]


def test_only_lines_with_an_unread_kanji_run_today_become_candidates(tmp_path):
    # (question id, explanation, own texts, the explanation as the result page draws it today)
    questions = [
        ("q:1", "店は九時に開く。\n商店九点开门。", {"店"}, "店（みせ）は九時に開く。\n商店九点开门。"),
        ("q:2", MISE + "\n" + MISE, set(), "店（みせ）は九時（くじ）に開（あ）く。\n" + MISE),
        ("q:3", "学校へ行った\n“学校”的意思。", set(), "学校（がっこう）へ行（い）った\n“学校”的意思。"),
        ("q:4", "木の下で", set(), "木の下で"),
    ]
    stages = Counter()
    lines = tool.explanation_targets(questions, stages)
    # 学校へ行った is read everywhere today: no candidate; the Chinese line never counts
    assert lines == [
        {"line": MISE, "question_ids": ["q:1", "q:2"], "unread_question_ids": ["q:1", "q:2"],
         "unread_runs": ["九時", "開", "店"]},
        {"line": "木の下で", "question_ids": ["q:4"], "unread_question_ids": ["q:4"], "unread_runs": ["木", "下"]}]
    assert stages["explanation Japanese lines"] == 3 and stages["explanation lines with an unread kanji run"] == 2
    assert stages["explanation questions with an unread kanji run"] == 3
    result = tool.annotate(FakeAnalyser(TABLE), knowledge(), explanations=lines, have_sentences={"木の下で"})
    assert list(result["sentences"]) == [MISE]  # an existing row is never a target
    assert [(e["line"], e["annotated"], e["candidate"]) for e in result["explanations"]] == [
        (MISE, False, True), ("木の下で", True, False)]
    assert result["totals"]["explanation lines with candidates"] == 1
    tool.write_outputs(tmp_path, result)
    written = json.loads((tmp_path / "explanation_lines.json").read_text("utf-8"))
    assert written["rule"] == tool.EXPLANATION_RULE and written["lines"] == result["explanations"]


# ----------------------------------------------------------------------------- the landing rules (follow-ups)

class TokenAnalyser(AnalyserAdapter):
    """Tokens with the analyser's full tags: {text: [Token, ...]}."""

    def __init__(self, table):
        self.table, self.asked = table, []

    def tokenise(self, texts):
        self.asked.append(sorted(texts))
        return {text: list(self.table[text]) for text in texts if text in self.table}, "fake-2"


def T(surface, reading, pos="名詞", pos1="一般", pos2="*", pron=None, lemma=None):
    return Token(surface, lemma or surface, tool.hiragana(reading), pos, pos1, pos2, "", tool.hiragana(pron or reading))


NUM = dict(pos="名詞", pos1="数")
CTR = dict(pos="名詞", pos1="接尾", pos2="助数詞")
LANDING = {
    "三本の木": [T("三", "サン", **NUM), T("本", "ホン", pron="ボン", **CTR), T("の", "ノ", "助詞", "連体化"),
             T("木", "キ")],
    "30分待つ": [T("三", "サン", **NUM), T("十", "ジュウ", pron="ジュッ", **NUM), T("分", "フン", pron="プン", **CTR),
              T("待つ", "マツ", "動詞", "自立")],
    "何本": [T("何", "ナニ", **NUM), T("本", "ホン", pron="ボン", **CTR)],
    "三時間": [T("三", "サン", **NUM), T("時", "ジ", **CTR), T("間", "カン", "名詞", "接尾")],
    "三回薬": [T("三", "サン", **NUM), T("回", "カイ", **CTR), T("薬", "クスリ", "名詞", "接尾")],
    "一週間": [T("一", "イチ", pron="イッ", **NUM), T("週間", "シュウカン", pron="シューカン", **CTR)],
    "名词の形": [T("名", "メイ", "接頭詞", "名詞接続"), T("词", "シ"), T("の", "ノ", "助詞", "連体化"), T("形", "カタチ")],
    # a blank: the pieces, and the text with the answer filled in
    "学校": [T("学校", "ガッコウ")],
    "行きます": [T("行き", "イキ", "動詞", "自立", lemma="行く"), T("ます", "マス", "助動詞")],
    "学校へ行きます": [T("学校", "ガッコウ"), T("へ", "エ", "助詞", "格助詞"), T("行き", "イキ", "動詞", "自立", lemma="行く"),
                T("ます", "マス", "助動詞")],
    "何": [T("何", "ナニ", "名詞", "代名詞")],
    "います": [T("い", "イ", "動詞", "自立"), T("ます", "マス", "助動詞")],
    "何人います": [T("何人", "ナンニン"), T("い", "イ", "動詞", "自立"), T("ます", "マス", "助動詞")],
}


def landing_spans(text, **options):
    analysed, _ = TokenAnalyser(LANDING).tokenise([*LANDING])
    spans, problems = tool.text_spans(text, analysed, knowledge(), **options)
    return {s["surface"]: (s["reading"], s["source"]) for s in spans}, problems


def test_rule_1_a_numeral_with_its_counter_is_one_segment_read_as_a_whole():
    spans, _ = landing_spans("三本の木")
    assert spans["三本"] == ("さんぼん", "analyser") and "三" not in spans and "本" not in spans
    spans, _ = landing_spans("30分待つ")  # digits the analyser rewrote: the run of digits before the counter
    assert spans["30分"] == ("さんじゅっぷん", "analyser")
    assert landing_spans("一週間")[0]["一週間"] == ("いっしゅうかん", "analyser")  # long vowels from the reading
    assert landing_spans("三時間")[0] == {"三時間": ("さんじかん", "analyser")}  # a kanji suffix after the counter
    assert landing_spans("三回薬")[0]["三回"] == ("さんかい", "analyser")  # 薬 is not part of the count
    assert "何本" not in landing_spans("何本")[0]  # 何 is not a numeral: the rule does not apply
    # the development edition keeps the readings of 2026-11-25
    assert "三本" not in landing_spans("三本の木", landing=False)[0]


def test_rule_1_without_a_kana_reading_the_unit_gets_none_and_an_override_still_wins():
    analysed = {"三本": [T("三", "サン", **NUM), T("本", "ホン", pron="?", **CTR)]}
    spans, problems = tool.text_spans("三本", analysed, knowledge())
    assert spans == [] and problems[-1]["reason"] == "numeral unit without a reading"
    analysed = {"三本": [T("三", "サン", **NUM), T("本", "ホン", pron="ボン", **CTR)]}
    own = knowledge(overrides=[{"span": "三本", "reading": "みもと"}])
    assert [(s["reading"], s["source"]) for s in tool.text_spans("三本", analysed, own)[0]] == [("みもと", "override")]


def test_rule_2_a_word_beside_a_blank_is_read_with_the_answer_filled_in_or_not_at_all():
    text = "学校（　）行きます"
    spans, _ = landing_spans(text, blanks=True, fills=[(2, 5, "へ")])
    assert spans == {"学校": ("がっこう", "course"), "行き": ("いき", "course")}
    spans, _ = landing_spans(text, blanks=True)  # no single answer: the words beside the blank get none
    assert spans == {}
    # the answer joins the word beside the blank (何＋人 = 何人): it cannot be mapped back, so no reading
    spans, _ = landing_spans("何（　）います", blanks=True, fills=[(1, 4, "人")])
    assert "何" not in spans
    assert tool.filled_text("学校（　）行きます", [(2, 5, "へ")]) == ("学校へ行きます", [(0, 2, 0, 2), (5, 9, 3, 7)])
    assert tool.filled_text("学校（　）行きます", [(2, 5, "へ\n")]) is None


def test_rule_3_a_single_kanji_card_quotation_takes_only_the_lessons_own_reading():
    lessons = [{"id": "word:t:hi", "family": "word", "title": "日", "reading": "ひ"},
               {"id": "word:t:ushiro", "family": "word", "title": "後ろ", "reading": "うしろ"},
               {"id": "word:t:hannichi", "family": "word", "title": "半日", "reading": "はんにち"}]
    known = CourseKnowledge.from_items(lessons, word_keys=set())
    assert known.own_chars == {"word:t:hi": {"日": "ひ"}, "word:t:ushiro": {"後": "うし"}}
    table = {"日": [T("日", "ニチ")], "後": [T("後", "アト")]}
    result = tool.annotate(TokenAnalyser(table), known, cards=[("word:t:hi", "focus", "日"),
                                                             ("word:t:ushiro", "focus", "後"),
                                                             ("word:t:hannichi", "focus", "日")])
    assert result["cards"]["word:t:hi"]["focus"]["日"] == [[0, 1, "ひ", "course", None]]
    assert result["cards"]["word:t:ushiro"]["focus"]["後"] == [[0, 1, "うし", "course", None]]
    assert "word:t:hannichi" not in result["cards"]  # 半日 states no reading for 日 alone: none, not the analyser's
    assert result["totals"]["card single-kanji quotations"] == 3


def test_rule_4_a_run_with_a_non_japanese_character_gets_no_reading():
    spans, problems = landing_spans("名词の形")
    assert spans == {"形": ("かたち", "analyser")}
    assert {p["surface"] for p in problems if p["flag"] == "unknown"} == {"名", "词"}
    assert tool.foreign_runs("名词の形と日本語") == [(0, 2)]


UNITS = {
    "行きました": [T("行き", "イキ", "動詞", "自立"), T("まし", "マシ", "助動詞"), T("た", "タ", "助動詞")],
    "食べている": [T("食べ", "タベ", "動詞", "自立"), T("て", "テ", "助詞", "接続助詞"), T("いる", "イル", "動詞", "非自立")],
    "学生です": [T("学生", "ガクセイ"), T("です", "デス", "助動詞")],
    "学生でした": [T("学生", "ガクセイ"), T("でし", "デシ", "助動詞"), T("た", "タ", "助動詞")],
    "お茶を": [T("お", "オ", "接頭詞", "名詞接続"), T("茶", "チャ"), T("を", "ヲ", "助詞", "格助詞")],
    "郵便局の田中さん": [T("郵便", "ユウビン"), T("局", "キョク", "名詞", "接尾"), T("の", "ノ", "助詞", "連体化"),
                  T("田中", "タナカ", "名詞", "固有名詞"), T("さん", "サン", "名詞", "接尾", "人名")],
    "二十時間": [T("二", "ニ", **NUM), T("十", "ジュウ", **NUM), T("時", "ジ", **CTR), T("間", "カン", "名詞", "接尾")],
    "静かだ": [T("静か", "シズカ", "名詞", "形容動詞語幹"), T("だ", "ダ", "助動詞")],
    "食べさせられた": [T("食べ", "タベ", "動詞", "自立"), T("させ", "サセ", "動詞", "接尾"), T("られ", "ラレ", "動詞", "接尾"),
                T("た", "タ", "助動詞")],
    "行かなければ": [T("行か", "イカ", "動詞", "自立"), T("なけれ", "ナケレ", "助動詞"), T("ば", "バ", "助詞", "接続助詞")],
    "高かった。です": [T("高かっ", "タカカッ", "形容詞", "自立"), T("た", "タ", "助動詞"), T("。", "、", "記号", "句点"),
                 T("です", "デス", "助動詞")],
}


@pytest.mark.parametrize("text, cut", [
    ("行きました", ["行きました"]), ("食べている", ["食べて", "いる"]), ("学生です", ["学生", "です"]),
    ("学生でした", ["学生", "でした"]), ("お茶を", ["お茶", "を"]), ("郵便局の田中さん", ["郵便局", "の", "田中さん"]),
    ("二十時間", ["二十時間"]), ("静かだ", ["静かだ"]), ("食べさせられた", ["食べさせられた"]),
    ("行かなければ", ["行かなければ"]), ("高かった。です", ["高かった", "です"]),  # punctuation breaks a chain
])
def test_a_word_boundary_is_a_word_as_a_learner_reads_it(text, cut):
    pairs, _ = tool.word_boundaries(text, UNITS)
    assert [text[a:b] for a, b in pairs] == cut


def test_a_unit_never_cuts_a_reading_segment():
    pairs, _ = tool.word_boundaries("食べている", UNITS, [{"start": 0, "end": 3, "surface": "食べて"}])
    assert pairs == [[0, 3], [3, 5]]
    pairs, _ = tool.word_boundaries("食べている", UNITS, [{"start": 2, "end": 4, "surface": "てい"}])
    assert pairs == [[0, 5]]


KANA_TABLE = {
    "そふは学校へ行った": [T("そ", "ソ", "副詞"), T("ふ", "フ"), T("は", "ハ", "助詞", "係助詞"), T("学校", "ガッコウ"),
                   T("へ", "エ", "助詞", "格助詞"), T("行っ", "イッ", "動詞", "自立", lemma="行く"), T("た", "タ", "助動詞")],
    "祖父は学校へ行った": [T("祖父", "ソフ"), T("は", "ハ", "助詞", "係助詞"), T("学校", "ガッコウ"), T("へ", "エ", "助詞", "格助詞"),
                   T("行っ", "イッ", "動詞", "自立", lemma="行く"), T("た", "タ", "助動詞")],
    "は学校へ行った": [T("は", "ハ", "助詞", "係助詞"), T("学校", "ガッコウ"), T("へ", "エ", "助詞", "格助詞"),
                 T("行っ", "イッ", "動詞", "自立", lemma="行く"), T("た", "タ", "助動詞")],
    "いろいろな色": [T("いろいろ", "イロイロ", "名詞", "形容動詞語幹"), T("な", "ナ", "助動詞"), T("色", "イロ")],
}


def test_a_kana_course_word_is_one_unit_when_the_analyser_agrees_with_its_kanji_form():
    analyser = TokenAnalyser(KANA_TABLE)
    result = tool.annotate(analyser, knowledge(), sentences=[("そふは学校へ行った", "shared_example")],
                           course_words={"そふは学校へ行った": [("そふ", "祖父")]})
    record = result["sentences"]["そふは学校へ行った"]
    assert ["そふは学校へ行った"[a:b] for a, b in record["word_boundaries"]] == ["そふ", "は", "学校", "へ", "行った"]
    assert result["totals"]["word units: course words placed"] == 1
    assert ["祖父は学校へ行った"] in analyser.asked and ["は学校へ行った"] in analyser.asked  # the check, the stretch


def test_a_course_reading_inside_a_longer_kana_word_is_not_placed():
    result = tool.annotate(TokenAnalyser(KANA_TABLE), knowledge(), sentences=[("いろいろな色", "shared_example")],
                           course_words={"いろいろな色": [("いろ", "色")]})
    pairs = result["sentences"]["いろいろな色"]["word_boundaries"]
    assert ["いろいろな色"[a:b] for a, b in pairs] == ["いろいろな", "色"]
    assert result["totals"].get("word units: course words placed", 0) == 0


@pytest.mark.parametrize("line, own, reason", [
    ("今天不加班。", (), "no kana"),                            # a Chinese translation, every character in JIS
    ("学生", ("学生",), None),                                   # the lesson's word: the course has its reading
    ("人：にん。", (), "reading note"),
    ("人：にん、日：にち", (), "reading note"),
    ("そふ写作「祖父」。", (), "Chinese explanation word"),
    ("ません表示礼貌否定。", (), "Chinese explanation word"),
    ("画面に表示されます。", (), None),                          # 表示 as a Japanese verb stays
    ("歌う→歌って（うたって）：て形。", (), "conjugation note"),
    ("短くて → 短い", (), "note with → or ＋"),                  # a → without a given reading: still a note
    ("「からには」前接普通形：働く＋からには。", (), "note with → or ＋"),
    ("「てもらう」也可以接「たい」。", (), "Chinese explanation word"),
])
def test_the_explanation_line_filters(line, own, reason):
    assert tool.explanation_verdict(line, own) == reason


def test_each_filter_is_counted_and_a_kanji_only_line_needs_a_course_reading():
    questions = [("q:1", "今天不加班。\n人：にん。\n学生\n木の下で", {"学生"}, "今天不加班。\n人：にん。\n学生\n木の下で"),
                 ("q:2", "学生", set(), "学生")]
    stages = Counter()
    lines = tool.explanation_targets(questions, stages)
    assert [entry["line"] for entry in lines] == ["学生", "木の下で"]
    assert lines[0]["question_ids"] == ["q:1"]  # q:2 has no course reading for 学生: not shown as a candidate there
    assert stages["explanation lines left out: no kana"] == 1  # 今天不加班。 (学生 is kept for q:1)
    assert stages["explanation lines left out: reading note"] == 1
    assert stages["explanation lines of kanji only kept (the question's own text with a course reading)"] == 1


BOUND = {
    "見たり聞いたり": [T("見", "ミ", "動詞", "自立", lemma="見る"), T("たり", "タリ", "助詞", "並立助詞"),
                 T("聞い", "キイ", "動詞", "自立", lemma="聞く"), T("たり", "タリ", "助詞", "並立助詞")],
    "食べすぎた": [T("食べ", "タベ", "動詞", "自立"), T("すぎ", "スギ", "動詞", "非自立", lemma="すぎる"), T("た", "タ", "助動詞")],
    "読みやすかった": [T("読み", "ヨミ", "動詞", "自立"), T("やすかっ", "ヤスカッ", "形容詞", "非自立", lemma="やすい"),
                T("た", "タ", "助動詞")],
    "残念ながら": [T("残念", "ザンネン", "名詞", "形容動詞語幹"), T("ながら", "ナガラ", "助詞", "接続助詞")],
    "人ながら": [T("人", "ヒト"), T("ながら", "ナガラ", "助詞", "接続助詞")],
}


@pytest.mark.parametrize("text, cut", [
    ("見たり聞いたり", ["見たり", "聞いたり"]), ("食べすぎた", ["食べすぎた"]), ("読みやすかった", ["読みやすかった"]),
    ("人ながら", ["人", "ながら"]),  # after a noun ながら stays apart
])
def test_a_verb_stem_and_its_bound_ending_are_one_unit(text, cut):
    pairs, _ = tool.word_boundaries(text, BOUND)
    assert [text[a:b] for a, b in pairs] == cut


def test_a_number_with_a_kana_counter_is_one_unit():
    text = "犬が六にんいます。一けんあります。二まん人。三つと三つで"
    assert [text[a:b] for a, b in tool.kana_counter_units(text)] == ["六にん", "一けん", "二まん人", "三つ", "三つ"]
    assert ["三つ年上"[a:b] for a, b in tool.kana_counter_units("三つ年上")] == ["三つ"]  # 年 is not its counter
    assert tool.kana_counter_units("三本と十人") == []  # written in kanji: the analyser's numeral rule decides
    table = {"六にんいます": [T("六", "ロク", **NUM), T("にんい", "ニンイ", "名詞", "形容動詞語幹"), T("ます", "マス", "助動詞")],
             "います": [T("い", "イ", "動詞", "自立"), T("ます", "マス", "助動詞")]}
    result = tool.annotate(TokenAnalyser({**table, "六": [T("六", "ロク", **NUM)]}), knowledge(),
                           sentences=[("六にんいます", "shared_example")])
    record = result["sentences"]["六にんいます"]
    assert ["六にんいます"[a:b] for a, b in record["word_boundaries"]] == ["六にん", "います"]
    assert result["totals"]["word units: kana counter"] == 1
