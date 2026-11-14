"""Loanword origins (design 11 section 19): format rules, pack plumbing and where pages show the line.

Synthetic lessons and entries only; the line appears after teaching or an answer, never on a page
that asks a question (a recall question asks for the katakana word itself).
"""
from contextlib import closing
from datetime import timedelta
import importlib.util
import json
from pathlib import Path
import re
import sqlite3

import pytest

from kanamemory import content_library_merge as merge
from kanamemory import loanword_origins as lo
from kanamemory.content_pack import ContentPackError, clear_cache, namespace_document, validate_pack
from kanamemory.detail_enrichment import source_cards
from kanamemory.learning_ui import LEARNING_PREFS
from support.app_fixtures import question_app as question_app, setup_app as setup_app
from support.fake_ui import text_of
from support.learning_fixtures import NOW, click, learning_app as learning_app

_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("build_teaching_content", _ROOT / "tools/build_teaching_content.py")
_BUILDER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_BUILDER)

CID = "word:synthetic:terebi"
EVIDENCE = {"source_ids": ["synthetic:1"]}


def entry(kind="direct", *, status="reviewed", title="テレビ", reading="テレビ", **fields):
    value = {"title": title, "reading": reading, "kind": kind, "evidence": EVIDENCE, "review": {"status": status}}
    value.update(fields)
    return value


CLIPPING = entry("clipping", sources=[{"language": "en", "word": "television"}], full_form="テレビジョン")
NAMESPACE = {"schema_version": 1, "items": {CID: CLIPPING}}


def flat(text):
    return re.sub(r"\s", "", text)


# ---------------------------------------------------------------- rules
@pytest.mark.parametrize("title, needed", [
    ("テレビ", True), ("コーヒー", True), ("オンライン授業", True), ("ボランティア活動", True), ("ア本", True),
    ("〜ページ", True), ("手紙", False), ("あいう", False), ("ア本本", False), ("", False), (None, False),
])
def test_needs_origin_counts_katakana_and_the_long_vowel_mark(title, needed):
    assert lo.needs_origin(title) is needed


@pytest.mark.parametrize("value, line", [
    (entry(sources=[{"language": "pt", "word": "pão"}], title="パン", reading="パン"), "来源：葡萄牙语 pão"),
    (entry("multiple", sources=[{"language": "en", "word": "coffee"}, {"language": "nl", "word": "koffie"}]),
     "来源：英语 coffee／荷兰语 koffie"),
    (CLIPPING, "来源：英语 television 的缩略（テレビジョン）"),
    (entry("wasei", sources=[{"language": "en", "word": "salary"}, {"language": "en", "word": "man"}]),
     "和制英语：salary＋man（英语里不这样说）"),
    (entry("wasei", sources=[{"language": "en", "word": "convenience"}, {"language": "en", "word": "store"}],
           full_form="コンビニエンスストア"),
     "和制英语：convenience＋store，コンビニエンスストア 的缩略（英语里不这样说）"),
    (entry("wasei", sources=[{"language": "en", "word": "a"}, {"language": "de", "word": "b"}]),
     "和制外来语：英语 a＋德语 b（原语言里不这样说）"),
    (entry("hybrid", title="オンライン授業", reading="おんラインじゅぎょう",
           sources=[{"language": "en", "word": "online", "part": "オンライン"}]), "混种词：オンライン←英语 online"),
])
def test_each_kind_has_its_one_line(value, line):
    assert lo.validate_entry(CID, value) is value
    assert lo.origin_line(value) == line
    candidate = {**value, "review": {"status": "candidate", "by": "prefill"}}
    assert lo.origin_line(candidate) == line + "（待核实）"


