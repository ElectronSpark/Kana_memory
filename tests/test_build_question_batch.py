"""The question-batch builder: template shapes, defaults, side fields, input errors, assemble and retarget.

Synthetic entries only; the real batches are compared byte for byte outside the test suite.
"""
import importlib.util
import json

import pytest

from support.paths import ROOT

spec = importlib.util.spec_from_file_location("kana_test_build_question_batch", ROOT / "tools/build_question_batch.py")
bqb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bqb)

PROXY = "word:test:1:proxy:sense:1"
REAL = "word:test:2:real:sense:1"
PLAN = {"words": {"新語": {"entry_id": "test:2", "proxy_id": PROXY}}}


def entry(kind="grammar_cloze", **extra):
    base = {"key": f"t.{kind}.001", "target_id": "grammar:x", "kind": kind, "prompt": "あれ（　）です。",
            "task": "补全。", "answer": "は", "feedback": "あれはです。\n那是。\n说明。", "scenario": "daily_routine"}
    if kind != "grammar_cloze" or "choices" in extra:
        base["choices"] = ["は", "が", "を"]
    base.update(extra)
    return {k: v for k, v in base.items() if v is not None}


def build_one(item, plan=None):
    document, fields = bqb.build_batch([item], "x.authored.json", plan)
    return document["variants"][0], fields[item["key"]]


def no_check(raw, *, target_id=None):
    return None


# --- template shapes ------------------------------------------------------------------------------------------


def test_choice_template_shape_and_key_order():
    variant, _ = build_one(entry("grammar_meaning", choices=["意思", "别的", "其他", "另外"], answer="意思"))
    t = variant["template"]
    assert list(variant) == ["key", "target_id", "kind", "template"]
    assert list(t) == ["prompt", "task", "answer", "choices", "input_mode", "strict", "safe", "feedback", "practice"]
    assert (t["input_mode"], t["strict"], t["safe"]) == ("choice", True, True)
    assert t["practice"] == {"schema_version": 1, "task_family": "grammar_in_context",
                             "context_id": bqb.context_id("grammar:x", "grammar_meaning", t["prompt"], t["task"]),
                             "scaffold": "recognition",
                             "target_spans": [{"start": 2, "end": 5, "quote": "（　）"}],
                             "background_ids": [], "reviewed": True}


def test_context_id_formula_is_pinned():
    assert bqb.context_id("lesson:x", "grammar_cloze", "p（　）", "t") == "context:26341ecbc7bda5100ad12c5e00d4dda7"


def test_cloze_with_choices_is_choice_and_without_is_text():
    with_choices, _ = build_one(entry("grammar_cloze", choices=["は", "が"]))
    assert with_choices["template"]["input_mode"] == "choice"
    assert with_choices["template"]["safe"] is False
    typed, _ = build_one(entry("grammar_cloze", accepted=["ハ", "は"]))
    t = typed["template"]
    assert list(t) == ["prompt", "task", "answer", "accepted_answers", "choices", "input_mode", "strict", "safe",
                       "feedback", "practice"]
    assert (t["input_mode"], t["choices"], t["strict"], t["safe"]) == ("text", [], False, False)
    assert t["accepted_answers"] == ["は", "ハ"]
    assert t["practice"]["scaffold"] == "retrieval"
    assert "support" not in t and "coverage_scope" not in t["practice"]


@pytest.mark.parametrize("kind", ["grammar_transform", "targeted_correction", "controlled_sentence_production"])
def test_typed_grammar_kinds(kind):
    variant, _ = build_one(entry(kind, prompt="あれがです。", span="が"))
    t = variant["template"]
    assert t["input_mode"] == "text" and t["choices"] == []
    assert t["support"] == {"audio_safe": False, "reading_safe": False, "accent_safe": False}
    assert t["practice"]["task_family"] == bqb.DEFAULT_FAMILY[kind]
    if kind == "targeted_correction":
        assert t["known_wrong"] == ["あれがです。"]
        assert list(t).index("known_wrong") == list(t).index("safe") + 1
    else:
        assert "known_wrong" not in t
    focused = kind != "grammar_transform"
    assert ("assessed_ids" in t["practice"]) is focused
    if focused:
        assert list(t["practice"])[-3:] == ["coverage_scope", "assessed_ids", "reviewed"]
        assert t["practice"]["assessed_ids"] == ["grammar:x"]


