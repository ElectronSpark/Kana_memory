"""One sentence per lesson: the course planner prefers a question whose sentence the learner has not met.

question_sentences defines a question's sentence units; select_practice_plan ranks a candidate showing a
sentence another question of the lesson already showed after one showing none, below the criteria that
decide which kind of task is due and above those that only spread repetition; course_planning reads what
was met (every ability's history, the queued questions of other abilities, the plans of the same pass).
"""
from types import SimpleNamespace

import pytest

from kanamemory.course_planning import iter_question_plans
from kanamemory.domain import StudyStore
from kanamemory.learning_practice_policy import met_sentences, select_practice_plan
from kanamemory.learning_store import LearningStore
from kanamemory.question_sentences import question_sentences
from support.learning_store_fixtures import START

CID = "grammar:sentence-test"
S0, S1, S2, S3 = "駅で友達を待っています", "本を毎日読んでいます", "公園で犬が走っています", "部屋で音楽を聞いています"


# --- the sentence units --------------------------------------------------------------------------------------

def test_answer_kinds_use_the_answer():
    for kind in ("targeted_correction", "grammar_reorder", "controlled_sentence_production"):
        units = question_sentences(kind, {"prompt": "駅で友達を待ちています。", "answer": "駅で友達を待っています。"})
        assert units == frozenset({S0}), kind
    assert isinstance(units, frozenset)


def test_first_gap_is_filled_with_the_answer():
    assert question_sentences("grammar_cloze", {"prompt": "駅で友達を待って（　）。", "answer": "います"}) == {S0}
    assert question_sentences("grammar_cloze", {"prompt": "駅で友達を待って( )。", "answer": "います"}) == {S0}
    # Only the first gap: the second stays a gap, and its brackets are dropped.
    assert question_sentences("grammar_cloze", {"prompt": "駅で（ ）を待って（ ）。", "answer": "友達"}) == {
        "駅で友達を待って"}


def test_prompt_without_gap_is_the_sentence():
    assert question_sentences("grammar_meaning", {"prompt": "駅で友達を待っています。", "answer": "正在等"}) == {S0}
    assert question_sentences("grammar_meaning", {"prompt": None, "answer": "x"}) == frozenset()


def test_lines_labels_splitting_and_dropped_characters():
    prompt = "Ａさん：駅で友達を待っています！\n店員: 本を、毎日「読んで」います[注]か？短い。"
    assert question_sentences("grammar_meaning", {"prompt": prompt, "answer": ""}) == {S0, "本を毎日読んでいますか"}
    # A part shorter than six characters is no unit; a label longer than four characters is kept.
    assert question_sentences("grammar_meaning", {"prompt": "はい。そうです。", "answer": ""}) == frozenset()
    assert question_sentences("grammar_meaning", {"prompt": "お店の人たち：はい", "answer": ""}) == {"お店の人たちはい"}


def test_met_sentences_keeps_the_fingerprints_that_showed_each_unit():
    a = {"kind": "grammar_meaning", "prompt": S0 + "。", "answer": "a"}
    b = {"kind": "grammar_cloze", "prompt": "駅で友達を待って（ ）。", "answer": "います"}
    met = met_sentences([a, b, {"prompt": "no kind"}, None])
    assert set(met) == {S0} and len(met[S0]) == 2


# --- selection through the course planner ---------------------------------------------------------------------

def template(sentence, context, *, family="choose_context", gap=False):
    prompt = sentence.replace("います", "（ ）") + "。" if gap else sentence + "。"
    return {"prompt": prompt, "answer": "います" if gap else "意味", "task": "選んでください", "feedback": "〜ています：正在做。",
            "choices": ["います", "いました"] if gap else ["意味", "別"], "input_mode": "choice",
            "practice": {"schema_version": 1, "task_family": family, "context_id": context,
                         "scaffold": "recognition", "target_spans": [{"start": 0, "end": 1, "quote": prompt[0]}],
                         "background_ids": [], "reviewed": True}}


def lesson(meaning=(S0,), cloze=(S0, S1)):
    return {"id": CID, "revision": "v1", "family": "grammar", "abilities": ["understanding", "application"],
            "instruction": {"title": "〜ている", "meaning": "正在做", "rule": "て形＋います"},
            "question_templates": {
                "grammar_meaning": [template(s, f"m{n}") for n, s in enumerate(meaning)],
                "grammar_cloze": [template(s, f"c{n}", gap=True) for n, s in enumerate(cloze)]}}


def seen(item, kind, variant, ability):
    q = dict(item["question_templates"][kind][variant], kind=kind, target_id=item["id"], revision=item["revision"],
             ability=ability, variant=variant)
    return {"question": q, "correct": True, "assisted": False, "outcome": "correct", "scheduled": True,
            "role": "primary"}


