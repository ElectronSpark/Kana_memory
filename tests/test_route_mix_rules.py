"""Main-route mix rules (design 11 section 16, M2-M9) on small synthetic trees; no course content."""
from collections import Counter
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from kanamemory.route_mix import placement
from kanamemory.route_mix.params import ALGORITHM_VERSION, MixParams, switched_kinds
from kanamemory.route_mix.placement import RULES_FIXED, RULES_MOVED, runs, section_of
from kanamemory.route_mix.plan import (MixPlan, apply_plan, assign_units, plan_key, plan_mix,
                                       same_apart_from_switches, snapshot, tree_digest)
from kanamemory.route_mix.readiness import Readiness, ReadinessCounter, choice_key, is_choice_key
from kanamemory.route_mix.sections import _digest, split_library_sections
from kanamemory.route_mix.sources import library_entries, overridden_entries, route_library_of

LIB = "library.test"


def entry(cid, depends=()):
    family = "word" if cid.startswith("word:") else "grammar"
    domain = "pronunciation" if cid.startswith("pronunciation:") else None
    return {"id": cid, "content_id": cid, "kind": "learning", "family": family, "domain": domain,
            "title": cid.split(":")[-1], "depends_on": list(depends)}


def build(*stages, optional=()):
    """stages: (stage id, [(unit id, [(section name, [entry ids or (id, depends)])])]); section
    depends_on from the entries, as assemble_study_path computes it."""
    tree = {"version": "fixture", "scope": "fixture", "stages": []}
    for stage_id, units in stages:
        stage = {"id": stage_id, "title": stage_id.upper(), "optional": stage_id in optional, "units": []}
        for unit_id, sections in units:
            unit = {"id": unit_id, "title": "T " + unit_id, "sections": []}
            for name, entries in sections:
                rows = [entry(*row) if isinstance(row, tuple) else entry(row) for row in entries]
                unit["sections"].append({
                    "id": "section:" + name, "title": name, "entries": rows, "depends_on": [],
                    "optional": stage_id in optional, "stage_id": stage_id, "stage_title": stage["title"],
                    "unit_id": unit_id, "unit_title": unit["title"],
                    "component_section_ids": ["section:" + name], "topics": [name]})
            stage["units"].append(unit)
        tree["stages"].append(stage)
    sections = [s for st in tree["stages"] for u in st["units"] for s in u["sections"]]
    owner = {e["id"]: s["id"] for s in sections for e in s["entries"]}
    order = {s["id"]: i for i, s in enumerate(sections)}
    for s in sections:
        s["depends_on"] = sorted({owner[d] for e in s["entries"] for d in e["depends_on"]
                                  if d in owner and owner[d] != s["id"]}, key=order.__getitem__)
    return tree


def flat(tree, stage_id=None):
    return [s for st in tree["stages"] if stage_id in (None, st["id"]) for u in st["units"] for s in u["sections"]]


def ids(tree, stage_id=None):
    return [s["id"].removeprefix("section:") for s in flat(tree, stage_id)]


def templates(*rows):
    """((sha, requirement ids), ...) for one ability."""
    return [(f"{index:064x}", list(requires)) for index, requires in enumerate(rows)]


def apply(tree, plan, *, mixable=("stage:one",), providers=None):
    return apply_plan(tree, plan, mixable=mixable, providers=providers or {})


def mixed(tree, table, *, mixable=("stage:one",), library_of=None, params=MixParams(), overridden=frozenset()):
    plan = plan_mix(snapshot(tree), Readiness(table), mixable=mixable, library_of=library_of or {},
                    key={"test": 1}, params=params, overridden=overridden)
    result = apply(tree, plan, mixable=mixable, providers=library_of)
    assert result is not None
    return plan, result


def alternating_case():
    """Grammar g1..g4 first, then word sections w1..w4 (two words each); w_i needs g_i."""
    grammar = [(f"g{i}", [f"grammar:g{i}"]) for i in range(1, 5)]
    words = [(f"w{i}", [f"word:w{i}a", f"word:w{i}b"]) for i in range(1, 5)]
    tree = build(("stage:zero", [("unit:k", [("k", ["grammar:k"])])]),
                 ("stage:one", [("unit:g", grammar), ("unit:w", words)]))
    table = {f"word:w{i}{x}": {"use": templates([f"grammar:g{i}"], [f"grammar:g{i}"])}
             for i in range(1, 5) for x in "ab"}
    return tree, table


# --- M2 identities, M3 skeleton, M4 only earlier -------------------------------------------------

def test_words_alternate_with_grammar_and_identities_stay():
    tree, table = alternating_case()
    before = deepcopy(tree)
    plan, result = mixed(tree, table)
    assert ids(result, "stage:one") == ["g1", "w1", "g2", "w2", "g3", "w3", "g4", "w4"]
    assert tree == before  # the derivation tree is never changed
    old = {s["id"]: s for s in flat(tree)}
    for section in flat(result):
        original = old[section["id"]]
        for name in ("id", "title", "entries", "depends_on", "component_section_ids", "topics", "optional"):
            assert section[name] == original[name]
    assert Counter(s["id"] for s in flat(result)) == Counter(old.keys())
    assert result["stages"][0] is tree["stages"][0]  # stage 0 is never mixed
    assert runs([section_of(s, {}) for s in flat(result, "stage:one")]) == (2, 1)


def test_skeleton_order_is_kept_and_movable_sections_only_move_earlier():
    tree, table = alternating_case()
    # w3 needs nothing: it may go anywhere before its own place, never after it.
    table["word:w3a"] = table["word:w3b"] = {"use": templates([], [])}
    _, result = mixed(tree, table)
    order = ids(result, "stage:one")
    assert [x for x in order if x.startswith("g")] == ["g1", "g2", "g3", "g4"]
    original = ids(tree, "stage:one")
    for name in order:
        if name.startswith("w"):
            skeleton_before = lambda sequence: sum(1 for x in sequence[:sequence.index(name)] if x.startswith("g"))
            assert skeleton_before(order) <= skeleton_before(original)


# --- M5 floor, section order, hard prerequisites ------------------------------------------------