def test_production_span_defaults_to_the_whole_prompt():
    variant, _ = build_one(entry("controlled_sentence_production", prompt="说那是书。"))
    assert variant["template"]["practice"]["target_spans"] == [{"start": 0, "end": 5, "quote": "说那是书。"}]


def test_reorder_generates_prompt_choices_answer_and_default_task():
    item = entry("grammar_reorder", prompt=None, task=None, answer=None, choices=None,
                 blocks=["あれは", "本", "です"], accepted=["本あれはです"], hint="那是书。")
    t = build_one(item)[0]["template"]
    assert list(t) == ["prompt", "task", "answer", "accepted_answers", "input_mode", "choices", "strict", "safe",
                       "feedback", "practice", "hint", "hint_policy"]
    assert t["prompt"] == "あれは ＿＿ ＿＿"
    assert t["task"] == bqb.REORDER_TASK
    assert t["answer"] == "あれは本です"
    assert t["accepted_answers"] == ["あれは本です", "本あれはです"]
    assert t["choices"] == ["あれは", "本", "です"]
    assert (t["input_mode"], t["strict"], t["safe"]) == ("reorder", False, False)
    assert t["practice"]["scaffold"] == "cued"
    assert t["practice"]["task_family"] == "sentence_reconstruction"
    assert t["practice"]["target_spans"] == [{"start": 0, "end": len(t["prompt"]), "quote": t["prompt"]}]
    assert (t["hint"], t["hint_policy"]) == ("那是书。", "fold_always")
    assert build_one(dict(item, task="排好。"))[0]["template"]["task"] == "排好。"


@pytest.mark.parametrize("kind,mode,safe", [("vocab_context_choice", "choice", False),
                                            ("vocab_orthography", "choice", False),
                                            ("vocab_usage", "text", False), ("vocab_meaning", "choice", True)])
def test_word_kinds(kind, mode, safe):
    t = build_one(entry(kind, target_id="word:test:3"))[0]["template"]
    assert t["input_mode"] == mode and t["safe"] is safe
    assert t["support"] == bqb.NO_SUPPORT
    assert t["practice"]["coverage_scope"] == "focused"
    assert t["practice"]["assessed_ids"] == ["word:test:3"]
    assert t["practice"]["task_family"] == bqb.WORD_FAMILY[kind]


# --- defaults and overrides -----------------------------------------------------------------------------------


def test_family_span_index_readings_and_hint_overrides():
    item = entry("grammar_meaning", prompt="はは、はい", span="は", span_index=2, family="information_extraction",
                 readings={"母": "はは"}, hint="提示")
    t = build_one(item)[0]["template"]
    assert t["practice"]["task_family"] == "information_extraction"
    assert t["practice"]["target_spans"] == [{"start": 3, "end": 4, "quote": "は"}]
    assert t["readings"] == {"母": "はは"}
    assert list(t)[-3:] == ["readings", "hint", "hint_policy"]


def test_span_defaults_to_whole_prompt_without_a_gap():
    t = build_one(entry("grammar_meaning", prompt="あれです"))[0]["template"]
    assert t["practice"]["target_spans"] == [{"start": 0, "end": 4, "quote": "あれです"}]


def test_empty_optional_fields_add_nothing():
    t = build_one(entry("grammar_meaning", hint="", readings={}, family=""))[0]["template"]
    assert "hint" not in t and "readings" not in t
    assert t["practice"]["task_family"] == "grammar_in_context"


def test_new_word_entry_gets_proxy_target_and_new_word():
    item = entry("vocab_meaning", target_id=None, word="新語")
    variant, _ = build_one(item, PLAN)
    assert variant["target_id"] == PROXY
    assert variant["template"]["practice"]["assessed_ids"] == [PROXY]
    assert variant["new_word"] == {"form": "新語", "entry_id": "test:2", "proxy_id": PROXY, "note": bqb.PROXY_NOTE}
    assert "target_id" not in item  # the input entry is not changed


# --- side fields ----------------------------------------------------------------------------------------------


