"""The answer overlay in packs and its tools (design 08): the table tool, the pack check, library merging.

Synthetic data only: made-up lessons, templates and packs in temporary folders.
"""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

from kanamemory import content_library_rules as rules
from kanamemory import content_pack
from kanamemory.answer_policy import overlay
from kanamemory.content_library_service import ContentLibraryService
from kanamemory.domain import StudyStore
from kanamemory.learning_questions import template_fingerprint
from support.content_library_support import (CORE_ITEMS, CORE_NAMESPACES, build_core_resources, extension_namespaces,
                                             pack_bytes)
from support.content_library_support import no_ambient_bundled_stamp  # noqa: F401  (autouse: no ambient stamp)

ROOT = Path(__file__).resolve().parents[1]
TE, SCHOOL = "grammar:demo:te", "word:demo:school"
TYPED = {"prompt": "食べる → ＿＿", "task": "改成て形。", "answer": "食べて", "accepted_answers": ["食べて", "たべて"],
         "input_mode": "text", "strict": False, "known_wrong": ["たべって"], "feedback": "て形。"}
CHOICE = {"prompt": "食べ（　）ください。", "answer": "て", "accepted_answers": ["て"], "choices": ["て", "た"],
          "input_mode": "choice", "strict": True}
RECALL = {"prompt": "学校", "task": "本课词：学校", "answer": "学校", "accepted_answers": ["学校", "がっこう"],
          "input_mode": "text", "strict": False}
PLAIN_RECALL = dict(RECALL, task="写出本课学的单词。")
ITEMS = {TE: {"id": TE, "family": "grammar", "domain": "grammar", "revision": "1", "title": "て形"},
         SCHOOL: {"id": SCHOOL, "family": "word", "domain": "vocabulary", "revision": "1", "title": "学校",
                  "reading": "がっこう"}}
HEADER = "sha\tcontent_id\tkind\tstrict\taccepted_add\tknown_wrong\tfold\n"


def _tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def table_tool(monkeypatch):
    tool = _tool("build_answer_overlay")
    monkeypatch.setattr("kanamemory.learning_questions._CONTENT", {"GRAMMAR_READINGS": {}})

    def question(item, kind, variant):
        raw = next(held[3] for held in templates().values() for held in held
                   if held[0]["id"] == item["id"] and held[1] == kind and held[2] == variant)
        return {"id": "q", "target_id": item["id"], "content_id": item["id"], "family": item["family"],
                "domain": item["domain"], "kind": kind, "variant": variant, "revision": "1", **copy.deepcopy(raw)}

    monkeypatch.setattr(tool, "_question", question)
    gloss = {"value": []}
    monkeypatch.setattr("kanamemory.question_support.glosses_showing",
                        lambda q, forms: [g for g in gloss["value"] if any(f in g[1] for f in forms)])
    tool.gloss = gloss
    return tool


def templates():
    rows = [(ITEMS[TE], "grammar_transform", 0, TYPED), (ITEMS[TE], "grammar_cloze", 0, CHOICE),
            (ITEMS[SCHOOL], "vocab_recall", 0, RECALL), (ITEMS[SCHOOL], "vocab_recall", 1, PLAIN_RECALL)]
    return {template_fingerprint(raw): [(item, kind, variant, raw)] for item, kind, variant, raw in rows}


def run(tool, tmp_path, lines, *, existing=None, found=None):
    table = tmp_path / "t.tsv"
    table.write_text(HEADER + "".join("\t".join(line) + "\n" for line in lines), encoding="utf-8")
    teaching = tmp_path / "teaching"
    teaching.mkdir(exist_ok=True)
    if existing is not None:
        (teaching / "answer_overlay.json").write_text(json.dumps(content_pack.namespace_document(
            "answer_overlay", existing), ensure_ascii=False), encoding="utf-8")
    return tool.build([table], teaching, templates=found or templates()), teaching


T_SHA, C_SHA, R_SHA, P_SHA = (template_fingerprint(raw) for raw in (TYPED, CHOICE, RECALL, PLAIN_RECALL))