def test_exempt_shows_nothing_and_a_note_follows_the_line():
    exempt = entry("exempt", reason="mimetic", title="コツコツ", reading="コツコツ")
    assert lo.validate_entry(CID, exempt) is exempt
    assert lo.origin_line(exempt) is None and lo.origin_note(exempt) is None
    assert lo.EXEMPT_REASONS == ("mimetic", "sino_japanese", "native", "coined")
    for reason in lo.EXEMPT_REASONS:  # a name coined for a work is exempt too: no word is its source
        other = entry("exempt", reason=reason, title="コツコツ", reading="コツコツ")
        assert lo.validate_entry(CID, other) is other and lo.origin_line(other) is None
    noted = {**CLIPPING, "note_zh": "日语里通常用短形式。"}
    item = {"id": CID, "family": "word", "title": "テレビ", "reading": "テレビ"}
    assert lo.origin_texts(item, {CID: noted}) == (("来源：英语 television 的缩略（テレビジョン）", "日语里通常用短形式。"),)
    assert lo.origin_texts(item, {CID: {**exempt, "title": "テレビ", "reading": "テレビ"}}) == ()


@pytest.mark.parametrize("change, rule", [
    ({"kind": "loan"}, "kind"),
    ({"sources": []}, "1-8 sources"),
    ({"sources": [{"language": "xx", "word": "a"}]}, "language"),
    ({"sources": [{"language": "en", "word": "[[a]]"}]}, "source word"),
    ({"sources": [{"language": "en", "word": "テレビ"}]}, "source word"),
    ({"sources": [{"language": "en", "word": "a#Verb"}]}, "source word"),
    ({"sources": [{"language": "en", "word": "x" * 81}]}, "source word"),
    ({"sources": [{"language": "en", "word": "a", "part": "テ"}]}, "part"),
    ({"sources": [{"language": "en", "word": "a"}, {"language": "en", "word": "b"}]}, "exactly one"),
    ({"kind": "multiple"}, "two languages"),
    ({"kind": "multiple", "sources": [{"language": "en", "word": "a"}, {"language": "en", "word": "b"}]},
     "two languages"),
    ({"kind": "clipping"}, "full_form"),
    ({"full_form": "テレビジョン"}, "full_form"),
    ({"kind": "clipping", "full_form": "television"}, "kana"),
    ({"kind": "hybrid"}, "part"),
    ({"kind": "hybrid", "sources": [{"language": "en", "word": "a", "part": "ラジオ"}]}, "substring"),
    ({"kind": "exempt"}, "no sources"),
    ({"kind": "exempt", "sources": [], "reason": "loan"}, "reason"),
    ({"reason": "mimetic"}, "reason"),
    ({"note_zh": "长" * 121}, "note_zh"),
    ({"evidence": {"source_ids": []}}, "evidence"),
    ({"evidence": {"source_ids": ["a"] * 2}}, "evidence"),
    ({"evidence": {"source_ids": [str(n) for n in range(9)]}}, "evidence"),
    ({"review": {"status": "approved"}}, "review"),
    ({"review": {"status": "reviewed", "by": ""}}, "review"),
    ({"title": ""}, "binding"),
    ({"extra": 1}, "unknown fields"),
])
def test_malformed_entries_name_the_lesson_and_the_rule(change, rule):
    value = {**entry(sources=[{"language": "en", "word": "television"}]), **change}
    with pytest.raises(lo.LoanwordOriginError, match=re.escape(CID) + ".*" + rule):
        lo.validate_entry(CID, value)
    assert lo.origin_line(value) is None


@pytest.mark.parametrize("value", [None, [], {"items": {}}, {"schema_version": 2, "items": {}},
                                   {"schema_version": 1, "items": []}, {"schema_version": 1, "items": {CID: {}}}])
def test_malformed_namespaces_are_refused(value):
    with pytest.raises(lo.LoanwordOriginError):
        lo.validate_namespace(value)