def test_fields_release_default_scenario_and_glosses():
    _, fields = build_one(entry())
    assert fields == {"opens_after_reviews": 0, "scenario_id": "daily_routine"}
    glosses = [{"start": 0, "meaning": "那", "surface": "あれ", "reading": "あれ", "note": "dropped"}]
    _, fields = build_one(entry(release=12, glosses=glosses))
    assert fields == {"opens_after_reviews": 12, "scenario_id": "daily_routine",
                      "glosses": [{"surface": "あれ", "reading": "あれ", "meaning": "那", "start": 0}]}


# --- input errors ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("change,message", [
    ({"kind": "grammar_unknown"}, "t.grammar_cloze.001: kind 'grammar_unknown' not allowed"),
    ({"feedback": None}, "t.grammar_cloze.001: required field 'feedback' missing"),
    ({"task": None}, "t.grammar_cloze.001: required field 'task' missing"),
    ({"target_id": None}, "t.grammar_cloze.001: required field 'target_id' (or 'word') missing"),
    ({"scenario": None}, "t.grammar_cloze.001: scenario missing"),
    ({"span": "が"}, "t.grammar_cloze.001: span 'が' occurrence 0 not in prompt"),
    ({"span_index": 1}, f"t.grammar_cloze.001: span {bqb.GAP!r} occurrence 1 not in prompt"),
    # two occurrences past the only one: the first builder's search wrapped round to it; here it is an error
    ({"span_index": 2}, f"t.grammar_cloze.001: span {bqb.GAP!r} occurrence 2 not in prompt"),
    ({"span_index": -1}, "span_index -1 must be a non-negative integer"),
    ({"release": 41}, "t.grammar_cloze.001: release 41 outside 0..40"),
    ({"release": -1}, "release -1 outside 0..40"),
    ({"release": "3"}, "release '3' outside 0..40"),
    ({"glosses": ["あれ"]}, "t.grammar_cloze.001: glosses must be a list of objects"),
    ({"glosses": {"surface": "あれ"}}, "glosses must be a list of objects"),
    ({"accepted": "は"}, "accepted must be a list"),
])
def test_entry_errors_name_the_key(change, message):
    item = {k: v for k, v in {**entry(), **change}.items() if v is not None}
    with pytest.raises(bqb.BatchError) as caught:
        bqb.build_batch([item], "x.authored.json")
    assert message in str(caught.value)


def test_choice_kind_needs_choices_and_reorder_needs_blocks_that_form_the_answer():
    with pytest.raises(bqb.BatchError, match="required field 'choices' missing"):
        bqb.build_batch([entry("grammar_meaning", choices=None)], "x")
    reorder = entry("grammar_reorder", prompt=None, choices=None, blocks=["あれは", "本", "です"])
    with pytest.raises(bqb.BatchError, match="do not form the answer"):
        bqb.build_batch([dict(reorder, answer="これは本です")], "x")
    assert bqb.build_batch([dict(reorder, answer="あれは本です")], "x")
    with pytest.raises(bqb.BatchError, match="reorder needs blocks"):
        bqb.build_batch([dict(reorder, blocks=[])], "x")


def test_duplicate_and_missing_keys():
    with pytest.raises(bqb.BatchError, match=r"missing 0, duplicated \['t.grammar_cloze.001'\]"):
        bqb.build_batch([entry(), entry()], "x")
    with pytest.raises(bqb.BatchError, match="missing 1"):
        bqb.build_batch([entry(key=None)], "x")


def test_every_problem_is_listed():
    with pytest.raises(bqb.BatchError) as caught:
        bqb.build_batch([entry(key="a", scenario=None), entry(key="b", release=99)], "x")
    assert "a: scenario missing" in str(caught.value) and "b: release 99" in str(caught.value)


def test_new_word_errors():
    item = entry("vocab_meaning", target_id=None, word="新語")
    with pytest.raises(bqb.BatchError, match="need plan.json"):
        bqb.build_batch([item], "x")
    with pytest.raises(bqb.BatchError, match="word '別語' not in plan.json"):
        bqb.build_batch([dict(item, word="別語")], "x", PLAN)


# --- command line, files and the app check ----------------------------------------------------------------------


def write_authored(folder, entries, name="pt.authored.json"):
    (folder / "authored").mkdir(parents=True, exist_ok=True)
    path = folder / "authored" / name
    path.write_text(json.dumps({"variants": entries}, ensure_ascii=False), "utf8")
    return path