class Reader:
    """A stub planner reader that counts its reads."""

    def __init__(self, item, *, pending=(), histories=None, queued=None):
        self.item, self.pending, self.histories = item, set(pending), histories or {}
        self.queued = queued
        self.reads = []
        self.queries = SimpleNamespace(pending_abilities=lambda cid: set(self.pending),
                                       instruction_ready_ids=lambda: {item["id"]})

    def get_unit(self, cid, ability):
        return {"attempt_count": 0}

    def practice_history(self, cid, ability):
        self.reads.append(("history", ability))
        return list(self.histories.get(ability, ()))

    def queued_questions(self, cid):
        self.reads.append(("queued", cid))
        return list(self.queued or ())


def plan(item, reader, **kwargs):
    return [(p.question["ability"], p.question["kind"], p.question["variant"]) for p in iter_question_plans(
        item, enabled_kinds=list(item["question_templates"]), queries=reader.queries, learning_store=reader,
        system_japanese_input=False, **kwargs)]


def test_without_a_met_sentence_the_first_template_is_chosen():
    item = lesson(meaning=(S2,))
    assert plan(item, Reader(item, pending={"understanding"})) == [("application", "grammar_cloze", 0)]


def test_met_through_another_abilitys_history():
    item = lesson()
    reader = Reader(item, pending={"understanding"},
                    histories={"understanding": [seen(item, "grammar_meaning", 0, "understanding")]})
    assert plan(item, reader) == [("application", "grammar_cloze", 1)]
    # The planned ability's history is read once and reused; the other ability's once, for the sentences.
    assert sorted(r for r in reader.reads if r[0] == "history") == [("history", "application"),
                                                                     ("history", "understanding")]


def test_met_through_a_queued_question():
    item = lesson()
    queued = [("understanding", dict(item["question_templates"]["grammar_meaning"][0], kind="grammar_meaning"))]
    reader = Reader(item, pending={"understanding"}, queued=queued)
    assert plan(item, reader) == [("application", "grammar_cloze", 1)]
    assert reader.reads.count(("queued", CID)) == 1


def test_met_through_an_earlier_plan_of_the_same_pass():
    item = lesson()
    reader = Reader(item)
    assert plan(item, reader) == [("understanding", "grammar_meaning", 0), ("application", "grammar_cloze", 1)]
    # Nothing is queued in this pass's lesson: the queued questions are not read.
    assert ("queued", CID) not in reader.reads


def test_when_every_candidate_shares_a_met_sentence_todays_choice_stays():
    item = lesson(cloze=(S0, S0))
    item["question_templates"]["grammar_cloze"][1]["prompt"] = "駅で友達を待って（ ）。"   # same sentence, other text
    history = {"understanding": [seen(item, "grammar_meaning", 0, "understanding")]}
    assert plan(item, Reader(item, pending={"understanding"}, histories=history)) == [
        ("application", "grammar_cloze", 0)]
    assert plan(item, Reader(item, pending={"understanding"})) == [("application", "grammar_cloze", 0)]


def test_a_templates_own_earlier_exposure_is_not_another_question():
    # Repetition of the same template is ordered by the existing spread criteria, not by this one.
    item = lesson(meaning=(S2,), cloze=(S0, S1))
    history = {"application": [seen(item, "grammar_cloze", 1, "application")]}
    reader = Reader(item, pending={"understanding"}, histories=history)
    expected = select_practice_plan(item, "application", ["grammar_cloze"], {"attempt_count": 0},
                                    history["application"])
    assert plan(item, reader) == [("application", "grammar_cloze", expected["variant"])]


def test_the_consolidation_decks_avoid_still_holds():
    item = lesson()
    history = {"understanding": [seen(item, "grammar_meaning", 0, "understanding")]}
    reader = Reader(item, pending={"understanding"}, histories=history)
    assert plan(item, reader, avoid_templates={"application": ("grammar_cloze", 1)}) == [
        ("application", "grammar_cloze", 0)]


def test_a_single_candidate_reads_nothing_for_the_sentences():
    item = lesson(cloze=(S0,))
    reader = Reader(item, pending={"understanding"}, queued=[("understanding", {"kind": "grammar_meaning"})])
    assert plan(item, reader) == [("application", "grammar_cloze", 0)]
    assert reader.reads == [("history", "application")]


def test_a_pass_over_three_abilities_picks_three_sentences():
    sentences = [S0 + "。", S1 + "。", S2 + "。"]

    def word(prompt, answer):
        return {"prompt": prompt, "answer": answer, "choices": [answer, "別"], "input_mode": "choice"}
    item = {"id": "word:sentence-test", "family": "word", "revision": "v1", "title": "待つ", "reading": "まつ",
            "meaning": "等", "abilities": ["understanding", "use", "reading"],
            "question_templates": {
                "vocab_meaning": [word(s, "等") for s in sentences],
                "vocab_usage": [word(s, "待") for s in sentences],
                "vocab_reading": [word(s, "ま") for s in sentences]}}
    chosen = plan(item, Reader(item))
    assert [ability for ability, _, _ in chosen] == ["understanding", "use", "reading"]
    assert sorted(variant for _, _, variant in chosen) == [0, 1, 2]


