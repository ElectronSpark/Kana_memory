"""WP-K1: template dependency gate (design 11 section 5, D1-D4) on synthetic data only."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kanamemory import content_gaps
from kanamemory.course_planning import iter_question_plans
from kanamemory.intro_batch_ui import intro_quiz_question
from kanamemory.learning_practice_policy import practice_candidates, select_practice_plan
from kanamemory.learning_questions import OOV_TOKEN_PREFIX, question_requires, template_requires
from kanamemory.study_queries import StudyQueries
from support.app_fixtures import question_app, setup_app  # noqa: F401 (fixtures used by learning_app)
from support.fake_ui import View
from support.learning_fixtures import learning_app  # noqa: F401
from support.learning_practice_policy_support import CID, raw
from support.question_dependency_support import (
    GRAMMAR_ID, WORD_ID, bare_lesson, grammar, install, namespace, requirement, word,
)


def offered(item, kinds, known=(), ability="application"):
    return {(c["kind"], c["variant"], c["fallback"]) for c in practice_candidates(item, kinds, known_background_ids=known)
            if c["kind"] in kinds}


# ------------------------------------------------------------------ accessor

def test_undeclared_template_requires_nothing_and_is_a_fallback(monkeypatch):
    install(monkeypatch, {})
    item = bare_lesson()
    assert template_requires(item, "grammar_cloze", item["question_templates"]["grammar_cloze"][0]) is None
    install(monkeypatch, {"schema_version": 1, "items": {}})
    assert template_requires(item, "grammar_cloze", item["question_templates"]["grammar_cloze"][0]) is None


def test_requires_is_the_union_of_background_ids_and_the_entry_without_the_target(monkeypatch):
    template = raw("x", background=["word:b"])
    install(monkeypatch, namespace((CID, "grammar_cloze", template, requirement(words=["word:a"], grammar=["grammar:g"]))))
    item = bare_lesson()
    assert template_requires(item, "grammar_cloze", template) == frozenset({"word:a", "word:b", "grammar:g"})
    # The target is never its own requirement (validation rejects it; the set drops it).
    assert template_requires({"id": "word:a"}, "grammar_cloze", template) == frozenset({"word:b"})
    # A reviewed template with an empty background declares (nothing): gated, not fallback.
    assert template_requires(item, "grammar_cloze", raw("y")) == frozenset()
    # Another kind's entry or another fingerprint never applies.
    assert template_requires(item, "grammar_transform", template) == frozenset({"word:b"})


def test_oov_surfaces_are_opaque_unknown_tokens(monkeypatch):
    template = bare_lesson()["question_templates"]["grammar_cloze"][0]
    install(monkeypatch, namespace((CID, "grammar_cloze", template, requirement(oov=["私", "できる"]))))
    requires = template_requires(bare_lesson(), "grammar_cloze", template)
    assert len(requires) == 2 and all(token.startswith(OOV_TOKEN_PREFIX) for token in requires)
    assert not requires <= {"私", "できる", "word:私", CID}


def test_malformed_entry_is_an_invalid_pack_not_a_silent_pass(monkeypatch):
    item = bare_lesson()
    template = item["question_templates"]["grammar_cloze"][0]
    install(monkeypatch, namespace((CID, "grammar_cloze", template, {"words": "word:a"})))
    with pytest.raises(ValueError):
        template_requires(item, "grammar_cloze", template)
    with pytest.raises(ValueError):
        select_practice_plan(item, "application", ["grammar_cloze"], {}, [])


# ---------------------------------------------------------------------- gate

def test_declared_templates_are_eligible_only_when_requirements_are_known(monkeypatch):
    item = bare_lesson()
    cloze = item["question_templates"]["grammar_cloze"]
    install(monkeypatch, namespace((CID, "grammar_cloze", cloze[0], requirement(words=["word:a"])),
                                   (CID, "grammar_cloze", cloze[1], requirement())))
    # Nothing known: the self-contained declared template is offered first; a
    # one-task gated pass is topped up with the undeclared third variant
    # (design 11 section 5, D4: fewer than two met tasks).
    assert offered(item, ["grammar_cloze"]) == {("grammar_cloze", 1, False), ("grammar_cloze", 2, True)}
    assert select_practice_plan(item, "application", ["grammar_cloze"], {}, []) == {
        "kind": "grammar_cloze", "variant": 1, "input_mode": "choice"}
    assert offered(item, ["grammar_cloze"], known=["word:a"]) == {("grammar_cloze", 0, False), ("grammar_cloze", 1, False)}
    # Knowledge is never inferred from enrollment or from the target itself.
    assert offered(item, ["grammar_cloze"], known=[CID]) == {("grammar_cloze", 1, False), ("grammar_cloze", 2, True)}


def test_fallback_templates_serve_when_no_gated_template_qualifies(monkeypatch):
    item = bare_lesson()
    cloze = item["question_templates"]["grammar_cloze"]
    install(monkeypatch, namespace((CID, "grammar_cloze", cloze[0], requirement(words=["word:a"]))))
    kinds = ["grammar_cloze", "grammar_transform"]
    result = offered(item, kinds)
    assert ("grammar_cloze", 0, False) not in result and ("grammar_cloze", 0, True) not in result
    assert result == {("grammar_cloze", 1, True), ("grammar_cloze", 2, True),
                      ("grammar_transform", 0, True), ("grammar_transform", 1, True), ("grammar_transform", 2, True)}
    plan = select_practice_plan(item, "application", kinds, {}, [])
    assert plan["kind"] == "grammar_cloze" and plan["variant"] == 1
    # Once a gated template qualifies it comes first; a single one is topped up with
    # the fallback templates (design 11 section 5, D4), which step back only from two met tasks.
    topped = list(practice_candidates(item, kinds, known_background_ids=["word:a"]))
    assert (topped[0]["kind"], topped[0]["variant"], topped[0].get("top_up")) == ("grammar_cloze", 0, None)
    assert {(c["kind"], c["variant"]) for c in topped[1:]} == {(kind, variant) for kind, variant, _ in result}
    assert all(c["top_up"] and c["fallback"] for c in topped[1:])


def test_oov_blocks_a_template_even_when_every_id_is_known(monkeypatch):
    item = bare_lesson()
    cloze = item["question_templates"]["grammar_cloze"]
    install(monkeypatch, namespace((CID, "grammar_cloze", cloze[0], requirement(words=["word:a"], oov=["私"])),
                                   (CID, "grammar_cloze", cloze[1], requirement(words=["word:a"]))))
    # (The undeclared third variant tops up the one-task gated pass; design 11 section 5, D4.)
    assert offered(item, ["grammar_cloze"], known=["word:a", "私", "word:私"]) == {
        ("grammar_cloze", 1, False), ("grammar_cloze", 2, True)}


def test_retirement_and_malformed_metadata_filters_still_apply(monkeypatch):
    item = bare_lesson()
    item["question_templates"]["grammar_cloze"][2] = raw("z")
    item["question_templates"]["grammar_cloze"][2]["practice"]["reviewed"] = False
    install(monkeypatch, namespace((CID, "grammar_cloze", item["question_templates"]["grammar_cloze"][2], requirement())))
    assert ("grammar_cloze", 2, False) not in offered(item, ["grammar_cloze"])
    assert ("grammar_cloze", 2, True) not in offered(item, ["grammar_cloze"])


def test_absent_namespace_and_no_metadata_keep_legacy_selection(monkeypatch):
    install(monkeypatch, {})
    item = bare_lesson()
    assert select_practice_plan(item, "application", list(item["question_templates"]), {}, []) == {
        "kind": "grammar_cloze", "variant": 0, "input_mode": "choice"}
    assert all(c["fallback"] for c in practice_candidates(item, list(item["question_templates"])))


# --------------------------------------------------------------- content_gap

def test_content_gap_is_recorded_once_without_text_when_the_gate_empties_a_slot(monkeypatch):
    buffer = content_gaps.ContentGapBuffer()
    monkeypatch.setattr(content_gaps, "_buffer", buffer)
    item = bare_lesson()
    cloze = item["question_templates"]["grammar_cloze"]
    item["question_templates"] = {"grammar_cloze": cloze[:2]}
    install(monkeypatch, namespace((CID, "grammar_cloze", cloze[0], requirement(words=["word:a", "word:b"])),
                                   (CID, "grammar_cloze", cloze[1], requirement(words=["word:b"], oov=["私"]))))
    # Design 11 §5 D4/D5 (R1): the slot is still served (relaxed pass) and the gap still recorded.
    assert select_practice_plan(item, "application", ["grammar_cloze"], {}, []) == {
        "kind": "grammar_cloze", "variant": 0, "input_mode": "choice"}
    events = content_gaps.gap_events()
    assert len(events) == 1
    event = events[0]
    assert (event["content_id"], event["ability"], event["missing"], event["kinds"]) == (CID, "application", 3, ("grammar_cloze",))
    dumped = json.dumps(events, ensure_ascii=False)
    assert "窓" not in dumped and "依頼" not in dumped and "word:a" not in dumped
    # A disabled or empty kind list is not a content gap.
    assert select_practice_plan(item, "application", [], {}, []) is None
    assert select_practice_plan(item, "application", ["grammar_transform"], {}, []) is None
    assert len(content_gaps.gap_events()) == 1 and content_gaps.gap_events()[0]["count"] == 1
    # Serving a fallback or a gated template records nothing new.
    assert select_practice_plan(item, "application", ["grammar_cloze"], {"known_background_ids": ["word:a", "word:b"]}, [])
    assert content_gaps.gap_events()[0]["count"] == 1


# ------------------------------------------- relaxed pass (design 11 §5 D4/D5, R1)

def tiers(item, kinds, known=()):
    return {(c["kind"], c["variant"], c["fallback"], c.get("relaxed"))
            for c in practice_candidates(item, kinds, known_background_ids=known)}


def cloze_only(monkeypatch, *requirements):
    """The bare lesson's three cloze templates with one requirement row each (None = undeclared)."""
    item = bare_lesson()
    cloze = item["question_templates"]["grammar_cloze"]
    item["question_templates"] = {"grammar_cloze": cloze}
    install(monkeypatch, namespace(*((CID, "grammar_cloze", cloze[index], value)
                                     for index, value in enumerate(requirements) if value is not None)))
    return item