def test_floor_is_min_of_derivation_count_and_three():
    grammar = [(f"g{i}", [f"grammar:g{i}"]) for i in range(1, 5)]
    tree = build(("stage:one", [("unit:g", grammar),
                                ("unit:w", [("many", ["word:many"]), ("few", ["word:few"])])]))
    table = {
        # 5 met templates where it stands now: floor 3, of which 3 need nothing -> it may go first.
        "word:many": {"use": templates([], [], [], ["grammar:g3"], ["grammar:g4"])},
        # 2 templates, both need g2: floor 2 -> not before g2.
        "word:few": {"use": templates(["grammar:g2"], ["grammar:g2", "grammar:g1"])},
    }
    _, result = mixed(tree, table)
    order = ids(result)
    assert order.index("few") > order.index("g2")
    assert order.index("many") < order.index("g2")
    readiness = Readiness(table)
    position = {e["id"]: i for i, e in enumerate(e for s in flat(result) for e in s["entries"])}
    old = {e["id"]: i for i, e in enumerate(e for s in flat(tree) for e in s["entries"])}
    for item in table:
        then = readiness.counts(item, lambda r, at=old[item]: old.get(r, at) < at)
        now = readiness.counts(item, lambda r, at=position[item]: position.get(r, at) < at)
        assert all(now[a] >= min(then[a], 3) for a in then)


def group_case():
    """Stage one: g1, g2, then the word w; w's one context task needs g2, its three other "use" tasks
    need nothing."""
    tree = build(("stage:one", [("unit:g", [("g1", ["grammar:g1"]), ("g2", ["grammar:g2"])]),
                                ("unit:w", [("w", ["word:w"])])]))
    return tree, templates(["grammar:g2"], [], [], [])


def test_the_default_switches_keep_a_question_among_the_switched_on_kinds():
    # Default switches: the context task (needs g2) and three other "use" tasks are all switched on, so
    # the choice key holds all four; before g2 three of them are met: w moves (variant C = no choice floor).
    tree, rows = group_case()
    plan, result = mixed(tree, {"word:w": {"use": rows}})
    assert ids(result).index("w") < ids(result).index("g2")  # by ability alone it moves
    default = {"use": rows, choice_key("use"): rows}
    plan, result = mixed(tree, {"word:w": default})
    assert ids(result).index("w") < ids(result).index("g2")
    assert MixParams().choice_floor == 1 and MixParams().key()["choice_floor"] == 1


def test_a_learner_with_one_kind_on_keeps_a_question_of_that_kind():
    # Only the context task is switched on: before g2 the ability keeps 3 of its 4 questions (its floor),
    # but none of this learner's kinds; that learner would meet w with the unlearned g2, so w may not
    # stand before g2.
    tree, rows = group_case()
    only_context = {"use": rows, choice_key("use"): rows[:1]}
    plan, result = mixed(tree, {"word:w": only_context})
    assert ids(result) == ["g1", "g2", "w"]
    # one of the switched-on kinds needs nothing: that one question is the floor, w moves
    plan, result = mixed(tree, {"word:w": {"use": rows, choice_key("use"): rows[:2]}})
    assert ids(result).index("w") < ids(result).index("g2")


def test_the_choice_floor_binds_only_where_the_old_position_had_a_question_of_a_switched_on_kind():
    tree, rows = group_case()
    # min(derivation count, 1): the switched-on kind's only question can never be met here (its
    # requirement is off the route), so at the old position it had none and binds nothing
    free, never = templates([], [], []), [("f" * 64, ["grammar:never"])]
    plan, result = mixed(tree, {"word:w": {"use": free + never, choice_key("use"): never}})
    assert ids(result).index("w") < ids(result).index("g2")
    # a switched-on kind with no template for this item: no choice key at all, nothing binds
    plan, result = mixed(tree, {"word:w": {"use": rows}})
    assert ids(result).index("w") < ids(result).index("g2")
    # the floor is per ability: a choice key of another ability does not bind "use"
    other = {"use": rows, choice_key("understanding"): templates([]), "understanding": templates([])}
    plan, result = mixed(tree, {"word:w": other})
    assert ids(result).index("w") < ids(result).index("g2")
    # the ability floor min(d, 3) still binds whatever the switches: three "use" questions need g2
    hard = templates(["grammar:g2"], ["grammar:g2"], ["grammar:g2"], [])
    plan, result = mixed(tree, {"word:w": {"use": hard, choice_key("use"): hard[3:]}})
    assert ids(result) == ["g1", "g2", "w"]


def test_the_switched_on_kinds_are_plain_data_in_the_plan_key():
    kinds = switched_kinds(["vocab_recall", "vocab_meaning", "vocab_recall", 7])
    assert kinds == ("vocab_meaning", "vocab_recall")
    assert switched_kinds({"vocab_usage"}) == ("vocab_usage",)
    assert switched_kinds("vocab_meaning") == () and switched_kinds(None) == ()  # a malformed setting
    params = MixParams(kinds=kinds)
    assert params.key()["kinds"] == ["vocab_meaning", "vocab_recall"] and "groups" not in params.key()
    assert params.key() != MixParams().key()  # another set of switches: another plan
    assert params.key() == MixParams(kinds=switched_kinds({"vocab_recall", "vocab_meaning"})).key()
    assert json.loads(json.dumps(params.key())) == params.key()  # what the plan file stores compares equal
    for bad in (["vocab_meaning"], ("vocab_recall", "vocab_meaning"), ("x", "x"), (1,)):
        with pytest.raises(ValueError):
            MixParams(kinds=bad)
    assert is_choice_key(choice_key("use")) and not is_choice_key("use")


def test_two_plan_keys_are_the_same_apart_from_the_switches_only_when_all_else_is_equal():
    def key(kinds=("vocab_meaning",), digest="t" * 64, content="x", **params):
        return plan_key(MixParams(kinds=kinds, **params), digest, {"core": [content]})
    one, other = key(), key(kinds=("vocab_meaning", "vocab_recall"))
    assert one != other and same_apart_from_switches(one, other) and same_apart_from_switches(one, one)
    assert same_apart_from_switches(json.loads(json.dumps(one)), other)  # as the plan file gives it back
    for different in (key(floor=2), key(algorithm=ALGORITHM_VERSION + 1), key(digest="u" * 64), key(content="y"),
                      {}, {"params": None}, {**one, "params": {"kinds": []}}):
        assert not same_apart_from_switches(one, different) and not same_apart_from_switches(different, one)