# --- the earlier criteria still win ---------------------------------------------------------------------------

def select(item, kind_list, history, met_questions, **state):
    state = {"met_sentences": lambda: met_sentences(met_questions), **state}
    return select_practice_plan(item, "application", kind_list, state, history)


def other(sentence):
    return {"kind": "grammar_meaning", "prompt": sentence + "。", "answer": "意味"}


def cloze_lesson(rows):
    item = lesson()
    item["question_templates"] = {"grammar_cloze": [template(s, c, family=f, gap=True) for s, c, f in rows]}
    return item


def test_support_still_wins():
    item = lesson()
    item["question_templates"] = {
        "grammar_cloze": [template(S0, "c0", gap=True)],
        "grammar_transform": [dict(template(S1, "t1"), input_mode="text", choices=[],
                                   answer="いみ", accepted_answers=["いみ"], practice=dict(template(S1, "t1")["practice"],
                                                                         scaffold="cued", task_family="transform")),
                              dict(template(S2, "t2"), input_mode="text", choices=[],
                                   answer="いみ", accepted_answers=["いみ"], practice=dict(template(S2, "t2")["practice"],
                                                                         scaffold="cued", task_family="transform"))]}
    failed = dict(seen(item, "grammar_transform", 1, "application"), correct=False, outcome="incorrect")
    kinds = ["grammar_cloze", "grammar_transform"]
    # After a failed cued task the recognition task comes first, though its sentence was met elsewhere.
    assert select(item, kinds, [failed], [other(S0)], attempt_count=1)["kind"] == "grammar_cloze"


def test_coverage_still_wins():
    item = cloze_lesson([(S0, "c0", "fill"), (S1, "c1", "fill"), (S2, "c2", "contrast")])
    history = [seen(item, "grammar_cloze", 0, "application")] * 3
    met = [other(S2)]
    # The coverage slot (every fourth completion) takes the unused family, though its sentence was met.
    assert select(item, ["grammar_cloze"], history, met, attempt_count=3)["variant"] == 2
    # Outside it the new criterion comes before the family spread, which alone would take the unused family.
    assert select(item, ["grammar_cloze"], history, met, attempt_count=2)["variant"] == 1
    assert select(item, ["grammar_cloze"], history, [], attempt_count=2)["variant"] == 2


def test_recent_context_still_wins():
    item = cloze_lesson([(S0, "c0", "fill"), (S1, "c0", "fill"), (S2, "c2", "fill")])
    history = [seen(item, "grammar_cloze", 0, "application")]
    # S1 shares the context of the last attempt; S2 has a fresh context but its sentence was met elsewhere.
    assert select(item, ["grammar_cloze"], history, [other(S2)], attempt_count=2)["variant"] == 2
    assert select(item, ["grammar_cloze"], history, [other(S1)], attempt_count=2)["variant"] == 2


def test_targeted_follow_up_still_wins():
    item = cloze_lesson([(S0, "c0", "fill"), (S1, "c1", "contrast"), (S2, "c2", "fill"), (S3, "c3", "contrast")])
    history = [dict(seen(item, "grammar_cloze", 1, "application"), correct=False, outcome="incorrect"),
               seen(item, "grammar_cloze", 0, "application")]
    # The targeted slot follows the failed family with a changed example, though its sentence was met.
    assert select(item, ["grammar_cloze"], history, [other(S3)], attempt_count=5)["variant"] == 3
    # Outside the slot the fresh task without a met sentence comes first.
    assert select(item, ["grammar_cloze"], history, [other(S3)], attempt_count=6)["variant"] == 2


# --- the store's queued questions ------------------------------------------------------------------------------

@pytest.fixture
def learning(tmp_path):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        yield store, LearningStore(store)


def test_a_queued_question_in_the_store_takes_its_sentence(learning):
    store, learner = learning
    item = lesson()
    learner.enroll(item, now=START)
    learner.advance_instruction(CID, "intro_done", now=START)
    learner.advance_instruction(CID, "guide_correct", now=START)
    saved = learner.get_item(CID)
    kwargs = dict(enabled_kinds=["grammar_meaning", "grammar_cloze"], queries=store.queries,
                  learning_store=learner, system_japanese_input=False)
    first = next(iter(iter_question_plans(saved, **kwargs)))
    assert (first.question["ability"], first.question["variant"]) == ("understanding", 0)
    from kanamemory.learning_questions import build_learning_question
    learner.append_questions([dict(build_learning_question(saved, "grammar_meaning", variant=0),
                                   id=first.question["id"], ability="understanding")], now=START)
    assert learner.queued_questions(CID)[0][0] == "understanding"
    plans = list(iter_question_plans(learner.get_item(CID), **kwargs))
    assert [(p.question["ability"], p.question["variant"]) for p in plans] == [("application", 1)]
