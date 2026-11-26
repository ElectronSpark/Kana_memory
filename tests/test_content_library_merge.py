"""Pure multi-library rules (08 phase 3/4): policy table, conflict rules a-e, catalogue, frontier, upgrade."""
import copy
import io
import json
import zipfile

import pytest

from kanamemory import content_library_merge as merge
from kanamemory import content_library_rules as rules
from kanamemory.course_prerequisites import active_rules
from kanamemory.knowledge_graph import alias_relations, validate_relations
from support.content_library_support import CORE_ITEMS, extension_namespaces, pack_bytes
from support.content_library_support import no_ambient_bundled_stamp  # noqa: F401  (autouse: no ambient stamp)

CORE = merge.CORE_LIBRARY_ID


def _item(cid, *, revision=1, meaning="义", depends_on=()):
    family = cid.split(":", 1)[0]
    return {"id": cid, "revision": revision, "family": family, "domain": family, "title": cid, "reading": "",
            "meaning": meaning, "instruction": "", "question_templates": [], "source_urls": [],
            "depends_on": list(depends_on)}


def _fingerprint(value):
    from kanamemory.material_review import item_fingerprint
    return item_fingerprint(value) if isinstance(value, dict) and "family" in value else merge.digest(value)


def test_builtin_policy_table_follows_section_6_2():
    assert merge.builtin_policy("learning_content", "_WORDS") == "items_by_id"
    assert merge.builtin_policy("presentation_metadata", "kana_intro_notes") == "map_by_key"
    assert merge.builtin_policy("course_prerequisites", "FORM_CONSUMERS") == "contribution"
    assert merge.builtin_policy("course_prerequisites", "FORM_BASES") == "singleton"
    assert merge.builtin_policy("course_prerequisites", "NAI_FORMS") == "singleton"
    assert merge.builtin_policy("study_path_catalog", "WORD_TOPICS") == "contribution"
    assert merge.builtin_policy("study_path_catalog", "STAGES") == "singleton"
    assert merge.builtin_policy("study_path_catalog", "COHERENCE_GROUPS") == "singleton"
    assert merge.builtin_policy("varied_practice", "items") == "items_by_id"
    assert merge.builtin_policy("unknown_ns", "_ITEMS") == "items_by_id"
    assert merge.builtin_policy("unknown_ns", "NOTES") == "map_by_key"


def test_manifest_policies_may_tighten_but_never_relax():
    assert merge.effective_policy("study_path_catalog", "WORD_TOPICS", [("author.n3", "items_by_id")]) == ("items_by_id", [])
    policy, refused = merge.effective_policy("learning_content", "_WORDS", [("author.n3", "contribution")])
    assert policy == "items_by_id" and refused == [("author.n3", "learning_content", "contribution", "items_by_id")]
    policy, refused = merge.effective_policy("study_path_catalog", "WORD_TOPICS", [("author.n3", "singleton")])
    assert policy == "contribution" and refused
    assert merge.effective_policy("study_path_catalog", "WORD_TOPICS", [(CORE, "singleton")])[0] == "singleton"
    core = {"WORD_TOPICS": {"basic": ["a"]}}
    ext = {"WORD_TOPICS": {"ext": ["b"]}}
    merged, report = merge.merge_namespace([(CORE, core), ("author.n3", ext)], namespace="route",
                                           declared_policies=[(CORE, "singleton")])
    assert merged["WORD_TOPICS"] == {"basic": ["a"]} and report.ignored_singletons == (("author.n3", "WORD_TOPICS"),)
    merged, report = merge.merge_namespace([(CORE, {"_ITEMS": {}}), ("author.n3", {"_ITEMS": {}})],
                                           namespace="learning_content", declared_policies=[("author.n3", "contribution")])
    assert report.refused_policies == (("author.n3", "learning_content", "contribution", "items_by_id"),)