def test_main_build_writes_the_two_files_beside_the_input(tmp_path):
    source = write_authored(tmp_path, [entry(), entry("grammar_meaning", key="t.m.001")])
    assert bqb.main(["build", str(source)], validate=no_check) == 0
    document, fields = bqb.build_batch(json.loads(source.read_text("utf8"))["variants"], "pt.authored.json")
    assert document["source"] == "scenario_20261114:pt.authored.json"
    variants = tmp_path / "authored" / "pt.variants.json"
    assert variants.read_text("utf8") == json.dumps(document, ensure_ascii=False, indent=1) + "\n"
    assert (tmp_path / "authored" / "pt.fields.json").read_text("utf8") == json.dumps(
        fields, ensure_ascii=False, indent=1) + "\n"


def test_main_build_options_and_bare_list_input(tmp_path):
    source = tmp_path / "pt.json"
    source.write_text(json.dumps([entry()], ensure_ascii=False), "utf8")
    out = tmp_path / "out"
    out.mkdir()
    assert bqb.main(["build", str(source), "--out-dir", str(out), "--source-prefix", "lab"], validate=no_check) == 0
    assert json.loads((out / "pt.variants.json").read_text("utf8"))["source"] == "lab:pt.json"


def test_main_build_reads_plan_from_the_batch_folder(tmp_path):
    source = write_authored(tmp_path, [entry("vocab_meaning", target_id=None, word="新語")])
    assert bqb.main(["build", str(source)], validate=no_check) == 1  # no plan.json yet
    (tmp_path / "plan.json").write_text(json.dumps(PLAN, ensure_ascii=False), "utf8")
    assert bqb.main(["build", str(source)], validate=no_check) == 0
    variant = json.loads((tmp_path / "authored" / "pt.variants.json").read_text("utf8"))["variants"][0]
    assert variant["target_id"] == PROXY


def test_app_rejection_names_the_key_and_writes_nothing(tmp_path, capsys):
    source = write_authored(tmp_path, [entry()])

    def reject(raw, *, target_id=None):
        raise ValueError("Unsupported reviewed practice metadata")

    assert bqb.main(["build", str(source)], validate=reject) == 1
    assert "t.grammar_cloze.001: practice rejected by the app" in capsys.readouterr().err
    assert not (tmp_path / "authored" / "pt.variants.json").exists()


def test_app_validator_accepts_every_kind_and_leaves_the_output_alone(tmp_path):
    validate = bqb.app_practice_validator()
    assert validate is not None
    entries = [entry(kind, key=f"k.{kind}", target_id="word:test:3", span="あれ" if kind == "targeted_correction" else None)
               for kind in bqb.DEFAULT_FAMILY if kind != "grammar_reorder"]
    entries.append(entry("grammar_reorder", key="k.reorder", prompt=None, answer=None, choices=None,
                         blocks=["あれは", "本", "です"]))
    checked, plain = tmp_path / "checked", tmp_path / "plain"
    for folder, check in ((checked, validate), (plain, no_check)):
        source = write_authored(folder, entries)
        assert bqb.main(["build", str(source)], validate=check) == 0
    for name in ("pt.variants.json", "pt.fields.json"):
        assert (checked / "authored" / name).read_bytes() == (plain / "authored" / name).read_bytes()


# --- assemble -------------------------------------------------------------------------------------------------


def test_assemble_joins_parts_in_stem_order(tmp_path):
    write_authored(tmp_path, [entry(key="b.1"), entry(key="b.2", release=3)], "b_part.authored.json")
    write_authored(tmp_path, [entry(key="a.1")], "a.authored.json")
    for name in ("a.authored.json", "b_part.authored.json"):
        assert bqb.main(["build", str(tmp_path / "authored" / name)], validate=no_check) == 0
    assert bqb.main(["assemble", str(tmp_path)]) == 0
    draft = json.loads((tmp_path / "draft.variants.json").read_text("utf8"))
    assert list(draft) == ["format", "version", "source", "variants"]
    assert draft["source"] == f"scenario_20261114:{tmp_path.name}"
    assert [v["key"] for v in draft["variants"]] == ["a.1", "b.1", "b.2"]
    fields = json.loads((tmp_path / "fields.json").read_text("utf8"))
    assert list(fields) == ["a.1", "b.1", "b.2"] and fields["b.2"]["opens_after_reviews"] == 3