def test_entries_of_one_section_count_their_earlier_entries_and_keep_hard_prerequisites():
    grammar = [(f"g{i}", [f"grammar:g{i}"]) for i in range(1, 4)]
    words = [("pair", ["word:a", "word:b"]), ("hard", [("word:c", ["grammar:g2"])])]
    tree = build(("stage:one", [("unit:g", grammar), ("unit:w", words)]))
    table = {"word:a": {"use": templates([])},
             "word:b": {"use": templates(["word:a"], ["word:a"])},  # met only through the earlier entry
             "word:c": {"use": templates([])}}                      # free, but hard-depends on g2
    _, result = mixed(tree, table)
    order = ids(result)
    assert order.index("pair") < order.index("g2")  # placeable at all only through its own first entry
    assert order.index("hard") > order.index("g2")


def test_counter_matches_counts_and_counts_with_earlier_entries():
    readiness = Readiness({"word:x": {"use": [("a" * 64, ["word:p", "grammar:q"]), ("b" * 64, ["word:p"]),
                                              ("a" * 64, ["grammar:r"])]}})
    local = ["grammar:q", "word:p", "grammar:r"]
    counter = ReadinessCounter(readiness, ["word:x"], local, lambda r: False)
    assert counter.count_of("word:x", "use") == 0
    assert counter.count_with("word:x", "use", {1}) == 1
    copy = counter.copy()
    counter.learn(1)
    assert counter.count_of("word:x", "use") == 1 and copy.count_of("word:x", "use") == 0
    counter.learn(2)  # the same fingerprint through its other row: still two questions at most
    assert counter.count_of("word:x", "use") == 2
    counter.learn(0)
    assert counter.count_of("word:x", "use") == 2
    known = {"word:p", "grammar:q", "grammar:r"}
    assert readiness.counts("word:x", known.__contains__) == {"use": 2}


# --- M4 reading rules: fixed or moved, whichever scores better -------------------------------------

def rules_case():
    grammar = [(f"g{i}", [f"grammar:g{i}"]) for i in range(1, 3)]
    words = [(f"w{i}", [f"word:w{i}{x}" for x in "abcd"]) for i in range(1, 5)]
    rules = [(f"r{i}", [f"pronunciation:r{i}"]) for i in range(1, 4)]
    tree = build(("stage:one", [("unit:g", grammar), ("unit:w", words), ("unit:r", rules)]))
    table = {f"word:w{i}{x}": {"use": templates(["grammar:g2"])} for i in range(1, 5) for x in "abcd"}
    return tree, table


def test_reading_rules_move_between_words_when_that_scores_better():
    tree, table = rules_case()
    plan, result = mixed(tree, table)
    stage = plan.summary["stages"]["stage:one"]
    assert stage["variant"] == RULES_MOVED
    longest, _ = runs([section_of(s, {}) for s in flat(result)])
    assert longest == 4  # 16 words that need the last grammar item, split by the three rules
    assert ids(result)[:2] == ["g1", "g2"]


def test_reading_rules_stay_when_moving_them_gains_nothing():
    grammar = [("g1", ["grammar:g1"]), ("g2", ["grammar:g2"])]
    words = [("w1", ["word:w1a", "word:w1b"]), ("w2", ["word:w2a", "word:w2b"])]
    tree = build(("stage:one", [("unit:g", grammar), ("unit:w", words), ("unit:r", [("r1", ["pronunciation:r1"])])]))
    table = {"word:w1a": {"use": templates(["grammar:g1"])}, "word:w1b": {"use": templates(["grammar:g1"])},
             "word:w2a": {"use": templates(["grammar:g2"])}, "word:w2b": {"use": templates(["grammar:g2"])},
             "pronunciation:r1": {"understanding": templates(["word:w2b"])}}  # the rule needs the last word
    plan, result = mixed(tree, table)
    assert plan.summary["stages"]["stage:one"]["variant"] == RULES_FIXED  # a tie: the rule stays
    assert ids(result) == ["g1", "w1", "g2", "w2", "r1"]


# --- M6 determinism and the score ------------------------------------------------------------------

def test_plan_is_deterministic():
    tree, table = rules_case()
    first = plan_mix(snapshot(tree), Readiness(table), mixable=["stage:one"], library_of={}, key={})
    second = plan_mix(snapshot(deepcopy(tree)), Readiness(dict(reversed(table.items()))),
                      mixable=["stage:one"], library_of={}, key={})
    assert first.stages == second.stages and first.summary == second.summary


def test_the_chosen_limit_has_the_best_score_of_the_search():
    grammar = [(f"g{i}", [f"grammar:g{i}a", f"grammar:g{i}b"]) for i in range(1, 7)]
    words = [(f"w{i}", [f"word:w{i}{x}" for x in "abc"]) for i in range(1, 9)]
    tree = build(("stage:one", [("unit:g", grammar), ("unit:w", words)]))
    table = {f"word:w{i}{x}": {"use": templates([f"grammar:g{min(6, 1 + i // 2)}b"])}
             for i in range(1, 9) for x in "abc"}
    params = MixParams(search_width=6)
    sections = tuple(section_of(s, {}) for s in flat(tree))
    known = frozenset()
    floors = {item: (("use", 1),) for item in table}
    mask = [s.kind == placement.WORD for s in sections]
    key, order, chosen, lower = placement._place_variant(
        sections, mask, {"readiness": Readiness(table), "prereqs": {}, "floors": floors}, known.__contains__, params)
    placer = placement._StagePlacer(sections, mask, Readiness(table), {}, floors, known.__contains__)
    early = placer.windows()
    nonword = [0]
    for i in placer.fixed:
        nonword.append(nonword[-1] + len(sections[i].entries) - sections[i].word_count)
    total = sum(placer.words[i] for i in placer.movable)
    scores = []
    for limit in range(lower, lower + params.search_width + 1):
        gaps = placer.sweep(limit, True, early, nonword, total)
        if gaps is not None:
            word, other = runs([sections[i] for i in placer.order(gaps)])
            scores.append((max(word * params.nonword_target, other * params.word_target), word, limit))
    assert key == min(scores) and chosen == min(scores)[2]
    assert placer.sweep(lower - 1, False, early, nonword, total) is None  # L0 is the smallest that fits


# --- M7 units ------------------------------------------------------------------------------------