def test_origin_for_needs_the_bound_word_lesson_and_never_raises():
    item = {"id": CID, "family": "word", "title": "テレビ", "reading": "テレビ"}
    assert lo.origin_for(item, {CID: CLIPPING}) is CLIPPING
    assert lo.origin_for({**item, "title": "テレビー"}, {CID: CLIPPING}) is None      # stale title
    assert lo.origin_for({**item, "reading": "てれび"}, {CID: CLIPPING}) is None     # stale reading
    assert lo.origin_for({**item, "family": "grammar"}, {CID: CLIPPING}) is None
    assert lo.origin_for(item, {}) is None
    assert lo.origin_for(item, {CID: {**CLIPPING, "kind": "loan"}}) is None
    assert lo.origin_for(item, {CID: "テレビ"}) is None and lo.origin_for(None, {CID: CLIPPING}) is None
    assert lo.origin_for(item, ["not", "a", "mapping"]) is None


def test_coverage_and_candidate_count():
    lessons = [{"id": CID, "family": "word", "title": "テレビ", "reading": "テレビ"},
               {"id": "word:b", "family": "word", "title": "ラジオ", "reading": "ラジオ"},
               {"id": "word:c", "family": "word", "title": "カメラ", "reading": "カメラ"},
               {"id": "word:d", "family": "word", "title": "ノート", "reading": "ノート"},
               {"id": "word:e", "family": "word", "title": "手紙", "reading": "てがみ"},
               {"id": "grammar:f", "family": "grammar", "title": "ラ", "reading": ""}]
    namespace = {"schema_version": 1, "items": {
        CID: {**CLIPPING, "review": {"status": "candidate"}},
        "word:c": {**CLIPPING, "title": "カメラ", "reading": "かめら"},
        "word:d": {**CLIPPING, "title": "ノート", "reading": "ノート", "kind": "direct"},
        "word:gone": CLIPPING}}
    assert lo.coverage(lessons, namespace) == (
        ("word:b", "missing"), ("word:c", "stale"), ("word:d", "full_form is only for clipping and wasei entries"),
        ("word:gone", "no such lesson"))
    assert lo.candidate_count(namespace) == 1 and lo.candidate_count(None) == 0
    assert lo.coverage(lessons, None) == (("word:b", "missing"), ("word:c", "missing"), ("word:d", "missing"),
                                          (CID, "missing"))


def test_language_tags_of_jmdict_and_the_lexicon_are_converted():
    assert [lo.language_code(tag) for tag in ("eng", "en", "en-US", "por", "ger", "dut", "fre", "gre", "ain")] == [
        "en", "en", "en", "pt", "de", "nl", "fr", "el", "ain"]
    assert lo.language_code("xyz") is None and lo.language_code(None) is None
    assert lo.language_name("por") == "葡萄牙语"


def test_libraries_merge_entries_by_id_and_the_core_wins():
    assert merge.builtin_policy(lo.NAMESPACE, "items") == "items_by_id"
    other = {**CLIPPING, "kind": "direct", "full_form": None}
    core = {"schema_version": 1, "items": {CID: CLIPPING}}
    library = {"schema_version": 1, "items": {CID: other, "word:ext": CLIPPING}}
    merged, report = merge.merge_namespace([(merge.CORE_LIBRARY_ID, core), ("ext.lib", library)],
                                           namespace=lo.NAMESPACE)
    assert merged["items"][CID] is CLIPPING and merged["items"]["word:ext"] is CLIPPING
    assert report.conflicts and report.conflicts[0][:2] == ("items", CID)


def test_dictionary_cards_put_a_reviewed_course_entry_first_and_label_candidates():
    lesson = {"id": CID, "family": "word", "title": "テレビ", "reading": "テレビ"}
    record = {"form": "テレビ", "reading": "テレビ", "english_review_candidates": [
        {"english_source_expression": "television", "etymology_evidence": {"source_language": "eng"}}]}
    lo_items = {CID: CLIPPING, "word:other": {**CLIPPING, "review": {"status": "candidate"}}}
    view = {CID: lesson, "word:other": {**lesson, "id": "word:other"}}.get
    original = lo.installed_items
    try:
        lo.installed_items = lambda: lo_items
        cards = source_cards(record, lesson_view=view)
    finally:
        lo.installed_items = original
    assert cards == (("课程来源（已审核）", "来源：英语 television 的缩略（テレビジョン）", None),
                     ("英语来源形式 · 候选（未审）", "television", None))
    unknown = {"origin": {"source_expressions": [{"donor_language": "xx", "source_expression": "pan"}]}}
    assert source_cards(unknown) == (("词典来源表达 · 候选（未审）", "pan", None),)
    mixed = {"english_review_candidates": [
        {"english_source_expression": "a", "etymology_evidence": {"source_language": "eng"}},
        {"english_source_expression": "b", "etymology_evidence": {}}]}
    assert source_cards(mixed)[0][0] == "来源形式 · 候选（未审）"
    assert source_cards({}) == ()