def test_assemble_errors_and_new_word_note():
    doc, fields = bqb.build_batch([entry(key="k")], "a")
    with pytest.raises(bqb.BatchError, match="b: duplicate key k"):
        bqb.assemble_batch([("a", doc, fields), ("b", doc, fields)], "batch")
    with pytest.raises(bqb.BatchError, match="a: fields file lacks 1 keys"):
        bqb.assemble_batch([("a", doc, {})], "batch")
    with pytest.raises(bqb.BatchError, match="a: fields file has 1 keys without a variant"):
        bqb.assemble_batch([("a", doc, {**fields, "z": {}})], "batch")
    word_doc, word_fields = bqb.build_batch([entry("vocab_meaning", key="w", target_id=None, word="新語")], "w", PLAN)
    draft, _ = bqb.assemble_batch([("w", word_doc, word_fields)], "w9")
    assert list(draft) == ["format", "version", "source", "note", "variants"]


# --- retarget -------------------------------------------------------------------------------------------------


def word_entries():
    return [entry("vocab_meaning", key="w.001", target_id=None, word="新語", prompt="しんご", span="しんご",
                  choices=["新词", "旧词"], answer="新词"),
            entry("vocab_usage", key="w.002", target_id=None, word="新語", prompt="新語を書く。", span="新語",
                  answer="しんご"),
            entry("grammar_cloze", key="g.001")]


def direct_entries():
    return [{**{k: v for k, v in e.items() if k != "word"}, "target_id": REAL} if e.get("word") else e
            for e in word_entries()]


@pytest.mark.parametrize("mapping", [{"test:2": REAL}, {"新語": REAL}, {"test:2": REAL, "新語": "word:other"},
                                     [{"id": REAL, "word_id": "test:2", "title": "新語", "reading": "しんご"}]])
def test_retarget_equals_a_direct_build_with_the_real_id(tmp_path, mapping):
    proxy_doc, fields = bqb.build_batch(word_entries(), "w.authored.json", PLAN)
    direct_doc, direct_fields = bqb.build_batch(direct_entries(), "w.authored.json")
    assert proxy_doc != direct_doc and fields == direct_fields
    source, ids, fields_path, out = (tmp_path / n for n in ("w.variants.json", "ids.json", "f.json", "out.json"))
    bqb.write_json(source, proxy_doc)
    bqb.write_json(ids, mapping)
    bqb.write_json(fields_path, fields)
    argv = ["retarget", str(source), "--ids", str(ids), "--fields", str(fields_path), "-o", str(out)]
    assert bqb.main(argv, validate=bqb.app_practice_validator()) == 0
    expected = tmp_path / "direct.json"
    bqb.write_json(expected, direct_doc)
    assert out.read_bytes() == expected.read_bytes()


def test_retarget_of_an_assembled_draft_drops_the_proxy_note():
    proxy_doc, fields = bqb.build_batch(word_entries(), "w", PLAN)
    direct_doc, _ = bqb.build_batch(direct_entries(), "w")
    draft, _ = bqb.assemble_batch([("w", proxy_doc, fields)], "w9")
    direct_draft, _ = bqb.assemble_batch([("w", direct_doc, fields)], "w9")
    assert bqb.retarget_document(draft, bqb.lesson_id_index({"test:2": REAL})) == direct_draft


def test_retarget_leaves_other_variants_unchanged():
    proxy_doc, _ = bqb.build_batch(word_entries(), "w", PLAN)
    result = bqb.retarget_document(proxy_doc, bqb.lesson_id_index({"test:2": REAL}))
    assert result["variants"][2] == proxy_doc["variants"][2]
    assert all("new_word" not in v for v in result["variants"])
    assert "new_word" in proxy_doc["variants"][0]  # the input is not changed


