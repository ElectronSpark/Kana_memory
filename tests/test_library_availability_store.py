"""Design 08 section 8.2: unavailable content is never selected; progress bytes stay intact.

Orphans are made two ways (support/library_availability_support.py): a
synthetic extension library disabled through the registry, and an id deleted
from the effective catalogue.
"""
from datetime import timedelta

import pytest

from kanamemory import content_availability as availability
from support.library_availability_support import (
    CORE_ID, EXT_ID, GONE_ID, KINDS, LATER, START, course, learner, question, table_hashes)  # noqa: F401

READY = START + timedelta(seconds=61)


def orphan(learner, how):
    if how == "registry":
        learner.disable()
        return EXT_ID
    learner.drop(GONE_ID)
    return GONE_ID


def restore(learner, how):
    if how == "registry":
        learner.enable()
    else:
        learner.restore(GONE_ID)


ORPHANS = pytest.mark.parametrize("how", ["registry", "catalogue"])


def _target(how):
    return EXT_ID if how == "registry" else GONE_ID


@ORPHANS
def test_current_and_pending_reader_skip_orphans_and_report_library_block(learner, how):
    learner.due_course(_target(how))
    learning = learner.learning
    assert learning.current(KINDS, now=READY)["content"]["id"] == _target(how)
    orphan(learner, how)
    current = learning.current(KINDS, now=READY)
    assert current["type"] == "content_blocked" and current["reason"] == "library_disabled"
    assert current["question_ids"] == [] and current["counts"]["library_unavailable"] == 1
    assert [row["content_id"] for row in learning.iter_pending_questions(KINDS)] == []
    assert learning.current(KINDS, now=READY, teaching_only=True) is None
    restore(learner, how)
    assert learning.current(KINDS, now=READY)["content"]["id"] == _target(how)


@ORPHANS
def test_orphan_does_not_hide_available_heads(learner, how):
    learner.due_course(_target(how))
    learner.due_course(CORE_ID)
    orphan(learner, how)
    with learner.learning.read_operation(now=READY) as read:
        assert learner.learning.current(KINDS, projection=read)["content"]["id"] == CORE_ID
        counts = learner.learning.counts(KINDS, projection=read)
    assert counts["ready"] == 1 and counts["disabled"] == 0 and counts["orphaned"] == 1
    assert counts["library_unavailable_pending"] == 1 and counts["unavailable_content"] == 0


@ORPHANS
def test_counts_exclude_orphans_from_ready_waiting_teaching_and_disabled(learner, how):
    learner.due_course(_target(how))
    learner.learning.enroll(course(CORE_ID), now=START)  # untouched intro without a question
    before = learner.learning.counts(KINDS, now=READY)
    assert before["ready"] == 1
    orphan(learner, how)
    after = learner.learning.counts(KINDS, now=READY)
    assert after["ready"] == after["waiting"] == after["instruction"] == after["disabled"] == 0
    assert after["pending"] == before["pending"] == 1
    assert after["unavailable_content"] == before["unavailable_content"] == 1  # CORE_ID only
    assert after["orphaned"] == 1 and after["library_unavailable"] == 1


@ORPHANS
def test_items_needing_questions_skip_orphans(learner, how):
    learner.learning.enroll([course(_target(how)), course(CORE_ID)], now=START)
    assert {item["id"] for item in learner.learning.items_needing_questions()} == {_target(how), CORE_ID}
    orphan(learner, how)
    assert [item["id"] for item in learner.learning.items_needing_questions()] == [CORE_ID]


@ORPHANS
def test_intro_batch_candidates_skip_orphans(learner, how):
    learning = learner.learning
    learning.set_intro_batch_sizes({"version": 1, "word": 1, "grammar": 3})
    learning.enroll([course(CORE_ID), course(_target(how))], now=START)
    for cid in (CORE_ID, _target(how)):
        learning.append_questions(question(cid, "q:" + cid), now=START)
    assert set(learning.intro_batch_candidates(KINDS, CORE_ID, now=START)) == {CORE_ID, _target(how)}
    orphan(learner, how)
    assert learning.intro_batch_candidates(KINDS, CORE_ID, now=START) == [CORE_ID]
    assert learning.intro_batch_candidates(KINDS, _target(how), now=START) == []