def test_units_follow_the_skeleton_and_the_tail_keeps_whole_units():
    grammar_a = [("g1", ["grammar:g1"]), ("g2", ["grammar:g2"])]
    grammar_b = [("g3", ["grammar:g3"])]
    early = [("e1", ["word:e1"])]
    tail_whole = [("t1", ["word:t1"]), ("t2", ["word:t2"])]
    tail_part = [("p1", ["word:p1"]), ("p2", ["word:p2"])]
    tree = build(("stage:one", [("unit:a", grammar_a), ("unit:b", grammar_b), ("unit:early", early),
                                ("unit:whole", tail_whole), ("unit:part", tail_part)]))
    late = templates(["grammar:g3"])
    table = {"word:e1": {"use": templates(["grammar:g1"])}, "word:t1": {"use": late}, "word:t2": {"use": late},
             "word:p1": {"use": templates([])}, "word:p2": {"use": late}}
    _, result = mixed(tree, table, params=MixParams(word_target=1))
    stage = result["stages"][0]
    units = {s["id"].removeprefix("section:"): u["id"] for u in stage["units"] for s in u["sections"]}
    assert units["g1"] == units["g2"] == "unit:a" and units["g3"] == "unit:b"
    assert units["e1"] in {"unit:a", "unit:b"} and units["p1"] in {"unit:a", "unit:b"}  # before a skeleton section
    assert units["t1"] == units["t2"] == "unit:whole"   # the whole unit stands after the last skeleton section
    assert units["p2"] == "unit:whole"                  # its own unit is split: it joins the unit before it
    assert "unit:early" not in [u["id"] for u in stage["units"]]  # empty units disappear
    seen = [u["id"] for u in stage["units"]]
    assert len(seen) == len(set(seen))
    for unit in stage["units"]:
        assert all(s["unit_id"] == unit["id"] and s["unit_title"] == unit["title"] for s in unit["sections"])


def test_assign_units_joins_the_previous_unit_after_a_kept_one():
    S = placement.Section
    rows = [S("a", "unit:g", ("grammar:a",), (False,), placement.GRAMMAR, None),
            S("x", "unit:x", ("word:x",), (True,), placement.WORD, None),
            S("y1", "unit:y", ("word:y1",), (True,), placement.WORD, None),
            S("x2", "unit:x2", ("word:x2",), (True,), placement.WORD, None)]
    units = assign_units(rows + [S("y2", "unit:y", ("word:y2",), (True,), placement.WORD, None)],
                         {"a"}, {})
    assert units == ["unit:g", "unit:x", "unit:x", "unit:x2", "unit:x2"]


# --- M8(a) cutting an extension library's sections ------------------------------------------------

def library_tree():
    words = [f"word:lib{i}" for i in range(10)]
    return build(("stage:zero", [("unit:k", [("lib-zero", [f"word:z{i}" for i in range(10)])])]),
                 ("stage:one", [("unit:g", [("lib-grammar", ["grammar:l1", ("grammar:l2", ["grammar:l1"]),
                                                             "grammar:l3"]),
                                            ("core", ["grammar:c1", "grammar:c2"])]),
                                ("unit:w", [("lib-words", words), ("mixed", ["word:core", "word:libx"]),
                                            ("user", [("word:u", ["word:lib9", "grammar:l2"])])])]),
                 ("stage:ref", [("unit:r", [("lib-ref", [f"word:r{i}" for i in range(10)])])]),
                 optional={"stage:ref"})


def provider(cid):
    name = cid.split(":", 1)[1]
    return LIB if name.startswith(("lib", "l1", "l2", "l3", "z", "r")) else None


def test_library_sections_are_cut_with_new_ids_titles_and_dependencies():
    tree = library_tree()
    before = deepcopy(tree)
    result = split_library_sections(tree, provider, fixed_stage_ids=("stage:zero",))
    assert tree == before
    names = ids(result)
    chunks = ["section:" + _digest(f"section:lib-words|{n}") for n in (1, 2, 3)]
    grammar = ["section:" + _digest(f"section:lib-grammar|{n}") for n in (1, 2, 3)]
    assert [s["id"] for s in flat(result, "stage:one")] == [*grammar, "section:core", *chunks,
                                                           "section:mixed", "section:user"]
    by_id = {s["id"]: s for s in flat(result)}
    assert [len(by_id[c]["entries"]) for c in chunks] == [4, 4, 2]
    assert [by_id[c]["title"] for c in chunks] == ["lib-words（1）", "lib-words（2）", "lib-words（3）"]
    assert by_id[chunks[0]]["component_section_ids"] == [chunks[0]] and by_id[chunks[0]]["topics"] == ["lib-words（1）"]
    assert by_id[grammar[1]]["depends_on"] == [grammar[0]]
    assert by_id["section:user"]["depends_on"] == [grammar[1], chunks[2]]
    # core, several-library, stage-0 and optional sections stay as they were (the same objects)
    old = {s["id"]: s for s in flat(tree)}
    for name in ("core", "mixed", "lib-zero", "lib-ref"):
        assert by_id["section:" + name] is old["section:" + name]
    assert "lib-words" not in names and "lib-grammar" not in names
    assert split_library_sections(tree, None) is tree
    assert split_library_sections(tree, lambda cid: None) is tree
    assert _digest("x") == __import__("hashlib").sha256(b"x").hexdigest()[:12]


# --- M8(b) library practice grammar moves to the library's last word stage -------------------------

def defer_case(*, hard=False, user_needs=False, last_stage_grammar=False):
    lib_entry = ("grammar:lpractice", []) if not last_stage_grammar else None
    stage_one = [("unit:g1", [("core1", ["grammar:c1"])] + ([("lpractice", [lib_entry])] if lib_entry else []))]
    user = ("word:lib-user", ["grammar:lpractice"] if hard else [])
    stage_one.append(("unit:w1", [("libword1", [user])]))
    stage_two = [("unit:g2", [("core2", ["grammar:c2"])] + ([("lpractice", ["grammar:lpractice"])]
                                                            if last_stage_grammar else [])),
                 ("unit:w2", [("libword2", ["word:lib-a", "word:lib-b"]), ("libword3", ["word:lib-c"])])]
    tree = build(("stage:one", stage_one), ("stage:two", stage_two))
    table = {"word:lib-a": {"use": templates([])}, "word:lib-b": {"use": templates([])},
             "word:lib-c": {"use": templates([])},
             "word:lib-user": {"use": templates(["grammar:lpractice"] if user_needs else [])}}
    providers = {e["id"]: LIB for s in flat(tree) for e in s["entries"] if ":l" in e["id"]}
    return tree, table, providers


def test_library_practice_grammar_moves_into_the_libraries_last_word_stage():
    tree, table, providers = defer_case()
    plan, result = mixed(tree, table, mixable=("stage:one", "stage:two"), library_of=providers)
    assert plan.summary["deferred"] == 1
    assert "lpractice" not in ids(result, "stage:one")
    two = ids(result, "stage:two")
    assert "lpractice" in two
    moved = next(s for s in flat(result) if s["id"] == "section:lpractice")
    assert moved["stage_id"] == "stage:two" and moved["unit_id"] in {u["id"] for u in result["stages"][1]["units"]}
    assert {s["id"] for s in flat(result)} == {s["id"] for s in flat(tree)}
    assert_deferred_keep_their_questions(tree, result, table)