def test_conflict_rules_a_b_c_by_id_with_priority_and_accepted_override():
    same = _item("grammar:a")
    changed = _item("grammar:a", revision=2, meaning="改")
    clash = _item("grammar:a", revision=1, meaning="改")
    core = {"_ITEMS": [same]}
    # a) same fingerprint: one entry, both sources recorded, no conflict
    merged, report = merge.merge_namespace([(CORE, core), ("author.n3", {"_ITEMS": [dict(same, depends_on=[])]})],
                                           namespace="learning_content", fingerprint=_fingerprint)
    assert merged["_ITEMS"] == [same] and report.conflicts == ()
    assert report.duplicates == (("_ITEMS", "grammar:a", CORE, "author.n3"),)
    # b) different fingerprint: priority keeps the core version and reports it
    merged, report = merge.merge_namespace([(CORE, core), ("author.n3", {"_ITEMS": [changed]})],
                                           namespace="learning_content", fingerprint=_fingerprint)
    assert merged["_ITEMS"][0]["meaning"] == "义" and report.conflicts == (("_ITEMS", "grammar:a", CORE, "author.n3"),)
    assert report.revision_conflicts == ()
    # b) an accepted override lets the lower-priority library win for that id only
    merged, report = merge.merge_namespace([(CORE, core), ("author.n3", {"_ITEMS": [changed]})],
                                           namespace="learning_content", fingerprint=_fingerprint,
                                           overrides_accepted={("author.n3", "grammar:a")})
    assert merged["_ITEMS"][0]["meaning"] == "改" and report.overridden == (("_ITEMS", "grammar:a", "author.n3", CORE),)
    # c) same revision, different fingerprint is a package error (reported; precheck refuses)
    _, report = merge.merge_namespace([(CORE, core), ("author.n3", {"_ITEMS": [clash]})],
                                      namespace="learning_content", fingerprint=_fingerprint)
    assert report.revision_conflicts == (("_ITEMS", "grammar:a", CORE, "author.n3"),)
    # Inputs are never mutated; tuples stay tuples.
    assert core == {"_ITEMS": [same]}
    merged, _ = merge.merge_namespace([(CORE, {"T": ("x",)}), ("author.n3", {"T": ("y",)})], namespace="n")
    assert merged["T"] == ("x", "y")


def test_contribution_unions_sequences_and_records_provenance():
    core = {"FORM_CONSUMERS": {"te": ("grammar:a",)}, "SEMANTIC_REQUIREMENTS": {"grammar:a": ("grammar:b",)},
            "FORM_BASES": {"te": ("grammar:te",)}}
    ext = {"FORM_CONSUMERS": {"te": ("grammar:ext:x", "grammar:a"), "nai": ("grammar:ext:y",)},
           "SEMANTIC_REQUIREMENTS": {"grammar:a": ("grammar:ext:z",)}, "FORM_BASES": {"te": ("grammar:other",)}}
    merged, report = merge.merge_namespace([(CORE, core), ("author.n3", ext)], namespace="course_prerequisites")
    assert merged["FORM_CONSUMERS"] == {"te": ("grammar:a", "grammar:ext:x"), "nai": ("grammar:ext:y",)}
    assert merged["SEMANTIC_REQUIREMENTS"] == {"grammar:a": ("grammar:b", "grammar:ext:z")}
    assert merged["FORM_BASES"] == {"te": ("grammar:te",)}
    assert ("author.n3", "FORM_BASES") in report.ignored_singletons
    assert set(report.contributions) == {("FORM_CONSUMERS", "te", "author.n3", ("grammar:ext:x",)),
                                         ("FORM_CONSUMERS", "nai", "author.n3", ("grammar:ext:y",)),
                                         ("SEMANTIC_REQUIREMENTS", "grammar:a", "author.n3", ("grammar:ext:z",))}