def test_retarget_errors():
    proxy_doc, fields = bqb.build_batch(word_entries(), "w", PLAN)
    with pytest.raises(bqb.BatchError, match=r"2 new-word variants unmapped: w.001 \(新語, test:2\); w.002"):
        bqb.retarget_document(proxy_doc, bqb.lesson_id_index({"test:9": REAL}))
    ambiguous = [{"id": REAL, "word_id": "test:2", "title": "新語"}, {"id": "word:x", "word_id": "test:2", "title": "新語"}]
    with pytest.raises(bqb.BatchError, match="unmapped"):
        bqb.retarget_document(proxy_doc, bqb.lesson_id_index(ambiguous))
    edited = json.loads(json.dumps(proxy_doc))
    edited["variants"][0]["template"]["prompt"] = "かえた"
    with pytest.raises(bqb.BatchError, match="w.001: context_id does not match the proxy target"):
        bqb.retarget_document(edited, bqb.lesson_id_index({"test:2": REAL}))
    with pytest.raises(bqb.BatchError, match="no lesson id"):
        bqb.lesson_id_index({"test:2": ""})
    assert bqb.fields_key_problems(proxy_doc, {}) == ["fields file lacks 3 keys: ['w.001', 'w.002', 'g.001']"]


# --- needs (author-declared dependencies the dependency tool cannot detect) ----------------------------------

NEEDS = ["grammar:test:listing_to", "word:test:5:real:sense:1"]


def test_needs_go_to_the_fields_file_only_and_leave_everything_else_byte_identical(tmp_path):
    plain, with_needs = tmp_path / "plain", tmp_path / "needs"
    write_authored(plain, [entry(), entry("grammar_meaning", key="t.m.001")])
    write_authored(with_needs, [entry(needs=NEEDS), entry("grammar_meaning", key="t.m.001")])
    for folder in (plain, with_needs):
        assert bqb.main(["build", str(folder / "authored" / "pt.authored.json")], validate=no_check) == 0
    assert ((plain / "authored" / "pt.variants.json").read_bytes()
            == (with_needs / "authored" / "pt.variants.json").read_bytes())
    plain_fields = json.loads((plain / "authored" / "pt.fields.json").read_text("utf8"))
    fields = json.loads((with_needs / "authored" / "pt.fields.json").read_text("utf8"))
    assert "needs" not in plain_fields["t.grammar_cloze.001"] and "needs" not in fields["t.m.001"]
    assert fields["t.grammar_cloze.001"] == {**plain_fields["t.grammar_cloze.001"], "needs": NEEDS}
    assert list(fields["t.grammar_cloze.001"]) == ["opens_after_reviews", "scenario_id", "needs"]


@pytest.mark.parametrize("needs,message", [
    ([], "needs must be a list of 1..8"), ("grammar:x:y", "needs must be a list of 1..8"),
    ([f"grammar:n{i}" for i in range(9)], "needs must be a list of 1..8"),
    (["grammar:a", ""], "needs must be a list of 1..8 non-empty"), (["grammar:a", " "], "needs must be a list"),
    (["grammar:a", 3], "needs must be a list"), (["grammar:a", "grammar:a"], "needs repeats an id"),
    (["grammar:x"], "needs names the entry's own target 'grammar:x'")])
def test_needs_errors_name_the_key(needs, message):
    with pytest.raises(bqb.BatchError, match=f"t.grammar_cloze.001: {message}"):
        build_one(entry(needs=needs))


def test_a_need_naming_a_stand_in_id_is_reported_at_build_and_at_retarget(tmp_path):
    with pytest.raises(bqb.BatchError, match=f"g.001: need '{PROXY}' is a new word's stand-in id"):
        bqb.build_batch([entry(key="g.001", needs=[PROXY])], "w", PLAN)
    # A word entry naming its own stand-in names its own target.
    with pytest.raises(bqb.BatchError, match="names the entry's own target"):
        bqb.build_batch([entry("vocab_meaning", key="w.001", target_id=None, word="新語", needs=[PROXY])], "w", PLAN)
    # retarget never rewrites a need; a stand-in among the needs of the fields file is reported.
    proxy_doc, fields = bqb.build_batch(word_entries(), "w", PLAN)
    fields["g.001"]["needs"] = [PROXY, "grammar:test:listing_to"]
    source, ids, fields_path, out = (tmp_path / n for n in ("w.variants.json", "ids.json", "f.json", "out.json"))
    bqb.write_json(source, proxy_doc)
    bqb.write_json(ids, {"test:2": REAL})
    bqb.write_json(fields_path, fields)
    argv = ["retarget", str(source), "--ids", str(ids), "--fields", str(fields_path), "-o", str(out)]
    assert bqb.main(argv, validate=no_check) == 1 and not out.exists()
    assert bqb.fields_needs_problems(proxy_doc, fields) == [
        f"g.001: need '{PROXY}' is a new word's stand-in id; name the real lesson id"]
    fields["g.001"]["needs"] = [REAL, "grammar:test:listing_to"]
    bqb.write_json(fields_path, fields)
    assert bqb.main(argv, validate=no_check) == 0
    assert json.loads(fields_path.read_text("utf8"))["g.001"]["needs"] == [REAL, "grammar:test:listing_to"]