def test_all_known_templates_are_chosen_before_fallback_and_relaxed(monkeypatch):
    item = cloze_only(monkeypatch, requirement(words=["word:a", "word:b"]), requirement(words=["word:c"]), requirement())
    assert tiers(item, ["grammar_cloze"]) == {("grammar_cloze", 2, False, None)}
    assert select_practice_plan(item, "application", ["grammar_cloze"], {}, [])["variant"] == 2
    # Knowing more widens the all-known tier; nothing relaxed appears beside it.
    assert tiers(item, ["grammar_cloze"], known=["word:c"]) == {
        ("grammar_cloze", 1, False, None), ("grammar_cloze", 2, False, None)}


def test_fallback_templates_are_offered_before_relaxed_ones(monkeypatch):
    item = cloze_only(monkeypatch, requirement(words=["word:a", "word:b"]), requirement(words=["word:c"]), None)
    assert tiers(item, ["grammar_cloze"]) == {("grammar_cloze", 2, True, None)}
    assert select_practice_plan(item, "application", ["grammar_cloze"], {}, [])["variant"] == 2


def test_relaxed_pass_serves_the_fewest_unmet_requirements_and_records_the_gap(monkeypatch):
    buffer = content_gaps.ContentGapBuffer()
    monkeypatch.setattr(content_gaps, "_buffer", buffer)
    # One unmet requirement (the 1T rule) wins over two and three.
    item = cloze_only(monkeypatch, requirement(words=["word:a", "word:b"]), requirement(words=["word:c"]),
                      requirement(words=["word:d"], grammar=["grammar:e"], oov=["私"]))
    assert tiers(item, ["grammar_cloze"]) == {("grammar_cloze", 1, False, 1)}
    assert select_practice_plan(item, "application", ["grammar_cloze"], {}, []) == {
        "kind": "grammar_cloze", "variant": 1, "input_mode": "choice"}
    [event] = content_gaps.gap_events()
    assert (event["content_id"], event["missing"]) == (CID, 6)
    # Without a one-unmet template the smallest available count is taken, ties kept.
    item = cloze_only(monkeypatch, requirement(words=["word:a", "word:b"]), requirement(words=["word:c"], oov=["私"]),
                      requirement(words=["word:d"], grammar=["grammar:e"], oov=["私"]))
    assert tiers(item, ["grammar_cloze"]) == {("grammar_cloze", 0, False, 2), ("grammar_cloze", 1, False, 2)}
    # A known requirement lowers the count; knowing it all leaves the relaxed pass.
    assert tiers(item, ["grammar_cloze"], known=["word:d", "grammar:e"]) == {("grammar_cloze", 2, False, 1)}
    assert tiers(item, ["grammar_cloze"], known=["word:a", "word:b"]) == {("grammar_cloze", 0, False, None)}


