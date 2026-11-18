"""The arranging tool for a wished beginning of the main route (tools/arrange_main_route_order.py).

Small fixture catalogues only (the same as tests/test_main_route_order.py, which tests may not import):
runs on the demo content.
"""
import json

import pytest

from arrange_main_route_order import arrange_order, read_wished, write_outputs
from check_main_route_order import check
from kanamemory import study_path_catalog as catalog
from kanamemory.route_mix.params import MixParams
from kanamemory.route_mix.readiness import Readiness

WORDS = "駅 学校 本 犬 猫 水 茶 肉 魚 米"


def word(form, depends=()):
    return {"id": "word:" + form, "title": form, "family": "word", "depends_on": list(depends)}


def grammar(key, depends=(), category="判断"):
    return {"id": "grammar:" + key, "title": key + "：说明", "family": "grammar", "category": category,
            "level": "入门", "depends_on": list(depends)}


@pytest.fixture
def fixture_rules(monkeypatch):
    """Two word topics in stage 1 and 2, grammar in stage 1, no weaving or small-unit plans."""
    monkeypatch.setattr(catalog, "WORD_TOPICS", [
        (1, "places", "地点词", "地点", "駅 学校"), (2, "things", "东西", "东西", "本 犬 猫"),
        (2, "food", "饮食", "饮食", "水 茶 肉 魚 米")])
    monkeypatch.setattr(catalog, "SMALL_UNIT_PLANS", {})
    monkeypatch.setattr(catalog, "GRAMMAR_ROUTE_CONFIG", {"levels": {"入门": 1}})
    monkeypatch.setattr(catalog, "GRAMMAR_TOPICS", {})
    monkeypatch.setattr(catalog, "COHERENCE_GROUPS", ())
    monkeypatch.setattr(catalog, "_PATH", {})


def catalogue():
    return [{"id": "pronunciation:mora", "title": "mora：说明", "family": "grammar", "domain": "pronunciation",
             "category": "读音规则", "depends_on": []},
            grammar("copula:plain"), grammar("copula:polite", ["grammar:copula:plain"]),
            grammar("topic:wa", ["grammar:copula:plain"], "话题"),
            *(word(form) for form in WORDS.split()), word("学生", ["grammar:topic:wa"])]


def sha(n):
    return f"{n:064x}"


# 茶 has three meaning questions that need 水 (one also 肉) and one switched-on one that needs 水:
# without an order 水 comes first, so it has 2 and 1; listed before 水 it has none.
TEA = Readiness({"word:茶": {"meaning": [(sha(1), ["word:水"]), (sha(2), ["word:水"]), (sha(3), ["word:水", "word:肉"])],
                             "on:meaning": [(sha(1), ["word:水"])]},
                 "word:駅": {"meaning": [(sha(4), [])]}})
STRUCTURED = [{"title": "吃喝", "sections": [{"title": "早饭", "entries": ["word:水", "word:茶"]},
                                            {"title": "是什么", "entries": ["grammar:copula:plain"]}]},
              {"title": "出门", "sections": [{"title": "地方", "entries": ["word:駅", "word:学校"]},
                                            {"title": "东西", "entries": ["word:本", "word:犬"]}]}]

def arranged(order, readiness=TEA, **options):
    options.setdefault("library_of", lambda cid: None)
    return arrange_order(order, items=catalogue(), readiness=readiness, params=MixParams(), **options)


def passes(order, readiness=TEA):
    return check(order, catalogue(), readiness=readiness, library_of=lambda cid: None, params=MixParams())[0] == 0


# 魚 has a question that needs 茶, 茶 one that needs 水 (the route without the order has 水 茶 肉 魚 in
# that order, so each has one gated question there and the floor is 1).
CHAIN = Readiness({"word:魚": {"meaning": [(sha(11), ["word:茶"])]},
                   "word:茶": {"meaning": [(sha(12), ["word:水"])]}})