def assert_deferred_keep_their_questions(tree, result, table):
    """M9: a deferred item has at least its derivation count of gated questions where it now stands."""
    readiness = Readiness(table)
    old = {e["id"]: i for i, e in enumerate(e for s in flat(tree) for e in s["entries"])}
    new = {e["id"]: i for i, e in enumerate(e for s in flat(result) for e in s["entries"])}
    stage_of = {e["id"]: st["id"] for st in tree["stages"] for u in st["units"] for s in u["sections"]
                for e in s["entries"]}
    moved = {e["id"] for st in result["stages"] for u in st["units"] for s in u["sections"] for e in s["entries"]
             if stage_of[e["id"]] != st["id"]}
    assert moved
    for item in moved & set(table):
        then = readiness.counts(item, lambda r, at=old[item]: old.get(r, at) < at)
        now = readiness.counts(item, lambda r, at=new[item]: new.get(r, at) < at)
        assert all(now[a] >= then[a] for a in then), item


def breaker_case(word_sections=6, words_per_section=4, deferred=2, needs=None):
    """Stage one: core grammar and the library's practice grammar ld1.. (nobody needs them); stage two:
    core grammar, then the library's words. ``needs``: (word section number, deferred number) -> the
    first word of that section has one template, which needs that deferred item."""
    practice = [(f"d{k}", [f"grammar:ld{k}"]) for k in range(1, deferred + 1)]
    words = [(f"s{k}", [f"word:lib{k}x{j}" for j in range(words_per_section)]) for k in range(1, word_sections + 1)]
    tree = build(("stage:one", [("unit:g1", [("core1", ["grammar:c1"]), *practice])]),
                 ("stage:two", [("unit:g2", [("core2", ["grammar:c2"])]), ("unit:lib", words)]))
    table = {e["id"]: {"use": templates([])} for s in flat(tree) for e in s["entries"] if e["id"].startswith("word:")}
    table.update({f"grammar:ld{k}": {"understanding": templates([])} for k in range(1, deferred + 1)})
    for (section, item) in (needs or ()):
        table[f"word:lib{section}x0"] = {"use": templates([f"grammar:ld{item}"])}
    providers = {e["id"]: LIB for s in flat(tree) for e in s["entries"] if ":l" in e["id"]}
    return tree, table, providers


def test_deferred_library_grammar_is_spread_between_words_at_an_even_pace():
    tree, table, providers = breaker_case()
    plan, result = mixed(tree, table, mixable=("stage:one", "stage:two"), library_of=providers)
    assert plan.summary["deferred"] == 2
    # step 1 spreads the words over the stage's own grammar; step 2 puts d1 and d2 between two words
    # each, about a third and two thirds into the stage (nothing needs them sooner)
    assert ids(result, "stage:two") == ["s1", "s2", "core2", "s3", "d1", "s4", "d2", "s5", "s6"]
    word_run, other_run = runs([section_of(s, providers) for s in flat(result, "stage:two")])
    assert word_run == 8 and other_run == 1
    assert_deferred_keep_their_questions(tree, result, table)


def test_deferred_library_grammar_comes_before_the_words_that_need_it_without_a_block():
    tree, table, providers = breaker_case(word_sections=8, deferred=3, needs=[(7, 3), (8, 3)])
    plan, result = mixed(tree, table, mixable=("stage:one", "stage:two"), library_of=providers)
    assert plan.summary["deferred"] == 3
    order = ids(result, "stage:two")
    assert order == ["s1", "s2", "core2", "s3", "d1", "s4", "d2", "s5", "s6", "d3", "s7", "s8"]
    places = [order.index(name) for name in ("d1", "d2", "d3")]
    assert places == sorted(places)                                    # their order is kept
    assert all(order[at - 1].startswith("s") and order[at + 1].startswith("s") for at in places)  # no block
    assert places[-1] < order.index("s7") and places[-1] < order.index("s8")
    assert_deferred_keep_their_questions(tree, result, table)


def test_deferred_sections_due_before_a_word_that_needs_the_last_are_spread_before_it():
    tree, table, providers = breaker_case(word_sections=6, deferred=4, needs=[(6, 4)])
    plan, result = mixed(tree, table, mixable=("stage:one", "stage:two"), library_of=providers)
    assert plan.summary["deferred"] == 4
    order = ids(result, "stage:two")
    assert order == ["s1", "d1", "s2", "d2", "s3", "core2", "s4", "d3", "s5", "d4", "s6"]
    assert_deferred_keep_their_questions(tree, result, table)


@pytest.mark.parametrize("case, expected", [
    # s4 needs d3 well before d3's pace: d1 ... d3 are spread evenly over the places up to s4
    ({"word_sections": 8, "words_per_section": 2, "deferred": 3, "needs": [(4, 3)]},
     ["s1", "d1", "s2", "d2", "s3", "d3", "s4", "core2", "s5", "s6", "s7", "s8"]),
    # four of them before s3, two places between words: two to a place, no more
    ({"word_sections": 8, "words_per_section": 2, "deferred": 4, "needs": [(3, 4)]},
     ["s1", "d1", "d2", "s2", "d3", "d4", "s3", "s4", "core2", "s5", "s6", "s7", "s8"]),
])
def test_a_group_whose_deadline_comes_before_its_pace_is_spread_evenly_up_to_it(case, expected):
    tree, table, providers = breaker_case(**case)
    plan, result = mixed(tree, table, mixable=("stage:one", "stage:two"), library_of=providers)
    assert plan.summary["deferred"] == case["deferred"]
    assert ids(result, "stage:two") == expected
    assert_deferred_keep_their_questions(tree, result, table)


def test_a_library_word_that_needs_a_deferred_item_stays_after_it():
    tree, table, providers = breaker_case(needs=[(1, 2)])
    plan, result = mixed(tree, table, mixable=("stage:one", "stage:two"), library_of=providers)
    assert plan.summary["deferred"] == 2  # a word of the last word stage is left to the M5 check
    order = ids(result, "stage:two")
    assert order.index("d1") < order.index("d2") < order.index("s1")
    assert_deferred_keep_their_questions(tree, result, table)