def test_nothing_is_served_only_when_the_slot_has_no_usable_template(monkeypatch):
    buffer = content_gaps.ContentGapBuffer()
    monkeypatch.setattr(content_gaps, "_buffer", buffer)
    item = cloze_only(monkeypatch, requirement(words=["word:a"]), requirement(words=["word:a"]), requirement(words=["word:a"]))
    assert select_practice_plan(item, "application", ["grammar_cloze"], {}, []) is not None
    # No template of an enabled kind, or no enabled kind: no question and no content gap.
    assert select_practice_plan(item, "application", ["grammar_transform"], {}, []) is None
    assert select_practice_plan(item, "application", [], {}, []) is None
    item["question_templates"]["grammar_cloze"] = []
    assert select_practice_plan(item, "application", ["grammar_cloze"], {}, []) is None
    assert [event["count"] for event in content_gaps.gap_events()] == [1]


def test_guided_exposure_never_uses_relaxed_candidates():
    from kanamemory.learning_practice_policy import guided_practice_sequence
    item = lesson_with_background(["word:unknown"])
    kinds = list(item["question_templates"])
    assert select_practice_plan(item, "application", kinds, {}, []) is not None
    assert all(c.get("relaxed") == 1 for c in practice_candidates(item, kinds))
    assert guided_practice_sequence(item, kinds) == []
    assert len(guided_practice_sequence(item, kinds, known_background_ids=["word:unknown"])) == 2