# ---------------------------------------------------------------- pack plumbing
def _write(source, name, value):
    source.mkdir(exist_ok=True)
    (source / (name + ".json")).write_text(json.dumps(namespace_document(name, value)), encoding="utf-8")


def test_the_build_validates_the_namespace_and_counts_candidates(tmp_path, monkeypatch):
    source, pack = tmp_path / "source", tmp_path / "teaching.sqlite3"
    _write(source, "lesson", {"values": [1]})
    assert "loanword_origin_candidates" not in _BUILDER.build(source, pack, profile="demo")
    _write(source, lo.NAMESPACE, {"schema_version": 1, "items": {
        CID: CLIPPING, "word:b": {**CLIPPING, "review": {"status": "candidate"}}}})
    assert _BUILDER.build(source, pack, profile="demo")["loanword_origin_candidates"] == 1
    monkeypatch.setenv("KANAMEMORY_TEACHING_PACK", str(pack))
    monkeypatch.setenv("KANAMEMORY_LIBRARY_ROOT", str(tmp_path / "no-libraries"))
    clear_cache()
    lo.clear_cache()
    try:
        item = {"id": CID, "family": "word", "title": "テレビ", "reading": "テレビ"}
        assert lo.origin_texts(item) == (("来源：英语 television 的缩略（テレビジョン）", None),)
    finally:
        lo.clear_cache()
    for bad in ({"schema_version": 1, "items": {CID: {**CLIPPING, "kind": "loan"}}}, {"schema_version": 2, "items": {}}):
        _write(source, lo.NAMESPACE, bad)
        for layout in ("sharded", "legacy"):
            with pytest.raises(ContentPackError, match="loanword origins"):
                _BUILDER.build(source, pack, profile="demo", layout=layout)


def test_a_pack_with_a_malformed_namespace_still_opens_and_shows_nothing(tmp_path, monkeypatch):
    pack = tmp_path / "legacy.sqlite3"
    bad = {"schema_version": 1, "items": {CID: {**CLIPPING, "sources": [{"language": "en", "word": "[[tv]]"}]}}}
    documents = sorted((name, json.dumps(namespace_document(name, value)["payload"], ensure_ascii=False,
                                         separators=(",", ":")))
                       for name, value in (("lesson", {"values": [1]}), (lo.NAMESPACE, bad)))
    with closing(sqlite3.connect(pack)) as connection:
        _BUILDER._write_legacy(connection, "demo", {name for name, _ in documents}, documents)
        connection.commit()
    assert validate_pack(pack)["namespace_count"] == 2            # the App's startup check
    with pytest.raises(ContentPackError, match="loanword origins"):
        validate_pack(pack, deep=True)                            # a build or audit
    monkeypatch.setenv("KANAMEMORY_TEACHING_PACK", str(pack))
    monkeypatch.setenv("KANAMEMORY_LIBRARY_ROOT", str(tmp_path / "no-libraries"))
    clear_cache()
    lo.clear_cache()
    try:
        assert lo.origin_texts({"id": CID, "family": "word", "title": "テレビ", "reading": "テレビ"}) == ()
    finally:
        lo.clear_cache()


# ---------------------------------------------------------------- pages
LINE = "来源：英语 television 的缩略（テレビジョン）"