@pytest.mark.parametrize("line, problem", [
    ([T_SHA[:11], TE, "grammar_transform", "1", "", "", ""], "12 to 64 lowercase hex"),
    (["0" * 64, TE, "grammar_transform", "1", "", "", ""], "unknown template sha"),
    ([T_SHA, SCHOOL, "grammar_transform", "1", "", "", ""], "content_id"),
    ([T_SHA, TE, "grammar_cloze", "1", "", "", ""], "kind grammar_cloze"),
    ([C_SHA, TE, "grammar_cloze", "1", "", "", ""], "need a typed template"),
    ([C_SHA, TE, "grammar_cloze", "", "で", "", ""], "need a typed template"),
    ([C_SHA, TE, "grammar_cloze", "", "", "た=>过去。", ""], "need a typed template"),
    ([T_SHA, TE, "grammar_transform", "2", "", "", ""], "strict must be"),
    ([T_SHA, TE, "grammar_transform", "", "", "", "0"], "fold must be"),
    ([T_SHA, TE, "grammar_transform", "", "食べてね｜｜", "", ""], "is empty"),
    ([T_SHA, TE, "grammar_transform", "", "たべて", "", ""], "already accepted"),
    ([T_SHA, TE, "grammar_transform", "", "食べて ", "", ""], "already accepted"),   # normalised as judged
    ([T_SHA, TE, "grammar_transform", "", "たべって", "", ""], "is a known-wrong answer"),
    ([T_SHA, TE, "grammar_transform", "", "たべた", "たべた=>过去形。", ""], "is a known-wrong answer"),
    ([T_SHA, TE, "grammar_transform", "", "", "たべて=>不对。", ""], "is accepted"),
    ([T_SHA, TE, "grammar_transform", "", "", "タベテ=>不对。", ""], "is accepted"),      # T7: the judge accepts it
    ([T_SHA, TE, "grammar_transform", "", "", "たべた", ""], "is not 'wrong answer=>feedback'"),
    ([P_SHA, SCHOOL, "vocab_recall", "", "", "", "1"], "without a folded hint"),
    ([T_SHA, TE, "grammar_transform", "", "たべる", "", ""], "a gloss of the question shows"),
])
def test_every_refusal_names_its_row(table_tool, tmp_path, line, problem):
    table_tool.gloss["value"] = [("食べる", "たべる")]
    result, _ = run(table_tool, tmp_path, [line])
    assert result["refusals"] and all(r.startswith(f"{tmp_path / 't.tsv'}:2: ") for r in result["refusals"])
    assert any(problem in r for r in result["refusals"]), result["refusals"]


def test_an_ambiguous_sha_prefix_and_a_twice_listed_template_are_refused(table_tool, tmp_path):
    found = templates()
    raw = dict(TYPED, prompt="見る → ＿＿")
    found[T_SHA[:12] + "f" * 52] = [(ITEMS[TE], "grammar_transform", 1, raw)]
    result, _ = run(table_tool, tmp_path, [[T_SHA[:12], TE, "grammar_transform", "1", "", "", ""]], found=found)
    assert any("ambiguous template sha" in r for r in result["refusals"])
    result, _ = run(table_tool, tmp_path, [[T_SHA, TE, "grammar_transform", "1", "", "", ""],
                                           [T_SHA[:16], TE, "grammar_transform", "0", "", "", ""]])
    assert result["refusals"] == [f"{tmp_path / 't.tsv'}:3: template {T_SHA[:12]} is listed twice"]


def test_write_replaces_the_listed_rows_keeps_the_others_and_removes_rows_of_missing_templates(table_tool,
                                                                                                tmp_path):
    existing = {"schema_version": 1, "items": {
        TE: {"grammar_transform": {T_SHA: {"strict": False}}},
        SCHOOL: {"vocab_recall": {R_SHA: {"fold": True}, "9" * 64: {"strict": True}}}}}
    result, teaching = run(table_tool, tmp_path, [
        [T_SHA[:12], TE, "grammar_transform", "1", "食べてください｜食べてね", "たべた=>这是过去形。", ""]],
        existing=existing)
    assert result["refusals"] == [] and result["changed"] == [T_SHA]
    assert result["removed_missing"] == [f"{SCHOOL} vocab_recall {'9' * 64}"]
    assert result["namespace"]["items"] == {
        TE: {"grammar_transform": {T_SHA: {"strict": True, "accepted": ["食べてください", "食べてね"],
                                           "known_wrong": [{"answer": "たべた", "feedback": "这是过去形。"}]}}},
        SCHOOL: {"vocab_recall": {R_SHA: {"fold": True}}}}
    path = table_tool.write(result["namespace"], teaching)
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["format"] == content_pack.NAMESPACE_FORMAT and document["name"] == "answer_overlay"
    assert overlay.validate_namespace(content_pack.decode_value(document["payload"])) == result["namespace"]
    assert b"\r\n" not in path.read_bytes()
    # A row with every field empty removes the template's row.
    result, _ = run(table_tool, tmp_path, [[R_SHA, SCHOOL, "vocab_recall", "", "", "", ""]])
    assert SCHOOL not in result["namespace"]["items"] and TE in result["namespace"]["items"]