def lesson_with_background(background):
    from support.learning_practice_policy_support import lesson
    item = lesson()
    for values in item["question_templates"].values():
        for template in values:
            template["practice"]["background_ids"] = list(background)
    return item


# ------------------------------------------------------------ course planning

def plans(item, enabled, ready):
    calls = []

    def instruction_ready_ids():
        calls.append(1)
        return set(ready)
    queries = SimpleNamespace(pending_abilities=lambda cid: set(), instruction_ready_ids=instruction_ready_ids)
    learning = SimpleNamespace(get_unit=lambda cid, ability: {"attempt_count": 0},
                               practice_history=lambda cid, ability: [])
    result = [(plan.question["ability"], plan.question["kind"], plan.question["variant"]) for plan in
              iter_question_plans(item, enabled_kinds=enabled, queries=queries, learning_store=learning,
                                  system_japanese_input=False)]
    return result, len(calls)


def test_question_plans_always_supply_the_known_set(monkeypatch):
    item = grammar()
    meaning = item["question_templates"]["grammar_meaning"]
    assert not any("practice" in raw for values in item["question_templates"].values() for raw in values)
    install(monkeypatch, namespace((GRAMMAR_ID, "grammar_meaning", meaning[0], requirement(words=["word:a"])),
                                   (GRAMMAR_ID, "grammar_meaning", meaning[1], requirement(words=["word:z"]))))
    enabled = ["grammar_meaning", "grammar_cloze"]
    result, calls = plans(item, enabled, {"word:a"})
    assert calls == 1 and result == [("understanding", "grammar_meaning", 0), ("application", "grammar_cloze", 0)]
    result, calls = plans(item, enabled, set())
    assert calls == 1 and result == [("understanding", "grammar_meaning", 2), ("application", "grammar_cloze", 0)]
    # Design 11 §5 D4/D5 (R1): an ability whose every template is gated out is still planned
    # (relaxed pass, one unmet each), so home counts and the continue chain never deadlock.
    item["question_templates"]["grammar_meaning"] = meaning[:2]
    result, calls = plans(item, enabled, set())
    assert calls == 1 and result == [("understanding", "grammar_meaning", 0), ("application", "grammar_cloze", 0)]