def test_an_unresolved_word_section_of_the_new_stage_is_checked_as_a_passed_entry():
    # Item 1: a word section with a hard prerequisite off the route is skeleton (not movable): the
    # placement does not check it, so the deferral must, and here it would lose its only question.
    def case(unresolved):
        tree, table, providers = defer_case()
        stuck = {"id": "section:stuck", "title": "stuck", "entries": [entry("word:lib-stuck")], "depends_on": [],
                 "optional": False, "stage_id": "stage:two", "stage_title": "STAGE:TWO", "unit_id": "unit:w2",
                 "unit_title": "T unit:w2", "component_section_ids": ["section:stuck"], "topics": ["stuck"]}
        if unresolved:
            stuck["unresolved_dependencies"] = ["word:off-route"]
        tree["stages"][1]["units"][1]["sections"].append(stuck)
        table["word:lib-stuck"] = {"use": templates(["grammar:lpractice"])}
        providers["word:lib-stuck"] = LIB
        return tree, table, providers
    tree, table, providers = case(unresolved=True)
    plan, result = mixed(tree, table, mixable=("stage:one", "stage:two"), library_of=providers)
    assert plan.summary["deferred"] == 0 and "lpractice" in ids(result, "stage:one")
    tree, table, providers = case(unresolved=False)  # a movable word: left to the placement's M5 check
    plan, result = mixed(tree, table, mixable=("stage:one", "stage:two"), library_of=providers)
    assert plan.summary["deferred"] == 1
    order = ids(result, "stage:two")
    assert order.index("lpractice") < order.index("stuck")
    assert_deferred_keep_their_questions(tree, result, table)


def three_stage_case(middle_table):
    """Library grammar in stage one, a library word in stage two (``middle_table``: its readiness, or
    None for none), the library's last word stage three."""
    tree = build(("stage:one", [("unit:g1", [("core1", ["grammar:c1"]), ("lpractice", ["grammar:lpractice"])])]),
                 ("stage:two", [("unit:g2", [("core2", ["grammar:c2"])]), ("unit:w2", [("middle", ["word:lib-mid"])])]),
                 ("stage:three", [("unit:g3", [("core3", ["grammar:c3"])]),
                                  ("unit:w3", [("last", ["word:lib-last1", "word:lib-last2"])])]))
    table = {"word:lib-last1": {"use": templates([])}, "word:lib-last2": {"use": templates([])}}
    if middle_table is not None:
        table["word:lib-mid"] = middle_table
    providers = {e["id"]: LIB for s in flat(tree) for e in s["entries"] if ":l" in e["id"]}
    return tree, table, providers


@pytest.mark.parametrize("middle, deferred", [
    ({"use": templates(["grammar:lpractice"])}, 0),   # a passed item whose question needs it: blocks
    (None, 0),                                         # a passed library item the table cannot see: blocks
    ({"use": templates([])}, 1),                       # a passed item that does not need it: moves
])
def test_an_item_between_the_stages_that_needs_the_deferred_one_blocks_the_deferral(middle, deferred):
    # Item 2: every entry passed on the way to the last word stage is checked by construction.
    tree, table, providers = three_stage_case(middle)
    mixable = ("stage:one", "stage:two", "stage:three")
    plan, result = mixed(tree, table, mixable=mixable, library_of=providers)
    assert plan.summary["deferred"] == deferred
    assert ("lpractice" in ids(result, "stage:three")) == bool(deferred)


def test_a_stage_that_only_loses_a_deferred_section_keeps_its_derivation_units():
    # Item 10: stage one loses lpractice and is otherwise as derived; a re-assignment (M7) would move
    # w0 into the unit of core1, the skeleton section after it.
    tree = build(("stage:one", [("unit:a", [("w0", ["word:w0"])]),
                                ("unit:b", [("core1", ["grammar:c1"]), ("lpractice", ["grammar:lpractice"])])]),
                 ("stage:two", [("unit:g2", [("core2", ["grammar:c2"])]),
                                ("unit:w2", [("libword", ["word:lib-a", "word:lib-b"])])]))
    table = {"word:w0": {"use": templates([])}, "word:lib-a": {"use": templates([])},
             "word:lib-b": {"use": templates([])}}
    providers = {e["id"]: LIB for s in flat(tree) for e in s["entries"] if ":l" in e["id"]}
    plan, result = mixed(tree, table, mixable=("stage:one", "stage:two"), library_of=providers)
    assert plan.summary["deferred"] == 1
    one = result["stages"][0]
    assert [(u["id"], [s["id"] for s in u["sections"]]) for u in one["units"]] == [
        ("unit:a", ["section:w0"]), ("unit:b", ["section:core1"])]
    assert plan.summary["stages"]["stage:one"]["changed"] is True


def test_deferred_sections_no_run_needs_stand_at_the_end_of_the_stage_in_their_order():
    tree, table, providers = breaker_case(word_sections=1, words_per_section=2, deferred=3)
    plan, result = mixed(tree, table, mixable=("stage:one", "stage:two"), library_of=providers)
    assert plan.summary["deferred"] == 3
    assert ids(result, "stage:two") == ["core2", "s1", "d1", "d2", "d3"]
    two = result["stages"][1]
    units = {s["id"]: u["id"] for u in two["units"] for s in u["sections"]}
    assert set(units.values()) <= {u["id"] for u in tree["stages"][1]["units"]}  # never an earlier stage's unit
    assert_deferred_keep_their_questions(tree, result, table)


@pytest.mark.parametrize("case", [{"hard": True}, {"user_needs": True}, {"last_stage_grammar": True}])
def test_library_grammar_stays_when_needed_passed_items_lose_questions_or_already_last(case):
    tree, table, providers = defer_case(**case)
    plan, result = mixed(tree, table, mixable=("stage:one", "stage:two"), library_of=providers)
    assert plan.summary["deferred"] == 0
    home = "stage:two" if case.get("last_stage_grammar") else "stage:one"
    assert "lpractice" in ids(result, home)


def test_core_grammar_is_never_deferred():
    tree, table, _ = defer_case()
    plan, result = mixed(tree, table, mixable=("stage:one", "stage:two"), library_of={})
    assert plan.summary["deferred"] == 0 and "lpractice" in ids(result, "stage:one")


def overriding_catalogue(provider_of, core_ids):
    """An availability catalogue whose provider() names a library also for core ids it overrides (an
    accepted override, content_library_merge), with the effective catalogue's core_ids()."""
    return SimpleNamespace(provider=provider_of, effective=SimpleNamespace(core_ids=lambda: frozenset(core_ids)))