def lesson(title="テレビ", reading="テレビ"):
    choice = [{"prompt": title, "answer": "电视", "choices": ["电视", "别的词"], "feedback": "说明",
               "input_mode": "choice", "safe": True}]
    return {"id": CID, "content_id": CID, "family": "word", "title": title, "reading": reading,
            "meaning": "电视", "revision": "1", "abilities": ["understanding"], "source_ids": ["test"],
            "instruction": {"summary": "电视", "rule": "名词。", "steps": [], "example": title + "です。",
                            "translation": "是电视。", "prerequisite": "", "contrast": ""},
            "question_templates": {"vocab_meaning": choice}}


@pytest.fixture
def origins(monkeypatch):
    table = {CID: CLIPPING}
    monkeypatch.setattr(lo, "installed_items", lambda: table)
    return table


def shown(app):
    return flat(LINE) in flat(text_of(app.body))


def test_batch_card_shows_the_line_the_quiz_does_not_and_wrong_feedback_repeats_the_card(learning_app, origins):
    app = learning_app
    app.store.set_setting(LEARNING_PREFS, {"enabled": ["vocab_meaning"]})
    app.learning_store.enroll([lesson()])
    app.continue_learning()
    assert app.page == "learning_card" and shown(app)
    click(app, "下一项")
    assert app.page == "learning_batch_quiz" and not shown(app)
    click(app, "别的词")
    assert shown(app)  # after the answer: the card again


def test_first_learning_page_shows_the_line_and_the_guided_question_does_not(learning_app, origins):
    app = learning_app
    app.store.set_setting(LEARNING_PREFS, {"enabled": ["vocab_meaning"]})
    app.learning_store.enroll([lesson()])
    saved = app.learning_store.get_item(CID)
    app.learning_store.set_initial_familiarity(CID, {"understanding": "new"},
                                               expected_introduction_id=saved["introduction_id"])
    app.continue_learning()
    assert app.page == "learning_intro" and shown(app)
    click(app, "跟着做一题")
    assert app.page == "learning_guide" and not shown(app)


EARLIER = NOW - timedelta(minutes=3)


def test_formal_question_and_its_hints_never_show_the_line_the_result_detail_does(learning_app, origins):
    app = learning_app
    app.store.set_setting(LEARNING_PREFS, {"enabled": ["vocab_meaning"]})
    app.learning_store.enroll([lesson()], now=EARLIER)
    app.learning_store.advance_instruction(CID, "intro_done", now=EARLIER)
    app.learning_store.advance_instruction(CID, "guide_correct", now=EARLIER)
    app.continue_learning()
    assert app.page == "learning_question" and not shown(app)
    app.learning_hint(card=True)
    assert app.page == "learning_prerequisite" and not shown(app)
    app.learning_hint()
    assert app.page == "learning_prerequisite" and not shown(app)
    app.continue_learning()
    assert app.page == "learning_question" and not shown(app)
    click(app, app._learning_question["answer"])
    assert app.page == "learning_feedback"
    app.learning_feedback_detail()
    assert app.page == "learning_feedback_detail" and shown(app)


@pytest.mark.parametrize("table, expected", [
    ({CID: CLIPPING}, LINE),
    ({CID: {**CLIPPING, "review": {"status": "candidate"}}}, LINE + "（待核实）"),
    ({CID: entry("exempt", reason="native")}, None),
    ({CID: {**CLIPPING, "reading": "てれび"}}, None),       # stale
    ({CID: {**CLIPPING, "kind": "loan"}}, None),           # malformed
    ({}, None),                                            # missing
])
def test_library_word_page(learning_app, monkeypatch, table, expected):
    monkeypatch.setattr(lo, "installed_items", lambda: table)
    learning_app.show_learning_detail(lesson())
    body = flat(text_of(learning_app.body))
    assert learning_app.page == "learning_detail"
    if expected is None:
        assert "来源：" not in body and "television" not in body
    else:
        assert flat(expected) in body