# ----------------------------------------------------------------- intro quiz

def test_quiz_never_falls_back_to_an_unmet_question(monkeypatch):
    item = {**word(), "enrolled_abilities": ["understanding", "use"]}
    meaning, usage = item["question_templates"]["vocab_meaning"], item["question_templates"]["vocab_usage"]
    kinds = ["vocab_meaning", "vocab_usage"]
    install(monkeypatch, namespace((WORD_ID, "vocab_meaning", meaning[1], requirement(words=["word:other"]))))
    plain = intro_quiz_question(item, kinds)
    assert (plain["kind"], plain["variant"]) == ("vocab_meaning", 1)
    gated = intro_quiz_question(item, kinds, known_background_ids=set())
    assert (gated["kind"], gated["variant"]) == ("vocab_meaning", 0)
    known = intro_quiz_question(item, kinds, known_background_ids={"word:other"})
    assert (known["kind"], known["variant"]) == ("vocab_meaning", 1)
    # Every preferred variant unmet: the next kind's fallback template.
    install(monkeypatch, namespace((WORD_ID, "vocab_meaning", meaning[0], requirement(words=["word:other"])),
                                   (WORD_ID, "vocab_meaning", meaning[1], requirement(oov=["私"])),
                                   (WORD_ID, "vocab_usage", usage[1], requirement(words=["word:other"]))))
    other = intro_quiz_question(item, kinds, known_background_ids=set())
    assert (other["kind"], other["variant"]) == ("vocab_usage", 0)
    assert question_requires(item, other) is None
    # Nothing requirement-safe: no quiz question rather than a leaking one.
    install(monkeypatch, namespace((WORD_ID, "vocab_meaning", meaning[0], requirement(words=["word:other"])),
                                   (WORD_ID, "vocab_meaning", meaning[1], requirement(words=["word:other"])),
                                   (WORD_ID, "vocab_usage", usage[0], requirement(words=["word:other"])),
                                   (WORD_ID, "vocab_usage", usage[1], requirement(words=["word:other"]))))
    assert intro_quiz_question(item, kinds, known_background_ids=set()) is None
    assert intro_quiz_question(item, kinds, known_background_ids={"word:other"})["variant"] == 1


# --------------------------------------------------------------- example cards

def test_learning_pages_pass_the_batch_known_set_to_example_cards(learning_app, monkeypatch):
    app = learning_app
    monkeypatch.setattr(StudyQueries, "instruction_ready_ids", lambda self: {"grammar:known"})
    app.learning_store.enroll([word()])
    app.example_cards = Mock(return_value=View())
    item = app.learning_store.get_item(WORD_ID)
    app.show_learning_detail(item)
    assert app.example_cards.call_args.kwargs["known_ids"] == app._intro_batch_known_ids() == {"grammar:known"}
    app.example_cards.reset_mock()
    app.learning_store.set_initial_familiarity(WORD_ID, {a: "new" for a in ("understanding", "reading", "use")},
                                               expected_introduction_id=item["introduction_id"])
    app.show_learning_instruction(app.learning_store.get_item(WORD_ID))
    assert app.page == "learning_intro"
    assert app.example_cards.call_args.kwargs["known_ids"] == {"grammar:known"}