def test_the_builders_own_limits_equal_the_helpers_and_the_apps():
    # The builder runs without the app and without the helper, so its two limits are copies of theirs.
    from kanamemory.learning_questions import MAX_OPENS_AFTER_REVIEWS
    from practice_authoring_fields import ANSWER_FORM_FIELDS, MAX_NEEDS
    assert (bqb.MAX_NEEDS, bqb.MAX_RELEASE) == (MAX_NEEDS, MAX_OPENS_AFTER_REVIEWS)
    assert bqb.ANSWER_FORM_FIELDS == ANSWER_FORM_FIELDS


# --- strict typed answers, the whole-expression recall question, the answer's display form ----------------------


def recall_entry(**extra):
    """A word lesson's "say the whole expression" question: Chinese situation cue -> the expression, typed."""
    base = {"key": "t.recall.001", "target_id": "word:test:yoroshiku", "kind": "vocab_recall",
            "prompt": "第一次见面，自我介绍完，你最后说什么？", "task": "写出整句表达。",
            "answer": "よろしくお願いします", "accepted": ["よろしくおねがいします"], "strict": True,
            "feedback": "よろしくお願いします。\n请多关照。", "scenario": "self_intro"}
    base.update(extra)
    return {k: v for k, v in base.items() if v is not None}


@pytest.mark.parametrize("kind", ["controlled_sentence_production", "grammar_transform", "grammar_cloze"])
def test_an_authored_strict_true_is_kept_on_typed_kinds_and_its_absence_builds_as_before(kind):
    plain = entry(kind, prompt="あれ（　）です。", choices=None) if kind != "grammar_cloze" else entry()
    plain.pop("choices", None)
    before, _ = build_one(plain)
    strict, _ = build_one({**plain, "strict": True})
    explicit, _ = build_one({**plain, "strict": False})
    assert before["template"]["input_mode"] == "text" and before["template"]["strict"] is False
    assert strict["template"]["strict"] is True
    assert {**strict["template"], "strict": False} == before["template"] == explicit["template"]
    assert list(strict["template"]) == list(before["template"])


@pytest.mark.parametrize("change,message", [
    ({"strict": "true"}, "strict must be true or false"),
    ({"strict": True, "choices": ["は", "が"]}, "only on a typed answer"),
    ({"kind": "grammar_meaning", "strict": True, "choices": ["は", "が"]}, "only on a typed answer"),
    ({"kind": "grammar_reorder", "strict": True, "blocks": ["あれ", "は"], "prompt": None, "answer": None},
     "only on a typed answer"),
])
def test_strict_is_refused_where_the_builder_does_not_decide_it(change, message):
    item = {k: v for k, v in {**entry(), **change}.items() if v is not None}
    with pytest.raises(bqb.BatchError) as caught:
        bqb.build_batch([item], "x.authored.json")
    assert message in str(caught.value)