def test_an_overridden_core_id_is_neither_cut_nor_deferred():
    # N1: for every M8 decision an id of the core catalogue is core, whoever an override gives it to
    tree = library_tree()
    words = [f"word:lib{i}" for i in range(10)]
    plain = route_library_of(overriding_catalogue(provider, ()))
    assert plain("word:lib0") == LIB and plain("grammar:c1") is None
    cut = ids(split_library_sections(tree, plain, fixed_stage_ids=("stage:zero",)))
    assert "lib-words" not in cut and "lib-grammar" not in cut
    overridden = route_library_of(overriding_catalogue(provider, [*words, "grammar:l2"]))
    assert overridden("word:lib0") is None and overridden("grammar:l1") == LIB
    kept = ids(split_library_sections(tree, overridden, fixed_stage_ids=("stage:zero",)))
    assert "lib-words" in kept  # ten core words: not an extension library's section, not cut
    assert "lib-grammar" in kept  # one of its items is core: a mixed section, not cut either

    def defer(core):
        tree, table, _ = defer_case()
        library_of = route_library_of(overriding_catalogue(lambda cid: LIB if ":l" in cid else None, core))
        return mixed(tree, table, mixable=("stage:one", "stage:two"),
                     library_of=library_entries(snapshot(tree), library_of))
    plan, result = defer(())
    assert plan.summary["deferred"] == 1
    plan, result = defer({"grammar:lpractice"})  # the same grammar, an overridden core id: stays
    assert plan.summary["deferred"] == 0 and "lpractice" in ids(result, "stage:one")


def test_a_deferral_never_passes_an_overridden_core_id():
    # An accepted override gives the core grammar item g of stage two to the library: core for M8,
    # so its readiness is not read, but its questions are the library's and may need the library
    # grammar libx of stage one; deferring libx to stage three (past g) is blocked. Without the
    # override (g a plain core item, core questions) libx moves as before.
    def case(override):
        tree = build(("stage:one", [("unit:g1", [("core1", ["grammar:c1"]), ("libx", ["grammar:libx"])])]),
                     ("stage:two", [("unit:g2", [("over", ["grammar:g"])]), ("unit:w2", [("mid", ["word:mid"])])]),
                     ("stage:three", [("unit:g3", [("core3", ["grammar:c3"])]),
                                      ("unit:w3", [("last", ["word:libw1", "word:libw2"])])]))
        library = {"grammar:libx", "word:libw1", "word:libw2"} | ({"grammar:g"} if override else set())
        library_of = route_library_of(overriding_catalogue(lambda cid: LIB if cid in library else None,
                                                           {"grammar:c1", "grammar:g", "word:mid", "grammar:c3"}))
        detached = snapshot(tree)
        providers, overridden = library_entries(detached, library_of), overridden_entries(detached, library_of)
        assert "grammar:g" not in providers and overridden == ({"grammar:g"} if override else frozenset())
        table = {"word:mid": {"use": templates([])}, "word:libw1": {"use": templates([])},
                 "word:libw2": {"use": templates([])}}  # g is core for M8: its readiness is never read
        return mixed(tree, table, mixable=("stage:one", "stage:two", "stage:three"), library_of=providers,
                     overridden=overridden)
    plan, result = case(override=False)
    assert plan.summary["deferred"] == 1 and "libx" in ids(result, "stage:three")
    plan, result = case(override=True)
    assert plan.summary["deferred"] == 0 and "libx" in ids(result, "stage:one")


def test_a_stage_whose_final_order_fails_the_check_takes_no_deferred_section(monkeypatch):
    # N4: step 2 forced to ignore the deadlines (every deferred section at the end): s1 would stand
    # without d2, which it needs, so stage two takes none of them and the route is placed again
    tree, table, providers = breaker_case(needs=[(1, 2)])
    monkeypatch.setattr(placement, "_insert_deferred", lambda order, chain, *args: (*order, *chain))
    plan, result = mixed(tree, table, mixable=("stage:one", "stage:two"), library_of=providers)
    assert plan.summary["deferred"] == 0
    assert ids(result, "stage:one") == ["core1", "d1", "d2"]
    assert not any(name.startswith("d") for name in ids(result, "stage:two"))
    readiness = Readiness(table)
    position = {e["id"]: i for i, e in enumerate(e for s in flat(result) for e in s["entries"])}
    assert readiness.counts("word:lib1x0", lambda r: position.get(r, 1 << 30) < position["word:lib1x0"]) == {"use": 1}


def test_the_final_check_counts_skeleton_and_deferred_items_fully_and_movable_items_to_their_floor():
    S = placement.Section
    grammar = S("g", "unit:u", ("grammar:g",), (False,), placement.GRAMMAR, None)
    word = S("w", "unit:u", ("word:w",), (True,), placement.WORD, None)
    readiness = Readiness({"grammar:g": {"understanding": templates(["word:w"], [], [], [], [])},
                           "word:w": {"use": templates([], [], [], ["grammar:g"])}})
    counts0 = {"grammar:g": {"understanding": 5}, "word:w": {"use": 4}}
    floors = {"grammar:g": (("understanding", 3),), "word:w": (("use", 3),)}
    positions0 = {"word:w": 0, "grammar:g": 1}

    def holds(order, movable, table=readiness, prereqs=None):
        return placement._stage_holds(order, movable, readiness=table, counts0=counts0, floors=floors,
                                      prereqs=prereqs or {}, positions0=positions0, known_before=lambda r: False)
    assert holds((word, grammar), {"w"})          # w movable: 3 >= its floor 3; g: 5 >= its count 5
    assert not holds((grammar, word), {"w"})      # g, skeleton: 4 < 5
    assert not holds((word, grammar), set())      # w counted fully: 3 < 4
    assert not holds((grammar, word), {"w"}, table=Readiness({}), prereqs={"grammar:g": ("word:w",)})
    assert holds((word, grammar), {"w"}, table=Readiness({}), prereqs={"grammar:g": ("word:w",)})


# --- the plan: key digest, JSON, and apply_plan refusing what does not fit ---------------------------

def test_tree_digest_covers_the_route_but_not_optional_stages():
    tree = library_tree()
    digest = tree_digest(tree)
    assert digest == tree_digest(snapshot(tree)) == tree_digest(deepcopy(tree))
    other = deepcopy(tree)
    flat(other, "stage:ref")[0]["entries"].append(entry("word:new"))
    assert tree_digest(other) == digest
    other = deepcopy(tree)
    flat(other, "stage:one")[0]["entries"][0]["depends_on"].append("grammar:c1")
    assert tree_digest(other) != digest