# ------------------------------------------------------------------ other callers

def test_diagnostic_candidates_gate_only_the_enabled_kinds(tmp_path, monkeypatch):
    """The gate picks its pass per ability, so a disabled kind's met template
    must not hide an enabled kind's relaxed one (design 11 section 5, D3/D4)."""
    from copy import deepcopy
    from kanamemory import learning_practice_policy, retention_sources
    from kanamemory.domain import StudyStore
    from kanamemory.learning_store import LearningStore
    from kanamemory.retention_session import DiagnosticStore
    from support.question_dependency_support import choice
    item = grammar()
    item["question_templates"]["grammar_transform"] = [choice("本を読む →（　）", "読んでいます", "読みます")]
    cloze = item["question_templates"]["grammar_cloze"][0]
    transform = item["question_templates"]["grammar_transform"][0]
    install(monkeypatch, namespace((GRAMMAR_ID, "grammar_cloze", cloze, requirement()),
                                   (GRAMMAR_ID, "grammar_transform", transform, requirement(words=["word:unknown"]))))
    both = ["grammar_cloze", "grammar_transform"]
    assert {c["kind"] for c in practice_candidates(item, both) if c["kind"] in both} == {"grammar_cloze"}
    assert [(c["kind"], c.get("relaxed")) for c in practice_candidates(item, ["grammar_transform"])] == [
        ("grammar_transform", 1)]
    seen, real = [], learning_practice_policy.practice_candidates

    def spy(target, kinds, **kwargs):
        seen.append(list(kinds))
        return real(target, kinds, **kwargs)
    monkeypatch.setattr(learning_practice_policy, "practice_candidates", spy)
    monkeypatch.setattr(retention_sources, "current_item", lambda store, cid, repo=None: deepcopy(item))
    with StudyStore(tmp_path / "diagnostic", time_zone="UTC") as store:
        learning = LearningStore(store)
        store.set_setting("learning_preferences_v1", {"enabled": ["grammar_transform"]})
        DiagnosticStore(store, learning)._candidates(GRAMMAR_ID, ["grammar_transform"])
    assert seen and all(set(kinds) <= {"grammar_transform"} for kinds in seen)


def test_guide_replacement_known_set_includes_introduced_kana(tmp_path, monkeypatch):
    """A replacement guide reads the same known set as the original guide (D5)."""
    from kanamemory.domain import StudyStore
    from kanamemory.learning_replacement import LearningReplacementStore
    from kanamemory.learning_store import LearningStore
    monkeypatch.setattr(StudyQueries, "introduced_kana_ids", lambda self: {"kana:kana-3042:hiragana"})
    monkeypatch.setattr(StudyQueries, "instruction_ready_ids", lambda self: {"grammar:known"})
    with StudyStore(tmp_path / "replacement", time_zone="UTC") as store:
        replacement = LearningReplacementStore(LearningStore(store))
        _, state = replacement._options(dict(word(), enrolled_abilities=["understanding"]))
    assert state["known_background_ids"] == {WORD_ID, "grammar:known", "kana:kana-3042:hiragana"}


# ------------------------------------------- caller admission (admit=, not_relaxed)