def test_vocab_recall_builds_the_whole_expression_question_of_a_word_lesson():
    variant, fields = build_one(recall_entry())
    t = variant["template"]
    assert variant["kind"] == "vocab_recall" and variant["target_id"] == "word:test:yoroshiku"
    assert list(t) == ["prompt", "task", "answer", "accepted_answers", "choices", "input_mode", "strict", "safe",
                       "feedback", "support", "practice"]
    assert (t["input_mode"], t["strict"], t["safe"], t["choices"]) == ("text", True, False, [])
    assert t["accepted_answers"] == ["よろしくお願いします", "よろしくおねがいします"]
    assert t["support"] == {"audio_safe": False, "reading_safe": False, "accent_safe": False}
    practice = t["practice"]
    assert (practice["task_family"], practice["scaffold"], practice["coverage_scope"], practice["assessed_ids"]) == (
        "expression_recall", "retrieval", "focused", ["word:test:yoroshiku"])
    prompt = t["prompt"]
    assert practice["target_spans"] == [{"start": 0, "end": len(prompt), "quote": prompt}]
    assert fields == {"opens_after_reviews": 0, "scenario_id": "self_intro"}
    # The app's own check accepts the template; the kind is a word kind of the use ability.
    from kanamemory.learning_questions import KIND_ABILITIES, WORD_KINDS, validate_practice
    validate_practice(t, target_id=variant["target_id"])
    assert KIND_ABILITIES["vocab_recall"] == "use" and "vocab_recall" in WORD_KINDS
    # The Chinese cue is shown in the interface face, the typed expression in the Japanese study face (the
    # word_form_recall family is a Japanese gap sentence, so it would put the cue in the study face).
    from kanamemory.question_support import learning_text_role
    question = {"kind": "vocab_recall", "practice": practice}
    assert (learning_text_role(question, "prompt"), learning_text_role(question, "answer")) == ("ui", "study")
    gap, _ = build_one(recall_entry(prompt="（　）。", family="word_form_recall"))
    assert learning_text_role({"kind": "vocab_recall", "practice": gap["template"]["practice"]}, "prompt") == "study"


def test_answer_display_and_reading_go_to_the_fields_never_into_the_template():
    plain_variant, plain_fields = build_one(recall_entry(answer="よろしくおねがいします", accepted=None))
    variant, fields = build_one(recall_entry(answer="よろしくおねがいします", accepted=None,
                                             answer_display="よろしくお願いします"))
    assert variant == plain_variant
    assert fields == {**plain_fields, "answer_display": "よろしくお願いします"}
    _, fields = build_one(recall_entry(answer_display="宜しくお願いします", answer_reading="よろしくおねがいします"))
    assert (fields["answer_display"], fields["answer_reading"]) == ("宜しくお願いします", "よろしくおねがいします")


@pytest.mark.parametrize("change,message", [
    ({"answer_display": ""}, "answer_display must be a non-empty string"),
    ({"answer_display": " 誰 "}, "without surrounding space"),
    ({"answer_display": 3}, "answer_display must be a non-empty string"),
    ({"answer_reading": "だれ"}, "answer_reading is the reading of answer_display; give both"),
])
def test_answer_form_field_types_are_checked_without_the_app(change, message):
    with pytest.raises(bqb.BatchError) as caught:
        bqb.build_batch([{**entry(), **change}], "x.authored.json")
    assert message in str(caught.value)


def test_main_build_checks_the_answer_form_with_the_app_and_writes_nothing_on_a_failure(tmp_path, capsys):
    good = recall_entry(answer="よろしくおねがいします", accepted=None, answer_display="よろしくお願いします")
    bad = recall_entry(key="t.recall.002", answer_display="宜しく", answer_reading="よろしくおねがいします")
    source = write_authored(tmp_path, [good, bad])
    assert bqb.main(["build", str(source)], validate=no_check) == 1
    assert "t.recall.002" in capsys.readouterr().err and not (tmp_path / "authored" / "pt.variants.json").exists()
    source = write_authored(tmp_path, [good])
    assert bqb.main(["build", str(source)], validate=no_check) == 0
    fields = json.loads((tmp_path / "authored" / "pt.fields.json").read_text("utf8"))
    assert fields["t.recall.001"]["answer_display"] == "よろしくお願いします"


def test_wrong_options_become_the_templates_known_wrong_and_are_checked():
    variant, fields = build_one(entry(choices=["は", "が", "わ"], wrong=["わ"]))
    t = variant["template"]
    assert t["known_wrong"] == ["わ"] and "wrong" not in fields
    assert list(t) == ["prompt", "task", "answer", "choices", "input_mode", "strict", "safe", "known_wrong",
                       "feedback", "practice"]
    assert "known_wrong" not in build_one(entry())[0]["template"]  # without the field: unchanged shape
    for change, message in (({"wrong": ["で"]}, "is not one of the choices"), ({"wrong": ["は"]}, "names the answer"),
                            ({"wrong": []}, "non-empty list"), ({"wrong": ["が", "が"]}, "non-empty list"),
                            ({"wrong": ["が"], "choices": None}, "only a choice entry")):
        with pytest.raises(bqb.BatchError) as caught:
            bqb.build_batch([entry(**{"choices": ["は", "が", "を"], **change})], "x.authored.json")
        assert message in str(caught.value)