def test_plan_json_round_trip_and_malformed_values():
    tree, table = alternating_case()
    plan, result = mixed(tree, table)
    again = MixPlan.from_json(plan.to_json())
    assert again == plan and apply(tree, again) == result
    for broken in (None, [], {"format": "x"}, {**plan.to_json(), "version": 2},
                   {**plan.to_json(), "stages": [{"id": "stage:one", "sections": [["only-one"]]}]},
                   {**plan.to_json(), "stages": [{"id": "", "sections": []}]}):
        with pytest.raises(ValueError):
            MixPlan.from_json(broken)


def test_apply_plan_refuses_a_plan_that_does_not_fit_the_tree():
    tree, table = alternating_case()
    plan, _ = mixed(tree, table)
    stage_id, rows = plan.stages[0]

    def variant(new_rows, stage=stage_id):
        return MixPlan(plan.key, ((stage, tuple(new_rows)),))
    assert apply(tree, variant(rows)) is not None
    assert apply(tree, variant(rows[:-1])) is None                          # a section left out
    assert apply(tree, variant([*rows, rows[0]])) is None                   # a section twice
    assert apply(tree, variant([*rows, ("section:ghost", "unit:g")])) is None
    assert apply(tree, variant([(rows[0][0], "unit:ghost"), *rows[1:]])) is None
    split = [(sid, "unit:w" if index % 2 else "unit:g") for index, (sid, _) in enumerate(rows)]
    assert apply(tree, variant(split)) is None                              # a unit in two runs
    assert apply(tree, variant(rows, stage="stage:missing")) is None
    # Item 6: stage 0 and optional stages are never reordered, not even to their own order
    zero = MixPlan(plan.key, (("stage:zero", (("section:k", "unit:k"),)), (stage_id, rows)))
    assert apply(tree, zero) is None
    assert apply(tree, zero, mixable=("stage:zero", "stage:one")) is not None  # only when named mixable
    backwards = MixPlan(plan.key, (("stage:zero", (("section:k", "unit:k"), ("section:g1", "unit:k"))),
                                   (stage_id, tuple(r for r in rows if r[0] != "section:g1"))))
    assert apply(tree, backwards, mixable=("stage:zero", "stage:one")) is None  # to an earlier stage
    hard = build(("stage:one", [("unit:g", [("g1", ["grammar:g1"])]),
                                ("unit:w", [("w1", [("word:w1", ["grammar:g1"])])])]))
    swapped = MixPlan({}, (("stage:one", (("section:w1", "unit:g"), ("section:g1", "unit:g"))),))
    assert apply(hard, swapped) is None                                     # a hard prerequisite after
    assert apply(tree, "not a plan") is None


def test_apply_plan_refuses_an_optional_stage():
    tree = library_tree()
    rows = tuple((s["id"], s["unit_id"]) for s in flat(tree, "stage:ref"))
    plan = MixPlan({}, (("stage:ref", rows),))
    assert apply(tree, plan, mixable=("stage:one", "stage:ref")) is None


def test_apply_plan_moves_only_deferrable_library_grammar_to_a_later_stage():
    # Item 6: a section changes stage only as M8(b) moves one
    tree, table, providers = defer_case()
    mixable = ("stage:one", "stage:two")
    plan, _ = mixed(tree, table, mixable=mixable, library_of=providers)
    assert plan.summary["deferred"] == 1
    assert apply(tree, plan, mixable=mixable, providers=providers) is not None
    assert apply(tree, plan, mixable=mixable, providers={}) is None         # not a library's: refused
    one, two = dict(plan.stages)["stage:one"], dict(plan.stages)["stage:two"]

    def moved(section_id):  # to the end of stage two, in its last unit (units stay contiguous)
        return MixPlan(plan.key, (("stage:one", tuple(r for r in one if r[0] != section_id)),
                                  ("stage:two", (*two, (section_id, two[-1][1])))))
    assert apply(tree, moved("section:core1"), mixable=mixable, providers=providers) is None   # core grammar
    assert apply(tree, moved("section:libword1"), mixable=mixable, providers=providers) is None  # a word section
    needed, needed_table, needed_providers = defer_case(hard=True)  # lib-user hard-depends on lpractice
    needed_plan = plan_mix(snapshot(needed), Readiness(needed_table), mixable=mixable,
                           library_of=needed_providers, key={})
    assert needed_plan.summary["deferred"] == 0
    stages = dict(needed_plan.stages)
    forced = MixPlan({}, (("stage:one", tuple(r for r in stages["stage:one"] if r[0] != "section:lpractice")),
                          ("stage:two", (*stages["stage:two"], ("section:lpractice", stages["stage:two"][-1][1])))))
    assert apply(needed, forced, mixable=mixable, providers=needed_providers) is None  # someone needs it


# --- imports: the pure modules stay pure, the adapters read no learner store and no UI ---------------

ROUTE_MIX_IMPORTS = {
    "params": {"__future__", "dataclasses"},
    "readiness": {"__future__", "collections.abc"},
    "sections": {"__future__", "collections.abc", "hashlib", ".params"},
    "placement": {"__future__", "bisect", "collections.abc", "dataclasses", ".params", ".readiness"},
    "plan": {"__future__", "collections.abc", "dataclasses", "hashlib", "json", ".params", ".placement",
             ".readiness"},
    "store": {"__future__", "json", "os", "pathlib", "threading"},
    "service": {"__future__", "asyncio", "functools", "pathlib", "threading", "time", ".params", ".placement",
                ".plan", ".store", ".sources", "."},
    "sources": {"__future__", "collections.abc", "contextlib", "pathlib", "re", "sqlite3", ".placement",
                ".readiness", "..study_path_catalog", "..content_availability", "..", "..material_review",
                "..build_info"},
}


def test_route_mix_modules_import_nothing_new():
    import ast
    from pathlib import Path
    folder = Path(placement.__file__).parent
    assert {path.stem for path in folder.glob("*.py")} - {"__init__"} == set(ROUTE_MIX_IMPORTS)
    for name, expected in ROUTE_MIX_IMPORTS.items():
        found = set()
        for node in ast.walk(ast.parse((folder / f"{name}.py").read_text("utf-8"))):  # lazy imports too
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                found.add("." * node.level + (node.module or ""))
        assert found == expected, name