def test_admit_runs_before_the_pass_is_chosen(monkeypatch):
    from kanamemory.learning_practice_policy import not_relaxed
    buffer = content_gaps.ContentGapBuffer()
    monkeypatch.setattr(content_gaps, "_buffer", buffer)
    item = cloze_only(monkeypatch, requirement(words=["word:a"]), requirement(words=["word:b", "word:c"]),
                      requirement(words=["word:d", "word:e"]))
    assert tiers(item, ["grammar_cloze"]) == {("grammar_cloze", 0, False, 1)}
    seen = []

    def refuse_first(descriptor, template):
        seen.append((descriptor["variant"], descriptor.get("relaxed"), template["kind"]))
        return descriptor["variant"] != 0
    # The caller refuses the least-unmet template: it gets the next relaxed tier, not nothing.
    offered_now = {(c["variant"], c["relaxed"]) for c in practice_candidates(item, ["grammar_cloze"], admit=refuse_first)}
    assert offered_now == {(1, 2), (2, 2)}
    assert sorted(seen) == [(0, 1, "grammar_cloze"), (1, 2, "grammar_cloze"), (2, 2, "grammar_cloze")]
    # Retention paths never take the relaxed tier: only-unmet content yields nothing there.
    assert list(practice_candidates(item, ["grammar_cloze"], admit=not_relaxed)) == []
    # An all-known template is unaffected by not_relaxed.
    assert {(c["variant"], c.get("relaxed")) for c in practice_candidates(
        item, ["grammar_cloze"], known_background_ids=["word:a"], admit=not_relaxed)} == {(0, None)}
    # A caller's refusal of a met template is not a content gap; unmet content still is.
    gaps = len(content_gaps.gap_events())
    met = cloze_only(monkeypatch, requirement(), requirement(words=["word:b"]), None)
    assert list(practice_candidates(met, ["grammar_cloze"], admit=lambda d, t: d["variant"] == 1)) != []
    assert len(content_gaps.gap_events()) == gaps


def test_new_content_check_takes_no_relaxed_candidate_but_the_diagnostic_does(tmp_path, monkeypatch):
    """Design 11 section 5, D4: the check-only trial of new content (RetentionCheckStore)
    skips an item x ability whose templates all have unmet requirements; the
    diagnostic of enrolled content keeps R1's relaxed tier."""
    from copy import deepcopy
    from kanamemory import learning_content, learning_practice_policy, retention_sources
    from kanamemory.domain import StudyStore
    from kanamemory.learning_store import LearningStore
    from kanamemory.retention_check import RetentionCheckStore
    from kanamemory.retention_session import DiagnosticStore
    item = grammar()
    cloze = item["question_templates"]["grammar_cloze"][0]
    install(monkeypatch, namespace((GRAMMAR_ID, "grammar_cloze", cloze, requirement(words=["word:unknown"]))))
    assert [c.get("relaxed") for c in practice_candidates(item, ["grammar_cloze"])] == [1]
    admitted, real = [], learning_practice_policy.practice_candidates

    def spy(target, kinds, **kwargs):
        admitted.append(kwargs.get("admit"))
        return real(target, kinds, **kwargs)
    monkeypatch.setattr(learning_practice_policy, "practice_candidates", spy)
    monkeypatch.setattr(learning_content, "get_learning_item", lambda cid: deepcopy(item) if cid == GRAMMAR_ID else None)
    monkeypatch.setattr(retention_sources, "current_item", lambda store, cid, repo=None: deepcopy(item))
    with StudyStore(tmp_path / "checks", time_zone="UTC") as store:
        learning = LearningStore(store)
        store.set_setting("learning_preferences_v1", {"enabled": ["grammar_cloze"]})
        with pytest.raises(ValueError, match="没有已启用的可靠检查题"):
            RetentionCheckStore(store).start([GRAMMAR_ID], ["grammar_cloze"], "new-content")
        assert admitted == [learning_practice_policy.not_relaxed]
        DiagnosticStore(store, learning)._candidates(GRAMMAR_ID, ["grammar_cloze"])
    assert admitted[1:] == [None]


# ------------------------------------------- thin gated pass (design 11 §5 D4)

def test_a_single_met_task_is_topped_up_with_fallback_tasks_after_it(monkeypatch):
    item = cloze_only(monkeypatch, requirement(), None, None)
    candidates = list(practice_candidates(item, ["grammar_cloze"]))
    assert [(c["variant"], c["fallback"], c.get("top_up")) for c in candidates] == [
        (0, False, None), (1, True, True), (2, True, True)]
    # Two met tasks need no top-up; the fallback tasks step back.
    item = cloze_only(monkeypatch, requirement(), requirement(), None)
    assert tiers(item, ["grammar_cloze"]) == {("grammar_cloze", 0, False, None), ("grammar_cloze", 1, False, None)}
    # A top-up never brings a relaxed task: unmet templates stay out while anything is met.
    item = cloze_only(monkeypatch, requirement(), requirement(words=["word:a"]), None)
    assert [(c["variant"], c.get("top_up"), c.get("relaxed")) for c in practice_candidates(item, ["grammar_cloze"])] == [
        (0, None, None), (2, True, None)]