@ORPHANS
def test_ensure_prerequisites_never_overlays_orphan_targets(learner, how):
    target = _target(how)
    dependency = course("grammar:core:base")
    learner.ids.add(dependency["id"])
    learner.learning.enroll(course(target, depends_on=[dependency["id"]]), now=START)
    orphan(learner, how)
    result = learner.learning.ensure_prerequisites(catalogue=[dependency], now=START)
    assert result["added"] == [] and learner.learning.get_item(dependency["id"]) is None
    restore(learner, how)
    assert learner.learning.ensure_prerequisites(catalogue=[dependency], now=START)["added"] == [dependency["id"]]


@ORPHANS
def test_word_listening_admission_skips_orphans(learner, how):
    learning = learner.learning
    cid = "word:ext:mizu" if how == "registry" else "word:core:gone"
    learner.ids.add(cid)
    if how == "registry":
        provider = learner.catalogue

        def catalogue():
            value = provider()
            return availability.Catalogue(ids=value.ids, providers={**value.providers, cid: "test.ext"})
        learner.store.content_catalogue = catalogue
    word = {"id": cid, "family": "word", "revision": "v1", "abilities": ["understanding"], "title": "水",
            "reading": "みず", "meaning": "水", "instruction": {"title": "水", "meaning": "水"}}
    learning.enroll(word, now=START)
    learning.advance_instruction(cid, "intro_done", now=START)
    learning.advance_instruction(cid, "guide_correct", now=START)
    learning.append_questions({**question(cid, "qw"), "kind": "vocab_meaning", "prompt": "水", "answer": "水"},
                              now=START)
    learning.submit("qw", "水", True, now=READY)
    learning.finalize("qw", 3, now=READY)
    if how == "registry":
        learner.disable()
    else:
        learner.drop(cid)
    assert learning.admit_word_listening(now=LATER) == []
    assert learning.get_unit(cid, "listening") is None


@ORPHANS
def test_write_entries_refuse_orphans_without_partial_writes(learner, how):
    target = _target(how)
    qid = learner.due_course(target)
    learning = learner.learning
    orphan(learner, how)
    before = table_hashes(learner.store)
    refused = [
        lambda: learning.submit(qid, "正在读书", True, now=READY),
        lambda: learning.advance_instruction(target, "check", now=READY),
        lambda: learning.enroll(course(target), now=READY, selected_abilities={target: ["understanding"]}),
        lambda: learning.enroll([course(CORE_ID), course(target)], now=READY),
        lambda: learning.append_questions(question(target, "q:again"), now=READY),
        lambda: learning.mark_instruction_exposed(target, now=READY),
        lambda: learning.set_initial_familiarity(target, {"understanding": "new"}, now=READY),
        lambda: learning.start_intro_batch([target], family="grammar", now=READY),
    ]
    for call in refused:
        with pytest.raises(ValueError, match="内容库"):
            call()
    assert table_hashes(learner.store) == before  # no half write (CORE_ID was not enrolled either)
    assert learning.get_item(CORE_ID) is None


def test_finalize_refuses_a_submitted_answer_after_the_library_is_disabled(learner):
    qid = learner.due_course(EXT_ID)
    learner.learning.submit(qid, "正在读书", True, now=READY)
    learner.disable()
    before = table_hashes(learner.store)
    with pytest.raises(ValueError, match="内容库"):
        learner.learning.finalize(qid, 3, now=READY)
    assert table_hashes(learner.store) == before
    learner.enable()
    assert learner.learning.finalize(qid, 3, now=READY)["rating"] == 3