def test_extension_rules_apply_only_to_present_consumers():
    contributions = (("FORM_CONSUMERS", "te", "author.n3", ("grammar:ext:x", "grammar:gone")),
                     ("SEMANTIC_REQUIREMENTS", "grammar:a", "author.n3", ("grammar:ext:z", "grammar:blocked")),
                     ("SEMANTIC_REQUIREMENTS", "grammar:absent", "author.n3", ("grammar:a",)),
                     ("FORM_CONSUMERS", "unknown", "author.n3", ("grammar:a",)))
    semantic = {"grammar:a": ("grammar:b", "grammar:ext:z", "grammar:blocked"), "grammar:absent": ("grammar:a",)}
    consumers = {"te": ("grammar:a", "grammar:ext:x", "grammar:gone"), "unknown": ("grammar:a",)}
    catalogue = {cid: {} for cid in ("grammar:a", "grammar:b", "grammar:ext:x", "grammar:ext:z", "grammar:blocked")}
    semantic, consumers, skipped = active_rules(catalogue, contributions=contributions, semantic=semantic,
                                                consumers=consumers, bases={"te": ("grammar:b",)},
                                                blocked=lambda cid: cid == "grammar:blocked")
    assert semantic == {"grammar:a": ("grammar:b", "grammar:ext:z")}
    assert consumers == {"te": ("grammar:a", "grammar:ext:x")}
    assert {(row[3], row[4]) for row in skipped} == {("grammar:gone", "missing_consumer"),
                                                     ("grammar:blocked", "blocked_requirement"),
                                                     ("grammar:a", "missing_consumer"), ("grammar:a", "unknown_form")}
    untouched = {"x": ("y",)}
    assert active_rules({}, contributions=(), semantic=untouched, consumers={}, bases={})[0] is untouched


def _extension(library_id, items, **manifest):
    payload = {"_ITEMS": {item["id"]: item for item in items}}
    base = {"provides": {"items": [item["id"] for item in items], "namespaces": ["lesson"]},
            "references": [], "depends_libraries": [], "overrides": [], "retires": [], "aliases": {}}
    base.update(manifest)
    return merge.ExtensionLibrary(library_id, base, {"lesson": payload})


def test_effective_catalogue_resolves_owners_references_aliases_and_blocking():
    core = {"grammar:core": _item("grammar:core"), "grammar:shared": _item("grammar:shared")}
    ext = _extension("author.n3", [
        _item("grammar:n3:a", depends_on=["grammar:core", "grammar:nowhere"]),
        _item("grammar:n3:b", depends_on=["grammar:n3:a"]),
        _item("grammar:n3:c", depends_on=["grammar:maybe", "grammar:old"]),
        _item("grammar:shared", meaning="改"),
    ], references=[{"id": "grammar:nowhere", "kind": "item", "requirement": "required", "note": ""},
                   {"id": "grammar:maybe", "kind": "item", "requirement": "optional", "note": ""}],
        overrides=[{"id": "grammar:shared", "reason": "fix"}],
        retires=[{"id": "grammar:n3:retired", "replaced_by": "grammar:n3:c", "reason": "merged"}],
        depends_libraries=[{"library_id": "author.base", "version_range": ""}])
    other = _extension("author.alias", [_item("grammar:new")], aliases={"grammar:old": "grammar:new"})
    catalogue = merge.build_effective_catalogue(
        core_items=core, extensions=[ext, other],
        unavailable=[merge.UnavailableLibrary("author.off", ("grammar:off", "grammar:core"), "disabled")])
    assert catalogue.library_of("grammar:core") == CORE and catalogue.library_of("grammar:n3:a") == "author.n3"
    assert catalogue.library_of("grammar:shared") == CORE  # override declared but not accepted
    assert catalogue.providers_of("grammar:shared") == (CORE, "author.n3")
    assert catalogue.library_of("word:jmdict:1:よみ:s1") is None
    assert catalogue.missing_required_references("grammar:n3:a") == ("grammar:nowhere",)
    assert catalogue.missing_required_references("grammar:n3:b") == ("grammar:nowhere",)  # transitive
    assert catalogue.missing_required_references("grammar:n3:c") == ()
    assert catalogue.missing_required_references("grammar:core") == ()
    assert catalogue.blocked_ids() == {"grammar:n3:a", "grammar:n3:b"}
    assert catalogue.dropped_optional == {"grammar:n3:c": ("grammar:maybe",)}
    assert catalogue.alias_rewrites == {"grammar:n3:c": (("grammar:old", "grammar:new"),)}
    assert catalogue.alias_target("grammar:old") == "grammar:new"
    cleaned = catalogue.payloads["author.n3"]["lesson"]["_ITEMS"]
    assert cleaned["grammar:n3:a"]["depends_on"] == ["grammar:core"]
    assert cleaned["grammar:n3:c"]["depends_on"] == ["grammar:new"]
    assert ext.payloads["lesson"]["_ITEMS"]["grammar:n3:a"]["depends_on"] == ["grammar:core", "grammar:nowhere"]
    assert catalogue.missing_libraries == {"author.n3": ("author.base",)}
    assert catalogue.is_unavailable("grammar:off") and not catalogue.is_unavailable("grammar:core")
    assert catalogue.unavailable_library("grammar:off") == "author.off"
    assert catalogue.retirement("grammar:n3:retired") == {"library_id": "author.n3", "replaced_by": "grammar:n3:c",
                                                          "reason": "merged", "replacement_available": True}
    assert ("undeclared_reference", "author.n3", "grammar:n3:c", "grammar:old") not in catalogue.diagnostics
    accepted = merge.build_effective_catalogue(core_items=core, extensions=[ext],
                                               overrides_accepted={("author.n3", "grammar:shared"),
                                                                   ("author.n3", "grammar:core")})
    assert accepted.library_of("grammar:shared") == "author.n3"
    assert accepted.library_of("grammar:core") == CORE  # accepted but never declared: ignored
    assert accepted.accepted_overrides == {("author.n3", "grammar:shared")}