def test_guided_paths_do_not_take_the_top_up(monkeypatch):
    from kanamemory.learning_practice_policy import guided_practice_sequence
    item = bare_lesson()
    cloze = item["question_templates"]["grammar_cloze"]
    item["question_templates"] = {"grammar_cloze": cloze}
    # One reviewed (met) task and two undeclared ones: the formal pass is topped up,
    # the guided sequence still sees only the reviewed task (no second context).
    cloze[0] = raw("guided", background=[])
    install(monkeypatch, namespace())
    assert [c.get("top_up") for c in practice_candidates(item, ["grammar_cloze"])] == [None, True, True]
    assert guided_practice_sequence(item, ["grammar_cloze"]) == []


def test_a_kind_with_one_met_task_is_topped_up_even_when_the_pass_has_more(monkeypatch):
    item = bare_lesson()
    cloze, transform = item["question_templates"]["grammar_cloze"], item["question_templates"]["grammar_transform"]
    install(monkeypatch, namespace((CID, "grammar_cloze", cloze[0], requirement()),
                                   (CID, "grammar_cloze", cloze[1], requirement(words=["word:a"])),
                                   (CID, "grammar_transform", transform[0], requirement()),
                                   (CID, "grammar_transform", transform[1], requirement())))
    candidates = [(c["kind"], c["variant"], c.get("top_up")) for c in practice_candidates(
        item, ["grammar_cloze", "grammar_transform"])]
    # Met tasks keep first place and order; only the thin kind (one met cloze) gains its fallback.
    assert candidates == [("grammar_cloze", 0, None), ("grammar_transform", 0, None),
                          ("grammar_transform", 1, None), ("grammar_cloze", 2, True)]


# ------------------------------------------- 1T gloss (design 11 §5 D5)

def test_unmet_declared_word_is_glossed_from_its_own_lesson(monkeypatch):
    from copy import deepcopy
    from kanamemory import learning_content
    from kanamemory.learning_questions import build_learning_question
    from kanamemory.question_support import unmet_requirement_glosses
    item = grammar()
    cloze = item["question_templates"]["grammar_cloze"][0]
    book = dict(word("word:synthetic:book"), title="本", reading="ほん", meaning="书")
    install(monkeypatch, namespace((GRAMMAR_ID, "grammar_cloze", cloze,
                                    requirement(words=[book["id"]], grammar=["grammar:synthetic:other"]))))
    lessons = {GRAMMAR_ID: item, book["id"]: book}
    monkeypatch.setattr(learning_content, "get_learning_item", lambda cid: deepcopy(lessons.get(cid)))
    question = build_learning_question(item, "grammar_cloze", variant=0)
    [hint] = unmet_requirement_glosses(question, set())
    assert (hint["surface"], hint["meaning"], hint["reading"], hint["start"], hint["target_id"]) == (
        "本", "书", "ほん", 0, book["id"])
    # Introduced words, reading tasks and guides get nothing; grammar ids are never glossed.
    assert unmet_requirement_glosses(question, {book["id"]}) == []
    assert unmet_requirement_glosses({**question, "tests_reading": True}, set()) == []
    assert unmet_requirement_glosses({**question, "guided": True}, set()) == []
    # A cognate's meaning repeats its written form, so its reading is what it explains.
    lessons[book["id"]] = dict(book, meaning="本")
    assert [h["meaning"] for h in unmet_requirement_glosses(question, set())] == ["ほん"]
    # A word whose sense would disclose the answer is not glossed.
    lessons[book["id"]] = dict(book, meaning="います")
    assert unmet_requirement_glosses(question, set()) == []