def test_check_writes_nothing_and_fails_on_a_refusal(table_tool, tmp_path, monkeypatch):
    monkeypatch.setattr(table_tool, "installed_templates", templates)
    table = tmp_path / "t.tsv"
    teaching = tmp_path / "teaching"
    teaching.mkdir()
    table.write_text(HEADER + f"{R_SHA}\t{SCHOOL}\tvocab_recall\t\t\t\t1\n", encoding="utf-8")
    assert table_tool.main(["--table", str(table), "--check", "--teaching", str(teaching)]) == 0
    assert list(teaching.iterdir()) == []
    assert table_tool.main(["--table", str(table), "--write", "--teaching", str(teaching)]) == 0
    assert (teaching / "answer_overlay.json").is_file()
    table.write_text(HEADER + f"{P_SHA}\t{SCHOOL}\tvocab_recall\t\t\t\t1\n", encoding="utf-8")
    before = (teaching / "answer_overlay.json").read_bytes()
    assert table_tool.main(["--table", str(table), "--write", "--teaching", str(teaching)]) == 1
    assert (teaching / "answer_overlay.json").read_bytes() == before
    table.write_text("sha\tkind\n", encoding="utf-8")
    assert table_tool.main(["--table", str(table), "--check", "--teaching", str(teaching)]) == 2


# ---------------------------------------------------------------- the pack check (T10)

def test_typed_templates_without_a_kana_answer_are_listed_by_the_apps_rule(monkeypatch):
    check = _tool("typed_answer_check")
    monkeypatch.setattr("kanamemory.learning_questions._CONTENT", {"GRAMMAR_READINGS": {}})
    kanji_only = dict(TYPED, answer="確認して", accepted_answers=["確認して"], prompt="確認する → ＿＿",
                      known_wrong=[])
    item = {**ITEMS[TE], "abilities": ["application"],
            "question_templates": {"grammar_transform": [kanji_only, TYPED], "grammar_cloze": [CHOICE]}}
    assert check.without_kana(items=[item]) == [{"content_id": TE, "kind": "grammar_transform", "variant": 0,
                                                 "template_sha256": template_fingerprint(kanji_only),
                                                 "answer": "確認して"}]


def _package(tmp_path, sources=None, **options):
    tool = _tool("package_library_pack")
    resources = build_core_resources(tmp_path / "resources", sources or copy.deepcopy(CORE_NAMESPACES))
    result = tool.package(resources, tmp_path / "pack.kmlib", **options)
    return tool, result, rules.validate_library_pack(tmp_path / "pack.kmlib").manifest


ROWS = [{"content_id": TE, "kind": "grammar_transform", "variant": 0, "template_sha256": "a" * 64,
         "answer": "確認して"}]


def test_the_pack_tool_warns_with_the_list_by_default_and_refuses_on_request(tmp_path):
    lines = []
    _, result, _ = _package(tmp_path / "warn", typed_check=lambda db, ids: ROWS, report=lines.append)
    assert result["typed_without_kana"] == 1 and "1 typed templates without a kana answer" in lines[0]
    assert TE in lines[0] and "a" * 64 in lines[0]
    with pytest.raises(ValueError, match="typed templates without a kana answer"):
        _package(tmp_path / "refuse", typed_check=lambda db, ids: ROWS, refuse_typed_without_kana=True)
    assert not (tmp_path / "refuse" / "pack.kmlib").exists()
    _, clean, _ = _package(tmp_path / "clean")  # the real check, in its own process, on the synthetic pack
    assert clean["typed_without_kana"] == 0


