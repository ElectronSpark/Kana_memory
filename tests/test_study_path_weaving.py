"""Vocabulary weaving changes navigation, never enrollment identities or edges."""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import sys

import pytest

from kanamemory import study_path_catalog as catalog
from kanamemory.study_path_catalog import _weave_vocabulary, flatten_sections


def section(name, family="grammar", *, titles=None, depends=(), optional=False,
            entry_depends=()):
    titles = titles or [name]
    return {"id": "section:" + name, "title": name, "optional": optional,
            "component_section_ids": ["section:" + name, "component:" + name],
            "topics": [name], "depends_on": list(depends),
            "entries": [{"id": family + ":" + name + (str(i) if i else ""),
                         "content_id": family + ":" + name + (str(i) if i else ""),
                         "kind": "learning", "family": family, "title": title,
                         "depends_on": list(entry_depends)} for i, title in enumerate(titles)]}


def unit(name, *sections):
    result = {"id": "unit:" + name, "title": name, "sections": list(sections)}
    for item in sections:
        item.update(unit_id=result["id"], unit_title=name)
    return result


def stage(name, *units, optional=False):
    result = {"id": "stage:" + name, "title": name, "optional": optional, "units": list(units)}
    for group in units:
        for item in group["sections"]:
            item.update(stage_id=result["id"], stage_title=name)
    return result


def path(*stages):
    return {"version": "fixture-v1", "stages": list(stages), "scope": "fixture"}


def mapping(form, *targets):
    return {"word_forms": [form], "before": list(targets)}


def identities(value):
    return {s["id"]: {key: deepcopy(data) for key, data in s.items()
                       if key not in {"unit_id", "unit_title"}}
            for s in flatten_sections(value)}


def assert_preserved(before, after):
    assert identities(before) == identities(after)
    original = [e["id"] for s in flatten_sections(before) for e in s["entries"]]
    moved = [e["id"] for s in flatten_sections(after) for e in s["entries"]]
    assert Counter(original) == Counter(moved)
    assert len(moved) == len(set(moved))
    for level in after["stages"]:
        for group in level["units"]:
            for item in group["sections"]:
                assert item["stage_id"] == level["id"]
                assert item["unit_id"] == group["id"] and item["unit_title"] == group["title"]


def test_moves_whole_word_sections_between_grammar_without_changing_identity_or_input():
    original = path(stage("daily",
        unit("words", section("place", "word", titles=["学校", "駅"]),
             section("time", "word", titles=["今日"]), section("other", "word", titles=["猫"])),
        unit("grammar", section("one"), section("two"), section("three"))))
    saved = deepcopy(original)
    rules = [mapping("駅", "grammar:two"), mapping("今日", "grammar:three")]
    result = _weave_vocabulary(original, rules)
    assert original == saved
    assert [[s["id"] for s in u["sections"]] for u in result["stages"][0]["units"]] == [
        ["section:other"], ["section:one", "section:place", "section:two", "section:time", "section:three"]]
    assert_preserved(original, result)
    assert _weave_vocabulary(result, rules) == result


def test_empty_source_unit_disappears_and_existing_word_sections_can_be_spread_out():
    original = path(stage("daily", unit("words", section("external", "word", titles=["家"])),
        unit("mixed", section("local", "word", titles=["朝"]), section("one"), section("two"))))
    result = _weave_vocabulary(original, [mapping("家", "grammar:one"), mapping("朝", "grammar:two")])
    assert [u["id"] for u in result["stages"][0]["units"]] == ["unit:mixed"]
    assert [s["id"] for s in flatten_sections(result)] == [
        "section:external", "section:one", "section:local", "section:two"]
    assert_preserved(original, result)


@pytest.mark.parametrize("edge", ["source_section", "dependent_section", "source_entry", "dependent_entry"])
def test_unsafe_move_preserves_source_and_dependent_order(edge):
    if edge.startswith("source"):
        word = section("word", "word", titles=["駅"],
                       depends=["section:base"] if edge == "source_section" else [],
                       entry_depends=["grammar:base"] if edge == "source_entry" else [])
        original = path(stage("daily", unit("grammar", section("target"), section("base")), unit("word", word)))
    else:
        dependent = section("dependent", depends=["section:word"] if edge == "dependent_section" else [],
                            entry_depends=["word:word"] if edge == "dependent_entry" else [])
        original = path(stage("daily", unit("word", section("word", "word", titles=["駅"])),
                              unit("grammar", dependent, section("target"))))
    assert _weave_vocabulary(original, [mapping("駅", "grammar:target")]) == original