def test_extension_cycle_is_a_merge_error_naming_the_library():
    core = {"grammar:core": _item("grammar:core")}
    ext = _extension("author.loop", [_item("grammar:x", depends_on=["grammar:y"]), _item("grammar:y", depends_on=["grammar:x"])])
    with pytest.raises(merge.ContentMergeError) as caught:
        merge.build_effective_catalogue(core_items=core, extensions=[ext])
    assert caught.value.library_ids == ("author.loop",) and "grammar:x" in str(caught.value)


def test_core_only_catalogue_is_lazy_and_answers_for_core_ids():
    calls = []
    catalogue = merge.core_only(core_ids=lambda: calls.append(1) or {"grammar:a": {}})
    assert not calls
    assert catalogue.library_of("grammar:a") == CORE and catalogue.library_of("grammar:b") is None
    assert catalogue.missing_required_references("grammar:a") == () and calls == [1]
    catalogue.library_of("grammar:a")
    assert calls == [1]


def test_dependency_frontier_batches_by_family_pool_in_prerequisite_order():
    order = ["kana:a", "pronunciation:r", "word:w1", "word:w2", "word:w3", "grammar:g1", "grammar:g2", "grammar:t"]
    deps = {"word:w2": ["word:w1"], "grammar:g2": ["grammar:g1"], "grammar:t": ["word:w3", "grammar:g2", "kana:a"]}
    limits = merge.frontier_limits(word_batch=2, grammar_batch=1, kana=1)
    result = merge.dependency_frontier(order, deps, targets=["grammar:t"], enrolled=["word:w1"], limits=limits,
                                       family_of=lambda cid: cid.split(":", 1)[0])
    assert result["first"] == ["kana:a", "word:w2", "word:w3", "grammar:g1", "grammar:t"]
    assert result["batches"][1:] == [["pronunciation:r", "grammar:g2"]]
    assert result["planned"] == ["pronunciation:r", "grammar:g2"]
    assert result["counts"] == {"kana": 1, "word": 2, "grammar": 2}
    every = merge.dependency_frontier(order, deps, targets=["grammar:t"], enrolled=[], limits=limits,
                                      family_of=lambda cid: cid.split(":", 1)[0], enroll_all=True)
    assert every["first"] == [*order[:-1], "grammar:t"] and every["planned"] == []
    blocked = merge.dependency_frontier(["word:w1"], {}, targets=["word:t"], enrolled=[], limits={"word": 0},
                                        family_of=lambda cid: "word")
    assert blocked["batches"] == [["word:w1", "word:t"]]