def test_a_lesson_whose_questions_need_a_later_word_moves_behind_it(fixture_rules, tmp_path):
    outcome = arranged(["word:茶", "word:駅", "word:水"])
    assert outcome.result.order == ["word:駅", "word:水", "word:茶"]
    assert outcome.errors == 0 and not outcome.failed and outcome.result.waiting == []
    assert not passes(["word:茶", "word:駅", "word:水"]) and passes(outcome.result.order)
    lines = write_outputs(outcome, report_dir=tmp_path)
    assert "moved (new place minus place in the wished order of the placed lessons): median -1, largest 2; " \
           "1 moved later (median of those 2)" in lines
    rows = (tmp_path / "order.tsv").read_text(encoding="utf-8").splitlines()
    assert rows[3] == "2\tword:茶\t茶\tword\t0\t2\t\tmeaning 2/2 on:meaning 1/1"


def test_lessons_that_are_ready_keep_their_wished_order(fixture_rules):
    order = ["word:本", "word:駅", "grammar:copula:plain", "word:犬", "word:学校"]
    outcome = arranged(order, Readiness({}))
    assert outcome.result.order == order
    # Ready lessons around a waiting one keep their order; the waiting one lands right after 水.
    outcome = arranged(["word:本", "word:茶", "word:駅", "word:水", "word:犬"])
    assert outcome.result.order == ["word:本", "word:駅", "word:水", "word:茶", "word:犬"]


def test_an_unlisted_hard_prerequisite_is_put_in_before_the_lesson_that_needs_it(fixture_rules):
    outcome = arranged(["word:駅", "word:学生"], Readiness({}))
    assert outcome.result.order == ["word:駅", "grammar:copula:plain", "grammar:topic:wa", "word:学生"]
    assert outcome.result.notes == {"grammar:topic:wa": "unlisted prerequisite of word:学生",
                                    "grammar:copula:plain": "unlisted prerequisite of grammar:topic:wa"}
    assert outcome.errors == 0 and passes(outcome.result.order, Readiness({}))
    assert not passes(["word:駅", "word:学生"], Readiness({}))  # the check: a prerequisite error


def test_a_lesson_that_can_never_be_ready_is_left_out_with_its_blockers(fixture_rules, tmp_path):
    outcome = arranged(["word:茶", "word:駅"])
    result = outcome.result
    assert result.order == ["word:駅"] and result.waiting == ["word:茶"] and outcome.errors == 0
    write_outputs(outcome, report_dir=tmp_path)
    rows = (tmp_path / "left_out.tsv").read_text(encoding="utf-8").splitlines()
    assert rows[1:] == [
        "word:茶\t茶\tword\t0\ttoo few gated questions\tmeaning\t2\t0\t2\t3\tword:水:3:out word:肉:1:out",
        "word:茶\t茶\tword\t0\ttoo few gated questions\ton:meaning\t1\t0\t1\t1\tword:水:1:out"]
    [blocker] = result.blockers
    assert (blocker.lesson, blocker.alone, blocker.this_round, blocker.cumulative, blocker.ready) == (
        "word:水", 1, 1, 1, True)
    summary = (tmp_path / "summary.txt").read_text(encoding="utf-8")
    assert "left out (never ready inside the list): 1 (1 word)\n  1 waits on a lesson outside the list\n" in summary
    assert "   1. word:水 水: 1 / 1 / 1\n" in summary


def test_add_blockers_puts_the_blocker_before_the_first_lesson_it_unlocks(fixture_rules):
    outcome = arranged(["word:駅", "word:茶", "word:学校"], add_blockers=1)
    result = outcome.result
    assert result.order == ["word:駅", "word:水", "word:茶", "word:学校"]
    assert [b.lesson for b in result.added] == ["word:水"] and result.waiting == [] and outcome.errors == 0
    assert result.notes["word:水"] == "added (blocker 1, before word:茶)"