def test_an_extension_check_covers_only_the_lessons_the_pack_carries(tmp_path):
    seen = {}

    def check(db, ids):
        seen["ids"] = ids
        return []
    _package(tmp_path, typed_check=check, library_id="author.x", extension=True, namespaces=["lesson"])
    assert seen["ids"] == set(CORE_ITEMS)


def test_a_pack_with_overlay_rows_needs_the_app_that_reads_them(tmp_path):
    rows = {"schema_version": 1, "items": {"word:demo:mizu": {"vocab_recall": {"a" * 64: {"strict": True}}}}}
    namespaces = {**copy.deepcopy(CORE_NAMESPACES), "answer_overlay": rows}
    tool, result, manifest = _package(tmp_path / "rows", namespaces, typed_check=lambda db, ids: [])
    assert manifest["requires"]["app_min_version"] == tool.ANSWER_OVERLAY_MIN_APP == result["app_min_version"]
    assert rules.app_too_old(manifest, "0.36.16") == tool.ANSWER_OVERLAY_MIN_APP
    _, _, newer = _package(tmp_path / "newer", namespaces, typed_check=lambda db, ids: [], app_min_version="0.40.0")
    assert newer["requires"]["app_min_version"] == "0.40.0"
    empty = {**copy.deepcopy(CORE_NAMESPACES), "answer_overlay": {"schema_version": 1, "items": {}}}
    _, _, plain = _package(tmp_path / "empty", empty, typed_check=lambda db, ids: [])
    assert "app_min_version" not in plain["requires"]


def test_a_malformed_overlay_is_refused_by_the_pack_build(tmp_path):
    bad = {**copy.deepcopy(CORE_NAMESPACES),
           "answer_overlay": {"schema_version": 1, "items": {TE: {"grammar_transform": {"a" * 64: {"fold": 1}}}}}}
    with pytest.raises(content_pack.ContentPackError, match="answer overlay"):
        build_core_resources(tmp_path / "bad", bad)


# ---------------------------------------------------------------- merging across libraries (T1)

def test_an_extension_librarys_rows_for_a_core_lesson_are_dropped_and_its_own_rows_kept(tmp_path, monkeypatch):
    core_rows = {"schema_version": 1, "items": {"word:demo:mizu": {"vocab_recall": {"a" * 64: {"strict": True}}}}}
    resources = build_core_resources(tmp_path / "resources", {**copy.deepcopy(CORE_NAMESPACES),
                                                              "answer_overlay": core_rows})
    monkeypatch.setenv("KANAMEMORY_TEACHING_PACK", str(resources / "teaching_content.sqlite3"))
    monkeypatch.setenv("KANAMEMORY_LIBRARY_ROOT", str(tmp_path / "libraries"))
    content_pack.clear_cache()
    own = {"grammar_transform": {"b" * 64: {"accepted": ["たべて"]}}}
    other = {"vocab_recall": {"a" * 64: {"strict": False}}}   # a row for a core lesson
    extension = {"lesson": extension_namespaces()["lesson"],
                 "answer_overlay": {"schema_version": 1, "items": {"grammar:ext:masu": own, "word:demo:mizu": other}}}
    store = StudyStore(tmp_path / "learner", time_zone="UTC")
    try:
        service = ContentLibraryService(store, catalogue_ids=lambda: set(CORE_ITEMS), catalogue_fingerprints=lambda: {},
                                        learned_ids=set, word_exists=lambda word_id: False)
        inspection = service.inspect_bytes(pack_bytes("author.fix", extension, items=["grammar:ext:masu"]), "lib.kmlib")
        assert inspection.precheck.blocking_reasons() == ()
        service.install(inspection)
        content_pack.clear_cache()  # "restart"
        merged = content_pack.load_namespace(overlay.NAMESPACE, {})
        assert dict(merged["items"]["word:demo:mizu"]) == core_rows["items"]["word:demo:mizu"]
        assert dict(merged["items"]["grammar:ext:masu"]) == own
        report = content_pack.library_merge_report().get("answer_overlay", {})
        assert report["conflicts"] == [("items", "word:demo:mizu", rules.CORE_LIBRARY_ID, "author.fix")]
    finally:
        store.close()
        content_pack.clear_cache()