def _manifest(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return rules.validate_manifest(json.loads(archive.read("manifest.json")))


def test_registry_overrides_accepted_shape_and_helper():
    registry = rules.default_registry()
    assert "overrides_accepted" not in rules.validate_registry(registry)
    registry["libraries"].append({"library_id": "author.n3", "library_version": "1", "profile": "extension",
                                  "content_sha256": "a" * 64, "enabled": True,
                                  "installed_at_utc": "2026-11-08T00:00:00+00:00",
                                  "source": {"kind": "file", "name": "n3.zip", "sha256": "b" * 64,
                                             "signature_status": "unsigned"},
                                  "path": "author.n3/1", "state": "ready"})
    registry["priority"].append("author.n3")
    accepted = rules.with_accepted_overrides(registry, "author.n3", ["grammar:demo:desu"])
    assert rules.accepted_overrides(accepted) == {("author.n3", "grammar:demo:desu")}
    assert "overrides_accepted" not in rules.with_accepted_overrides(accepted, "author.n3", [])
    for rows in ([{"library_id": rules.CORE_LIBRARY_ID, "id": "x"}], [{"library_id": "author.zz", "id": "x"}],
                 [{"library_id": "author.n3", "id": "x"}] * 2, [{"library_id": "author.n3"}], "x"):
        broken = copy.deepcopy(registry)
        broken["overrides_accepted"] = rows
        with pytest.raises(rules.ContentLibraryError):
            rules.validate_registry(broken)


def test_precheck_rule_c_blocks_and_upgrade_precheck_counts_each_category():
    from kanamemory.material_review import item_fingerprint
    namespaces = extension_namespaces(conflict=True)
    data = pack_bytes("author.n3", namespaces, manifest_overrides={"revisions": {"grammar:demo:desu": "1"}})
    manifest = _manifest(data)
    installed = {cid: item_fingerprint(item) for cid, item in CORE_ITEMS.items()}
    plain = rules.precheck_pack(manifest, installed_fingerprints=installed, learned_ids=(),
                                installed_library_ids=[rules.CORE_LIBRARY_ID])
    assert plain.revision_errors == () and not plain.blocking_reasons()
    check = rules.precheck_pack(manifest, installed_fingerprints=installed, learned_ids=(),
                                installed_library_ids=[rules.CORE_LIBRARY_ID],
                                installed_revisions={"grammar:demo:desu": "1"})
    assert check.revision_errors == ("grammar:demo:desu",) and "包错误" in check.blocking_reasons()[0]
    bumped = rules.precheck_pack(manifest, installed_fingerprints=installed, learned_ids=(),
                                 installed_library_ids=[rules.CORE_LIBRARY_ID],
                                 installed_revisions={"grammar:demo:desu": "0"})
    assert bumped.revision_errors == ()
    with pytest.raises(rules.ContentLibraryError):
        rules.validate_manifest({**json.loads(json.dumps(manifest)), "revisions": {"word:absent": "1"}})
    old_items = {"grammar:ext:masu": "f" * 64, "grammar:ext:gone": "e" * 64, "grammar:ext:old": "d" * 64,
                 "grammar:ext:retired": "c" * 64, "grammar:demo:desu": item_fingerprint(namespaces["lesson"]["_ITEMS"]["grammar:demo:desu"])}
    upgrade_manifest = _manifest(pack_bytes("author.n3", namespaces, version="20260201", manifest_overrides={
        "retires": [{"id": "grammar:ext:retired", "replaced_by": "grammar:ext:masu", "reason": "merged"}],
        "aliases": {"grammar:ext:old": "grammar:ext:masu"}, "overrides": [{"id": "grammar:demo:desu", "reason": "fix"}]}))
    upgrade = rules.upgrade_precheck(upgrade_manifest, previous_fingerprints=old_items, previous_version="20260102",
                                     learned_ids={"grammar:ext:masu", "grammar:ext:gone", "grammar:ext:retired",
                                                  "grammar:ext:old"})
    assert upgrade.fingerprint_changed_ids == ("grammar:ext:masu",)
    assert upgrade.fingerprint_changed_learned == ("grammar:ext:masu",)
    assert upgrade.added_ids == () and upgrade.removed_ids == ("grammar:ext:gone",)
    assert upgrade.removed_learned == ("grammar:ext:gone",)
    assert upgrade.retired_learned == (("grammar:ext:retired", "grammar:ext:masu"),)
    assert upgrade.aliased_learned == (("grammar:ext:old", "grammar:ext:masu"),)
    assert upgrade.overrides == ("grammar:demo:desu",)
    # grammar:demo:desu is provided by both with the same fingerprint; masu changed; nothing else was there before.
    assert upgrade.unchanged_ids == ("grammar:demo:desu",)
    lines = upgrade.lines()
    assert lines[0] == "与被替换的版本相同 1 项"
    assert lines[1].startswith("指纹变化 1 项（其中已学 1 项") and "不再提供 1 项（其中已学 1 项" in lines[3]
    assert "retires 的项 1 项（其中已学 1 项）" in lines[4] and "别名 1 条" in lines[5]


def test_alias_relations_route_new_enrolment_without_cycles():
    catalogue = {"grammar:new": {"id": "grammar:new"}, "grammar:old": {"id": "grammar:old"}}
    relations = alias_relations({"grammar:old": "grammar:new"}, revision="1")
    assert relations[0]["type"] == "alias" and validate_relations(relations, catalogue)
    with pytest.raises(ValueError):
        validate_relations(alias_relations({"grammar:old": "grammar:new", "grammar:new": "grammar:old"}, revision="1"),
                           catalogue)


def test_long_reverse_dependency_chain_merges_without_recursion():
    """Review B-01: 3000 chained items listed last-first no longer raise RecursionError."""
    count = 3000
    items = [_item(f"grammar:chain:{n}", depends_on=[f"grammar:chain:{n - 1}"] if n else ["grammar:nowhere"])
             for n in reversed(range(count))]
    ext = _extension("author.chain", items, references=[
        {"id": "grammar:nowhere", "kind": "item", "requirement": "required", "note": ""}])
    catalogue = merge.build_effective_catalogue(core_items={"grammar:core": _item("grammar:core")}, extensions=[ext])
    assert catalogue.missing_required_references(f"grammar:chain:{count - 1}") == ("grammar:nowhere",)
    assert len(catalogue.blocked_ids()) == count and catalogue.max_dependency_depth == count - 1
    shallow = merge.build_effective_catalogue(core_items={"grammar:core": _item("grammar:core")}, extensions=[
        _extension("author.two", [_item("grammar:two:b", depends_on=["grammar:two:a"]),
                                  _item("grammar:two:a", depends_on=["grammar:core"])])])
    assert shallow.max_dependency_depth == 2 and shallow.blocked_ids() == frozenset()  # b → a → core
    assert merge.offline_merge_problems(core_items={"grammar:core": {}}, extensions=[ext], max_depth=count) == ()
    deep = merge.offline_merge_problems(core_items={"grammar:core": {}}, extensions=[ext])
    assert len(deep) == 1 and "过深" in deep[0] and str(count - 1) in deep[0]


def test_offline_merge_problems_reports_cycles_and_failures_as_blocking():
    loop = _extension("author.loop", [_item("grammar:x", depends_on=["grammar:y"]),
                                      _item("grammar:y", depends_on=["grammar:x"])])
    problems = merge.offline_merge_problems(core_items={}, extensions=[loop])
    assert len(problems) == 1 and "成环" in problems[0] and "grammar:x" in problems[0]
    broken = merge.ExtensionLibrary("author.broken", {"provides": {"items": 5}}, {})
    assert merge.offline_merge_problems(core_items={}, extensions=[broken]) == ("离线合并失败：TypeError",)