def test_lessons_that_need_each_other_get_a_blocker_that_waits_until_its_own_blocker_comes(fixture_rules):
    outcome = arranged(["word:魚"], CHAIN)
    assert outcome.result.order == [] and outcome.result.waiting == ["word:魚"]
    first, second = outcome.result.blockers
    # 茶 alone lets nothing in (it needs 水): taken by what it would let in, and it waits.
    assert (first.lesson, first.alone, first.this_round, first.cumulative, first.estimated, first.ready,
            first.needs) == ("word:茶", 0, 1, 0, True, False, ("word:水",))
    assert (second.lesson, second.this_round, second.cumulative, second.estimated, second.ready) == (
        "word:水", 1, 1, False, True)
    outcome = arranged(["word:魚"], CHAIN, add_blockers=2)
    assert outcome.result.order == ["word:水", "word:茶", "word:魚"] and outcome.errors == 0
    assert passes(outcome.result.order, CHAIN)


def test_limit_keeps_the_first_lessons_and_lists_the_rest(fixture_rules, tmp_path):
    outcome = arranged(["word:駅", "word:学校", "word:本"], Readiness({}), limit=2)
    assert outcome.result.order == ["word:駅", "word:学校"] and outcome.result.beyond == ["word:本"]
    write_outputs(outcome, report_dir=tmp_path)
    assert "word:本\t本\tword\t2\tafter --limit" in (tmp_path / "left_out.tsv").read_text(encoding="utf-8")


def test_unknown_repeated_kept_and_library_lessons_are_left_out_and_a_library_lesson_fails(fixture_rules):
    order = ["word:駅", "new:placeholder", "word:駅", "pronunciation:mora", "word:犬"]
    outcome = arranged(order, Readiness({}), library_of=lambda cid: "library.test" if cid == "word:犬" else None)
    assert outcome.result.order == ["word:駅"]
    assert [(cid, reason) for cid, _, reason in outcome.result.dropped] == [
        ("new:placeholder", "unknown id"), ("word:駅", "repeated"),
        ("pronunciation:mora", "kept in place by the app (sounds stage or optional stage)"),
        ("word:犬", "extension library library.test (an error)")]
    assert outcome.errors == 0 and outcome.failed  # the input had a library lesson, as the check says


def test_the_wished_order_is_read_from_a_tsv_by_pos_or_from_either_json_form(fixture_rules, tmp_path):
    tsv = tmp_path / "proposal.tsv"
    tsv.write_bytes("pos\tkind\tid\n2\tword\tword:水\n0\tword\tword:茶\n1\tword\tword:駅\n".encode("utf-8"))
    value, ranks = read_wished(tsv)
    assert value == ["word:茶", "word:駅", "word:水"]
    assert ranks == {"word:茶": "0", "word:駅": "1", "word:水": "2"}
    assert arranged(value, ranks=ranks).result.order == ["word:駅", "word:水", "word:茶"]
    units = tmp_path / "units.json"
    units.write_text(json.dumps(STRUCTURED, ensure_ascii=False), encoding="utf-8")
    value, ranks = read_wished(units)
    outcome = arranged(value, Readiness({}), ranks=ranks)
    assert outcome.result.order == ["word:水", "word:茶", "grammar:copula:plain", "word:駅", "word:学校",
                                    "word:本", "word:犬"]
    assert write_outputs(outcome)[0] == "the input has units: the result is a flat list (units and titles are not kept)"


def test_two_runs_give_the_same_bytes(fixture_rules, tmp_path):
    files = ("order.json", "order.tsv", "left_out.tsv", "blockers.tsv", "summary.txt")
    for run in ("a", "b"):
        outcome = arranged(["word:魚", "word:茶", "word:駅", "word:学生"], CHAIN, add_blockers=1)
        write_outputs(outcome, tmp_path / run / "order.json", tmp_path / run)
    assert all((tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes() for name in files)
    assert json.loads((tmp_path / "a" / "order.json").read_text(encoding="utf-8")) == outcome.result.order
    assert passes(outcome.result.order, CHAIN)