def test_safe_move_keeps_required_predecessor_and_following_dependent_intact():
    original = path(stage("daily", unit("grammar", section("base")),
        unit("words", section("word", "word", titles=["駅"], depends=["section:base"])),
        unit("later", section("target"), section("dependent", depends=["section:word"]))))
    result = _weave_vocabulary(original, [mapping("駅", "grammar:target")])
    assert [u["id"] for u in result["stages"][0]["units"]] == ["unit:grammar", "unit:later"]
    assert [s["id"] for s in result["stages"][0]["units"][1]["sections"]] == [
        "section:word", "section:target", "section:dependent"]
    assert_preserved(original, result)


def test_first_matching_rule_owns_section_even_if_no_target_then_fallback_ids_are_local():
    original = path(stage("daily", unit("words", section("word", "word", titles=["駅", "学校"])),
                          unit("grammar", section("local"))),
                    stage("later", unit("other", section("foreign"))))
    assert _weave_vocabulary(original, [mapping("駅", "grammar:missing"),
                                        mapping("学校", "grammar:local")]) == original
    result = _weave_vocabulary(original, [mapping("駅", "grammar:missing", "grammar:foreign", "grammar:local"),
                                         mapping("学校", "grammar:foreign")])
    assert [s["id"] for s in result["stages"][0]["units"][0]["sections"]] == ["section:word", "section:local"]
    assert_preserved(original, result)


@pytest.mark.parametrize("case", ["cross_stage", "missing", "optional_stage", "optional_source", "optional_target", "unresolved"])
def test_missing_cross_stage_optional_or_unresolved_mappings_leave_path_unchanged(case):
    word = section("word", "word", titles=["駅"], optional=case == "optional_source")
    grammar = section("target", optional=case == "optional_target")
    if case == "unresolved":
        word["unresolved_dependencies"] = ["grammar:retired"]
    if case == "cross_stage":
        original = path(stage("daily", unit("word", word)), stage("later", unit("grammar", grammar)))
    else:
        original = path(stage("daily", unit("word", word), unit("grammar", grammar), optional=case == "optional_stage"))
    target = "grammar:missing" if case == "missing" else "grammar:target"
    assert _weave_vocabulary(original, [mapping("駅", target)]) == original


@pytest.mark.parametrize("rules", [[], {}, "bad", [None], [{"word_forms": "駅", "before": ["grammar:target"]}],
                                   [{"word_forms": ["駅"], "before": []}],
                                   [{"word_forms": ["駅", None], "before": ["grammar:target"]}]])
def test_invalid_or_absent_configuration_is_an_exact_noop(rules):
    original = path(stage("daily", unit("words", section("word", "word", titles=["駅"])),
                          unit("grammar", section("target"))))
    assert _weave_vocabulary(original, rules) == original


def test_title_matching_is_exact_and_optional_extension_remains_byte_for_byte_equal():
    optional = stage("personal", unit("personal", section("personal", "word", titles=["駅"], optional=True)), optional=True)
    original = path(stage("daily", unit("words", section("word", "word", titles=["駅前"])),
                          unit("grammar", section("target"))), optional)
    assert _weave_vocabulary(original, [mapping("駅", "grammar:target")]) == original
    result = _weave_vocabulary(original, [mapping("駅前", "grammar:target")])
    assert result["stages"][-1] == optional
    assert_preserved(original, result)


def test_builder_reads_content_configuration_after_combining_and_keeps_section_ids(monkeypatch):
    monkeypatch.setattr(catalog, "WORD_TOPICS", [(3, "places", "地点词", "地点", "駅 学校")])
    monkeypatch.setattr(catalog, "SMALL_UNIT_PLANS", {})
    monkeypatch.setattr(catalog, "_PATH", {})
    items = [{"id": "word:station", "title": "駅", "family": "word", "depends_on": []},
             {"id": "grammar:test:first", "title": "规则一", "family": "grammar", "category": "定位", "depends_on": []},
             {"id": "grammar:test:second", "title": "规则二", "family": "grammar", "category": "定位", "depends_on": ["grammar:test:first"]}]
    before_items = deepcopy(items)
    baseline = catalog.build_study_path([], items)
    monkeypatch.setitem(catalog._PATH, "VOCABULARY_WEAVE", [mapping("駅", "grammar:test:second")])
    result = catalog.build_study_path([], items)
    assert items == before_items
    assert_preserved(baseline, result)
    target = next(s for s in flatten_sections(result) if any(e["id"] == "grammar:test:second" for e in s["entries"]))
    vocabulary = next(s for s in flatten_sections(result) if any(e["id"] == "word:station" for e in s["entries"]))
    assert len(target["component_section_ids"]) == 2
    assert vocabulary["unit_id"] == target["unit_id"]
    assert flatten_sections(result).index(vocabulary) + 1 == flatten_sections(result).index(target)