@ORPHANS
def test_disable_enable_round_trip_keeps_every_progress_table_byte_identical(learner, how):
    qid = learner.due_course(_target(how))
    learner.learning.save_draft(qid, {"answer": "正在"})
    learner.due_course(CORE_ID)
    learner.ids.add("grammar:core:intro")
    learner.learning.enroll(course("grammar:core:intro"), now=START)
    before = table_hashes(learner.store)
    orphan(learner, how)
    learning = learner.learning
    # Every read point, plus refused writes, while the content is unavailable.
    with learning.read_operation(now=READY) as read:
        learning.current(KINDS, projection=read)
        learning.counts(KINDS, projection=read)
        learning.current(KINDS, projection=read, teaching_only=True)
    list(learning.iter_pending_questions(KINDS))
    learning.items_needing_questions()
    learning.ensure_prerequisites(catalogue=[], now=READY)
    learner.store.unavailable_content_ids(READY)
    with pytest.raises(ValueError):
        learning.submit(qid, "正在读书", True, now=READY)
    restore(learner, how)
    after = table_hashes(learner.store)
    assert after == before
    assert learning.get_question(qid)["draft"] == {"answer": "正在"}


def test_registry_change_only_touches_the_registry_entry_state(learner):
    from kanamemory import content_library_rules as rules
    original = learner.store.get_setting(rules.REGISTRY_SETTING)
    learner.disable()
    learner.enable()
    changed = learner.store.get_setting(rules.REGISTRY_SETTING)
    assert [e["library_id"] for e in changed["libraries"]] == [e["library_id"] for e in original["libraries"]]
    assert changed["libraries"][1]["enabled"] is True
    assert changed["libraries"][1]["state"] == "pending_restart"  # applied on restart; reads use the registry now


def test_unavailable_content_ids_merge_item_controls_and_library_state(learner):
    learner.due_course(EXT_ID)
    assert learner.store.unavailable_content_ids(START) == frozenset()
    learner.disable()
    assert learner.store.unavailable_content_ids(START) == {EXT_ID}
    with learner.learning.read_operation(now=START) as read:
        assert learner.store.unavailable_content_ids(START) == read.availability().unavailable_ids == {EXT_ID}


def test_read_operation_shares_one_view_and_a_write_renews_it(learner):
    learner.due_course(CORE_ID)
    with learner.learning.read_operation(now=READY) as read:
        first = read.availability()
        assert read.availability() is first
        learner.learning.enroll(course(GONE_ID), now=READY)
        assert GONE_ID in read.availability().states


def test_store_without_a_registry_keeps_single_pack_behaviour(tmp_path):
    from kanamemory.domain import StudyStore
    from kanamemory.learning_store import LearningStore
    with StudyStore(tmp_path / "plain", time_zone="UTC") as store:
        learning = LearningStore(store)
        learning.enroll(course("synthetic:not-in-any-catalogue"), now=START)
        view = learning.availability()
        assert view.registry is None and view.unavailable_ids == frozenset()
        assert learning.counts(KINDS, now=START)["library_unavailable"] == 0


def test_dictionary_only_words_follow_the_dictionary_layer(learner, monkeypatch):
    import kanamemory.content_library_service as service
    from kanamemory.material_qualification import bind_resources
    cid = "word:jmdict:1:みず:1"
    item = {"id": cid, "family": "word", "revision": "v1", "abilities": ["understanding"], "title": "水",
            "reading": "みず", "dictionary_only": True, "instruction": {"title": "水"}}
    learner.learning.enroll(item, now=START)
    # No dictionary bound: the non-removable dictionary layer is assumed present.
    assert learner.learning.availability().state_of(cid) == availability.AVAILABLE

    class Repo:
        def __init__(self, path, known):
            self.path, self.known = path, known

        def get_word(self, word_id):
            return {"id": word_id} if word_id in self.known else None
    ids = learner.catalogue().ids
    learner.store.content_catalogue = None
    monkeypatch.setattr(service, "availability_catalogue",
                        lambda word_exists=None: availability.Catalogue(ids=ids, word_exists=word_exists))
    bind_resources(learner.store, repo=Repo("lexicon-a", {"jmdict:1"}))
    assert learner.learning.availability().state_of(cid) == availability.AVAILABLE
    bind_resources(learner.store, repo=Repo("lexicon-b", set()))
    assert learner.learning.availability().state_of(cid) == availability.ORPHANED
